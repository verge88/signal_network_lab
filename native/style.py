import re
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPalette
from PySide6.QtCore import Qt, QRectF

COLORS=dict(bg='#11151c',panel='#191f29',raised='#212a36',line='#2c3746',text='#e5ebf5',muted='#8c9bb0',accent='#8fe0c2',blue='#82adfa',orange='#e7b375',red='#ed8c93')

THEMES={'monochrome':'Чёрно-белая · Pure Black','light_monochrome':'Светлая · Монохром','dark':'Тёмная · Signal'}
_current_theme='monochrome'

# Semantic overrides keep surfaces truly black and primary actions white.
# Remaining colors become neutral gray, including transparent painter colors.
MONO_COLORS={
    '#11151c':'#000000','#151a23':'#000000','#191f29':'#000000',
    '#141c27':'#000000','#131a24':'#000000','#171e28':'#000000',
    '#212a36':'#101010','#232c39':'#090909','#202c3b':'#080808',
    '#18222f':'#000000','#202a38':'#080808','#1d2734':'#080808',
    '#1b2430':'#080808','#202936':'#0c0c0c','#1d2530':'#060606',
    '#213a35':'#111111','#263b38':'#151515','#2d3e4c':'#181818',
    '#2b3747':'#171717','#344354':'#222222','#283444':'#171717',
    '#2c493f':'#222222','#294c43':'#262626','#34564d':'#262626',
    '#2e6e60':'#ffffff','#356354':'#ffffff',
    '#8fe0c2':'#ffffff','#82adfa':'#d5d5d5','#d9b27b':'#bbbbbb',
    '#a4eed3':'#e5e5e5','#a6efd1':'#ffffff','#9ce8c9':'#ffffff',
    '#a6edd4':'#ffffff','#80c6b0':'#ffffff','#79bfa7':'#ffffff',
    '#80cdaa':'#ffffff','#82cdb3':'#ffffff','#c6f4e3':'#ffffff',
    '#102b23':'#000000','#163c2c':'#000000',
    '#e5ebf5':'#f0f0f0','#f0f3fa':'#ffffff','#e7eef9':'#ffffff',
    '#e3edf8':'#ffffff','#d9e3f3':'#ededed','#cfdaeb':'#e5e5e5',
    '#d6e4f7':'#eeeeee','#d4f7e9':'#ffffff','#e1eaf7':'#eeeeee',
    '#d8e9f4':'#eeeeee','#ed9c9f':'#eeeeee','#e7ad81':'#eeeeee',
    '#8c9bb0':'#a0a0a0','#6f8198':'#8b8b8b','#62778d':'#888888',
    '#668f85':'#a0a0a0','#7389a3':'#929292','#536c87':'#777777',
    '#566376':'#626262','#849bb4':'#aaaaaa','#9eb2cc':'#bdbdbd',
    '#2a3443':'#292929','#28313e':'#252525','#344153':'#383838',
    '#344255':'#333333','#2c3849':'#292929','#293546':'#242424',
    '#243245':'#202020','#2a3544':'#252525','#3a4c60':'#454545',
    '#344659':'#333333','#39495b':'#424242','#456052':'#535353',
    '#d5aa75':'#b0b0b0','#82c9ae':'#ffffff','#7fa8ea':'#898989',
}

# Mirror semantic dark-monochrome roles: white surfaces, dark ink, black actions.
LIGHT_MONO_COLORS={key:QColor(255-QColor(value).red(),255-QColor(value).green(),255-QColor(value).blue()).name() for key,value in MONO_COLORS.items()}


def set_theme(name):
    global _current_theme
    if name not in THEMES:raise ValueError('Неизвестная тема: '+str(name))
    _current_theme=name


def current_theme():return _current_theme


def saved_theme(settings):
    value=settings.value('theme','monochrome')
    return value if value in THEMES else 'monochrome'


def themed_color(*args):
    value=QColor(*args)
    if _current_theme in ('monochrome','light_monochrome') and value.isValid():
        # Painter colors can pass through this function twice (e.g. node icons
        # and chart gradients). Already-neutral colors must remain unchanged.
        if _current_theme=='light_monochrome' and value.red()==value.green()==value.blue():return value
        mapping=LIGHT_MONO_COLORS if _current_theme=='light_monochrome' else MONO_COLORS
        mapped=mapping.get(value.name().lower())
        if mapped:
            result=QColor(mapped);result.setAlpha(value.alpha());return result
        gray=round(.2126*value.red()+.7152*value.green()+.0722*value.blue())
        if _current_theme=='light_monochrome':gray=255-gray
        value.setRgb(gray,gray,gray,value.alpha())
    return value


