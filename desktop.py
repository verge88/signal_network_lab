"""Signal Lab native entrypoint. No HTTP server or browser runtime is used."""
import argparse
import multiprocessing
import os
import sys
import traceback
from pathlib import Path
from time import perf_counter


def main():
    startup_started=perf_counter()
    parser=argparse.ArgumentParser(description='Signal Lab Desktop')
    parser.add_argument('--smoke-test',action='store_true',help='Verify Qt pages, simulation, dataset, training and inference offscreen')
    parser.add_argument('--screenshots',type=Path,help='Output directory for self-test renders')
    parser.add_argument('--data-dir',type=Path,help='Use a specific local research workspace')
    args=parser.parse_args()
    if args.data_dir:os.environ['SIGNAL_LAB_DATA_DIR']=str(args.data_dir.resolve())
    if args.smoke_test:
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        # A self-test uses a separate workspace and never rewrites research history.
        import tempfile
        temporary=tempfile.TemporaryDirectory(prefix='signal-lab-native-')
        os.environ['SIGNAL_LAB_DATA_DIR']=temporary.name
    from PySide6.QtCore import Qt,QTimer
    from PySide6.QtGui import QFont,QFontDatabase
    from PySide6.QtWidgets import QApplication,QMessageBox
    app=QApplication(sys.argv[:1]);app.setApplicationName('Signal Lab Desktop');app.setApplicationVersion('2.18.2');app.setOrganizationName('SignalLab');app.setStyle('Fusion')
    from native.qt_gc import GuiGarbageCollector
    app.gui_collector=GuiGarbageCollector(app)
    from native.startup import StartupSplash
    splash=StartupSplash();app.startup_splash=splash;splash.show_startup()
    app.startup_state=dict(splash_shown=splash.isVisible(),before_window_import='native.window' not in sys.modules,before_ml_import='sklearn' not in sys.modules,shown_ms=round((perf_counter()-startup_started)*1000))
    # Show the card before data migration, scientific imports or UI construction.
    fonts=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
    for filename in ('segoeui.ttf','seguisb.ttf','segoeuib.ttf'):
        font_path=fonts/filename
        if font_path.exists():QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont('Segoe UI',10));splash.update_status('Загрузка компонентов…')
    if args.smoke_test and args.screenshots:
        args.screenshots.mkdir(parents=True,exist_ok=True);splash.grab().save(str(args.screenshots/'startup-splash.png'))
    from lab import storage
    # Windowed executables have no stderr. Keep native fault stacks available.
    import faulthandler
    crash_directory=args.screenshots if args.smoke_test and args.screenshots else storage.DATA
    crash_directory.mkdir(parents=True,exist_ok=True)
    crash_log=(crash_directory/'native-fault.log').open('a',encoding='utf-8')
    faulthandler.enable(crash_log,all_threads=True)
    if getattr(sys,'frozen',False) and not args.smoke_test:
        splash.update_status('Подготовка рабочего пространства…')
        candidate=Path(sys.executable).resolve().parents[2]
        if (candidate/'desktop.py').is_file() and (candidate/'lab'/'schema.py').is_file():
            storage.migrate_workspace(candidate/'data')
    runtime_errors=[]
    def exception_hook(kind,value,tb):
        text=''.join(traceback.format_exception(kind,value,tb))
        runtime_errors.append(text)
        from lab.storage import DATA
        with (DATA/'desktop-errors.log').open('a',encoding='utf-8') as f:f.write(text+'\n')
        if args.smoke_test:
            if args.screenshots:
                args.screenshots.mkdir(parents=True,exist_ok=True)
                (args.screenshots/'native-error.log').write_text(text,encoding='utf-8')
            print(text,file=sys.stderr,flush=True);app.exit(1)
        else:QMessageBox.critical(None,'Signal Lab — ошибка',str(value)+'\n\nПодробности: '+str(DATA/'desktop-errors.log'))
    sys.excepthook=exception_hook
    try:
        splash.update_status('Загрузка компонентов…')
        from native.window import MainWindow
        splash.update_status('Подготовка интерфейса…');window=MainWindow();window.show();app.processEvents()
        app.startup_state.update(main_window_visible=window.isVisible(),completed=splash.complete(window),ready_ms=round((perf_counter()-startup_started)*1000))
        app.startup_state['splash_hidden']=not splash.isVisible()
        if args.smoke_test and args.screenshots:
            import json
            (args.screenshots/'startup.json').write_text(json.dumps(app.startup_state,indent=2),encoding='utf-8')
    finally:splash.close()
    if args.smoke_test:
        from native.smoke import SmokeTest
        test=SmokeTest(app,window,args.screenshots);QTimer.singleShot(50,test.start)
    result=app.exec()
    window.thread_pool.waitForDone();window.timer.stop()
    # Destroy child objects while Qt and the exception handler are still alive.
    from shiboken6 import delete
    delete(window);app.gui_collector.stop()
    if args.smoke_test and runtime_errors:result=1
    faulthandler.disable();crash_log.close()
    if args.smoke_test:temporary.cleanup()
    return result


if __name__=='__main__':
    multiprocessing.freeze_support()
    try:
        result=main()
    except Exception:
        # Windowed Windows executables have no stderr; preserve startup errors.
        text=traceback.format_exc()
        if '--screenshots' in sys.argv:
            target=Path(sys.argv[sys.argv.index('--screenshots')+1])
        else:target=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'SignalLab'
        target.mkdir(parents=True,exist_ok=True)
        (target/'native-startup-error.log').write_text(text,encoding='utf-8')
        result=1
    raise SystemExit(result)
