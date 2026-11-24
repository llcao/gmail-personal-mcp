#!/usr/bin/env python3
"""
Zero-dependency Local Stdio MCP Server for Personal Gmail.

Protocol: Model Context Protocol (MCP) over newline-delimited JSON-RPC 2.0 on stdin/stdout.
Safety: Read-only + Draft-only (`create_draft`, `update_draft`). NEVER sends or deletes emails.
Works with Antigravity CLI/App, Claude Desktop, Claude Code, Cursor, and any Stdio MCP host.
"""

from __future__ import annotations

import base64
import json
import sys
import traceback
from email.message import EmailMessage
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import gmail_triage as gt  # noqa: E402

SERVER_INFO = {
    "name": "gmail-personal-mcp",
    "version": "1.0.0",
}

SUPPORTED_PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "list_labels",
        "description": "List Gmail labels (user labels and optionally system labels) with message/unread counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_system": {
                    "type": "boolean",
                    "description": "Include system labels (INBOX, STARRED, SNOOZED, etc.). Defaults to false.",
                    "default": False,
                },
                "with_counts": {
                    "type": "boolean",
                    "description": "Fetch messagesTotal/messagesUnread per user label. Defaults to true.",
                    "default": True,
                },
            },
        },
    },
    {
        "name": "search_threads",
        "description": "Search Gmail threads using standard Gmail query syntax (e.g. 'is:unread in:inbox', 'newer_than:7d from:alice'). Returns thread ID, sender, subject, date, labels, clean snippet, and Gmail URL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query string.",
                    "default": "newer_than:3d -in:spam -in:trash",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of threads to return (1-50, default 15).",
                    "default": 15,
                },
            },
        },
    },
    {
        "name": "get_thread",
        "description": "Fetch full details and clean plain-text bodies of all messages in a Gmail thread by thread_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "thread_id": {
                    "type": "string",
                    "description": "Gmail thread ID returned by search_threads or daily_triage_brief.",
                },
                "max_chars_per_message": {
                    "type": "integer",
                    "description": "Maximum plain-text characters per message (default 3000).",
                    "default": 3000,
                },
            },
            "required": ["thread_id"],
        },
    },
    {
        "name": "daily_triage_brief",
        "description": "Run the personalized daily Gmail triage scan using rules in config.json (priority topic urgent items, deadline/VIP emails, read-but-unreplied direct threads, and unread primary emails). Saves reports/latest_fetch.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Lookback window in days (default 3).",
                    "default": 3,
                },
                "max_threads": {
                    "type": "integer",
                    "description": "Maximum recent threads to scan (default 80).",
                    "default": 80,
                },
            },
        },
    },
    {
        "name": "list_drafts",
        "description": "List recent drafts currently saved in Gmail Drafts (draft_id, thread_id, to, subject, snippet, gmail_url).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of drafts to list (1-30, default 15).",
                    "default": 15,
                },
            },
        },
    },
    {
        "name": "create_draft",
        "description": "Save a Gmail draft in Drafts (NEVER sends the email). If thread_id is provided, automatically threads it as a proper reply (Re: subject, In-Reply-To, References).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address(es), comma-separated."},
                "cc": {"type": "string", "description": "Optional CC email address(es).", "default": ""},
                "subject": {
                    "type": "string",
                    "description": "Email subject (optional if thread_id is provided).",
                    "default": "",
                },
                "body": {"type": "string", "description": "Plain-text email body."},
                "thread_id": {
                    "type": "string",
                    "description": "Optional Gmail thread_id to attach this draft as a reply.",
                    "default": "",
                },
            },
            "required": ["to", "body"],
        },
    },
    {
        "name": "update_draft",
        "description": "Update an existing Gmail draft in-place by draft_id (NEVER sends the email).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "string", "description": "Existing Gmail draft_id."},
                "to": {"type": "string", "description": "Recipient email address(es)."},
                "cc": {"type": "string", "description": "Optional CC email address(es).", "default": ""},
                "subject": {"type": "string", "description": "Email subject.", "default": ""},
                "body": {"type": "string", "description": "Plain-text email body."},
                "thread_id": {"type": "string", "description": "Optional Gmail thread_id.", "default": ""},
            },
            "required": ["draft_id", "to", "body"],
        },
    },
]


