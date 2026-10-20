"""Bounded, multi-pass parameter search with independent grouped evaluation."""
import hashlib
import json
import math
from copy import deepcopy
from concurrent.futures import CancelledError
import numpy as np
from pydantic import BaseModel,Field,model_validator
from .schema import Project
from .simulator import simulate
from .calibration import observations,transformed,measure,sample_distance,METRICS,evidence_report


NODE_FIELDS={'processing_ms','database_ms','capacity','workers','error_rate','cache_hit_ratio','detection_ms','recovery_ms','advertisement_delay_ms','observer_detection_ms','db_connections','db_queue_limit','db_timeout_ms','cache_entries','cache_ttl_s','context_limit','context_ttl_s','upf_rate_mbps'}
LINK_FIELDS={'latency_ms','reverse_latency_ms','loss','reverse_loss','bandwidth_mbps','jitter_ms','recovery_ms','detection_ms','heartbeat_ms','control_timeout_ms'}
GLOBALS=('latency','processing','loss','recovery')


class Parameter(BaseModel):
    path:str=Field(max_length=150)
    lower:float=Field(allow_inf_nan=False)
    upper:float=Field(allow_inf_nan=False)
    logarithmic:bool=False
    @model_validator(mode='after')
    def bounds(self):
        if self.lower>=self.upper or (self.logarithmic and self.lower<=0):raise ValueError('Неверные границы параметра')
        return self


class Options(BaseModel):
    parameters:list[Parameter]=Field(default_factory=list,max_length=16)
    passes:int=Field(default=4,ge=1,le=12)
    max_evaluations:int=Field(default=40,ge=5,le=200)
    seeds:int=Field(default=2,ge=1,le=5)
    test_fraction:float=Field(default=.3,ge=.2,le=.5)
    metric_weights:dict[str,float]=Field(default_factory=dict)
    @model_validator(mode='after')
    def valid(self):
        if len({p.path for p in self.parameters})!=len(self.parameters):raise ValueError('Параметры не должны повторяться')
        if any(k not in METRICS or not math.isfinite(v) or v<=0 for k,v in self.metric_weights.items()):raise ValueError('Некорректные веса метрик')
        return self


def configuration(project,values):
    scales={k:values.get('global:'+k,1.) for k in GLOBALS};data=transformed(project,scales).model_dump()
    for path,value in values.items():
        parts=path.split(':')
        if parts[0]=='global':continue
        group,nid,key=parts
        item=next(x for x in data['nodes' if group=='node' else 'links'] if x['id']==nid)
        item[key]=int(round(value)) if key in ('workers','db_connections','db_queue_limit','cache_entries','context_limit') else value
    return Project.model_validate(data)