def themed_stylesheet(source):
    return re.sub(r'#[0-9a-fA-F]{6}',lambda m:themed_color(m.group()).name(),source)


def stylesheet():return themed_stylesheet(STYLESHEET)


def application_palette():
    p=QPalette()
    for role,value in [(QPalette.ColorRole.Window,'#11151c'),(QPalette.ColorRole.WindowText,'#e5ebf5'),(QPalette.ColorRole.Base,'#171e28'),(QPalette.ColorRole.AlternateBase,'#1b2430'),(QPalette.ColorRole.Text,'#e5ebf5'),(QPalette.ColorRole.Button,'#232c39'),(QPalette.ColorRole.ButtonText,'#d6e4f7'),(QPalette.ColorRole.Highlight,'#356354'),(QPalette.ColorRole.HighlightedText,'#102b23' if _current_theme in ('monochrome','light_monochrome') else '#e5ebf5'),(QPalette.ColorRole.ToolTipBase,'#151a23'),(QPalette.ColorRole.ToolTipText,'#e5ebf5'),(QPalette.ColorRole.PlaceholderText,'#8c9bb0')]:
        p.setColor(role,themed_color(value))
    for role in (QPalette.ColorRole.Text,QPalette.ColorRole.ButtonText,QPalette.ColorRole.WindowText):p.setColor(QPalette.ColorGroup.Disabled,role,themed_color('#566376'))
    return p


