"""Versioned experiment designs. Independent simulation runs are the sampling unit."""
from concurrent.futures import CancelledError
from itertools import product
import math
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from scipy.stats import t as student_t
from .schema import Project
from . import storage

CONTRACT = 'experiments-v1'
METRICS = ('latency_p95', 'delivery_ratio', 'throughput')
NODE_FIELDS = {
    'processing_ms': 'Обработка, ms', 'capacity': 'Мощность, msg/s', 'workers': 'Потоки CPU',
    'error_rate': 'Вероятность ошибки', 'database_ms': 'Обращение к БД, ms',
    'db_connections': 'Соединения БД', 'db_queue_limit': 'Очередь БД', 'db_timeout_ms': 'Тайм-аут БД, ms',
    'cache_entries': 'Вместимость кэша', 'cache_ttl_s': 'TTL кэша, s', 'cache_hit_ratio': 'Попадание в кэш',
    'context_limit': 'Вместимость контекстов', 'context_ttl_s': 'TTL контекста, s',
    'pdu_session_limit': 'Вместимость PDU-сессий', 'upf_rate_mbps': 'Скорость UPF, Mbit/s',
}
LINK_FIELDS = {
    'latency_ms': 'Прямая задержка, ms', 'reverse_latency_ms': 'Обратная задержка, ms',
    'loss': 'Прямые потери', 'reverse_loss': 'Обратные потери', 'jitter_ms': 'Джиттер, ms',
    'bandwidth_mbps': 'Полоса, Mbit/s', 'heartbeat_ms': 'Heartbeat, ms',
    'detection_ms': 'Обнаружение отказа, ms', 'recovery_ms': 'Восстановление, ms',
}
FLOW_FIELDS = {'rate': 'Новые процедуры, /s', 'timeout_ms': 'Тайм-аут процедуры, ms', 'max_retries': 'Повторы процедуры'}
CLUSTER_FIELDS = {k: v for k, v in (
    ('replication_delay_ms', 'Задержка репликации, ms'), ('heartbeat_ms', 'Heartbeat, ms'),
    ('heartbeat_timeout_ms', 'Тайм-аут видимости, ms'), ('failover_ms', 'Переключение, ms'),
    ('sync_ms', 'Повторная синхронизация, ms'))}
FIELDS = {'node': NODE_FIELDS, 'link': LINK_FIELDS, 'flow': FLOW_FIELDS, 'cluster': CLUSTER_FIELDS}
COLLECTIONS = {'node': 'nodes', 'link': 'links', 'flow': 'flows', 'cluster': 'clusters'}


class Axis(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    path: str
    values: list[float] = Field(min_length=2, max_length=16)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.values)) != len(self.values):
            raise ValueError('Значения одного параметра не должны повторяться')
        return self


