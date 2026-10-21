"""Reproducible offline calibration against independent measured experiments."""
import csv
import io
import math
import json
import hashlib
import re
from datetime import datetime
from copy import deepcopy
import numpy as np
from .schema import Project
from .simulator import simulate

METRICS = ('delivery_ratio','latency_mean','latency_p95','latency_p99','throughput','retry_ratio','recovery_ms')
TEMPLATE = 'run_id,offered_rate,delivery_ratio,latency_mean,latency_p95,latency_p99,throughput,retry_ratio,recovery_ms,latency_samples_ms,scenario,incidents_json,source_kind,measurement_id,device,firmware,measured_at,raw_sha256\n'


def observations(text,min_groups=4,max_rows=24):
    if len(text.encode('utf-8'))>5_000_000: raise ValueError('Лимит измерений: 5 МБ')
    csv.field_size_limit(max(csv.field_size_limit(),5_000_000))
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if not {'run_id','offered_rate'}.issubset(reader.fieldnames or []): raise ValueError('CSV должен содержать run_id и offered_rate')
    rows=[]
    for i,r in enumerate(reader):
        if i>=max_rows: raise ValueError(f'Лимит измерений: {max_rows} опытов')
        try:
            row=dict(run_id=r['run_id'].strip(),offered_rate=float(r['offered_rate']))
            if not row['run_id'] or len(row['run_id'])>100 or not 0<row['offered_rate']<=3000: raise ValueError()
            for key in METRICS:
                if r.get(key,'').strip():
                    value=float(r[key])
                    if not math.isfinite(value) or value<0 or (key=='delivery_ratio' and value>1): raise ValueError()
                    row[key]=value
            if not any(k in row for k in METRICS): raise ValueError()
            if r.get('latency_samples_ms','').strip():
                samples=[float(x) for x in r['latency_samples_ms'].split('|')]
                if len(samples)<20 or len(samples)>20000 or not all(math.isfinite(x) and x>=0 for x in samples): raise ValueError()
                row['latency_samples_ms']=samples
            if r.get('scenario','').strip():row['scenario']=r['scenario'].strip()[:100]
            if r.get('incidents_json','').strip():
                from .schema import Incident
                incidents=json.loads(r['incidents_json'])
                if not isinstance(incidents,list) or len(incidents)>30:raise ValueError()
                row['incidents']=[Incident.model_validate(incident).model_dump() for incident in incidents]
            source=(r.get('source_kind') or 'unknown').strip()
            if source not in ('unknown','synthetic','lab'):raise ValueError()
            evidence={key:(r.get(key) or '').strip() for key in ('measurement_id','device','firmware','measured_at','raw_sha256')}
            if any(len(value)>200 for value in evidence.values()):raise ValueError()
            if source=='lab':
                if not all(evidence.values()) or not re.fullmatch('[0-9a-fA-F]{64}',evidence['raw_sha256']):raise ValueError()
                if datetime.fromisoformat(evidence['measured_at'].replace('Z','+00:00')).tzinfo is None:raise ValueError()
                identity='|'.join(evidence[k] for k in ('device','firmware','measurement_id'))
                row['independence_group']='lab:'+hashlib.sha256(identity.encode()).hexdigest()[:20]
            else:row['independence_group']=row['run_id']
            row['evidence']=dict(source_kind=source,**evidence)
            rows.append(row)
        except (ValueError,KeyError,AttributeError): raise ValueError(f'Некорректный опыт в строке {i+2}')
    if len({r['independence_group'] for r in rows})<min_groups: raise ValueError('Нужно минимум четыре независимых run_id / measurement_id' if min_groups==4 else f'Нужно минимум {min_groups} независимых run_id / measurement_id')
    for rid in {r['run_id'] for r in rows}:
        if len({r['independence_group'] for r in rows if r['run_id']==rid})!=1:raise ValueError('Один run_id не может относиться к разным источникам измерений')
    return rows


def evidence_report(text,rows,train_ids,test_ids):
    sources=sorted({r['evidence']['source_kind'] for r in rows})
    return dict(input_csv_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(),sources=sources,status='declared_lab_holdout' if sources==['lab'] else 'synthetic_or_unverified',raw_artifacts_verified=False,hardware_accuracy_proven=False,independent_groups=len({r['independence_group'] for r in rows}),observations=[dict(run_id=r['run_id'],group_id=r['independence_group'],split='train' if r['independence_group'] in train_ids else 'test',**r['evidence']) for r in rows],interpretation='Metadata is declared by the researcher. File hashes identify supplied evidence; they do not establish hardware accuracy or independence by themselves.')


def transformed(project, scales):
    d=project.model_dump()
    for n in d['nodes']:
        n['processing_ms']=min(1000,n['processing_ms']*scales['processing'])
        n['database_ms']=min(1000,n['database_ms']*scales['processing'])
        n['recovery_ms']=min(30000,n['recovery_ms']*scales['recovery'])
    for l in d['links']:
        l['latency_ms']=min(5000,l['latency_ms']*scales['latency'])
        if l['reverse_latency_ms'] is not None:l['reverse_latency_ms']=min(5000,l['reverse_latency_ms']*scales['latency'])
        l['loss']=min(1,l['loss']*scales['loss'])
        if l['reverse_loss'] is not None:l['reverse_loss']=min(1,l['reverse_loss']*scales['loss'])
        l['recovery_ms']=min(30000,l['recovery_ms']*scales['recovery'])
    for e in d['incidents']:
        if e['recovery_ms'] is not None:e['recovery_ms']=min(30000,e['recovery_ms']*scales['recovery'])
    return Project.model_validate(d)


def measure(result):
    s=result['summary']
    recovery=[e['first_success_after_end']*1000 for e in result.get('incidents',[]) if e.get('first_success_after_end') is not None]
    return dict(**{k:s[k] for k in METRICS[:5]},retry_ratio=s.get('transport_retries',0)/max(1,sum(x['packets']-x['acks'] for x in result['links'].values())),recovery_ms=float(np.mean(recovery)) if recovery else None)


def sample_distance(observed, simulated):
    if not simulated:return dict(ks=None,wasserstein_ms=None,observed_n=len(observed),simulated_n=0)
    a,b=np.sort(observed),np.sort(simulated); support=np.unique(np.r_[a,b])
    ks=float(np.max(np.abs(np.searchsorted(a,support,side='right')/len(a)-np.searchsorted(b,support,side='right')/len(b))))
    points=np.linspace(0,1,1001)
    return dict(ks=ks,wasserstein_ms=float(np.trapezoid(np.abs(np.quantile(a,points)-np.quantile(b,points)),points)),observed_n=len(a),simulated_n=len(b))


def calibrate(project, text, progress=None, cancel=None, options=None):
    from .calibration_search import calibrate as search
    return search(project,text,progress,cancel,options)
