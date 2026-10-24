"""Native parameter editors generated from the shared estimator catalogue."""
from copy import deepcopy
from PySide6.QtWidgets import QWidget,QGridLayout,QVBoxLayout,QLineEdit,QSpinBox,QDoubleSpinBox,QDialog,QScrollArea
from lab.ml_algorithms import ALGORITHMS,parameters
from .widgets import label,combo,row,button


class ParameterFloat(QDoubleSpinBox):
    def textFromValue(self,value):
        text=f'{value:.{self.decimals()}f}'
        if self.decimals():text=text.rstrip('0').rstrip('.')
        return text.replace('.',self.locale().decimalPoint())


class ModelOptions(QWidget):
    def __init__(self,parent=None,exclude=()):
        super().__init__(parent);self.algorithm=None;self.controls={};self.saved={};self.exclude=set(exclude)
        self.grid=QGridLayout(self);self.grid.setContentsMargins(0,0,0,0);self.grid.setHorizontalSpacing(16);self.grid.setVerticalSpacing(7)
    def raw_values(self):
        return {key:w.currentData() if item['type']=='choice' else w.text() if item['type']=='layers' else w.value() for key,(w,item) in self.controls.items()}
    def set_algorithm(self,algorithm,initial=None):
        if self.algorithm:self.saved[self.algorithm]=self.raw_values()
        while self.grid.count():
            item=self.grid.takeAt(0)
            if item.widget():item.widget().hide();item.widget().deleteLater()
        self.controls={};self.algorithm=algorithm
        values=initial if initial is not None else self.saved.get(algorithm,{})
        index=0
        for key,item in ALGORITHMS[algorithm]['parameters'].items():
            if key in self.exclude:continue
            value=values.get(key,item['default']);kind=item['type']
            if kind=='choice':
                w=combo([(v,'Без ограничения' if v is None else str(v)) for v in item['values']]);w.setCurrentIndex(w.findData(value))
            elif kind=='layers':w=QLineEdit(', '.join(map(str,value)) if isinstance(value,(list,tuple)) else str(value))
            else:
                w=QSpinBox() if kind=='int' else ParameterFloat()
                if kind=='float':w.setDecimals(12)
                w.setRange(item['min'],item['max']);w.setValue(value)
                if kind=='float':w.setStepType(QDoubleSpinBox.StepType.AdaptiveDecimalStepType)
            w.setAccessibleName(item['title']);w.setToolTip(item['title']);self.controls[key]=(w,item)
            if hasattr(w,'setKeyboardTracking'):w.setKeyboardTracking(False)
            column=index%3;line=(index//3)*2
            title=label(item['title'],'Muted');title.setWordWrap(True);self.grid.addWidget(title,line,column);self.grid.addWidget(w,line+1,column);self.grid.setColumnStretch(column,1);index+=1
    def values(self):return self.raw_values()


class ModelOptionsDialog(QDialog):
    def __init__(self,parent,algorithms,settings=None,trees=120):
        super().__init__(parent);self.setWindowTitle('Параметры моделей');self.resize(850,480)
        self.config=deepcopy(settings or {});self.trees=trees;self.current=None
        layout=QVBoxLayout(self);self.selector=combo([(k,ALGORITHMS[k]['title']) for k in algorithms]);layout.addLayout(row('Модель',self.selector))
        self.description=label('','Muted');self.description.setWordWrap(True);layout.addWidget(self.description)
        area=QScrollArea();area.setWidgetResizable(True);self.options=ModelOptions();area.setWidget(self.options);layout.addWidget(area,1)
        layout.addLayout(row(button('Вернуть стандартные',self.reset),button('Применить',self.accept,primary=True),button('Отмена',self.reject),stretch=True))
        self.selector.currentIndexChanged.connect(self.changed);self.changed()
    def changed(self):
        if self.current:self.config[self.current]=self.options.values()
        self.current=self.selector.currentData();self.options.set_algorithm(self.current,self.config.get(self.current,parameters(self.current,trees=self.trees)))
        self.description.setText(ALGORITHMS[self.current]['description'])
    def reset(self):self.options.set_algorithm(self.current,parameters(self.current,trees=self.trees))
    def result_config(self):
        self.config[self.current]=self.options.values()
        return {key:parameters(key,value,trees=self.trees) for key,value in self.config.items()}
