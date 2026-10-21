import math
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QPainterPath, QLinearGradient, QFont, QPolygonF
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QHeaderView, QAbstractItemView, QTableWidgetItem, QDoubleSpinBox, QSpinBox, QComboBox, QFormLayout
from .style import icon, themed_color as QColor


def label(text='',name=None):
    w=QLabel(text)
    if name:w.setObjectName(name)
    return w


def button(text,fn=None,kind=None,primary=False):
    w=QPushButton(text)
    w.setProperty('iconKind',kind);w.setProperty('primaryIcon',primary)
    if kind:w.setIcon(icon(kind,'#163c2c' if primary else '#a2b4cb'))
    if primary:w.setObjectName('Primary')
    if fn:w.clicked.connect(fn)
    return w


def row(*widgets,stretch=False):
    layout=QHBoxLayout()
    layout.setContentsMargins(0,0,0,0)
    layout.setSpacing(9)
    for w in widgets:
        if isinstance(w,str):layout.addWidget(label(w))
        else:layout.addWidget(w)
    if stretch:layout.addStretch()
    return layout


def spin(value,lo,hi,decimals=0,suffix=''):
    w=QDoubleSpinBox() if decimals else QSpinBox()
    w.setRange(lo,hi)
    if decimals:w.setDecimals(decimals);w.setSingleStep(.1 if decimals<3 else .001)
    w.setValue(value)
    if suffix:w.setSuffix(' '+suffix)
    w.setKeyboardTracking(False)
    return w


def combo(items,current=None):
    w=QComboBox()
    for item in items:
        if isinstance(item,tuple):w.addItem(item[1],item[0])
        else:w.addItem(item,item)
    if current is not None:
        idx=w.findData(current)
        if idx>=0:w.setCurrentIndex(idx)
    return w


def panel(title,subtitle=''):
    frame=QFrame();frame.setObjectName('Panel')
    layout=QVBoxLayout(frame);layout.setContentsMargins(19,17,19,18);layout.setSpacing(13)
    head=QHBoxLayout();head.addWidget(label(title,'PanelTitle'));head.addStretch()
    layout.addLayout(head)
    if subtitle:
        desc=label(subtitle,'Muted');desc.setWordWrap(True);layout.addWidget(desc)
    return frame,layout,head


def table(headers):
    t=QTableWidget(0,len(headers));t.setHorizontalHeaderLabels(headers)
    t.setAlternatingRowColors(True);t.setShowGrid(False)
    t.verticalHeader().hide();t.verticalHeader().setDefaultSectionSize(41)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    return t


def populate(t,rows):
    t.setSortingEnabled(False);t.setRowCount(len(rows))
    for i,r in enumerate(rows):
        for j,v in enumerate(r):
            it=QTableWidgetItem(str(v));it.setToolTip(str(v));t.setItem(i,j,it)


class Metric(QFrame):
    def __init__(self,title,value='—',detail='',parent=None,compact=False):
        super().__init__(parent);self.setObjectName('Metric')
        l=QVBoxLayout(self);l.setContentsMargins(*(14,8,14,8) if compact else (18,13,18,13));l.setSpacing(3 if compact else 5)
        caption=label(title,'Muted');l.addWidget(caption);self.value=label(value,'MetricValue');l.addWidget(self.value)
        if compact:caption.setStyleSheet('font-size:10px');self.value.setStyleSheet('font-size:23px')
        self.detail=label(detail,'Muted');self.detail.setStyleSheet('font-size:10px');l.addWidget(self.detail)
        self.setMinimumHeight(76 if compact else 104)
    def update_value(self,value,detail=None):
        self.value.setText(str(value))
        if detail is not None:self.detail.setText(detail)


