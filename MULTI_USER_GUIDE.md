# Multi-User Gmail to Google Group Migration Guide

## 🎯 **Overview**

The migration tool now supports **user-specific token files** and **batch processing** for migrating multiple Gmail accounts to Google Groups efficiently.

## 📁 **File Organization**

### **User-Specific Files**
Each user gets their own files based on their Gmail username:

```
grpmigrate/
├── hrfr_gmail_token.json          # Gmail token for hrfr@e2f.com
├── hrfr_migration_progress.json   # Progress tracking for hrfr
├── hrfr_migration_report.json     # Migration report for hrfr
├── john_gmail_token.json          # Gmail token for john@company.com
├── john_migration_progress.json   # Progress tracking for john
├── john_migration_report.json     # Migration report for john
└── ...
```

### **Shared Files**
These files are shared across all users:

```
grpmigrate/
├── gmail_credentials.json         # Gmail OAuth credentials (shared)
├── admin_credentials.json         # Admin OAuth credentials (shared)
├── admin_token.json              # Admin token (shared)
├── batch_config.yaml             # Batch configuration
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
- `{username}_migration_progress.json`
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
- `{username}_migration_progress.json` for each user
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
batch_size: 10
batch_delay: 1
domain: "trustscale.ai"
```

### **Batch Config (`batch_config.yaml`)**

```yaml
# Global settings
gmail_credentials_file: "gmail_credentials.json"
admin_credentials_file: "admin_credentials.json"
domain: "trustscale.ai"
gmail_query: "in:all"
batch_size: 10
batch_delay: 1
user_delay: 5  # Delay between users

# List of users
users:
  - gmail_account: "hrfr@e2f.com"
    group_email: "group-hrfr@trustscale.ai"
    
  - gmail_account: "john@company.com"
    group_email: "group-john@company.com"
    gmail_query: "after:2023/01/01"  # Override global query
    
  - gmail_account: "jane@company.com"
    group_email: "group-jane@company.com"
    batch_size: 5  # Override global batch size
```

## 🔄 **Resume Capability**

Each user has their own progress file, so you can:

1. **Resume individual users**: If one user's migration fails, you can resume just that user
2. **Resume batch migration**: The batch tool will skip users who are already completed
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
cat hrfr_migration_progress.json

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

### **Different Batch Sizes**

```yaml
users:
  - gmail_account: "heavy-user@company.com"
    group_email: "group-heavy@company.com"
    batch_size: 5  # Smaller batches for heavy users
    
  - gmail_account: "light-user@company.com"
    group_email: "group-light@company.com"
    batch_size: 20  # Larger batches for light users
```

## 🔧 **Troubleshooting**

### **Common Issues**

1. **Token Conflicts**: Each user gets their own token file, so no conflicts
2. **Progress Tracking**: Each user has separate progress tracking
3. **Rate Limiting**: Built-in delays between users and batches
4. **Resume Capability**: Can resume from where you left off

### **File Management**

```bash
# List all user-specific files
ls *_gmail_token.json
ls *_migration_progress.json
ls *_migration_report.json

# Clean up specific user
rm hrfr_gmail_token.json hrfr_migration_progress.json hrfr_migration_report.json

# Clean up all user files
make clean
```

## 📈 **Performance Tips**

1. **Batch Size**: Adjust based on user's email volume
2. **User Delay**: Increase if hitting rate limits
3. **Parallel Processing**: Run multiple users simultaneously (different terminals)
4. **Monitoring**: Watch logs for any issues

## 🎯 **Best Practices**

1. **Test First**: Run with a small batch first
2. **Monitor Progress**: Check logs and progress files regularly
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
cp batch_config.yaml my_batch.yaml
# Edit my_batch.yaml with your users

# 4. Run batch migration
python batch_migration.py --config my_batch.yaml

# 5. Check results
cat batch_migration_report.json

# 6. Resume any failed users
python migrate.py --config config.yaml
```

This approach makes it easy to manage migrations for many users while keeping everything organized and resumable!





