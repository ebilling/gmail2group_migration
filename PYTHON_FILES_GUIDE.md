# Python Files Guide

This document explains the purpose of each Python (`.py`) file in the Gmail to Google Group migration project.

## 📁 Core Migration Scripts

### `migrate.py` ⭐ **Main Script**
**Purpose:** The primary migration script that migrates emails from a single Gmail account to a Google Group.

**Key Features:**
- Dual authentication (separate Gmail user and Admin authentications)
- Uses Groups Migration API (preserves original email metadata)
- Progress tracking with resume capability
- Batch processing with rate limiting
- User-specific token and progress files
- Detailed logging and reporting

**Usage:**
```bash
python migrate.py --config config.yaml
# or
make dual-auth
```

**Class:** `DualAuthMigrator` - Handles the complete migration process

**What it does:**
1. Authenticates with Gmail API (user credentials)
2. Authenticates with Admin SDK (admin credentials)
3. Retrieves emails from Gmail account
4. Migrates emails to Google Group using Groups Migration API
5. Tracks progress and generates reports

---

### `batch_migration.py` 📦 **Batch Processing**
**Purpose:** Migrates multiple Gmail accounts to their respective Google Groups in a single run.

**Key Features:**
- Processes multiple users from a batch configuration file
- Uses `migrate.py` internally via `DualAuthMigrator` class
- User-specific token files (prevents conflicts)
- Individual progress tracking per user
- Overall batch report generation
- Can resume from where it left off

**Usage:**
```bash
python batch_migration.py --config batch_config.yaml
# or
make batch-migrate
```

**Class:** `BatchMigrator` - Orchestrates multiple user migrations

**What it does:**
1. Loads batch configuration (`batch_config.yaml`)
2. Iterates through each user in the config
3. Creates a `DualAuthMigrator` instance for each user
4. Runs migration for each user
5. Collects results and generates batch report
6. Handles delays between users to avoid rate limits

---

## 🧪 Testing & Validation

### `test_migration.py` ✔️ **Test Suite**
**Purpose:** Validates the migration setup and configuration without performing actual migration.

**Key Features:**
- Tests all dependencies are installed
- Validates module imports
- Checks configuration file format
- Verifies OAuth credentials files
- Tests Gmail API connection
- Tests Admin API connection

**Usage:**
```bash
python test_migration.py --config config.yaml
# or
make test
```

**Test Functions:**
- `test_dependencies()` - Checks required Python packages
- `test_imports()` - Verifies modules can be imported
- `test_configuration()` - Validates config.yaml format
- `test_credentials()` - Checks OAuth credential files
- `test_gmail_connection()` - Tests Gmail API authentication
- `test_admin_connection()` - Tests Admin API authentication

**What it does:**
1. Runs all validation tests in sequence
2. Reports which tests pass/fail
3. Provides clear error messages for failures
4. Confirms setup is ready before migration

---

## 🛠️ Setup & Utilities

### `setup.py` ⚙️ **Initial Setup**
**Purpose:** Automated setup script that prepares the project for first use.

**Key Features:**
- Installs Python dependencies from `requirements.txt`
- Creates necessary directories (`logs/`, `reports/`, `backups/`)
- Creates credential templates
- Creates sample configuration files

**Usage:**
```bash
python setup.py
# or
make setup
```

**Functions:**
- `install_requirements()` - Installs all required packages
- `create_directories()` - Creates logs, reports, backups directories
- `create_credentials_template()` - Creates `credentials_template.json`
- `create_sample_config()` - Creates sample configuration

**What it does:**
1. Installs all dependencies from requirements.txt
2. Creates directory structure
3. Generates template files for easy setup
4. Provides next steps guidance

---

## 📊 File Relationships

```
migrate.py (Core)
    ↓ imports
    DualAuthMigrator class
    ↓ used by
batch_migration.py (Batch Processor)
    ↓ uses
    DualAuthMigrator instances

test_migration.py (Validator)
    ↓ tests
    migrate.py functionality
    ↓ validates
    Configuration, credentials, connections

setup.py (Installer)
    ↓ prepares
    Project environment
    ↓ creates
    Directories, templates, configs
```

## 🎯 When to Use Each File

| Scenario | Use This File |
|----------|---------------|
| **First time setup** | `setup.py` |
| **Validate your setup** | `test_migration.py` |
| **Migrate one user's emails** | `migrate.py` |
| **Migrate multiple users** | `batch_migration.py` |
| **Resume interrupted migration** | `migrate.py` (automatically resumes) |

## 📝 Summary

- **`migrate.py`** - The main workhorse, does the actual email migration
- **`batch_migration.py`** - Wraps `migrate.py` to handle multiple users
- **`test_migration.py`** - Validates everything is configured correctly
- **`setup.py`** - One-time setup to prepare the environment

All scripts work together to provide a complete migration solution with validation, setup, and execution capabilities.

---

**Last Updated:** 2025-01-20


