"""Native declarative attack designer; never sends traffic outside the simulator."""
import copy
import uuid
from pathlib import Path
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPen, QFont
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QPlainTextEdit,QCheckBox,QDialogButtonBox,QLabel,QSplitter,QListWidget,QListWidgetItem,QTabWidget,QScrollArea,QFileDialog,QAbstractItemView)
from lab import storage, research
from lab.schema import Project, AttackScenario, AttackStep
from lab.attacks import EFFECTS,PROFILES,PRESETS,candidates,from_preset,compile_attacks,encode_template,decode_template
from .widgets import label,button,spin,combo,table,populate,panel
from .style import themed_color


def uid(prefix):return prefix+'-'+uuid.uuid4().hex[:10]
def fmt(value):return f'{value:g}'
def targets(project,effect):
    group=EFFECTS[effect][1]
    values=project[{'node':'nodes','link':'links','flow':'flows'}[group]]
    return [(v['id'],v.get('name',v['id'])+' · '+v['id']+((' · '+v['source']+' → '+v['target']) if 'source' in v else '')) for v in values]


class AttackTimeline(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.project=None;self.setAccessibleName('Расписание этапов атак')
    def set_project(self,project):
        self.project=project
        count=sum(len(a['steps']) for a in project.get('attacks',[]))
        self.setMinimumHeight(max(125,42+count*31));self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.project:return
        width=max(1,self.width()-214);duration=self.project['duration'];left=195
        p.setFont(QFont('Segoe UI',9));p.setPen(themed_color('#74879e'))
        for i in range(7):
            x=left+width*i/6;tick=f'{duration*i/6:.1f}'.rstrip('0').rstrip('.')
            p.drawText(QRectF(x-(64 if i==6 else 32),0,64,22),Qt.AlignmentFlag.AlignRight if i==6 else Qt.AlignmentFlag.AlignCenter,tick+' с')
            p.setPen(QPen(themed_color('#283747'),1));p.drawLine(int(x),25,int(x),self.height());p.setPen(themed_color('#74879e'))
        y=31
        for raw in self.project.get('attacks',[]):
            a=AttackScenario.model_validate(raw)
            for s in a.steps:
                p.setPen(themed_color('#e5ebf5' if a.enabled else '#74879e'))
                title=f'{a.name} / {s.name}';p.drawText(QRectF(2,y,183,26),Qt.AlignmentFlag.AlignVCenter,p.fontMetrics().elidedText(title,Qt.TextElideMode.ElideRight,183))
                from lab.attacks import segments
                p.setPen(Qt.PenStyle.NoPen);color=themed_color('#8fe0c2' if a.enabled else '#425166');color.setAlpha(190 if a.enabled else 90);p.setBrush(color)
                for repeat in range(a.repeats):
                    for off,span,value in segments(s):
                        lo=a.start+repeat*a.interval+s.offset+off;hi=min(duration,lo+span)
                        if hi<=0 or lo>=duration:continue
                        p.drawRoundedRect(QRectF(left+max(0,lo)*width/duration,y+4,max(1,(hi-max(0,lo))*width/duration),18),3,3)
                y+=31
        if y==31:
            p.setPen(themed_color('#74879e'));p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'Добавьте предустановку или создайте собственный сценарий')


def timeline_area(timeline):
    scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(timeline);scroll.setMinimumHeight(130);return scroll


