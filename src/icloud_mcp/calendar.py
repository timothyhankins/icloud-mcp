"""CalDAV tools for calendar management."""

import caldav
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from fastmcp import Context
from .auth import require_auth
from .config import config


def _get_caldav_client(email: str, password: str) -> caldav.DAVClient:
    """Create CalDAV client (stateless)."""
    return caldav.DAVClient(
        url=config.CALDAV_SERVER,
        username=email,
        password=password
    )


async def list_calendars(context: Context) -> List[Dict[str, Any]]:
    """
    List all available calendars.

    Returns:
        List of calendars with id, name, and description
    """
    email, password = require_auth(context)
    client = _get_caldav_client(email, password)
    principal = client.principal()
    calendars = principal.calendars()

    result = []
    for cal in calendars:
        result.append({
            "id": str(cal.url),
            "name": cal.name or "Unnamed Calendar",
            "url": str(cal.url)
        })

    return result


async def list_events(
    context: Context,
    calendar_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    List calendar events with optional filtering.

    Args:
        calendar_id: Specific calendar URL/ID (optional, defaults to all non-reminder calendars)
        start_date: Start date filter in ISO format (YYYY-MM-DD)
        end_date: End date filter in ISO format (YYYY-MM-DD)

    Returns:
        List of events with details
    """
    email, password = require_auth(context)
    client = _get_caldav_client(email, password)
    principal = client.principal()

    # Parse dates
    start = datetime.fromisoformat(start_date) if start_date else datetime.now() - timedelta(days=90)
    end = datetime.fromisoformat(end_date) if end_date else datetime.now() + timedelta(days=365)

    result = []

    # Get calendars
    if calendar_id:
        calendars_to_search = [caldav.Calendar(client=client, url=calendar_id)]
    else:
        all_calendars = principal.calendars()
        if not all_calendars:
            return []

        # Filter out reminder calendars (they don't have events in the same format)
        calendars_to_search = [
            cal for cal in all_calendars
            if cal.name and '⚠' not in cal.name and 'reminder' not in cal.name.lower()
        ]

        # If all calendars are filtered out, search all
        if not calendars_to_search:
            calendars_to_search = all_calendars

    # Search events in all relevant calendars
    for calendar in calendars_to_search:
        try:
            # Fetch events using date_search
            events = calendar.date_search(start=start, end=end, expand=True)

            for event in events:
                try:
                    event.load()  # Ensure event data is loaded
                    vevent = event.vobject_instance.vevent

                    # Parse start/end dates safely
                    start_value = None
                    end_value = None

                    if hasattr(vevent, 'dtstart') and vevent.dtstart:
                        try:
                            start_value = vevent.dtstart.value
                            if hasattr(start_value, 'isoformat'):
                                start_value = start_value.isoformat()
                            else:
                                start_value = str(start_value)
                        except:
                            pass

                    if hasattr(vevent, 'dtend') and vevent.dtend:
                        try:
                            end_value = vevent.dtend.value
                            if hasattr(end_value, 'isoformat'):
                                end_value = end_value.isoformat()
                            else:
                                end_value = str(end_value)
                        except:
                            pass

                    result.append({
                        "id": str(event.url),
                        "summary": str(vevent.summary.value) if hasattr(vevent, 'summary') and vevent.summary else "",
                        "description": str(vevent.description.value) if hasattr(vevent, 'description') and vevent.description else "",
                        "start": start_value,
                        "end": end_value,
                        "location": str(vevent.location.value) if hasattr(vevent, 'location') and vevent.location else "",
                        "calendar": calendar.name or "Unknown",
                        "url": str(event.url)
                    })
                except Exception as e:
                    # Skip malformed events
                    continue
        except Exception as e:
            # Skip calendars that fail to search
            continue

    return result


async def create_event(
    context: Context,
    summary: str,
    start: str,
    end: str,
    description: Optional[str] = None,
    location: Optional[str] = None,
    calendar_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a new calendar event.

    Args:
        summary: Event title
        start: Start datetime in ISO format
        end: End datetime in ISO format
        description: Event description (optional)
        location: Event location (optional)
        calendar_id: Target calendar URL/ID (optional, defaults to primary)

    Returns:
        Created event details
    """
    email, password = require_auth(context)
    client = _get_caldav_client(email, password)
    principal = client.principal()

    # Get calendar
    if calendar_id:
        calendar = caldav.Calendar(client=client, url=calendar_id)
    else:
        calendars = principal.calendars()
        if not calendars:
            raise ValueError("No calendars found")
        calendar = calendars[0]

    # Build iCalendar data
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)

    ical_data = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//iCloud MCP//EN
