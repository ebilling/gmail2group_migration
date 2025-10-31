# Dual Authentication Setup Guide

## Problem Solved

The original error occurred because:
- Gmail user doesn't have admin permissions for group migration
- Groups Migration API requires admin-level scopes
- Single OAuth flow can't handle both user and admin permissions

## Solution: Dual Authentication

This approach uses **two separate OAuth authentications**:

1. **Gmail Authentication** - For reading emails (user-level)
2. **Admin Authentication** - For group migration (admin-level)

## Setup Steps

### 1. Create Two OAuth 2.0 Credentials

#### Gmail Credentials (User-level)
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create **OAuth 2.0 Client ID** → **Desktop Application**
3. Name it "Gmail Migration Tool - User"
4. Download as `gmail_credentials.json`
5. **Scopes needed**: `https://www.googleapis.com/auth/gmail.readonly`

#### Admin Credentials (Admin-level)
1. In the same project, create another **OAuth 2.0 Client ID**
2. Name it "Gmail Migration Tool - Admin"
3. Download as `admin_credentials.json`
4. **Scopes needed**: 
   - `https://www.googleapis.com/auth/admin.directory.group`
   - `https://www.googleapis.com/auth/admin.directory.group.member`

### 2. Configure OAuth Consent Screen

1. Go to **APIs & Services** → **OAuth consent screen**
2. Choose **External** (unless you have Google Workspace)
3. Fill in required fields:
   - App name: "Gmail Migration Tool"
   - User support email: Your email
   - Developer contact: Your email
4. Add **both sets of scopes**:
   - Gmail scopes
   - Admin scopes
5. Add test users (your Gmail account and admin account)

### 3. Update Configuration

Copy `config_sample.yaml` to `config.yaml` and update:

```yaml
# Gmail account to migrate emails from
gmail_account: "user@yourdomain.com"

# Google Group email to migrate emails to
group_email: "your-group@yourdomain.com"

# Gmail OAuth credentials (for reading emails)
gmail_credentials_file: "gmail_credentials.json"

# Admin OAuth credentials (for group migration)
admin_credentials_file: "admin_credentials.json"
```

### 4. Run the Migration

```bash
python migrate.py --config config.yaml
```

## How It Works

### Authentication Flow

1. **First OAuth Flow**: Authenticates as Gmail user
   - Uses `gmail_credentials.json`
   - Gets permission to read emails
   - Creates `gmail_token.json`

2. **Second OAuth Flow**: Authenticates as Admin
   - Uses `admin_credentials.json`
   - Gets permission to modify groups
   - Creates `admin_token.json`

### Migration Process

1. **Read emails** using Gmail API (user authentication)
2. **Write to group** using Admin SDK (admin authentication)
3. **Track progress** to avoid duplicates
4. **Handle errors** gracefully

## File Structure

```
grpmigrate/
├── migrate.py                  # Main migration script
├── config_sample.yaml          # Configuration template
├── gmail_credentials.json      # Gmail OAuth credentials
├── admin_credentials.json      # Admin OAuth credentials
├── gmail_token.json            # Gmail access token (auto-generated)
├── admin_token.json            # Admin access token (auto-generated)
└── migration_progress.json    # Progress tracking (auto-generated)
```

## Benefits

- ✅ **Solves scope conflict** - Separate authentications for different permissions
- ✅ **More secure** - Each authentication has minimal required permissions
- ✅ **Flexible** - Can use different accounts for Gmail and admin operations
- ✅ **Compliant** - Follows Google's security best practices

## Troubleshooting

### Common Issues

1. **"Invalid scopes" error**
   - Make sure you're using the correct scopes for each credential file
   - Gmail credentials should only have Gmail scopes
   - Admin credentials should only have Admin scopes

2. **"Access denied" error**
   - Verify the admin account has group management permissions
   - Check that the group exists and is accessible

3. **"Group not found" error**
   - Ensure the group email address is correct
   - Verify the admin account can access the group

### Testing

Run the test script to verify setup:

```bash
python test_migration.py
```

This will test both authentications and verify access to Gmail and Google Groups.

## Security Notes

- **Minimal permissions**: Each credential file has only the scopes it needs
- **Separate tokens**: Gmail and admin tokens are stored separately
- **No cross-contamination**: Gmail user can't accidentally modify groups
- **Audit trail**: All operations are logged for transparency





