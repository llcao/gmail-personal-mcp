# gmail-personal-mcp

A **privacy-first, local Stdio MCP server and daily triage assistant** for personal Gmail accounts (`@gmail.com`, including university or work emails forwarded to Gmail).

Built for busy people—especially **university faculty, researchers, and conference chairs**—who want a systematic way to:
1. **Review their inbox every morning** without noise;
2. **Catch important or "read-but-unreplied" emails** before deadlines slip;
3. **Automatically prepare threaded reply drafts in Gmail Drafts**, so you simply review and click **Send**;
4. **Organize cluttered Gmail labels & filters** using ready-made presets.

> **Safety & Privacy First**
> - **100% Local**: Connects directly from your machine to Google's Gmail API (`gmail.googleapis.com`). Zero third-party cloud middleman.
> - **Draft-Only, Never Auto-Send**: Only uses `gmail.readonly` + `gmail.compose`. There is **no code to send, delete, or trash emails**.
> - **Zero Extra MCP SDK Bloat**: Pure Python standard-library JSON-RPC 2.0 Stdio MCP server (`requirements.txt` has only 3 lightweight packages).
> - **Works on Personal `@gmail.com` Accounts**: Avoids the `403 Forbidden` Google Workspace Developer Preview gate on hosted remote MCP endpoints, and automatically detects system HTTP proxies.

---

## What You Need (Prerequisites)

- **Python 3.10+**
- A **Gmail account** (`@gmail.com`) — if your university/company uses Outlook/Exchange, simply enable auto-forwarding to Gmail.
- A free **Google Cloud (GCP) OAuth Desktop `credentials.json`** (~3 minutes to create, no credit card needed; see [docs/GCP_SETUP.md](docs/GCP_SETUP.md)).
- This repo has been tested with **Google Antigravity** (`agy` CLI). But very likely other  MCP-compatible AI client like Claude Code/Codex/Cursor can also work.


---

## Quick Start (5 Minutes)

### 1. Clone and run the interactive setup wizard
```bash
git clone https://github.com/llcao/gmail-personal-mcp.git
cd gmail-personal-mcp
python3 setup_wizard.py
```
The wizard automatically:
- Creates `.venv` and installs the 3 lightweight dependencies;
- Lets you pick a preset (`personal`, `university_faculty`, or `conference_chair`);
- Generates your local `config.json` (gitignored) and `gmail_filters.xml` (gitignored);
- Prints the exact `mcp_config.json` snippet for your machine.

### 2. (Optional) Import your personalized Gmail Filters
In Gmail web UI: **Settings → See all settings → Filters and Blocked Addresses → Import filters** → select the generated `gmail_filters.xml`.

Recommended Gmail UI settings for maximum clarity:
- Keep only **Primary** (and optionally **Promotions** as an ad quarantine); turn off **Updates**, **Forums**, and **Social**.
- Hide **Important** markers (AI auto-markers are noisy); use **Starred ⭐** for active to-dos and **Snoozed ⏰** for future deadlines.

### 3. Add Google OAuth `credentials.json` & authorize once
Follow [docs/GCP_SETUP.md](docs/GCP_SETUP.md) (3 minutes) to download `credentials.json` into this folder, then run:
```bash
chmod 600 credentials.json
./.venv/bin/python gmail_triage.py auth
```
This opens your browser once to grant `gmail.readonly` + `gmail.compose` access and **automatically generates your local `token.json`** (set to `chmod 600` and gitignored — you do not need to create `token.json` manually).

### 4. Register the MCP Server in your AI client
Add the snippet printed by `setup_wizard.py` to your client's MCP config:
- **Antigravity CLI / App**: `~/.gemini/config/mcp_config.json`
- **Claude Desktop**: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "gmail": {
      "command": "/absolute/path/to/gmail-personal-mcp/.venv/bin/python",
      "args": [
        "/absolute/path/to/gmail-personal-mcp/gmail_mcp_server.py"
      ]
    }
  }
}
```
Restart your AI client, and you can immediately ask:
- *"Check my inbox for missed urgent or read-but-unreplied emails over the last 3 days."*
- *"Search my emails for reimbursement follow-ups and draft replies in Gmail Drafts."*
- *"Audit my Gmail labels and suggest a cleanup plan."*

---

## Built-In MCP Tools (7)

| Tool | Purpose | Safety |
| --- | --- | --- |
| `daily_triage_brief` | Scans recent threads using `config.json` rules: priority topic, deadlines/VIPs, **read-but-unreplied direct threads**, and unread primary emails | Read-only |
| `search_threads` | Searches Gmail using standard query syntax; strips forwarded `CAUTION: External email` banners | Read-only |
| `get_thread` | Reads full plain-text conversation history for a thread | Read-only |
| `list_labels` | Lists Gmail labels with total & unread counts | Read-only |
| `list_drafts` | Lists current drafts (prevents duplicate drafts) | Read-only |
| `create_draft` | Saves a properly threaded reply draft (`Re:`, `In-Reply-To`, `References`) into Gmail Drafts | Draft-only (never sends) |
| `update_draft` | Updates an existing draft in-place by `draft_id` | Draft-only (never sends) |

---

## Included Role Presets (`presets/`)

| Preset | Designed For | Highlights |
| --- | --- | --- |
| `presets/personal.json` | Everyday personal Gmail | Clean urgency detection, finance/receipt labeling, silent newsletter archiving |
| `presets/university_faculty.json` | University professors / PIs | Separates `1-Dept-Admin`, `Grants`, `Hiring-PhD-RA` (auto-archived for weekly batch review), and `Campus-Notices`; strips university forwarding banners; deprioritizes noisy journal alerts |
| `presets/conference_chair.json` | Conference Program / Area Chairs | Top-priority tracking for desk-reject appeals, ethics/anonymity alerts, and internal PC lists; ignores `Ticket Received -` auto-acks; includes a "please copy Help Desk" draft template (never CCs Help Desk on authors' behalf) |

---

## Optional: Daily 07:00 macOS Auto-Check (Wake-on-Sleep)

On macOS, `launchd` automatically runs missed jobs immediately after your Mac wakes from sleep:
```bash
chmod +x scripts/run_daily_check.sh scripts/install_launchagent.sh
./scripts/install_launchagent.sh 7 0
```
Each morning at 07:00 (or right after wake-up), it waits for Wi-Fi/proxy, runs `gmail_triage.py fetch`, writes `reports/YYYY-MM-DD_digest.md`, and shows a macOS Notification Center summary.

---

## Project Structure

```text
gmail-personal-mcp/
├── gmail_triage.py          # Core Gmail REST client, triage engine, label auditor & filter XML exporter
├── gmail_mcp_server.py      # Zero-dependency Stdio MCP server (7 tools)
├── setup_wizard.py          # Interactive setup & filter generator
├── presets/                 # Ready-to-use JSON presets (personal, university_faculty, conference_chair)
├── scripts/                 # Optional macOS launchd 07:00 wake-on-sleep runner
├── docs/
│   ├── GCP_SETUP.md         # Step-by-step Google Cloud OAuth setup & troubleshooting
│   └── WHY_LOCAL_MCP.md     # Architecture notes: why local Stdio MCP vs. hosted remote MCP & scripts
└── requirements.txt
```

## Security Reminder
- Never commit `credentials.json` (your GCP Desktop app ID), `token.json` (your auto-generated local OAuth token), `config.json`, `gmail_filters.xml`, or `reports/` — all of them are ignored by `.gitignore` by default.
- You can revoke access at any time from [Google Account Permissions](https://myaccount.google.com/permissions).

## License
MIT (see [LICENSE](LICENSE)).
