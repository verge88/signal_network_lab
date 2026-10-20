"""Native form editors for UPF rules and mobility schedules."""
from copy import deepcopy
from PySide6.QtWidgets import QWidget,QHBoxLayout,QDialog,QVBoxLayout,QDialogButtonBox,QLineEdit,QCheckBox
from lab.schema import UpfRule,MobilityEvent
from .widgets import label,button,table,populate,combo,spin


class ConfigList(QWidget):
    def __init__(self,parent,project,kind,values):
        super().__init__();self.owner=parent;self.project=project;self.kind=kind;self.items=deepcopy(values)
        layout=QHBoxLayout(self);layout.setContentsMargins(0,0,0,0);self.count=label('');layout.addWidget(self.count);layout.addWidget(button('Настроить…',self.configure,'settings'));layout.addStretch();self.refresh()
    def refresh(self):self.count.setText(f'Записей: {len(self.items)}')
    def value(self):return deepcopy(self.items)
    def configure(self):
        d=ListDialog(self.owner,self.project,self.kind,self.items)
        if d.exec()==QDialog.DialogCode.Accepted:self.items=d.values();self.refresh()
        d.deleteLater()


class ListDialog(QDialog):
    def __init__(self,parent,project,kind,values):
        super().__init__(parent);self.project=project;self.kind=kind;self.items=deepcopy(values);self.resize(860,500);self.setWindowTitle('Правила UPF' if kind=='upf' else 'Расписание мобильности')
        root=QVBoxLayout(self);tools=QHBoxLayout()
        for title,fn,ico in [('Добавить',lambda:self.edit(),'plus'),('Изменить',lambda:self.edit(True),'settings'),('Удалить',self.remove,'trash')]:tools.addWidget(button(title,fn,ico))
        tools.addStretch();root.addLayout(tools);self.table=table(['Название / время','Выбор / новый gNB','Действие / AMF','Скорость / UPF']);self.table.doubleClicked.connect(lambda:self.edit(True));root.addWidget(self.table,1)
        self.error=label('');self.error.setWordWrap(True);root.addWidget(self.error)
        note=label('Правила проверяются по возрастанию precedence; используется первое совпадение.' if kind=='upf' else 'Задайте связи NGAP, SBI, PFCP и GTPU для новых узлов. Перемещение выполняется для общей группы UE с теми же gNB, AMF, PLMN, срезом и DNN.','Muted');note.setWordWrap(True);root.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);root.addWidget(buttons);self.refresh()
    def refresh(self):
        populate(self.table,[[r['name'],str(r['precedence'])+' · '+(r['flow_id'] or r['dnn'] or 'Все'),{'forward':'Пересылать','drop':'Отбросить'}[r['action']],str(r['rate_mbps'])+' Mbps'] if self.kind=='upf' else [str(r['time'])+' с',r['gnb'],r['amf'] or 'Текущий',r['upf'] or 'Текущий'] for r in self.items])
    def values(self):return deepcopy(self.items)
    def remove(self):
        i=self.table.currentRow()
        if i>=0:self.items.pop(i);self.refresh()
    def edit(self,existing=False):
        from .dialogs import Editor
        i=self.table.currentRow()
        if existing and i<0:return
        if not existing and len(self.items)>=(32 if self.kind=='upf' else 12):self.error.setText('Достигнут предел записей');return
        if self.kind=='upf':
            r=UpfRule.model_validate(self.items[i] if existing else {}).model_dump();d=Editor('Правило UPF',self)
            d.field('name','Название',QLineEdit(r['name']));d.field('precedence','Precedence (меньше = раньше)',spin(r['precedence'],0,65535))
            d.field('flow_id','Сигнальный поток',combo([('','Все потоки')]+[(f['id'],f['id']) for f in self.project.get('flows',[])],r['flow_id']))
            for key,title in [('dnn','DNN (пусто = любой)'),('snssai','S-NSSAI (пусто = любой)')]:d.field(key,title,QLineEdit(r[key]))
            d.field('direction','Направление',combo([('both','Оба'),('uplink','К сети данных'),('downlink','К UE')],r['direction']));d.field('action','Действие',combo([('forward','Пересылать'),('drop','Отбросить')],r['action']))
            d.tab('Ограничение скорости');d.numeric(r,[('rate_mbps','Скорость (0 = без лимита)',0,100000,4,'Mbps'),('burst_bytes','Бюджет всплеска',32,10000000,0,'B'),('max_packet_bytes','Максимальный пакет',32,65536,0,'B')])
        else:
            choices=[(n['id'],n['name']) for n in self.project['nodes'] if n['kind']=='gNB']
            if not choices:self.error.setText('Добавьте gNB в проект');return
            r=MobilityEvent.model_validate(self.items[i] if existing else dict(time=5,gnb=choices[0][0])).model_dump();d=Editor('Перемещение группы UE',self)
            d.field('time','Начало',spin(r['time'],0,599,2,'с'))
            for key,kind,title in [('gnb','gNB','Новый gNB'),('amf','AMF','Новый AMF'),('upf','UPF','Новый UPF')]:
                values=[(n['id'],n['name']) for n in self.project['nodes'] if n['kind']==kind]
                if key!='gnb':values=[('','Сохранить текущий')]+values
                d.field(key,title,combo(values,r[key]))
            d.numeric(r,[('interruption_ms','Минимальная пауза обслуживания',0,30000,1,'ms'),('timeout_ms','Тайм-аут переноса',1,30000,1,'ms')]);check=QCheckBox();check.setChecked(r['transfer_context']);d.field('transfer_context','Перенести контексты',check)
        if d.exec()==QDialog.DialogCode.Accepted:
            try:
                item=(UpfRule if self.kind=='upf' else MobilityEvent).model_validate(d.values()).model_dump()
                if existing:self.items[i]=item
                else:self.items.append(item)
                if self.kind=='mobility':self.items.sort(key=lambda x:x['time'])
                self.refresh();self.error.clear()
            except ValueError as e:self.error.setText(str(e))
        d.deleteLater()
