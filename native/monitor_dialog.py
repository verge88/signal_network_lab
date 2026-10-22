"""Reusable native workspace for distributed monitoring experiments."""
from copy import deepcopy
import json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QFormLayout,QTabWidget,QScrollArea,
    QLineEdit,QCheckBox,QFileDialog,QTableWidgetItem,QAbstractItemView,QHeaderView)
from lab import storage,monitor_study
from lab.schema import Project,Monitoring
from lab.monitoring import default_features
from lab.monitor_expressions import OBSERVABLES
from .widgets import label,button,row,table,populate,combo,spin,Chart


class MonitorDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.owner=parent;self.settings=parent.settings;self.dataset=None;self.record=None
        self.setWindowTitle('Конструктор распределённого мониторинга');self.resize(1140,820)
        root=QVBoxLayout(self);self.info=label('');self.info.setWordWrap(True);root.addWidget(self.info)
        self.tabs=QTabWidget();root.addWidget(self.tabs,1)
        plan=QWidget();form=QFormLayout(plan);self.enabled=QCheckBox('Включить обмен мониторинга в обычных прогонах');form.addRow(self.enabled)
        self.fields={}
        for key,title,value,lo,hi,d in [('poll_s','Период опроса, s',5,.1,3600,2),('decision_s','Период решения, s',5,.1,3600,2),('timeout_s','Тайм-аут отчёта, s',2,.01,120,2),('ttl_s','Срок актуальности, s',15,.1,7200,2),('history','История решений, окон',5,1,100,0),('query_bytes','Размер опроса, байт',50,32,65536,0),('report_bytes','Размер отчёта и синхронизации, байт',200,32,65536,0),('priority','Приоритет (0 — высший)',1,0,2,0)]:
            self.fields[key]=spin(value,lo,hi,d);form.addRow(title,self.fields[key])
        self.sync=QCheckBox('Передавать полученные отчёты между сборщиками');form.addRow(self.sync)
        self.protocols=QLineEdit();form.addRow('Разрешённые протоколы маршрута',self.protocols)
        note=label('Сборщиком и независимым наблюдателем может быть любой узел. Несколько сборщиков и зоны задаются назначениями. Размер сообщения включает выбранный полезный объём; транспорт добавляет заголовки и ACK.','Muted');note.setWordWrap(True);form.addRow(note);self.tabs.addTab(plan,'Обмен')
        assignments=QWidget();al=QVBoxLayout(assignments);al.addWidget(label('Участник сообщает свои измерения сборщику. Наблюдатель видит только сообщения, реально обработанные на этом узле.','Muted'))
        self.members=table(['Участник','Сборщик','Зона','Независимый наблюдатель']);al.addWidget(self.members,1)
        al.addLayout(row(button('Добавить назначение',lambda:parent.call(self.add_member),'plus'),button('Удалить',lambda:self.remove(self.members),'trash'),button('Заполнить по топологии',self.auto_members),stretch=True));self.tabs.addTab(assignments,'Участники и зоны')
        feature_page=QWidget();fl=QVBoxLayout(feature_page)
        available=label('Наблюдения: '+', '.join(sorted(OBSERVABLES))+'.\nФункции: abs, min, max, sqrt, log1p, safe_div(a,b). Формулы используют только наблюдения; каждый признак имеет группу для абляции.','Muted');available.setWordWrap(True);fl.addWidget(available)
        self.features=table(['Имя признака','Числовая формула','Группа']);self.features.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked|QAbstractItemView.EditTrigger.EditKeyPressed);fl.addWidget(self.features,1)
        self.features.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents);self.features.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents)
        fl.addLayout(row(button('Добавить признак',self.add_feature,'plus'),button('Удалить',lambda:self.remove(self.features),'trash'),button('Базовый набор',self.reset_features),stretch=True));self.tabs.addTab(feature_page,'Признаки')
        faults_page=QWidget();sl=QVBoxLayout(faults_page);note=label('Синтетические искажения отчётов отделены от фактического состояния сети. Физические отказы и нагрузки задаются обычным конструктором сценариев.','Muted');note.setWordWrap(True);sl.addWidget(note)
        self.faults=table(['Участник','Начало, s','Длительность, s','Искажение','Множитель','Задержка, ms']);sl.addWidget(self.faults,1)
        sl.addLayout(row(button('Добавить искажение',lambda:parent.call(self.add_fault),'plus'),button('Удалить',lambda:self.remove(self.faults),'trash'),stretch=True));self.tabs.addTab(faults_page,'Искажения отчётов')
        experiment=QWidget();el=QVBoxLayout(experiment)
        self.runs=spin(12,6,24);self.seed=spin(42,0,2147483647);self.trees=spin(120,20,300)
        el.addLayout(row('Независимых прогонов',self.runs,'Seed обучения',self.seed,'Деревьев',self.trees,stretch=True))
        self.topology_variants=QCheckBox('Три варианта связности: исходная сеть и 1 / 2 дополнительных канала');el.addWidget(self.topology_variants)
        self.algorithms={}
        line=row(stretch=False)
        for key,title in [('random_forest','Random Forest'),('isolation_forest','Isolation Forest'),('logistic','Logistic Regression')]:
            check=QCheckBox(title);check.setChecked(key!='logistic');self.algorithms[key]=check;line.addWidget(check)
        el.addLayout(line)
        self.evaluation=combo([('run','Независимые прогоны'),('topology','Новая топология'),('fault','Новый тип воздействия')]);self.holdout=QLineEdit();self.holdout.setPlaceholderText('ID топологии / тип; пусто — автоматический выбор')
        el.addLayout(row('Итоговая проверка',self.evaluation,'Только в test',self.holdout))
        self.data_button=button('Сформировать датасет',lambda:parent.call(self.generate),'plus',True)
        self.compare_button=button('Сравнить и выполнить абляцию',lambda:parent.call(self.compare),'experiment',True)
        el.addLayout(row(self.data_button,self.compare_button,button('Отменить',parent.cancel_job),stretch=True))
        self.summary=label('');self.summary.setWordWrap(True);el.addWidget(self.summary)
        self.chart=Chart();self.chart.setMaximumHeight(200);el.addWidget(self.chart)
        self.results=table(['Алгоритм / вариант','Признаков','F1','PR-AUC','FPR','Эпизоды','Тревог / узел·ч','Задержка, s']);el.addWidget(self.results,1)
        self.results.horizontalHeaderItem(5).setToolTip('Обнаруженные эпизоды / все эпизоды воздействия')
        self.results.horizontalHeaderItem(6).setToolTip('Ложные тревоги на суммарный нормальный час наблюдения одного участника')
        self.results.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Interactive);self.results.setColumnWidth(0,300)
        el.addLayout(row(button('Применить выбранную модель к датасету',lambda:parent.call(self.infer)),button('Экспорт таблиц и графика…',lambda:parent.call(self.export_visuals),'download'),stretch=True));self.tabs.addTab(experiment,'Проверка метода')
        observed=QWidget();ol=QVBoxLayout(observed);self.observations=table(['Прогон','Узел','Время, s','Метка','Признаки / решение']);ol.addWidget(self.observations,1);self.tabs.addTab(observed,'Наблюдения')
        history=QWidget();hl=QVBoxLayout(history);self.history=combo([]);self.kind=combo([('monitor_dataset','Датасеты'),('monitor_study','Сравнения')]);self.kind.currentIndexChanged.connect(self.refresh_history)
        hl.addLayout(row(self.kind,self.history,button('Открыть',lambda:parent.call(self.open_history),'folder')))
        text=label('Сравнение использует одни и те же независимые прогоны train/validation/test, seed и исходные наблюдения во всех вариантах. После удаления группы каждая модель обучается заново. Порог выбирается только по validation.\n\nРазметка — пересечение окна с заданным воздействием на участника, его поток или прилегающий канал. Это метка сценария, а не доказательство фактического ущерба. Ложные тревоги нормированы на суммарное нормальное время наблюдения участников.\n\nУсловие мониторинга задаётся сохранённой схемой формул. Модель принимает только совпадающую версию и определения наблюдений. Подгонка параметров по итоговому test требует новой независимой проверки. Синтетические результаты не подтверждают точность оборудования.','Muted');text.setWordWrap(True);hl.addWidget(text);hl.addStretch();self.tabs.addTab(history,'История и методика')
        costs=QWidget();cl=QVBoxLayout(costs);cl.addWidget(label('Полный служебный обмен с транспортными заголовками, ACK и повторами, включая завершение уже начатого обмена после конца наблюдений.','Muted'))
        self.costs=table(['Прогон','Отчётов','Тайм-аутов','Служебных байт в каналах','p95 сети, ms','Успешность сети']);cl.addWidget(self.costs,1);self.tabs.addTab(costs,'Стоимость мониторинга')
        root.addLayout(row(button('Из текущего проекта',self.reload_project,'folder'),button('Применить к проекту',lambda:parent.call(self.apply),'settings'),button('Профиль JSON…',lambda:parent.call(self.save_profile),'save'),button('Открыть профиль…',lambda:parent.call(self.load_profile),'folder'),button('Данные / отчёт…',lambda:parent.call(self.save_data),'save'),button('Импорт данных…',lambda:parent.call(self.import_data),'folder'),button('Закрыть',self.close),stretch=True))
        for b in (self.data_button,self.compare_button):parent.compute_buttons.append(b);b.setEnabled(not parent.active_job)
        self.reload_project();self.refresh_history()

    def reload_project(self):
        self.source=deepcopy(self.owner.project);self.set_config(self.source.get('monitoring',Monitoring().model_dump()))
        self.info.setText('Проект: '+self.source['name']+' · изменения конструктора применяются по кнопке или к отдельной серии.')

    def set_config(self,data):
        c=Monitoring.model_validate(data);self.enabled.setChecked(c.enabled);self.sync.setChecked(c.sync);self.protocols.setText(', '.join(c.protocols))
        for key,w in self.fields.items():w.setValue(getattr(c,key))
        self.members.setRowCount(0)
        for m in c.members:self.add_member(m.model_dump())
        self.features.setRowCount(0)
        for f in c.features:self.add_feature(f.model_dump())
        if not c.features:self.reset_features()
        self.faults.setRowCount(0)
        for f in c.faults:self.add_fault(f.model_dump())

    def node_choices(self):return [(n['id'],n['name']+' ['+n['id']+']') for n in self.source['nodes']]
    @staticmethod
    def remove(widget):
        if widget.currentRow()>=0:widget.removeRow(widget.currentRow())
    def add_member(self,data=None):
        d=data if isinstance(data,dict) else {};choices=self.node_choices();i=self.members.rowCount();self.members.insertRow(i)
        for j,key in [(0,'node'),(1,'collector'),(3,'witness')]:self.members.setCellWidget(i,j,combo(choices,d.get(key) or choices[min(j,len(choices)-1)][0]))
        self.members.setCellWidget(i,2,QLineEdit(d.get('zone','Зона 1')));self.members.setRowHeight(i,44)
    def auto_members(self):
        degree={n['id']:0 for n in self.source['nodes']}
        for l in self.source['links']:degree[l['source']]+=1;degree[l['target']]+=1
        collector=max(degree,key=degree.get);self.members.setRowCount(0)
        for node in degree:
            if node!=collector:self.add_member(dict(node=node,collector=collector,witness=collector,zone='Зона 1'))
    def add_feature(self,data=None):
        d=data if isinstance(data,dict) else dict(name='feature_'+str(self.features.rowCount()+1),expression='reported_processed_s',group='local')
        i=self.features.rowCount();self.features.insertRow(i)
        for j,k in enumerate(('name','expression','group')):self.features.setItem(i,j,QTableWidgetItem(d[k]))
    def reset_features(self):
        self.features.setRowCount(0)
        for f in default_features():self.add_feature(f)
    def add_fault(self,data=None):
        d=data if isinstance(data,dict) else dict(start=self.source['duration']*.3,duration=self.source['duration']*.3)
        nodes=[(self.members.cellWidget(i,0).currentData(),self.members.cellWidget(i,0).currentText()) for i in range(self.members.rowCount())]
        if not nodes:raise ValueError('Сначала добавьте участников')
        i=self.faults.rowCount();self.faults.insertRow(i);self.faults.setRowHeight(i,44)
        widgets=[combo(nodes,d.get('node')),spin(d.get('start',0),0,86399,2),spin(d.get('duration',5),.01,86400,2),combo([('scale','Масштабирование'),('freeze','Повтор прежних значений'),('suppress','Не отправлять отчёт'),('delay','Задержать отчёт')],d.get('mode','scale')),spin(d.get('factor',.1),0,100,3),spin(d.get('delay_ms',5000),0,60000)]
        for j,w in enumerate(widgets):self.faults.setCellWidget(i,j,w)
    def config(self):
        members=[dict(node=self.members.cellWidget(i,0).currentData(),collector=self.members.cellWidget(i,1).currentData(),zone=self.members.cellWidget(i,2).text(),witness=self.members.cellWidget(i,3).currentData()) for i in range(self.members.rowCount())]
        features=[{k:self.features.item(i,j).text().strip() if self.features.item(i,j) else '' for j,k in enumerate(('name','expression','group'))} for i in range(self.features.rowCount())]
        faults=[dict(node=self.faults.cellWidget(i,0).currentData(),start=self.faults.cellWidget(i,1).value(),duration=self.faults.cellWidget(i,2).value(),mode=self.faults.cellWidget(i,3).currentData(),factor=self.faults.cellWidget(i,4).value(),delay_ms=self.faults.cellWidget(i,5).value()) for i in range(self.faults.rowCount())]
        return Monitoring(enabled=self.enabled.isChecked(),members=members,features=features,faults=faults,sync=self.sync.isChecked(),protocols=[s.strip() for s in self.protocols.text().split(',')],**{k:w.value() for k,w in self.fields.items()})
    def project(self):
        return Project.model_validate(dict(deepcopy(self.source),monitoring=self.config().model_dump()))
    def apply(self):
        project=self.project().model_dump();self.owner.mutate('Настройка мониторинга',lambda p:p.update(monitoring=project['monitoring']));self.info.setText('Профиль применён к текущему проекту. F5 выполняет сеть с мониторингом.')
    def generate(self):
        p=self.project();runs=self.runs.value();variants=self.topology_variants.isChecked();self.owner.start_job('Серия мониторинга',lambda progress,cancel:monitor_study.generate(p,runs,progress,cancel,topology_variants=variants),self.accept_dataset)
    def accept_dataset(self,data):
        self.dataset=data;self.record=None;self.results.setRowCount(0);self.chart.set_series([])
        self.summary.setText(f'{data["metadata"]["runs"]} прогонов · {len(data["rows"])} строк · {len(data["schema"]["features"])} признаков. Датасет сохранён; можно сравнить алгоритмы и группы.')
        self.show_rows(data['rows']);self.refresh_history();self.tabs.setCurrentIndex(4)
        populate(self.costs,[[r['run_id'],r['counts'].get('reports',0),r['counts'].get('timeouts',0),r['wire_bytes'],r['summary']['latency_p95'],r['summary']['delivery_ratio']] for r in data.get('provenance',[])])
    def compare(self):
        if not self.dataset:raise ValueError('Сформируйте или откройте датасет')
        data=self.dataset;algorithms=[k for k,w in self.algorithms.items() if w.isChecked()];seed=self.seed.value();trees=self.trees.value();mode=self.evaluation.currentData();held=self.holdout.text().strip() or None
        self.owner.start_job('Сравнение детекторов и абляция',lambda progress,cancel:monitor_study.compare(data,algorithms,seed=seed,trees=trees,progress=progress,cancel=cancel,evaluation_mode=mode,holdout_value=held),self.accept_study)
    def accept_study(self,record):
        self.record=record;models=record['models'];fmt=lambda x:'—' if x is None else f'{x:.5g}'
        populate(self.results,[[m['algorithm']+' / '+m['variant'],len(m['features']),fmt(m['metrics']['f1']),fmt(m['metrics']['pr_auc']),fmt(m['metrics']['false_positive_rate']),str(m['operational']['detected'])+' / '+str(m['operational']['episodes']),fmt(m['operational']['false_alarms_per_hour']),fmt(m['operational']['delay_mean_s'])] for m in models])
        self.chart.set_series([dict(name='F1',points=[(i+1,m['metrics']['f1']) for i,m in enumerate(models)])])
        self.summary.setText(str(len(models))+' вариантов. Одинаковые независимые части данных; порог настроен по validation. Ось X: номер варианта, Y: F1.');self.refresh_history();self.tabs.setCurrentIndex(4)
    def show_rows(self,rows):
        populate(self.observations,[[r['run_id'],r['entity_id'],r['time'],r['label'],json.dumps({k:v for k,v in r.items() if k not in ('fault_intervals','fault_types')},ensure_ascii=False)] for r in rows[:1000]])
    def infer(self):
        if not self.record or not self.dataset:raise ValueError('Откройте датасет и сравнение')
        i=self.results.currentRow()
        if i<0:raise ValueError('Выберите модель')
        model=self.record['models'][i];predictions=__import__('lab.ml',fromlist=['predict']).predict(model['model_id'],self.dataset['rows'],model['threshold'])
        self.show_rows([dict(r,score=p['score'],anomaly=p['anomaly']) for r,p in zip(self.dataset['rows'],predictions)]);self.tabs.setCurrentIndex(5)
    def refresh_history(self,*args):
        self.history.clear()
        for item in storage.listing(self.kind.currentData()):self.history.addItem(item['name'],item['id'])
    def open_history(self):
        if not self.history.currentData():raise ValueError('Нет сохранённых записей')
        record=storage.get(self.kind.currentData(),self.history.currentData())
        if self.kind.currentData()=='monitor_dataset':self.accept_dataset(record)
        else:self.accept_study(record)
    def save_profile(self):
        c=self.project().monitoring.model_dump();path,_=QFileDialog.getSaveFileName(self,'Профиль мониторинга','monitoring-profile.json','JSON (*.json)')
        if path:Path(path).write_text(json.dumps(dict(contract='monitoring-profile-v1',monitoring=c),ensure_ascii=False,indent=2),encoding='utf-8')
    def load_profile(self):
        path,_=QFileDialog.getOpenFileName(self,'Профиль мониторинга','','JSON (*.json)')
        if path:
            data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
            if data.get('contract')!='monitoring-profile-v1':raise ValueError('Неизвестный профиль')
            Project.model_validate(dict(deepcopy(self.source),monitoring=data['monitoring']))
            self.set_config(data['monitoring'])
    def save_data(self):
        data=self.record or self.dataset
        if not data:raise ValueError('Нет данных')
        path,_=QFileDialog.getSaveFileName(self,'Данные и схема','monitoring.json','JSON (*.json);;CSV (*.csv)')
        if path:
            if Path(path).suffix.lower()=='.csv':
                if not self.dataset:raise ValueError('Нет датасета')
                text=monitor_study.export_csv(self.dataset)
            else:text=json.dumps(data,ensure_ascii=False,indent=2)
            Path(path).write_text(text,encoding='utf-8')
    def import_data(self):
        path,_=QFileDialog.getOpenFileName(self,'Данные со схемой','','JSON (*.json)')
        if path:self.accept_dataset(monitor_study.import_data(Path(path).read_text(encoding='utf-8-sig')))
    def export_visuals(self):
        from .export_dialog import ExportDialog
        sources=[dict(key='monitor_results',kind='table',widget=self.results,title='Мониторинг · сравнение'),dict(key='monitor_chart',kind='chart',widget=self.chart,title='Мониторинг · F1',x_label='Вариант',y_label='F1'),dict(key='monitor_observations',kind='table',widget=self.observations,title='Мониторинг · первые 1000 наблюдений'),dict(key='monitor_costs',kind='table',widget=self.costs,title='Мониторинг · стоимость')]
        dialog=ExportDialog(self,sources)
        try:dialog.exec()
        finally:dialog.deleteLater()
