import math
from shiboken6 import isValid
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, Slot, QTimer, QEvent
from PySide6.QtGui import QPainter, QPen, QPainterPath, QBrush, QFont
from PySide6.QtWidgets import QApplication, QGraphicsObject, QGraphicsPathItem, QGraphicsItem, QGraphicsScene, QGraphicsView
from .style import themed_color as QColor, current_theme
from .node_icons import draw_node_icon,NODE_ROLES

PALETTE={'SS7':'#d5aa75','M3UA':'#82c9ae','Diameter':'#7fa8ea','NGAP':'#a5baff','SBI':'#8fe0c2','PFCP':'#d5aa75','GTPU':'#82adfa','IP':'#8fe0c2'}


class NodeItem(QGraphicsObject):
    moved=Signal(str,float,float)
    def __init__(self,node):
        super().__init__();self.node=node;self.edges=[];self.origin=None;self.stats=None;self.connecting=False;self.connection_target=False
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable|QGraphicsItem.GraphicsItemFlag.ItemIsSelectable|QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setPos(node['x'],node['y']);self.setZValue(2);self.setToolTip(f'{node["name"]}\n{node["kind"]} · {NODE_ROLES.get(node["kind"],"Сетевой узел")}\n{node["capacity"]:g} msg/s\nПеретащите карточку для перемещения.\nНажмите на узел, затем перетащите появившийся кружок к другому узлу для создания канала.')
    def boundingRect(self):return QRectF(-84,-48,168,112)
    def connector_at(self,point):
        return self.isSelected() and any((point-QPointF(x,4)).manhattanLength()<=11 for x in (-78,78))
    def paint(self,p,option,widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n=self.node;blue=n['kind'] in ('MME','DRA','HSS');color=QColor('#82adfa' if blue else '#d9b27b' if n['kind']=='STP' else '#8fe0c2')
        if not n['enabled']:p.setOpacity(.38)
        rect=QRectF(-78,-41,156,90)
        if self.isSelected() or self.connecting or self.connection_target:
            p.setPen(QPen(QColor('#8fe0c2'),1.5));p.setBrush(QColor(89,175,145,15));p.drawRoundedRect(rect.adjusted(-5,-5,5,5),15,15)
        p.setBrush(QColor('#202c3b'));p.setPen(QPen(color if self.isSelected() else QColor('#3a4c60'),1.4));p.drawRoundedRect(rect,10,10)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#2d3e4c'));p.drawRoundedRect(QRectF(-66,-30,33,33),6,6)
        draw_node_icon(p,n['kind'],QRectF(-63,-27,27,27),color)
        p.setPen(color);p.setFont(QFont('Segoe UI',9,QFont.Weight.DemiBold));p.drawText(QRectF(-24,-29,88,25),Qt.AlignmentFlag.AlignVCenter,n['kind'])
        p.setBrush(QColor('#80cdaa' if n['enabled'] else '#627084'));p.setPen(Qt.PenStyle.NoPen);p.drawEllipse(QPointF(65,-27),3,3)
        p.setPen(QColor('#e3edf8'));p.setFont(QFont('Segoe UI',10,QFont.Weight.DemiBold));name=n['name'];fm=p.fontMetrics();name=fm.elidedText(name,Qt.TextElideMode.ElideRight,134)
        p.drawText(QRectF(-65,5,134,20),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,name)
        p.setPen(QColor('#849bb4'));p.setFont(QFont('Segoe UI',8));p.drawText(QRectF(-65,25,135,18),f'{n["capacity"]:g} msg/s  ·  q {n["queue_limit"]}')
        if self.isSelected():
            p.setOpacity(1);p.setPen(QPen(QColor('#8fe0c2' if self.connecting or self.connection_target else '#849bb4'),1.5));p.setBrush(QColor('#141c27'))
            for x in (-78,78):p.drawEllipse(QPointF(x,4),4.5,4.5)
        if self.stats and self.stats.get('dropped'):
            p.setPen(QColor('#e7ad81'));p.setFont(QFont('Segoe UI',8));p.drawText(QRectF(-80,50,160,14),Qt.AlignmentFlag.AlignCenter,f'Отброшено: {self.stats["dropped"]}')
    def itemChange(self,change,value):
        if change==QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            return QPointF(max(40,min(19900,value.x())),max(60,min(19900,value.y())))
        if change==QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for e in self.edges:e.adjust()
        return super().itemChange(change,value)
    def mousePressEvent(self,event):self.origin=self.pos();super().mousePressEvent(event)
    def mouseReleaseEvent(self,event):
        super().mouseReleaseEvent(event)
        if self.origin is not None and self.origin!=self.pos():self.moved.emit(self.node['id'],self.pos().x(),self.pos().y())
        self.origin=None


class EdgeItem(QGraphicsPathItem):
    def __init__(self,link,a,b):
        super().__init__();self.link=link;self.a=a;self.b=b;self.count=None;self.show_label=True
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable);self.setZValue(0)
        a.edges.append(self);b.edges.append(self);self.adjust();self.setToolTip(f'{link["protocol"]} · {link["latency_ms"]:g} ms · {link["bandwidth_mbps"]:g} Mbps')
    def adjust(self):
        a=self.a.pos();b=self.b.pos();p=QPainterPath(a);p.lineTo(b);self.setPath(p)
    def shape(self):
        from PySide6.QtGui import QPainterPathStroker
        s=QPainterPathStroker();s.setWidth(18);return s.createStroke(self.path())
    def boundingRect(self):return super().boundingRect().adjusted(-30,-15,30,15)
    def paint(self,p,option,widget=None):
        color=QColor(PALETTE[self.link['protocol']]);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.link['enabled']:p.setOpacity(.25)
        line_style=Qt.PenStyle.SolidLine if self.link['enabled'] else Qt.PenStyle.DashLine
        if current_theme() in ('monochrome','light_monochrome') and self.link['enabled']:
            line_style={'SS7':Qt.PenStyle.DashLine,'M3UA':Qt.PenStyle.SolidLine,'Diameter':Qt.PenStyle.DotLine,'NGAP':Qt.PenStyle.DashDotLine,'SBI':Qt.PenStyle.SolidLine,'PFCP':Qt.PenStyle.DashDotDotLine,'GTPU':Qt.PenStyle.DashLine,'IP':Qt.PenStyle.DotLine}[self.link['protocol']]
        p.setPen(QPen(QColor('#c6f4e3') if self.isSelected() else color,2.5 if self.isSelected() else 1.5,line_style));p.drawPath(self.path())
        if not self.show_label:return
        mid=(self.a.pos()+self.b.pos())/2
        p.setBrush(QColor('#18222f'));p.setPen(QPen(QColor('#344659'),.8));p.drawRoundedRect(QRectF(mid.x()-28,mid.y()-11,56,22),5,5)
        p.setPen(QColor('#9eb2cc'));p.setFont(QFont('Segoe UI',8));p.drawText(QRectF(mid.x()-27,mid.y()-11,54,22),Qt.AlignmentFlag.AlignCenter,f'{self.link["latency_ms"]:g} ms')


