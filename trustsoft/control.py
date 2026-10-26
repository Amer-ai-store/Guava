"""Durable allowlisted read-only jobs for one enrolled outbound agent."""
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time

ACTIONS = ('server_probe', 'sql_probe', 'export_probe', 'backup_probe', 'reconnect')
MAX_RESULT = 8 * 1024 * 1024

class ControlError(Exception):
    pass

class ControlStore:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        self.path = str(path)
        if self.path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        if self.path != ':memory:':
            os.chmod(self.path, 0o600)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY, action TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
          status TEXT NOT NULL, lease TEXT, leased REAL, completed REAL, output TEXT);
        CREATE TABLE IF NOT EXISTS agent(id TEXT PRIMARY KEY, last_seen REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at REAL, event TEXT, job_id TEXT);
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES('broker_id',?)", (secrets.token_hex(16),))
        self.broker_id = self.db.execute("SELECT value FROM metadata WHERE key='broker_id'").fetchone()[0]

    def audit(self, event, job_id=None):
        self.db.execute('INSERT INTO audit(at,event,job_id) VALUES(?,?,?)', (self.clock(),event,job_id))

    def expire(self):
        now = self.clock()
        self.db.execute("UPDATE jobs SET status='expired' WHERE status IN ('queued','running') AND expires<?", (now,))

    def enqueue(self, action, automatic=False):
        if action not in ACTIONS:
            raise ControlError('action_not_allowed')
        with self.lock, self.db:
            self.expire()
            pending = self.db.execute("SELECT id FROM jobs WHERE action=? AND status IN ('queued','running')", (action,)).fetchone()
            if pending:
                return {'id': pending['id'], 'deduplicated': True}
            last = self.db.execute('SELECT created FROM jobs WHERE action=? ORDER BY created DESC LIMIT 1', (action,)).fetchone()
            if last and self.clock()-last['created'] < 15:
                raise ControlError('action_cooldown')
            if self.db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 5:
                raise ControlError('queue_full')
            job_id = secrets.token_hex(16)
            self.db.execute('INSERT INTO jobs(id,action,created,expires,status) VALUES(?,?,?,?,?)',
                            (job_id,action,self.clock(),self.clock()+120,'queued'))
            self.audit('automatic_probe_queued' if automatic else 'operator_probe_queued', job_id)
            return {'id':job_id,'deduplicated':False}

    def poll(self, agent_id):
        if agent_id != 'JAWAFFASVR':
            raise ControlError('agent_not_enrolled')
        with self.lock:
            # Queue timers separately from the lease transaction.
            for action, interval in (('sql_probe',60), ('server_probe',300), ('backup_probe',300)):
                choices = ('sql_probe','reconnect') if action == 'sql_probe' else (action,)
                placeholders = ','.join('?' for _ in choices)
                previous = self.db.execute('SELECT created FROM jobs WHERE action IN ('+placeholders+') ORDER BY created DESC LIMIT 1', choices).fetchone()
                if not previous or self.clock()-previous['created'] >= interval:
                    try:
                        self.enqueue(action, automatic=True)
                    except ControlError:
                        pass
            with self.db:
                # First UPDATE acquires the SQLite write lock before selecting a job.
                # This also serializes leases across independent broker processes.
                self.expire()
                self.db.execute('INSERT OR REPLACE INTO agent(id,last_seen) VALUES(?,?)', (agent_id,self.clock()))
                if self.db.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone():
                    return None
                job = self.db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created,id LIMIT 1").fetchone()
                if not job:
                    return None
                lease = secrets.token_urlsafe(32)
                self.db.execute("UPDATE jobs SET status='running',lease=?,leased=? WHERE id=?", (lease,self.clock(),job['id']))
                self.audit('probe_leased', job['id'])
                return {'id':job['id'],'action':job['action'],'lease':lease,'expires':job['expires']}

    def complete(self, job_id, lease, output):
        if not isinstance(output, dict) or not isinstance(output.get('execution_ok'),bool):
            raise ControlError('invalid_result')
        if any(key in output and not isinstance(output[key],dict) for key in ('sql','diagnostics','backups')):
            raise ControlError('invalid_result')
        raw = json.dumps(output, ensure_ascii=False, allow_nan=False)
        if len(raw.encode()) > MAX_RESULT:
            raise ControlError('result_too_large')
        with self.lock, self.db:
            self.expire()
            row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row or row['status'] != 'running' or not isinstance(lease,str) or not secrets.compare_digest(row['lease'],lease):
                raise ControlError('invalid_or_expired_lease')
            # Agent success means a completed check, not verified SQL or backup health.
            state = 'completed' if output.get('execution_ok') is True else 'failed'
            self.db.execute('UPDATE jobs SET status=?,output=?,completed=? WHERE id=?', (state,raw,self.clock(),job_id))
            self.audit('probe_'+state, job_id)

    def snapshot(self):
        with self.lock, self.db:
            self.expire()
            row = self.db.execute('SELECT last_seen FROM agent WHERE id=?', ('JAWAFFASVR',)).fetchone()
            seen = row['last_seen'] if row else None
            jobs = [dict(r) for r in self.db.execute(
                'SELECT id,action,created,expires,status,completed,output FROM jobs ORDER BY created DESC LIMIT 30')]
            for job in jobs:
                job['output'] = json.loads(job['output']) if job['output'] else None
                # Keep repeated full catalogs out of the dashboard job history response.
                sql = job['output'].get('sql') if isinstance(job['output'],dict) else None
                if isinstance(sql,dict):
                    for field in ('catalog','relations'):
                        rows = sql.pop(field,None)
                        sql[field+'_rows'] = len(rows) if isinstance(rows,list) else None
            events = [dict(r) for r in self.db.execute('SELECT at,event,job_id FROM audit ORDER BY id DESC LIMIT 50')]
            return {'broker_id':self.broker_id,'agent_id':'JAWAFFASVR','last_seen':seen,'online':seen is not None and self.clock()-seen <= 90,
                    'jobs':jobs,'audit':events,'actions':list(ACTIONS),'export_monitor':export_progress(jobs)}

    def sql_snapshot(self):
        with self.lock:
            status = self.snapshot()
            if not status['online']:
                return None
            row = self.db.execute("""SELECT output,completed FROM jobs WHERE action IN ('sql_probe','reconnect')
                AND status IN ('completed','failed') ORDER BY completed DESC LIMIT 1""").fetchone()
            if not row or self.clock()-row['completed'] > 90:
                return None
            output = json.loads(row['output'])
            data = output.get('sql', {})
            if output.get('execution_ok') is not True or data.get('state') != 'verified_read':
                return None
            identity = data.get('identity', {})
            if identity.get('database_name') != 'Jawafa_DB' or str(identity.get('server_name','')).split('\\')[0].split('.')[0].upper() != 'JAWAFFASVR':
                return None
            if not isinstance(data.get('catalog'), list) or not isinstance(data.get('relations'), list):
                return None
            return data

    def close(self):
        self.db.close()

