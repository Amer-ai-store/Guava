CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent(id TEXT PRIMARY KEY,last_seen REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, action TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,
 status TEXT NOT NULL,lease TEXT,completed REAL,output TEXT,identity_only INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_action_created ON jobs(action,created DESC);
CREATE INDEX IF NOT EXISTS jobs_state_expiry ON jobs(status,expires);
CREATE INDEX IF NOT EXISTS jobs_completed ON jobs(completed DESC);
CREATE UNIQUE INDEX IF NOT EXISTS one_running ON jobs(status) WHERE status='running';
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_action ON jobs(action) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,at REAL,event TEXT,job_id TEXT);
CREATE TABLE IF NOT EXISTS login_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,at REAL);
CREATE INDEX IF NOT EXISTS login_at ON login_attempts(at);
CREATE TABLE IF NOT EXISTS schema_chunks(hash TEXT NOT NULL,part INTEGER NOT NULL,content TEXT NOT NULL,PRIMARY KEY(hash,part));
