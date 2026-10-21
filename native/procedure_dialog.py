"""Native event-by-event procedure viewer and vector sequence fragment."""
import json
import math
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QRectF,Signal,QPointF,QSize
from PySide6.QtGui import QPainter,QPen,QBrush,QFont,QFontMetrics,QImage,QPdfWriter,QPageSize,QPageLayout,QPolygonF
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QSplitter,QTabWidget,
    QPlainTextEdit,QLineEdit,QSlider,QGraphicsView,QGraphicsScene,QGraphicsItem,QFileDialog,QCheckBox)
from lab import research
from lab.procedure_trace import KINDS,title,describe,snapshot
from .widgets import label,button,combo,row,table,populate
from .style import themed_color as QColor


def num(value):return '—' if value is None else f'{value:.4f}'.rstrip('0').rstrip('.')


class SequenceView(QGraphicsView):
    event_selected=Signal(int)
    PAGE=120
    def __init__(self,parent=None):
        super().__init__(parent);self.scene_model=QGraphicsScene(self);self.setScene(self.scene_model);self.session=None;self.project=None;self.cursor=-1;self.page=-1;self.future=False
        self.setRenderHints(QPainter.RenderHint.Antialiasing|QPainter.RenderHint.TextAntialiasing);self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setAccessibleName('Диаграмма обмена по событиям процедуры');self.scene_model.selectionChanged.connect(self.on_select)
    def on_select(self):
        items=self.scene_model.selectedItems()
        if items and isinstance(items[0].data(0),int) and items[0].data(0)!=self.cursor:self.event_selected.emit(items[0].data(0))
    def set_session(self,session,project):
        self.session=session;self.project=project;self.page=-1;self.resetTransform()
    def set_cursor(self,index,future=False):
        self.cursor=index;self.future=future;self.draw_page()
    def draw_page(self):
        self.scene_model.blockSignals(True);self.scene_model.clear();self.setBackgroundBrush(QColor('#141c27'));self.scene_model.setBackgroundBrush(QColor('#141c27'))
        if not self.session or self.cursor<0:self.scene_model.blockSignals(False);return
        events=self.session['events'];page=self.cursor//self.PAGE;lo=page*self.PAGE;hi=min(len(events),lo+self.PAGE)
        nodes={node['id']:node for node in self.project['nodes']};actors=[]
        for event in events:
            for key in ('node','source','target','receiver'):
                value=event.get(key)
                if value in nodes and value not in actors:actors.append(value)
        if not actors:actors=list(nodes)[:2]
        positions={actor:210+i*180 for i,actor in enumerate(actors)};width=max(450,len(actors)*180+150)
        shown=hi if self.future else self.cursor+1;height=105+max(1,shown-lo)*36
        font=QFont('Segoe UI',9)
        def text(x,y,value,color='#e5ebf5'):
            item=self.scene_model.addSimpleText(str(value),font);item.setBrush(QColor(color));item.setPos(x,y);item.setAcceptedMouseButtons(Qt.MouseButton.NoButton);return item
        text(10,0,f'События {lo+1}–{hi} из {len(events)} · порядок событий, время указано в ms','#8c9bb0')
        for actor,x in positions.items():
            caption=f'{nodes[actor]["kind"]} · {actor}';text(x-64,27,QFontMetrics(font).elidedText(caption,Qt.TextElideMode.ElideRight,164)).setToolTip(caption)
            self.scene_model.addLine(x,52,x,height,QPen(QColor('#344153'),1,Qt.PenStyle.DashLine))
        for index in range(lo,shown):
            event=events[index];y=80+(index-lo)*36;kind=event['kind'];future=index>self.cursor
            bg=self.scene_model.addRect(0,y-2,width,32,QPen(Qt.PenStyle.NoPen),QBrush(QColor('#263b38') if index==self.cursor else QColor('#141c27')))
            bg.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable);bg.setData(0,index);bg.setZValue(-1)
            text(6,y,f'#{event["id"]}  {event["time_ms"]:.3f}', '#566376' if future else '#8c9bb0')
            source=event.get('node') or event.get('source');target=event.get('receiver') or event.get('target')
            arrow=kind in ('message_created','message_arrived','channel_transmit','ack_sent') and source in positions and target in positions and source!=target
            color='#566376' if future else '#e7b375' if kind in ('timer_expired','transport_retry','procedure_retry','message_failed','channel_drop','node_fault') else '#8fe0c2'
            if arrow:
                x1,x2=positions[source],positions[target];direction=1 if x2>x1 else -1
                self.scene_model.addLine(x1,y+22,x2,y+22,QPen(QColor(color),1.5,Qt.PenStyle.DashLine if event.get('role')=='answer' or kind=='ack_sent' else Qt.PenStyle.SolidLine))
                self.scene_model.addPolygon(QPolygonF([QPointF(x2,y+22),QPointF(x2-direction*7,y+18),QPointF(x2-direction*7,y+26)]),QPen(QColor(color)),QBrush(QColor(color)))
                caption=title(event)+' · '+str(event.get('operation',''))
                text(min(x1,x2)+5,y-1,caption[:72],color)
            else:
                x=positions.get(source,210);caption=title(event)
                if event.get('message_id') is not None:caption+=' · M'+str(event['message_id'])
                if event.get('reason'):caption+=' · '+event['reason']
                text(x-62,y+1,caption[:70],color)
        self.scene_model.blockSignals(False);self.scene_model.setSceneRect(0,0,width,height)
        selected=events[self.cursor];actor=selected.get('node') or selected.get('source');x=positions.get(actor,210)
        self.ensureVisible(QRectF(max(0,x-90),75+(self.cursor-lo)*36,240,40),15,30)
    def wheelEvent(self,event):
        if event.modifiers()&Qt.KeyboardModifier.ControlModifier:self.scale(1.15 if event.angleDelta().y()>0 else 1/1.15,1.15 if event.angleDelta().y()>0 else 1/1.15);event.accept()
        else:super().wheelEvent(event)
    def export_fragment(self,path,fmt):
        rect=self.scene_model.sceneRect()
        if fmt=='svg':
            device=QSvgGenerator();device.setFileName(str(path));device.setSize(QSize(math.ceil(rect.width()),math.ceil(rect.height())));device.setViewBox(rect)
        elif fmt=='pdf':
            device=QPdfWriter(str(path));device.setResolution(150);device.setPageSize(QPageSize(QPageSize.PageSizeId.A4));device.setPageOrientation(QPageLayout.Orientation.Landscape)
        else:
            factor=min(2.,math.sqrt(16000000/max(1,rect.width()*rect.height())))
            device=QImage(math.ceil(rect.width()*factor),math.ceil(rect.height()*factor),QImage.Format.Format_ARGB32);device.fill(QColor('#141c27'))
        painter=QPainter(device)
        try:self.scene_model.render(painter,QRectF(0,0,device.width(),device.height()),rect)
        finally:painter.end()
        if fmt=='png' and not device.save(str(path),'PNG'):raise ValueError('Не удалось сохранить PNG')


class ProcedureDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.owner=parent;self.settings=parent.settings;self.run_record=None;self.session=None;self.cursor=-1;self.syncing=False
        self.setWindowTitle('Пошаговый разбор процедуры');self.resize(1300,900);self.setMinimumSize(1080,700)
        root=QVBoxLayout(self);root.setContentsMargins(14,12,14,12);root.setSpacing(9)
        self.info=label('Выберите процедуру','Muted');self.info.setWordWrap(True);root.addWidget(self.info)
        self.sessions=combo([]);self.replay_button=button('Записать выбранную процедуру',lambda:self.owner.call(self.replay),'experiment');self.replay_button.setToolTip('Полный повтор исходной конфигурации и seed с подробной записью выбранного ID. Сохраняется отдельным прогоном.')
        self.owner.compute_buttons.append(self.replay_button)
        root.addLayout(row(label('Сессия'),self.sessions,self.replay_button))
        self.future=QCheckBox('Показывать будущие события');self.search=QLineEdit();self.search.setPlaceholderText('Найти событие, узел или операцию…')
        root.addLayout(row(button('В начало',lambda:self.goto(0)),button('Назад',lambda:self.goto(self.cursor-1)),button('Пуск / пауза',self.toggle_play,'play'),button('Вперёд',lambda:self.goto(self.cursor+1)),button('В конец',lambda:self.goto(10**9)),self.future,self.search,button('Найти далее',self.find_next)))
        self.slider=QSlider(Qt.Orientation.Horizontal);self.slider.valueChanged.connect(self.goto);root.addWidget(self.slider)
        split=QSplitter(Qt.Orientation.Horizontal);root.addWidget(split,1)
        diagram=QWidget();layout=QVBoxLayout(diagram);layout.setContentsMargins(0,0,0,0)
        layout.addLayout(row(label('Диаграмма обмена','PanelTitle'),button('−',lambda:self.diagram.scale(1/1.2,1/1.2)),button('+',lambda:self.diagram.scale(1.2,1.2)),button('1:1',lambda:self.diagram.resetTransform()),stretch=True))
        self.diagram=SequenceView();layout.addWidget(self.diagram,1);split.addWidget(diagram)
        self.tabs=QTabWidget();self.tabs.setStyleSheet('QTabBar::tab { padding: 9px 8px; }');split.addWidget(self.tabs);split.setSizes([720,550])
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.tabs.addTab(self.details,'Причины')
        self.events_table=table(['№','Время, ms','Событие','Узел / сообщение']);self.tabs.addTab(self.events_table,'События')
        self.timers=table(['Таймер','Состояние','Срок, ms','Осталось, ms']);self.tabs.addTab(self.timers,'Таймеры')
        self.timings=table(['Сообщение','CPU: ожидание','БД: ожидание','Канал: ожидание','CPU / БД: работа']);self.tabs.addTab(self.timings,'Время, ms')
        self.resources=table(['Ресурс','Узел','Сообщение','Прошло, ms']);self.tabs.addTab(self.resources,'В работе')
        self.contexts=table(['Узел / хранилище','Контекст','Состояние','TTL до, ms']);self.tabs.addTab(self.contexts,'Контексты')
        root.addLayout(row(button('К причине',self.goto_cause),button('Следующий тайм-аут',self.next_timeout),button('Экспорт таблиц…',lambda:self.owner.call(self.export_tables),'download'),button('Диаграмма: фрагмент…',lambda:self.owner.call(self.export_diagram),'download'),button('Журнал JSON…',lambda:self.owner.call(self.export_json),'save'),stretch=True))
        self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.play_step)
        self.sessions.currentIndexChanged.connect(self.choose_session);self.future.toggled.connect(lambda:self.goto(self.cursor));self.diagram.event_selected.connect(self.goto)
        self.events_table.itemSelectionChanged.connect(self.select_event)
    def set_run(self,record,procedure_id=None):
        self.timer.stop();self.run_record=record;self.records={}
        for trace in record.get('traces',[]):
            if trace.get('procedure_id') is not None:self.records[trace['procedure_id']]=dict(trace,events=[])
        for session in record.get('procedure_journal',{}).get('sessions',[]):self.records[session['procedure_id']]=session
        self.sessions.blockSignals(True);self.sessions.clear()
        for pid,session in sorted(self.records.items()):
            self.sessions.addItem(f'#{pid} · {session.get("session_id","")} · {session.get("operation","")} · {session.get("status","")}'+(' · подробно' if session.get('events') else ' · без журнала'),pid)
        selected=self.sessions.findData(procedure_id)
        if selected<0:selected=next((i for i in range(self.sessions.count()) if self.records[self.sessions.itemData(i)].get('events')),0)
        self.sessions.setCurrentIndex(selected);self.sessions.blockSignals(False);self.choose_session()
    def refresh_theme(self):
        if self.session and self.session.get('events'):self.diagram.set_cursor(self.cursor,self.future.isChecked())
    def choose_session(self):
        self.timer.stop();self.session=self.records.get(self.sessions.currentData());self.cursor=-1
        events=self.session.get('events',[]) if self.session else []
        self.slider.blockSignals(True);self.slider.setRange(0,max(0,len(events)-1));self.slider.blockSignals(False)
        self.events_table.blockSignals(True)
        populate(self.events_table,[[event['id'],num(event['time_ms']),title(event),str(event.get('node',''))+' / '+str(event.get('message_id',''))] for event in events])
        self.events_table.blockSignals(False)
        self.diagram.set_session(self.session if events else None,self.run_record['project']);self.goto(len(events)-1)
        self.replay_button.setEnabled(self.owner.active_job is None and self.session is not None and self.run_record['project'].get('engine','procedures')=='procedures')
    def goto(self,index):
        if self.syncing:return
        events=self.session.get('events',[]) if self.session else []
        if not events:
            self.info.setText('Подробного журнала нет. Старые записи не восстанавливаются из агрегатов. Можно выполнить отдельный повтор исходной конфигурации с записью выбранной процедуры.')
            self.details.setPlainText('Выберите записанную сессию или нажмите «Записать выбранную процедуру». Повтор старой версии движка может отличаться; исходный прогон сохраняется.')
            for widget in (self.timers,self.timings,self.resources,self.contexts):populate(widget,[])
            self.diagram.set_cursor(-1);return
        self.cursor=max(0,min(len(events)-1,index));event=events[self.cursor];self.syncing=True
        self.slider.setValue(self.cursor);self.events_table.selectRow(self.cursor);self.syncing=False
        self.diagram.set_cursor(self.cursor,self.future.isChecked());self.details.setPlainText(describe(self.session,self.cursor));state=snapshot(self.session,self.cursor);time=event['time_ms']
        journal=self.run_record.get('procedure_journal',{});replay=self.run_record.get('replay')
        note=f'Событие {self.cursor+1}/{len(events)} · {time:.3f} ms · {title(event)}. Подробных сессий: {len(journal.get("sessions",[]))} из {journal.get("seen","—")}.'
        if self.session.get('dropped_events'):note+=f' Журнал усечён: пропущено {self.session["dropped_events"]} событий; состояние после последней записи неизвестно.'
        if replay:note+=' Повтор: '+('метрики и окна совпали.' if replay['same_summary'] and replay['same_windows'] else 'метрики отличаются; идентичность старой сессии не гарантируется.')
        self.info.setText(note)
        populate(self.timers,[[key,{'active':'Действует','expired':'Истёк','cancelled':'Отменён'}[value['state']],num(value.get('deadline_ms')),num(max(0,value.get('deadline_ms',time)-time)) if value['state']=='active' else '—'] for key,value in state['timers'].items()])
        populate(self.timings,[[value['message_id'],num(value['cpu_wait_ms']),num(value['db_wait_ms']),num(value['channel_wait_ms']),num(value['cpu_service_ms'])+' / '+num(value['db_service_ms'])] for value in state['timings']])
        populate(self.resources,[[value['resource'],value['node'],value['message_id'],num(time-value['start'])] for value in state['resources']])
        populate(self.contexts,[[value['node']+' / '+value['store'],json.dumps(value['key'],ensure_ascii=False),json.dumps(value['value'],ensure_ascii=False),num(value['deadline_ms'])] for value in state['contexts']])
    def select_event(self):
        if not self.syncing and self.events_table.currentRow()>=0:self.goto(self.events_table.currentRow())
    def goto_cause(self):
        if self.session and self.cursor>=0:
            cause=self.session['events'][self.cursor].get('cause_event')
            if cause:self.goto(cause-1)
    def next_timeout(self):
        if self.session:
            index=next((i for i,event in enumerate(self.session['events']) if i>self.cursor and event['kind']=='timer_expired'),None)
            if index is not None:self.goto(index)
    def find_next(self):
        if not self.session:return
        query=self.search.text().casefold();events=self.session['events'];order=list(range(self.cursor+1,len(events)))+list(range(self.cursor+1))
        for index in order:
            if query in (title(events[index])+' '+json.dumps(events[index],ensure_ascii=False)).casefold():self.goto(index);return
    def toggle_play(self):
        if self.timer.isActive():self.timer.stop()
        elif self.session and self.session.get('events'):
            if self.cursor==len(self.session['events'])-1:self.goto(0)
            self.timer.start()
    def play_step(self):
        if not self.session or self.cursor>=len(self.session['events'])-1:self.timer.stop()
        else:self.goto(self.cursor+1)
    def hideEvent(self,event):self.timer.stop();super().hideEvent(event)
    def replay(self):
        if not self.session:raise ValueError('Выберите процедуру')
        pid=self.sessions.currentData();record=self.run_record
        def ready(result):
            self.owner.run_record=result;self.owner.refresh_measurements();self.owner.refresh_library();self.set_run(result,pid);self.show();self.raise_()
        self.owner.start_job('Повтор с подробной записью процедуры',lambda progress,cancel:research.replay_procedure(record,pid,progress,cancel),ready)
    def export_tables(self):
        if not self.session or not self.session.get('events'):raise ValueError('Подробного журнала нет')
        from .export_dialog import ExportDialog
        sources=[dict(key=key,kind='table',title=title,widget=widget,metadata=dict(run_id=self.run_record.get('id'),procedure_id=self.sessions.currentData(),cursor=self.cursor)) for key,title,widget in [('procedure_events','События процедуры',self.events_table),('procedure_timers','Таймеры на выбранном шаге',self.timers),('procedure_timings','Время сообщений на выбранном шаге',self.timings),('procedure_resources','Ресурсы на выбранном шаге',self.resources),('procedure_contexts','Контексты на выбранном шаге',self.contexts)]]
        dialog=ExportDialog(self,sources,initial='procedure_events')
        try:dialog.exec()
        finally:dialog.deleteLater()
    def export_json(self):
        if not self.session or not self.session.get('events'):raise ValueError('Подробного журнала нет')
        path,_=QFileDialog.getSaveFileName(self,'Журнал выбранной процедуры','procedure-journal.json','JSON (*.json)')
        if path:Path(path).write_text(json.dumps(dict(run_id=self.run_record.get('id'),engine=self.run_record.get('environment'),journal_version=self.run_record['procedure_journal']['version'],session=self.session),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    def export_diagram(self):
        if not self.session or not self.session.get('events'):raise ValueError('Подробного журнала нет')
        path,selected=QFileDialog.getSaveFileName(self,'Текущий фрагмент диаграммы','procedure-fragment.svg','SVG (*.svg);;PNG (*.png);;PDF (*.pdf)')
        if not path:return
        fmt='svg' if 'SVG' in selected else 'png' if 'PNG' in selected else 'pdf';target=Path(path)
        if not target.suffix:target=target.with_suffix('.'+fmt)
        self.diagram.export_fragment(target,fmt)
