from __future__ import annotations

import json
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog

from skyvern.webeye.actions.actions import (
    ClickAction,
    CompleteAction,
    GoBackAction,
    GotoUrlAction,
    InputTextAction,
    KeypressAction,
    ScrollAction,
    WaitAction,
)

if TYPE_CHECKING:
    from skyvern.forge.sdk.schemas.tasks import Task
    from skyvern.forge.sdk.schemas.steps import Step

LOG = structlog.get_logger()

Action = Any

N1_COORDINATE_SPACE = 1000


class YutoriN1ActionType(StrEnum):
    LEFT_CLICK = "left_click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    TRIPLE_CLICK = "triple_click"
    MIDDLE_CLICK = "middle_click"
    TYPE = "type"
    KEY_PRESS = "key_press"
    SCROLL = "scroll"
    STOP = "stop"
    SLEEP = "sleep"
    ZOOM_INTO_AREA = "zoom_into_area"
    ZOOM = "zoom"
    EXTRACT_CONTENT = "extract_content_and_links"
    GO_BACK = "go_back"
    GO_FORWARD = "go_forward"
    GOTO_URL = "goto_url"
    REFRESH = "refresh"
    WAIT = "wait"
    DRAG = "drag"
    MOUSE_MOVE = "mouse_move"
    MOUSE_DOWN = "mouse_down"
    MOUSE_UP = "mouse_up"
    HOLD_KEY = "hold_key"
    EXTRACT_ELEMENTS = "extract_elements"
    FIND = "find"
    SET_ELEMENT_VALUE = "set_element_value"
    EXECUTE_JS = "execute_js"


def parse_and_convert_yutori_n1_actions(
    response: Any,
    viewport_width: int,
    viewport_height: int,
    task: "Task | None" = None,
    step: "Step | None" = None,
) -> list[Action]:
    message = response.choices[0].message

    if not message.tool_calls:
        # N1 signals task completion with finish_reason=stop and no tool_calls.
        # Content may be empty string — treat any no-tool-call response as CompleteAction.
        base_params: dict[str, Any] = {}
        if task is not None and step is not None:
            base_params = {
                "organization_id": task.organization_id,
                "workflow_run_id": task.workflow_run_id,
                "task_id": task.task_id,
                "step_id": step.step_id,
                "step_order": step.order,
                "action_order": 0,
            }
        summary = message.content or "Task completed"
        return [CompleteAction(data_extraction_goal=summary, **base_params)]

    actions: list[Action] = []
    for idx, tc in enumerate(message.tool_calls):
        try:
            args = json.loads(tc.function.arguments)
            action = _convert_tool_call(tc.function.name, args, viewport_width, viewport_height, task, step, idx)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            LOG.warning("N1 tool call conversion failed", name=tc.function.name, arguments=tc.function.arguments, error=str(e))
            continue
        if action is not None:
            actions.append(action)
        else:
            LOG.warning("N1 tool call returned None action (unknown type?)", name=tc.function.name, arguments=tc.function.arguments)
    if not actions and message.tool_calls:
        # All tool_calls converted to None — unknown action types. Treat as completion
        # rather than returning empty list (which causes Skyvern to mark step as failed).
        LOG.warning("All N1 tool calls returned None, treating as CompleteAction", names=[tc.function.name for tc in message.tool_calls])
        base_params: dict[str, Any] = {}
        if task is not None and step is not None:
            base_params = {
                "organization_id": task.organization_id,
                "workflow_run_id": task.workflow_run_id,
                "task_id": task.task_id,
                "step_id": step.step_id,
                "step_order": step.order,
                "action_order": 0,
            }
        return [CompleteAction(data_extraction_goal="Task completed (unknown action types)", **base_params)]
    return actions


def _denormalize(value: float, dimension: int) -> int:
    return int(value / N1_COORDINATE_SPACE * dimension)


def _parse_coordinate(args: dict, viewport_width: int, viewport_height: int) -> tuple[int, int] | None:
    # N1 uses "coordinates" (plural); support "coordinate" as fallback
    raw = args.get("coordinates") or args.get("coordinate")
    if raw is None:
        return None
    return (
        _denormalize(float(raw[0]), viewport_width),
        _denormalize(float(raw[1]), viewport_height),
    )


