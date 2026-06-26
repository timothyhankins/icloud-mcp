"""CalDAV tools for Reminders (VTODO) management.

Apple Reminders are VTODO components served over the same CalDAV endpoint as
calendar events. Reminder lists are CalDAV collections whose supported component
set includes ``VTODO``. This module mirrors the patterns in ``calendar.py``:
a shared stateless client, strict iCloud-friendly iCal formatting on create, and
a per-host client + raw PUT for update/delete to avoid parent-dependency issues.
"""

import caldav
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from mcp.server.fastmcp import Context
from .auth import require_auth
from .calendar import _get_caldav_client


def _supports_todo(calendar: caldav.Calendar) -> bool:
    """Return True if a calendar collection advertises VTODO support."""
    try:
        return "VTODO" in (calendar.get_supported_components() or [])
    except Exception:
        # Some collections don't advertise the component set; treat as non-todo.
        return False


def _reminder_calendars(principal: caldav.Principal) -> List[caldav.Calendar]:
    """Return only the calendars that hold reminders (VTODO collections)."""
    return [cal for cal in principal.calendars() if _supports_todo(cal)]


def _escape(text: str) -> str:
    """Escape special characters for an iCalendar text value."""
    return (
        text.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("\n", "\\n")
    )


def _format_due(due: str) -> str:
    """Build a DUE property line from an ISO date or datetime string."""
    # Date-only (YYYY-MM-DD) -> all-day style DUE
    if len(due) == 10:
        d = datetime.fromisoformat(due)
        return f"DUE;VALUE=DATE:{d.strftime('%Y%m%d')}"
    dt = datetime.fromisoformat(due)
    return f"DUE:{dt.strftime('%Y%m%dT%H%M%S')}"


def _iso(value: Any) -> Optional[str]:
    """Best-effort ISO string for a vobject date/datetime value."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _serialize_todo(todo: caldav.CalendarObjectResource, list_name: str = "") -> Dict[str, Any]:
    """Convert a loaded Todo resource into a plain dict."""
    vtodo = todo.vobject_instance.vtodo
    status = str(vtodo.status.value) if hasattr(vtodo, "status") and vtodo.status else "NEEDS-ACTION"
    return {
        "id": str(todo.url),
        "title": str(vtodo.summary.value) if hasattr(vtodo, "summary") and vtodo.summary else "",
        "notes": str(vtodo.description.value) if hasattr(vtodo, "description") and vtodo.description else "",
        "due": _iso(vtodo.due.value) if hasattr(vtodo, "due") and vtodo.due else None,
        "priority": int(vtodo.priority.value) if hasattr(vtodo, "priority") and vtodo.priority else None,
        "status": status,
        "completed": status == "COMPLETED",
        "list": list_name,
        "url": str(todo.url),
    }


async def list_reminder_lists(context: Context) -> List[Dict[str, Any]]:
    """
    List all reminder lists (CalDAV collections that support VTODO).

    Returns:
        List of reminder lists with id, name, and url
    """
    email, password = require_auth()
    client = _get_caldav_client(email, password)
    principal = client.principal()

    result = []
    for cal in _reminder_calendars(principal):
        result.append({
            "id": str(cal.url),
            "name": cal.name or "Unnamed List",
            "url": str(cal.url),
        })
    return result


async def list_reminders(
    context: Context,
    list_id: Optional[str] = None,
    include_completed: bool = False,
) -> List[Dict[str, Any]]:
    """
    List reminders (todos), optionally scoped to one list.

    Args:
        list_id: Specific reminder list URL/ID (optional, defaults to all lists)
        include_completed: Include completed reminders (default: only pending)

    Returns:
        List of reminders with details
    """
    email, password = require_auth()
    client = _get_caldav_client(email, password)
    principal = client.principal()

    if list_id:
        lists_to_search = [caldav.Calendar(client=client, url=list_id)]
    else:
        lists_to_search = _reminder_calendars(principal)

    result = []
    for cal in lists_to_search:
        try:
            todos = cal.get_todos(include_completed=include_completed)
        except Exception:
            continue
        list_name = cal.name or "Unknown"
        for todo in todos:
            try:
                result.append(_serialize_todo(todo, list_name))
            except Exception:
                # Skip malformed todos
                continue
    return result


async def create_reminder(
    context: Context,
    title: str,
    due: Optional[str] = None,
    notes: Optional[str] = None,
    priority: Optional[int] = None,
    list_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a new reminder (VTODO).

    Args:
        title: Reminder title
        due: Due date/datetime in ISO format (optional). Date-only (YYYY-MM-DD)
            creates an all-day reminder; include a time for a timed reminder.
        notes: Reminder notes/description (optional)
        priority: Priority 1-9 per RFC 5545 (1=high, 5=medium, 9=low) (optional)
        list_id: Target reminder list URL/ID (optional, defaults to first VTODO list)

    Returns:
        Created reminder details
    """
    email, password = require_auth()
    client = _get_caldav_client(email, password)
    principal = client.principal()

    if list_id:
        calendar = caldav.Calendar(client=client, url=list_id)
    else:
        reminder_lists = _reminder_calendars(principal)
        if not reminder_lists:
            raise ValueError("No reminder lists found (no VTODO-capable collections)")
        calendar = reminder_lists[0]

    now = datetime.now(timezone.utc)
    # UID without dots (iCloud compatible), matching calendar.py
    uid = f"{int(now.timestamp())}{now.microsecond}@icloud-mcp"

    ical_data = (
        "BEGIN:VCALENDAR\n"
        "VERSION:2.0\n"
        "PRODID:-//iCloud MCP//EN\n"
        "CALSCALE:GREGORIAN\n"
        "BEGIN:VTODO\n"
        f"UID:{uid}\n"
        f"DTSTAMP:{now.strftime('%Y%m%dT%H%M%SZ')}\n"
        f"SUMMARY:{_escape(title)}\n"
        "STATUS:NEEDS-ACTION\n"
    )
    if due:
        ical_data += _format_due(due) + "\n"
    if notes:
        ical_data += f"DESCRIPTION:{_escape(notes)}\n"
    if priority is not None:
        ical_data += f"PRIORITY:{int(priority)}\n"
    ical_data += "END:VTODO\nEND:VCALENDAR"

    try:
        todo = calendar.add_todo(ical_data)
    except Exception as e:
        raise ValueError(f"Failed to create reminder in list '{calendar.name}': {str(e)}")

    return {
        "id": str(todo.url),
        "title": title,
        "due": due,
        "notes": notes or "",
        "priority": priority,
        "status": "NEEDS-ACTION",
        "completed": False,
        "list": calendar.name,
        "url": str(todo.url),
    }


