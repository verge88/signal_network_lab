"""Original 24-unit vector glyphs for signalling nodes; no bitmap assets."""
from functools import lru_cache
from PySide6.QtCore import Qt,QRectF,QSize
from PySide6.QtGui import QPainter,QPainterPath,QPen,QPixmap,QIcon,QColor
from PySide6.QtWidgets import QComboBox
from lab.schema import NODE_KINDS
from .style import themed_color


NODE_ROLES={
    'MSC':'Коммутация вызовов','STP':'Пересылка сигнализации','SG':'Сигнальный шлюз',
    'HLR':'Реестр абонентов','MME':'Управление мобильностью EPC','DRA':'Маршрутизация Diameter',
    'HSS':'Данные и аутентификация EPC','SCP':'Логика услуг',
    'gNB':'Базовая станция 5G','AMF':'Доступ и мобильность 5G','SMF':'Управление сессиями',
    'UPF':'Пересылка пользовательских данных','AUSF':'Аутентификация 5G',
    'UDM':'Управление данными абонентов','UDR':'Хранилище данных',
    'PCF':'Управление политиками','NRF':'Обнаружение сетевых функций',
    'NSSF':'Выбор сетевого среза','SCP5G':'Прокси сервисов 5G',
    'DN':'Внешняя сеть данных',
}

# Every type has its own silhouette; colour is never the sole identifier.
GLYPHS={
    'DN':dict(circles=[(3,3,18,18)],paths=[[(3,12),(21,12)],[(12,3),(8,8),(8,16),(12,21),(16,16),(16,8),(12,3)]]),
    'MSC':dict(paths=[[(6,3),(3,5),(4,10),(8,16),(14,20),(19,21),(21,18),(17,14),(14,16),(8,10),(10,7),(6,3)],[(14,3),(21,3),(21,10)],[(17,7),(21,3)]]),
    'STP':dict(paths=[[(12,7),(17,12),(12,17),(7,12),(12,7)],[(12,2),(12,7)],[(9,4),(12,2),(15,4)],[(17,12),(22,12)],[(20,9),(22,12),(20,15)],[(12,17),(12,22)],[(9,20),(12,22),(15,20)],[(2,12),(7,12)],[(4,9),(2,12),(4,15)]]),
    'SG':dict(boxes=[(3,3,5,18,1.5),(16,3,5,18,1.5)],paths=[[(1,12),(23,12)],[(10,8),(14,12),(10,16)]]),
    'HLR':dict(paths=[[(12,5),(8,3),(3,3),(3,19),(8,19),(12,21),(16,19),(21,19),(21,3),(16,3),(12,5),(12,21)],[(6,7),(9,8)],[(6,11),(9,12)],[(16,13),(18,13)],[(15,16),(19,16)]],circles=[(15,6,4,4)]),
    'MME':dict(boxes=[(2,5,5,13,1),(17,5,5,13,1)],paths=[[(9,8),(15,8),(13,6)],[(15,15),(9,15),(11,17)],[(4,15),(5,15)],[(19,15),(20,15)]]),
    'DRA':dict(circles=[(7,7,10,10)],paths=[[(12,2),(12,7)],[(2,12),(7,12)],[(17,12),(22,12)],[(12,17),(12,22)],[(19,9),(22,12),(19,15)],[(9,19),(12,22),(15,19)],[(10,10),(14,14)],[(10,14),(14,10)]]),
    'HSS':dict(circles=[(3,3,11,4),(14,10,6,6)],paths=[[(3,5),(3,18)],[(14,5),(14,8)],[(17,16),(17,22)],[(17,19),(20,19)],[(17,22),(19,22)]],arcs=[(3,9,11,4,180,180),(3,16,11,4,180,180)]),
    'SCP':dict(boxes=[(6,6,12,12,2)],paths=[[(3,8),(6,8)],[(3,16),(6,16)],[(18,8),(21,8)],[(18,16),(21,16)],[(8,3),(8,6)],[(16,3),(16,6)],[(8,18),(8,21)],[(16,18),(16,21)],[(9,13),(11,10),(13,14),(15,11)]]),
    'gNB':dict(paths=[[(12,9),(12,21)],[(8,21),(12,13),(16,21)],[(8,21),(16,21)]],circles=[(10.5,6,3,3)],arcs=[(5,1,14,14,145,70),(5,1,14,14,-35,70),(1,-3,22,22,145,70),(1,-3,22,22,-35,70)]),
    'AMF':dict(paths=[[(12,2),(21,6),(20,14),(17,19),(12,22),(7,19),(4,14),(3,6),(12,2)],[(7,12),(17,12)],[(13,8),(17,12),(13,16)]]),
    'SMF':dict(boxes=[(9,2,6,5,1),(2,17,6,5,1),(16,17,6,5,1)],paths=[[(12,7),(12,12),(5,12),(5,17)],[(12,12),(19,12),(19,17)]],circles=[(10,10,4,4)]),
    'UPF':dict(paths=[[(3,5),(12,5),(12,2),(22,12),(12,22),(12,19),(3,19)],[(2,9),(8,9)],[(2,15),(8,15)],[(7,12),(16,12)],[(13,9),(16,12),(13,15)]]),
    'AUSF':dict(paths=[[(12,2),(21,6),(20,14),(17,19),(12,22),(7,19),(4,14),(3,6),(12,2)],[(12,12),(12,17)],[(12,16),(15,16)]],circles=[(9,6,6,6)]),
    'UDM':dict(circles=[(9,2,6,6),(2,18,4,4),(10,18,4,4),(18,18,4,4)],arcs=[(6,8,12,10,0,180)],paths=[[(6,13),(18,13)],[(12,13),(12,16)],[(4,18),(4,16),(20,16),(20,18)],[(12,16),(12,18)]]),
    'UDR':dict(circles=[(4,3,16,5)],paths=[[(4,5.5),(4,18)],[(20,5.5),(20,18)]],arcs=[(4,8,16,5,180,180),(4,13,16,5,180,180),(4,16,16,5,180,180)]),
    'PCF':dict(paths=[[(5,3),(5,6)],[(5,12),(5,21)],[(12,3),(12,12)],[(12,18),(12,21)],[(19,3),(19,7)],[(19,13),(19,21)]],circles=[(2,6,6,6),(9,12,6,6),(16,7,6,6)]),
    'NRF':dict(boxes=[(3,3,5,5,1),(12,3,5,5,1),(3,12,5,5,1)],circles=[(11,11,8,8)],paths=[[(18,18),(22,22)]]),
    'NSSF':dict(paths=[[(3,7),(12,2),(21,7),(12,12),(3,7)],[(3,12),(12,17),(21,12)],[(3,17),(12,22),(21,17)],[(16,18),(18,20),(22,15)]]),
    'SCP5G':dict(paths=[[(12,3),(20,7.5),(20,16.5),(12,21),(4,16.5),(4,7.5),(12,3)],[(12,12),(12,3)],[(12,12),(4,16.5)],[(12,12),(20,16.5)],[(9,10),(12,12),(15,10)]],circles=[(9,9,6,6)]),
}


