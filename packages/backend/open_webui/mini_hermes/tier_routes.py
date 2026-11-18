"""Admin provisioning and model routing settings."""

from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from open_webui.events import EVENTS, publish_event
from open_webui.mini_hermes.model_routing import (
    ModelRoutingConfig,
    get_connections,
    get_model_routing_config,
    update_model_routing_config,
)
from open_webui.mini_hermes.tier_service import PlanSnapshot, Tier, UsageSnapshot, tier_repository
from open_webui.utils.auth import get_admin_user, get_verified_user

router = APIRouter()


class PlanForm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tier: Tier
    monthly_request_limit: int | None = Field(default=None, strict=True, gt=0, le=2**31 - 1)


@router.get('/me/plan', response_model=UsageSnapshot)
async def get_my_plan(user=Depends(get_verified_user)):
    config = await get_model_routing_config()
    return await tier_repository.get_usage(user.id, datetime.now(timezone.utc), config.free_user_daily_limit)


@router.get('/admin/users/{user_id}/plan', response_model=PlanSnapshot)
async def get_user_plan(user_id: str, _admin=Depends(get_admin_user)):
    return await tier_repository.get_plan(user_id, include_pending=True)


@router.put('/admin/users/{user_id}/plan', response_model=PlanSnapshot)
async def set_user_plan(request: Request, user_id: str, form: PlanForm, admin=Depends(get_admin_user)):
    plan = await tier_repository.set_plan(user_id, form.tier, form.monthly_request_limit, admin.id)
    await publish_event(
        request,
        EVENTS.USER_UPDATED,
        actor=admin,
        subject_id=user_id,
        data={'tier': plan.tier, 'monthly_request_limit': plan.monthly_request_limit},
    )
    return plan


@router.get('/admin/model-routing')
async def get_routing(_admin=Depends(get_admin_user)):
    config = await get_model_routing_config()
    connections = await get_connections()
    return {
        'config': config,
        'connections': [
            {
                'id': connection.id,
                'url': urlsplit(connection.url).netloc.rsplit('@', 1)[-1],
                'enabled': connection.enabled,
            }
            for connection in connections.values()
        ],
    }


@router.put('/admin/model-routing', response_model=ModelRoutingConfig)
async def set_routing(request: Request, form: ModelRoutingConfig, admin=Depends(get_admin_user)):
    config = await update_model_routing_config(form)
    await publish_event(
        request,
        EVENTS.MODEL_PROVIDER_CONFIG_UPDATED,
        actor=admin,
        subject_id='mh.model_routing',
        subject_type='model.provider_config',
        data={'version': config.version},
    )
    return config
