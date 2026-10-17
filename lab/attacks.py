"""Declarative, bounded attacks on the offline behavioural model only.

Profiles are piecewise constant (ramps: 12 bins). Coincident effects combine
by maximum severity, never by summation. Original project remains immutable.
"""
import json
import uuid
from .schema import AttackScenario, AttackStep, Incident

NODE_EFFECTS={'node_capacity','node_delay','node_error','node_down','node_restart'}
EFFECTS={
    'surge':('Всплеск сигнального потока','flow',1,100,'× нагрузки'),
    'latency':('Увеличение задержки канала','link',1,100,'× задержки'),
    'loss_absolute':('Вероятность потери пакетов','link',0,1,'вероятность 0–1'),
    'node_capacity':('Истощение мощности узла','node',.01,1,'оставшаяся доля 0–1'),
    'node_delay':('Замедление обработки узла','node',1,100,'× времени обработки'),
    'node_error':('Ошибки приложения','node',0,1,'вероятность 0–1'),
    'node_down':('Недоступность узла','node',1,1,'отказ'),
    'node_restart':('Перезапуск с потерей контекста','node',1,1,'перезапуск'),
    'link_down':('Блокирование канала','link',1,1,'разрыв'),
}
PROFILES=[('constant','Постоянный'),('ramp','Плавное усиление · 12 ступеней'),('pulse','Импульсный')]
PRESETS=[
    dict(id='ss7_flood',name='SS7 · шторм MAP-запросов',description='Усиливает существующий MAP-поток: очередь, тайм-ауты и повторы проходят через модель сети.',effect='surge',magnitude=4,operations=['MAP UpdateLocation','MAP SendAuthenticationInfo']),
    dict(id='diameter_flood',name='Diameter · шторм ULR / AIR / CCR',description='Усиливает поток Diameter; исходная операция и настройки повторов сохраняются.',effect='surge',magnitude=4,operations=['Diameter ULR','Diameter AIR','Diameter CCR']),
    dict(id='5g_registration',name='5G · шторм регистраций',description='Увеличивает число процедур регистрации с зависимыми RPC к NF.',effect='surge',magnitude=4,operations=['5G Registration','5G Registration + PDU Session']),
    dict(id='5g_pdu',name='5G · шторм PDU-сессий',description='Усиливает существующий поток PDU, включая настроенные проверки N3/N6.',effect='surge',magnitude=4,operations=['5G PDU Session','5G Registration + PDU Session']),
    dict(id='sbi_exhaustion',name='5G · истощение ресурсов NF',description='Снижает предел допуска выбранной NF до 10% от исходного.',effect='node_capacity',magnitude=.1,kinds=['AMF','SMF','AUSF','UDM','UDR','NRF','PCF','NSSF','SCP5G']),
    dict(id='slow_service',name='Замедление сервиса / базы',description='Повышает время обработки, включая обращения к базе; исходная мощность допуска сохраняется.',effect='node_delay',magnitude=10),
    dict(id='errors',name='Массовые ошибки приложения',description='Задаёт вероятность ошибки на прикладном ответе. Выберите конечный сервис, а не транзитный STP.',effect='node_error',magnitude=.5,kinds=['HLR','HSS','SCP','AMF','SMF','AUSF','UDM','UDR','NRF','PCF','NSSF','UPF']),
    dict(id='reverse_delay',name='Асимметричная задержка / шторм повторов',description='Увеличивает задержку обратного направления канала; повторы зависят от его RTO.',effect='latency',magnitude=20,direction='reverse'),
    dict(id='selective_loss',name='Избирательные потери пакетов',description='Задаёт абсолютную вероятность потери; действует и при нулевой исходной потере.',effect='loss_absolute',magnitude=.3),
    dict(id='blackhole',name='Блокирование сигнального канала',description='Отключает выбранный канал на заданное время с моделированием обнаружения и перемаршрутизации.',effect='link_down',magnitude=1),
    dict(id='amf_restart',name='5G · потеря контекста AMF',description='Перезапускает AMF; контексты регистрации должны восстанавливаться моделируемыми процедурами.',effect='node_restart',magnitude=1,kinds=['AMF']),
    dict(id='compound',name='Многоэтапное истощение сервиса',description='Плавное снижение допуска, затем замедление и ошибки приложения на одном узле.',effect='node_capacity',magnitude=.1,kinds=['HLR','HSS','SCP','AMF','SMF','AUSF','UDM','UDR','NRF','PCF','NSSF','UPF']),
]


