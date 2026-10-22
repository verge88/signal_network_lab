"""Desktop use cases. These functions do not depend on a web server or Qt."""
import csv
import io
import math
import json
import numpy as np
from concurrent.futures import CancelledError
from .schema import Project
from .simulator import simulate, FEATURES
from . import storage, ml
from .features import specification, matrix, topology_id


def checked(project):
    return Project.model_validate(project.model_dump() if isinstance(project,Project) else project)


def checkpoint(cancel):
    if cancel and cancel():raise CancelledError('Вычисление отменено')


def run(project, progress=None, cancel=None):
    p=checked(project)
    result=simulate(p,progress,cancel)
    checkpoint(cancel)
    return storage.save('run',f'{p.name} · seed {p.seed} · events {len(p.incidents)} · атак {sum(a.enabled for a in p.attacks)}',dict(project=p.model_dump(),**result))


def replay_procedure(record,procedure_id,progress=None,cancel=None):
    data=checked(record['project']).model_dump()
    if data['engine']!='procedures':raise ValueError('Подробный разбор требует движок процедур')
    data.update(journal_sessions=1,journal_procedure_id=procedure_id,journal_events=10000)
    project=checked(data);result=simulate(project,progress,cancel);checkpoint(cancel)
    replay=dict(source_run_id=record.get('id'),procedure_id=procedure_id,source_engine=record.get('environment',{}).get('engine'),
        current_engine=result['environment']['engine'],same_summary=result['summary']==record['summary'],same_windows=result['windows']==record['windows'])
    return storage.save('run',f'Разбор процедуры {procedure_id} / {project.name}',dict(project=project.model_dump(),replay=replay,**result))


