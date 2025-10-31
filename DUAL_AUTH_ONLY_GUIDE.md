# Dual Auth Only - File Cleanup Guide

This guide helps you identify which files can be safely removed if you're **only using the dual authentication migration approach** (`migrate.py`).

## 🗑️ Files You Can Remove

These files are **not used** by `migrate.py` and can be safely removed:

### Python Scripts (Not Used)
- `main.py` - Single auth migration script (uses different modules)
- `groups_migration.py` - Only used by `main.py`
- `advanced_migration.py` - Only used by `main.py`
- `groups_migration_api.py` - Alternative standalone script
- `service_account_auth.py` - Service account authentication script

### Configuration Files (Not Used)
- `config_service_account.yaml` - Service account configuration template
- `credentials_template.json` - Template for single auth (dual auth uses separate files)

### Documentation (Optional)
- `API_KEY_GUIDE.md` - About API keys (not relevant for OAuth dual auth approach)
  - *Note: You might want to keep this for reference*

## ✅ Files You MUST Keep

These files are **required** for dual auth migration:

### Core Scripts
- `migrate.py` - The main dual auth migration script ⭐
- `batch_migration.py` - Batch migration (uses `migrate.py`)
- `test_migration.py` - Testing script (supports `--dual-auth` flag)

### Configuration
- `config.yaml` - Your configuration file
- `config_sample.yaml` - Configuration template
- `batch_config.yaml` - Batch configuration template (if using batch migration)
- `gmail_credentials.json` - Gmail OAuth credentials ⭐
- `admin_credentials.json` - Admin OAuth credentials ⭐

### Shared Files
- `requirements.txt` - Python dependencies
- `setup.py` - Setup script
- `Makefile` - Convenience commands

### Documentation (Recommended)
- `README.md` - Main documentation
- `DUAL_AUTH_SETUP.md` - Dual auth setup guide ⭐
- `MULTI_USER_GUIDE.md` - Batch migration guide (if using batch)
- `TEST_GUIDE.md` - Testing guide
- `OAUTH_SETUP.md` - OAuth setup reference

### Runtime Files (Generated)
- `admin_token.json` - Auto-generated admin token
- `{username}_gmail_token.json` - User-specific Gmail tokens
- `{username}_migration_progress.json` - Progress tracking files
- `logs/` - Log directory
- `reports/` - Reports directory

## 🧹 Cleanup Script

Use the provided `cleanup_dual_auth_only.sh` script to automatically remove unused files:

```bash
./cleanup_dual_auth_only.sh
```

Or run manually:

```bash
# Remove unused Python scripts
rm -f main.py groups_migration.py advanced_migration.py groups_migration_api.py service_account_auth.py

# Remove unused config files
rm -f config_service_account.yaml credentials_template.json

# Optional: Remove API key guide (keep for reference if unsure)
# rm -f API_KEY_GUIDE.md
```

## 📊 Before Cleanup - File Summary

**Total project files:** ~30 files  
**Files needed for dual auth:** ~20 files  
**Files that can be removed:** ~10 files

## ⚠️ Important Notes

1. **Backup First**: Before removing files, make sure you have a backup or are using version control (git)
2. **Keep Templates**: You might want to keep template files for reference
3. **Documentation**: Consider keeping documentation files for reference, even if not directly used
4. **Test After Cleanup**: Run `make test-dual` after cleanup to ensure everything still works

## 🔍 Verification

After cleanup, verify dual auth still works:

```bash
# Test dual auth setup
make test-dual

# Try a test migration
python migrate.py --config config.yaml --max-emails 1 --test-mode
```

## 📋 Quick Reference

### Minimal Dual Auth Setup Requires:
```
grpmigrate/
├── migrate.py                  # Core script
├── config.yaml                 # Configuration
├── gmail_credentials.json      # Gmail OAuth
├── admin_credentials.json      # Admin OAuth
├── requirements.txt            # Dependencies
├── DUAL_AUTH_SETUP.md          # Setup guide
└── README.md                   # Main docs
```

### Optional but Recommended:
```
├── batch_migration.py          # If doing batch migrations
├── batch_config.yaml           # Batch configuration
├── test_migration.py           # Testing
├── Makefile                    # Convenience commands
└── MULTI_USER_GUIDE.md         # Batch migration guide
```

---

**Last Updated:** 2025-01-20  
**For:** Dual Authentication Migration Only