def _build_draft_payload(
    client: gt.GmailClient,
    to_addr: str,
    cc_addr: str,
    subject: str,
    body: str,
    thread_id: str,
) -> tuple[dict[str, Any], str]:
    in_reply_to = ""
    references = ""
    if thread_id:
        td = client.get(
            f"threads/{thread_id}",
            [
                ("format", "metadata"),
                ("metadataHeaders", "Subject"),
                ("metadataHeaders", "Message-ID"),
                ("metadataHeaders", "References"),
            ],
        )
        msgs = td.get("messages", [])
        if msgs:
            last_hdrs = gt.header_map(msgs[-1].get("payload", {}).get("headers", []))
            if not subject:
                orig_subj = last_hdrs.get("subject", "")
                subject = orig_subj if orig_subj.lower().startswith("re:") else f"Re: {orig_subj}"
            msg_id = last_hdrs.get("message-id", "")
            orig_refs = last_hdrs.get("references", "")
            if msg_id:
                in_reply_to = msg_id
                references = f"{orig_refs} {msg_id}".strip() if orig_refs else msg_id

    final_subject = subject or "(No Subject)"
    em = EmailMessage()
    em["To"] = to_addr
    if cc_addr:
        em["Cc"] = cc_addr
    em["Subject"] = final_subject
    if in_reply_to:
        em["In-Reply-To"] = in_reply_to
        em["References"] = references
    em.set_content(body)

    raw_b64 = base64.urlsafe_b64encode(em.as_bytes()).decode("ascii")
    payload: dict[str, Any] = {"message": {"raw": raw_b64}}
    if thread_id:
        payload["message"]["threadId"] = thread_id
    return payload, final_subject


def tool_list_labels(args: dict[str, Any]) -> dict[str, Any]:
    include_system = bool(args.get("include_system", False))
    with_counts = bool(args.get("with_counts", True))
    client = gt.get_gmail_client(allow_interactive=False)
    labels = client.get("labels").get("labels", [])
    filtered = [l for l in labels if include_system or l.get("type") == "user"]
    filtered.sort(key=lambda x: (x.get("type", ""), x.get("name", "").lower()))

    result = []
    for l in filtered:
        if with_counts and l.get("type") == "user":
            info = client.get(f"labels/{l['id']}")
            result.append(
                {
                    "id": info["id"],
                    "name": info["name"],
                    "type": info.get("type", "user"),
                    "messagesTotal": info.get("messagesTotal", 0),
                    "messagesUnread": info.get("messagesUnread", 0),
                    "threadsTotal": info.get("threadsTotal", 0),
                }
            )
        else:
            result.append({"id": l["id"], "name": l["name"], "type": l.get("type", "")})
    return {"count": len(result), "labels": result}


def tool_search_threads(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "newer_than:3d -in:spam -in:trash")
    max_results = max(1, min(50, int(args.get("max_results", 15))))

    client = gt.get_gmail_client(allow_interactive=False)
    labels_resp = client.get("labels")
    label_id_to_name = {l["id"]: l["name"] for l in labels_resp.get("labels", [])}

    resp = client.get("threads", {"q": query, "maxResults": max_results})
    threads = resp.get("threads", [])

    meta_params: list[tuple[str, Any]] = [
        ("format", "metadata"),
        ("metadataHeaders", "From"),
        ("metadataHeaders", "To"),
        ("metadataHeaders", "Cc"),
        ("metadataHeaders", "Subject"),
        ("metadataHeaders", "Date"),
    ]

    results = []
    for t in threads:
        td = client.get(f"threads/{t['id']}", meta_params)
        msgs = td.get("messages", [])
        if not msgs:
            continue
        last_msg = msgs[-1]
        hdrs = gt.header_map(last_msg.get("payload", {}).get("headers", []))
        label_ids = last_msg.get("labelIds", [])
        results.append(
            {
                "thread_id": t["id"],
                "message_count": len(msgs),
                "date": gt.parse_date_iso(hdrs.get("date", ""), last_msg.get("internalDate")),
                "from": hdrs.get("from", ""),
                "to": hdrs.get("to", ""),
                "subject": hdrs.get("subject", "(No Subject)"),
                "snippet": gt.strip_external_banners(last_msg.get("snippet", "")),
                "is_unread": "UNREAD" in label_ids,
                "is_starred": "STARRED" in label_ids,
                "labels": [label_id_to_name.get(lid, lid) for lid in label_ids],
                "gmail_url": f"https://mail.google.com/mail/u/0/#inbox/{t['id']}",
            }
        )
    return {"query": query, "count": len(results), "threads": results}


