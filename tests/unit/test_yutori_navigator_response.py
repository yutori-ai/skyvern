from unittest.mock import AsyncMock

import pytest

from skyvern.forge.sdk.api.llm.yutori_navigator_llm_caller import NavigatorResponse
from skyvern.forge.sdk.api.llm.yutori_navigator_response import parse_navigator_response_to_actions
from skyvern.webeye.actions.actions import NullAction
from skyvern.webeye.actions.handler import handle_null_action


def _tool_call(name: str, arguments: str) -> dict:
    return {
        "id": "tc_1",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


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
