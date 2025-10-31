#!/usr/bin/env python3
"""
Dual Authentication Gmail to Google Group Migration Tool

This version handles two separate OAuth authentications:
1. Gmail user authentication (for reading emails)
2. Google Workspace admin authentication (for group migration)

This resolves the scope conflict issue.
"""

import argparse
import json
import logging
import os
import sys
import time
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Configure logging
# Ensure logs directory exists
Path('logs').mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/gmail_migration.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

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
        'https://www.googleapis.com/auth/apps.groups.migration'  # Groups Migration API scope
    ]
    
    def __init__(self, config: Dict):
        """Initialize the dual authentication migrator."""
        self.config = config
        self.gmail_service = None
        self.admin_service = None
        self.groups_migration_service = None  # Groups Migration API service
        self.gmail_credentials = None
        self.admin_credentials = None
        self.processed_emails = set()
        self.failed_emails = []
        
        # Create user-specific progress file
        gmail_account = config.get('gmail_account', 'unknown')
        username = gmail_account.split('@')[0]
        self.progress_file = Path(f'{username}_migration_progress.json')
        
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
            self.gmail_service = build('gmail', 'v1', credentials=creds)
            
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
            self.admin_service = build('admin', 'directory_v1', credentials=creds)
            
            # Build Groups Migration API service
            self.groups_migration_service = build('groupsmigration', 'v1', credentials=creds)
            
            logger.info("Successfully authenticated with Admin SDK and Groups Migration API")
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
        """Load migration progress from previous runs."""
        if self.progress_file.exists():
            try:
                with open(self.progress_file, 'r') as f:
                    progress_data = json.load(f)
                    self.processed_emails = set(progress_data.get('processed_emails', []))
                    self.failed_emails = progress_data.get('failed_emails', [])
                    logger.info(f"Loaded progress: {len(self.processed_emails)} emails processed, {len(self.failed_emails)} failed")
            except Exception as e:
                logger.warning(f"Could not load progress file: {e}")
    
    def save_progress(self) -> None:
        """Save current migration progress."""
        progress_data = {
            'processed_emails': list(self.processed_emails),
            'failed_emails': self.failed_emails,
            'last_updated': datetime.now().isoformat()
        }
        
        try:
            with open(self.progress_file, 'w') as f:
                json.dump(progress_data, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save progress: {e}")
    
    def get_all_emails(self) -> List[Dict]:
        """Retrieve emails from the Gmail account."""
        emails = []
        page_token = None
        query = self.config.get('gmail_query', 'in:all')
        max_emails = self.config.get('max_emails')
        
        logger.info(f"Starting to retrieve emails with query: {query}")
        if max_emails:
            logger.info(f"Limiting retrieval to {max_emails} emails")
        
        try:
            while True:
                # Get list of message IDs
                results = self.gmail_service.users().messages().list(
                    userId='me',
                    q=query,
                    pageToken=page_token,
                    maxResults=500
                ).execute()
                
                messages = results.get('messages', [])
                if not messages:
                    break
                
                # Get full message details for each email
                for message in messages:
                    try:
                        msg_id = message['id']
                        
                        # Skip if already processed
                        if msg_id in self.processed_emails:
                            continue
                        
                        # Get full message
                        msg_detail = self.gmail_service.users().messages().get(
                            userId='me',
                            id=msg_id,
                            format='raw'
                        ).execute()
                        
                        emails.append({
                            'id': msg_id,
                            'raw': msg_detail['raw'],
                            'threadId': msg_detail.get('threadId'),
                            'labelIds': msg_detail.get('labelIds', []),
                            'snippet': msg_detail.get('snippet', ''),
                            'sizeEstimate': msg_detail.get('sizeEstimate', 0)
                        })
                        
                        logger.debug(f"Retrieved email {msg_id}")
                        
                        # Stop if we've reached the max_emails limit
                        if max_emails and len(emails) >= max_emails:
                            logger.info(f"Reached max_emails limit ({max_emails}), stopping retrieval")
                            break
                        
                    except HttpError as e:
                        if e.resp.status == 429:  # Rate limit exceeded
                            logger.warning("Rate limit exceeded, waiting 60 seconds...")
                            time.sleep(60)
                            continue
                        else:
                            logger.error(f"Error retrieving message {message['id']}: {e}")
                            self.failed_emails.append({
                                'id': message['id'],
                                'error': str(e),
                                'timestamp': datetime.now().isoformat()
                            })
                
                # Break out of outer loop if we've reached the limit
                if max_emails and len(emails) >= max_emails:
                    break
                    
                page_token = results.get('nextPageToken')
                if not page_token:
                    break
                    
                # Rate limiting
                time.sleep(0.1)
                
        except Exception as e:
            logger.error(f"Error retrieving emails: {e}")
            raise
        
        logger.info(f"Retrieved {len(emails)} emails from Gmail")
        return emails
    
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
    
    def migrate_email_to_group(self, email: Dict) -> bool:
        """
        Migrate a single email to the Google Group using Groups Migration API.
        
        The Groups Migration API preserves all original email metadata, timestamps,
        threading, and content automatically - no need to modify the email.
        """
        try:
            import base64
            from googleapiclient.http import MediaIoBaseUpload
            import io
            
            # Get the raw email content (already base64 encoded from Gmail API)
            # Decode it to bytes for the Groups Migration API
            raw_email_bytes = base64.urlsafe_b64decode(email['raw'])
            
            # TEST MODE: Save original email to file for debugging
            if self.config.get('test_mode', False):
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
            
            # Create media upload for the Groups Migration API
            # The API expects message/rfc822 mimetype with the raw email bytes
            media = MediaIoBaseUpload(
                io.BytesIO(raw_email_bytes),
                mimetype='message/rfc822',
                resumable=True
            )
            
            # Use Groups Migration API to insert the email
            # This preserves all original metadata, timestamps, threading, etc.
            result = self.groups_migration_service.archive().insert(
                groupId=self.config['group_email'],
                media_body=media
            ).execute()
            
            logger.info(f"Successfully migrated email {email['id']} to group {self.config['group_email']}")
            logger.debug(f"Groups Migration API result: {result}")
            
            # Mark as processed
            self.processed_emails.add(email['id'])
            return True
            
        except HttpError as e:
            if e.resp.status == 429:  # Rate limit exceeded
                logger.warning(f"Rate limit exceeded for email {email['id']}, waiting...")
                time.sleep(60)  # Wait 1 minute before retrying
                return self.migrate_email_to_group(email)  # Retry
            elif e.resp.status == 403:
                logger.error(f"Access denied for Groups Migration API: {e}")
                logger.error("Ensure the admin account has Groups Migration API permissions")
                return False
            elif e.resp.status == 404:
                logger.error(f"Group {self.config['group_email']} not found: {e}")
                return False
            else:
                logger.error(f"HTTP error migrating email {email['id']}: {e}")
                return False
        except Exception as e:
            logger.error(f"Failed to migrate email {email['id']}: {e}")
            self.failed_emails.append({
                'id': email['id'],
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            })
            return False
    
    def migrate_all_emails(self) -> None:
        """Migrate all emails from Gmail to Google Group."""
        logger.info("Starting email migration process")
        
        # Verify group access
        if not self.verify_group_access(self.config['group_email']):
            logger.error("Cannot access target Google Group")
            return
        
        # Load previous progress
        self.load_progress()
        
        # Get all emails
        emails = self.get_all_emails()
        
        if not emails:
            logger.info("No emails to migrate")
            return
        
        # Filter out already processed emails FIRST
        emails_to_process = [email for email in emails if email['id'] not in self.processed_emails]
        
        # Apply max_emails limit AFTER filtering (for testing)
        max_emails = self.config.get('max_emails')
        if max_emails:
            emails_to_process = emails_to_process[:max_emails]
            logger.info(f"Limited to {max_emails} emails for testing")
        
        logger.info(f"Processing {len(emails_to_process)} emails (after filtering and limits)")
        
        # Process emails in batches
        batch_size = self.config.get('batch_size', 10)
        total_emails = len(emails_to_process)
        processed_count = 0
        failed_count = 0
        
        print(f"\n🚀 Starting migration of {total_emails} emails...")
        print(f"📧 Target: {self.config['group_email']}")
        print(f"⚙️  Batch size: {batch_size}")
        print("-" * 60)
        
        for i in range(0, len(emails_to_process), batch_size):
            batch = emails_to_process[i:i + batch_size]
            batch_num = i//batch_size + 1
            total_batches = (len(emails_to_process) + batch_size - 1)//batch_size
            
            for email in batch:
                processed_count += 1
                
                # Show progress every 10 emails or at the end
                if processed_count % 10 == 0 or processed_count == total_emails:
                    progress_pct = (processed_count / total_emails) * 100
                    print(f"📊 Progress: {processed_count}/{total_emails} ({progress_pct:.1f}%) | ✅ Success: {processed_count - failed_count} | ❌ Failed: {failed_count}")
                
                success = self.migrate_email_to_group(email)
                if success:
                    # Only log successful migrations at DEBUG level to reduce noise
                    logger.debug(f"Successfully migrated email {email['id']}")
                else:
                    failed_count += 1
                    logger.warning(f"Failed to migrate email {email['id']}")
                
                # Save progress after each email
                self.save_progress()
                
                # Rate limiting
                time.sleep(self.config.get('batch_delay', 1))
        
        print("-" * 60)
        print(f"✅ Migration completed!")
        print(f"📊 Final stats: {processed_count - failed_count} successful, {failed_count} failed out of {total_emails} total")
        
        logger.info("Migration completed")
        logger.info(f"Total processed: {len(self.processed_emails)}")
        logger.info(f"Total failed: {len(self.failed_emails)}")
        
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
            'processed_emails': list(self.processed_emails),
            'failed_emails': self.failed_emails
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
    parser.add_argument('--gmail-credentials', help='Gmail OAuth credentials file', default='gmail_credentials.json')
    parser.add_argument('--admin-credentials', help='Admin OAuth credentials file', default='admin_credentials.json')
    parser.add_argument('--query', help='Gmail search query', default='in:all')
    parser.add_argument('--batch-size', type=int, help='Batch size for processing', default=10)
    parser.add_argument('--max-emails', type=int, help='Maximum number of emails to process (for testing)', default=None)
    parser.add_argument('--test-mode', action='store_true', help='Test mode: save original and migrated emails to files for debugging')
    
    args = parser.parse_args()
    
    # Load configuration
    if os.path.exists(args.config):
        config = load_config(args.config)
    else:
        # Create config from command line arguments
        config = {
            'gmail_account': args.gmail_account,
            'group_email': args.group_email,
            'gmail_credentials_file': args.gmail_credentials,
            'admin_credentials_file': args.admin_credentials,
            'gmail_query': args.query,
            'batch_size': args.batch_size,
            'max_emails': args.max_emails
        }
    
    # Override config with command line arguments (if provided)
    if args.gmail_account:
        config['gmail_account'] = args.gmail_account
    if args.group_email:
        config['group_email'] = args.group_email
    if args.gmail_credentials:
        config['gmail_credentials_file'] = args.gmail_credentials
    if args.admin_credentials:
        config['admin_credentials_file'] = args.admin_credentials
    if args.query:
        config['gmail_query'] = args.query
    if args.batch_size:
        config['batch_size'] = args.batch_size
    if args.max_emails:
        config['max_emails'] = args.max_emails
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