def tool_get_thread(args: dict[str, Any]) -> dict[str, Any]:
    thread_id = str(args.get("thread_id", "")).strip()
    if not thread_id:
        raise ValueError("thread_id is required")
    max_chars = max(200, min(15000, int(args.get("max_chars_per_message", 3000))))

    client = gt.get_gmail_client(allow_interactive=False)
    labels_resp = client.get("labels")
    label_id_to_name = {l["id"]: l["name"] for l in labels_resp.get("labels", [])}

    td = client.get(f"threads/{thread_id}", {"format": "full"})
    messages = []
    for m in td.get("messages", []):
        hdrs = gt.header_map(m.get("payload", {}).get("headers", []))
        label_ids = m.get("labelIds", [])
        body = gt.extract_plain_preview(m.get("payload", {}), limit=max_chars) or gt.strip_external_banners(
            m.get("snippet", "")
        )
        messages.append(
            {
                "message_id": m.get("id", ""),
                "date": gt.parse_date_iso(hdrs.get("date", ""), m.get("internalDate")),
                "from": hdrs.get("from", ""),
                "to": hdrs.get("to", ""),
                "cc": hdrs.get("cc", ""),
                "subject": hdrs.get("subject", "(No Subject)"),
                "labels": [label_id_to_name.get(lid, lid) for lid in label_ids],
                "body": body,
            }
        )
    return {
        "thread_id": thread_id,
        "gmail_url": f"https://mail.google.com/mail/u/0/#inbox/{thread_id}",
        "message_count": len(messages),
        "messages": messages,
    }


def tool_daily_triage_brief(args: dict[str, Any]) -> dict[str, Any]:
    days = max(1, min(14, int(args.get("days", 3))))
    max_threads = max(10, min(120, int(args.get("max_threads", 80))))

    class _Args:
        pass

    ns = _Args()
    ns.days = days
    ns.max = max_threads
    ns.print_md = False

    orig_stdout = sys.stdout
    try:
        sys.stdout = sys.stderr
        gt.cmd_fetch(ns)  # type: ignore[arg-type]
    finally:
        sys.stdout = orig_stdout

    latest_json = gt.REPORTS_DIR / "latest_fetch.json"
    data = json.loads(latest_json.read_text(encoding="utf-8"))
    keep_cats = {"priority_urgent", "priority_fyi", "urgent_candidate", "read_unreplied", "unread_primary"}
    filtered_items = [x for x in data.get("items", []) if x.get("category") in keep_cats]
    return {
        "generated_at": data.get("generated_at"),
        "account": data.get("account"),
        "lookback_days": data.get("lookback_days"),
        "total_threads_scanned": data.get("total_threads_scanned"),
        "counts": data.get("counts"),
        "priority_items": filtered_items,
    }


def tool_list_drafts(args: dict[str, Any]) -> dict[str, Any]:
    max_results = max(1, min(30, int(args.get("max_results", 15))))
    client = gt.get_gmail_client(allow_interactive=False)
    resp = client.get("drafts", {"maxResults": max_results})
    draft_refs = resp.get("drafts", [])
    drafts = []
    for d in draft_refs:
        detail = client.get(f"drafts/{d['id']}", {"format": "metadata"})
        msg = detail.get("message", {})
        hdrs = gt.header_map(msg.get("payload", {}).get("headers", []))
        tid = msg.get("threadId", "")
        drafts.append(
            {
                "draft_id": d["id"],
                "thread_id": tid,
                "to": hdrs.get("to", ""),
                "cc": hdrs.get("cc", ""),
                "subject": hdrs.get("subject", "(No Subject)"),
                "snippet": gt.strip_external_banners(msg.get("snippet", "")),
                "gmail_url": f"https://mail.google.com/mail/u/0/#drafts/{tid or d['id']}",
            }
        )
    return {"count": len(drafts), "drafts": drafts}