class NetworkView(QGraphicsView):
    selected=Signal(str,str)
    node_moved=Signal(str,float,float)
    delete_requested=Signal()
    connection_requested=Signal(str,str)
    connection_mode_changed=Signal(bool)
    connection_hint=Signal(str)
    def __init__(self,parent=None):
        self.scene_model=QGraphicsScene();super().__init__(self.scene_model,parent)
        self.scene_model.setSceneRect(-30,-20,1260,740);self.node_items={};self.edge_items={};self.loading=False
        self.connection_mode=False;self.connection_source=None;self.connection_hover=None;self.connection_preview=None;self.connection_press=None;self.connection_kind=None;self.panning=False
        self.setRenderHints(QPainter.RenderHint.Antialiasing|QPainter.RenderHint.TextAntialiasing)
        self.setBackgroundBrush(QColor('#141c27'));self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setAccessibleName('Редактор топологии сигнальной сети')
        self.setMouseTracking(True);self.viewport().setMouseTracking(True)
        self.scene_model.selectionChanged.connect(self.selection)
        self.first_fit=True;self.fit_mode=True;self.pan_position=None;self.pan_button=None
        self.show_grid=True;self.show_link_labels=True;self.pan_speed=1.;self.zoom_factor=1.15
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    def set_project(self,p,selected=None,stats=None):
        center=self.mapToScene(self.viewport().rect().center())
        self.set_connection_mode(False)
        self.loading=True;self.scene_model.clear();self.node_items={};self.edge_items={}
        for n in p['nodes']:
            item=NodeItem(n);item.moved.connect(self.node_moved);item.stats=(stats or {}).get('nodes',{}).get(n['id']);self.scene_model.addItem(item);self.node_items[n['id']]=item
        for l in p['links']:
            item=EdgeItem(l,self.node_items[l['source']],self.node_items[l['target']]);item.show_label=self.show_link_labels;self.scene_model.addItem(item);self.edge_items[l['id']]=item
        bounds=self.scene_model.itemsBoundingRect().adjusted(-80,-80,80,80)
        self.scene_model.setSceneRect(bounds)
        self.setSceneRect(self.sceneRect().united(bounds))
        if not self.fit_mode:self.centerOn(center)
        if selected:
            kind,rid=selected;item=(self.node_items if kind=='node' else self.edge_items).get(rid)
            if item:item.setSelected(True)
        self.loading=False
        if self.first_fit:self.fit();self.first_fit=False
    def refresh_theme(self):
        self.setBackgroundBrush(QColor('#141c27'));self.scene_model.update();self.viewport().update()
    def configure_view(self,grid=True,link_labels=True,pan_speed=1.,zoom_factor=1.15):
        self.show_grid=bool(grid);self.show_link_labels=bool(link_labels);self.pan_speed=float(pan_speed);self.zoom_factor=float(zoom_factor)
        for item in self.edge_items.values():item.show_label=self.show_link_labels;item.update()
        self.viewport().update()
    @Slot()
    def selection(self):
        if self.loading or not isValid(self) or not isValid(self.scene_model):return
        items=self.scene_model.selectedItems()
        if len(items)!=1:self.selected.emit('','');return
        item=items[0]
        if isinstance(item,NodeItem):self.selected.emit('node',item.node['id'])
        else:self.selected.emit('link',item.link['id'])
    @Slot()
    def fit(self):
        if not isValid(self) or not isValid(self.scene_model):return
        self.fit_mode=True
        self.setSceneRect(self.scene_model.sceneRect())
        self.fitInView(self.scene_model.itemsBoundingRect().adjusted(-45,-45,45,45),Qt.AspectRatioMode.KeepAspectRatio)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.fit_mode:QTimer.singleShot(0,self.fit)
    def center_view(self,center):
        visible=self.mapToScene(self.viewport().rect()).boundingRect();visible.moveCenter(center)
        margin=max(visible.width(),visible.height())
        self.setSceneRect(self.sceneRect().united(visible.adjusted(-margin,-margin,margin,margin)))
        self.centerOn(center)
    def pan_view(self,delta):
        if delta.isNull():return
        self.fit_mode=False;inverse=self.transform().inverted()[0]
        offset=inverse.map(QPointF(delta))-inverse.map(QPointF())
        self.center_view(self.mapToScene(self.viewport().rect().center())-offset)
    def wheelEvent(self,event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps=event.angleDelta().y()/120 if event.angleDelta().y() else event.pixelDelta().y()/120
            if steps:
                self.fit_mode=False;point=event.position().toPoint();before=self.mapToScene(point);scale=self.transform().m11()
                zoom=max(.015,min(3.5,scale*self.zoom_factor**max(-50,min(50,steps))))
                anchor=self.transformationAnchor();self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
                self.scale(zoom/scale,zoom/scale)
                self.center_view(self.mapToScene(self.viewport().rect().center())+before-self.mapToScene(point))
                self.setTransformationAnchor(anchor)
        else:
            delta=QPointF(event.pixelDelta())
            if delta.isNull():delta=QPointF(event.angleDelta())*(60/120)
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and not delta.x():delta=QPointF(delta.y(),0)
            self.pan_view(delta*self.pan_speed)
        if self.connection_source:self.update_connection(event.position().toPoint())
        event.accept()
    def node_at(self,point):
        return next((item for item in self.items(point) if isinstance(item,NodeItem)),None)
    def cancel_connection(self):
        for item in (self.node_items.get(self.connection_source),self.connection_hover):
            if item is not None and isValid(item):item.connecting=False;item.connection_target=False;item.update()
        if self.connection_preview is not None and isValid(self.connection_preview):
            self.scene_model.removeItem(self.connection_preview)
        self.connection_source=None;self.connection_hover=None;self.connection_preview=None;self.connection_press=None;self.connection_kind=None
    def set_connection_mode(self,enabled):
        pending=bool(self.connection_source);self.cancel_connection();enabled=bool(enabled);changed=enabled!=self.connection_mode;self.connection_mode=enabled
        self.setDragMode(QGraphicsView.DragMode.NoDrag if enabled else QGraphicsView.DragMode.RubberBandDrag)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        if enabled:self.setFocus();self.connection_hint.emit('Канал: выберите первый узел, затем второй, или перетащите между ними. Esc — отмена.')
        elif changed or pending:self.connection_hint.emit('Создание канала мышью отменено.')
        if changed:self.connection_mode_changed.emit(enabled)
    def begin_connection(self,node,point,kind):
        self.setFocus();self.connection_source=node.node['id'];self.connection_press=point;self.connection_kind=kind;node.connecting=True;node.update()
        self.scene_model.clearSelection();node.setSelected(True)
        self.connection_preview=QGraphicsPathItem();pen=QPen(QColor('#8fe0c2'),2,Qt.PenStyle.DashLine);pen.setCosmetic(True);self.connection_preview.setPen(pen)
        self.connection_preview.setAcceptedMouseButtons(Qt.MouseButton.NoButton);self.connection_preview.setZValue(1);self.scene_model.addItem(self.connection_preview)
        self.update_connection(point);self.connection_hint.emit('Укажите второй узел. Пустое место или Esc — отмена.')
    def update_connection(self,point):
        source=self.node_items[self.connection_source];target=self.node_at(point)
        if target is source:target=None
        if self.connection_hover is not target:
            if self.connection_hover is not None:self.connection_hover.connection_target=False;self.connection_hover.update()
            self.connection_hover=target
            if target is not None:target.connection_target=True;target.update()
        end=target.pos() if target is not None else self.mapToScene(point)
        start=source.pos()+QPointF(78 if end.x()>=source.pos().x() else -78,4)
        if target is not None:end=target.pos()+QPointF(-78 if start.x()<=target.pos().x() else 78,4)
        offset=QPointF(max(45,abs(end.x()-start.x())*.4)*(1 if end.x()>=start.x() else -1),0)
        path=QPainterPath(start);path.cubicTo(start+offset,end-offset,end);self.connection_preview.setPath(path)
    def finish_connection(self,target):
        source=self.connection_source;destination=target.node['id'] if target is not None else None
        self.set_connection_mode(False)
        if source and destination and source!=destination:
            self.connection_hint.emit('Задайте параметры нового канала.');self.connection_requested.emit(source,destination)
    def mousePressEvent(self,event):
        point=event.position().toPoint()
        if event.button()==Qt.MouseButton.MiddleButton or (event.button()==Qt.MouseButton.LeftButton and self.panning):
            self.setFocus();self.pan_position=event.position();self.pan_button=event.button();self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor);event.accept();return
        if event.button()==Qt.MouseButton.RightButton and (self.connection_mode or self.connection_source):
            self.set_connection_mode(False);event.accept();return
        if event.button()==Qt.MouseButton.LeftButton and not self.panning:
            node=self.node_at(point)
            if self.connection_mode:
                if node is None:self.set_connection_mode(False)
                elif self.connection_source:
                    if node.node['id']!=self.connection_source:self.finish_connection(node)
                else:self.begin_connection(node,point,'mode')
                event.accept();return
            if node is not None and node.connector_at(node.mapFromScene(self.mapToScene(point))):
                self.begin_connection(node,point,'port');event.accept();return
        super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self.pan_position is not None:
            self.pan_view(event.position()-self.pan_position);self.pan_position=event.position();event.accept();return
        if self.connection_source:
            self.update_connection(event.position().toPoint());event.accept();return
        if not self.panning:
            point=event.position().toPoint();node=self.node_at(point)
            connector=node is not None and node.connector_at(node.mapFromScene(self.mapToScene(point)))
            self.viewport().setCursor(Qt.CursorShape.CrossCursor if self.connection_mode or connector else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):
        if event.button()==self.pan_button:
            self.pan_position=None;self.pan_button=None;self.viewport().setCursor(Qt.CursorShape.OpenHandCursor if self.panning else Qt.CursorShape.CrossCursor if self.connection_mode else Qt.CursorShape.ArrowCursor)
            if self.connection_source:self.update_connection(event.position().toPoint())
            event.accept();return
        if event.button()==Qt.MouseButton.LeftButton and self.connection_source:
            point=event.position().toPoint()
            if self.connection_kind=='port' or (point-self.connection_press).manhattanLength()>=QApplication.startDragDistance():self.finish_connection(self.node_at(point))
            event.accept();return
        super().mouseReleaseEvent(event)
    def event(self,event):
        if event.type()==QEvent.Type.ShortcutOverride and event.key()==Qt.Key.Key_Escape and (getattr(self,'connection_source',None) or getattr(self,'connection_mode',False)):
            event.accept();return True
        return super().event(event)
    def focusOutEvent(self,event):
        self.set_connection_mode(False);self.panning=False;self.pan_position=None;self.pan_button=None;super().focusOutEvent(event)
    def hideEvent(self,event):
        self.set_connection_mode(False);self.panning=False;self.pan_position=None;self.pan_button=None;super().hideEvent(event)
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape and (self.connection_mode or self.connection_source):self.set_connection_mode(False);event.accept()
        elif event.key()==Qt.Key.Key_Delete:self.set_connection_mode(False);self.delete_requested.emit();event.accept()
        elif event.key()==Qt.Key.Key_Space:self.set_connection_mode(False);self.panning=True;self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag);event.accept()
        elif event.key()==Qt.Key.Key_F:self.fit();event.accept()
        else:super().keyPressEvent(event)
    def keyReleaseEvent(self,event):
        if event.key()==Qt.Key.Key_Space:self.panning=False;self.setDragMode(QGraphicsView.DragMode.RubberBandDrag);event.accept()
        else:super().keyReleaseEvent(event)
    def drawBackground(self,p,rect):
        p.fillRect(rect,QColor('#141c27'))
        if not self.show_grid:return
        p.setPen(QPen(QColor('#243245'),1))
        scale=max(.001,abs(p.worldTransform().m11()))
        step=25*max(1,math.ceil(8/(25*scale)));left=int(rect.left()/step)*step;top=int(rect.top()/step)*step
        for x in range(left,int(rect.right())+step,step):
            for y in range(top,int(rect.bottom())+step,step):p.drawPoint(QPointF(x,y))
