"""Native experiment planning, uncertainty inspection and reproducible results."""
from copy import deepcopy
import json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QTabWidget,
    QLineEdit,QCheckBox,QScrollArea,QFileDialog,QTableWidget,QHeaderView,QAbstractItemView,QSizePolicy)
from lab import experiments
from .widgets import label,button,row,table,populate,combo,spin,Chart

METRIC_NAMES = [('latency_p95','Среднее p95 по прогонам, ms'),('delivery_ratio','Средняя успешность, доля'),('throughput','Средние завершения, /s')]
VERDICTS = dict(pass_='Соответствует',fail='Не соответствует',inconclusive='Недостаточно точности',unavailable='Нет данных')


def formatted(value):
    return '—' if value is None else f'{value:.5g}'


def bounds(stats):
    return formatted(stats['mean'])+'  ['+formatted(stats['low'])+'; '+formatted(stats['high'])+']'+(' · нет данных: '+str(stats['missing']) if stats['missing'] else '')


class ExperimentDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.owner=parent;self.settings=parent.settings;self.record=None;self.source=None
        self.setWindowTitle('Конструктор серий экспериментов');self.resize(1260,850);self.setMinimumSize(980,640)
        root=QVBoxLayout(self);root.setContentsMargins(16,14,16,14);root.setSpacing(10)
        root.addWidget(label('Серии экспериментов','PanelTitle'))
        self.info=label('Настройте параметры, прогрев и число независимых прогонов.','Muted');self.info.setWordWrap(True);root.addWidget(self.info)
        self.tabs=QTabWidget();root.addWidget(self.tabs,1)
        scroll=QScrollArea();scroll.setWidgetResizable(True);config=QWidget();scroll.setWidget(config);layout=QVBoxLayout(config);layout.setSpacing(12)
        self.mode=combo([('series','Сочетания параметров'),('capacity','Поиск допустимой нагрузки')]);self.design=combo([('cartesian','Все сочетания'),('paired','Совместно по строкам значений')])
        layout.addLayout(row(self.caption('Режим'),self.mode,self.caption('План'),self.design))
        self.axes=QTableWidget(0,2);self.axes.setHorizontalHeaderLabels(['Параметр','Значения через точку с запятой']);self.axes.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.axes.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.axes.verticalHeader().hide()
        layout.addLayout(row(button('Добавить параметр',lambda:parent.call(self.add_axis),'plus'),button('Удалить параметр',lambda:parent.call(self.remove_axis),'trash'),stretch=True));layout.addWidget(self.axes)
        limits=QWidget();form=QFormLayout(limits);form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.load_min=spin(.25,.001,20,3);self.load_max=spin(2,.001,20,3);self.search_points=spin(8,2,32)
        form.addRow('Нагрузка · от / до / точек',self.inline(self.load_min,self.load_max,self.search_points))
        self.p95_limit=spin(200,.01,100000,2,'ms');self.success_min=spin(90,.01,100,2,'%');form.addRow('Требования · p95 ≤ / успешность ≥',self.inline(self.p95_limit,self.success_min))
        self.capacity_controls=limits;layout.addWidget(limits)
        form=QFormLayout();form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.warmup=spin(0,0,86399.99,2,'s');self.repetitions=spin(10,1,1000);self.confidence=spin(95,80,99.9,1,'%')
        form.addRow('Прогрев / максимум повторений / доверие',self.inline(self.warmup,self.repetitions,self.confidence))
        self.adaptive=QCheckBox('Остановить точку при достижении точности');form.addRow(self.adaptive)
        self.minimum=spin(10,3,1000);self.batch=spin(5,1,100);form.addRow('Минимум повторений / шаг проверки',self.inline(self.minimum,self.batch))
        self.precision_choice=combo(METRIC_NAMES);self.absolute=spin(5,0,100000,4);self.relative=spin(5,0,100,2,'%')
        form.addRow('Метрика точности',self.precision_choice);form.addRow('Допуск · абсолютный / относительный',self.inline(self.absolute,self.relative))
        self.save_runs=QCheckBox('Сохранять также полные отдельные прогоны в библиотеку');form.addRow(self.save_runs);layout.addLayout(form)
        note=label('Прогрев входит в длительность проекта. Абсолютный допуск задан в единицах выбранной метрики: ms, доля 0–1 или /s. Достаточен любой из двух допусков. При поиске нагрузки проверяются требования к среднему p95 и средней успешности независимых прогонов.','Muted');note.setWordWrap(True);layout.addWidget(note);layout.addStretch()
        self.tabs.addTab(scroll,'План')
        results=QWidget();result_layout=QVBoxLayout(results)
        self.summary=label('Выполните серию, чтобы увидеть сравнение.','Muted');self.summary.setWordWrap(True);result_layout.addWidget(self.summary)
        self.plot_choice=combo(METRIC_NAMES);self.plot_choice.currentIndexChanged.connect(self.render_plot)
        result_layout.addLayout(row(self.caption('График · ось X: номер точки'),self.plot_choice,stretch=True));self.chart=Chart();self.chart.setMaximumHeight(230);result_layout.addWidget(self.chart)
        self.points_table=table(['Точка · параметры','Повторов / остановка','p95, ms · [интервал]','Успешность · [интервал]','Завершения, /s · [интервал]','Требования']);result_layout.addWidget(self.points_table,1)
        result_layout.addLayout(row(button('Загрузить выбранную точку в проект',lambda:parent.call(self.apply_point),'settings'),stretch=True));self.tabs.addTab(results,'Сравнение')
        runs=QWidget();runs_layout=QVBoxLayout(runs);runs_layout.addWidget(label('Каждая строка — независимый прогон. Seed сопоставлены между точками.','Muted'))
        self.runs_table=table(['Точка','Seed','Начато после прогрева','Успешно','p95, ms','Успешность','Завершения, /s','Завершено в хвосте','ID сохранённого прогона']);runs_layout.addWidget(self.runs_table,1)
        self.tabs.addTab(runs,'Прогоны')
        method=label('Единица статистики — независимый прогон, а не отдельный пакет.\n\nДля среднего p95 и скорости завершений используется интервал Стьюдента. Он приближённый и опирается на распределение средних по прогонам. Это интервал среднего p95, а не квантиля объединённого трафика.\n\nДля успешности используется консервативный эмпирический интервал Бернштейна по долям отдельных прогонов. Нулевые наблюдаемые потери не означают нулевую погрешность.\n\nПоправка Бонферрони учитывает все запланированные точки, три метрики и возможные проверки досрочной остановки. Неудачная точка не удаляется из отчёта. Отсутствующие измерения блокируют интервал и остановку по точности.\n\nПоиск нагрузки показывает наибольшую проверенную точку, для которой верхняя граница p95 и нижняя граница успешности удовлетворяют требованиям. Непроверенные нагрузки не подтверждены. Прохождение верхней точки означает, что границу диапазона следует расширить.\n\nРеальные измерения оборудования необходимы для внешнего подтверждения модели. Подробная методика находится в разделе «Справка → Серии экспериментов».','Muted');method.setWordWrap(True);method.setAlignment(Qt.AlignmentFlag.AlignTop)
        method_page=QWidget();ml=QVBoxLayout(method_page);ml.addWidget(method);ml.addStretch();self.tabs.addTab(method_page,'Методика')
        self.run_button=button('Выполнить серию',lambda:parent.call(self.start),'experiment',True)
        root.addLayout(row(button('Из текущего проекта',self.reload_project,'folder'),button('Проверить план',lambda:parent.call(self.preview),'settings'),self.run_button,button('Отменить',parent.cancel_job),button('Экспорт…',lambda:parent.call(self.export_results),'download'),button('JSON…',lambda:parent.call(self.save_report),'save'),button('Закрыть',self.close),stretch=True))
        self.mode.currentIndexChanged.connect(self.update_controls);self.adaptive.toggled.connect(self.update_controls)
        parent.compute_buttons.append(self.run_button);self.run_button.setEnabled(not parent.active_job)
        self.update_controls()
    @staticmethod
    def caption(text):
        widget=label(text);widget.setSizePolicy(QSizePolicy.Policy.Maximum,QSizePolicy.Policy.Preferred);return widget
    @staticmethod
    def inline(*widgets):
        container=QWidget();layout=QHBoxLayout(container);layout.setContentsMargins(0,0,0,0)
        for w in widgets:layout.addWidget(w)
        return container
    def update_controls(self,*args):
        capacity=self.mode.currentData()=='capacity';self.capacity_controls.setVisible(capacity);self.axes.setEnabled(not capacity);self.design.setEnabled(not capacity)
        self.adaptive.setText('Остановить точку после решения по требованиям' if capacity else 'Остановить точку при достижении точности')
        for widget in (self.minimum,self.batch):widget.setEnabled(self.adaptive.isChecked())
        for widget in (self.precision_choice,self.absolute,self.relative):widget.setEnabled(self.adaptive.isChecked() and not capacity)
    def reload_project(self):
        self.source=deepcopy(self.owner.project);self.axes.setRowCount(0);self.add_axis()
        self.info.setText('Проект: '+self.source['name']+' · длительность '+str(self.source['duration'])+' s. Изменения плана применяются только к серии.')
    def add_axis(self,path='load',values=None):
        if self.axes.rowCount()>=4:raise ValueError('В серии допускается до четырёх параметров')
        if not self.source:self.source=deepcopy(self.owner.project)
        i=self.axes.rowCount();self.axes.insertRow(i);self.axes.setRowHeight(i,44)
        self.axes.setCellWidget(i,0,combo(experiments.parameter_catalog(self.source),path));self.axes.setCellWidget(i,1,QLineEdit('; '.join(str(v) for v in (values or [.5,1,1.5,2]))))
        self.resize_axes()
    def resize_axes(self):
        self.axes.setFixedHeight(self.axes.horizontalHeader().sizeHint().height()+self.axes.rowCount()*44+4)
    def remove_axis(self):
        if self.axes.rowCount()<=1:raise ValueError('Оставьте хотя бы один параметр')
        index=self.axes.currentRow()
        if index<0:raise ValueError('Выберите строку параметра')
        self.axes.removeRow(index);self.resize_axes()
    def options(self):
        axes=[]
        if self.mode.currentData()=='series':
            for i in range(self.axes.rowCount()):
                text=self.axes.cellWidget(i,1).text().strip()
                values=[float(v.strip().replace(',','.')) for v in text.split(';')] if ';' in text else [float(v.strip()) for v in text.split(',')]
                axes.append(dict(path=self.axes.cellWidget(i,0).currentData(),values=values))
        return experiments.Options(mode=self.mode.currentData(),axes=axes or [dict(path='load',values=[1,2])],design=self.design.currentData(),
            repetitions=self.repetitions.value(),adaptive=self.adaptive.isChecked(),min_repetitions=self.minimum.value(),batch=self.batch.value(),
            warmup_s=self.warmup.value(),confidence=self.confidence.value()/100,precision_metric=self.precision_choice.currentData(),
            absolute_tolerance=self.absolute.value(),relative_tolerance=self.relative.value()/100,load_min=self.load_min.value(),load_max=self.load_max.value(),
            search_points=self.search_points.value(),p95_limit_ms=self.p95_limit.value(),success_min=self.success_min.value()/100,save_runs=self.save_runs.isChecked())
    def preview(self):
        design=experiments.plan(self.source,self.options());self.info.setText(f'План проверен: {len(design["points"])} точек · до {design["runs_max"]} прогонов · до {design["base_procedures_max"]:,.0f} базовых процедур. Состояние и события сохраняются во время прогрева.')
        return design
    def start(self):
        self.preview();source=deepcopy(self.source);options=self.options()
        self.owner.start_job('Серия экспериментов',lambda progress,cancel:experiments.run_series(source,options,progress,cancel),self.set_record)
    def set_record(self,record):
        self.record=record;self.source=deepcopy(record['project']);opts=experiments.Options.model_validate(record['options'])
        self.mode.setCurrentIndex(self.mode.findData(opts.mode));self.design.setCurrentIndex(self.design.findData(opts.design));self.axes.setRowCount(0)
        for axis in opts.axes:self.add_axis(axis.path,axis.values)
        for widget,value in ((self.repetitions,opts.repetitions),(self.minimum,opts.min_repetitions),(self.batch,opts.batch),(self.warmup,opts.warmup_s),(self.confidence,opts.confidence*100),(self.absolute,opts.absolute_tolerance),(self.relative,opts.relative_tolerance*100),(self.load_min,opts.load_min),(self.load_max,opts.load_max),(self.search_points,opts.search_points),(self.p95_limit,opts.p95_limit_ms),(self.success_min,opts.success_min*100)):widget.setValue(value)
        self.precision_choice.setCurrentIndex(self.precision_choice.findData(opts.precision_metric));self.adaptive.setChecked(opts.adaptive);self.save_runs.setChecked(opts.save_runs);self.update_controls()
        titles=dict(experiments.parameter_catalog(self.source));titles['load']='Нагрузка';self.parameter_titles=titles
        populate(self.points_table,[[str(g['point']+1)+' · '+', '.join(titles[k]+' = '+formatted(v) for k,v in g['parameters'].items()),str(g['n'])+' / '+dict(precision='точность',requirements='решение',budget='лимит').get(g['stop_reason'],'лимит'),*[bounds(g['metrics'][key]) for key in experiments.METRICS],VERDICTS.get('pass_' if g['verdict']=='pass' else g['verdict'],'—') if opts.mode=='capacity' else '—'] for g in record['groups']])
        populate(self.runs_table,[[r['point']+1,r['seed'],r['sent'],r['delivered'],formatted(r['latency_p95']),formatted(r['delivery_ratio']),formatted(r['throughput']),r['completed_in_tail'],r.get('run_id') or 'Только в отчёте серии'] for r in record['results']])
        text=f'{len(record["groups"])} точек · {len(record["results"])} прогонов · прогрев {opts.warmup_s:g} s · совместное доверие {opts.confidence:.1%}; интервалы p95 и скорости приближённые.'
        if record.get('capacity'):
            cap=record['capacity'];text+=' Максимальная подтверждённая точка: '+formatted(cap['best_tested_load'])+'×. Шаг сетки: '+formatted(cap['grid_step'])+'×.'
            if cap['right_censored']:text+=' Верхняя точка проходит: предел за пределами проверенного диапазона не определён.'
            if cap['non_monotone']:text+=' Обнаружена немонотонность: есть прохождение после более низкой неуспешной нагрузки.'
            if cap['inconclusive']:text+=f' Неопределённых точек: {cap["inconclusive"]}.'
        self.summary.setText(text);self.info.setText(record['name']);self.render_plot();self.tabs.setCurrentIndex(1)
    def render_plot(self,*args):
        if not self.record:return
        key=self.plot_choice.currentData();series=[]
        for field,name,color in [('mean','Среднее','#8fe0c2'),('low','Нижняя граница','#71849e'),('high','Верхняя граница','#b295db')]:
            # Separate segments at missing observations: no implied interpolation across absent evidence.
            points=[]
            for g in self.record['groups']:
                value=g['metrics'][key][field]
                if value is None:
                    if points:series.append(dict(name=name,color=color,points=points,fill=False,dash=field!='mean'));points=[]
                else:points.append((g['point']+1,value))
            if points:series.append(dict(name=name,color=color,points=points,fill=False,dash=field!='mean'))
        self.chart.set_series(series)
    def apply_point(self):
        if not self.record:raise ValueError('Сначала выполните или откройте серию')
        index=self.points_table.currentRow()
        if index<0:raise ValueError('Выберите точку в таблице')
        point=self.record['groups'][index]['point'];sample=next(r for r in self.record['results'] if r['point']==point)
        config=experiments.configuration(self.record['project'],sample['parameters'],sample['seed']).model_dump()
        self.owner.mutate('Конфигурация точки серии',lambda p:(p.clear(),p.update(config)))
        self.info.setText('Точка загружена в проект с seed первого прогона. F5 выполнит полный прогон; прогрев применяется при запуске серии.')
    def export_sources(self):
        meta=dict(series_id=(self.record or {}).get('id'),contract=experiments.CONTRACT,inference=(self.record or {}).get('inference'))
        return [dict(key=key,kind=kind,widget=widget,title=title,metadata=meta,x_label='Номер точки',y_label=self.plot_choice.currentText()) for key,kind,widget,title in [('experiment_points','table',self.points_table,'Серия · сравнение точек'),('experiment_runs','table',self.runs_table,'Серия · независимые прогоны'),('experiment_chart','chart',self.chart,'Серия · '+self.plot_choice.currentText())]]
    def export_results(self):
        if not self.record:raise ValueError('Нет результатов серии')
        from .export_dialog import ExportDialog
        dialog=ExportDialog(self,self.export_sources())
        try:dialog.exec()
        finally:dialog.deleteLater()
    def save_report(self):
        if not self.record:raise ValueError('Нет результатов серии')
        path,_=QFileDialog.getSaveFileName(self,'Отчёт серии','experiment-series.json','JSON (*.json)')
        if path:Path(path).write_text(json.dumps(self.record,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
