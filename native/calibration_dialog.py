import json
from PySide6.QtWidgets import QDialog,QVBoxLayout,QFormLayout,QHBoxLayout,QDialogButtonBox,QMessageBox
from .widgets import label,button,combo,spin,table
from lab.calibration_search import Options,NODE_FIELDS,LINK_FIELDS


class CalibrationDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.setWindowTitle('Настройки калибровки');self.resize(850,600);self.settings=parent.settings
        try:self.options=Options.model_validate(json.loads(self.settings.value('calibration/options','{}')))
        except (ValueError,TypeError):self.options=Options()
        self.choices=[('global:'+key,'Глобально · '+name) for key,name in [('latency','задержка'),('processing','обработка'),('loss','потери'),('recovery','восстановление')]]
        for group,fields in [('node',NODE_FIELDS),('link',LINK_FIELDS)]:
            for item in parent.project['nodes' if group=='node' else 'links']:
                for field in sorted(fields):self.choices.append((f'{group}:{item["id"]}:{field}',f'{item.get("name",item["id"])} · {field}'))
        root=QVBoxLayout(self);root.addWidget(label('Параметры и границы поиска','PanelTitle'));text=label('Глобальные коэффициенты или абсолютные параметры конкретных узлов и каналов. Абсолютный параметр потерь позволяет подобрать ненулевую вероятность из исходного нуля.','Muted');text.setWordWrap(True);root.addWidget(text)
        form=QFormLayout();self.passes=spin(self.options.passes,1,12);self.budget=spin(self.options.max_evaluations,5,200);self.seeds=spin(self.options.seeds,1,5);self.test_fraction=spin(self.options.test_fraction,.2,.5,2)
        for title,field in [('Проходов с уточнением шага',self.passes),('Бюджет вариантов',self.budget),('Seed на независимый опыт',self.seeds),('Доля отложенных run_id',self.test_fraction)]:form.addRow(title,field)
        root.addLayout(form);self.parameters=table(['Параметр','Минимум','Максимум','Шкала']);root.addWidget(self.parameters,1)
        for spec in (self.options.parameters or [dict(path='global:'+key,lower=.25,upper=4,logarithmic=True) for key in ('latency','processing','loss','recovery')]):self.add_parameter(spec.model_dump() if hasattr(spec,'model_dump') else spec)
        row=QHBoxLayout();row.addWidget(button('Добавить параметр',lambda:self.add_parameter()));row.addWidget(button('Удалить строку',lambda:self.parameters.removeRow(self.parameters.currentRow()) if self.parameters.currentRow()>=0 else None));row.addStretch();root.addLayout(row)
        note=label('Поиск использует только train. После выбора параметров test оценивается отдельно; интервалы ошибки рассчитываются по независимым группам.','Muted');note.setWordWrap(True);root.addWidget(note)
        actions=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);actions.button(QDialogButtonBox.StandardButton.Ok).setText('Применить');actions.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена');actions.accepted.connect(self.apply);actions.rejected.connect(self.reject);root.addWidget(actions)

    def add_parameter(self,spec=None):
        if self.parameters.rowCount()>=16:return
        spec=spec or dict(path='global:latency',lower=.25,upper=4,logarithmic=True);index=self.parameters.rowCount();self.parameters.insertRow(index)
        target=combo(self.choices,spec['path']);lower=spin(spec['lower'],0,100000,6);upper=spin(spec['upper'],.000001,100000,6);scale=combo([(False,'Линейная'),(True,'Логарифмическая')],spec.get('logarithmic',False))
        for column,widget in enumerate((target,lower,upper,scale)):self.parameters.setCellWidget(index,column,widget)
        def defaults():
            path=target.currentData();key=path.split(':')[-1]
            lo,hi=(.25,4) if path.startswith('global:') else (0,1) if key in ('loss','reverse_loss','error_rate','cache_hit_ratio') else (1,128) if key=='workers' else (1,100000) if key=='capacity' else (.001,100000) if key=='bandwidth_mbps' else (0,5000) if key in ('latency_ms','reverse_latency_ms') else (50,60000) if key=='heartbeat_ms' else (10,30000) if key=='control_timeout_ms' else (0,1000) if key in ('processing_ms','database_ms','jitter_ms') else (0,30000)
            lower.setValue(lo);upper.setValue(hi);scale.setCurrentIndex(1 if path.startswith('global:') else 0)
        target.currentIndexChanged.connect(defaults)

    def values(self):
        parameters=[]
        for row in range(self.parameters.rowCount()):parameters.append(dict(path=self.parameters.cellWidget(row,0).currentData(),lower=self.parameters.cellWidget(row,1).value(),upper=self.parameters.cellWidget(row,2).value(),logarithmic=self.parameters.cellWidget(row,3).currentData()))
        return Options(parameters=parameters,passes=self.passes.value(),max_evaluations=self.budget.value(),seeds=self.seeds.value(),test_fraction=self.test_fraction.value(),metric_weights=self.options.metric_weights)

    def apply(self):
        try:self.options=self.values();self.settings.setValue('calibration/options',self.options.model_dump_json());self.settings.sync();self.accept()
        except ValueError as error:QMessageBox.warning(self,'Параметры калибровки',str(error))
