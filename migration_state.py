"""Durable migration checkpoints and a rate controller shared by local runs."""

import json
import hashlib
import fcntl
import os
import random
import sqlite3
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


class DailyQuotaExceeded(RuntimeError):
    """A daily API quota cannot be fixed by retrying the next message."""


class RateLimitExhausted(RuntimeError):
    """Stop the run with its checkpoint intact after repeated throttling."""


class GroupRunLock:
    """Prevent two local processes inserting into the same group at once."""

    def __init__(self, state_db, group):
        self.group = group.lower().strip()
        digest = hashlib.sha256(self.group.encode()).hexdigest()[:20]
        self.path = Path(state_db).parent / 'group_locks' / f'{digest}.lock'
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+')
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.handle.close()
            raise RuntimeError(
                f'Another local process is already migrating group {self.group}'
            ) from error
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(f'pid={os.getpid()} group={self.group}\n')
        self.handle.flush()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def _connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


class MigrationState:
    """Indexed message IDs, failures and progress for one source/target pair."""

    def __init__(self, path, source, target):
        self.path = Path(path)
        self.source = source.lower().strip()
        self.target = target.lower().strip()
        self.db = _connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS migrations (
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                oldest_email_date TEXT,
                oldest_email_id TEXT,
                legacy_imported INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (source, target)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS processed (
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                message_id TEXT NOT NULL,
                PRIMARY KEY (source, target, message_id)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS failed (
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                message_id TEXT NOT NULL,
                error TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                PRIMARY KEY (source, target, message_id)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS legacy_imports (
                path TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                target TEXT NOT NULL
            );
        """)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(
                "INSERT OR IGNORE INTO migrations (source, target) VALUES (?, ?)",
                (self.source, self.target),
            )
        self.processed_count = self.db.execute(
            "SELECT COUNT(*) FROM processed WHERE source = ? AND target = ?",
            (self.source, self.target),
        ).fetchone()[0]
        self.failed_count = self.db.execute(
            "SELECT COUNT(*) FROM failed WHERE source = ? AND target = ?",
            (self.source, self.target),
        ).fetchone()[0]
        self._saved_oldest = None

    def import_legacy(self, progress_file):
        """Import the old JSON exactly once without changing or deleting it."""
        imported = self.db.execute(
            "SELECT legacy_imported FROM migrations WHERE source = ? AND target = ?",
            (self.source, self.target),
        ).fetchone()[0]
        if imported:
            return

        progress_file = Path(progress_file) if progress_file is not None else None
        data = {}
        if progress_file is not None and progress_file.exists():
            # A damaged checkpoint must stop the run rather than silently start over.
            with progress_file.open() as handle:
                data = json.load(handle)
            if not isinstance(data, dict) or not isinstance(data.get('processed_emails', []), list):
                raise ValueError(f"Invalid legacy progress file: {progress_file}")

        legacy_path = str(progress_file.resolve()) if progress_file and progress_file.exists() else None

        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if legacy_path:
                owner = self.db.execute(
                    "SELECT source, target FROM legacy_imports WHERE path = ?",
                    (legacy_path,),
                ).fetchone()
                if owner and owner != (self.source, self.target):
                    raise ValueError(
                        f"Legacy checkpoint {progress_file} was already imported for "
                        f"{owner[0]} -> {owner[1]}; set import_legacy_progress: false "
                        "for a new source/target migration"
                    )
                self.db.execute(
                    "INSERT OR IGNORE INTO legacy_imports VALUES (?, ?, ?)",
                    (legacy_path, self.source, self.target),
                )
            self.db.executemany(
                "INSERT OR IGNORE INTO processed VALUES (?, ?, ?)",
                ((self.source, self.target, message_id)
                 for message_id in data.get('processed_emails', []) if isinstance(message_id, str)),
            )
            for failure in data.get('failed_emails', []):
                if isinstance(failure, dict) and failure.get('id'):
                    self.db.execute(
                        "INSERT OR REPLACE INTO failed VALUES (?, ?, ?, ?, ?)",
                        (self.source, self.target, failure['id'],
                         str(failure.get('error', '')), str(failure.get('timestamp', ''))),
                    )
            self.db.execute(
                "DELETE FROM failed WHERE source = ? AND target = ? AND message_id IN "
                "(SELECT message_id FROM processed WHERE source = ? AND target = ?)",
                (self.source, self.target, self.source, self.target),
            )
            self.db.execute(
                "UPDATE migrations SET oldest_email_date = COALESCE(oldest_email_date, ?), "
                "oldest_email_id = COALESCE(oldest_email_id, ?), legacy_imported = 1 "
                "WHERE source = ? AND target = ?",
                (data.get('oldest_email_date'), data.get('oldest_email_id'),
                 self.source, self.target),
            )
        self.processed_count = self.db.execute(
            "SELECT COUNT(*) FROM processed WHERE source = ? AND target = ?",
            (self.source, self.target),
        ).fetchone()[0]
        self.failed_count = self.db.execute(
            "SELECT COUNT(*) FROM failed WHERE source = ? AND target = ?",
            (self.source, self.target),
        ).fetchone()[0]

    def oldest(self):
        row = self.db.execute(
            "SELECT oldest_email_date, oldest_email_id FROM migrations "
            "WHERE source = ? AND target = ?",
            (self.source, self.target),
        ).fetchone()
        self._saved_oldest = row
        return row

    def save_oldest(self, date, message_id):
        value = (date.isoformat() if date else None, message_id)
        if value == self._saved_oldest:
            return
        with self.db:
            self.db.execute(
                "UPDATE migrations SET oldest_email_date = ?, oldest_email_id = ? "
                "WHERE source = ? AND target = ?",
                (*value, self.source, self.target),
            )
        self._saved_oldest = value

    def contains(self, message_id):
        return self.db.execute(
            "SELECT 1 FROM processed WHERE source = ? AND target = ? AND message_id = ?",
            (self.source, self.target, message_id),
        ).fetchone() is not None

    def mark_processed(self, message_id):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO processed VALUES (?, ?, ?)",
                (self.source, self.target, message_id),
            )
            cleared = self.db.execute(
                "DELETE FROM failed WHERE source = ? AND target = ? AND message_id = ?",
                (self.source, self.target, message_id),
            )
        self.processed_count += cursor.rowcount
        self.failed_count -= cleared.rowcount

    def iter_processed(self):
        cursor = self.db.execute(
            "SELECT message_id FROM processed WHERE source = ? AND target = ?",
            (self.source, self.target),
        )
        for (message_id,) in cursor:
            yield message_id

    def record_failure(self, failure):
        message_id = failure['id']
        existed = self.db.execute(
            "SELECT 1 FROM failed WHERE source = ? AND target = ? AND message_id = ?",
            (self.source, self.target, message_id),
        ).fetchone() is not None
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO failed VALUES (?, ?, ?, ?, ?)",
                (self.source, self.target, message_id,
                 str(failure.get('error', '')), str(failure.get('timestamp', ''))),
            )
        if not existed:
            self.failed_count += 1

    def clear_failure(self, message_id):
        with self.db:
            cursor = self.db.execute(
                "DELETE FROM failed WHERE source = ? AND target = ? AND message_id = ?",
                (self.source, self.target, message_id),
            )
        self.failed_count -= cursor.rowcount

    def iter_failures(self):
        cursor = self.db.execute(
            "SELECT message_id, error, timestamp FROM failed "
            "WHERE source = ? AND target = ?",
            (self.source, self.target),
        )
        for message_id, error, timestamp in cursor:
            yield {'id': message_id, 'error': error, 'timestamp': timestamp}


class ProcessedMessages:
    def __init__(self, state):
        self.state = state

    def __contains__(self, message_id):
        return self.state.contains(message_id)

    def __len__(self):
        return self.state.processed_count

    def __iter__(self):
        return self.state.iter_processed()

    def add(self, message_id):
        self.state.mark_processed(message_id)


class FailedMessages:
    def __init__(self, state):
        self.state = state

    def __len__(self):
        return self.state.failed_count

    def __iter__(self):
        return self.state.iter_failures()

    def append(self, failure):
        self.state.record_failure(failure)

    def remove_id(self, message_id):
        self.state.clear_failure(message_id)


def error_reason(error):
    """Return Google's structured reason rather than guessing from HTTP 403."""
    try:
        payload = json.loads(error.content)
        details = payload.get('error', {})
        reasons = [entry.get('reason', '') for entry in details.get('errors', [])]
        reasons += [entry.get('reason', '') for entry in details.get('details', [])]
        return next((reason for reason in reasons if reason), '')
    except (ValueError, TypeError, AttributeError):
        return ''


def is_rate_error(error):
    status = error.resp.status
    if status in (429, 503):
        return True
    reason = error_reason(error).lower().replace('_', '')
    return status == 403 and ('ratelimit' in reason or 'quotaexceeded' in reason)


def is_daily_quota(error):
    reason = error_reason(error).lower().replace('_', '')
    return 'dailylimit' in reason or 'dailyquota' in reason


def retry_after_seconds(error):
    header = error.resp.get('retry-after') or error.resp.get('Retry-After')
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except (TypeError, ValueError):
        try:
            return max(0.0, (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def backoff_seconds(attempt, error=None):
    delay = min(5 * (2 ** attempt), 300) + random.uniform(0, 1)
    server_delay = retry_after_seconds(error) if error is not None else None
    return max(delay, server_delay or 0)


class SharedRateLimiter:
    """Adaptive, cross-process QPS limit for runs sharing one local DB."""

    def __init__(self, path, scope='groupsmigration', initial_qps=5.0, max_qps=9.0):
        if initial_qps <= 0 or max_qps <= 0 or initial_qps > max_qps:
            raise ValueError('Rate limits must satisfy 0 < initial_qps <= max_qps')
        self.db = _connect(path)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS rate_control (
                scope TEXT PRIMARY KEY,
                qps REAL NOT NULL,
                next_at REAL NOT NULL,
                cooldown_until REAL NOT NULL,
                success_count INTEGER NOT NULL,
                last_increase REAL NOT NULL,
                throttle_count INTEGER NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        self.scope = scope
        self.initial_qps = initial_qps
        self.max_qps = max_qps
        self.wait_seconds = 0.0
        self.permits = 0

    def _row(self):
        return self.db.execute(
            "SELECT qps, next_at, cooldown_until, success_count, last_increase, "
            "throttle_count, updated_at FROM rate_control WHERE scope = ?",
            (self.scope,),
        ).fetchone()

    def acquire(self):
        """Wait outside the DB lock; recheck after every short sleep."""
        while True:
            now = time.time()
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                row = self._row()
                if row is None:
                    self.db.execute(
                        "INSERT INTO rate_control VALUES (?, ?, 0, 0, 0, ?, 0, ?)",
                        (self.scope, self.initial_qps, now, now),
                    )
                    row = self._row()
                qps, next_at, cooldown_until, _, _, _, updated_at = row
                qps = min(qps, self.max_qps)
                if now - updated_at > 3600 and now >= cooldown_until:
                    qps, next_at = self.initial_qps, 0
                    self.db.execute(
                        "UPDATE rate_control SET qps = ?, next_at = 0, success_count = 0, "
                        "last_increase = ?, updated_at = ? WHERE scope = ?",
                        (qps, now, now, self.scope),
                    )
                ready_at = max(next_at, cooldown_until)
                if now >= ready_at:
                    self.db.execute(
                        "UPDATE rate_control SET next_at = ?, updated_at = ? WHERE scope = ?",
                        (now + 1.0 / qps, now, self.scope),
                    )
                    self.permits += 1
                    return
            sleep_for = min(max(ready_at - time.time(), 0.01), 1.0)
            self.wait_seconds += sleep_for
            time.sleep(sleep_for)

    def record_success(self):
        now = time.time()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            qps, _, _, successes, last_increase, _, _ = self._row()
            successes += 1
            if successes >= 50 and now - last_increase >= 20:
                qps = min(self.max_qps, qps + 0.5)
                successes = 0
                last_increase = now
            self.db.execute(
                "UPDATE rate_control SET qps = ?, success_count = ?, last_increase = ?, "
                "updated_at = ? WHERE scope = ?",
                (qps, successes, last_increase, now, self.scope),
            )

    def record_throttle(self, delay):
        now = time.time()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            qps, next_at, cooldown_until, _, _, _, _ = self._row()
            qps = max(0.2, qps / 2)
            cooldown_until = max(cooldown_until, now + delay)
            self.db.execute(
                "UPDATE rate_control SET qps = ?, next_at = ?, cooldown_until = ?, "
                "success_count = 0, last_increase = ?, throttle_count = throttle_count + 1, "
                "updated_at = ? WHERE scope = ?",
                (qps, max(next_at, cooldown_until), cooldown_until, now, now, self.scope),
            )
        return qps

    def status(self):
        row = self._row()
        if row is None:
            return {'qps': self.initial_qps, 'throttles': 0, 'wait_seconds': self.wait_seconds}
        return {'qps': row[0], 'throttles': row[5], 'wait_seconds': self.wait_seconds}
