"""Native WSL2 5G lab controller, separate from the virtual-time simulator."""
import json
from pathlib import Path
import zipfile
from concurrent.futures import CancelledError
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QFormLayout,QTabWidget,QLineEdit,
    QCheckBox,QPlainTextEdit,QFileDialog,QScrollArea,QHeaderView)
from lab import storage,testbed
from lab.testbed import Profile,Subscriber
from lab.testbed_agent import ue_configs,redact,names,addresses,fingerprint
from .widgets import label,button,row,combo,spin,table,populate,Chart


class TestbedDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.owner=parent;self.settings=parent.settings;self.profile=Profile();self.control_profile=None;self.pending_profile=None;self.report=None
        self.setWindowTitle('5G-стенд · Open5GS / UERANSIM');self.resize(1180,840)
        root=QVBoxLayout(self);self.notice=label('5G-стенд · реальные Linux-процессы и реальное время. Моделирование SignalLab запускается отдельно.','PanelTitle');self.notice.setWordWrap(True);root.addWidget(self.notice)
        self.history=combo([]);self.profile_name=QLineEdit(self.profile.name)
        root.addLayout(row('Профиль',self.history,button('Открыть',lambda:parent.call(self.load_profile),'folder'),button('Новый',self.new_profile,'plus'),button('Сохранить',lambda:parent.call(self.save_profile),'save')))
        self.tabs=QTabWidget();root.addWidget(self.tabs,1)
        connection=QWidget();cl=QVBoxLayout(connection);form=QFormLayout();cl.addLayout(form)
        self.distro=QLineEdit('Ubuntu');self.use_root=QCheckBox('Выполнять операции стенда от root внутри выбранного WSL-дистрибутива')
        form.addRow('Название профиля',self.profile_name);form.addRow('Дистрибутив WSL2',self.distro);form.addRow(self.use_root)
        self.paths={}
        for key,title,value in [('core_bin','Каталог бинарных файлов Open5GS','/usr/bin'),('config_dir','Исходные конфигурации Open5GS','/etc/open5gs'),('ran_bin','Каталог nr-gnb / nr-ue / nr-cli','/opt/UERANSIM/build')]:
            self.paths[key]=QLineEdit(value);form.addRow(title,self.paths[key])
        self.checks=table(['Проверка','Результат','Подробности']);cl.addWidget(self.checks,1)
        self.diagnostics=label('Проверьте среду. Исходные системные службы и их конфигурации не перезапускаются.','Muted');self.diagnostics.setWordWrap(True);cl.addWidget(self.diagnostics)
        self.scan_button=button('Найти дистрибутивы',lambda:parent.call(self.scan),'folder');self.check_button=button('Проверить среду',lambda:parent.call(lambda:self.action('preflight')),'settings',True)
        self.suggest_button=button('Использовать найденный путь',self.use_suggestion);self.suggest_button.setEnabled(False)
        cl.addLayout(row(self.scan_button,self.check_button,self.suggest_button,stretch=True));self.tabs.addTab(connection,'Linux-среда')
        network=QWidget();nl=QVBoxLayout(network);nf=QFormLayout();nl.addLayout(nf);self.fields={}
        for key,title,value in [('mcc','MCC','999'),('mnc','MNC','70'),('sd','SD · 6 hex-цифр','000001'),('dnn','DNN / APN','internet'),('ran_subnet','Подсеть RAN · IPv4 /30','10.203.0.0/30'),('ue_subnet','Подсеть UE · IPv4 /16…/28','10.45.0.0/16')]:
            self.fields[key]=QLineEdit(value);nf.addRow(title,self.fields[key])
        self.tac=spin(1,1,65535);self.sst=spin(1,1,255);nf.addRow('TAC',self.tac);nf.addRow('SST',self.sst)
        self.preview=QPlainTextEdit();self.preview.setReadOnly(True);nl.addWidget(self.preview,1)
        nl.addLayout(row(button('Проверить и показать план',lambda:parent.call(self.render_plan),'settings'),button('Экспорт конфигураций…',lambda:parent.call(self.export_configs),'download'),stretch=True));self.tabs.addTab(network,'Конфигурация SA')
        subscribers=QWidget();sl=QVBoxLayout(subscribers);sl.addWidget(label('Только тестовые IMSI и ключи. Профиль хранится в локальной базе; поля скрыты на экране, шифрование хранилища не предоставляется.','Muted'))
        self.subscribers=table(['IMSI','K · 32 hex','OPc · 32 hex']);sl.addWidget(self.subscribers,1)
        self.show_keys=QCheckBox('Показать тестовые ключи');self.show_keys.toggled.connect(self.toggle_keys)
        sl.addLayout(row(button('Добавить UE',lambda:parent.call(self.add_subscriber),'plus'),button('Убрать строку',self.remove_row,'trash'),self.show_keys,stretch=True))
        self.provision_button=button('Записать абонентов в стенд',lambda:parent.call(lambda:self.action('provision')),'save')
        self.remove_sub_button=button('Удалить выбранного из стенда',lambda:parent.call(self.remove_subscriber),'trash')
        sl.addLayout(row(self.provision_button,self.remove_sub_button,stretch=True));self.tabs.addTab(subscribers,'Тестовые абоненты')
        runtime=QWidget();rl=QVBoxLayout(runtime);self.state_note=label('Среда ещё не опрошена.');self.state_note.setWordWrap(True);rl.addWidget(self.state_note)
        self.core=table(['Компонент','Процесс','Готовность']);self.core.setMaximumHeight(230);rl.addWidget(self.core)
        self.ues=table(['IMSI','Процесс UE','Регистрация','Активных PDU','Диагностика']);rl.addWidget(self.ues,1)
        self.log_choice=combo([]);self.log_choice.currentIndexChanged.connect(self.show_log);rl.addLayout(row('Журнал',self.log_choice));self.logs=QPlainTextEdit();self.logs.setReadOnly(True);rl.addWidget(self.logs,1)
        self.runtime_buttons=[]
        actions=[('prepare','1 · Подготовить'),('start_core','2 · Ядро'),('start_ran','3 · gNB'),('start_ues','4 · UE'),('status','Обновить'),('stop','Остановить'),('cleanup','Очистить сеть')]
        line=row(stretch=False)
        for key,title in actions:
            control=button(title,lambda checked=False,a=key:parent.call(lambda:self.action(a)),primary=key=='status');line.addWidget(control);self.runtime_buttons.append(control)
        rl.addLayout(line);rl.addWidget(label('Отмена прекращает ожидание ответа. Запущенные Linux-процессы проверяйте через «Обновить»; они не являются контрольной точкой симулятора.','Muted'));self.tabs.addTab(runtime,'Запуск и диагностика')
        measurements=QWidget();ml=QVBoxLayout(measurements);self.measure_note=label('ICMP проверяется через TUN конкретной PDU-сессии до локального шлюза UPF. Это отдельная метрика, не задержка регистрации.','Muted');self.measure_note.setWordWrap(True);ml.addWidget(self.measure_note)
        self.measure_button=button('Измерить и сохранить',lambda:parent.call(self.measure),'experiment',True)
        ml.addLayout(row(self.measure_button,button('Отчёт JSON…',lambda:parent.call(self.save_report),'save'),button('Экспорт…',lambda:parent.call(self.export_visuals),'download'),stretch=True))
        self.chart=Chart();self.chart.setMaximumHeight(220);ml.addWidget(self.chart)
        self.measurements=table(['IMSI','TUN','Назначение','Передано / принято','RTT среднее, ms','RTT p95, ms','Результат']);ml.addWidget(self.measurements,1)
        self.saved=combo([]);ml.addLayout(row('История измерений',self.saved,button('Открыть',lambda:parent.call(self.open_measurement),'folder')))
        self.sim_runs=combo([]);ml.addLayout(row('Рядом с симуляцией',self.sim_runs,button('Сопоставить',lambda:parent.call(self.compare_simulation),'chart')))
        self.comparison=label('');self.comparison.setWordWrap(True);ml.addWidget(self.comparison);self.tabs.addTab(measurements,'Измерения и сравнение')
        procedures=QWidget();pl=QVBoxLayout(procedures);pl.addWidget(label('Парные события NAS из журнала UE. Отказы сохраняются; неполные и неоднозначные пары не получают выдуманную длительность.','Muted'))
        self.procedures=table(['IMSI','Процедура','Начало · часы Linux','Конец','Исход','Время, ms']);pl.addWidget(self.procedures,1);self.tabs.addTab(procedures,'Процедуры NAS')
        help_page=QScrollArea();help_page.setWidgetResizable(True);help_content=QWidget();hl=QVBoxLayout(help_content);help_page.setWidget(help_content)
        help_text=label('Порядок работы\n\n1. Выберите Ubuntu WSL2 и проверьте среду. Для интерфейсов и пространств сети включите root внутри WSL. Установка компонентов и обновление ядра здесь не выполняются.\n\n2. Укажите каталог UERANSIM, MCC/MNC, TAC, срез и непересекающиеся подсети. Если уже есть ogstun с 10.45.0.0/16, задайте другую UE-подсеть, например 10.84.0.0/24. Проверьте план.\n\n3. Добавьте тестовые абонентские профили. Нажмите «Подготовить»: создаются собственные конфигурации и изолированная RAN-сеть. Затем запишите абонентов в отдельную MongoDB-базу этого профиля.\n\n4. Запустите ядро, gNB и UE. Обновляйте состояние до регистрации и появления активных PDU. Живой процесс ещё не доказывает готовность протокола.\n\n5. Измерьте ICMP через UE TUN, сохраните отчёт. Ошибки и отсутствие данных сохраняются как ошибки, без нулевой задержки.\n\n6. «Остановить» завершает только процессы, запущенные этим профилем. «Очистить сеть» удаляет только его сетевое пространство и интерфейсы. Файлы журналов и база абонентов сохраняются. Перед изменением подготовленного профиля остановите процессы и очистите его сеть.\n\nРежим стенда работает по реальному времени. Seed симулятора и его контрольные точки к Linux-процессам не применяются. Перенос произвольного графа топологии в Open5GS не выполняется: первая версия поддерживает отдельный шаблон 5G SA. Полная инструкция: Справка → 5G-стенд на WSL2.','Muted');help_text.setWordWrap(True);hl.addWidget(help_text);hl.addStretch();self.tabs.addTab(help_page,'Порядок работы')
        root.addLayout(row(button('Отменить ожидание',parent.cancel_job),button('Справка',lambda:parent.show_help('5G-стенд на WSL2')),button('Закрыть',self.close),stretch=True))
        self.compute_controls=[self.scan_button,self.check_button,self.provision_button,self.remove_sub_button,self.measure_button,*self.runtime_buttons]
        parent.compute_buttons.extend(self.compute_controls)
        for control in self.compute_controls:control.setEnabled(not parent.active_job)
        self.set_profile(self.profile);self.refresh_history()

    def set_profile(self,p,control_profile=None,pending_profile=None):
        self.profile=p;self.control_profile=control_profile;self.pending_profile=pending_profile;self.profile_name.setText(p.name);self.distro.setText(p.distro);self.use_root.setChecked(p.root)
        for key,control in self.paths.items():control.setText(getattr(p,key))
        for key,control in self.fields.items():control.setText(getattr(p,key))
        self.tac.setValue(p.tac);self.sst.setValue(p.sst);self.subscribers.setRowCount(0)
        for sub in p.subscribers:self.add_subscriber(sub)
        self.render_plan();self.reset_diagnostics()
        self.history.setCurrentIndex(self.history.findData(p.id))

    def reset_diagnostics(self):
        self.last_state=None;self.report=None;self.log_data={};self.suggestions=[];self.suggest_button.setEnabled(False)
        for widget in (self.checks,self.core,self.ues,self.measurements,self.procedures):populate(widget,[])
        self.log_choice.clear();self.logs.clear();self.chart.set_series([]);self.comparison.clear()
        self.state_note.setText(f'Профиль: {self.profile.name} · ID {self.profile.id} · {self.profile.distro}. Среда ещё не опрошена.')
        if self.pending_profile:self.state_note.setText(self.state_note.text()+' Результат предыдущей операции неизвестен. Нажмите «Обновить».')
        self.diagnostics.setText('Проверьте среду выбранного профиля.');self.measure_note.setText('Измерения выбранного профиля ещё не открыты.')

    def values(self):
        subs=[dict(imsi=self.subscribers.cellWidget(i,0).text().strip(),key=self.subscribers.cellWidget(i,1).text().strip(),opc=self.subscribers.cellWidget(i,2).text().strip()) for i in range(self.subscribers.rowCount())]
        return Profile(id=self.profile.id,name=self.profile_name.text(),distro=self.distro.text(),root=self.use_root.isChecked(),tac=self.tac.value(),sst=self.sst.value(),subscribers=subs,**{k:v.text().strip() for k,v in {**self.paths,**self.fields}.items()})

    def add_subscriber(self,sub=None):
        if self.subscribers.rowCount()>=16:raise ValueError('Первая версия поддерживает до 16 UE')
        if not isinstance(sub,Subscriber):
            prefix=self.fields['mcc'].text()+self.fields['mnc'].text();sub=Subscriber(imsi=prefix+str(self.subscribers.rowCount()+1).zfill(15-len(prefix)))
        i=self.subscribers.rowCount();self.subscribers.insertRow(i);self.subscribers.setRowHeight(i,43)
        for j,key in enumerate(('imsi','key','opc')):
            control=QLineEdit(getattr(sub,key));control.setMaxLength(15 if j==0 else 32)
            if j and not self.show_keys.isChecked():control.setEchoMode(QLineEdit.EchoMode.Password)
            self.subscribers.setCellWidget(i,j,control)
    def remove_row(self):
        if self.subscribers.currentRow()>=0:self.subscribers.removeRow(self.subscribers.currentRow())
    def toggle_keys(self,value):
        for i in range(self.subscribers.rowCount()):
            for j in (1,2):self.subscribers.cellWidget(i,j).setEchoMode(QLineEdit.EchoMode.Normal if value else QLineEdit.EchoMode.Password)
    def render_plan(self):
        p=self.values();doc=dict(template='5G SA · Open5GS + UERANSIM',profile=testbed.public_profile(p),isolation=names(p.model_dump()),addresses=addresses(p.model_dump()),ueransim=redact(ue_configs(p.model_dump()),p.model_dump()))
        self.preview.setPlainText(json.dumps(doc,ensure_ascii=False,indent=2))
    def new_profile(self):self.set_profile(Profile())
    def save_profile(self):
        self.persist_profile(self.values())
    def persist_profile(self,p,pending=False,operation_profile=None):
        attempt=(operation_profile or p) if pending else self.pending_profile
        storage.save('testbed_profile',p.name,dict(profile=p.model_dump(),control_profile=self.control_profile.model_dump() if self.control_profile else None,pending_profile=attempt.model_dump() if attempt else None),record_id=p.id)
        self.profile=p;self.pending_profile=attempt;self.refresh_history()
    def reconcile_profile(self,p,result):
        if result.get('profile_id',p.id)!=p.id:return
        state=result.get('resource_state')
        if state not in ('absent','present'):return
        if state=='absent' and (result.get('processes_present') is not False or result.get('network',{}).get('absent') is not True):return
        try:saved=storage.get('testbed_profile',p.id)
        except KeyError:saved=dict(name=p.name,profile=p.model_dump())
        managed=p if state=='present' else None
        storage.save('testbed_profile',saved['name'],dict(profile=saved['profile'],control_profile=managed.model_dump() if managed else None,pending_profile=None),record_id=p.id)
        if self.profile.id==p.id:self.control_profile=managed;self.pending_profile=None
        return state
    def refresh_history(self):
        saved=self.saved.currentData();simulation=self.sim_runs.currentData()
        self.history.clear();self.saved.clear();self.sim_runs.clear()
        for p in storage.listing('testbed_profile'):self.history.addItem(p['name'],p['id'])
        for p in storage.listing('testbed_run'):self.saved.addItem(p['name'],p['id'])
        for p in storage.listing('run'):self.sim_runs.addItem(p['name'],p['id'])
        self.history.setCurrentIndex(self.history.findData(self.profile.id))
        if saved:self.saved.setCurrentIndex(self.saved.findData(saved))
        if simulation:self.sim_runs.setCurrentIndex(self.sim_runs.findData(simulation))
    def load_profile(self):
        if not self.history.currentData():raise ValueError('Нет сохранённых профилей')
        record=storage.get('testbed_profile',self.history.currentData());managed=record.get('control_profile');pending=record.get('pending_profile')
        self.set_profile(Profile.model_validate(record['profile']),Profile.model_validate(managed) if managed else None,Profile.model_validate(pending) if pending else None)
    def scan(self):
        self.owner.start_job('Поиск WSL2-дистрибутивов',lambda progress,cancel:testbed.distributions(cancel),self.accept_distributions)
    def accept_distributions(self,rows):
        populate(self.checks,[[r['name'],'WSL '+str(r['version']),r['state']] for r in rows]);self.diagnostics.setText('Укажите имя дистрибутива WSL2 из списка. docker-desktop не является подготовленной средой стенда.')
    def action(self,action,**kwargs):
        if self.owner.active_job:raise ValueError('Дождитесь завершения текущей операции')
        anchor=self.pending_profile or self.control_profile
        if self.pending_profile and action not in ('status','preflight'):raise ValueError('Результат предыдущей операции ещё не подтверждён. Сначала нажмите «Обновить», чтобы проверить состояние Linux.')
        p=anchor if action in ('status','stop','cleanup') and anchor else self.values()
        if action not in ('preflight','status','stop','cleanup'):
            if self.control_profile and fingerprint(p.model_dump())!=fingerprint(self.control_profile.model_dump()):raise ValueError('Параметры подготовленного профиля изменены. Сначала остановите и очистите его сеть; эти действия используют сохранённую конфигурацию.')
        if action not in ('preflight','status'):
            settings=self.profile if action in ('stop','cleanup') and anchor else p
            self.persist_profile(settings,pending=True,operation_profile=p)
        self.owner.start_job('5G-стенд · '+action,lambda progress,cancel:self.operation(p,action,progress,cancel,**kwargs),lambda outcome:self.complete_operation(outcome,p,action),result_on_cancel=True)
    @staticmethod
    def operation(p,action,progress,cancel,**kwargs):
        try:
            result=testbed.measure(p,progress,cancel) if action=='measure' else testbed.request(p,action,progress,cancel,**kwargs)
            return dict(ok=True,result=result)
        except Exception as error:
            status=None
            if isinstance(error,testbed.AgentError) and action not in ('status','preflight'):
                try:status=testbed.request(p,'status',cancel=cancel)
                except Exception:pass
            return dict(ok=False,error=str(error) or ('Операция отменена' if isinstance(error,CancelledError) else type(error).__name__),status=status)
    def complete_operation(self,outcome,p,action):
        evidence=outcome.get('result') if outcome['ok'] else outcome.get('status')
        confirmed=self.reconcile_profile(p,evidence) if evidence else None
        if evidence and action!='preflight' and not confirmed:evidence=dict(evidence,resource_state='unknown')
        if outcome['ok']:
            if action=='measure':self.accept_measurement(evidence,p.id)
            else:self.accept_result(evidence,p.id,action,p)
            return
        unknown=not confirmed
        message=outcome['error']+(' Состояние Linux не подтверждено. Нажмите «Обновить» перед повторной операцией.' if unknown else '')
        if self.profile.id==p.id:
            if evidence:self.accept_result(evidence,p.id,'status',p)
            if unknown:self.state_note.setText(f'ID {p.id} · {p.distro}: состояние Linux не подтверждено. Нажмите «Обновить».')
        self.owner.error(message)
    def accept_result(self,result,profile_id=None,action=None,request_profile=None):
        bound_id=profile_id or result.get('profile_id',self.profile.id)
        if result.get('profile_id',bound_id)!=bound_id:return
        if bound_id!=self.profile.id:return
        if 'checks' in result:
            populate(self.checks,[[r['check'],'Готово' if r['ok'] else 'Не готово',r['detail']] for r in result['checks']])
            paths=', '.join(result.get('suggested_ran_bins',[]));self.diagnostics.setText(('Среда готова.' if result['ready'] else 'Исправьте отмеченные пункты.')+(' Найден UERANSIM: '+paths if paths else '')+' Ядро: '+result.get('kernel',''))
            self.suggestions=result.get('suggested_ran_bins',[]);self.suggest_button.setEnabled(bool(self.suggestions))
        else:
            self.last_state=result;populate(self.core,[[r['component'],'Работает' if r['alive'] else 'Нет','Открыт SBI / процесс UPF' if r['ready'] else 'Не подтверждена'] for r in result.get('core',[])])
            populate(self.ues,[[r['imsi'],'Работает' if r['alive'] else 'Нет','Да' if r['registered'] else 'Нет' if r['registered'] is False else 'Нет данных',len(r['sessions']),r.get('error','')] for r in result.get('ues',[])])
            self.log_data=result.get('logs',{});self.log_choice.clear()
            for name in self.log_data:self.log_choice.addItem(name,name)
            self.show_log()
            state='Подготовлен' if result.get('prepared') else 'Не подготовлен'
            if result.get('stopped'):state='Процессы этого профиля остановлены'
            if result.get('cleaned'):state='Изолированная сеть этого профиля удалена'
            if result.get('files'):state='Подготовлены конфигурации и изолированная сеть'
            if result.get('subscribers'):state='Тестовые профили абонентов записаны'
            if result.get('resource_state')=='absent':state+=' · сеть и процессы отсутствуют; настройки можно менять'
            if result.get('resource_state')=='unknown':state+=' · наличие управляемых ресурсов не подтверждено; требуется проверка Linux'
            if 'gnb_alive' in result:state+=' · gNB: '+('работает' if result['gnb_alive'] else 'не запущен')
            if 'gnb_connected' in result:state+=' · NGAP: '+('подключён' if result['gnb_connected'] else 'не подключён' if result['gnb_connected'] is False else 'не подтверждён')
            bound=request_profile or self.control_profile or self.profile
            self.state_note.setText(f'{bound.name} · ID {bound.id} · {bound.distro} · '+state);self.tabs.setCurrentIndex(3)
    def show_log(self,*args):self.logs.setPlainText(getattr(self,'log_data',{}).get(self.log_choice.currentData(),''))
    def use_suggestion(self):
        if getattr(self,'suggestions',[]):self.paths['ran_bin'].setText(self.suggestions[0])
    def remove_subscriber(self):
        i=self.subscribers.currentRow()
        if i<0:raise ValueError('Выберите абонента')
        self.action('remove_subscriber',imsi=self.subscribers.cellWidget(i,0).text())
    def measure(self):
        if self.owner.active_job:raise ValueError('Дождитесь завершения текущей операции')
        if self.pending_profile:raise ValueError('Сначала нажмите «Обновить», чтобы проверить результат предыдущей Linux-операции')
        p=self.values()
        if self.control_profile and fingerprint(p.model_dump())!=fingerprint(self.control_profile.model_dump()):raise ValueError('Для измерений восстановите параметры подготовленного профиля')
        self.persist_profile(p,pending=True)
        self.owner.start_job('5G-стенд · измерение пользовательского обмена',lambda progress,cancel:self.operation(p,'measure',progress,cancel),lambda outcome:self.complete_operation(outcome,p,'measure'),result_on_cancel=True)
    def accept_measurement(self,record,profile_id=None):
        if profile_id and (profile_id!=self.profile.id or record.get('profile_id',profile_id)!=profile_id):return
        self.report=record;fmt=lambda v:'—' if v is None else f'{v:.4g}'
        populate(self.measurements,[[r['imsi'],r.get('interface','—'),r.get('target','—'),str(r.get('sent','—'))+' / '+str(r.get('received','—')),fmt(r.get('rtt_mean_ms')),fmt(r.get('rtt_p95_ms')),r.get('error','Завершено' if r.get('exit_code')==0 else 'Ошибка / потери')] for r in record.get('data_tests',[])])
        self.chart.set_series([dict(name=r['imsi'],points=[(i+1,v) for i,v in enumerate(r.get('rtt_samples_ms',[]))]) for r in record.get('data_tests',[])[:8]])
        populate(self.procedures,[[group['imsi'],event['procedure'],event['start'],event['end'],event['status'],event['latency_ms']] for group in record.get('procedure_observations',[]) for event in group['events']])
        report_profile=record.get('profile',{});identity=record.get('profile_id',report_profile.get('id','не указан'))
        self.measure_note.setText(f'Источник: {record.get("source","не указан")} · профиль {report_profile.get("name","")} · ID {identity}. ICMP через UE TUN до локального UPF; NAS отдельно.')
        self.refresh_history();self.tabs.setCurrentIndex(4)
    def open_measurement(self):
        if not self.saved.currentData():raise ValueError('Нет сохранённых измерений')
        self.accept_measurement(storage.get('testbed_run',self.saved.currentData()))
    def save_report(self):
        if not self.report:raise ValueError('Сначала выполните измерение')
        path,_=QFileDialog.getSaveFileName(self,'Отчёт 5G-стенда','testbed-measurement.json','JSON (*.json)')
        if path:Path(path).write_text(json.dumps(self.report,ensure_ascii=False,indent=2),encoding='utf-8')
    def compare_simulation(self):
        if not self.report or not self.sim_runs.currentData():raise ValueError('Выберите измерение стенда и сохранённый расчёт')
        simulation=storage.get('run',self.sim_runs.currentData());summary=simulation['summary']
        self.comparison.setText(f'Симуляция: {simulation["name"]}; модельное p95 {summary["latency_p95"]:.4g} ms, успешность {summary["delivery_ratio"]:.2%}. Стенд: ICMP RTT до шлюза UPF. Границы и метод измерения различаются: ошибка p95 и калибровка по этим значениям не рассчитываются. Для проверки достоверности нужны независимые измерения одинаковых процедур и согласованный план.')
    def export_configs(self):
        p=self.values();path,_=QFileDialog.getSaveFileName(self,'Конфигурации с тестовыми K/OPc','testbed-configs.zip','ZIP (*.zip)')
        if path:
            with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
                for name,config in ue_configs(p.model_dump()).items():archive.writestr(name,json.dumps(config,indent=2))
                archive.writestr('profile.json',json.dumps(p.model_dump(),ensure_ascii=False,indent=2))
                archive.writestr('README.txt','Contains test authentication keys. Core configs are generated from installed Open5GS templates in the selected Linux environment.\n')
    def export_visuals(self):
        from .export_dialog import ExportDialog
        dialog=ExportDialog(self,[dict(key='testbed_measures',kind='table',widget=self.measurements,title='5G-стенд · ICMP'),dict(key='testbed_rtt',kind='chart',widget=self.chart,title='5G-стенд · RTT',x_label='Проба',y_label='RTT, ms'),dict(key='testbed_ues',kind='table',widget=self.ues,title='5G-стенд · UE / PDU'),dict(key='testbed_procedures',kind='table',widget=self.procedures,title='5G-стенд · парные события NAS')])
        try:dialog.exec()
        finally:dialog.deleteLater()