BEGIN:VEVENT
UID:{datetime.now().timestamp()}@icloud-mcp
DTSTART:{start_dt.strftime('%Y%m%dT%H%M%S')}
DTEND:{end_dt.strftime('%Y%m%dT%H%M%S')}
SUMMARY:{summary}
"""

    if description:
        ical_data += f"DESCRIPTION:{description}\n"
    if location:
        ical_data += f"LOCATION:{location}\n"

    ical_data += "END:VEVENT\nEND:VCALENDAR"

    # Create event
    event = calendar.save_event(ical_data)

    return {
        "id": str(event.url),
        "summary": summary,
        "start": start,
        "end": end,
        "description": description or "",
        "location": location or "",
        "url": str(event.url)
    }


async def update_event(
    context: Context,
    event_id: str,
    summary: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None
) -> Dict[str, Any]:
    """
    Update an existing calendar event.

    Args:
        event_id: Event URL/ID
        summary: New event title (optional)
        start: New start datetime in ISO format (optional)
        end: New end datetime in ISO format (optional)
        description: New description (optional)
        location: New location (optional)

    Returns:
        Updated event details
    """
    email, password = require_auth(context)
    client = _get_caldav_client(email, password)

    # Load existing event
    event = caldav.Event(client=client, url=event_id)
    event.load()

    vevent = event.vobject_instance.vevent

    # Update fields
    if summary:
        vevent.summary.value = summary
    if start:
        vevent.dtstart.value = datetime.fromisoformat(start)
    if end:
        vevent.dtend.value = datetime.fromisoformat(end)
    if description is not None:
        if hasattr(vevent, 'description'):
            vevent.description.value = description
        else:
            vevent.add('description').value = description
    if location is not None:
        if hasattr(vevent, 'location'):
            vevent.location.value = location
        else:
            vevent.add('location').value = location

    # Save changes
    event.save()

    return {
        "id": str(event.url),
        "summary": str(vevent.summary.value) if hasattr(vevent, 'summary') else "",
        "start": vevent.dtstart.value.isoformat() if hasattr(vevent, 'dtstart') else None,
        "end": vevent.dtend.value.isoformat() if hasattr(vevent, 'dtend') else None,
        "description": str(vevent.description.value) if hasattr(vevent, 'description') else "",
        "location": str(vevent.location.value) if hasattr(vevent, 'location') else "",
        "url": str(event.url)
    }


async def delete_event(context: Context, event_id: str) -> Dict[str, str]:
    """
    Delete a calendar event.

    Args:
        event_id: Event URL/ID to delete

    Returns:
        Confirmation message
    """
    email, password = require_auth(context)
    client = _get_caldav_client(email, password)

    event = caldav.Event(client=client, url=event_id)
    event.delete()

    return {"status": "success", "message": f"Event {event_id} deleted"}


async def search_events(
    context: Context,
    query: str,
    calendar_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Search for events by text query.

    Args:
        query: Search text (matches summary and description)
        calendar_id: Specific calendar URL/ID (optional)
        start_date: Start date filter in ISO format (optional)
        end_date: End date filter in ISO format (optional)

    Returns:
        List of matching events
    """
    # Get all events
    events = await list_events(context, calendar_id, start_date, end_date)

    # Filter by query
    query_lower = query.lower()
    filtered_events = [
        event for event in events
        if query_lower in event.get("summary", "").lower()
        or query_lower in event.get("description", "").lower()
        or query_lower in event.get("location", "").lower()
    ]

    return filtered_events
