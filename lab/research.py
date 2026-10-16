"""Desktop use cases. These functions do not depend on a web server or Qt."""
import csv
import io
import math
import numpy as np
from concurrent.futures import CancelledError
from .schema import Project
from .simulator import simulate, FEATURES
from . import storage, ml


def checked(project):
    return Project.model_validate(project.model_dump() if isinstance(project,Project) else project)


def checkpoint(cancel):
    if cancel and cancel():raise CancelledError('Вычисление отменено')


def run(project, progress=None, cancel=None):
    p=checked(project)
    result=simulate(p,progress,cancel)
    checkpoint(cancel)
    return storage.save('run',f'{p.name} · seed {p.seed} · events {len(p.incidents)}',dict(project=p.model_dump(),**result))


def dataset(project, runs=12, name='Signaling telemetry', progress=None, cancel=None):
    p=checked(project)
    if not 6<=runs<=24:raise ValueError('Количество прогонов: от 6 до 24')
    prepared=[]
    for i in range(runs):
        d=p.model_dump()
        d['seed']=(d['seed']+1009*i)%2147483647
        rng=np.random.default_rng(d['seed'])
        d['incidents']=[]
        for f in d['flows']:f['rate']*=float(rng.uniform(.75,1.25))
        if i%2:
            if p.incidents:d['incidents']=[e.model_dump() for e in p.incidents]
            else:
                flow=d['flows'][i%len(d['flows'])]
                from .procedures import ProcedureSimulation
                probe=ProcedureSimulation(checked(d));probe_flow=next(f for f in probe.p.flows if f.id==flow['id'])
                path=probe.route(probe_flow.source,probe.destination(probe_flow),probe_flow)
                compatible=[l for _,lid in (path or []) for l in d['links'] if l['id']==lid]
                kind=['surge','latency','node_down'][((i-1)//2)%3]
                if kind=='latency' and not compatible:kind='surge'
                target=flow['id'] if kind=='surge' else compatible[0]['id'] if kind=='latency' else flow['target']
                d['incidents']=[dict(kind=kind,target=target,start=d['duration']*.32,duration=d['duration']*.36,magnitude=float(rng.uniform(3,6)))]
        prepared.append(checked(d))
    if sum(sum(f.rate for f in p.flows)*p.duration for p in prepared)>1_500_000:raise ValueError('Уменьшите размер датасета: лимит 1,5 млн базовых сообщений')
    rows=[]
    provenance=[]
    for i,config in enumerate(prepared):
        checkpoint(cancel)
        r=simulate(config,lambda value:progress(int((i+value/100)/runs*100)) if progress else None,cancel)
        rid=f'run-{i:03d}-seed-{config.seed}'
        rows.extend(dict(run_id=rid,**w) for w in r['windows'])
        provenance.append(dict(run_id=rid,project=config.model_dump(),summary=r['summary']))
    checkpoint(cancel)
    return storage.save('dataset',name,dict(rows=rows,provenance=provenance,metadata=dict(rows=len(rows),runs=runs,positives=sum(r['label'] for r in rows),features=FEATURES,source='synthetic',environment=r['environment'],feature_contract=r['feature_contract'],label_policy=config.label_policy if config.engine=='procedures' else 'scheduled')))


def sweep(project, values, repetitions=3, progress=None, cancel=None):
    p=checked(project)
    if not 2<=len(values)<=8 or any(not math.isfinite(v) or v<=0 or v>5 for v in values):raise ValueError('Нужно 2–8 множителей в диапазоне (0, 5]')
    if len(set(values))!=len(values):raise ValueError('Множители не должны повторяться')
    if not 1<=repetitions<=5:raise ValueError('Число повторений: 1–5')
    prepared=[]
    for v in values:
        for j in range(repetitions):
            d=p.model_dump()
            d['seed']=(d['seed']+j)%2147483647
            for f in d['flows']:f['rate']*=v
            prepared.append((v,checked(d)))
    if sum(sum(f.rate for f in p.flows)*p.duration for _,p in prepared)>1_000_000:raise ValueError('Уменьшите размер серии: лимит 1 млн базовых сообщений')
    results=[]
    for i,(v,config) in enumerate(prepared):
        checkpoint(cancel)
        r=run(config,lambda value:progress(int((i+value/100)/len(prepared)*100)) if progress else None,cancel)
        results.append(dict(multiplier=v,run_id=r['id'],seed=config.seed,**r['summary']))
    groups=[]
    for v in values:
        subset=[r for r in results if r['multiplier']==v]
        group=dict(multiplier=v,n=len(subset))
        for k in ['latency_p95','delivery_ratio','throughput']:
            vals=[r[k] for r in subset]
            group[k]=float(np.mean(vals))
            group[k+'_ci95']=float(1.96*np.std(vals,ddof=1)/np.sqrt(len(vals))) if len(vals)>1 else None
        groups.append(group)
    return storage.save('sweep',f'Нагрузка / {p.name}',dict(results=results,groups=groups,metadata=dict(repetitions=repetitions,values=values)))


def train(dataset_id, algorithm='random_forest', seed=42, trees=120, contamination=.1, threshold_mode='validation', progress=None, cancel=None):
    checkpoint(cancel)
    if algorithm not in ('random_forest','logistic','isolation_forest'):raise ValueError('Неизвестный алгоритм')
    if not 20<=trees<=300 or not 0<contamination<=.5 or not 0<=seed<=2147483647:raise ValueError('Некорректные параметры обучения')
    if threshold_mode not in ('validation','contamination') or (threshold_mode=='contamination' and algorithm!='isolation_forest'):raise ValueError('Неподдерживаемый метод выбора порога')
    d=storage.get('dataset',dataset_id)
    if progress:progress(10)
    result=ml.train(d['rows'],algorithm,seed=seed,trees=trees,contamination=contamination,threshold_mode=threshold_mode)
    if cancel and cancel():
        # Remove only the artifact created by this cancelled training request.
        (ml.DATA/'models'/f'{result["model_id"]}.joblib').unlink(missing_ok=True)
        raise CancelledError('Обучение отменено')
    record=storage.save('model',f'{algorithm} / {d["name"]}',dict(dataset_id=dataset_id,**result,metadata=dict(algorithm=algorithm,f1=result['metrics']['f1'],dataset=d['name'])),result['model_id'])
    if progress:progress(100)
    return record


def import_csv(text,name):
    if len(text.encode('utf-8'))>10_000_000:raise ValueError('Лимит CSV: 10 МБ')
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    required={'run_id','label',*FEATURES}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):raise ValueError('Обязательные поля: run_id, label, '+', '.join(FEATURES))
    rows=[]
    for i,r in enumerate(reader):
        if i>=20000:raise ValueError('Лимит: 20 000 строк')
        try:
            label=float(r['label'])
            if label not in (0,1) or not r['run_id'] or len(r['run_id'])>100:raise ValueError()
            row={f:float(r[f]) for f in FEATURES}
            t=float(r.get('time') or i)
            if not all(math.isfinite(v) for v in [*row.values(),t]):raise ValueError()
            contract=r.get('feature_contract') or 'messages-v1'
            if contract not in ('procedures-v2','messages-v1'):raise ValueError()
            rows.append(dict(run_id=r['run_id'],label=int(label),time=t,feature_contract=contract,**row))
        except (ValueError,TypeError):raise ValueError(f'Некорректные данные в строке {i+2}')
    if len(rows)<30:raise ValueError('Нужно минимум 30 строк')
    return storage.save('dataset',name,dict(rows=rows,metadata=dict(rows=len(rows),runs=len({r['run_id'] for r in rows}),positives=sum(r['label'] for r in rows),features=FEATURES,source='csv')))


def csv_text(rows):
    if not rows:raise ValueError('Нет табличных данных для экспорта')
    output=io.StringIO()
    writer=csv.DictWriter(output,fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()
