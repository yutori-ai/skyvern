from __future__ import annotations

import base64
import json
import logging
from typing import Any

import structlog
from openai import AsyncOpenAI

_file_log = logging.getLogger("n1_skyvern")
if not _file_log.handlers:
    _h = logging.FileHandler("/tmp/n1_skyvern.log")
    _h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _file_log.addHandler(_h)
    _file_log.setLevel(logging.DEBUG)

from skyvern.forge.sdk.models import Step
from skyvern.forge.sdk.schemas.tasks import Task

LOG = structlog.get_logger()

YUTORI_N1_DEFAULT_MODEL = "n1-latest"
YUTORI_N1_BASE_URL = "https://api.yutori.com/v1"

# N1 reasons over a sliding window of recent screenshots; sending the full history
# wastes bandwidth without improving results.
YUTORI_N1_MAX_SCREENSHOT_TURNS = 2


class YutoriN1LLMCaller:
    """Multi-turn conversation manager for the Yutori N1 computer-use model.

    N1 is OpenAI Chat Completions-compatible, returning browser actions as tool_calls.
    Coordinates are predicted in a 1000x1000 normalized space and must be denormalized
    to viewport pixel coordinates before use.

    Conversation format per https://docs.yutori.com/reference/n1#multi-turn-conversations:
      user (task + screenshot) -> assistant (tool_calls) -> tool (url + screenshot) -> ...
    The screenshot and current URL go in the tool response, not a separate user message.
    """

    def __init__(
        self,
        api_key: str,
        model: str = YUTORI_N1_DEFAULT_MODEL,
        base_url: str = YUTORI_N1_BASE_URL,
    ) -> None:
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self._messages: list[dict[str, Any]] = []
        self._task: Task | None = None
        self._pending_tool_call_ids: list[str] = []

    def initialize_conversation(self, task: Task) -> None:
        self._task = task
        self._messages = []
        self._pending_tool_call_ids = []
        LOG.debug("Initialized Yutori N1 conversation", task_id=task.task_id)

    def add_tool_result(self, screenshot_bytes: bytes, current_url: str) -> None:
        """Add the screenshot and URL as a tool result or initial user message.

        On the first turn (no messages yet), this creates a user message with the
        task description and screenshot. On subsequent turns, the screenshot and URL
        are embedded into tool-role messages for the pending tool_calls from the
        previous assistant response.
        """
        if not screenshot_bytes:
            return

        screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
        image_content = {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"},
        }

        if not self._messages:
            # First turn: user message with task + screenshot
            user_content: list[dict[str, Any]] = []
            if self._task:
                user_content.append({"type": "text", "text": f"Task: {self._task.navigation_goal or ''}"})
            user_content.append(image_content)
            self._messages.append({"role": "user", "content": user_content})
        elif self._pending_tool_call_ids:
            # Subsequent turns: screenshot + URL go in the last tool response.
            # Earlier tool_calls get a text-only acknowledgment.
            for i, tc_id in enumerate(self._pending_tool_call_ids):
                if i < len(self._pending_tool_call_ids) - 1:
                    self._messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": f"Current URL: {current_url}",
                    })
                else:
                    # Last tool_call gets the screenshot
                    self._messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": [
                            {"type": "text", "text": f"Current URL: {current_url}"},
                            image_content,
                        ],
                    })
            self._pending_tool_call_ids = []

        LOG.debug("Added tool result to Yutori N1 conversation", total_messages=len(self._messages))

    def _build_trimmed_messages(self) -> list[dict[str, Any]]:
        """Return conversation with images stripped from all but the last N screenshot turns.

        Older turns keep their text content so N1 retains action history context.
        Screenshots can appear in user messages (first turn) or tool messages (subsequent).
        """
        if not self._messages:
            return []

        def _has_image(msg: dict[str, Any]) -> bool:
            content = msg.get("content")
            if not isinstance(content, list):
                return False
            return any(item.get("type") == "image_url" for item in content)

        screenshot_indices = [i for i, msg in enumerate(self._messages) if _has_image(msg)]
        keep_images = set(screenshot_indices[-YUTORI_N1_MAX_SCREENSHOT_TURNS:])

        result = []
        for i, msg in enumerate(self._messages):
            if i not in keep_images and isinstance(msg.get("content"), list):
                text_only = [item for item in msg["content"] if item.get("type") != "image_url"]
                if not text_only:
                    # Tool message can't be empty — use text fallback
                    if msg["role"] == "tool":
                        result.append({**msg, "content": "Action executed."})
                    # User message with only image and no text — skip
                    continue
                result.append({**msg, "content": text_only})
            else:
                result.append(msg)

        return result

    async def generate_response(self, step: Step) -> Any:
        system_prompt = self._build_system_prompt()
        messages: list[Any] = self._build_trimmed_messages()
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            max_completion_tokens=4096,
        )

        request_id = (
            getattr(response, "request_id", None)
            or (response.model_extra.get("request_id") if response.model_extra else None)
        )
        finish_reason = response.choices[0].finish_reason
        tool_names = (
            [tc.function.name for tc in response.choices[0].message.tool_calls]
            if response.choices[0].message.tool_calls
            else []
        )
        LOG.info(
            "Yutori N1 response received",
            step_order=step.order,
            request_id=request_id,
            finish_reason=finish_reason,
        )
        task_id = self._task.task_id if self._task else None
        _file_log.info(json.dumps({
            "task_id": task_id,
            "step_order": step.order,
            "request_id": request_id,
            "finish_reason": finish_reason,
            "tool_calls": tool_names,
        }))

        self._append_assistant_message(response.choices[0].message)
        return response

    def _build_system_prompt(self) -> str | None:
        # N1 is trained to handle tasks from the user message directly.
        # A system prompt overrides N1's tool-calling behavior, so we don't use one.
        return None

    def _append_assistant_message(self, message: Any) -> None:
        msg: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
            # Store tool_call IDs so the next add_tool_result() can create
            # the matching tool-role messages with the new screenshot.
            self._pending_tool_call_ids = [tc.id for tc in message.tool_calls]
        self._messages.append(msg)
