"""Convert Yutori Navigator tool_call responses into Skyvern action objects."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog
from yutori.navigator import denormalize_coordinates, map_key_to_playwright, map_keys_individual

from skyvern.webeye.actions.actions import (
    ClickAction,
    CompleteAction,
    DragAction,
    GoBackAction,
    GoForwardAction,
    GotoUrlAction,
    InputTextAction,
    KeypressAction,
    LeftMouseAction,
    MoveAction,
    ReloadPageAction,
    ScrollAction,
    WaitAction,
)

if TYPE_CHECKING:
    from skyvern.forge.sdk.schemas.tasks import Task
    from skyvern.forge.sdk.schemas.steps import Step

LOG = structlog.get_logger()

Action = Any

# Expanded tool set actions that are handled inline by the agent loop
# (they return data to Navigator, not browser navigation actions).
EXPANDED_TOOL_ACTIONS = frozenset({"extract_elements", "find", "set_element_value", "execute_js"})


class YutoriNavigatorActionType(StrEnum):
    # Core browser tools
    LEFT_CLICK = "left_click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    TRIPLE_CLICK = "triple_click"
    MIDDLE_CLICK = "middle_click"
    TYPE = "type"
    KEY_PRESS = "key_press"
    SCROLL = "scroll"
    STOP = "stop"
    WAIT = "wait"
    DRAG = "drag"
    MOUSE_MOVE = "mouse_move"
    MOUSE_DOWN = "mouse_down"
    MOUSE_UP = "mouse_up"
    HOLD_KEY = "hold_key"
    GO_BACK = "go_back"
    GO_FORWARD = "go_forward"
    GOTO_URL = "goto_url"
    REFRESH = "refresh"
    # Expanded tool set (handled inline by agent, not converted to Skyvern actions)
    EXTRACT_ELEMENTS = "extract_elements"
    FIND = "find"
    SET_ELEMENT_VALUE = "set_element_value"
    EXECUTE_JS = "execute_js"
    # Legacy actions (backward compat)
    EXTRACT_CONTENT = "extract_content_and_links"
    SLEEP = "sleep"


def parse_navigator_response_to_actions(
    nav_response: Any,
    viewport_width: int,
    viewport_height: int,
    task: "Task | None" = None,
    step: "Step | None" = None,
) -> list[Action]:
    """Convert a NavigatorResponse to Skyvern actions.

    Expects nav_response to have .content, .tool_calls (list of dicts), .finish_reason.
    """
    tool_calls = nav_response.tool_calls if hasattr(nav_response, "tool_calls") else []
    content = nav_response.content if hasattr(nav_response, "content") else ""

    if not tool_calls:
        base_params = _base_params(task, step, 0)
        summary = content or "Task completed"
        return [CompleteAction(data_extraction_goal=summary, **base_params)]

    actions: list[Action] = []
    for idx, tc in enumerate(tool_calls):
        name = tc["function"]["name"] if isinstance(tc, dict) else tc.function.name
        arguments = tc["function"]["arguments"] if isinstance(tc, dict) else tc.function.arguments

        # Skip expanded tool set actions — handled inline by agent loop
        if name in EXPANDED_TOOL_ACTIONS:
            continue
        try:
            args = json.loads(arguments)
            action = _convert_tool_call(name, args, viewport_width, viewport_height, task, step, idx)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            LOG.warning("Navigator tool call conversion failed", name=name, error=str(e))
            continue
        if action is not None:
            actions.append(action)
        else:
            LOG.warning("Navigator unknown tool call", name=name)

    if not actions and tool_calls:
        all_names = [
            tc["function"]["name"] if isinstance(tc, dict) else tc.function.name
            for tc in tool_calls
        ]
        if all(n in EXPANDED_TOOL_ACTIONS for n in all_names):
            return []
        base_params = _base_params(task, step, 0)
        return [CompleteAction(data_extraction_goal="Task completed (unknown action types)", **base_params)]

    return actions


def _base_params(task: "Task | None", step: "Step | None", action_order: int) -> dict[str, Any]:
    if task is None or step is None:
        return {}
    return {
        "organization_id": task.organization_id,
        "workflow_run_id": task.workflow_run_id,
        "task_id": task.task_id,
        "step_id": step.step_id,
        "step_order": step.order,
        "action_order": action_order,
    }


def _parse_coordinate(args: dict, viewport_width: int, viewport_height: int) -> tuple[int, int] | None:
    raw = args.get("coordinates") or args.get("coordinate")
    if raw is None:
        return None
    return denormalize_coordinates(raw, viewport_width, viewport_height)


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
        action_type = YutoriNavigatorActionType(name)
    except ValueError:
        return None

    coord = _parse_coordinate(args, viewport_width, viewport_height)
    x = coord[0] if coord is not None else None
    y = coord[1] if coord is not None else None
    bp = _base_params(task, step, action_order)

    # ---- Click actions ----
    if action_type == YutoriNavigatorActionType.LEFT_CLICK:
        return ClickAction(element_id="", x=x, y=y, button="left", repeat=1, **bp)

    if action_type == YutoriNavigatorActionType.DOUBLE_CLICK:
        return ClickAction(element_id="", x=x, y=y, button="left", repeat=2, **bp)

    if action_type == YutoriNavigatorActionType.RIGHT_CLICK:
        return ClickAction(element_id="", x=x, y=y, button="right", repeat=1, **bp)

    if action_type == YutoriNavigatorActionType.TRIPLE_CLICK:
        return ClickAction(element_id="", x=x, y=y, button="left", repeat=3, **bp)

    if action_type == YutoriNavigatorActionType.MIDDLE_CLICK:
        return ClickAction(element_id="", x=x, y=y, button="middle", repeat=1, **bp)

    # ---- Mouse actions ----
    if action_type == YutoriNavigatorActionType.MOUSE_MOVE:
        if coord is not None:
            return MoveAction(x=x, y=y, **bp)
        return WaitAction(**bp)

    if action_type == YutoriNavigatorActionType.MOUSE_DOWN:
        if coord is not None:
            return LeftMouseAction(x=x, y=y, direction="down", **bp)
        return WaitAction(**bp)

    if action_type == YutoriNavigatorActionType.MOUSE_UP:
        if coord is not None:
            return LeftMouseAction(x=x, y=y, direction="up", **bp)
        return WaitAction(**bp)

    if action_type == YutoriNavigatorActionType.DRAG:
        raw_start = args.get("start_coordinates")
        raw_end = args.get("end_coordinates") or args.get("coordinates")
        if raw_start and raw_end:
            sx, sy = denormalize_coordinates(raw_start, viewport_width, viewport_height)
            ex, ey = denormalize_coordinates(raw_end, viewport_width, viewport_height)
            return DragAction(start_x=sx, start_y=sy, path=[(ex, ey)], **bp)
        if raw_start:
            sx, sy = denormalize_coordinates(raw_start, viewport_width, viewport_height)
            return DragAction(start_x=sx, start_y=sy, path=[], **bp)
        return WaitAction(**bp)

    # ---- Keyboard actions ----
    if action_type == YutoriNavigatorActionType.TYPE:
        return InputTextAction(element_id="", text=args.get("text", ""), **bp)

    if action_type == YutoriNavigatorActionType.KEY_PRESS:
        key_expr = args.get("key") or args.get("key_comb", "")
        if not key_expr:
            return None
        return KeypressAction(keys=map_key_to_playwright(key_expr), **bp)

    if action_type == YutoriNavigatorActionType.HOLD_KEY:
        key_expr = args.get("key", "")
        if not key_expr:
            return WaitAction(**bp)
        return KeypressAction(keys=map_keys_individual(key_expr), hold=True, **bp)

    # ---- Scroll ----
    if action_type == YutoriNavigatorActionType.SCROLL:
        direction = args.get("direction", "down")
        amount = int(args.get("amount", 3)) * 100
        if direction == "up":
            return ScrollAction(x=x, y=y, scroll_x=0, scroll_y=-amount, **bp)
        if direction == "down":
            return ScrollAction(x=x, y=y, scroll_x=0, scroll_y=amount, **bp)
        if direction == "left":
            return ScrollAction(x=x, y=y, scroll_x=-amount, scroll_y=0, **bp)
        if direction == "right":
            return ScrollAction(x=x, y=y, scroll_x=amount, scroll_y=0, **bp)
        return ScrollAction(x=x, y=y, scroll_x=0, scroll_y=amount, **bp)

    # ---- Navigation ----
    if action_type == YutoriNavigatorActionType.GOTO_URL:
        url = args.get("url", "")
        if not url:
            return WaitAction(**bp)
        return GotoUrlAction(url=url, **bp)

    if action_type == YutoriNavigatorActionType.GO_BACK:
        return GoBackAction(**bp)

    if action_type == YutoriNavigatorActionType.GO_FORWARD:
        return GoForwardAction(**bp)

    if action_type == YutoriNavigatorActionType.REFRESH:
        return ReloadPageAction(**bp)

    # ---- Completion / Wait ----
    if action_type == YutoriNavigatorActionType.STOP:
        summary = args.get("summary") or args.get("message")
        return CompleteAction(data_extraction_goal=summary, **bp)

    if action_type in (YutoriNavigatorActionType.WAIT, YutoriNavigatorActionType.SLEEP):
        return WaitAction(**bp)

    # Legacy extract action
    if action_type == YutoriNavigatorActionType.EXTRACT_CONTENT:
        summary = args.get("summary") or args.get("query") or args.get("goal") or "Content extracted"
        return CompleteAction(data_extraction_goal=summary, **bp)

    return WaitAction(**bp)
