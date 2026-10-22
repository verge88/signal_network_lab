"""Independent monitoring datasets and paired feature-group ablations."""
import csv
import io
import json
import math
from pathlib import Path
import numpy as np
from . import storage,ml
from .schema import Project
from .simulator import simulate
from .research import checkpoint
from .features import topology_id
from .monitoring import schema_spec


def generate(project,runs=12,progress=None,cancel=None,topology_variants=False):
    p=Project.model_validate(project.model_dump() if isinstance(project,Project) else project)
    if not p.monitoring.enabled:raise ValueError('Включите и настройте мониторинг')
    if not 6<=runs<=24:raise ValueError('Количество прогонов: 6–24')
    if not (p.incidents or p.monitoring.faults or any(a.enabled for a in p.attacks)):raise ValueError('Добавьте сетевое воздействие или искажение отчёта для сравнения с нормой')
    if runs*len(p.monitoring.members)*math.ceil(p.duration/p.monitoring.decision_s)>300000:raise ValueError('Лимит датасета: 300 000 строк')
    rows=[];provenance=[]
    for i in range(runs):
        checkpoint(cancel);data=p.model_dump();data['seed']=(p.seed+1009*i)%2147483647
        rng=np.random.default_rng(data['seed'])
        for f in data['flows']:f['rate']*=float(rng.uniform(.85,1.15))
        if topology_variants:
            candidates=[l for l in data['links'] if l['enabled']]
            if not candidates:raise ValueError('Для вариантов связности нужен включённый канал')
            for j in range((i//2)%3):
                ident=f'monitor-variant-{i}-{j}'
                if any(l['id']==ident for l in data['links']):raise ValueError('Конфликт ID варианта канала')
                data['links'].append(dict(candidates[j%len(candidates)],id=ident))
        if i%2==0:data['incidents']=[];data['attacks']=[];data['monitoring']['faults']=[]
        config=Project.model_validate(data)
        r=simulate(config,lambda v:progress(int((i+v/100)/runs*100)) if progress else None,cancel)
        rid=f'monitor-{i:03d}-{config.seed}';topology=topology_id(data)
        rows.extend(dict(row,run_id=rid,topology_id=topology) for row in r['monitoring']['rows'])
        provenance.append(dict(run_id=rid,project=data,summary=r['summary'],counts=r['monitoring']['counts'],wire_bytes=r['monitoring']['wire_bytes'],link_wire_bytes=r['monitoring']['link_wire_bytes']))
    checkpoint(cancel)
    return storage.save('monitor_dataset',p.name+' · мониторинг',dict(schema=schema_spec(p.monitoring),rows=rows,provenance=provenance,metadata=dict(source='synthetic',runs=runs,rows=len(rows),positives=sum(r['label'] for r in rows))))


def compare(dataset,algorithms=('random_forest','isolation_forest'),groups=None,seed=42,trees=120,progress=None,cancel=None,evaluation_mode='run',holdout_value=None):
    rows=dataset['rows'];spec=dataset['schema'];key='monitoring:'+spec['id']
    ml.input_matrix(rows,key,spec)
    defined=sorted({f['group'] for f in spec['definitions']})
    groups=defined if groups is None else groups
    if not set(groups)<=set(defined):raise ValueError('Неизвестная группа абляции')
    if not algorithms or not set(algorithms)<={'random_forest','isolation_forest','logistic'}:raise ValueError('Выберите доступный алгоритм')
    variants=[('Полный набор',spec['features'])]+[(f'Без группы: {group}',[f['name'] for f in spec['definitions'] if f['group']!=group]) for group in groups]
    variants=[(name,features) for name,features in variants if features]
    models=[];split=None
    try:
        for algorithm in algorithms:
            for variant,features in variants:
                checkpoint(cancel)
                result=ml.train(rows,algorithm,seed=seed,trees=trees,feature_set=key,custom_spec=spec,feature_names=features,fixed_split=split,evaluation_mode=evaluation_mode,holdout_value=holdout_value)
                models.append(dict(result,variant=variant))
                if split is None:split={k:v['runs'] for k,v in result['split'].items()}
                if progress:progress(int(len(models)*100/(len(algorithms)*len(variants))))
        checkpoint(cancel)
        return storage.save('monitor_study','Сравнение · '+dataset['name'],dict(dataset_id=dataset['id'],schema=spec,models=models,split=split,
            method='Paired run-disjoint splits; same seed and observations; retrain after excluding each feature group; validation-only threshold.',metadata=dict(models=len(models),source=dataset['metadata'].get('source','unknown'))))
    except BaseException:
        for model in models:(ml.DATA/'models'/f'{model["model_id"]}.joblib').unlink(missing_ok=True)
        raise


def export_csv(dataset):
    out=io.StringIO();fields=['run_id','time','window_seconds','entity_id','collector','zone','label','schema_id','feature_contract','topology_id','fault_types','fault_intervals',*dataset['schema']['features']]
    writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader()
    for row in dataset['rows']:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items() if k in fields})
    return out.getvalue()


def import_data(text):
    if len(text)>50_000_000:raise ValueError('Слишком большой файл')
    data=json.loads(text)
    if len(data.get('rows',[]))>300000:raise ValueError('Лимит 300 000 строк')
    ml.input_matrix(data['rows'],'monitoring:'+data['schema']['id'],data['schema'])
    for r in data['rows']:
        if r.get('label') not in (0,1) or not isinstance(r.get('run_id'),str) or not r['run_id'] or not isinstance(r.get('entity_id'),str):raise ValueError('Нужны run_id, entity_id и бинарная разметка')
        if not np.isfinite([r['time'],r['window_seconds']]).all() or r['time']<0 or r['window_seconds']<=0:raise ValueError('Некорректное окно')
    # Assert temporal identity and non-overlap before fitting or saving anything.
    from .features import operational_metrics
    operational_metrics([dict(r,run_id=r['run_id']+'/'+r['entity_id']) for r in data['rows']],[0.]*len(data['rows']),1.)
    return storage.save('monitor_dataset',str(data.get('name','Импорт мониторинга'))[:100],dict(schema=data['schema'],rows=data['rows'],metadata=dict(source='imported',runs=len({r['run_id'] for r in data['rows']}),rows=len(data['rows']),positives=sum(r['label'] for r in data['rows']))))
