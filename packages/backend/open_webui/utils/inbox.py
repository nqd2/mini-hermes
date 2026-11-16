"""Persistent inbox visibility for the retained product surface.

The Office Agent no longer ships social, calendar, channel, automation, or
comment products. Their notification objects must never be queried against the
fresh V2 schema. The retained inbox carries system announcements only.
"""

from sqlalchemy import and_, false, select
from sqlalchemy.orm import load_only

from open_webui.models.notifications import Notification


def source_models() -> dict:
    """No product resource can currently be attached to an inbox item."""
    return {}


async def validate_source(event_type, resource_type, resource_id, object_type, object_id, db):
    if event_type != 'system.announcement':
        raise ValueError('Unsupported notification event')
    if any((resource_type, resource_id, object_type, object_id)):
        raise ValueError('System announcements cannot attach resources')


async def resource_queries(user, db):
    return {}


async def eligible_recipients(recipients, resource_type, resource_id, actor_id, db):
    from open_webui.models.users import User

    if resource_type is not None or resource_id is not None:
        return {}
    users = (await db.scalars(select(User).where(User.id.in_(recipients), User.role.in_(['user', 'admin'])))).all()
    return {row.id: recipients[row.id] for row in users}


async def visible_clause(user, db):
    if user.role not in {'user', 'admin'}:
        return false()
    return and_(
        Notification.user_id == user.id,
        Notification.event_type == 'system.announcement',
        Notification.resource_type.is_(None),
        Notification.object_type.is_(None),
    )


async def hydrate(rows, db):
    """Attach public actor data without loading retired resource tables."""
    from open_webui.models.users import User

    actors = {
        user.id: {'id': user.id, 'name': user.name}
        for user in (
            await db.scalars(
                select(User)
                .options(load_only(User.id, User.name))
                .where(User.id.in_({row.actor_id for row in rows if row.actor_id}))
            )
        ).all()
    }
    return [
        {
            **{column.name: getattr(row, column.name) for column in Notification.__table__.columns},
            'actor': actors.get(row.actor_id),
            'title': '',
            'preview': '',
            'data': dict(row.data or {}),
            # Epoch nanoseconds must not become imprecise JavaScript cursor values.
            'created_at': str(row.created_at),
            'updated_at': str(row.updated_at),
            'seen_at': str(row.seen_at) if row.seen_at is not None else None,
            'read_at': str(row.read_at) if row.read_at is not None else None,
        }
        for row in rows
    ]


async def consume_event(event, db):
    """Retired product events cannot create persistent inbox records."""
    return set()
