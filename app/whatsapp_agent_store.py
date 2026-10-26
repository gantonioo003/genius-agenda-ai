"""Fila privada de turnos: persistir antes do ack e nunca repetir trabalho incerto."""
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import sqlite3
from threading import Lock
import unicodedata
from uuid import UUID, uuid4

from app.whatsapp_inbox import InboxClaim, InboxEvent

DEFAULT_AGENT_PATH = Path('data/whatsapp-agent.sqlite3')
SCHEMA_VERSION = '1'
_HASH = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)
_STATES = {'staged', 'queued', 'processing', 'prepared', 'delivering', 'sent', 'rejected', 'uncertain'}
_MESSAGE_TYPES = {'message_' + name for name in (
    'text','image','audio','video','document','sticker','location','contacts',
    'interactive','button','reaction','order','system','unknown','unsupported')}
_COLUMNS = ('sequence','job_id','request_id','event_key','sender_key','event_type',
            'text','reply','state','attempt_id','fingerprint','created_at','updated_at')
_ACTIVE_INDEX = "CREATE UNIQUE INDEX agent_sender_active ON agent_jobs(sender_key) WHERE state IN ('processing','delivering','uncertain')"

class AgentStoreError(RuntimeError):
    def __init__(self):
        super().__init__('A fila privada do agente está indisponível ou inconsistente.')

@dataclass(frozen=True, repr=False)
class AgentInput:
    event_key: str
    event_type: str
    text: str | None = None
    def __repr__(self): return 'AgentInput(<private>)'

@dataclass(frozen=True, repr=False)
class AgentJob:
    sequence: int
    job_id: str
    request_id: str
    event_key: str
    sender_key: str
    event_type: str
    text: str | None
    reply: str | None
    state: str
    attempt_id: str | None
    fingerprint: str | None
    def __repr__(self): return 'AgentJob(<private>)'

@dataclass(frozen=True, repr=False)
class AgentSession:
    conversation_id: str
    authorization_action: str | None = None
    def __repr__(self): return 'AgentSession(<private>)'

@dataclass(frozen=True)
class Activation:
    known: bool
    count: int
    state: str

@dataclass(frozen=True)
class ClaimState:
    known: bool
    count: int
    states: tuple[str, ...]


def _uuid(value):
    if not isinstance(value, str): raise ValueError()
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value: raise ValueError()
    return value

def _hash(value):
    if not isinstance(value, str) or _HASH.fullmatch(value) is None: raise ValueError()
    return value

def _text(value, limit, *, allow_blank=False):
    if (not isinstance(value, str) or not 0 < len(value) <= limit
            or not allow_blank and not value.strip()
            or any(unicodedata.category(char).startswith('C') and char not in '\t\r\n' for char in value)):
        raise ValueError()
    value.encode('utf-8')
    return value

def _timestamp(value):
    if not isinstance(value,str) or len(value)>40: raise ValueError()
    result=datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset()!=timezone.utc.utcoffset(result) or result.isoformat()!=value:
        raise ValueError()
    return result

def _input(value):
    if type(value) is not AgentInput: raise ValueError()
    _hash(value.event_key)
    if value.event_type not in _MESSAGE_TYPES: raise ValueError()
    if value.event_type == 'message_text': _text(value.text,4096,allow_blank=True)
    elif value.text is not None: raise ValueError()
    return value

def _session(value):
    if type(value) is not AgentSession: raise ValueError()
    _uuid(value.conversation_id)
    if value.authorization_action not in {None,'create','edit','reschedule'}: raise ValueError()
    return value

