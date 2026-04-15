from unittest.mock import AsyncMock

import pytest
from yutori.navigator.tools import EXECUTE_JS_SCRIPT

from skyvern.forge.sdk.api.llm.yutori_navigator_llm_caller import NavigatorResponse
from skyvern.forge.sdk.api.llm.yutori_navigator_response import parse_navigator_response_to_actions
from skyvern.webeye.actions.actions import (
    ClickAction,
    CompleteAction,
    ExecuteJsAction,
    GoBackAction,
    GoForwardAction,
    NullAction,
    ReloadPageAction,
)
from skyvern.webeye.actions.handler import handle_null_action


def _tool_call(name: str, arguments: str) -> dict:
    return {
        "id": "tc_1",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


def test_parse_left_click_maps_coordinates() -> None:
    response = NavigatorResponse(
        tool_calls=[
            _tool_call("left_click", '{"coordinates":[500,250]}'),
        ]
    )

    actions = parse_navigator_response_to_actions(response, viewport_width=1200, viewport_height=800)

    assert len(actions) == 1
    assert isinstance(actions[0], ClickAction)
    assert actions[0].x == 600
    assert actions[0].y == 200
    assert actions[0].button == "left"
    assert actions[0].repeat == 1


def test_parse_navigation_actions() -> None:
    response = NavigatorResponse(
        tool_calls=[
            _tool_call("go_back", "{}"),
            _tool_call("go_forward", "{}"),
            _tool_call("refresh", "{}"),
        ]
    )

    actions = parse_navigator_response_to_actions(response, viewport_width=1200, viewport_height=800)

    assert len(actions) == 3
    assert isinstance(actions[0], GoBackAction)
    assert isinstance(actions[1], GoForwardAction)
    assert isinstance(actions[2], ReloadPageAction)


def test_parse_completion_without_tool_calls() -> None:
    response = NavigatorResponse(content="Finished the task", tool_calls=[])

    actions = parse_navigator_response_to_actions(response, viewport_width=1200, viewport_height=800)

    assert len(actions) == 1
    assert isinstance(actions[0], CompleteAction)
    assert actions[0].data_extraction_goal == "Finished the task"


def test_parse_wait_tool_returns_sleeping_null_action() -> None:
    response = NavigatorResponse(
        tool_calls=[
            _tool_call("wait", '{"duration": 7}'),
        ]
    )

    actions = parse_navigator_response_to_actions(response, viewport_width=1280, viewport_height=800)

    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, NullAction)
    assert action.sleep_seconds == 7
    assert action.result_data == "Waited 7s"


def test_parse_wait_tool_defaults_when_duration_is_null() -> None:
    response = NavigatorResponse(
        tool_calls=[
            _tool_call("wait", '{"duration": null}'),
        ]
    )

    actions = parse_navigator_response_to_actions(response, viewport_width=1280, viewport_height=800)

    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, NullAction)
    assert action.sleep_seconds == 5
    assert action.result_data == "Waited 5s"


def test_parse_ref_scroll_returns_null_action_without_extra_scroll() -> None:
    response = NavigatorResponse(
        tool_calls=[
            _tool_call("scroll", '{"ref": "ref_123", "coordinates": [500, 250], "direction": "down"}'),
        ]
    )

    actions = parse_navigator_response_to_actions(response, viewport_width=1280, viewport_height=800)

    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, NullAction)
    assert action.sleep_seconds == 0
    assert action.result_data == "Scrolled to element"


def test_parse_ref_scroll_accepts_singular_coordinate_key() -> None:
    response = NavigatorResponse(
        tool_calls=[
            _tool_call("scroll", '{"ref": "ref_123", "coordinate": [500, 250], "direction": "down"}'),
        ]
    )

    actions = parse_navigator_response_to_actions(response, viewport_width=1280, viewport_height=800)

    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, NullAction)
    assert action.result_data == "Scrolled to element"


def test_parse_scroll_defaults_when_amount_is_null() -> None:
    response = NavigatorResponse(
        tool_calls=[
            _tool_call("scroll", '{"coordinates": [500, 250], "amount": null, "direction": "down"}'),
        ]
    )

    actions = parse_navigator_response_to_actions(response, viewport_width=1280, viewport_height=800)

    assert len(actions) == 1
    action = actions[0]
    assert action.action_type == "scroll"
    assert action.scroll_y == 300


def test_parse_expanded_tools_map_to_execute_js_action() -> None:
    response = NavigatorResponse(
        tool_calls=[
            _tool_call("extract_elements", '{"filter":"visible"}'),
        ]
    )

    actions = parse_navigator_response_to_actions(response, viewport_width=1200, viewport_height=800)

    assert len(actions) == 1
    assert isinstance(actions[0], ExecuteJsAction)


def test_parse_execute_js_uses_sdk_wrapper() -> None:
    response = NavigatorResponse(
        tool_calls=[
            _tool_call("execute_js", '{"text":"document.title"}'),
        ]
    )

    actions = parse_navigator_response_to_actions(response, viewport_width=1200, viewport_height=800)

    assert len(actions) == 1
    assert isinstance(actions[0], ExecuteJsAction)
    assert EXECUTE_JS_SCRIPT in actions[0].js_code


def test_parse_execute_js_unwraps_sdk_result() -> None:
    response = NavigatorResponse(
        tool_calls=[
            _tool_call("execute_js", '{"text":"document.title"}'),
        ]
    )

    actions = parse_navigator_response_to_actions(response, viewport_width=1200, viewport_height=800)

    assert len(actions) == 1
    assert isinstance(actions[0], ExecuteJsAction)
    assert "r.success === false" in actions[0].js_code
    assert "if (r.hasResult) return typeof r.result === 'string' ? r.result : JSON.stringify(r.result);" in actions[0].js_code


@pytest.mark.asyncio
async def test_handle_null_action_waits_and_returns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("skyvern.webeye.actions.handler.asyncio.sleep", sleep_mock)

    results = await handle_null_action(
        NullAction(sleep_seconds=1.5, result_data="Waited 1.5s"),
        None,
        None,
        None,
        None,
    )

    sleep_mock.assert_awaited_once_with(1.5)
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].data == "Waited 1.5s"