class StepEditor(QDialog):
    def __init__(self,parent,project,step=None):
        super().__init__(parent);self.setWindowTitle('Этап воздействия');self.setMinimumWidth(660);self.project=project;self.original=step or dict(id=uid('step'),name='Новый этап',effect='surge',target=project['flows'][0]['id'],duration=min(5,project['duration']*.2))
        s=AttackStep.model_validate(self.original);root=QVBoxLayout(self);form=QFormLayout();form.setHorizontalSpacing(18);form.setVerticalSpacing(10);root.addLayout(form)
        self.name=QLineEdit(s.name);self.name.setMaxLength(100);form.addRow('Название',self.name)
        self.effect=combo([(k,v[0]) for k,v in EFFECTS.items()],s.effect);form.addRow('Воздействие',self.effect)
        self.target=combo([]);form.addRow('Цель',self.target)
        self.offset=spin(s.offset,0,599,2,'с');self.duration=spin(s.duration,.01,600,2,'с');form.addRow('Отступ от начала сценария',self.offset);form.addRow('Длительность этапа',self.duration)
        self.magnitude=spin(s.magnitude,0,100,4);self.value_title=label('');form.addRow(self.value_title,self.magnitude)
        self.direction=combo([('both','Оба'),('forward','Источник → назначение'),('reverse','Назначение → источник')],s.direction);form.addRow('Направление канала',self.direction)
        self.profile=combo(PROFILES,s.profile);form.addRow('Профиль',self.profile)
        self.period=spin(s.period,.1,600,2,'с');self.duty=spin(s.duty,.05,.95,2);form.addRow('Период импульсов',self.period);form.addRow('Доля включённого времени',self.duty)
        self.info=label('','Muted');self.info.setWordWrap(True);root.addWidget(self.info)
        self.error=label('');self.error.setWordWrap(True);root.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Применить');buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена');buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);root.addWidget(buttons)
        self.effect.currentIndexChanged.connect(self.effect_changed);self.profile.currentIndexChanged.connect(self.profile_changed)
        self.effect_changed(initial=s);self.profile_changed()
    def effect_changed(self,*args,initial=None):
        key=self.effect.currentData();spec=EFFECTS[key];previous=self.target.currentData();self.target.clear()
        for rid,title in targets(self.project,key):self.target.addItem(title,rid)
        wanted=initial.target if initial else previous;idx=self.target.findData(wanted)
        if initial and idx<0:self.target.addItem('⚠ Цель отсутствует: '+wanted,wanted);idx=self.target.count()-1
        if idx>=0:self.target.setCurrentIndex(idx)
        self.magnitude.setRange(spec[2],spec[3]);self.value_title.setText(spec[4]);self.magnitude.setEnabled(spec[2]!=spec[3])
        if initial:self.magnitude.setValue(initial.magnitude)
        else:self.magnitude.setValue(.1 if key=='node_capacity' else .3 if key in ('loss_absolute','node_error') else 3 if spec[3]>1 else 1)
        channel=spec[1]=='link';self.direction.setEnabled(channel)
        if not channel:self.direction.setCurrentIndex(0)
        outage=key in ('node_down','node_restart','link_down');self.profile.setEnabled(not outage)
        if outage:self.profile.setCurrentIndex(0)
        self.info.setText('Направление определяется полями «источник» и «назначение» канала. Вероятности задаются от 0 до 1; 0,3 = 30%. Мощность 0,1 = осталось 10%. Ошибки приложения действуют на конечном сервисе. Задержка узла применяется при начале обработки.')
        self.profile_changed()
    def profile_changed(self):
        pulse=self.profile.currentData()=='pulse';self.period.setEnabled(pulse);self.duty.setEnabled(pulse)
    def values(self):
        return AttackStep(id=self.original['id'],name=self.name.text().strip(),effect=self.effect.currentData(),target=self.target.currentData() or '',offset=self.offset.value(),duration=self.duration.value(),magnitude=self.magnitude.value(),direction=self.direction.currentData(),profile=self.profile.currentData(),period=self.period.value(),duty=self.duty.value()).model_dump()
    def accept(self):
        try:
            v=self.values()
            if v['target'] not in {rid for rid,_ in targets(self.project,v['effect'])}:raise ValueError('Выберите существующую цель')
            from lab.attacks import segments
            segments(AttackStep.model_validate(v));self.result_data=v;super().accept()
        except (ValueError,TypeError) as e:self.error.setText(str(e))


