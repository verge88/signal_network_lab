"""Read-only native inspection of the saved PDU lifecycle report."""
import json
from pathlib import Path
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLineEdit,QSplitter,QTabWidget,QFileDialog,QHeaderView
from PySide6.QtCore import Qt
from .widgets import label,button,row,table,populate,combo

STATES=dict(ESTABLISHING='Создаётся',ACTIVE='Активна',RELEASING='Освобождается',RECONCILING='Повторная очистка',ORPHANED='Остались ресурсы',RELEASED='Освобождена')


def time_text(ms):return f'{ms/1000:.3f}'.replace('.',',')


class SessionDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.settings=parent.settings;self.record=None;self.visible=[]
        self.setWindowTitle('Жизненный цикл 5G-сессий');self.resize(1280,820);self.setMinimumSize(900,600)
        root=QVBoxLayout(self);root.setContentsMargins(18,16,18,16);root.setSpacing(12)
        root.addWidget(label('PDU-сессии и согласование состояния','PanelTitle'))
        self.summary=label('Выберите завершённый прогон.','Muted');self.summary.setWordWrap(True);root.addWidget(self.summary)
        self.search=QLineEdit();self.search.setPlaceholderText('UE, PDU Session ID, узел, DNN или причина')
        self.state=combo([('all','Все состояния')]+list(STATES.items()));self.state.setMinimumWidth(180)
        root.addLayout(row(self.search,self.state));self.search.textChanged.connect(self.refresh);self.state.currentIndexChanged.connect(self.refresh)
        split=QSplitter(Qt.Orientation.Vertical);root.addWidget(split,1)
        self.sessions=table(['Процедура / ID','UE / gNB','DNN / срез','AMF / SMF / UPF','Состояние','Создана / изменена, s','Контексты','Автоочистка','Причина']);split.addWidget(self.sessions)
        self.sessions.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column,width in enumerate([115,100,145,155,145,140,140,105,170]):self.sessions.setColumnWidth(column,width)
        self.sessions.itemSelectionChanged.connect(self.select_session);self.sessions.doubleClicked.connect(lambda _:parent.call(self.open_procedure))
        self.tabs=QTabWidget();split.addWidget(self.tabs)
        self.events=table(['Время, s','Процедура','PDU Session ID','Состояние','Причина']);self.tabs.addTab(self.events,'Переходы состояния')
        self.contexts=table(['Узел','Процедура','PDU Session ID','Состояние контекста','Истекает, s']);self.tabs.addTab(self.contexts,'Оставшиеся контексты')
        split.setSizes([400,240])
        self.note=label('Агрегаты охватывают весь прогон. Таблицы учитывают лимит трасс; выбранная строка фильтрует переходы и контексты.','Muted');self.note.setWordWrap(True);root.addWidget(self.note)
        root.addLayout(row(button('Пошаговый разбор',lambda:parent.call(self.open_procedure),'chart'),button('Экспорт таблиц…',lambda:parent.call(self.export_tables),'download'),button('Отчёт JSON…',lambda:parent.call(self.save_report),'save'),button('Закрыть',self.close),stretch=True))

    def set_run(self,record):
        self.record=record;self.search.clear();self.state.setCurrentIndex(0);report=record.get('session_lifecycle',{})
        if not report:
            self.summary.setText('Этот прогон не содержит отчёта жизненного цикла. Выполните новый расчёт 5G.')
            self.note.setText('Для старого прогона состояния и события сессий не восстанавливаются.');self.refresh();return
        states=' · '.join(f'{STATES.get(k,k)}: {v}' for k,v in report['states'].items())
        self.summary.setText(f'{record.get("name","Эксперимент")} · сессий: {report["total"]} · оставшихся контекстов: {report["remaining_contexts"]} · незавершённых отмен регистрации: {report["pending_deregistrations"]}\n{states}')
        self.note.setText(f'Показано {len(report["sessions"])} / {report["total"]} сессий; переходов {len(report["events"])} / {report["event_count"]}. '+('Выборка сессий ограничена лимитом трасс. ' if report.get('session_sampled') else '')+('Журнал переходов усечён. ' if report.get('events_truncated') else '')+'Состояние и TTL контекстов показаны на конец расчёта, включая завершение уже начатых обменов. Выбор строки фильтрует нижние таблицы.')
        self.refresh()

    def refresh(self,*args):
        report=(self.record or {}).get('session_lifecycle',{});text=self.search.text().strip().lower();state=self.state.currentData()
        self.visible=[r for r in report.get('sessions',[]) if (state=='all' or r['state']==state) and (not text or text in json.dumps(r,ensure_ascii=False).lower() or text in STATES.get(r['state'],'').lower())]
        self.sessions.blockSignals(True)
        populate(self.sessions,[[f'{r["owner"]} / {r["pdu_session_id"]}',f'{r["subscriber_id"]} / {r["gnb"]}',r['dnn']+' / '+r['snssai'],r['amf']+' / '+str(r['nf_selected'].get('SMF','—'))+' / '+str(r['nf_selected'].get('UPF','—')),STATES.get(r['state'],r['state']),time_text(r['created_ms'])+' / '+time_text(r['updated_ms']),', '.join(r['context_nodes']) or 'Нет',r['cleanup_attempts'],r.get('reason') or '—'] for r in self.visible])
        self.sessions.clearSelection();self.sessions.setCurrentCell(-1,-1);self.sessions.blockSignals(False);self.select_session()

    def selected_owner(self):
        index=self.sessions.currentRow();return self.visible[index]['owner'] if 0<=index<len(self.visible) else None

    def select_session(self):
        report=(self.record or {}).get('session_lifecycle',{});owner=self.selected_owner();owners={r['owner'] for r in self.visible}
        eligible=lambda r:r.get('procedure_id',r.get('owner'))==owner if owner is not None else r.get('procedure_id',r.get('owner')) in owners
        populate(self.events,[[f'{r["time"]:.3f}'.replace('.',','),r['procedure_id'],r['pdu_session_id'],STATES.get(r['state'],r['state']),r.get('reason') or '—'] for r in report.get('events',[]) if eligible(r)])
        populate(self.contexts,[[r['node'],r['owner'],r['pdu_session_id'],', '.join(r['flags']),time_text(r['expires_ms'])] for r in report.get('contexts',[]) if eligible(r)])

    def export_sources(self):
        return [dict(key=key,kind='table',widget=widget,title=title,metadata=dict(run_id=(self.record or {}).get('id'),contract='pdu-lifecycle-v1',sampled=(self.record or {}).get('session_lifecycle',{}).get('session_sampled',False))) for key,widget,title in [('sessions',self.sessions,'PDU-сессии · текущий фильтр'),('events',self.events,'PDU · переходы состояния'),('contexts',self.contexts,'PDU · оставшиеся контексты')]]

    def export_tables(self):
        from .export_dialog import ExportDialog
        dialog=ExportDialog(self,self.export_sources())
        try:dialog.exec()
        finally:dialog.deleteLater()

    def save_report(self):
        if not self.record or 'session_lifecycle' not in self.record:raise ValueError('Нет отчёта жизненного цикла')
        path,_=QFileDialog.getSaveFileName(self,'Сохранить отчёт жизненного цикла','pdu-lifecycle.json','JSON (*.json)')
        if path:Path(path).write_text(json.dumps(dict(run_id=self.record.get('id'),engine=self.record.get('environment',{}).get('engine'),report=self.record['session_lifecycle']),ensure_ascii=False,indent=2),encoding='utf-8')

    def open_procedure(self,*args):
        owner=self.selected_owner()
        if owner is None:return
        window=self.parent()
        if not getattr(window,'procedure_dialog',None):
            from .procedure_dialog import ProcedureDialog
            window.procedure_dialog=ProcedureDialog(window)
        window.procedure_dialog.set_run(self.record,owner);window.procedure_dialog.show();window.procedure_dialog.raise_();window.procedure_dialog.activateWindow()
