"""Core nodes for ComfyUI-Loop-Controller.

Design goals:
1) Queue-driven loop (no Python for-loop in one execution)
2) Implicit runtime state sharing between head/tail nodes
3) Fail-fast extraction for index and data quality issues
"""

from __future__ import annotations

import copy
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
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
    session_id: str = ""
    last_queue_key: str = ""


_STATE_LOCK = threading.Lock()
_STATE = _LoopState()
_LOOP_META_KEY = "loop_controller"


def _get_local_api_url() -> str:
    """Detect ComfyUI API base URL from startup args.

    Supports both argument styles:
    - --port 6006
    - --port=6006

    Falls back to ComfyUI default 8188 when parsing fails.
    """
    port = 8188
    argv = sys.argv

    for i, arg in enumerate(argv):
        if arg == "--port" and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except (TypeError, ValueError):
                pass
        elif arg.startswith("--port="):
            try:
                port = int(arg.split("=", maxsplit=1)[1])
            except (TypeError, ValueError):
                pass

    return f"http://127.0.0.1:{port}"


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
        loop_meta = extra_pnginfo.get(_LOOP_META_KEY)
        if isinstance(loop_meta, dict) and loop_meta.get("is_auto_loop") is True:
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


def _extract_loop_meta(extra_pnginfo: Any) -> Dict[str, Any]:
    """Extract loop metadata from extra_pnginfo in a backward-compatible way."""
    if isinstance(extra_pnginfo, dict):
        namespaced = extra_pnginfo.get(_LOOP_META_KEY)
        if isinstance(namespaced, dict):
            return namespaced
        return extra_pnginfo if extra_pnginfo.get("is_auto_loop") is True else {}

    if isinstance(extra_pnginfo, str):
        text = extra_pnginfo.strip()
        if not text:
            return {}
        try:
            return _extract_loop_meta(json.loads(text))
        except (TypeError, json.JSONDecodeError):
            return {}

    if isinstance(extra_pnginfo, (list, tuple)):
        for item in extra_pnginfo:
            found = _extract_loop_meta(item)
            if found:
                return found
        return {}

    return {}


