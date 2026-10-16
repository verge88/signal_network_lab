"""Create a code-drawn multi-resolution Windows icon using Qt only."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import struct
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage,QPainter,QColor,QPen,QPainterPath
from PySide6.QtCore import Qt,QBuffer,QIODevice,QRectF

app=QApplication([])
images=[]
for size in (16,24,32,48,64,128,256):
    image=QImage(size,size,QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.transparent)
    p=QPainter(image);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.scale(size/64,size/64)
    p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#18382e'));p.drawRoundedRect(QRectF(1,1,62,62),14,14)
    p.setPen(QPen(QColor('#8fe0c2'),3.4,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin));path=QPainterPath();path.moveTo(9,34)
    for x,y in [(19,34),(26,14),(36,51),(44,25),(49,34),(55,34)]:path.lineTo(x,y)
    p.drawPath(path);p.end();buffer=QBuffer();buffer.open(QIODevice.OpenModeFlag.WriteOnly);image.save(buffer,'PNG');images.append((size,bytes(buffer.data())))
offset=6+16*len(images);entries=[];payload=[]
for size,data in images:
    entries.append(struct.pack('<BBBBHHII',size if size<256 else 0,size if size<256 else 0,0,0,1,32,len(data),offset));offset+=len(data);payload.append(data)
root=Path(__file__).resolve().parent.parent
(root/'assets').mkdir(exist_ok=True)
(root/'assets'/'SignalLab.ico').write_bytes(struct.pack('<HHH',0,1,len(images))+b''.join(entries)+b''.join(payload))
