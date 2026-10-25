"""Native application settings with immediate, persistent changes."""
from pathlib import Path
from PySide6.QtCore import Qt,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget,QVBoxLayout,QFormLayout,QTabWidget,QScrollArea,QCheckBox,QLineEdit,QFileDialog,QApplication
from lab import storage
from . import preferences
from .style import THEMES
from .theme_palettes import EDITOR_PALETTES
from .widgets import label,button,row,panel,combo,spin


class SettingsPage(QScrollArea):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.fields={};self.refreshing=True;self.setWidgetResizable(True)
        content=QWidget();layout=QVBoxLayout(content);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12);self.setWidget(content)
        note=label('Изменения применяются сразу и сохраняются для следующих запусков.','Muted');note.setWordWrap(True);layout.addWidget(note)
        self.tabs=QTabWidget();layout.addWidget(self.tabs)
        appearance,al=self.tab('Оформление')
        card,form,cl=self.section(al,'Тема и рабочая область')
        self.add_combo(form,'theme','Тема',list(THEMES.items()))
        self.palette_button=button('Палитра цветов…',lambda:owner.call(owner.show_palette),'settings');form.addRow('',self.palette_button)
        self.add_check(form,'sidebarCollapsed','Свернуть левое меню')
        self.add_check(form,'preferences/statusBar','Показывать строку состояния')
        self.add_check(form,'topologyMetrics','Показывать показатели на вкладке топологии')
        self.add_spin(form,'overviewHeight','Высота блока показателей',125,480,0,'px')
        hint=label('Высота применяется к показателям на топологии и телеметрии. Панель заданий видна и при скрытой строке состояния.','Muted');hint.setWordWrap(True);cl.addWidget(hint);al.addStretch()
        topology,tl=self.tab('Карта топологии')
        card,form,cl=self.section(tl,'Отображение и навигация')
        self.add_check(form,'graph/grid','Показывать точечную сетку')
        self.add_check(form,'graph/linkLabels','Показывать задержки на каналах')
        self.add_check(form,'preferences/fitOnTopology','Вписывать схему при переходе на вкладку топологии')
        self.add_spin(form,'graph/panSpeed','Скорость прокрутки колёсиком',.25,4.,2,'×')
        self.add_spin(form,'graph/zoomFactor','Шаг масштаба · Ctrl + колесо',1.05,1.5,2,'×')
        hint=label('Колесо — перемещение по вертикали; Shift + колесо — по горизонтали; Ctrl + колесо — масштаб. Зажатое колесо или Пробел с перетаскиванием — свободное перемещение. Эти настройки меняют вид карты, без изменения параметров сети.','Muted');hint.setWordWrap(True);cl.addWidget(hint);tl.addStretch()
        behavior,bl=self.tab('Запуск и поведение')
        card,form,cl=self.section(bl,'При запуске и завершении расчётов')
        self.add_combo(form,'preferences/startPage','Стартовая вкладка',[(i,title) for i,title in enumerate(('Топология','Эксперименты','Телеметрия','ML-детекторы','Результаты','Справка','Атаки','Настройки'))])
        self.add_check(form,'preferences/rememberWindow','Запоминать размер и положение окна, ширину инспектора')
        self.add_check(form,'preferences/restoreWorkspace','Восстанавливать последнюю рабочую схему')
        self.add_check(form,'preferences/beepOnSuccess','Звуковой сигнал после успешного вычисления')
        hint=label('Стартовая вкладка и восстановление рабочей схемы учитываются при следующем запуске. Сохранённые проекты и результаты доступны в библиотеке при любых значениях этих настроек.','Muted');hint.setWordWrap(True);cl.addWidget(hint);bl.addStretch()
        data,dl=self.tab('Данные и экспорт')
        card,form,cl=self.section(dl,'Локальное хранилище')
        path=QLineEdit(str(storage.DATA));path.setReadOnly(True);path.setToolTip(str(storage.DATA));form.addRow('Папка данных',path)
        cl.addLayout(row(button('Открыть папку',lambda:owner.call(lambda:self.open_folder(storage.DATA)),'folder'),button('Скопировать путь',lambda:QApplication.clipboard().setText(str(storage.DATA))),stretch=True))
        self.log_note=label('','Muted');self.log_note.setWordWrap(True);cl.addWidget(self.log_note)
        card,form,cl=self.section(dl,'Экспорт по умолчанию')
        export=QLineEdit();export.setReadOnly(True);self.fields['export/directory']=export;form.addRow('Папка экспорта',export)
        cl.addLayout(row(button('Выбрать папку…',lambda:owner.call(self.choose_export_directory),'folder'),stretch=True))
        hint=label('Папку можно изменить и в окне сохранения. Форматы и оформление схем, графиков и таблиц настраиваются в окне экспорта.','Muted');hint.setWordWrap(True);cl.addWidget(hint)
        version=QApplication.applicationVersion() or 'из исходников';dl.addLayout(row(label('SignalLab · '+version,'Muted'),button('О программе',owner.about),stretch=True));dl.addStretch()
        self.feedback=label('','Muted');self.feedback.setWordWrap(True)
        layout.addLayout(row(self.feedback,button('Восстановить стандартные настройки',lambda:owner.call(self.reset),'undo'),stretch=False));layout.addStretch()
        self.refreshing=False;self.refresh()
    def tab(self,title):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(0,12,0,0);layout.setSpacing(12);self.tabs.addTab(page,title);return page,layout
    def section(self,layout,title):
        card,cl,head=panel(title);layout.addWidget(card);form=QFormLayout();form.setHorizontalSpacing(18);form.setVerticalSpacing(12);form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft);form.setFormAlignment(Qt.AlignmentFlag.AlignTop|Qt.AlignmentFlag.AlignLeft);cl.addLayout(form);return card,form,cl
    def add_combo(self,form,key,title,items):
        field=combo(items);field.setMaximumWidth(520);field.setAccessibleName(title);self.fields[key]=field;form.addRow(title,field)
        field.currentIndexChanged.connect(lambda index,k=key:self.changed(k,self.fields[k].currentData()))
    def add_check(self,form,key,title):
        field=QCheckBox(title);self.fields[key]=field;form.addRow(field);field.toggled.connect(lambda value,k=key:self.changed(k,value))
    def add_spin(self,form,key,title,lo,hi,decimals,suffix):
        field=spin(preferences.defaults()[key],lo,hi,decimals,suffix);field.setMaximumWidth(240);field.setAccessibleName(title);self.fields[key]=field;form.addRow(title,field)
        field.valueChanged.connect(lambda value,k=key:self.changed(k,value))
    def changed(self,key,value):
        if not self.refreshing:self.owner.call(lambda:self.owner.apply_preferences({key:value}))
    def refresh(self):
        self.refreshing=True
        try:
            values=getattr(self.owner,'preferences',preferences.load(self.owner.settings))
            values['export/directory']=preferences.load(self.owner.settings)['export/directory']
            for key,field in self.fields.items():
                value=values[key]
                if isinstance(field,QCheckBox):field.setChecked(value)
                elif hasattr(field,'findData'):field.setCurrentIndex(field.findData(value))
                elif isinstance(field,QLineEdit):field.setText(value);field.setToolTip(value)
                else:field.setValue(value)
            self.palette_button.setEnabled(values['theme'] in EDITOR_PALETTES)
            logs=[str(storage.DATA/name) for name in ('desktop-errors.log','native-fault.log') if (storage.DATA/name).exists()]
            self.log_note.setText('Журналы диагностики: '+', '.join(logs) if logs else 'При возникновении ошибок журналы появятся в папке данных.')
        finally:self.refreshing=False
    def reset(self):
        self.owner.apply_preferences(preferences.defaults());self.feedback.setText('Стандартные настройки восстановлены. Проекты, результаты и профили стенда сохранены.')
    def choose_export_directory(self):
        path=QFileDialog.getExistingDirectory(self,'Папка экспорта',self.fields['export/directory'].text())
        if path:self.owner.apply_preferences({'export/directory':path})
    def open_folder(self,path):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve()))):raise OSError('Не удалось открыть папку')
