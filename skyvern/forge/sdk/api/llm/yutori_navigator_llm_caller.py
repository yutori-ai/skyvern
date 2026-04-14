"""Yutori Navigator LLM Caller — extends Skyvern's LLMCaller for conversation management.

Manages multi-turn conversation history for the Yutori Navigator computer-use model.
Uses the Yutori SDK for screenshot encoding, key mapping, and coordinate conversion.
Routes API calls through the base class's _dispatch_llm_call() for artifact persistence,
cost tracking, and error handling.

Conversation format per https://docs.yutori.com/reference/browser-use:
  user (task + screenshot) -> assistant (tool_calls) -> tool (result + url + screenshot) -> ...
"""

from __future__ import annotations

import json
import logging
from typing import Any

import structlog

from yutori.navigator import format_stop_and_summarize, screenshot_to_data_url

from dataclasses import dataclass, field

from skyvern.forge.sdk.api.llm.api_handler_factory import LLMCaller
from skyvern.forge.sdk.models import Step
from skyvern.forge.sdk.schemas.tasks import Task


@dataclass
class NavigatorResponse:
    """Normalized response from Yutori Navigator for use in the agent loop."""
    content: str = ""
    finish_reason: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    """Each tool_call is {"id": ..., "function": {"name": ..., "arguments": ...}}"""

_file_log = logging.getLogger("yutori_skyvern")
if not _file_log.handlers:
    _h = logging.FileHandler("/tmp/yutori_skyvern.log")
    _h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _file_log.addHandler(_h)
    _file_log.setLevel(logging.DEBUG)

LOG = structlog.get_logger()


def _describe_action_result(name: str, arguments_json: str) -> str:
    """Generate a descriptive tool result string matching the SDK's navigator example."""
    try:
        args = json.loads(arguments_json)
    except (json.JSONDecodeError, TypeError):
        args = {}

    if name in ("left_click", "double_click", "triple_click", "middle_click", "right_click"):
        button = {"middle_click": "middle", "right_click": "right"}.get(name, "left")
        count = {"double_click": 2, "triple_click": 3}.get(name, 1)
        return f"Clicked {count}x with {button}"
    if name == "mouse_move":
        return "Mouse moved and hovering"
    if name == "mouse_down":
        return "Mouse button pressed"
    if name == "mouse_up":
        return "Mouse button released"
    if name == "drag":
        return "Dragged successfully"
    if name == "scroll":
        return f"Scrolled {args.get('direction', 'down')}"
    if name == "type":
        return f"Typed {len(args.get('text', ''))} characters"
    if name == "key_press":
        return f"Pressed key: {args.get('key', '')}"
    if name == "hold_key":
        return f"Held key: {args.get('key', '')}"
    if name == "goto_url":
        return f"Navigated to {args.get('url', '')}"
    if name == "go_back":
        return "Navigated back"
    if name == "go_forward":
        return "Navigated forward"
    if name == "refresh":
        return "Refreshed the page"
    if name == "wait":
        return f"Waited {args.get('duration', 5)}s"
    return f"Executed {name}"