def calibrate(project,text,progress=None,cancel=None,options=None):
    p=Project.model_validate(project.model_dump() if isinstance(project,Project) else project)
    settings=Options.model_validate(options or {});rows=observations(text)
    if p.engine!='procedures':raise ValueError('Калибровка требует режим процедур')
    if sum(f.rate for f in p.flows)<=0:raise ValueError('Задайте ненулевую базовую нагрузку')
    parameters=settings.parameters or [Parameter(path='global:'+key,lower=.25,upper=4,logarithmic=True) for key in GLOBALS]
    baseline={};spaces={}
    for spec in parameters:
        parts=spec.path.split(':')
        if len(parts)==2 and parts[0]=='global' and parts[1] in GLOBALS:value=1.
        elif len(parts)==3 and parts[0] in ('node','link'):
            group,nid,key=parts;allowed=NODE_FIELDS if group=='node' else LINK_FIELDS
            if key not in allowed:raise ValueError('Неподдерживаемый параметр: '+spec.path)
            item=next((item for item in (p.nodes if group=='node' else p.links) if item.id==nid),None)
            if item is None:raise ValueError('Не найден объект: '+nid)
            value=getattr(item,key)
            if value is None:value=getattr(item,{'reverse_loss':'loss','reverse_latency_ms':'latency_ms','observer_detection_ms':'detection_ms'}[key])
        else:raise ValueError('Некорректный путь параметра: '+spec.path)
        baseline[spec.path]=float(value);spaces[spec.path]=spec
        for bound in (spec.lower,spec.upper):configuration(p,{**baseline,spec.path:bound})
    groups=sorted({row['independence_group'] for row in rows});rng=np.random.default_rng(p.seed);rng.shuffle(groups)
    count=max(2,min(len(groups)-2,round(len(groups)*(1-settings.test_fraction))))
    train_ids,test_ids=groups[:count],groups[count:];train=[i for i,row in enumerate(rows) if row['independence_group'] in train_ids];test=[i for i,row in enumerate(rows) if row['independence_group'] in test_ids]
    estimated=(settings.max_evaluations*sum(rows[i]['offered_rate'] for i in train)+2*sum(rows[i]['offered_rate'] for i in test))*p.duration*settings.seeds
    if estimated>2_000_000:raise ValueError('Снизьте бюджет поиска, число seed, длительность или нагрузку: лимит 2 млн базовых процедур')
    cache={};search=[];calls=0;total=max(1,(settings.max_evaluations*len(train)+2*len(test))*settings.seeds)
    def checkpoint():
        if cancel and cancel():raise CancelledError('Калибровка отменена')
    def evaluate(values,indices):
        nonlocal calls
        outputs=[]
        key_values=tuple(sorted((key,round(value,12)) for key,value in values.items()))
        for index in indices:
            row=rows[index];repetitions=[];samples=[]
            for repeat in range(settings.seeds):
                checkpoint();key=(key_values,index,repeat)
                if key not in cache:
                    config=configuration(p,values).model_dump();config['seed']=(p.seed+int.from_bytes(hashlib.sha256(row['run_id'].encode()).digest()[:4],'big')+1009*repeat)%2147483647
                    base=sum(f['rate'] for f in config['flows'])
                    for flow in config['flows']:flow['rate']*=row['offered_rate']/base
                    if 'incidents' in row:config['incidents']=row['incidents'];config['attacks']=[]
                    result=simulate(Project.model_validate(config),cancel=cancel)
                    cache[key]=(measure(result),[r['latency_ms'] for r in result['traces'] if r['status']=='delivered'])
                    calls+=1
                    if progress:progress(min(95,int(95*calls/total)))
                predicted,latencies=cache[key];repetitions.append(predicted);samples.extend(latencies)
            predicted={metric:float(np.mean([r[metric] for r in repetitions if r[metric] is not None])) if any(r[metric] is not None for r in repetitions) else None for metric in METRICS}
            outputs.append(dict(run_id=row['run_id'],group_id=row['independence_group'],offered_rate=row['offered_rate'],scenario=row.get('scenario','project'),observed={metric:row[metric] for metric in METRICS if metric in row},predicted=predicted,seed_predictions=deepcopy(repetitions),distribution=sample_distance(row['latency_samples_ms'],samples) if 'latency_samples_ms' in row else None))
        errors={};group_errors={}
        for metric in METRICS:
            values_by_group={}
            for output in outputs:
                if metric not in output['observed']:continue
                actual=output['observed'][metric];prediction=output['predicted'][metric]
                if prediction is None:prediction=p.duration*1000
                scale=.01 if metric=='delivery_ratio' else max(.01,actual) if metric=='retry_ratio' else max(1,actual)
                values_by_group.setdefault(output['group_id'],[]).append(abs(prediction-actual)/scale)
            if values_by_group:
                group_errors[metric]={group:float(np.mean(values)) for group,values in values_by_group.items()};errors[metric]=float(np.mean(list(group_errors[metric].values())))
        weights={metric:settings.metric_weights.get(metric,1.) for metric in errors}
        objective=sum(weights[m]*error**2 for m,error in errors.items())/max(1e-12,sum(weights.values()))
        return dict(objective=objective,normalized_mae=errors,group_errors=group_errors,experiments=outputs)
    initial_train=evaluate(baseline,train)
    best={key:min(spaces[key].upper,max(spaces[key].lower,value)) for key,value in baseline.items()};best_score=evaluate(best,train)
    search.append(dict(parameters=deepcopy(best),objective=best_score['objective'],iteration=0))
    def encode(value,spec):return math.log(value) if spec.logarithmic else value
    def decode(value,spec):return math.exp(value) if spec.logarithmic else value
    for iteration in range(settings.passes):
        for path,spec in spaces.items():
            if len(search)>=settings.max_evaluations:break
            lo,hi=encode(spec.lower,spec),encode(spec.upper,spec);step=(hi-lo)/4/(2**iteration);center=encode(best[path],spec)
            candidates=sorted({max(lo,min(hi,center-step)),max(lo,min(hi,center+step))})
            for point in candidates:
                if len(search)>=settings.max_evaluations:break
                candidate=dict(best);candidate[path]=decode(point,spec)
                if abs(candidate[path]-best[path])<1e-10:continue
                score=evaluate(candidate,train);search.append(dict(parameters=deepcopy(candidate),objective=score['objective'],iteration=iteration+1))
                if score['objective']<best_score['objective']-1e-12:best,best_score=candidate,score
    # Freeze parameters before evaluating independent test groups.
    initial_test=evaluate(baseline,test);final_test=evaluate(best,test);intervals={}
    bootstrap=np.random.default_rng(p.seed^0xCA11B)
    for metric,values in final_test['group_errors'].items():
        observations_=np.array(list(values.values()));means=bootstrap.choice(observations_,size=(1000,len(observations_)),replace=True).mean(axis=1)
        intervals[metric]=dict(lower=float(np.quantile(means,.025)),upper=float(np.quantile(means,.975)),independent_groups=len(observations_))
    checkpoint()
    if progress:progress(100)
    return dict(project=p.model_dump(),calibrated_project=configuration(p,best).model_dump(),
        evidence=evidence_report(text,rows,train_ids,test_ids),
        scales={key:best.get('global:'+key,1.) for key in GLOBALS},parameters=best,options=settings.model_dump(),parameter_bounds=[spec.model_dump() for spec in parameters],split=dict(train=train_ids,test=test_ids,seed=p.seed),before=dict(train=initial_train,test=initial_test),after=dict(train=best_score,test=final_test),search=search,simulations=calls,test_error_ci95=intervals,method='bounded multi-pass coordinate refinement; repeated common seeds; grouped test untouched by search',feature_contract='procedures-v2',coverage=dict(train_load=[min(rows[i]['offered_rate'] for i in train),max(rows[i]['offered_rate'] for i in train)],test_load=[min(rows[i]['offered_rate'] for i in test),max(rows[i]['offered_rate'] for i in test)]),limitations=['Calibration does not prove hardware accuracy or identifiability.','Confidence intervals describe held-out groups supplied by the researcher, not unseen network regimes.','Global multipliers cannot change zero baseline loss; use an absolute link loss parameter.'])
