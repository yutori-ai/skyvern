from __future__ import annotations

import base64
from typing import Any

import structlog
from openai import AsyncOpenAI

from skyvern.forge.prompts import prompt_engine
from skyvern.forge.sdk.models import Step
from skyvern.forge.sdk.schemas.tasks import Task

LOG = structlog.get_logger()

YUTORI_N1_DEFAULT_MODEL = "n1-latest"
YUTORI_N1_BASE_URL = "https://api.yutori.com/v1"


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

    async def generate_response(self, step: Step) -> Any:
        system_prompt = self._build_system_prompt()
        messages: list[Any] = list(self._messages)
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            max_completion_tokens=4096,
        )

        self._append_assistant_message(response.choices[0].message)
        LOG.debug("Yutori N1 response received", step_order=step.order)
        return response

    def _build_system_prompt(self) -> str | None:
        if not self._task:
            return None
        try:
            return prompt_engine.load_prompt(
                "yutori-n1-system-prompt",
                navigation_goal=self._task.navigation_goal,
                data_extraction_goal=self._task.data_extraction_goal,
                navigation_payload=self._task.navigation_payload,
                error_code_mapping_str=str(self._task.error_code_mapping) if self._task.error_code_mapping else None,
            )
        except Exception:
            LOG.warning("Failed to load yutori-n1-system-prompt; proceeding without system prompt")
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
