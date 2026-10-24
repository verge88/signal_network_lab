"""Shared, bounded estimator catalogue for native training and monitoring studies."""
import math
import numpy as np
from sklearn.cluster import KMeans
from sklearn.covariance import EllipticEnvelope
from sklearn.decomposition import PCA
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis, QuadraticDiscriminantAnalysis
from sklearn.ensemble import (RandomForestClassifier, ExtraTreesClassifier, GradientBoostingClassifier,
    HistGradientBoostingClassifier, AdaBoostClassifier, BaggingClassifier, IsolationForest)
from sklearn.linear_model import LogisticRegression, SGDClassifier, RidgeClassifier, SGDOneClassSVM
from sklearn.mixture import GaussianMixture
from sklearn.naive_bayes import GaussianNB, BernoulliNB
from sklearn.neighbors import KNeighborsClassifier, LocalOutlierFactor
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC, LinearSVC, OneClassSVM
from sklearn.tree import DecisionTreeClassifier


def integer(title,default,lo,hi):return dict(title=title,type='int',default=default,min=lo,max=hi)
def real(title,default,lo,hi):return dict(title=title,type='float',default=default,min=lo,max=hi)
def choice(title,default,values):return dict(title=title,type='choice',default=default,values=values)

DEPTH=integer('Глубина дерева · 0 без ограничения',10,0,100)
LEAF=integer('Минимум объектов в листе',2,1,1000)
TREES=integer('Число деревьев / итераций',120,10,2000)
WEIGHT=choice('Вес классов','balanced',['balanced',None])
C=real('C · обратная регуляризация',1.,.000001,100000.)
ALPHA=real('Сила регуляризации',.0001,.000000001,1000.)
ITER=integer('Максимум итераций',1500,50,20000)
NEIGHBORS=integer('Число соседей',5,1,1000)
RATE=real('Скорость обучения',.1,.00001,2.)
GAMMA=choice('Gamma · ширина ядра','scale',['scale','auto'])


def spec(title,group,description,params=None,normal=False,score='probability',scale=False):
    return dict(title=title,group=group,description=description,parameters=params or {},normal_only=normal,score_kind=score,scaled=scale)


ALGORITHMS={
    'random_forest':spec('Random Forest','Деревья и ансамбли','Случайный лес; обучение по размеченным нормальным и аномальным окнам.',dict(n_estimators=TREES,max_depth=DEPTH,min_samples_leaf=LEAF,class_weight=WEIGHT)),
    'logistic':spec('Logistic Regression','Линейные модели','Логистическая регрессия с масштабированием по train.',dict(C=C,max_iter=ITER,class_weight=WEIGHT),scale=True),
    'isolation_forest':spec('Isolation Forest','Детекторы аномалий','Изолирующий лес. Обучается только на нормальных окнах train.',dict(n_estimators=TREES,max_samples=choice('Объектов на дерево · auto до 256','auto',['auto',.25,.5,1.])),normal=True,score='anomaly'),
    'decision_tree':spec('Decision Tree','Деревья и ансамбли','Одно интерпретируемое дерево решений.',dict(max_depth=DEPTH,min_samples_leaf=LEAF,criterion=choice('Критерий','gini',['gini','entropy','log_loss']),class_weight=WEIGHT)),
    'extra_trees':spec('Extra Trees','Деревья и ансамбли','Ансамбль деревьев с дополнительной случайностью порогов.',dict(n_estimators=TREES,max_depth=DEPTH,min_samples_leaf=LEAF,class_weight=WEIGHT)),
    'gradient_boosting':spec('Gradient Boosting','Деревья и ансамбли','Последовательный градиентный бустинг деревьев.',dict(n_estimators=TREES,learning_rate=RATE,max_depth=integer('Глубина дерева',3,1,30),min_samples_leaf=LEAF,subsample=real('Доля объектов на итерацию',1.,.1,1.))),
    'hist_gradient_boosting':spec('Histogram Gradient Boosting','Деревья и ансамбли','Гистограммный бустинг. Внутренняя случайная validation отключена.',dict(max_iter=integer('Итераций бустинга',120,10,2000),learning_rate=RATE,max_leaf_nodes=integer('Максимум листьев',31,2,255),min_samples_leaf=integer('Минимум объектов в листе',10,1,1000),l2_regularization=real('L2-регуляризация',1.,0.,1000.))),
    'adaboost':spec('AdaBoost','Деревья и ансамбли','Адаптивный бустинг коротких деревьев.',dict(n_estimators=TREES,learning_rate=real('Скорость обучения',.5,.00001,2.))),
    'bagging':spec('Bagging','Деревья и ансамбли','Независимые деревья на выборках с возвращением.',dict(n_estimators=TREES,max_samples=real('Доля объектов в выборке',1.,.1,1.),max_features=real('Доля признаков',1.,.1,1.))),
    'knn':spec('k-Nearest Neighbors','Соседи и вероятностные','Классификация по ближайшим размеченным соседям; признаки масштабируются.',dict(n_neighbors=NEIGHBORS,weights=choice('Вес соседа','distance',['uniform','distance']),p=choice('Метрика · p',2,[1,2])),scale=True),
    'svm':spec('SVM · ядро','Опорные векторы','Ядерная SVM. Оценка — отступ, не вероятность. На больших выборках обучение может быть долгим.',dict(C=C,kernel=choice('Ядро','rbf',['rbf','linear','poly','sigmoid']),gamma=GAMMA,degree=integer('Степень poly',3,1,8),class_weight=WEIGHT,max_iter=ITER),score='margin',scale=True),
    'linear_svm':spec('Linear SVM','Опорные векторы','Линейная SVM с масштабированием. Оценка — отступ.',dict(C=C,class_weight=WEIGHT,max_iter=ITER),score='margin',scale=True),
    'sgd':spec('SGD Classifier','Линейные модели','Линейный классификатор со стохастическим градиентом.',dict(loss=choice('Функция потерь','hinge',['hinge','log_loss','modified_huber']),alpha=ALPHA,penalty=choice('Регуляризация','l2',['l2','l1','elasticnet']),max_iter=ITER,class_weight=WEIGHT),score='margin',scale=True),
    'ridge':spec('Ridge Classifier','Линейные модели','Линейная классификация с L2-регуляризацией. Оценка — отступ.',dict(alpha=real('L2-регуляризация',1.,.000001,100000.),class_weight=WEIGHT),score='margin',scale=True),
    'gaussian_nb':spec('Gaussian Naive Bayes','Соседи и вероятностные','Гауссовский наивный Байес; предполагает условную независимость признаков.',dict(var_smoothing=real('Сглаживание дисперсии',.000000001,.000000000001,1.)),scale=True),
    'bernoulli_nb':spec('Bernoulli Naive Bayes','Соседи и вероятностные','Наивный Байес с бинаризацией масштабированных признаков.',dict(alpha=real('Сглаживание',1.,.000001,1000.),binarize=real('Порог бинаризации после масштаба',0.,-10.,10.)),scale=True),
    'lda':spec('Linear Discriminant Analysis','Дискриминантный анализ','Линейный дискриминант с автоматическим сжатием ковариации.',dict(shrinkage=choice('Сжатие ковариации','auto',['auto',None])),scale=True),
    'qda':spec('Quadratic Discriminant Analysis','Дискриминантный анализ','Квадратичный дискриминант с eigen-решателем, сжатием и регуляризацией ковариации.',dict(reg_param=real('Регуляризация ковариации',.1,.000001,1.),shrinkage=choice('Сжатие ковариации','auto',['auto',None])),scale=True),
    'mlp':spec('MLP · нейронная сеть','Нейронные сети','Многослойный перцептрон. Ранняя остановка по случайным окнам отключена.',dict(hidden_layer_sizes=dict(title='Скрытые слои · нейронов через запятую',type='layers',default=[64,32]),activation=choice('Активация','relu',['relu','tanh','logistic']),alpha=ALPHA,learning_rate_init=real('Начальная скорость обучения',.001,.000001,.1),max_iter=integer('Максимум эпох',300,20,5000)),scale=True),
    'one_class_svm':spec('One-Class SVM','Детекторы аномалий','Ядерная граница нормального поведения. Обучается только на нормальных train.',dict(nu=real('Nu · граница нормального класса',.1,.001,.99),kernel=choice('Ядро','rbf',['rbf','linear','poly','sigmoid']),gamma=GAMMA,max_iter=ITER),normal=True,score='anomaly',scale=True),
    'sgd_one_class_svm':spec('SGD One-Class SVM','Детекторы аномалий','Линейная граница нормального поведения, стохастическое обучение.',dict(nu=real('Nu · граница нормального класса',.1,.001,.99),max_iter=ITER),normal=True,score='anomaly',scale=True),
    'local_outlier_factor':spec('Local Outlier Factor · novelty','Детекторы аномалий','Локальная плотность. Novelty-режим для новых окон; train-оценки берутся отдельно.',dict(n_neighbors=integer('Число соседей',20,2,1000),p=choice('Метрика · p',2,[1,2])),normal=True,score='anomaly',scale=True),
    'elliptic_envelope':spec('Elliptic Envelope','Детекторы аномалий','Робастная гауссовская область. Масштабирование и PCA по нормальным train; предполагает один эллиптический кластер.',dict(components=integer('Максимум компонент PCA',5,1,64)),normal=True,score='anomaly',scale=True),
    'gaussian_mixture':spec('Gaussian Mixture · плотность','Детекторы аномалий','Смесь гауссовских распределений нормы. Оценка — отрицательный log likelihood.',dict(n_components=integer('Число компонент смеси',2,1,32),covariance_type=choice('Ковариация','diag',['diag','full','tied','spherical']),reg_covar=real('Регуляризация ковариации',.000001,.000000001,1.),max_iter=integer('Максимум итераций',200,20,2000)),normal=True,score='anomaly',scale=True),
    'kmeans':spec('K-Means · расстояние до нормы','Детекторы аномалий','Кластеры нормальных train. Оценка — расстояние до ближайшего центра, не вероятность.',dict(n_clusters=integer('Число кластеров',2,1,64),n_init=integer('Начальных размещений',10,1,50),max_iter=integer('Максимум итераций',300,20,2000)),normal=True,score='anomaly',scale=True),
}