def _decode(row):
    try:
        (sequence,job_id,request_id,event_key,sender_key,event_type,text,reply,state,
         attempt_id,fingerprint,created_at,updated_at)=row
        if type(sequence) is not int or sequence<1: raise ValueError()
        for value in (job_id,request_id): _uuid(value)
        for value in (event_key,sender_key): _hash(value)
        if event_type not in _MESSAGE_TYPES or state not in _STATES: raise ValueError()
        if _timestamp(updated_at)<_timestamp(created_at): raise ValueError()
        if state in {'staged','queued'}:
            _input(AgentInput(event_key,event_type,text))
            if any(value is not None for value in (reply,attempt_id,fingerprint)): raise ValueError()
        else:
            _uuid(attempt_id)
            if state=='processing':
                _input(AgentInput(event_key,event_type,text))
                if reply is not None or fingerprint is not None: raise ValueError()
            elif state in {'prepared','delivering'}:
                _text(reply,4000)
                if text is not None or fingerprint is not None: raise ValueError()
            elif state in {'sent','rejected'}:
                if state=='sent': _hash(fingerprint)
                elif fingerprint is not None: raise ValueError()
                if text is not None or reply is not None: raise ValueError()
            else:
                if fingerprint is not None: raise ValueError()
                if reply is None: _input(AgentInput(event_key,event_type,text))
                else:
                    _text(reply,4000)
                    if text is not None: raise ValueError()
        return AgentJob(sequence,job_id,request_id,event_key,sender_key,event_type,text,reply,state,attempt_id,fingerprint)
    except Exception:
        raise AgentStoreError() from None