def dataset(project, runs=12, name='Signaling telemetry', progress=None, cancel=None, feature_set='legacy-v1', topology_variants=False):
    p=checked(project)
    spec=specification(feature_set)
    if spec['telemetry'] and p.engine!='procedures':raise ValueError('Расширенные признаки требуют движок процедур')
    if not 6<=runs<=24:raise ValueError('Количество прогонов: от 6 до 24')
    prepared=[]
    for i in range(runs):
        d=p.model_dump()
        d['seed']=(d['seed']+1009*i)%2147483647
        rng=np.random.default_rng(d['seed'])
        d['incidents']=[]
        d['attacks']=[]
        if topology_variants:
            # Three actual multigraphs: original, one extra path, two extra paths.
            candidates=[link for link in d['links'] if link['enabled']]
            if not candidates:raise ValueError('Для вариантов топологии нужен хотя бы один включённый канал')
            for j in range((i//2)%3):
                extra=dict(candidates[j%len(candidates)]);extra['id']=f'ml-{i}-{j}'
                extra['latency_ms']*=1.25+j*.25;d['links'].append(extra)
        for f in d['flows']:f['rate']*=float(rng.uniform(.75,1.25))
        if i%2:
            if p.incidents or any(a.enabled for a in p.attacks):
                d['incidents']=[e.model_dump() for e in p.incidents]
                d['attacks']=[a.model_dump() for a in p.attacks]
            else:
                flow=d['flows'][i%len(d['flows'])]
                from .procedures import ProcedureSimulation
                probe=ProcedureSimulation(checked(d));probe_flow=next(f for f in probe.p.flows if f.id==flow['id'])
                # Plan incidents on the configured topology before runtime associations open.
                path=(probe.route(probe_flow.source,probe.destination(probe_flow),probe_flow,ignore_faults=True)
                      if probe.nodes[probe_flow.source].enabled else None)
                compatible=[l for _,lid in (path or []) for l in d['links'] if l['id']==lid]
                kind=['surge','latency','node_down'][(((i-1)//2)+(i//6 if topology_variants else 0))%3]
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
        matrix(r['windows'],feature_set)
        from .attacks import compile_attacks
        effects,_=compile_attacks(config);events=[*config.incidents,*effects]
        topology=topology_id(config.model_dump());fault_types=sorted({event.kind for event in events})
        intervals=[[event.start,min(config.duration,event.start+event.duration)] for event in events]
        rows.extend(dict(run_id=rid,feature_set=feature_set,topology_id=topology,fault_types=fault_types,fault_intervals=intervals,**w) for w in r['windows'])
        provenance.append(dict(run_id=rid,topology_id=topology,fault_types=fault_types,project=config.model_dump(),summary=r['summary']))
    checkpoint(cancel)
    return storage.save('dataset',name,dict(rows=rows,provenance=provenance,metadata=dict(rows=len(rows),runs=runs,positives=sum(r['label'] for r in rows),features=list(spec['features']),feature_set=feature_set,topologies=len({p['topology_id'] for p in provenance}),source='synthetic',environment=r['environment'],feature_contract=r['feature_contract'],label_policy=config.label_policy if config.engine=='procedures' else 'scheduled')))


def sweep(project, values, repetitions=3, progress=None, cancel=None):
    p=checked(project)
    if not 2<=len(values)<=8 or any(not math.isfinite(v) or v<=0 or v>5 for v in values):raise ValueError('Нужно 2–8 множителей в диапазоне (0, 5]')
    if len(set(values))!=len(values):raise ValueError('Множители не должны повторяться')
    if not 1<=repetitions<=1000:raise ValueError('Число повторений: 1–1000')
    if p.engine=='procedures':
        from .experiments import run_series,Options,Axis
        return run_series(p,Options(axes=[Axis(path='load',values=values)],repetitions=repetitions,save_runs=True),progress,cancel)
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
            from .experiments import interval
            group[k+'_ci95']=interval(vals,k)['half_width']
        groups.append(group)
    return storage.save('sweep',f'Нагрузка / {p.name}',dict(results=results,groups=groups,metadata=dict(repetitions=repetitions,values=values)))


def train(dataset_id, algorithm='random_forest', seed=42, trees=120, contamination=.1, threshold_mode='validation', progress=None, cancel=None, feature_set=None, evaluation_mode='run', holdout_value=None):
    checkpoint(cancel)
    if algorithm not in ('random_forest','logistic','isolation_forest'):raise ValueError('Неизвестный алгоритм')
    if not 20<=trees<=300 or not 0<contamination<=.5 or not 0<=seed<=2147483647:raise ValueError('Некорректные параметры обучения')
    if threshold_mode not in ('validation','contamination') or (threshold_mode=='contamination' and algorithm!='isolation_forest'):raise ValueError('Неподдерживаемый метод выбора порога')
    d=storage.get('dataset',dataset_id)
    if progress:progress(10)
    result=ml.train(d['rows'],algorithm,seed=seed,trees=trees,contamination=contamination,threshold_mode=threshold_mode,
        feature_set=feature_set or d['metadata'].get('feature_set','legacy-v1'),evaluation_mode=evaluation_mode,holdout_value=holdout_value)
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
    required={'run_id','label'}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):raise ValueError('Обязательные поля: run_id, label и признаки выбранной версии')
    rows=[]
    for i,r in enumerate(reader):
        if i>=20000:raise ValueError('Лимит: 20 000 строк')
        try:
            label=float(r['label'])
            if label not in (0,1) or not r['run_id'] or len(r['run_id'])>100:raise ValueError()
            key=r.get('feature_set') or 'legacy-v1';spec=specification(key)
            if spec['telemetry'] and not r.get('time'):raise ValueError()
            row={f:float(r[f]) for f in spec['features']}
            t=float(r.get('time') or i)
            if not all(math.isfinite(v) for v in [*row.values(),t]):raise ValueError()
            contract=r.get('feature_contract') or 'messages-v1'
            if contract not in ('procedures-v2','messages-v1'):raise ValueError()
            row.update(run_id=r['run_id'],label=int(label),time=t,feature_contract=contract,feature_set=key)
            if r.get('telemetry_contract'):row['telemetry_contract']=r['telemetry_contract']
            for field in ('fault_types','fault_intervals'):
                if r.get(field):row[field]=json.loads(r[field])
            if 'fault_types' in row and (not isinstance(row['fault_types'],list) or any(not isinstance(v,str) or len(v)>100 for v in row['fault_types'])):raise ValueError()
            if 'fault_intervals' in row:
                if not isinstance(row['fault_intervals'],list):raise ValueError()
                for interval in row['fault_intervals']:
                    if len(interval)!=2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in interval) or not 0<=interval[0]<interval[1]:raise ValueError()
            if r.get('topology_id'):row['topology_id']=r['topology_id'][:100]
            if r.get('window_seconds'):
                row['window_seconds']=float(r['window_seconds'])
                if not math.isfinite(row['window_seconds']) or row['window_seconds']<=0:raise ValueError()
            matrix([row],key);rows.append(row)
        except (ValueError,TypeError,KeyError):raise ValueError(f'Некорректные данные в строке {i+2}')
    if len(rows)<30:raise ValueError('Нужно минимум 30 строк')
    if len({r['feature_set'] for r in rows})!=1 or len({r['feature_contract'] for r in rows})!=1:raise ValueError('Нельзя смешивать версии признаков в CSV')
    return storage.save('dataset',name,dict(rows=rows,metadata=dict(rows=len(rows),runs=len({r['run_id'] for r in rows}),positives=sum(r['label'] for r in rows),features=list(spec['features']),feature_set=key,feature_contract=contract,source='csv')))


def csv_text(rows):
    if not rows:raise ValueError('Нет табличных данных для экспорта')
    output=io.StringIO()
    writer=csv.DictWriter(output,fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in r.items()} for r in rows)
    return output.getvalue()
