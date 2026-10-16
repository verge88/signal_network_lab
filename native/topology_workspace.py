"""An in-window topology canvas with movable native tool and inspector panels."""
from PySide6.QtCore import Qt, QPoint, QSize, QEvent, QTimer
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton
from lab.schema import NODE_KINDS
from .node_icons import node_icon, NODE_ROLES
from .style import icon


class DragHeader(QWidget):
    def __init__(self, panel, title):
        super().__init__(panel);self.panel=panel;self.offset=None
        self.setCursor(Qt.CursorShape.SizeAllCursor);self.setToolTip('Перетащите панель за заголовок')
        self.bar=QHBoxLayout(self);self.bar.setContentsMargins(23,3,3,3);self.bar.setSpacing(6)
        caption=QLabel(title);caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.bar.addWidget(caption,1)
    def paintEvent(self,event):
        super().paintEvent(event);painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.palette().windowText().color(),2,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
        for x in (8,13):
            for y in (-5,0,5):painter.drawPoint(x,self.height()//2+y)
        painter.end()
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.offset=event.globalPosition().toPoint()-self.panel.mapToGlobal(QPoint());self.panel.raise_();event.accept()
        else:super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self.offset is not None:
            self.panel.move(self.panel.parentWidget().mapFromGlobal(event.globalPosition().toPoint()-self.offset));self.panel.constrain();event.accept()
        else:super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):self.offset=None;event.accept()


class FloatingPanel(QFrame):
    def __init__(self,parent,title):
        super().__init__(parent);self.setObjectName('FloatingPanel')
        self.column=QVBoxLayout(self);self.column.setContentsMargins(5,5,5,5);self.column.setSpacing(5)
        self.header=DragHeader(self,title);self.column.addWidget(self.header)
    def constrain(self):
        area=self.parentWidget().rect();margin=8
        self.move(max(margin,min(self.x(),area.width()-self.width()-margin)),max(margin,min(self.y(),area.height()-self.height()-margin)))
    def resizeEvent(self,event):super().resizeEvent(event);self.constrain()


class PanelGrip(QWidget):
    def __init__(self,panel):
        super().__init__(panel);self.panel=panel;self.origin=None;self.setFixedSize(16,16);self.setCursor(Qt.CursorShape.SizeFDiagCursor);self.setToolTip('Изменить размер инспектора')
    def paintEvent(self,event):
        painter=QPainter(self);painter.setPen(QPen(self.palette().windowText().color(),1))
        for offset in (3,7,11):painter.drawLine(offset,13,13,offset)
        painter.end()
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.origin=(event.globalPosition().toPoint(),self.panel.size());event.accept()
    def mouseMoveEvent(self,event):
        if self.origin is not None:
            delta=event.globalPosition().toPoint()-self.origin[0];size=self.origin[1]+QSize(delta.x(),delta.y());area=self.panel.parentWidget()
            self.panel.resize(min(size.width(),area.width()-self.panel.x()-8),min(size.height(),area.height()-self.panel.y()-8));event.accept()
    def mouseReleaseEvent(self,event):self.origin=None;event.accept()


