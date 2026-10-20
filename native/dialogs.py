import json
from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox, QLineEdit, QLabel, QMessageBox, QTabWidget, QWidget, QScrollArea, QPlainTextEdit, QCheckBox
from lab.schema import Node, Link, Flow, NODE_KINDS, LINK_PROTOCOLS, FLOW_PROTOCOLS, OPERATIONS
from .widgets import spin, combo
from .node_icons import node_kind_combo

KINDS=NODE_KINDS
INCIDENTS=[('node_down','Отказ узла'),('node_restart','Перезапуск и потеря состояния'),('link_down','Разрыв канала'),('surge','Всплеск нагрузки'),('latency','Изменение задержки'),('loss','Изменение потерь'),('loss_absolute','Вероятность потери'),('node_capacity','Оставшаяся мощность'),('node_delay','Замедление обработки'),('node_error','Ошибки приложения')]


class Editor(QDialog):
    def __init__(self,title,parent=None):
        super().__init__(parent);self.setWindowTitle(title);self.setMinimumWidth(430)
        self.root=QVBoxLayout(self);self.root.setContentsMargins(25,22,25,22);self.root.setSpacing(18)
        self.tabs=QTabWidget();self.root.addWidget(self.tabs);self.tab('Основные')
        self.fields={}
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Применить');self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName('Primary')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена')
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject)
        self.root.addWidget(self.buttons)
    def tab(self,title):
        scroll=QScrollArea();scroll.setWidgetResizable(True);page=QWidget();scroll.setWidget(page)
        self.form=QFormLayout(page);self.form.setHorizontalSpacing(20);self.form.setVerticalSpacing(13)
        self.tabs.addTab(scroll,title)
        self.resize(650,680)
    def numeric(self,data,spec):
        for key,title,lo,hi,dec,unit in spec:self.field(key,title,spin(data[key],lo,hi,dec,unit))
    def field(self,key,title,widget):
        widget.setAccessibleName(title);self.fields[key]=widget;self.form.addRow(title,widget);return widget
    def values(self):
        result={}
        for key,w in self.fields.items():
            if isinstance(w,QLineEdit):result[key]=w.text().strip()
            elif isinstance(w,QCheckBox):result[key]=w.isChecked()
            elif isinstance(w,QPlainTextEdit):result[key]=json.loads(w.toPlainText())
            elif hasattr(w,'currentData'):result[key]=w.currentData()
            else:result[key]=w.value()
        if result.get('observer_detection_ms',0) is not None and result.get('observer_detection_ms',0)<0:result['observer_detection_ms']=None
        if result.get('shutdown_at_s',0) is not None and result.get('shutdown_at_s',0)<0:result['shutdown_at_s']=None
        if 'peer_routing_context' in result:result['peer_routing_context']=int(result['peer_routing_context']) if result['peer_routing_context'] else None
        return result


