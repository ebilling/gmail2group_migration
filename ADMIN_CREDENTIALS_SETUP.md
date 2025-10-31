# Admin Credentials Setup Guide

This guide helps you recreate `admin_credentials.json` after it's been deleted.

## Quick Steps

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project (or create a new one)
3. Enable required APIs
4. Create OAuth 2.0 credentials
5. Download the credentials file

## Detailed Instructions

### Step 1: Access Google Cloud Console

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project (the same project used for `gmail_credentials.json`)

### Step 2: Enable Required APIs

Ensure these APIs are enabled:
- **Gmail API**
- **Admin SDK API** (includes Groups Migration API)

**To enable:**
1. Go to **APIs & Services** → **Library**
2. Search for each API
3. Click **Enable** for each one

### Step 3: Create OAuth 2.0 Credentials (Admin)

1. Go to **APIs & Services** → **Credentials**
2. Click **Create Credentials** → **OAuth 2.0 Client ID**
3. If prompted, configure the OAuth consent screen first (see below)
4. Choose **Application type**: **Desktop application**
5. Give it a name: **"Gmail Migration Tool - Admin"**
6. Click **Create**
7. Click the **Download** button (⬇️) next to the new credential
8. Save the file as `admin_credentials.json` in your project directory

### Step 4: Configure OAuth Consent Screen (if needed)

If you haven't configured the consent screen:

1. Go to **APIs & Services** → **OAuth consent screen**
2. Choose **External** (or **Internal** if you have Google Workspace)
3. Fill in required information:
   - **App name**: "Gmail Migration Tool"
   - **User support email**: Your email address
   - **Developer contact**: Your email address
4. Click **Save and Continue**
5. **Add scopes**:
   - `https://www.googleapis.com/auth/admin.directory.group`
   - `https://www.googleapis.com/auth/admin.directory.group.member`
   - `https://www.googleapis.com/auth/apps.groups.migration`
   - `https://www.googleapis.com/auth/gmail.readonly`
6. Click **Save and Continue**
7. **Add test users** (if External):
   - Your Gmail account email
   - Your Admin account email
8. Click **Save and Continue** → **Back to Dashboard**

### Step 5: Verify the File

After downloading, verify `admin_credentials.json` has this structure:

```json
{
  "installed": {
    "client_id": "xxxxx.apps.googleusercontent.com",
    "project_id": "your-project-id",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_secret": "GOCSPX-xxxxx",
    "redirect_uris": ["http://localhost"]
  }
}
```

### Step 6: Test Authentication

Run the test script to verify everything works:

```bash
make test
```

Or test authentication directly:

```bash
python migrate.py --config config.yaml --max-emails 1 --test-mode
```

You'll be prompted to authenticate with your admin account in the browser.

## Important Notes

⚠️ **Security:**
- Keep `admin_credentials.json` secure and never commit it to version control
- If credentials are compromised, revoke them in Google Cloud Console and create new ones

📝 **File Location:**
- Place `admin_credentials.json` in the same directory as `migrate.py`
- Ensure your `config.yaml` points to it: `admin_credentials_file: "admin_credentials.json"`

🔄 **Re-authentication:**
- After creating `admin_credentials.json`, you'll need to authenticate when you first run the migration
- The tool will create `admin_token.json` automatically after successful authentication
- `admin_token.json` can be reused until it expires (then it auto-refreshes)

## Troubleshooting

### "Invalid credentials" error
- Verify the file is named exactly `admin_credentials.json`
- Check that the JSON structure is correct (use a JSON validator)
- Ensure you downloaded it as "Desktop application" type

### "Access denied" error
- Verify the admin account has Google Workspace admin permissions
- Check that required APIs are enabled
- Ensure OAuth consent screen is configured correctly

### "Scope not authorized" error
- Verify all required scopes are added in OAuth consent screen
- Check that test users (if External) include your admin account
- Ensure Groups Migration API is enabled

## Next Steps

After creating `admin_credentials.json`:

1. **Test the setup**: `make test`
2. **Run a test migration**: `python migrate.py --config config.yaml --max-emails 1 --test-mode`
3. **Start full migration**: `make dual-auth`

---

**Need help?** See [DUAL_AUTH_SETUP.md](DUAL_AUTH_SETUP.md) for complete setup instructions.


