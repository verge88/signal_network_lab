"""Resolution-independent native preview; text and paths stay vector at any zoom."""
from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsItem
from .exporting import paint_visual, table_document


class ExportPage(QGraphicsItem):
    def __init__(self,data,style):
        super().__init__();self.data=data;self.style=style;self.document=None
        if data.kind=='table':
            self.document=table_document(data,style,932,True)
            self.bounds=QRectF(0,0,960,max(510,self.document.size().height()+28))
        else:self.bounds=QRectF(0,0,style.width,style.height)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    def boundingRect(self):return self.bounds
    def paint(self,painter,option,widget=None):
        if self.document is None:paint_visual(painter,self.bounds,self.data,self.style)
        else:
            painter.fillRect(self.bounds,QColor(self.style.background));painter.save();painter.translate(14,12)
            self.document.drawContents(painter);painter.restore()


class ExportPreview(QGraphicsView):
    zoom_changed=Signal(float)
    def __init__(self,parent=None):
        super().__init__(parent);self.setScene(QGraphicsScene(self));self.page=None;self.fit_mode=True
        self.setMinimumSize(490,280);self.setRenderHints(QPainter.RenderHint.Antialiasing|QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag);self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter);self.setAccessibleName('Векторный предпросмотр экспорта')
        self.setToolTip('Колесо — масштаб; перетаскивание — перемещение. «Вписать» — весь лист; 100% — исходный размер.')
    def set_content(self,data,style):
        center=self.mapToScene(self.viewport().rect().center());previous_kind=self.page.data.kind if self.page else None
        self.page=None;self.scene().clear();self.page=ExportPage(data,style);self.scene().addItem(self.page);self.setSceneRect(self.page.boundingRect())
        if self.fit_mode or previous_kind!=data.kind:self.fit_page()
        else:self.centerOn(center);self.zoom_changed.emit(self.transform().m11())
        self.viewport().update()
    def clear(self):self.page=None;self.scene().clear()
    def fit_page(self):
        self.fit_mode=True
        if self.page:
            self.fitInView(self.page.boundingRect().adjusted(-8,-8,8,8),Qt.AspectRatioMode.KeepAspectRatio)
            self.zoom_changed.emit(self.transform().m11())
    def set_zoom(self,zoom):
        self.fit_mode=False;zoom=max(.05,min(8.,zoom));center=self.mapToScene(self.viewport().rect().center())
        self.resetTransform();self.scale(zoom,zoom);self.centerOn(center);self.zoom_changed.emit(zoom)
    def zoom_by(self,factor):self.set_zoom(self.transform().m11()*factor)
    def wheelEvent(self,event):
        if event.angleDelta().y():
            self.fit_mode=False;current=self.transform().m11();zoom=max(.05,min(8.,current*1.2**(event.angleDelta().y()/120)))
            self.scale(zoom/current,zoom/current);self.zoom_changed.emit(zoom)
        event.accept()
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.fit_mode:self.fit_page()