def node_editor(parent,node=None):
    n=Node.model_validate(dict(id='edit',x=0,y=0,**(node or dict(name='New network node',kind='STP',capacity=800,queue_limit=100,processing_ms=2))) if not node else node).model_dump()
    d=Editor('Сетевой элемент',parent)
    name=d.field('name','Название',QLineEdit(n['name']));name.setMaxLength(80)
    d.field('kind','Тип элемента',node_kind_combo(n['kind']))
    d.field('capacity','Общий предел допуска',spin(n['capacity'],1,100000,1,'msg/s'))
    d.field('processing_ms','Обработка',spin(n['processing_ms'],0,1000,2,'ms'))
    d.field('queue_limit','Очередь',spin(n['queue_limit'],0,10000,0,'msg'))
    d.field('workers','Параллельные обработчики',spin(n['workers'],1,128))
    d.tab('Обслуживание')
    d.field('service_distribution','Распределение времени',combo([('constant','Постоянное'),('exponential','Экспоненциальное'),('lognormal','Логнормальное')],n['service_distribution']))
    d.numeric(n,[('service_cv','CV логнормального времени',0,3,2,''),('database_ms','Задержка при промахе кэша',0,1000,2,'ms'),('cache_hit_ratio','Доля попаданий в кэш',0,1,3,''),('error_rate','Вероятность ошибки ответа',0,1,4,'')])
    d.numeric(n,[('detection_ms','Обнаружение отказа',0,30000,1,'ms'),('switch_ms','Переключение маршрута',0,10000,1,'ms'),('recovery_ms','Восстановление доступности',0,30000,1,'ms'),('recovery_ramp_ms','Плавный возврат мощности',0,30000,1,'ms')])
    d.field('advertisement_delay_ms','Задержка распространения маршрутов',spin(n['advertisement_delay_ms'],0,30000,1,'ms'))
    d.field('observer_detection_ms','Локальное обнаружение (-1 = как у ресурса)',spin(n['observer_detection_ms'] if n['observer_detection_ms'] is not None else -1,-1,30000,1,'ms'))
    d.tab('Адреса и маршруты')
    for key,title in [('point_code','Point code'),('realm','Realm'),('host','Host / NF endpoint (пусто = ID)')]:d.field(key,title,QLineEdit(n[key]))
    for key,title in [('routing_table','Маршруты: PC/realm/GT:префикс/* → [ID]'),('gtt','GTT этого узла: префикс → point code'),('applications','Diameter Application IDs')]:
        w=QPlainTextEdit(json.dumps(n[key],ensure_ascii=False,indent=2));w.setMaximumHeight(110);d.field(key,title,w)
    d.tab('5G · Профиль NF')
    d.field('plmn','PLMN (MCC+MNC)',QLineEdit(n['plmn']));d.field('nf_status','Регистрация NF',combo(['REGISTERED','SUSPENDED'],n['nf_status']))
    for key,title in [('slices','Поддерживаемые S-NSSAI'),('dnns','Поддерживаемые DNN'),('services','Сервисы (пусто = по типу NF)')]:
        w=QPlainTextEdit(json.dumps(n[key],ensure_ascii=False,indent=2));w.setMaximumHeight(105);d.field(key,title,w)
    d.tab('БД, кэш и контексты')
    d.field('storage_model','Модель БД',combo([('queued','Пул соединений и конечный кэш'),('probabilistic','Сравнение: вероятность попадания')],n['storage_model']))
    d.numeric(n,[('db_connections','Соединений БД',1,128,0,''),('db_queue_limit','Очередь БД',0,10000,0,''),('db_timeout_ms','Тайм-аут БД',1,60000,1,'ms'),('cache_entries','Записей кэша данных',0,100000,0,''),('cache_ttl_s','TTL данных',.01,86400,2,'s'),('context_limit','Контекстов на узел',1,1000000,0,''),('context_ttl_s','TTL контекста',.01,86400,2,'s'),('transaction_cache_limit','Кэш ответов: записей',0,100000,0,''),('transaction_cache_ttl_s','Кэш ответов: TTL',.01,86400,2,'s')])
    d.tab('5G · Правила UPF')
    from .service_lists import ConfigList
    project=getattr(parent,'project',{})
    d.field('upf_default_action','При отсутствии правила',combo([('forward','Пересылать'),('drop','Отбросить')],n['upf_default_action']))
    d.numeric(n,[('upf_rate_mbps','Скорость по умолчанию (0 = без лимита)',0,100000,4,'Mbps'),('upf_burst_bytes','Бюджет всплеска',32,10000000,0,'B'),('upf_queue_limit','Очередь шейпера',0,10000,0,'')])
    d.field('upf_rules','Правила выбора и QoS',ConfigList(d,project,'upf',n['upf_rules']))
    return d


