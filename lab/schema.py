from typing import Literal
from pydantic import BaseModel, Field, model_validator

NODE_KINDS=['MSC','STP','SG','HLR','MME','DRA','HSS','SCP','gNB','AMF','SMF','UPF','AUSF','UDM','UDR','PCF','NRF','NSSF','SCP5G','DN']
LINK_PROTOCOLS=['SS7','M3UA','Diameter','NGAP','SBI','PFCP','GTPU','IP']
FLOW_PROTOCOLS=['SS7','M3UA','Diameter','NGAP']
FIVEG_OPERATIONS=['5G Registration','5G PDU Session','5G Registration + PDU Session']
OPERATIONS=['MAP UpdateLocation','MAP SendAuthenticationInfo','ISUP IAM','Diameter ULR','Diameter AIR','Diameter CCR',*FIVEG_OPERATIONS]


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
    operation: Literal['MAP UpdateLocation', 'MAP SendAuthenticationInfo', 'ISUP IAM', 'Diameter ULR', 'Diameter AIR', 'Diameter CCR','5G Registration','5G PDU Session','5G Registration + PDU Session'] = 'MAP UpdateLocation'
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

    @model_validator(mode='after')
    def procedure_compatible(self):
        if (self.operation in FIVEG_OPERATIONS)!=(self.protocol=='NGAP'):
            raise ValueError('Операции 5G требуют домен NGAP; для NGAP выберите процедуру 5G')
        if self.procedure=='registration' and self.operation not in ('MAP UpdateLocation','Diameter ULR'):
            raise ValueError('Для двухэтапной регистрации выберите MAP UpdateLocation или Diameter ULR')
        return self


class Incident(BaseModel):
    kind: Literal['node_down', 'node_restart', 'link_down', 'surge', 'latency', 'loss']
    target: str
    start: float = Field(default=20, ge=0, le=599)
    duration: float = Field(default=20, gt=0, le=600)
    magnitude: float = Field(default=3, ge=0, le=100)
    direction: Literal['both', 'forward', 'reverse'] = 'both'
    detection_ms: float | None = Field(default=None, ge=0, le=30000)
    recovery_ms: float | None = Field(default=None, ge=0, le=30000)


class Project(BaseModel):
    name: str = Field(default='Core Network / Research', max_length=100)
    nodes: list[Node] = Field(min_length=2, max_length=60)
    links: list[Link] = Field(max_length=150)
    flows: list[Flow] = Field(min_length=1, max_length=30)
    incidents: list[Incident] = Field(default_factory=list, max_length=30)
    duration: int = Field(default=60, ge=10, le=600)
    seed: int = Field(default=42, ge=0, le=2147483647)
    window: int = Field(default=5, ge=1, le=30)
    engine: Literal['procedures', 'messages'] = 'procedures'
    routing_mode: Literal['shortest', 'loadshare'] = 'shortest'
    trace_limit: int = Field(default=1500, ge=100, le=20000)
    label_policy: Literal['affected', 'scheduled', 'degradation'] = 'affected'
    degradation_latency_ms: float = Field(default=200, ge=1, le=60000)
    control_plane: bool = True
    routing_knowledge: Literal['distributed','shared'] = 'distributed'
    route_refresh_ms: float = Field(default=5000,ge=100,le=60000)

    @model_validator(mode='after')
    def consistent(self):
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError('Идентификаторы узлов должны быть уникальными')
        for group in (self.links, self.flows):
            if len({x.id for x in group}) != len(group):
                raise ValueError('Идентификаторы связей/потоков должны быть уникальными')
            for x in group:
                if x.source not in ids or x.target not in ids or x.source == x.target:
                    raise ValueError('Некорректные конечные узлы связи/потока')
        for e in self.incidents:
            targets = ids if e.kind in ('node_down', 'node_restart') else {f.id for f in self.flows} if e.kind == 'surge' else {l.id for l in self.links}
            if e.target not in targets:
                raise ValueError('Не найден объект сценария: ' + e.target)
            if e.start >= self.duration:
                raise ValueError('Событие начинается после окончания эксперимента')
        for n in self.nodes:
            for destinations in n.routing_table.values():
                if not destinations or len(destinations)>60 or any(x not in ids or x==n.id for x in destinations):
                    raise ValueError('Таблица маршрутизации содержит неизвестный следующий узел')
            if any(pc not in {x.point_code for x in self.nodes} for pc in n.gtt.values()):
                raise ValueError('GTT ссылается на неизвестный point code')
        by_id={n.id:n for n in self.nodes}
        for f in self.flows:
            if f.operation in FIVEG_OPERATIONS:
                if self.engine!='procedures':raise ValueError('5G требует движок процедур v2')
                if by_id[f.source].kind!='gNB' or by_id[f.target].kind!='AMF':raise ValueError('Процедура 5G начинается на gNB и адресуется AMF')
                if f.data_network and (f.data_network not in ids or by_id[f.data_network].kind!='DN'):raise ValueError('Сеть данных должна ссылаться на DN')
                for kind,nid in f.nf_bindings.items():
                    if nid not in by_id or by_id[nid].kind!=kind:raise ValueError('Привязка NF должна указывать на узел соответствующего типа')
        max_rate = sum(f.rate * (1 + f.profile_amplitude) * (3 if f.traffic_profile=='bursty' else 1) for f in self.flows)
        surge = max([e.magnitude for e in self.incidents if e.kind == 'surge'] + [1])
        if max_rate * self.duration * max(1, surge) > 350000:
            raise ValueError('Слишком большой эксперимент: уменьшите длительность или интенсивность (лимит 350 000 сообщений)')
        if self.engine=='procedures' and sum(f.rate*(1+f.max_retries)*(32 if f.operation=='5G Registration + PDU Session' else 16 if f.operation in FIVEG_OPERATIONS else 2 if f.procedure=='registration' else 1) for f in self.flows)*self.duration*max(1,surge)>700000:
            raise ValueError('Слишком большой эксперимент с учётом этапов и повторов (лимит 700 000 попыток)')
        if sum(f.rate*f.data_packets for f in self.flows if f.protocol=='NGAP' and 'PDU' in f.operation and f.user_plane_enabled)*self.duration*max(1,surge)>500000:raise ValueError('Уменьшите пользовательскую нагрузку: лимит 500 000 проверочных пакетов')
        return self
