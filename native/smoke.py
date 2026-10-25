"""Development smoke harness executed inside the application, never against another app."""
import json
import traceback
from PySide6.QtCore import QObject,QTimer,QSettings,Qt,QPoint,QPointF
from PySide6.QtGui import QImage,QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog,QSpinBox,QFileDialog
import numpy as np
from lab import ml,research,storage
from .dialogs import node_editor,link_editor,flow_editor,incident_editor
from .style import saved_theme


class SmokeTest(QObject):
    def __init__(self,app,window,output):
        super().__init__(window);self.app=app;self.window=window;self.output=output;self.stage=0;self.errors=[];self.completed=[]
        if output:output.mkdir(parents=True,exist_ok=True)
        self.window.error=self.fail
        self.timeout=QTimer(self);self.timeout.setSingleShot(True);self.timeout.timeout.connect(lambda:self.fail('Native self-test timed out'));self.timeout.start(240000)
    def fail(self,text):
        self.errors.append(str(text))
        if self.output:(self.output/'native-smoke.json').write_text(json.dumps(dict(status='failed',checks=self.completed,errors=self.errors),indent=2),encoding='utf-8')
        print('NATIVE SMOKE FAIL: '+str(text),flush=True);self.timeout.stop();self.app.exit(1)
    def capture(self,name):
        if self.output:
            if not self.window.grab().save(str(self.output/f'{name}.png')):raise RuntimeError('Cannot save Qt render')
    def capture_node_icons(self,name):
        if not self.output:return
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter,QFont
        from lab.schema import NODE_KINDS
        from .node_icons import draw_node_icon,NODE_ROLES
        from .style import themed_color
        image=QImage(1040,790,QImage.Format.Format_RGB32);image.fill(themed_color('#11151c'));p=QPainter(image)
        try:
            p.setPen(themed_color('#e5ebf5'));p.setFont(QFont('Segoe UI',18,QFont.Weight.DemiBold));p.drawText(28,38,f'Иконки сетевых узлов · {len(NODE_KINDS)} типов')
            p.setFont(QFont('Segoe UI',10));p.setPen(themed_color('#8c9bb0'));p.drawText(28,61,'48 / 24 / 16 px · отдельный символ для каждого типа')
            for index,kind in enumerate(NODE_KINDS):
                x=24+(index%4)*256;y=88+(index//4)*138
                for offset,size in ((0,48),(60,24),(96,16)):draw_node_icon(p,kind,QRectF(x+offset,y+9,size,size),themed_color('#8fe0c2'))
                p.setPen(themed_color('#e5ebf5'));p.setFont(QFont('Segoe UI',12,QFont.Weight.DemiBold));p.drawText(x,y+82,kind)
                p.setPen(themed_color('#8c9bb0'));p.setFont(QFont('Segoe UI',10));p.drawText(QRectF(x,y+90,236,39),Qt.TextFlag.TextWordWrap,NODE_ROLES[kind])
        finally:p.end()
        image.save(str(self.output/f'{name}-node-icons.png'))
    def verify_themes(self):
        w=self.window;original_settings=w.settings;original_theme=w.theme;original_sidebar=w.sidebar_collapsed;original_overview=w.overview_height;original_metrics=w.topology_metrics_action.isChecked()
        project=json.dumps(w.project,sort_keys=True);selection=w.selected;undo_count=w.undo.count()
        settings_path=str(storage.DATA/'theme-self-test.ini')
        try:
            w.settings=QSettings(settings_path,QSettings.Format.IniFormat)
            for name in ('dark','monochrome','light_monochrome','graphite','porcelain'):
                w.apply_theme(name);w.set_sidebar_collapsed(False,persist=False);w.set_overview_height(125,persist=False)
                assert saved_theme(QSettings(settings_path,QSettings.Format.IniFormat))==name
                self.capture_node_icons(name)
                for page,title in enumerate(('topology','experiments','telemetry','ml','results','help','attacks','settings')):
                    w.show_page(page);self.app.processEvents()
                    if name in ('monochrome','light_monochrome'):
                        background='#ffffff' if name=='light_monochrome' else '#000000'
                        channel=255 if name=='light_monochrome' else 0
                        assert self.app.palette().color(QPalette.ColorRole.Window).name()==background
                        assert w.graph.backgroundBrush().color().name()==background
                        image=w.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
                        pixels=np.frombuffer(image.bits(),dtype=np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*4].reshape(image.height(),image.width(),4)
                        rgb=pixels[:,:,:3]
                        assert int(np.ptp(rgb,axis=2).max())==0, f'Chromatic pixels remain on {title}'
                        assert float(np.all(rgb==channel,axis=2).mean())>.5, f'Incorrect {name} background on {title}'
                        text=self.app.palette().color(QPalette.ColorRole.Text)
                        assert (text.lightness()<40) if name=='light_monochrome' else (text.lightness()>200)
                    if name in ('graphite','porcelain'):
                        from .theme_palettes import EDITOR_PALETTES
                        colors=EDITOR_PALETTES[name]
                        assert self.app.palette().color(QPalette.ColorRole.Window).name()==colors['bg']
                        assert self.app.palette().color(QPalette.ColorRole.Text).name()==colors['text']
                        assert w.graph.backgroundBrush().color().name()==colors['bg']
                    self.capture(f'{name}-{title}')
                if name in ('graphite','porcelain'):
                    from .palette_dialog import PaletteDialog
                    dialog=PaletteDialog(w,name,lambda key:w.apply_theme(key,persist=False))
                    dialog.show();self.app.processEvents()
                    assert len(dialog.swatches)==22 and dialog.selector.currentData()==name
                    if self.output:dialog.grab().save(str(self.output/f'{name}-palette.png'))
                    dialog.apply_button.click();assert w.theme==name
                    dialog.close();dialog.deleteLater();self.app.processEvents()
                w.show_page(0);self.app.processEvents();expanded_width=w.pages.width()
                w.sidebar_toggle.click();self.app.processEvents()
                assert w.sidebar_collapsed and w.pages.width()>expanded_width
                assert QSettings(settings_path,QSettings.Format.IniFormat).value('sidebarCollapsed',False,type=bool)
                for i,b in enumerate(w.nav_buttons):
                    assert not b.text() and b.toolTip() and b.accessibleName()
                    b.click();assert w.pages.currentIndex()==i and b.isChecked()
                w.show_page(0);self.app.processEvents();self.capture(f'{name}-sidebar-collapsed')
                w.sidebar_action.trigger();self.app.processEvents()
                assert not w.sidebar_collapsed and all(b.text() for b in w.sidebar_buttons)
                assert not QSettings(settings_path,QSettings.Format.IniFormat).value('sidebarCollapsed',True,type=bool)
                w.topology_metrics_action.setChecked(True);w.show_page(0);w.set_overview_height(125);self.app.processEvents()
                compact_height=w.overview_widget.height();available_height=w.pages.height()
                handle=w.workspace_split.handle(1);start=handle.rect().center()
                QTest.mousePress(handle,Qt.MouseButton.LeftButton,pos=start)
                QTest.mouseMove(handle,start+QPoint(0,60));QTest.mouseRelease(handle,Qt.MouseButton.LeftButton,pos=start+QPoint(0,60));self.app.processEvents()
                assert w.overview_widget.height()>compact_height+30,'Dragging the header boundary did not resize the overview'
                assert w.pages.height()<available_height-30
                saved=QSettings(settings_path,QSettings.Format.IniFormat).value('overviewHeight',0,type=int)
                assert saved==w.overview_height and abs(saved-w.overview_widget.height())<=1
                for page in (1,3,4):
                    w.show_page(page);self.app.processEvents()
                    assert w.overview_widget.height()<compact_height and w.overview_height==saved
                w.show_page(2);self.app.processEvents();assert abs(w.overview_widget.height()-saved)<=1
                def accept_height():
                    dialog=w.findChildren(QDialog)[-1];dialog.findChild(QSpinBox).setValue(210);dialog.accept()
                QTimer.singleShot(0,accept_height);w.overview_action.trigger();self.app.processEvents()
                assert w.overview_height==210 and abs(w.overview_widget.height()-210)<=1
                self.capture(f'{name}-overview-resized')
                original_size=w.size();w.set_overview_height(300);w.resize(1080,700);self.app.processEvents()
                assert w.pages.height()>=350 and w.overview_height==300, f'Small window: pages={w.pages.height()}, preference={w.overview_height}'
                w.resize(original_size);self.app.processEvents();assert abs(w.overview_widget.height()-300)<=1, f'Restored header: {w.overview_widget.height()}, window={w.size()}, splitter={w.workspace_split.sizes()}'
                w.set_overview_height(125);w.topology_metrics_action.setChecked(False);w.show_page(0);self.app.processEvents()
            assert json.dumps(w.project,sort_keys=True)==project
            assert w.selected==selection and w.undo.count()==undo_count
            self.completed.append('pure-black and light monochrome themes, neutral rendering on all eight pages including settings, handbook and attack designer and saved theme')
            self.completed.append('Graphite and Porcelain: all eight pages, colored topology, saved theme, 22 exact palette swatches and native theme application')
            self.completed.append('collapsible sidebar, icon navigation, tooltips, menu toggle and persisted state across all themes')
            self.completed.append('compact overview, real mouse resizing, numeric height dialog, saved height and page-switch restoration across all themes')
        finally:
            w.settings=original_settings;w.apply_theme(original_theme,persist=False);w.set_sidebar_collapsed(original_sidebar,persist=False);w.topology_metrics_action.setChecked(original_metrics);w.set_overview_height(original_overview,persist=False)
    def start(self):
        try:
            w=self.window
            self.verify_startup()
            self.verify_settings()
            import gc
            assert not gc.isenabled() and self.app.gui_collector.collect()
            self.completed.append('Python cyclic collection is scheduled on the GUI thread; automatic collection in simulation workers is disabled')
            self.verify_protocol_regressions()
            self.verify_validation()
            self.verify_procedure_viewer()
            self.verify_expanded_ml()
            self.verify_standard_ml()
            self.verify_monitoring()
            self.verify_testbed()
            self.verify_handbook()
            self.verify_attacks()
            self.verify_resource_services()
            self.verify_expanded_topology()
            self.verify_mouse_links()
            # Exercise native forms and all page construction paths.
            for factory in [lambda:node_editor(w),lambda:link_editor(w,w.project),lambda:flow_editor(w,w.project),lambda:incident_editor(w,w.project)]:
                d=factory();assert d.values();d.deleteLater()
            before=w.project['nodes'][0]['capacity'];w.mutate('Smoke edit',lambda p:p['nodes'][0].update(capacity=777));assert w.project['nodes'][0]['capacity']==777
            w.undo.undo();assert w.project['nodes'][0]['capacity']==before
            w.undo.redo();assert w.project['nodes'][0]['capacity']==777
            w.undo.undo();w.undo.setClean()
            w.project['duration']=20;w.project['window']=2;w.install_project(w.project)
            w.project['incidents']=[dict(kind='node_restart',target='stp-a',start=5,duration=4,magnitude=1)]
            w.install_project(w.project)
            self.completed.append('native forms, selection, undo/redo')
            w.job_completed.connect(self.next_stage)
            w.start_run()
        except Exception:self.fail(traceback.format_exc())
    def verify_settings(self):
        from . import preferences
        w=self.window;previous=(w.settings,dict(w.preferences),w.pages.currentIndex(),w.settings_page.tabs.currentIndex())
        project=json.dumps(w.project,sort_keys=True);history=w.undo.count();w.settings=QSettings(str(storage.DATA/'preferences-self-test.ini'),QSettings.Format.IniFormat)
        try:
            w.apply_preferences(preferences.defaults(),persist=False);w.settings.setValue('calibration/options','keep');w.settings.setValue('export/style','keep-style')
            w.nav_buttons[7].click();assert w.pages.currentIndex()==7 and w.run_button.isHidden()
            page=w.settings_page;page.fields['sidebarCollapsed'].setChecked(True);assert w.sidebar_collapsed
            page.fields['graph/grid'].setChecked(False);page.fields['graph/linkLabels'].setChecked(False)
            assert not w.graph.show_grid and all(not edge.show_label for edge in w.graph.edge_items.values())
            page.fields['graph/panSpeed'].setValue(2.);assert w.graph.pan_speed==2.
            page.fields['preferences/startPage'].setCurrentIndex(7);assert preferences.load(w.settings)['preferences/startPage']==7
            w.set_sidebar_collapsed(False);assert not page.fields['sidebarCollapsed'].isChecked()
            for theme in ('graphite','porcelain','monochrome','light_monochrome','dark'):
                page.fields['theme'].setCurrentIndex(page.fields['theme'].findData(theme));assert w.theme==theme
                for index,title in enumerate(('appearance','topology','behavior','data')):
                    page.tabs.setCurrentIndex(index);self.app.processEvents()
                    if self.output:page.grab().save(str(self.output/f'{theme}-settings-{title}.png'))
            page.reset();assert preferences.load(w.settings)==preferences.defaults()
            assert w.settings.value('calibration/options')=='keep' and w.settings.value('export/style')=='keep-style'
            assert json.dumps(w.project,sort_keys=True)==project and w.undo.count()==history
            w.show_help('Настройки программы');assert 'Ctrl+,' in w.help_widget.reader.toPlainText()
            self.completed.append('native settings: eight-page navigation, immediate persistent preferences, existing menus synchronized, configurable map navigation/display, safe defaults preserving research and all five themes')
        finally:
            settings,values,page,tab=previous;w.settings=settings;w.apply_preferences(values,persist=False);w.settings_page.tabs.setCurrentIndex(tab);w.show_page(page)
    def verify_testbed(self):
        from .testbed_dialog import TestbedDialog
        from lab import testbed
        from lab.testbed import Profile
        from unittest.mock import patch
        w=self.window;theme=w.theme;dialog=TestbedDialog(w)
        try:
            assert 'def main(request)' in testbed.agent_source()
            p=Profile(name='Interface self-test');dialog.set_profile(p)
            assert dialog.values()==p and p.subscribers[0].key not in dialog.preview.toPlainText()
            assert dialog.subscribers.cellWidget(0,1).echoMode()==dialog.subscribers.cellWidget(0,1).EchoMode.Password
            dialog.add_subscriber();assert dialog.subscribers.rowCount()==2;dialog.subscribers.selectRow(1);dialog.remove_row()
            fixture=dict(source='fixture',clock='wall',prepared=True,core=[dict(component='nrf',alive=True,ready=True)],gnb_alive=True,
                ues=[dict(imsi=p.subscribers[0].imsi,alive=True,registered=True,sessions=[dict(address='10.45.0.2')])],logs={'ue.log':'Interface fixture; not a live measurement'})
            checks=dict(ready=True,kernel='fixture',checks=[dict(check='SCTP',ok=True,detail='fixture')],suggested_ran_bins=['/opt/UERANSIM/build'])
            def synchronous(title,fn,done,*args,**kwargs):done(fn(lambda value:None,lambda:False))
            with patch.object(w,'start_job',side_effect=synchronous),patch.object(testbed,'request',return_value=checks) as rpc:
                dialog.action('preflight');assert rpc.call_args.args[1]=='preflight' and dialog.checks.rowCount()==1
            absent=dict(profile_id=p.id,prepared=False,resource_state='absent',processes_present=False,network=dict(absent=True,complete=False))
            with patch.object(w,'start_job',side_effect=synchronous),patch.object(w,'error') as errors:
                with patch.object(testbed,'request',side_effect=[testbed.AgentError('Not prepared'),absent]):dialog.measure()
                assert errors.call_count==1 and dialog.control_profile is None and dialog.pending_profile is None
                with patch.object(testbed,'request',return_value=dict(profile_id=p.id,prepared=True,resource_state='present')):dialog.action('prepare')
                assert dialog.control_profile.id==p.id and dialog.pending_profile is None
                with patch.object(testbed,'request',return_value=dict(absent,cleaned=True)):dialog.action('cleanup')
                with patch.object(testbed,'request',side_effect=TimeoutError('Unknown outcome')):dialog.action('prepare')
                assert dialog.control_profile is None and dialog.pending_profile.id==p.id
                with patch.object(testbed,'request',return_value=absent):dialog.action('status')
                assert dialog.control_profile is None and dialog.pending_profile is None
            dialog.accept_result(fixture);assert dialog.ues.rowCount()==1
            with patch.object(testbed,'request',return_value=dict(fixture,profile_id=p.id,data_tests=[],procedure_observations=[])):
                saved=testbed.measure(p);reopened=storage.get('testbed_run',saved['id'])
                assert reopened['source']=='open5gs-ueransim' and reopened['clock']=='wall' and reopened['profile_id']==p.id
                assert reopened['profile']['subscribers'][0]['key']=='[скрыто]'
            record=storage.save('testbed_run','Interface fixture',dict(**fixture,data_tests=[dict(imsi=p.subscribers[0].imsi,interface='uesimtun0',target='10.45.0.1',sent=2,received=2,exit_code=0,rtt_samples_ms=[1.,2.],rtt_mean_ms=1.5,rtt_p95_ms=2.)],procedure_observations=[dict(imsi=p.subscribers[0].imsi,events=[dict(procedure='registration',start='fixture-start',end='fixture-end',latency_ms=10,status='success')])]))
            dialog.accept_measurement(record);assert dialog.measurements.rowCount()==1 and dialog.procedures.rowCount()==1
            for name in ('monochrome','light_monochrome','dark','graphite','porcelain'):
                w.apply_theme(name,persist=False);dialog.show()
                for tab in range(dialog.tabs.count()):
                    dialog.tabs.setCurrentIndex(tab);self.app.processEvents()
                    if self.output:assert dialog.grab().save(str(self.output/f'{name}-testbed-{tab}.png'))
            self.completed.append('5G testbed native controller: failed-first-operation recovery, timeout intent reconciled by status, credentials masking, packaged Linux worker, seven tabs in five themes, complete agent response saved/reopened, RPC without live mutations, NAS/ICMP views and separate history')
        finally:
            for control in dialog.compute_controls:
                if control in w.compute_buttons:w.compute_buttons.remove(control)
            dialog.close();dialog.deleteLater();w.apply_theme(theme,persist=False)

    def verify_monitoring(self):
        from .monitor_dialog import MonitorDialog
        from lab import monitor_study
        from lab.presets import preset
        from lab.schema import Project
        from lab.monitoring import default_features
        from .exporting import table_data,export_file,ExportStyle
        w=self.window;theme=w.theme;dialog=MonitorDialog(w)
        try:
            p=preset('diameter').model_dump();p.update(duration=20,window=2,journal_sessions=0)
            p['flows'][0]['rate']=1
            p['monitoring']=dict(enabled=True,members=[dict(node='mme',collector='dra',witness='hss',zone='A')],features=default_features(),poll_s=2,decision_s=2,timeout_s=1,ttl_s=5,faults=[dict(node='mme',start=6,duration=8,mode='scale',factor=.1)])
            p=Project.model_validate(p);dialog.source=p.model_dump();dialog.set_config(p.monitoring.model_dump())
            assert dialog.config()==p.monitoring and dialog.tabs.count()==8
            if self.output:
                from unittest.mock import patch
                invalid=p.monitoring.model_dump();invalid['members'][0]['collector']='missing-node'
                profile=self.output/'invalid-monitoring-profile.json';profile.write_text(json.dumps(dict(contract='monitoring-profile-v1',monitoring=invalid)),encoding='utf-8')
                with patch('native.monitor_dialog.QFileDialog.getOpenFileName',return_value=(str(profile),'JSON')):
                    try:dialog.load_profile()
                    except ValueError:pass
                    else:raise AssertionError('Foreign node profile silently remapped')
                assert dialog.config()==p.monitoring
            data=monitor_study.generate(p,6);record=monitor_study.compare(data,('random_forest',),trees=20)
            dialog.accept_dataset(data);dialog.accept_study(record)
            assert dialog.results.rowCount()==4 and dialog.costs.rowCount()==6
            dialog.results.selectRow(0);dialog.infer();assert dialog.observations.rowCount()==60
            for name in ('monochrome','light_monochrome','dark','graphite','porcelain'):
                w.apply_theme(name,persist=False);dialog.show()
                for tab in (0,1,2,3,4,7):
                    dialog.tabs.setCurrentIndex(tab);self.app.processEvents()
                    if self.output:assert dialog.grab().save(str(self.output/f'{name}-monitoring-{tab}.png'))
            if self.output:
                (self.output/'monitoring-dataset.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
                export_file(self.output/'monitoring-comparison.html',table_data(dialog.results,'Мониторинг'),ExportStyle(),'html')
            self.completed.append('generic monitoring: configurable collectors/zones/witnesses, real CPU/link traffic, numeric feature schema, paired group ablation, custom inference, saved history and eight native tabs in five themes')
        finally:
            for control in (dialog.data_button,dialog.compare_button):
                if control in w.compute_buttons:w.compute_buttons.remove(control)
            dialog.close();dialog.deleteLater();w.apply_theme(theme,persist=False)

    def verify_expanded_ml(self):
        from lab.presets import preset
        from lab.features import SETS
        w=self.window;previous=(w.dataset_record,w.model_record,w.current_threshold,w.feature_set_combo.currentData(),w.evaluation_mode.currentData(),w.theme)
        try:
            p=preset('diameter');p.duration=12;p.window=1;p.incidents=[];p.journal_sessions=0
            for f in p.flows:f.rate=2
            data=research.dataset(p,12,feature_set='combined-v1',topology_variants=True)
            assert data['metadata']['topologies']==3 and len(SETS)==5
            assert sorted(event['kind'] for run in data['provenance'] for event in run['project']['incidents'])==['latency','latency','node_down','node_down','surge','surge']
            imported=research.import_csv(research.csv_text(data['rows']),'Rich telemetry self-test')
            w.dataset_record=imported
            for mode in ('topology','fault'):
                model=research.train(imported['id'],trees=20,evaluation_mode=mode)
                assert model['feature_set']=='combined-v1' and len(model['features'])==30
                assert model['operational']['available'] and model['evaluation']['holdout']
                w.model_record=model;w.refresh_ml();w.evaluation_mode.setCurrentIndex(w.evaluation_mode.findData(mode));self.app.processEvents()
                assert w.holdout_value.count()>0 and 'эпизодов' in w.operational_info.text()
            for theme in ('dark','graphite','porcelain','monochrome','light_monochrome'):
                w.apply_theme(theme,persist=False);w.show_page(3);self.app.processEvents();self.capture(theme+'-expanded-ml')
                scroll=w.pages.widget(3);scroll.ensureWidgetVisible(w.operational_info);self.app.processEvents();self.capture(theme+'-ml-diagnostics');scroll.verticalScrollBar().setValue(0)
            w.threshold.setValue(900);assert w.current_threshold==.9
            assert len(ml.predict(model['id'],data['rows'],model['threshold']))==len(data['rows'])
            self.completed.append('versioned ML: 30 event-clock inputs, three real topologies, held-out topology/fault groups, detection delay, false alarms/hour, rich CSV, inference and all five native themes')
        finally:
            w.dataset_record,w.model_record,w.current_threshold,key,mode,theme=previous
            w.feature_dataset_id=None;w.refresh_ml();w.feature_set_combo.setCurrentIndex(w.feature_set_combo.findData(key));w.evaluation_mode.setCurrentIndex(w.evaluation_mode.findData(mode))
            w.apply_theme(theme,persist=False);w.show_page(0)
    def verify_standard_ml(self):
        from lab.features import LEGACY
        from .ml_options import ModelOptionsDialog
        w=self.window;previous=(w.dataset_record,w.model_record,w.current_threshold,w.algorithm.currentData(),w.theme)
        rng=np.random.default_rng(81);rows=[];models=[]
        for run in range(12):
            for tick in range(20):
                truth=int(tick>=10);values=rng.normal(size=len(LEGACY))+truth*np.linspace(1.,3.,len(LEGACY));values[-1]=0.
                rows.append(dict(zip(LEGACY,values),run_id=str(run),time=tick,label=truth,window_seconds=1,fault_intervals=[[10,20]],feature_contract='procedures-v2'))
        try:
            assert w.algorithm.count()==25
            for key in ml.ALGORITHMS:
                w.algorithm.setCurrentIndex(w.algorithm.findData(key));assert w.model_options.algorithm==key
                parameters=w.model_options.values();result=ml.train(rows,key,trees=20,parameters=parameters);models.append(result)
                test=[r for r in rows if r['run_id'] in result['split']['test']['runs']]
                np.testing.assert_allclose([r['score'] for r in ml.predict(result['model_id'],test,result['threshold'])],result['test_scores'],atol=1e-9)
                assert np.isfinite(result['test_scores']).all()
                if key in ('svm','mlp','gaussian_mixture'):
                    w.model_record=dict(result,id=result['model_id']);w.refresh_ml();w.show_page(3);self.app.processEvents()
                    w.exact_threshold_changed(-3.25);assert w.current_threshold==-3.25 and w.threshold_input.value()==-3.25
                    if key=='svm':
                        w.apply_theme('graphite',persist=False);self.capture('ml-catalogue-svm');scroll=w.pages.widget(3);scroll.ensureWidgetVisible(w.threshold_input);self.app.processEvents();self.capture('ml-margin-threshold');scroll.verticalScrollBar().setValue(0)
                    elif key=='mlp':
                        w.apply_theme('porcelain',persist=False);self.capture('ml-catalogue-mlp')
            dialog=ModelOptionsDialog(w,['random_forest','svm','mlp','local_outlier_factor'],trees=25)
            try:
                dialog.show();self.app.processEvents();dialog.selector.setCurrentIndex(2);self.app.processEvents()
                assert dialog.result_config()['mlp']['hidden_layer_sizes']==[64,32]
                if self.output:dialog.grab().save(str(self.output/'ml-model-options.png'))
            finally:dialog.close();dialog.deleteLater()
            self.completed.append('25 native ML models: individual parameters, training, persistence/reload and inference; signed exact thresholds; train-only normalization; monitoring parameter editor')
            if self.output:(self.output/'ml-algorithms.json').write_text(json.dumps([dict(algorithm=m['algorithm'],f1=m['metrics']['f1'],score_kind=m['score_kind'],threshold=m['threshold'],fit=m['fit']) for m in models],ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            for model in models:(ml.DATA/'models'/f'{model["model_id"]}.joblib').unlink(missing_ok=True)
            w.dataset_record,w.model_record,w.current_threshold,key,theme=previous;w.display_model_id=None;w.refresh_ml();w.algorithm.setCurrentIndex(w.algorithm.findData(key));w.apply_theme(theme,persist=False);w.show_page(0)
    def verify_attacks(self):
        from lab.schema import Project
        from lab.attacks import PRESETS,from_preset,decode_template
        from lab.presets import preset
        from .attacks import AttackEditor,StepEditor
        w=self.window;page=w.attacks_widget;before=json.dumps(w.project,sort_keys=True);history=w.undo.index();theme=w.theme
        try:
            p=Project.model_validate(w.project);scenario=from_preset(p,'compound').model_dump()
            dialog=AttackEditor(w,w.project,scenario);dialog.show();self.app.processEvents()
            assert dialog.buttons.button(dialog.buttons.StandardButton.Ok).isEnabled()
            dialog.clone_step();assert len(dialog.steps)==4;dialog.remove_step();assert len(dialog.steps)==3
            step=StepEditor(dialog,w.project);step.effect.setCurrentIndex(step.effect.findData('loss_absolute'));step.target.setCurrentIndex(0);step.magnitude.setValue(.25);step.profile.setCurrentIndex(step.profile.findData('pulse'));step.accept();assert step.result_data['magnitude']==.25 and step.result_data['profile']=='pulse';step.deleteLater()
            for mode in ('dark','monochrome','light_monochrome'):
                w.apply_theme(mode,persist=False);dialog.show();self.app.processEvents()
                if self.output:dialog.grab().save(str(self.output/f'{mode}-attack-editor.png'))
            dialog.accept();page.store(dialog.result_data);dialog.deleteLater()
            assert len(w.project['attacks'])==1;page.clone_selected();assert len(w.project['attacks'])==2
            page.toggle_selected();assert not page.selected()['enabled'];w.undo.undo();assert page.selected()['enabled']
            page.save_template();assert page.saved.count()>0
            saved=storage.listing('attack_template')[0];loaded=decode_template(storage.get('attack_template',saved['id'])['template']);assert loaded.steps
            storage.delete('attack_template',saved['id']);page.reload_templates()
            config=preset('5g').model_dump();config['duration']=10
            for f in config['flows']:f['rate']=.3
            config['attacks']=[from_preset(Project.model_validate(config),'5g_registration').model_dump(),from_preset(Project.model_validate(config),'sbi_exhaustion').model_dump()]
            w.mutate('Проверка конструктора 5G',lambda p:p.update(config));record=research.run(w.project)
            w.run_record=record;w.refresh_measurements();assert page.report_table.rowCount()==2
            for mode in ('dark','monochrome','light_monochrome'):
                w.apply_theme(mode,persist=False);w.show_page(6);self.app.processEvents();page.tabs.setCurrentIndex(0);self.capture(f'{mode}-attack-builder');page.tabs.setCurrentIndex(2);self.capture(f'{mode}-attack-report')
            assert len(PRESETS)==12 and len(w.export_sources())==29
            self.completed.append('attack builder: 12 presets, custom step/profile editing, native timeline and reports in all themes, clone/toggle/undo, local templates, 5G effects and styled table export sources')
        finally:
            while w.undo.index()>history:w.undo.undo()
            assert json.dumps(w.project,sort_keys=True)==before
            w.run_record=None;page.refresh_report();w.apply_theme(theme,persist=False);w.show_page(0)
    def verify_resource_services(self):
        from lab.presets import preset
        from .service_lists import ListDialog
        w=self.window;before=json.dumps(w.project,sort_keys=True);history=w.undo.index();theme=w.theme
        try:
            p=preset('5g-mobile').model_dump();p['duration']=12;p['window']=1;p['flows'][0]['mobility'][0]['time']=3
            for f in p['flows']:f['rate']=.4
            w.mutate('Проверка БД, QoS и мобильности',lambda config:config.update(p));w.run_record=research.run(w.project);w.refresh_measurements()
            assert w.resource_table.rowCount()==15 and w.mobility_table.rowCount()==1
            assert w.run_record['mobility'][0]['status']=='completed'
            assert any(s['requests']>0 for s in w.run_record['resources']['database'].values())
            assert w.run_record['user_plane']['sent']>0
            node=next(n for n in w.project['nodes'] if n['kind']=='UPF');d=node_editor(w,node);assert d.values()['storage_model']=='queued' and d.values()['context_limit']==2000
            flow=flow_editor(w,w.project,w.project['flows'][0]);assert flow.values()['mobility'][0]['gnb']=='gnb2'
            link=link_editor(w,w.project);assert link.values()['peer_routing_context'] is None and link.values()['shutdown_at_s'] is None
            rules=ListDialog(w,w.project,'upf',[dict(name='QoS',precedence=10,flow_id='',dnn='internet',snssai='',direction='both',action='forward',rate_mbps=1,burst_bytes=12000,max_packet_bytes=65536)])
            for name in ('monochrome','light_monochrome','dark'):
                w.apply_theme(name,persist=False);w.show_page(2);w.details_tabs.setCurrentIndex(9);self.app.processEvents();self.capture(name+'-database-resources')
                w.details_tabs.setCurrentIndex(10);self.app.processEvents();self.capture(name+'-mobility')
                d.tabs.setCurrentIndex(d.tabs.count()-2);d.show();self.app.processEvents()
                if self.output:d.grab().save(str(self.output/f'{name}-resource-editor.png'))
                d.hide();rules.show();self.app.processEvents()
                if self.output:rules.grab().save(str(self.output/f'{name}-upf-rules.png'))
                rules.hide()
            for dialog in (d,flow,link,rules):dialog.deleteLater()
            self.completed.append('realism 2.5: finite DB pool, LRU/TTL caches, bounded contexts, PDU release, application/QoS forms, native UPF rules, resource-bearing gNB/AMF/UPF mobility and two exportable telemetry tables in all themes')
        finally:
            while w.undo.index()>history:w.undo.undo()
            assert json.dumps(w.project,sort_keys=True)==before
            w.run_record=None;w.apply_theme(theme,persist=False);w.show_page(0)
    def verify_procedure_viewer(self):
        from lab.schema import Project
        from lab.simulator import simulate
        from .export_dialog import ExportDialog
        w=self.window;previous=w.run_record;theme=w.theme
        p=Project(name='Procedure viewer fixture',duration=10,window=1,control_plane=False,routing_knowledge='shared',journal_procedure_id=1,
            nodes=[dict(id='a',name='Access',kind='MME',x=0,y=0,capacity=100000),dict(id='b',name='HSS',kind='HSS',x=300,y=0,storage_model='queued',database_ms=8,cache_entries=0)],
            links=[dict(id='ab',source='a',target='b',protocol='Diameter',latency_ms=10,jitter_ms=0,loss=0,reverse_latency_ms=100,rto_ms=10,rto_min_ms=10,rto_max_ms=10,transport_retries=2)],
            flows=[dict(id='f',source='a',target='b',protocol='Diameter',operation='Diameter ULR',rate=5)])
        result=dict(id='viewer-fixture',project=p.model_dump(),**simulate(p));w.run_record=result
        try:
            w.show_procedure(1);dialog=w.procedure_dialog;session=dialog.session
            assert session['procedure_id']==1 and session['events'][0]['kind']=='procedure_start'
            assert any(event['kind']=='transport_retry' for event in session['events'])
            dialog.goto(0);assert dialog.timers.rowCount()==0 and dialog.contexts.rowCount()==0
            dialog.next_timeout();assert session['events'][dialog.cursor]['kind']=='timer_expired'
            cause=session['events'][dialog.cursor]['cause_event'];dialog.goto_cause();assert dialog.cursor==cause-1
            dialog.goto(8);dialog.toggle_play();dialog.play_step();assert dialog.cursor==9;dialog.timer.stop()
            for name in ('monochrome','light_monochrome','dark','graphite','porcelain'):
                w.apply_theme(name,persist=False);dialog.goto(min(len(session['events'])-1,80));dialog.show();self.app.processEvents()
                if self.output:dialog.grab().save(str(self.output/f'{name}-procedure-viewer.png'))
            dialog.goto(len(session['events'])-1);dialog.tabs.setCurrentIndex(2);self.app.processEvents()
            if self.output:
                dialog.grab().save(str(self.output/'procedure-timers.png'))
                (self.output/'procedure-journal.json').write_text(json.dumps(result['procedure_journal'],ensure_ascii=False,indent=2),encoding='utf-8')
                for fmt in ('svg','png','pdf'):dialog.diagram.export_fragment(self.output/('procedure-fragment.'+fmt),fmt)
            legacy=dict(result);legacy.pop('procedure_journal');dialog.set_run(legacy,1);assert 'Подробного журнала нет' in dialog.info.text()
            dialog.set_run(result,1);dialog.goto(0);dialog.toggle_play();dialog.hide();assert not dialog.timer.isActive()
            self.completed.append('procedure stepper: causal journals, real CPU/DB/channel timing, timers/retries/context snapshots, legacy-record handling, playback cleanup, five themes and SVG/PNG/PDF fragments')
        finally:
            if getattr(w,'procedure_dialog',None):w.procedure_dialog.hide()
            w.run_record=previous;w.apply_theme(theme,persist=False)
    def verify_validation(self):
        from lab.schema import Project,Incident
        from lab.validation import synthetic_example,validate,report_html
        from lab.procedures import ProcedureSimulation
        from .validation_dialog import ValidationOptions
        from .export_dialog import ExportDialog
        from .exporting import chart_data,export_file,ExportStyle
        w=self.window;theme=w.theme;before=json.dumps(w.project,sort_keys=True)
        p=Project(name='Проверка сравнения',duration=10,window=1,control_plane=False,routing_knowledge='distributed',
            nodes=[dict(id='a',name='Access',kind='MME',x=0,y=0,processing_ms=0,capacity=100000),dict(id='b',name='HSS',kind='HSS',x=300,y=0,processing_ms=0,capacity=100000)],
            links=[dict(id='ab',source='a',target='b',protocol='Diameter',latency_ms=10,jitter_ms=0,loss=0,bandwidth_mbps=1000)],
            flows=[dict(id='f',source='a',target='b',protocol='Diameter',operation='Diameter ULR',rate=8,max_retries=0)])
        example=synthetic_example(p);result=validate(p,example['text'],options=dict(seeds=1,min_samples=20,expected_conditions=[dict(scenario='нет измерений',offered_rate=40)]))
        assert not result['hardware_accuracy_proven'] and result['conditions'][-1]['status']=='insufficient'
        p.incidents=[Incident(kind='node_down',target='b',start=2,duration=3,detection_ms=0,recovery_ms=0),Incident(kind='node_down',target='b',start=3,duration=4,detection_ms=0,recovery_ms=0)]
        for node in p.nodes:node.switch_ms=0
        faults=ProcedureSimulation(p).run()['control_plane']['view_events']
        assert not [event for event in faults if event['resource']=='node' and event['target']=='b' and event['up'] and 2<=event['time']<7]
        self.completed.append('overlapping node outages [2,5) and [3,7) do not advertise recovery before 7 seconds')
        try:
            w.show_validation();dialog=w.validation_dialog;dialog.set_measurements(example['text'],'Синтетические измерения');dialog.accept_result(result,'Проверка сравнения')
            assert dialog.conditions.rowCount()==2 and len(dialog.export_sources())==4
            for name in ('monochrome','light_monochrome','dark','graphite','porcelain'):
                w.apply_theme(name,persist=False);dialog.tabs.setCurrentIndex(0);dialog.show();self.app.processEvents()
                if self.output:dialog.grab().save(str(self.output/f'{name}-validation.png'))
                dialog.tabs.setCurrentIndex(2);self.app.processEvents()
                if self.output:dialog.grab().save(str(self.output/f'{name}-validation-coverage.png'))
            options=ValidationOptions(dialog,dialog.options);options.show();self.app.processEvents()
            if self.output:options.grab().save(str(self.output/'validation-options.png'))
            options.close();options.deleteLater()
            exporter=ExportDialog(dialog,dialog.export_sources(),initial='validation_cdf');assert exporter.source_combo.count()==4;exporter.close();exporter.deleteLater()
            if self.output:
                (self.output/'validation-measurements-synthetic.csv').write_text(example['text'],encoding='utf-8-sig')
                (self.output/'validation-report.html').write_text(report_html(result),encoding='utf-8')
                (self.output/'validation-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                export_file(self.output/'validation-cdf.svg',chart_data(dialog.cdf_chart,'Распределение задержек','Задержка, ms','Накопленная доля'),ExportStyle(),'svg')
            self.completed.append('measured-network validation: frozen tolerances, ECDF/time overlays, load/failure coverage and missing evidence, saved reports, all five themes and styled graph/table export')
        finally:
            if getattr(w,'validation_dialog',None):w.validation_dialog.hide()
            w.apply_theme(theme,persist=False);assert json.dumps(w.project,sort_keys=True)==before
    def verify_startup(self):
        from .startup import StartupSplash,SPLASH_COLORS
        state=self.app.startup_state
        assert all(state[key] for key in ('splash_shown','before_window_import','before_ml_import','main_window_visible','completed','splash_hidden')),str(state)
        assert not self.app.startup_splash.isVisible()
        for theme in SPLASH_COLORS:
            splash=StartupSplash(theme);splash.show_startup();splash.update_status('Подготовка интерфейса…')
            assert splash.isVisible() and splash.width()==400 and splash.height()==176
            QTest.mouseClick(splash,Qt.MouseButton.LeftButton,pos=QPoint(150,90));assert splash.isVisible()
            if self.output:splash.grab().save(str(self.output/f'{theme}-startup-splash.png'))
            assert splash.complete(self.window) and not splash.isVisible();splash.deleteLater()
        self.completed.append('startup splash is visible before main-window and ML imports, follows all themes, survives clicks and closes only after the main window is visible')
    def verify_handbook(self):
        from lab.schema import NODE_KINDS,LINK_PROTOCOLS
        w=self.window;original=json.dumps(w.project,sort_keys=True);history=w.undo.count()
        w.activateWindow();w.graph.setFocus();self.app.processEvents();QTest.keyClick(w.graph,Qt.Key.Key_F1);self.app.processEvents()
        assert w.pages.currentIndex()==5 and w.nav_buttons[5].isChecked()
        help_page=w.help_widget;assert len(help_page.chapters)>=25
        w.show_help('Словарь сокращений');text=help_page.reader.toPlainText()
        for term in NODE_KINDS+LINK_PROTOCOLS:assert term in text,'Missing glossary term: '+term
        help_page.search.setText('Коэффициент вариации');self.app.processEvents();assert help_page.matches
        assert 'Коэффициент вариации' in help_page.reader.textCursor().selectedText()
        self.capture('native-help-search')
        help_page.search.setText('неизвестное_слово_которого_нет_в_справке');assert 'Ничего не найдено' in help_page.status.text()
        w.show_help('Основы сигнальных сетей');assert 'Сигнализация' in help_page.reader.toPlainText();self.capture('native-help-basics')
        assert json.dumps(w.project,sort_keys=True)==original and w.undo.count()==history
        w.show_page(0);w.set_topology_expanded(True);w.activateWindow();w.graph.setFocus();self.app.processEvents();QTest.keyClick(w.graph,Qt.Key.Key_F1);self.app.processEvents()
        assert w.pages.currentIndex()==5 and not w.topology_expanded
        w.show_help('Начало работы');w.show_page(0)
        self.completed.append(f'offline handbook: {len(help_page.chapters)} chapters, all node/protocol glossary entries, full-text Cyrillic search, no-results recovery, F1 from expanded topology and native help navigation')
    def verify_expanded_topology(self):
        from lab.schema import NODE_KINDS
        w=self.window;original=json.dumps(w.project,sort_keys=True);selection=w.selected;theme=w.theme;settings=w.settings;size=w.size()
        w.settings=QSettings(str(storage.DATA/'expanded-self-test.ini'),QSettings.Format.IniFormat)
        def drag(widget,delta):
            start=QPoint(15,widget.height()//2);QTest.mousePress(widget,Qt.MouseButton.LeftButton,pos=start)
            QTest.mouseMove(widget,start+delta);QTest.mouseRelease(widget,Qt.MouseButton.LeftButton,pos=start+delta);self.app.processEvents()
        try:
            for name in ('dark','monochrome','light_monochrome'):
                w.apply_theme(name,persist=False);w.show_page(0);self.app.processEvents();sizes=w.topology_split.sizes()
                w.expand_topology_button.click();self.app.processEvents();workspace=w.expanded_workspace
                assert w.topology_expanded and not w.sidebar.isVisible() and not w.workspace_right.isVisible()
                assert w.graph.isVisible() and w.graph.parentWidget() is workspace and workspace.geometry()==w.centralWidget().rect()
                assert workspace.inspector_panel.isVisible() and workspace.inspector_button.isChecked()
                inspector_hit=workspace.childAt(workspace.inspector_panel.geometry().center())
                assert inspector_hit is workspace.inspector_panel or workspace.inspector_panel.isAncestorOf(inspector_hit),'The graph covers the floating inspector'
                assert set(workspace.node_buttons)==set(NODE_KINDS)
                workspace.fit_button.click();self.app.processEvents();self.capture(name+'-expanded-topology')
                count=len(w.project['nodes'])
                for kind in NODE_KINDS:
                    workspace.node_buttons[kind].click();self.app.processEvents()
                    assert self.app.activeModalWidget() is None
                    assert w.project['nodes'][-1]['kind']==kind and len(w.project['nodes'])==count+1
                    assert w.inspector_fields['kind'].currentData()==kind
                    node=w.project['nodes'][-1];assert all(abs(other['x']-node['x'])>=180 or abs(other['y']-node['y'])>=115 for other in w.project['nodes'][:-1])
                    w.undo.undo();assert json.dumps(w.project,sort_keys=True)==original
                workspace.node_buttons['AMF'].click();w.inspector_fields['capacity'].setValue(1234);w.apply_inspector()
                assert w.project['nodes'][-1]['capacity']==1234;w.undo.undo();w.undo.undo()
                w.undo.redo();assert w.project['nodes'][-1]['kind']=='AMF';w.undo.undo()
                tools_position=workspace.tools.pos();drag(workspace.tools.header,QPoint(65,35));assert workspace.tools.pos()!=tools_position
                inspector_position=workspace.inspector_panel.pos();drag(workspace.inspector_panel.header,QPoint(-65,40));assert workspace.inspector_panel.pos()!=inspector_position
                original_window_size=w.size();original_inspector_size=workspace.inspector_panel.size()
                drag(workspace.inspector_grip,QPoint(30,-35));assert workspace.inspector_panel.size()!=original_inspector_size and w.size()==original_window_size
                workspace.close_inspector.click();assert not workspace.inspector_panel.isVisible()
                workspace.inspector_button.click();assert workspace.inspector_panel.isVisible()
                workspace.connect_button.click();assert w.graph.connection_mode and w.draw_link_button.isChecked() and w.draw_link_action.isChecked()
                QTest.keyClick(w.graph,Qt.Key.Key_Escape);assert w.topology_expanded and not w.graph.connection_mode and not workspace.connect_button.isChecked()
                workspace.arrange_button.click();w.undo.undo();assert json.dumps(w.project,sort_keys=True)==original
                for control in (workspace.link_button,workspace.export_button):
                    answered=[]
                    def dismiss():
                        dialog=self.app.activeModalWidget();answered.append(dialog is not None)
                        if dialog is not None:dialog.reject()
                    QTimer.singleShot(30,dismiss);control.click();assert answered==[True]
                self.capture(name+'-expanded-topology-moved')
                w.resize(1080,700);self.app.processEvents()
                assert workspace.geometry()==w.centralWidget().rect()
                for floating in (workspace.tools,workspace.inspector_panel):assert workspace.rect().contains(floating.geometry())
                self.capture(name+'-expanded-topology-small');w.resize(size);self.app.processEvents()
                saved_position=workspace.tools.pos();workspace.exit_button.click();self.app.processEvents()
                assert not w.topology_expanded and w.sidebar.isVisible() and w.workspace_right.isVisible()
                assert w.topology_split.count()==2 and w.inspector.parentWidget() is w.topology_split and w.graph.parentWidget() is not workspace
                assert abs(w.topology_split.sizes()[1]-sizes[1])<=2
                w.activateWindow();w.graph.setFocus();self.app.processEvents()
                QTest.keyClick(w.graph,Qt.Key.Key_F11);self.app.processEvents();assert w.topology_expanded,'F11 did not expand the topology'
                assert workspace.tools.pos()==saved_position,'Floating panel position was not restored'
                QTest.keyClick(w.graph,Qt.Key.Key_Escape);self.app.processEvents();assert not w.topology_expanded,'Escape did not restore the normal workspace'
                w.set_topology_expanded(True);w.show_page(1);assert not w.topology_expanded and w.pages.currentIndex()==1
            assert json.dumps(w.project,sort_keys=True)==original
            self.completed.append('expanded topology: all 20 one-click node types, undo/redo, draggable tools and resizable inspector, native link/export dialogs, theme switching, small window bounds and complete layout restoration')
        finally:
            w.set_topology_expanded(False);w.resize(size);w.selected=selection;w.install_project(json.loads(original));w.undo.clear();w.show_page(0);w.settings=settings;w.apply_theme(theme,persist=False)
    def verify_mouse_links(self):
        w=self.window;original_theme=w.theme;before=json.dumps(w.project,sort_keys=True);count=len(w.project['links']);coordinates=[(n['x'],n['y']) for n in w.project['nodes']]
        def point(nid,port=False):return w.graph.mapFromScene(w.graph.node_items[nid].mapToScene(QPointF(78 if port else 0,4 if port else 0)))
        def answer(accept=True,protocol='M3UA'):
            results=[];errors=[]
            def respond(attempt=0):
                d=self.app.activeModalWidget()
                if d is None:
                    if attempt<50:QTimer.singleShot(10,lambda:respond(attempt+1))
                    else:errors.append('Link dialog did not open')
                    return
                try:
                    values=d.values();assert values['source']=='msc' and values['target']=='sg' and values['protocol']==protocol
                    results.append(values)
                    if self.output:d.grab().save(str(self.output/'mouse-link-parameters.png'))
                    d.accept() if accept else d.reject()
                except Exception as error:errors.append(str(error));d.reject()
            QTimer.singleShot(25,respond)
            return results,errors
        try:
            for theme in ('dark','monochrome','light_monochrome'):
                w.apply_theme(theme,persist=False);w.show_page(0);self.app.processEvents();w.graph.fit()
                QTest.mouseClick(w.graph.viewport(),Qt.MouseButton.LeftButton,pos=point('msc'))
                QTest.mousePress(w.graph.viewport(),Qt.MouseButton.LeftButton,pos=point('msc',True))
                QTest.mouseMove(w.graph.viewport(),point('sg'));assert w.graph.connection_hover is w.graph.node_items['sg']
                self.capture(theme+'-mouse-link-preview');assert json.dumps(w.project,sort_keys=True)==before
                results,errors=answer();QTest.mouseRelease(w.graph.viewport(),Qt.MouseButton.LeftButton,pos=point('sg'));QTest.qWait(60)
                assert results and not errors,str(errors);assert len(w.project['links'])==count+1
                link=w.project['links'][-1];assert (link['source'],link['target'],link['protocol'])==('msc','sg','M3UA')
                assert coordinates==[(n['x'],n['y']) for n in w.project['nodes']]
                self.capture(theme+'-mouse-link-created');w.undo.undo();assert json.dumps(w.project,sort_keys=True)==before
                w.undo.redo();assert len(w.project['links'])==count+1;w.undo.undo()
            w.draw_link_button.click();QTest.mouseClick(w.graph.viewport(),Qt.MouseButton.LeftButton,pos=point('msc'));QTest.keyClick(w.graph,Qt.Key.Key_Escape)
            assert not w.graph.connection_mode and not w.draw_link_button.isChecked() and not w.draw_link_action.isChecked()
            assert w.graph.connection_preview is None and json.dumps(w.project,sort_keys=True)==before
            w.show_page(1);w.draw_link_action.trigger();self.app.processEvents();assert w.pages.currentIndex()==0 and w.graph.connection_mode
            QTest.mouseClick(w.graph.viewport(),Qt.MouseButton.LeftButton,pos=point('msc'))
            results,errors=answer(False);QTest.mouseClick(w.graph.viewport(),Qt.MouseButton.LeftButton,pos=point('sg'));QTest.qWait(60)
            assert results and not errors,str(errors);assert json.dumps(w.project,sort_keys=True)==before
            assert not w.draw_link_button.isChecked() and not w.draw_link_action.isChecked()
            self.completed.append('mouse link creation: port drag in all themes, two-click mode, prefilled dialog, cancellation, unchanged positions and undo/redo')
        finally:w.graph.set_connection_mode(False);w.apply_theme(original_theme,persist=False)
    def verify_clusters(self):
        from copy import deepcopy
        from lab.presets import preset
        from lab.fiveg import FiveGSimulation
        from lab.schema import Incident,StateCluster
        from .cluster_dialog import cluster_editor
        from .export_dialog import ExportDialog
        from .exporting import export_file
        w=self.window;previous=w.run_record;project=deepcopy(w.project);theme=w.theme
        p=preset('5g-ha');p.duration=10;p.window=1;p.control_plane=False;p.routing_knowledge='shared';p.flows[0].rate=0;p.flows[0].subscribers=1
        for link in p.links:link.loss=0;link.jitter_ms=0
        p.incidents=[Incident(kind='node_restart',target='amf',start=2,duration=6,detection_ms=0,recovery_ms=0)]
        for c in p.clusters:c.heartbeat_ms=100;c.heartbeat_timeout_ms=300;c.failover_ms=200;c.sync_ms=200
        class Scheduled(FiveGSimulation):
            def prepare(self):super().prepare();self.total=1;self.push(100,'new',(1,self.p.flows[0]))
        result=dict(id='cluster-fixture',name='AMF / SMF / UDR · резерв после перезапуска',project=p.model_dump(),**Scheduled(p).run())
        assert result['summary']['delivered']==1 and any(v['event']=='session_rebound' for v in result['clusters']['events'])
        try:
            w.install_project(p.model_dump());w.run_record=result;w.show_clusters();d=w.cluster_dialog
            assert d.configuration.rowCount()==3 and d.groups.rowCount()==3 and d.views.rowCount()==6 and d.events.rowCount()>0
            editor=cluster_editor(d,w.project,w.project['clusters'][0]);assert StateCluster.model_validate(editor.values()).members==p.clusters[0].members
            editor.tabs.setCurrentIndex(1);editor.show();self.app.processEvents()
            if self.output:editor.grab().save(str(self.output/'cluster-options.png'))
            editor.close();editor.deleteLater()
            before=deepcopy(w.project['clusters']);d.configs[0]['replication_delay_ms']+=10;d.apply_config();assert w.project['clusters'][0]['replication_delay_ms']==before[0]['replication_delay_ms']+10
            w.undo.undo();assert w.project['clusters']==before;d.reload()
            for name in ('monochrome','light_monochrome','dark','graphite','porcelain'):
                w.apply_theme(name,persist=False);d.tabs.setCurrentIndex(1);d.show();self.app.processEvents()
                if self.output:d.grab().save(str(self.output/f'{name}-cluster-results.png'))
            for index,title in [(0,'configuration'),(2,'views'),(3,'events')]:
                d.tabs.setCurrentIndex(index);self.app.processEvents()
                if self.output:d.grab().save(str(self.output/f'cluster-{title}.png'))
            export=ExportDialog(d,d.export_sources());assert len(export.sources)==3 and len(export.data().rows)==3
            if self.output:
                for fmt in ('csv','json','xlsx','html','pdf'):export_file(self.output/('cluster-results.'+fmt),export.data(),export.style(),fmt)
                (self.output/'state-clusters.json').write_text(json.dumps(result['clusters'],ensure_ascii=False,indent=2),encoding='utf-8')
            export.close();export.deleteLater()
            w.run_record=dict(id='legacy',project=p.model_dump());d.load_result();assert d.groups.rowCount()==0 and d.views.rowCount()==0 and 'отсутствует' in d.info.text()
            w.run_record=result;d.load_result();d.close()
            self.completed.append('state clusters: AMF/SMF/UDR HA preset, resource-bearing replication, session failover, native member/timing controls, project undo, saved views/events, legacy handling and styled exports in five themes')
        finally:w.run_record=previous;w.install_project(project);w.apply_theme(theme,persist=False)

    def verify_experiments(self):
        from copy import deepcopy
        from lab import experiments
        from .export_dialog import ExportDialog
        from .exporting import export_file
        w=self.window;d=w.experiment_dialog;r=d.record;project=deepcopy(w.project);theme=w.theme
        assert r['contract']=='experiments-v1' and len(r['results'])==12
        assert d.points_table.rowCount()==2 and d.runs_table.rowCount()==12
        assert all(v['warmup_s']==2 and v['observation_s']==8 for v in r['results'])
        assert [v['seed'] for v in r['results'][:6]]==[v['seed'] for v in r['results'][6:]]
        assert d.run_button.isEnabled()
        d.points_table.selectRow(1);d.apply_point();assert w.project['flows'][0]['rate']==r['project']['flows'][0]['rate'];w.undo.undo();assert w.project==project
        w.refresh_library();w.refresh_results()
        index=next(i for i in range(w.series_table.rowCount()) if w.series_table.item(i,4).text()==r['id'])
        w.series_table.selectRow(index);w.open_sweep();assert d.record['id']==r['id']
        for name in ('monochrome','light_monochrome','dark','graphite','porcelain'):
            w.apply_theme(name,persist=False);d.tabs.setCurrentIndex(0);self.app.processEvents()
            if self.output:d.grab().save(str(self.output/f'{name}-experiment-plan.png'))
            d.tabs.setCurrentIndex(1);self.app.processEvents()
            if self.output:d.grab().save(str(self.output/f'{name}-experiment-results.png'))
        export=ExportDialog(d,d.export_sources());assert len(export.sources)==3
        assert export.snapshots['experiment_chart']['all'].x_label=='Номер точки'
        if self.output:
            for key in ('experiment_points','experiment_runs'):
                for fmt in ('CSV','JSON','XLSX','HTML','PDF'):
                    snapshot=export.snapshots[key]['all'];path=self.output/(key+'.'+fmt.lower());export_file(path,snapshot,export.style(),fmt.lower());assert path.stat().st_size>0
            export_file(self.output/'experiment-chart.svg',export.snapshots['experiment_chart']['all'],export.style(),'svg')
        export.close();export.deleteLater()
        d.mode.setCurrentIndex(d.mode.findData('capacity'));d.adaptive.setChecked(True);d.repetitions.setValue(30);d.minimum.setValue(10)
        d.tabs.setCurrentIndex(0);self.app.processEvents()
        assert d.capacity_controls.isVisible() and not d.axes.isEnabled() and not d.absolute.isEnabled()
        assert d.preview()['runs_max']==240
        d.set_record(r);w.show_help('Серии экспериментов');assert 'Прогрев' in w.help_widget.reader.toPlainText()
        d.close();w.apply_theme(theme,persist=False)
        self.completed.append('experiment constructor: background repeats, exact warmup, paired seeds, native multidimensional/capacity controls, saved-series reopening, undo, interval charts and styled exports in five themes')

    def verify_long_runs(self):
        from lab import long_runs,checkpoints
        from .export_dialog import ExportDialog
        from .exporting import export_file
        w=self.window;d=w.long_run_dialog;theme=w.theme;meta=long_runs.get(d.current_id)
        assert meta['status']=='completed' and meta['records']>0
        self.assert_long_physics=checkpoints.engine_for(self.long_source).run()
        actual=storage.get('run',meta['result_id'])
        assert actual['summary']==self.assert_long_physics['summary'] and actual['windows']==self.assert_long_physics['windows']
        assert meta['compaction']['messages']>0 and d.jobs_table.rowCount()>0
        d.load_stream();assert d.stream_table.rowCount()>0
        export=ExportDialog(d,[dict(key='long_stream',kind='table',widget=d.stream_table,title='Длительный прогон · записи')])
        if self.output:
            count=long_runs.export_stream(meta['id'],self.output/'long-run-events.jsonl')
            assert count==meta['records'] and long_runs.export_stream(meta['id'],self.output/'long-run-events.csv','csv')==count
            for fmt in ('json','xlsx','pdf'):export_file(self.output/('long-run-visible.'+fmt),export.data(),export.style(),fmt)
        export.close();export.deleteLater()
        for name in ('monochrome','light_monochrome','dark','graphite','porcelain'):
            w.apply_theme(name,persist=False)
            for tab,title in [(1,'jobs'),(2,'stream')]:
                d.tabs.setCurrentIndex(tab);self.app.processEvents()
                if self.output:d.grab().save(str(self.output/f'{name}-long-run-{title}.png'))
        d.duration.setValue(86400);d.measurement_window.setValue(1);d.source['flows'][0]['rate']=100;d.load.setValue(1);d.fit_budget()
        assert d.project().duration==86400 and d.measurement_window.value()>=9
        d.tabs.setCurrentIndex(0);self.app.processEvents()
        if self.output:d.grab().save(str(self.output/'long-run-plan.png'))
        w.show_help('Длительные прогоны');assert 'Контрольная точка' in w.help_widget.reader.toPlainText()
        d.close();w.run_record=self.long_previous;w.refresh_measurements();w.apply_theme(theme,persist=False)
        self.completed.append('durable long runs: native background pause, close-request checkpoint, exact resume, stable final telemetry, streamed JSONL/CSV, styled table exports, daily budget controls and five themes')

    def verify_session_lifecycle(self):
        from lab.presets import preset
        from lab.schema import Incident
        from lab.fiveg import FiveGSimulation
        from .export_dialog import ExportDialog
        from .exporting import export_file
        w=self.window;previous=w.run_record;theme=w.theme
        p=preset('5g');p.duration=10;p.window=1;p.control_plane=False;p.routing_knowledge='shared';p.trace_limit=1000
        p.flows=[next(f for f in p.flows if f.operation=='5G PDU Session')];f=p.flows[0]
        f.rate=0;f.subscribers=1;f.pdu_session_count=2;f.session_lifetime_s=1;f.max_retries=0;f.service_timeout_ms=100;f.timeout_ms=400;f.session_cleanup_retries=1;f.session_cleanup_retry_s=.1
        second=f.model_copy(deep=True);second.id='second';second.pdu_session_id=2;second.pdu_session_count=1;second.session_lifetime_s=0;p.flows.append(second)
        for link in p.links:link.loss=0;link.jitter_ms=0
        p.incidents=[Incident(kind='node_down',target='upf',start=.8,duration=9,detection_ms=0,recovery_ms=0)]
        class Scheduled(FiveGSimulation):
            def prepare(self):
                super().prepare();self.total=2
                self.push(100,'new',(1,self.p.flows[0]));self.push(300,'new',(2,self.p.flows[1]))
        result=dict(id='pdu-lifecycle-fixture',name='Две сессии UE · частичное освобождение при отказе UPF',project=p.model_dump(),**Scheduled(p).run())
        w.run_record=result
        try:
            editor=flow_editor(w,p.model_dump(),p.flows[0].model_dump());assert editor.fields['pdu_session_count'].value()==2
            assert editor.fields['operation'].findData('5G Deregistration')>=0 and editor.fields['operation'].findData('5G PDU Session Release')>=0
            assert editor.fields['session_cleanup_retries'].value()==1;editor.close();editor.deleteLater()
            editor=node_editor(w,p.nodes[1].model_dump());assert editor.fields['pdu_session_limit'].value()==p.nodes[1].pdu_session_limit;editor.close();editor.deleteLater()
            w.show_session_lifecycle();dialog=w.session_dialog
            assert dialog.sessions.rowCount()==2 and result['session_lifecycle']['states']=={'ORPHANED':1,'ACTIVE':1}
            dialog.state.setCurrentIndex(dialog.state.findData('ORPHANED'));assert dialog.sessions.rowCount()==1
            dialog.sessions.selectRow(0);assert dialog.selected_owner()==1 and dialog.contexts.rowCount()==1 and dialog.contexts.item(0,0).text()=='upf'
            dialog.open_procedure();assert w.procedure_dialog.sessions.currentData()==1 and w.procedure_dialog.run_record['id']==result['id'];w.procedure_dialog.close()
            dialog.state.setCurrentIndex(0);dialog.search.setText('несуществующий');assert dialog.sessions.rowCount()==0 and dialog.contexts.rowCount()==0
            dialog.search.clear()
            for name in ('monochrome','light_monochrome','dark','graphite','porcelain'):
                w.apply_theme(name,persist=False);dialog.show();self.app.processEvents()
                if self.output:dialog.grab().save(str(self.output/f'{name}-session-lifecycle.png'))
            dialog.sessions.selectRow(0);dialog.tabs.setCurrentIndex(1);self.app.processEvents()
            if self.output:
                dialog.grab().save(str(self.output/'session-orphaned-context.png'))
                (self.output/'session-lifecycle.json').write_text(json.dumps(result['session_lifecycle'],ensure_ascii=False,indent=2),encoding='utf-8')
            export=ExportDialog(dialog,dialog.export_sources());assert len(export.sources)==3 and len(export.data().rows)==2
            if self.output:
                for fmt in ('csv','json','xlsx','html','pdf'):export_file(self.output/('pdu-sessions.'+fmt),export.data(),export.style(),fmt)
            export.close();export.deleteLater()
            dialog.set_run(dict(id='legacy',project=p.model_dump()));assert dialog.sessions.rowCount()==0 and 'не содержит' in dialog.summary.text() and 'старого прогона' in dialog.note.text()
            dialog.set_run(result);dialog.close()
            self.completed.append('5G lifecycle: per-UE PDU IDs, native flow/node controls, orphan-resource inspection, state/search filters, recorded procedure navigation, legacy runs and styled exports in five themes')
        finally:w.run_record=previous;w.apply_theme(theme,persist=False)

    def verify_protocol_regressions(self):
        from lab.procedures import ProcedureSimulation,ENGINE_VERSION
        from lab.fiveg import FiveGSimulation
        from lab.presets import preset
        from lab.schema import Incident
        class One(ProcedureSimulation):
            def prepare(self):super().prepare();self.total=1;self.push(2000,'new',(1,self.p.flows[0]))
        class One5G(FiveGSimulation):
            def prepare(self):super().prepare();self.total=1;self.push(2000,'new',(1,self.p.flows[0]))
        p=preset('diameter');p.duration=10;p.flows[0].rate=0;p.flows[0].timeout_ms=5000
        for link in p.links:
            link.loss=0;link.jitter_ms=0;link.latency_ms=80;link.reverse_latency_ms=80
            link.rto_ms=10;link.rto_min_ms=10;link.rto_max_ms=10;link.transport_retries=0
        result=One(p).run();assert result['summary']['delivered']==1
        p=preset('ss7');p.duration=10;p.flows=p.flows[:1];p.flows[0].rate=0;p.flows[0].global_title='123456'
        endpoint=next(n for n in p.nodes if n.kind=='HLR');isolated=next(n for n in p.nodes if n.kind=='SG')
        isolated.enabled=False;isolated.gtt={'123':endpoint.point_code};p.links=[l for l in p.links if isolated.id not in (l.source,l.target)]
        for link in p.links:link.loss=0;link.jitter_ms=0
        assert One(p).run()['summary']['delivered']==0
        for node in p.nodes:
            if node.kind=='STP':node.gtt={'1':endpoint.point_code}
        assert One(p).run()['summary']['delivered']==1
        p=preset('5g');p.duration=10;p.flows=[f for f in p.flows if f.operation=='5G PDU Session'];p.flows[0].rate=0
        for link in p.links:link.loss=0;link.jitter_ms=0
        p.incidents=[Incident(kind='node_restart',target='amf',start=0,duration=1,detection_ms=0,recovery_ms=0)]
        result=One5G(p).run();assert result['summary']['failure_reasons']=={'not_registered':1}
        self.completed.append('engine '+ENGINE_VERSION+': late-ACK delivery, local GTT and one-time initial 5G registration regressions')
    def verify_exports(self):
        from . import export_dialog as export_ui
        from .export_dialog import ExportDialog
        from .exporting import FORMATS,export_file
        w=self.window;original_settings=w.settings;original_theme=w.theme
        destination=((self.output or storage.DATA)/'exports').resolve();destination.mkdir(exist_ok=True)
        original_file_dialog=export_ui.QFileDialog
        class TestFileDialog(QFileDialog):
            def __init__(self,*args):
                super().__init__(*args);self.setOption(QFileDialog.Option.DontUseNativeDialog,True)
        export_ui.QFileDialog=TestFileDialog
        try:
            w.settings=QSettings(str(storage.DATA/'export-self-test.ini'),QSettings.Format.IniFormat)
            assert len(w.export_sources())==29
            for theme in ('monochrome','light_monochrome','dark'):
                w.apply_theme(theme,persist=False)
                dialog=ExportDialog(w,w.export_sources(),'telemetry_chart');dialog.show();self.app.processEvents();dialog.refresh_preview()
                assert dialog.save_button.isEnabled(),dialog.error_label.text()
                dialog.preset_combo.setCurrentText('Научный');dialog.refresh_preview();assert dialog.style().accent=='#2457a7'
                if self.output:dialog.grab().save(str(self.output/f'{theme}-export-chart-dialog.png'))
                chart=dialog.data();assert chart.series and chart.metadata['record_id']==w.run_record['id']
                if theme=='light_monochrome':
                    for fmt in FORMATS['chart']:export_file(destination/f'telemetry.{fmt}',chart,dialog.style(),fmt)
                dialog.source_combo.setCurrentIndex(dialog.source_combo.findData('fiveg_table'));dialog.refresh_preview()
                assert dialog.save_button.isEnabled();assert len(dialog.data().rows)==w.fiveg_table.rowCount()
                if self.output:dialog.grab().save(str(self.output/f'{theme}-export-table-dialog.png'))
                if theme=='light_monochrome':
                    table=dialog.data()
                    for fmt in FORMATS['table']:export_file(destination/f'fiveg-services.{fmt}',table,dialog.style(),fmt)
                w.fiveg_table.selectRow(0)
                selected_dialog=ExportDialog(w,w.export_sources(),'fiveg_table');selected_dialog.scope_combo.setCurrentIndex(1);selected_dialog.refresh_preview()
                assert len(selected_dialog.data().rows)==1 and selected_dialog.save_button.isEnabled()
                selected_dialog.close();selected_dialog.deleteLater()
                dialog.source_combo.setCurrentIndex(dialog.source_combo.findData('telemetry_chart'));dialog.format_combo.setCurrentIndex(dialog.format_combo.findData('png'))
                chosen=destination/f'{theme}-dialog-save.png'
                def accept_save():
                    file_dialog=dialog.findChild(QFileDialog);file_dialog.selectFile(str(chosen));QDialog.accept(file_dialog)
                QTimer.singleShot(0,accept_save);dialog.save_export()
                assert chosen.exists() and dialog.last_path==chosen
                assert json.loads(w.settings.value('export/style'))['accent']=='#2457a7'
                dialog.close();dialog.deleteLater()
                restored=ExportDialog(w,w.export_sources(),'telemetry_chart');assert restored.style().accent=='#2457a7';restored.close();restored.deleteLater()
                chart_style=w.settings.value('export/style')
                w.graph.scene_model.clearSelection();w.graph.node_items['gnb'].setSelected(True);w.graph.node_items['upf'].setSelected(True)
                topology=ExportDialog(w,w.export_sources(),'topology');topology.show();self.app.processEvents();topology.preset_combo.setCurrentText('Научный')
                topology.style_fields['grid'].setChecked(False);topology.style_fields['protocol_colors'].setChecked(True);topology.style_fields['node_ids'].setChecked(True)
                topology.style_fields['width'].setValue(1800);topology.style_fields['height'].setValue(1100);topology.style_fields['node_background'].setText('#fef2dd');topology.refresh_preview()
                assert topology.save_button.isEnabled(),topology.error_label.text();assert len(topology.data().nodes)==len(w.project['nodes'])
                topology.actual_size_button.click();assert topology.preview.transform().m11()==1
                topology.zoom_in_button.click();assert topology.preview.transform().m11()>1
                zoom=topology.preview.transform().m11();topology.refresh_preview();assert topology.preview.transform().m11()==zoom
                if self.output:topology.grab().save(str(self.output/f'{theme}-export-topology-zoom.png'))
                topology.fit_preview_button.click();assert topology.preview.fit_mode
                if self.output:topology.grab().save(str(self.output/f'{theme}-export-topology-dialog.png'))
                if theme=='light_monochrome':
                    for fmt in FORMATS['topology']:export_file(destination/f'topology.{fmt}',topology.data(),topology.style(),fmt)
                topology.scope_combo.setCurrentIndex(1);topology.refresh_preview();assert len(topology.data().nodes)==2 and len(topology.data().links)==1
                topology.format_combo.setCurrentIndex(topology.format_combo.findData('svg'));topology.style_fields['transparent_background'].setChecked(True);topology.refresh_preview()
                chosen_topology=destination/f'{theme}-topology-fragment.svg'
                def accept_topology_save():
                    file_dialog=topology.findChild(QFileDialog);file_dialog.selectFile(str(chosen_topology));QDialog.accept(file_dialog)
                QTimer.singleShot(0,accept_topology_save);topology.save_export()
                assert chosen_topology.exists() and topology.last_path==chosen_topology
                assert w.settings.value('export/style')==chart_style and json.loads(w.settings.value('export/topologyStyle'))['node_background']=='#fef2dd'
                topology.close();topology.deleteLater()
                restored=ExportDialog(w,w.export_sources(),'topology');assert restored.style().node_background=='#fef2dd';restored.close();restored.deleteLater()
            self.completed.append('styled export dialog in all themes, selected rows, saved appearance, all 12 chart/table formats and native file saving')
            self.completed.append('styled topology export: PNG/JPEG/SVG/PDF, whole graph and selected fragment, native saving, transparent SVG and separate appearance in all themes')
            self.completed.append('vector export preview with native text, 100% and wheel/button zoom, fit-to-view and persistent zoom after style edits')
        finally:export_ui.QFileDialog=original_file_dialog;w.settings=original_settings;w.apply_theme(original_theme,persist=False)
    def next_stage(self,result):
        # Wait for the worker's finished signal before scheduling the next job.
        QTimer.singleShot(50,self.advance)
    def advance(self):
        try:
            w=self.window
            if w.active_job:QTimer.singleShot(25,self.advance);return
            if self.stage==0:
                assert w.run_record['summary']['sent']>0
                assert w.run_record['feature_contract']=='procedures-v2'
                assert w.lifecycle_table.rowCount()>0 and w.message_table.rowCount()>0
                assert w.procedure_table.rowCount()==len(w.project['flows'])
                for tab in range(w.details_tabs.count()):
                    w.details_tabs.setCurrentIndex(tab);w.show_page(2);self.app.processEvents();self.capture(f'procedure-telemetry-{tab}')
                w.details_tabs.setCurrentIndex(0)
                self.capture('native-topology');w.show_page(1);self.capture('native-experiments');w.show_page(2);self.capture('native-telemetry')
                self.completed.append('background simulation and native telemetry');self.stage=1;w.dataset_runs.setValue(6);w.generate_dataset()
            elif self.stage==1:
                assert w.dataset_record['metadata']['runs']==6;self.completed.append('background dataset');self.stage=2;w.trees.setValue(25);w.train_model()
            elif self.stage==2:
                assert w.model_record and w.model_record['metrics']['samples']>0
                groups=[set(w.model_record['split'][k]['runs']) for k in ('train','validation','test')]
                assert not(groups[0]&groups[1] or groups[0]&groups[2] or groups[1]&groups[2])
                w.threshold.setValue(1000);assert w.model_metrics['confusion'][0][1]==0
                w.threshold.setValue(round(w.model_record['threshold']*1000));self.test_threshold=w.current_threshold;self.capture('native-ml');self.completed.append('native ML training, group split and threshold');self.stage=3;w.predict()
            elif self.stage==3:
                assert w.prediction_table.rowCount()==len(w.run_record['windows']);assert w.current_threshold==self.test_threshold;self.completed.append('background inference and retained threshold');w.show_page(4);self.capture('native-results')
                from lab.calibration import calibrate
                from lab.presets import preset
                config=preset('diameter');config.duration=10;config.flows[0].rate=1
                text='run_id,offered_rate,latency_mean,delivery_ratio\n'+''.join(f'fixture-{i},1,100,1\n' for i in range(4))
                self.stage=4;w.start_job('Проверка калибровки',lambda progress,cancel:calibrate(config,text,progress,cancel),lambda result:w.accept_calibration(result,'Synthetic calibration self-test'))
            elif self.stage==4:
                result=w.calibration_record;assert result and w.calibration_table.rowCount()>0
                assert not(set(result['split']['train'])&set(result['split']['test']))
                before=json.dumps(w.project,sort_keys=True);w.apply_calibration();w.undo.undo();assert json.dumps(w.project,sort_keys=True)==before
                self.completed.append('background calibration, held-out runs, configuration undo');w.show_page(1);self.app.processEvents();self.capture('native-calibration')
                from .calibration_dialog import CalibrationDialog
                dialog=CalibrationDialog(w);assert len(dialog.values().parameters)==4
                dialog.add_parameter(dict(path='link:'+w.project['links'][0]['id']+':loss',lower=0,upper=.2,logarithmic=False))
                assert len(dialog.values().parameters)==5
                target=dialog.parameters.cellWidget(4,0);target.setCurrentIndex(target.findData('node:'+w.project['nodes'][0]['id']+':capacity'))
                assert dialog.values().parameters[-1].lower==1
                target.setCurrentIndex(target.findData('link:'+w.project['links'][0]['id']+':loss'))
                dialog.show();self.app.processEvents()
                if self.output:dialog.grab().save(str(self.output/'calibration-options.png'))
                dialog.close();dialog.deleteLater()
                self.completed.append('native calibration options, absolute link loss, bounds and multi-seed search')
                from lab.presets import preset
                config=preset('5g');config.duration=10;config.window=1
                for flow in config.flows:flow.rate=.5
                w.install_project(config.model_dump());w.select('node','amf');w.graph.fit()
                for name,factory in [('nf',lambda:node_editor(w,w.project['nodes'][1])),('flow',lambda:flow_editor(w,w.project,w.project['flows'][0])),('interface',lambda:link_editor(w,w.project,w.project['links'][0]))]:
                    d=factory();assert d.values();d.tabs.setCurrentIndex(d.tabs.count()-1);d.show();self.app.processEvents()
                    if self.output:d.grab().save(str(self.output/f'5g-{name}-editor.png'))
                    d.close();d.deleteLater()
                self.stage=5;w.start_run()
            elif self.stage==5:
                assert w.run_record['fiveg']['contexts']['pdu_active']>0
                assert w.fiveg_table.rowCount()>0
                assert w.control_table.rowCount()>0 and w.routing_state_table.rowCount()>0
                assert w.run_record['user_plane']['delivered']>0 and w.user_plane_table.rowCount()>0
                assert {m.get('protocol') for m in w.run_record['message_traces']}=={'NGAP','SBI','PFCP'}
                w.show_page(0);self.app.processEvents();w.graph.fit();self.capture('5g-topology')
                w.show_page(2)
                for index,name in [(5,'5g-services'),(6,'control-exchanges'),(7,'router-views'),(8,'5g-user-plane')]:
                    w.details_tabs.setCurrentIndex(index);self.app.processEvents();self.capture(name)
                self.completed.append('5G SA native topology, NF/flow editors, registration, PDU sessions and service telemetry')
                self.completed.append('resource-bearing management, per-observer routing knowledge, N3/N6 user traffic and native telemetry')
                from lab.presets import preset
                config=preset('diameter');config.duration=10;config.window=1;config.flows[0].rate=.8
                w.show_experiments();d=w.experiment_dialog;d.source=config.model_dump();d.axes.setRowCount(0);d.add_axis('load',[.5,1]);d.repetitions.setValue(6);d.warmup.setValue(2)
                self.stage=6;d.start();assert not d.run_button.isEnabled()
            elif self.stage==6:
                self.verify_experiments()
                from lab.presets import preset
                self.long_previous=w.run_record;self.long_source=preset('diameter');self.long_source.duration=10;self.long_source.window=1;self.long_source.flows[0].rate=2
                w.show_long_runs();d=w.long_run_dialog;d.source=self.long_source.model_dump();d.duration.setValue(10);d.measurement_window.setValue(1);d.load.setValue(1);d.checkpoint_sim.setValue(1);d.batch.setValue(32);d.auto_pause.setChecked(True);d.pause_at.setValue(3)
                self.stage=7;d.start_new()
            elif self.stage==7:
                from lab import long_runs
                from PySide6.QtGui import QCloseEvent
                d=w.long_run_dialog;assert long_runs.get(d.current_id)['status']=='paused';d.refresh()
                self.long_close_requests=[];w.close=lambda:self.long_close_requests.append(True)
                self.stage=8;d.resume_selected();event=QCloseEvent();w.closeEvent(event);assert not event.isAccepted()
            elif self.stage==8:
                from lab import long_runs
                if not self.long_close_requests:QTimer.singleShot(25,self.advance);return
                state=long_runs.get(w.long_run_dialog.current_id)['status'];assert state in ('paused','completed');del w.close
                self.stage=9
                if state=='paused':w.long_run_dialog.refresh();w.long_run_dialog.resume_selected()
                else:QTimer.singleShot(25,self.advance)
            elif self.stage==9:
                self.verify_long_runs();self.verify_clusters();self.verify_session_lifecycle();self.verify_themes();self.verify_exports();w.show_page(0);w.undo.setClean()
                w.mutate('Проверка закрытия с историей',lambda p:p['nodes'][0].update(capacity=p['nodes'][0]['capacity']+1))
                assert not w.undo.isClean()
                self.completed.append('shutdown regression: destroy the actual main window with dirty undo history and reject exceptions raised during final cleanup')
                self.timeout.stop()
                report=dict(status='passed',checks=self.completed,qt_widgets=True,webview=False)
                if self.output:(self.output/'native-smoke.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                print(json.dumps(report),flush=True);self.app.exit(0)
        except Exception:self.fail(traceback.format_exc())
