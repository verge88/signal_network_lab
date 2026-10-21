"""Frozen-policy comparisons, without fitting or extrapolating equipment accuracy."""
import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from concurrent.futures import CancelledError
from datetime import datetime,timezone
from html import escape
from typing import Literal
import numpy as np
from pydantic import BaseModel,Field,model_validator,ConfigDict
from scipy.stats import wasserstein_distance
from .schema import Project
from .simulator import simulate
from .calibration import observations,measure,METRICS,TEMPLATE as CALIBRATION_TEMPLATE

VERSION='validation-v1'
TEMPLATE=CALIBRATION_TEMPLATE.rstrip('\n')+',duration_s,window_s,evaluation_role,time_series_json\n'
LABELS=dict(delivery_ratio='Доля успешных процедур',latency_mean='Средняя задержка, ms',
    latency_p95='Задержка p95, ms',latency_p99='Задержка p99, ms',throughput='Завершения / s',
    retry_ratio='Доля повторов',recovery_ms='Восстановление, ms',distribution_ks='KS-расстояние',
    wasserstein_ms='Wasserstein, ms')
STATUS=dict(passed='В допуске',failed='Вне допуска',insufficient='Недостаточно данных')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


class Tolerance(BaseModel):
    model_config=ConfigDict(extra='forbid')
    absolute:float=Field(default=0,ge=0,allow_inf_nan=False)
    relative:float=Field(default=0,ge=0,le=10,allow_inf_nan=False)


def default_tolerances():
    return {key:Tolerance(absolute=absolute,relative=relative) for key,absolute,relative in (
        ('delivery_ratio',.01,0),('latency_mean',2,.1),('latency_p95',5,.1),
        ('latency_p99',10,.15),('throughput',1,.05),('retry_ratio',.01,0),('recovery_ms',25,.15))}


