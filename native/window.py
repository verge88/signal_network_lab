import copy
import json
import os
import sys
import time
import uuid
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import Qt, QSettings, QTimer, QThreadPool, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QUndoCommand, QUndoStack, QDesktopServices
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QFrame,QHBoxLayout,QVBoxLayout,QLabel,QPushButton,QStackedWidget,QSplitter,QScrollArea,QFormLayout,QLineEdit,QCheckBox,QComboBox,QDialog,QFileDialog,QMessageBox,QProgressBar,QSlider,QTabWidget,QAbstractItemView,QDialogButtonBox,QPlainTextEdit,QSizePolicy,QMenu)
from lab import storage, research, ml
from lab.presets import preset
from lab.schema import Project, Node, NODE_KINDS, LINK_PROTOCOLS
from lab.attacks import NODE_EFFECTS
from lab.features import SETS as FEATURE_SETS, operational_metrics
from .style import icon, THEMES, set_theme, saved_theme, themed_color, themed_stylesheet, stylesheet, application_palette
from .widgets import label, button, row, panel, table, populate, spin, combo, Metric, Chart
from .graph import NetworkView
from .node_icons import node_kind_combo,refresh_kind_icons
from .jobs import Job
from .ml_options import ModelOptions
from . import preferences as app_preferences
from .dialogs import node_editor,link_editor,flow_editor,incident_editor,KINDS,INCIDENTS

NAMES=dict(INCIDENTS)
OVERVIEW_MIN_HEIGHT=125
FEATURE_NAMES=dict(rate='Интенсивность',delivery_ratio='Успешные процедуры',latency_mean='Задержка mean',latency_p95='Задержка p95',latency_p99='Задержка p99',loss_ratio='Неуспешные процедуры',timeout_ratio='Тайм-ауты',queue_mean='Очередь mean',queue_max='Очередь max',utilization='Утилизация',route_changes='Смена маршрута',retries='Повторы процедур',transport_retries='Повторы транспорта',completion_rate='Завершения / s',outstanding='Незавершённые процедуры',scheduled_event='Запланированное событие',affected='Воздействие на трафик',observed_degradation='Наблюдаемое ухудшение')
FEATURE_NAMES.update(user_sent='5G · отправлено пакетов',user_delivered='5G · доставлено пакетов',user_loss_ratio='5G · доля потерь данных',user_rtt_p95='5G · RTT данных p95')
FEATURE_NAMES.update(signal_arrivals_s='Сигнализация · новые процедуры / s',signal_completed_s='Сигнализация · завершения / s',signal_failure_ratio='Сигнализация · доля ошибок',signal_timeout_ratio='Сигнализация · доля тайм-аутов',signal_latency_p95='Сигнализация · p95, ms',signal_retries_s='Повторы процедур / s',transport_retries_s='Повторы транспорта / s',control_messages_s='Управляющие сообщения / s',control_reconnects_s='Переподключения / s',neighbor_unavailable_ratio='Недоступность соседей',data_sent_s='Данные · отправлено / s',data_completed_s='Данные · завершено / s',data_loss_ratio='Данные · доля ошибок',data_rtt_p95='Данные · RTT p95, ms',data_goodput_mbps='Данные · goodput, Mbit/s',data_qos_violation_ratio='Данные · нарушение QoS',data_inflight_mean='Данные · пакетов в работе',db_requests_s='БД · запросы / s',db_wait_mean_ms='БД · ожидание, ms',db_overflow_s='БД · переполнения / s',db_timeouts_s='БД · тайм-ауты / s',db_queue_mean='БД · средняя очередь',db_active_ratio='БД · занятость соединений',cache_hit_ratio='Кэш · доля попаданий',cache_occupancy_ratio='Кэш · заполнение',cache_expired_s='Кэш · истечения / s',cache_evicted_s='Кэш · вытеснения / s',context_occupancy_ratio='Контексты · заполнение',context_expired_s='Контексты · истечения / s',context_rejected_s='Контексты · отказы / s')


def number(v,d=1):return '—' if v is None else f'{v:,.{d}f}'.replace(',',' ').replace('.',',')
def percent(v,d=2):return '—' if v is None else number(v*100,d)+'%'
def timestamp(s):
    try:return datetime.fromisoformat(s).astimezone().strftime('%d.%m %H:%M')
    except ValueError:return s
def fingerprint(p):
    q=Project.model_validate(p).model_dump()
    for n in q['nodes']:n.pop('x',None);n.pop('y',None)
    for key in ('journal_sessions','journal_events','journal_procedure_id'):q.pop(key,None)
    return json.dumps(q,sort_keys=True)


class Snapshot(QUndoCommand):
    def __init__(self,window,before,after,title):
        super().__init__(title);self.window=window;self.before=before;self.after=after
    def redo(self):self.window.install_project(copy.deepcopy(self.after))
    def undo(self):self.window.install_project(copy.deepcopy(self.before))


