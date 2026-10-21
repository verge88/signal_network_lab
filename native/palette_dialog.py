"""Native, copyable color reference; inspecting a palette never changes the theme."""
from PySide6.QtGui import QColor,QPainter,QPen
from PySide6.QtWidgets import (QApplication,QDialog,QVBoxLayout,QHBoxLayout,QLabel,
    QComboBox,QPushButton,QScrollArea,QWidget,QGridLayout,QDialogButtonBox)
from .theme_palettes import EDITOR_PALETTES,PALETTE_LABELS


def palette_text(key):
    palette=EDITOR_PALETTES[key]
    return palette['name']+'\n'+'\n'.join(f'{label}: {palette[role].upper()}' for role,label in PALETTE_LABELS.items())


class Swatch(QWidget):
    def __init__(self,color,parent=None):
        super().__init__(parent);self.color=QColor(color);self.setFixedSize(32,24)
        self.setAccessibleName('Образец '+color.upper());self.setToolTip(color.upper())
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self.color);painter.setPen(QPen(QColor('#808080'),1))
        painter.drawRoundedRect(self.rect().adjusted(1,1,-1,-1),3,3);painter.end()


class PaletteDialog(QDialog):
    def __init__(self,parent=None,theme='graphite',apply=None):
        super().__init__(parent);self.apply_callback=apply;self.setWindowTitle('Палитра оформления')
        self.resize(690,600);self.setMinimumSize(520,360)
        layout=QVBoxLayout(self);layout.setContentsMargins(18,18,18,18);layout.setSpacing(12)
        self.selector=QComboBox()
        for key,palette in EDITOR_PALETTES.items():self.selector.addItem(palette['name'],key)
        self.selector.setCurrentIndex(max(0,self.selector.findData(theme)));layout.addWidget(self.selector)
        hint=QLabel('Нажмите HEX-код, чтобы скопировать цвет.');hint.setObjectName('Muted');hint.setWordWrap(True);layout.addWidget(hint)
        scroll=QScrollArea();scroll.setWidgetResizable(True);layout.addWidget(scroll,1)
        self.content=QWidget();self.grid=QGridLayout(self.content);self.grid.setContentsMargins(2,2,10,2);self.grid.setVerticalSpacing(8);scroll.setWidget(self.content)
        self.feedback=QLabel('');self.feedback.setObjectName('Muted');layout.addWidget(self.feedback)
        actions=QHBoxLayout();self.copy_all=QPushButton('Копировать палитру');self.copy_all.clicked.connect(self.copy_palette);actions.addWidget(self.copy_all);actions.addStretch()
        self.apply_button=QPushButton('Применить тему');self.apply_button.setObjectName('Primary');self.apply_button.setEnabled(apply is not None);self.apply_button.clicked.connect(self.apply_selected);actions.addWidget(self.apply_button);layout.addLayout(actions)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.button(QDialogButtonBox.StandardButton.Close).setText('Закрыть');buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.selector.currentIndexChanged.connect(self.refresh);self.refresh()
    def refresh(self):
        while self.grid.count():
            item=self.grid.takeAt(0)
            if item.widget():item.widget().deleteLater()
        self.swatches=[];self.color_buttons=[];palette=EDITOR_PALETTES[self.selector.currentData()]
        for row,(role,title) in enumerate(PALETTE_LABELS.items()):
            color=palette[role];swatch=Swatch(color);self.swatches.append(swatch)
            self.grid.addWidget(swatch,row,0);self.grid.addWidget(QLabel(title),row,1)
            button=QPushButton(color.upper());button.setAccessibleName(title+' '+color.upper());button.setToolTip('Скопировать '+color.upper());button.setFixedWidth(96)
            button.clicked.connect(lambda checked=False,value=color:self.copy_color(value));self.color_buttons.append(button);self.grid.addWidget(button,row,2)
        self.grid.setColumnStretch(1,1);self.feedback.clear()
    def copy_color(self,color):
        QApplication.clipboard().setText(color.upper());self.feedback.setText('Скопирован цвет '+color.upper())
    def copy_palette(self):
        QApplication.clipboard().setText(palette_text(self.selector.currentData()));self.feedback.setText('Палитра скопирована')
    def apply_selected(self):
        if self.apply_callback:self.apply_callback(self.selector.currentData());self.feedback.setText('Тема применена')
