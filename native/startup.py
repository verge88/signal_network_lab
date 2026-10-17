"""Small Qt splash that can be painted before the simulation and ML imports."""
from PySide6.QtCore import Qt,QSettings,QRectF
from PySide6.QtGui import QColor,QFont,QPainter,QPainterPath,QPen,QPixmap,QCursor
from PySide6.QtWidgets import QApplication,QSplashScreen


SPLASH_COLORS={
    'monochrome':('#000000','#ffffff','#a0a0a0','#292929','#ffffff'),
    'light_monochrome':('#ffffff','#161616','#606060','#d6d6d6','#000000'),
    'dark':('#11151c','#e5ebf5','#8c9bb0','#344153','#8fe0c2'),
}


class StartupSplash(QSplashScreen):
    def __init__(self,theme=None):
        super().__init__(QPixmap(),Qt.WindowType.WindowStaysOnTopHint)
        self.theme=theme or QSettings('SignalLab','SignalLabDesktop').value('theme','monochrome')
        if self.theme not in SPLASH_COLORS:self.theme='monochrome'
        self.status='Запуск программы…';self.ready=False
        app=QApplication.instance();self.target_screen=app.screenAt(QCursor.pos()) or app.primaryScreen()
        self.setWindowTitle('SignalLab — запуск');self.setAccessibleName('Загрузка SignalLab');self.render_card()
        self.move(self.target_screen.availableGeometry().center()-self.rect().center())
    def render_card(self):
        background,ink,muted,line,accent=map(QColor,SPLASH_COLORS[self.theme])
        dpr=self.target_screen.devicePixelRatio();pixmap=QPixmap(round(400*dpr),round(176*dpr));pixmap.setDevicePixelRatio(dpr);pixmap.fill(Qt.GlobalColor.transparent)
        painter=QPainter(pixmap)
        try:
            painter.setRenderHints(QPainter.RenderHint.Antialiasing|QPainter.RenderHint.TextAntialiasing)
            painter.setPen(QPen(line,1));painter.setBrush(background);painter.drawRoundedRect(QRectF(.5,.5,399,175),12,12)
            painter.save();painter.translate(22,27);painter.scale(1.7,1.7);painter.setPen(QPen(accent,1.5,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin));path=QPainterPath()
            for index,(x,y) in enumerate(((2,12),(6,12),(9,4),(14,20),(18,8),(20,12),(22,12))):
                path.lineTo(x,y) if index else path.moveTo(x,y)
            painter.drawPath(path);painter.restore()
            font=QFont('Segoe UI');font.setPixelSize(25);font.setWeight(QFont.Weight.DemiBold);painter.setFont(font);painter.setPen(ink);painter.drawText(QRectF(78,24,295,35),Qt.AlignmentFlag.AlignVCenter,'SignalLab')
            font.setPixelSize(12);font.setWeight(QFont.Weight.Normal);painter.setFont(font);painter.setPen(muted);painter.drawText(QRectF(79,61,295,22),'Лаборатория сигнальных сетей')
            font.setPixelSize(13);painter.setFont(font);painter.setPen(ink);painter.drawText(QRectF(25,108,350,24),self.status)
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(line);painter.drawRoundedRect(QRectF(25,147,350,3),1.5,1.5)
            painter.setBrush(accent);painter.drawRoundedRect(QRectF(25,147,64,3),1.5,1.5)
        finally:painter.end()
        self.setPixmap(pixmap)
    def update_status(self,text):
        self.status=text;self.render_card();self.repaint();QApplication.processEvents()
    def show_startup(self):
        self.show();self.raise_();self.update_status(self.status)
    def complete(self,window):
        if not window.isVisible():return False
        self.ready=True;self.finish(window);return True
    def mousePressEvent(self,event):
        # QSplashScreen normally dismisses itself on any click. Keep it visible
        # until the main window has actually been shown.
        event.accept()