class Condition(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scenario:str=Field(min_length=1,max_length=100)
    offered_rate:float=Field(gt=0,le=3000,allow_inf_nan=False)


class Options(BaseModel):
    model_config=ConfigDict(extra='forbid')
    seeds:int=Field(default=3,ge=1,le=10)
    min_runs:int=Field(default=3,ge=2,le=20)
    min_samples:int=Field(default=50,ge=20,le=20000)
    require_distribution:bool=True
    require_time_series:bool=True
    required_metrics:list[str]=Field(default_factory=lambda:['delivery_ratio','latency_p95','throughput'],min_length=1,max_length=7)
    tolerances:dict[str,Tolerance]=Field(default_factory=default_tolerances)
    max_ks:float=Field(default=.15,ge=0,le=1,allow_inf_nan=False)
    max_wasserstein_ms:float=Field(default=5,ge=0,allow_inf_nan=False)
    expected_conditions:list[Condition]=Field(default_factory=list,max_length=100)
    @model_validator(mode='after')
    def valid(self):
        if set(self.tolerances)!=set(METRICS) or not set(self.required_metrics)<=set(METRICS):raise ValueError('Неизвестная или пропущенная метрика допуска')
        if len(set(self.required_metrics))!=len(self.required_metrics):raise ValueError('Обязательные метрики не должны повторяться')
        keys=[(c.scenario,c.offered_rate) for c in self.expected_conditions]
        if len(set(keys))!=len(keys):raise ValueError('Проверяемые условия не должны повторяться')
        return self


def parse(text):
    rows=observations(text,min_groups=1,max_rows=100)
    raw=list(csv.DictReader(io.StringIO(text.lstrip('\ufeff'))))
    for index,(row,source) in enumerate(zip(rows,raw)):
        try:
            row['scenario']=row.get('scenario','project')
            row['evaluation_role']=(source.get('evaluation_role') or 'unknown').strip()
            if row['evaluation_role'] not in ('validation','calibration','unknown'):raise ValueError()
            for key in ('duration_s','window_s'):
                if source.get(key,'').strip():
                    value=float(source[key])
                    if not math.isfinite(value) or value<=0:raise ValueError()
                    row[key]=value
            points=json.loads(source.get('time_series_json') or '[]')
            if not isinstance(points,list) or len(points)>1200:raise ValueError()
            times=set();series=[]
            for point in points:
                if not isinstance(point,dict) or not {'time','width_s'}<=point.keys():raise ValueError()
                clean={key:float(value) for key,value in point.items() if key in (*METRICS,'time','width_s')}
                if not any(key in clean for key in METRICS):raise ValueError()
                if not all(math.isfinite(v) and v>=0 for v in clean.values()) or clean['width_s']<=0 or clean['time'] in times:raise ValueError()
                if any(clean.get(key,0)>1 for key in ('delivery_ratio','retry_ratio')):raise ValueError()
                times.add(clean['time']);series.append(clean)
            row['time_series']=sorted(series,key=lambda point:point['time'])
        except (ValueError,TypeError,KeyError,AttributeError):raise ValueError(f'Некорректный временной ряд или условие в строке {index+2}')
    return rows


def configured(project,row,seed):
    data=project.model_dump();base=sum(flow['rate'] for flow in data['flows'])
    if base<=0:raise ValueError('Задайте ненулевую базовую нагрузку')
    for flow in data['flows']:flow['rate']*=row['offered_rate']/base
    if 'incidents' in row:data['incidents']=row['incidents'];data['attacks']=[]
    if 'duration_s' in row:
        if not row['duration_s'].is_integer():raise ValueError('Длительность опыта должна быть целым числом секунд')
        data['duration']=int(row['duration_s'])
    if 'window_s' in row:data['window']=row['window_s']
    data['seed']=seed
    return Project.model_validate(data)


def ecdf(samples):
    if not samples:return []
    values=np.sort(samples);support=np.unique(values)
    if len(support)>400:support=support[np.unique(np.linspace(0,len(support)-1,400,dtype=int))]
    points=[];previous=0.
    for x in support:
        cumulative=float(np.searchsorted(values,x,side='right')/len(values))
        points.extend([[float(x),previous],[float(x),cumulative]]);previous=cumulative
    return points


def window_measurements(result,project):
    rows=[]
    for window in result['windows']:
        point=dict(time=window['time'],width_s=min(project.window,project.duration-window['time']))
        for metric in METRICS:
            value=window.get('completion_rate' if metric=='throughput' else metric)
            if metric.startswith('latency_') and not window.get('delivered'):continue
            if value is not None:point[metric]=value
        rows.append(point)
    return rows


def distribution(observed,predicted,options):
    result=dict(observed_n=len(observed),simulated_n=len(predicted),observed_ecdf=ecdf(observed),simulated_ecdf=ecdf(predicted),ks=None,wasserstein_ms=None,status='insufficient')
    if not observed or not predicted:return result
    a,b=np.sort(observed),np.sort(predicted);support=np.unique(np.r_[a,b])
    result['ks']=float(np.max(np.abs(np.searchsorted(a,support,side='right')/len(a)-np.searchsorted(b,support,side='right')/len(b))))
    result['wasserstein_ms']=float(wasserstein_distance(a,b))
    if min(len(a),len(b))>=options.min_samples:
        result['status']='passed' if result['ks']<=options.max_ks and result['wasserstein_ms']<=options.max_wasserstein_ms else 'failed'
    return result


def error(metric,observed,predicted,options,scope='aggregate',time=None):
    tolerance=options.tolerances[metric];allowed=max(tolerance.absolute,tolerance.relative*abs(observed))
    absolute=abs(predicted-observed) if predicted is not None else None
    return dict(scope=scope,metric=metric,time=time,observed=observed,predicted=predicted,
        absolute_error=absolute,relative_error=absolute/abs(observed) if observed and absolute is not None else None,
        allowed=allowed,status='insufficient' if absolute is None else 'passed' if absolute<=allowed+1e-12 else 'failed')


def compare_series(observed,windows,project,options):
    predicted=[];comparisons=[];missing=[]
    for point in observed:
        time=point['time'];width=point['width_s']
        matches=[[w for w in seed_windows if abs(w['time']-time)<1e-8] for seed_windows in windows]
        expected_width=min(project.window,project.duration-time)
        if time>=project.duration or expected_width<=0 or abs(width-expected_width)>1e-8 or any(len(group)!=1 for group in matches):
            missing.append(time);continue
        estimate=dict(time=time,width_s=width)
        for metric in METRICS:
            if metric not in point:continue
            values=[group[0][metric] for group in matches if metric in group[0]]
            value=float(np.mean(values)) if len(values)==len(windows) else None
            estimate[metric]=value;comparisons.append(error(metric,point[metric],value,options,'window',time))
        predicted.append(estimate)
    complete=bool(observed) and not missing and len(observed)==math.ceil(project.duration/project.window)
    # Missing metrics are not filled with zero or borrowed from another window.
    complete=complete and all(all(key in point and key in predicted[i] and predicted[i][key] is not None for key in options.required_metrics) for i,point in enumerate(observed))
    return dict(observed=observed,predicted=predicted,missing_times=missing,complete=complete,errors=comparisons)


def validate(project,text,progress=None,cancel=None,options=None,calibration_groups=(),calibration_hashes=()):
    p=Project.model_validate(project.model_dump() if isinstance(project,Project) else project);policy=Options.model_validate(options or {})
    if p.engine!='procedures':raise ValueError('Проверка достоверности требует движок процедур')
    rows=parse(text);prepared=[configured(p,row,p.seed) for row in rows]
    if sum(row['offered_rate']*config.duration*policy.seeds for row,config in zip(rows,prepared))>2_000_000:raise ValueError('Снизьте число опытов, seed, длительность или нагрузку: лимит 2 млн базовых процедур')
    def checkpoint():
        if cancel and cancel():raise CancelledError('Проверка достоверности отменена')
    checkpoint();frozen=policy.model_dump();experiments=[];count=0;total=len(rows)*policy.seeds
    # Either the same experiment identity OR the same raw file links rows into
    # one unit. Renaming run_id/measurement_id must not create more evidence.
    parents={}
    def root(key):
        parents.setdefault(key,key)
        if parents[key]!=key:parents[key]=root(parents[key])
        return parents[key]
    def tokens(row):
        values=[row['independence_group']]
        if row['evidence']['source_kind']=='lab':values.append('raw:'+row['evidence']['raw_sha256'].lower())
        return values
    for row in rows:
        keys=tokens(row)
        for key in keys[1:]:
            a,b=sorted((root(keys[0]),root(key)));parents[b]=a
    blocked=set();known=set(calibration_groups)|{'raw:'+value.lower() for value in calibration_hashes}
    for row in rows:
        if set(tokens(row))&known or row['evaluation_role']=='calibration':blocked.add(root(row['independence_group']))
    for index,(row,base) in enumerate(zip(rows,prepared)):
        checkpoint();summaries=[];samples=[];windows=[];seeds=[]
        for repeat in range(policy.seeds):
            checkpoint();seed=(p.seed+int.from_bytes(hashlib.sha256(row['run_id'].encode()).digest()[:4],'big')+1009*repeat)%2147483647
            config=configured(p,row,seed);result=simulate(config,cancel=cancel);summaries.append(measure(result));windows.append(window_measurements(result,config));seeds.append(seed)
            samples.extend(trace['latency_ms'] for trace in result['traces'] if trace['status']=='delivered')
            count+=1
            if progress:progress(int(count/total*95))
        predictions={key:float(np.mean(values)) if len(values:=[summary[key] for summary in summaries if summary[key] is not None])==policy.seeds else None for key in METRICS}
        errors=[error(key,row[key],predictions[key],policy) for key in METRICS if key in row]
        dist=distribution(row.get('latency_samples_ms',[]),samples,policy)
        series=compare_series(row['time_series'],windows,base,policy)
        missing=[LABELS[key] for key in policy.required_metrics if key not in row or predictions[key] is None]
        if policy.require_distribution and dist['status']=='insufficient':missing.append('Выборка задержек')
        if policy.require_time_series and not series['complete']:missing.append('Полный временной ряд с согласованными окнами')
        group=root(row['independence_group']);overlap=group in blocked
        independent=row['evaluation_role']=='validation' and not overlap
        if not independent:missing.append('Независимый проверочный опыт' if not overlap else 'Измерения уже использованы в калибровке')
        statuses=[value['status'] for value in errors]+[value['status'] for value in series['errors']]
        if policy.require_distribution or dist['status']=='failed':statuses.append(dist['status'])
        status='failed' if 'failed' in statuses else 'insufficient' if missing or 'insufficient' in statuses else 'passed'
        condition=base.model_dump();condition.pop('seed');condition_hash=digest(condition)
        experiments.append(dict(index=index,run_id=row['run_id'],group_id=group,independent=independent,scenario=row['scenario'],offered_rate=row['offered_rate'],
            condition_sha256=condition_hash,source=row['evidence'],evaluation_role=row['evaluation_role'],duration=base.duration,window=base.window,
            incidents=condition['incidents'],attacks=condition['attacks'],seeds=seeds,predicted=predictions,seed_predictions=summaries,
            errors=errors,distribution=dist,time_series=series,missing=missing,status=status))
    grouped=defaultdict(list)
    for experiment in experiments:grouped[(experiment['scenario'],experiment['offered_rate'],experiment['condition_sha256'])].append(experiment)
    conditions=[]
    for (scenario,rate,config_hash),items in grouped.items():
        groups={item['group_id'] for item in items if item['independent']};missing=sorted({reason for item in items for reason in item['missing']})
        if len(groups)<policy.min_runs:missing.append(f'Независимых опытов: {len(groups)}; требуется {policy.min_runs}')
        status='failed' if any(item['status']=='failed' for item in items) else 'insufficient' if missing else 'passed'
        sources=sorted({item['source']['source_kind'] for item in items})
        conditions.append(dict(scenario=scenario,offered_rate=rate,condition_sha256=config_hash,experiments=[item['index'] for item in items],independent_runs=len(groups),sources=sources,status=status,missing=missing,
            support='declared_lab_data' if status=='passed' and sources==['lab'] else 'synthetic_only' if sources==['synthetic'] else 'unverified'))
    for expected in policy.expected_conditions:
        if not any(c['scenario']==expected.scenario and c['offered_rate']==expected.offered_rate for c in conditions):
            conditions.append(dict(**expected.model_dump(),condition_sha256=None,experiments=[],independent_runs=0,sources=[],status='insufficient',missing=['Измерения этого условия отсутствуют'],support='unverified'))
    checkpoint()
    report=dict(version=VERSION,generated_at=datetime.now(timezone.utc).isoformat(),project=p.model_dump(),config_sha256=digest(p.model_dump()),
        input_csv_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(),policy=frozen,policy_sha256=digest(frozen),
        simulations=count,experiments=experiments,conditions=conditions,
        summary=dict(conditions=len(conditions),**{key:sum(c['status']==key for c in conditions) for key in STATUS}),
        hardware_accuracy_proven=False,raw_artifacts_verified=False,
        limitations=['Выводы относятся только к перечисленным нагрузкам, сценариям, конфигурациям и длительности. Интерполяция и экстраполяция не выполняются.',
            'Источник и независимость измерений заявляет исследователь. Исходные файлы оборудования не проверены; синтетический пример не доказывает внешнюю достоверность.',
            'Для ошибок проверяется каждый опыт, seed усредняются. Объединение опытов не скрывает отдельные выходы за допуск.',
            'ECDF и расстояния используют равномерную выборку успешных завершённых процедур симулятора. Число точек отображения ограничено 400; расстояния считают по всей предоставленной выборке.',
            'Совпадение с допусками не является статистическим доказательством эквивалентности или соответствия протокольным стандартам.'])
    if progress:progress(100)
    return report


def synthetic_example(project,progress=None,cancel=None):
    p=Project.model_validate(project.model_dump() if isinstance(project,Project) else project);rows=[]
    if p.engine!='procedures':raise ValueError('Пример требует движок процедур')
    if sum(f.rate for f in p.flows)<=0:raise ValueError('Задайте ненулевую базовую нагрузку')
    if sum(f.rate for f in p.flows)*p.duration*3>500000:raise ValueError('Уменьшите нагрузку или длительность для демонстрационного примера')
    for index in range(3):
        if cancel and cancel():raise CancelledError('Создание примера отменено')
        config=p.model_copy(deep=True);config.seed=(p.seed+7919*(index+1))%2147483647
        result=simulate(config,cancel=cancel);samples=[trace['latency_ms'] for trace in result['traces'] if trace['status']=='delivered']
        rows.append(dict(run_id=f'synthetic-{index+1}',offered_rate=sum(f.rate for f in p.flows),scenario='project',source_kind='synthetic',evaluation_role='validation',
            duration_s=p.duration,window_s=p.window,**{key:value for key,value in measure(result).items() if value is not None},
            latency_samples_ms='|'.join(str(value) for value in samples) if len(samples)>=20 else '',
            time_series_json=json.dumps(window_measurements(result,p)),
            incidents_json=json.dumps([incident.model_dump() for incident in p.incidents])))
        if progress:progress(round((index+1)/3*100))
    stream=io.StringIO();fields=TEMPLATE.strip().split(',');writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    return dict(text=stream.getvalue(),rows=3,source='synthetic',config_sha256=digest(p.model_dump()))


def chart_svg(series,title,x_label,y_label):
    points=[point for item in series for point in item['points']]
    if not points:return ''
    xmin=min(x for x,y in points);xmax=max(x for x,y in points);ymin=min(0,min(y for x,y in points));ymax=max(y for x,y in points)
    if xmax==xmin:xmin-=.5;xmax+=.5
    if ymax==ymin:ymax=ymin+1
    px=lambda x:70+(x-xmin)/(xmax-xmin)*650
    py=lambda y:225-(y-ymin)/(ymax-ymin)*170
    parts=['<svg xmlns="http://www.w3.org/2000/svg" role="img" viewBox="0 0 780 280"><title>'+escape(title)+'</title><rect width="780" height="280" fill="white"/><g font-family="sans-serif" font-size="12" fill="#222"><text x="70" y="20">'+escape(title)+'</text>']
    for index in range(5):
        y=ymin+(ymax-ymin)*index/4;x=xmin+(xmax-xmin)*index/4
        parts.append(f'<path d="M70 {py(y):.3f} H720" fill="none" stroke="#ddd"/><text x="62" y="{py(y)+4:.3f}" text-anchor="end">{y:.4g}</text><text x="{px(x):.3f}" y="244" text-anchor="middle">{x:.4g}</text>')
    for index,item in enumerate(series):
        color=('#0072b2','#d55e00')[index%2];dash=' stroke-dasharray="7 4"' if index%2 else ''
        coords=' '.join(f'{px(x):.3f},{py(y):.3f}' for x,y in item['points'])
        parts.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2"{dash}/><text x="{380+index*160}" y="38" fill="{color}">'+escape(item['name'])+'</text>')
    parts.append('<text x="395" y="271" text-anchor="middle">'+escape(x_label)+'</text><text transform="translate(16 140) rotate(-90)" text-anchor="middle">'+escape(y_label)+'</text></g></svg>')
    return ''.join(parts)


def report_html(report):
    def cell(value):return escape('—' if value is None else 'да' if value is True else 'нет' if value is False else f'{value:.6g}' if isinstance(value,float) else str(value))
    def table(headers,rows):return '<table><tr>'+''.join('<th>'+cell(x)+'</th>' for x in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+cell(x)+'</td>' for x in row)+'</tr>' for row in rows)+'</table>'
    conditions=table(['Сценарий','Нагрузка, msg/s','Независимых опытов','Источник','Результат','Недостающие данные'],
        [[c['scenario'],c['offered_rate'],c['independent_runs'],', '.join(c['sources']),STATUS[c['status']],'; '.join(c['missing'])] for c in report['conditions']])
    details=[]
    for experiment in report['experiments']:
        details.append('<h2>'+cell(experiment['run_id'])+' · '+cell(experiment['scenario'])+'</h2>')
        details.append(table(['Условие','Значение'],[['Нагрузка, msg/s',experiment['offered_rate']],['Длительность / окно, s',str(experiment['duration'])+' / '+str(experiment['window'])],['SHA-256 условия',experiment['condition_sha256']],['Источник',experiment['source']['source_kind']],['Назначение опыта',experiment['evaluation_role']],['Независимая группа',experiment['group_id']],['Устройство / прошивка',experiment['source'].get('device','')+' / '+experiment['source'].get('firmware','')],['Измерение / время',experiment['source'].get('measurement_id','')+' / '+experiment['source'].get('measured_at','')],['SHA-256 исходного файла',experiment['source'].get('raw_sha256','')],['Seed симуляции',', '.join(str(seed) for seed in experiment['seeds'])]]))
        details.append(table(['Область / время, s','Метрика','Измерение','Симулятор','Абс. ошибка','Допуск','Результат'],
            [[x['scope']+(' / '+str(x['time']) if x['time'] is not None else ''),LABELS[x['metric']],x['observed'],x['predicted'],x['absolute_error'],x['allowed'],STATUS[x['status']]] for x in experiment['errors']]))
        window_errors=experiment['time_series']['errors'];details.append('<p>Сопоставлено оконных метрик: '+cell(len(window_errors))+'; вне допуска: '+cell(sum(x['status']=='failed' for x in window_errors))+'; сопоставимость полного ряда: '+cell(experiment['time_series']['complete'])+'. Подробные ошибки каждого окна сохранены в JSON.</p>')
        dist=experiment['distribution'];details.append('<p>KS: '+cell(dist['ks'])+'; Wasserstein, ms: '+cell(dist['wasserstein_ms'])+'; N измерений / симуляции: '+cell(dist['observed_n'])+' / '+cell(dist['simulated_n'])+'</p>')
        details.append(chart_svg([dict(name='Измерения',points=dist['observed_ecdf']),dict(name='Симулятор',points=dist['simulated_ecdf'])],'Распределение задержек','Задержка, ms','Накопленная доля'))
        series=experiment['time_series']
        for metric in report['policy']['required_metrics']:
            details.append(chart_svg([dict(name='Измерения',points=[(point['time'],point[metric]) for point in series['observed'] if metric in point]),dict(name='Симулятор',points=[(point['time'],point[metric]) for point in series['predicted'] if point.get(metric) is not None])],LABELS[metric],'Время, s',LABELS[metric]))
    policy=report['policy'];plan=table(['Метрика','Абсолютный допуск','Относительный допуск, %','Обязательна'],[[LABELS[key],spec['absolute'],spec['relative']*100,key in policy['required_metrics']] for key,spec in policy['tolerances'].items()])
    plan+='<p>Seed на опыт: '+cell(policy['seeds'])+'; независимых опытов на условие: '+cell(policy['min_runs'])+'; минимум задержек: '+cell(policy['min_samples'])+'; KS ≤ '+cell(policy['max_ks'])+'; Wasserstein ≤ '+cell(policy['max_wasserstein_ms'])+' ms. Распределение обязательно: '+cell(policy['require_distribution'])+'; временной ряд обязателен: '+cell(policy['require_time_series'])+'.</p>'
    return '<!doctype html><html lang="ru"><meta charset="utf-8"><title>Проверка достоверности модели</title><style>body{font:14px sans-serif;color:#222;background:#fff;margin:28px}table{border-collapse:collapse;width:100%;margin:12px 0}th,td{border:1px solid #ccc;padding:6px;text-align:left;overflow-wrap:anywhere}th{background:#eee}svg{max-width:100%;height:auto}code{overflow-wrap:anywhere}</style><body><h1>Проверка достоверности модели</h1><p>Внешняя точность по оборудованию не доказана. Подлинность исходных артефактов не проверена.</p><p>Конфигурация: <code>'+cell(report['config_sha256'])+'</code><br>План допусков: <code>'+cell(report['policy_sha256'])+'</code><br>Измерения CSV: <code>'+cell(report['input_csv_sha256'])+'</code></p>'+conditions+''.join(details)+'<h2>Заданный план проверки</h2>'+plan+'<h2>Границы выводов</h2><ul>'+''.join('<li>'+cell(text)+'</li>' for text in report['limitations'])+'</ul></body></html>'
