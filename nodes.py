"""Core nodes for ComfyUI-Loop-Controller.

Design goals:
1) Queue-driven loop (no Python for-loop in one execution)
2) Implicit runtime state sharing between head/tail nodes
3) Fail-fast extraction for index and data quality issues
"""

from __future__ import annotations

import copy
import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List


class AnyType(str):
    """ComfyUI wildcard type helper.

    By making "__ne__" always return False, this type behaves as a universal
    connector type in ComfyUI custom nodes.
    """

    def __ne__(self, __value: object) -> bool:  # pylint: disable=unused-argument
        return False


ANY = AnyType("*")


@dataclass
class _LoopState:
    """Shared in-memory runtime state.

    current_index: currently running index in queue-based loop.
    total: target total runs for this loop session.
    """

    current_index: int = 0
    total: int = 0


_STATE_LOCK = threading.Lock()
_STATE = _LoopState()


def _contains_auto_loop_marker(extra_pnginfo: Any) -> bool:
    """Detect whether request metadata carries the auto-loop marker.

    ComfyUI can pass `extra_pnginfo` in slightly different shapes depending on
    the caller or extension stack. This function recursively inspects nested
    dict/list containers and also tries JSON text payloads.
    """
    if extra_pnginfo is None:
        return False

    if isinstance(extra_pnginfo, dict):
        if extra_pnginfo.get("is_auto_loop") is True:
            return True
        return any(_contains_auto_loop_marker(v) for v in extra_pnginfo.values())

    if isinstance(extra_pnginfo, (list, tuple)):
        return any(_contains_auto_loop_marker(v) for v in extra_pnginfo)

    if isinstance(extra_pnginfo, str):
        text = extra_pnginfo.strip()
        if not text:
            return False
        try:
            parsed = json.loads(text)
            return _contains_auto_loop_marker(parsed)
        except (TypeError, json.JSONDecodeError):
            return False

    return False


def _normalize_to_list(value: Any) -> List[Any]:
    """Normalize various list-like inputs into a Python list.

    - Multiline string => split lines, strip surrounding whitespace, drop empty.
    - list/tuple => keep as normal list.
    - Generic iterable (excluding bytes/str/dict) => convert to list.
    - Other objects => single-item list fallback.
    """
    if isinstance(value, str):
        normalized = [line.strip() for line in value.splitlines() if line.strip()]
        return normalized

    if isinstance(value, list):
        return value

    if isinstance(value, tuple):
        return list(value)

    if isinstance(value, dict):
        # Dict is usually not the intended list source here; treat as one item
        # to avoid silently iterating keys and producing surprising mismatches.
        return [value]

    if isinstance(value, Iterable):
        try:
            return list(value)
        except TypeError:
            return [value]

    return [value]


class LoopStartNode:
    """Loop head node: clock engine for queue-driven loop progress."""

    CATEGORY = "Loop Controller"
    FUNCTION = "run"
    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("index",)

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Dict[str, Any]]:
        return {
            "required": {
                "total": ("INT", {"default": 10, "min": 1, "max": 100000, "step": 1}),
                "start_index": ("INT", {"default": 0, "min": 0, "max": 100000, "step": 1}),
            },
            "hidden": {
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    def run(self, total: int, start_index: int, extra_pnginfo: Any = None):
        is_auto_loop = _contains_auto_loop_marker(extra_pnginfo)

        with _STATE_LOCK:
            # Manual queue click starts from user-defined position.
            # Auto-loop queue continues by +1 each request.
            if is_auto_loop:
                _STATE.current_index += 1
            else:
                _STATE.current_index = start_index

            _STATE.total = total
            current_index = _STATE.current_index

        return (current_index,)


class ListItemExtractorNode:
    """Fail-fast extractor node for any list-like workflow input."""

    CATEGORY = "Loop Controller"
    FUNCTION = "extract"
    RETURN_TYPES = (ANY,)
    RETURN_NAMES = ("item",)

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Dict[str, Any]]:
        return {
            "required": {
                "index": ("INT", {"default": 0, "min": 0, "max": 100000, "step": 1}),
                "list": (ANY,),
            }
        }

    def extract(self, index: int, list: Any):  # pylint: disable=redefined-builtin
        items = _normalize_to_list(list)

        if len(items) == 0:
            raise ValueError(
                "❌ 列表为空，无法提取数据。 / List is empty, cannot extract any item."
            )

        if index >= len(items):
            raise IndexError(
                "❌ 索引越界！要求获取第 {idx} 个，但列表总共只有 {size} 个。 / "
                "Index out of range: requested #{idx}, but list has only {size} item(s).".format(
                    idx=index,
                    size=len(items),
                )
            )

        return (items[index],)


class LoopTriggerNode:
    """Loop tail node: re-queue workflow until progress reaches total."""

    CATEGORY = "Loop Controller"
    FUNCTION = "trigger"
    RETURN_TYPES = ()
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Dict[str, Any]]:
        return {
            "required": {
                "any": (ANY,),
            },
            "hidden": {
                "prompt": "PROMPT",
                "client_id": "UNIQUE_ID",
            },
        }

    @staticmethod
    def _queue_next(prompt: Dict[str, Any], client_id: str | None):
        # Keep graph data untouched and only augment API payload metadata.
        payload = {
            "prompt": copy.deepcopy(prompt),
            "client_id": client_id,
            "extra_data": {
                "extra_pnginfo": {
                    "is_auto_loop": True,
                }
            },
        }

        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url="http://127.0.0.1:8188/prompt",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                # Read to force network errors immediately and fail fast.
                response.read()
        except urllib.error.URLError as exc:
            raise RuntimeError(
                "❌ 自动排队失败：无法连接 ComfyUI API (/prompt)。"
                " / Auto-queue failed: cannot reach ComfyUI /prompt API."
            ) from exc

    def trigger(
        self,
        any: Any,  # pylint: disable=unused-argument,redefined-builtin
        prompt: Dict[str, Any] | None = None,
        client_id: str | None = None,
    ):
        with _STATE_LOCK:
            current_index = _STATE.current_index
            total = _STATE.total

        if total <= 0:
            # Defensive state guard in case start node wasn't executed.
            raise RuntimeError(
                "❌ 循环状态未初始化，请先执行 Loop Start 节点。 / "
                "Loop state is not initialized. Please run Loop Start first."
            )

        completed = current_index + 1
        should_continue = completed < total

        if should_continue:
            if not isinstance(prompt, dict) or len(prompt) == 0:
                raise RuntimeError(
                    "❌ 自动排队失败：缺少有效的 prompt 数据。 / "
                    "Auto-queue failed: missing valid prompt payload."
                )
            self._queue_next(prompt=prompt, client_id=client_id)
            progress_text = f"Progress: {completed} / {total}"
        else:
            progress_text = f"✅ Finished: {completed} / {total}"

        return {"ui": {"text": [progress_text]}, "result": ()}


NODE_CLASS_MAPPINGS = {
    "Loop Start": LoopStartNode,
    "List Item Extractor": ListItemExtractorNode,
    "Loop Trigger": LoopTriggerNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "Loop Start": "Loop Start / 循环起始",
    "List Item Extractor": "List Item Extractor / 列表项提取器",
    "Loop Trigger": "Loop Trigger / 循环触发器",
}
