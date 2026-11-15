"""Calendar tools exposed through the built-in tool registry."""

import logging
from typing import Optional

from fastapi import Request

from open_webui.utils.json_codec import JSONCodec

log = logging.getLogger(__name__)

# Calendar tools


MAX_CALENDAR_RANGE_END_NS = 2**63 - 1


def _get_user_tz(user_dict: dict):
    """Get the user's timezone as a ZoneInfo, falling back to UTC."""
    from zoneinfo import ZoneInfo

    tz_name = None
    if user_dict:
        tz_name = user_dict.get('timezone')
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return ZoneInfo('UTC')


def _dt_to_ns(dt_str: str, tz) -> int:
    """Convert a datetime string to nanoseconds since epoch, interpreting in the given timezone."""
    from datetime import datetime

    dt = datetime.fromisoformat(dt_str)
    # If naive (no timezone info), localize to user's timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return int(dt.timestamp() * 1_000) * 1_000_000


def _ns_to_dt(ns: int, tz) -> str:
    """Convert nanoseconds since epoch to a datetime string in the given timezone."""
    from datetime import datetime

    seconds = ns / 1_000_000_000
    dt = datetime.fromtimestamp(seconds, tz=tz)
    return dt.strftime('%Y-%m-%d %H:%M')


def _event_to_dict(event, tz) -> dict:
    """Convert a calendar event model to a human-friendly dict with local timestamps."""
    alert_minutes = None
    if event.meta and 'alert_minutes' in event.meta:
        alert_minutes = event.meta['alert_minutes']
    return {
        'id': event.id,
        'calendar_id': event.calendar_id,
        'title': event.title,
        'description': event.description or '',
        'start': _ns_to_dt(event.start_at, tz),
        'end': _ns_to_dt(event.end_at, tz) if event.end_at else None,
        'all_day': event.all_day,
        'location': event.location or '',
        'reminder_minutes': alert_minutes if alert_minutes is not None else 10,
        'color': event.color,
        'is_cancelled': event.is_cancelled,
    }