def link_editor(parent,project,link=None):
    l=Link.model_validate(link or dict(id='edit',source=project['nodes'][0]['id'],target=project['nodes'][1]['id'])).model_dump()
    d=Editor('Соединение',parent);choices=[(n['id'],n['name']) for n in project['nodes']]
    d.field('source','Первый узел',combo(choices,l['source']));d.field('target','Второй узел',combo(choices,l['target']))
    d.field('protocol','Транспорт / интерфейс',combo(LINK_PROTOCOLS,l['protocol']))
    for key,title,lo,hi,decimal,unit in [('latency_ms','Задержка',0,5000,2,'ms'),('jitter_ms','Джиттер σ',0,1000,2,'ms'),('bandwidth_mbps','Полоса',.001,100000,3,'Mbps'),('loss','Вероятность потери',0,1,4,'')]:d.field(key,title,spin(l[key],lo,hi,decimal,unit))
    d.field('reverse_latency_ms','Обратная задержка',spin(l['reverse_latency_ms'] if l['reverse_latency_ms'] is not None else l['latency_ms'],0,5000,2,'ms'))
    d.field('reverse_loss','Обратные потери',spin(l['reverse_loss'] if l['reverse_loss'] is not None else l['loss'],0,1,4))
    d.tab('Транспорт')
    d.field('path_group','Группа резервных путей',QLineEdit(l['path_group']))
    d.numeric(l,[('buffer_bytes','Буфер направления',1500,100000000,0,'B'),('mtu','MTU',256,9000,0,'B'),('header_bytes','Заголовки',16,128,0,'B'),('loss_correlation','Корреляция серий потерь',0,.99,2,''),('streams','Потоки упорядочивания',1,256,0,''),('cwnd_packets','Начальное окно',1,128,0,'pkt'),('transport_retries','Повторы транспорта',0,6,0,''),('rto_ms','RTO initial',10,10000,1,'ms'),('rto_min_ms','RTO min',1,10000,1,'ms'),('rto_max_ms','RTO max',10,30000,1,'ms')])
    d.tab('Состояния')
    d.field('asp_state','M3UA ASP',combo(['ACTIVE','INACTIVE','DOWN'],l['asp_state']));d.field('peer_state','Peer / 5G association',combo(['OPEN','CLOSED'],l['peer_state']))
    d.numeric(l,[('routing_context','M3UA Routing Context',0,2147483647,0,''),('startup_ms','Установление ассоциации',0,30000,1,'ms'),('detection_ms','Обнаружение отказа',0,30000,1,'ms'),('switch_ms','Переключение маршрута',0,10000,1,'ms'),('recovery_ms','Восстановление доступности',0,30000,1,'ms')])
    d.tab('Управляющий обмен')
    d.numeric(l,[('heartbeat_ms','Интервал heartbeat / watchdog',50,60000,1,'ms'),('control_timeout_ms','Таймер управляющего запроса',10,30000,1,'ms'),('control_retries','Повторы управляющего запроса',0,4,0,''),('reconnect_ms','Пауза восстановления ассоциации',10,30000,1,'ms'),('control_bytes','Размер управляющего сообщения',32,9000,0,'B')])
    d.field('peer_routing_context','M3UA: routing context пира (пусто = тот же)',QLineEdit('' if l['peer_routing_context'] is None else str(l['peer_routing_context'])))
    d.field('shutdown_at_s','Штатное закрытие (-1 = не закрывать)',spin(-1 if l['shutdown_at_s'] is None else l['shutdown_at_s'],-1,599,2,'s'))
    return d


def flow_editor(parent,project,flow=None):
    f=Flow.model_validate(flow or dict(project['flows'][0],id='edit',rate=50)).model_dump()
    d=Editor('Поток сигнализации',parent);choices=[(n['id'],n['name']) for n in project['nodes']]
    d.field('source','Источник',combo(choices,f['source']));d.field('target','Назначение',combo(choices,f['target']))
    d.field('protocol','Домен маршрутизации',combo(FLOW_PROTOCOLS,f['protocol']))
    d.field('operation','Операция',combo(OPERATIONS,f['operation']))
    d.field('procedure','Процедура',combo([('single','Авто / запрос → ответ'),('registration','MAP/Diameter: auth → регистрация')],f['procedure']))
    d.field('rate','Новые процедуры',spin(f['rate'],0,3000,1,'proc/s'))
    d.field('message_bytes','Размер сообщения',spin(f['message_bytes'],32,65536,0,'B'))
    d.field('timeout_ms','Тайм-аут',spin(f['timeout_ms'],1,30000,1,'ms'))
    d.numeric(f,[('response_bytes','Размер ответа',32,65536,0,'B'),('max_retries','Повторы процедуры',0,4,0,''),('retry_backoff_ms','Пауза перед повтором',0,10000,1,'ms')])
    d.tab('Абоненты и нагрузка')
    d.field('traffic_profile','Профиль',combo([('poisson','Пуассоновский'),('periodic','Периодическая активность'),('bursty','Короткие всплески')],f['traffic_profile']))
    d.numeric(f,[('subscribers','Размер группы абонентов',1,1000000,0,''),('profile_period_s','Период активности',1,86400,1,'s'),('profile_amplitude','Амплитуда профиля',0,.95,2,''),('message_size_cv','CV размера сообщения',0,2,2,''),('priority','Приоритет (0 = высший)',0,2,0,'')])
    d.tab('Маршрутизация')
    for key,title in [('destination_pc','Destination point code'),('global_title','SCCP Global Title'),('destination_realm','Destination realm'),('destination_host','Destination host')]:d.field(key,title,QLineEdit(f[key]))
    d.numeric(f,[('application_id','Diameter Application ID',0,2147483647,0,''),('routing_context','M3UA Routing Context',0,2147483647,0,'')])
    d.tab('5G · Сервисы и срез')
    for key,title in [('plmn','PLMN (MCC+MNC)'),('snssai','S-NSSAI (SST-SD)'),('dnn','DNN')]:d.field(key,title,QLineEdit(f[key]))
    for key,title in [('use_nrf','Discovery через NRF'),('use_nssf','Выбор среза через NSSF'),('use_pcf','Политики PDU через PCF'),('initial_registered','Для отдельной PDU: UE уже зарегистрирован')]:
        w=QCheckBox();w.setChecked(f[key]);d.field(key,title,w)
    d.field('service_timeout_ms','Таймер внутреннего RPC',spin(f['service_timeout_ms'],1,30000,1,'ms'))
    w=QPlainTextEdit(json.dumps(f['nf_bindings'],ensure_ascii=False,indent=2));w.setMaximumHeight(110);d.field('nf_bindings','Привязки: тип NF → ID узла',w)
    d.tab('5G · Пользовательский трафик')
    enabled=QCheckBox();enabled.setChecked(f['user_plane_enabled']);d.field('user_plane_enabled','Проверять передачу после PDU',enabled)
    d.field('data_network','Сеть данных',combo([('','Автоматически по DNN')]+[(n['id'],n['name']) for n in project['nodes'] if n['kind']=='DN'],f['data_network']))
    d.numeric(f,[('data_packets','Проверочных пакетов на сессию',1,1000,0,''),('data_interval_ms','Интервал пакетов',1,60000,1,'ms'),('data_bytes','Полезная нагрузка пакета',32,65536,0,'B'),('data_timeout_ms','Таймер проверки доставки',1,30000,1,'ms')])
    d.field('application_profile','Профиль приложения',combo([('probe','Равномерные проверки'),('interactive','Интерактивный · случайные интервалы'),('video','Видео · активность / паузы'),('bulk','Передача файла · интенсивный поток')],f['application_profile']))
    d.numeric(f,[('video_on_s','Видео: активность',.01,600,2,'s'),('video_off_s','Видео: пауза',0,600,2,'s'),('data_priority','Приоритет данных (0 = высший)',0,2,0,''),('qos_budget_ms','Бюджет RTT (0 = не оценивать)',0,60000,1,'ms'),('session_lifetime_s','Срок PDU (0 = только TTL)',0,86400,2,'s')])
    d.tab('5G · Мобильность')
    from .service_lists import ConfigList
    d.field('mobility','Перемещения группы UE',ConfigList(d,project,'mobility',f['mobility']))
    def operation_changed():
        op=d.fields['operation'].currentData();proto='NGAP' if op.startswith('5G ') else 'Diameter' if op.startswith('Diameter') else 'M3UA'
        d.fields['protocol'].setCurrentIndex(d.fields['protocol'].findData(proto))
        if op.startswith('5G '):d.fields['procedure'].setCurrentIndex(0)
    d.fields['operation'].currentIndexChanged.connect(operation_changed)
    return d


