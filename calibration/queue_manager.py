"""Transactional persistent queue, event journal, and exclusive Unix controller lock."""
from contextlib import contextmanager
import fcntl
import json
import os
import socket
import sys
import sqlite3
import time
from pathlib import Path


@contextmanager
def exclusive(state):
    state.mkdir(parents=True,exist_ok=True)
    # Atomic mkdir also protects Docker bind mounts whose advisory locks may not propagate.
    guard=state/'controller.guard'
    try:guard.mkdir()
    except FileExistsError:
        raise RuntimeError('Controller guard exists; another instance or an unreviewed interrupted owner holds this state')
    try:
        (guard/'owner.json').write_text(json.dumps({'pid':os.getpid(),'host':socket.gethostname(),'started_at':time.time()}))
        with (state/'controller.lock').open('a+') as handle:
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Another controller or its child stage holds this state lock')
            yield handle.fileno()
    finally:
        retain=False
        for record in (state/'jobs').rglob('process.json'):
            try:os.killpg(json.loads(record.read_text())['process_group'],0)
            except ProcessLookupError:continue
            except Exception:retain=True;break
            else:retain=True;break
        if retain:
            print('Controller guard retained: a stage group may still be alive; inspect before recover-lock',file=sys.stderr)
        else:
            (guard/'owner.json').unlink(missing_ok=True)
            guard.rmdir()


def recover_guard(state,reason):
    """Explicit conservative recovery after a killed owner and all stage groups exit."""
    if not reason:raise ValueError('Guard recovery requires --reason')
    guard=state/'controller.guard'
    recovery=state/'recovery.guard';recovery.mkdir()
    try:
        owner_path=guard/'owner.json';original=owner_path.read_bytes();owner=json.loads(original)
        if owner['host']!=socket.gethostname():raise RuntimeError('Owner belongs to another host/container; operator inspection is required')
        def alive(pid,group=False):
            try:
                (os.killpg if group else os.kill)(pid,0)
                return True
            except ProcessLookupError:return False
        if alive(owner['pid']):raise RuntimeError('Controller owner is still alive')
        for record in (state/'jobs').rglob('process.json'):
            if alive(json.loads(record.read_text())['process_group'],group=True):
                raise RuntimeError('A recorded stage process group is still alive')
        if owner_path.read_bytes()!=original:raise RuntimeError('Guard owner changed during recovery')
        Queue(state).event('guard_recovered',owner=owner,reason=reason)
        owner_path.unlink();guard.rmdir()
    finally:recovery.rmdir()


class Queue:
    def __init__(self,state,readonly=False):
        self.state=Path(state);self.db=self.state/'queue.sqlite3';self.readonly=readonly
        if not readonly:
            with self.connect() as db:
                db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, raw_sha TEXT UNIQUE, seq INTEGER UNIQUE, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(n INTEGER PRIMARY KEY, time REAL, kind TEXT, data TEXT);
                CREATE TABLE IF NOT EXISTS issues(marker TEXT PRIMARY KEY, data TEXT);
                CREATE TABLE IF NOT EXISTS geometries(id TEXT PRIMARY KEY, data TEXT);
                ''')
    def connect(self):
        return sqlite3.connect('file:'+str(self.db)+'?mode=ro' if self.readonly else self.db,
                               uri=self.readonly,timeout=30)
    def get(self,key,default=None):
        with self.connect() as db:r=db.execute('SELECT data FROM meta WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else default
    def put(self,key,value):
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(key,json.dumps(value)))
    def event(self,kind,**data):
        with self.connect() as db:db.execute('INSERT INTO events(time,kind,data) VALUES (?,?,?)',(time.time(),kind,json.dumps(data)))
    def jobs(self):
        with self.connect() as db:return [json.loads(r[0]) for r in db.execute('SELECT data FROM jobs ORDER BY COALESCE(seq,0),id')]
    def job(self,identity):
        return next((j for j in self.jobs() if j['id']==identity),None)
    def add(self,record):
        with self.connect() as db:db.execute('INSERT INTO jobs VALUES (?,?,?,?)',
            (record['id'],record['raw_sha256'],record['sequence'],json.dumps(record)))
        self.event('discovered',input_id=record['id'],sequence=record['sequence'])
    def update(self,record):
        with self.connect() as db:db.execute('UPDATE jobs SET data=? WHERE id=?',(json.dumps(record),record['id']))
    def issue(self,marker,error):
        value={'marker':str(marker),'error':str(error)}
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO issues VALUES (?,?)',(str(marker),json.dumps(value)))
    def issues(self):
        with self.connect() as db:return [json.loads(r[0]) for r in db.execute('SELECT data FROM issues ORDER BY marker')]
    def geometry(self,identity):
        with self.connect() as db:r=db.execute('SELECT data FROM geometries WHERE id=?',(identity,)).fetchone()
        if not r:raise ValueError('Unknown Geometry version: '+identity)
        return json.loads(r[0])
    def geometries(self):
        with self.connect() as db:return [json.loads(r[0]) for r in db.execute('SELECT data FROM geometries ORDER BY id')]
    def geometry_put(self,record):
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO geometries VALUES (?,?)',(record['id'],json.dumps(record)))
