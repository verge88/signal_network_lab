"""Pure projections of a recorded journal; never replay the model in the viewer."""
import json

KINDS=dict(procedure_start='Начало процедуры',procedure_finished='Завершение процедуры',message_created='Создано сообщение',
    message_arrived='Сообщение обработано адресатом',message_failed='Сообщение отклонено',route_selected='Выбран маршрут',route_unavailable='Маршрут отсутствует',
    cpu_queued='Очередь CPU',cpu_started='Начало CPU',cpu_finished='Конец CPU',database_queued='Очередь БД',database_started='Начало БД',
    database_finished='Конец БД',database_bypassed='Попадание в кэш',database_aborted='Прерывание БД',transport_queued='Очередь транспорта',
    packet_sent='Отправка DATA',channel_transmit='Передача по каналу',channel_drop='Потеря на канале',packet_received='Получен DATA',
    ack_sent='Отправлено ACK',ack_received='Получено ACK',timer_set='Установлен таймер',timer_expired='Истёк таймер',timer_cancelled='Таймер отменён',
    procedure_retry='Повтор процедуры / RPC',transport_retry='Повтор DATA',transport_exhausted='Исчерпаны повторы транспорта',context_initial='Исходный контекст',
    context_changed='Изменение контекста',nf_selected='Выбрана функция 5G',gtt_translation='Локальный перевод GTT',gtt_forward='Передача к узлу GTT',node_fault='Отказ узла')
KINDS.update(cpu_aborted='CPU прерван отказом',cpu_queue_aborted='Очередь CPU прервана отказом')
KINDS['user_data_finished']='Завершён пользовательский пакет'
KINDS['pdu_state']='Состояние PDU-сессии'
FIELDS=dict(node='Узел',source='Отправитель',target='Адресат',receiver='Получатель перехода',message_id='Сообщение',rpc_id='RPC',parent_rpc='Родительский RPC',
    operation='Операция',protocol='Протокол',role='Роль',reason='Причина',result='Результат',status='Статус',bytes='Размер, B',queue_depth='Глубина очереди',
    wait_ms='Ожидание, ms',service_ms='Обработка, ms',done_ms='Запланировано завершение, ms',deadline_ms='Срок таймера, ms',timer_id='Таймер',timer_type='Тип таймера',
    attempt='Номер повтора',step='Этап',backoff_ms='Пауза повтора, ms',scheduled_ms='Повтор запланирован, ms',link='Канал',transfer_id='Передача',packet_index='Фрагмент',
    rto_ms='RTO, ms',cwnd='Окно перегрузки',flight='В полёте',running='Занято CPU',workers='Работников CPU',priority='Приоритет',
    serialization_ms='Сериализация, ms',propagation_ms='Распространение, ms',tx_start_ms='Начало передачи, ms',tx_end_ms='Конец передачи, ms',arrival_ms='Доставка запланирована, ms',
    receiver_delivered='Уже передано получателю',pending_arrivals='Приходов DATA в ожидании',accepted='ACK принят отправителем',duplicate='Повторный DATA',
    algorithm='Алгоритм',knowledge='Представление доступности',selector='Абонент для распределения',hops='Правила и стоимость переходов',
    before='Контекст до',after='Контекст после',context_key='Ключ контекста',store='Хранилище',actor_procedure='Изменившая процедура',
    previous_deadline_ms='Предыдущий срок TTL, ms',storage_model='Модель БД',probabilistic_database_in_cpu='Вероятностная БД включена во время CPU',
    database_task='Запрос БД',active_connections='Занято соединений БД',cache_hit='Попадание в кэш',elapsed_ms='Прошло, ms',nf_kind='Тип NF',selected='Выбран экземпляр',
    service='Сервис',pinned='Закреплённый экземпляр',global_title='Global Title',matching_prefix='Префикс GTT',translated_target='Адресат после GTT',next_hop='Следующий переход',
    subscriber='Абонент',category='Категория',chunks='Фрагментов',stream='Поток транспорта',pending_gtt='Ожидается перевод GTT',forwarder='Маршрутизатор')
FIELDS.update(user_packet_id='Пользовательский пакет',rtt_ms='RTT, ms',embedded_database_ms='БД внутри CPU, ms',admission_wait_ms='Ожидание допуска транспорта, ms')
FIELDS.update(pdu_session_id='PDU Session ID',state='Состояние сессии')


def title(event):return KINDS.get(event['kind'],event['kind'])


def causal_chain(session,event_id):
    events={event['id']:event for event in session.get('events',[])};result=[];seen=set()
    while event_id in events and event_id not in seen:
        seen.add(event_id);event=events[event_id];result.append(event);event_id=event.get('cause_event')
    return list(reversed(result))


