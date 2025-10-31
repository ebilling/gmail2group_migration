# OAuth 2.0 Setup Guide for Gmail to Google Groups Migration

## Why OAuth 2.0 is Required

The migration tool requires OAuth 2.0 credentials because:

1. **Gmail API** - Needs permission to access user's email account
2. **Admin SDK Groups Migration API** - Needs admin permissions to modify group archives
3. **User Data Access** - API keys cannot access user-specific data

## Step-by-Step OAuth 2.0 Setup

### 1. Google Cloud Console Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable the following APIs:
   - **Gmail API**
   - **Admin SDK API** (includes Groups Migration API)

### 2. Create OAuth 2.0 Credentials

1. Go to **APIs & Services** → **Credentials**
2. Click **Create Credentials** → **OAuth 2.0 Client ID**
3. Choose **Desktop Application** as the application type
4. Give it a name (e.g., "Gmail Migration Tool")
5. Click **Create**

### 3. Download Credentials

1. Click the download button (⬇️) next to your new OAuth client
2. Save the JSON file as `credentials.json` in your migration tool directory
3. The file should look like this:

```json
{
  "installed": {
    "client_id": "your-client-id.apps.googleusercontent.com",
    "project_id": "your-project-id",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_secret": "your-client-secret",
    "redirect_uris": ["http://localhost"]
  }
}
```

### 4. Configure OAuth Consent Screen

1. Go to **APIs & Services** → **OAuth consent screen**
2. Choose **External** (unless you have Google Workspace)
3. Fill in required fields:
   - App name: "Gmail Migration Tool"
   - User support email: Your email
   - Developer contact: Your email
4. Add scopes:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/admin.groups.migration`
5. Add test users (your Gmail account)

## Alternative: Service Account (Advanced)

If you have Google Workspace admin access, you can use a Service Account instead:

### 1. Create Service Account

1. Go to **APIs & Services** → **Credentials**
2. Click **Create Credentials** → **Service Account**
3. Fill in details and create
4. Download the JSON key file

### 2. Enable Domain-wide Delegation

1. In the service account details, enable **Domain-wide delegation**
2. Note the **Client ID**
3. In Google Admin Console, add the Client ID with these scopes:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/admin.groups.migration`

## Using Your API Key (Limited)

If you absolutely must use an API key, you can only use it for:
- Public data access
- Some Admin SDK operations (but not user data)

**You cannot use an API key for:**
- Accessing Gmail user data
- Groups Migration API operations
- Any user-specific operations

## Next Steps

Once you have OAuth 2.0 credentials:

1. Save them as `credentials.json`
2. Update `config.yaml` with your settings
3. Run the migration tool

The tool will automatically handle the OAuth flow when you run it.