def tool_create_draft(args: dict[str, Any]) -> dict[str, Any]:
    to_addr = str(args.get("to", "")).strip()
    cc_addr = str(args.get("cc", "")).strip()
    subject = str(args.get("subject", "")).strip()
    body = str(args.get("body", ""))
    thread_id = str(args.get("thread_id", "")).strip()
    if not to_addr or not body.strip():
        raise ValueError("'to' and 'body' are required")

    client = gt.get_gmail_client(allow_interactive=False)
    payload, final_subject = _build_draft_payload(client, to_addr, cc_addr, subject, body, thread_id)
    created = client.post("drafts", payload)
    created_tid = created.get("message", {}).get("threadId", thread_id)
    return {
        "status": "draft_created",
        "draft_id": created.get("id", ""),
        "thread_id": created_tid,
        "to": to_addr,
        "cc": cc_addr,
        "subject": final_subject,
        "gmail_url": f"https://mail.google.com/mail/u/0/#inbox/{created_tid}" if created_tid else "https://mail.google.com/mail/u/0/#drafts",
    }


def tool_update_draft(args: dict[str, Any]) -> dict[str, Any]:
    draft_id = str(args.get("draft_id", "")).strip()
    to_addr = str(args.get("to", "")).strip()
    cc_addr = str(args.get("cc", "")).strip()
    subject = str(args.get("subject", "")).strip()
    body = str(args.get("body", ""))
    thread_id = str(args.get("thread_id", "")).strip()
    if not draft_id or not to_addr or not body.strip():
        raise ValueError("'draft_id', 'to', and 'body' are required")

    client = gt.get_gmail_client(allow_interactive=False)
    payload, final_subject = _build_draft_payload(client, to_addr, cc_addr, subject, body, thread_id)
    updated = client.put(f"drafts/{draft_id}", payload)
    created_tid = updated.get("message", {}).get("threadId", thread_id)
    return {
        "status": "draft_updated",
        "draft_id": updated.get("id", draft_id),
        "thread_id": created_tid,
        "to": to_addr,
        "cc": cc_addr,
        "subject": final_subject,
        "gmail_url": f"https://mail.google.com/mail/u/0/#inbox/{created_tid}" if created_tid else "https://mail.google.com/mail/u/0/#drafts",
    }


DISPATCH = {
    "list_labels": tool_list_labels,
    "search_threads": tool_search_threads,
    "get_thread": tool_get_thread,
    "daily_triage_brief": tool_daily_triage_brief,
    "list_drafts": tool_list_drafts,
    "create_draft": tool_create_draft,
    "update_draft": tool_update_draft,
}


def send_response(msg: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_request(req: dict[str, Any]) -> None:
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}
    if req_id is None:
        return

    if method == "initialize":
        client_ver = params.get("protocolVersion") or SUPPORTED_PROTOCOL_VERSION
        send_response(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": client_ver,
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO,
                },
            }
        )
        return

    if method == "ping":
        send_response({"jsonrpc": "2.0", "id": req_id, "result": {}})
        return

    if method == "tools/list":
        send_response({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})
        return

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        fn = DISPATCH.get(tool_name)
        if fn is None:
            send_response(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                }
            )
            return
        try:
            result_obj = fn(tool_args)
            send_response(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result_obj, ensure_ascii=False, indent=2)}],
                        "isError": False,
                    },
                }
            )
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            send_response(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True},
                }
            )
        return

    send_response(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    )


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            send_response({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}})
            continue
        handle_request(req)


if __name__ == "__main__":
    main()