class AttackEditor(QDialog):
    def __init__(self,parent,project,scenario,existing_id=None):
        super().__init__(parent);self.setWindowTitle('Конструктор атаки');self.resize(1000,740);self.setMinimumSize(780,640);self.project=copy.deepcopy(project);self.original=copy.deepcopy(scenario);self.existing_id=existing_id;self.steps=copy.deepcopy(scenario['steps'])
        root=QVBoxLayout(self);root.setContentsMargins(20,16,20,16);root.setSpacing(10)
        form=QFormLayout();form.setVerticalSpacing(9);self.name=QLineEdit(scenario['name']);self.name.setMaxLength(100);form.addRow('Название',self.name)
        timing=QHBoxLayout();timing.setSpacing(18);self.enabled=QCheckBox('Включена');self.enabled.setChecked(scenario['enabled']);timing.addWidget(self.enabled)
        self.start=spin(scenario['start'],0,599,2,'с');self.repeats=spin(scenario['repeats'],1,10);self.interval=spin(scenario['interval'],.1,600,2,'с')
        for title,w in [('Начало',self.start),('Повторов',self.repeats),('Интервал начал',self.interval)]:
            group=QWidget();group_layout=QHBoxLayout(group);group_layout.setContentsMargins(0,0,0,0);group_layout.setSpacing(7);group_layout.addWidget(label(title));w.setMaximumWidth(125 if w is not self.repeats else 60);group_layout.addWidget(w);timing.addWidget(group)
        timing.addStretch();form.addRow(timing);root.addLayout(form)
        self.description=QPlainTextEdit(scenario['description']);self.description.setPlaceholderText('Гипотеза, ожидаемое поведение и условия эксперимента');self.description.setMaximumHeight(66);root.addWidget(self.description)
        tools=QHBoxLayout()
        for text,fn,ico in [('Этап',self.add_step,'plus'),('Изменить',self.edit_step,'settings'),('Копия',self.clone_step,'copy'),('Удалить',self.remove_step,'trash')]:tools.addWidget(button(text,fn,ico))
        tools.addStretch();root.addLayout(tools)
        self.steps_table=table(['Этап','Воздействие / цель','Начало / длительность','Значение / профиль']);self.steps_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection);self.steps_table.doubleClicked.connect(lambda:self.edit_step());root.addWidget(self.steps_table,2)
        self.timeline=AttackTimeline();root.addWidget(timeline_area(self.timeline),1)
        self.status=label('','Muted');self.status.setWordWrap(True);root.addWidget(self.status)
        self.error=label('');self.error.setWordWrap(True);root.addWidget(self.error)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Сохранить сценарий');self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена');self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject);root.addWidget(self.buttons)
        for w in (self.start,self.repeats,self.interval):w.valueChanged.connect(self.refresh)
        self.enabled.toggled.connect(self.refresh);self.name.textChanged.connect(self.refresh);self.refresh()
    def values(self):
        return AttackScenario(id=self.original['id'],name=self.name.text().strip(),description=self.description.toPlainText().strip(),enabled=self.enabled.isChecked(),start=self.start.value(),repeats=self.repeats.value(),interval=self.interval.value(),steps=self.steps).model_dump()
    def candidate(self):
        p=copy.deepcopy(self.project);a=self.values();p['attacks']=[a if x['id']==self.existing_id else x for x in p.get('attacks',[])]
        if self.existing_id is None:p['attacks'].append(a)
        return Project.model_validate(p)
    def refresh(self,*args):
        selected=self.steps_table.currentRow();populate(self.steps_table,[[s['name'],EFFECTS[s['effect']][0]+' · '+s['target'],f"+{s['offset']:g} / {s['duration']:g} с",f"{s['magnitude']:g} · {dict(PROFILES)[s['profile']]}" ] for s in self.steps])
        if self.steps:self.steps_table.selectRow(max(0,min(selected,len(self.steps)-1)))
        try:
            p=self.candidate();events,_=compile_attacks(p);self.error.clear();self.status.setText(f'Проверка пройдена · {len(events)} интервалов в проекте · эксперимент {p.duration} с. Совпадающие эффекты объединяются по максимальной тяжести.')
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
            preview=p.model_dump();preview['attacks']=[self.values()];self.timeline.set_project(preview)
        except (ValueError,TypeError) as e:
            self.error.setText(str(e));self.status.setText('Исправьте настройки перед сохранением');self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
    def add_step(self):self.open_step()
    def edit_step(self):
        i=self.steps_table.currentRow()
        if i>=0:self.open_step(i)
    def open_step(self,index=None):
        if index is None and len(self.steps)>=20:self.error.setText('Не более 20 этапов в сценарии');return
        d=StepEditor(self,self.project,self.steps[index] if index is not None else None)
        if d.exec()==QDialog.DialogCode.Accepted:
            if index is None:self.steps.append(d.result_data)
            else:self.steps[index]=d.result_data
            self.refresh()
        d.deleteLater()
    def clone_step(self):
        i=self.steps_table.currentRow()
        if i<0:return
        if len(self.steps)>=20:self.error.setText('Не более 20 этапов в сценарии');return
        s=copy.deepcopy(self.steps[i]);s['id']=uid('step');s['name']+=' · копия';s['name']=s['name'][:100];self.steps.append(s);self.refresh()
    def remove_step(self):
        i=self.steps_table.currentRow()
        if i>=0:self.steps.pop(i);self.refresh()
    def accept(self):
        try:self.candidate();self.result_data=self.values();super().accept()
        except (ValueError,TypeError) as e:self.error.setText(str(e))


