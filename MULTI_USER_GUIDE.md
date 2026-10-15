# Multi-User Gmail to Google Group Migration Guide

## 🎯 **Overview**

The migration tool supports **user-specific token files**, a shared SQLite checkpoint, and adaptive rate control for multiple Gmail accounts.

## 📁 **File Organization**

### **User-Specific Files**
Each user gets their own files based on their Gmail username:

```
grpmigrate/
├── hrfr_gmail_token.json          # Gmail token for hrfr@e2f.com
├── .state/migration.sqlite3       # Durable progress and rate control
├── hrfr_migration_progress.json   # Legacy import backup, if present
├── reports/hrfr_migration_report.json
├── john_gmail_token.json          # Gmail token for john@company.com
└── ...
```

### **Shared Files**
These files are shared across all users:

```
grpmigrate/
├── gmail_credentials.json         # Gmail OAuth credentials (shared)
├── admin_credentials.json         # Admin OAuth credentials (shared)
├── admin_token.json              # Admin token (shared)
├── batch_config_sample.yaml      # Batch configuration template
├── batch_config.yaml             # Local batch configuration (ignored by Git)
└── batch_migration_report.json   # Overall batch report
```

## 🚀 **Usage Options**

### **Option 1: Single User Migration**

```bash
# Migrate one user
make dual-auth
```

**Files created:**
- `{username}_gmail_token.json`
- `.state/migration.sqlite3` (shared, keyed by source account and target group)
- `{username}_migration_report.json`

### **Option 2: Batch Migration (Multiple Users)**

```bash
# Migrate all users in batch_config.yaml
make batch-migrate

# Migrate specific user from batch config
make batch-user
# Then enter: hrfr@e2f.com
```

**Files created:**
- `{username}_gmail_token.json` for each user
- `.state/migration.sqlite3` for all local runs
- `{username}_migration_report.json` for each user
- `batch_migration_report.json` (overall summary)

## ⚙️ **Configuration**

### **Single User Config (`config.yaml`)**

```yaml
gmail_account: "hrfr@e2f.com"
group_email: "group-hrfr@trustscale.ai"
gmail_credentials_file: "gmail_credentials.json"
admin_credentials_file: "admin_credentials.json"
gmail_query: "in:all"
state_db: ".state/migration.sqlite3"
rate_initial_qps: 5
rate_max_qps: 9
max_rate_retries: 7
domain: "trustscale.ai"
```

### **Batch Config (`batch_config.yaml`)**

```yaml
# Global settings
gmail_credentials_file: "gmail_credentials.json"
admin_credentials_file: "admin_credentials.json"
domain: "trustscale.ai"
gmail_query: "in:all"
state_db: ".state/migration.sqlite3"
rate_initial_qps: 5
rate_max_qps: 9
max_rate_retries: 7
user_delay: 0  # Optional pause between users

# List of users
users:
  - gmail_account: "hrfr@e2f.com"
    group_email: "group-hrfr@trustscale.ai"
    
  - gmail_account: "john@company.com"
    group_email: "group-john@company.com"
    gmail_query: "after:2023/01/01"  # Override global query
    
  - gmail_account: "jane@company.com"
    group_email: "group-jane@company.com"
```

## 🔄 **Resume Capability**

Each source-account/target-group pair has its own rows in the shared checkpoint, so you can:

1. **Resume individual users**: If one user's migration fails, you can resume just that user
2. **Resume batch migration**: The batch tool skips already completed messages
3. **Mix and match**: Run some users individually, others in batch

### **Resume Examples**

```bash
# Resume specific user
python migrate.py --config config.yaml

# Resume batch (skips completed users)
make batch-migrate
```

## 📊 **Monitoring Progress**

### **Individual User Progress**

```bash
# Check progress for specific user
sqlite3 .state/migration.sqlite3 'SELECT source, target, COUNT(*) FROM processed GROUP BY source, target;'

# Check report for specific user
cat reports/hrfr_migration_report.json
```

### **Batch Progress**

```bash
# Check overall batch report
cat reports/batch_migration_report.json
```

### **Live Monitoring**

```bash
# Watch logs in real-time
tail -f logs/batch_migration.log

# Watch individual user logs
tail -f logs/gmail_migration.log
```

## 🛠️ **Advanced Usage**

### **Custom Queries Per User**

```yaml
users:
  - gmail_account: "user1@company.com"
    group_email: "group-user1@company.com"
    gmail_query: "in:sent"  # Only sent emails
    
  - gmail_account: "user2@company.com"
    group_email: "group-user2@company.com"
    gmail_query: "from:important@client.com"  # Specific sender
    
  - gmail_account: "user3@company.com"
    group_email: "group-user3@company.com"
    gmail_query: "after:2023/01/01 before:2023/12/31"  # Date range
```

### **Lower Rate Ceiling**

```yaml
rate_max_qps: 5  # Set globally for all runs sharing this API quota
users:
  - gmail_account: "heavy-user@company.com"
    group_email: "group-heavy@company.com"
  - gmail_account: "light-user@company.com"
    group_email: "group-light@company.com"
```

## 🔧 **Troubleshooting**

### **Common Issues**

1. **Token Conflicts**: Each user gets their own token file, so no conflicts
2. **Progress Tracking**: Each user has separate progress tracking
3. **Rate Limiting**: Shared adaptive pacing for local runs using one state database
4. **Resume Capability**: Can resume from where you left off

### **File Management**

```bash
# List all user-specific files
ls *_gmail_token.json
ls .state/migration.sqlite3
ls *_migration_report.json

# Clean up specific user
rm hrfr_gmail_token.json reports/hrfr_migration_report.json

# Clean up all user files
make clean
```

## 📈 **Performance Tips**

1. **Rate Ceiling**: Lower `rate_max_qps` if other applications share the quota
2. **Checkpoint**: Keep `.state/migration.sqlite3` to resume safely
3. **Parallel Processing**: Run multiple users simultaneously (different terminals)
4. **Monitoring**: Watch logs for any issues

## 🎯 **Best Practices**

1. **Test First**: Run with a small batch first
2. **Monitor Progress**: Check logs and the SQLite checkpoint regularly
3. **Backup Tokens**: Keep token files safe (they contain access tokens)
4. **Resume Strategy**: Use resume capability for large batches
5. **Error Handling**: Check failed users and retry individually

## 📋 **Example Workflow**

```bash
# 1. Setup
make setup

# 2. Test
make test-dual

# 3. Configure batch
cp batch_config_sample.yaml batch_config.yaml
# Edit batch_config.yaml with your users

# 4. Run batch migration
python batch_migration.py --config batch_config.yaml

# 5. Check results
cat batch_migration_report.json

# 6. Resume any failed users
python migrate.py --config config.yaml
```

This approach makes it easy to manage migrations for many users while keeping everything organized and resumable!