def _to_int(value: Any) -> int | None:
    """Best-effort int conversion."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


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


def _unwrap_single_index(value: Any) -> int:
    """Unwrap the index list supplied when INPUT_IS_LIST is enabled."""
    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise ValueError(
                "❌ 循环索引必须是单个值。 / Loop index must contain exactly one value."
            )
        value = value[0]

    index = _to_int(value)
    if index is None:
        raise TypeError(
            "❌ 循环索引必须是整数。 / Loop index must be an integer."
        )
    return index


def _normalize_list_input(value: Any) -> List[Any]:
    """Normalize a whole-list input without reintroducing ComfyUI fan-out.

    With INPUT_IS_LIST enabled, scalar inputs arrive in a one-element wrapper.
    A real list output, however, arrives as the complete list. Unwrap only
    containers that unambiguously represent one wrapped list-like value.
    """
    if isinstance(value, (list, tuple)):
        values = list(value)
        if len(values) == 1 and isinstance(values[0], (list, tuple)):
            return _normalize_to_list(values[0])
        if len(values) == 1 and isinstance(values[0], str):
            return _normalize_to_list(values[0])
        return values

    return _normalize_to_list(value)


class LoopStartNode:
    """Loop head node: clock engine for queue-driven loop progress."""

    CATEGORY = "Loop Controller"
    FUNCTION = "run"
    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("index",)
    DESCRIPTION = (
        "Clock engine for queue-based looping. Reads start_index from current "
        "prompt (rewritten by Loop Trigger for auto-loop) and writes "
        "current_index/total/session "
        "into shared memory for Loop Trigger."
    )

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Dict[str, Any]]:
        return {
            "required": {
                "total": (
                    "INT",
                    {
                        "default": 10,
                        "min": 1,
                        "max": 100000,
                        "step": 1,
                        "tooltip": "Total number of loop iterations",
                    },
                ),
                "start_index": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 100000,
                        "step": 1,
                        "tooltip": "Starting index used when you manually click Queue",
                    },
                ),
            },
            "hidden": {
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    def run(self, total: int, start_index: int, extra_pnginfo: Any = None):
        loop_meta = _extract_loop_meta(extra_pnginfo)
        is_auto_loop = bool(
            loop_meta.get("is_auto_loop") is True
            or _contains_auto_loop_marker(extra_pnginfo)
        )
        requested_total = _to_int(loop_meta.get("total"))
        requested_session = loop_meta.get("session_id")

        with _STATE_LOCK:
            # Use prompt-carried start_index as the single source of truth.
            # Loop Trigger rewrites next prompt's start_index to next_index.
            _STATE.current_index = start_index
            if is_auto_loop:
                if requested_total is not None and requested_total > 0:
                    _STATE.total = requested_total
                if isinstance(requested_session, str) and requested_session.strip():
                    _STATE.session_id = requested_session.strip()
            else:
                _STATE.total = total
                _STATE.session_id = uuid.uuid4().hex
                _STATE.last_queue_key = ""

            # For auto-loop, prefer explicit total from metadata; if missing, keep
            # latest runtime total but never overwrite with invalid values.
            if _STATE.total <= 0:
                _STATE.total = total
            current_index = _STATE.current_index

        return (current_index,)

    @classmethod
    def IS_CHANGED(cls, **kwargs):  # pylint: disable=unused-argument
        # Each queued prompt represents a distinct loop iteration.
        return time.time()


class ListItemExtractorNode:
    """Fail-fast extractor node for any list-like workflow input."""

    CATEGORY = "Loop Controller"
    FUNCTION = "extract"
    RETURN_TYPES = (ANY,)
    RETURN_NAMES = ("item",)
    INPUT_IS_LIST = True
    DESCRIPTION = (
        "Extracts one item from a list by index. Raises immediately on empty "
        "lists or out-of-range indexes."
    )

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Dict[str, Any]]:
        return {
            "required": {
                "index": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 100000,
                        "step": 1,
                        "tooltip": "Index from Loop Start",
                    },
                ),
                "list": (
                    ANY,
                    {
                        "tooltip": "Any list-like input, including multiline text",
                    },
                ),
            }
        }

    def extract(self, index: Any, list: Any):  # pylint: disable=redefined-builtin
        current_index = _unwrap_single_index(index)
        items = _normalize_list_input(list)

        if len(items) == 0:
            raise ValueError(
                "❌ 列表为空，无法提取数据。 / List is empty, cannot extract any item."
            )

        if current_index < 0 or current_index >= len(items):
            raise IndexError(
                "❌ 索引越界！要求获取第 {idx} 个，但列表总共只有 {size} 个。 / "
                "Index out of range: requested #{idx}, but list has only {size} item(s).".format(
                    idx=current_index,
                    size=len(items),
                )
            )

        return (items[current_index],)


class LoopTriggerNode:
    """Loop tail node: re-queue workflow until progress reaches total."""

    CATEGORY = "Loop Controller"
    FUNCTION = "trigger"
    RETURN_TYPES = ()
    OUTPUT_NODE = True
    DESCRIPTION = (
        "End-of-graph trigger. Re-queues until current_index + 1 reaches total "
        "by writing explicit next_index metadata for the next queued run, and "
        "reports progress in the node panel."
    )

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Dict[str, Any]]:
        return {
            "required": {
                "any": (
                    ANY,
                    {
                        "tooltip": "Execution dependency, usually connected from a Save node",
                    },
                ),
            },
            "hidden": {
                "prompt": "PROMPT",
                "client_id": "UNIQUE_ID",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    @staticmethod
    def _inject_next_index_into_prompt(
        prompt: Dict[str, Any], next_index: int, total: int
    ) -> Dict[str, Any]:
        """Rewrite Loop Start inputs so next queued run has explicit index."""
        next_prompt = copy.deepcopy(prompt)
        found_loop_start = False

        for node_data in next_prompt.values():
            if not isinstance(node_data, dict):
                continue
            if node_data.get("class_type") != "Loop Start":
                continue

            inputs = node_data.get("inputs")
            if not isinstance(inputs, dict):
                inputs = {}
                node_data["inputs"] = inputs
            inputs["start_index"] = next_index
            inputs["total"] = total
            found_loop_start = True

        if not found_loop_start:
            raise RuntimeError(
                "❌ 自动排队失败：在 prompt 中未找到 Loop Start 节点，无法推进 next_index。 / "
                "Auto-queue failed: Loop Start node not found in prompt, cannot advance next_index."
            )

        return next_prompt

    @staticmethod
    def _queue_next(
        prompt: Dict[str, Any],
        client_id: str | None,
        extra_pnginfo: Any,
        *,
        next_index: int,
        total: int,
        session_id: str,
    ):
        # Keep graph data untouched and only augment API payload metadata.
        next_extra_pnginfo: Dict[str, Any]
        if isinstance(extra_pnginfo, dict):
            next_extra_pnginfo = copy.deepcopy(extra_pnginfo)
        else:
            next_extra_pnginfo = {}
        next_extra_pnginfo["is_auto_loop"] = True
        next_extra_pnginfo[_LOOP_META_KEY] = {
            "is_auto_loop": True,
            "next_index": next_index,
            "total": total,
            "session_id": session_id,
        }

        next_prompt = LoopTriggerNode._inject_next_index_into_prompt(
            prompt=prompt, next_index=next_index, total=total
        )

        payload = {
            "prompt": next_prompt,
            "client_id": client_id,
            "extra_data": {
                "extra_pnginfo": next_extra_pnginfo
            },
        }

        data = json.dumps(payload).encode("utf-8")
        api_url = f"{_get_local_api_url()}/prompt"
        request = urllib.request.Request(
            url=api_url,
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
                f"❌ 自动排队失败：无法连接 ComfyUI API ({api_url})。"
                f" / Auto-queue failed: cannot reach ComfyUI API ({api_url})."
            ) from exc

    @classmethod
    def IS_CHANGED(cls, **kwargs):  # pylint: disable=unused-argument
        # Force this output node to run every queued prompt execution.
        return time.time()

    def trigger(
        self,
        any: Any,  # pylint: disable=unused-argument,redefined-builtin
        prompt: Dict[str, Any] | None = None,
        client_id: str | None = None,
        extra_pnginfo: Any = None,
    ):
        with _STATE_LOCK:
            current_index = _STATE.current_index
            total = _STATE.total
            session_id = _STATE.session_id

        if total <= 0:
            # Defensive state guard in case start node wasn't executed.
            raise RuntimeError(
                "❌ 循环状态未初始化，请先执行 Loop Start 节点。 / "
                "Loop state is not initialized. Please run Loop Start first."
            )

        completed = current_index + 1
        should_continue = completed < total
        next_index = current_index + 1

        if should_continue:
            if not isinstance(prompt, dict) or len(prompt) == 0:
                raise RuntimeError(
                    "❌ 自动排队失败：缺少有效的 prompt 数据。 / "
                    "Auto-queue failed: missing valid prompt payload."
                )
            if not session_id:
                raise RuntimeError(
                    "❌ 循环会话未初始化，请手动点击 Queue 重新开始。 / "
                    "Loop session is not initialized. Please queue manually to start."
                )

            queue_key = f"{session_id}:{current_index}->{next_index}:{total}"
            with _STATE_LOCK:
                if _STATE.last_queue_key == queue_key:
                    duplicate = True
                else:
                    _STATE.last_queue_key = queue_key
                    duplicate = False

            if not duplicate:
                self._queue_next(
                    prompt=prompt,
                    client_id=client_id,
                    extra_pnginfo=extra_pnginfo,
                    next_index=next_index,
                    total=total,
                    session_id=session_id,
                )
            print(
                "[Loop-Controller] "
                f"index={current_index} next={next_index} total={total} "
                f"continue={should_continue} duplicate={duplicate}"
            )
            progress_text = f"Progress: {completed} / {total}"
        else:
            print(
                "[Loop-Controller] "
                f"index={current_index} next={next_index} total={total} "
                f"continue={should_continue}"
            )
            progress_text = f"✅ Finished: {completed} / {total}"

        return {
            "ui": {
                "text": [progress_text],
                "progress": [
                    {
                        "completed": completed,
                        "total": total,
                        "done": not should_continue,
                    }
                ],
            },
            "result": (),
        }


NODE_CLASS_MAPPINGS = {
    "Loop Start": LoopStartNode,
    "List Item Extractor": ListItemExtractorNode,
    "Loop Trigger": LoopTriggerNode,
}

# English source names. Chinese UI strings live in locales/zh/.
NODE_DISPLAY_NAME_MAPPINGS = {
    "Loop Start": "Loop Start",
    "List Item Extractor": "List Item Extractor",
    "Loop Trigger": "Loop Trigger",
}