class YutoriNavigatorLLMCaller(LLMCaller):
    """Yutori Navigator LLM caller extending Skyvern's LLMCaller base class.

    Manages multi-turn conversation via self.message_history (inherited).
    API calls route through the base class → _dispatch_llm_call() → _call_yutori_navigator()
    in api_handler_factory.py, giving us artifact persistence, cost tracking, and
    error handling for free.
    """

    def __init__(self, llm_key: str, screenshot_scaling_enabled: bool = False):
        super().__init__(llm_key, screenshot_scaling_enabled)
        self._conversation_initialized = False
        self._pending_tool_calls: list[dict[str, str]] = []
        self._task: Task | None = None

    def initialize_conversation(self, task: Task) -> None:
        """Initialize conversation with task description. Called once at step 0."""
        if not self._conversation_initialized:
            self._task = task
            self.message_history = []
            self._pending_tool_calls = []
            self._conversation_initialized = True
            LOG.debug("Initialized Yutori Navigator conversation", task_id=task.task_id)

    def add_tool_result(self, screenshot_bytes: bytes, current_url: str) -> None:
        """Add screenshot and URL as a tool result or initial user message.

        First turn: user message with task text + screenshot.
        Subsequent turns: tool-role messages with action result + URL + screenshot.
        """
        if not screenshot_bytes:
            return

        data_url = screenshot_to_data_url(screenshot_bytes)
        image_content = {
            "type": "image_url",
            "image_url": {"url": data_url},
        }

        if not self.message_history:
            # First turn: user message with task + screenshot
            user_content: list[dict[str, Any]] = []
            if self._task:
                user_content.append({"type": "text", "text": f"Task: {self._task.navigation_goal or ''}"})
            user_content.append(image_content)
            self.message_history.append({"role": "user", "content": user_content})
        elif self._pending_tool_calls:
            # Subsequent turns: tool response with action result + URL + screenshot
            for i, tc in enumerate(self._pending_tool_calls):
                result_text = _describe_action_result(tc["name"], tc["arguments"])
                result_text += f"\nCurrent URL: {current_url}"
                if i < len(self._pending_tool_calls) - 1:
                    self.message_history.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_text,
                    })
                else:
                    # Last tool_call gets the screenshot
                    self.message_history.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": [
                            {"type": "text", "text": result_text},
                            image_content,
                        ],
                    })
            self._pending_tool_calls = []

    def add_dom_tool_result(self, tool_call_id: str, result: str) -> None:
        """Add a result from an expanded DOM tool (extract_elements, find, etc.)."""
        self.message_history.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        })

    def add_stop_and_summarize(self, screenshot_bytes: bytes, current_url: str) -> None:
        """Append a stop-and-summarize user message for the last step.

        Called when max steps is about to be reached, so the model produces
        a summary instead of another action. This becomes part of the normal
        step's LLM call, keeping costs and artifacts tracked.

        Flushes any pending tool_call responses first so the conversation
        stays valid (assistant tool_calls must be followed by tool responses
        before a user message).
        """
        # Flush pending tool calls from the previous step's response
        if self._pending_tool_calls:
            data_url = screenshot_to_data_url(screenshot_bytes)
            image_content = {"type": "image_url", "image_url": {"url": data_url}}
            for i, tc in enumerate(self._pending_tool_calls):
                result_text = _describe_action_result(tc["name"], tc["arguments"])
                result_text += f"\nCurrent URL: {current_url}"
                if i < len(self._pending_tool_calls) - 1:
                    self.message_history.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_text,
                    })
                else:
                    self.message_history.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": [
                            {"type": "text", "text": result_text},
                            image_content,
                        ],
                    })
            self._pending_tool_calls = []

        task_goal = self._task.navigation_goal if self._task else "the given task"
        data_url = screenshot_to_data_url(screenshot_bytes)
        stop_message = format_stop_and_summarize(task_goal)

        self.message_history.append({
            "role": "user",
            "content": [
                {"type": "text", "text": stop_message},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        })

    async def call(self, **kwargs: Any) -> Any:
        """Override to route through base class with message history."""
        return await super().call(
            use_message_history=True,
            raw_response=True,
            **kwargs,
        )

    async def generate_response(self, step: Step) -> NavigatorResponse:
        """Generate Navigator response and update conversation history.

        Returns an NavigatorResponse with normalized fields so callers don't need
        to handle dict vs object differences from the base class.
        """
        response = await self.call(step=step)

        # raw_response=True returns response.model_dump(exclude_none=True) — a dict
        if isinstance(response, dict):
            choice = response.get("choices", [{}])[0]
            message_data = choice.get("message", {})
            tool_calls_data = message_data.get("tool_calls") or []
            content = message_data.get("content") or ""
            finish_reason = choice.get("finish_reason")
            request_id = response.get("request_id")
        else:
            msg = response.choices[0].message
            content = msg.content or ""
            finish_reason = response.choices[0].finish_reason
            tool_calls_data = []
            if msg.tool_calls:
                tool_calls_data = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ]
            request_id = getattr(response, "request_id", None) or (
                response.model_extra.get("request_id") if getattr(response, "model_extra", None) else None
            )

        # Debug logging
        tool_names = [tc["function"]["name"] for tc in tool_calls_data]
        task_id = self._task.task_id if self._task else None
        _file_log.info(json.dumps({
            "task_id": task_id,
            "step_order": step.order,
            "request_id": request_id,
            "finish_reason": finish_reason,
            "tool_calls": tool_names,
        }))

        # Append assistant message to history
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls_data:
            assistant_msg["tool_calls"] = tool_calls_data
            self._pending_tool_calls = [
                {"id": tc["id"], "name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}
                for tc in tool_calls_data
            ]
        self.message_history.append(assistant_msg)

        return NavigatorResponse(
            content=content,
            finish_reason=finish_reason,
            tool_calls=tool_calls_data,
        )
