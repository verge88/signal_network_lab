import json
import sqlite3
import uuid
import threading
import os
import sys
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

def default_data_path():
    override=os.environ.get('SIGNAL_LAB_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys,'frozen',False):
        return Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'SignalLab'
    return Path(__file__).resolve().parent.parent / 'data'


DATA = default_data_path()
DATA.mkdir(exist_ok=True,parents=True)
_initialization_lock=threading.Lock()
_initialized=set()


def migrate_workspace(source):
    """Copy an existing workspace to an empty one without modifying the source."""
    source=Path(source).resolve()
    if source==DATA.resolve() or (DATA/'lab.sqlite3').exists() or not (source/'lab.sqlite3').is_file():
        return False
    DATA.mkdir(exist_ok=True,parents=True)
    # Copy durable jobs before the run library: every completed job then refers
    # to a result that was already committed when the library backup is taken.
    durable=source/'long-runs'
    if durable.is_dir():
        destination=DATA/'long-runs';destination.mkdir(exist_ok=True)
        for path in durable.glob('*.sqlite'):
            target=destination/path.name
            if target.exists():continue
            old_job=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);new_job=sqlite3.connect(target)
            try:old_job.backup(new_job)
            finally:old_job.close();new_job.close()
    old=sqlite3.connect(source/'lab.sqlite3')
    new=sqlite3.connect(DATA/'lab.sqlite3')
    try:old.backup(new)
    finally:old.close();new.close()
    for name in ('models','desktop-workspace.json'):
        origin=source/name
        if origin.is_dir():shutil.copytree(origin,DATA/name,dirs_exist_ok=True)
        elif origin.is_file():shutil.copy2(origin,DATA/name)
    return True


@contextmanager
def connect():
    DATA.mkdir(exist_ok=True)
    db = sqlite3.connect(DATA / 'lab.sqlite3', timeout=30)
    try:
        with _initialization_lock:
            if str(DATA) not in _initialized:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, kind TEXT, name TEXT, created TEXT, payload TEXT)')
                db.commit()
                _initialized.add(str(DATA))
        with db:
            yield db
    finally:
        db.close()


def save(kind, name, payload, record_id=None):
    rid=record_id or uuid.uuid4().hex[:16]
    created=datetime.now(timezone.utc).isoformat()
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?,?)',(rid,kind,name,created,json.dumps(payload,allow_nan=False)))
    return dict(id=rid,kind=kind,name=name,created=created,**payload)


def get(kind, rid):
    with connect() as db:
        row=db.execute('SELECT name, created, payload FROM records WHERE kind=? AND id=?',(kind,rid)).fetchone()
    if not row: raise KeyError('Запись не найдена')
    return dict(id=rid,name=row[0],created=row[1],**json.loads(row[2]))


def listing(kind):
    with connect() as db:
        rows=db.execute('SELECT id,name,created,payload FROM records WHERE kind=? ORDER BY created DESC LIMIT 100',(kind,)).fetchall()
    return [dict(id=r[0],name=r[1],created=r[2],summary=json.loads(r[3]).get('summary'),metadata=json.loads(r[3]).get('metadata')) for r in rows]


def delete(kind,rid):
    with connect() as db:db.execute('DELETE FROM records WHERE kind=? AND id=?',(kind,rid))