def icon(kind, color='#9cacc1', size=22):
    pix=QPixmap(size*2,size*2)
    pix.fill(Qt.GlobalColor.transparent)
    p=QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size/12,size/12)
    p.setPen(QPen(themed_color(color),1.5,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
    paths={
        'pulse':[(2,12),(6,12),(9,4),(14,20),(18,8),(20,12),(22,12)],
        'play':[(8,5),(20,12),(8,19),(8,5)],
        'plus':[(5,12),(19,12)],
        'link':[(4,7),(9,7),(15,17),(20,17)],
        'chart':[(4,3),(4,21),(21,21),(21,20)],
        'folder':[(3,5),(10,5),(12,8),(21,8),(21,20),(3,20),(3,5)],
        'save':[(4,3),(17,3),(21,7),(21,21),(3,21),(3,3)],
        'undo':[(8,4),(3,9),(8,14)],
        'redo':[(16,4),(21,9),(16,14)],
        'download':[(12,3),(12,16),(7,11),(12,16),(17,11)],
        'fit':[(9,3),(3,3),(3,9)],
        'trash':[(5,6),(6,21),(18,21),(19,6)],
        'experiment':[(9,3),(10,3),(10,9),(5,18),(6,21),(18,21),(19,18),(14,9),(14,3),(15,3)],
        'settings':[(4,7),(20,7)],
        'stop':[(6,6),(18,6),(18,18),(6,18),(6,6)],
        'arrow':[(5,12),(19,12),(14,7),(19,12),(14,17)],
        'sidebar-collapse':[(15,5),(8,12),(15,19)],
        'sidebar-expand':[(9,5),(16,12),(9,19)],
        'restore':[(9,9),(9,3),(21,3),(21,15),(15,15)],
        'close':[(5,5),(19,19)],
    }
    if kind in paths:
        points=paths[kind]
        path=QPainterPath();path.moveTo(*points[0])
        for point in points[1:]:path.lineTo(*point)
        p.drawPath(path)
        if kind=='plus':p.drawLine(12,5,12,19)
        if kind=='restore':p.drawRect(3,9,12,12)
        if kind=='close':p.drawLine(19,5,5,19)
        if kind=='chart':p.drawPolyline([__import__('PySide6.QtCore',fromlist=['QPointF']).QPointF(x,y) for x,y in [(7,17),(12,11),(16,13),(21,5)]])
        if kind=='download':p.drawLine(3,18,3,21);p.drawLine(3,21,21,21);p.drawLine(21,21,21,18)
        if kind=='fit':
            p.drawLine(15,3,21,3);p.drawLine(21,3,21,9);p.drawLine(3,15,3,21);p.drawLine(3,21,9,21);p.drawLine(15,21,21,21);p.drawLine(21,21,21,15)
        if kind=='save':p.drawRect(7,3,9,6);p.drawRect(7,14,10,7)
        if kind=='trash':p.drawLine(3,6,21,6);p.drawLine(9,3,15,3)
        if kind in ('undo','redo'):p.drawArc(QRectF(5,8,14,11),-150*16,230*16)
        if kind=='settings':p.drawLine(4,17,20,17);p.drawEllipse(8,4,6,6);p.drawEllipse(13,14,6,6)
    elif kind=='topology':
        for x,y in [(3,3),(15,3),(15,15)]:p.drawRoundedRect(QRectF(x,y,6,6),1.5,1.5)
        p.drawLine(9,6,15,6);p.drawLine(6,9,6,18);p.drawLine(6,18,15,18)
    elif kind=='connect':
        p.drawEllipse(QRectF(2,8,6,6));p.drawEllipse(QRectF(16,8,6,6));p.drawLine(8,11,16,11)
    elif kind=='help':
        p.drawRoundedRect(QRectF(4,3,16,18),2,2);p.drawLine(8,7,16,7);p.drawLine(8,11,16,11);p.drawLine(8,15,13,15)
    elif kind=='brain':
        p.drawRoundedRect(QRectF(6,4,12,16),4,4)
        for y in (7,12,17):p.drawLine(3,y,6,y);p.drawLine(18,y,21,y)
        p.drawLine(12,4,12,20);p.drawLine(8,9,12,9);p.drawLine(12,15,16,15)
    elif kind=='history':p.drawEllipse(QRectF(3,3,18,18));p.drawLine(12,7,12,13);p.drawLine(12,13,16,15)
    else:
        p.drawRoundedRect(QRectF(4,3,16,7),2,2);p.drawRoundedRect(QRectF(4,14,16,7),2,2)
        p.drawLine(7,6,9,6);p.drawLine(7,17,9,17);p.drawLine(13,6,17,6);p.drawLine(13,17,17,17)
    p.end()
    pix.setDevicePixelRatio(2)
    return QIcon(pix)


STYLESHEET='''
QWidget { background: #11151c; color: #e5ebf5; font-family: "Segoe UI"; font-size: 12px; }
QMainWindow, QDialog { background: #11151c; }
QFrame#Sidebar { background: #151a23; border-right: 1px solid #28313e; }
QFrame#Panel, QFrame#Metric { background: #191f29; border: 1px solid #2a3443; border-radius: 10px; }
QFrame#Toolbar { background: #191f29; border: 1px solid #2a3443; border-radius: 8px; }
QFrame#FloatingPanel { background: #191f29; border: 1px solid #536980; border-radius: 8px; }
QPushButton#QuickNode { padding: 3px 4px; font-size: 11px; }
QPushButton#FloatingTool { padding: 3px; }
QPushButton#FloatingTool:checked { background: #263b38; border-color: #8fe0c2; }
QLabel { background: transparent; }
QLabel#Title { font-size: 25px; font-weight: 600; color: #f0f3fa; }
QLabel#Subtitle, QLabel#Muted { color: #8c9bb0; }
QLabel#Eyebrow { color: #6f8198; font-size: 10px; font-weight: 600; letter-spacing: 2px; }
QLabel#PanelTitle { font-size: 13px; font-weight: 600; }
QLabel#MetricValue { font-size: 27px; font-weight: 600; color: #e7eef9; }
QLabel#Badge { color: #8fe0c2; background: #213a35; padding: 5px 9px; border-radius: 5px; font-size: 10px; }
QPushButton { background: #232c39; border: 1px solid #344153; padding: 9px 14px; border-radius: 6px; color: #cfdaeb; font-weight: 500; }
QPushButton:hover { background: #2b3747; border-color: #536980; }
QPushButton:pressed { background: #344354; }
QPushButton:disabled { color: #566376; background: #1d2530; border-color: #293342; }
QPushButton#Primary { background: #8fe0c2; color: #102b23; border-color: #8fe0c2; font-weight: 600; }
QPushButton#Primary:hover { background: #a4eed3; }
QPushButton#Danger { color: #ed9c9f; }
QPushButton#Nav { text-align: left; border: 0; background: transparent; padding: 13px 16px; color: #8f9db1; border-radius: 7px; }
QPushButton#Nav:hover { color: #e5ebf5; background: #202936; }
QPushButton#Nav:checked { background: #263b38; color: #a6efd1; font-weight: 600; }
QPushButton#LinkMode:checked { background: #263b38; color: #a6efd1; border: 1px solid #8fe0c2; }
QPushButton#Nav[collapsed="true"] { text-align: center; padding: 12px 0; }
QPushButton#SidebarToggle { padding: 0; background: transparent; border: 0; }
QPushButton#SidebarToggle:hover { background: #202936; }
QToolButton { background: transparent; border: 1px solid transparent; padding: 6px; border-radius: 5px; color: #a7b7cb; }
QToolButton:hover { background: #283444; border-color: #3b4d63; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit { background: #131a24; border: 1px solid #344153; border-radius: 5px; padding: 7px 9px; selection-background-color: #2e6e60; color: #d9e3f3; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid #82cdb3; }
QComboBox::drop-down { border: 0; width: 22px; }
QComboBox QAbstractItemView { background: #202a38; border: 1px solid #3a4b60; selection-background-color: #34564d; color: #e1eaf7; }
QSpinBox::up-button, QDoubleSpinBox::up-button { width: 14px; border: 0; }
QSpinBox::down-button, QDoubleSpinBox::down-button { width: 14px; border: 0; }
QCheckBox { spacing: 9px; background: transparent; color: #aebed1; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #50637a; border-radius: 4px; background: #18222f; }
QCheckBox::indicator:checked { background: #79bfa7; border-color: #8fe0c2; }
QTableView, QListWidget { background: #171e28; alternate-background-color: #1b2430; border: 1px solid #2c3849; border-radius: 6px; gridline-color: #293546; selection-background-color: #294c43; selection-color: #d4f7e9; outline: 0; }
QTableView::item { padding: 6px; border: 0; }
QHeaderView::section { background: #202936; border: 0; border-bottom: 1px solid #344255; padding: 10px 9px; color: #94a5bd; font-size: 10px; font-weight: 600; }
QTableCornerButton::section { background: #202936; border: 0; }
QListWidget::item { padding: 12px 10px; border-bottom: 1px solid #293546; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: #39495b; border-radius: 4px; min-height: 35px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 9px; }
QScrollBar::handle:horizontal { background: #39495b; border-radius: 4px; min-width: 35px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QTabWidget::pane { border: 0; background: transparent; }
QTabBar::tab { background: transparent; color: #7f92ab; padding: 11px 18px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #9ce8c9; border-bottom-color: #8fe0c2; }
QTabBar::tab:hover { color: #d3e3f3; }
QSplitter::handle { background: #11151c; }
QSplitter::handle:hover { background: #456052; }
QSplitter#WorkspaceSplit::handle { background: #11151c; }
QSplitter#WorkspaceSplit::handle:hover { background: #212a36; }
QFrame#OverviewGrip { background: #62778d; border: none; border-radius: 1px; }
QProgressBar { border: 0; border-radius: 3px; background: #273342; height: 6px; color: #a9cfbd; }
QProgressBar::chunk { background: #8fe0c2; border-radius: 3px; }
QSlider::groove:horizontal { height: 5px; background: #344255; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #80c6b0; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px; background: #a6edd4; }
QMenuBar { background: #151a23; color: #96a8c0; border-bottom: 1px solid #28313e; }
QMenuBar::item:selected, QMenu::item:selected { background: #2c493f; }
QMenu { background: #1d2734; border: 1px solid #3a4b60; padding: 5px; }
QMenu::item { padding: 8px 25px 8px 12px; }
QStatusBar { background: #151a23; color: #8195af; font-size: 10px; border-top: 1px solid #293546; }
QStatusBar::item { border: 0; }
QToolTip { background: #263447; color: #e4edf9; border: 1px solid #4b617c; padding: 7px; }
QGroupBox { border: 1px solid #2e3c4e; border-radius: 6px; margin-top: 13px; padding: 14px 8px 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #94acc6; }
'''
