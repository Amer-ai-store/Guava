"""Standard-library HTTP service. Bind loopback behind an authenticated TLS reverse proxy."""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from trustsoft.source import SqlSource, SourceError, encode
from trustsoft.investigation import investigate
from trustsoft.control import ControlStore, ControlError, backup_inventory, MAX_RESULT
from trustsoft.agent_source import AgentSource

WEB = Path(__file__).resolve().parent.parent / 'web'

class Monitor:
    def __init__(self, source):
        self.source = source
        self.lock = threading.Lock()
        self.refresh_lock = threading.Lock()
        self.state = {'state': 'waiting', 'data': None, 'last_success': None, 'error': None, 'failures': 0, 'events': []}

    def refresh(self):
        if not self.refresh_lock.acquire(blocking=False):
            return
        try:
            now = time.time()
            try:
                data = self.source.snapshot()
                # Normalize decimals/datetimes once; independent snapshots for readers.
                data = json.loads(json.dumps(data, default=encode))
                data['investigation'] = investigate(data.get('sections', {}))
                with self.lock:
                    old = self.state['state']
                    self.state.update(state='connected', data=data, last_success=now, error=None, failures=0)
                    if old != 'connected':
                        self.event('connection_restored', now)
            except SourceError as exc:
                with self.lock:
                    self.state.update(state='disconnected', error=str(exc), failures=self.state['failures'] + 1)
                    self.event('connection_failed', now)
            except Exception:
                with self.lock:
                    self.state.update(state='disconnected', error='source_processing_failed', failures=self.state['failures'] + 1)
                    self.event('connection_failed', now)
        finally:
            self.refresh_lock.release()

    def event(self, kind, now):
        self.state['events'] = (self.state['events'] + [{'type': kind, 'utc_epoch': now}])[-50:]

    def snapshot(self):
        with self.lock:
            result = dict(self.state)
            result['stale'] = result['last_success'] is None or result['state'] != 'connected' or time.time() - result['last_success'] > 90
            return result

    def run(self, stop):
        while not stop.is_set():
            self.refresh()
            failures = self.snapshot()['failures']
            stop.wait(min(300, 30 * (2 ** min(failures, 3))))

class App(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, monitor, token, control=None):
        self.monitor = monitor
        self.token_hash = hashlib.sha256(token.encode()).digest()
        self.sessions = {}
        self.session_roles = {}
        admin = os.getenv('TRUSTSOFT_ADMIN_TOKEN', '')
        agent = os.getenv('TRUSTSOFT_AGENT_TOKEN', '')
        if admin and (len(admin) < 32 or admin == token or admin == agent):
            raise ValueError('Admin, reader and agent credentials must be distinct, at least 32 characters each.')
        if agent and (len(agent) < 32 or agent == token):
            raise ValueError('Agent credential must be distinct and at least 32 characters.')
        self.admin_hash = hashlib.sha256(admin.encode()).digest() if admin else None
        self.agent_hash = hashlib.sha256(agent.encode()).digest() if agent else None
        self.control = control or ControlStore(os.getenv('TRUSTSOFT_CONTROL_DB', 'local/control.sqlite3'))
        self.attempts = []
        self.auth_lock = threading.Lock()
        self.secure_cookie = os.getenv('TRUSTSOFT_SECURE_COOKIE', '1') == '1'
        super().__init__(address, Handler)

