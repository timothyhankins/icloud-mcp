"""Smart folder search — token-gated REST endpoints for iOS Shortcuts.

Replicates desktop Mail smart folders server-side using full IMAP SEARCH
criteria (including receiving address via TO/CC, which the Shortcuts app's
native Find Messages action cannot express). Results come back as JSON or
as a plain-text digest a Shortcut can show directly with zero parsing.

Named presets are defined in the SMARTFOLDERS env var as a JSON dict:

    {"consulting": {"to": "alias@me.com", "days": 30, "folders": "INBOX,Archive"}}

Endpoints are gated by the SHORTCUTS_TOKEN env var (fail closed when unset).
All IMAP access is read-only (folders are selected with readonly=True).
"""

import email
import hmac
import html as html_escape
import json
import logging
import os
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from .auth import require_auth
from .email import _close_imap_client, _decode_mime_header, _get_imap_client

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def _digest_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("SMARTFOLDERS_TZ", "America/New_York"))
    except Exception as _e:
        return ZoneInfo("UTC")


def check_token(supplied: Optional[str]) -> bool:
    """Constant-time check of the shared Shortcuts token. Fail closed if unset."""
    expected = os.getenv("SHORTCUTS_TOKEN")
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied, expected)


def get_presets() -> Dict[str, Dict[str, Any]]:
    """Named smart folder definitions from the SMARTFOLDERS env var (JSON dict)."""
    raw = os.getenv("SMARTFOLDERS", "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"SMARTFOLDERS env var is not valid JSON: {e}")
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _or_group(terms: List[list]) -> list:
    """Combine criteria groups with IMAP's binary OR, right-nested for n > 2."""
    if len(terms) == 1:
        return terms[0]
    return ["OR", terms[0], _or_group(terms[1:])]


def _build_criteria(params: Dict[str, Any]) -> list:
    """Translate smart folder params into an IMAPClient search criteria list."""
    criteria: list = []

    if _as_bool(params.get("unread", False)):
        criteria.append("UNSEEN")
    if _as_bool(params.get("flagged", False)):
        criteria.append("FLAGGED")

    # Receiving address: delivered-to shows up in TO or CC headers
    to_addrs = _as_list(params.get("to", ""))
    if to_addrs:
        criteria.append(_or_group(
            [["OR", ["TO", addr], ["CC", addr]] for addr in to_addrs]
        ))

    from_addrs = _as_list(params.get("from", ""))
    if from_addrs:
        criteria.append(_or_group([["FROM", addr] for addr in from_addrs]))

    subject = str(params.get("subject", "")).strip()
    if subject:
        criteria.extend(["SUBJECT", subject])

    text = str(params.get("text", "")).strip()
    if text:
        criteria.extend(["TEXT", text])

    days = params.get("days")
    if days:
        criteria.extend(["SINCE", date.today() - timedelta(days=int(days))])

    return criteria if criteria else ["ALL"]


def _parse_message_date(msg: email.message.Message) -> datetime:
    try:
        parsed = parsedate_to_datetime(msg.get("Date", ""))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception as _e:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def run_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """Run a smart folder search and return counts, messages, and a text digest."""
    username, password = require_auth()

    folders = _as_list(params.get("folders", "")) or ["INBOX"]
    limit = min(int(params.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
    criteria = _build_criteria(params)

    collected: List[Dict[str, Any]] = []
    total_matched = 0
    client = None
    try:
        client = _get_imap_client(username, password)

        for folder in folders:
            try:
                client.select_folder(folder, readonly=True)
                matched = client.search(criteria)
            except Exception as e:
                logger.error(f"Smart folder search failed in {folder}: {e}")
                continue

            total_matched += len(matched)
            # UID order approximates arrival order; take the newest per folder
            recent_ids = list(matched)[-limit:]
            if not recent_ids:
                continue

            response = client.fetch(recent_ids, [b"FLAGS", b"BODY.PEEK[HEADER]"])
            for msg_id, data in response.items():
                raw_header = None
                for key in (b"BODY[HEADER]", "BODY[HEADER]"):
                    if key in data:
                        raw_header = data[key]
                        break
                if raw_header is None:
                    continue

                msg = email.message_from_bytes(raw_header)
                flags = data.get(b"FLAGS", data.get("FLAGS", []))
                flags = [f.decode() if isinstance(f, bytes) else f for f in flags]
                subject = _decode_mime_header(msg.get("Subject", ""))
                # message:// deep links resolve on iOS/macOS for messages Mail
                # has synced on-device; unresolvable ones just open Mail.
                mid = (msg.get("Message-ID") or "").strip()
                collected.append({
                    "id": str(msg_id),
                    "folder": folder,
                    "subject": " ".join(subject.split()) or "(no subject)",
                    "from": " ".join(_decode_mime_header(msg.get("From", "")).split()),
                    "to": _decode_mime_header(msg.get("To", "")),
                    "date": _parse_message_date(msg).isoformat(),
                    "_sort": _parse_message_date(msg),
                    "unread": "\\Seen" not in flags,
                    "message_url": (
                        "message://" + urllib.parse.quote(mid, safe="") if mid else ""
                    ),
                })
    finally:
        if client is not None:
            _close_imap_client(client)

    collected.sort(key=lambda m: m["_sort"], reverse=True)
    collected = collected[:limit]
    for m in collected:
        del m["_sort"]

    return {
        "name": params.get("name", "Mail search"),
        "folders": folders,
        "total_matched": total_matched,
        "unread": sum(1 for m in collected if m["unread"]),
        "messages": collected,
        "text": _format_digest(params.get("name", "Mail search"), folders,
                               total_matched, collected),
    }


def _sender_display(from_header: str) -> str:
    """Prefer the display name; fall back to the bare address."""
    name, _, addr = from_header.rpartition("<")
    name = name.strip().strip('"')
    return name if name else addr.rstrip(">").strip() or from_header


def _format_when(iso_date: str) -> str:
    try:
        stamp = datetime.fromisoformat(iso_date).astimezone(_digest_tz())
        return stamp.strftime("%a %b %-d, %-I:%M %p")
    except Exception as _e:
        return iso_date


def _format_digest(name: str, folders: List[str], total: int,
                   messages: List[Dict[str, Any]]) -> str:
    """Plain-text digest a Shortcut can pass straight to a Show action."""
    if not messages:
        return f"{name}: no matching messages."

    unread = sum(1 for m in messages if m["unread"])
    shown = len(messages)
    header = f"{name} — {unread} unread · {total} matched"
    if total > shown:
        header += f" (showing {shown})"

    show_folder = len(folders) > 1
    lines = [header, ""]
    for m in messages:
        marker = "●" if m["unread"] else "○"
        lines.append(f"{marker} {_sender_display(m['from'])} — {m['subject']}")
        meta = f"   {_format_when(m['date'])}"
        if show_folder:
            meta += f" · {m['folder']}"
        lines.append(meta)
    return "\n".join(lines)


HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #111; --fg: #e5e5e5; --dim: #8a8a8a; --line: #2a2a2a;
    --accent: #6366f1;
  }}
  @media (prefers-color-scheme: light) {{
    :root {{ --bg: #fff; --fg: #1a1a1a; --dim: #767676; --line: #e5e5e5; }}
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: var(--bg); color: var(--fg);
    margin: 0; padding: 16px 16px calc(24px + env(safe-area-inset-bottom));
    -webkit-text-size-adjust: 100%;
  }}
  h1 {{ font-size: 1.15rem; margin: 0.25rem 0 0.15rem; }}
  .sub {{ color: var(--dim); font-size: 0.85rem; margin-bottom: 0.5rem; }}
  .row {{
    display: flex; gap: 10px; align-items: flex-start;
    padding: 12px 2px; border-bottom: 1px solid var(--line);
    text-decoration: none; color: inherit;
  }}
  .dot {{
    flex: none; width: 9px; height: 9px; border-radius: 50%;
    margin-top: 6px; background: transparent;
  }}
  .unread .dot {{ background: var(--accent); }}
  .txt {{ min-width: 0; }}
  .s {{ font-size: 0.95rem; }}
  .unread .s {{ font-weight: 650; }}
  .j {{ font-size: 0.9rem; color: var(--dim); margin-top: 1px; overflow-wrap: anywhere; }}
  .unread .j {{ color: var(--fg); }}
  .t {{ font-size: 0.78rem; color: var(--dim); margin-top: 2px; }}
  .empty {{ color: var(--dim); padding: 24px 0; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="sub">{sub}</div>
{rows}
</body>
</html>"""


def render_html(result: Dict[str, Any]) -> str:
    """Tappable HTML view — each row deep-links into Mail via message://."""
    esc = html_escape.escape
    name = esc(result["name"])
    show_folder = len(result["folders"]) > 1

    if not result["messages"]:
        rows = '<div class="empty">No matching messages.</div>'
        sub = "nothing matched"
    else:
        shown = len(result["messages"])
        sub = f"{result['unread']} unread · {result['total_matched']} matched"
        if result["total_matched"] > shown:
            sub += f" (showing {shown})"
        parts = []
        for m in result["messages"]:
            when = esc(_format_when(m["date"]))
            if show_folder:
                when += f" · {esc(m['folder'])}"
            inner = (
                f'<span class="dot"></span><div class="txt">'
                f'<div class="s">{esc(_sender_display(m["from"]))}</div>'
                f'<div class="j">{esc(m["subject"])}</div>'
                f'<div class="t">{when}</div></div>'
            )
            cls = "row unread" if m["unread"] else "row"
            url = m.get("message_url")
            if url:
                parts.append(f'<a class="{cls}" href="{esc(url)}">{inner}</a>')
            else:
                parts.append(f'<div class="{cls}">{inner}</div>')
        rows = "\n".join(parts)

    return HTML_PAGE.format(title=name, sub=sub, rows=rows)
