# Native Windows distribution. Qt Widgets/Core/Gui only; no web frontend or server.
from pathlib import Path
from importlib.metadata import distribution
from PyInstaller.utils.hooks import copy_metadata

root=Path(SPECPATH)
qt_path=Path(distribution('PySide6-Essentials').locate_file('PySide6'))
qt_licenses=Path(distribution('PySide6-Essentials')._path)/'licenses'
license_data=[(str(qt_licenses),'licenses/Qt')] if qt_licenses.exists() else []
for package in ['PySide6-Essentials','shiboken6','numpy','scipy','scikit-learn','joblib','pydantic','pydantic-core','threadpoolctl']:
    license_data+=copy_metadata(package)
a=Analysis(
    [str(root/'desktop.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root/'README.md'),'.'),(str(root/'docs'),'docs'),(str(root/'requirements-native.txt'),'.'),(str(root/'THIRD-PARTY-NOTICES.md'),'.')]+license_data,
    hiddenimports=['sklearn.ensemble._forest','sklearn.ensemble._iforest','sklearn.linear_model._logistic'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5','PyQt6','PySide2','PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtWebEngineQuick','PySide6.QtQml','PySide6.QtQuick','PySide6.QtQuickWidgets','PySide6.QtNetwork','fastapi','uvicorn','httpx','IPython','jupyter','notebook','matplotlib','pandas','torch','tensorflow','jax','sympy','pytest','tkinter'],
    noarchive=False,
)
# Qt 6.12 imports the Windows ICU API. A developer PATH may also contain an
# unrelated ICU build (e.g. Poppler); its differently named exports break Qt.
# Use the OS-provided API/ICU libraries, never bundle PATH-resolved substitutes.
a.binaries=[entry for entry in a.binaries if Path(entry[0]).name.lower() not in ('icuuc.dll','icuin.dll','icudt.dll') and not Path(entry[0]).name.lower().startswith(('api-ms-win-','ext-ms-win-'))]
# Use Qt's newer, redistributable VC runtime consistently at bundle root.
for runtime in ('vcruntime140.dll','vcruntime140_1.dll'):
    candidate=qt_path/runtime
    if candidate.exists():
        a.binaries=[entry for entry in a.binaries if entry[0].lower()!=runtime]
        a.binaries.append((runtime,str(candidate),'BINARY'))
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='SignalLab',icon=str(root/'assets'/'SignalLab.ico'),debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,disable_windowed_traceback=False,uac_admin=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='SignalLab')
