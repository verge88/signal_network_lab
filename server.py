import csv
import io
import json
import math
import threading
from pathlib import Path
from typing import Literal
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from lab.schema import Project, Incident
from lab.presets import preset
from lab.simulator import simulate, FEATURES
from lab import storage, ml

ROOT=Path(__file__).resolve().parent
app=FastAPI(title='Signal Lab',version='1.0.0',docs_url='/api/docs')
compute_lock=threading.Lock()


@app.middleware('http')
async def local_only(request: Request, call_next):
    # Bind to loopback and reject browser cross-origin writes / DNS rebinding.
    if request.headers.get('host','').split(':')[0] not in ('127.0.0.1','localhost','testserver'):
        return JSONResponse({'detail':'Local host required'},status_code=403)
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        if origin and origin not in ('http://127.0.0.1:8765','http://localhost:8765'):
            return JSONResponse({'detail':'Cross-origin write rejected'},status_code=403)
        if request.headers.get('content-length','0').isdigit() and int(request.headers.get('content-length','0'))>12_000_000:
            return JSONResponse({'detail':'Файл превышает 12 МБ'},status_code=413)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Cache-Control']='no-store'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'self'"
    return response


@app.exception_handler(ValueError)
async def value_error(_,exc): return JSONResponse({'detail':str(exc)},status_code=422)


@app.exception_handler(KeyError)
async def missing(_,exc): return JSONResponse({'detail':str(exc)},status_code=404)


def compute(fn):
    if not compute_lock.acquire(blocking=False): raise HTTPException(409,'Уже выполняется вычисление. Дождитесь завершения.')
    try: return fn()
    finally: compute_lock.release()


@app.get('/api/health')
def health(): return {'status':'ok','mode':'offline-simulation','version':'1.0.0'}


@app.get('/api/presets/{name}')
def get_preset(name: Literal['hybrid','ss7','diameter','5g']): return preset(name)


@app.post('/api/projects')
def save_project(project: Project): return storage.save('project',project.name,{'project':project.model_dump()})


@app.post('/api/validate')
def validate_project(project: Project): return project


@app.get('/api/records/{kind}')
def records(kind: Literal['project','run','dataset','model','sweep']): return storage.listing(kind)


@app.get('/api/records/{kind}/{rid}')
def record(kind: Literal['project','run','dataset','model','sweep'],rid: str): return storage.get(kind,rid)


@app.post('/api/simulate')
def run(project: Project):
    return compute(lambda: storage.save('run',f'{project.name} · seed {project.seed}',{'project':project.model_dump(),**simulate(project)}))


class SweepRequest(BaseModel):
    project: Project
    values: list[float] = Field(default=[0.5,1,1.5,2],min_length=2,max_length=8)
    repetitions: int = Field(default=3,ge=1,le=5)


@app.post('/api/sweep')
def sweep(req: SweepRequest):
    def work():
        if any(not math.isfinite(v) or v<=0 or v>5 for v in req.values): raise ValueError('Множители должны быть в диапазоне (0, 5]')
        # Validate the entire batch before creating any result.
        projects=[]
        for v in req.values:
            for j in range(req.repetitions):
                d=req.project.model_dump()
                d['seed']=(req.project.seed+j)%2147483647
                for f in d['flows']: f['rate']*=v
                projects.append((v,Project.model_validate(d)))
        if sum(sum(f.rate for f in p.flows)*p.duration for _,p in projects)>1_000_000: raise ValueError('Для серии уменьшите нагрузку, длительность или число повторов (лимит 1 млн базовых сообщений)')
        results=[]
        for v,p in projects:
            result=storage.save('run',f'Sweep ×{v} · seed {p.seed}',{'project':p.model_dump(),**simulate(p)})
            results.append(dict(multiplier=v,run_id=result['id'],seed=p.seed,**result['summary']))
        groups=[]
        for v in req.values:
            subset=[r for r in results if r['multiplier']==v]
            group=dict(multiplier=v,n=len(subset))
            for key in ['latency_p95','delivery_ratio','throughput']:
                vals=[r[key] for r in subset]
                group[key]=float(np.mean(vals))
                group[key+'_ci95']=float(1.96*np.std(vals,ddof=1)/np.sqrt(len(vals))) if len(vals)>1 else None
            groups.append(group)
        return storage.save('sweep',f'Sweep / {req.project.name}',dict(results=results,groups=groups,metadata=dict(repetitions=req.repetitions,values=req.values)))
    return compute(work)


class DatasetRequest(BaseModel):
    project: Project
    runs: int = Field(default=12,ge=6,le=24)
    name: str = Field(default='Signaling telemetry',max_length=100)


