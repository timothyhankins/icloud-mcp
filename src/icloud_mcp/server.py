"""iCloud MCP Server.

CalDAV, CardDAV, and IMAP/SMTP access for Claude.

Local:   python run.py              (stdio for Claude Code)
Remote:  python run.py --http       (SSE for Railway / Claude.ai)

OAuth 2.0 auth for Claude.ai connector; static bearer token for Claude Code.
PIN gate on authorize step if MCP_AUTH_PIN is set.
"""

import os
import sys

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from . import calendar, contacts, email as email_module, reminders
from .auth import AuthenticationError
from .config import config

load_dotenv()

# Determine transport mode early — host/port are set at init time
_use_sse = "--http" in sys.argv or os.environ.get("MCP_TRANSPORT") == "sse"

# Module-level reference to the OAuth provider (needed by the PIN route handler)
_oauth_provider = None


def _build_mcp() -> FastMCP:
    """Build the FastMCP instance with optional OAuth for SSE mode."""
    global _oauth_provider

    if not _use_sse:
        return FastMCP("iCloud MCP Server")

    auth_token = os.environ.get("MCP_AUTH_TOKEN")
    auth_pin = os.environ.get("MCP_AUTH_PIN")
    base_url = os.environ.get("MCP_BASE_URL", "")
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", config.MCP_SERVER_PORT))

    if auth_token:
        from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

        from .oauth import ICloudOAuthProvider

        _oauth_provider = ICloudOAuthProvider(
            auth_token=auth_token,
            auth_pin=auth_pin,
            base_url=base_url,
        )

        return FastMCP(
            "iCloud MCP Server",
            host=host,
            port=port,
            auth=AuthSettings(
                issuer_url=base_url,
                resource_server_url=base_url,
                client_registration_options=ClientRegistrationOptions(
                    enabled=True,
                    valid_scopes=["read", "write"],
                    default_scopes=["read", "write"],
                ),
                revocation_options=None,
                required_scopes=[],
            ),
            auth_server_provider=_oauth_provider,
        )
    else:
        # No auth token — run without auth (local dev)
        return FastMCP(
            "iCloud MCP Server",
            host=host,
            port=port,
        )


mcp = _build_mcp()


# ============================================================================
# Health Check Endpoint
# ============================================================================

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    """Health check endpoint for Railway."""
    from starlette.responses import JSONResponse
    return JSONResponse({
        "status": "healthy",
        "service": "icloud-mcp",
        "transport": "sse" if _use_sse else "stdio"
    })


# ============================================================================
# PIN Confirmation Route (public, no auth required)
# ============================================================================

