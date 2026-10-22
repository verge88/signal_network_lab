from typing import Literal
import math
from pydantic import BaseModel, ConfigDict, Field, model_validator

NODE_KINDS=['MSC','STP','SG','HLR','MME','DRA','HSS','SCP','gNB','AMF','SMF','UPF','AUSF','UDM','UDR','PCF','NRF','NSSF','SCP5G','DN']
LINK_PROTOCOLS=['SS7','M3UA','Diameter','NGAP','SBI','PFCP','GTPU','IP']
FLOW_PROTOCOLS=['SS7','M3UA','Diameter','NGAP']
FIVEG_OPERATIONS=['5G Registration','5G PDU Session','5G Registration + PDU Session','5G PDU Session Release','5G Deregistration']
OPERATIONS=['MAP UpdateLocation','MAP SendAuthenticationInfo','ISUP IAM','Diameter ULR','Diameter AIR','Diameter CCR',*FIVEG_OPERATIONS]


class UpfRule(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    name: str=Field(default='Правило',max_length=80)
    precedence: int=Field(default=100,ge=0,le=65535)
    flow_id: str=Field(default='',max_length=50)
    dnn: str=Field(default='',max_length=100)
    snssai: str=Field(default='',max_length=20)
    direction: Literal['both','uplink','downlink']='both'
    action: Literal['forward','drop']='forward'
    rate_mbps: float=Field(default=0,ge=0,le=100000)
    burst_bytes: int=Field(default=65536,ge=32,le=10000000)
    max_packet_bytes: int=Field(default=65536,ge=32,le=65536)


class MobilityEvent(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    time: float=Field(ge=0,lt=86400)
    gnb: str=Field(min_length=1,max_length=50)
    amf: str=Field(default='',max_length=50)
    upf: str=Field(default='',max_length=50)
    interruption_ms: float=Field(default=50,ge=0,le=30000)
    timeout_ms: float=Field(default=2000,ge=1,le=30000)
    transfer_context: bool=True


class Node(BaseModel):
    id: str = Field(min_length=1, max_length=50, pattern=r'^[\w-]+$')
    name: str = Field(max_length=80)
    kind: Literal['MSC', 'STP', 'SG', 'HLR', 'MME', 'DRA', 'HSS', 'SCP', 'gNB', 'AMF', 'SMF', 'UPF', 'AUSF', 'UDM', 'UDR', 'PCF', 'NRF', 'NSSF', 'SCP5G','DN']
    x: float = Field(ge=0, le=20000)
    y: float = Field(ge=0, le=20000)
    capacity: float = Field(default=1000, ge=1, le=100000)
    processing_ms: float = Field(default=2, ge=0, le=1000)
    queue_limit: int = Field(default=100, ge=0, le=10000)
    enabled: bool = True
    point_code: str = Field(default='1-1-1', max_length=40)
    realm: str = Field(default='lab.example', max_length=100)
    host: str = Field(default='', max_length=100)
    workers: int = Field(default=4, ge=1, le=128)
    service_distribution: Literal['constant', 'exponential', 'lognormal'] = 'constant'
    service_cv: float = Field(default=.5, ge=0, le=3)
    database_ms: float = Field(default=0, ge=0, le=1000)
    cache_hit_ratio: float = Field(default=.8, ge=0, le=1)
    error_rate: float = Field(default=0, ge=0, le=1)
    detection_ms: float = Field(default=300, ge=0, le=30000)
    switch_ms: float = Field(default=50, ge=0, le=10000)
    recovery_ms: float = Field(default=200, ge=0, le=30000)
    recovery_ramp_ms: float = Field(default=1000, ge=0, le=30000)
    applications: list[int] = Field(default_factory=lambda: [16777251, 4], max_length=20)
    routing_table: dict[str, list[str]] = Field(default_factory=dict, max_length=100)
    gtt: dict[str, str] = Field(default_factory=dict, max_length=100)
    plmn: str = Field(default='00101', pattern=r'^\d{5,6}$')
    slices: list[str] = Field(default_factory=lambda:['1-010203'], min_length=1, max_length=20)
    dnns: list[str] = Field(default_factory=lambda:['internet'], min_length=1, max_length=20)
    nf_status: Literal['REGISTERED','SUSPENDED'] = 'REGISTERED'
    services: list[str] = Field(default_factory=list, max_length=30)
    advertisement_delay_ms: float = Field(default=5,ge=0,le=30000)
    observer_detection_ms: float | None = Field(default=None,ge=0,le=30000)
    storage_model: Literal['queued','probabilistic']='queued'
    db_connections: int=Field(default=4,ge=1,le=128)
    db_queue_limit: int=Field(default=100,ge=0,le=10000)
    db_timeout_ms: float=Field(default=1000,ge=1,le=60000)
    cache_entries: int=Field(default=1000,ge=0,le=100000)
    cache_ttl_s: float=Field(default=60,gt=0,le=86400)
    context_limit: int=Field(default=10000,ge=1,le=1000000)
    pdu_session_limit: int=Field(default=10000,ge=1,le=1000000)
    context_ttl_s: float=Field(default=300,gt=0,le=86400)
    transaction_cache_limit: int=Field(default=10000,ge=0,le=100000)
    transaction_cache_ttl_s: float=Field(default=30,gt=0,le=86400)
    upf_rules: list[UpfRule]=Field(default_factory=list,max_length=32)
    upf_default_action: Literal['forward','drop']='forward'
    upf_rate_mbps: float=Field(default=0,ge=0,le=100000)
    upf_burst_bytes: int=Field(default=65536,ge=32,le=10000000)
    upf_queue_limit: int=Field(default=100,ge=0,le=10000)


class Link(BaseModel):
    id: str = Field(min_length=1, max_length=50, pattern=r'^[\w-]+$')
    source: str
    target: str
    protocol: Literal['SS7', 'M3UA', 'Diameter','NGAP','SBI','PFCP','GTPU','IP'] = 'M3UA'
    latency_ms: float = Field(default=8, ge=0, le=5000)
    jitter_ms: float = Field(default=2, ge=0, le=1000)
    bandwidth_mbps: float = Field(default=10, gt=0, le=100000)
    loss: float = Field(default=0.001, ge=0, le=1)
    enabled: bool = True

    reverse_latency_ms: float | None = Field(default=None, ge=0, le=5000)
    reverse_loss: float | None = Field(default=None, ge=0, le=1)
    buffer_bytes: int = Field(default=1048576, ge=1500, le=100000000)
    mtu: int = Field(default=1500, ge=256, le=9000)
    header_bytes: int = Field(default=64, ge=16, le=128)
    loss_correlation: float = Field(default=0, ge=0, le=.99)
    transport_retries: int = Field(default=3, ge=0, le=6)
    rto_ms: float = Field(default=200, ge=10, le=10000)
    rto_min_ms: float = Field(default=50, ge=1, le=10000)
    rto_max_ms: float = Field(default=3000, ge=10, le=30000)
    streams: int = Field(default=8, ge=1, le=256)
    cwnd_packets: int = Field(default=8, ge=1, le=128)
    path_group: str = Field(default='', max_length=50)
    routing_context: int = Field(default=1, ge=0, le=4294967295)

    asp_state: Literal['ACTIVE', 'INACTIVE', 'DOWN'] = 'ACTIVE'
    peer_state: Literal['OPEN', 'CLOSED'] = 'OPEN'
    startup_ms: float = Field(default=0, ge=0, le=30000)
    detection_ms: float = Field(default=300, ge=0, le=30000)
    switch_ms: float = Field(default=50, ge=0, le=10000)
    recovery_ms: float = Field(default=200, ge=0, le=30000)
    heartbeat_ms: float = Field(default=1000,ge=50,le=60000)
    control_timeout_ms: float = Field(default=500,ge=10,le=30000)
    control_retries: int = Field(default=1,ge=0,le=4)
    reconnect_ms: float = Field(default=500,ge=10,le=30000)
    control_bytes: int = Field(default=128,ge=32,le=9000)
    peer_routing_context: int | None=Field(default=None,ge=0,le=4294967295)
    shutdown_at_s: float | None=Field(default=None,ge=0,lt=86400)

    @model_validator(mode='after')
    def rto_bounds(self):
        if self.rto_min_ms > self.rto_max_ms:
            raise ValueError('RTO min не может превышать RTO max')
        return self


class Flow(BaseModel):
    id: str = Field(min_length=1, max_length=50)
    source: str
    target: str
    protocol: Literal['SS7', 'M3UA', 'Diameter','NGAP'] = 'M3UA'
    operation: Literal['MAP UpdateLocation', 'MAP SendAuthenticationInfo', 'ISUP IAM', 'Diameter ULR', 'Diameter AIR', 'Diameter CCR','5G Registration','5G PDU Session','5G Registration + PDU Session','5G PDU Session Release','5G Deregistration'] = 'MAP UpdateLocation'
    rate: float = Field(default=30, ge=0, le=3000)
    message_bytes: int = Field(default=256, ge=32, le=65536)
    timeout_ms: float = Field(default=1000, ge=1, le=30000)
    procedure: Literal['single', 'registration'] = 'single'
    max_retries: int = Field(default=1, ge=0, le=4)
    retry_backoff_ms: float = Field(default=200, ge=0, le=10000)
    response_bytes: int = Field(default=192, ge=32, le=65536)
    subscribers: int = Field(default=10000, ge=1, le=1000000)
    traffic_profile: Literal['poisson', 'periodic', 'bursty'] = 'poisson'
    profile_period_s: float = Field(default=60, ge=1, le=86400)
    profile_step_s: float = Field(default=.1,ge=.1,le=60)
    profile_amplitude: float = Field(default=0, ge=0, le=.95)
    message_size_cv: float = Field(default=0, ge=0, le=2)
    priority: int = Field(default=1, ge=0, le=2)
    destination_pc: str = Field(default='', max_length=40)
    destination_realm: str = Field(default='', max_length=100)
    destination_host: str = Field(default='', max_length=100)
    global_title: str = Field(default='', max_length=40)
    application_id: int = Field(default=16777251, ge=0, le=4294967295)
    routing_context: int = Field(default=1, ge=0, le=4294967295)
    snssai: str = Field(default='1-010203', pattern=r'^[0-9]{1,3}(-[0-9A-Fa-f]{6})?$')
    dnn: str = Field(default='internet', min_length=1, max_length=100)
    plmn: str = Field(default='00101', pattern=r'^\d{5,6}$')
    nf_bindings: dict[str,str] = Field(default_factory=dict, max_length=12)
    use_nrf: bool = True
    use_nssf: bool = True
    use_pcf: bool = True
    initial_registered: bool = True
    service_timeout_ms: float = Field(default=1000, ge=1, le=30000)
    user_plane_enabled: bool = True
    data_network: str = Field(default='',max_length=50)
    data_packets: int = Field(default=10,ge=1,le=1000)
    data_interval_ms: float = Field(default=100,ge=1,le=60000)
    data_bytes: int = Field(default=1200,ge=32,le=65536)
    data_timeout_ms: float = Field(default=2000,ge=1,le=30000)
    application_profile: Literal['probe','interactive','video','bulk']='probe'
    video_on_s: float=Field(default=1,gt=0,le=600)
    video_off_s: float=Field(default=1,ge=0,le=600)
    data_priority: int=Field(default=1,ge=0,le=2)
    qos_budget_ms: float=Field(default=0,ge=0,le=60000)
    session_lifetime_s: float=Field(default=30,ge=0,le=86400)
    pdu_session_id: int=Field(default=1,ge=1,le=15)
    pdu_session_count: int=Field(default=1,ge=1,le=15)
    session_cleanup_retries: int=Field(default=3,ge=0,le=20)
    session_cleanup_retry_s: float=Field(default=1,ge=.1,le=60)
    deregister_after_s: float=Field(default=0,ge=0,le=86400)
    mobility: list[MobilityEvent]=Field(default_factory=list,max_length=12)

    @model_validator(mode='after')
    def procedure_compatible(self):
        if self.pdu_session_id+self.pdu_session_count>16:raise ValueError('Диапазон PDU Session ID должен помещаться в 1–15')
        if (self.operation in FIVEG_OPERATIONS)!=(self.protocol=='NGAP'):
            raise ValueError('Операции 5G требуют домен NGAP; для NGAP выберите процедуру 5G')
        if self.procedure=='registration' and self.operation not in ('MAP UpdateLocation','Diameter ULR'):
            raise ValueError('Для двухэтапной регистрации выберите MAP UpdateLocation или Diameter ULR')
        return self


class Incident(BaseModel):
    kind: Literal['node_down', 'node_restart', 'link_down', 'surge', 'latency', 'loss', 'loss_absolute', 'node_capacity', 'node_delay', 'node_error']
    target: str
    start: float = Field(default=20, ge=0, lt=86400)
    duration: float = Field(default=20, gt=0, le=86400)
    magnitude: float = Field(default=3, ge=0, le=100)
    direction: Literal['both', 'forward', 'reverse'] = 'both'
    detection_ms: float | None = Field(default=None, ge=0, le=30000)
    recovery_ms: float | None = Field(default=None, ge=0, le=30000)

    @model_validator(mode='after')
    def effect_bounds(self):
        if self.kind in ('loss_absolute','node_error') and self.magnitude>1:raise ValueError('Вероятность должна быть от 0 до 1')
        if self.kind=='node_capacity' and not .01<=self.magnitude<=1:raise ValueError('Оставшаяся мощность: 0,01–1')
        if self.kind=='node_delay' and self.magnitude<1:raise ValueError('Множитель обработки должен быть не меньше 1')
        if self.kind in ('node_capacity','node_delay','node_error') and self.direction!='both':raise ValueError('Направление применяется только к каналу')
        return self


class AttackStep(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=50, pattern=r'^[\w-]+$')
    name: str = Field(default='Этап', min_length=1, max_length=100)
    effect: Literal['surge','latency','loss_absolute','node_capacity','node_delay','node_error','node_down','node_restart','link_down'] = 'surge'
    target: str = Field(min_length=1, max_length=50)
    offset: float = Field(default=0, ge=0, le=599)
    duration: float = Field(default=10, gt=0, le=600)
    magnitude: float = Field(default=3, ge=0, le=100)
    direction: Literal['both','forward','reverse'] = 'both'
    profile: Literal['constant','ramp','pulse'] = 'constant'
    period: float = Field(default=2, ge=.1, le=600)
    duty: float = Field(default=.5, ge=.05, le=.95)

    @model_validator(mode='after')
    def bounds(self):
        if self.effect in ('surge','latency','node_delay') and self.magnitude<1:
            raise ValueError('Множитель нагрузки или задержки должен быть не меньше 1')
        if self.effect in ('loss_absolute','node_error') and self.magnitude>1:
            raise ValueError('Вероятность задаётся в диапазоне 0–1')
        if self.effect=='node_capacity' and not .01<=self.magnitude<=1:
            raise ValueError('Оставшаяся мощность узла: от 0,01 до 1')
        if self.effect in ('node_down','node_restart','link_down') and self.profile!='constant':
            raise ValueError('Для отказов и перезапусков используйте постоянный профиль и повторы сценария')
        if self.effect not in ('latency','loss_absolute','link_down') and self.direction!='both':
            raise ValueError('Направление применяется только к каналу')
        if self.profile=='pulse' and self.duration/self.period>512:raise ValueError('Слишком частые импульсы: увеличьте период')
        return self


class AttackScenario(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=50, pattern=r'^[\w-]+$')
    name: str = Field(default='Собственный сценарий', min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)
    enabled: bool = True
    start: float = Field(default=10, ge=0, lt=86400)
    repeats: int = Field(default=1, ge=1, le=10)
    interval: float = Field(default=20, ge=.1, le=86400)
    steps: list[AttackStep] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def unique_steps(self):
        if len({s.id for s in self.steps})!=len(self.steps):raise ValueError('ID этапов должны быть уникальными')
        return self


class StateCluster(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    id: str=Field(min_length=1,max_length=50,pattern=r'^[\w-]+$')
    name: str=Field(default='Кластер состояния',max_length=80)
    members: list[str]=Field(min_length=2,max_length=6)
    replication_delay_ms: float=Field(default=20,ge=0,le=30000)
    heartbeat_ms: float=Field(default=500,ge=100,le=10000)
    heartbeat_timeout_ms: float=Field(default=1500,ge=100,le=60000)
    failover_ms: float=Field(default=500,ge=0,le=30000)
    sync_ms: float=Field(default=2000,ge=100,le=60000)
    request_timeout_ms: float=Field(default=2000,ge=10,le=60000)
    partition_policy: Literal['availability','quorum']='availability'

    @model_validator(mode='after')
    def bounds(self):
        if len(set(self.members))!=len(self.members):raise ValueError('Экземпляры кластера должны быть различными')
        if self.heartbeat_timeout_ms<self.heartbeat_ms:raise ValueError('Тайм-аут heartbeat должен быть не меньше его интервала')
        return self


class Project(BaseModel):
    name: str = Field(default='Core Network / Research', max_length=100)
    nodes: list[Node] = Field(min_length=2, max_length=60)
    links: list[Link] = Field(max_length=150)
    flows: list[Flow] = Field(min_length=1, max_length=30)
    incidents: list[Incident] = Field(default_factory=list, max_length=30)
    attacks: list[AttackScenario] = Field(default_factory=list, max_length=20)
    clusters: list[StateCluster]=Field(default_factory=list,max_length=10)
    duration: int = Field(default=60, ge=10, le=86400)
    seed: int = Field(default=42, ge=0, le=2147483647)
    window: int = Field(default=5, ge=1, le=3600)
    engine: Literal['procedures', 'messages'] = 'procedures'
    routing_mode: Literal['shortest', 'loadshare'] = 'shortest'
    trace_limit: int = Field(default=1500, ge=100, le=20000)
    journal_sessions: int = Field(default=20,ge=0,le=100)
    journal_events: int = Field(default=2000,ge=100,le=10000)
    journal_procedure_id: int | None = Field(default=None,ge=1,le=2000000)
    label_policy: Literal['affected', 'scheduled', 'degradation'] = 'affected'
    degradation_latency_ms: float = Field(default=200, ge=1, le=60000)
    control_plane: bool = True
    routing_knowledge: Literal['distributed','shared'] = 'distributed'
    route_refresh_ms: float = Field(default=5000,ge=100,le=60000)

    @model_validator(mode='after')
    def consistent(self):
        if self.duration>600 and self.engine!='procedures':raise ValueError('Длительные прогоны требуют движок процедур')
        if math.ceil(self.duration/self.window)>10000:raise ValueError('Лимит 10 000 окон: увеличьте окно измерений')
        if sum(math.ceil(self.duration/f.profile_step_s) for f in self.flows if f.traffic_profile!='poisson' or f.profile_amplitude)>2_000_000:raise ValueError('Лимит 2 млн интервалов профиля: увеличьте шаг профиля нагрузки')
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError('Идентификаторы узлов должны быть уникальными')
        for group in (self.links, self.flows):
            if len({x.id for x in group}) != len(group):
                raise ValueError('Идентификаторы связей/потоков должны быть уникальными')
            for x in group:
                if x.source not in ids or x.target not in ids or x.source == x.target:
                    raise ValueError('Некорректные конечные узлы связи/потока')
        from .attacks import compile_attacks, NODE_EFFECTS
        if len({a.id for a in self.attacks})!=len(self.attacks):raise ValueError('ID атак должны быть уникальными')
        if any(a.enabled for a in self.attacks) and self.engine!='procedures':raise ValueError('Конструктор атак требует движок процедур')
        if self.engine!='procedures' and any(e.kind in ('loss_absolute','node_capacity','node_delay','node_error') for e in self.incidents):raise ValueError('Дополнительные эффекты требуют движок процедур')
        for a in self.attacks:
            for s in a.steps:
                targets=ids if s.effect in NODE_EFFECTS else {f.id for f in self.flows} if s.effect=='surge' else {l.id for l in self.links}
                if s.target not in targets:raise ValueError(f'Атака «{a.name}»: не найдена цель {s.target}')
            if a.enabled and a.start+(a.repeats-1)*a.interval+max(s.offset+s.duration for s in a.steps)>self.duration+1e-9:
                raise ValueError(f'Атака «{a.name}» выходит за длительность эксперимента')
        compiled,_=compile_attacks(self)
        events=[*self.incidents,*compiled]
        for e in events:
            targets = ids if e.kind in NODE_EFFECTS else {f.id for f in self.flows} if e.kind == 'surge' else {l.id for l in self.links}
            if e.target not in targets:
                raise ValueError('Не найден объект сценария: ' + e.target)
            if e.start >= self.duration:
                raise ValueError('Событие начинается после окончания эксперимента')
        for link in self.links:
            if link.shutdown_at_s is not None and (link.shutdown_at_s>=self.duration or link.protocol not in ('M3UA','Diameter') or not self.control_plane or self.engine!='procedures'):
                raise ValueError('Штатное закрытие требует M3UA/Diameter, управление и время внутри эксперимента')
        for n in self.nodes:
            if n.upf_rules and n.kind!='UPF':raise ValueError('Правила пользовательского пути задаются на UPF')
            if any(r.flow_id and r.flow_id not in {f.id for f in self.flows} for r in n.upf_rules):raise ValueError('Правило UPF ссылается на неизвестный поток')
            for destinations in n.routing_table.values():
                if not destinations or len(destinations)>60 or any(x not in ids or x==n.id for x in destinations):
                    raise ValueError('Таблица маршрутизации содержит неизвестный следующий узел')
            if any(pc not in {x.point_code for x in self.nodes} for pc in n.gtt.values()):
                raise ValueError('GTT ссылается на неизвестный point code')
        by_id={n.id:n for n in self.nodes}
        clustered=set()
        if self.clusters and self.engine!='procedures':raise ValueError('Кластеры требуют движок процедур')
        if len({c.id for c in self.clusters})!=len(self.clusters):raise ValueError('ID кластеров должны быть уникальными')
        for c in self.clusters:
            if any(n not in ids for n in c.members):raise ValueError('Кластер ссылается на неизвестный узел')
            if clustered.intersection(c.members):raise ValueError('Узел может входить только в один кластер')
            clustered.update(c.members)
            kinds={by_id[n].kind for n in c.members}
            if len(kinds)!=1 or next(iter(kinds)) not in ('HSS','UDR','AMF','SMF'):raise ValueError('Кластер объединяет экземпляры одного типа: HSS, UDR, AMF или SMF')
            profiles={(by_id[n].plmn,tuple(sorted(by_id[n].slices)),tuple(sorted(by_id[n].dnns))) for n in c.members}
            if len(profiles)!=1:raise ValueError('Экземпляры кластера должны иметь одинаковые PLMN, срезы и DNN')
        for f in self.flows:
            if f.mobility and (f.protocol!='NGAP' or self.engine!='procedures'):raise ValueError('Мобильность требует поток 5G и движок процедур')
            times=[m.time for m in f.mobility]
            if times!=sorted(set(times)):raise ValueError('События мобильности должны иметь уникальное возрастающее время')
            for m in f.mobility:
                if m.time>=self.duration:raise ValueError('Перемещение начинается после эксперимента')
                if m.upf and not any(n.kind=='SMF' for n in self.nodes):raise ValueError('Перенос UPF требует SMF и связность PFCP')
                for nid,kind in [(m.gnb,'gNB'),(m.amf,'AMF'),(m.upf,'UPF')]:
                    if nid and (nid not in by_id or by_id[nid].kind!=kind):raise ValueError('Неверная цель мобильности: '+kind)
            if f.operation in FIVEG_OPERATIONS:
                if self.engine!='procedures':raise ValueError('5G требует движок процедур v2')
                if by_id[f.source].kind!='gNB' or by_id[f.target].kind!='AMF':raise ValueError('Процедура 5G начинается на gNB и адресуется AMF')
                if f.data_network and (f.data_network not in ids or by_id[f.data_network].kind!='DN'):raise ValueError('Сеть данных должна ссылаться на DN')
                for kind,nid in f.nf_bindings.items():
                    if nid not in by_id or by_id[nid].kind!=kind:raise ValueError('Привязка NF должна указывать на узел соответствующего типа')
        max_rate = sum(f.rate * (1 + f.profile_amplitude) * (3 if f.traffic_profile=='bursty' else 1) for f in self.flows)
        surge = max([e.magnitude for e in events if e.kind == 'surge'] + [1])
        if max_rate * self.duration * max(1, surge) > 350000:
            raise ValueError('Слишком большой эксперимент: уменьшите длительность или интенсивность (лимит 350 000 сообщений)')
        if self.engine=='procedures' and sum(f.rate*(1+f.max_retries)*(32 if f.operation=='5G Registration + PDU Session' else 16 if f.operation in FIVEG_OPERATIONS else 2 if f.procedure=='registration' else 1) for f in self.flows)*self.duration*max(1,surge)>700000:
            raise ValueError('Слишком большой эксперимент с учётом этапов и повторов (лимит 700 000 попыток)')
        if sum(f.rate*f.data_packets for f in self.flows if f.protocol=='NGAP' and 'PDU' in f.operation and f.user_plane_enabled)*self.duration*max(1,surge)>500000:raise ValueError('Уменьшите пользовательскую нагрузку: лимит 500 000 проверочных пакетов')
        return self