class AttacksPage(QWidget):
    def __init__(self,window):
        super().__init__();self.window=window;self.project=window.project;root=QVBoxLayout(self);root.setContentsMargins(0,0,0,0);root.setSpacing(10)
        split=QSplitter(Qt.Orientation.Horizontal);root.addWidget(split,1)
        left,ll,lh=panel('Предустановки');left.setMinimumWidth(270);left.setMaximumWidth(400);split.addWidget(left)
        self.search=QLineEdit();self.search.setPlaceholderText('Поиск типа атаки');ll.addWidget(self.search)
        self.catalog=QTabWidget();ll.addWidget(self.catalog,1);self.presets=QListWidget();self.saved=QListWidget();self.catalog.addTab(self.presets,'Встроенные');self.catalog.addTab(self.saved,'Мои шаблоны')
        for listing in (self.presets,self.saved):listing.setWordWrap(True);listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.description=label('','Muted');self.description.setWordWrap(True);ll.addWidget(self.description)
        self.add_button=button('Добавить выбранную',lambda:window.call(self.add_preset),'plus',True);ll.addWidget(self.add_button);ll.addWidget(button('Собственный сценарий',lambda:window.call(self.create_custom),'experiment'))
        imports=QHBoxLayout();imports.addWidget(button('Импорт JSON',lambda:window.call(self.import_template),'folder'));imports.addWidget(button('Удалить шаблон',lambda:window.call(self.delete_template),'trash'));ll.addLayout(imports)
        right,rl,rh=panel('Сценарии проекта');split.addWidget(right);split.setStretchFactor(1,1)
        tools=QHBoxLayout()
        for text,fn,ico in [('Изменить',self.edit_selected,'settings'),('Копия',self.clone_selected,'copy'),('Вкл. / выкл.',self.toggle_selected,'play'),('Удалить',self.remove_selected,'trash')]:tools.addWidget(button(text,lambda checked=False,f=fn:window.call(f),ico))
        tools.addStretch();rl.addLayout(tools)
        self.scenarios=table(['Сценарий','Состояние','Начало / конец','Этапы / повторы']);self.scenarios.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection);self.scenarios.setMinimumHeight(130);self.scenarios.setMaximumHeight(220);rl.addWidget(self.scenarios,1)
        self.tabs=QTabWidget();rl.addWidget(self.tabs,2);self.timeline=AttackTimeline();self.tabs.addTab(timeline_area(self.timeline),'Расписание')
        self.steps_table=table(['Сценарий / этап','Воздействие','Цель / направление','Профиль','Значение']);self.tabs.addTab(self.steps_table,'Все этапы')
        report=QWidget();report_layout=QVBoxLayout(report);report_layout.setContentsMargins(0,8,0,0);self.report_info=label('Нет прогона. Запустите эксперимент с включёнными сценариями.','Muted');self.report_info.setWordWrap(True);report_layout.addWidget(self.report_info)
        self.report_table=table(['Атака','Интервалы','Затронуто','Успех / отказ','Повторы процедур / транспорта']);report_layout.addWidget(self.report_table);self.tabs.addTab(report,'Отчёт прогона')
        self.info=label('','Muted');self.info.setWordWrap(True);rl.addWidget(self.info)
        templates=QHBoxLayout();templates.addWidget(button('Сохранить в мои шаблоны',lambda:window.call(self.save_template),'save'));templates.addWidget(button('Экспорт JSON',lambda:window.call(self.export_template),'export'));templates.addStretch();rl.addLayout(templates)
        footer=QHBoxLayout();self.run=window.action_button('Запустить с атаками',window.start_run,'play',True,True);footer.addWidget(self.run);footer.addWidget(window.action_button('Базовый прогон без атак',self.baseline,'chart',compute=True));footer.addWidget(button('Подготовить ML-датасет',lambda:window.show_page(3),'brain'));footer.addStretch();root.addLayout(footer)
        note=label('Воздействия выполняются внутри офлайн-модели. Всплеск усиливает существующий поток; его операцию и повторы настраивают в «Экспериментах».','Muted');note.setWordWrap(True);root.addWidget(note)
        self.search.textChanged.connect(self.filter_catalog);self.presets.currentRowChanged.connect(self.preset_description);self.saved.currentRowChanged.connect(self.saved_description);self.catalog.currentChanged.connect(lambda:self.preset_description() if self.catalog.currentIndex()==0 else self.saved_description())
        self.scenarios.doubleClicked.connect(lambda:window.call(self.edit_selected));self.scenarios.itemSelectionChanged.connect(self.selection_changed)
        self.reload_templates();self.refresh(window.project)
    def selected(self):
        i=self.scenarios.currentRow()
        if i<0:raise ValueError('Выберите сценарий в таблице')
        return self.project['attacks'][i]
    def refresh(self,project):
        selected=self.scenarios.currentRow();self.project=project;attacks=project.get('attacks',[])
        self.scenarios.blockSignals(True);populate(self.scenarios,[[a['name'],'Включена' if a['enabled'] else 'Выключена',f"{a['start']:g} / {a['start']+(a['repeats']-1)*a['interval']+max(s['offset']+s['duration'] for s in a['steps']):g} с",f"{len(a['steps'])} / {a['repeats']}" ] for a in attacks]);self.scenarios.blockSignals(False)
        if attacks:self.scenarios.selectRow(max(0,min(selected,len(attacks)-1)))
        self.timeline.set_project(project);populate(self.steps_table,[[a['name']+' / '+s['name'],EFFECTS[s['effect']][0],s['target']+' / '+{'both':'оба','forward':'прямое','reverse':'обратное'}[s['direction']],dict(PROFILES)[s['profile']],fmt(s['magnitude'])] for a in attacks for s in a['steps']])
        previous=self.presets.currentRow();self.presets.clear();p=Project.model_validate(project)
        for spec in PRESETS:
            available=bool(candidates(p,spec));item=QListWidgetItem(spec['name']+('' if available else ' · нет цели'));item.setData(Qt.ItemDataRole.UserRole,spec['id']);item.setData(Qt.ItemDataRole.UserRole+1,available);item.setToolTip(spec['description']);self.presets.addItem(item)
        self.presets.setCurrentRow(max(0,previous));self.filter_catalog();self.selection_changed();self.refresh_report()
    def filter_catalog(self,*args):
        text=self.search.text().casefold()
        for listing in (self.presets,self.saved):
            for i in range(listing.count()):listing.item(i).setHidden(text not in listing.item(i).text().casefold())
    def preset_description(self,*args):
        if self.catalog.currentIndex()!=0:return
        item=self.presets.currentItem()
        if item:self.description.setText(next(p['description'] for p in PRESETS if p['id']==item.data(Qt.ItemDataRole.UserRole)))
        self.add_button.setEnabled(bool(item and item.data(Qt.ItemDataRole.UserRole+1)))
    def saved_description(self,*args):
        if self.catalog.currentIndex()!=1:return
        item=self.saved.currentItem()
        if item:self.description.setText('Пользовательский шаблон. Перед сохранением в проект проверьте цели, расписание и значения каждого этапа.')
        self.add_button.setEnabled(item is not None)
    def selection_changed(self):
        try:a=self.selected();self.info.setText(a['description'] or 'Сценарий без описания')
        except ValueError:self.info.setText('До 20 сценариев, 20 этапов на сценарий и 10 повторов. Общий лимит — 512 интервалов воздействия.')
    def edit(self,a,existing=False):
        d=AttackEditor(self.window,self.project,a,a['id'] if existing else None)
        if d.exec()==QDialog.DialogCode.Accepted:self.store(d.result_data,existing)
        d.deleteLater()
    def store(self,a,existing=False):
        def change(p):
            if existing:p['attacks']=[copy.deepcopy(a) if x['id']==a['id'] else x for x in p['attacks']]
            else:p['attacks'].append(copy.deepcopy(a))
        self.window.mutate('Изменить атаку' if existing else 'Добавить атаку',change)
        self.scenarios.selectRow(next(i for i,x in enumerate(self.project['attacks']) if x['id']==a['id']))
    def add_preset(self):
        if self.catalog.currentIndex()==1:
            item=self.saved.currentItem()
            if not item:raise ValueError('Выберите шаблон')
            a=decode_template(storage.get('attack_template',item.data(Qt.ItemDataRole.UserRole))['template']);self.edit(a.model_dump());return
        item=self.presets.currentItem()
        if not item:raise ValueError('Выберите предустановку')
        self.edit(from_preset(Project.model_validate(self.project),item.data(Qt.ItemDataRole.UserRole)).model_dump())
    def create_custom(self):
        a=AttackScenario(id=uid('attack'),start=min(10,self.project['duration']*.2),steps=[AttackStep(id=uid('step'),name='Всплеск нагрузки',target=self.project['flows'][0]['id'],duration=min(5,self.project['duration']*.3))]);self.edit(a.model_dump())
    def edit_selected(self):self.edit(self.selected(),True)
    def clone_selected(self):
        a=copy.deepcopy(self.selected());a['id']=uid('attack');a['name']=(a['name']+' · копия')[:100];self.store(a)
    def toggle_selected(self):
        a=copy.deepcopy(self.selected());a['enabled']=not a['enabled'];self.store(a,True)
    def remove_selected(self):
        rid=self.selected()['id'];self.window.mutate('Удалить атаку',lambda p:p.update(attacks=[a for a in p['attacks'] if a['id']!=rid]))
    def reload_templates(self):
        self.saved.clear()
        for r in storage.listing('attack_template'):
            item=QListWidgetItem(r['name']);item.setData(Qt.ItemDataRole.UserRole,r['id']);self.saved.addItem(item)
        if self.saved.count():self.saved.setCurrentRow(0)
    def save_template(self):
        a=self.selected();matches=[r for r in storage.listing('attack_template') if r['name']==a['name']];storage.save('attack_template',a['name'],dict(template=encode_template(a)),matches[0]['id'] if matches else None);self.reload_templates();self.window.statusBar().showMessage('Шаблон сохранён в локальной библиотеке',6000)
    def delete_template(self):
        item=self.saved.currentItem()
        if self.catalog.currentIndex()!=1 or not item:raise ValueError('Выберите запись во вкладке «Мои шаблоны»')
        rid=item.data(Qt.ItemDataRole.UserRole);payload=storage.get('attack_template',rid)
        storage.delete('attack_template',rid);self.deleted_template=(rid,payload);self.reload_templates()
        self.description.setText('Шаблон удалён. Сценарии проекта сохранены. Кнопка ниже восстанавливает последний удалённый шаблон.')
        if not hasattr(self,'restore_button'):
            self.restore_button=button('Восстановить удалённый шаблон',lambda:self.window.call(self.restore_template),'history');self.layout().addWidget(self.restore_button)
        self.restore_button.show()
    def restore_template(self):
        rid,payload=self.deleted_template;storage.save('attack_template',payload['name'],dict(template=payload['template']),rid);self.reload_templates();self.restore_button.hide()
    def export_template(self):
        text=encode_template(self.selected());path,_=QFileDialog.getSaveFileName(self,'Экспорт шаблона атаки','attack.json','Шаблон атаки (*.json)')
        if path:Path(path).write_text(text,encoding='utf-8')
    def import_template(self):
        path,_=QFileDialog.getOpenFileName(self,'Импорт шаблона атаки','','Шаблон атаки (*.json)')
        if not path:return
        source=Path(path)
        if source.stat().st_size>262144:raise ValueError('Шаблон должен быть меньше 256 КБ')
        self.edit(decode_template(source.read_text(encoding='utf-8-sig')).model_dump())
    def baseline(self):
        p=copy.deepcopy(self.project);p['attacks']=[]
        def done(r):self.window.run_record=r;self.window.refresh_measurements();self.window.statusBar().showMessage('Базовый прогон сохранён. Ручные события эксперимента учитывались.',10000)
        self.window.start_job('Базовый прогон без атак',lambda progress,cancel:research.run(p,progress,cancel),done)
    def refresh_report(self):
        r=self.window.run_record
        if not r:populate(self.report_table,[]);return
        rows=r.get('attacks',[]);populate(self.report_table,[[a['name']+('' if a['enabled'] else ' · выкл.'),a['intervals'],a['affected_procedures'],f"{a['successful']} / {a['failed']}",f"{a['retries']} / {a['transport_retries']}" ] for a in rows])
        self.report_info.setText(f"Прогон {r.get('id','—')} · seed {r['seed']}. Отчёт относится к сохранённому снимку. «Затронуто» означает контакт с целью во время воздействия; отказы не доказывают причинность. Для сравнения используйте базовый прогон в «Результатах»." if rows else 'В выбранном прогоне нет сценариев конструктора. Результаты ручных событий доступны в телеметрии.')
