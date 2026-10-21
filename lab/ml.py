import uuid
import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score, average_precision_score, confusion_matrix, roc_curve, precision_recall_curve
from .storage import DATA
from .simulator import FEATURES
from .features import specification, matrix, operational_metrics


def metrics(y, score, threshold):
    y=np.asarray(y)
    score=np.asarray(score)
    pred=(score>=threshold).astype(int)
    cm=confusion_matrix(y,pred,labels=[0,1]).tolist()
    both=len(np.unique(y))==2
    return dict(precision=float(precision_score(y,pred,zero_division=0)),recall=float(recall_score(y,pred,zero_division=0)),f1=float(f1_score(y,pred,zero_division=0)),roc_auc=float(roc_auc_score(y,score)) if both else None,pr_auc=float(average_precision_score(y,score)) if both else None,confusion=cm,false_positive_rate=cm[0][1]/max(1,sum(cm[0])),samples=len(y),positives=int(sum(y)))


def scores(model, algorithm, x):
    return -model.score_samples(x) if algorithm=='isolation_forest' else model.predict_proba(x)[:,1]


def train(rows, algorithm, seed=42, threshold=None, trees=120, contamination=0.1, threshold_mode='validation', feature_set='legacy-v1', evaluation_mode='run', holdout_value=None):
    contracts={r.get('feature_contract','messages-v1') for r in rows}
    if len(contracts)!=1:raise ValueError('Нельзя смешивать определения признаков messages-v1 и procedures-v2 в одном датасете')
    contract=next(iter(contracts))
    features=list(specification(feature_set)['features'])
    x=np.asarray(matrix(rows,feature_set),dtype=float)
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
    ntest=max(2,int(len(ids)*0.2))
    for _ in range(200):
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
    if algorithm=='logistic':
        model=make_pipeline(StandardScaler(),LogisticRegression(max_iter=1500,class_weight='balanced',random_state=seed))
        model.fit(x[tr],y[tr])
        importance=np.abs(model[-1].coef_[0])
    elif algorithm=='random_forest':
        model=RandomForestClassifier(n_estimators=trees,max_depth=10,min_samples_leaf=2,class_weight='balanced',random_state=seed,n_jobs=1)
        model.fit(x[tr],y[tr])
        importance=model.feature_importances_
    elif algorithm=='isolation_forest':
        model=IsolationForest(n_estimators=trees,contamination=contamination,random_state=seed,n_jobs=1)
        model.fit(x[tr & (y==0)])
        importance=None
    else: raise ValueError('Неизвестный алгоритм')
    validation=scores(model,algorithm,x[va])
    if threshold is None and algorithm=='isolation_forest' and threshold_mode=='contamination':
        threshold=float(-model.offset_)
    if threshold is None:
        candidates=np.unique(np.r_[0,validation,1])
        threshold=float(max(candidates,key=lambda v:(f1_score(y[va],validation>=v,zero_division=0),v)))
    test=scores(model,algorithm,x[te])
    fpr,tpr,_=roc_curve(y[te],test)
    precision,recall,_=precision_recall_curve(y[te],test)
    test_rows=[{key:r[key] for key in ('run_id','time','label','window_seconds','fault_intervals') if key in r} for r,selected in zip(rows,te) if selected]
    operational=operational_metrics(test_rows,test,threshold)
    conditions={name:dict(topologies=sorted({r['topology_id'] for r,m in zip(rows,mask) if m and 'topology_id' in r}),
        fault_types=sorted({v for r,m in zip(rows,mask) if m for v in r.get('fault_types',[])})) for name,mask in zip(('train','validation','test'),(tr,va,te))}
    model_id=uuid.uuid4().hex[:16]
    directory=DATA/'models'
    directory.mkdir(exist_ok=True)
    joblib.dump(dict(model=model,algorithm=algorithm,features=features,feature_set=feature_set,feature_contract=contract),(directory/f'{model_id}.joblib'))
    return dict(model_id=model_id,algorithm=algorithm,feature_set=feature_set,feature_contract=contract,threshold=threshold,seed=seed,
        evaluation=dict(mode=evaluation_mode,holdout=holdout_value,conditions=conditions),operational=operational,test_observations=test_rows,
        training_config=dict(trees=trees,contamination=contamination,threshold_mode=threshold_mode,seed=seed),features=features,
        numpy_version=np.__version__,sklearn_version=sklearn.__version__,metrics=metrics(y[te],test,threshold),validation=metrics(y[va],validation,threshold),
        split={name:dict(runs=list(p),samples=int(m.sum())) for name,p,m in zip(['train','validation','test'],parts,[tr,va,te])},
        importance=[dict(feature=f,value=float(v)) for f,v in sorted(zip(features,importance),key=lambda z:-z[1])] if importance is not None else [],
        test_labels=y[te].tolist(),test_scores=test.tolist(),curves=dict(roc=[list(a) for a in zip(fpr.tolist(),tpr.tolist())],pr=[list(a) for a in zip(recall.tolist(),precision.tolist())]))


def predict(model_id, rows, threshold):
    # Only internally generated IDs; user supplied pickle files are never loaded.
    artifact=joblib.load(DATA/'models'/f'{model_id}.joblib')
    if {r.get('feature_contract','messages-v1') for r in rows}!={artifact.get('feature_contract','messages-v1')}:
        raise ValueError('Модель и прогон используют разные определения признаков (messages-v1 / procedures-v2). Обучите модель на соответствующем режиме.')
    key=artifact.get('feature_set','legacy-v1')
    if list(specification(key)['features'])!=artifact['features']:raise ValueError('Набор признаков модели не соответствует версии')
    x=np.asarray(matrix(rows,key),dtype=float)
    score=scores(artifact['model'],artifact['algorithm'],x)
    return [dict(time=r['time'],score=float(s),anomaly=bool(s>=threshold),label=r['label']) for r,s in zip(rows,score)]
