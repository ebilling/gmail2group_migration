"""Offline checks for checkpoint migration and rate-limit behavior."""

import base64
import errno
import io
import json
import os
import re
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from googleapiclient.errors import HttpError
from httplib2 import Response

from migration_state import (
    DailyQuotaExceeded, GroupRunLock, MigrationState, SharedRateLimiter, error_reason,
    is_daily_quota, is_rate_error, retry_after_seconds,
)


class FakeHttpError(HttpError):
    def __init__(self, status, reason='', retry_after=None):
        response = Response({'status': str(status)})
        if retry_after is not None:
            response['retry-after'] = retry_after
        content = json.dumps({
            'error': {'errors': [{'reason': reason}]}
        }).encode()
        super().__init__(response, content)


class StateTests(unittest.TestCase):
    def test_import_legacy_once_and_keep_source_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / 'account_migration_progress.json'
            legacy.write_text(json.dumps({
                'processed_emails': ['m1', 'm2', 'm1'],
                'failed_emails': [{'id': 'm3', 'error': 'old', 'timestamp': 'now'}],
                'oldest_email_date': '2020-01-01T00:00:00',
                'oldest_email_id': 'm1',
            }))
            database = root / 'state.sqlite3'
            state = MigrationState(database, 'Account@Example.com', 'Group@example.com')
            state.import_legacy(legacy)
            self.assertEqual(state.processed_count, 2)
            self.assertTrue(state.contains('m1'))
            self.assertEqual(state.failed_count, 1)
            self.assertEqual(state.oldest()[1], 'm1')
            state.mark_processed('m3')
            state.clear_failure('m3')
            self.assertEqual(state.processed_count, 3)
            self.assertEqual(state.failed_count, 0)
            self.assertTrue(legacy.exists())

            # Reopen and reimport: the old JSON must not undo the new progress.
            resumed = MigrationState(database, 'account@example.com', 'group@example.com')
            resumed.import_legacy(legacy)
            self.assertEqual(resumed.processed_count, 3)
            self.assertEqual(resumed.failed_count, 0)
            other_group = MigrationState(database, 'account@example.com', 'other@example.com')
            self.assertFalse(other_group.contains('m1'))
            with self.assertRaisesRegex(ValueError, 'already imported'):
                other_group.import_legacy(legacy)
            other_group.import_legacy(None)
            self.assertEqual(other_group.processed_count, 0)

    def test_damaged_legacy_file_stops_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / 'bad.json'
            legacy.write_text('{incomplete')
            state = MigrationState(root / 'state.sqlite3', 'a@example.com', 'g@example.com')
            with self.assertRaises(json.JSONDecodeError):
                state.import_legacy(legacy)
            self.assertEqual(state.processed_count, 0)

    def test_two_limiters_share_rate_and_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'state.sqlite3'
            first = SharedRateLimiter(database, initial_qps=100, max_qps=100)
            second = SharedRateLimiter(database, initial_qps=100, max_qps=100)
            first.acquire()
            started = time.monotonic()
            second.acquire()
            self.assertGreaterEqual(time.monotonic() - started, 0.005)
            self.assertEqual(first.record_throttle(0.05), 50)
            started = time.monotonic()
            second.acquire()
            self.assertGreaterEqual(time.monotonic() - started, 0.04)
            self.assertEqual(second.status()['throttles'], 1)
            self.assertEqual(second.status()['qps'], 50)

    def test_structured_quota_reasons_and_retry_after(self):
        self.assertTrue(is_rate_error(FakeHttpError(403, 'userRateLimitExceeded')))
        self.assertTrue(is_rate_error(FakeHttpError(503)))
        self.assertFalse(is_rate_error(FakeHttpError(403, 'forbidden')))
        self.assertTrue(is_daily_quota(FakeHttpError(403, 'dailyLimitExceeded')))
        self.assertEqual(error_reason(FakeHttpError(403, 'rateLimitExceeded')),
                         'rateLimitExceeded')
        self.assertEqual(retry_after_seconds(FakeHttpError(429, retry_after='17')), 17)

    def test_same_group_cannot_run_twice_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'state.sqlite3'
            with GroupRunLock(database, 'group@example.com'):
                with self.assertRaises(RuntimeError):
                    with GroupRunLock(database, 'GROUP@example.com'):
                        pass
                with GroupRunLock(database, 'other@example.com'):
                    pass


class MigratorTests(unittest.TestCase):
    def test_verified_middle_is_skipped_and_force_all_rechecks_it(self):
        from migrate import DualAuthMigrator

        day = 86400 * 1000
        base = 1_700_000_000_000
        messages = [(f'm{n}', base + n * day) for n in (70, 60, 50, 40, 30)]

        class Request:
            def __init__(self, response):
                self.response = response

            def execute(self):
                return self.response

        class Service:
            def __init__(self):
                self.queries = []
                self.raw_gets = []

            def users(self):
                return self

            def messages(self):
                return self

            def list(self, **kwargs):
                query = kwargs['q']
                self.queries.append(query)
                after = re.search(r'after:(\d+)', query)
                before = re.search(r'before:(\d+)', query)
                matched = [message_id for message_id, timestamp in sorted(
                    messages, key=lambda item: item[1], reverse=True)
                           if (not after or timestamp // 1000 > int(after.group(1)))
                           and (not before or timestamp // 1000 < int(before.group(1)))]
                offset = int(kwargs.get('pageToken', 0))
                page = {'messages': [{'id': message_id} for message_id in matched[offset:offset + 2]],
                        'resultSizeEstimate': len(matched)}
                if offset + 2 < len(matched):
                    page['nextPageToken'] = str(offset + 2)
                return Request(page)

            def get(self, **kwargs):
                timestamp = dict(messages)[kwargs['id']]
                if kwargs['format'] == 'raw':
                    self.raw_gets.append(kwargs['id'])
                    return Request({'raw': 'cmF3', 'internalDate': str(timestamp)})
                return Request({'id': kwargs['id'], 'internalDate': str(timestamp)})

        with tempfile.TemporaryDirectory() as directory:
            config = {
                'gmail_account': 'a@example.com', 'group_email': 'g@example.com',
                'gmail_query': 'in:all', 'gmail_prefetch': 0,
                'state_db': str(Path(directory) / 'state.sqlite3'),
            }
            first = DualAuthMigrator(config)
            first.progress_file = Path(directory) / 'missing.json'
            first.load_progress()
            first.gmail_service = Service()
            for message_id in ('m70', 'm60', 'm50'):
                first.state.mark_processed(message_id)
            iterator = first.iter_emails()
            self.assertEqual(next(iterator)['id'], 'm40')
            first.state.mark_processed('m40')
            iterator.close()
            self.assertEqual(first.state.verified_range('in:all'),
                             (dict(messages)['m70'], dict(messages)['m50']))

            resumed = DualAuthMigrator(config)
            resumed.progress_file = first.progress_file
            resumed.load_progress()
            resumed.gmail_service = Service()
            for message in resumed.iter_emails():
                resumed.state.mark_processed(message['id'])
            self.assertEqual(resumed.gmail_service.raw_gets, ['m30'])
            self.assertEqual(resumed.state.verified_range('in:all'),
                             (dict(messages)['m70'], dict(messages)['m30']))
            self.assertEqual(resumed.gmail_service.queries[0],
                             f"(in:all) after:{dict(messages)['m70'] // 1000 - 86400}")
            self.assertIn(f"(in:all) before:{dict(messages)['m50'] // 1000 + 86400}",
                          resumed.gmail_service.queries)
            self.assertTrue(any('after:' in query for query in resumed.gmail_service.queries))
            self.assertTrue(any('before:' in query for query in resumed.gmail_service.queries))
            self.assertFalse(any(query == 'in:all' for query in resumed.gmail_service.queries),
                             resumed.gmail_service.queries)

            audit = DualAuthMigrator({**config, 'force_all': True})
            audit.progress_file = first.progress_file
            audit.load_progress()
            audit.gmail_service = Service()
            messages.append(('m45', base + 45 * day))
            audited = []
            for message in audit.iter_emails():
                audited.append(message['id'])
                audit.state.mark_processed(message['id'])
            self.assertEqual(audited, ['m45'])
            self.assertEqual(audit.gmail_service.queries[0], 'in:all')
            self.assertEqual(audit.state.verified_range('in:all'),
                             (dict(messages)['m70'], dict(messages)['m30']))

            changed = DualAuthMigrator({**config, 'gmail_query': 'in:sent'})
            changed.progress_file = first.progress_file
            changed.load_progress()
            changed.gmail_service = Service()
            self.assertEqual(list(changed.iter_emails()), [])
            self.assertEqual(changed.gmail_service.queries[0], 'in:sent')

    def test_failed_recent_email_does_not_expand_verified_range(self):
        from migrate import DualAuthMigrator

        class Request:
            def __init__(self, response=None, error=None):
                self.response, self.error = response, error

            def execute(self):
                if self.error:
                    raise self.error
                return self.response

        class Service:
            def users(self):
                return self

            def messages(self):
                return self

            def list(self, **kwargs):
                if 'after:' in kwargs['q']:
                    return Request({'messages': [{'id': 'new'}, {'id': 'old'}]})
                return Request({'messages': []})

            def get(self, **kwargs):
                return Request(error=FakeHttpError(404, 'notFound'))

        with tempfile.TemporaryDirectory() as directory:
            migrator = DualAuthMigrator({
                'gmail_account': 'a@example.com', 'group_email': 'g@example.com',
                'gmail_prefetch': 0, 'state_db': str(Path(directory) / 'state.sqlite3'),
            })
            migrator.progress_file = Path(directory) / 'missing.json'
            migrator.load_progress()
            migrator.state.extend_verified_range('in:all', 2_000_000_000_000, 1_900_000_000_000)
            migrator.gmail_service = Service()
            with self.assertLogs('migrate', level='ERROR'):
                self.assertEqual(list(migrator.iter_emails()), [])
            self.assertEqual(migrator.state.verified_range('in:all'),
                             (2_000_000_000_000, 1_900_000_000_000))
            self.assertEqual(len(migrator.failed_emails), 2)

    def test_date_progress_uses_time_of_day_and_timezone(self):
        from migrate import DualAuthMigrator

        migrator = DualAuthMigrator({'gmail_account': 'a@example.com'})
        now = datetime.now()
        start = now - timedelta(days=10)
        oldest = now - timedelta(days=5, hours=12)
        migrator.oldest_email_date = oldest
        self.assertAlmostEqual(migrator.calculate_date_based_progress(start), 55.0, places=3)

        migrator.oldest_email_date = oldest - timedelta(hours=1)
        later_progress = migrator.calculate_date_based_progress(start)
        self.assertGreater(later_progress, 55.4)

        migrator.oldest_email_date = oldest.astimezone().astimezone(
            timezone(timedelta(hours=3))
        )
        self.assertAlmostEqual(migrator.calculate_date_based_progress(start), 55.0, places=3)

    def test_terminal_progress_reuses_one_line(self):
        from migrate import DualAuthMigrator

        class Terminal(io.StringIO):
            def isatty(self):
                return True

        migrator = DualAuthMigrator({'gmail_account': 'a@example.com'})
        terminal = Terminal()
        with redirect_stdout(terminal):
            with patch('migrate.shutil.get_terminal_size', return_value=os.terminal_size((80, 24))):
                migrator.show_progress('Progress: first update')
                migrator.show_progress('Progress: 2')
                migrator.finish_progress()
        output = terminal.getvalue()
        self.assertEqual(output.count('\r'), 2)
        self.assertEqual(output.count('\n'), 1)
        self.assertIn('\rProgress: 2', output)
        self.assertTrue(output.endswith('\n'))

    def test_eta_uses_observed_mail_density_or_explicit_cap(self):
        from migrate import estimate_remaining_seconds

        start = datetime(2020, 1, 1)
        current_oldest = datetime(2020, 4, 1)
        now = datetime(2020, 4, 10)
        self.assertEqual(
            estimate_remaining_seconds(start, current_oldest, now, 90, 20, 900),
            90 * 900 * 91 / (20 * 9),
        )
        self.assertEqual(
            estimate_remaining_seconds(start, current_oldest, now, 90, 20, 900, 100),
            360,
        )
        self.assertIsNone(
            estimate_remaining_seconds(start, current_oldest, now, 90, 20, 20)
        )

    def test_progress_metrics_only_appear_with_debug(self):
        from migrate import DualAuthMigrator

        def progress_line(debug, use_date=True, interactive=False, initial_processed=0):
            with tempfile.TemporaryDirectory() as directory:
                migrator = DualAuthMigrator({
                    'gmail_account': 'a@example.com',
                    'group_email': 'g@example.com',
                    'state_db': str(Path(directory) / 'state.sqlite3'),
                    'debug': debug,
                    'max_emails': None if use_date else 10,
                })
                migrator.progress_file = Path(directory) / 'missing.json'
                if initial_processed:
                    migrator.load_progress()
                    with migrator.state.db:
                        migrator.state.db.execute('BEGIN IMMEDIATE')
                        migrator.state.db.executemany(
                            'INSERT INTO processed VALUES (?, ?, ?)',
                            (('a@example.com', 'g@example.com', f'old{i}')
                             for i in range(initial_processed)),
                        )
                    migrator.state.processed_count += initial_processed
                migrator.verify_group_access = lambda group: True
                migrator.get_start_date_for_progress = (
                    lambda: datetime.now() - timedelta(days=365) if use_date else None
                )
                migrator.iter_emails = lambda: ({'id': f'm{i}'} for i in range(10))

                def migrate_message(message):
                    migrator.processed_emails.add(message['id'])
                    migrator.oldest_email_date = datetime.now() - timedelta(days=2)
                    return True

                migrator.migrate_email_to_group = migrate_message
                class Terminal(io.StringIO):
                    def isatty(self):
                        return True

                output = Terminal() if interactive else io.StringIO()
                with self.assertLogs('migrate', level='INFO') as captured:
                    with redirect_stdout(output):
                        migrator.migrate_all_emails()
                lines = [line for line in captured.output if 'Progress update:' in line]
                self.assertEqual(len(lines), 1)
                migrator.state.db.close()
                migrator.rate_limiter.db.close()
                return lines[0], output.getvalue()

        normal, normal_output = progress_line(False)
        self.assertIn('ETA=calculating', normal)
        self.assertRegex(normal, r'\(\d+\.\d{3}% by date\)')
        self.assertRegex(normal_output, r'\d+\.\d{3}% by date')
        self.assertRegex(normal, r'oldest=\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')
        self.assertRegex(normal_output, r'Oldest: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')
        self.assertNotIn('rate=', normal)
        self.assertNotIn('upload=', normal)
        interactive_log, interactive_output = progress_line(False, interactive=True)
        self.assertIn('10 processed', interactive_log)
        self.assertEqual(interactive_output.count('\r'), 10)
        self.assertIn('1 processed', interactive_output)
        self.assertIn('9 processed', interactive_output)
        grouped_log, grouped_output = progress_line(False, initial_processed=1234)
        self.assertIn('success=1,244', grouped_log)
        self.assertIn('✅ 1,244', grouped_output)
        debug, _ = progress_line(True)
        self.assertIn('ETA=calculating', debug)
        self.assertIn('rate=', debug)
        self.assertIn('upload=', debug)
        count_progress, count_output = progress_line(False, use_date=False)
        self.assertIn('(100.000%)', count_progress)
        self.assertIn('100.000%', count_output)

    def test_gmail_prefetch_overlaps_processing_and_respects_limit(self):
        from migrate import DualAuthMigrator

        second_started = threading.Event()
        fetched = []

        class Request:
            def __init__(self, response):
                self.response = response

            def execute(self):
                return self.response

        class Messages:
            def list(self, **kwargs):
                return Request({'messages': [{'id': 'm1'}, {'id': 'm2'}, {'id': 'm3'}]})

            def get(self, **kwargs):
                message_id = kwargs['id']
                fetched.append(message_id)
                if message_id == 'm2':
                    second_started.set()
                return Request({'raw': 'cmF3'})

        class Service:
            def users(self):
                return self

            def messages(self):
                return Messages()

            def close(self):
                pass

        migrator = DualAuthMigrator({
            'gmail_account': 'a@example.com', 'gmail_prefetch': 2,
        })
        migrator.gmail_service = Service()
        with patch.object(migrator, '_prefetch_gmail_service', return_value=Service()):
            emails = migrator.iter_emails()
            self.assertEqual(next(emails)['id'], 'm1')
            self.assertTrue(second_started.wait(1), 'next Gmail read did not start during processing')
            self.assertEqual([message['id'] for message in emails], ['m2', 'm3'])
        self.assertEqual(fetched, ['m1', 'm2', 'm3'])

        fetched.clear()
        migrator.config['max_emails'] = 1
        migrator.processed_emails = {'m1'}
        with patch.object(migrator, '_prefetch_gmail_service', return_value=Service()):
            self.assertEqual([message['id'] for message in migrator.iter_emails()], ['m2'])
        self.assertEqual(fetched, ['m2'])

    def test_gmail_throttle_retries_same_request(self):
        from migrate import DualAuthMigrator

        migrator = DualAuthMigrator({'gmail_account': 'a@example.com'})
        attempts = []

        class Request:
            def execute(self):
                attempts.append(1)
                if len(attempts) == 1:
                    raise FakeHttpError(429, 'userRateLimitExceeded')
                return {'id': 'm1'}

        with patch('migrate.backoff_seconds', return_value=0), patch('migrate.time.sleep'):
            self.assertEqual(migrator.execute_gmail(Request), {'id': 'm1'})
        self.assertEqual(len(attempts), 2)

    def test_gmail_page_connection_reset_retries_without_losing_position(self):
        from migrate import DualAuthMigrator

        attempts = []
        closed = []

        class Transport:
            def close(self):
                closed.append(1)

        class Request:
            def __init__(self, response=None, reset=False):
                self.response = response
                self.reset = reset
                self.http = Transport()

            def execute(self):
                if self.reset:
                    raise ConnectionResetError(errno.ECONNRESET, 'Connection reset by peer')
                return self.response

        class Messages:
            def list(self, **kwargs):
                token = kwargs.get('pageToken')
                if token is None:
                    return Request({'messages': [{'id': 'done'}], 'nextPageToken': 'page-2'})
                attempts.append(token)
                return Request(
                    {'messages': [{'id': 'pending', 'raw': 'cmF3'}]},
                    reset=len(attempts) == 1,
                )

            def get(self, **kwargs):
                return Request({'raw': 'cmF3'})

        class Service:
            def users(self):
                return self

            def messages(self):
                return Messages()

        migrator = DualAuthMigrator({'gmail_account': 'a@example.com', 'gmail_prefetch': 0})
        migrator.gmail_service = Service()
        migrator.processed_emails = {'done'}
        with patch('migrate.backoff_seconds', return_value=0), patch('migrate.time.sleep'):
            self.assertEqual([message['id'] for message in migrator.iter_emails()], ['pending'])
        self.assertEqual(attempts, ['page-2', 'page-2'])
        self.assertEqual(len(closed), 1)
        self.assertEqual(len(migrator.failed_emails), 0)

    def test_gmail_connection_exhaustion_stops_without_marking_email_failed(self):
        from migrate import DualAuthMigrator

        migrator = DualAuthMigrator({'gmail_account': 'a@example.com', 'max_rate_retries': 1})
        attempts = []

        class Request:
            def execute(self):
                attempts.append(1)
                raise ConnectionResetError(errno.ECONNRESET, 'Connection reset by peer')

        with patch('migrate.backoff_seconds', return_value=0), patch('migrate.time.sleep'):
            with self.assertRaisesRegex(ConnectionError, 'after 2 attempts'):
                migrator.execute_gmail(Request)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(migrator.failed_emails), 0)

    def test_archive_throttle_retries_then_commits_message(self):
        from migrate import DualAuthMigrator

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            migrator = DualAuthMigrator({
                'gmail_account': 'account@example.com',
                'group_email': 'group@example.com',
                'state_db': str(root / 'state.sqlite3'),
                'rate_initial_qps': 100,
                'rate_max_qps': 100,
            })
            migrator.progress_file = root / 'missing.json'
            migrator.load_progress()
            attempts = []
            upload_modes = []

            class Request:
                def execute(self):
                    attempts.append(1)
                    if len(attempts) == 1:
                        raise FakeHttpError(429, 'rateLimitExceeded')
                    return {'kind': 'groupsmigration#groups'}

            class Archive:
                def insert(self, **kwargs):
                    upload_modes.append(kwargs['media_body'].resumable())
                    return Request()

            class Service:
                def archive(self):
                    return Archive()

            migrator.groups_migration_service = Service()
            raw = b'Date: Wed, 01 Jan 2020 00:00:00 +0000\r\n\r\nhello'
            message = {'id': 'm1', 'raw': base64.urlsafe_b64encode(raw).decode()}
            with patch('migrate.backoff_seconds', return_value=0):
                self.assertTrue(migrator.migrate_email_to_group(message))
            self.assertEqual(len(attempts), 2)
            self.assertEqual(upload_modes, [False, False])
            self.assertIn('m1', migrator.processed_emails)
            self.assertEqual(migrator.rate_limiter.status()['throttles'], 1)

    def test_daily_quota_stops_without_marking_message_failed(self):
        from migrate import DualAuthMigrator

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            migrator = DualAuthMigrator({
                'gmail_account': 'account@example.com',
                'group_email': 'group@example.com',
                'state_db': str(root / 'state.sqlite3'),
            })
            migrator.progress_file = root / 'missing.json'
            migrator.load_progress()

            class Request:
                def execute(self):
                    raise FakeHttpError(403, 'dailyLimitExceeded')

            class Archive:
                def insert(self, **kwargs):
                    return Request()

            class Service:
                def archive(self):
                    return Archive()

            migrator.groups_migration_service = Service()
            message = {'id': 'm1', 'raw': base64.urlsafe_b64encode(b'Subject: x\r\n\r\nx').decode()}
            with self.assertRaises(DailyQuotaExceeded):
                migrator.migrate_email_to_group(message)
            self.assertNotIn('m1', migrator.processed_emails)
            self.assertEqual(len(migrator.failed_emails), 0)


class BatchReportTests(unittest.TestCase):
    def test_interrupted_migration_reports_checkpoint_and_incomplete_status(self):
        from batch_migration import BatchMigrator

        class InterruptedMigrator:
            def __init__(self, config):
                self.processed_emails = {'m1', 'm2'}
                self.failed_emails = []

            def authenticate_both(self):
                return True

            def verify_group_access(self, group):
                return True

            def migrate_all_emails(self):
                raise ConnectionError('Gmail connection failed after retries')

        with tempfile.TemporaryDirectory() as directory:
            batch = BatchMigrator.__new__(BatchMigrator)
            batch.batch_config_file = 'test.yaml'
            batch.batch_config = {}
            batch.debug = False
            batch.results = []
            with patch('batch_migration.DualAuthMigrator', InterruptedMigrator):
                result = batch.migrate_user({
                    'gmail_account': 'a@example.com', 'group_email': 'g@example.com'
                })
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['total_processed'], 2)
            batch.results.append(result)
            original_directory = os.getcwd()
            try:
                os.chdir(directory)
                with self.assertLogs('batch_migration', level='INFO') as captured:
                    batch.generate_batch_report()
                report = json.loads(Path('reports/batch_migration_report.json').read_text())
            finally:
                os.chdir(original_directory)
            self.assertEqual(report['total_emails_processed'], 2)
            self.assertTrue(any('BATCH MIGRATION INCOMPLETE' in line for line in captured.output))


if __name__ == '__main__':
    unittest.main()