def export_progress(jobs):
    samples = []
    for job in sorted(jobs, key=lambda j:j.get('completed') or 0, reverse=True):
        output = job.get('output') or {}
        diagnostic = output.get('diagnostics') or {}
        export = diagnostic.get('export') or {}
        if isinstance(export,dict) and export.get('state') == 'observed' and isinstance(export.get('items'),list) and export['items'] and isinstance(export['items'][0],dict):
            sample = dict(export['items'][0])
            sample['observed_at'] = job['completed']
            samples.append(sample)
        if len(samples) == 2:
            break
    result = {'state':'insufficient_evidence','completeness':'unverified','samples':samples}
    if len(samples) == 2:
        current, previous = samples
        if all(isinstance(s.get(k),(int,float)) for s in samples for k in ('count','bytes')):
            result['count_delta'] = current['count']-previous['count']
            result['bytes_delta'] = current['bytes']-previous['bytes']
            result['state'] = 'change_observed' if result['count_delta'] or result['bytes_delta'] else 'unchanged_samples'
    return result

def backup_inventory(directory):
    if not directory:
        return {'state':'not_configured','files':[],'restorability':'not_verified'}
    root = Path(directory)
    try:
        files = []
        for p in root.iterdir():
            if p.is_file() and not p.is_symlink() and p.suffix.lower() in ('.bak','.zip','.csv','.json'):
                stat = p.stat()
                files.append({'name':p.name,'bytes':stat.st_size,'modified_utc_epoch':stat.st_mtime})
                if len(files) >= 200:
                    return {'state':'inventory_limited','files':files,'restorability':'not_verified'}
        return {'state':'observed','files':files,'restorability':'not_verified'}
    except OSError:
        return {'state':'unavailable','files':[],'restorability':'not_verified'}
