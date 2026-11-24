#!/usr/bin/env python3
"""
Interactive setup wizard for gmail-personal-mcp.

Creates .venv, installs lightweight dependencies, builds a personalized config.json
from a role preset, exports gmail_filters.xml, and prints the exact MCP config snippet.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PRESETS_DIR = BASE_DIR / "presets"
VENV_DIR = BASE_DIR / ".venv"


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or default


def main() -> None:
    print("\n=== gmail-personal-mcp Interactive Setup ===\n")

    # 1. Create venv & install requirements
    venv_py = VENV_DIR / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not venv_py.exists():
        print("[1/4] Creating Python virtual environment (.venv)...")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
    print("[1/4] Installing dependencies (google-auth, google-auth-oauthlib, requests)...")
    subprocess.check_call([str(venv_py), "-m", "pip", "install", "-q", "-r", str(BASE_DIR / "requirements.txt")])

    # 2. Choose preset
    print("\n[2/4] Choose a starting preset:")
    print("  1) personal            - Clean personal Gmail inbox")
    print("  2) university_faculty  - University Professor / PI (Dept Admin, Grants, Hiring, Campus Notices)")
    print("  3) conference_chair    - Conference Program/Area Chair + Faculty")
    choice = ask("Select preset (1/2/3)", "2")
    preset_map = {"1": "personal", "2": "university_faculty", "3": "conference_chair"}
    preset_name = preset_map.get(choice, "university_faculty")
    cfg = json.loads((PRESETS_DIR / f"{preset_name}.json").read_text(encoding="utf-8"))

    # 3. Personalize config.json (saved locally, gitignored)
    print(f"\n[3/4] Personalizing `{preset_name}` preset (saved locally to config.json, gitignored):")
    gmail_addr = ask("Your personal Gmail address (e.g. alice@gmail.com)", "")
    work_addr = ask("Optional forwarded university/work email (leave blank if none)", "")

    my_emails = [e for e in (gmail_addr, work_addr) if e]
    if my_emails:
        cfg["my_emails"] = my_emails

    if work_addr and "@" in work_addr:
        uni_domain = work_addr.split("@", 1)[1].lower()
        cfg["university_domain"] = uni_domain
        cfg["vip_senders"] = [f"@{uni_domain}"]
    elif preset_name != "personal":
        cfg["university_domain"] = ""
        cfg["vip_senders"] = []

    if preset_name == "conference_chair":
        conf_kw = ask("Conference name keyword (e.g. NeurIPS 2026, ICML 2026)", "CONF2026")
        helpdesk = ask("Official Help Desk email (optional)", "")
        pt = cfg["priority_topic"]
        pt["enabled"] = True
        pt["keywords"] = [conf_kw, "program chair", "program chairs", "freshdesk"]
        if helpdesk:
            pt["helpdesk_email"] = helpdesk
            pt["senders_or_domains"] = [helpdesk]
        else:
            pt["senders_or_domains"] = []

    config_path = BASE_DIR / "config.json"
    config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  -> Wrote {config_path}")

    # Export ready-to-import Gmail filters XML
    subprocess.check_call([str(venv_py), str(BASE_DIR / "gmail_triage.py"), "export-filters"])

    # 4. Print MCP snippet & next steps
    mcp_snippet = {
        "mcpServers": {
            "gmail": {
                "command": str(venv_py),
                "args": [str(BASE_DIR / "gmail_mcp_server.py")],
            }
        }
    }
    print("\n[4/4] Setup files ready!\n")
    print("Next steps:")
    print("  1. (Optional, 0 GCP needed) Import `gmail_filters.xml` in Gmail:")
    print("     Gmail Settings -> See all settings -> Filters and Blocked Addresses -> Import filters.")
    print("  2. Follow `docs/GCP_SETUP.md` (3 min, free) to place `credentials.json` in this folder, then run:")
    print(f"     {venv_py} {BASE_DIR / 'gmail_triage.py'} auth")
    print("  3. Add this snippet to your MCP client config (e.g. ~/.gemini/config/mcp_config.json, Claude Desktop, or Cursor):\n")
    print(json.dumps(mcp_snippet, indent=2))
    print()


if __name__ == "__main__":
    main()
