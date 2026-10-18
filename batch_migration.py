#!/usr/bin/env python3
"""
Batch Gmail to Google Group Migration Tool

This script allows you to migrate multiple Gmail accounts to Google Groups
using the dual authentication approach. Each user gets their own token files
and progress tracking.
"""

import argparse
import json
import logging
import sys
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from migrate import DualAuthMigrator, configure_logging

logger = logging.getLogger(__name__)

class BatchMigrator:
    """Handles batch migration of multiple Gmail accounts to Google Groups."""
    
    def __init__(self, batch_config_file: str, debug: bool = False, force_all: bool = False):
        """Initialize the batch migrator."""
        self.batch_config_file = batch_config_file
        self.batch_config = self.load_batch_config()
        self.debug = debug
        self.force_all = force_all
        self.results = []
        
    def load_batch_config(self) -> Dict:
        """Load batch configuration from YAML file."""
        try:
            with open(self.batch_config_file, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Error loading batch config file {self.batch_config_file}: {e}")
            sys.exit(1)
    
    def migrate_user(self, user_config: Dict) -> Dict:
        """Migrate a single user's emails to their group."""
        gmail_account = user_config.get('gmail_account')
        group_email = user_config.get('group_email')
        
        logger.info(f"Starting migration for {gmail_account} -> {group_email}")
        
        # Create individual config for this user
        individual_config = {
            'gmail_account': gmail_account,
            'group_email': group_email,
            'gmail_credentials_file': self.batch_config.get('gmail_credentials_file', 'gmail_credentials.json'),
            'admin_credentials_file': self.batch_config.get('admin_credentials_file', 'admin_credentials.json'),
            'gmail_query': user_config.get('gmail_query', self.batch_config.get('gmail_query', 'in:all')),
            'gmail_prefetch': user_config.get('gmail_prefetch', self.batch_config.get('gmail_prefetch', 2)),
            'debug': self.debug or user_config.get('debug', self.batch_config.get('debug', False)),
            'force_all': getattr(self, 'force_all', False) or user_config.get(
                'force_all', self.batch_config.get('force_all', False)
            ),
            'state_db': self.batch_config.get('state_db', '.state/migration.sqlite3'),
            'rate_scope': self.batch_config.get('rate_scope', 'groupsmigration'),
            'rate_initial_qps': self.batch_config.get('rate_initial_qps', 5),
            'rate_max_qps': self.batch_config.get('rate_max_qps', 9),
            'max_rate_retries': user_config.get('max_rate_retries', self.batch_config.get('max_rate_retries', 7)),
            'max_retries': user_config.get('max_retries', self.batch_config.get('max_retries', 3)),
            'import_legacy_progress': user_config.get(
                'import_legacy_progress', self.batch_config.get('import_legacy_progress', True)
            ),
            'domain': self.batch_config.get('domain', 'yourdomain.com'),
            'migration': {
                **(self.batch_config.get('migration') or {}),
                **(user_config.get('migration') or {})
            }
        }
        
        # Create migrator for this user
        migrator = DualAuthMigrator(individual_config)
        
        result = {
            'gmail_account': gmail_account,
            'group_email': group_email,
            'status': 'failed',
            'error': None,
            'total_processed': 0,
            'total_failed': 0,
            'start_time': None,
            'end_time': None
        }
        
        try:
            result['start_time'] = datetime.now().isoformat()
            
            # Authenticate
            if not migrator.authenticate_both():
                result['error'] = 'Authentication failed'
                return result
            
            # Verify group access
            if not migrator.verify_group_access(group_email):
                result['error'] = f'Cannot access group {group_email}'
                return result
            
            # Run migration
            migrator.migrate_all_emails()
            
            # Generate report
            migrator.generate_report()
            
            result['status'] = 'success'
            result['total_processed'] = len(migrator.processed_emails)
            result['total_failed'] = len(migrator.failed_emails)
            
            logger.info(f"✅ Migration completed for {gmail_account}: {result['total_processed']:,} processed, {result['total_failed']:,} failed")
            
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"❌ Migration failed for {gmail_account}: {e}")
        
        finally:
            # These counts include checkpointed work from earlier runs, even if
            # this run was interrupted before the normal report was generated.
            result['total_processed'] = len(migrator.processed_emails)
            result['total_failed'] = len(migrator.failed_emails)
            result['end_time'] = datetime.now().isoformat()
        
        return result
    
    def migrate_all_users(self) -> None:
        """Migrate all users in the batch configuration."""
        users = self.batch_config.get('users', [])
        
        if not users:
            logger.error("No users found in batch configuration")
            return
        
        logger.info(f"Starting batch migration for {len(users)} users")
        logger.info("=" * 60)
        
        for i, user_config in enumerate(users, 1):
            logger.info(f"\n[{i}/{len(users)}] Processing user: {user_config.get('gmail_account')}")
            logger.info("-" * 40)
            
            result = self.migrate_user(user_config)
            self.results.append(result)
            
            # Optional pause between users; archive inserts use the shared limiter.
            if i < len(users):
                delay = self.batch_config.get('user_delay', 0)
                if delay > 0:
                    logger.info(f"Waiting {delay} seconds before next user...")
                    import time
                    time.sleep(delay)
        
        # Generate batch report
        self.generate_batch_report()
    
    def generate_batch_report(self) -> None:
        """Generate a batch migration report."""
        # Ensure reports directory exists
        Path('reports').mkdir(exist_ok=True)
        
        report_file = Path('reports/batch_migration_report.json')
        
        successful = [r for r in self.results if r['status'] == 'success']
        failed = [r for r in self.results if r['status'] == 'failed']
        
        total_processed = sum(r['total_processed'] for r in self.results)
        total_failed = sum(r['total_failed'] for r in self.results)
        
        report = {
            'batch_migration_date': datetime.now().isoformat(),
            'batch_config_file': self.batch_config_file,
            'total_users': len(self.results),
            'successful_users': len(successful),
            'failed_users': len(failed),
            'total_emails_processed': total_processed,
            'total_emails_failed': total_failed,
            'user_results': self.results,
            'summary': {
                'successful': [{'gmail_account': r['gmail_account'], 'group_email': r['group_email'], 'processed': r['total_processed']} for r in successful],
                'failed': [{'gmail_account': r['gmail_account'], 'group_email': r['group_email'], 'error': r['error']} for r in failed]
            }
        }
        
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info("\n" + "=" * 60)
        logger.info("BATCH MIGRATION INCOMPLETE" if failed else "BATCH MIGRATION COMPLETED")
        logger.info("=" * 60)
        logger.info(f"Total users: {len(self.results):,}")
        logger.info(f"Successful: {len(successful):,}")
        logger.info(f"Failed: {len(failed):,}")
        logger.info(f"Total emails processed: {total_processed:,}")
        logger.info(f"Total emails failed: {total_failed:,}")
        logger.info(f"Batch report saved to: {report_file}")
        
        if failed:
            logger.info("\nFailed migrations:")
            for result in failed:
                logger.info(f"  - {result['gmail_account']}: {result['error']}")

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Batch Gmail to Google Group Migration')
    parser.add_argument('--config', '-c', required=True, help='Batch configuration file')
    parser.add_argument('--user', help='Migrate specific user only (gmail_account)')
    parser.add_argument('--debug', action='store_true', help='Include rate and timing metrics in progress updates')
    parser.add_argument('--force-all', action='store_true',
                        help='Scan every Gmail result and rebuild verified ranges')
    
    args = parser.parse_args()
    configure_logging('logs/batch_migration.log')
    
    # Create batch migrator
    migrator = BatchMigrator(args.config, debug=args.debug, force_all=args.force_all)
    
    if args.user:
        # Migrate specific user
        users = migrator.batch_config.get('users', [])
        user_config = next((u for u in users if u.get('gmail_account') == args.user), None)
        
        if not user_config:
            logger.error(f"User {args.user} not found in batch configuration")
            sys.exit(1)
        
        logger.info(f"Migrating single user: {args.user}")
        result = migrator.migrate_user(user_config)
        migrator.results.append(result)
        migrator.generate_batch_report()
    else:
        # Migrate all users
        migrator.migrate_all_users()

    if any(result['status'] != 'success' for result in migrator.results):
        sys.exit(1)

if __name__ == '__main__':
    main()





