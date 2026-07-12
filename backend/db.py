"""Persistencia en SQLite: usuarios, sesiones y trabajos de generación.

El historial de eventos (tokens SSE) sigue viviendo en memoria — es efímero por
naturaleza — pero los metadatos del trabajo se guardan aquí para que el historial
por usuario sobreviva reinicios del servidor.

sqlite3 es síncrono; todas las operaciones son puntuales (sub-ms) así que se
ejecutan directo en el event loop, serializadas con un lock por si algún día se
usan hilos.
"""
import sqlite3
import threading
import time

from . import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    prompt TEXT NOT NULL,
    model TEXT NOT NULL,
    stack TEXT NOT NULL,
    device TEXT NOT NULL DEFAULT 'gpu',      -- gpu | cpu
    kind TEXT NOT NULL DEFAULT 'generate',   -- generate | edit
    parent_id TEXT,                          -- job del que parte una edición
    status TEXT NOT NULL,
    error TEXT,
    files TEXT NOT NULL DEFAULT '',          -- nombres separados por \n
    created_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id, created_at DESC);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Agrega columnas nuevas a bases de datos creadas por versiones previas.

    CREATE TABLE IF NOT EXISTS no altera tablas ya existentes, así que una
    base creada antes de introducir `device` no la tendría sin este paso.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "device" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN device TEXT NOT NULL DEFAULT 'gpu'")
        conn.commit()


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
        _conn.commit()
        _migrate(_conn)
    return _conn


def _exec(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        cur = connect().execute(sql, params)
        connect().commit()
        return cur


def _query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return connect().execute(sql, params).fetchall()


# ── Usuarios ─────────────────────────────────────────────────────
def create_user(username: str, password_hash: str) -> int:
    cur = _exec(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (username, password_hash, time.time()),
    )
    return cur.lastrowid


def get_user_by_name(username: str) -> sqlite3.Row | None:
    rows = _query("SELECT * FROM users WHERE username = ?", (username,))
    return rows[0] if rows else None


def get_user(user_id: int) -> sqlite3.Row | None:
    rows = _query("SELECT * FROM users WHERE id = ?", (user_id,))
    return rows[0] if rows else None


# ── Sesiones ─────────────────────────────────────────────────────
def create_session(token: str, user_id: int, ttl_seconds: float) -> None:
    now = time.time()
    _exec(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, now, now + ttl_seconds),
    )


def get_session_user(token: str) -> sqlite3.Row | None:
    rows = _query(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token = ? AND s.expires_at > ?""",
        (token, time.time()),
    )
    return rows[0] if rows else None


def delete_session(token: str) -> None:
    _exec("DELETE FROM sessions WHERE token = ?", (token,))


def purge_expired_sessions() -> None:
    _exec("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))


# ── Trabajos ─────────────────────────────────────────────────────
def insert_job(job_id: str, user_id: int, prompt: str, model: str, stack: str,
               kind: str = "generate", parent_id: str | None = None,
               device: str = "gpu") -> None:
    _exec(
        """INSERT INTO jobs (id, user_id, prompt, model, stack, device, kind,
                             parent_id, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
        (job_id, user_id, prompt, model, stack, device, kind, parent_id, time.time()),
    )


def update_job(job_id: str, status: str, error: str | None = None,
               files: list[str] | None = None, finished_at: float | None = None) -> None:
    _exec(
        "UPDATE jobs SET status = ?, error = ?, files = ?, finished_at = ? WHERE id = ?",
        (status, error, "\n".join(files or []), finished_at, job_id),
    )


def get_job(job_id: str) -> sqlite3.Row | None:
    rows = _query("SELECT * FROM jobs WHERE id = ?", (job_id,))
    return rows[0] if rows else None


def jobs_for_user(user_id: int, limit: int = 60) -> list[sqlite3.Row]:
    return _query(
        "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )
