#!/usr/bin/env python3
"""
Dual Authentication Gmail to Google Group Migration Tool

This version handles two separate OAuth authentications:
1. Gmail user authentication (for reading emails)
2. Google Workspace admin authentication (for group migration)

This resolves the scope conflict issue.
"""

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import email
import errno
import json
import logging
import os
import shutil
import socket
import sys
import time
import yaml
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Event
from typing import Dict, List, Optional

from google.auth.transport.requests import Request
from google.auth.exceptions import TransportError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from httplib2 import ServerNotFoundError

from migration_state import (
    DailyQuotaExceeded, FailedMessages, GroupRunLock, MigrationState, ProcessedMessages,
    RateLimitExhausted, SharedRateLimiter, backoff_seconds, is_daily_quota,
    is_rate_error,
)

logger = logging.getLogger(__name__)


def local_naive(value):
    """Compare aware message timestamps with local, naive query dates."""
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value


def configure_logging(log_file):
    """Send CLI logs to a file, leaving concise progress prints in the terminal."""
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    root = logging.getLogger()
    for old_handler in root.handlers[:]:
        root.removeHandler(old_handler)
        old_handler.close()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def is_transient_gmail_transport_error(error):
    """Recognize connection failures that are safe to retry on Gmail reads."""
    if isinstance(error, (ConnectionError, TimeoutError, ServerNotFoundError, TransportError)):
        return True
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    return isinstance(error, OSError) and error.errno in {
        errno.ECONNRESET, errno.ECONNABORTED, errno.ETIMEDOUT,
        errno.EPIPE, errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH,
    }


def estimate_remaining_seconds(start_date, oldest_now, current_time,
                               elapsed_seconds, processed_count, total_processed,
                               max_emails=None):
    """Estimate remaining messages from observed mail density and run speed."""
    if elapsed_seconds < 30 or processed_count < 10:
        return None
    if max_emails:
        remaining = max(max_emails - processed_count, 0)
        return remaining * elapsed_seconds / processed_count
    if not (start_date and oldest_now and total_processed >= 100):
        return None

    covered = (local_naive(current_time) - local_naive(oldest_now)).total_seconds()
    remaining = (local_naive(oldest_now) - local_naive(start_date)).total_seconds()
    if covered < 86400 or remaining < 0:
        return None
    estimated_remaining_messages = total_processed * remaining / covered
    return estimated_remaining_messages * elapsed_seconds / processed_count


class DualAuthMigrator:
    """Handles migration with separate Gmail and Admin authentications."""
    
    # Gmail API scopes (user-level)
    GMAIL_SCOPES = [
        'https://www.googleapis.com/auth/gmail.readonly'
    ]
    
    # Admin SDK scopes (admin-level)
    ADMIN_SCOPES = [
        'https://www.googleapis.com/auth/admin.directory.group',
        'https://www.googleapis.com/auth/admin.directory.group.member',
        'https://www.googleapis.com/auth/apps.groups.migration',  # Groups Migration API scope
        'https://www.googleapis.com/auth/drive.file',  # Drive API scope for uploading attachments
        'https://www.googleapis.com/auth/admin.directory.user.readonly'  # User info scope
    ]
    
    def __init__(self, config: Dict):
        """Initialize the dual authentication migrator."""
        self.config = config
        self.gmail_service = None
        self.admin_service = None
        self.groups_migration_service = None  # Groups Migration API service
        self.drive_service = None  # Google Drive API service
        self.gmail_credentials = None
        self.admin_credentials = None
        self.processed_emails = set()
        self.failed_emails = []
        self.email_result_size_estimate = None
        self.oldest_email_date = None
        self.oldest_email_id = None
        self.state = None
        self.rate_limiter = None
        self.timing_totals = {'gmail_get': 0.0, 'archive_upload': 0.0, 'checkpoint': 0.0}
        self._progress_line_length = 0

        # Create user-specific progress file
        gmail_account = config.get('gmail_account', 'unknown')
        username = gmail_account.split('@')[0]
        self.progress_file = Path(f'{username}_migration_progress.json')

    def show_progress(self, message):
        """Refresh one terminal line; keep redirected output readable."""
        if not sys.stdout.isatty():
            print(message)
            return
        columns = shutil.get_terminal_size(fallback=(120, 24)).columns
        # Leave room for the extra terminal columns occupied by emoji.
        max_chars = max(columns - 8, 1)
        if len(message) > max_chars:
            message = message[:max_chars - 1] + '…'
        print('\r' + message.ljust(self._progress_line_length), end='', flush=True)
        self._progress_line_length = len(message)

    def finish_progress(self):
        """End the in-place progress line before ordinary output or exit."""
        if self._progress_line_length:
            print(flush=True)
            self._progress_line_length = 0

    def show_message(self, message):
        self.finish_progress()
        print(message)
        
    def authenticate_gmail(self) -> bool:
        """Authenticate with Gmail API using user credentials."""
        try:
            creds = None
            
            # Create token filename based on Gmail account username
            gmail_account = self.config.get('gmail_account', 'unknown')
            username = gmail_account.split('@')[0]  # Extract username part
            token_file = Path(f'{username}_gmail_token.json')
            
            # Load existing Gmail credentials if available
            if token_file.exists():
                creds = Credentials.from_authorized_user_file(str(token_file), self.GMAIL_SCOPES)
            
            # If there are no valid credentials, get new ones
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.config['gmail_credentials_file'], self.GMAIL_SCOPES
                    )
                    
                    print(f"\n🔐 Gmail Authentication Required")
                    print(f"The browser will open automatically for Gmail authentication.")
                    print(f"If it doesn't open, please check the terminal for the URL to open manually.")
                    print(f"Make sure you're logged into the Gmail account: {gmail_account}")
                    
                    # Use local server but with manual control
                    creds = flow.run_local_server(port=0, open_browser=True)
                
                # Save Gmail credentials for next run
                with open(token_file, 'w') as token:
                    token.write(creds.to_json())
            
            self.gmail_credentials = creds
            
            # Build Gmail API service
            self.gmail_service = build('gmail', 'v1', credentials=creds, cache_discovery=False)
            
            logger.info(f"Successfully authenticated with Gmail API for {gmail_account}")
            logger.info(f"Token saved to: {token_file}")
            return True
            
        except Exception as e:
            logger.error(f"Gmail authentication failed: {e}")
            return False
    
    def authenticate_admin(self) -> bool:
        """Authenticate with Admin SDK using admin credentials."""
        try:
            creds = None
            token_file = Path('admin_token.json')
            
            # Load existing admin credentials if available
            if token_file.exists():
                creds = Credentials.from_authorized_user_file(str(token_file), self.ADMIN_SCOPES)
            
            # If there are no valid credentials, get new ones
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.config['admin_credentials_file'], self.ADMIN_SCOPES
                    )
                    
                    print(f"\n🔐 Admin Authentication Required")
                    print(f"The browser will open automatically for Admin authentication.")
                    print(f"If it doesn't open, please check the terminal for the URL to open manually.")
                    print(f"Make sure you're logged into the Admin account for domain: {self.config.get('domain', 'your-domain')}")
                    
                    # Use local server but with manual control
                    creds = flow.run_local_server(port=0, open_browser=True)
                
                # Save admin credentials for next run
                with open(token_file, 'w') as token:
                    token.write(creds.to_json())
            
            self.admin_credentials = creds
            
            # Build Admin SDK service
            self.admin_service = build('admin', 'directory_v1', credentials=creds, cache_discovery=False)
            
            # Build Groups Migration API service
            self.groups_migration_service = build(
                'groupsmigration', 'v1', credentials=creds, cache_discovery=False
            )
            
            # Build Drive API service
            self.drive_service = build('drive', 'v3', credentials=creds, cache_discovery=False)
            
            logger.info("Successfully authenticated with Admin SDK, Groups Migration API, and Drive API")
            return True
            
        except Exception as e:
            logger.error(f"Admin authentication failed: {e}")
            return False
    
    def authenticate_both(self) -> bool:
        """Authenticate with both Gmail and Admin APIs."""
        logger.info("Starting dual authentication process...")
        
        # Authenticate with Gmail
        if not self.authenticate_gmail():
            logger.error("Failed to authenticate with Gmail")
            return False
        
        # Authenticate with Admin SDK
        if not self.authenticate_admin():
            logger.error("Failed to authenticate with Admin SDK")
            return False
        
        logger.info("Dual authentication completed successfully")
        return True
    
    def load_progress(self) -> None:
        """Open the indexed checkpoint and import legacy JSON once, if present."""
        if self.state is not None:
            return
        db_path = self.config.get('state_db', '.state/migration.sqlite3')
        self.state = MigrationState(
            db_path, self.config['gmail_account'], self.config['group_email']
        )
        legacy_file = self.progress_file if self.config.get('import_legacy_progress', True) else None
        self.state.import_legacy(legacy_file)
        self.processed_emails = ProcessedMessages(self.state)
        self.failed_emails = FailedMessages(self.state)
        oldest_date_str, self.oldest_email_id = self.state.oldest()
        if oldest_date_str:
            try:
                self.oldest_email_date = datetime.fromisoformat(oldest_date_str)
            except (ValueError, TypeError) as e:
                logger.warning(f"Could not parse oldest_email_date in checkpoint: {e}")
                self.oldest_email_id = None
        self.rate_limiter = SharedRateLimiter(
            db_path,
            scope=self.config.get('rate_scope', 'groupsmigration'),
            initial_qps=float(self.config.get('rate_initial_qps', 5)),
            max_qps=float(self.config.get('rate_max_qps', 9)),
        )
        logger.info(
            "Loaded progress: %s processed, %s failed; SQLite checkpoint: %s",
            f'{len(self.processed_emails):,}', f'{len(self.failed_emails):,}', db_path,
        )
    
    def save_progress(self) -> None:
        """Persist the oldest-message marker; IDs and failures commit immediately."""
        if self.state is not None:
            started = time.monotonic()
            try:
                self.state.save_oldest(self.oldest_email_date, self.oldest_email_id)
            finally:
                self.timing_totals['checkpoint'] += time.monotonic() - started

    def execute_gmail(self, request_factory, stop_event=None):
        """Retry the same Gmail request on transient or quota failures."""
        max_retries = int(self.config.get('max_rate_retries', 7))
        for attempt in range(max_retries + 1):
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError('Gmail prefetch stopped')
            request = None
            try:
                request = request_factory()
                return request.execute()
            except HttpError as error:
                if is_daily_quota(error):
                    raise DailyQuotaExceeded(f"Gmail daily quota exceeded: {error}") from error
                retryable = is_rate_error(error) or error.resp.status in (408, 500, 502, 504)
                if not retryable:
                    raise
                if attempt >= max_retries:
                    raise RateLimitExhausted(
                        f"Gmail request failed after {max_retries + 1} attempts: {error}"
                    ) from error
                delay = backoff_seconds(attempt, error)
                logger.warning("Gmail request throttled or unavailable; retrying in %.1fs", delay)
                if stop_event is None:
                    time.sleep(delay)
                elif stop_event.wait(delay):
                    raise RuntimeError('Gmail prefetch stopped')
            except (OSError, ServerNotFoundError, TransportError) as error:
                if not is_transient_gmail_transport_error(error):
                    raise
                transport = getattr(request, 'http', None)
                if transport is not None:
                    try:
                        transport.close()
                    except Exception:
                        logger.debug("Could not close interrupted Gmail connection", exc_info=True)
                if attempt >= max_retries:
                    raise ConnectionError(
                        f"Gmail connection failed after {max_retries + 1} attempts: {error}"
                    ) from error
                delay = backoff_seconds(attempt)
                logger.warning(
                    "Gmail connection interrupted (%s); retrying request in %.1fs "
                    "(attempt %d/%d)", error, delay, attempt + 1, max_retries + 1,
                )
                if stop_event is None:
                    time.sleep(delay)
                elif stop_event.wait(delay):
                    raise RuntimeError('Gmail prefetch stopped')

    def insert_archive(self, raw_email_bytes, email_id):
        """Apply the shared adaptive limit to one archive insertion."""
        from googleapiclient.http import MediaIoBaseUpload
        import io

        max_retries = int(self.config.get('max_rate_retries', 7))
        for attempt in range(max_retries + 1):
            self.rate_limiter.acquire()
            media = MediaIoBaseUpload(
                io.BytesIO(raw_email_bytes), mimetype='message/rfc822', resumable=False
            )
            try:
                started = time.monotonic()
                try:
                    result = self.groups_migration_service.archive().insert(
                        groupId=self.config['group_email'], media_body=media
                    ).execute()
                finally:
                    self.timing_totals['archive_upload'] += time.monotonic() - started
                self.rate_limiter.record_success()
                return result
            except HttpError as error:
                if is_daily_quota(error):
                    raise DailyQuotaExceeded(
                        f"Groups Migration daily quota exceeded: {error}"
                    ) from error
                if not is_rate_error(error):
                    raise
                delay = backoff_seconds(attempt, error)
                qps = self.rate_limiter.record_throttle(delay)
                logger.warning(
                    "Groups Migration throttled email %s; shared rate now %.2f QPS, "
                    "cooldown %.1fs (attempt %d/%d)",
                    email_id, qps, delay, attempt + 1, max_retries + 1,
                )
                if attempt >= max_retries:
                    raise RateLimitExhausted(
                        f"Groups Migration remained throttled after {max_retries + 1} "
                        f"attempts on email {email_id}"
                    ) from error

    def _prefetch_gmail_service(self):
        """Give the Gmail fetch thread its own HTTP transport and credentials."""
        credentials = Credentials.from_authorized_user_info(
            json.loads(self.gmail_credentials.to_json()), self.GMAIL_SCOPES
        )
        return build('gmail', 'v1', credentials=credentials, cache_discovery=False)
    
    def iter_emails(self):
        """Stream Gmail results, skipping a verified completed middle stretch."""
        query = self.config.get('gmail_query', 'in:all')
        max_emails = self.config.get('max_emails')
        force_all = self.config.get('force_all', False)
        if force_all and max_emails:
            raise ValueError('--force-all cannot be combined with max_emails')
        yielded = 0
        skipped_processed = 0
        self.email_result_size_estimate = None
        saved_range = self.state.verified_range(query) if self.state else None
        if force_all and self.state:
            # A full audit may discover a backfilled message inside the old range.
            self.state.clear_verified_range(query)
            saved_range = None
        if saved_range and not max_emails:
            newest_ms, oldest_ms = saved_range
            searches = [
                ('recent', f'({query}) after:{max(0, newest_ms // 1000 - 86400)}'),
                ('older', f'({query}) before:{oldest_ms // 1000 + 86400}'),
            ]
            logger.info('Using verified Gmail range %d..%d; scanning both one-day edges',
                        oldest_ms, newest_ms)
        else:
            searches = [('full', query)]
            logger.info('Scanning full Gmail query: %s', query)
        if max_emails:
            logger.info(f"Limiting retrieval to {max_emails:,} emails")

        prefetch = int(self.config.get('gmail_prefetch', 2))
        if not 0 <= prefetch <= 8:
            raise ValueError('gmail_prefetch must be between 0 and 8')

        def internal_ms(message_id):
            detail = self.execute_gmail(lambda: self.gmail_service.users().messages().get(
                userId='me', id=message_id, format='metadata', fields='id,internalDate'
            ))
            return int(detail['internalDate'])

        def stream(results, search_query, mode):
            nonlocal yielded, skipped_processed
            current_results = results
            executor = None
            fetch_service = self.gmail_service
            stop_fetch = Event()
            prefix_active = True
            prefix_first = None
            prefix_first_ms = None
            prefix_last = None
            prefix_last_ms = None
            scan_complete = False

            def observe_processed(message_id, message_ms=None):
                nonlocal prefix_first, prefix_first_ms, prefix_last, prefix_last_ms
                if not prefix_active or not self.state or max_emails:
                    return
                if prefix_first is None:
                    prefix_first, prefix_first_ms = message_id, message_ms
                prefix_last, prefix_last_ms = message_id, message_ms

            def flush_prefix():
                nonlocal prefix_first_ms, prefix_last_ms
                if (not prefix_active or prefix_first is None or not self.state or max_emails
                        or mode == 'recent'):
                    return
                if prefix_last_ms is None:
                    prefix_last_ms = internal_ms(prefix_last)
                if mode == 'full':
                    if prefix_first_ms is None:
                        prefix_first_ms = internal_ms(prefix_first)
                    self.state.extend_verified_range(query, prefix_first_ms, prefix_last_ms)
                elif mode == 'older':
                    self.state.extend_verified_range(
                        query, saved_range[0], min(saved_range[1], prefix_last_ms)
                    )

            try:
                if prefetch > 1:
                    fetch_service = self._prefetch_gmail_service()
                    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='gmail-fetch')

                def fetch_message(msg_id):
                    started = time.monotonic()
                    detail = self.execute_gmail(
                        lambda: fetch_service.users().messages().get(
                            userId='me', id=msg_id, format='raw'
                        ),
                        stop_event=stop_fetch if executor else None,
                    )
                    return detail, time.monotonic() - started

                while True:
                    messages = current_results.get('messages', []) or []
                    if not messages:
                        break

                    def pending_ids():
                        for message in messages:
                            msg_id = message['id']
                            yield msg_id, msg_id in self.processed_emails

                    ids = iter(pending_ids())
                    pending = deque()

                    def fill_pending():
                        while len(pending) < max(prefetch, 1):
                            if max_emails and yielded + sum(
                                not already_processed for _, _, already_processed in pending
                            ) >= max_emails:
                                break
                            try:
                                msg_id, already_processed = next(ids)
                            except StopIteration:
                                break
                            future = (executor.submit(fetch_message, msg_id)
                                      if executor and not already_processed else None)
                            pending.append((msg_id, future, already_processed))

                    fill_pending()
                    while pending:
                        msg_id, future, already_processed = pending.popleft()
                        if already_processed:
                            skipped_processed += 1
                            observe_processed(msg_id)
                            fill_pending()
                            continue
                        if mode in ('full', 'older'):
                            # Save the completed prefix before a slow upload or interruption.
                            flush_prefix()
                        try:
                            msg_detail, gmail_seconds = (
                                future.result() if future else fetch_message(msg_id)
                            )
                        except HttpError as e:
                            logger.error(f"Error retrieving message {msg_id}: {e}")
                            self.failed_emails.append({
                                'id': msg_id,
                                'error': str(e),
                                'timestamp': datetime.now().isoformat()
                            })
                            prefix_active = False
                            fill_pending()
                            continue

                        self.timing_totals['gmail_get'] += gmail_seconds

                        yield {
                            'id': msg_id,
                            'raw': msg_detail['raw'],
                            'threadId': msg_detail.get('threadId'),
                            'labelIds': msg_detail.get('labelIds', []),
                            'snippet': msg_detail.get('snippet', ''),
                            'sizeEstimate': msg_detail.get('sizeEstimate', 0),
                            'internalDate': msg_detail.get('internalDate'),
                        }

                        if msg_id in self.processed_emails:
                            timestamp = msg_detail.get('internalDate')
                            observe_processed(msg_id, int(timestamp) if timestamp else None)
                        else:
                            prefix_active = False

                        logger.debug(f"Streamed email {msg_id}")

                        yielded += 1
                        if max_emails and yielded >= max_emails:
                            logger.info(f"Reached max_emails limit ({max_emails:,}), stopping retrieval")
                            return
                        fill_pending()

                    page_token = current_results.get('nextPageToken')
                    if not page_token:
                        scan_complete = True
                        break

                    flush_prefix()

                    time.sleep(0.1)

                    try:
                        current_results = self.execute_gmail(
                            lambda: self.gmail_service.users().messages().list(
                                userId='me', q=search_query, pageToken=page_token, maxResults=500
                            )
                        )
                    except Exception as e:
                        logger.error(f"Error retrieving emails: {e}")
                        raise
            finally:
                stop_fetch.set()
                if executor:
                    executor.shutdown(wait=True, cancel_futures=True)
                if fetch_service is not self.gmail_service:
                    fetch_service.close()
                if prefix_active:
                    flush_prefix()
                if mode == 'recent' and scan_complete and prefix_active and prefix_first:
                    if prefix_first_ms is None:
                        prefix_first_ms = internal_ms(prefix_first)
                    self.state.extend_verified_range(
                        query, max(saved_range[0], prefix_first_ms), saved_range[1]
                    )
                logger.info(f"Streamed {yielded:,} emails for processing (skipped {skipped_processed:,} already processed)")

        def stream_searches():
            for mode, search_query in searches:
                logger.info('Gmail %s search: %s', mode, search_query)
                try:
                    first_page = self.execute_gmail(lambda: self.gmail_service.users().messages().list(
                        userId='me', q=search_query, maxResults=500
                    ))
                except Exception as error:
                    logger.error('Error retrieving emails: %s', error)
                    raise
                if self.email_result_size_estimate is None:
                    self.email_result_size_estimate = first_page.get('resultSizeEstimate', 0)
                yield from stream(first_page, search_query, mode)
                if max_emails and yielded >= max_emails:
                    return

        return stream_searches()

    def get_all_emails(self) -> List[Dict]:
        """Retrieve all emails from Gmail into memory (legacy helper)."""
        logger.warning("get_all_emails loads the entire mailbox into memory; prefer iter_emails() for streaming")
        return list(self.iter_emails())
    
    def get_user_info(self, user_email: str) -> Optional[Dict]:
        """Get user information including creation date from Admin SDK."""
        try:
            user_info = self.admin_service.users().get(userKey=user_email).execute()
            
            # Parse creationTime (could be ISO datetime string or milliseconds timestamp)
            creation_time_ms = user_info.get('creationTime')
            creation_date = None
            if creation_time_ms:
                try:
                    # Try to parse as ISO datetime string first
                    if isinstance(creation_time_ms, str) and ('T' in creation_time_ms or '-' in creation_time_ms[:10]):
                        # ISO datetime format (e.g., '2021-01-29T15:01:47.000Z')
                        # Handle 'Z' timezone indicator (UTC)
                        iso_str = creation_time_ms
                        if iso_str.endswith('Z'):
                            iso_str = iso_str[:-1] + '+00:00'
                        creation_date = datetime.fromisoformat(iso_str)
                    else:
                        # Assume it's a milliseconds timestamp (integer or string)
                        creation_date = datetime.fromtimestamp(int(creation_time_ms) / 1000)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Could not parse creationTime '{creation_time_ms}': {e}")
                    creation_date = None
            
            return {
                'email': user_info.get('primaryEmail'),
                'name': user_info.get('name', {}).get('fullName'),
                'creation_time': creation_time_ms,
                'creation_date': creation_date.isoformat() if creation_date else None,
                'suspended': user_info.get('suspended', False),
                'is_admin': user_info.get('isAdmin', False),
                'org_unit_path': user_info.get('orgUnitPath', '/')
            }
        except HttpError as e:
            if e.resp.status == 404:
                logger.error(f"User {user_email} not found")
            elif e.resp.status == 403:
                logger.error(f"Access denied to user info for {user_email}")
            else:
                logger.error(f"Error getting user info for {user_email}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting user info for {user_email}: {e}")
            return None
    
    def verify_group_access(self, group_email: str) -> bool:
        """Verify that we have access to the target Google Group."""
        try:
            # Try to get group information
            group_info = self.admin_service.groups().get(groupKey=group_email).execute()
            
            logger.info(f"Group {group_email} is accessible")
            logger.debug(f"Group info: {group_info}")
            return True
            
        except HttpError as e:
            if e.resp.status == 404:
                logger.error(f"Group {group_email} not found")
            elif e.resp.status == 403:
                logger.error(f"Access denied to group {group_email}")
            else:
                logger.error(f"Error accessing group {group_email}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error verifying group access: {e}")
            return False
    
    def extract_attachments(self, email_msg, include_inline_images: bool = True) -> List[Dict]:
        """Extract attachments and optionally inline images from an email message."""
        attachments = []
        if email_msg.is_multipart():
            for part in email_msg.walk():
                # Skip multipart containers
                if part.get_content_maintype() == 'multipart':
                    continue
                
                content_disposition = part.get("Content-Disposition", "")
                content_id = part.get("Content-ID", "")
                content_location = part.get("Content-Location", "")
                content_type = part.get_content_type()
                
                # Check if this is an attachment
                is_attachment = "attachment" in content_disposition.lower()
                
                # Check if this is an inline image (has Content-ID or Content-Location, or is an image with no disposition)
                is_inline_image = (
                    include_inline_images and
                    (content_id or content_location) and
                    content_type.startswith('image/') and
                    "attachment" not in content_disposition.lower()
                )
                
                if is_attachment or is_inline_image:
                    payload = part.get_payload(decode=True)
                    if payload:
                        # Try to get filename from various sources
                        filename = part.get_filename()
                        if not filename:
                            # Generate filename from Content-ID or Content-Location
                            if content_id:
                                # Remove < > brackets and use as filename hint
                                cid = content_id.strip('<>')
                                # Extract extension from content type
                                ext = content_type.split('/')[-1] if '/' in content_type else 'bin'
                                filename = f"inline_{cid}.{ext}"
                            elif content_location:
                                filename = content_location.split('/')[-1]
                            else:
                                ext = content_type.split('/')[-1] if '/' in content_type else 'bin'
                                filename = f"inline_image.{ext}"
                        
                        attachments.append({
                            'filename': filename,
                            'content': payload,
                            'content_type': content_type,
                            'content_id': content_id,
                            'is_inline': is_inline_image
                        })
        return attachments
    
    def upload_attachment_to_drive(self, attachment: Dict, email_id: str) -> Optional[str]:
        """Upload an attachment to Google Drive and return the file ID."""
        try:
            from googleapiclient.http import MediaIoBaseUpload
            import io
            
            filename = attachment['filename']
            content = attachment['content']
            content_type = attachment.get('content_type', 'application/octet-stream')
            
            # Create a folder structure: Migrated Attachments/{group_email}/{email_id}/
            group_email = self.config.get('group_email', 'unknown')
            folder_name = f"Migrated Attachments/{group_email}"
            
            # Find or create the folder
            folder_id = self.get_or_create_drive_folder(folder_name)
            if not folder_id:
                logger.error(f"Failed to create/find Drive folder: {folder_name}")
                return None
            
            # Upload file to Drive
            file_metadata = {
                'name': f"{email_id}_{filename}",
                'parents': [folder_id]
            }
            media = MediaIoBaseUpload(
                io.BytesIO(content),
                mimetype=content_type,
                resumable=True
            )
            
            file = self.drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id,webViewLink'
            ).execute()
            
            file_id = file.get('id')
            web_link = file.get('webViewLink')
            
            logger.info(f"Uploaded attachment {filename} to Drive: {web_link}")
            return file_id
            
        except HttpError as e:
            error_details = f"HTTP {e.resp.status}: {e.error_details if hasattr(e, 'error_details') else str(e)}"
            logger.error(f"Failed to upload attachment {attachment.get('filename', 'unknown')} to Drive: {error_details}")
            logger.error(f"Full error: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to upload attachment {attachment.get('filename', 'unknown')} to Drive: {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            return None
    
    def get_or_create_drive_folder(self, folder_path: str) -> Optional[str]:
        """Get or create a folder structure in Drive. Returns folder ID."""
        try:
            folders = folder_path.split('/')
            parent_id = None
            
            for folder_name in folders:
                # Check if folder exists
                query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder'"
                if parent_id:
                    query += f" and '{parent_id}' in parents"
                else:
                    query += " and 'root' in parents"
                
                results = self.drive_service.files().list(
                    q=query,
                    spaces='drive',
                    fields='files(id, name)'
                ).execute()
                
                items = results.get('files', [])
                
                if items:
                    # Folder exists, use it
                    parent_id = items[0]['id']
                else:
                    # Create folder
                    file_metadata = {
                        'name': folder_name,
                        'mimeType': 'application/vnd.google-apps.folder'
                    }
                    if parent_id:
                        file_metadata['parents'] = [parent_id]
                    
                    folder = self.drive_service.files().create(
                        body=file_metadata,
                        fields='id'
                    ).execute()
                    parent_id = folder.get('id')
            
            return parent_id
            
        except HttpError as e:
            error_details = f"HTTP {e.resp.status}: {e.error_details if hasattr(e, 'error_details') else str(e)}"
            logger.error(f"Failed to get/create Drive folder {folder_path}: {error_details}")
            logger.error(f"Full error: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to get/create Drive folder {folder_path}: {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            return None
    
    def share_file_with_group(self, file_id: str, group_email: str) -> bool:
        """Share a Drive file with a Google Group."""
        try:
            permission = {
                'type': 'group',
                'role': 'reader',
                'emailAddress': group_email
            }
            
            self.drive_service.permissions().create(
                fileId=file_id,
                body=permission,
                fields='id'
            ).execute()
            
            logger.debug(f"Shared Drive file {file_id} with group {group_email}")
            return True
            
        except Exception as e:
            logger.warning(f"Failed to share file {file_id} with group {group_email}: {e}")
            return False
    
    def replace_attachments_with_links(self, email_msg, attachment_links: List[Dict]) -> bytes:
        """Replace attachments and inline images in email with Drive links and return modified email as bytes."""
        try:
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            import re
            
            # Create mapping of Content-ID to Drive link for inline images
            content_id_to_link = {}
            filename_to_link = {}
            
            for link_info in attachment_links:
                filename = link_info['filename']
                link = link_info['link']
                content_id = link_info.get('content_id', '')
                is_inline = link_info.get('is_inline', False)
                
                filename_to_link[filename] = link
                if is_inline and content_id:
                    # Store with and without brackets
                    cid_clean = content_id.strip('<>')
                    content_id_to_link[content_id] = link
                    content_id_to_link[f"<{cid_clean}>"] = link
                    content_id_to_link[cid_clean] = link
            
            # Create new message
            new_msg = MIMEMultipart()
            
            # Copy headers (excluding Content-Type and Content-Disposition)
            for header_name in email_msg.keys():
                if header_name.lower() not in ['content-type', 'content-disposition', 'content-transfer-encoding']:
                    new_msg[header_name] = email_msg[header_name]
            
            # Get original body parts (skip attachments and inline images we're removing)
            body_parts = []
            for part in email_msg.walk():
                # Skip multipart containers
                if part.get_content_maintype() == 'multipart':
                    continue
                
                # Skip attachments and inline images we're moving to Drive
                content_disposition = part.get("Content-Disposition", "")
                content_id = part.get("Content-ID", "")
                is_attachment = "attachment" in content_disposition.lower()
                is_inline_to_replace = (
                    content_id and
                    part.get_content_type().startswith('image/') and
                    any(content_id.strip('<>') in cid for cid in content_id_to_link.keys())
                )
                
                if not is_attachment and not is_inline_to_replace:
                    # This is the message body
                    body_content = part.get_payload(decode=True)
                    if body_content:
                        body_parts.append((part.get_content_type(), body_content))
            
            # Add body parts
            text_part = None
            html_part = None
            for content_type, body_content in body_parts:
                if content_type == 'text/plain':
                    text_part = MIMEText(body_content.decode('utf-8', errors='ignore'), 'plain')
                    new_msg.attach(text_part)
                elif content_type == 'text/html':
                    # Replace Content-ID references in HTML with Drive links
                    html_str = body_content.decode('utf-8', errors='ignore')
                    
                    # Replace Content-ID references (cid:...)
                    for cid, link in content_id_to_link.items():
                        cid_clean = cid.strip('<>')
                        # Replace various formats: cid:xxx, cid:<xxx>, etc.
                        patterns = [
                            rf'cid:{re.escape(cid_clean)}',
                            rf'cid:<{re.escape(cid_clean)}>',
                            rf'cid:&lt;{re.escape(cid_clean)}&gt;',
                        ]
                        for pattern in patterns:
                            html_str = re.sub(pattern, link, html_str, flags=re.IGNORECASE)
                    
                    html_part = MIMEText(html_str, 'html')
                    new_msg.attach(html_part)
                else:
                    # Preserve other body parts as-is
                    from email.mime.base import MIMEBase
                    other_part = MIMEBase(*content_type.split('/', 1))
                    other_part.set_payload(body_content)
                    new_msg.attach(other_part)
            
            # Add attachment/inline image links as text (for plain text clients)
            if attachment_links:
                inline_items = [l for l in attachment_links if l.get('is_inline', False)]
                attach_items = [l for l in attachment_links if not l.get('is_inline', False)]
                
                link_text = "\n\n--- Files moved to Google Drive ---\n"
                
                if inline_items:
                    link_text += "\nInline images:\n"
                    for link_info in inline_items:
                        link_text += f"  {link_info['filename']}: {link_info['link']}\n"
                
                if attach_items:
                    link_text += "\nAttachments:\n"
                    for link_info in attach_items:
                        link_text += f"  {link_info['filename']}: {link_info['link']}\n"
                
                # Append links to the text part if it exists, otherwise create new text part
                if text_part:
                    text_part.set_payload(text_part.get_payload() + link_text)
                else:
                    new_msg.attach(MIMEText(link_text, 'plain'))
            
            # Convert to bytes
            return new_msg.as_bytes()
            
        except Exception as e:
            logger.error(f"Failed to replace attachments with links: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            raise
    
    def save_to_dead_letter(self, email: Dict, email_size: int, error_msg: str) -> None:
        """Save failed email to dead_letter folder."""
        try:
            dead_letter_dir = Path('dead_letter')
            dead_letter_dir.mkdir(exist_ok=True)
            
            # Extract username from gmail_account
            gmail_account = self.config.get('gmail_account', 'unknown')
            username = gmail_account.split('@')[0]
            
            # Create filename: username_emailid.json
            filename = f"{username}_{email['id']}.json"
            filepath = dead_letter_dir / filename
            
            # Save email metadata and content as JSON
            email_data = {
                'id': email['id'],
                'email_address': gmail_account,
                'size_bytes': email_size,
                'size_mb': round(email_size / (1024 * 1024), 2),
                'error': error_msg,
                'timestamp': datetime.now().isoformat(),
                'threadId': email.get('threadId'),
                'labelIds': email.get('labelIds', []),
                'snippet': email.get('snippet', ''),
                'sizeEstimate': email.get('sizeEstimate', 0),
                'raw_email_base64': email['raw']  # Save the base64 encoded raw email
            }
            
            with open(filepath, 'w') as f:
                json.dump(email_data, f, indent=2)
            
            logger.info(f"Saved failed email to dead_letter: {filepath}")
        except Exception as e:
            logger.error(f"Failed to save email to dead_letter folder: {e}")
    
    def migrate_email_to_group(self, email: Dict, retry_count: int = 0) -> bool:
        """
        Migrate a single email to the Google Group using Groups Migration API.

        The Groups Migration API preserves all original email metadata, timestamps,
        threading, and content automatically - no need to modify the email.

        Args:
            email: Email dictionary from Gmail API
            retry_count: Internal retry counter for timeout/connection errors
        """
        max_retries = self.config.get('max_retries', 3)
        try:
            import base64
            
            # Get the raw email content (already base64 encoded from Gmail API)
            # Decode it to bytes for the Groups Migration API
            raw_email_bytes = base64.urlsafe_b64decode(email['raw'])
            email_size = len(raw_email_bytes)
            
            # Check max_email_size from config (nested under migration:)
            max_email_size = self.config.get('migration', {}).get('max_email_size', 26214400)  # Default 25MB
            skip_oversized = self.config.get('migration', {}).get('skip_oversized_emails', False)
            
            if email_size > max_email_size:
                if skip_oversized:
                    logger.warning(f"Skipping email {email['id']}: size {email_size} bytes exceeds max_email_size {max_email_size} bytes")
                    return False
                else:
                    # Try to handle oversized emails by moving attachments to Drive
                    upload_attachments = self.config.get('migration', {}).get('upload_attachments_to_drive', False)
                    
                    if upload_attachments and self.drive_service:
                        logger.info(f"Email {email['id']} is oversized. Attempting to move attachments to Drive...")
                        self.show_message(f"📎 Email {email['id']} is oversized ({email_size / (1024*1024):.2f} MB). Moving attachments to Drive...")
                        
                        try:
                            # Verify Drive service is available
                            if not self.drive_service:
                                raise Exception("Drive service not initialized. Check authentication and Drive API enablement.")
                            
                            # Parse email to extract attachments
                            from email import message_from_bytes
                            email_msg = message_from_bytes(raw_email_bytes)
                            
                            # Extract attachments and inline images
                            attachments = self.extract_attachments(email_msg, include_inline_images=True)
                            
                            if attachments:
                                # Count inline images vs attachments
                                inline_count = sum(1 for a in attachments if a.get('is_inline', False))
                                attach_count = len(attachments) - inline_count
                                
                                if inline_count > 0 and attach_count > 0:
                                    logger.info(f"Found {len(attachments)} items to move to Drive ({attach_count} attachment(s), {inline_count} inline image(s))")
                                elif inline_count > 0:
                                    logger.info(f"Found {len(attachments)} inline image(s) to move to Drive")
                                else:
                                    logger.info(f"Found {len(attachments)} attachment(s) to move to Drive")
                                attachment_links = []
                                
                                # Upload each attachment
                                for idx, attachment in enumerate(attachments, 1):
                                    logger.info(f"Uploading attachment {idx}/{len(attachments)}: {attachment.get('filename', 'unknown')} ({len(attachment.get('content', []))} bytes)")
                                    file_id = self.upload_attachment_to_drive(attachment, email['id'])
                                    if file_id:
                                        try:
                                            # Get file web link
                                            file_info = self.drive_service.files().get(
                                                fileId=file_id,
                                                fields='webViewLink, name'
                                            ).execute()
                                            
                                            web_link = file_info.get('webViewLink')
                                            
                                            # Share with group
                                            group_email = self.config.get('group_email')
                                            share_success = self.share_file_with_group(file_id, group_email)
                                            if not share_success:
                                                logger.warning(f"Failed to share file {file_id} with group, but upload succeeded")
                                            
                                            attachment_links.append({
                                                'filename': attachment['filename'],
                                                'link': web_link or f"https://drive.google.com/file/d/{file_id}/view",
                                                'content_id': attachment.get('content_id', ''),
                                                'is_inline': attachment.get('is_inline', False)
                                            })
                                            logger.info(f"Successfully processed attachment {attachment['filename']}")
                                        except Exception as e:
                                            logger.error(f"Failed to get link or share file {file_id}: {e}")
                                            # Still add the file_id-based link
                                            attachment_links.append({
                                                'filename': attachment['filename'],
                                                'link': f"https://drive.google.com/file/d/{file_id}/view",
                                                'content_id': attachment.get('content_id', ''),
                                                'is_inline': attachment.get('is_inline', False)
                                            })
                                    else:
                                        logger.error(f"Failed to upload attachment {attachment.get('filename', 'unknown')}")
                                
                                if attachment_links:
                                    logger.info(f"Successfully uploaded {len(attachment_links)}/{len(attachments)} attachments")
                                    # Replace attachments with links and create modified email
                                    modified_email_bytes = self.replace_attachments_with_links(email_msg, attachment_links)
                                    modified_size = len(modified_email_bytes)
                                    
                                    logger.info(f"Email size reduced from {email_size / (1024*1024):.2f} MB to {modified_size / (1024*1024):.2f} MB")
                                    
                                    # Update raw_email_bytes to use modified version
                                    import base64
                                    raw_email_bytes = modified_email_bytes
                                    email_size = modified_size
                                    
                                    # Re-encode for API
                                    email['raw'] = base64.urlsafe_b64encode(modified_email_bytes).decode('utf-8')
                                    
                                    logger.info(f"Modified email {email['id']} - attachments replaced with Drive links. Retrying migration...")
                                else:
                                    logger.error(f"Failed to upload any attachments for email {email['id']} (attempted {len(attachments)} attachments)")
                                    raise Exception(f"Failed to upload any of {len(attachments)} attachments to Drive")
                            else:
                                logger.warning(f"No attachments found in oversized email {email['id']}")
                                raise Exception("No attachments to move - email body itself is too large")
                                
                        except Exception as e:
                            error_type = type(e).__name__
                            error_str = str(e)
                            logger.error(f"Failed to process oversized email {email['id']} with attachment upload: {error_type}: {error_str}")
                            import traceback
                            logger.debug(f"Traceback: {traceback.format_exc()}")
                            error_msg = f'Email size {email_size} bytes ({email_size / (1024*1024):.2f} MB) exceeds max_email_size {max_email_size} bytes ({max_email_size / (1024*1024):.2f} MB). Attachment upload failed: {error_str}'
                            self.show_message(f"❌ Email {email['id']}: Actual size {email_size} bytes ({email_size / (1024*1024):.2f} MB) | Attachment upload failed: {error_str}")
                            self.failed_emails.append({
                                'id': email['id'],
                                'error': error_msg,
                                'timestamp': datetime.now().isoformat()
                            })
                            self.save_to_dead_letter(email, email_size, error_msg)
                            return False
                    else:
                        error_msg = f'Email size {email_size} bytes ({email_size / (1024*1024):.2f} MB) exceeds max_email_size {max_email_size} bytes ({max_email_size / (1024*1024):.2f} MB)'
                        logger.error(f"Email {email['id']}: {error_msg}")
                        self.show_message(f"❌ Email {email['id']}: Actual size {email_size} bytes ({email_size / (1024*1024):.2f} MB)")
                        self.failed_emails.append({
                            'id': email['id'],
                            'error': error_msg,
                            'timestamp': datetime.now().isoformat()
                        })
                        self.save_to_dead_letter(email, email_size, error_msg)
                        return False
            
            # TEST MODE: Save original email to file for debugging
            if self.config.get('test_mode', False):
                self.finish_progress()
                print(f"\n🔍 TEST MODE: Analyzing email {email['id']}")
                print("=" * 60)
                
                # Save original email as binary
                with open('original_email.txt', 'wb') as f:
                    f.write(raw_email_bytes)
                
                print(f"📧 Email ID: {email['id']}")
                print(f"📧 Thread ID: {email.get('threadId', 'N/A')}")
                print(f"📧 Labels: {email.get('labelIds', [])}")
                print(f"📧 Email size: {len(raw_email_bytes)} bytes")
                print(f"📄 Original email saved to: original_email.txt")
                print("=" * 60)
                print("⏸️  TEST MODE: Migration paused. Review original_email.txt before proceeding.")
                print("=" * 60)
                
                # In test mode, don't actually migrate
                logger.info(f"TEST MODE: Would migrate email {email['id']} to group {self.config['group_email']}")
                return True
            
            # The insert helper coordinates all local runs using the same quota.
            result = self.insert_archive(raw_email_bytes, email['id'])
            
            logger.debug(f"Successfully migrated email {email['id']} to group {self.config['group_email']}")
            logger.debug(f"Groups Migration API result: {result}")
            
            # Extract email date for tracking oldest email (only for successfully processed emails)
            # Note: raw_email_bytes is still available here since it was decoded earlier
            # Note: use 'email' module function, not the parameter name 'email'
            try:
                # Import here to avoid shadowing the 'email' parameter
                from email import message_from_bytes
                email_msg = message_from_bytes(raw_email_bytes)
                date_header = email_msg.get('Date')
                if date_header:
                    try:
                        email_date = parsedate_to_datetime(date_header)
                        # Track oldest email date
                        was_none = self.oldest_email_date is None
                        if was_none or email_date < self.oldest_email_date:
                            self.oldest_email_date = email_date
                            self.oldest_email_id = email['id']
                            if was_none:
                                logger.info(f"Set oldest email date to {email_date.strftime('%Y-%m-%d')} from first processed email {email['id']}")
                            else:
                                logger.debug(f"Updated oldest email date to {email_date.strftime('%Y-%m-%d')} from email {email['id']}")
                    except ValueError as ve:
                        logger.warning(f"Could not parse date header '{date_header}' from email {email['id']}: {ve}")
                else:
                    logger.warning(f"Email {email['id']} has no Date header")
            except Exception as e:
                logger.warning(f"Could not extract date from email {email['id']}: {e}")
            
            # Mark as processed
            started = time.monotonic()
            try:
                self.processed_emails.add(email['id'])
            finally:
                self.timing_totals['checkpoint'] += time.monotonic() - started
            return True
            
        except (DailyQuotaExceeded, RateLimitExhausted):
            # Stop the run with its checkpoint intact. The email is still pending.
            raise
        except HttpError as e:
            # Get email size for error reporting
            try:
                raw_email_bytes = base64.urlsafe_b64decode(email['raw'])
                email_size = len(raw_email_bytes)
            except:
                email_size = email.get('sizeEstimate', 0)
            
            if e.resp.status == 408 or e.resp.status == 504:  # Request timeout or Gateway timeout
                # Retry timeout HTTP errors with exponential backoff
                if retry_count < max_retries:
                    wait_time = min(60 * (2 ** retry_count), 300)  # Exponential backoff, max 5 minutes
                    logger.warning(f"HTTP timeout error ({e.resp.status}) for email {email['id']} (attempt {retry_count + 1}/{max_retries + 1}). Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    return self.migrate_email_to_group(email, retry_count + 1)
                else:
                    # All retries exhausted, save to dead_letter
                    error_msg = f"HTTP timeout error ({e.resp.status}) after {max_retries + 1} attempts: {e}"
                    logger.error(error_msg)
                    self.show_message(f"❌ Email {email['id']}: HTTP timeout after all retries | Actual size: {email_size} bytes ({email_size / (1024*1024):.2f} MB)")
                    self.failed_emails.append({
                        'id': email['id'],
                        'error': error_msg,
                        'timestamp': datetime.now().isoformat()
                    })
                    self.save_to_dead_letter(email, email_size, error_msg)
                    return False
            elif e.resp.status == 403:
                error_msg = f"Access denied for Groups Migration API: {e}"
                logger.error(error_msg)
                logger.error("Ensure the admin account has Groups Migration API permissions")
                self.show_message(f"❌ Email {email['id']}: Access denied | Actual size: {email_size} bytes ({email_size / (1024*1024):.2f} MB)")
                self.failed_emails.append({
                    'id': email['id'],
                    'error': error_msg,
                    'timestamp': datetime.now().isoformat()
                })
                self.save_to_dead_letter(email, email_size, error_msg)
                return False
            elif e.resp.status == 404:
                error_msg = f"Group {self.config['group_email']} not found: {e}"
                logger.error(error_msg)
                self.show_message(f"❌ Email {email['id']}: Group not found | Actual size: {email_size} bytes ({email_size / (1024*1024):.2f} MB)")
                self.failed_emails.append({
                    'id': email['id'],
                    'error': error_msg,
                    'timestamp': datetime.now().isoformat()
                })
                self.save_to_dead_letter(email, email_size, error_msg)
                return False
            else:
                error_msg = f"HTTP error migrating email {email['id']}: {e}"
                logger.error(error_msg)
                self.show_message(f"❌ Email {email['id']}: HTTP error | Actual size: {email_size} bytes ({email_size / (1024*1024):.2f} MB) | Status: {e.resp.status}")
                # Check if error message mentions size limits
                error_str = str(e).lower()
                if '26214400' in error_str or 'size' in error_str or 'too large' in error_str:
                    logger.error(f"Email appears to exceed Google API size limit (25MB): {email_size} bytes ({email_size / (1024*1024):.2f} MB)")
                self.failed_emails.append({
                    'id': email['id'],
                    'error': error_msg,
                    'timestamp': datetime.now().isoformat()
                })
                self.save_to_dead_letter(email, email_size, error_msg)
                return False
        except Exception as e:
            # Get email size for error reporting
            try:
                raw_email_bytes = base64.urlsafe_b64decode(email['raw'])
                email_size = len(raw_email_bytes)
            except:
                email_size = email.get('sizeEstimate', 0)
            
            # Check if this is a timeout or connection error that we should retry
            error_str = str(e).lower()
            is_timeout = (
                'timeout' in error_str or
                'timed out' in error_str or
                'connection' in error_str or
                'read operation' in error_str
            )

            # Retry timeout/connection errors with exponential backoff
            if is_timeout and retry_count < max_retries:
                wait_time = min(60 * (2 ** retry_count), 300)  # Exponential backoff, max 5 minutes
                logger.warning(f"Timeout/connection error for email {email['id']} (attempt {retry_count + 1}/{max_retries + 1}): {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
                return self.migrate_email_to_group(email, retry_count + 1)

            error_msg = f"Failed to migrate email {email['id']}: {e}"
            logger.error(error_msg)
            self.show_message(f"❌ Email {email['id']}: Exception | Actual size: {email_size} bytes ({email_size / (1024*1024):.2f} MB)")
            self.failed_emails.append({
                'id': email['id'],
                'error': error_msg,
                'timestamp': datetime.now().isoformat()
            })
            self.save_to_dead_letter(email, email_size, error_msg)
            return False
    
    def parse_after_date_from_query(self, query: str) -> Optional[datetime]:
        """Parse 'after:YYYY/MM/DD' or 'after:YYYY-MM-DD' from Gmail query."""
        import re
        # Match patterns like: after:2023/01/15 or after:2023-01-15
        patterns = [
            r'after:(\d{4})/(\d{1,2})/(\d{1,2})',
            r'after:(\d{4})-(\d{1,2})-(\d{1,2})'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                year, month, day = map(int, match.groups())
                try:
                    return datetime(year, month, day)
                except ValueError:
                    logger.warning(f"Invalid date in query: {match.group(0)}")
        return None
    
    def get_start_date_for_progress(self) -> Optional[datetime]:
        """Get the start date for progress calculation (after: date or user creation date)."""
        query = self.config.get('gmail_query', 'in:all')
        
        # Try to parse after: date from query
        after_date = self.parse_after_date_from_query(query)
        if after_date:
            logger.debug(f"Using 'after:' date from query as progress start: {after_date.strftime('%Y-%m-%d')}")
            return after_date
        
        # Fall back to user creation date
        gmail_account = self.config.get('gmail_account')
        if gmail_account:
            user_info = self.get_user_info(gmail_account)
            if user_info and user_info.get('creation_date'):
                creation_date_str = user_info['creation_date']
                creation_date = datetime.fromisoformat(creation_date_str.replace('Z', '+00:00'))
                creation_date = local_naive(creation_date)
                logger.debug(f"Using user creation date as progress start: {creation_date.strftime('%Y-%m-%d')}")
                return creation_date
        
        return None
    
    def calculate_date_based_progress(self, start_date=None) -> Optional[float]:
        """Calculate progress percentage based on dates instead of email count."""
        if not self.oldest_email_date:
            return None
        
        start_date = start_date or self.get_start_date_for_progress()
        if not start_date:
            return None
        
        now = datetime.now()
        total_seconds = (now - local_naive(start_date)).total_seconds()
        covered_seconds = (now - local_naive(self.oldest_email_date)).total_seconds()

        if total_seconds <= 0:
            return None
        
        # Use the full timestamps so progress can change within a day.
        progress = (covered_seconds / total_seconds) * 100.0
        
        # Clamp between 0 and 100
        progress = max(0.0, min(100.0, progress))
        
        return progress
    
    def migrate_all_emails(self) -> None:
        """Run one group at a time across local processes."""
        with GroupRunLock(
            self.config.get('state_db', '.state/migration.sqlite3'),
            self.config['group_email'],
        ):
            self._migrate_all_emails()

    def _migrate_all_emails(self) -> None:
        """Migrate all emails from Gmail to Google Group."""
        logger.info("Starting email migration process")
        
        # Verify group access
        if not self.verify_group_access(self.config['group_email']):
            logger.error("Cannot access target Google Group")
            return
        
        # Load previous progress
        self.load_progress()
        
        # Get start date for progress calculation
        start_date = self.get_start_date_for_progress()
        run_started_at = time.monotonic()
        
        # Stream emails instead of loading everything into memory
        email_iter = self.iter_emails()
        max_emails = self.config.get('max_emails')
        processed_count = 0
        failed_count = 0
        emails_streamed = False
        last_progress_count = 0
        last_progress_time = time.monotonic()
        last_timing_totals = self.timing_totals.copy()

        # Use date-based progress calculation instead of email count estimate
        use_date_progress = start_date is not None
        total_target = None
        
        if not use_date_progress:
            # Fall back to old method if we can't determine start date
            if self.email_result_size_estimate is not None:
                remaining_estimate = max(self.email_result_size_estimate - len(self.processed_emails), 0)
                total_target = remaining_estimate
            if max_emails:
                total_target = max_emails if total_target is None else min(total_target, max_emails)

        if use_date_progress:
            if start_date:
                logger.info(f"Using date-based progress calculation (start date: {start_date.strftime('%Y-%m-%d')})")
                print(f"\n🚀 Starting migration with date-based progress...")
                print(f"📅 Start date: {start_date.strftime('%Y-%m-%d')}")
        elif total_target:
            logger.info(f"Processing up to {total_target:,} emails (based on available estimate)")
            print(f"\n🚀 Starting migration of up to {total_target:,} emails...")
        else:
            logger.info("Processing emails in streaming mode (total count unavailable)")
            print("\n🚀 Starting migration (streaming emails...)")
        
        print(f"📧 Target: {self.config['group_email']}")
        if self.config.get('debug', False):
            print(f"⚙️  Adaptive shared rate: up to {self.rate_limiter.max_qps:g} inserts/second")
        print("-" * 60)

        def process_email(email):
            nonlocal processed_count, failed_count, last_progress_count, last_progress_time, last_timing_totals
            processed_count += 1

            success = self.migrate_email_to_group(email)
            if success:
                logger.debug(f"Successfully migrated email {email['id']}")
            else:
                failed_count += 1
                logger.warning(f"Failed to migrate email {email['id']}")

            self.save_progress()

            # Refresh interactive terminals for each message. Keep file logs and
            # redirected output at the existing ten-message cadence.
            log_progress = processed_count % 10 == 0
            if total_target and processed_count >= total_target:
                log_progress = True
            show_progress = sys.stdout.isatty() or log_progress

            if show_progress:
                now = time.monotonic()
                rate_log = ""
                if log_progress and self.config.get('debug', False):
                    window_count = processed_count - last_progress_count
                    per_minute = 60 * window_count / max(now - last_progress_time, 0.001)
                    last_progress_count, last_progress_time = processed_count, now
                    timing_ms = {
                        name: 1000 * (total - last_timing_totals[name]) / window_count
                        for name, total in self.timing_totals.items()
                    }
                    last_timing_totals = self.timing_totals.copy()
                    rate_status = self.rate_limiter.status()
                    rate_log = (
                        f" | rate={rate_status['qps']:.2f} QPS"
                        f" | throttles={rate_status['throttles']}"
                        f" | rate_wait={rate_status['wait_seconds']:.1f}s"
                        f" | throughput={per_minute:.1f}/min"
                        f" | gmail_get={timing_ms['gmail_get']:.0f}ms/msg"
                        f" | upload={timing_ms['archive_upload']:.0f}ms/msg"
                        f" | checkpoint={timing_ms['checkpoint']:.0f}ms/msg"
                    )
                oldest_str = ""
                oldest_log = ""
                if self.oldest_email_date:
                    oldest_time = local_naive(self.oldest_email_date).strftime('%Y-%m-%d %H:%M:%S')
                    oldest_str = f" | 📅 Oldest: {oldest_time}"
                    oldest_log = f" | oldest={oldest_time}"
                
                # Total successfully processed emails (including previous runs)
                total_success = len(self.processed_emails)
                processed_text = f'{processed_count:,}'
                success_text = f'{total_success:,}'
                failed_text = f'{failed_count:,}'
                
                # Calculate date-based progress if available
                date_progress = self.calculate_date_based_progress(start_date) if use_date_progress else None
                remaining_seconds = estimate_remaining_seconds(
                    start_date, self.oldest_email_date, datetime.now(),
                    now - run_started_at, processed_count, total_success, max_emails,
                )
                if remaining_seconds is None:
                    eta_state = 'calculating' if start_date or max_emails else 'unavailable'
                    eta_print = f" | ⏳ ETA: {eta_state}"
                    eta_log = f" | ETA={eta_state}"
                else:
                    completion = datetime.now().astimezone() + timedelta(seconds=remaining_seconds)
                    eta_text = completion.strftime('%Y-%m-%d %H:%M %Z')
                    eta_print = f" | ⏳ ETA: ~{eta_text}"
                    eta_log = f" | ETA~{eta_text}"
                
                if date_progress is not None:
                    # Use date-based progress
                    self.show_progress(f"📊 {date_progress:.3f}% by date{oldest_str} | {processed_text} processed | ✅ {success_text} | ❌ {failed_text}{eta_print}")
                    if log_progress:
                        logger.info(
                            "Progress update: %s processed (%.3f%% by date) | success=%s | failed=%s%s",
                            processed_text, date_progress, success_text, failed_text,
                            oldest_log + eta_log + rate_log,
                        )
                elif total_target:
                    # Fall back to email count-based progress
                    progress_pct = min((processed_count / total_target) * 100, 100.0)
                    self.show_progress(f"📊 {progress_pct:.3f}%{oldest_str} | {processed_text}/{total_target:,} processed | ✅ {success_text} | ❌ {failed_text}{eta_print}")
                    if log_progress:
                        logger.info(
                            "Progress update: %s/%s processed (%.3f%%) | success=%s | failed=%s%s",
                            processed_text, f'{total_target:,}', progress_pct,
                            success_text, failed_text, oldest_log + eta_log + rate_log,
                        )
                else:
                    # No progress calculation available
                    self.show_progress(f"📊 {processed_text} processed{oldest_str} | ✅ {success_text} | ❌ {failed_text}{eta_print}")
                    if log_progress:
                        logger.info(
                            "Progress update: %s processed | success=%s | failed=%s%s",
                            processed_text, success_text, failed_text,
                            oldest_log + eta_log + rate_log,
                        )

        try:
            with closing(email_iter):
                for email in email_iter:
                    emails_streamed = True
                    process_email(email)
        except BaseException:
            self.finish_progress()
            raise

        if len(self.failed_emails):
            self.finish_progress()
            raise RuntimeError(
                f'Migration incomplete: {len(self.failed_emails):,} unresolved email failures'
            )

        if not emails_streamed and processed_count == 0:
            logger.info("No emails to migrate")
            print("No emails to migrate.")
            return

        # Emit final progress if we didn't hit the exact target boundary
        if processed_count % 10 != 0 and processed_count > 0:
            oldest_str = ""
            oldest_log = ""
            if self.oldest_email_date:
                oldest_time = local_naive(self.oldest_email_date).strftime('%Y-%m-%d %H:%M:%S')
                oldest_str = f" | 📅 Oldest: {oldest_time}"
                oldest_log = f" | oldest={oldest_time}"
            
            # Total successfully processed emails (including previous runs)
            total_success = len(self.processed_emails)
            processed_text = f'{processed_count:,}'
            success_text = f'{total_success:,}'
            failed_text = f'{failed_count:,}'
            
            # Calculate date-based progress if available
            date_progress = self.calculate_date_based_progress(start_date) if use_date_progress else None
            
            if date_progress is not None:
                # Use date-based progress
                self.show_progress(f"📊 {date_progress:.3f}% by date{oldest_str} | {processed_text} processed | ✅ {success_text} | ❌ {failed_text}")
                logger.info(
                    "Progress update: %s processed (%.3f%% by date) | success=%s | failed=%s%s",
                    processed_text, date_progress, success_text, failed_text, oldest_log,
                )
            elif total_target:
                # Fall back to email count-based progress
                progress_pct = min((processed_count / total_target) * 100, 100.0)
                self.show_progress(f"📊 {progress_pct:.3f}%{oldest_str} | {processed_text}/{total_target:,} processed | ✅ {success_text} | ❌ {failed_text}")
                logger.info(
                    "Progress update: %s/%s processed (%.3f%%) | success=%s | failed=%s%s",
                    processed_text, f'{total_target:,}', progress_pct,
                    success_text, failed_text, oldest_log,
                )
            else:
                # No progress calculation available
                self.show_progress(f"📊 {processed_text} processed{oldest_str} | ✅ {success_text} | ❌ {failed_text}")
                logger.info(
                    "Progress update: %s processed | success=%s | failed=%s%s",
                    processed_text, success_text, failed_text, oldest_log,
                )

        self.finish_progress()
        print("-" * 60)
        print("✅ Migration completed!")
        total_success = len(self.processed_emails)
        print(f"📊 Final stats: {total_success:,} successful (total), {failed_count:,} failed out of {processed_count:,} processed this run")

        logger.info("Migration completed")
        logger.info(f"Total processed: {len(self.processed_emails):,}")
        logger.info(f"Total failed: {len(self.failed_emails):,}")
        if self.oldest_email_date:
            logger.info(f"Oldest email processed: {self.oldest_email_date.strftime('%Y-%m-%d %H:%M:%S')} (email ID: {self.oldest_email_id})")
        else:
            logger.info("No email dates were extracted (all emails may have been skipped or failed)")

        # Save final progress
        self.save_progress()
    
    def generate_report(self) -> None:
        """Generate a migration report."""
        # Ensure reports directory exists
        Path('reports').mkdir(exist_ok=True)
        
        gmail_account = self.config.get('gmail_account', 'unknown')
        username = gmail_account.split('@')[0]
        report_file = Path(f'reports/{username}_migration_report.json')
        
        report = {
            'migration_date': datetime.now().isoformat(),
            'gmail_account': gmail_account,
            'group_email': self.config.get('group_email'),
            'config': self.config,
            'total_processed': len(self.processed_emails),
            'total_failed': len(self.failed_emails),
            'state_db': str(self.state.path) if self.state else None,
            'failed_emails': list(self.failed_emails)
        }
        
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"Migration report saved to {report_file}")

def load_config(config_file: str) -> Dict:
    """Load configuration from YAML file."""
    try:
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Error loading config file {config_file}: {e}")
        sys.exit(1)

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Dual Auth Gmail to Google Group Migration')
    parser.add_argument('--config', '-c', help='Configuration file path', default='config.yaml')
    parser.add_argument('--gmail-account', help='Gmail account to migrate from')
    parser.add_argument('--group-email', help='Google Group email to migrate to')
    parser.add_argument('--gmail-credentials', help='Gmail OAuth credentials file', default=None)
    parser.add_argument('--admin-credentials', help='Admin OAuth credentials file', default=None)
    parser.add_argument('--query', help='Gmail search query', default=None)
    parser.add_argument('--batch-size', type=int, help='Batch size for processing', default=None)
    parser.add_argument('--max-emails', type=int, help='Maximum number of emails to process (for testing)', default=None)
    parser.add_argument('--debug', action='store_true', help='Include rate and timing metrics in progress updates')
    parser.add_argument('--force-all', action='store_true',
                        help='Scan the full Gmail query and rebuild the verified range')
    parser.add_argument('--test-mode', action='store_true', help='Test mode: save original and migrated emails to files for debugging')
    
    args = parser.parse_args()
    configure_logging('logs/gmail_migration.log')
    
    # Load configuration
    if os.path.exists(args.config):
        config = load_config(args.config)
    else:
        # Create config from command line arguments (when no config file exists)
        config = {
            'gmail_account': args.gmail_account,
            'group_email': args.group_email,
            'max_emails': args.max_emails
        }
        # Only set values if explicitly provided, otherwise use sensible defaults
        config['gmail_credentials_file'] = args.gmail_credentials if args.gmail_credentials else 'gmail_credentials.json'
        config['admin_credentials_file'] = args.admin_credentials if args.admin_credentials else 'admin_credentials.json'
        config['gmail_query'] = args.query if args.query else 'in:all'
        if args.batch_size is not None:
            config['batch_size'] = args.batch_size
    
    # Override config with command line arguments (if explicitly provided)
    if args.gmail_account:
        config['gmail_account'] = args.gmail_account
    if args.group_email:
        config['group_email'] = args.group_email
    if args.gmail_credentials is not None:
        config['gmail_credentials_file'] = args.gmail_credentials
    if args.admin_credentials is not None:
        config['admin_credentials_file'] = args.admin_credentials
    if args.query is not None:
        config['gmail_query'] = args.query
    if args.batch_size is not None:
        config['batch_size'] = args.batch_size
    if args.max_emails is not None:
        config['max_emails'] = args.max_emails
    if args.debug:
        config['debug'] = True
    if args.force_all:
        config['force_all'] = True
    if args.test_mode:
        config['test_mode'] = True
        config['max_emails'] = 1  # Force test mode to only process 1 email
    
    # Validate required configuration
    required_fields = ['gmail_account', 'group_email', 'gmail_credentials_file', 'admin_credentials_file']
    for field in required_fields:
        if not config.get(field):
            logger.error(f"Missing required configuration: {field}")
            sys.exit(1)
    
    # Create and run migrator
    migrator = DualAuthMigrator(config)
    
    if not migrator.authenticate_both():
        logger.error("Dual authentication failed")
        sys.exit(1)
    
    try:
        migrator.migrate_all_emails()
        migrator.generate_report()
    except KeyboardInterrupt:
        logger.info("Migration interrupted by user")
        migrator.save_progress()
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        migrator.save_progress()
        sys.exit(1)

if __name__ == '__main__':
    main()