def definition(algorithm):
    if algorithm not in ALGORITHMS:raise ValueError('Неизвестный алгоритм')
    return ALGORITHMS[algorithm]


def parameters(algorithm,values=None,trees=120):
    definitions=definition(algorithm)['parameters']
    if values is not None and not isinstance(values,dict):raise ValueError('Параметры модели должны быть словарём')
    values=values or {}
    if set(values)-set(definitions):raise ValueError('Неизвестные параметры модели: '+', '.join(sorted(set(values)-set(definitions))))
    result={}
    for key,item in definitions.items():
        value=values.get(key,trees if key=='n_estimators' else item['default'])
        if item['type']=='choice':valid=any(type(value) is type(v) and value==v for v in item['values'])
        elif item['type']=='layers':
            if isinstance(value,str):
                try:value=[int(v.strip()) for v in value.split(',')]
                except ValueError:raise ValueError('Слои MLP: укажите числа через запятую')
            valid=isinstance(value,(tuple,list)) and 1<=len(value)<=4 and all(type(v) is int and 1<=v<=512 for v in value)
            if valid:value=list(value)
        else:
            valid=type(value) in ((int,) if item['type']=='int' else (int,float)) and math.isfinite(value) and item['min']<=value<=item['max']
        if not valid:raise ValueError('Некорректный параметр: '+item['title'])
        result[key]=value
    return result


