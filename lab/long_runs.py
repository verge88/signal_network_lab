"""Disk journals and exact continuation at complete event boundaries."""
from collections import Counter
from concurrent.futures import CancelledError
from contextlib import closing
from datetime import datetime,timezone
import csv
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from pydantic import BaseModel,ConfigDict,Field
from .schema import Project
from . import storage,checkpoints

CONTRACT='long-run-v1'


class Options(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    checkpoint_sim_s:float=Field(default=600,ge=1,le=3600)
    checkpoint_wall_s:float=Field(default=30,ge=1,le=600)
    batch_events:int=Field(default=512,ge=16,le=20000)
    max_events:int=Field(default=12000000,ge=1000,le=50000000)
    pause_at_s:float|None=Field(default=None,ge=0,le=86400)


class PausedRun(Exception):pass


def stamp():return datetime.now(timezone.utc).isoformat()


def path_for(ident):
    if not re.fullmatch(r'[0-9a-f]{32}',ident):raise ValueError('Неверный ID длительного прогона')
    return storage.DATA/'long-runs'/(ident+'.sqlite')


def read_meta(db):return {key:json.loads(value) for key,value in db.execute('SELECT key,value FROM meta')}


def get(ident):
    path=path_for(ident)
    if not path.is_file():raise ValueError('Длительный прогон не найден')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:return read_meta(db)


def listing():
    folder=storage.DATA/'long-runs';rows=[]
    if folder.exists():
        for path in folder.glob('*.sqlite'):
            if not re.fullmatch(r'[0-9a-f]{32}',path.stem):continue
            try:rows.append(get(path.stem))
            except (ValueError,sqlite3.Error,json.JSONDecodeError):
                rows.append(dict(id=path.stem,name='Повреждённый журнал',status='damaged',updated='',simulated_s=0,steps=0,checkpoint_s=None,records=0))
    return sorted(rows,key=lambda r:r.get('updated',''),reverse=True)


class Journal:
    def __init__(self,ident,create=False):
        self.ident=ident;self.path=path_for(ident);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=None;self.db=None;self.buffer=[];self.seq=0
        if not create and not self.path.is_file():raise ValueError('Журнал прогона не найден')
        if create and self.path.exists():raise ValueError('Журнал с таким ID уже существует')
        self.lock=self.path.with_suffix('.lock').open('a+b')
        try:
            if self.lock.seek(0,2)==0:self.lock.write(b'0');self.lock.flush()
            self.lock.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.lock.close();self.lock=None;raise ValueError('Этот прогон уже выполняется другим процессом')
        try:
            self.db=sqlite3.connect(self.path,timeout=15);self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
            if create:
                with self.db:
                    self.db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                    self.db.execute('CREATE TABLE records(seq INTEGER PRIMARY KEY,time_ms REAL,kind TEXT,seen INTEGER,payload TEXT)')
                    self.db.execute('CREATE TABLE checkpoint(id INTEGER PRIMARY KEY CHECK(id=1),blob BLOB,digest TEXT,cursor INTEGER)')
                    self.db.execute('CREATE TABLE windows(idx INTEGER PRIMARY KEY,payload TEXT)')
            self.seq=self.db.execute('SELECT COALESCE(MAX(seq),0) FROM records').fetchone()[0]
        except Exception:self.close();raise
    def update(self,**values):
        self.db.executemany('INSERT OR REPLACE INTO meta VALUES (?,?)',[(key,json.dumps(value,ensure_ascii=False,allow_nan=False)) for key,value in values.items()])
    def append(self,kind,engine,record,seen):
        self.seq+=1;self.buffer.append((self.seq,engine.t,kind,seen,json.dumps(record,ensure_ascii=False,allow_nan=False)))
    def flush_rows(self):
        self.db.executemany('INSERT INTO records VALUES (?,?,?,?,?)',self.buffer)
    def flush(self,engine):
        with self.db:
            self.flush_rows();self.update(simulated_s=engine.t/1000,steps=engine.steps,records=self.seq,updated=stamp())
        self.buffer.clear()
    def snapshot(self,engine,status='running',compaction=None):
        blob,digest=checkpoints.dumps(engine)
        with self.db:
            self.flush_rows();self.db.execute('INSERT OR REPLACE INTO checkpoint VALUES(1,?,?,?)',(blob,digest,self.seq))
            self.update(status=status,simulated_s=engine.t/1000,steps=engine.steps,records=self.seq,
                checkpoint_s=engine.t/1000,checkpoint_steps=engine.steps,checkpoint_bytes=len(blob),updated=stamp(),compaction=dict(compaction or {}))
        self.buffer.clear()
    def restore(self,progress=None,cancel=None):
        record=self.db.execute('SELECT blob,digest,cursor FROM checkpoint WHERE id=1').fetchone()
        if record is None:raise ValueError('Контрольная точка ещё не создана; запустите новый прогон')
        engine=checkpoints.loads(record[0],record[1],progress,cancel)
        with self.db:
            self.db.execute('DELETE FROM records WHERE seq>?',(record[2],));self.db.execute('DELETE FROM windows')
            self.update(status='running',simulated_s=engine.t/1000,steps=engine.steps,records=record[2],error=None,updated=stamp())
        self.seq=record[2];return engine
    def close(self):
        if self.db:self.db.close();self.db=None
        if self.lock:
            try:
                self.lock.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(self.lock.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.lock,fcntl.LOCK_UN)
            finally:self.lock.close();self.lock=None


def compact(engine):
    """Retire only objects with no future event, queue or active exchange reference."""
    e=engine;transfers=set();messages=set();tasks=set();control=set();rpc=set();cluster=set()
    for _,_,_,kind,data in e.events:
        if kind in ('packet_rx','packet_timeout','ack'):transfers.add(data[0]['transfer'])
        elif kind=='node_done':messages.add(data[1])
        elif kind in ('resource_db_done','resource_db_timeout'):tasks.add(data)
        elif kind.startswith('rpc_'):rpc.add(data[0])
        elif kind=='ctrl_timeout':control.add(data[0])
        elif kind=='cluster_apply':cluster.add(data[0])
    for a in e.associations.values():
        transfers.update(p['transfer'] for p in a['queue']);transfers.update(p['transfer'] for p in a['flight'].values())
        transfers.update(a['completed'])
        for queue in a['streams'].values():transfers.update(queue)
    counts=Counter()
    for ident in list(e.transfers):
        if ident not in transfers:del e.transfers[ident];counts['transfers']+=1
    for queue in e.storage.queues.values():tasks.update(queue)
    for active in e.storage.running.values():tasks.update(active)
    for ident,task in list(e.storage.tasks.items()):
        if task['closed'] and ident not in tasks:del e.storage.tasks[ident];counts['database_tasks']+=1
    for task in e.storage.tasks.values():messages.add(task['msg']['id'])
    for tr in e.transfers.values():messages.add(tr['msg']['id'])
    for queue in e.node_queue.values():messages.update(item[2]['id'] for item in queue)
    for active in e.running.values():messages.update(item[0]['id'] for item in active.values())
    for ident in list(e.messages):
        if ident not in messages:del e.messages[ident];counts['messages']+=1
    for msg in e.messages.values():
        if msg.get('rpc_id') is not None:rpc.add(msg['rpc_id'])
        if msg.get('category')=='replication':cluster.add(msg['context'])
        request=msg.get('control',{}).get('request')
        if request is not None:control.add(request)
    control.update(s['pending'] for s in e.control.states.values() if s['pending'] is not None)
    for ident,request in list(e.control.requests.items()):
        if request['done'] and ident not in control:del e.control.requests[ident];counts['control_requests']+=1
    for ident in list(e.clusters.requests):
        if ident not in cluster:del e.clusters.requests[ident];counts['replication_requests']+=1
    if hasattr(e,'rpcs'):
        rpc.update(i for i,r in e.rpcs.items() if not r['closed']);todo=list(rpc)
        while todo:
            frame=e.rpcs[todo.pop()]
            for key in ('parent','waiting'):
                ident=frame.get(key)
                if ident is not None and ident not in rpc:rpc.add(ident);todo.append(ident)
        for ident in list(e.rpcs):
            if ident not in rpc:del e.rpcs[ident];counts['rpc_frames']+=1
    return counts


def start(project,options=None,progress=None,cancel=None,ident=None):
    p=Project.model_validate(project.model_dump() if isinstance(project,Project) else project)
    if p.engine!='procedures':raise ValueError('Сохранение состояния требует движок процедур')
    o=Options.model_validate(options.model_dump() if isinstance(options,Options) else options or {})
    ident=ident or uuid.uuid4().hex;journal=Journal(ident,create=True)
    try:
        with journal.db:journal.update(id=ident,name=p.name,status='preparing',created=stamp(),updated=stamp(),project=p.model_dump(),
            options=o.model_dump(),environment=checkpoints.environment(),contract=CONTRACT,result_key=uuid.uuid4().hex,result_id=None,
            simulated_s=0,steps=0,records=0,checkpoint_s=None,compaction={})
        engine=checkpoints.engine_for(p,progress,cancel)
        return execute(engine,journal,o,progress,cancel)
    finally:journal.close()


def resume(ident,options=None,progress=None,cancel=None):
    journal=Journal(ident)
    try:
        meta=read_meta(journal.db)
        if meta['status']=='completed':raise ValueError('Этот прогон уже завершён')
        if options is None:
            data=dict(meta['options']);data['pause_at_s']=None
        else:data=options.model_dump() if isinstance(options,Options) else options
        o=Options.model_validate(data)
        if o.max_events<=meta.get('checkpoint_steps',0):raise ValueError('Новый лимит событий должен быть больше числа уже обработанных событий')
        engine=journal.restore(progress,cancel)
        return execute(engine,journal,o,progress,cancel)
    finally:journal.close()


def execute(engine,journal,options,progress,cancel):
    e=engine;o=options;last_time=e.t;last_wall=time.monotonic();last_batch=e.steps;cleaned=Counter(read_meta(journal.db).get('compaction',{}))
    e.event_limit=o.max_events
    e.progress=None
    channels={id(e.traces):'procedures',id(e.message_traces):'messages',id(e.control.traces):'control',id(e.control.view_events):'routing'}
    if hasattr(e,'user_plane'):channels[id(e.user_plane.traces)]='user_data'
    e.stream_sink=lambda target,record,seen:journal.append(channels[id(target)],e,record,seen)
    e.cancel=lambda: bool(cancel and cancel()) if not e.prepared else False
    def hook(_):
        nonlocal last_time,last_wall,last_batch
        pause=bool(cancel and cancel()) or (o.pause_at_s is not None and e.t>=o.pause_at_s*1000)
        due=e.t-last_time>=o.checkpoint_sim_s*1000 or time.monotonic()-last_wall>=o.checkpoint_wall_s
        initial=readied[0] is False
        if initial or pause or due or e.steps-last_batch>=o.batch_events:
            cleaned.update(compact(e));last_batch=e.steps
            if initial or pause or due:
                journal.snapshot(e,'paused' if pause else 'running',cleaned);last_time=e.t;last_wall=time.monotonic();readied[0]=True
            else:journal.flush(e)
            if progress:progress(min(99,int(100*e.t/max(1,e.p.duration*1000))))
        if pause:raise PausedRun()
    readied=[False]
    try:
        with journal.db:journal.update(options=o.model_dump(),status='running',updated=stamp())
        result=e.run(hook);journal.flush(e)
        meta=read_meta(journal.db)
        result['long_run']=dict(id=journal.ident,contract=CONTRACT,stream_records=journal.seq,compaction=dict(cleaned),
            checkpoint_format=checkpoints.FORMAT,stream_policy='all reservoir inputs at emission; final windows written on completion')
        saved=storage.save('run','Длительный / '+e.original_project.name,dict(project=e.original_project.model_dump(),**result),meta['result_key'])
        with journal.db:
            journal.db.executemany('INSERT OR REPLACE INTO windows VALUES(?,?)',[(i,json.dumps(row,ensure_ascii=False,allow_nan=False)) for i,row in enumerate(result['windows'])])
            journal.update(status='completed',result_id=saved['id'],simulated_s=e.t/1000,steps=e.steps,updated=stamp(),compaction=dict(cleaned))
        if progress:progress(100)
    except PausedRun:pass
    except Exception as error:
        try:
            with journal.db:journal.update(status='cancelled' if isinstance(error,CancelledError) else 'failed',error=str(error),updated=stamp())
        except sqlite3.Error:pass
        raise
    finally:e.stream_sink=None
    return read_meta(journal.db)


def stream_rows(ident,limit=500,after=0):
    if not 1<=limit<=10000 or after<0:raise ValueError('Неверный диапазон журнала')
    path=path_for(ident)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        return [dict(seq=r[0],time_ms=r[1],kind=r[2],seen=r[3],record=json.loads(r[4])) for r in db.execute('SELECT seq,time_ms,kind,seen,payload FROM records WHERE seq>? ORDER BY seq LIMIT ?',(after,limit))]


def export_stream(ident,destination,fmt='jsonl',progress=None,cancel=None):
    if fmt not in ('jsonl','csv'):raise ValueError('Формат журнала: JSONL или CSV')
    target=Path(destination);source=path_for(ident)
    protected=[source,source.with_suffix('.lock'),Path(str(source)+'-wal'),Path(str(source)+'-shm')]
    if target.resolve() in [p.resolve() for p in protected]:raise ValueError('Выберите отдельный файл для экспорта')
    count=0;temporary=target.with_name(target.name+'.partial-'+uuid.uuid4().hex)
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=5)) as db,temporary.open('w',encoding='utf-8' if fmt=='jsonl' else 'utf-8-sig',newline='') as stream:
            db.execute('BEGIN');total=db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
            writer=csv.writer(stream) if fmt=='csv' else None
            if writer:writer.writerow(['seq','time_ms','kind','seen','record_json'])
            for seq,when,kind,seen,payload in db.execute('SELECT seq,time_ms,kind,seen,payload FROM records ORDER BY seq'):
                if count%1024==0:
                    if cancel and cancel():raise CancelledError('Экспорт отменён')
                    if progress:progress(int(100*count/max(1,total)))
                if writer:writer.writerow([seq,when,kind,seen,payload])
                else:stream.write(json.dumps(dict(seq=seq,time_ms=when,kind=kind,seen=seen,record=json.loads(payload)),ensure_ascii=False)+'\n')
                count+=1
        if cancel and cancel():raise CancelledError('Экспорт отменён')
        os.replace(temporary,target)
    finally:temporary.unlink(missing_ok=True)
    if progress:progress(100)
    return count
