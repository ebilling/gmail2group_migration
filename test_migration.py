#!/usr/bin/env python3
"""
Test script for Gmail to Google Group Migration Tool

This script tests the migration setup and validates configuration
without actually performing the migration. Supports both single
and dual authentication approaches.
"""

import json
import logging
import sys
import argparse
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_configuration(dual_auth=True):
    """Test configuration file loading and validation."""
    logger.info("Testing configuration...")
    
    config_file = Path("config.yaml")
    if not config_file.exists():
        logger.error("Configuration file 'config.yaml' not found")
        return False
    
    try:
        import yaml
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
        
        # Check required fields for dual auth
        required_fields = ['gmail_account', 'group_email', 'gmail_credentials_file', 'admin_credentials_file']
        
        for field in required_fields:
            if not config.get(field):
                logger.error(f"Missing required configuration field: {field}")
                return False
        
        logger.info("✓ Configuration file is valid")
        return True
        
    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        return False

def test_credentials(dual_auth=True):
    """Test OAuth credentials file(s)."""
    logger.info("Testing credentials...")
    
    # Test both Gmail and Admin credentials
    gmail_creds_file = Path("gmail_credentials.json")
    admin_creds_file = Path("admin_credentials.json")
    
    if not gmail_creds_file.exists():
        logger.error("Gmail credentials file 'gmail_credentials.json' not found")
        return False
    
    if not admin_creds_file.exists():
        logger.error("Admin credentials file 'admin_credentials.json' not found")
        return False
    
    # Test Gmail credentials
    try:
        with open(gmail_creds_file, 'r') as f:
            gmail_creds = json.load(f)
        
        if 'installed' not in gmail_creds:
            logger.error("Invalid Gmail credentials format - expected 'installed' type")
            return False
        
        required_fields = ['client_id', 'client_secret']
        for field in required_fields:
            if not gmail_creds['installed'].get(field):
                logger.error(f"Missing required Gmail credentials field: {field}")
                return False
        
        logger.info("✓ Gmail credentials file is valid")
        
    except Exception as e:
        logger.error(f"Error loading Gmail credentials: {e}")
        return False
    
    # Test Admin credentials
    try:
        with open(admin_creds_file, 'r') as f:
            admin_creds = json.load(f)
        
        if 'installed' not in admin_creds:
            logger.error("Invalid Admin credentials format - expected 'installed' type")
            return False
        
        required_fields = ['client_id', 'client_secret']
        for field in required_fields:
            if not admin_creds['installed'].get(field):
                logger.error(f"Missing required Admin credentials field: {field}")
                return False
        
        logger.info("✓ Admin credentials file is valid")
        
    except Exception as e:
        logger.error(f"Error loading Admin credentials: {e}")
        return False
    
    logger.info("✓ All credentials files are valid")
    return True

def test_dependencies():
    """Test if all required dependencies are installed."""
    logger.info("Testing dependencies...")
    
    # Map package names to their actual import names
    required_packages = {
        'google-api-python-client': 'googleapiclient',
        'google-auth-oauthlib': 'google_auth_oauthlib',
        'google-auth': 'google.auth',
        'PyYAML': 'yaml'
    }
    
    missing_packages = []
    
    for package_name, import_name in required_packages.items():
        try:
            __import__(import_name)
            logger.debug(f"✓ {package_name} is installed")
        except ImportError:
            missing_packages.append(package_name)
            logger.error(f"✗ {package_name} is not installed")
    
    if missing_packages:
        logger.error(f"Missing packages: {', '.join(missing_packages)}")
        logger.error("Run: pip install -r requirements.txt")
        logger.error("Or activate virtual environment: source venv/bin/activate")
        return False
    
    logger.info("✓ All dependencies are installed")
    return True

def test_imports(dual_auth=True):
    """Test if all modules can be imported."""
    logger.info("Testing module imports...")
    
    try:
        from migrate import DualAuthMigrator
        logger.info("✓ Dual authentication modules imported successfully")
        return True
    except ImportError as e:
        logger.error(f"Import error: {e}")
        return False

def test_gmail_connection(dual_auth=True):
    """Test Gmail API connection (requires valid credentials)."""
    logger.info("Testing Gmail API connection...")
    
    try:
        import yaml
        
        # Load config
        with open('config.yaml', 'r') as f:
            config = yaml.safe_load(f)
        
        from migrate import DualAuthMigrator
        # Create dual auth migrator
        migrator = DualAuthMigrator(config)
        
        # Test Gmail authentication only
        if migrator.authenticate_gmail():
            logger.info("✓ Gmail API connection successful")
            return True
        else:
            logger.error("✗ Gmail API authentication failed")
            return False
            
    except Exception as e:
        logger.error(f"Gmail API connection test failed: {e}")
        return False

def test_admin_connection():
    """Test Admin API connection (requires valid admin credentials)."""
    logger.info("Testing Admin API connection...")
    
    try:
        from migrate import DualAuthMigrator
        import yaml
        
        # Load config
        with open('config.yaml', 'r') as f:
            config = yaml.safe_load(f)
        
        # Create dual auth migrator
        migrator = DualAuthMigrator(config)
        
        # Test admin authentication
        if migrator.authenticate_admin():
            logger.info("✓ Admin API connection successful")
            return True
        else:
            logger.error("✗ Admin API authentication failed")
            return False
            
    except Exception as e:
        logger.error(f"Admin API connection test failed: {e}")
        return False

def main():
    """Run all tests."""
    parser = argparse.ArgumentParser(description='Test Gmail to Google Group Migration Tool (Dual Auth)')
    parser.add_argument('--config', '-c', default='config.yaml',
                       help='Configuration file to test (default: config.yaml)')
    
    args = parser.parse_args()
    
    logger.info("Starting migration tool tests (Dual Auth)...")
    logger.info("=" * 50)
    
    logger.info("Testing DUAL AUTHENTICATION setup")
    logger.info("-" * 30)
    
    tests = [
        ("Dependencies", test_dependencies),
        ("Module Imports", lambda: test_imports(dual_auth=True)),
        ("Configuration", lambda: test_configuration(dual_auth=True)),
        ("Gmail Credentials", lambda: test_credentials(dual_auth=True)),
        ("Gmail Connection", lambda: test_gmail_connection(dual_auth=True)),
        ("Admin Connection", test_admin_connection)
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        logger.info(f"\nRunning {test_name} test...")
        try:
            if test_func():
                passed += 1
                logger.info(f"✓ {test_name} test passed")
            else:
                logger.error(f"✗ {test_name} test failed")
        except Exception as e:
            logger.error(f"✗ {test_name} test failed with exception: {e}")
    
    logger.info("\n" + "=" * 50)
    logger.info(f"Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        logger.info("✓ All tests passed! Migration tool is ready to use.")
        logger.info("Run: make dual-auth")
        return True
    else:
        logger.error("✗ Some tests failed. Please fix the issues before running the migration.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
