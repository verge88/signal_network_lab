"""Native management of durable simulation jobs and streamed completion records."""
from copy import deepcopy
import math
from pathlib import Path
import uuid
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog,QWidget,QVBoxLayout,QFormLayout,QTabWidget,QFileDialog,QCheckBox,QScrollArea
from lab import long_runs,storage
from lab.schema import Project
from .widgets import label,button,row,spin,table,populate

STATES=dict(preparing='Подготовка',running='Выполняется / прерван',paused='На паузе',completed='Завершён',failed='Ошибка',cancelled='Отменён до сохранения',damaged='Повреждён')
KINDS=dict(procedures='Процедура',messages='Сообщение',control='Управляющий обмен',routing='Представление маршрута',user_data='Пользовательские данные')


class LongRunDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.owner=parent;self.settings=parent.settings;self.current_id=None;self.owns_job=False;self.jobs=[]
        self.setWindowTitle('Длительные прогоны');self.resize(1260,840);self.setMinimumSize(980,640)
        root=QVBoxLayout(self);root.setContentsMargins(16,14,16,14);root.setSpacing(10)
        root.addWidget(label('Длительные эксперименты','PanelTitle'));self.info=label('До 24 часов модельного времени. Результаты записываются на диск во время расчёта.','Muted');self.info.setWordWrap(True);root.addWidget(self.info)
        self.tabs=QTabWidget();root.addWidget(self.tabs,1)
        scroll=QScrollArea();scroll.setWidgetResizable(True);config=QWidget();scroll.setWidget(config);layout=QVBoxLayout(config)
        form=QFormLayout();form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.duration=spin(3600,10,86400,0,'s');self.measurement_window=spin(30,1,3600,0,'s');self.load=spin(1,.000001,20,6,'×')
        form.addRow('Длительность',self.duration);form.addRow('Окно измерений',self.measurement_window);form.addRow('Множитель всех потоков',self.load)
        self.checkpoint_sim=spin(600,1,3600,1,'s');self.checkpoint_wall=spin(30,1,600,1,'s');self.batch=spin(512,16,20000);self.event_limit=spin(12000000,1000,50000000)
        form.addRow('Сохранение · через модельное время',self.checkpoint_sim);form.addRow('Сохранение · через реальное время',self.checkpoint_wall);form.addRow('Пакет событий для записи и очистки',self.batch);form.addRow('Предельное число событий',self.event_limit)
        self.auto_pause=QCheckBox('Пауза на модельной отметке');self.pause_at=spin(10,0,86400,2,'s');self.pause_at.setEnabled(False);self.auto_pause.toggled.connect(self.pause_at.setEnabled)
        form.addRow(self.auto_pause,self.pause_at);layout.addLayout(form)
        layout.addLayout(row(button('Из текущего проекта',self.load_project,'folder'),button('Проверить план',lambda:parent.call(self.preview),'settings'),button('Подобрать масштаб по бюджету',lambda:parent.call(self.fit_budget),'chart'),stretch=True))
        note=label('Сохранение происходит после целого события, когда достигнут любой из двух интервалов. Пауза также сохраняет состояние. Окно измерений ограничено 10 000 строками; бюджеты процедур и пользовательского трафика сохраняются. Параметры сети при продолжении берутся из контрольной точки. Здесь можно изменить интервалы сохранения и лимит событий.','Muted');note.setWordWrap(True);layout.addWidget(note)
        note=label('Контрольная точка содержит очереди, таймеры, RNG, контексты и реплики. Для продолжения нужны те же версии движка, Python, NumPy и Pydantic. История процедур и точные выборки метрик остаются в памяти в пределах бюджета; это режим с ограничениями памяти, а не неограниченный поток.','Muted');note.setWordWrap(True);layout.addWidget(note);layout.addStretch();self.tabs.addTab(scroll,'Новый прогон')
        library=QWidget();ll=QVBoxLayout(library)
        self.jobs_table=table(['Проект','Состояние','Модельное время, s','Событий','Записей','Точка, s','Обновлён','ID']);ll.addWidget(self.jobs_table,1)
        self.details=label('Выберите прогон.','Muted');self.details.setWordWrap(True);ll.addWidget(self.details);self.jobs_table.itemSelectionChanged.connect(self.show_selection)
        self.tabs.addTab(library,'Сохранённые расчёты')
        stream=QWidget();sl=QVBoxLayout(stream);self.stream_info=label('Показаны первые 500 сохранённых записей выбранного расчёта. Полный журнал экспортируется последовательно.','Muted');self.stream_info.setWordWrap(True);sl.addWidget(self.stream_info)
        self.stream_table=table(['№','Время, s','Тип','Процедура / сообщение','Результат','Источник → адресат','Задержка, ms']);sl.addWidget(self.stream_table,1)
        sl.addLayout(row(button('Обновить первые 500 записей',lambda:parent.call(self.load_stream),'chart'),button('Экспорт видимой таблицы…',lambda:parent.call(self.export_table),'download'),stretch=True));self.tabs.addTab(stream,'Поток записей')
        self.new_button=button('Начать',lambda:parent.call(self.start_new),'play',True);self.resume_button=button('Продолжить выбранный',lambda:parent.call(self.resume_selected),'play');self.pause_button=button('Приостановить текущий',parent.cancel_job);self.pause_button.setEnabled(False)
        root.addLayout(row(self.new_button,self.resume_button,self.pause_button,button('Открыть результат',lambda:parent.call(self.open_result),'folder'),button('Полный журнал…',lambda:parent.call(self.export_all),'download'),button('Закрыть',self.close),stretch=True))
        parent.compute_buttons.extend([self.new_button,self.resume_button]);self.new_button.setEnabled(not parent.active_job);self.resume_button.setEnabled(not parent.active_job)
        self.timer=QTimer(self);self.timer.setInterval(1500);self.timer.timeout.connect(self.poll);self.timer.start();self.load_project();self.refresh()
    def load_project(self):
        self.source=deepcopy(self.owner.project);duration=self.source['duration'];self.duration.setValue(duration if duration>600 else 3600)
        self.measurement_window.setValue(max(self.source['window'],math.ceil(self.duration.value()/10000)));self.load.setValue(1)
        self.info.setText('Исходная сеть: '+self.source['name']+'. Длительность, окно и множитель применяются к новому расчёту.')
    def project(self):
        data=deepcopy(self.source);data.update(duration=self.duration.value(),window=self.measurement_window.value())
        for flow in data['flows']:flow['rate']*=self.load.value()
        return Project.model_validate(data)
    def options(self,resume=False):
        return long_runs.Options(checkpoint_sim_s=self.checkpoint_sim.value(),checkpoint_wall_s=self.checkpoint_wall.value(),batch_events=self.batch.value(),
            max_events=self.event_limit.value(),pause_at_s=None if resume or not self.auto_pause.isChecked() else self.pause_at.value())
    def preview(self):
        p=self.project()
        if p.engine!='procedures':raise ValueError('Выберите движок процедур в параметрах проекта')
        self.options();self.info.setText(f'План проверен: {p.duration:,} s · {math.ceil(p.duration/p.window):,} окон · {sum(f.rate for f in p.flows):.6g} новых процедур/s. Исходный проект сохраняется внутри расчёта.')
        return p
    def fit_budget(self):
        self.measurement_window.setValue(max(self.measurement_window.value(),math.ceil(self.duration.value()/10000)))
        for _ in range(30):
            try:self.preview();return
            except ValueError as error:
                if not any(text in str(error) for text in ('350 000','700 000','500 000','less than or equal to 3000')):raise
                if self.load.value()<=self.load.minimum():raise
                self.load.setValue(max(self.load.minimum(),self.load.value()/2))
        raise ValueError('Настройте интенсивность потоков вручную')
    def start_new(self):
        p=self.preview();o=self.options();ident=uuid.uuid4().hex
        self.owner.start_job('Длительный прогон · запись на диск',lambda progress,cancel:long_runs.start(p,o,progress,cancel,ident=ident),self.accept_result,result_on_cancel=True)
        self.current_id=ident;self.owns_job=True;self.tabs.setCurrentIndex(1)
    def selected(self):
        index=self.jobs_table.currentRow()
        if index<0 or index>=len(self.jobs):raise ValueError('Выберите сохранённый расчёт')
        return self.jobs[index]
    def resume_selected(self):
        ident=self.selected()['id'];o=self.options(resume=True)
        self.owner.start_job('Продолжение сохранённого расчёта',lambda progress,cancel:long_runs.resume(ident,o,progress,cancel),self.accept_result,result_on_cancel=True)
        self.current_id=ident;self.owns_job=True
    def accept_result(self,meta):
        self.owner.long_close_ready=meta['status'] in ('paused','completed')
        self.current_id=meta['id'];self.owns_job=False;self.refresh()
        if meta['status']=='completed':
            self.owner.run_record=storage.get('run',meta['result_id']);self.owner.refresh_measurements();self.info.setText('Расчёт завершён. Итог доступен в телеметрии и библиотеке прогонов.')
        else:self.info.setText(f'Сохранено на паузе: {meta["checkpoint_s"]:.3f} s. Расчёт можно продолжить после закрытия программы.')
    def poll(self):
        self.pause_button.setEnabled(bool(self.owns_job and self.owner.active_job))
        if not self.owner.active_job:self.owns_job=False
        if self.isVisible() and self.tabs.currentIndex()==1:self.refresh()
    def refresh(self):
        index=self.jobs_table.currentRow();selected=self.jobs[index]['id'] if 0<=index<len(self.jobs) else self.current_id
        self.jobs=long_runs.listing()
        populate(self.jobs_table,[[j['name'],STATES.get(j['status'],j['status']),f'{j.get("simulated_s",0):.3f} / {j.get("project",{}).get("duration","—")}',j.get('steps',0),j.get('records',0),'—' if j.get('checkpoint_s') is None else f'{j["checkpoint_s"]:.3f}',j.get('updated',''),j['id']] for j in self.jobs])
        for i,j in enumerate(self.jobs):
            if j['id']==selected:self.jobs_table.selectRow(i);break
        self.show_selection()
    def show_selection(self):
        index=self.jobs_table.currentRow()
        if not 0<=index<len(self.jobs):return
        job=self.jobs[index];self.current_id=job['id'];clean=job.get('compaction',{})
        text=f'Контрольная точка: {job.get("checkpoint_s","—")} s · освобождено объектов: {sum(clean.values()):,}. Журнал: {long_runs.path_for(job["id"])}'
        if job.get('error'):text+=' · Ошибка: '+job['error']
        self.details.setText(text)
    def open_result(self):
        job=self.selected()
        if not job.get('result_id'):raise ValueError('Итоговый результат появится после завершения расчёта; промежуточные записи доступны на вкладке «Поток записей»')
        self.owner.run_record=storage.get('run',job['result_id']);self.owner.refresh_measurements();self.owner.show_page(2)
    def load_stream(self):
        ident=self.current_id
        if not ident:raise ValueError('Выберите расчёт')
        rows=long_runs.stream_rows(ident);self.visible_stream=rows
        populate(self.stream_table,[[r['seq'],f'{r["time_ms"]/1000:.6f}',KINDS.get(r['kind'],r['kind']),r['record'].get('operation',r['record'].get('procedure_id','—')),r['record'].get('status','—'),str(r['record'].get('source','—'))+' → '+str(r['record'].get('target','—')),r['record'].get('latency_ms',r['record'].get('rtt_ms','—'))] for r in rows])
        self.stream_info.setText(f'Расчёт {ident} · первые {len(rows)} записей. Это события на момент записи; окончательные метрики появятся после завершения.')
    def export_table(self):
        from .export_dialog import ExportDialog
        d=ExportDialog(self,[dict(key='long_stream',kind='table',widget=self.stream_table,title='Длительный прогон · первые 500 записей',metadata=dict(job_id=self.current_id,limit=500,final_metrics=False))])
        try:d.exec()
        finally:d.deleteLater()
    def export_all(self):
        if not self.current_id:raise ValueError('Выберите расчёт')
        path,selected=QFileDialog.getSaveFileName(self,'Полный поток записей','simulation-events.jsonl','JSON Lines (*.jsonl);;CSV (*.csv)')
        if not path:return
        fmt='csv' if Path(path).suffix.lower()=='.csv' or selected.startswith('CSV') else 'jsonl';ident=self.current_id
        self.owner.start_job('Потоковый экспорт журнала',lambda progress,cancel:long_runs.export_stream(ident,path,fmt,progress,cancel),lambda count:self.info.setText(f'Сохранено записей: {count:,} · {path}'))