def incident_editor(parent,project,incident=None):
    e=incident or dict(kind='node_down',target=project['nodes'][0]['id'],start=project['duration']/3,duration=project['duration']/3,magnitude=4)
    d=Editor('Событие сценария',parent)
    k=d.field('kind','Тип события',combo(INCIDENTS,e['kind']))
    target=d.field('target','Объект воздействия',combo([]))
    def targets():
        kind=k.currentData()
        items=project['nodes'] if kind in ('node_down','node_restart','node_capacity','node_delay','node_error') else project['flows'] if kind=='surge' else project['links']
        target.clear()
        for item in items:target.addItem(item.get('name',item['id']),item['id'])
        idx=target.findData(e['target'])
        if idx>=0:target.setCurrentIndex(idx)
        d.fields['magnitude'].setEnabled(kind not in ('node_down','node_restart','link_down'))
        d.fields['magnitude'].setRange(.01 if kind=='node_capacity' else 1 if kind=='node_delay' else 0,1 if kind in ('node_capacity','node_error','loss_absolute') else 100)
        channel=kind in ('link_down','latency','loss','loss_absolute');d.fields['direction'].setEnabled(channel)
        if not channel:d.fields['direction'].setCurrentIndex(0)
    d.field('start','Начало',spin(e['start'],0,project['duration']-.01,2,'s'))
    d.field('duration','Длительность',spin(e['duration'],.01,600,2,'s'))
    d.field('magnitude','Множитель',spin(e['magnitude'],0,100,2))
    d.field('direction','Направление канала',combo([('both','Оба'),('forward','Источник → назначение'),('reverse','Назначение → источник')],e.get('direction','both')))
    d.field('detection_ms','Обнаружение отказа',spin(e.get('detection_ms') if e.get('detection_ms') is not None else 300,0,30000,1,'ms'))
    d.field('recovery_ms','Восстановление доступности',spin(e.get('recovery_ms') if e.get('recovery_ms') is not None else 200,0,30000,1,'ms'))
    k.currentIndexChanged.connect(targets);targets()
    return d