@app.post('/api/datasets/generate')
def dataset(req: DatasetRequest):
    def work():
        rows=[]
        provenance=[]
        prepared=[]
        for i in range(req.runs):
            d=req.project.model_dump()
            d['seed']=(d['seed']+1009*i)%2147483647
            rng=np.random.default_rng(d['seed'])
            d['incidents']=[]
            for f in d['flows']: f['rate']*=float(rng.uniform(.75,1.25))
            if i%2:
                if req.project.incidents:
                    d['incidents']=[e.model_dump() for e in req.project.incidents]
                else:
                    flow=d['flows'][i%len(d['flows'])]
                    from lab.procedures import ProcedureSimulation
                    probe=ProcedureSimulation(Project.model_validate(d));probe_flow=next(f for f in probe.p.flows if f.id==flow['id'])
                    path=probe.route(probe_flow.source,probe.destination(probe_flow),probe_flow)
                    compatible=[l for _,lid in (path or []) for l in d['links'] if l['id']==lid]
                    kind=['surge','latency','node_down'][((i-1)//2)%3]
                    if kind=='latency' and not compatible: kind='surge'
                    target=flow['id'] if kind=='surge' else compatible[0]['id'] if kind=='latency' else flow['target']
                    d['incidents']=[dict(kind=kind,target=target,start=d['duration']*.32,duration=d['duration']*.36,magnitude=float(rng.uniform(3,6)))]
            prepared.append(Project.model_validate(d))
        if sum(sum(f.rate for f in p.flows)*p.duration for p in prepared)>1_500_000: raise ValueError('Слишком большой датасет: уменьшите интенсивность или длительность (лимит 1,5 млн базовых сообщений)')
        for i,p in enumerate(prepared):
            result=simulate(p)
            rid=f'run-{i:03d}-seed-{p.seed}'
            rows.extend(dict(run_id=rid,**r) for r in result['windows'])
            provenance.append(dict(run_id=rid,project=p.model_dump(),summary=result['summary']))
        metadata=dict(rows=len(rows),runs=req.runs,positives=sum(r['label'] for r in rows),features=FEATURES,source='synthetic',environment=result['environment'],feature_contract=result['feature_contract'],label_policy=p.label_policy if p.engine=='procedures' else 'scheduled')
        return storage.save('dataset',req.name,dict(rows=rows,provenance=provenance,metadata=metadata))
    return compute(work)


class CSVRequest(BaseModel):
    name: str = Field(default='Imported telemetry',max_length=100)
    csv: str = Field(max_length=10_000_000)


@app.post('/api/datasets/import')
def import_csv(req: CSVRequest):
    reader=csv.DictReader(io.StringIO(req.csv.lstrip('\ufeff')))
    required=['run_id','label',*FEATURES]
    if not reader.fieldnames or not set(required).issubset(reader.fieldnames): raise ValueError('CSV должен содержать: '+', '.join(required))
    rows=[]
    for i,r in enumerate(reader):
        if i>=20000: raise ValueError('Лимит импорта: 20 000 строк')
        try:
            label=float(r['label'])
            if label not in (0,1): raise ValueError()
            row={f:float(r[f]) for f in FEATURES}
            if any(not math.isfinite(v) for v in row.values()): raise ValueError()
            if not r['run_id'] or len(r['run_id'])>100: raise ValueError()
            t=float(r.get('time') or i)
            if not math.isfinite(t): raise ValueError()
            contract=r.get('feature_contract') or 'messages-v1'
            if contract not in ('messages-v1','procedures-v2'):raise ValueError()
            rows.append(dict(run_id=r['run_id'],label=int(label),time=t,feature_contract=contract,**row))
        except (ValueError,TypeError): raise ValueError(f'Некорректные данные в строке {i+2}')
    if len(rows)<30: raise ValueError('Нужно минимум 30 строк')
    return storage.save('dataset',req.name,dict(rows=rows,metadata=dict(rows=len(rows),runs=len({r['run_id'] for r in rows}),positives=sum(r['label'] for r in rows),features=FEATURES,source='csv')))


class TrainRequest(BaseModel):
    dataset_id: str
    algorithm: Literal['logistic','random_forest','isolation_forest'] = 'random_forest'
    seed: int = Field(default=42,ge=0,le=2147483647)
    threshold: float | None = Field(default=None,ge=0,le=1)
    trees: int = Field(default=120,ge=20,le=300)
    contamination: float = Field(default=.1,gt=0,le=.5)
    threshold_mode: Literal['validation','contamination'] = 'validation'


@app.post('/api/train')
def training(req: TrainRequest):
    def work():
        dataset=storage.get('dataset',req.dataset_id)
        if req.threshold_mode=='contamination' and req.algorithm!='isolation_forest': raise ValueError('Порог contamination доступен только для Isolation Forest')
        result=ml.train(dataset['rows'],req.algorithm,req.seed,req.threshold,req.trees,req.contamination,req.threshold_mode)
        return storage.save('model',f'{req.algorithm} / {dataset["name"]}',dict(dataset_id=req.dataset_id,**result,metadata=dict(algorithm=req.algorithm,f1=result['metrics']['f1'],dataset=dataset['name'])),result['model_id'])
    return compute(work)


class EvaluateRequest(BaseModel):
    threshold: float = Field(ge=0,le=1)


@app.post('/api/models/{rid}/evaluate')
def evaluate(rid: str,req: EvaluateRequest):
    m=storage.get('model',rid)
    return ml.metrics(m['test_labels'],m['test_scores'],req.threshold)


class PredictRequest(BaseModel):
    model_id: str
    run_id: str
    threshold: float | None = Field(default=None,ge=0,le=1)


@app.post('/api/predict')
def inference(req: PredictRequest):
    m=storage.get('model',req.model_id)
    r=storage.get('run',req.run_id)
    threshold=req.threshold if req.threshold is not None else m['threshold']
    return dict(threshold=threshold,windows=ml.predict(m['model_id'],r['windows'],threshold))


@app.get('/api/export/{kind}/{rid}')
def export(kind: Literal['run','dataset','project','model','sweep'],rid: str,format: Literal['json','csv']='json'):
    record=storage.get(kind,rid)
    if format=='json': return Response(json.dumps(record,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="{kind}-{rid}.json"'})
    rows=record.get('rows',record.get('windows',record.get('results',[])))
    if not rows: raise ValueError('Для этой записи нет табличного CSV')
    out=io.StringIO()
    writer=csv.DictWriter(out,fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{kind}-{rid}.csv"'})


app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')


@app.get('/')
def index(): return FileResponse(ROOT/'static'/'index.html')


if __name__=='__main__':
    import uvicorn
    print('Signal Lab: http://127.0.0.1:8765',flush=True)
    uvicorn.run(app,host='127.0.0.1',port=8765)