class AgentStore:
    """Uma tentativa por fase, FIFO por remetente e retenção do estado incerto."""
    def __init__(self,path=DEFAULT_AGENT_PATH,*,clock=None,max_entries=10000):
        if type(max_entries) is not int or not 1<=max_entries<=100000: raise AgentStoreError()
        self.path=Path(path)
        self._clock=clock or (lambda:datetime.now(timezone.utc))
        self.max_entries=max_entries
        self._initialized=False
        self._initialization_lock=Lock()
    def _now(self):
        try:
            value=self._clock()
            if not isinstance(value,datetime) or value.utcoffset() is None: raise ValueError()
            return value.astimezone(timezone.utc).isoformat()
        except Exception: raise AgentStoreError() from None
    @contextmanager
    def _connection(self):
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(self.path,timeout=1,isolation_level=None)) as connection:
                yield connection
        except (sqlite3.Error,OSError): raise AgentStoreError() from None
    @staticmethod
    @contextmanager
    def _transaction(connection):
        try:
            connection.execute('BEGIN IMMEDIATE')
            yield
            connection.commit()
        except BaseException:
            if connection.in_transaction: connection.rollback()
            raise
    @staticmethod
    def _check(connection):
        objects=set(connection.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
        if objects!={('table','agent_queue_metadata'),('table','agent_jobs'),('table','agent_sessions'),('index','agent_sender_active')}:
            raise AgentStoreError()
        meta=tuple((r[1],r[2],r[3],r[5]) for r in connection.execute('PRAGMA table_info(agent_queue_metadata)'))
        if meta!=(('name','TEXT',1,1),('value','TEXT',1,0)): raise AgentStoreError()
        expected=tuple((name,'INTEGER' if name=='sequence' else 'TEXT',0 if name in {'sequence','text','reply','attempt_id','fingerprint'} else 1,1 if name=='sequence' else 0) for name in _COLUMNS)
        actual=tuple((r[1],r[2],r[3],r[5]) for r in connection.execute('PRAGMA table_info(agent_jobs)'))
        if actual!=expected: raise AgentStoreError()
        sessions=tuple((r[1],r[2],r[3],r[5]) for r in connection.execute('PRAGMA table_info(agent_sessions)'))
        if sessions!=(('sender_key','TEXT',1,1),('conversation_id','TEXT',1,0),('authorization_action','TEXT',0,0)): raise AgentStoreError()
        if connection.execute('SELECT name,value FROM agent_queue_metadata').fetchall()!=[('schema_version',SCHEMA_VERSION)]: raise AgentStoreError()
        if connection.execute("SELECT sql FROM sqlite_master WHERE name='agent_sender_active'").fetchone()!=(_ACTIVE_INDEX,): raise AgentStoreError()
        if connection.execute('PRAGMA quick_check').fetchall()!=[('ok',)]: raise AgentStoreError()
        for row in connection.execute('SELECT '+','.join(_COLUMNS)+' FROM agent_jobs'): _decode(row)
        try:
            for sender,cid,action in connection.execute('SELECT sender_key,conversation_id,authorization_action FROM agent_sessions'):
                _hash(sender); _session(AgentSession(cid,action))
        except Exception: raise AgentStoreError() from None
    def initialize(self):
        if self._initialized:return
        with self._initialization_lock:
            if self._initialized:return
            self._now()
            with self._connection() as connection,self._transaction(connection):
                if not connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall():
                    connection.execute('CREATE TABLE agent_queue_metadata(name TEXT NOT NULL PRIMARY KEY,value TEXT NOT NULL)')
                    connection.execute('INSERT INTO agent_queue_metadata VALUES(?,?)',('schema_version',SCHEMA_VERSION))
                    connection.execute('CREATE TABLE agent_jobs(sequence INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL UNIQUE,request_id TEXT NOT NULL,event_key TEXT NOT NULL UNIQUE,sender_key TEXT NOT NULL,event_type TEXT NOT NULL,text TEXT,reply TEXT,state TEXT NOT NULL,attempt_id TEXT,fingerprint TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)')
                    connection.execute('CREATE TABLE agent_sessions(sender_key TEXT NOT NULL PRIMARY KEY,conversation_id TEXT NOT NULL,authorization_action TEXT)')
                    connection.execute(_ACTIVE_INDEX)
                self._check(connection)
            self._initialized=True
    @contextmanager
    def _active(self):
        self.initialize()
        with self._connection() as connection,self._transaction(connection):
            self._check(connection)
            yield connection,self._now()
    @staticmethod
    def _row(connection,job_id):
        return connection.execute('SELECT '+','.join(_COLUMNS)+' FROM agent_jobs WHERE job_id=?',(job_id,)).fetchone()
    def stage(self,claim,inputs,sender_key):
        try:
            if type(claim) is not InboxClaim or claim.status!='new' or type(claim.events) is not tuple: raise ValueError()
            _uuid(claim.request_id); _hash(sender_key)
            if type(claim.duplicate_count) is not int or claim.duplicate_count<0: raise ValueError()
            expected={}
            for event in claim.events:
                if type(event) is not InboxEvent: raise ValueError()
                InboxEvent(event.event_key,event.event_type)
                if event.event_type.startswith('message_'):
                    if event.event_key in expected: raise ValueError()
                    expected[event.event_key]=event.event_type
            if type(inputs) is not tuple or len(inputs)>100: raise ValueError()
            values={}
            for value in inputs:
                value=_input(value)
                if value.event_key in values: raise ValueError()
                values[value.event_key]=value
            if {key:value.event_type for key,value in values.items()}!=expected: raise ValueError()
        except Exception: raise AgentStoreError() from None
        if not values:return 0
        with self._active() as (connection,now):
            existing={}
            for value in values.values():
                row=connection.execute('SELECT '+','.join(_COLUMNS)+' FROM agent_jobs WHERE event_key=?',(value.event_key,)).fetchone()
                if row is not None:
                    old=_decode(row)
                    # Once the text is redacted, its original identity still cannot
                    # be rebound to another request, sender or message type.
                    if old.request_id!=claim.request_id or old.sender_key!=sender_key or old.event_type!=value.event_type or old.state in {'staged','queued','processing'} and old.text!=value.text:
                        raise AgentStoreError()
                    existing[value.event_key]=old
            if connection.execute('SELECT COUNT(*) FROM agent_jobs').fetchone()[0]+len(values)-len(existing)>self.max_entries: raise AgentStoreError()
            for key,value in values.items():
                if key in existing:continue
                connection.execute('INSERT INTO agent_jobs(job_id,request_id,event_key,sender_key,event_type,text,reply,state,attempt_id,fingerprint,created_at,updated_at) VALUES(?,?,?,?,?,?,NULL,?,NULL,NULL,?,?)',
                    (str(uuid4()),claim.request_id,key,sender_key,value.event_type,value.text,'staged',now,now))
            return len(values)
    def get_claim(self,request_id):
        try:_uuid(request_id)
        except Exception:raise AgentStoreError() from None
        with self._active() as (connection,_):
            states=tuple(row[0] for row in connection.execute('SELECT state FROM agent_jobs WHERE request_id=? ORDER BY sequence',(request_id,)))
            return ClaimState(bool(states),len(states),states)
    def activate(self,request_id):
        try:_uuid(request_id)
        except Exception:raise AgentStoreError() from None
        with self._active() as (connection,now):
            count=connection.execute('SELECT COUNT(*) FROM agent_jobs WHERE request_id=?',(request_id,)).fetchone()[0]
            if count:
                connection.execute("UPDATE agent_jobs SET state='queued',updated_at=? WHERE request_id=? AND state='staged'",(now,request_id))
            return Activation(bool(count),count,'queued' if count else 'missing')
    def get_job(self,job_id):
        try:_uuid(job_id)
        except Exception:raise AgentStoreError() from None
        with self._active() as (connection,_):
            row=self._row(connection,job_id)
            return None if row is None else _decode(row)
    def claim_next(self):
        with self._active() as (connection,now):
            row=connection.execute('SELECT '+','.join('j.'+name for name in _COLUMNS)+" FROM agent_jobs j WHERE j.state IN ('queued','prepared') AND NOT EXISTS(SELECT 1 FROM agent_jobs prior WHERE prior.sender_key=j.sender_key AND prior.sequence<j.sequence AND prior.state NOT IN ('sent','rejected')) AND NOT EXISTS(SELECT 1 FROM agent_jobs busy WHERE busy.sender_key=j.sender_key AND busy.state IN ('processing','delivering','uncertain')) ORDER BY j.sequence LIMIT 1").fetchone()
            if row is None:return None
            job=_decode(row)
            state='processing' if job.state=='queued' else 'delivering'
            attempt=str(uuid4())
            connection.execute('UPDATE agent_jobs SET state=?,attempt_id=?,updated_at=? WHERE job_id=? AND state=?',(state,attempt,now,job.job_id,job.state))
            return _decode(self._row(connection,job.job_id))
    def _owned(self,connection,job,states):
        if type(job) is not AgentJob or job.state not in states: raise AgentStoreError()
        row=self._row(connection,job.job_id)
        if row is None or _decode(row)!=job:raise AgentStoreError()
    def prepare(self,job,reply):
        try:_text(reply,4000)
        except Exception:raise AgentStoreError() from None
        with self._active() as (connection,now):
            self._owned(connection,job,{'processing'})
            connection.execute("UPDATE agent_jobs SET state='prepared',text=NULL,reply=?,updated_at=? WHERE job_id=?",(reply,now,job.job_id))
            return _decode(self._row(connection,job.job_id))
    def complete(self,job,fingerprint):
        try:_hash(fingerprint)
        except Exception:raise AgentStoreError() from None
        with self._active() as (connection,now):
            self._owned(connection,job,{'delivering'})
            connection.execute("UPDATE agent_jobs SET state='sent',text=NULL,reply=NULL,fingerprint=?,updated_at=? WHERE job_id=?",(fingerprint,now,job.job_id))
            return _decode(self._row(connection,job.job_id))
    def reject(self,job):
        """Somente recusa comprovada antes/na chamada de envio; não é retry."""
        with self._active() as (connection,now):
            self._owned(connection,job,{'delivering'})
            connection.execute("UPDATE agent_jobs SET state='rejected',text=NULL,reply=NULL,updated_at=? WHERE job_id=?",(now,job.job_id))
            return _decode(self._row(connection,job.job_id))
    def uncertain(self,job):
        with self._active() as (connection,now):
            self._owned(connection,job,{'processing','delivering'})
            connection.execute("UPDATE agent_jobs SET state='uncertain',updated_at=? WHERE job_id=?",(now,job.job_id))
            return _decode(self._row(connection,job.job_id))
    def get_session(self,sender_key):
        try:_hash(sender_key)
        except Exception:raise AgentStoreError() from None
        with self._active() as (connection,_):
            row=connection.execute('SELECT conversation_id,authorization_action FROM agent_sessions WHERE sender_key=?',(sender_key,)).fetchone()
            return None if row is None else _session(AgentSession(*row))
    def set_session(self,sender_key,conversation_id,authorization_action=None):
        try:_hash(sender_key);value=_session(AgentSession(conversation_id,authorization_action))
        except Exception:raise AgentStoreError() from None
        with self._active() as (connection,_):
            connection.execute('INSERT INTO agent_sessions VALUES(?,?,?) ON CONFLICT(sender_key) DO UPDATE SET conversation_id=excluded.conversation_id,authorization_action=excluded.authorization_action',(sender_key,value.conversation_id,value.authorization_action))
        return value
    def state_counts(self):
        with self._active() as (connection,_):
            return {state:count for state,count in connection.execute('SELECT state,COUNT(*) FROM agent_jobs GROUP BY state')}
