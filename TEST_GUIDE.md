# Test Script Guide

## Test Script Overview

The `test_migration.py` script validates your dual authentication setup for the Gmail to Google Group migration tool.

## Usage

### Command Line Options

```bash
python test_migration.py [OPTIONS]
```

**Options:**
- `--config CONFIG` - Configuration file to test (default: config.yaml)
- `--help` - Show help message

### Makefile Commands

```bash
make test       # Test dual auth setup
make test-dual  # Test dual auth setup (alias)
make test-all   # Test dual auth setup (alias)
```

## Tests Performed

The test script validates:

1. **Dependencies** - Check if required Python packages are installed
2. **Module Imports** - Verify dual auth modules can be imported
3. **Configuration** - Validate config.yaml has required dual auth fields
4. **Gmail Credentials** - Check gmail_credentials.json file format
5. **Gmail Connection** - Test Gmail API authentication
6. **Admin Connection** - Test Admin API authentication

## Required Files

- `config.yaml` (with `gmail_credentials_file` and `admin_credentials_file` fields)
- `gmail_credentials.json` (Gmail OAuth 2.0 credentials)
- `admin_credentials.json` (Admin OAuth 2.0 credentials)

## Configuration Requirements

### Dual Authentication Config

```yaml
gmail_account: "user@yourdomain.com"
group_email: "your-group@yourdomain.com"
gmail_credentials_file: "gmail_credentials.json"
admin_credentials_file: "admin_credentials.json"
domain: "yourdomain.com"
```

## Test Output

### Successful Test Run

```
Starting migration tool tests (Dual Auth)...
==================================================
Testing DUAL AUTHENTICATION setup
------------------------------

Running Dependencies test...
✓ All dependencies are installed
✓ Dependencies test passed

Running Module Imports test...
✓ Dual authentication modules imported successfully
✓ Module Imports test passed

Running Configuration test...
✓ Configuration file is valid
✓ Configuration test passed

Running Gmail Credentials test...
✓ Gmail credentials file is valid
✓ Admin credentials file is valid
✓ All credentials files are valid
✓ Gmail Credentials test passed

Running Gmail Connection test...
✓ Gmail API connection successful
✓ Gmail Connection test passed

Running Admin Connection test...
✓ Admin API connection successful
✓ Admin Connection test passed

==================================================
Test Results: 6/6 tests passed
✓ All tests passed! Migration tool is ready to use.
Run: make dual-auth
```

### Failed Test Run

```
Starting migration tool tests (Dual Auth)...
==================================================
Testing DUAL AUTHENTICATION setup
------------------------------

Running Dependencies test...
✓ All dependencies are installed
✓ Dependencies test passed

Running Module Imports test...
✓ Dual authentication modules imported successfully
✓ Module Imports test passed

Running Configuration test...
✗ Missing required configuration field: gmail_credentials_file
✗ Configuration test failed

Running Gmail Credentials test...
✗ Gmail credentials file 'gmail_credentials.json' not found
✗ Gmail Credentials test failed

==================================================
Test Results: 2/6 tests passed
✗ Some tests failed. Please fix the issues before running the migration.
```

## Troubleshooting

### Common Issues

1. **Missing Configuration Fields**
   - Ensure both `gmail_credentials_file` and `admin_credentials_file` are set in `config.yaml`

2. **Missing Credential Files**
   - Create both `gmail_credentials.json` and `admin_credentials.json`
   - See [DUAL_AUTH_SETUP.md](DUAL_AUTH_SETUP.md) for detailed setup

3. **Authentication Failures**
   - Verify OAuth credentials are valid
   - Check that required APIs are enabled in Google Cloud Console
   - Ensure proper scopes are configured

4. **Import Errors**
   - Run `pip install -r requirements.txt`
   - Check Python version compatibility (3.7+)

## Integration with Makefile

The test script integrates with the Makefile for easy testing:

```bash
# Test dual auth setup
make test

# Run dual auth migration
make dual-auth
```

## Best Practices

1. **Run tests before migration** - Always test your setup before running the actual migration
2. **Check all requirements** - Ensure all required files and configurations are in place
3. **Verify API access** - Make sure your credentials have the necessary permissions
4. **Test with small datasets** - Consider testing with a limited Gmail query first using `--max-emails`

## Next Steps

After successful tests:

1. Run `make dual-auth` to start migration
2. Monitor progress in `logs/gmail_migration.log`
3. Check migration reports in `reports/` directory
4. Verify results in the Google Group
