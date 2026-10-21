"""Measured-network comparison workspace, built entirely with Qt widgets."""
import copy
import json
from pathlib import Path
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QScrollArea,QTableWidgetItem,
    QFormLayout,QDialogButtonBox,QFileDialog,QCheckBox,QLineEdit,QTabWidget,QTextBrowser)
from lab import storage
from lab.validation import Options,Tolerance,METRICS,LABELS,STATUS,TEMPLATE,parse,validate,synthetic_example,report_html
from .widgets import label,button,combo,spin,table,populate,row,Chart


def number(value):return '—' if value is None else f'{value:.6g}'


class ValidationOptions(QDialog):
    def __init__(self,parent,options):
        super().__init__(parent);self.setWindowTitle('Допуски и условия проверки');self.resize(930,780);self.original=options
        outer=QVBoxLayout(self);scroll=QScrollArea();scroll.setWidgetResizable(True);content=QWidget();root=QVBoxLayout(content);scroll.setWidget(content);outer.addWidget(scroll,1)
        note=label('План фиксируется перед сравнением. Допуск = большее из абсолютного отклонения и процента от измеренного значения.','Muted');note.setWordWrap(True);root.addWidget(note)
        form=QFormLayout();self.seeds=spin(options.seeds,1,10);self.runs=spin(options.min_runs,2,20);self.samples=spin(options.min_samples,20,20000)
        form.addRow('Seed симуляции на опыт',self.seeds);form.addRow('Независимых опытов на условие',self.runs);form.addRow('Минимум задержек в каждой выборке',self.samples)
        self.distributions=QCheckBox('Требовать распределение задержек');self.distributions.setChecked(options.require_distribution)
        self.series=QCheckBox('Требовать полный временной ряд');self.series.setChecked(options.require_time_series)
        form.addRow(self.distributions);form.addRow(self.series)
        self.ks=spin(options.max_ks,0,1,4);self.wasserstein=spin(options.max_wasserstein_ms,0,1000000,3)
        form.addRow('Допустимое KS-расстояние',self.ks);form.addRow('Допустимое Wasserstein-расстояние, ms',self.wasserstein);root.addLayout(form)
        self.metrics=table(['Метрика','Обязательна','Абсолютный допуск','Относительный допуск, %']);self.metrics.setMinimumHeight(260);root.addWidget(self.metrics)
        for index,metric in enumerate(METRICS):
            self.metrics.insertRow(index)
            self.metrics.setItem(index,0,QTableWidgetItem(LABELS[metric]));check=QCheckBox();check.setChecked(metric in options.required_metrics)
            self.metrics.setCellWidget(index,1,check);self.metrics.setCellWidget(index,2,spin(options.tolerances[metric].absolute,0,1000000,6));self.metrics.setCellWidget(index,3,spin(options.tolerances[metric].relative*100,0,1000,3))
        root.addWidget(label('Условия, которые должны присутствовать в отчёте','PanelTitle'))
        self.conditions=table(['Сценарий из CSV','Нагрузка, msg/s']);self.conditions.setMinimumHeight(110);root.addWidget(self.conditions,1)
        for condition in options.expected_conditions:self.add_condition(condition.scenario,condition.offered_rate)
        root.addLayout(row(button('Добавить условие',lambda:self.add_condition()),button('Удалить',self.remove_condition),stretch=True))
        self.error=label('');self.error.setWordWrap(True);outer.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Сохранить план');buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена');buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);outer.addWidget(buttons)
    def add_condition(self,scenario='project',rate=100):
        if self.conditions.rowCount()>=100:return
        index=self.conditions.rowCount();self.conditions.insertRow(index);self.conditions.setCellWidget(index,0,QLineEdit(scenario));self.conditions.setCellWidget(index,1,spin(rate,.001,3000,3))
    def remove_condition(self):
        index=self.conditions.currentRow()
        if index>=0:self.conditions.removeRow(index)
    def values(self):
        return Options(seeds=self.seeds.value(),min_runs=self.runs.value(),min_samples=self.samples.value(),require_distribution=self.distributions.isChecked(),require_time_series=self.series.isChecked(),
            max_ks=self.ks.value(),max_wasserstein_ms=self.wasserstein.value(),
            required_metrics=[metric for i,metric in enumerate(METRICS) if self.metrics.cellWidget(i,1).isChecked()],
            tolerances={metric:Tolerance(absolute=self.metrics.cellWidget(i,2).value(),relative=self.metrics.cellWidget(i,3).value()/100) for i,metric in enumerate(METRICS)},
            expected_conditions=[dict(scenario=self.conditions.cellWidget(i,0).text().strip(),offered_rate=self.conditions.cellWidget(i,1).value()) for i in range(self.conditions.rowCount())])
    def accept(self):
        try:self.result_options=self.values();super().accept()
        except ValueError as error:self.error.setText(str(error))


class ValidationDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.owner=parent;self.settings=parent.settings;self.text=None;self.source_name='';self.record=None
        self.setWindowTitle('Проверка достоверности модели');self.resize(1220,860);self.setMinimumSize(1000,680)
        try:self.options=Options.model_validate_json(self.settings.value('validation/options','{}'))
        except (ValueError,TypeError):self.options=Options()
        root=QVBoxLayout(self);root.setContentsMargins(16,14,16,14);root.setSpacing(10)
        self.info=label('Загрузите измерения и задайте допуски. Проверяется текущая конфигурация проекта без подбора параметров.','Muted');self.info.setWordWrap(True);root.addWidget(self.info)
        root.addLayout(row(self.action('Шаблон CSV',self.save_template,'download'),self.action('Загрузить измерения',self.load_measurements,'folder'),
            self.action('Синтетический пример',self.make_example,'experiment',compute=True),self.action('Допуски и условия',self.configure,'settings'),
            self.action('Сравнить',self.compare,'play',primary=True,compute=True)))
        self.input_info=label('Измерения не загружены','Muted');self.input_info.setWordWrap(True);root.addWidget(self.input_info)
        self.history=combo([]);self.history.currentIndexChanged.connect(self.choose_report);root.addLayout(row(label('Сохранённый отчёт'),self.history))
        self.tabs=QTabWidget();root.addWidget(self.tabs,1)
        comparisons=QWidget();layout=QVBoxLayout(comparisons);self.experiment=combo([]);self.metric_combo=combo([(key,LABELS[key]) for key in METRICS],'latency_p95')
        layout.addLayout(row(label('Опыт'),self.experiment,label('Временная метрика'),self.metric_combo))
        layout.addWidget(label('Распределение задержек: X — ms, Y — накопленная доля. Измерения — сплошная линия; симулятор — штриховая.','Muted'))
        self.cdf_chart=Chart();self.cdf_chart.y_range=(0,1);self.cdf_chart.empty_text='Выборки задержек отсутствуют';self.cdf_chart.setMinimumHeight(180);layout.addWidget(self.cdf_chart,1)
        self.distribution_info=label('');self.distribution_info.setWordWrap(True);layout.addWidget(self.distribution_info)
        self.time_chart=Chart();self.time_chart.empty_text='Нет временного ряда для выбранной метрики';self.time_chart.setMinimumHeight(180);layout.addWidget(self.time_chart,1)
        self.series_info=label('');self.series_info.setWordWrap(True);layout.addWidget(self.series_info);self.tabs.addTab(comparisons,'Сравнение')
        self.errors=table(['Опыт / сценарий / нагрузка','Метрика / время, s','Измерение','Симулятор','Абс. ошибка','Допуск','Результат']);self.tabs.addTab(self.errors,'Отклонения')
        self.conditions=table(['Сценарий','Нагрузка, msg/s','Независимых опытов','Источник','Результат','Недостающие данные']);self.tabs.addTab(self.conditions,'Условия проверки')
        self.report_view=QTextBrowser();self.report_view.setOpenExternalLinks(False);self.tabs.addTab(self.report_view,'Отчёт')
        root.addLayout(row(self.action('Экспорт графиков и таблиц…',self.export_visuals,'download'),self.action('Сохранить отчёт…',self.save_report,'save'),self.action('Сохранить измерения…',self.save_measurements,'save'),stretch=True))
        self.experiment.currentIndexChanged.connect(self.render_experiment);self.metric_combo.currentIndexChanged.connect(self.render_experiment)
        self.reload_history()
    def action(self,text,fn,kind,primary=False,compute=False):
        widget=button(text,lambda checked=False:self.owner.call(fn),kind,primary)
        if compute:self.owner.compute_buttons.append(widget);widget.setEnabled(self.owner.active_job is None)
        return widget
    def save_template(self):
        path,_=QFileDialog.getSaveFileName(self,'Шаблон измерений для проверки','validation-measurements.csv','CSV (*.csv)')
        if path:Path(path).write_text(TEMPLATE,encoding='utf-8-sig')
    def load_measurements(self):
        path,_=QFileDialog.getOpenFileName(self,'Измерения независимых проверочных опытов','','CSV (*.csv)')
        if not path:return
        source=Path(path)
        if source.stat().st_size>5_000_000:raise ValueError('Лимит измерений: 5 МБ')
        self.set_measurements(source.read_text(encoding='utf-8-sig'),source.stem)
    def set_measurements(self,text,name):
        rows=parse(text);self.text=text;self.source_name=name
        sources=', '.join(sorted({row['evidence']['source_kind'] for row in rows}))
        self.input_info.setText(f'{name}: {len(rows)} опытов. Источники: {sources}. Допуски фиксируются при нажатии «Сравнить».')
    def save_measurements(self):
        if not self.text:raise ValueError('Сначала загрузите измерения или создайте синтетический пример')
        path,_=QFileDialog.getSaveFileName(self,'Сохранить текущие измерения','validation-measurements.csv','CSV (*.csv)')
        if path:Path(path).write_text(self.text,encoding='utf-8-sig')
    def configure(self):
        dialog=ValidationOptions(self,self.options)
        try:
            if dialog.exec()!=QDialog.DialogCode.Accepted:return
            self.options=dialog.result_options;self.settings.setValue('validation/options',self.options.model_dump_json());self.settings.sync()
            self.input_info.setText(self.input_info.text().split('\n')[0]+'\nНовый план сохранён и будет применён при следующем сравнении. Старые отчёты сохраняют прежние допуски.')
        finally:dialog.deleteLater()
    def make_example(self):
        project=copy.deepcopy(self.owner.project)
        def ready(result):self.set_measurements(result['text'],'Синтетический пример');self.info.setText('Загружен пример из симулятора. Он проверяет сравнение и визуализацию; соответствие оборудованию по нему не оценивается. Нажмите «Сравнить».');self.show();self.raise_()
        self.owner.start_job('Синтетические измерения для примера',lambda progress,cancel:synthetic_example(project,progress,cancel),ready)
    def compare(self):
        if not self.text:raise ValueError('Сначала загрузите измерения или создайте синтетический пример')
        project=copy.deepcopy(self.owner.project);text=self.text;options=self.options.model_dump();name=self.source_name
        groups=set();hashes=set()
        for saved in storage.listing('calibration'):
            calibration=storage.get('calibration',saved['id'])
            for observation in calibration.get('evidence',{}).get('observations',[]):
                groups.add(observation['group_id'])
                if observation.get('raw_sha256'):hashes.add(observation['raw_sha256'].lower())
        self.owner.start_job('Сравнение измерений и симулятора',lambda progress,cancel:validate(project,text,progress,cancel,options,groups,hashes),lambda result:self.accept_result(result,name))
    def accept_result(self,result,name):
        self.record=storage.save('validation',name,result);self.reload_history();self.render();self.show();self.raise_()
    def reload_history(self):
        selected=self.record['id'] if self.record else None;self.history.blockSignals(True);self.history.clear();self.history.addItem('Выберите отчёт',None)
        for saved in storage.listing('validation'):self.history.addItem(saved['name']+' / '+saved['id'][:6],saved['id'])
        self.history.setCurrentIndex(max(0,self.history.findData(selected)));self.history.blockSignals(False)
    def choose_report(self):
        rid=self.history.currentData();self.record=storage.get('validation',rid) if rid else None;self.render()
    def render(self):
        self.experiment.blockSignals(True);self.experiment.clear()
        if not self.record:
            populate(self.conditions,[]);populate(self.errors,[]);self.cdf_chart.set_series([]);self.time_chart.set_series([]);self.report_view.clear();self.distribution_info.clear();self.series_info.clear();self.info.setText('Выберите отчёт или загрузите измерения для новой проверки.');self.experiment.blockSignals(False);return
        record=self.record;summary=record['summary']
        self.info.setText(f'Конфигурация «{record["project"]["name"]}» · {record["config_sha256"][:12]} · условий {summary["conditions"]}: в допуске {summary["passed"]}, вне допуска {summary["failed"]}, недостаточно данных {summary["insufficient"]}. Внешняя точность по оборудованию не доказана.')
        for experiment in record['experiments']:self.experiment.addItem(f'{experiment["run_id"]} · {experiment["scenario"]} · {experiment["offered_rate"]:g} msg/s',experiment['index'])
        self.experiment.blockSignals(False)
        populate(self.conditions,[[c['scenario'],number(c['offered_rate']),c['independent_runs'],', '.join(c['sources']) or '—',STATUS[c['status']],'; '.join(c['missing']) or '—'] for c in record['conditions']])
        policy=record['policy'];plan=f'Seed на опыт: {policy["seeds"]}; независимых опытов на условие: {policy["min_runs"]}; минимум задержек: {policy["min_samples"]}.\n'
        plan+='Распределение обязательно: '+('да' if policy['require_distribution'] else 'нет')+'; временной ряд обязателен: '+('да' if policy['require_time_series'] else 'нет')+'.\n'
        plan+='KS ≤ '+number(policy['max_ks'])+'; Wasserstein ≤ '+number(policy['max_wasserstein_ms'])+' ms.\n'
        plan+='\n'.join(LABELS[key]+': абс. '+number(spec['absolute'])+', отн. '+number(spec['relative']*100)+'%'+(' · обязательна' if key in policy['required_metrics'] else '') for key,spec in policy['tolerances'].items())
        self.report_view.setPlainText('Проверка достоверности модели\n\n'+self.info.text()+'\n\n'+'\n'.join(record['limitations'])+'\n\nКонфигурация SHA-256: '+record['config_sha256']+'\nCSV SHA-256: '+record['input_csv_sha256']+'\nПлан SHA-256: '+record['policy_sha256']+'\n\nПлан допусков:\n'+plan)
        self.render_experiment()
    def render_experiment(self):
        if not self.record or self.experiment.currentData() is None:return
        experiment=self.record['experiments'][self.experiment.currentData()];metric=self.metric_combo.currentData();dist=experiment['distribution'];series=experiment['time_series']
        observed=dict(name='Измерения',color='#82adfa',points=dist['observed_ecdf'],fill=False,markers=False)
        predicted=dict(name='Симулятор',color='#d9b27b',points=dist['simulated_ecdf'],fill=False,markers=False,dash=True)
        self.cdf_chart.set_series([observed,predicted])
        self.distribution_info.setText(f'KS {number(dist["ks"])} / допуск {number(self.record["policy"]["max_ks"])} · Wasserstein {number(dist["wasserstein_ms"])} / допуск {number(self.record["policy"]["max_wasserstein_ms"])} ms · выборки {dist["observed_n"]} / {dist["simulated_n"]} · {STATUS[dist["status"]]}')
        self.time_chart.set_series([dict(name='Измерения',color='#82adfa',points=[(point['time'],point[metric]) for point in series['observed'] if metric in point],fill=False),dict(name='Симулятор',color='#d9b27b',points=[(point['time'],point[metric]) for point in series['predicted'] if point.get(metric) is not None],fill=False,dash=True)],experiment['incidents'])
        self.series_info.setText('X — время, s; Y — '+LABELS[metric]+'. '+('Полный ряд сопоставлен.' if series['complete'] else 'Ряд неполный или окна не совпадают.')+' '+('; '.join(experiment['missing'])))
        rows=[]
        for current in self.record['experiments']:
            for item in current['errors']:
                rows.append([current['run_id']+' / '+current['scenario']+' / '+number(current['offered_rate']),LABELS[item['metric']],number(item['observed']),number(item['predicted']),number(item['absolute_error']),number(item['allowed']),STATUS[item['status']]])
        for item in series['errors']:
            if item['metric']==metric:rows.append([experiment['run_id'],LABELS[metric]+' / '+number(item['time']),number(item['observed']),number(item['predicted']),number(item['absolute_error']),number(item['allowed']),STATUS[item['status']]])
        populate(self.errors,rows)
    def export_sources(self):
        metadata=dict(report_id=self.record['id'],policy_sha256=self.record['policy_sha256']) if self.record else {}
        return [dict(key='validation_cdf',kind='chart',title='Проверка · распределение задержек',widget=self.cdf_chart,x_label='Задержка, ms',y_label='Накопленная доля',metadata=metadata),
            dict(key='validation_time',kind='chart',title='Проверка · '+LABELS[self.metric_combo.currentData()],widget=self.time_chart,x_label='Время, s',y_label=LABELS[self.metric_combo.currentData()],metadata=metadata),
            dict(key='validation_errors',kind='table',title='Проверка · отклонения',widget=self.errors,metadata=metadata),
            dict(key='validation_conditions',kind='table',title='Проверка · условия',widget=self.conditions,metadata=metadata)]
    def export_visuals(self):
        if not self.record:raise ValueError('Сначала выберите или выполните проверку')
        from .export_dialog import ExportDialog
        dialog=ExportDialog(self,self.export_sources(),initial='validation_cdf')
        try:dialog.exec()
        finally:dialog.deleteLater()
    def save_report(self):
        if not self.record:raise ValueError('Нет отчёта для сохранения')
        path,selected=QFileDialog.getSaveFileName(self,'Отчёт о достоверности','validation-report.html','HTML (*.html);;JSON (*.json)')
        if not path:return
        target=Path(path)
        if not target.suffix:target=target.with_suffix('.json' if 'JSON' in selected else '.html')
        if target.suffix.lower() not in ('.json','.html','.htm'):raise ValueError('Выберите HTML или JSON')
        target.write_text(json.dumps(self.record,ensure_ascii=False,indent=2,allow_nan=False) if target.suffix.lower()=='.json' else report_html(self.record),encoding='utf-8')
