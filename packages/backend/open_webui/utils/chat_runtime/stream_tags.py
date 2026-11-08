"""Stateful conversion of streamed special tags into Open WebUI output items."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from open_webui.utils.chat_runtime.stream_state import _start_tag_pattern, output_id


class TagOutputTransformer:
    """Turn reasoning, solution and code tags into structured response items."""

    def __init__(self, id_factory: Callable[[str], str] = output_id, clock: Callable[[], float] = time.time):
        self._id_factory = id_factory
        self._clock = clock
        self._scan_positions: dict[tuple[str, str], int] = {}
        self._boundary_positions: dict[tuple[str, str], tuple[int, int, int]] = {}

    @staticmethod
    def _extract_attributes(tag_content: str | None) -> dict[str, str]:
        if not tag_content:
            return {}
        return {key: value for key, value in re.findall(r'(\w+)\s*=\s*"([^"]+)"', tag_content)}

    @staticmethod
    def _get_last_text(output: list[dict[str, Any]]) -> str:
        if output and output[-1].get('type') == 'message':
            parts = output[-1].get('content', [])
            if parts and parts[-1].get('type') == 'output_text':
                return parts[-1].get('text', '')
        return ''

    @staticmethod
    def _set_last_text(output: list[dict[str, Any]], text: str) -> None:
        if output and output[-1].get('type') == 'message':
            parts = output[-1].get('content', [])
            if parts and parts[-1].get('type') == 'output_text':
                parts[-1]['text'] = text

    def _get_scanned_length(self, item: dict[str, Any], text: str, content_type: str) -> int:
        item_id = item.get('id')
        if not item_id:
            return 0
        scanned_length = self._scan_positions.get((item_id, content_type), 0)
        return scanned_length if scanned_length <= len(text) else 0

    def _save_scanned_length(self, item: dict[str, Any], text: str, content_type: str) -> None:
        item_id = item.get('id')
        if item_id:
            self._scan_positions[(item_id, content_type)] = len(text)

    def _clear_scanned_length(self, item: dict[str, Any], content_type: str) -> None:
        item_id = item.get('id')
        if item_id:
            self._scan_positions.pop((item_id, content_type), None)
            self._boundary_positions.pop((item_id, content_type), None)

    def _get_tag_boundaries(
        self, item: dict[str, Any], text: str, scanned_length: int, content_type: str
    ) -> tuple[int, int]:
        """Return the last open tag and last delimiter before the scanned boundary."""
        key = (item.get('id'), content_type)
        scanned, last_open, last_boundary = self._boundary_positions.get(key, (0, -1, -1))
        if scanned > scanned_length:
            scanned, last_open, last_boundary = 0, -1, -1

        if scanned < scanned_length:
            open_tag = text.rfind('<', scanned, scanned_length)
            if open_tag != -1:
                last_open = open_tag
            boundary = max(text.rfind('>', scanned, scanned_length), text.rfind('\n', scanned, scanned_length))
            if boundary != -1:
                last_boundary = boundary
            self._boundary_positions[key] = (scanned_length, last_open, last_boundary)

        return last_open, last_boundary

    def _append_tag_item(
        self,
        output: list[dict[str, Any]],
        content_type: str,
        start_tag: str,
        end_tag: str,
        attributes: dict[str, str],
    ) -> None:
        output_item_type = {
            'reasoning': 'reasoning',
            'solution': 'message',
            'code_interpreter': 'open_webui:code_interpreter',
        }.get(content_type, content_type)

        if output_item_type == 'reasoning':
            output.append(
                {
                    'type': 'reasoning',
                    'id': self._id_factory('r'),
                    'status': 'in_progress',
                    'start_tag': start_tag,
                    'end_tag': end_tag,
                    'attributes': attributes,
                    'content': [],
                    'summary': None,
                    'started_at': self._clock(),
                }
            )
        elif output_item_type == 'open_webui:code_interpreter':
            output.append(
                {
                    'type': 'open_webui:code_interpreter',
                    'id': self._id_factory('ci'),
                    'status': 'in_progress',
                    'start_tag': start_tag,
                    'end_tag': end_tag,
                    'attributes': attributes,
                    'lang': attributes.get('lang', 'python'),
                    'code': '',
                    'output': None,
                    'started_at': self._clock(),
                }
            )
        else:
            output.append(
                {
                    'type': 'message',
                    'id': self._id_factory('msg'),
                    'status': 'in_progress',
                    'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': ''}],
                    '_tag_type': content_type,
                    'start_tag': start_tag,
                    'end_tag': end_tag,
                    'attributes': attributes,
                    'started_at': self._clock(),
                }
            )

    def _set_open_item_content(self, output: list[dict[str, Any]], content: str) -> None:
        item = output[-1]
        if item.get('type') == 'reasoning':
            item['content'] = [{'type': 'output_text', 'text': content}]
        elif item.get('type') == 'open_webui:code_interpreter':
            item['code'] = content
        else:
            self._set_last_text(output, content)

    def _transform_message_tail(
        self, content_type: str, tags: list[tuple[str, str]], output: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]] | None, bool]:
        item = output[-1]
        item_text = self._get_last_text(output)
        scanned_length = self._get_scanned_length(item, item_text, content_type)
        max_start_tag_length = max((len(start_tag) for start_tag, _ in tags), default=1)
        search_start = max(0, scanned_length - max_start_tag_length + 1)

        if scanned_length and any(start.startswith('<') and start.endswith('>') for start, _ in tags):
            open_tag_start, last_tag_boundary = self._get_tag_boundaries(item, item_text, scanned_length, content_type)
            if open_tag_start > last_tag_boundary:
                search_start = min(search_start, open_tag_start)

        for start_tag, end_tag in tags:
            match = re.compile(_start_tag_pattern(start_tag)).search(item_text, search_start)
            if not match:
                continue

            self._clear_scanned_length(item, content_type)
            try:
                attr_content = match.group(1) or ''
            except Exception:
                attr_content = ''
            attributes = self._extract_attributes(attr_content)
            before_tag = item_text[: match.start()]
            after_tag = item_text[match.end() :]
            self._set_last_text(output, before_tag)

            if not before_tag.strip():
                if output and output[-1].get('type') == 'message':
                    output.pop()

            self._append_tag_item(output, content_type, start_tag, end_tag, attributes)
            if after_tag:
                self._set_open_item_content(output, after_tag)
                _, ended = self.transform(content_type, tags, output)
                return output, ended
            return output, False

        self._save_scanned_length(item, item_text, content_type)
        return None, False

    def _get_open_block_content(self, item: dict[str, Any], output: list[dict[str, Any]]) -> str:
        if item.get('type') == 'reasoning':
            parts = item.get('content', [])
            if parts and parts[-1].get('type') == 'output_text':
                return parts[-1].get('text', '')
            return ''
        if item.get('type') == 'open_webui:code_interpreter':
            return item.get('code', '')
        return self._get_last_text(output)

    def _finish_open_block(
        self, content_type: str, output: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]] | None, bool]:
        item = output[-1]
        start_tag = item.get('start_tag', '')
        end_tag = item.get('end_tag', '')
        block_content = self._get_open_block_content(item, output)
        scanned_length = self._get_scanned_length(item, block_content, content_type)
        end_tag_search_start = max(0, scanned_length - max(len(end_tag), 1) + 1)

        if block_content.find(end_tag, end_tag_search_start) == -1:
            self._save_scanned_length(item, block_content, content_type)
            return None, False

        self._clear_scanned_length(item, content_type)
        block_content = re.sub(_start_tag_pattern(start_tag), '', block_content)
        end_tag_regex = re.compile(re.escape(end_tag), re.DOTALL)
        split_content = end_tag_regex.split(block_content, maxsplit=1)
        block_content = split_content[0].strip() if split_content else ''
        leftover_content = split_content[1].lstrip() if len(split_content) > 1 else ''

        if block_content:
            if item.get('type') == 'reasoning':
                item['content'] = [{'type': 'output_text', 'text': block_content}]
                item['ended_at'] = self._clock()
                item['duration'] = int(item['ended_at'] - item['started_at'])
                item['status'] = 'completed'
            elif item.get('type') == 'open_webui:code_interpreter':
                item['code'] = block_content
                item['ended_at'] = self._clock()
                item['duration'] = int(item['ended_at'] - item['started_at'])
            else:
                self._set_last_text(output, block_content)
                item['ended_at'] = self._clock()
        else:
            output.pop()

        output.append(
            {
                'type': 'message',
                'id': self._id_factory('msg'),
                'status': 'in_progress',
                'role': 'assistant',
                'content': [{'type': 'output_text', 'text': leftover_content}],
            }
        )
        return output, True

    def transform(
        self, content_type: str, tags: list[tuple[str, str]], output: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]] | None, bool]:
        """Rewrite the output tail in place, returning whether an end tag closed."""
        last_type = output[-1].get('type', '') if output else ''
        if last_type == 'message' and output[-1].get('_tag_type') != content_type:
            return self._transform_message_tail(content_type, tags, output)

        is_reasoning = last_type == 'reasoning' and content_type == 'reasoning'
        is_code = last_type == 'open_webui:code_interpreter' and content_type == 'code_interpreter'
        is_tagged_message = last_type == 'message' and output[-1].get('_tag_type') == content_type
        if is_reasoning or is_code or is_tagged_message:
            return self._finish_open_block(content_type, output)
        return None, False
