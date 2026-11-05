"""Decide whether a chat turn may expose Mini-Hermes document tools."""

from __future__ import annotations

import re


_COMMAND = re.compile(r'(?<!\S)/(stop-tool|tool)(?![\w-])', re.IGNORECASE)
_ACTION = re.compile(
    r'\b(edit|update|change|create|generate|fill|replace|convert|export|download|save|'
    r'sửa|chỉnh|đổi|thay|tạo|làm|cập nhật|điền|thêm|xóa|xoá|xuất|lưu|tải|'
    r'đọc|xem|phân tích|tóm tắt)\b', re.IGNORECASE
)
_DOCUMENT = re.compile(
    r'\b(file|document|docx|xlsx|pptx|pdf|word|excel|slide|sheet|'
    r'tệp|tài liệu|báo cáo|bảng tính|trình chiếu|link tải)\b', re.IGNORECASE
)


def document_agent_mode(messages: list[dict], has_files: bool = False) -> bool:
    """Explicit commands persist; an uncommanded document request activates one turn."""
    command_state: bool | None = None
    latest_user_text = ''
    for message in messages:
        if message.get('role') != 'user':
            continue
        content = message.get('content') or ''
        if isinstance(content, list):
            content = ' '.join(
                part.get('text', '') for part in content
                if isinstance(part, dict) and part.get('type') == 'text'
            )
        if not isinstance(content, str):
            continue
        latest_user_text = content
        for match in _COMMAND.finditer(content):
            command_state = match.group(1).lower() == 'tool'
    if command_state is not None:
        return command_state
    return bool(_ACTION.search(latest_user_text) and (has_files or _DOCUMENT.search(latest_user_text)))