class Chart(QWidget):
    """Native QPainter chart with hover readout; no HTML or JS."""
    def __init__(self,parent=None):
        super().__init__(parent);self.series=[];self.incidents=[];self.hover=None;self.setMinimumHeight(160);self.setMouseTracking(True)
        self.setAccessibleName('График измерений')
    def set_series(self,series,incidents=None):
        self.series=series;self.incidents=incidents or [];self.hover=None;self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect=QRectF(48,16,max(1,self.width()-66),max(1,self.height()-49))
        values=[v for s in self.series for _,v in s.get('points',[])]
        if not values:
            p.setPen(QColor('#74879e'));p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,getattr(self,'empty_text','Запустите эксперимент, чтобы увидеть измерения'));return
        xmin=min(x for s in self.series for x,_ in s['points']);xmax=max(x for s in self.series for x,_ in s['points'])
        if xmax==xmin:xmin-=.5;xmax+=.5
        ymin=min(0,min(values));ymax=max(values)*1.12
        if getattr(self,'y_range',None):ymin,ymax=self.y_range
        if ymax<=ymin:ymax=ymin+1
        px=lambda x:rect.left()+(x-xmin)/(xmax-xmin)*rect.width()
        py=lambda y:rect.bottom()-(y-ymin)/(ymax-ymin)*rect.height()
        p.setFont(QFont('Segoe UI',9))
        for i in range(4):
            y=ymin+(ymax-ymin)*i/3
            p.setPen(QPen(QColor('#2a3544'),1,Qt.PenStyle.DashLine));p.drawLine(QPointF(rect.left(),py(y)),QPointF(rect.right(),py(y)))
            p.setPen(QColor('#71849e'));p.drawText(QRectF(0,py(y)-9,40,18),Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,f'{y:.1f}' if ymax<10 else f'{y:.0f}')
        for e in self.incidents:
            start=max(xmin,e['start']);end=min(xmax,e['start']+e['duration'])
            if end>start:p.fillRect(QRectF(px(start),rect.top(),px(end)-px(start),rect.height()),QColor(227,174,104,18))
        for idx,s in enumerate(self.series):
            path=QPainterPath();points=s['points']
            if not points:continue
            path.moveTo(px(points[0][0]),py(points[0][1]))
            for x,y in points[1:]:path.lineTo(px(x),py(y))
            color=QColor(s.get('color','#8fe0c2'))
            if idx==0 and s.get('fill',True):
                area=QPainterPath(path);area.lineTo(px(points[-1][0]),rect.bottom());area.lineTo(px(points[0][0]),rect.bottom());area.closeSubpath()
                g=QLinearGradient(0,rect.top(),0,rect.bottom());c=QColor(color);c.setAlpha(40);g.setColorAt(0,c);c.setAlpha(0);g.setColorAt(1,c);p.fillPath(area,g)
            p.setPen(QPen(color,2,Qt.PenStyle.DashLine if s.get('dash') else Qt.PenStyle.SolidLine));p.drawPath(path)
            p.setBrush(color)
            if s.get('markers',True):
                for x,y in points:p.drawEllipse(QPointF(px(x),py(y)),2.5,2.5)
        p.setPen(QColor('#71849e'))
        for i in range(6):
            x=xmin+(xmax-xmin)*i/5;decimals=min(6,max(0,math.ceil(-math.log10((xmax-xmin)/5))))
            p.drawText(QRectF(px(x)-34,rect.bottom()+9,68,18),Qt.AlignmentFlag.AlignCenter,f'{x:.{decimals}f}')
        lx=50
        for s in self.series:
            p.setPen(QColor(s.get('color','#8fe0c2')));p.drawText(lx,11,s.get('name',''));lx+=max(100,len(s.get('name',''))*7+25)
        if self.hover is not None:
            x=xmin+(max(rect.left(),min(rect.right(),self.hover))-rect.left())/rect.width()*(xmax-xmin)
            point=min(next(s['points'] for s in self.series if s['points']),key=lambda v:abs(v[0]-x))
            p.setPen(QPen(QColor('#667c94'),1,Qt.PenStyle.DashLine));p.drawLine(QPointF(px(point[0]),rect.top()),QPointF(px(point[0]),rect.bottom()))
            text=f'{point[0]:g}  /  {point[1]:.3f}'
            p.setPen(QColor('#d8e9f4'));p.drawText(QRectF(rect.right()-155,18,150,24),Qt.AlignmentFlag.AlignRight,text)
    def mouseMoveEvent(self,event):self.hover=event.position().x();self.update()
    def leaveEvent(self,event):self.hover=None;self.update()