class MainWindow(QMainWindow):
    job_completed=Signal(object)
    def __init__(self):
        super().__init__();self.setWindowTitle('Signal Lab Desktop[*]');self.setWindowIcon(icon('pulse','#8fe0c2',32));self.setMinimumSize(1080,700);self.resize(1480,930)
        self.settings=QSettings('SignalLab','SignalLabDesktop')
        self.preferences=app_preferences.load(self.settings);self._applying_preferences=False
        self.overview_height=self.preferences['overviewHeight']
        self.theme=saved_theme(self.settings);set_theme(self.theme)
        self.project=preset().model_dump();self.run_record=None;self.dataset_record=None;self.model_record=None;self.sweep_record=None;self.calibration_record=None
        self.selected=('node','stp-a');self.path=None;self.active_job=None;self.started=0;self.compute_buttons=[];self.records={};self.loaded_runs=[];self.model_metrics=None
        self.topology_expanded=False;self.expanded_workspace=None
        self.display_model_id=None;self.current_threshold=None
        self.thread_pool=QThreadPool(self);self.thread_pool.setMaxThreadCount(1)
        self.undo=QUndoStack(self);self.undo.cleanChanged.connect(self.document_clean_changed)
        QApplication.instance().setPalette(application_palette());QApplication.instance().setStyleSheet(stylesheet())
        self.create_shell();self.create_menus();self.install_export_actions();self.load_workspace();self.refresh_library();self.install_project(self.project);self.refresh_results();self.refresh_ml()
        self.apply_preferences(self.preferences,persist=False)
        geometry=self.settings.value('geometry') if self.preferences['preferences/rememberWindow'] else None
        if geometry:self.restoreGeometry(geometry)
        sizes=self.settings.value('topologySplitter') if self.preferences['preferences/rememberWindow'] else None
        if sizes:self.topology_split.setSizes([int(v) for v in sizes])
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(500)
        self.statusBar().showMessage('Готово. Вычисления выполняются локально. F5 — запустить эксперимент.')
        self.undo.setClean();self.setWindowModified(False)
        self.job_completed.connect(self.notify_job_completed);self.show_page(self.preferences['preferences/startPage'])
        QTimer.singleShot(150,self.graph.fit)
        QTimer.singleShot(0,self.apply_overview_height)

    @Slot(bool)
    def document_clean_changed(self,clean):
        # A QObject receiver disconnects before its children are destroyed.
        # A lambda retained by QUndoStack can outlive the MainWindow C++ object.
        self.setWindowModified(not clean)
    def call(self,fn,*args):
        try:return fn(*args)
        except Exception as e:self.error(str(e))
    def error(self,text):
        self.statusBar().showMessage('Ошибка: '+text,10000)
        QMessageBox.warning(self,'Signal Lab',text)
    def action_button(self,text,fn,kind=None,primary=False,compute=False):
        b=button(text,lambda checked=False:self.call(fn),kind,primary)
        if compute:self.compute_buttons.append(b)
        return b
    def create_shell(self):
        central=QWidget();root=QHBoxLayout(central);root.setContentsMargins(0,0,0,0);root.setSpacing(0);self.setCentralWidget(central)
        side=QFrame();side.setObjectName('Sidebar');side.setFixedWidth(205);self.sidebar=side;self.sidebar_collapsed=False
        sl=QVBoxLayout(side);sl.setContentsMargins(16,22,16,20);sl.setSpacing(8);self.sidebar_layout=sl
        self.sidebar_buttons=[];self.sidebar_details=[];self.sidebar_spacers=[]
        self.sidebar_header=QHBoxLayout();self.sidebar_header.setContentsMargins(0,0,0,0);self.sidebar_header.setSpacing(6)
        brand=label('SIGNAL <span style="color:#8fe0c2;font-weight:400">LAB</span>');brand.setStyleSheet('font-size:20px;font-weight:650;letter-spacing:1px');self.sidebar_header.addWidget(brand,1);self.brand_label=brand
        self.sidebar_toggle=self.action_button('',self.toggle_sidebar,'sidebar-collapse');self.sidebar_toggle.setObjectName('SidebarToggle');self.sidebar_toggle.setFixedSize(30,32);self.sidebar_header.addWidget(self.sidebar_toggle);sl.addLayout(self.sidebar_header)
        desktop=label('DESKTOP RESEARCH STUDIO','Eyebrow');desktop.setStyleSheet('font-size:8px;color:#62778d;letter-spacing:1.4px');sl.addWidget(desktop);self.sidebar_details.append(desktop)
        def gap(expanded,collapsed):
            sl.addSpacing(expanded);self.sidebar_spacers.append((sl.itemAt(sl.count()-1).spacerItem(),expanded,collapsed))
        def section(text):
            title=label(text,'Eyebrow');sl.addWidget(title);self.sidebar_details.append(title)
        def sidebar_button(b,title):
            b.setProperty('expandedText',b.text());b.setToolTip(title);b.setAccessibleName(title);self.sidebar_buttons.append(b);sl.addWidget(b)
        gap(24,8);section('  ЛАБОРАТОРИЯ');gap(5,0)
        self.nav_buttons=[]
        nav=[('Топология','topology'),('Эксперименты','experiment'),('Телеметрия','chart'),('ML-детекторы','brain'),('Результаты','history'),('Справка','help'),('Атаки','shield')]
        for i,(text,ico) in enumerate(nav):
            b=button('  '+text,None,ico);b.setObjectName('Nav');b.setCheckable(True);b.setChecked(i==0);b.clicked.connect(lambda checked=False,index=i:self.show_page(index));sidebar_button(b,text);self.nav_buttons.append(b)
        gap(24,16);section('  РАБОЧЕЕ ПРОСТРАНСТВО');gap(5,0)
        for text,fn,ico in [('Открыть проект',self.open_project,'folder'),('Сохранить проект',self.save_project,'save')]:
            b=self.action_button(text,fn,ico);b.setObjectName('Nav');sidebar_button(b,text)
        sl.addStretch();settings_button=button('  Настройки',None,'settings');settings_button.setObjectName('Nav');settings_button.setCheckable(True);settings_button.clicked.connect(lambda:self.show_page(7));sidebar_button(settings_button,'Настройки');self.nav_buttons.append(settings_button)
        offline=label('●  OFFLINE ENGINE   /   v2.11','Muted');offline.setStyleSheet('font-size:9px;color:#668f85;padding-top:14px');sl.addWidget(offline);self.sidebar_details.append(offline)
        root.addWidget(side)
        right=QWidget();self.workspace_right=right;rl=QVBoxLayout(right);rl.setContentsMargins(0,0,0,0);rl.setSpacing(0);root.addWidget(right,1)
        body=QWidget();bl=QVBoxLayout(body);bl.setContentsMargins(13,8,13,10);bl.setSpacing(12);rl.addWidget(body,1)
        self.workspace_split=QSplitter(Qt.Orientation.Vertical);self.workspace_split.setObjectName('WorkspaceSplit');self.workspace_split.setChildrenCollapsible(False);self.workspace_split.setHandleWidth(10);bl.addWidget(self.workspace_split,1)
        self.overview_widget=QWidget();overview=QVBoxLayout(self.overview_widget);overview.setContentsMargins(0,0,0,0);overview.setSpacing(10)
        heading=QHBoxLayout();self.title=label('Топология сети','Title');self.title.setStyleSheet('font-size:22px')
        heading.addWidget(self.title,alignment=Qt.AlignmentFlag.AlignVCenter);heading.addStretch()
        self.run_button=self.action_button('Запустить  F5',self.start_run,'play',True,True);heading.addWidget(self.run_button,alignment=Qt.AlignmentFlag.AlignVCenter);overview.addLayout(heading)
        self.metric_widget=QWidget();metrics=QHBoxLayout(self.metric_widget);metrics.setContentsMargins(0,0,0,0);metrics.setSpacing(12)
        self.metrics=[Metric(title,compact=True) for title in ('УЗЛЫ / КАНАЛЫ','УСПЕШНЫЕ ЗАВЕРШЕНИЯ / s','ЗАДЕРЖКА p95','УСПЕХ ПРОЦЕДУР / ДОСТАВКА')]
        for m in self.metrics:metrics.addWidget(m)
        overview.addWidget(self.metric_widget,1);self.metric_widget.hide();self.workspace_split.addWidget(self.overview_widget)
        self.pages=QStackedWidget();self.workspace_split.addWidget(self.pages);self.workspace_split.setStretchFactor(0,0);self.workspace_split.setStretchFactor(1,1)
        handle=self.workspace_split.handle(1);handle.setToolTip('Потяните, чтобы изменить высоту верхнего блока. Точный размер: Вид → Высота верхнего блока…');handle.setAccessibleName('Изменить высоту верхнего блока')
        grip_layout=QHBoxLayout(handle);grip_layout.setContentsMargins(0,0,0,0);grip=QFrame();grip.setObjectName('OverviewGrip');grip.setFixedSize(44,2);grip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents);grip_layout.addWidget(grip,alignment=Qt.AlignmentFlag.AlignCenter)
        self.workspace_split.splitterMoved.connect(self.overview_resized)
        self.pages.addWidget(self.topology_page());self.pages.addWidget(self.experiments_page());self.pages.addWidget(self.telemetry_page());self.pages.addWidget(self.ml_page());self.pages.addWidget(self.results_page());self.pages.addWidget(self.help_page())
        from .attacks import AttacksPage
        self.attacks_widget=AttacksPage(self);attacks_scroll=QScrollArea();attacks_scroll.setWidgetResizable(True);attacks_scroll.setWidget(self.attacks_widget);self.pages.addWidget(attacks_scroll)
        from .settings_page import SettingsPage
        self.settings_page=SettingsPage(self);self.pages.addWidget(self.settings_page)
        self.job_bar=QFrame();self.job_bar.setObjectName('Toolbar');jl=QHBoxLayout(self.job_bar);jl.setContentsMargins(15,8,15,8);self.job_label=label('');jl.addWidget(self.job_label)
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setFixedWidth(240);self.progress.setTextVisible(False);jl.addWidget(self.progress);self.elapsed=label('0 с','Muted');jl.addWidget(self.elapsed)
        self.cancel_button=self.action_button('Отменить',self.cancel_job,'stop');jl.addWidget(self.cancel_button);self.job_bar.hide();bl.addWidget(self.job_bar)
    def create_menus(self):
        file=self.menuBar().addMenu('Файл');edit=self.menuBar().addMenu('Правка');view=self.menuBar().addMenu('Вид');research_menu=self.menuBar().addMenu('Исследование');help_menu=self.menuBar().addMenu('Справка')
        for title,shortcut,fn in [('Новый гибридный проект','Ctrl+N',lambda:self.new_project('hybrid')),('Открыть…','Ctrl+O',self.open_project),('Сохранить','Ctrl+S',self.save_project),('Сохранить как…','Ctrl+Shift+S',lambda:self.save_project(True)),('Экспорт выбранного прогона…','Ctrl+E',self.export_run)]:
            self.menu_action(file,title,fn,shortcut)
        file.addSeparator();self.menu_action(file,'Папка данных',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(storage.DATA))))
        self.menu_action(file,'Экспорт схемы топологии…',lambda:self.export_visuals('topology'),'Ctrl+Shift+T')
        self.visual_export_action=self.menu_action(file,'Экспорт схем, графиков и таблиц…',self.export_visuals,'Ctrl+Shift+E')
        self.menu_action(file,'Выход',self.close,'Ctrl+Q')
        undo_action=self.undo.createUndoAction(self,'Отменить');undo_action.setShortcut(QKeySequence.StandardKey.Undo);edit.addAction(undo_action)
        redo_action=self.undo.createRedoAction(self,'Повторить');redo_action.setShortcut(QKeySequence.StandardKey.Redo);edit.addAction(redo_action)
        self.menu_action(edit,'Настройки программы…',lambda:self.show_page(7),'Ctrl+,')
        edit.addSeparator();self.menu_action(edit,'Добавить узел',self.add_node,'Ctrl+Shift+N');self.menu_action(edit,'Добавить канал',self.add_link,'Ctrl+L');self.menu_action(edit,'Удалить выбранный объект',self.delete_selected)
        self.draw_link_action=QAction('Создать канал мышью',self);self.draw_link_action.setCheckable(True);self.draw_link_action.setShortcut('Ctrl+Shift+L');self.draw_link_action.toggled.connect(self.toggle_link_drawing);self.graph.connection_mode_changed.connect(self.draw_link_action.setChecked);edit.addAction(self.draw_link_action)
        self.menu_action(view,'Вся топология',self.graph.fit,'Ctrl+0');self.menu_action(view,'Сбросить расположение',self.auto_layout)
        self.expand_topology_action=QAction('Развернуть топологию',self);self.expand_topology_action.setCheckable(True);self.expand_topology_action.setShortcut('F11');self.expand_topology_action.toggled.connect(self.set_topology_expanded);view.addAction(self.expand_topology_action)
        self.sidebar_action=QAction('Свернуть левое меню',self);self.sidebar_action.setCheckable(True);self.sidebar_action.setShortcut('Ctrl+B');self.sidebar_action.toggled.connect(self.set_sidebar_collapsed);view.addAction(self.sidebar_action)
        self.overview_action=self.menu_action(view,'Высота верхнего блока…',self.configure_overview_height)
        self.menu_action(view,'Компактный верхний блок',lambda:self.set_overview_height(OVERVIEW_MIN_HEIGHT))
        self.topology_metrics_action=QAction('Показатели на топологии',self);self.topology_metrics_action.setCheckable(True)
        self.topology_metrics_action.toggled.connect(self.set_topology_metrics);view.addAction(self.topology_metrics_action)
        theme_menu=view.addMenu('Тема');theme_group=QActionGroup(self);theme_group.setExclusive(True);self.theme_actions={}
        for theme_name,title in THEMES.items():
            action=QAction(title,self);action.setCheckable(True);action.setChecked(self.theme==theme_name)
            action.triggered.connect(lambda checked=False,name=theme_name:self.apply_theme(name))
            theme_group.addAction(action);theme_menu.addAction(action);self.theme_actions[theme_name]=action
        self.palette_action=self.menu_action(view,'Палитра оформления…',self.show_palette)
        templates=view.addMenu('Шаблоны топологии')
        for name,key in [('Гибридное ядро','hybrid'),('SS7 / SIGTRAN','ss7'),('Diameter EPC','diameter'),('5G SA / Service-Based Core','5g')]:self.menu_action(templates,name,lambda p=key:self.new_project(p))
        self.menu_action(research_menu,'Запустить эксперимент',self.start_run,'F5');self.menu_action(research_menu,'Отменить / выйти из режима карты',self.escape_action,'Escape');self.menu_action(research_menu,'Сформировать ML-датасет',self.generate_dataset)
        self.menu_action(research_menu,'Конструктор атак',lambda:self.show_page(6),'Ctrl+Shift+A')
        self.menu_action(research_menu,'Проверка достоверности…',self.show_validation)
        self.menu_action(research_menu,'Пошаговый разбор процедуры…',self.show_procedure)
        self.menu_action(research_menu,'Жизненный цикл 5G-сессий…',self.show_session_lifecycle)
        self.menu_action(research_menu,'Кластеры и репликация…',self.show_clusters)
        self.menu_action(research_menu,'Конструктор серий…',self.show_experiments)
        self.menu_action(research_menu,'Длительные прогоны…',self.show_long_runs)
        self.menu_action(research_menu,'Распределённый мониторинг…',self.show_monitoring)
        self.menu_action(research_menu,'5G-стенд…',self.show_testbed)
        self.help_action=self.menu_action(help_menu,'Руководство пользователя',self.show_help,'F1')
        self.menu_action(templates,'5G · мобильность и QoS',lambda:self.new_project('5g-mobile'))
        self.menu_action(templates,'5G · репликация и резерв',lambda:self.new_project('5g-ha'))
        self.menu_action(help_menu,'Основы сигнальных сетей',lambda:self.show_help('Основы сигнальных сетей'))
        self.menu_action(help_menu,'Словарь сокращений',lambda:self.show_help('Словарь сокращений'))
        help_menu.addSeparator();self.menu_action(help_menu,'О модели и ограничениях',self.about);self.menu_action(help_menu,'Горячие клавиши',self.shortcuts)
    def show_palette(self):
        from .palette_dialog import PaletteDialog
        dialog=PaletteDialog(self,self.theme,self.apply_theme)
        try:dialog.exec()
        finally:dialog.deleteLater()
    def show_monitoring(self):
        if not getattr(self,'monitor_dialog',None):
            from .monitor_dialog import MonitorDialog
            self.monitor_dialog=MonitorDialog(self)
        self.monitor_dialog.show();self.monitor_dialog.raise_();self.monitor_dialog.activateWindow()
    def show_testbed(self):
        if not getattr(self,'testbed_dialog',None):
            from .testbed_dialog import TestbedDialog
            self.testbed_dialog=TestbedDialog(self)
        self.testbed_dialog.refresh_history();self.testbed_dialog.show();self.testbed_dialog.raise_();self.testbed_dialog.activateWindow()
    def show_validation(self):
        if not getattr(self,'validation_dialog',None):
            from .validation_dialog import ValidationDialog
            self.validation_dialog=ValidationDialog(self)
        self.validation_dialog.reload_history();self.validation_dialog.show();self.validation_dialog.raise_();self.validation_dialog.activateWindow()
    def show_session_lifecycle(self):
        if not self.run_record:raise ValueError('Сначала выполните эксперимент или выберите сохранённый прогон')
        if not getattr(self,'session_dialog',None):
            from .session_dialog import SessionDialog
            self.session_dialog=SessionDialog(self)
        self.session_dialog.set_run(self.run_record);self.session_dialog.show();self.session_dialog.raise_();self.session_dialog.activateWindow()
    def show_long_runs(self):
        if not getattr(self,'long_run_dialog',None):
            from .long_run_dialog import LongRunDialog
            self.long_run_dialog=LongRunDialog(self)
        self.long_run_dialog.refresh();self.long_run_dialog.show();self.long_run_dialog.raise_();self.long_run_dialog.activateWindow()
    def show_experiments(self,record=None):
        if not getattr(self,'experiment_dialog',None):
            from .experiment_dialog import ExperimentDialog
            self.experiment_dialog=ExperimentDialog(self);self.experiment_dialog.reload_project()
        if record:self.experiment_dialog.set_record(record)
        self.experiment_dialog.show();self.experiment_dialog.raise_();self.experiment_dialog.activateWindow()
    def show_clusters(self):
        if not getattr(self,'cluster_dialog',None):
            from .cluster_dialog import ClusterDialog
            self.cluster_dialog=ClusterDialog(self)
        self.cluster_dialog.reload();self.cluster_dialog.show();self.cluster_dialog.raise_();self.cluster_dialog.activateWindow()

    def show_procedure(self,procedure_id=None):
        if not self.run_record:raise ValueError('Сначала выполните или выберите сохранённый прогон')
        if procedure_id is None:
            index=self.trace_table.currentRow();visible=getattr(self,'visible_traces',[])
            if 0<=index<len(visible):procedure_id=visible[index].get('procedure_id')
        if not getattr(self,'procedure_dialog',None):
            from .procedure_dialog import ProcedureDialog
            self.procedure_dialog=ProcedureDialog(self)
        self.procedure_dialog.set_run(self.run_record,procedure_id);self.procedure_dialog.show();self.procedure_dialog.raise_();self.procedure_dialog.activateWindow()
    def apply_theme(self,name,persist=True):
        set_theme(name);self.theme=name;app=QApplication.instance();app.setPalette(application_palette());app.setStyleSheet(stylesheet())
        self.setWindowIcon(icon('pulse','#8fe0c2',32))
        for w in self.findChildren(QWidget):
            original=w.property('themeOriginalStyle')
            if original is None:
                original=w.styleSheet();w.setProperty('themeOriginalStyle',original)
            if original:w.setStyleSheet(themed_stylesheet(original))
        self.brand_label.setText('SIGNAL <span style="color:'+themed_color('#8fe0c2').name()+';font-weight:400">LAB</span>')
        for b in self.findChildren(QPushButton):
            kind=b.property('iconKind')
            if kind:b.setIcon(icon(kind,'#163c2c' if b.property('primaryIcon') else '#a2b4cb'))
        for widget in self.findChildren(QComboBox):
            if widget.property('nodeKindIcons'):refresh_kind_icons(widget)
        self.graph.refresh_theme()
        if self.expanded_workspace:self.expanded_workspace.refresh_icons()
        if getattr(self,'procedure_dialog',None):self.procedure_dialog.refresh_theme()
        self.help_widget.refresh_theme()
        self.attacks_widget.timeline.update()
        for chart in self.findChildren(Chart):chart.update()
        for key,action in self.theme_actions.items():action.setChecked(key==name)
        if persist:self.settings.setValue('theme',name);self.settings.sync();self.statusBar().showMessage('Тема: '+THEMES[name],6000)
        self.preferences['theme']=name;self.sync_settings_page()
    def sync_settings_page(self):
        if not self._applying_preferences and hasattr(self,'settings_page'):self.settings_page.refresh()
    def apply_preferences(self,values,persist=True):
        checked={key:app_preferences.validate(key,value) for key,value in values.items()}
        if persist:app_preferences.save(self.settings,checked)
        self.preferences.update(checked);self._applying_preferences=True
        try:
            if 'theme' in checked:self.apply_theme(checked['theme'],persist=False)
            if 'sidebarCollapsed' in checked:self.set_sidebar_collapsed(checked['sidebarCollapsed'],persist=False)
            if 'overviewHeight' in checked:self.set_overview_height(checked['overviewHeight'],persist=False)
            if 'topologyMetrics' in checked:self.set_topology_metrics(checked['topologyMetrics'],persist=False)
            if 'preferences/statusBar' in checked:self.statusBar().setVisible(checked['preferences/statusBar'])
            if any(key.startswith('graph/') for key in checked):
                self.graph.configure_view(grid=self.preferences['graph/grid'],link_labels=self.preferences['graph/linkLabels'],pan_speed=self.preferences['graph/panSpeed'],zoom_factor=self.preferences['graph/zoomFactor'])
        finally:self._applying_preferences=False
        self.sync_settings_page()
        if persist:self.settings_page.feedback.setText('Настройки сохранены.')
    def set_topology_metrics(self,visible,persist=True):
        visible=bool(visible);self.topology_metrics_action.blockSignals(True);self.topology_metrics_action.setChecked(visible);self.topology_metrics_action.blockSignals(False)
        self.preferences['topologyMetrics']=visible;self.update_metrics_visibility()
        if persist:self.settings.setValue('topologyMetrics',visible);self.settings.sync()
        self.sync_settings_page()
    @Slot(object)
    def notify_job_completed(self,result):
        if self.preferences['preferences/beepOnSuccess'] and not (isinstance(result,dict) and result.get('ok') is False):QApplication.beep()
    def export_sources(self):
        sources=[]
        charts=[('rate_chart','Монитор · сигнальный трафик','Время, s','msg/s'),('telemetry_chart','Телеметрия · '+FEATURE_NAMES.get(self.telemetry_metric.currentData(),''),'Время, s',FEATURE_NAMES.get(self.telemetry_metric.currentData(),'')),('roc_chart','ROC-кривая','False Positive Rate','True Positive Rate'),('pr_chart','Precision–Recall','Recall','Precision')]
        for key,title,x,y in charts:
            record=self.model_record if key in ('roc_chart','pr_chart') else self.run_record
            sources.append(dict(key=key,title=title,kind='chart',widget=getattr(self,key),x_label=x,y_label=y,metadata=dict(record_id=record.get('id') if record else None)))
        sources.append(dict(key='topology',title='Топология · '+self.project['name'],kind='topology',widget=self.graph,metadata=dict(project_name=self.project['name'])))
        sources.append(dict(key='attacks_table',title='Конструктор атак · этапы',kind='table',widget=self.attacks_widget.steps_table))
        sources.append(dict(key='attacks_report',title='Конструктор атак · отчёт прогона',kind='table',widget=self.attacks_widget.report_table))
        sources.append(dict(key='resource_table',title='БД, кэш и контексты',kind='table',widget=self.resource_table))
        sources.append(dict(key='mobility_table',title='5G · перемещения групп UE',kind='table',widget=self.mobility_table))
        for key,title in [('flows_table','Сигнальные потоки'),('incidents_table','События сценария'),('sweep_table','Параметрическое исследование'),('calibration_table','Калибровка'),('trace_table','Транзакции · текущий фильтр'),('lifecycle_table','Отказы и состояния'),('procedure_table','Процедуры'),('transport_table','Транспорт'),('message_table','Запросы и ответы'),('fiveg_table','5G · NF / сервисы'),('control_table','Управляющий обмен'),('routing_state_table','Представления маршрутизаторов'),('user_plane_table','5G · пользовательские пакеты'),('confusion','Матрица ошибок'),('importance','Важность признаков'),('prediction_table','Применение детектора'),('runs_table','Библиотека прогонов'),('comparison','Сравнение прогонов'),('projects_table','Библиотека проектов'),('series_table','Библиотека серий')]:
            sources.append(dict(key=key,title=title,kind='table',widget=getattr(self,key)))
        return sources
    def install_export_actions(self):
        for source in self.export_sources():
            if source['kind']=='topology':continue
            widget=source['widget'];widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(lambda point,w=widget,key=source['key']:self.export_context_menu(w,point,key))
            widget.setToolTip('Правой кнопкой → Экспорт с оформлением…')
    def export_context_menu(self,widget,point,key):
        menu=QMenu(self);self.menu_action(menu,'Экспорт с оформлением…',lambda:self.export_visuals(key));origin=widget.viewport() if hasattr(widget,'viewport') else widget;menu.exec(origin.mapToGlobal(point))
    def export_visuals(self,key=None):
        from .export_dialog import ExportDialog
        key=key or ('topology','flows_table','telemetry_chart','roc_chart','runs_table','topology','attacks_table')[self.pages.currentIndex()]
        dialog=ExportDialog(self,self.export_sources(),key);dialog.exec();dialog.deleteLater()
    def toggle_sidebar(self):self.set_sidebar_collapsed(not self.sidebar_collapsed)
    def set_sidebar_collapsed(self,collapsed,persist=True):
        collapsed=bool(collapsed);self.sidebar_collapsed=collapsed
        self.sidebar.setFixedWidth(72 if collapsed else 205)
        self.sidebar_layout.setContentsMargins(10 if collapsed else 16,22,10 if collapsed else 16,20)
        self.brand_label.setVisible(not collapsed)
        self.sidebar_header.setAlignment(self.sidebar_toggle,Qt.AlignmentFlag.AlignHCenter if collapsed else Qt.AlignmentFlag.AlignRight)
        for detail in self.sidebar_details:detail.setVisible(not collapsed)
        for spacer,expanded,compact in self.sidebar_spacers:spacer.changeSize(0,compact if collapsed else expanded,QSizePolicy.Policy.Minimum,QSizePolicy.Policy.Fixed)
        for b in self.sidebar_buttons:
            b.setText('' if collapsed else b.property('expandedText'));b.setProperty('collapsed',collapsed)
            b.style().unpolish(b);b.style().polish(b);b.update()
        self.sidebar_layout.invalidate()
        kind='sidebar-expand' if collapsed else 'sidebar-collapse';self.sidebar_toggle.setProperty('iconKind',kind);self.sidebar_toggle.setIcon(icon(kind,'#a2b4cb'))
        title='Развернуть левое меню' if collapsed else 'Свернуть левое меню'
        self.sidebar_toggle.setToolTip(title+' (Ctrl+B)');self.sidebar_toggle.setAccessibleName(title)
        self.sidebar_action.blockSignals(True);self.sidebar_action.setChecked(collapsed);self.sidebar_action.setText(title);self.sidebar_action.blockSignals(False)
        if persist:self.settings.setValue('sidebarCollapsed',collapsed);self.settings.sync()
        self.preferences['sidebarCollapsed']=collapsed;self.sync_settings_page()
    def update_metrics_visibility(self):
        index=self.pages.currentIndex()
        self.metric_widget.setVisible(index==2 or (index==0 and self.topology_metrics_action.isChecked()))
        self.apply_overview_height()
    def apply_overview_height(self):
        metrics=not self.metric_widget.isHidden()
        self.overview_widget.layout().activate()
        minimum=self.overview_widget.minimumSizeHint().height()
        self.overview_widget.setMaximumHeight(480 if metrics else minimum)
        height=max(minimum,self.overview_height) if metrics else minimum
        self.workspace_split.handle(1).setEnabled(metrics)
        self.overview_action.setEnabled(metrics)
        self.workspace_split.setSizes([height,max(1,self.workspace_split.height()-height-self.workspace_split.handleWidth())])
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if getattr(self,'topology_expanded',False):self.expanded_workspace.setGeometry(self.centralWidget().rect())
        if hasattr(self,'overview_action'):QTimer.singleShot(0,self.apply_overview_height)
    def set_overview_height(self,height,persist=True):
        self.overview_height=max(OVERVIEW_MIN_HEIGHT,min(480,int(height)));self.apply_overview_height()
        if persist:self.settings.setValue('overviewHeight',self.overview_height);self.settings.sync()
        self.preferences['overviewHeight']=self.overview_height;self.sync_settings_page()
    def overview_resized(self,position,index):
        if not self.metric_widget.isHidden():self.set_overview_height(self.workspace_split.sizes()[0])
    def configure_overview_height(self):
        d=QDialog(self);d.setWindowTitle('Высота верхнего блока');layout=QVBoxLayout(d)
        layout.addWidget(label('Изменяйте высоту также перетаскиванием границы под показателями.'))
        form=QFormLayout();height=spin(self.overview_height,OVERVIEW_MIN_HEIGHT,480,suffix='px');form.addRow('Высота',height);layout.addLayout(form)
        actions=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.RestoreDefaults)
        actions.button(QDialogButtonBox.StandardButton.RestoreDefaults).setText('Компактно')
        actions.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(lambda:height.setValue(OVERVIEW_MIN_HEIGHT));actions.accepted.connect(d.accept);actions.rejected.connect(d.reject);layout.addWidget(actions)
        if d.exec():self.set_overview_height(height.value())
    def menu_action(self,menu,title,fn,shortcut=None):
        a=QAction(title,self)
        if shortcut:a.setShortcut(shortcut)
        a.triggered.connect(lambda checked=False:self.call(fn));menu.addAction(a);return a
    def topology_page(self):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12)
        self.topology_split=QSplitter(Qt.Orientation.Horizontal)
        canvas,cl,ch=panel('Сетевая карта');self.canvas_layout=cl;cl.setContentsMargins(0,0,0,0);ch.setContentsMargins(16,12,16,0)
        self.map_status=label('Готово','Muted');ch.addWidget(self.map_status)
        ch.addWidget(self.action_button('Экспорт…',lambda:self.export_visuals('topology'),'download'))
        self.expand_topology_button=self.action_button('Развернуть',lambda:self.set_topology_expanded(True),'fit');self.expand_topology_button.setToolTip('Топология на всю рабочую область программы (F11)');ch.addWidget(self.expand_topology_button)
        toolbar=QHBoxLayout();toolbar.setContentsMargins(12,0,12,0)
        toolbar.addWidget(self.action_button('Узел',self.add_node,'plus'));toolbar.addWidget(self.action_button('Канал',self.add_link,'link'))
        self.draw_link_button=button('Соединить',None,'link');self.draw_link_button.setObjectName('LinkMode');self.draw_link_button.setCheckable(True);self.draw_link_button.setToolTip('Создать канал мышью: выберите два узла или перетащите между ними. Ctrl+Shift+L; Esc — отмена.');toolbar.addWidget(self.draw_link_button)
        toolbar.addWidget(self.action_button('Расположить',self.auto_layout,'topology'));toolbar.addStretch();toolbar.addWidget(self.action_button('Монитор',lambda:self.monitor_card.setVisible(not self.monitor_card.isVisible()),'chart'));toolbar.addWidget(self.action_button('Вся сеть',lambda:self.graph.fit(),'fit'))
        cl.addLayout(toolbar);self.graph=NetworkView();self.graph.setMinimumSize(450,250);cl.addWidget(self.graph,1)
        self.draw_link_button.toggled.connect(self.graph.set_connection_mode);self.graph.connection_mode_changed.connect(self.draw_link_button.setChecked)
        footer=label('SS7 / M3UA / Diameter / 5G: NGAP · SBI · PFCP · N3/N6\nКанал: нажмите узел и перетащите появившийся кружок к другому узлу · Колесо: прокрутка · Shift: по горизонтали · Ctrl: масштаб · Зажатое колесо / Пробел: панорама','Muted');footer.setWordWrap(True);footer.setStyleSheet('font-size:9px;padding:0 10px 11px;color:#7389a3');cl.addWidget(footer)
        self.topology_split.addWidget(canvas)
        self.inspector=QScrollArea();self.inspector.setWidgetResizable(True);self.inspector.setMinimumWidth(270);self.inspector.setMaximumWidth(355);self.topology_split.addWidget(self.inspector);self.topology_split.setSizes([900,300]);self.topology_split.setStretchFactor(0,1)
        layout.addWidget(self.topology_split,1)
        graph_card,gc,gh=panel('Монитор эксперимента');graph_card.setMaximumHeight(215);gh.addWidget(label('msg/s  ·  окна создания сообщений','Muted'));self.rate_chart=Chart();gc.addWidget(self.rate_chart);layout.addWidget(graph_card);self.monitor_card=graph_card;graph_card.hide()
        self.graph.selected.connect(self.select);self.graph.node_moved.connect(self.move_node,Qt.ConnectionType.QueuedConnection);self.graph.delete_requested.connect(lambda:self.call(self.delete_selected))
        self.graph.connection_requested.connect(lambda source,target:self.call(self.add_link,source,target),Qt.ConnectionType.QueuedConnection)
        self.graph.connection_hint.connect(lambda text:self.statusBar().showMessage(text,8000))
        return page
    def set_topology_expanded(self,expanded):
        expanded=bool(expanded)
        if expanded==self.topology_expanded:return
        if expanded:
            self.show_page(0)
            if self.expanded_workspace is None:
                from .topology_workspace import ExpandedTopology
                self.expanded_workspace=ExpandedTopology(self)
            self.normal_topology_sizes=self.topology_split.sizes();self.topology_expanded=True
            self.expanded_workspace.open_canvas();self.sidebar.hide();self.workspace_right.hide()
        else:
            self.topology_expanded=False;self.expanded_workspace.close_canvas();self.sidebar.show();self.workspace_right.show();self.apply_overview_height()
        self.expand_topology_action.blockSignals(True);self.expand_topology_action.setChecked(expanded);self.expand_topology_action.blockSignals(False)
        self.graph.setFocus()
    def escape_action(self):
        if self.graph.connection_mode or self.graph.connection_source:self.graph.set_connection_mode(False)
        elif self.topology_expanded:self.set_topology_expanded(False)
        else:self.cancel_job()
    def experiments_page(self):
        outer=QScrollArea();outer.setWidgetResizable(True);content=QWidget();l=QVBoxLayout(content);l.setContentsMargins(0,0,0,0);l.setSpacing(15);outer.setWidget(content)
        def parameter_row(*fields):
            layout=QHBoxLayout();layout.setContentsMargins(0,0,0,0);layout.setSpacing(18)
            for title,control in fields:
                field=QHBoxLayout();field.setContentsMargins(0,0,0,0);field.setSpacing(9)
                caption=label(title,'Muted');caption.setBuddy(control)
                field.addWidget(caption);field.addWidget(control,1);layout.addLayout(field,1)
            return layout
        config,cc,ch=panel('Параметры эксперимента');form=QHBoxLayout();form.setSpacing(15)
        self.project_name=QLineEdit();self.project_name.setMaxLength(100);self.duration=spin(60,10,86400,0,'s');self.seed=spin(42,0,2147483647);self.window_size=spin(5,1,3600,0,'s')
        for title,w in [('Название проекта',self.project_name),('Длительность',self.duration),('Seed',self.seed),('Окно признаков',self.window_size)]:
            v=QVBoxLayout();v.setSpacing(6);caption=label(title,'Muted');caption.setBuddy(w);v.addWidget(caption);v.addWidget(w);form.addLayout(v,2 if w is self.project_name else 1)
        cc.addLayout(form);ch.addWidget(self.action_button('Применить',self.apply_experiment,'settings'));l.addWidget(config)
        self.engine_combo=combo([('procedures','Процедуры · v2'),('messages','Отдельные сообщения · v1')]);self.routing_combo=combo([('shortest','Предпочтительный маршрут'),('loadshare','Распределение по абонентам')]);self.label_combo=combo([('affected','Воздействие на трафик'),('scheduled','Запланированное событие'),('degradation','Наблюдаемое ухудшение')]);self.trace_limit=spin(1500,100,20000)
        cc.addLayout(parameter_row(('Движок',self.engine_combo),('Маршрутизация',self.routing_combo)))
        self.control_plane_check=QCheckBox('Установление ассоциаций и heartbeat');self.knowledge_combo=combo([('distributed','Отдельное представление у каждого узла'),('shared','Общее знание · сравнение со старой моделью')]);self.route_refresh=spin(5000,100,60000,0,'ms')
        cc.addLayout(row(self.control_plane_check,self.knowledge_combo));cc.addLayout(row(label('Обновление представлений','Muted'),self.route_refresh,stretch=True))
        cc.addLayout(parameter_row(('Метка ML',self.label_combo),('Выборка трасс',self.trace_limit)))
        self.journal_sessions=spin(20,0,100);self.journal_events=spin(2000,100,10000)
        self.journal_sessions.setToolTip('Подробная выборка процедур для пошагового разбора. 0 — отключить. Отбор не меняет симуляцию.')
        cc.addLayout(parameter_row(('Подробных сессий',self.journal_sessions),('Событий на сессию',self.journal_events)))
        self.degradation_threshold=spin(200,1,60000,1,'ms');cc.addLayout(row(label('Порог p95 для метки ухудшения','Muted'),self.degradation_threshold,stretch=True))
        flows,fc,fh=panel('Сигнальные потоки','В режиме v2 интенсивность задаёт новые процедуры. Ответы, этапы регистрации и повторы создают дополнительную нагрузку.')
        for text,fn,ico in [('Добавить',lambda:self.edit_flow(False),'plus'),('Изменить',lambda:self.edit_flow(True),'settings'),('Удалить',self.delete_flow,'trash')]:fh.addWidget(self.action_button(text,fn,ico))
        self.flows_table=table(['Источник → назначение','Протокол','Операция','msg/s','Размер, B','Тайм-аут, ms']);self.flows_table.setMinimumHeight(185);self.flows_table.setMaximumHeight(270);self.flows_table.doubleClicked.connect(lambda idx:self.call(self.edit_flow,True));fc.addWidget(self.flows_table);l.addWidget(flows)
        incidents,ic,ih=panel('События сценария','Отказы и изменения нагрузки, задержки и потерь по времени. События действуют на [начало, конец).')
        ih.addWidget(self.action_button('Конструктор атак',lambda:self.show_page(6),'shield'))
        for text,fn,ico in [('Запланировать',lambda:self.edit_incident(False),'plus'),('Изменить',lambda:self.edit_incident(True),'settings'),('Удалить',self.delete_incident,'trash')]:ih.addWidget(self.action_button(text,fn,ico))
        self.incidents_table=table(['Событие','Объект','Начало, s','Длительность, s','Множитель']);self.incidents_table.setMinimumHeight(145);self.incidents_table.setMaximumHeight(230);self.incidents_table.doubleClicked.connect(lambda idx:self.call(self.edit_incident,True));ic.addWidget(self.incidents_table);l.addWidget(incidents)
        sweep,sc,sh=panel('Параметрическое исследование','Независимые повторения с сопоставленными seed. Конструктор добавляет прогрев, несколько параметров, контроль точности и поиск допустимой нагрузки.')
        sh.addWidget(self.action_button('Конструктор серий…',self.show_experiments,'settings'))
        sh.addWidget(self.action_button('Длительные прогоны…',self.show_long_runs,'history'))
        self.sweep_values=QLineEdit('0.5, 1, 1.5, 2');self.sweep_repeats=spin(10,1,1000);sc.addLayout(row(label('Множители нагрузки','Muted'),self.sweep_values,label('Повторов','Muted'),self.sweep_repeats,self.action_button('Запустить серию',self.start_sweep,'experiment',True,True)))
        self.sweep_table=table(['Нагрузка','Повторов','p95 ± CI, ms','Доставка','Throughput']);self.sweep_table.setMinimumHeight(150);self.sweep_table.hide();sc.addWidget(self.sweep_table);l.addWidget(sweep);l.addStretch()
        calibration,ca,cah=panel('Калибровка по измерениям','CSV независимых опытов: run_id, offered_rate и измеренные метрики. Подбор на train; отчёт на отложенных run_id.')
        cah.addWidget(self.action_button('Шаблон CSV',self.calibration_template,'download'));cah.addWidget(self.action_button('Подобрать параметры',self.start_calibration,'experiment',True,True))
        cah.addWidget(self.action_button('Настройки поиска',self.configure_calibration,'settings'))
        self.calibration_info=label('Нужны минимум четыре независимых опыта. Результат содержит исходную и подобранную конфигурации.','Muted');self.calibration_info.setWordWrap(True);ca.addWidget(self.calibration_info)
        self.calibration_combo=combo([]);self.calibration_combo.currentIndexChanged.connect(self.choose_calibration);ca.addWidget(self.calibration_combo)
        self.calibration_table=table(['Выборка','Метрика','Исходная ошибка','После подбора','95% CI ошибки']);self.calibration_table.setMinimumHeight(150);ca.addWidget(self.calibration_table)
        ca.addLayout(row(self.action_button('Применить конфигурацию',self.apply_calibration,'settings'),self.action_button('Экспорт отчёта',self.export_calibration,'download'),stretch=True));l.insertWidget(l.count()-1,calibration)
        validation,vl,vh=panel('Проверка достоверности модели','Измеренная сеть ↔ симулятор: распределения и временные ряды, допуски и границы подтверждённых условий. Настройки модели во время проверки не подбираются.')
        vh.addWidget(self.action_button('Открыть сравнение',self.show_validation,'chart'));l.insertWidget(l.count()-1,validation)
        return outer
    def telemetry_page(self):
        page=QWidget();l=QVBoxLayout(page);l.setContentsMargins(0,0,0,0)
        split=QSplitter(Qt.Orientation.Vertical);l.addWidget(split)
        chart,cc,ch=panel('Измерения по времени');self.telemetry_metric=combo([(x,FEATURE_NAMES[x]) for x in FEATURE_NAMES],'latency_p95');ch.addWidget(self.telemetry_metric);ch.addWidget(self.action_button('Экспорт…',lambda:self.export_visuals('telemetry_chart'),'download'));ch.addWidget(self.action_button('Окна CSV',self.export_windows,'download'));self.telemetry_chart=Chart();self.telemetry_chart.setMinimumHeight(200);cc.addWidget(self.telemetry_chart);split.addWidget(chart)
        log,lc,lh=panel('Журнал прохождения сообщений');self.trace_search=QLineEdit();self.trace_search.setPlaceholderText('Поиск по узлу, операции или статусу');lh.addWidget(self.trace_search);self.trace_filter=combo([('all','Все статусы'),('delivered','Доставлено'),('failed','Не доставлено')]);lh.addWidget(self.trace_filter)
        lh.addWidget(self.action_button('Разобрать процедуру',self.show_procedure,'chart'))
        lh.addWidget(self.action_button('PDU-сессии',self.show_session_lifecycle,'topology'))
        trace_page=QWidget();trace_layout=QVBoxLayout(trace_page);trace_layout.setContentsMargins(0,8,0,0)
        self.trace_table=table(['Время, s','Протокол','Операция','Маршрут','Задержка, ms','Статус','Процедура / этапы','Повторы']);trace_layout.addWidget(self.trace_table,1);self.trace_note=label('Равномерная выборка завершённых процедур. Агрегаты включают весь прогон.','Muted');self.trace_note.setWordWrap(True);trace_layout.addWidget(self.trace_note)
        self.details_tabs=QTabWidget();self.details_tabs.addTab(trace_page,'Транзакции');lc.addWidget(self.details_tabs,1)
        self.lifecycle_table=table(['Время, s','Объект','Состояние']);self.details_tabs.addTab(self.lifecycle_table,'Отказы и состояния')
        self.procedure_table=table(['Поток','Успех','p95, ms','p99, ms','Повторы']);self.details_tabs.addTab(self.procedure_table,'Процедуры')
        self.transport_table=table(['Канал / направление','Пакеты','Байты','Потери','Повторы','p99, ms']);self.details_tabs.addTab(self.transport_table,'Транспорт')
        self.message_table=table(['Время, s','Transaction ID','Роль','Операция','Источник → адресат','Протокол']);self.details_tabs.addTab(self.message_table,'Запросы и ответы')
        self.fiveg_table=table(['Сервис / операция','Запросы','Успех','Ошибки','Тайм-ауты']);self.details_tabs.addTab(self.fiveg_table,'5G · NF / сервисы')
        self.control_table=table(['Время, s','Канал / протокол','Операция','Источник → адресат','Размер, B']);self.details_tabs.addTab(self.control_table,'Управляющий обмен')
        self.routing_state_table=table(['Время, s','Наблюдатель','Ресурс','Состояние','Источник знания']);self.details_tabs.addTab(self.routing_state_table,'Доступность у узлов')
        data_page=QWidget();data_layout=QVBoxLayout(data_page);data_layout.setContentsMargins(0,8,0,0);self.user_plane_info=label('Пользовательский трафик проверяется отдельно от успешного PDU.','Muted');self.user_plane_info.setWordWrap(True);data_layout.addWidget(self.user_plane_info)
        self.user_plane_table=table(['Время, s','Сессия / пакет','UPF → DN','Статус','RTT, ms','Размер, B']);data_layout.addWidget(self.user_plane_table);self.details_tabs.addTab(data_page,'5G · N3 / N6')
        self.resource_table=table(['Узел','БД: запросы / завершения','Кэш: попадания / промахи','Очередь / соединения max','Тайм-аут / отказ','Контексты','Кэш данных / ответов']);self.details_tabs.addTab(self.resource_table,'БД и контексты')
        self.mobility_table=table(['Начало / конец, s','Поток','gNB','AMF','UPF','Результат']);self.details_tabs.addTab(self.mobility_table,'5G · мобильность')
        split.addWidget(log);split.setSizes([270,350])
        self.telemetry_metric.currentIndexChanged.connect(self.refresh_telemetry_chart);self.trace_filter.currentIndexChanged.connect(self.refresh_traces);self.trace_search.textChanged.connect(self.refresh_traces)
        self.trace_table.doubleClicked.connect(lambda index:self.call(self.show_procedure))
        return page
    def ml_page(self):
        outer=QScrollArea();outer.setWidgetResizable(True);content=QWidget();l=QVBoxLayout(content);l.setContentsMargins(0,0,0,0);l.setSpacing(15);outer.setWidget(content)
        data,dl,dh=panel('01  /  Датасет телеметрии','Разделение по независимым run_id: окна одного прогона не пересекаются между train, validation и test.')
        self.dataset_combo=combo([]);self.dataset_combo.currentIndexChanged.connect(self.choose_dataset);self.dataset_runs=spin(12,6,24);dh.addWidget(self.action_button('Импорт CSV',self.import_dataset,'folder'));dl.addLayout(row(self.dataset_combo,label('Прогонов','Muted'),self.dataset_runs,self.action_button('Сформировать',self.generate_dataset,'plus',True,True),self.action_button('CSV',self.export_dataset,'download')))
        self.feature_set_combo=combo([(key,spec['title']) for key,spec in FEATURE_SETS.items()]);self.feature_set_combo.setCurrentIndex(4)
        self.topology_variants=QCheckBox('Три варианта связности');self.topology_variants.setChecked(True)
        self.topology_variants.setToolTip('Исходная схема и две схемы с дополнительными резервными каналами. Положения и названия узлов не создают новую топологию.')
        dl.addLayout(row(label('Набор признаков','Muted'),self.feature_set_combo,self.topology_variants,stretch=True))
        self.dataset_info=label('Нет датасета. Сформируйте выборку или импортируйте телеметрию CSV.','Muted');self.dataset_info.setWordWrap(True);dl.addWidget(self.dataset_info);l.addWidget(data)
        model,mlayout,mh=panel('02  /  Обучение детектора')
        self.algorithm=combo([(key,value['title']) for key,value in ml.ALGORITHMS.items()]);self.trees=spin(120,10,2000);self.train_seed=spin(42,0,2147483647);self.contamination=spin(.1,.001,.5,3)
        self.threshold_mode=combo([('validation','По F1 validation'),('contamination','По contamination')]);self.algorithm.currentIndexChanged.connect(self.algorithm_changed)
        config=QHBoxLayout()
        for title,w in [('Алгоритм',self.algorithm),('Деревьев / итераций',self.trees),('Seed',self.train_seed),('Доля выбросов',self.contamination),('Порог',self.threshold_mode)]:
            v=QVBoxLayout();v.addWidget(label(title,'Muted'));v.addWidget(w);config.addLayout(v,2 if w in (self.algorithm,self.threshold_mode) else 1)
        mlayout.addLayout(config);self.algorithm_note=label('','Muted');self.algorithm_note.setWordWrap(True);mlayout.addWidget(self.algorithm_note)
        self.model_options=ModelOptions(exclude=('n_estimators',));mlayout.addWidget(self.model_options)
        mh.addWidget(self.action_button('Обучить модель',self.train_model,'brain',True,True));self.algorithm_changed();l.addWidget(model)
        self.evaluation_mode=combo([('run','Независимые прогоны'),('topology','Новая топология'),('fault','Новый тип отказа')]);self.holdout_value=combo([])
        self.evaluation_mode.currentIndexChanged.connect(self.refresh_holdout)
        mlayout.addLayout(row(label('Итоговая проверка','Muted'),self.evaluation_mode,label('Только в test','Muted'),self.holdout_value))
        evaluation,el,eh=panel('03  /  Качество и настройка','Метрики ниже относятся к независимой test-части. После ручного подбора порога по test нужна новая итоговая выборка.')
        self.model_combo=combo([]);self.model_combo.currentIndexChanged.connect(self.choose_model);eh.addWidget(self.model_combo);eh.addWidget(self.action_button('Отчёт JSON',self.export_model,'download'))
        metrics=QHBoxLayout();self.ml_metrics=[Metric(t) for t in ['F1-SCORE','PRECISION','RECALL','ROC AUC']]
        for m in self.ml_metrics:metrics.addWidget(m)
        el.addLayout(metrics)
        detail=QHBoxLayout();self.confusion=table(['Истина / прогноз','Норма','Аномалия']);self.confusion.setMaximumHeight(130);self.confusion.setMinimumWidth(280);detail.addWidget(self.confusion)
        self.importance=table(['Признак','Важность']);self.importance.setMaximumHeight(220);detail.addWidget(self.importance,1);el.addLayout(detail)
        tr=QHBoxLayout();tr.addWidget(label('Порог срабатывания','Muted'));self.threshold=QSlider(Qt.Orientation.Horizontal);self.threshold.setRange(0,1000);self.threshold.setValue(500);self.threshold.setAccessibleName('Порог детектора');tr.addWidget(self.threshold,1)
        self.threshold_input=spin(.5,-1e12,1e12,8);self.threshold_input.setAccessibleName('Точный порог детектора');self.threshold_input.setMaximumWidth(220);tr.addWidget(self.threshold_input)
        self.threshold_label=label('0,500','Badge');tr.addWidget(self.threshold_label);el.addLayout(tr)
        self.threshold_range=(0.,1.);self.threshold_input.valueChanged.connect(self.exact_threshold_changed)
        self.threshold.valueChanged.connect(self.threshold_changed);self.model_info=label('Модель ещё не обучена.','Muted');self.model_info.setWordWrap(True);el.addWidget(self.model_info)
        self.operational_info=label('Время обнаружения и ложные тревоги появятся после обучения.','Muted');self.operational_info.setWordWrap(True);el.addWidget(self.operational_info)
        curves=QHBoxLayout();self.roc_chart=Chart();self.roc_chart.setMinimumHeight(175);self.pr_chart=Chart();self.pr_chart.setMinimumHeight(175);curves.addWidget(self.roc_chart);curves.addWidget(self.pr_chart);el.addLayout(curves);l.addWidget(evaluation)
        inference,il,ih=panel('Проверить модель на другом прогоне');self.inference_run=combo([]);ih.addWidget(self.inference_run);ih.addWidget(self.action_button('Применить',self.predict,'play',True,True));self.prediction_table=table(['Время, s','Оценка','Детектор','Метка сценария']);self.prediction_table.setMaximumHeight(200);il.addWidget(self.prediction_table);self.prediction_info=label('Выберите сохранённую модель и завершённый эксперимент.','Muted');il.addWidget(self.prediction_info);l.addWidget(inference);l.addStretch()
        return outer
    def results_page(self):
        page=QWidget();l=QVBoxLayout(page);l.setContentsMargins(0,0,0,0);l.setSpacing(14)
        tabs=QTabWidget();l.addWidget(tabs,1)
        runs=QWidget();rl=QVBoxLayout(runs);rl.setContentsMargins(0,12,0,0)
        rl.addLayout(row(self.action_button('Открыть прогон',self.load_selected_run,'folder'),self.action_button('Восстановить конфигурацию',self.restore_run,'undo'),self.action_button('Сравнить выбранные',self.compare_runs,'chart'),self.action_button('Экспорт JSON',self.export_run,'download'),stretch=True))
        self.runs_table=table(['Дата','Эксперимент','Seed / ID','Сообщения','Доставка','p95, ms']);self.runs_table.doubleClicked.connect(lambda idx:self.call(self.load_selected_run));rl.addWidget(self.runs_table,1)
        self.comparison=table(['Прогон','Seed','Нагрузка, msg/s','Доставка','p95, ms','Throughput']);self.comparison.setMaximumHeight(220);self.comparison.hide();rl.addWidget(self.comparison);tabs.addTab(runs,'Прогоны')
        projects=QWidget();pl=QVBoxLayout(projects);pl.setContentsMargins(0,12,0,0);pl.addLayout(row(self.action_button('Открыть из библиотеки',self.open_library_project,'folder'),self.action_button('Сохранить в библиотеку',self.save_library_project,'save'),stretch=True));self.projects_table=table(['Дата','Проект','ID']);pl.addWidget(self.projects_table);tabs.addTab(projects,'Проекты')
        series=QWidget();sel=QVBoxLayout(series);sel.setContentsMargins(0,12,0,0);sel.addLayout(row(self.action_button('Открыть серию',self.open_sweep,'folder'),stretch=True));self.series_table=table(['Дата','Исследование','Точек','Повторов','ID']);sel.addWidget(self.series_table);tabs.addTab(series,'Серии')
        return page

    def help_page(self):
        from .help_page import HelpPage
        self.help_widget=HelpPage();return self.help_widget
    def show_help(self,section=None):
        self.show_page(5);self.help_widget.open_section(section);self.help_widget.search.setFocus()
    def show_page(self,index):
        if self.topology_expanded and index!=0:self.set_topology_expanded(False)
        self.pages.setCurrentIndex(index)
        for i,b in enumerate(self.nav_buttons):b.setChecked(i==index)
        titles=['Топология сети','Конструктор экспериментов','Телеметрия сети','Мастерская детекторов','Результаты исследований','Справка и руководство','Конструктор атак','Настройки программы']
        self.run_button.setVisible(index!=7)
        self.title.setText(titles[index]);self.update_metrics_visibility()
        if index==0 and self.preferences['preferences/fitOnTopology']:QTimer.singleShot(0,self.graph.fit)
        if index==7:self.sync_settings_page()
    def load_workspace(self):
        if not self.preferences['preferences/restoreWorkspace']:return
        draft=storage.DATA/'desktop-workspace.json'
        if draft.exists():
            try:self.project=Project.model_validate_json(draft.read_text(encoding='utf-8')).model_dump()
            except Exception:self.statusBar().showMessage('Черновик не прошёл проверку; загружен шаблон',10000)
        else:
            projects=storage.listing('project')
            if projects:
                self.project=Project.model_validate(storage.get('project',projects[0]['id'])['project']).model_dump()
    def save_workspace(self):
        target=storage.DATA/'desktop-workspace.json';temp=target.with_suffix('.tmp');temp.write_text(json.dumps(self.project,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(target)
    def install_project(self,p):
        self.project=Project.model_validate(p).model_dump()
        available={n['id'] for n in p['nodes']} if self.selected[0]=='node' else {e['id'] for e in p['links']}
        if self.selected[1] not in available:self.selected=('node',p['nodes'][0]['id'])
        stats=self.run_record if self.run_record and fingerprint(p)==fingerprint(self.run_record['project']) else None
        self.graph.set_project(self.project,self.selected,stats);self.fill_inspector();self.refresh_experiment();self.refresh_metrics();self.attacks_widget.refresh(self.project);self.save_workspace()
    def mutate(self,title,fn):
        before=copy.deepcopy(self.project);after=copy.deepcopy(before);fn(after);after=Project.model_validate(after).model_dump()
        if before!=after:self.undo.push(Snapshot(self,before,after,title));self.setWindowModified(not self.undo.isClean())
    def select(self,kind,rid):
        self.selected=(kind,rid);self.fill_inspector()
    def fill_inspector(self):
        frame,layout,head=panel('Инспектор объекта')
        if self.topology_expanded:head.itemAt(0).widget().hide()
        kind,rid=self.selected
        item=next((x for x in self.project['nodes' if kind=='node' else 'links'] if x['id']==rid),None)
        self.inspector_fields={}
        if item is None:layout.addWidget(label('Выберите узел или канал на карте.','Muted'));layout.addStretch();self.inspector.setWidget(frame);return
        layout.addWidget(label(item.get('name',f'{item.get("source","")} ↔ {item.get("target","")}'),'PanelTitle'));layout.addWidget(label(item['id'],'Muted'))
        form=QFormLayout();form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows);form.setVerticalSpacing(11);layout.addLayout(form)
        def add(key,title,w):w.setAccessibleName(title);form.addRow(title,w);self.inspector_fields[key]=w
        if kind=='node':
            w=QLineEdit(item['name']);w.setMaxLength(80);add('name','Название узла',w);add('kind','Тип элемента',node_kind_combo(item['kind']))
            add('capacity','Производительность',spin(item['capacity'],1,100000,1,'msg/s'));add('processing_ms','Время обработки',spin(item['processing_ms'],0,1000,2,'ms'));add('queue_limit','Лимит очереди',spin(item['queue_limit'],0,10000,0,'msg'))
            add('workers','Параллельные обработчики',spin(item['workers'],1,128))
            if item['kind'] in ('gNB','AMF','SMF','UPF','AUSF','UDM','UDR','PCF','NRF','NSSF','SCP5G'):
                add('plmn','PLMN',QLineEdit(item['plmn']));add('nf_status','Статус NF',combo(['REGISTERED','SUSPENDED'],item['nf_status']));add('host','Endpoint NF',QLineEdit(item['host']))
            else:add('point_code','Point code',QLineEdit(item['point_code']));add('realm','Realm',QLineEdit(item['realm']))
        else:
            add('protocol','Транспорт / интерфейс',combo(LINK_PROTOCOLS,item['protocol']))
            for key,title,lo,hi,d,unit in [('latency_ms','Задержка',0,5000,2,'ms'),('jitter_ms','Джиттер σ',0,1000,2,'ms'),('bandwidth_mbps','Полоса',.001,100000,3,'Mbps'),('loss','Вероятность потери',0,1,4,'')]:add(key,title,spin(item[key],lo,hi,d,unit))
        enabled=QCheckBox('Объект включён');enabled.setChecked(item['enabled']);layout.addWidget(enabled);self.inspector_fields['enabled']=enabled
        layout.addWidget(self.action_button('Применить параметры',self.apply_inspector,'settings',True))
        layout.addWidget(self.action_button('Расширенные параметры…',self.edit_selected,'settings'))
        note=label('Изменения применяются к новым прогонам. История измерений сохраняет свою конфигурацию.','Muted');note.setWordWrap(True);note.setStyleSheet('font-size:10px;padding-top:6px');layout.addWidget(note)
        layout.addStretch();delete=self.action_button('Удалить объект',self.delete_selected,'trash');delete.setObjectName('Danger');layout.addWidget(delete);self.inspector.setWidget(frame)
    def apply_inspector(self):
        kind,rid=self.selected;values={}
        for k,w in self.inspector_fields.items():
            values[k]=w.isChecked() if isinstance(w,QCheckBox) else w.text().strip() if isinstance(w,QLineEdit) else w.currentData() if isinstance(w,QComboBox) else w.value()
        if kind=='node' and not values['name']:raise ValueError('Введите название узла')
        def change(p):next(x for x in p['nodes' if kind=='node' else 'links'] if x['id']==rid).update(values)
        self.mutate('Изменить параметры объекта',change)
    def edit_selected(self):
        kind,rid=self.selected;item=next(x for x in self.project['nodes' if kind=='node' else 'links'] if x['id']==rid)
        d=node_editor(self,item) if kind=='node' else link_editor(self,self.project,item)
        if d.exec()!=QDialog.DialogCode.Accepted:return
        values=d.values()
        self.mutate('Расширенные параметры',lambda p:next(x for x in p['nodes' if kind=='node' else 'links'] if x['id']==rid).update(values))
    def move_node(self,rid,x,y):
        self.call(self.mutate,'Переместить узел',lambda p:next(n for n in p['nodes'] if n['id']==rid).update(x=x,y=y))
    def add_node(self):
        d=node_editor(self)
        if d.exec()!=QDialog.DialogCode.Accepted:return
        v=d.values()
        if not v['name']:raise ValueError('Введите название')
        rid='node-'+uuid.uuid4().hex[:7];self.selected=('node',rid)
        if v['point_code']=='1-1-1':v['point_code']=f'2-20-{len(self.project["nodes"])+1}'
        self.mutate('Добавить узел',lambda p:p['nodes'].append(dict(id=rid,x=600,y=300,enabled=True,**v)))
    def quick_add_node(self,kind):
        if kind not in NODE_KINDS:raise ValueError('Неизвестный тип узла')
        number=1;names={n['name'] for n in self.project['nodes']}
        while f'{kind} {number}' in names:number+=1
        center=self.graph.mapToScene(self.graph.viewport().rect().center());position=None
        for radius in range(25):
            offsets=[(x,y) for y in range(-radius,radius+1) for x in range(-radius,radius+1) if max(abs(x),abs(y))==radius]
            for dx,dy in offsets:
                x=max(100,min(19800,center.x()+dx*190));y=max(100,min(19800,center.y()+dy*125))
                if all(abs(n['x']-x)>=180 or abs(n['y']-y)>=115 for n in self.project['nodes']):position=(x,y);break
            if position is not None:break
        if position is None:raise ValueError('Нет свободного места в видимой области. Переместите карту.')
        rid='node-'+uuid.uuid4().hex[:7]
        code=1;used_codes={n['point_code'] for n in self.project['nodes']}
        while f'2-20-{code}' in used_codes:code+=1
        node=Node(id=rid,name=f'{kind} {number}',kind=kind,x=position[0],y=position[1],point_code=f'2-20-{code}').model_dump()
        self.graph.set_connection_mode(False);self.graph.fit_mode=False;self.selected=('node',rid);self.mutate('Добавить '+kind,lambda p:p['nodes'].append(node))
        self.graph.ensureVisible(self.graph.node_items[rid],65,65)
        self.statusBar().showMessage(f'{node["name"]} добавлен. Параметры — в инспекторе; Ctrl+Z — отменить.',5000)
    def toggle_link_drawing(self,enabled):
        if enabled:self.show_page(0)
        self.graph.set_connection_mode(enabled)
    def add_link(self,source=None,target=None):
        initial=None
        if source is not None and target is not None:
            nodes={n['id']:n for n in self.project['nodes']}
            if source not in nodes or target not in nodes or source==target:return
            kinds=frozenset((nodes[source]['kind'],nodes[target]['kind']))
            interfaces={frozenset(('gNB','AMF')):'NGAP',frozenset(('SMF','UPF')):'PFCP',frozenset(('gNB','UPF')):'GTPU',frozenset(('UPF','DN')):'IP'}
            sbi={'AMF','SMF','AUSF','UDM','UDR','PCF','NRF','NSSF','SCP5G'}
            protocol=interfaces.get(kinds,'SBI' if kinds<=sbi else 'Diameter' if kinds<={'MME','DRA','HSS','SCP'} else 'M3UA')
            initial=dict(id='edit',source=source,target=target,protocol=protocol)
        d=link_editor(self,self.project,initial)
        if d.exec()!=QDialog.DialogCode.Accepted:self.statusBar().showMessage('Добавление канала отменено.',5000);return
        v=d.values()
        if v['source']==v['target']:raise ValueError('Выберите разные узлы')
        if any({l['source'],l['target']}=={v['source'],v['target']} and l['protocol']==v['protocol'] for l in self.project['links']) and not v.get('path_group'):raise ValueError('Для параллельного канала укажите группу резервных путей во вкладке «Транспорт»')
        rid='link-'+uuid.uuid4().hex[:7];self.selected=('link',rid);self.mutate('Добавить канал',lambda p:p['links'].append(dict(id=rid,enabled=True,**v)))
        self.statusBar().showMessage('Канал добавлен. Ctrl+Z — отменить.',8000)
    def delete_selected(self):
        kind,rid=self.selected
        if not kind:return
        if kind=='node' and any(rid in c['members'] for c in self.project.get('clusters',[])):raise ValueError('Узел входит в кластер. Сначала измените его состав в меню «Исследование → Кластеры и репликация».')
        if kind=='node' and any(rid in (m['gnb'],m['amf'],m['upf']) for f in self.project['flows'] for m in f['mobility']):raise ValueError('Узел используется расписанием мобильности. Сначала измените цель перемещения в редакторе потока.')
        removed_links={l['id'] for l in self.project['links'] if rid in (l['source'],l['target'])} if kind=='node' else {rid}
        if any((s['effect'] in NODE_EFFECTS and kind=='node' and s['target']==rid) or (s['effect'] not in NODE_EFFECTS and s['effect']!='surge' and s['target'] in removed_links) for a in self.project.get('attacks',[]) for s in a['steps']):raise ValueError('Объект используется сценарием атаки. Сначала измените цель или удалите сценарий во вкладке «Атаки».')
        if kind=='node' and any(f['source']==rid or f['target']==rid for f in self.project['flows']):raise ValueError('Узел используется потоком. Сначала измените или удалите поток в разделе «Эксперименты».')
        def change(p):
            if kind=='node':
                ids={l['id'] for l in p['links'] if rid in (l['source'],l['target'])};p['nodes']=[n for n in p['nodes'] if n['id']!=rid];p['links']=[l for l in p['links'] if l['id'] not in ids];p['incidents']=[e for e in p['incidents'] if not (e['kind'] in NODE_EFFECTS and e['target']==rid) and not (e['kind'] in ('link_down','latency','loss','loss_absolute') and e['target'] in ids)]
                for n in p['nodes']:
                    n['routing_table']={k:[x for x in v if x!=rid] for k,v in n['routing_table'].items() if any(x!=rid for x in v)}
                    n['gtt']={k:v for k,v in n['gtt'].items() if v in {x['point_code'] for x in p['nodes']}}
                for f in p['flows']:
                    f['nf_bindings']={k:v for k,v in f['nf_bindings'].items() if v!=rid}
                    if f.get('data_network')==rid:f['data_network']=''
            else:p['links']=[l for l in p['links'] if l['id']!=rid];p['incidents']=[e for e in p['incidents'] if not(e['kind'] in ('link_down','latency','loss','loss_absolute') and e['target']==rid)]
        self.mutate('Удалить объект',change)
    def auto_layout(self):
        def change(p):
            import math
            columns=max(3,min(8,math.ceil(math.sqrt(len(p['nodes'])*1.5))))
            for i,n in enumerate(p['nodes']):n.update(x=140+(i%columns)*280,y=110+(i//columns)*180)
        self.mutate('Расположить узлы',change);self.graph.fit()
    def refresh_experiment(self):
        p=self.project;self.project_name.setText(p['name']);self.duration.setValue(p['duration']);self.seed.setValue(p['seed']);self.window_size.setValue(p['window'])
        for w,key in [(self.engine_combo,'engine'),(self.routing_combo,'routing_mode'),(self.label_combo,'label_policy')]:w.setCurrentIndex(w.findData(p[key]))
        self.trace_limit.setValue(p['trace_limit'])
        self.journal_sessions.setValue(p['journal_sessions']);self.journal_events.setValue(p['journal_events'])
        self.degradation_threshold.setValue(p['degradation_latency_ms'])
        self.control_plane_check.setChecked(p['control_plane']);self.knowledge_combo.setCurrentIndex(self.knowledge_combo.findData(p['routing_knowledge']));self.route_refresh.setValue(p['route_refresh_ms'])
        populate(self.flows_table,[[f['source']+' → '+f['target'],f['protocol'],f['operation'],number(f['rate']),f['message_bytes'],number(f['timeout_ms'])] for f in p['flows']])
        populate(self.incidents_table,[[NAMES[e['kind']],e['target'],number(e['start']),number(e['duration']),'—' if e['kind'] in ('node_down','link_down') else number(e['magnitude'])] for e in p['incidents']])
    def apply_experiment(self):
        v=dict(name=self.project_name.text().strip(),duration=self.duration.value(),seed=self.seed.value(),window=self.window_size.value(),engine=self.engine_combo.currentData(),routing_mode=self.routing_combo.currentData(),label_policy=self.label_combo.currentData(),trace_limit=self.trace_limit.value(),degradation_latency_ms=self.degradation_threshold.value())
        if not v['name']:raise ValueError('Введите название проекта')
        v.update(journal_sessions=self.journal_sessions.value(),journal_events=self.journal_events.value(),journal_procedure_id=None)
        v.update(control_plane=self.control_plane_check.isChecked(),routing_knowledge=self.knowledge_combo.currentData(),route_refresh_ms=self.route_refresh.value())
        self.mutate('Изменить эксперимент',lambda p:p.update(v))
    def edit_flow(self,existing=False):
        idx=self.flows_table.currentRow()
        if existing and idx<0:raise ValueError('Выберите поток в таблице')
        flow=self.project['flows'][idx] if existing else None;d=flow_editor(self,self.project,flow)
        if d.exec()!=QDialog.DialogCode.Accepted:return
        v=d.values()
        if v['operation'].startswith('Diameter')!=(v['protocol']=='Diameter'):raise ValueError('Операция должна соответствовать домену протокола')
        def change(p):
            if existing:p['flows'][idx].update(v)
            else:p['flows'].append(dict(id='flow-'+uuid.uuid4().hex[:7],**v))
        self.mutate('Изменить поток' if existing else 'Добавить поток',change)
    def delete_flow(self):
        idx=self.flows_table.currentRow()
        if idx<0:raise ValueError('Выберите поток')
        if any(s['effect']=='surge' and s['target']==self.project['flows'][idx]['id'] for a in self.project.get('attacks',[]) for s in a['steps']):raise ValueError('Поток используется сценарием атаки. Сначала измените цель во вкладке «Атаки».')
        def change(p):
            f=p['flows'].pop(idx);p['incidents']=[e for e in p['incidents'] if not(e['kind']=='surge' and e['target']==f['id'])]
            for node in p['nodes']:node['upf_rules']=[r for r in node['upf_rules'] if r['flow_id']!=f['id']]
        self.mutate('Удалить поток',change)
    def edit_incident(self,existing=False):
        idx=self.incidents_table.currentRow()
        if existing and idx<0:raise ValueError('Выберите событие')
        d=incident_editor(self,self.project,self.project['incidents'][idx] if existing else None)
        if d.exec()!=QDialog.DialogCode.Accepted:return
        def change(p):
            if existing:p['incidents'][idx]=d.values()
            else:p['incidents'].append(d.values())
        self.mutate('Изменить событие' if existing else 'Добавить событие',change)
    def delete_incident(self):
        idx=self.incidents_table.currentRow()
        if idx<0:raise ValueError('Выберите событие')
        self.mutate('Удалить событие',lambda p:p['incidents'].pop(idx))

    def start_job(self,title,fn,done,result_on_cancel=False):
        if self.active_job:raise ValueError('Дождитесь завершения текущего вычисления или отмените его')
        job=Job(fn,result_on_cancel);self.active_job=job;self.started=time.monotonic();self.progress.setRange(0,100);self.progress.setValue(0);self.job_label.setText(title);self.job_bar.show();self.cancel_button.setEnabled(True)
        for b in self.compute_buttons:b.setEnabled(False)
        job.signals.progress.connect(self.progress.setValue)
        job.signals.result.connect(lambda r:self.call(done,r));job.signals.result.connect(self.job_completed)
        job.signals.failed.connect(self.error);job.signals.cancelled.connect(lambda:self.statusBar().showMessage('Вычисление отменено. Отдельные прогоны сохранены, если это включено в настройках серии.',10000));job.signals.finished.connect(self.finish_job)
        self.thread_pool.start(job)
    def tick(self):
        if self.active_job:self.elapsed.setText(f'{int(time.monotonic()-self.started)} с')
    def cancel_job(self):
        if self.active_job:self.active_job.cancel();self.cancel_button.setEnabled(False);self.job_label.setText('Отмена… ожидаю безопасной точки завершения')
    def finish_job(self):
        self.active_job=None;self.job_bar.hide()
        for b in self.compute_buttons:b.setEnabled(True)
        self.call(self.refresh_library);self.call(self.refresh_results)
        if getattr(self,'close_after_long_job',False):
            self.close_after_long_job=False
            if getattr(self,'long_close_ready',False):QTimer.singleShot(0,self.close)
    def start_run(self):
        if self.project['duration']>600:
            self.show_long_runs();self.long_run_dialog.load_project();self.long_run_dialog.start_new();return
        p=copy.deepcopy(self.project)
        def done(r):self.run_record=r;self.refresh_measurements();self.statusBar().showMessage(f'Прогон завершён: {r["summary"]["sent"]:,} сообщений · p95 {r["summary"]["latency_p95"]:.1f} ms',12000)
        self.start_job('Расчёт дискретных событий',lambda progress,cancel:research.run(p,progress,cancel),done)
    def start_sweep(self):
        values=[float(v.strip()) for v in self.sweep_values.text().split(',')];reps=self.sweep_repeats.value();p=copy.deepcopy(self.project)
        self.start_job('Серия экспериментов по нагрузке',lambda progress,cancel:research.sweep(p,values,reps,progress,cancel),self.show_sweep)
    def show_sweep(self,r):
        if r.get('contract')=='experiments-v1':
            self.sweep_record=r;self.show_experiments(r);return
        self.sweep_record=r;self.sweep_table.show();populate(self.sweep_table,[[g['multiplier'],g['n'],number(g['latency_p95'])+(' ± '+number(g['latency_p95_ci95']) if g['latency_p95_ci95'] is not None else ''),percent(g['delivery_ratio']),number(g['throughput'])] for g in r['groups']]);self.show_page(1)
    def calibration_template(self):
        from lab.calibration import TEMPLATE
        path,_=QFileDialog.getSaveFileName(self,'Шаблон измерений','measurements.csv','CSV (*.csv)')
        if path:Path(path).write_text(TEMPLATE,encoding='utf-8-sig')
    def start_calibration(self):
        from lab.calibration import calibrate
        path,_=QFileDialog.getOpenFileName(self,'Измерения независимых опытов','','CSV (*.csv)')
        if not path:return
        source=Path(path)
        if source.stat().st_size>5_000_000:raise ValueError('Лимит измерений: 5 МБ')
        text=source.read_text(encoding='utf-8-sig');project=copy.deepcopy(self.project)
        options=json.loads(self.settings.value('calibration/options','{}'))
        self.start_job('Калибровка и отложенная проверка',lambda progress,cancel:calibrate(project,text,progress,cancel,options),lambda result:self.accept_calibration(result,source.stem))
    def configure_calibration(self):
        from .calibration_dialog import CalibrationDialog
        dialog=CalibrationDialog(self);dialog.exec();dialog.deleteLater()
    def accept_calibration(self,result,name):
        self.calibration_record=storage.save('calibration',name,result);self.render_calibration()
    def choose_calibration(self):
        rid=self.calibration_combo.currentData();self.calibration_record=storage.get('calibration',rid) if rid else None;self.render_calibration()
    def render_calibration(self):
        result=self.calibration_record
        if not result:populate(self.calibration_table,[]);self.calibration_info.setText('Выберите отчёт или загрузите измерения для подбора.');return
        self.calibration_info.setText('Train: '+', '.join(result['split']['train'])+'; test: '+', '.join(result['split']['test'])+f'. Симуляций: {result.get("simulations","—")}. Параметры: '+json.dumps(result.get('parameters',result['scales']),ensure_ascii=False))
        evidence=result.get('evidence',{});self.calibration_info.setText(self.calibration_info.text()+'\nПроисхождение: '+', '.join(evidence.get('sources',['не указано']))+'. '+('Есть метаданные стенда; подлинность исходных артефактов не проверена.' if evidence.get('status')=='declared_lab_holdout' else 'Внешняя достоверность по оборудованию не подтверждена.'))
        populate(self.calibration_table,[[split,key,number(result['before'][split]['normalized_mae'][key],4),number(value,4),(' – '.join(number(interval[k],4) for k in ('lower','upper')) if split=='test' and (interval:=result.get('test_error_ci95',{}).get(key)) else '—')] for split in ('train','test') for key,value in result['after'][split]['normalized_mae'].items()])
    def apply_calibration(self):
        result=getattr(self,'calibration_record',None)
        if not result:raise ValueError('Сначала выполните калибровку')
        self.mutate('Применить калибровку',lambda p:p.update(copy.deepcopy(result['calibrated_project'])))
    def export_calibration(self):self.write_export(getattr(self,'calibration_record',None),'calibration')
    def refresh_metrics(self):
        p=self.project;r=self.run_record;s=r['summary'] if r else {}
        self.metrics[0].update_value(f'{sum(n["enabled"] for n in p["nodes"])} / {len(p["nodes"])}',f'{len(p["links"])} каналов  ·  {len(p["flows"])} потока')
        self.metrics[1].update_value(number(s.get('throughput'))+' /s',f'{s.get("delivered",0):,} успешных завершений' if r else 'Ожидание прогона')
        self.metrics[2].update_value(number(s.get('latency_p95'))+' ms','Средняя: '+number(s.get('latency_mean'))+' ms' if r else 'Обработка + очередь + транспорт')
        self.metrics[3].update_value(percent(s.get('delivery_ratio')),'Потери: '+percent(s.get('loss_ratio')) if r else 'Данные появятся после расчёта')
        stale=r is not None and fingerprint(p)!=fingerprint(r['project']);self.map_status.setText('Изменения после прогона' if stale else f'seed {r["seed"]} · {r["duration"]} s' if r else 'Готово к эксперименту')
    def refresh_measurements(self):
        self.refresh_metrics();self.graph.set_project(self.project,self.selected,self.run_record if fingerprint(self.project)==fingerprint(self.run_record['project']) else None)
        r=self.run_record;self.rate_chart.set_series([dict(name='Сигнальный трафик · msg/s',color='#8fe0c2',points=[(w['time'],w['rate']) for w in r['windows']])],self.run_events());self.refresh_telemetry_chart();self.refresh_traces();self.refresh_results();self.attacks_widget.refresh_report()
    def run_events(self):
        from lab.attacks import compile_attacks
        p=Project.model_validate(self.run_record['project']);effects,_=compile_attacks(p)
        return [e.model_dump() for e in [*p.incidents,*effects]]
    def refresh_telemetry_chart(self):
        if not self.run_record:return
        k=self.telemetry_metric.currentData();self.telemetry_chart.set_series([dict(name=FEATURE_NAMES[k],points=[(w['time'],w.get(k,0)) for w in self.run_record['windows']])],self.run_events())
    def refresh_traces(self):
        if not self.run_record:return
        text=self.trace_search.text().lower();kind=self.trace_filter.currentData();rows=sorted(self.run_record['traces'],key=lambda r:r['time'])
        filtered=[r for r in rows if (kind=='all' or (r['status']=='delivered')==(kind=='delivered')) and (not text or text in json.dumps(r).lower())]
        self.visible_traces=filtered
        populate(self.trace_table,[[number(r['time'],3),r['protocol'],r['operation'],' → '.join(r['route']),number(r['latency_ms'],2),r['status'],str(r.get('procedure_id','—'))+' / '+str(r.get('steps_completed','—')),r.get('retries',0)] for r in filtered])
        policy='Равномерная выборка завершённых процедур' if self.run_record.get('trace_policy') else 'Первые завершённые сообщения (v1)'
        self.trace_note.setText(f'Показано {len(filtered)} из {len(rows)} записей. {policy}; агрегаты учитывают все {self.run_record["summary"]["sent"]:,}.')
        populate(self.lifecycle_table,[[number(x['time'],3),x['object'],x['state']] for x in self.run_record.get('lifecycle',[])])
        populate(self.procedure_table,[[x['flow_id'],percent(x['success_ratio']),number(x['latency_p95']),number(x['latency_p99']),x['retries']] for x in self.run_record.get('procedures',[])])
        populate(self.transport_table,[[lid+' / '+direction,x['packets'],x['bytes'],x['dropped'],x['retransmissions'],number(x.get('latency_p99'))] for lid,l in self.run_record['links'].items() for direction,x in l.get('directions',{}).items()])
        populate(self.message_table,[[number(x['time'],3),x['transaction_id'],x['role'],x['operation'],x['source']+' → '+x['target'],x.get('protocol','—')] for x in self.run_record.get('message_traces',[])])
        populate(self.fiveg_table,[[name,s['requests'],s['success'],s['errors'],s['timeouts']] for name,s in self.run_record.get('fiveg',{}).get('services',{}).items()])
        control=self.run_record.get('control_plane',{});data=self.run_record.get('user_plane',{})
        populate(self.control_table,[[number(x['time'],3),x.get('link','—')+' / '+x['protocol'],x['operation'],x['source']+' → '+x['target'],x['bytes']] for x in control.get('messages',[])])
        populate(self.routing_state_table,[[number(x['time'],3),x['observer'],x['resource']+' / '+x['target']+' / '+x['direction'],'UP' if x['up'] else 'DOWN',x['origin']] for x in control.get('view_events',[])])
        populate(self.user_plane_table,[[number(x['time'],3),str(x['procedure_id'])+' / '+str(x['packet_id']),str(x['upf'])+' → '+str(x['dn']),x['status'],number(x['rtt_ms'],2),x['bytes']] for x in data.get('traces',[])])
        self.user_plane_info.setText(f'Пользовательские пакеты: {data.get("delivered",0)} / {data.get("sent",0)} · доставка {percent(data.get("delivery_ratio"))} · RTT p95 {number(data.get("rtt_p95"))} ms · goodput {number(data.get("goodput_mbps"),3)} Mbps. Успех PDU оценивается отдельно.')
        self.user_plane_info.setText(self.user_plane_info.text()+f' В бюджете QoS: {data.get("qos_met",0)}. Освобождено PDU: {data.get("released_sessions",0)}. UPF: '+json.dumps(data.get('upf_policy',{}),ensure_ascii=False))
        resources=self.run_record.get('resources',{});stores=resources.get('stores',{})
        entries=lambda name,n:stores.get(name,{}).get('by_node',{}).get(n,0)
        populate(self.resource_table,[[n,f"{s['requests']} / {s['completed']}",f"{s['hits']} / {s['misses']}",f"{s['max_queue']} / {s['max_active']}",f"{s['timeouts']} / {s['rejected']}",entries('fiveg_contexts',n)+entries('subscriber_contexts',n),f"{entries('data_cache',n)} / {entries('transactions',n)}"] for n,s in resources.get('database',{}).items()])
        mobility_names=dict(completed='Завершено',handover_timeout='Тайм-аут переноса',context_limit='Лимит контекстов',context_conflict='Конфликт контекстов',peer_unavailable='Узел недоступен',peer_restarted='Узел перезапущен',profile_not_supported='Профиль не поддерживается',mobility_busy='Перенос уже выполняется')
        populate(self.mobility_table,[[number(m['time'],3)+' / '+number(m.get('end'),3),m['flow_id'],m.get('old_gnb','—')+' → '+m.get('new_gnb','—'),m.get('old_amf','—')+' → '+m.get('new_amf','—'),m.get('new_upf') or 'Текущий',mobility_names.get(m['status'],m['status'])] for m in self.run_record.get('mobility',[])])

    def refresh_library(self):
        self.records={kind:storage.listing(kind) for kind in ['run','dataset','model','project','sweep','calibration']}
        if self.run_record is None and self.records['run']:
            self.run_record=storage.get('run',self.records['run'][0]['id']);self.refresh_measurements()
        if self.dataset_record is None and self.records['dataset']:self.dataset_record=storage.get('dataset',self.records['dataset'][0]['id'])
        if self.model_record is None and self.records['model']:self.model_record=storage.get('model',self.records['model'][0]['id'])
        if self.calibration_record is None and self.records['calibration']:self.calibration_record=storage.get('calibration',self.records['calibration'][0]['id'])
        for widget,kind,selected in [(self.dataset_combo,'dataset',self.dataset_record),(self.model_combo,'model',self.model_record),(self.inference_run,'run',self.run_record),(self.calibration_combo,'calibration',self.calibration_record)]:
            widget.blockSignals(True);widget.clear();widget.addItem('Выберите запись',None)
            for r in self.records[kind]:widget.addItem(r['name']+'  /  '+r['id'][:6],r['id'])
            if selected:
                idx=widget.findData(selected['id']);widget.setCurrentIndex(max(0,idx))
            widget.blockSignals(False)
        self.refresh_ml();self.render_calibration()
    def refresh_results(self):
        if not self.records:return
        self.loaded_runs=self.records.get('run',[])
        populate(self.runs_table,[[timestamp(r['created']),r['name'],r['id'][:8],f'{r["summary"]["sent"]:,}',percent(r['summary']['delivery_ratio']),number(r['summary']['latency_p95'])] for r in self.loaded_runs])
        populate(self.projects_table,[[timestamp(r['created']),r['name'],r['id']] for r in self.records.get('project',[])])
        populate(self.series_table,[[timestamp(r['created']),r['name'],len(r['metadata']['values']),r['metadata']['repetitions'],r['id']] for r in self.records.get('sweep',[])])
    def choose_dataset(self):
        rid=self.dataset_combo.currentData();self.dataset_record=storage.get('dataset',rid) if rid else None;self.refresh_ml()
    def choose_model(self):
        rid=self.model_combo.currentData();self.model_record=storage.get('model',rid) if rid else None;self.model_metrics=None;self.refresh_ml()
    def algorithm_changed(self):
        algorithm=self.algorithm.currentData();meta=ml.definition(algorithm)
        self.trees.setEnabled('n_estimators' in meta['parameters']);self.contamination.setEnabled(meta['normal_only']);self.threshold_mode.setEnabled(meta['normal_only'])
        if not meta['normal_only']:self.threshold_mode.setCurrentIndex(0)
        self.model_options.set_algorithm(algorithm);self.algorithm_note.setText(meta['group']+' · '+meta['description']+(' Масштабирование обучается только на train.' if meta['scaled'] else ''))
    def refresh_ml(self):
        d=self.dataset_record;m=self.model_record
        if d and getattr(self,'feature_dataset_id',None)!=d['id']:
            self.feature_dataset_id=d['id'];self.feature_set_combo.setCurrentIndex(max(0,self.feature_set_combo.findData(d['metadata'].get('feature_set','legacy-v1'))))
        self.refresh_holdout()
        self.dataset_info.setText(f'{d["metadata"]["rows"]} окон  ·  {d["metadata"]["runs"]} независимых прогонов  ·  {d["metadata"]["positives"]} аномальных окон  ·  источник: {d["metadata"]["source"]}' if d else 'Нет датасета. Сформируйте выборку или импортируйте CSV.')
        if d:self.dataset_info.setText(self.dataset_info.text()+'  ·  '+d['metadata'].get('feature_set','legacy-v1')+f'  ·  топологий: {len({r.get("topology_id") for r in d["rows"] if r.get("topology_id")}) or "нет сведений"}')
        if not m:
            self.display_model_id=None;self.current_threshold=None
            for metric in self.ml_metrics:metric.update_value('—','')
            populate(self.confusion,[]);populate(self.importance,[]);self.model_info.setText('Модель ещё не выбрана.');self.operational_info.setText('Нет итоговой проверки.');self.roc_chart.set_series([]);self.pr_chart.set_series([]);return
        if self.display_model_id!=m['id']:
            self.display_model_id=m['id'];self.current_threshold=m['threshold']
        self.threshold_range=ml.threshold_limits(m);self.sync_threshold_controls();self.render_model_metrics(ml.metrics(m['test_labels'],m['test_scores'],self.current_threshold))
        populate(self.importance,[[FEATURE_NAMES.get(r['feature'],r['feature']),number(r['value'],4)] for r in m['importance']])
        self.roc_chart.set_series([dict(name='ROC curve · FPR / TPR',color='#82adfa',points=m['curves']['roc'])]);self.pr_chart.set_series([dict(name='Precision–Recall curve',color='#8fe0c2',points=m['curves']['pr'])])
        evaluation=m.get('evaluation',{});key=m.get('feature_set','legacy-v1')
        self.model_info.setText(f'{m.get("algorithm_title",m["algorithm"])}  ·  {FEATURE_SETS.get(key,{}).get("title",key)}  ·  Train {m["split"]["train"]["samples"]} / Validation {m["split"]["validation"]["samples"]} / Test {m["split"]["test"]["samples"]}  ·  проверка: {dict(run="прогоны",topology="топология",fault="тип отказа").get(evaluation.get("mode"),"прогоны")} {evaluation.get("holdout") or ""}  ·  исходный порог {m["threshold"]:.6g}'+(' · обучена только на нормальных train' if m.get('normal_only') else '')+'\n'+('Оценка вероятности аномалии.' if m.get('score_kind','probability')=='probability' else 'Оценка в единицах модели; больше — ближе к аномалии. Не вероятность.')+(' Встроенная важность признаков отсутствует.' if not m['importance'] else '')+('\nПредупреждения обучения: '+'; '.join(m.get('fit',{}).get('warnings',[])) if m.get('fit',{}).get('warnings') else ''))
        self.render_operational()
    def refresh_holdout(self):
        if not hasattr(self,'evaluation_mode'):return
        mode=self.evaluation_mode.currentData();previous=self.holdout_value.currentData();self.holdout_value.clear()
        if self.dataset_record:
            rows=self.dataset_record['rows']
            values=sorted({r['topology_id'] for r in rows if 'topology_id' in r}) if mode=='topology' else sorted({v for r in rows for v in r.get('fault_types',[])}) if mode=='fault' else []
            for value in values:self.holdout_value.addItem(NAMES.get(value,value),value)
        self.holdout_value.setEnabled(mode!='run')
        if self.holdout_value.findData(previous)>=0:self.holdout_value.setCurrentIndex(self.holdout_value.findData(previous))
    def render_operational(self):
        m=self.model_record
        if not m:return
        op=operational_metrics(m['test_observations'],m['test_scores'],self.current_threshold) if m.get('test_observations') else m.get('operational',{})
        if not op.get('available'):
            self.operational_info.setText('Нет данных о длительности окон и времени отказов: скорость обнаружения и тревоги/час не оценены.');return
        self.operational_info.setText(f'Обнаружено {op["detected"]} / {op["episodes"]} эпизодов · пропущено {op["missed"]} · задержка: средняя {number(op["delay_mean_s"],2)} s, max {number(op["delay_max_s"],2)} s · ложных тревог/час {number(op["false_alarms_per_hour"],2)} · нормальное наблюдение {number(op["normal_exposure_s"])} s. Решение — в конце окна; соседние тревоги объединены. Пропуски не считаются нулевой задержкой.')
    def render_model_metrics(self,met):
        self.model_metrics=met
        for tile,key in zip(self.ml_metrics,['f1','precision','recall','roc_auc']):tile.update_value(number(met[key],3),'TEST SET')
        c=met['confusion'];populate(self.confusion,[['Норма',f'{c[0][0]}  TN',f'{c[0][1]}  FP'],['Аномалия',f'{c[1][0]}  FN',f'{c[1][1]}  TP']])
    def threshold_changed(self,value):
        lo,hi=self.threshold_range;self.set_detection_threshold(lo+(hi-lo)*value/1000)
    def exact_threshold_changed(self,value):self.set_detection_threshold(value)
    def sync_threshold_controls(self):
        value=self.current_threshold;lo,hi=self.threshold_range
        self.threshold.blockSignals(True);self.threshold.setValue(round(1000*(value-lo)/(hi-lo)));self.threshold.blockSignals(False)
        self.threshold_input.blockSignals(True);self.threshold_input.setValue(value);self.threshold_input.blockSignals(False);self.threshold_label.setText(number(value,6))
    def set_detection_threshold(self,value):
        self.current_threshold=value;self.sync_threshold_controls();m=self.model_record
        if m:self.render_model_metrics(ml.metrics(m['test_labels'],m['test_scores'],value));self.render_operational()
        populate(self.prediction_table,[]);self.prediction_info.setText('Порог изменён. Повторите применение детектора к прогону.')
    def generate_dataset(self):
        p=copy.deepcopy(self.project);count=self.dataset_runs.value();feature_set=self.feature_set_combo.currentData();variants=self.topology_variants.isChecked()
        def done(r):self.dataset_record=r;self.refresh_library();self.show_page(3);self.statusBar().showMessage('Датасет сформирован: '+str(r['metadata']['rows'])+' окон',10000)
        self.start_job('Генерация независимых прогонов и ML-признаков',lambda progress,cancel:research.dataset(p,count,p['name']+' / telemetry',progress,cancel,feature_set=feature_set,topology_variants=variants),done)
    def train_model(self):
        if not self.dataset_record:raise ValueError('Выберите или сформируйте датасет')
        dataset_id=self.dataset_record['id'];algorithm=self.algorithm.currentData();seed=self.train_seed.value();trees=self.trees.value();contamination=self.contamination.value();mode=self.threshold_mode.currentData() if ml.definition(algorithm)['normal_only'] else 'validation';parameters=self.model_options.values()
        feature_set=self.feature_set_combo.currentData();evaluation=self.evaluation_mode.currentData();holdout=self.holdout_value.currentData()
        def done(r):self.model_record=r;self.refresh_library();self.statusBar().showMessage(f'Модель обучена. Test F1: {r["metrics"]["f1"]:.3f}',12000)
        self.start_job('Обучение и независимая оценка детектора',lambda progress,cancel:research.train(dataset_id,algorithm,seed,trees,contamination,mode,progress,cancel,feature_set=feature_set,evaluation_mode=evaluation,holdout_value=holdout,parameters=parameters),done)
    def import_dataset(self):
        path,_=QFileDialog.getOpenFileName(self,'Импорт телеметрии CSV','','CSV (*.csv)')
        if not path:return
        p=Path(path)
        if p.stat().st_size>10_000_000:raise ValueError('Лимит CSV: 10 МБ')
        text=p.read_text(encoding='utf-8-sig')
        def done(r):self.dataset_record=r;self.refresh_library()
        self.start_job('Импорт и проверка телеметрии',lambda progress,cancel:research.import_csv(text,p.stem),done)
    def predict(self):
        if not self.model_record:raise ValueError('Выберите обученную модель')
        rid=self.inference_run.currentData()
        if not rid:raise ValueError('Выберите завершённый прогон')
        m=copy.deepcopy(self.model_record);threshold=self.current_threshold
        def work(progress,cancel):
            r=storage.get('run',rid);result=ml.predict(m['model_id'],r['windows'],threshold);research.checkpoint(cancel);return result
        def done(rows):
            populate(self.prediction_table,[[number(r['time']),number(r['score'],4),'АНОМАЛИЯ' if r['anomaly'] else 'норма',r['label']] for r in rows]);self.prediction_info.setText(f'{sum(r["anomaly"] for r in rows)} из {len(rows)} окон отмечены детектором. Порог {threshold:.3f}.')
        self.start_job('Применение детектора к выбранному прогону',work,done)

    def selected_record(self,kind,table_widget):
        row=table_widget.currentRow()
        if row<0 or row>=len(self.records[kind]):raise ValueError('Выберите запись в таблице')
        return storage.get(kind,self.records[kind][row]['id'])
    def load_selected_run(self):
        self.run_record=self.selected_record('run',self.runs_table);self.refresh_measurements();self.refresh_library();self.statusBar().showMessage('Выбран прогон '+self.run_record['id']+'; текущая конфигурация не изменена.',10000)
    def restore_run(self):
        if not self.run_record:raise ValueError('Выберите завершённый прогон')
        saved=copy.deepcopy(self.run_record['project']);self.mutate('Восстановить конфигурацию прогона',lambda p:(p.clear(),p.update(saved)));self.show_page(0)
    def compare_runs(self):
        indices=sorted({i.row() for i in self.runs_table.selectedIndexes()})
        if len(indices)<2:raise ValueError('Выберите минимум два прогона (Ctrl / Shift + щелчок)')
        rows=[storage.get('run',self.records['run'][i]['id']) for i in indices]
        self.comparison.show();populate(self.comparison,[[r['id'][:8],r['seed'],number(sum(f['rate'] for f in r['project']['flows'])),percent(r['summary']['delivery_ratio']),number(r['summary']['latency_p95']),number(r['summary']['throughput'])] for r in rows])
    def save_library_project(self):
        storage.save('project',self.project['name'],dict(project=self.project));self.refresh_library();self.refresh_results();self.statusBar().showMessage('Проект сохранён в библиотеке',8000)
    def open_library_project(self):
        r=self.selected_record('project',self.projects_table)
        if not self.may_replace():return
        self.path=None;self.undo.clear();self.install_project(r['project']);self.undo.setClean();self.setWindowModified(False);self.show_page(0)
    def open_sweep(self):self.show_sweep(self.selected_record('sweep',self.series_table))
    def may_replace(self):
        if self.undo.isClean():return True
        result=QMessageBox.question(self,'Сохранить изменения?','Сохранить текущий проект перед заменой? Черновик рабочей области хранится автоматически.',QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Save)
        if result==QMessageBox.StandardButton.Cancel:return False
        if result==QMessageBox.StandardButton.Save:return self.save_project()
        return True
    def new_project(self,name):
        if not self.may_replace():return
        self.path=None;self.undo.clear();self.install_project(preset(name).model_dump());self.undo.setClean();self.setWindowModified(False);self.graph.fit();self.show_page(0)
    def open_project(self):
        if not self.may_replace():return
        path,_=QFileDialog.getOpenFileName(self,'Открыть проект Signal Lab','','Signal Lab / JSON (*.json)')
        if not path:return
        p=Path(path)
        if p.stat().st_size>2_000_000:raise ValueError('Проект превышает 2 МБ')
        data=json.loads(p.read_text(encoding='utf-8-sig'));project=Project.model_validate(data.get('project',data)).model_dump()
        self.path=p;self.undo.clear();self.install_project(project);self.undo.setClean();self.setWindowModified(False);self.show_page(0);self.graph.fit()
    def save_project(self,save_as=False):
        path=self.path
        if save_as or path is None:
            chosen,_=QFileDialog.getSaveFileName(self,'Сохранить проект',str(path or storage.DATA/'signal-lab-project.json'),'Signal Lab / JSON (*.json)')
            if not chosen:return False
            path=Path(chosen)
        temp=path.with_name(path.name+'.tmp');temp.write_text(json.dumps(self.project,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
        self.path=path;self.undo.setClean();self.setWindowModified(False);storage.save('project',self.project['name'],dict(project=self.project));self.refresh_library();self.statusBar().showMessage('Сохранено: '+str(path),10000);return True
    def write_export(self,record,kind,csv=False):
        if not record:raise ValueError('Сначала выберите запись для экспорта')
        suffix='csv' if csv else 'json';chosen,_=QFileDialog.getSaveFileName(self,'Экспорт',str(storage.DATA/f'{kind}-{record["id"]}.{suffix}'),f'{suffix.upper()} (*.{suffix})')
        if not chosen:return
        content=research.csv_text(record.get('rows',record.get('windows',record.get('results',[])))) if csv else json.dumps(record,ensure_ascii=False,indent=2)
        Path(chosen).write_text(content,encoding='utf-8-sig' if csv else 'utf-8');self.statusBar().showMessage('Экспортировано: '+chosen,10000)
    def export_run(self):self.write_export(self.run_record,'run')
    def export_windows(self):self.write_export(self.run_record,'windows',True)
    def export_dataset(self):self.write_export(self.dataset_record,'dataset',True)
    def export_model(self):
        if not self.model_record:raise ValueError('Выберите модель')
        r=copy.deepcopy(self.model_record);r['exploratory_threshold']=self.current_threshold;r['exploratory_test_metrics']=self.model_metrics
        if r.get('test_observations'):r['exploratory_operational']=operational_metrics(r['test_observations'],r['test_scores'],self.current_threshold)
        self.write_export(r,'model')
    def shortcuts(self):
        QMessageBox.information(self,'Горячие клавиши','Ctrl+N — новый проект\nCtrl+O — открыть\nCtrl+S — сохранить\nCtrl+Shift+S — сохранить как\nCtrl+Z / Ctrl+Y — отменить / повторить\nCtrl+Shift+N — добавить узел\nCtrl+L — добавить канал\nCtrl+Shift+L — соединить узлы мышью\nCtrl+Shift+T — экспорт схемы топологии\nF1 — справка и руководство\nF11 — развернуть / свернуть карту\nF5 — запустить эксперимент\nEscape — отменить соединение / свернуть карту / отменить вычисление\nF / Ctrl+0 — вся карта\nКолесо — прокрутка карты\nShift + колесо — по горизонтали\nCtrl + колесо — масштаб\nЗажатое колесо или Пробел + мышь — панорама\nDelete на карте — удалить выбранное (Ctrl+Z для восстановления)')
    def about(self):
        QMessageBox.information(self,'Signal Lab Desktop 2.20.0','Нативная лаборатория SS7 / SIGTRAN / Diameter / 5G SA.\nQt Widgets, локальные вычисления и ML.\n\nРегистрация и PDU-сессии, вложенные RPC, NGAP/SBI/PFCP и проверочные пакеты N3/N6 через UPF и DN.\n\nУправляющие сообщения используют очереди и каналы. У каждого узла своё представление доступности с распространением обновлений.\n\nКалибровка: параметры узлов и каналов, границы, несколько проходов и seed, независимая проверка и интервалы ошибки.\n\nПоведенческая офлайн-модель выбранных механизмов. Полные стеки 3GPP/RFC, радиоканал и криптография не реализованы. Совпадение с оборудованием требует измерений.\n\nДанные: '+str(storage.DATA))
    def closeEvent(self,event):
        if self.active_job:
            dialog=getattr(self,'long_run_dialog',None)
            if dialog and dialog.owns_job:
                self.close_after_long_job=True;self.long_close_ready=False;self.cancel_job()
                self.statusBar().showMessage('Сохраняю контрольную точку перед закрытием…');event.ignore();return
            self.error('Сначала отмените вычисление и дождитесь его завершения. Для обучения отмена проверяется после завершения fit().');event.ignore();return
        if not self.may_replace():event.ignore();return
        if self.topology_expanded:self.set_topology_expanded(False)
        try:
            self.save_workspace()
            if self.preferences['preferences/rememberWindow']:self.settings.setValue('geometry',self.saveGeometry());self.settings.setValue('topologySplitter',self.topology_split.sizes())
            self.settings.sync()
        except Exception as e:self.error(str(e));event.ignore();return
        event.accept()