class Handler(BaseHTTPRequestHandler):
    server_version = 'TrustSoft'
    def log_message(self, *args):
        pass  # Never log cookies, financial rows, or credentials.

    def send(self, status, body, mime='application/json; charset=utf-8', cookie=None):
        raw = json.dumps(body, ensure_ascii=False).encode() if mime.startswith('application/json') else body
        self.send_response(status)
        for key, val in {
            'Content-Type': mime, 'Content-Length': str(len(raw)), 'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        }.items():
            self.send_header(key, val)
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self, admin=False):
        cookie = self.headers.get('Cookie', '')
        ids = [v.strip().split('=',1)[1] for v in cookie.split(';') if v.strip().startswith(('ts=', 'ts_admin='))]
        with self.server.auth_lock:
            return any(self.server.sessions.get(sid, 0) > time.time() and
                       (not admin or self.server.session_roles.get(sid) == 'admin') for sid in ids)

    def payload(self, limit=4096):
        length = int(self.headers.get('Content-Length', '0'))
        if length <= 0 or length > limit:
            raise ValueError('invalid_body_size')
        result = json.loads(self.rfile.read(length))
        if not isinstance(result, dict):
            raise ValueError('invalid_json')
        return result

    def control_post(self):
        agent_route = self.path in ('/api/agent/hello', '/api/agent/poll', '/api/agent/result')
        if agent_route:
            header = self.headers.get('Authorization', '')
            if not header.startswith('Bearer ') or self.server.agent_hash is None or not hmac.compare_digest(
                hashlib.sha256(header[7:].encode()).digest(), self.server.agent_hash):
                return self.send(401, {'error':'agent_unauthorized'})
        else:
            if not self.authorized(admin=True):
                return self.send(403, {'error':'admin_required'})
            if self.headers.get('X-TrustSoft-Action') != '1' or self.headers.get('Sec-Fetch-Site') not in (None,'same-origin','none'):
                return self.send(403, {'error':'cross_site_request'})
            if self.server.agent_hash is None:
                return self.send(503, {'error':'agent_not_configured'})
        try:
            data = self.payload(MAX_RESULT + 4096 if agent_route else 4096)
            if self.path == '/api/agent/hello':
                if data != {'agent_id':'JAWAFFASVR'}:
                    raise ValueError()
                return self.send(200, {'broker_id':self.server.control.broker_id})
            if self.path == '/api/agent/poll':
                if set(data) != {'agent_id'}:
                    raise ValueError()
                job = self.server.control.poll(data['agent_id'])
                return self.send(200, {'job':job})
            if self.path == '/api/agent/result':
                if set(data) != {'id','lease','output'} or not isinstance(data['id'],str):
                    raise ValueError()
                self.server.control.complete(data['id'],data['lease'],data['output'])
                return self.send(200, {'accepted':True})
            if set(data) != {'action'} or not isinstance(data['action'],str):
                raise ValueError()
            job = self.server.control.enqueue(data['action'])
            return self.send(202, job)
        except ControlError as exc:
            return self.send(409, {'error':str(exc)})
        except (ValueError, KeyError, TypeError):
            return self.send(400, {'error':'invalid_request'})

    def do_POST(self):
        self.connection.settimeout(10)
        if self.path in ('/api/control/jobs','/api/agent/hello','/api/agent/poll','/api/agent/result'):
            return self.control_post()
        if self.path not in ('/api/login','/api/admin/login'):
            return self.send(405, {'error': 'read_only_api'})
        is_admin = self.path == '/api/admin/login'
        if is_admin and self.server.admin_hash is None:
            return self.send(503, {'error':'admin_not_configured'})
        # Do not trust forwarded host headers. No cross-site browser logins.
        if self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
            return self.send(403, {'error': 'cross_site_request'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 4096:
                return self.send(413, {'error': 'invalid_body_size'})
            payload = json.loads(self.rfile.read(length))
            token = payload.get('token', '')
            if not isinstance(token, str):
                raise ValueError()
        except (ValueError, AttributeError):
            return self.send(400, {'error': 'invalid_json'})
        with self.server.auth_lock:
            now = time.time()
            self.server.attempts = [t for t in self.server.attempts if t > now - 60]
            if len(self.server.attempts) >= 10:
                return self.send(429, {'error': 'try_later'})
            self.server.attempts.append(now)
            expected = self.server.admin_hash if is_admin else self.server.token_hash
            if not hmac.compare_digest(hashlib.sha256(token.encode()).digest(), expected):
                return self.send(401, {'error': 'unauthorized'})
            self.server.sessions = {s:e for s,e in self.server.sessions.items() if e > now}
            self.server.session_roles = {s:r for s,r in self.server.session_roles.items() if s in self.server.sessions}
            if len(self.server.sessions) >= 100:
                return self.send(429, {'error': 'session_limit'})
            sid = secrets.token_urlsafe(32)
            self.server.sessions[sid] = now + 3600
            self.server.session_roles[sid] = 'admin' if is_admin else 'reader'
        cookie = ('ts_admin=' if is_admin else 'ts=')+sid+'; Path=/; HttpOnly; SameSite=Strict; Max-Age=3600'
        if self.server.secure_cookie:
            cookie += '; Secure'
        self.send(200, {'ok': True}, cookie=cookie)

    def do_GET(self):
        if self.path == '/healthz':
            # Hosting liveness only: do not restart a healthy web process for a remote SQL outage.
            return self.send(200, {'process':'running'})
        if self.path.startswith('/api/'):
            if not self.authorized():
                return self.send(401, {'error': 'unauthorized'})
            if self.path == '/api/control/state':
                if not self.authorized(admin=True):
                    return self.send(403, {'error':'admin_required'})
                data = self.server.control.snapshot()
                data.update(agent_configured=self.server.agent_hash is not None,
                            backups=backup_inventory(os.getenv('TRUSTSOFT_BACKUP_DIR')))
                return self.send(200, data)
            state = self.server.monitor.snapshot()
            if self.path == '/api/snapshot':
                return self.send(200, state)
            if self.path == '/api/health':
                return self.send(200 if not state['stale'] else 503, {k:v for k,v in state.items() if k != 'data'})
            return self.send(404, {'error': 'not_found'})
        files = {'/': ('index.html','text/html; charset=utf-8'), '/control': ('control.html','text/html; charset=utf-8'), '/app.js': ('app.js','text/javascript; charset=utf-8'), '/style.css': ('style.css','text/css; charset=utf-8')}
        if self.path not in files:
            return self.send(404, {'error': 'not_found'})
        name, mime = files[self.path]
        self.send(200, (WEB / name).read_bytes(), mime)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8080, type=int)
    args = parser.parse_args()
    token = os.getenv('TRUSTSOFT_API_TOKEN', '')
    if len(token) < 32:
        parser.error('Set TRUSTSOFT_API_TOKEN to a secure random value of at least 32 characters.')
    control = ControlStore(os.getenv('TRUSTSOFT_CONTROL_DB', 'local/control.sqlite3'))
    monitor = Monitor(AgentSource(control) if os.getenv('TRUSTSOFT_SOURCE') == 'agent' else SqlSource())
    stop = threading.Event()
    app = App((args.host, args.port), monitor, token, control=control)
    thread = threading.Thread(target=monitor.run, args=(stop,), daemon=True)
    thread.start()
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        app.server_close()
        thread.join(timeout=2)
        # Daemon request workers may still hold the store briefly; process exit closes it.

if __name__ == '__main__':
    main()
