import uuid
import time
import warnings
import joblib
import numpy as np
import sklearn
from threadpoolctl import threadpool_limits
from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score, average_precision_score, confusion_matrix, roc_curve, precision_recall_curve
from .storage import DATA
from .simulator import FEATURES
from .features import specification, matrix, operational_metrics
from .ml_algorithms import ALGORITHMS,definition,parameters as model_parameters,build,score,normal_threshold,estimator


def metrics(y, score, threshold):
    y=np.asarray(y)
    score=np.asarray(score)
    pred=(score>=threshold).astype(int)
    cm=confusion_matrix(y,pred,labels=[0,1]).tolist()
    both=len(np.unique(y))==2
    return dict(precision=float(precision_score(y,pred,zero_division=0)),recall=float(recall_score(y,pred,zero_division=0)),f1=float(f1_score(y,pred,zero_division=0)),roc_auc=float(roc_auc_score(y,score)) if both else None,pr_auc=float(average_precision_score(y,score)) if both else None,confusion=cm,false_positive_rate=cm[0][1]/max(1,sum(cm[0])),samples=len(y),positives=int(sum(y)))


def scores(model, algorithm, x):
    return score(model,algorithm,x)


def best_threshold(y,values):
    """Exact maximum validation F1, with the highest threshold breaking ties."""
    order=np.argsort(-values,kind='stable');ordered=values[order]
    ends=np.r_[np.flatnonzero(np.diff(ordered)),len(ordered)-1]
    tp=np.cumsum(y[order])[ends];denominator=np.sum(y)+ends+1
    f1=np.divide(2*tp,denominator,out=np.zeros(len(ends),dtype=float),where=denominator>0)
    return float(ordered[ends[int(np.argmax(f1))]])


def threshold_limits(record):
    """Display actual score units, including negative margins and large distances."""
    values=[*record['test_scores'],record['threshold']]
    if record.get('score_kind','probability' if record['algorithm']!='isolation_forest' else 'anomaly')=='probability':return 0.,1.
    lo,hi=float(min(values)),float(max(values));padding=max((hi-lo)*.05,abs(lo)*.01,.001)
    return lo-padding,hi+padding


def input_matrix(rows,feature_set,custom_spec=None,feature_names=None):
    if custom_spec is None:return matrix(rows,feature_set)
    from .monitoring import schema_spec
    from .schema import Monitoring
    # Recompute schema identity from its definitions/semantics, never trust an imported ID.
    semantics=custom_spec['semantics']
    config=Monitoring(features=custom_spec['definitions'],poll_s=semantics['poll'],decision_s=semantics['decision'],ttl_s=semantics['ttl'],history=semantics['history'])
    expected=schema_spec(config)
    if expected!=custom_spec or feature_set!='monitoring:'+expected['id']:raise ValueError('Повреждена схема пользовательских признаков')
    names=feature_names or expected['features']
    if not names or len(set(names))!=len(names) or not set(names)<=set(expected['features']):raise ValueError('Некорректный выбор признаков')
    if any(r.get('schema_id')!=expected['id'] or r.get('feature_contract')!='monitoring-v1' for r in rows):raise ValueError('Данные и схема мониторинга несовместимы')
    try:values=[[float(r[f]) for f in names] for r in rows]
    except (TypeError,KeyError,ValueError) as error:raise ValueError('Отсутствуют пользовательские признаки') from error
    if not rows or not np.isfinite(values).all():raise ValueError('Признаки должны быть конечными числами')
    return values