class Options(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    mode: Literal['series', 'capacity'] = 'series'
    axes: list[Axis] = Field(default_factory=lambda: [Axis(path='load', values=[.5, 1, 1.5, 2])], min_length=1, max_length=4)
    design: Literal['cartesian', 'paired'] = 'cartesian'
    repetitions: int = Field(default=10, ge=1, le=1000)
    adaptive: bool = False
    min_repetitions: int = Field(default=10, ge=3, le=1000)
    batch: int = Field(default=5, ge=1, le=100)
    warmup_s: float = Field(default=0, ge=0, lt=86400)
    confidence: float = Field(default=.95, ge=.8, le=.999)
    precision_metric: Literal['latency_p95', 'delivery_ratio', 'throughput'] = 'latency_p95'
    absolute_tolerance: float = Field(default=5, ge=0, le=100000)
    relative_tolerance: float = Field(default=.05, ge=0, le=1)
    load_min: float = Field(default=.25, gt=0, le=20)
    load_max: float = Field(default=2, gt=0, le=20)
    search_points: int = Field(default=8, ge=2, le=32)
    p95_limit_ms: float = Field(default=200, gt=0, le=100000)
    success_min: float = Field(default=.9, gt=0, le=1)
    save_runs: bool = False

    @model_validator(mode='after')
    def bounds(self):
        if self.adaptive and self.repetitions < self.min_repetitions:
            raise ValueError('Максимум повторений должен быть не меньше минимума')
        if self.adaptive and self.absolute_tolerance == self.relative_tolerance == 0:
            raise ValueError('Задайте абсолютную или относительную точность')
        if self.mode == 'capacity' and self.load_min >= self.load_max:
            raise ValueError('Верхняя нагрузка должна быть больше нижней')
        paths = [a.path for a in self.axes]
        if len(set(paths)) != len(paths):
            raise ValueError('Параметры серии не должны повторяться')
        if 'load' in paths and any(p.startswith('flow:') and p.endswith(':rate') for p in paths):
            raise ValueError('Выберите общий множитель нагрузки или абсолютные скорости потоков')
        return self


def parameter_catalog(project):
    p = Project.model_validate(project.model_dump() if isinstance(project,Project) else project)
    choices = [('load', 'Нагрузка · множитель всех потоков')]
    for kind, fields in FIELDS.items():
        for obj in getattr(p, COLLECTIONS[kind]):
            for field, title in fields.items():
                name=getattr(obj,'name',None) or (getattr(obj,'source','')+' → '+getattr(obj,'target',''))
                choices.append((f'{kind}:{obj.id}:{field}', f'{name} [{obj.id}] · {title}'))
    return choices


def configuration(project, parameters, seed=None):
    data = project.model_dump() if isinstance(project, Project) else Project.model_validate(project).model_dump()
    allowed = dict(parameter_catalog(data))
    for path, value in parameters.items():
        if path not in allowed or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Неизвестный параметр или неконечное значение: ' + str(path))
        if path == 'load':
            if not 0 < value <= 20:raise ValueError('Множитель нагрузки: больше 0 и не больше 20')
            for flow in data['flows']:flow['rate'] *= value
        else:
            kind, ident, field = path.split(':')
            next(o for o in data[COLLECTIONS[kind]] if o['id'] == ident)[field] = value
    if seed is not None:data['seed'] = seed
    return Project.model_validate(data)


def plan(project, options):
    p = Project.model_validate(project.model_dump() if isinstance(project,Project) else project)
    o = Options.model_validate(options.model_dump() if isinstance(options,Options) else options)
    if p.engine != 'procedures':raise ValueError('Конструктор серий требует движок процедур')
    if o.warmup_s >= p.duration:raise ValueError('Прогрев должен быть короче длительности эксперимента')
    if o.mode == 'capacity':
        points = [dict(load=float(v)) for v in np.linspace(o.load_min, o.load_max, o.search_points)]
        if len({v['load'] for v in points}) != len(points):raise ValueError('Расширьте диапазон нагрузки: точки сетки совпадают при точности чисел')
    else:
        if o.design == 'paired' and len({len(a.values) for a in o.axes}) != 1:
            raise ValueError('Для совместного изменения списки должны иметь одинаковую длину')
        count = math.prod(len(a.values) for a in o.axes) if o.design == 'cartesian' else len(o.axes[0].values)
        if count > 64:raise ValueError('Лимит плана: 64 сочетания параметров')
        combinations = product(*(a.values for a in o.axes)) if o.design == 'cartesian' else zip(*(a.values for a in o.axes))
        points = [dict(zip((a.path for a in o.axes), values)) for values in combinations]
    configs = [configuration(p, point) for point in points]  # Validate every point before any calculation or write.
    runs = len(points) * o.repetitions
    work = sum(sum(f.rate for f in c.flows) * c.duration for c in configs) * o.repetitions
    if runs > 4096:raise ValueError('Лимит плана: 4096 прогонов; уменьшите число точек или повторений')
    if work > 2_000_000:raise ValueError('Лимит серии: 2 млн базовых процедур с учётом максимума повторений')
    looks = len(checkpoints(o)) if o.adaptive else 1
    return dict(points=points, runs_max=runs, base_procedures_max=work, looks_max=looks,
                interval_alpha=(1-o.confidence)/(len(points)*looks*len(METRICS)))


def checkpoints(options):
    return sorted(set([*range(options.min_repetitions, options.repetitions+1, options.batch), options.repetitions]))


def interval(values, metric, alpha=.05):
    """Mean of independent run statistics; never treat correlated packets as samples."""
    valid = [float(v) for v in values if v is not None and math.isfinite(v)]
    n = len(valid)
    result = dict(mean=float(np.mean(valid)) if n else None, n=n, missing=len(values)-n,
                  low=None, high=None, half_width=None,
                  method='empirical-bernstein' if metric == 'delivery_ratio' else 'student-t')
    if n < 2 or n != len(values):return result
    variance = float(np.var(valid, ddof=1))
    if metric == 'delivery_ratio':
        if any(v < 0 or v > 1 for v in valid):raise ValueError('Доля успешности должна лежать в [0, 1]')
        log_term = math.log(4/alpha)
        radius = math.sqrt(2*variance*log_term/n) + 7*log_term/(3*(n-1))
        low, high = max(0., result['mean']-radius), min(1., result['mean']+radius)
    else:
        radius = float(student_t.ppf(1-alpha/2, n-1))*math.sqrt(variance/n)
        low, high = max(0., result['mean']-radius), result['mean']+radius
    result.update(low=low, high=high, half_width=max(result['mean']-low, high-result['mean']))
    return result


def measurement(engine, warmup_s):
    end = engine.p.duration * 1000
    cohort = [pr for pr in engine.procedures.values() if not pr.get('control') and warmup_s*1000 <= pr['start'] < end]
    successes = [pr for pr in cohort if pr.get('status') == 'delivered']
    latencies = [pr['elapsed'] for pr in successes]
    inside = sum(pr['start']+pr['elapsed'] < end for pr in successes)
    return dict(warmup_s=warmup_s, observation_s=engine.p.duration-warmup_s, sent=len(cohort),
                delivered=len(successes), completed_in_observation=inside, completed_in_tail=len(successes)-inside,
                latency_p95=float(np.percentile(latencies,95)) if latencies else None,
                delivery_ratio=len(successes)/len(cohort) if cohort else None,
                throughput=inside/(engine.p.duration-warmup_s) if cohort else None,
                unfinished=sum(pr.get('status') is None for pr in cohort), policy='exact-start-cohort-v1')


def observed_run(project, warmup_s, progress=None, cancel=None, save=False):
    if any(f.operation.startswith('5G ') for f in project.flows):
        from .fiveg import FiveGSimulation as Simulation
    else:
        from .procedures import ProcedureSimulation as Simulation
    engine = Simulation(project, progress, cancel)
    result = engine.run()
    measured = measurement(engine, warmup_s)
    if cancel and cancel():raise CancelledError('Вычисление отменено')
    record = storage.save('run', f'Серия / {project.name} · seed {project.seed}',
                          dict(project=project.model_dump(), measurement=measured, **result)) if save else None
    return dict(seed=project.seed, run_id=record['id'] if record else None, config_sha256=result['config_sha256'],
                environment=result['environment'], **measured)


def summarize(rows, parameters, options, alpha, stopped=False):
    metrics = {key: interval([r[key] for r in rows], key, alpha) for key in METRICS}
    target = metrics[options.precision_metric]
    precise = (target['half_width'] is not None and target['half_width'] <=
               max(options.absolute_tolerance, options.relative_tolerance*abs(target['mean'])))
    verdict = 'unavailable'
    latency, ratio = metrics['latency_p95'], metrics['delivery_ratio']
    if (latency['low'] is not None and latency['low'] > options.p95_limit_ms) or (ratio['high'] is not None and ratio['high'] < options.success_min):verdict = 'fail'
    elif latency['low'] is not None and ratio['low'] is not None:
        if latency['high'] <= options.p95_limit_ms and ratio['low'] >= options.success_min:verdict = 'pass'
        else:verdict = 'inconclusive'
    return dict(parameters=parameters, multiplier=parameters.get('load'), n=len(rows), metrics=metrics,
                precision_reached=bool(precise), stop_reason=('requirements' if stopped=='requirements' else 'precision') if stopped else 'budget',
                verdict=verdict, sent=sum(r['sent'] for r in rows), delivered=sum(r['delivered'] for r in rows))


def capacity_report(groups):
    ordered = sorted(groups, key=lambda g:g['parameters']['load'])
    passing = [g['parameters']['load'] for g in ordered if g['verdict'] == 'pass']
    non_monotone = any(a['verdict'] == 'fail' and b['verdict'] == 'pass'
                       for i,a in enumerate(ordered) for b in ordered[i+1:])
    best = max(passing) if passing else None
    return dict(best_tested_load=best, right_censored=bool(best is not None and best == ordered[-1]['parameters']['load']),
                non_monotone=non_monotone, inconclusive=sum(g['verdict'] in ('unavailable','inconclusive') for g in ordered),
                grid_step=ordered[1]['parameters']['load']-ordered[0]['parameters']['load'],
                status='confirmed_tested' if best is not None else 'insufficient_evidence' if any(g['verdict'] != 'fail' for g in ordered) else 'no_passing_point',
                scope='tested grid points only; no claim about untested loads or hardware')


def run_series(project, options, progress=None, cancel=None):
    p = Project.model_validate(project.model_dump() if isinstance(project,Project) else project)
    o = Options.model_validate(options.model_dump() if isinstance(options,Options) else options)
    design = plan(p, o)
    results, groups = [], []
    completed = 0
    if progress:progress(0)
    for point_index, point in enumerate(design['points']):
        rows = []; stopped = False
        for j in range(o.repetitions):
            if cancel and cancel():raise CancelledError('Вычисление отменено')
            config = configuration(p, point, (p.seed+j)%2147483647)
            data = observed_run(config, o.warmup_s,
                lambda value:progress(min(99,int(100*(completed+value/100)/design['runs_max']))) if progress else None,
                cancel, o.save_runs)
            rows.append(data); results.append(dict(point=point_index, parameters=point, **data)); completed += 1
            if o.adaptive and len(rows) in checkpoints(o):
                current=summarize(rows, point, o, design['interval_alpha'])
                if o.mode=='capacity' and current['verdict'] in ('pass','fail'):
                    stopped='requirements';break
                if o.mode=='series' and current['precision_reached']:
                    stopped=True;break
        group = summarize(rows, point, o, design['interval_alpha'], stopped)
        group['point'] = point_index
        # Preserve quick-series consumers without confusing new multidimensional labels.
        for key, stats in group['metrics'].items():
            group[key] = stats['mean']; group[key+'_ci95'] = stats['half_width']
        groups.append(group)
    if cancel and cancel():raise CancelledError('Вычисление отменено')
    report = dict(contract=CONTRACT, project=p.model_dump(), options=o.model_dump(), results=results, groups=groups,
                  metadata=dict(values=design['points'], repetitions=o.repetitions, actual_runs=completed, **design),
                  inference=dict(unit='independent seeded run', confidence=o.confidence, alpha=design['interval_alpha'],
                                 correction='Bonferroni over planned points × metrics × possible looks',
                                 latency_assumption='Student t interval for mean run-p95 is approximate; not a pooled latency quantile',
                                 success_method='two-sided empirical Bernstein for independent bounded run ratios'),
                  capacity=capacity_report(groups) if o.mode == 'capacity' else None)
    saved = storage.save('sweep', ('Пропускная способность / ' if o.mode == 'capacity' else 'Конструктор серий / ')+p.name, report)
    if progress:progress(100)
    return saved