def _convert_tool_call(
    name: str,
    args: dict,
    viewport_width: int,
    viewport_height: int,
    task: "Task | None" = None,
    step: "Step | None" = None,
    action_order: int = 0,
) -> Action | None:
    try:
        action_type = YutoriN1ActionType(name)
    except ValueError:
        return None

    coord = _parse_coordinate(args, viewport_width, viewport_height)
    x = coord[0] if coord is not None else None
    y = coord[1] if coord is not None else None

    base_params: dict[str, Any] = {}
    if task is not None and step is not None:
        base_params = {
            "organization_id": task.organization_id,
            "workflow_run_id": task.workflow_run_id,
            "task_id": task.task_id,
            "step_id": step.step_id,
            "step_order": step.order,
            "action_order": action_order,
        }

    if action_type == YutoriN1ActionType.LEFT_CLICK:
        return ClickAction(element_id="", x=x, y=y, button="left", repeat=1, **base_params)

    if action_type == YutoriN1ActionType.DOUBLE_CLICK:
        return ClickAction(element_id="", x=x, y=y, button="left", repeat=2, **base_params)

    if action_type == YutoriN1ActionType.RIGHT_CLICK:
        return ClickAction(element_id="", x=x, y=y, button="right", repeat=1, **base_params)

    if action_type == YutoriN1ActionType.TRIPLE_CLICK:
        return ClickAction(element_id="", x=x, y=y, button="left", repeat=3, **base_params)

    if action_type == YutoriN1ActionType.MIDDLE_CLICK:
        return ClickAction(element_id="", x=x, y=y, button="left", repeat=1, **base_params)

    if action_type == YutoriN1ActionType.TYPE:
        return InputTextAction(element_id="", text=args.get("text", ""), **base_params)

    if action_type == YutoriN1ActionType.KEY_PRESS:
        # N1 uses "key_comb" for key combinations; also support plain "key" as fallback
        key = args.get("key_comb") or args.get("key", "")
        if not key:
            return None
        # N1 returns combo strings like "Control+l"; Playwright's keyboard.press accepts this format
        return KeypressAction(keys=[key], **base_params)

    if action_type == YutoriN1ActionType.SCROLL:
        direction = args.get("direction", "down")
        # N1 scroll amounts are small integers (e.g. 3). Multiply by 100 to get
        # pixel values consistent with how the internal Yutori N1 navigator handles scroll.
        amount = int(args.get("amount", 3)) * 100
        if direction == "up":
            return ScrollAction(x=x, y=y, scroll_x=0, scroll_y=-amount, **base_params)
        if direction == "down":
            return ScrollAction(x=x, y=y, scroll_x=0, scroll_y=amount, **base_params)
        if direction == "left":
            return ScrollAction(x=x, y=y, scroll_x=-amount, scroll_y=0, **base_params)
        if direction == "right":
            return ScrollAction(x=x, y=y, scroll_x=amount, scroll_y=0, **base_params)
        return ScrollAction(x=x, y=y, scroll_x=0, scroll_y=amount, **base_params)

    if action_type == YutoriN1ActionType.STOP:
        summary = args.get("summary") or args.get("message")
        return CompleteAction(data_extraction_goal=summary, **base_params)

    if action_type == YutoriN1ActionType.SLEEP:
        return WaitAction(**base_params)

    if action_type == YutoriN1ActionType.GO_BACK:
        return GoBackAction(**base_params)

    if action_type == YutoriN1ActionType.GO_FORWARD:
        return KeypressAction(keys=["Alt+ArrowRight"], **base_params)

    if action_type == YutoriN1ActionType.REFRESH:
        return KeypressAction(keys=["F5"], **base_params)

    if action_type == YutoriN1ActionType.GOTO_URL:
        url = args.get("url", "")
        if not url:
            return WaitAction(**base_params)
        return GotoUrlAction(url=url, **base_params)

    if action_type == YutoriN1ActionType.WAIT:
        return WaitAction(**base_params)

    if action_type == YutoriN1ActionType.DRAG:
        # Use start_coordinates for the source click
        raw_start = args.get("start_coordinates")
        if raw_start:
            sx = _denormalize(float(raw_start[0]), viewport_width)
            sy = _denormalize(float(raw_start[1]), viewport_height)
            return ClickAction(element_id="", x=sx, y=sy, button="left", repeat=1, **base_params)
        return WaitAction(**base_params)

    if action_type in (
        YutoriN1ActionType.MOUSE_MOVE,
        YutoriN1ActionType.MOUSE_DOWN,
        YutoriN1ActionType.MOUSE_UP,
    ):
        if coord is not None:
            return ClickAction(element_id="", x=x, y=y, button="left", repeat=1, **base_params)
        return WaitAction(**base_params)

    if action_type == YutoriN1ActionType.HOLD_KEY:
        key = args.get("key", "")
        if not key:
            return WaitAction(**base_params)
        return KeypressAction(keys=[key], **base_params)

    if action_type == YutoriN1ActionType.ZOOM_INTO_AREA or action_type == YutoriN1ActionType.ZOOM:
        # No zoom in Skyvern; click center of the area to proceed
        if coord is not None:
            return ClickAction(element_id="", x=x, y=y, button="left", repeat=1, **base_params)
        return WaitAction(**base_params)

    if action_type == YutoriN1ActionType.EXTRACT_CONTENT:
        # N1 wants to extract data — treat as task completion
        summary = args.get("summary") or args.get("query") or args.get("goal") or "Content extracted"
        return CompleteAction(data_extraction_goal=summary, **base_params)

    if action_type in (
        YutoriN1ActionType.EXTRACT_ELEMENTS,
        YutoriN1ActionType.FIND,
        YutoriN1ActionType.SET_ELEMENT_VALUE,
        YutoriN1ActionType.EXECUTE_JS,
    ):
        # DOM-manipulation tools not supported in Skyvern — wait and let N1 retry
        return WaitAction(**base_params)

    return WaitAction(**base_params)
