# Gmail to Google Group Migration Tool Makefile

.PHONY: help install setup test test-dual test-all run clean dual-auth batch-migrate batch-user

# Detect Python - use venv if available, otherwise system Python
PYTHON := $(shell if [ -f venv/bin/python ]; then echo venv/bin/python; else echo python3; fi)

# Default target
help:
	@echo "Gmail to Google Group Migration Tool"
	@echo "===================================="
	@echo ""
	@echo "Available targets:"
	@echo "  install       - Install Python dependencies"
	@echo "  setup         - Run complete setup (install + create templates)"
	@echo "  test          - Run tests (dual auth)"
	@echo "  test-dual     - Run tests (dual auth, alias)"
	@echo "  test-all      - Run tests (dual auth, alias)"
	@echo "  dual-auth     - Run the dual authentication migration tool"
	@echo "  batch-migrate  - Run batch migration for multiple users"
	@echo "  batch-user     - Run batch migration for a specific user"
	@echo "  cleanup-dual-auth - Remove files not needed for dual auth only"
	@echo "  clean         - Clean up generated files"
	@echo "  help          - Show this help message"
	@echo ""
	@echo "Usage:"
	@echo "  make setup         # First time setup"
	@echo "  make test          # Test dual auth setup"
	@echo "  make dual-auth     # Start migration (single user)"
	@echo "  make batch-migrate # Start batch migration (multiple users)"
	@echo "  make batch-user    # Start batch migration (specific user)"

# Install Python dependencies
install:
	@echo "Installing Python dependencies..."
	@echo "Using Python: $(PYTHON)"
	$(PYTHON) -m pip install -r requirements.txt

# Run complete setup
setup: install
	@echo "Running complete setup..."
	@echo "Using Python: $(PYTHON)"
	$(PYTHON) setup.py

# Run tests (dual auth - default)
test:
	@echo "Running tests (dual auth)..."
	@echo "Using Python: $(PYTHON)"
	$(PYTHON) test_migration.py

# Run tests (dual auth - alias)
test-dual:
	@echo "Running tests (dual auth)..."
	@echo "Using Python: $(PYTHON)"
	$(PYTHON) test_migration.py

# Run all tests (same as test for dual auth only)
test-all:
	@echo "Running tests (dual auth)..."
	@echo "Using Python: $(PYTHON)"
	$(PYTHON) test_migration.py

# Run the dual authentication migration tool
dual-auth:
	@echo "Starting dual authentication migration..."
	@echo "Using Python: $(PYTHON)"
	$(PYTHON) migrate.py --config config.yaml

# Run batch migration for multiple users
batch-migrate:
	@echo "Starting batch migration..."
	@echo "Using Python: $(PYTHON)"
	$(PYTHON) batch_migration.py --config batch_config.yaml

# Run batch migration for a specific user
batch-user:
	@echo "Starting batch migration for specific user..."
	@read -p "Enter Gmail account: " user; \
	$(PYTHON) batch_migration.py --config batch_config.yaml --user $$user

# Clean up generated files
clean:
	@echo "Cleaning up..."
	rm -f token.json
	rm -f gmail_token.json
	rm -f admin_token.json
	rm -f *_gmail_token.json
	rm -f *_migration_progress.json
	rm -f *_migration_report.json
	rm -f migration_progress.json
	rm -f migration_report.json
	rm -f batch_migration_report.json
	# Clean up old log files from root (if any exist - legacy files)
	rm -f gmail_migration.log
	rm -f batch_migration.log
	# Clean up test artifacts
	rm -f original_email.txt
	rm -f migrated_email.txt
	# Clean up directories
	rm -rf logs/
	rm -rf reports/
	rm -rf backups/

# Create necessary directories
dirs:
	@mkdir -p logs reports backups

# Show configuration help
config-help:
	@echo "Configuration Help"
	@echo "=================="
	@echo ""
	@echo "1. Copy config_sample.yaml to config.yaml"
	@echo "2. Update the following settings in config.yaml:"
	@echo "   - gmail_account: Your Gmail address"
	@echo "   - group_email: Target Google Group address"
	@echo "   - domain: Your Google Workspace domain"
	@echo "3. Download OAuth credentials from Google Cloud Console"
	@echo "4. Save credentials as 'credentials.json'"
	@echo ""
	@echo "Required Google Cloud Console setup:"
	@echo "- Enable Gmail API"
	@echo "- Enable Admin SDK API"
	@echo "- Create OAuth 2.0 credentials (Desktop application)"
	@echo "- Download credentials JSON file"

# Show usage examples
examples:
	@echo "Usage Examples"
	@echo "=============="
	@echo ""
	@echo "Basic migration (dual auth):"
	@echo "  make dual-auth"
	@echo ""
	@echo "Migrate specific emails:"
	@echo "  python migrate.py --query 'from:important@example.com'"
	@echo ""
	@echo "Migrate emails from date range:"
	@echo "  python migrate.py --query 'after:2023/01/01 before:2023/12/31'"
	@echo ""
	@echo "Custom configuration:"
	@echo "  python migrate.py --config my_config.yaml"
	@echo ""
	@echo "Test setup:"
	@echo "  make test"

# Cleanup unused files (dual auth only)
cleanup-dual-auth:
	@echo "Running cleanup for dual auth only setup..."
	@./cleanup_dual_auth_only.sh