PIN_PAGE_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>iCloud MCP — Authorize</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #0a0a0a;
            color: #e0e0e0;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
        }}
        .card {{
            background: #1a1a1a;
            border: 1px solid #333;
            border-radius: 12px;
            padding: 2rem;
            max-width: 360px;
            width: 90%;
            text-align: center;
        }}
        h1 {{
            font-size: 1.4rem;
            margin: 0 0 0.5rem;
        }}
        p {{
            color: #888;
            font-size: 0.9rem;
            margin: 0 0 1.5rem;
        }}
        input[type="password"] {{
            width: 100%;
            padding: 12px;
            font-size: 1.1rem;
            border: 1px solid #444;
            border-radius: 8px;
            background: #111;
            color: #fff;
            text-align: center;
            letter-spacing: 0.3em;
            box-sizing: border-box;
            margin-bottom: 1rem;
        }}
        input[type="password"]:focus {{
            outline: none;
            border-color: #6366f1;
        }}
        button {{
            width: 100%;
            padding: 12px;
            font-size: 1rem;
            font-weight: 600;
            border: none;
            border-radius: 8px;
            background: #6366f1;
            color: #fff;
            cursor: pointer;
        }}
        button:hover {{
            background: #5558e6;
        }}
        .error {{
            color: #ef4444;
            font-size: 0.85rem;
            margin-top: 0.75rem;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h1>iCloud MCP</h1>
        <p>Enter your PIN to authorize this connection.</p>
        <form method="POST">
            <input type="hidden" name="session" value="{session}">
            <input type="password" name="pin" placeholder="PIN" autofocus>
            <button type="submit">Authorize</button>
            {error}
        </form>
    </div>
</body>
</html>"""


@mcp.custom_route("/confirm-pin", methods=["GET", "POST"])
async def confirm_pin(request):
    from starlette.responses import HTMLResponse, RedirectResponse

    if _oauth_provider is None:
        return HTMLResponse("<h1>Auth not configured</h1>", status_code=500)

    if request.method == "GET":
        session = request.query_params.get("session", "")
        return HTMLResponse(
            PIN_PAGE_HTML.format(session=session, error=""),
            status_code=200,
        )

    # POST — validate PIN
    form = await request.form()
    session_id = form.get("session", "")
    pin = form.get("pin", "")

    redirect_url = _oauth_provider.confirm_pin(session_id, pin)

    if redirect_url is None:
        return HTMLResponse(
            PIN_PAGE_HTML.format(
                session=session_id,
                error='<p class="error">Invalid PIN. Try again.</p>',
            ),
            status_code=200,
        )

    return RedirectResponse(url=redirect_url, status_code=302)


# ============================================================================
# Calendar Tools (CalDAV)
# ============================================================================

@mcp.tool()
async def calendar_list_calendars(context) -> list | dict:
    """
    List all available calendars.

    Returns a list of calendars with their IDs, names, and URLs.
    """
    try:
        return await calendar.list_calendars(context)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def calendar_list_events(
    context,
    calendar_id: str = None,
    start_date: str = None,
    end_date: str = None
) -> list | dict:
    """
    List calendar events with optional filtering.

    Args:
        calendar_id: Specific calendar URL/ID (optional)
        start_date: Start date in ISO format YYYY-MM-DD (optional)
        end_date: End date in ISO format YYYY-MM-DD (optional)
    """
    try:
        return await calendar.list_events(context, calendar_id, start_date, end_date)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def calendar_create_event(
    context,
    summary: str,
    start: str,
    end: str,
    description: str = None,
    location: str = None,
    attendees: list[str] = None,
    calendar_id: str = None
) -> dict:
    """
    Create a new calendar event.

    Args:
        summary: Event title
        start: Start datetime in ISO format (e.g., "2025-11-15T10:00:00")
        end: End datetime in ISO format (e.g., "2025-11-15T11:00:00")
        description: Event description (optional)
        location: Event location (optional)
        attendees: List of attendee email addresses to invite (optional)
        calendar_id: Target calendar URL/ID (optional)
    """
    try:
        return await calendar.create_event(context, summary, start, end, description, location, attendees, calendar_id)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def calendar_update_event(
    context,
    event_id: str,
    summary: str = None,
    start: str = None,
    end: str = None,
    description: str = None,
    location: str = None,
    attendees: list[str] = None
) -> dict:
    """
    Update an existing calendar event.

    Args:
        event_id: Event URL/ID
        summary: New event title (optional)
        start: New start datetime in ISO format (optional)
        end: New end datetime in ISO format (optional)
        description: New description (optional)
        location: New location (optional)
        attendees: New list of attendee email addresses (optional, replaces existing)
    """
    try:
        return await calendar.update_event(context, event_id, summary, start, end, description, location, attendees)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def calendar_delete_event(context, event_id: str) -> dict:
    """
    Delete a calendar event.

    Args:
        event_id: Event URL/ID to delete
    """
    try:
        return await calendar.delete_event(context, event_id)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def calendar_search_events(
    context,
    query: str,
    calendar_id: str = None,
    start_date: str = None,
    end_date: str = None
) -> list | dict:
    """
    Search for events by text query.

    Args:
        query: Search text (matches summary, description, location)
        calendar_id: Specific calendar URL/ID (optional)
        start_date: Start date in ISO format (optional)
        end_date: End date in ISO format (optional)
    """
    try:
        return await calendar.search_events(context, query, calendar_id, start_date, end_date)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


# ============================================================================
# Reminders Tools (CalDAV / VTODO)
# ============================================================================


@mcp.tool()
async def reminders_list_lists(context) -> list | dict:
    """
    List all reminder lists (VTODO-capable CalDAV collections).

    Returns a list of reminder lists with their IDs, names, and URLs.
    """
    try:
        return await reminders.list_reminder_lists(context)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def reminders_list(
    context,
    list_id: str = None,
    include_completed: bool = False
) -> list | dict:
    """
    List reminders, optionally scoped to a single list.

    Args:
        list_id: Specific reminder list URL/ID (optional, defaults to all lists)
        include_completed: Include completed reminders (default: only pending)
    """
    try:
        return await reminders.list_reminders(context, list_id, include_completed)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def reminders_create(
    context,
    title: str,
    due: str = None,
    notes: str = None,
    priority: int = None,
    list_id: str = None
) -> dict:
    """
    Create a new reminder.

    Args:
        title: Reminder title
        due: Due date/datetime in ISO format (optional). Date-only YYYY-MM-DD
            makes an all-day reminder; include a time for a timed reminder.
        notes: Reminder notes (optional)
        priority: Priority 1-9 (1=high, 5=medium, 9=low) (optional)
        list_id: Target reminder list URL/ID (optional, defaults to first list)
    """
    try:
        return await reminders.create_reminder(context, title, due, notes, priority, list_id)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def reminders_update(
    context,
    reminder_id: str,
    title: str = None,
    due: str = None,
    notes: str = None,
    priority: int = None,
    completed: bool = None
) -> dict:
    """
    Update an existing reminder.

    Args:
        reminder_id: Reminder URL/ID
        title: New title (optional)
        due: New due date/datetime in ISO format (optional)
        notes: New notes (optional)
        priority: New priority 1-9 (optional)
        completed: Mark complete (True) or reopen (False) (optional)
    """
    try:
        return await reminders.update_reminder(context, reminder_id, title, due, notes, priority, completed)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def reminders_complete(context, reminder_id: str) -> dict:
    """
    Mark a reminder as completed.

    Args:
        reminder_id: Reminder URL/ID
    """
    try:
        return await reminders.complete_reminder(context, reminder_id)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def reminders_delete(context, reminder_id: str) -> dict:
    """
    Delete a reminder.

    Args:
        reminder_id: Reminder URL/ID to delete
    """
    try:
        return await reminders.delete_reminder(context, reminder_id)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def reminders_search(
    context,
    query: str,
    list_id: str = None,
    include_completed: bool = False
) -> list | dict:
    """
    Search reminders by text (matches title and notes).

    Args:
        query: Search text
        list_id: Specific reminder list URL/ID (optional)
        include_completed: Include completed reminders (default: only pending)
    """
    try:
        return await reminders.search_reminders(context, query, list_id, include_completed)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


# ============================================================================
# Contacts Tools (CardDAV)
# ============================================================================

@mcp.tool()
async def contacts_list(context, limit: int = None) -> list | dict:
    """
    List all contacts.

    Args:
        limit: Maximum number of contacts to return (optional)
    """
    try:
        return await contacts.list_contacts(context, limit)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def contacts_get(context, contact_id: str) -> dict:
    """
    Get a specific contact by ID.

    Args:
        contact_id: Contact URL/ID
    """
    try:
        return await contacts.get_contact(context, contact_id)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def contacts_create(
    context,
    name: str,
    phones: list[str] = None,
    emails: list[str] = None,
    addresses: list[str] = None,
    organization: str = None,
    title: str = None
) -> dict:
    """
    Create a new contact.

    Args:
        name: Full name
        phones: List of phone numbers (optional)
        emails: List of email addresses (optional)
        addresses: List of postal addresses (optional)
        organization: Company/organization name (optional)
        title: Job title (optional)
    """
    try:
        return await contacts.create_contact(context, name, phones, emails, addresses, organization, title)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def contacts_update(
    context,
    contact_id: str,
    name: str = None,
    phones: list[str] = None,
    emails: list[str] = None,
    addresses: list[str] = None,
    organization: str = None,
    title: str = None
) -> dict:
    """
    Update an existing contact.

    Args:
        contact_id: Contact URL/ID
        name: New full name (optional)
        phones: New list of phone numbers (optional)
        emails: New list of email addresses (optional)
        addresses: New list of postal addresses (optional)
        organization: New company/organization (optional)
        title: New job title (optional)
    """
    try:
        return await contacts.update_contact(context, contact_id, name, phones, emails, addresses, organization, title)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def contacts_delete(context, contact_id: str) -> dict:
    """
    Delete a contact.

    Args:
        contact_id: Contact URL/ID to delete
    """
    try:
        return await contacts.delete_contact(context, contact_id)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def contacts_search(context, query: str) -> list | dict:
    """
    Search for contacts by text query.

    Args:
        query: Search text (matches name, email, phone)
    """
    try:
        return await contacts.search_contacts(context, query)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


# ============================================================================
# Email Tools (IMAP/SMTP)
# ============================================================================

@mcp.tool()
async def email_list_folders(context) -> list | dict:
    """
    List all email folders/mailboxes.

    Returns a list of folders with their names and flags.
    """
    try:
        return await email_module.list_folders(context)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def email_list_messages(
    context,
    folder: str = "INBOX",
    limit: int = 50,
    unread_only: bool = False
) -> list | dict:
    """
    List messages in a folder.

    Args:
        folder: Folder name (default: INBOX). Common folder names: INBOX, Sent Messages, Drafts, Trash, Archive
        limit: Maximum number of messages to return (default: 50)
        unread_only: Only return unread messages (default: False)

    Note: The Sent folder may be named "Sent Messages", "Sent", or "Sent Items" depending on your email provider.
    """
    try:
        return await email_module.list_messages(context, folder, limit, unread_only)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def email_get_message(
    context,
    message_id: str,
    folder: str = "INBOX",
    include_body: bool = True,
    full_html: bool = False
) -> dict:
    """
    Get a specific message with full details.

    Args:
        message_id: Message ID
        folder: Folder name (default: INBOX)
        include_body: Include message body content (default: True)
        full_html: Include full HTML body (default: False, only text body returned)
    """
    try:
        return await email_module.get_message(context, message_id, folder, include_body, full_html)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def email_get_messages(
    context,
    message_ids: list[str],
    folder: str = "INBOX",
    include_body: bool = True,
    full_html: bool = False
) -> list | dict:
    """
    Get multiple messages at once (bulk fetch).

    Args:
        message_ids: List of message IDs to fetch
        folder: Folder name (default: INBOX)
        include_body: Include message body content (default: True)
        full_html: Include full HTML body (default: False, only text body returned)
    """
    try:
        return await email_module.get_messages(context, message_ids, folder, include_body, full_html)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def email_search(
    context,
    query: str,
    folder: str = "INBOX",
    limit: int = 50
) -> list | dict:
    """
    Search for messages by text query.

    Args:
        query: Search text (searches subject and from fields)
        folder: Folder name (default: INBOX)
        limit: Maximum number of results (default: 50)
    """
    try:
        return await email_module.search_messages(context, query, folder, limit)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def email_send(
    context,
    to: str,
    subject: str,
    body: str,
    cc: str = None,
    bcc: str = None,
    html: bool = False
) -> dict:
    """
    Send an email message via SMTP.

    Args:
        to: Recipient email address
        subject: Email subject
        body: Email body content
        cc: CC recipients (optional, comma-separated)
        bcc: BCC recipients (optional, comma-separated)
        html: Whether body is HTML (default: False)
    """
    try:
        return await email_module.send_message(context, to, subject, body, cc, bcc, html)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def email_move(
    context,
    message_id: str,
    from_folder: str,
    to_folder: str
) -> dict:
    """
    Move a message to another folder.

    Args:
        message_id: Message ID
        from_folder: Source folder
        to_folder: Destination folder
    """
    try:
        return await email_module.move_message(context, message_id, from_folder, to_folder)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def email_delete(
    context,
    message_id: str,
    folder: str = "INBOX",
    permanent: bool = False
) -> dict:
    """
    Delete a message.

    Args:
        message_id: Message ID
        folder: Folder name (default: INBOX)
        permanent: Permanently delete (True) or move to trash (False)
    """
    try:
        return await email_module.delete_message(context, message_id, folder, permanent)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def email_mark_read(
    context,
    message_id: str,
    folder: str = "INBOX"
) -> dict:
    """
    Mark a message as read.

    Args:
        message_id: Message ID
        folder: Folder name (default: INBOX)
    """
    try:
        return await email_module.mark_as_read(context, message_id, folder)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


@mcp.tool()
async def email_mark_unread(
    context,
    message_id: str,
    folder: str = "INBOX"
) -> dict:
    """
    Mark a message as unread.

    Args:
        message_id: Message ID
        folder: Folder name (default: INBOX)
    """
    try:
        return await email_module.mark_as_unread(context, message_id, folder)
    except AuthenticationError as e:
        return {"error": str(e), "status": 401}
    except Exception as e:
        return {"error": str(e), "status": 500}


# ============================================================================
# Server Entrypoint
# ============================================================================

def main():
    if _use_sse:
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