async def search_calendar_events(
    query: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    count: int = 10,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search calendar events, reminders, and scheduled items by text and/or date range.
    Helpful for finding upcoming events, reminders, or schedule items.

    :param query: Search text to match against event title, description, or location (optional)
    :param start: Only return events starting at or after this datetime, e.g. "2026-04-20 00:00" (optional)
    :param end: Only return events starting before this datetime, e.g. "2026-04-27 00:00" (optional)
    :param count: Maximum number of events to return (default: 10)
    :return: JSON list of matching events with id, title, description, start, end, calendar_id, location
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.calendar import CalendarEvents

        user_id = __user__.get('id')
        tz = _get_user_tz(__user__)

        if isinstance(count, str):
            try:
                count = int(count)
            except ValueError:
                count = 10

        if start or end:
            # Date range query — use get_events_by_range
            try:
                start_ns = _dt_to_ns(start, tz) if start else 0
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid start datetime: {e}'})

            try:
                end_ns = _dt_to_ns(end, tz) if end else MAX_CALENDAR_RANGE_END_NS
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid end datetime: {e}'})

            items = await CalendarEvents.get_events_by_range(
                user_id=user_id,
                start=start_ns,
                end=end_ns,
            )

            # Apply text filter if query is also provided
            if query:
                q = query.lower()
                items = [
                    e
                    for e in items
                    if q in (e.title or '').lower()
                    or q in (e.description or '').lower()
                    or q in (e.location or '').lower()
                ]

            events = [_event_to_dict(item, tz) for item in items[:count]]
            return JSONCodec.dumps(
                {'events': events, 'total': len(items)},
                ensure_ascii=False,
            )
        else:
            # Text-only search
            result = await CalendarEvents.search_events(
                user_id=user_id,
                query=query,
                skip=0,
                limit=count,
            )

            events = [_event_to_dict(item, tz) for item in result.items]
            return JSONCodec.dumps(
                {'events': events, 'total': result.total},
                ensure_ascii=False,
            )
    except Exception as e:
        log.exception(f'search_calendar_events error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def create_calendar_event(
    title: str,
    start: str,
    end: Optional[str] = None,
    description: Optional[str] = None,
    calendar_id: Optional[str] = None,
    all_day: bool = False,
    location: Optional[str] = None,
    reminder_minutes: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a calendar event, reminder, or alarm. Use this when the user wants to
    schedule an event, set a reminder, create an alarm, or says things like
    "remind me", "don't let me forget", "notify me at", or "add to my calendar".
    For simple reminders, omit end/location/all_day and set reminder_minutes to 0.

    :param title: Event or reminder title (e.g. "Team standup", "Take medicine", "Call mom")
    :param start: Start datetime in the user's local time (e.g. "2026-04-20 09:00")
    :param end: End datetime in the user's local time (optional — omit for reminders or point-in-time events)
    :param description: Event description or notes (optional)
    :param calendar_id: Target calendar ID (optional, uses default calendar if omitted)
    :param all_day: Whether this is an all-day event (default: false)
    :param location: Event location (optional)
    :param reminder_minutes: Minutes before the event to send a notification (optional, default: 10). Use 0 for "at time of event", -1 for no notification.
    :return: JSON with the created event details including id
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.calendar import CalendarEventForm, CalendarEvents, Calendars

        user_id = __user__.get('id')

        # Resolve calendar_id: use provided, or fall back to default
        if not calendar_id:
            calendars = await Calendars.get_calendars_by_user(user_id)
            default_cal = next((c for c in calendars if c.is_default), None)
            if not default_cal and calendars:
                default_cal = calendars[0]
            if not default_cal:
                return JSONCodec.dumps({'error': 'No calendars found. Cannot create event.'})
            calendar_id = default_cal.id

        # Verify access
        cal = await Calendars.get_calendar_by_id(calendar_id)
        if not cal:
            return JSONCodec.dumps({'error': 'Calendar not found'})
        if cal.user_id != user_id and __user__.get('role') != 'admin':
            from open_webui.models.access_grants import AccessGrants
            from open_webui.models.groups import Groups

            user_group_ids = [g.id for g in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
            if not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='calendar',
                resource_id=cal.id,
                permission='write',
                user_group_ids=set(user_group_ids),
            ):
                return JSONCodec.dumps({'error': 'Access denied to this calendar'})

        # Coerce boolean from LLM
        if isinstance(all_day, str):
            all_day = all_day.lower() in ('true', '1', 'yes')

        # Convert datetime strings to nanoseconds using user's timezone
        tz = _get_user_tz(__user__)
        try:
            start_ns = _dt_to_ns(start, tz)
        except (ValueError, TypeError) as e:
            return JSONCodec.dumps({'error': f'Invalid start datetime: {e}. Use format like "2026-04-20 09:00"'})

        end_ns = None
        if end:
            try:
                end_ns = _dt_to_ns(end, tz)
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid end datetime: {e}. Use format like "2026-04-20 10:00"'})
        elif not all_day:
            # Default to 1 hour duration
            end_ns = start_ns + 3_600_000_000_000

        # Build meta with reminder setting
        meta = {}
        if reminder_minutes is not None:
            if isinstance(reminder_minutes, str):
                try:
                    reminder_minutes = int(reminder_minutes)
                except ValueError:
                    reminder_minutes = 10
            meta['alert_minutes'] = reminder_minutes
        else:
            meta['alert_minutes'] = 10

        form = CalendarEventForm(
            calendar_id=calendar_id,
            title=title,
            description=description,
            start_at=start_ns,
            end_at=end_ns,
            all_day=all_day,
            location=location,
            meta=meta,
        )

        event = await CalendarEvents.insert_new_event(user_id, form)
        if not event:
            return JSONCodec.dumps({'error': 'Failed to create event'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                **_event_to_dict(event, tz),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'create_calendar_event error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def update_calendar_event(
    event_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    all_day: Optional[bool] = None,
    location: Optional[str] = None,
    is_cancelled: Optional[bool] = None,
    reminder_minutes: Optional[int] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Update an existing calendar event. Only provided fields are changed;
    omitted fields stay the same.

    :param event_id: The ID of the event to update
    :param title: New event title (optional)
    :param description: New event description (optional)
    :param start: New start datetime string in your local time, e.g. "2026-04-20 09:00" (optional)
    :param end: New end datetime string in your local time (optional)
    :param all_day: Whether this is an all-day event (optional)
    :param location: New event location (optional)
    :param is_cancelled: Set to true to cancel the event (optional)
    :param reminder_minutes: Minutes before the event to send a reminder notification (optional). Use 0 for "at time of event", -1 for no reminder. Accepts any positive integer for custom timing (e.g. 120 for 2 hours before).
    :return: JSON with the updated event details
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.calendar import CalendarEvents, CalendarEventUpdateForm, Calendars
        from open_webui.models.groups import Groups

        user_id = __user__.get('id')

        event = await CalendarEvents.get_event_by_id(event_id)
        if not event:
            return JSONCodec.dumps({'error': 'Event not found'})

        # Check write access to the event's calendar
        cal = await Calendars.get_calendar_by_id(event.calendar_id)
        if not cal:
            return JSONCodec.dumps({'error': 'Access denied'})
        if cal.user_id != user_id and __user__.get('role') != 'admin':
            user_group_ids = [g.id for g in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
            if not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='calendar',
                resource_id=cal.id,
                permission='write',
                user_group_ids=set(user_group_ids),
            ):
                return JSONCodec.dumps({'error': 'Access denied'})

        # Coerce boolean strings from LLM
        if isinstance(all_day, str):
            all_day = all_day.lower() in ('true', '1', 'yes')
        if isinstance(is_cancelled, str):
            is_cancelled = is_cancelled.lower() in ('true', '1', 'yes')

        # Convert datetime strings to nanoseconds using user's timezone
        tz = _get_user_tz(__user__)
        start_ns = None
        if start is not None:
            try:
                start_ns = _dt_to_ns(start, tz)
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid start datetime: {e}'})

        end_ns = None
        if end is not None:
            try:
                end_ns = _dt_to_ns(end, tz)
            except (ValueError, TypeError) as e:
                return JSONCodec.dumps({'error': f'Invalid end datetime: {e}'})

        # Build meta update with reminder setting if provided
        meta = None
        if reminder_minutes is not None:
            if isinstance(reminder_minutes, str):
                try:
                    reminder_minutes = int(reminder_minutes)
                except ValueError:
                    reminder_minutes = None
            if reminder_minutes is not None:
                meta = {'alert_minutes': reminder_minutes}

        update_fields = {
            'title': title,
            'description': description,
            'start_at': start_ns,
            'end_at': end_ns,
            'all_day': all_day,
            'location': location,
            'is_cancelled': is_cancelled,
            'meta': meta,
        }
        form = CalendarEventUpdateForm(**{k: v for k, v in update_fields.items() if v is not None})

        updated = await CalendarEvents.update_event_by_id(event_id, form)
        if not updated:
            return JSONCodec.dumps({'error': 'Failed to update event'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                **_event_to_dict(updated, tz),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'update_calendar_event error: {e}')
        return JSONCodec.dumps({'error': str(e)})


async def delete_calendar_event(
    event_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Delete a calendar event permanently.

    :param event_id: The ID of the event to delete
    :return: JSON confirming the event was deleted
    """
    if __request__ is None:
        return JSONCodec.dumps({'error': 'Request context not available'})

    if not __user__:
        return JSONCodec.dumps({'error': 'User context not available'})

    try:
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.calendar import CalendarEvents, Calendars
        from open_webui.models.groups import Groups

        user_id = __user__.get('id')

        event = await CalendarEvents.get_event_by_id(event_id)
        if not event:
            return JSONCodec.dumps({'error': 'Event not found'})

        # Check write access
        cal = await Calendars.get_calendar_by_id(event.calendar_id)
        if not cal:
            return JSONCodec.dumps({'error': 'Access denied'})
        if cal.user_id != user_id and __user__.get('role') != 'admin':
            user_group_ids = [g.id for g in await Groups.get_groups_by_member_id(user_id, include_inherited=True)]
            if not await AccessGrants.has_access(
                user_id=user_id,
                resource_type='calendar',
                resource_id=cal.id,
                permission='write',
                user_group_ids=set(user_group_ids),
            ):
                return JSONCodec.dumps({'error': 'Access denied'})

        title = event.title
        result = await CalendarEvents.delete_event_by_id(event_id)
        if not result:
            return JSONCodec.dumps({'error': 'Failed to delete event'})

        return JSONCodec.dumps(
            {
                'status': 'success',
                'message': f'Event "{title}" deleted',
            },
            ensure_ascii=False,
        )
    except Exception as e:
        log.exception(f'delete_calendar_event error: {e}')
        return JSONCodec.dumps({'error': str(e)})
