import datetime as dt
import json

import pytest

from open_webui.tools.time_resources import calculate_timestamp, get_current_timestamp


@pytest.mark.asyncio
async def test_get_current_timestamp_includes_consistent_user_local_time():
    result = json.loads(await get_current_timestamp(__user__={'timezone': 'Asia/Bangkok'}))

    utc = dt.datetime.fromisoformat(result['current_iso'])
    local = dt.datetime.fromisoformat(result['user_local_iso'])

    assert result['user_timezone'] == 'Asia/Bangkok'
    assert utc.utcoffset() == dt.timedelta(0)
    assert local.utcoffset() == dt.timedelta(hours=7)
    assert local.timestamp() == utc.timestamp()
    assert result['current_timestamp'] == int(utc.timestamp())


@pytest.mark.asyncio
async def test_calculate_timestamp_subtracts_days_and_weeks_and_keeps_local_time():
    result = json.loads(
        await calculate_timestamp(
            days_ago=1,
            weeks_ago=2,
            __user__={'timezone': 'Asia/Bangkok'},
        )
    )

    assert result['current_timestamp'] - result['calculated_timestamp'] == 15 * 24 * 60 * 60
    assert result['user_timezone'] == 'Asia/Bangkok'
    assert dt.datetime.fromisoformat(result['calculated_local_iso']).utcoffset() == (dt.timedelta(hours=7))
