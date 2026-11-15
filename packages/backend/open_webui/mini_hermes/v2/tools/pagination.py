"""Wire Budget Pagination Engine for Mini-Hermes V2 Document Tools (Feature 27).

Enforces response budget based on total serialized JSON character length
(default <= 5,952 characters). Handles discrete records greedily and slices
oversized strings using native Unicode codepoint bounds without character loss.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Callable


DEFAULT_WIRE_BUDGET_CHARS = 5952
PLACEHOLDER_CURSOR = "mhv2c1.k1.eyJ2IjoxLCJwb3NpdGlvbiI6eyJpdGVtX29yZGluYWwiOjk5OTk5OSwiZnJhZ21lbnQiOm51bGx9fQ.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


class OutputBudgetTooSmallError(Exception):
    """Raised when the minimal response envelope exceeds max_output_chars."""

    def __init__(self, message: str = "Minimal envelope exceeds output character budget", status_code: int = 400):
        super().__init__(message)
        self.code = "OUTPUT_BUDGET_TOO_SMALL"
        self.message = message
        self.status_code = status_code


def measure_wire_characters(payload: dict[str, Any]) -> int:
    """Measures the exact wire JSON character length with ensure_ascii=False."""
    return len(json.dumps(payload, ensure_ascii=False))


def measure_wire_metrics(payload: dict[str, Any]) -> dict[str, int]:
    """Measures serialized JSON characters, UTF-8 bytes, and estimated tokens separately."""
    serialized = json.dumps(payload, ensure_ascii=False)
    utf8_bytes = len(serialized.encode("utf-8"))
    return {
        "serialized_chars": len(serialized),
        "utf8_bytes": utf8_bytes,
        "estimated_tokens": max(1, (len(serialized) + 3) // 4) if serialized else 0,
    }


def paginate_records(
    base_envelope: dict[str, Any],
    items: list[Any],
    items_key: str = "items",
    *,
    serialize_item_fn: Callable[[Any], dict[str, Any]] | None = None,
    make_cursor_fn: Callable[[int, dict[str, Any] | None], str] | None = None,
    update_envelope_fn: Callable[[dict[str, Any], list[list[int]], dict[str, Any] | None], None] | None = None,
    max_output_chars: int = DEFAULT_WIRE_BUDGET_CHARS,
    start_ordinal: int = 0,
    start_fragment: dict[str, Any] | None = None,
    sliceable_field: str = "raw_value",
) -> tuple[dict[str, Any], str | None]:
    """Paginates records within max_output_chars wire JSON budget.

    Args:
        base_envelope: Pre-populated response envelope. Must have base_envelope['data'].
        items: Full sequence of candidate items for the scope.
        items_key: Key inside base_envelope['data'] where items list is stored.
        serialize_item_fn: Optional transformer from raw item to serializable dict.
        make_cursor_fn: Callback(item_ordinal, fragment) -> signed cursor string.
        update_envelope_fn: Optional callback(envelope, whole_intervals, fragment_meta) updated before wire measurement.
        max_output_chars: Wire character limit (default 5,952).
        start_ordinal: Starting item index.
        start_fragment: Starting fragment continuation {"field": str, "codepoint_offset": int}.
        sliceable_field: Field to slice if a single item exceeds the budget.

    Returns:
        (completed_envelope, next_cursor_or_none)
    """
    envelope = copy.deepcopy(base_envelope)
    if "data" not in envelope or not isinstance(envelope["data"], dict):
        envelope["data"] = {}
    envelope["data"][items_key] = []
    envelope["next_cursor"] = PLACEHOLDER_CURSOR
    if update_envelope_fn is not None:
        update_envelope_fn(envelope, [], None)

    # 1. Budget check: minimal envelope with placeholder cursor must fit
    minimal_len = measure_wire_characters(envelope)
    if minimal_len > max_output_chars:
        envelope["next_cursor"] = None
        raise OutputBudgetTooSmallError(
            f"Minimal envelope wire size ({minimal_len} chars) exceeds budget ({max_output_chars} chars)"
        )

    # If start_ordinal is already beyond items, traversal is finished
    if start_ordinal >= len(items):
        envelope["next_cursor"] = None
        if update_envelope_fn is not None:
            update_envelope_fn(envelope, [], None)
        return envelope, None

    emitted_items: list[dict[str, Any]] = []
    whole_intervals: list[list[int]] = []
    fragment_meta: dict[str, Any] | None = None
    next_cursor: str | None = None

    def _add_whole_ordinal(ord_idx: int) -> list[list[int]]:
        if not whole_intervals:
            return [[ord_idx, ord_idx + 1]]
        updated = [list(iv) for iv in whole_intervals]
        if updated[-1][1] == ord_idx:
            updated[-1][1] = ord_idx + 1
        else:
            updated.append([ord_idx, ord_idx + 1])
        return updated

    idx = start_ordinal
    while idx < len(items):
        raw_item = items[idx]
        item_dict = serialize_item_fn(raw_item) if serialize_item_fn else dict(raw_item)

        # Check if we are resuming a fragmented item
        if idx == start_ordinal and start_fragment is not None:
            field_name = start_fragment.get("field", sliceable_field)
            old_offset = int(start_fragment.get("codepoint_offset", 0))
            full_text = str(item_dict.get(field_name, ""))
            remainder = full_text[old_offset:]

            # Try emitting the entire remainder
            test_item = copy.deepcopy(item_dict)
            test_item[field_name] = remainder
            if test_item.get("cached_value") == full_text:
                test_item["cached_value"] = None

            cand_frag_meta = {
                "item_ordinal": idx,
                "field": field_name,
                "start_codepoint": old_offset,
                "end_codepoint": len(full_text),
                "total_codepoints": len(full_text),
                "is_final_fragment": True,
            }
            envelope["data"][items_key] = emitted_items + [test_item]
            dummy_cursor = (
                make_cursor_fn(idx + 1, None)
                if (idx + 1 < len(items) and make_cursor_fn)
                else None
            )
            envelope["next_cursor"] = dummy_cursor
            envelope["status"] = "partial" if dummy_cursor is not None else "complete"
            if update_envelope_fn is not None:
                update_envelope_fn(envelope, whole_intervals, cand_frag_meta)

            if measure_wire_characters(envelope) <= max_output_chars:
                emitted_items.append(test_item)
                fragment_meta = cand_frag_meta
                idx += 1
                start_fragment = None
                continue
            else:
                # Slicing the remainder further
                test_item, new_offset = _slice_oversized_item(
                    envelope=envelope,
                    items_key=items_key,
                    emitted_items=emitted_items,
                    item_dict=item_dict,
                    field_name=field_name,
                    full_text=full_text,
                    old_offset=old_offset,
                    idx=idx,
                    whole_intervals=whole_intervals,
                    make_cursor_fn=make_cursor_fn,
                    update_envelope_fn=update_envelope_fn,
                    max_output_chars=max_output_chars,
                )
                emitted_items.append(test_item)
                fragment_meta = {
                    "item_ordinal": idx,
                    "field": field_name,
                    "start_codepoint": old_offset,
                    "end_codepoint": new_offset,
                    "total_codepoints": len(full_text),
                    "is_final_fragment": False,
                }
                frag = {"field": field_name, "codepoint_offset": new_offset}
                next_cursor = make_cursor_fn(idx, frag) if make_cursor_fn else None
                break

        # Discrete record packing
        test_items = emitted_items + [item_dict]
        candidate_whole = _add_whole_ordinal(idx)
        envelope["data"][items_key] = test_items
        dummy_cursor = (
            make_cursor_fn(idx + 1, None)
            if (idx + 1 < len(items) and make_cursor_fn)
            else None
        )
        envelope["next_cursor"] = dummy_cursor
        envelope["status"] = "partial" if dummy_cursor is not None else "complete"
        if update_envelope_fn is not None:
            update_envelope_fn(envelope, candidate_whole, fragment_meta)

        current_wire_chars = measure_wire_characters(envelope)
        if current_wire_chars <= max_output_chars:
            emitted_items.append(item_dict)
            whole_intervals = candidate_whole
            idx += 1
        else:
            # Item doesn't fit
            if len(emitted_items) > 0:
                # Stop packing discrete items; next page begins at this item
                next_cursor = make_cursor_fn(idx, None) if make_cursor_fn else None
                break
            else:
                # Single item exceeds budget -> Slice oversized cell
                field_name = sliceable_field
                full_text = str(item_dict.get(field_name, ""))
                if not full_text:
                    raise OutputBudgetTooSmallError(
                        f"Non-sliceable single item wire size ({current_wire_chars} chars) exceeds budget ({max_output_chars} chars)"
                    )

                test_item, new_offset = _slice_oversized_item(
                    envelope=envelope,
                    items_key=items_key,
                    emitted_items=[],
                    item_dict=item_dict,
                    field_name=field_name,
                    full_text=full_text,
                    old_offset=0,
                    idx=idx,
                    whole_intervals=whole_intervals,
                    make_cursor_fn=make_cursor_fn,
                    update_envelope_fn=update_envelope_fn,
                    max_output_chars=max_output_chars,
                )
                emitted_items.append(test_item)
                fragment_meta = {
                    "item_ordinal": idx,
                    "field": field_name,
                    "start_codepoint": 0,
                    "end_codepoint": new_offset,
                    "total_codepoints": len(full_text),
                    "is_final_fragment": False,
                }
                frag = {"field": field_name, "codepoint_offset": new_offset}
                next_cursor = make_cursor_fn(idx, frag) if make_cursor_fn else None
                break

    if idx >= len(items) and next_cursor is None:
        next_cursor = None

    envelope["data"][items_key] = emitted_items
    envelope["next_cursor"] = next_cursor
    envelope["status"] = "partial" if next_cursor is not None else "complete"
    if update_envelope_fn is not None:
        update_envelope_fn(envelope, whole_intervals, fragment_meta)
    return envelope, next_cursor


def _slice_oversized_item(
    envelope: dict[str, Any],
    items_key: str,
    emitted_items: list[dict[str, Any]],
    item_dict: dict[str, Any],
    field_name: str,
    full_text: str,
    old_offset: int,
    idx: int,
    whole_intervals: list[list[int]],
    make_cursor_fn: Callable[[int, dict[str, Any] | None], str] | None,
    update_envelope_fn: Callable[[dict[str, Any], list[list[int]], dict[str, Any] | None], None] | None,
    max_output_chars: int,
) -> tuple[dict[str, Any], int]:
    """Finds the maximum Unicode codepoint slice of full_text that fits in budget."""
    total_codepoints = len(full_text)
    low = old_offset + 1
    high = total_codepoints
    best_offset = old_offset

    # Binary search for maximum valid codepoint offset
    while low <= high:
        mid = (low + high) // 2
        candidate_slice = full_text[old_offset:mid]
        candidate_item = copy.deepcopy(item_dict)
        candidate_item[field_name] = candidate_slice
        if candidate_item.get("cached_value") == full_text:
            candidate_item["cached_value"] = None

        frag = {"field": field_name, "codepoint_offset": mid}
        dummy_cursor = make_cursor_fn(idx, frag) if make_cursor_fn else PLACEHOLDER_CURSOR
        cand_frag_meta = {
            "item_ordinal": idx,
            "field": field_name,
            "start_codepoint": old_offset,
            "end_codepoint": mid,
            "total_codepoints": total_codepoints,
            "is_final_fragment": False,
        }

        envelope["data"][items_key] = emitted_items + [candidate_item]
        envelope["next_cursor"] = dummy_cursor
        envelope["status"] = "partial"
        if update_envelope_fn is not None:
            update_envelope_fn(envelope, whole_intervals, cand_frag_meta)

        if measure_wire_characters(envelope) <= max_output_chars:
            best_offset = mid
            low = mid + 1
        else:
            high = mid - 1

    if best_offset <= old_offset:
        raise OutputBudgetTooSmallError(
            f"Cannot fit even 1 character slice into remaining output budget ({max_output_chars} chars)"
        )

    sliced_item = copy.deepcopy(item_dict)
    sliced_item[field_name] = full_text[old_offset:best_offset]
    if sliced_item.get("cached_value") == full_text:
        sliced_item["cached_value"] = None
    return sliced_item, best_offset