def draw_node_icon(painter,kind,rect,color):
    glyph=GLYPHS.get(kind,dict(boxes=[(4,4,16,16,3)],circles=[(9,9,6,6)]))
    painter.save();painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.topLeft());painter.scale(rect.width()/24,rect.height()/24)
    painter.setPen(QPen(QColor(color),1.65,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin));painter.setBrush(Qt.BrushStyle.NoBrush)
    for points in glyph.get('paths',[]):
        path=QPainterPath();path.moveTo(*points[0])
        for point in points[1:]:path.lineTo(*point)
        painter.drawPath(path)
    for ellipse in glyph.get('circles',[]):painter.drawEllipse(QRectF(*ellipse))
    for x,y,w,h,r in glyph.get('boxes',[]):painter.drawRoundedRect(QRectF(x,y,w,h),r,r)
    for x,y,w,h,start,span in glyph.get('arcs',[]):painter.drawArc(QRectF(x,y,w,h),start*16,span*16)
    painter.restore()


@lru_cache(maxsize=160)
def _cached_icon(kind,color,size):
    pixmap=QPixmap(size*2,size*2);pixmap.fill(Qt.GlobalColor.transparent);painter=QPainter(pixmap)
    try:draw_node_icon(painter,kind,QRectF(0,0,size*2,size*2),QColor.fromRgba(color))
    finally:painter.end()
    pixmap.setDevicePixelRatio(2);return QIcon(pixmap)


def node_icon(kind,color='#a2b4cb',size=24):return _cached_icon(kind,themed_color(color).rgba(),size)


def refresh_kind_icons(widget):
    for index in range(widget.count()):widget.setItemIcon(index,node_icon(widget.itemData(index)))


def node_kind_combo(current=None):
    widget=QComboBox();widget.setProperty('nodeKindIcons',True);widget.setIconSize(QSize(24,24))
    for kind in NODE_KINDS:
        widget.addItem(node_icon(kind),kind,kind);widget.setItemData(widget.count()-1,NODE_ROLES[kind],Qt.ItemDataRole.ToolTipRole)
    if current is not None:widget.setCurrentIndex(widget.findData(current))
    return widget
