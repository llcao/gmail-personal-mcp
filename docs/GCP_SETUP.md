# Google Cloud (GCP) OAuth Setup Guide (~3 Minutes, Free)

This tool connects directly from your machine to Google's Gmail API (`gmail.googleapis.com`) using your own OAuth Desktop credentials. No credit card is required, and no third-party server ever sees your emails.

> **Why Desktop OAuth + Local Stdio MCP instead of Google's Remote MCP?**  
> Google's hosted Remote Gmail MCP (`gmailmcp.googleapis.com`) currently requires the Google Workspace Developer Preview Program and often returns `403 Forbidden` for personal `@gmail.com` accounts. A local Stdio MCP server avoids that restriction, works directly in CLI clients, respects local HTTP proxies, and keeps your privacy rules 100% under your control.

---

## Step 1: Create a Google Cloud Project
Open: https://console.cloud.google.com/projectcreate
1. Enter a **Project name** (e.g. `Gmail-Personal-MCP`) and click **Create**.
2. Confirm the new project is selected in the top-left project dropdown.

## Step 2: Enable the Gmail API
Open: https://console.cloud.google.com/apis/library/gmail.googleapis.com
- Click **Enable**.  
  *(Skipping this step causes `403 SERVICE_DISABLED` later.)*

## Step 3: Configure OAuth Consent Screen & Add Yourself as a Test User
Open: https://console.cloud.google.com/auth/overview
1. Click **Get started**:
   - **App name**: `Gmail Personal MCP`
   - **User support email**: your Gmail address
   - **Audience**: **External**
   - **Contact Information**: your Gmail address → Agree → **Create**
2. Open **Audience**: https://console.cloud.google.com/auth/audience
   - Scroll to **Test users** → click **+ Add users** → add your own `@gmail.com` address → click **Save**.
   - **Important**: Skipping this causes `Error 403: access_denied (developer-approved testers)`.

## Step 4: Create a Desktop OAuth Client & Download `credentials.json`
Open: https://console.cloud.google.com/auth/clients/create
1. **Application type**: choose **Desktop app** (not Web application).
2. Enter any name (e.g. `My-Laptop`) and click **Create**.
3. Click **Download JSON** in the confirmation dialog.
4. Move and rename the downloaded file to `credentials.json` inside this repository folder, and lock its permissions:
   ```bash
   mv ~/Downloads/client_secret_*.json /path/to/gmail-personal-mcp/credentials.json
   chmod 600 /path/to/gmail-personal-mcp/credentials.json
   ```

## Step 5: Complete One-Time Local Authorization
```bash
cd /path/to/gmail-personal-mcp
./.venv/bin/python gmail_triage.py auth
```
When your browser opens Google's consent page:
1. If Google displays **"Google hasn't verified this app"** (normal for your own personal Testing app), click **Advanced → Go to Gmail Personal MCP (unsafe)**.
2. Verify the scopes (`gmail.readonly` and `gmail.compose` — **no send/delete code exists in this repo**) and click **Allow**.
3. `token.json` is saved locally with `chmod 600`.

---

## Troubleshooting

| Symptom / Error | Cause | Fix |
| --- | --- | --- |
| `Error 403: access_denied` | Your Gmail is not listed under **Test users** | Go to [Audience](https://console.cloud.google.com/auth/audience) → add your email under **Test users** → Save |
| `403 SERVICE_DISABLED` | Gmail API is not enabled in your GCP project | Open [Gmail API Library](https://console.cloud.google.com/apis/library/gmail.googleapis.com) → click **Enable** |
| `invalid_grant` after ~7 days | GCP OAuth apps in **Testing** mode expire refresh tokens every 7 days | Delete `token.json` and re-run `./.venv/bin/python gmail_triage.py auth` (5 seconds) |
| Connection timeout behind Clash / corporate proxy | Standard `httplib2` ignores system proxies | Already fixed here: `gmail_triage.py` uses `requests` (`AuthorizedSession`) + OS proxy auto-detection |

## Revoking Access Anytime
Visit [Google Account Third-Party Connections](https://myaccount.google.com/permissions), remove `Gmail Personal MCP`, and delete `token.json` locally.