def snapshot(session,index):
    timers={};contexts={};timings={};resources={};db_started=set();now=session['events'][index]['time_ms'] if session.get('events') and index>=0 else 0.
    for event in session.get('events',[])[:max(0,index+1)]:
        kind=event['kind'];timer=event.get('timer_id');mid=event.get('message_id');node=event.get('node')
        if kind=='timer_set' and timer:timers[timer]=dict(event,state='active')
        elif kind in ('timer_cancelled','timer_expired') and timer:
            item=timers.setdefault(timer,dict(event));item.update(state='cancelled' if kind=='timer_cancelled' else 'expired',last_event=event['id'])
        if kind in ('context_initial','context_changed'):
            key=(event['store'],tuple(event['context_key']))
            contexts[key]=dict(node=node,store=event['store'],key=event['context_key'],value=event.get('after'),deadline_ms=event.get('deadline_ms'),reason=event.get('reason','initial'),event=event['id'])
        if mid is not None:
            row=timings.setdefault(mid,dict(message_id=mid,operation=event.get('operation',''),cpu_wait_ms=0.,db_wait_ms=0.,channel_wait_ms=0.,cpu_service_ms=0.,db_service_ms=0.))
            if event.get('operation'):row['operation']=event['operation']
            if kind in ('cpu_queued','database_queued'):
                resource='Очередь CPU' if kind=='cpu_queued' else 'Очередь БД';resources[(resource,mid,node)]=dict(start=event['time_ms'],node=node,resource=resource,message_id=mid)
            if kind=='cpu_started':
                row['cpu_wait_ms']+=event['wait_ms'];resources.pop(('Очередь CPU',mid,node),None);resources[('CPU',mid,node)]=dict(start=event['time_ms'],node=node,resource='CPU',message_id=mid)
            elif kind in ('cpu_finished','cpu_aborted'):row['cpu_service_ms']+=event['service_ms'];resources.pop(('CPU',mid,node),None)
            elif kind=='cpu_queue_aborted':row['cpu_wait_ms']+=event['wait_ms'];resources.pop(('Очередь CPU',mid,node),None)
            elif kind=='database_started':
                db_started.add(event['database_task']);row['db_wait_ms']+=event['wait_ms'];resources.pop(('Очередь БД',mid,node),None);resources[('БД',mid,node)]=dict(start=event['time_ms'],node=node,resource='БД',message_id=mid)
            elif kind in ('database_finished','database_aborted'):
                if event.get('database_task') not in db_started:row['db_wait_ms']+=event['wait_ms']
                row['db_service_ms']+=event['service_ms'];resources.pop(('БД',mid,node),None);resources.pop(('Очередь БД',mid,node),None)
            elif kind=='channel_transmit':row['channel_wait_ms']+=min(event['wait_ms'],max(0,now-event['time_ms']))
        if kind=='node_fault':resources={key:value for key,value in resources.items() if value['node']!=node}
    for resource in resources.values():
        row=timings[resource['message_id']];elapsed=max(0,now-resource['start'])
        field={'CPU':'cpu_service_ms','БД':'db_service_ms','Очередь CPU':'cpu_wait_ms','Очередь БД':'db_wait_ms'}[resource['resource']]
        row[field]+=elapsed
    return dict(timers=timers,contexts=list(contexts.values()),timings=list(timings.values()),resources=list(resources.values()))


def describe(session,index):
    events=session.get('events',[])
    if not events:return 'Подробный журнал этой процедуры не записан.'
    event=events[index];lines=[f'Событие {event["id"]}: {title(event)}',f'Время: {event["time_ms"]:.6f} ms',f'От начала: {event["time_ms"]-session["start_ms"]:.6f} ms']
    for key,value in event.items():
        if key in ('id','kind','time_ms','cause_event'):continue
        text='—' if value is None else 'да' if value is True else 'нет' if value is False else json.dumps(value,ensure_ascii=False,indent=2) if isinstance(value,(dict,list)) else f'{value:.6g}' if isinstance(value,float) else str(value)
        lines.append(FIELDS.get(key,key)+': '+text)
    lines.extend(['','Связанные причины (зафиксированные связи):'])
    for cause in causal_chain(session,event['id']):lines.append(f'#{cause["id"]} · {cause["time_ms"]:.3f} ms · {title(cause)}')
    lines.append('\nПорядок событий сам по себе не доказывает причинную связь. Строки с общей очередью показывают её фактическую глубину, а не приписывают всю задержку одному сообщению.')
    return '\n'.join(lines)