def train(rows, algorithm, seed=42, threshold=None, trees=120, contamination=0.1, threshold_mode='validation', feature_set='legacy-v1', evaluation_mode='run', holdout_value=None,custom_spec=None,feature_names=None,fixed_split=None,parameters=None):
    meta=definition(algorithm);config=model_parameters(algorithm,parameters,trees)
    if type(seed) is not int or not 0<=seed<=2147483647 or not 0<contamination<=.5:raise ValueError('Некорректные параметры обучения')
    if threshold_mode not in ('validation','contamination') or (threshold_mode=='contamination' and not meta['normal_only']):raise ValueError('Неподдерживаемый метод выбора порога')
    if threshold is not None and not np.isfinite(threshold):raise ValueError('Порог должен быть конечным числом')
    if not rows or any(r.get('label') not in (0,1) for r in rows):raise ValueError('Нужны окна с бинарной разметкой 0 / 1')
    contracts={r.get('feature_contract','messages-v1') for r in rows}
    if len(contracts)!=1:raise ValueError('Нельзя смешивать определения признаков messages-v1 и procedures-v2 в одном датасете')
    contract=next(iter(contracts))
    features=list(feature_names or (custom_spec['features'] if custom_spec else specification(feature_set)['features']))
    x=np.asarray(input_matrix(rows,feature_set,custom_spec,features),dtype=float)
    y=np.asarray([r['label'] for r in rows],dtype=int)
    groups=np.asarray([r['run_id'] for r in rows])
    ids=np.unique(groups)
    if len(ids)<6: raise ValueError('Нужно минимум 6 независимых прогонов (run_id)')
    if evaluation_mode not in ('run','topology','fault'):raise ValueError('Неизвестный способ независимой проверки')
    held=set()
    if evaluation_mode!='run':
        field='topology_id' if evaluation_mode=='topology' else 'fault_types'
        if any(field not in r for r in rows):raise ValueError('В датасете отсутствуют сведения о топологии / типах отказов')
        attributes={}
        for r in rows:
            value={r[field]} if field=='topology_id' else set(r[field])
            previous=attributes.setdefault(r['run_id'],value)
            if previous!=value:raise ValueError('Метаданные прогона должны совпадать во всех его окнах')
        choices=sorted(set().union(*attributes.values()))
        if not choices:raise ValueError('Нет условий для независимой проверки')
        holdout_value=holdout_value or choices[-1]
        if holdout_value not in choices:raise ValueError('Выбранное условие отсутствует в датасете')
        held={run for run,values in attributes.items() if holdout_value in values}
        if len(held)==len(ids):raise ValueError('Для проверки нужны другие топологии / типы отказов в обучающей части')
    # Find a group-disjoint 60/20/20 partition with both classes in each split.
    rng=np.random.default_rng(seed)
    split=None
    if fixed_split:
        parts=[np.asarray(fixed_split[k]) for k in ('train','validation','test')]
        sets=[set(part) for part in parts]
        if set.union(*sets)!=set(ids) or any(sets[i]&sets[j] for i in range(3) for j in range(i)):raise ValueError('Части должны разделять все независимые прогоны')
        masks=[np.isin(groups,p) for p in parts]
        if not all(len(np.unique(y[m]))==2 for m in masks):raise ValueError('В каждой части нужны оба класса')
        split=(parts,masks)
    ntest=max(2,int(len(ids)*0.2))
    for _ in range(0 if fixed_split else 200):
        if held:
            shuffled=rng.permutation([i for i in ids if i not in held]);nval=max(1,int(len(shuffled)*.25))
            parts=[shuffled[nval:],shuffled[:nval],np.asarray(sorted(held))]
        else:
            shuffled=rng.permutation(ids)
            parts=[shuffled[ntest*2:],shuffled[ntest:ntest*2],shuffled[:ntest]]
        masks=[np.isin(groups,p) for p in parts]
        if all(len(np.unique(y[m]))==2 for m in masks):
            split=(parts,masks)
            break
    if split is None: raise ValueError('Для разбиения нужны нормальные и аномальные окна в train/validation/test. Добавьте независимые прогоны с событиями.')
    parts,(tr,va,te)=split
    fit_mask=tr & (y==0) if meta['normal_only'] else tr
    if fit_mask.sum()<3:raise ValueError('Нужно минимум три обучающих окна выбранного класса')
    model=build(algorithm,config,seed,contamination,int(fit_mask.sum()),x.shape[1])
    started=time.perf_counter()
    with warnings.catch_warnings(record=True) as caught,threadpool_limits(limits=1):
        warnings.simplefilter('always')
        try:model.fit(x[fit_mask]) if meta['normal_only'] else model.fit(x[fit_mask],y[fit_mask])
        except (ValueError,np.linalg.LinAlgError) as error:raise ValueError(meta['title']+': '+str(error)) from error
    fit_seconds=time.perf_counter()-started
    notices=list(dict.fromkeys(str(w.message)[:500] for w in caught))[:10]
    last=estimator(model);importance=getattr(last,'feature_importances_',None)
    importance_method='impurity' if importance is not None else None
    if importance is None and not meta['normal_only'] and hasattr(last,'coef_'):
        importance=np.abs(np.asarray(last.coef_).reshape(-1));importance_method='absolute_coefficient'
    validation=scores(model,algorithm,x[va])
    if threshold is None and meta['normal_only'] and threshold_mode=='contamination':
        threshold=normal_threshold(model,algorithm,x[fit_mask],contamination)
    if threshold is None:
        threshold=best_threshold(y[va],validation)
    threshold=float(threshold)
    if not np.isfinite(threshold):raise ValueError('Модель не смогла определить конечный порог')
    test=scores(model,algorithm,x[te])
    fpr,tpr,_=roc_curve(y[te],test)
    precision,recall,_=precision_recall_curve(y[te],test)
    test_rows=[{key:r[key] for key in ('run_id','time','label','window_seconds','fault_intervals','entity_id') if key in r} for r,selected in zip(rows,te) if selected]
    operational=operational_metrics([dict(r,run_id=r['run_id']+'/'+r['entity_id']) if 'entity_id' in r else r for r in test_rows],test,threshold)
    conditions={name:dict(topologies=sorted({r['topology_id'] for r,m in zip(rows,mask) if m and 'topology_id' in r}),
        fault_types=sorted({v for r,m in zip(rows,mask) if m for v in r.get('fault_types',[])})) for name,mask in zip(('train','validation','test'),(tr,va,te))}
    model_id=uuid.uuid4().hex[:16]
    directory=DATA/'models'
    directory.mkdir(exist_ok=True)
    result=dict(model_id=model_id,algorithm=algorithm,algorithm_title=meta['title'],score_kind=meta['score_kind'],normal_only=meta['normal_only'],feature_set=feature_set,feature_contract=contract,threshold=threshold,seed=seed,
        evaluation=dict(mode=evaluation_mode,holdout=holdout_value,conditions=conditions),operational=operational,test_observations=test_rows,
        training_config=dict(trees=config.get('n_estimators',trees),contamination=contamination,threshold_mode=threshold_mode,seed=seed,parameters=config),features=features,custom_spec=custom_spec,
        fit=dict(samples=int(fit_mask.sum()),normal_only=meta['normal_only'],scaled=meta['scaled'],seconds=fit_seconds,warnings=notices,
            pca_components=int(model['pca'].n_components_) if algorithm=='elliptic_envelope' else None),importance_method=importance_method,
        numpy_version=np.__version__,sklearn_version=sklearn.__version__,metrics=metrics(y[te],test,threshold),validation=metrics(y[va],validation,threshold),
        split={name:dict(runs=list(p),samples=int(m.sum())) for name,p,m in zip(['train','validation','test'],parts,[tr,va,te])},
        importance=[dict(feature=f,value=float(v)) for f,v in sorted(zip(features,importance),key=lambda z:-z[1])] if importance is not None else [],
        test_labels=y[te].tolist(),test_scores=test.tolist(),curves=dict(roc=[list(a) for a in zip(fpr.tolist(),tpr.tolist())],pr=[list(a) for a in zip(recall.tolist(),precision.tolist())]))
    path=directory/f'{model_id}.joblib';temporary=path.with_suffix('.tmp')
    try:
        joblib.dump(dict(model=model,algorithm=algorithm,features=features,feature_set=feature_set,feature_contract=contract,custom_spec=custom_spec,parameters=config),temporary)
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)
    return result


def predict(model_id, rows, threshold):
    # Only internally generated IDs; user supplied pickle files are never loaded.
    artifact=joblib.load(DATA/'models'/f'{model_id}.joblib')
    if {r.get('feature_contract','messages-v1') for r in rows}!={artifact.get('feature_contract','messages-v1')}:
        raise ValueError('Модель и прогон используют разные определения признаков (messages-v1 / procedures-v2). Обучите модель на соответствующем режиме.')
    key=artifact.get('feature_set','legacy-v1')
    custom=artifact.get('custom_spec')
    if not custom and list(specification(key)['features'])!=artifact['features']:raise ValueError('Набор признаков модели не соответствует версии')
    x=np.asarray(input_matrix(rows,key,custom,artifact['features']),dtype=float)
    score=scores(artifact['model'],artifact['algorithm'],x)
    return [dict(time=r['time'],score=float(s),anomaly=bool(s>=threshold),label=r['label']) for r,s in zip(rows,score)]