def build(algorithm,config,seed,contamination,n_samples,n_features):
    """No hidden sampling, calibration split or fit outside the supplied train data."""
    meta=definition(algorithm);p=dict(config)
    if p.get('max_depth')==0:p['max_depth']=None
    if algorithm in ('knn','local_outlier_factor') and p['n_neighbors']>=n_samples:
        raise ValueError(f'Число соседей должно быть меньше обучающей выборки ({n_samples})')
    if p.get('n_clusters',p.get('n_components',1))>n_samples:raise ValueError('Компонент / кластеров больше, чем обучающих объектов')
    factories={
        'random_forest':lambda:RandomForestClassifier(**p,random_state=seed,n_jobs=1),
        'logistic':lambda:LogisticRegression(**p,random_state=seed),
        'isolation_forest':lambda:IsolationForest(**p,contamination=contamination,random_state=seed,n_jobs=1),
        'decision_tree':lambda:DecisionTreeClassifier(**p,random_state=seed),
        'extra_trees':lambda:ExtraTreesClassifier(**p,random_state=seed,n_jobs=1),
        'gradient_boosting':lambda:GradientBoostingClassifier(**p,random_state=seed),
        'hist_gradient_boosting':lambda:HistGradientBoostingClassifier(**p,random_state=seed,early_stopping=False),
        'adaboost':lambda:AdaBoostClassifier(**p,random_state=seed),
        'bagging':lambda:BaggingClassifier(**p,random_state=seed,n_jobs=1),
        'knn':lambda:KNeighborsClassifier(**p,n_jobs=1),
        'svm':lambda:SVC(**p,probability=False,random_state=seed,cache_size=128),
        'linear_svm':lambda:LinearSVC(**p,random_state=seed,dual='auto'),
        'sgd':lambda:SGDClassifier(**p,random_state=seed,early_stopping=False),
        'ridge':lambda:RidgeClassifier(**p),
        'gaussian_nb':lambda:GaussianNB(**p),
        'bernoulli_nb':lambda:BernoulliNB(**p),
        'lda':lambda:LinearDiscriminantAnalysis(**p,solver='lsqr'),
        'qda':lambda:QuadraticDiscriminantAnalysis(**p,solver='eigen'),
        'mlp':lambda:MLPClassifier(**dict(p,hidden_layer_sizes=tuple(p['hidden_layer_sizes'])),random_state=seed,early_stopping=False),
        'one_class_svm':lambda:OneClassSVM(**p,cache_size=128),
        'sgd_one_class_svm':lambda:SGDOneClassSVM(**p,random_state=seed),
        'local_outlier_factor':lambda:LocalOutlierFactor(**p,novelty=True,contamination=contamination,n_jobs=1),
        'elliptic_envelope':lambda:EllipticEnvelope(contamination=contamination,random_state=seed),
        'gaussian_mixture':lambda:GaussianMixture(**p,random_state=seed,n_init=1),
        'kmeans':lambda:KMeans(**p,random_state=seed),
    }
    steps=[]
    if meta['scaled']:steps.append(('scale',StandardScaler()))
    if algorithm=='elliptic_envelope':steps.append(('pca',PCA(n_components=min(p['components'],n_features,n_samples-1),svd_solver='full')))
    steps.append(('estimator',factories[algorithm]()))
    return Pipeline(steps) if len(steps)>1 else steps[0][1]


def estimator(model):return model[-1] if isinstance(model,Pipeline) else model


def score(model,algorithm,x):
    meta=definition(algorithm)
    if algorithm=='kmeans':values=np.min(model.transform(x),axis=1)
    elif meta['normal_only']:values=-model.score_samples(x)
    elif meta['score_kind']=='margin':values=model.decision_function(x)
    else:
        index=list(model.classes_).index(1);values=model.predict_proba(x)[:,index]
    values=np.asarray(values,dtype=float)
    if values.shape!=(len(x),) or not np.isfinite(values).all():raise ValueError('Модель вернула неконечные оценки. Проверьте признаки и регуляризацию.')
    return values


def normal_threshold(model,algorithm,x,contamination):
    last=estimator(model)
    if algorithm in ('isolation_forest','local_outlier_factor','elliptic_envelope'):return float(-last.offset_)
    # LOF must never score its training rows through novelty score_samples.
    return float(np.quantile(score(model,algorithm,x),1-contamination))
