"""Native export workspace with a data snapshot and independent appearance settings."""
import json
import copy
from dataclasses import asdict,replace
from pathlib import Path
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,
    QScrollArea,QLabel,QLineEdit,QCheckBox,QFontComboBox,QColorDialog,QFileDialog,
    QMessageBox,QDialogButtonBox)
from .widgets import label,button,combo,spin
from .exporting import (ExportStyle,PRESETS,FORMATS,table_data,chart_data,topology_data,
                        export_file)
from .export_preview import ExportPreview


class ExportDialog(QDialog):
    def __init__(self,parent,sources,initial=None):
        super().__init__(parent);self.setWindowTitle('Экспорт схем, графиков и таблиц');self.resize(1200,790);self.setMinimumSize(1000,650)
        self.settings=parent.settings;self.sources=sources;self.last_path=None;self.loading=True
        self.snapshots={}
        for source in sources:
            if source['kind']=='table':snapshot={scope:table_data(source['widget'],source['title'],scope=='selected') for scope in ('all','selected')}
            elif source['kind']=='topology':snapshot={scope:topology_data(source['widget'],source['title'],scope=='selected') for scope in ('all','selected')}
            else:snapshot={'all':chart_data(source['widget'],source['title'])}
            for data in snapshot.values():data.metadata=copy.deepcopy(source.get('metadata',{}))
            self.snapshots[source['key']]=snapshot
        initial_source=next((s for s in sources if s['key']==initial),sources[0])
        try:saved=json.loads(self.settings.value('export/topologyStyle' if initial_source['kind']=='topology' else 'export/style','{}'))
        except (ValueError,TypeError):saved={}
        self.saved_style=ExportStyle.load(saved)
        root=QVBoxLayout(self);root.setContentsMargins(20,18,20,16);root.setSpacing(12)
        root.addWidget(label('Экспорт результатов','PanelTitle'))
        root.addWidget(label('Схема сохраняет расположение узлов. Графики — исходные точки; таблицы — строки текущего фильтра.','Muted'))
        top=QFormLayout();self.source_combo=combo([(s['key'],s['title']) for s in sources],initial);self.format_combo=combo([]);self.scope_combo=combo([('all','Все строки таблицы'),('selected','Только выделенные строки')]);self.title_edit=QLineEdit()
        self.title_edit.setMaxLength(200)
        top.addRow('Объект',self.source_combo);line=QHBoxLayout();line.addWidget(self.format_combo);line.addWidget(self.scope_combo);top.addRow('Формат / область',line);top.addRow('Заголовок',self.title_edit);root.addLayout(top)
        body=QHBoxLayout();root.addLayout(body,1)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setMinimumWidth(345);scroll.setMaximumWidth(390);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);settings=QWidget();form=QFormLayout(settings);form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows);form.setContentsMargins(3,3,12,8);form.setSpacing(10);scroll.setWidget(settings);body.addWidget(scroll)
        self.preset_combo=combo(['Последнее оформление']+list(PRESETS));form.addRow('Стиль',self.preset_combo)
        self.style_fields={};self.chart_fields=[];self.table_fields=[];self.visual_fields=[];self.topology_rows=[];self.kind_rows=[];self.style_form=form
        self.font_combo=QFontComboBox();self.font_combo.setMaximumWidth(200);self.style_fields['font_family']=self.font_combo;form.addRow('Шрифт',self.font_combo)
        for key,title,value,lo,hi,decimals in [('font_size','Размер шрифта',14,8,32,0),('padding','Отступы ячеек',8,2,20,0),('line_width','Толщина линии',2,.5,8,1)]:
            control=spin(value,lo,hi,decimals);self.style_fields[key]=control;form.addRow(title,control)
            if key=='padding':self.kind_rows.append((form.rowCount()-1,{'table'}))
        self.table_fields.append(self.style_fields['padding']);self.visual_fields.append(self.style_fields['line_width'])
        for key,title in [('background','Фон'),('foreground','Текст'),('accent','Линии / каналы'),('grid_color','Сетка / границы'),('header_background','Шапка таблицы'),('alternate_background','Чередование строк')]:
            field=QLineEdit();field.setMaxLength(7);field.setMaximumWidth(112);choose=button('…',lambda checked=False,k=key:self.choose_color(k));choose.setFixedWidth(36)
            layout=QHBoxLayout();layout.addWidget(field);layout.addWidget(choose);form.addRow(title,layout);self.style_fields[key]=field
            if key=='accent':self.visual_fields.extend([field,choose]);self.accent_fields=[field,choose]
            if key in ('header_background','alternate_background'):self.table_fields.extend([field,choose]);self.kind_rows.append((form.rowCount()-1,{'table'}))
        self.style_fields['line_style']=combo([('solid','Сплошная'),('dash','Штриховая'),('dot','Точечная')]);form.addRow('Тип линии',self.style_fields['line_style']);self.visual_fields.append(self.style_fields['line_style'])
        for key,title,kind in [('grid','Сетка / границы',None),('markers','Точки на линиях','chart'),('legend','Легенда','visual'),('events','Интервалы событий','chart'),('alternating','Чередование строк','table'),('landscape','PDF: альбомный лист',None)]:
            field=QCheckBox(title);self.style_fields[key]=field;form.addRow(field)
            if kind=='chart':self.chart_fields.append(field);self.kind_rows.append((form.rowCount()-1,{'chart'}))
            elif kind=='visual':self.visual_fields.append(field)
            elif kind=='table':self.table_fields.append(field);self.kind_rows.append((form.rowCount()-1,{'table'}))
        self.size_fields=[]
        for key,title,value,lo,hi in [('width','Ширина изображения, px',1600,640,4096),('height','Высота изображения, px',900,360,4096),('dpi','Разрешение, DPI',150,72,600)]:
            field=spin(value,lo,hi);self.style_fields[key]=field;form.addRow(title,field);self.size_fields.append(field)
        self.x_edit=QLineEdit();self.y_edit=QLineEdit();form.addRow('Подпись X',self.x_edit);form.addRow('Подпись Y',self.y_edit);self.chart_fields.extend([self.x_edit,self.y_edit])
        self.kind_rows.extend([(form.rowCount()-2,{'chart'}),(form.rowCount()-1,{'chart'})])
        form.addRow(label('Оформление топологии','PanelTitle'));self.topology_rows.append(form.rowCount()-1)
        for key,title in [('node_background','Заливка узлов'),('node_border','Контур узлов')]:
            field=QLineEdit();field.setMaxLength(7);field.setMaximumWidth(112);choose=button('…',lambda checked=False,k=key:self.choose_color(k));choose.setFixedWidth(36)
            layout=QHBoxLayout();layout.addWidget(field);layout.addWidget(choose);form.addRow(title,layout);self.style_fields[key]=field;self.topology_rows.append(form.rowCount()-1)
        for key,title,value,lo,hi,decimals in [('node_scale','Масштаб карточек узлов',1,.5,2,2),('canvas_margin','Поля схемы, px',32,8,160,0)]:
            field=spin(value,lo,hi,decimals);self.style_fields[key]=field;form.addRow(title,field);self.topology_rows.append(form.rowCount()-1)
        for key,title in [('node_names','Названия узлов'),('node_types','Типы узлов'),('node_ids','ID узлов'),('node_details','Ёмкость и очереди узлов'),('node_icons','Иконки узлов'),('link_labels','Подписи каналов'),('link_protocol','Протоколы в подписях'),('link_latency','Задержки в подписях'),('protocol_colors','Цвета по протоколам'),('protocol_patterns','Типы линий по протоколам'),('topology_title','Заголовок схемы'),('transparent_background','Прозрачный фон (PNG / SVG)')]:
            field=QCheckBox(title);self.style_fields[key]=field;form.addRow(field);self.topology_rows.append(form.rowCount()-1)
        self.style_fields['protocol_patterns'].setToolTip('Если выключено, используется общий тип линии.')
        reset=button('Вернуть светлый монохром',lambda:self.apply_style(PRESETS['Светлый монохром']));form.addRow(reset)
        preview=QVBoxLayout();body.addLayout(preview,1)
        preview_header=QHBoxLayout();preview_header.addWidget(label('Предпросмотр','PanelTitle'));preview_header.addStretch()
        self.preview=ExportPreview();self.zoom_label=label('','Muted');self.zoom_label.setMinimumWidth(48);preview_header.addWidget(self.zoom_label)
        self.zoom_out_button=button('−',lambda:self.preview.zoom_by(1/1.25));self.zoom_out_button.setToolTip('Уменьшить предпросмотр')
        self.zoom_in_button=button('+',lambda:self.preview.zoom_by(1.25));self.zoom_in_button.setToolTip('Увеличить предпросмотр')
        self.actual_size_button=button('100%',lambda:self.preview.set_zoom(1));self.actual_size_button.setToolTip('Исходный размер без уменьшения')
        self.fit_preview_button=button('Вписать',self.preview.fit_page)
        for control in (self.zoom_out_button,self.zoom_in_button,self.actual_size_button,self.fit_preview_button):preview_header.addWidget(control)
        self.preview.zoom_changed.connect(lambda value:self.zoom_label.setText(f'{value*100:.0f}%'))
        preview.addLayout(preview_header);preview.addWidget(self.preview,1)
        self.info=label('','Muted');self.info.setWordWrap(True);preview.addWidget(self.info)
        self.note=label('Оформление сохраняется в изображениях, SVG, PDF, XLSX и HTML. CSV, TSV и JSON содержат данные без оформления.','Muted');self.note.setWordWrap(True);preview.addWidget(self.note)
        self.error_label=label('');self.error_label.setWordWrap(True);root.addWidget(self.error_label)
        actions=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Close);self.save_button=actions.button(QDialogButtonBox.StandardButton.Save);self.save_button.setText('Экспортировать…');self.save_button.setObjectName('Primary');actions.button(QDialogButtonBox.StandardButton.Close).setText('Закрыть');actions.accepted.connect(self.save_export);actions.rejected.connect(self.reject);root.addWidget(actions)
        self.preview_timer=QTimer(self);self.preview_timer.setSingleShot(True);self.preview_timer.timeout.connect(self.refresh_preview)
        self.source_combo.currentIndexChanged.connect(self.source_changed);self.scope_combo.currentIndexChanged.connect(self.schedule_preview);self.format_combo.currentIndexChanged.connect(self.format_changed)
        self.style_fields['protocol_patterns'].toggled.connect(self.format_changed);self.style_fields['protocol_colors'].toggled.connect(self.format_changed)
        self.title_edit.textChanged.connect(self.schedule_preview);self.x_edit.textChanged.connect(self.schedule_preview);self.y_edit.textChanged.connect(self.schedule_preview);self.preset_combo.currentTextChanged.connect(self.preset_changed)
        for field in self.style_fields.values():
            if isinstance(field,QCheckBox):field.toggled.connect(self.schedule_preview)
            elif isinstance(field,QLineEdit):field.textChanged.connect(self.schedule_preview)
            elif isinstance(field,QFontComboBox):field.currentFontChanged.connect(self.schedule_preview)
            elif hasattr(field,'valueChanged'):field.valueChanged.connect(self.schedule_preview)
            else:field.currentIndexChanged.connect(self.schedule_preview)
        self.apply_style(self.saved_style);self.loading=False;self.source_changed()

    def source(self):return next(s for s in self.sources if s['key']==self.source_combo.currentData())

    def data(self):
        source=self.source()
        scope=self.scope_combo.currentData() if source['kind'] in ('table','topology') else 'all'
        data=copy.deepcopy(self.snapshots[source['key']][scope]);data.title=self.title_edit.text()
        if data.kind=='chart':data.x_label=self.x_edit.text();data.y_label=self.y_edit.text()
        return data

    def style(self):
        values={}
        for key,field in self.style_fields.items():
            if isinstance(field,QCheckBox):values[key]=field.isChecked()
            elif isinstance(field,QFontComboBox):values[key]=field.currentFont().family()
            elif isinstance(field,QLineEdit):values[key]=field.text()
            elif hasattr(field,'value'):values[key]=field.value()
            else:values[key]=field.currentData()
        style=ExportStyle(**values);style.validate();return style

    def apply_style(self,style):
        previous=self.loading;self.loading=True
        for key,field in self.style_fields.items():
            value=getattr(style,key)
            if isinstance(field,QCheckBox):field.setChecked(value)
            elif isinstance(field,QFontComboBox):
                font=field.currentFont();font.setFamily(value);field.setCurrentFont(font)
            elif isinstance(field,QLineEdit):field.setText(value)
            elif hasattr(field,'setValue'):field.setValue(value)
            else:field.setCurrentIndex(field.findData(value))
        self.loading=previous
        if not self.loading:self.schedule_preview()

    def preset_changed(self,title):self.apply_style(self.saved_style if title=='Последнее оформление' else replace(PRESETS[title]))

    def choose_color(self,key):
        field=self.style_fields[key];color=QColorDialog.getColor(QColor(field.text()),self,'Цвет оформления')
        if color.isValid():field.setText(color.name())

    def source_changed(self):
        self.preview.fit_mode=True
        source=self.source();kind=source['kind'];self.title_edit.setText(source['title']);self.scope_combo.setEnabled(kind in ('table','topology'))
        self.scope_combo.blockSignals(True);self.scope_combo.clear()
        for key,title in ([('all','Вся топология'),('selected','Выбранный фрагмент')] if kind=='topology' else [('all','Все строки таблицы'),('selected','Только выделенные строки')]):self.scope_combo.addItem(title,key)
        self.scope_combo.blockSignals(False)
        self.x_edit.setText(source.get('x_label','Время, s'));self.y_edit.setText(source.get('y_label',''))
        self.format_combo.blockSignals(True);self.format_combo.clear()
        for fmt in FORMATS[kind]:self.format_combo.addItem(fmt.upper(),fmt)
        remembered=self.settings.value('export/'+kind+'Format','xlsx' if kind=='table' else 'png');self.format_combo.setCurrentIndex(max(0,self.format_combo.findData(remembered)));self.format_combo.blockSignals(False)
        for field in self.chart_fields:field.setEnabled(kind=='chart')
        for field in self.table_fields:field.setEnabled(kind=='table')
        for field in self.visual_fields:field.setEnabled(kind in ('chart','topology'))
        for row in self.topology_rows:self.style_form.setRowVisible(row,kind=='topology')
        for row,kinds in self.kind_rows:self.style_form.setRowVisible(row,kind in kinds)
        self.format_changed()

    def format_changed(self):
        fmt=self.format_combo.currentData()
        topology=self.source()['kind']=='topology'
        self.style_fields['line_style'].setEnabled(self.source()['kind']=='chart' or topology and not self.style_fields['protocol_patterns'].isChecked())
        for field in self.accent_fields:field.setEnabled(self.source()['kind']=='chart' or topology and not self.style_fields['protocol_colors'].isChecked())
        for field in self.size_fields:field.setEnabled(self.source()['kind'] in ('chart','topology') and fmt in ('png','jpeg','svg'))
        self.style_fields['transparent_background'].setEnabled(self.source()['kind']=='topology' and fmt in ('png','svg'))
        self.style_fields['landscape'].setEnabled(fmt=='pdf' or fmt=='xlsx');self.schedule_preview()

    def schedule_preview(self,*args):
        if not self.loading:self.preview_timer.start(140)

    def refresh_preview(self):
        try:
            data=self.data();data.validate();style=self.style()
            if data.kind=='topology' and self.format_combo.currentData() not in ('png','svg'):style=replace(style,transparent_background=False)
            self.preview.set_content(data,style)
            self.info.setText(f'{len(data.nodes)} узлов, {len(data.links)} каналов. Вся схема вписывается в холст независимо от масштаба карты. SVG и PDF — векторные.' if data.kind=='topology' else f'{len(data.rows)} строк × {len(data.headers)} столбцов. Предпросмотр: первые 12 строк; в файле будет весь выбранный объём.' if data.kind=='table' else f'{len(data.series)} ряд(а), {sum(len(s["points"]) for s in data.series)} точек. SVG и PDF сохраняют векторные линии и текст.')
            self.error_label.clear();self.save_button.setEnabled(True)
        except Exception as e:self.preview.clear();self.error_label.setText(str(e));self.save_button.setEnabled(False)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'preview_timer'):self.schedule_preview()

    def save_export(self):
        try:
            data=self.data();data.validate();style=self.style();fmt=self.format_combo.currentData()
            directory=self.settings.value('export/directory',str(Path.home()/'Documents'))
            dialog=QFileDialog(self,'Экспортировать',str(Path(directory)/('signal-lab-'+self.source()['key']+'.'+fmt)),f'{fmt.upper()} (*.{fmt})');dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave);dialog.setDefaultSuffix(fmt)
            if not dialog.exec():return
            path=Path(dialog.selectedFiles()[0]);export_file(path,data,style,fmt)
            self.last_path=path;self.settings.setValue('export/topologyStyle' if data.kind=='topology' else 'export/style',json.dumps(asdict(style)));self.settings.setValue('export/'+data.kind+'Format',fmt);self.settings.setValue('export/directory',str(path.parent));self.settings.sync();self.saved_style=style
            self.error_label.setText('Сохранено: '+str(path));self.parent().statusBar().showMessage('Экспортировано: '+str(path),10000)
        except Exception as e:QMessageBox.warning(self,'Экспорт',str(e))