def segments(s):
    if s.profile=='constant':return [(0.,s.duration,s.magnitude)]
    if s.profile=='ramp':
        baseline=1. if s.effect in ('surge','latency','node_delay','node_capacity') else 0.
        return [(i*s.duration/12,s.duration/12,baseline+(s.magnitude-baseline)*(i+1)/12) for i in range(12)]
    result=[];offset=0.
    while offset<s.duration-1e-9:
        result.append((offset,min(s.period*s.duty,s.duration-offset),s.magnitude))
        if len(result)>512:raise ValueError('Слишком частые импульсы: увеличьте период')
        offset+=s.period
    return result


def compile_attacks(project):
    events=[];origins=[]
    for a in project.attacks:
        if not a.enabled:continue
        for repeat in range(a.repeats):
            for s in a.steps:
                for offset,duration,value in segments(s):
                    events.append(Incident(kind=s.effect,target=s.target,start=a.start+repeat*a.interval+s.offset+offset,duration=duration,magnitude=value,direction=s.direction))
                    origins.append(dict(attack_id=a.id,step_id=s.id,repeat=repeat+1))
                    if len(events)>512:raise ValueError('Лимит конструктора: 512 интервалов воздействия; уменьшите число этапов, повторов или импульсов')
    return events,origins


def candidates(project,preset):
    group=EFFECTS[preset['effect']][1]
    values=getattr(project,{'node':'nodes','link':'links','flow':'flows'}[group])
    return [x for x in values if ('operations' not in preset or x.operation in preset['operations']) and ('kinds' not in preset or x.kind in preset['kinds'])]


def from_preset(project,key,target=None):
    p=next(p for p in PRESETS if p['id']==key);choices=candidates(project,p)
    if not choices:raise ValueError('Нет подходящей цели. Добавьте соответствующий узел, канал или сигнальный поток в проект.')
    target=target or choices[0].id
    if target not in {x.id for x in choices}:raise ValueError('Цель не подходит для предустановки')
    duration=min(10.,project.duration*.3);start=min(10.,project.duration*.2)
    steps=[dict(id='step-1',name=p['name'],effect=p['effect'],target=target,duration=duration,magnitude=p['magnitude'],direction=p.get('direction','both'))]
    if key=='compound':
        steps[0]['profile']='ramp'
        steps.extend([dict(id='step-2',name='Замедление',effect='node_delay',target=target,offset=duration*.3,duration=duration*.7,magnitude=8),dict(id='step-3',name='Ошибки',effect='node_error',target=target,offset=duration*.6,duration=duration*.4,magnitude=.5)])
    return AttackScenario(id='attack-'+uuid.uuid4().hex[:10],name=p['name'],description=p['description'],start=start,interval=duration+5,steps=steps)


def encode_template(scenario):
    a=AttackScenario.model_validate(scenario.model_dump() if isinstance(scenario,AttackScenario) else scenario)
    return json.dumps(dict(format='signal-lab-attack',version=1,scenario=a.model_dump()),ensure_ascii=False,indent=2,allow_nan=False)


def decode_template(text):
    if len(text.encode('utf-8'))>262144:raise ValueError('Шаблон атаки должен быть меньше 256 КБ')
    data=json.loads(text)
    if not isinstance(data,dict) or set(data)!={'format','version','scenario'} or data['format']!='signal-lab-attack' or data['version']!=1:
        raise ValueError('Неподдерживаемый формат шаблона атаки')
    a=AttackScenario.model_validate(data['scenario']);a.id='attack-'+uuid.uuid4().hex[:10]
    return a