def _load_todo(email: str, password: str, reminder_id: str):
    """Load a Todo using a client bound to the reminder's own host.

    iCloud serves objects from per-shard hosts (e.g. p72-caldav.icloud.com);
    a client built from the object URL avoids URL-join errors, matching the
    approach in calendar.update_event / delete_event.
    """
    parsed = urlparse(reminder_id)
    host_url = f"{parsed.scheme}://{parsed.netloc}"
    host_client = caldav.DAVClient(url=host_url, username=email, password=password)
    todo = caldav.Todo(client=host_client, url=reminder_id)
    todo.load()
    return host_client, todo


def _put_todo(host_client: caldav.DAVClient, reminder_id: str, todo: caldav.Todo) -> None:
    """Serialize and PUT a modified todo directly (avoids parent dependency)."""
    updated_ical = todo.vobject_instance.serialize()
    host_client.put(reminder_id, updated_ical, {"Content-Type": "text/calendar; charset=utf-8"})


async def update_reminder(
    context: Context,
    reminder_id: str,
    title: Optional[str] = None,
    due: Optional[str] = None,
    notes: Optional[str] = None,
    priority: Optional[int] = None,
    completed: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Update an existing reminder.

    Args:
        reminder_id: Reminder URL/ID
        title: New title (optional)
        due: New due date/datetime in ISO format (optional)
        notes: New notes/description (optional)
        priority: New priority 1-9 (optional)
        completed: Mark complete (True) or reopen (False) (optional)

    Returns:
        Updated reminder details
    """
    email, password = require_auth()

    try:
        host_client, todo = _load_todo(email, password, reminder_id)
    except Exception as e:
        raise Exception(f"Error loading reminder: {str(e)}")

    vtodo = todo.vobject_instance.vtodo

    def _set(name: str, value: Any) -> None:
        if hasattr(vtodo, name):
            getattr(vtodo, name).value = value
        else:
            vtodo.add(name).value = value

    if title is not None:
        _set("summary", title)
    if notes is not None:
        _set("description", notes)
    if priority is not None:
        _set("priority", str(int(priority)))
    if due is not None:
        # Remove any existing DUE so we can re-add with the right VALUE param
        if hasattr(vtodo, "due"):
            vtodo.remove(vtodo.due)
        if len(due) == 10:
            d = vtodo.add("due")
            d.value = datetime.fromisoformat(due).date()
        else:
            d = vtodo.add("due")
            d.value = datetime.fromisoformat(due)

    if completed is not None:
        now = datetime.now(timezone.utc)
        if completed:
            _set("status", "COMPLETED")
            _set("percent-complete", "100")
            _set("completed", now)
        else:
            _set("status", "NEEDS-ACTION")
            _set("percent-complete", "0")
            if hasattr(vtodo, "completed"):
                vtodo.remove(vtodo.completed)

    try:
        _put_todo(host_client, reminder_id, todo)
    except Exception as e:
        raise Exception(f"Error saving reminder: {str(e)}")

    return _serialize_todo(todo)


async def complete_reminder(context: Context, reminder_id: str) -> Dict[str, Any]:
    """
    Mark a reminder as completed.

    Args:
        reminder_id: Reminder URL/ID

    Returns:
        Updated reminder details
    """
    return await update_reminder(context, reminder_id, completed=True)


async def delete_reminder(context: Context, reminder_id: str) -> Dict[str, str]:
    """
    Delete a reminder.

    Args:
        reminder_id: Reminder URL/ID to delete

    Returns:
        Confirmation message
    """
    email, password = require_auth()

    parsed = urlparse(reminder_id)
    host_url = f"{parsed.scheme}://{parsed.netloc}"
    host_client = caldav.DAVClient(url=host_url, username=email, password=password)
    todo = caldav.Todo(client=host_client, url=reminder_id)
    todo.delete()

    return {"status": "success", "message": f"Reminder {reminder_id} deleted"}


async def search_reminders(
    context: Context,
    query: str,
    list_id: Optional[str] = None,
    include_completed: bool = False,
) -> List[Dict[str, Any]]:
    """
    Search reminders by text (matches title and notes).

    Args:
        query: Search text
        list_id: Specific reminder list URL/ID (optional)
        include_completed: Include completed reminders (default: only pending)

    Returns:
        List of matching reminders
    """
    reminders = await list_reminders(context, list_id, include_completed)
    q = query.lower()
    return [
        r for r in reminders
        if q in r.get("title", "").lower() or q in r.get("notes", "").lower()
    ]
