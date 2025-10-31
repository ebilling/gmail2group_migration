#!/usr/bin/env python3
"""
Setup script for Gmail to Google Group Migration Tool
"""

import os
import sys
import subprocess
from pathlib import Path

def install_requirements():
    """Install required Python packages."""
    print("Installing required packages...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("✓ Requirements installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to install requirements: {e}")
        return False

def create_credentials_template():
    """Create a template for OAuth credentials."""
    credentials_template = {
        "installed": {
            "client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
            "project_id": "your-project-id",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": "YOUR_CLIENT_SECRET",
            "redirect_uris": ["http://localhost"]
        }
    }
    
    credentials_file = Path("credentials_template.json")
    if not credentials_file.exists():
        import json
        with open(credentials_file, "w") as f:
            json.dump(credentials_template, f, indent=2)
        print(f"✓ Created credentials template: {credentials_file}")
        print("  Please download your actual credentials from Google Cloud Console")
        print("  and save them as 'credentials.json'")
    else:
        print("✓ Credentials template already exists")

def create_sample_config():
    """Create a sample configuration file."""
    config_file = Path("config_sample.yaml")
    if not config_file.exists():
        import shutil
        shutil.copy("config.yaml", config_file)
        print(f"✓ Created sample configuration: {config_file}")
        print("  Please copy this to 'config.yaml' and update with your settings")
    else:
        print("✓ Sample configuration already exists")

def create_directories():
    """Create necessary directories."""
    directories = ["logs", "backups", "reports"]
    for directory in directories:
        Path(directory).mkdir(exist_ok=True)
        print(f"✓ Created directory: {directory}")

def main():
    """Main setup function."""
    print("Setting up Gmail to Google Group Migration Tool...")
    print("=" * 50)
    
    # Install requirements
    if not install_requirements():
        print("Setup failed: Could not install requirements")
        return False
    
    # Create directories
    create_directories()
    
    # Create template files
    create_credentials_template()
    create_sample_config()
    
    print("\n" + "=" * 50)
    print("Setup completed successfully!")
    print("\nNext steps:")
    print("1. Download OAuth credentials from Google Cloud Console")
    print("2. Save credentials as 'credentials.json'")
    print("3. Copy 'config_sample.yaml' to 'config.yaml'")
    print("4. Update 'config.yaml' with your settings")
    print("5. Run: python main.py --config config.yaml")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)






