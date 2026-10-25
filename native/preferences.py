"""Application preferences only; research profiles and results are separate."""
import math
from pathlib import Path
from .style import THEMES
from .theme_palettes import DEFAULT_THEME


def defaults():
    documents=Path.home()/'Documents'
    return {'theme':DEFAULT_THEME,'sidebarCollapsed':False,'overviewHeight':125,'topologyMetrics':False,
        'graph/grid':True,'graph/linkLabels':True,'graph/panSpeed':1.,'graph/zoomFactor':1.15,
        'preferences/startPage':0,'preferences/rememberWindow':True,'preferences/statusBar':True,
        'preferences/restoreWorkspace':True,'preferences/fitOnTopology':True,'preferences/beepOnSuccess':False,
        'export/directory':str(documents if documents.is_dir() else Path.home())}


def validate(key,value):
    reference=defaults()
    if key not in reference:raise ValueError('Неизвестная настройка программы')
    default=reference[key]
    if type(default) is bool:
        if type(value) is bool:return value
        if isinstance(value,str) and value.lower() in ('true','false','1','0'):return value.lower() in ('true','1')
        raise ValueError('Ожидается переключатель: '+key)
    if key=='theme':
        if value not in THEMES:raise ValueError('Неизвестная тема')
        return value
    if key=='export/directory':
        if not isinstance(value,str) or not value.strip():raise ValueError('Выберите папку экспорта')
        path=Path(value).expanduser().resolve()
        if not path.is_dir():raise ValueError('Папка экспорта не существует')
        return str(path)
    bounds={'overviewHeight':(125,480),'preferences/startPage':(0,7),'graph/panSpeed':(.25,4.),'graph/zoomFactor':(1.05,1.5)}
    try:
        if isinstance(value,bool):raise ValueError()
        number=float(value)
        if not math.isfinite(number) or not bounds[key][0]<=number<=bounds[key][1]:raise ValueError()
        if type(default) is int:
            if not number.is_integer():raise ValueError()
            return int(number)
        return number
    except (TypeError,ValueError,OverflowError):raise ValueError('Некорректное значение настройки: '+key)


def load(settings):
    result=defaults()
    for key,default in result.items():
        try:result[key]=validate(key,settings.value(key,default))
        except (TypeError,ValueError,OSError):result[key]=default
    return result


def save(settings,values):
    checked={key:validate(key,value) for key,value in values.items()}
    for key,value in checked.items():settings.setValue(key,value)
    settings.sync()
    if settings.status()!=settings.Status.NoError:raise OSError('Не удалось сохранить настройки программы')
    return checked
