import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['SIGNAL_LAB_DATA_DIR'] = str(ROOT/'tmp/interface-review/editor-workspace')
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QFontDatabase
from native.style import set_theme, stylesheet, application_palette
from native.dialogs import node_editor, link_editor, flow_editor, incident_editor
from native.service_lists import ListDialog
from native.attacks import StepEditor
from lab.presets import preset

app=QApplication([])
for font in ('segoeui.ttf','seguisb.ttf','segoeuib.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)
app.setFont(QFont('Segoe UI',10));set_theme('graphite')
app.setPalette(application_palette());app.setStyleSheet(stylesheet())
owner=QWidget();owner.settings=QSettings(str(ROOT/'tmp/interface-review/editor-settings.ini'),QSettings.Format.IniFormat)
p=preset('5g-mobile').model_dump();out=ROOT/'tmp/interface-review'
factories=[('node',lambda:node_editor(owner,p['nodes'][1])),('link',lambda:link_editor(owner,p,p['links'][0])),('flow',lambda:flow_editor(owner,p,p['flows'][0])),('incident',lambda:incident_editor(owner,p)),('upf-list',lambda:ListDialog(owner,p,'upf',[])),('mobility-list',lambda:ListDialog(owner,p,'mobility',[])),('attack-step',lambda:StepEditor(owner,p))]
for name, factory in factories:
    dialog=factory();dialog.show();app.processEvents()
    tabs=getattr(dialog,'tabs',None)
    for i in range(tabs.count() if tabs else 1):
        if tabs:tabs.setCurrentIndex(i)
        app.processEvents();dialog.grab().save(str(out/f'editor-{name}-{i}.png'))
    dialog.close();dialog.deleteLater();app.processEvents()
print('Editor captures complete',flush=True)