class ExpandedTopology(QWidget):
    def __init__(self,window):
        super().__init__(window.centralWidget());self.window=window;self.setObjectName('ExpandedTopology')
        self.canvas=QVBoxLayout(self);self.canvas.setContentsMargins(0,0,0,0)
        self.tools=FloatingPanel(self,'Узлы и инструменты');self.tools.setFixedWidth(392)
        self.exit_button=self.control('Свернуть карту (F11)',lambda:window.set_topology_expanded(False),'restore')
        self.tools.header.bar.addWidget(self.exit_button)
        grid=QGridLayout();grid.setContentsMargins(3,0,3,0);grid.setSpacing(4);self.node_buttons={}
        for index,kind in enumerate(NODE_KINDS):
            button=QPushButton(kind);button.setObjectName('QuickNode');button.setIcon(node_icon(kind));button.setIconSize(QSize(20,20));button.setFixedHeight(31)
            button.setProperty('quickNodeKind',kind);button.setToolTip(f'Добавить {kind} · {NODE_ROLES[kind]}');button.setAccessibleName('Добавить '+kind)
            button.clicked.connect(lambda checked=False,k=kind:window.call(window.quick_add_node,k));grid.addWidget(button,index//5,index%5);self.node_buttons[kind]=button
        self.tools.column.addLayout(grid)
        actions=QHBoxLayout();actions.setContentsMargins(3,2,3,1);actions.setSpacing(4)
        self.link_button=self.control('Добавить канал (Ctrl+L)',window.add_link,'link')
        self.connect_button=self.control('Соединить узлы мышью (Ctrl+Shift+L)',None,'connect');self.connect_button.setCheckable(True)
        self.connect_button.toggled.connect(window.toggle_link_drawing);window.graph.connection_mode_changed.connect(self.connect_button.setChecked)
        self.export_button=self.control('Экспорт схемы (Ctrl+Shift+T)',lambda:window.export_visuals('topology'),'download')
        self.arrange_button=self.control('Расположить узлы',window.auto_layout,'topology')
        self.fit_button=self.control('Показать всю сеть (Ctrl+0)',window.graph.fit,'fit')
        self.inspector_button=self.control('Показать / скрыть инспектор',self.toggle_inspector,'settings');self.inspector_button.setCheckable(True)
        for button in (self.link_button,self.connect_button,self.export_button,self.arrange_button,self.fit_button,self.inspector_button):actions.addWidget(button,1)
        self.tools.column.addLayout(actions);self.tools.adjustSize()
        self.inspector_panel=FloatingPanel(self,'Инспектор объекта');self.inspector_panel.setMinimumSize(295,250);self.inspector_panel.setMaximumWidth(450)
        self.close_inspector=self.control('Скрыть инспектор',self.toggle_inspector,'close');self.inspector_panel.header.bar.addWidget(self.close_inspector)
        bottom=QHBoxLayout();bottom.setContentsMargins(0,0,0,0);bottom.addStretch();self.inspector_grip=PanelGrip(self.inspector_panel);bottom.addWidget(self.inspector_grip)
        self.inspector_panel.column.addLayout(bottom);self.inspector_panel.hide();self.hide()
        window.centralWidget().installEventFilter(self)
    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.Resize and self.window.topology_expanded:self.setGeometry(watched.rect())
        return super().eventFilter(watched,event)
    def control(self,title,fn,kind):
        button=QPushButton();button.setObjectName('FloatingTool');button.setProperty('iconKind',kind);button.setIcon(icon(kind,'#a2b4cb',20));button.setIconSize(QSize(20,20));button.setFixedSize(34,30)
        button.setToolTip(title);button.setAccessibleName(title)
        if fn is not None:button.clicked.connect(lambda checked=False:self.window.call(fn))
        return button
    def refresh_icons(self):
        for kind,button in self.node_buttons.items():button.setIcon(node_icon(kind))
    def toggle_inspector(self):
        visible=not self.inspector_panel.isVisible();self.inspector_panel.setVisible(visible);self.inspector_button.setChecked(visible)
        if visible:self.inspector_panel.constrain();self.inspector_panel.raise_()
    def open_canvas(self):
        w=self.window;self.setGeometry(w.centralWidget().rect());self.canvas.addWidget(w.graph)
        w.inspector.setMaximumWidth(16777215);self.inspector_panel.column.insertWidget(1,w.inspector,1)
        w.fill_inspector()
        w.graph.show();w.inspector.show();self.show();self.raise_()
        self.tools.show();self.tools.adjustSize();self.tools.move(w.settings.value('expandedTopology/toolsPosition',QPoint(16,16)))
        geometry=w.settings.value('expandedTopology/inspectorGeometry')
        if geometry is not None:self.inspector_panel.setGeometry(geometry)
        else:self.inspector_panel.setGeometry(self.width()-334,16,318,min(660,self.height()-32))
        self.inspector_panel.resize(self.inspector_panel.width(),min(self.inspector_panel.height(),self.height()-16))
        self.inspector_panel.show();self.inspector_button.setChecked(True);self.tools.constrain();self.inspector_panel.constrain();self.inspector_panel.raise_();self.tools.raise_()
        self.connect_button.setChecked(w.graph.connection_mode);w.graph.setFocus();QTimer.singleShot(0,w.graph.fit)
    def close_canvas(self):
        w=self.window;w.settings.setValue('expandedTopology/toolsPosition',self.tools.pos());w.settings.setValue('expandedTopology/inspectorGeometry',self.inspector_panel.geometry())
        self.canvas.removeWidget(w.graph);w.canvas_layout.insertWidget(2,w.graph,1);self.inspector_panel.column.removeWidget(w.inspector)
        w.inspector.setMaximumWidth(355);w.topology_split.addWidget(w.inspector);w.topology_split.setSizes(w.normal_topology_sizes)
        w.fill_inspector()
        w.graph.show();w.inspector.show();self.hide()
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'inspector_panel'):
            self.inspector_panel.resize(self.inspector_panel.width(),min(self.inspector_panel.height(),self.height()-16))
            self.tools.constrain();self.inspector_panel.constrain()

