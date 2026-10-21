"""Native configuration and saved-run inspection of state clusters."""
from copy import deepcopy
import json
import uuid
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QWidget,QListWidget,QListWidgetItem,QLineEdit,QTabWidget,QFileDialog
from lab.schema import StateCluster,Project
from .dialogs import Editor
from .widgets import label,button,row,table,populate,combo

ROLES=dict(LEADER='Лидер',FOLLOWER='Резерв',WAITING='Переключение',NO_QUORUM='Нет большинства',OFFLINE='Недоступен')
EVENTS=dict(leader_changed='Изменён лидер',restart_state_loss='Потеря локального состояния',session_state_lost='Потеря состояния сессии',session_rebound='Сессия переведена на резерв',repository_read='Чтение состояния UDR')


class ClusterMembers(QWidget):
    def __init__(self,project,members,blocked,parent=None):
        super().__init__(parent);self.nodes=[n for n in project['nodes'] if n['id'] not in blocked];self.initial=members
        kinds=[k for k in ('HSS','UDR','AMF','SMF') if sum(n['kind']==k for n in self.nodes)>=2]
        if not kinds:raise ValueError('Добавьте минимум два свободных экземпляра HSS, UDR, AMF или SMF')
        kind=next((n['kind'] for n in self.nodes if members and n['id']==members[0]),kinds[0])
        root=QVBoxLayout(self);root.setContentsMargins(0,0,0,0);self.kind=combo(kinds,kind);self.primary=combo([]);self.members=QListWidget();self.members.setMinimumHeight(150)
        root.addWidget(self.kind);root.addWidget(label('Предпочтительный первый лидер','Muted'));root.addWidget(self.primary);root.addWidget(self.members)
        self.kind.currentIndexChanged.connect(self.fill);self.members.itemChanged.connect(self.update_primary);self.fill()
    def fill(self,*args):
        self.members.blockSignals(True);self.members.clear()
        for n in self.nodes:
            if n['kind']!=self.kind.currentData():continue
            item=QListWidgetItem(n['name']+' · '+n['id']);item.setData(Qt.ItemDataRole.UserRole,n['id']);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if n['id'] in self.initial else Qt.CheckState.Unchecked);self.members.addItem(item)
        self.members.blockSignals(False);self.update_primary()
    def update_primary(self,*args):
        old=self.primary.currentData() or (self.initial[0] if self.initial else None);self.primary.clear()
        for i in range(self.members.count()):
            item=self.members.item(i)
            if item.checkState()==Qt.CheckState.Checked:self.primary.addItem(item.text(),item.data(Qt.ItemDataRole.UserRole))
        index=self.primary.findData(old)
        if index>=0:self.primary.setCurrentIndex(index)
    def value(self):
        members=[self.primary.itemData(i) for i in range(self.primary.count())];first=self.primary.currentData()
        return ([first]+[n for n in members if n!=first]) if first else []


def cluster_editor(parent,project,existing=None,blocked=()):
    initial=existing or StateCluster(id='cluster-'+uuid.uuid4().hex[:7],members=['temporary-a','temporary-b']).model_dump()
    d=Editor('Кластер состояния',parent);d.field('id','ID',QLineEdit(initial['id']));d.field('name','Название',QLineEdit(initial['name']))
    d.field('members','Экземпляры одного типа',ClusterMembers(project,initial['members'] if existing else [],blocked,d))
    d.field('partition_policy','При разделении сети',combo([('availability','Работать в доступных частях'),('quorum','Работать при видимом большинстве')],initial['partition_policy']))
    d.tab('Репликация и переключение')
    d.numeric(initial,[('replication_delay_ms','Задержка применения копии',0,30000,1,'ms'),('heartbeat_ms','Интервал heartbeat',100,10000,1,'ms'),('heartbeat_timeout_ms','Тайм-аут видимости соседа',100,60000,1,'ms'),('failover_ms','Пауза перед переключением',0,30000,1,'ms'),('sync_ms','Интервал повторной синхронизации',100,60000,1,'ms'),('request_timeout_ms','Ожидание подтверждения копии',10,60000,1,'ms')])
    return d


class ClusterDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.owner=parent;self.settings=parent.settings;self.configs=[];self.record=None
        self.setWindowTitle('Кластеры и репликация состояния');self.resize(1320,850);self.setMinimumSize(1000,650)
        root=QVBoxLayout(self);root.setContentsMargins(18,16,18,16);root.setSpacing(12)
        root.addWidget(label('Кластеры состояния','PanelTitle'));self.info=label('','Muted');self.info.setWordWrap(True);root.addWidget(self.info)
        self.tabs=QTabWidget();root.addWidget(self.tabs,1)
        config=QWidget();layout=QVBoxLayout(config)
        layout.addLayout(row(button('Добавить',lambda:parent.call(self.edit),'plus'),button('Изменить',lambda:parent.call(lambda:self.edit(True)),'settings'),button('Удалить',lambda:parent.call(self.remove),'trash'),stretch=True))
        self.configuration=table(['Название / ID','Экземпляры · первый лидер','Политика','Копирование, ms','Heartbeat / тайм-аут, ms','Переключение, ms']);layout.addWidget(self.configuration,1)
        self.configuration.doubleClicked.connect(lambda _:parent.call(lambda:self.edit(True)));self.tabs.addTab(config,'Настройка проекта')
        self.groups=table(['Кластер','Применено копий','Макс. лаг, ms','Потеря записей','Потеря сессий','Переведено сессий','Расхождений ключей','Отказы по вместимости']);self.tabs.addTab(self.groups,'Репликация · результат')
        self.views=table(['Кластер','Экземпляр','Роль','Лидер / эпоха','Видимые экземпляры','Версий состояния']);self.tabs.addTab(self.views,'Локальные представления')
        self.events=table(['Время, s','Кластер','Событие','Экземпляр','Прежний узел / лидер','Новый лидер','Процедура / PDU ID','Потеря записей']);self.tabs.addTab(self.events,'События')
        note=label('Heartbeat и копии используют каналы, CPU и БД. Репликация асинхронная, конфликты разрешаются по времени записи. Видимость соседей может устаревать; это модель без гарантий консенсуса.','Muted');note.setWordWrap(True);root.addWidget(note)
        root.addLayout(row(button('Применить к проекту',lambda:parent.call(self.apply_config),'settings'),button('Обновить результат',lambda:parent.call(self.load_result),'chart'),button('Экспорт таблиц…',lambda:parent.call(self.export_tables),'download'),button('Отчёт JSON…',lambda:parent.call(self.save_report),'save'),button('Закрыть',self.close),stretch=True))
    def reload(self):
        self.configs=deepcopy(self.owner.project.get('clusters',[]));self.refresh_config();self.load_result()
    def refresh_config(self):
        populate(self.configuration,[[c['name']+' / '+c['id'],', '.join(c['members']),'Доступность' if c['partition_policy']=='availability' else 'Большинство',c['replication_delay_ms'],str(c['heartbeat_ms'])+' / '+str(c['heartbeat_timeout_ms']),c['failover_ms']] for c in self.configs])
    def edit(self,existing=False):
        index=self.configuration.currentRow()
        if existing and index<0:raise ValueError('Выберите кластер')
        blocked={n for i,c in enumerate(self.configs) if not existing or i!=index for n in c['members']}
        d=cluster_editor(self,self.owner.project,self.configs[index] if existing else None,blocked)
        try:
            if d.exec()!=QDialog.DialogCode.Accepted:return
            value=StateCluster.model_validate(d.values()).model_dump()
            proposed=deepcopy(self.configs)
            if existing:proposed[index]=value
            else:proposed.append(value)
            Project.model_validate(dict(self.owner.project,clusters=proposed));self.configs=proposed;self.refresh_config()
        finally:d.deleteLater()
    def remove(self):
        index=self.configuration.currentRow()
        if index<0:raise ValueError('Выберите кластер')
        self.configs.pop(index);self.refresh_config()
    def apply_config(self):
        value=Project.model_validate(dict(self.owner.project,clusters=self.configs)).model_dump()['clusters']
        self.owner.mutate('Настроить кластеры состояния',lambda p:p.update(clusters=deepcopy(value)))
        self.owner.statusBar().showMessage('Настройки кластеров применены. Выполните новый эксперимент; Ctrl+Z — отменить.',8000)
    def load_result(self):
        self.record=self.owner.run_record;report=(self.record or {}).get('clusters',{})
        self.info.setText(('Результат: '+str(self.record.get('name',self.record.get('id','Прогон')))+f' · переходов: {len(report.get("events",[]))} / {report.get("event_count",0)}'+(' · журнал усечён' if report.get('truncated') else '')) if self.record and report else 'Отчёт о кластерах отсутствует. Настройте группу и выполните новый эксперимент.')
        populate(self.groups,[[c['name'],c.get('applied_updates',0),round(c.get('lag_max_ms',0),3),c.get('unreplicated_entries_lost',0),c.get('session_state_lost',0),c.get('session_rebound',0),c['divergent_keys'],c.get('capacity_rejections',0)] for c in report.get('groups',[])])
        populate(self.views,[[v['cluster_id'],v['node'],ROLES.get(v['role'],v['role']),str(v['leader'] or '—')+' / '+str(v['term']),', '.join(v['live']),v['entries']] for v in report.get('views',[])])
        populate(self.events,[[f'{v["time"]:.3f}',v['cluster_id'],EVENTS.get(v['event'],v['event']),v.get('node','—'),v.get('previous','—'),v.get('leader','—'),str(v.get('procedure_id','—'))+' / '+str(v.get('pdu_session_id','—')),v.get('unreplicated_entries','—')] for v in report.get('events',[])])
    def export_sources(self):
        report=(self.record or {}).get('clusters',{})
        return [dict(key=key,kind='table',widget=widget,title=title,metadata=dict(run_id=(self.record or {}).get('id'),contract=report.get('version'),events_truncated=report.get('truncated',False))) for key,widget,title in [('cluster_groups',self.groups,'Кластеры · репликация'),('cluster_views',self.views,'Кластеры · локальные представления'),('cluster_events',self.events,'Кластеры · события')]]
    def export_tables(self):
        from .export_dialog import ExportDialog
        d=ExportDialog(self,self.export_sources())
        try:d.exec()
        finally:d.deleteLater()
    def save_report(self):
        report=(self.record or {}).get('clusters')
        if report is None:raise ValueError('Нет отчёта о кластерах')
        path,_=QFileDialog.getSaveFileName(self,'Сохранить отчёт','state-clusters.json','JSON (*.json)')
        if path:Path(path).write_text(json.dumps(dict(run_id=self.record.get('id'),report=report),ensure_ascii=False,indent=2),encoding='utf-8')
