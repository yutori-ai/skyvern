from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from skyvern.webeye.actions.actions import (
    ClickAction,
    CompleteAction,
    InputTextAction,
    KeypressAction,
    ScrollAction,
    WaitAction,
)

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
    EXTRACT_CONTENT = "extract_content_and_links"


def parse_and_convert_yutori_n1_actions(
    response: Any,
    viewport_width: int,
    viewport_height: int,
) -> list[Action]:
    message = response.choices[0].message
    if not message.tool_calls:
        return []

    actions: list[Action] = []
    for tc in message.tool_calls:
        try:
            args = json.loads(tc.function.arguments)
            action = _convert_tool_call(tc.function.name, args, viewport_width, viewport_height)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if action is not None:
            actions.append(action)
    return actions


def _denormalize(value: float, dimension: int) -> int:
    return int(value / N1_COORDINATE_SPACE * dimension)


def _parse_coordinate(args: dict, viewport_width: int, viewport_height: int) -> tuple[int, int] | None:
    raw = args.get("coordinate")
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
) -> Action | None:
    try:
        action_type = YutoriN1ActionType(name)
    except ValueError:
        return None

    coord = _parse_coordinate(args, viewport_width, viewport_height)
    x = coord[0] if coord is not None else None
    y = coord[1] if coord is not None else None

    if action_type == YutoriN1ActionType.LEFT_CLICK:
        return ClickAction(x=x, y=y, button="left", repeat=1)

    if action_type == YutoriN1ActionType.DOUBLE_CLICK:
        return ClickAction(x=x, y=y, button="left", repeat=2)

    if action_type == YutoriN1ActionType.RIGHT_CLICK:
        return ClickAction(x=x, y=y, button="right", repeat=1)

    if action_type == YutoriN1ActionType.TRIPLE_CLICK:
        return ClickAction(x=x, y=y, button="left", repeat=3)

    if action_type == YutoriN1ActionType.MIDDLE_CLICK:
        return ClickAction(x=x, y=y, button="left", repeat=1)

    if action_type == YutoriN1ActionType.TYPE:
        return InputTextAction(text=args.get("text", ""))

    if action_type == YutoriN1ActionType.KEY_PRESS:
        key = args.get("key", "")
        return KeypressAction(keys=[key] if key else [])

    if action_type == YutoriN1ActionType.SCROLL:
        direction = args.get("direction", "down")
        amount = int(args.get("amount", 3))
        if direction == "up":
            return ScrollAction(x=x, y=y, scroll_x=0, scroll_y=-amount)
        if direction == "down":
            return ScrollAction(x=x, y=y, scroll_x=0, scroll_y=amount)
        if direction == "left":
            return ScrollAction(x=x, y=y, scroll_x=-amount, scroll_y=0)
        if direction == "right":
            return ScrollAction(x=x, y=y, scroll_x=amount, scroll_y=0)
        return ScrollAction(x=x, y=y, scroll_x=0, scroll_y=amount)

    if action_type == YutoriN1ActionType.STOP:
        summary = args.get("summary") or args.get("message")
        return CompleteAction(data_extraction_goal=summary)

    if action_type == YutoriN1ActionType.SLEEP:
        return WaitAction()

    return WaitAction()
