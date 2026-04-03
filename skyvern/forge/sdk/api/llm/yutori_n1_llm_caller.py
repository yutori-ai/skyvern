from __future__ import annotations

import base64
from typing import Any

import structlog
from openai import AsyncOpenAI

from skyvern.forge.sdk.models import Step
from skyvern.forge.sdk.schemas.tasks import Task

LOG = structlog.get_logger()

YUTORI_N1_DEFAULT_MODEL = "n1-latest"
YUTORI_N1_BASE_URL = "https://api.yutori.com/v1"

# N1 reasons over a sliding window of recent screenshots; sending the full history
# wastes bandwidth without improving results. Each "turn" is one user+assistant pair.
YUTORI_N1_MAX_SCREENSHOT_TURNS = 2


class YutoriN1LLMCaller:
    """Multi-turn conversation manager for the Yutori N1 computer-use model.

    N1 is OpenAI Chat Completions-compatible, returning browser actions as tool_calls.
    Coordinates are predicted in a 1000×1000 normalized space and must be denormalized
    to viewport pixel coordinates before use.
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

    def initialize_conversation(self, task: Task) -> None:
        self._task = task
        self._messages = []
        LOG.debug("Initialized Yutori N1 conversation", task_id=task.task_id)

    def add_screenshot(self, screenshot_bytes: bytes) -> None:
        if not screenshot_bytes:
            return
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

        user_content: list[dict[str, Any]] = []

        if not self._messages and self._task:
            task_parts = [f"Task: {self._task.navigation_goal or ''}"]
            user_content.append({"type": "text", "text": "\n".join(task_parts)})

        user_content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{screenshot_b64}",
                "detail": "high",
            },
        })

        self._messages.append({"role": "user", "content": user_content})
        LOG.debug("Added screenshot to Yutori N1 conversation", total_messages=len(self._messages))

    def _build_trimmed_messages(self) -> list[dict[str, Any]]:
        """Return conversation trimmed to the task message + last N screenshot turns."""
        if not self._messages:
            return []

        # First message always contains the task description — keep it.
        task_message = self._messages[0]

        # Remaining messages are interleaved user(screenshot)/assistant(tool_calls) pairs.
        rest = self._messages[1:]

        # Keep only the last YUTORI_N1_MAX_SCREENSHOT_TURNS * 2 messages (user + assistant each).
        max_tail = YUTORI_N1_MAX_SCREENSHOT_TURNS * 2
        trimmed_tail = rest[-max_tail:] if len(rest) > max_tail else rest

        return [task_message] + trimmed_tail

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

        request_id = getattr(response, "request_id", None) or (
            response.model_extra.get("request_id") if response.model_extra else None
        )
        LOG.info(
            "Yutori N1 response received",
            step_order=step.order,
            request_id=request_id,
            finish_reason=response.choices[0].finish_reason,
        )

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
        self._messages.append(msg)
