# Gmail to Google Group Migration Tool

A comprehensive Python tool for migrating emails from Gmail accounts to Google Groups within the same Google Workspace tenant while preserving all email history, timestamps, threading, and metadata.

## 🎯 Features

- **Complete Email Migration**: Migrates all emails from Gmail to Google Groups with full fidelity
- **Multiple Authentication Methods**: Supports single OAuth, dual OAuth, and service account authentication
- **Batch Processing**: Handle migrations for multiple users efficiently
- **Resume Capability**: Automatically saves progress and can resume interrupted migrations
- **Progress Tracking**: Detailed logging and progress reporting for each user
- **History Preservation**: Maintains original timestamps, threading, and metadata
- **Error Handling**: Robust error handling with retry mechanisms and rate limit management
- **Configurable**: Flexible configuration for different migration scenarios and users

## 📋 Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Authentication Methods](#authentication-methods)
- [Usage](#usage)
- [Configuration](#configuration)
- [Advanced Features](#advanced-features)
- [File Structure](#file-structure)
- [Troubleshooting](#troubleshooting)
- [Additional Guides](#additional-guides)
- [Support](#support)

## 🔧 Prerequisites

### Google Workspace Requirements

1. **Admin Access**: You need Google Workspace Admin privileges for the Groups Migration API
2. **API Access**: Enable the following APIs in Google Cloud Console:
   - Gmail API
   - Admin SDK API (includes Groups Migration API)

### System Requirements

- Python 3.7 or higher
- Google Workspace domain
- OAuth 2.0 credentials or Service Account (see [Authentication Methods](#authentication-methods))

## 📦 Installation

1. **Clone or navigate to the project directory**:
   ```bash
   cd /path/to/grpmigrate
   ```

2. **Run the setup script**:
   ```bash
   python setup.py
   ```

3. **Or install dependencies manually**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Using Makefile** (recommended):
   ```bash
   make setup
   ```

## 🚀 Quick Start

### Single User Migration

1. **Configure authentication** (see [Authentication Methods](#authentication-methods))
2. **Create configuration file**:
   ```bash
   cp config_sample.yaml config.yaml
   # Edit config.yaml with your settings
   ```
3. **Test your setup**:
   ```bash
   make test       # Test dual auth setup
   ```
4. **Run migration**:
   ```bash
   make dual-auth  # Start migration
   ```

### Batch Migration (Multiple Users)

1. **Configure batch settings**:
   ```bash
   cp batch_config.yaml my_batch.yaml
   # Edit my_batch.yaml with your users
   ```
2. **Run batch migration**:
   ```bash
   make batch-migrate
   ```

## 🔐 Authentication Methods

The tool supports three authentication methods:

### 1. Single OAuth (Simple Setup)

Uses one set of OAuth credentials for both Gmail and Admin operations.

**Requirements:**
- One OAuth 2.0 Client ID
- Gmail user must have admin permissions

**Setup:**
- See [OAUTH_SETUP.md](OAUTH_SETUP.md) for detailed instructions

**Usage:**
```bash
python main.py --config config.yaml
```

### 2. Dual OAuth (Recommended)

Uses separate OAuth credentials for Gmail user and Admin operations. This resolves scope conflicts.

**Requirements:**
- Two OAuth 2.0 Client IDs (Gmail and Admin)
- Any Gmail user (doesn't need admin permissions)
- Admin account for group operations

**Setup:**
- See [DUAL_AUTH_SETUP.md](DUAL_AUTH_SETUP.md) for detailed instructions

**Usage:**
```bash
python migrate.py --config config.yaml
# or
make dual-auth
```

### 3. Service Account (Advanced)

Uses a service account with domain-wide delegation for fully automated migrations.

**Requirements:**
- Google Workspace Admin access
- Service Account with domain-wide delegation enabled

**Setup:**
- See [OAUTH_SETUP.md](OAUTH_SETUP.md) for service account setup

**Usage:**
```bash
python service_account_auth.py
```

## 📖 Usage

### Command Line Options

#### Single User Migration

```bash
python main.py --config config.yaml
python migrate.py --config config.yaml

# Command line options:
python main.py \
  --config config.yaml \
  --gmail-account user@domain.com \
  --group-email group@domain.com \
  --credentials credentials.json \
  --query "in:all" \
  --batch-size 10
```

#### Batch Migration

```bash
python batch_migration.py --config batch_config.yaml

# Migrate specific user from batch config:
make batch-user
# Then enter: user@domain.com
```

### Gmail Query Examples

Filter which emails to migrate using Gmail search queries:

```bash
# All emails
--query "in:all"

# Sent emails only
--query "in:sent"

# Specific sender
--query "from:important@example.com"

# Date range
--query "after:2023/01/01 before:2023/12/31"

# Subject contains keyword
--query "subject:important"

# Emails with attachments
--query "has:attachment"

# Combine multiple filters
--query "from:client@example.com after:2023/01/01 has:attachment"
```

## ⚙️ Configuration

### Single User Configuration (`config.yaml`)

```yaml
# Gmail account to migrate emails from
gmail_account: "user@yourdomain.com"

# Google Group email to migrate emails to
group_email: "your-group@yourdomain.com"

# OAuth credentials (choose one method)
credentials_file: "credentials.json"              # Single auth
gmail_credentials_file: "gmail_credentials.json"  # Dual auth
admin_credentials_file: "admin_credentials.json"  # Dual auth

# Gmail search query
gmail_query: "in:all"

# Processing settings
batch_size: 10
batch_delay: 1
max_retries: 3

# Google Workspace domain
domain: "yourdomain.com"

# Migration settings
migration:
  preserve_timestamps: true
  preserve_threading: true
  preserve_labels: true
  max_email_size: 26214400  # 25MB
  skip_oversized_emails: false
```

### Batch Configuration (`batch_config.yaml`)

```yaml
# Global settings (applied to all users)
gmail_credentials_file: "gmail_credentials.json"
admin_credentials_file: "admin_credentials.json"
domain: "yourdomain.com"
gmail_query: "in:all"
batch_size: 10
batch_delay: 1
user_delay: 5  # Delay between users

# List of users to migrate
users:
  - gmail_account: "user1@company.com"
    group_email: "group-user1@company.com"
    
  - gmail_account: "user2@company.com"
    group_email: "group-user2@company.com"
    gmail_query: "after:2023/01/01"  # Override global query
    
  - gmail_account: "user3@company.com"
    group_email: "group-user3@company.com"
    batch_size: 5  # Override global batch size
```

## 🎯 Advanced Features

### Resume Capability

The tool automatically saves progress after each batch. If a migration is interrupted, simply run the same command again to resume:

```bash
# Migration was interrupted
python migrate.py --config config.yaml
# Tool detects progress file and resumes from last checkpoint
```

### User-Specific Token Files

For batch migrations, each user gets their own token and progress files:

```
grpmigrate/
├── hrfr_gmail_token.json          # User-specific tokens
├── hrfr_migration_progress.json   # User-specific progress
├── hrfr_migration_report.json     # User-specific reports
├── john_gmail_token.json
├── john_migration_progress.json
└── ...
```

### Progress Monitoring

Monitor migration progress in real-time:

```bash
# Watch logs
tail -f logs/gmail_migration.log

# Check progress file
cat migration_progress.json

# Check report
cat reports/migration_report.json

# Batch migration monitoring
tail -f logs/batch_migration.log
cat reports/batch_migration_report.json
```

## 📁 File Structure

```
grpmigrate/
├── migrate.py                 # Dual auth migration script (recommended)
├── batch_migration.py         # Batch migration script
├── test_migration.py          # Test script
├── setup.py                   # Setup script
├── Makefile                   # Convenience commands
├── requirements.txt           # Python dependencies
├── config_sample.yaml         # Configuration template
├── batch_config.yaml          # Batch configuration template
├── README.md                  # This file
├── OAUTH_SETUP.md            # OAuth setup guide
├── DUAL_AUTH_SETUP.md        # Dual auth setup guide
├── MULTI_USER_GUIDE.md       # Multi-user migration guide
├── API_KEY_GUIDE.md          # API key information
├── TEST_GUIDE.md             # Testing guide
├── credentials.json          # OAuth credentials (create this)
├── gmail_credentials.json    # Gmail OAuth (dual auth)
├── admin_credentials.json    # Admin OAuth (dual auth)
├── token.json                # Auto-generated tokens
├── migration_progress.json   # Progress tracking
├── migration_report.json     # Migration reports
├── logs/                     # Log files
├── reports/                  # Detailed reports
└── backups/                  # Backup files
```

## 🔍 Troubleshooting

### Common Issues

1. **Authentication Errors**
   - Verify credentials file format is correct
   - Check that OAuth scopes are properly configured
   - Ensure the service account has necessary permissions (if using service account)
   - See [OAUTH_SETUP.md](OAUTH_SETUP.md) for detailed setup

2. **Permission Denied**
   - Verify admin access to Google Workspace
   - Check that required APIs are enabled in Google Cloud Console
   - Ensure the account has Groups Migration API access
   - Try dual auth method if single auth fails

3. **Rate Limiting**
   - Reduce `batch_size` in configuration
   - Increase `batch_delay` between batches
   - Increase `user_delay` for batch migrations
   - The tool automatically handles rate limits with retries

4. **Large Email Issues**
   - Increase `max_email_size` in configuration
   - Enable `skip_oversized_emails` to skip problematic emails
   - Check Gmail API quotas

5. **Token Conflicts (Batch Migrations)**
   - Each user gets their own token file automatically
   - Delete user-specific token files if you need to re-authenticate

### Log Analysis

Check log files for detailed error information:

```bash
# Main log file (single user migration)
cat logs/gmail_migration.log

# Batch migration log
cat logs/batch_migration.log

# Watch logs in real-time
tail -f logs/gmail_migration.log
tail -f logs/batch_migration.log

# Check logs directory
ls -la logs/
```

### Testing

Always test your setup before running migrations:

```bash
# Test dual auth setup
make test
```

## 📚 Additional Guides

For detailed information on specific topics, see these guides:

- **[OAUTH_SETUP.md](OAUTH_SETUP.md)** - Complete OAuth 2.0 setup guide
- **[DUAL_AUTH_SETUP.md](DUAL_AUTH_SETUP.md)** - Dual authentication setup (recommended)
- **[MULTI_USER_GUIDE.md](MULTI_USER_GUIDE.md)** - Multi-user and batch migration guide
- **[DUAL_AUTH_ONLY_GUIDE.md](DUAL_AUTH_ONLY_GUIDE.md)** - Cleanup guide if only using dual auth
- **[API_KEY_GUIDE.md](API_KEY_GUIDE.md)** - API key vs OAuth information
- **[TEST_GUIDE.md](TEST_GUIDE.md)** - Testing and validation guide

## 🛠️ Makefile Commands

Quick reference for common tasks:

```bash
make help              # Show all available commands
make setup             # Complete setup (install + templates)
make install           # Install dependencies only
make test              # Test dual auth setup
make dual-auth         # Run dual auth migration
make batch-migrate     # Run batch migration for all users
make batch-user        # Run batch migration for specific user
make cleanup-dual-auth # Remove files not needed for dual auth only
make clean             # Clean up generated files
make config-help       # Show configuration help
make examples          # Show usage examples
```

## ⚠️ Important Notes

### Data Safety

- **Backup First**: Always backup your data before migration
- **Test Migration**: Test with a small subset of emails first (use `--query` to limit)
- **Verify Results**: Check the migration report after completion
- **Rate Limits**: Be aware of Google API rate limits for large migrations

### Chlimitsations

- Maximum email size: 25MB (including attachments) - adjustable in config
- Rate limits apply to API calls - tool includes automatic retries
- Some email formats may not be perfectly preserved
- Requires Google Workspace admin access for Groups Migration API
- Migration is one-way (Gmail → Group, not reversible)

### Legal Considerations

- Ensure you have permission to migrate emails
- Consider data privacy and retention policies
- Check your organization's data handling requirements
- Comply with applicable laws and regulations

## 🤝 Support

For issues and questions:

1. **Check the guides**: Review the detailed guides in this repository
2. **Review logs**: Check log files for detailed error messages
3. **Verify configuration**: Ensure all settings are correct
4. **Test setup**: Run `make test` to validate your configuration
5. **Check prerequisites**: Verify all APIs are enabled and credentials are valid

## 📄 License

This tool is provided as-is for educational and administrative purposes. Use at your own risk and ensure compliance with your organization's policies and applicable laws.

## 🤝 Contributing

Contributions are welcome! Please ensure any changes:

- Maintain compatibility with existing configuration formats
- Include appropriate tests
- Update relevant documentation
- Follow the existing code style

## 📝 Output Files

The tool creates several output files organized into directories:

### Single User Migration
- `logs/gmail_migration.log` - Detailed migration log
- `migration_progress.json` - Progress tracking (for resume, stored in root)
- `reports/migration_report.json` - Final migration report
- `token.json` - OAuth tokens (auto-generated, stored in root)
- `{username}_gmail_token.json` - User-specific tokens (stored in root)
- `{username}_migration_progress.json` - User-specific progress (stored in root)
- `reports/{username}_migration_report.json` - User-specific reports

### Batch Migration
- `logs/batch_migration.log` - Batch migration log
- `reports/batch_migration_report.json` - Overall batch report
- `{username}_gmail_token.json` - User-specific tokens (stored in root)
- `{username}_migration_progress.json` - User-specific progress (stored in root)
- `reports/{username}_migration_report.json` - User-specific reports

**Note:** Progress files and tokens are kept in the root directory for easy access and resume functionality. Logs and reports are organized into `logs/` and `reports/` directories respectively.

---

**Ready to start?** Begin with the [Quick Start](#quick-start) section or check out the [DUAL_AUTH_SETUP.md](DUAL_AUTH_SETUP.md) guide for the recommended setup method.
