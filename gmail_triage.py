#!/usr/bin/env python3
"""
Local Gmail triage, label audit, and filter-export CLI.

Security & privacy design:
- Uses only `gmail.readonly` and `gmail.compose` OAuth scopes.
- NEVER calls Gmail send, delete, or trash endpoints.
- Runs 100% locally using your own Google Cloud OAuth Desktop credentials.
- Automatically detects HTTP/HTTPS proxy from environment or macOS system settings.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import email.utils
import json
import os
import re
import stat
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.request import getproxies

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"
CONFIG_PATH = BASE_DIR / "config.json"
DEFAULT_PRESET_PATH = BASE_DIR / "presets" / "personal.json"
REPORTS_DIR = BASE_DIR / "reports"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

EXTERNAL_BANNER_RE = re.compile(
    r"(?:CAUTION:\s*External email\.?\s*Do not click links or open attachments unless you recognize the sender and know the content is safe\.?\s*)+",
    re.IGNORECASE,
)


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if DEFAULT_PRESET_PATH.exists():
        return json.loads(DEFAULT_PRESET_PATH.read_text(encoding="utf-8"))
    return {
        "lookback_days": 3,
        "max_messages": 100,
        "audit_days": 30,
        "my_emails": [],
        "vip_senders": [],
        "bulk_notice_senders": [],
        "priority_topic": {"enabled": False},
        "urgent_keywords": ["urgent", "deadline", "action required", "overdue"],
        "deprioritize_domains_or_keywords": ["noreply@", "no-reply@", "donotreply@"],
    }


def save_token_securely(creds: Credentials) -> None:
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(TOKEN_PATH, stat.S_IRUSR | stat.S_IWUSR)


def detect_http_proxy() -> str | None:
    """Detect HTTPS/HTTP proxy from environment variables or OS proxy settings."""
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(key)
        if val:
            return val
    proxies = getproxies()
    return proxies.get("https") or proxies.get("http")


class GmailClient:
    """Minimal Gmail REST client using AuthorizedSession (connection pooling, proxy-aware, rate-limit backoff)."""

    def __init__(self, creds: Credentials) -> None:
        self.session = AuthorizedSession(creds)
        proxy_url = detect_http_proxy()
        if proxy_url:
            self.session.proxies.update({"http": proxy_url, "https": proxy_url})

    def get(
        self,
        path: str,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        max_retries: int = 5,
    ) -> dict[str, Any]:
        return self._request("GET", path, params=params, max_retries=max_retries)

    def post(
        self,
        path: str,
        json_body: dict[str, Any],
        max_retries: int = 5,
    ) -> dict[str, Any]:
        return self._request("POST", path, json_body=json_body, max_retries=max_retries)

    def put(
        self,
        path: str,
        json_body: dict[str, Any],
        max_retries: int = 5,
    ) -> dict[str, Any]:
        return self._request("PUT", path, json_body=json_body, max_retries=max_retries)

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        json_body: dict[str, Any] | None = None,
        max_retries: int = 5,
    ) -> dict[str, Any]:
        url = f"{GMAIL_API_BASE}/{path.lstrip('/')}"
        for attempt in range(max_retries + 1):
            resp = self.session.request(method, url, params=params, json=json_body, timeout=30)
            if resp.status_code in (429, 500, 502, 503, 504) or (
                resp.status_code == 403 and "rateLimitExceeded" in resp.text
            ):
                if attempt < max_retries:
                    wait_s = min(30.0, 2.0 ** (attempt + 1))
                    print(
                        f"[WARN] Gmail API rate limit ({resp.status_code}), retrying in {wait_s:.0f}s...",
                        file=sys.stderr,
                    )
                    time.sleep(wait_s)
                    continue
            if resp.status_code >= 400:
                print(f"[API ERROR {resp.status_code}] {resp.text}", file=sys.stderr)
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Failed Gmail {method} after retries: {url}")


def get_gmail_client(allow_interactive: bool = False) -> GmailClient:
    proxy_url = detect_http_proxy()
    if proxy_url:
        os.environ.setdefault("https_proxy", proxy_url)
        os.environ.setdefault("http_proxy", proxy_url)

    creds: Credentials | None = None
    if TOKEN_PATH.exists():
        stored_scopes = set(json.loads(TOKEN_PATH.read_text(encoding="utf-8")).get("scopes") or [])
        if set(SCOPES).issubset(stored_scopes):
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        elif not allow_interactive:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), list(stored_scopes) or SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                save_token_securely(creds)
            except Exception:
                if not allow_interactive:
                    raise
                creds = None

    if not creds or not creds.valid:
        if allow_interactive:
            if not CREDENTIALS_PATH.exists():
                print(
                    f"[ERROR] Missing {CREDENTIALS_PATH}\n"
                    f"Please follow docs/GCP_SETUP.md to download your Google OAuth Desktop credentials.json.",
                    file=sys.stderr,
                )
                sys.exit(2)
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
            save_token_securely(creds)
        else:
            print(
                "[ERROR] Not authenticated yet. Run `./.venv/bin/python gmail_triage.py auth` first.",
                file=sys.stderr,
            )
            sys.exit(2)

    return GmailClient(creds)


def header_map(headers: list[dict[str, str]]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in headers}


def extract_email_addr(raw: str) -> str:
    _, addr = email.utils.parseaddr(raw or "")
    return addr.lower().strip()


def strip_external_banners(text: str) -> str:
    return EXTERNAL_BANNER_RE.sub("", text or "").strip()


def extract_plain_preview(payload: dict[str, Any], limit: int = 600) -> str:
    """Extract a short plain-text preview from a Gmail message payload."""

    def walk(part: dict[str, Any]) -> str | None:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if mime == "text/plain" and data:
            try:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            except Exception:
                return None
        for sub in part.get("parts", []) or []:
            found = walk(sub)
            if found:
                return found
        return None

    text = walk(payload) or ""
    text = re.sub(r"\s+", " ", text).strip()
    return strip_external_banners(text)[:limit]


def parse_date_iso(date_header: str, internal_ms: str | None) -> str:
    if internal_ms:
        try:
            ts = int(internal_ms) / 1000.0
            return dt.datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    try:
        parsed = email.utils.parsedate_to_datetime(date_header)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return date_header or ""


def is_automated(headers: dict[str, str], sender_addr: str, deprioritize: list[str]) -> bool:
    if "list-unsubscribe" in headers or "list-id" in headers:
        return True
    auto_sub = headers.get("auto-submitted", "").lower()
    if auto_sub and auto_sub != "no":
        return True
    if any(pat.lower() in sender_addr for pat in deprioritize):
        return True
    return False


def match_keywords(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    hits = []
    for kw in keywords:
        k = kw.lower().strip()
        if not k:
            continue
        if k.isascii():
            if re.search(rf"\b{re.escape(k)}\b", lowered):
                hits.append(kw)
        else:
            if k in lowered:
                hits.append(kw)
    return hits


def match_any_substring(text: str, patterns: list[str]) -> list[str]:
    lowered = text.lower()
    return [p for p in patterns if p.strip() and p.lower() in lowered]


def match_vip(sender_addr: str, vip_list: list[str]) -> bool:
    return any(v.lower() in sender_addr for v in vip_list if v.strip())


def cmd_auth(_args: argparse.Namespace) -> None:
    client = get_gmail_client(allow_interactive=True)
    profile = client.get("profile")
    labels_resp = client.get("labels")
    unread_inbox = client.get("messages", {"q": "in:inbox is:unread", "maxResults": 1}).get(
        "resultSizeEstimate", 0
    )
    print(f"Authentication successful (scopes: {', '.join(SCOPES)})!")
    print(f"Account      : {profile.get('emailAddress')}")
    print(f"Total msgs   : {profile.get('messagesTotal')}")
    print(f"Total threads: {profile.get('threadsTotal')}")
    print(f"Labels count : {len(labels_resp.get('labels', []))}")
    print(f"Unread inbox : ~{unread_inbox}")


def cmd_fetch(args: argparse.Namespace) -> None:
    cfg = load_config()
    days = args.days if args.days is not None else int(cfg.get("lookback_days", 3))
    max_results = args.max if args.max is not None else int(cfg.get("max_messages", 100))
    vip_list = cfg.get("vip_senders", [])
    bulk_notices = [p.lower() for p in cfg.get("bulk_notice_senders", [])]
    urgent_keywords = cfg.get("urgent_keywords", [])
    deprioritize = cfg.get("deprioritize_domains_or_keywords", [])
    uni_domain = (cfg.get("university_domain") or "").lower().strip()

    pt_cfg = cfg.get("priority_topic", {})
    pt_enabled = bool(pt_cfg.get("enabled", False))
    pt_label = (pt_cfg.get("label") or "0-Priority").lower()
    pt_senders = pt_cfg.get("senders_or_domains", [])
    pt_auto_acks = [p.lower() for p in pt_cfg.get("auto_ack_subjects", [])]
    pt_keywords = pt_cfg.get("keywords", [])
    pt_hp_keywords = pt_cfg.get("high_priority_keywords", [])
    pt_internal_list = (pt_cfg.get("internal_list_email") or "").lower().strip()

    client = get_gmail_client(allow_interactive=False)
    profile = client.get("profile")
    my_email = (profile.get("emailAddress") or "").lower()
    my_emails = {my_email} | {e.lower() for e in cfg.get("my_emails", []) if e.strip()}

    labels_resp = client.get("labels")
    label_id_to_name = {l["id"]: l["name"] for l in labels_resp.get("labels", [])}

    query = f"newer_than:{days}d -in:spam -in:trash -in:chats"
    threads_list: list[dict[str, Any]] = []
    page_token = None
    while len(threads_list) < max_results:
        params: dict[str, Any] = {"q": query, "maxResults": min(50, max_results - len(threads_list))}
        if page_token:
            params["pageToken"] = page_token
        resp = client.get("threads", params)
        threads_list.extend(resp.get("threads", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    meta_params: list[tuple[str, Any]] = [
        ("format", "metadata"),
        ("metadataHeaders", "From"),
        ("metadataHeaders", "To"),
        ("metadataHeaders", "Cc"),
        ("metadataHeaders", "Subject"),
        ("metadataHeaders", "Date"),
        ("metadataHeaders", "List-Unsubscribe"),
        ("metadataHeaders", "List-Id"),
        ("metadataHeaders", "Auto-Submitted"),
    ]

    items: list[dict[str, Any]] = []
    for idx, t in enumerate(threads_list):
        if idx > 0 and idx % 10 == 0:
            time.sleep(0.25)
        thread_data = client.get(f"threads/{t['id']}", meta_params)
        msgs = thread_data.get("messages", [])
        if not msgs:
            continue

        last_msg = msgs[-1]
        hdrs = header_map(last_msg.get("payload", {}).get("headers", []))
        sender_raw = hdrs.get("from", "")
        sender_addr = extract_email_addr(sender_raw)
        to_raw = hdrs.get("to", "")
        cc_raw = hdrs.get("cc", "")
        subject = hdrs.get("subject", "(No Subject)")
        date_str = parse_date_iso(hdrs.get("date", ""), last_msg.get("internalDate"))
        label_ids = last_msg.get("labelIds", [])
        label_names = [label_id_to_name.get(lid, lid) for lid in label_ids]
        snippet = strip_external_banners(last_msg.get("snippet", ""))
        preview = snippet

        is_from_me = (sender_addr in my_emails) or ("SENT" in label_ids and "INBOX" not in label_ids)
        is_unread = "UNREAD" in label_ids
        is_starred = "STARRED" in label_ids
        is_important = "IMPORTANT" in label_ids
        in_inbox = "INBOX" in label_ids
        in_primary = in_inbox and not any(
            c in label_ids for c in ("CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL")
        )
        is_bulk_notice = any(p in sender_addr for p in bulk_notices)
        automated = is_bulk_notice or is_automated(hdrs, sender_addr, deprioritize)

        # Forwarded university/work human mail may carry Auto-Submitted from forwarding server
        if (
            uni_domain
            and uni_domain in to_raw.lower()
            and not is_bulk_notice
            and "list-unsubscribe" not in hdrs
            and "list-id" not in hdrs
            and not any(pat.lower() in sender_addr for pat in deprioritize)
        ):
            automated = False

        to_cc_lower = f"{to_raw} {cc_raw}".lower()
        direct_to_me = any(e and e in to_raw.lower() for e in my_emails)
        vip = (not is_bulk_notice) and match_vip(sender_addr, vip_list)

        combined_text = f"{subject} {preview}"
        kw_hits = match_keywords(combined_text, urgent_keywords)

        # Priority topic detection (e.g. Conference PC / key project)
        pt_sender_hits = match_any_substring(f"{sender_raw} {to_raw} {cc_raw}", pt_senders) if pt_enabled else []
        pt_subj_hits = match_keywords(subject, pt_keywords) if pt_enabled else []
        pt_label_hit = any(pt_label in ln.lower() for ln in label_names) if pt_enabled else False
        is_uni_internal = bool(uni_domain and sender_addr.endswith(f"@{uni_domain}"))
        is_priority_topic = bool(
            pt_enabled and (pt_sender_hits or pt_subj_hits or (pt_label_hit and not is_uni_internal))
        )
        is_pt_ack = any(subject.lower().startswith(p) for p in pt_auto_acks)
        pt_hp_hits = match_keywords(combined_text, pt_hp_keywords) if (is_priority_topic and not is_pt_ack) else []

        category = "other"
        if not is_from_me:
            if is_priority_topic:
                if is_pt_ack:
                    category = "priority_fyi"
                elif (
                    is_unread
                    or is_starred
                    or pt_hp_hits
                    or (in_inbox and (direct_to_me or (pt_internal_list and pt_internal_list in to_cc_lower)))
                ):
                    category = "priority_urgent"
                else:
                    category = "priority_fyi"
            elif (kw_hits and not automated) or (vip and (is_unread or kw_hits)) or is_starred:
                category = "urgent_candidate"
            elif (not is_unread) and direct_to_me and (not automated) and in_inbox:
                category = "read_unreplied"
            elif is_unread and in_primary and not automated:
                category = "unread_primary"
            elif is_unread and (automated or not in_primary):
                category = "noise_or_bulk"

        items.append(
            {
                "thread_id": t["id"],
                "last_message_id": last_msg.get("id", ""),
                "message_count": len(msgs),
                "date": date_str,
                "from": sender_raw,
                "from_email": sender_addr,
                "to": to_raw,
                "cc": cc_raw,
                "subject": subject,
                "snippet": snippet,
                "preview": preview,
                "labels": label_names,
                "is_from_me": is_from_me,
                "is_unread": is_unread,
                "is_starred": is_starred,
                "is_important": is_important,
                "in_inbox": in_inbox,
                "direct_to_me": direct_to_me,
                "is_automated": automated,
                "is_vip": vip,
                "is_priority_topic": is_priority_topic,
                "priority_signals": list(dict.fromkeys(pt_sender_hits + pt_subj_hits[:4])),
                "priority_urgent_signals": pt_hp_hits,
                "urgent_keywords": kw_hits,
                "category": category,
                "gmail_url": f"https://mail.google.com/mail/u/0/#inbox/{t['id']}",
            }
        )

    # Enrich only top-priority items with full plain-text preview (low quota cost)
    enrich_targets = [
        x for x in items if x["category"] in ("priority_urgent", "urgent_candidate", "read_unreplied")
    ][:25]
    for x in enrich_targets:
        mid = x.get("last_message_id")
        if not mid:
            continue
        try:
            full_msg = client.get(f"messages/{mid}", {"format": "full"})
            plain = extract_plain_preview(full_msg.get("payload", {}), limit=700)
            if plain:
                x["preview"] = plain
        except Exception:
            pass

    now = dt.datetime.now().astimezone()
    summary = {
        "generated_at": now.isoformat(timespec="seconds"),
        "account": my_email,
        "lookback_days": days,
        "total_threads_scanned": len(items),
        "counts": {
            "priority_urgent": sum(1 for x in items if x["category"] == "priority_urgent"),
            "priority_fyi": sum(1 for x in items if x["category"] == "priority_fyi"),
            "urgent_candidate": sum(1 for x in items if x["category"] == "urgent_candidate"),
            "read_unreplied": sum(1 for x in items if x["category"] == "read_unreplied"),
            "unread_primary": sum(1 for x in items if x["category"] == "unread_primary"),
            "noise_or_bulk": sum(1 for x in items if x["category"] == "noise_or_bulk"),
        },
        "items": items,
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    latest_json = REPORTS_DIR / "latest_fetch.json"
    dated_md = REPORTS_DIR / f"{now.strftime('%Y-%m-%d')}_digest.md"

    latest_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Daily Gmail Triage ({now.strftime('%Y-%m-%d %H:%M')})",
        "",
        f"- **Account**: `{my_email}`",
        f"- **Window**: Last {days} days ({len(items)} threads scanned)",
        f"- **Priority Urgent**: {summary['counts']['priority_urgent']} | "
        f"**Urgent / VIP**: {summary['counts']['urgent_candidate']} | "
        f"**Read-Unreplied**: {summary['counts']['read_unreplied']} | "
        f"**Unread Primary**: {summary['counts']['unread_primary']}",
        "",
    ]

    def append_section(title: str, cat: str) -> None:
        subset = [x for x in items if x["category"] == cat]
        lines.append(f"## {title} ({len(subset)})")
        lines.append("")
        if not subset:
            lines.append("_None_")
            lines.append("")
            return
        for x in subset:
            flags = []
            if x["is_unread"]:
                flags.append("UNREAD")
            if x["is_starred"]:
                flags.append("STARRED")
            if x["priority_urgent_signals"]:
                flags.append("PRIORITY:" + ",".join(x["priority_urgent_signals"][:4]))
            elif x["urgent_keywords"]:
                flags.append("KW:" + ",".join(x["urgent_keywords"][:4]))
            if x["is_vip"]:
                flags.append("VIP")
            flag_str = f" `{' | '.join(flags)}`" if flags else ""
            lines.append(f"- **[{x['subject']}]({x['gmail_url']})**{flag_str}")
            lines.append(f"  - From: `{x['from']}` | Date: `{x['date']}` | Messages: {x['message_count']}")
            lines.append(f"  - Snippet: {x['snippet'][:240]}")
        lines.append("")

    if pt_enabled:
        append_section(f"0. {pt_cfg.get('label', 'Priority Topic')} — Urgent / Action Needed", "priority_urgent")
        append_section(f"0b. {pt_cfg.get('label', 'Priority Topic')} — FYI", "priority_fyi")
    append_section("1. Urgent / Deadline / VIP / Starred", "urgent_candidate")
    append_section("2. Read but Unreplied (Direct to You)", "read_unreplied")
    append_section("3. Other Unread Primary Emails", "unread_primary")

    noise_senders = Counter(
        x["from_email"] for x in items if x["category"] == "noise_or_bulk"
    ).most_common(10)
    if noise_senders:
        lines.append("## 4. Top Unread Automated / Bulk Senders (Filter Candidates)")
        lines.append("")
        for sender, cnt in noise_senders:
            lines.append(f"- `{sender}`: {cnt} unread")
        lines.append("")

    dated_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved JSON report: {latest_json}")
    print(f"Saved Markdown   : {dated_md}")
    if args.print_md:
        print("\n" + "\n".join(lines))


def cmd_audit(args: argparse.Namespace) -> None:
    cfg = load_config()
    days = args.days if args.days is not None else int(cfg.get("audit_days", 30))
    client = get_gmail_client(allow_interactive=False)

    labels_resp = client.get("labels")
    user_labels = [l["name"] for l in labels_resp.get("labels", []) if l.get("type") == "user"]

    query = f"newer_than:{days}d -in:spam -in:trash"
    msg_ids: list[str] = []
    page_token = None
    while len(msg_ids) < 300:
        params: dict[str, Any] = {"q": query, "maxResults": min(100, 300 - len(msg_ids))}
        if page_token:
            params["pageToken"] = page_token
        resp = client.get("messages", params)
        msg_ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    sender_total = Counter()
    sender_unread = Counter()
    domain_total = Counter()
    unsub_senders = Counter()

    for idx, mid in enumerate(msg_ids):
        if idx > 0 and idx % 20 == 0:
            time.sleep(0.25)
        m = client.get(
            f"messages/{mid}",
            [("format", "metadata"), ("metadataHeaders", "From"), ("metadataHeaders", "List-Unsubscribe")],
        )
        hdrs = header_map(m.get("payload", {}).get("headers", []))
        addr = extract_email_addr(hdrs.get("from", ""))
        if not addr:
            continue
        sender_total[addr] += 1
        if "UNREAD" in m.get("labelIds", []):
            sender_unread[addr] += 1
        if "@" in addr:
            domain_total[addr.split("@", 1)[1]] += 1
        if "list-unsubscribe" in hdrs:
            unsub_senders[addr] += 1

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / "org_audit.json"
    audit_data = {
        "days": days,
        "messages_sampled": len(msg_ids),
        "existing_user_labels": sorted(user_labels),
        "top_senders": sender_total.most_common(25),
        "top_unread_senders": sender_unread.most_common(25),
        "top_domains": domain_total.most_common(20),
        "top_newsletter_senders": unsub_senders.most_common(25),
    }
    out.write_text(json.dumps(audit_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved inbox audit: {out}")
    print(f"Sampled {len(msg_ids)} messages from the last {days} days.")
    print(f"Existing user labels ({len(user_labels)}): {', '.join(sorted(user_labels)[:20])}")


def cmd_labels_audit(args: argparse.Namespace) -> None:
    """List every user label with message/thread counts and optional last-activity date."""
    client = get_gmail_client(allow_interactive=False)
    labels_resp = client.get("labels")
    user_labels = [l for l in labels_resp.get("labels", []) if l.get("type") == "user"]
    user_labels.sort(key=lambda x: x["name"].lower())

    detailed: list[dict[str, Any]] = []
    for idx, lbl in enumerate(user_labels):
        if idx > 0 and idx % 15 == 0:
            time.sleep(0.25)
        info = client.get(f"labels/{lbl['id']}")
        entry: dict[str, Any] = {
            "id": info["id"],
            "name": info["name"],
            "messagesTotal": info.get("messagesTotal", 0),
            "messagesUnread": info.get("messagesUnread", 0),
            "threadsTotal": info.get("threadsTotal", 0),
            "threadsUnread": info.get("threadsUnread", 0),
            "latest_date": None,
        }
        if args.with_dates and entry["messagesTotal"] > 0:
            try:
                msg_list = client.get("messages", {"labelIds": info["id"], "maxResults": 1}).get("messages", [])
                if msg_list:
                    m = client.get(f"messages/{msg_list[0]['id']}", [("format", "metadata"), ("metadataHeaders", "Date")])
                    hdrs = header_map(m.get("payload", {}).get("headers", []))
                    entry["latest_date"] = parse_date_iso(hdrs.get("date", ""), m.get("internalDate"))[:10]
            except Exception:
                pass
        detailed.append(entry)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / "labels_audit.json"
    out.write_text(json.dumps({"total_user_labels": len(detailed), "labels": detailed}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Found {len(detailed)} user labels. Saved to: {out}")


def cmd_export_filters(args: argparse.Namespace) -> None:
    """Export a personalized Gmail Filters XML generated purely from config.json."""
    cfg = load_config()
    fr = cfg.get("filter_rules", {})
    pt = cfg.get("priority_topic", {})
    uni_domain = (cfg.get("university_domain") or "").strip()
    my_emails = cfg.get("my_emails", [])
    uni_email = next((e for e in my_emails if uni_domain and uni_domain in e.lower()), "")

    ns = "http://www.w3.org/2005/Atom"
    apps = "http://schemas.google.com/apps/2006"
    ET.register_namespace("", ns)
    ET.register_namespace("apps", apps)

    feed = ET.Element(f"{{{ns}}}feed")
    ET.SubElement(feed, f"{{{ns}}}title").text = "Mail Filters"

    def add_filter(criteria: dict[str, str], actions: dict[str, str]) -> None:
        entry = ET.SubElement(feed, f"{{{ns}}}entry")
        ET.SubElement(entry, f"{{{ns}}}category", term="filter")
        ET.SubElement(entry, f"{{{ns}}}title").text = "Mail Filter"
        for k, v in criteria.items():
            ET.SubElement(entry, f"{{{apps}}}property", name=k, value=v)
        for k, v in actions.items():
            ET.SubElement(entry, f"{{{apps}}}property", name=k, value=v)

    exclude_from_newsletters: list[str] = []
    exclude_subject_terms: list[str] = []

    # 1. Optional Priority Topic (e.g. Conference Chair)
    if pt.get("enabled"):
        pt_label = pt.get("label") or "0-Priority"
        clauses = []
        for kw in pt.get("keywords", [])[:3]:
            clauses.append(f'subject:"{kw}"' if " " in kw else f"subject:{kw}")
            exclude_subject_terms.append(f'-subject:"{kw}"' if " " in kw else f"-subject:{kw}")
        for s in pt.get("senders_or_domains", []):
            clauses.append(s if "@" in s else f"from:{s}")
            exclude_from_newsletters.append(s)
        if clauses:
            add_filter(
                criteria={"hasTheWord": " OR ".join(clauses)},
                actions={
                    "label": pt_label,
                    "smartLabelToApply": "^smartlabel_personal",
                    "shouldNeverSpam": "true",
                },
            )

    # 2. Optional Family / School domains
    family_domains = [d.strip() for d in fr.get("family_school_domains", []) if d.strip()]
    if family_domains:
        add_filter(
            criteria={"from": " OR ".join(family_domains)},
            actions={
                "label": "0-Family-School",
                "smartLabelToApply": "^smartlabel_personal",
                "shouldNeverSpam": "true",
            },
        )
        exclude_from_newsletters.extend(family_domains)

    # 3. Optional University Faculty rules (driven by university_domain)
    if uni_domain:
        exclude_from_newsletters.append(f"@{uni_domain}")
        bulk_terms = "notice OR news OR marketing OR staff.club OR staff.association"
        if fr.get("enable_faculty_admin_label"):
            add_filter(
                criteria={
                    "hasTheWord": f'from:@{uni_domain} -from:({bulk_terms}) -subject:(Seminar OR Talk OR Lecture OR Symposium OR Webinar OR Exhibition OR "Call for Proposals" OR "Call for Applications")'
                },
                actions={
                    "label": "1-Dept-Admin",
                    "smartLabelToApply": "^smartlabel_personal",
                    "shouldNeverSpam": "true",
                },
            )
        if fr.get("enable_grants_label"):
            add_filter(
                criteria={
                    "hasTheWord": f'from:@{uni_domain} subject:("Call for Proposals" OR "Call for Applications" OR "Research Scheme" OR "Grant" OR "Funding")'
                },
                actions={"label": "Grants", "shouldNeverSpam": "true"},
            )
        if fr.get("enable_hiring_label") and uni_email:
            extra_excl = " ".join(exclude_subject_terms[:2])
            add_filter(
                criteria={
                    "hasTheWord": f'to:{uni_email} (PhD OR MPhil OR "RA application" OR "RA inquiry" OR "Research Internship") (Prospective OR Inquiry OR Enquiry OR Application OR Supervision) -from:@{uni_domain} {extra_excl}'.strip()
                },
                actions={"label": "Hiring-PhD-RA", "shouldArchive": "true"},
            )
        if fr.get("enable_campus_notices_label"):
            add_filter(
                criteria={
                    "hasTheWord": f'(from:@{uni_domain} (from:({bulk_terms}) OR subject:(Seminar OR Talk OR Lecture OR Exhibition))) OR from:notifications@instructure.com'
                },
                actions={"label": "Campus-Notices", "shouldArchive": "true"},
            )

    # 4. Journal Editorial systems (auto-archive without marking important)
    if fr.get("enable_journal_archive_label"):
        add_filter(
            criteria={"from": "onbehalfof@manuscriptcentral.com"},
            actions={"label": "Journals", "shouldArchive": "true"},
        )
        exclude_from_newsletters.append("manuscriptcentral.com")

    # 5. Finance & Tax domains
    fin_domains = [d.strip() for d in fr.get("finance_domains", []) if d.strip()]
    if fin_domains:
        add_filter(
            criteria={"from": " OR ".join(fin_domains)},
            actions={"label": "Finance & Tax"},
        )
        exclude_from_newsletters.extend(fin_domains)

    # 6. Travel & Receipts
    add_filter(
        criteria={
            "hasTheWord": 'receipt OR invoice OR itinerary OR booking OR reimbursement OR "Expense Claim" OR 发票 OR 报销 OR 行程单'
        },
        actions={"label": "Travel & Receipts"},
    )

    # 7. Deadlines / Action Required
    add_filter(
        criteria={
            "hasTheWord": '("action required" OR deadline OR overdue OR urgent OR "final notice" OR "for reply by" OR 紧急 OR 截止) -from:(manuscriptcentral.com OR instructure.com) -category:promotions'
        },
        actions={"label": "2-Deadline", "shouldNeverSpam": "true"},
    )

    # 8. Silent archive for newsletters/promotions (no Newsletters label clutter!)
    if fr.get("archive_promotions_and_newsletters", True):
        excl_from = f" -from:({' OR '.join(dict.fromkeys(exclude_from_newsletters))})" if exclude_from_newsletters else ""
        excl_subj = f" {' '.join(exclude_subject_terms[:2])}" if exclude_subject_terms else ""
        add_filter(
            criteria={"hasTheWord": f'("unsubscribe" OR "退订"){excl_from}{excl_subj}'},
            actions={"shouldArchive": "true"},
        )

    out_path = Path(args.output) if args.output else (BASE_DIR / "gmail_filters.xml")
    tree = ET.ElementTree(feed)
    ET.indent(tree, space="  ")
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    print(f"Exported personalized Gmail filters XML to: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local privacy-first Gmail triage & audit helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="Run OAuth 2.0 flow (gmail.readonly + gmail.compose) and save token.json")
    p_auth.set_defaults(func=cmd_auth)

    p_fetch = sub.add_parser("fetch", help="Fetch recent threads and generate triage JSON + Markdown")
    p_fetch.add_argument("--days", type=int, default=None, help="Lookback window in days")
    p_fetch.add_argument("--max", type=int, default=None, help="Max threads to scan")
    p_fetch.add_argument("--print-md", action="store_true", help="Print Markdown digest to stdout")
    p_fetch.set_defaults(func=cmd_fetch)

    p_audit = sub.add_parser("audit", help="Audit 30-day inbox senders/labels for organization advice")
    p_audit.add_argument("--days", type=int, default=None, help="Audit window in days")
    p_audit.set_defaults(func=cmd_audit)

    p_labels = sub.add_parser("labels-audit", help="Audit all user labels with message counts and recency")
    p_labels.add_argument("--with-dates", action="store_true", help="Also fetch latest email date per label")
    p_labels.set_defaults(func=cmd_labels_audit)

    p_filt = sub.add_parser("export-filters", help="Generate gmail_filters.xml from config.json for Gmail import")
    p_filt.add_argument("--output", type=str, default=None, help="Output XML file path")
    p_filt.set_defaults(func=cmd_export_filters)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
