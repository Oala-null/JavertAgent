"""院内本地监测账本：原子排队、最新输入去重及不确定任务隔离。"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS patients (
    pid TEXT PRIMARY KEY COLLATE NOCASE, done_hash TEXT, done_time REAL,
    blocked_reason TEXT, last_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pid TEXT NOT NULL COLLATE NOCASE REFERENCES patients(pid),
    digest TEXT NOT NULL, signature TEXT NOT NULL, snapshot TEXT NOT NULL,
    status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    folder TEXT NOT NULL DEFAULT '', tag TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL, updated REAL NOT NULL, retry_at REAL NOT NULL DEFAULT 0,
    started REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_queued ON jobs(pid) WHERE status='QUEUED';
CREATE UNIQUE INDEX IF NOT EXISTS one_running ON jobs(pid) WHERE status='RUNNING';
"""

_PUBLIC_META = ('service_state', 'scan_state', 'scan_total', 'scan_checked',
                'last_scan_finished', 'audit_state', 'current_tag', 'last_audit_tag',
                'last_audit_status', 'history_verified_imports', 'history_unverified')


def _stats(connection):
    counts = dict(connection.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status'))
    reasons = dict(connection.execute(
        "SELECT reason,COUNT(*) FROM jobs WHERE reason<>'' GROUP BY reason"))
    blocked = dict(connection.execute(
        'SELECT blocked_reason,COUNT(*) FROM patients WHERE blocked_reason IS NOT NULL GROUP BY blocked_reason'))
    phase = connection.execute("SELECT value FROM meta WHERE key='phase'").fetchone()
    metadata = {key: json.loads(row[0]) for key in _PUBLIC_META
                if (row := connection.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone())}
    ineligible = dict(connection.execute(
        "SELECT last_reason,COUNT(*) FROM patients WHERE last_reason<>'' GROUP BY last_reason"))
    return {'state': 'READY', 'known_patients': connection.execute('SELECT COUNT(*) FROM patients').fetchone()[0],
            'status': counts, 'reasons': reasons, 'blocked_reasons': blocked,
            'ineligible_reasons': ineligible,
            'blocked_patients': sum(blocked.values()),
            'phase': json.loads(phase[0]) if phase else 'idle', **metadata}


def read_stats(path: Path) -> dict:
    """status不加载运行配置，不创建或迁移账本。"""
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError('LEDGER_SYMLINK_REJECTED')
    if not path.exists():
        return {'state': 'NOT_STARTED'}
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        return _stats(connection)
    finally:
        connection.close()


class Store:
    def __init__(self, path: Path, context: str):
        self.path = Path(path)
        self.context = context
        self.lock = threading.RLock()
        if not isinstance(context, str) or not context:
            raise ValueError('LEDGER_CONTEXT_REQUIRED')
        if self.path.is_symlink():
            raise RuntimeError('LEDGER_SYMLINK_REJECTED')
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        self.connection = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute('PRAGMA foreign_keys=ON')
            self.connection.execute('PRAGMA synchronous=FULL')
            self.connection.executescript(_SCHEMA)
            with self._transaction() as connection:
                previous = self._meta(connection, 'context')
                if previous is not None and previous != context:
                    raise RuntimeError('LEDGER_CONTEXT_CHANGED')
                if previous is None:
                    if connection.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]:
                        raise RuntimeError('LEDGER_CONTEXT_MISSING')
                    self._put_meta(connection, 'context', context)
                    self._put_meta(connection, 'salt', secrets.token_hex(32))
                salt = self._meta(connection, 'salt')
                if not isinstance(salt, str) or len(salt) != 64:
                    raise RuntimeError('LEDGER_SALT_INVALID')
                self.salt = bytes.fromhex(salt)
        except BaseException:
            self.connection.close()
            raise

    @contextmanager
    def _transaction(self):
        with self.lock:
            self.connection.execute('BEGIN IMMEDIATE')
            try:
                yield self.connection
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    @staticmethod
    def _meta(connection, key):
        row = connection.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _put_meta(connection, key, value):
        connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',
                           (key, json.dumps(value, ensure_ascii=False)))

    def get_meta(self, key):
        with self.lock:
            return self._meta(self.connection, key)

    def set_meta(self, key, value):
        if key in {'context', 'salt'}:
            raise ValueError('LEDGER_RESERVED_META')
        with self._transaction() as connection:
            self._put_meta(connection, key, value)

    @staticmethod
    def _patient(connection, pid):
        if not isinstance(pid, str) or not pid:
            raise ValueError('PATIENT_KEY_REQUIRED')
        connection.execute('INSERT OR IGNORE INTO patients(pid) VALUES (?)', (pid,))
        return connection.execute('SELECT * FROM patients WHERE pid=?', (pid,)).fetchone()

    @staticmethod
    def _job(row):
        if row is None:
            return None
        result = dict(row)
        result['signature'] = json.loads(result['signature'])
        return result

    @staticmethod
    def _cancel_queued(connection, pid, reason):
        paths = [r[0] for r in connection.execute("SELECT snapshot FROM jobs WHERE pid=? AND status='QUEUED'", (pid,))]
        connection.execute("UPDATE jobs SET status='SUPERSEDED',reason=?,updated=? WHERE pid=? AND status='QUEUED'",
                           (reason, time.time(), pid))
        return paths

    @staticmethod
    def _discard(connection, paths):
        # 不删除仍绑定运行、完成或待核查证据的目录。
        return [path for path in dict.fromkeys(paths) if path and not connection.execute(
            "SELECT 1 FROM jobs WHERE snapshot=? AND status<>'SUPERSEDED' LIMIT 1", (path,)).fetchone()]

    def known_pids(self):
        with self.lock:
            return [row[0] for row in self.connection.execute('SELECT pid FROM patients ORDER BY pid')]

    def matches_effective(self, pid, digest):
        """只读快速判定；是否撤销旧排队仍由后续observe原子决定。"""
        with self.lock:
            patient = self.connection.execute('SELECT * FROM patients WHERE pid=?', (pid,)).fetchone()
            if patient is None:
                return False
            if patient['blocked_reason'] is not None:
                return True
            running = self.connection.execute("SELECT digest FROM jobs WHERE pid=? AND status='RUNNING'", (pid,)).fetchone()
            if digest == (running['digest'] if running else patient['done_hash']):
                return True
            return self.connection.execute("SELECT 1 FROM jobs WHERE pid=? AND digest=? AND status='QUEUED'", (pid, digest)).fetchone() is not None

    def observe(self, pid, signature, snapshot):
        if (not isinstance(signature, dict) or not isinstance(signature.get('digest'), str)
                or not signature['digest'] or not isinstance(signature.get('files'), dict)
                or not isinstance(signature.get('rows'), dict) or not snapshot):
            raise ValueError('INPUT_SIGNATURE_INVALID')
        digest = signature['digest']
        encoded = json.dumps(signature, ensure_ascii=False, sort_keys=True)
        snapshot = str(snapshot)
        with self._transaction() as connection:
            patient = self._patient(connection, pid)
            running = connection.execute("SELECT digest FROM jobs WHERE pid=? AND status='RUNNING'", (pid,)).fetchone()
            reference = running['digest'] if running else patient['done_hash']
            if patient['blocked_reason'] is not None or digest == reference:
                reason = 'PATIENT_BLOCKED' if patient['blocked_reason'] is not None else 'INPUT_UNCHANGED'
                paths = self._cancel_queued(connection, pid, reason) + [snapshot]
                return False, self._discard(connection, paths)
            queued = connection.execute("SELECT * FROM jobs WHERE pid=? AND status='QUEUED'", (pid,)).fetchone()
            if queued and queued['digest'] == digest:
                return False, self._discard(connection, [snapshot])
            now = time.time()
            if queued:
                connection.execute("UPDATE jobs SET pid=?,digest=?,signature=?,snapshot=?,reason='',retry_at=0,updated=? WHERE id=?",
                                   (pid, digest, encoded, snapshot, now, queued['id']))
                paths = self._discard(connection, [queued['snapshot']])
            else:
                connection.execute("INSERT INTO jobs(pid,digest,signature,snapshot,status,created,updated) VALUES (?,?,?,?,'QUEUED',?,?)",
                                   (pid, digest, encoded, snapshot, now, now))
                paths = []
            connection.execute("UPDATE patients SET last_reason='' WHERE pid=?", (pid,))
            return True, paths

    def mark_ineligible(self, pid, reason):
        with self._transaction() as connection:
            self._patient(connection, pid)
            paths = self._cancel_queued(connection, pid, reason)
            connection.execute('UPDATE patients SET last_reason=? WHERE pid=?', (reason, pid))
            return self._discard(connection, paths)

    def next_job(self):
        with self.lock:
            row = self.connection.execute("""SELECT j.* FROM jobs j JOIN patients p ON p.pid=j.pid
                WHERE j.status='QUEUED' AND j.retry_at<=? AND p.blocked_reason IS NULL
                AND NOT EXISTS (SELECT 1 FROM jobs r WHERE r.pid=j.pid AND r.status='RUNNING')
                ORDER BY j.created,j.id LIMIT 1""", (time.time(),)).fetchone()
            return self._job(row)

    def claim(self, job_id, folder, tag):
        with self._transaction() as connection:
            now = time.time()
            cursor = connection.execute("""UPDATE jobs SET status='RUNNING',folder=?,tag=?,updated=?,started=?
                WHERE id=? AND status='QUEUED' AND retry_at<=?
                AND EXISTS (SELECT 1 FROM patients p WHERE p.pid=jobs.pid AND p.blocked_reason IS NULL)
                AND NOT EXISTS (SELECT 1 FROM jobs r WHERE r.pid=jobs.pid AND r.status='RUNNING')""",
                (str(folder), str(tag), now, now, job_id, now))
            if cursor.rowcount != 1:
                return None
            return self._job(connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    @staticmethod
    def _done(connection, pid, digest, finished):
        # 真实串行完成遵循因果顺序；历史导入自行比较时间，不能让回拨阻止新成功。
        connection.execute('UPDATE patients SET done_hash=?,done_time=? WHERE pid=?',
                           (digest, finished, pid))

    def finish(self, job_id, status, reason=''):
        if status not in {'PASSED', 'ROUTER_SKIPPED', 'NEEDS_REVIEW'}:
            raise ValueError('FINISH_STATUS_INVALID')
        with self._transaction() as connection:
            job = connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if job is None:
                raise RuntimeError('JOB_NOT_FOUND')
            if job['status'] == status:
                return
            if job['status'] != 'RUNNING':
                raise RuntimeError('JOB_NOT_RUNNING')
            now = time.time()
            connection.execute('UPDATE jobs SET status=?,reason=?,updated=? WHERE id=?', (status, reason, now, job_id))
            if status == 'NEEDS_REVIEW':
                connection.execute('UPDATE patients SET blocked_reason=? WHERE pid=?', (reason or 'NEEDS_REVIEW', job['pid']))
            else:
                self._done(connection, job['pid'], job['digest'], now)

    def retry_before_start(self, job_id, reason, delay=300):
        if not math.isfinite(delay) or delay < 0:
            raise ValueError('RETRY_DELAY_INVALID')
        with self._transaction() as connection:
            job = connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if job is None or job['status'] != 'RUNNING':
                raise RuntimeError('JOB_NOT_RUNNING')
            newer = connection.execute("SELECT digest FROM jobs WHERE pid=? AND status='QUEUED'", (job['pid'],)).fetchone()
            patient = connection.execute('SELECT blocked_reason,done_hash FROM patients WHERE pid=?', (job['pid'],)).fetchone()
            blocked = patient['blocked_reason']
            status = 'SUPERSEDED' if newer or blocked is not None else 'QUEUED'
            now = time.time()
            connection.execute("UPDATE jobs SET status=?,reason=?,retry_at=?,updated=?,started=NULL,folder='',tag='' WHERE id=?",
                               (status, reason, now + delay, now, job_id))
            paths = [job['snapshot']] if status == 'SUPERSEDED' else []
            # B未进入审计，期间数据回到已完成的A：撤销A排队，避免重复A。
            if newer and newer['digest'] == patient['done_hash'] and blocked is None:
                paths.extend(self._cancel_queued(connection, job['pid'], 'INPUT_UNCHANGED'))
            return self._discard(connection, paths)

    def running_jobs(self):
        with self.lock:
            return [self._job(row) for row in self.connection.execute("SELECT * FROM jobs WHERE status='RUNNING' ORDER BY id")]

    def import_success(self, pid, digest, finished):
        if (not isinstance(digest, str) or not digest or not math.isfinite(finished)
                or finished > time.time() + 300):
            raise ValueError('IMPORTED_RECEIPT_INVALID')
        with self._transaction() as connection:
            patient = self._patient(connection, pid)
            # 旧历史只用于建立基线；已有监测器实际成功后，不再用不可靠的墙钟覆盖因果顺序。
            if connection.execute("SELECT 1 FROM jobs WHERE pid=? AND status IN ('PASSED','ROUTER_SKIPPED') LIMIT 1",(pid,)).fetchone():
                return []
            if patient['done_time'] is not None and patient['done_time'] >= finished:
                return []
            self._done(connection, pid, digest, finished)
            running = connection.execute("SELECT 1 FROM jobs WHERE pid=? AND status='RUNNING'", (pid,)).fetchone()
            paths = []
            if not running:
                paths = [row[0] for row in connection.execute("SELECT snapshot FROM jobs WHERE pid=? AND status='QUEUED' AND digest=?", (pid, digest))]
                connection.execute("UPDATE jobs SET status='SUPERSEDED',reason='IMPORTED_DONE',updated=? WHERE pid=? AND status='QUEUED' AND digest=?",
                                   (time.time(), pid, digest))
            return self._discard(connection, paths)

    def block_patient(self, pid, reason):
        with self._transaction() as connection:
            self._patient(connection, pid)
            connection.execute('UPDATE patients SET blocked_reason=? WHERE pid=?', (reason or 'NEEDS_REVIEW', pid))

    def stats(self):
        with self.lock:
            return _stats(self.connection)

    def close(self):
        with self.lock:
            self.connection.close()
