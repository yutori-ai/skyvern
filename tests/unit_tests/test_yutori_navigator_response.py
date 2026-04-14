from types import SimpleNamespace

from skyvern.forge.sdk.api.llm.yutori_navigator_response import parse_navigator_response_to_actions
from skyvern.webeye.actions.actions import (
    ClickAction,
    CompleteAction,
    GoBackAction,
    GoForwardAction,
    ReloadPageAction,
)


def test_parse_navigator_left_click_maps_coordinates() -> None:
    response = SimpleNamespace(
        content="",
        tool_calls=[
            {
                "id": "call_1",
                "function": {
                    "name": "left_click",
                    "arguments": '{"coordinates":[500,250]}',
                },
            }
        ],
        finish_reason="tool_calls",
    )

    actions = parse_navigator_response_to_actions(response, viewport_width=1200, viewport_height=800)

    assert len(actions) == 1
    assert isinstance(actions[0], ClickAction)
    assert actions[0].x == 600
    assert actions[0].y == 200
    assert actions[0].button == "left"
    assert actions[0].repeat == 1


def test_parse_navigator_navigation_actions() -> None:
    response = SimpleNamespace(
        content="",
        tool_calls=[
            {"id": "call_1", "function": {"name": "go_back", "arguments": "{}"}},
            {"id": "call_2", "function": {"name": "go_forward", "arguments": "{}"}},
            {"id": "call_3", "function": {"name": "refresh", "arguments": "{}"}},
        ],
        finish_reason="tool_calls",
    )

    actions = parse_navigator_response_to_actions(response, viewport_width=1200, viewport_height=800)

    assert len(actions) == 3
    assert isinstance(actions[0], GoBackAction)
    assert isinstance(actions[1], GoForwardAction)
    assert isinstance(actions[2], ReloadPageAction)


def test_parse_navigator_completion_without_tool_calls() -> None:
    response = SimpleNamespace(content="Finished the task", tool_calls=[], finish_reason="stop")

    actions = parse_navigator_response_to_actions(response, viewport_width=1200, viewport_height=800)

    assert len(actions) == 1
    assert isinstance(actions[0], CompleteAction)
    assert actions[0].data_extraction_goal == "Finished the task"


def test_parse_navigator_expanded_tools_only_returns_no_browser_actions() -> None:
    response = SimpleNamespace(
        content="",
        tool_calls=[
            {
                "id": "call_1",
                "function": {
                    "name": "extract_elements",
                    "arguments": '{"filter":"visible"}',
                },
            }
        ],
        finish_reason="tool_calls",
    )

    actions = parse_navigator_response_to_actions(response, viewport_width=1200, viewport_height=800)

    assert actions == []
