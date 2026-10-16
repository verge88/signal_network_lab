"""Development smoke harness executed inside the application, never against another app."""
import json
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
        self.timeout=QTimer(self);self.timeout.setSingleShot(True);self.timeout.timeout.connect(lambda:self.fail('Native self-test timed out'));self.timeout.start(120000)
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
        w=self.window;original_settings=w.settings;original_theme=w.theme;original_sidebar=w.sidebar_collapsed;original_overview=w.overview_height
        project=json.dumps(w.project,sort_keys=True);selection=w.selected;undo_count=w.undo.count()
        settings_path=str(storage.DATA/'theme-self-test.ini')
        try:
            w.settings=QSettings(settings_path,QSettings.Format.IniFormat)
            for name in ('dark','monochrome','light_monochrome'):
                w.apply_theme(name);w.set_sidebar_collapsed(False,persist=False);w.set_overview_height(125,persist=False)
                assert saved_theme(QSettings(settings_path,QSettings.Format.IniFormat))==name
                self.capture_node_icons(name)
                for page,title in enumerate(('topology','experiments','telemetry','ml','results')):
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
                    self.capture(f'{name}-{title}')
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
                w.show_page(0);w.set_overview_height(125);self.app.processEvents()
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
                w.set_overview_height(125);w.show_page(0);self.app.processEvents()
            assert json.dumps(w.project,sort_keys=True)==project
            assert w.selected==selection and w.undo.count()==undo_count
            self.completed.append('pure-black and light monochrome themes, neutral rendering on all five pages and saved theme')
            self.completed.append('collapsible sidebar, icon navigation, tooltips, menu toggle and persisted state across all themes')
            self.completed.append('compact overview, real mouse resizing, numeric height dialog, saved height and page-switch restoration across all themes')
        finally:
            w.settings=original_settings;w.apply_theme(original_theme,persist=False);w.set_sidebar_collapsed(original_sidebar,persist=False);w.set_overview_height(original_overview,persist=False)
    def start(self):
        try:
            w=self.window
            self.verify_protocol_regressions()
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
        except Exception as e:self.fail(e)
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
            assert len(w.export_sources())==24
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
            self.completed.append('styled export dialog in all themes, selected rows, saved appearance, all 12 chart/table formats and native file saving')
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
                self.verify_themes();self.verify_exports();w.show_page(0);w.undo.setClean();self.timeout.stop()
                report=dict(status='passed',checks=self.completed,qt_widgets=True,webview=False)
                if self.output:(self.output/'native-smoke.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                print(json.dumps(report),flush=True);self.app.exit(0)
        except Exception as e:self.fail(e)
