"""Image input normalization shared across chat preparation and tool loops."""

import logging
import re

from open_webui.utils.files import get_image_base64_from_url, get_image_url_from_base64
from open_webui.utils.misc import is_raster_image_content_type

log = logging.getLogger(__name__)


def get_images_from_messages(message_list):
    images = []

    for message in reversed(message_list):
        message_images = []
        for file in message.get('files', []):
            if file.get('type') == 'image':
                message_images.append(file.get('url'))
            elif is_raster_image_content_type(file.get('content_type')):
                message_images.append(file.get('url'))

        if message_images:
            images.append(message_images)

    return images


async def get_image_urls(delta_images, request, metadata, user) -> list[str]:
    if not isinstance(delta_images, list):
        return []

    image_urls = []
    for img in delta_images:
        if not isinstance(img, dict) or img.get('type') != 'image_url':
            continue

        url = img.get('image_url', {}).get('url')
        if not url:
            continue

        if re.match(r'data:image/\w+;base64', url):
            url = await get_image_url_from_base64(request, url, metadata, user)

        image_urls.append(url)

    return image_urls


async def convert_url_images_to_base64(form_data, user=None):
    messages = form_data.get('messages', [])

    for message in messages:
        content = message.get('content')
        if not isinstance(content, list):
            continue

        new_content = []
        for item in content:
            new_content.append(await _convert_url_image_item(item, user))

        message['content'] = new_content

    return form_data


async def _convert_url_image_item(item, user):
    if not isinstance(item, dict) or item.get('type') not in ('image_url', 'input_image'):
        return item

    image_url_data = item.get('image_url', {})
    if isinstance(image_url_data, dict):
        image_url = image_url_data.get('url') or ''
    elif isinstance(image_url_data, str):
        image_url = image_url_data
    else:
        image_url = ''

    if image_url.startswith('data:image/'):
        return item

    try:
        base64_data = await get_image_base64_from_url(image_url, user=user)
        if not base64_data:
            return item
        if isinstance(image_url_data, str):
            return {**item, 'image_url': base64_data}

        image_url_payload = {'url': base64_data}
        if isinstance(image_url_data, dict) and image_url_data.get('detail'):
            image_url_payload['detail'] = image_url_data['detail']
        return {
            'type': item['type'],
            'image_url': image_url_payload,
        }
    except Exception as e:
        log.debug('Error converting image URL to base64: %s', e)
        return item
