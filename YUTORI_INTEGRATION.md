# Yutori Navigator x Skyvern Integration

Integration of Yutori's Navigator computer-use model into Skyvern's browser automation framework, using the [Yutori Python SDK](https://github.com/yutori-ai/yutori-sdk-python).

---

## Architecture

Navigator is wired into Skyvern as a CUA engine (`engine: "yutori-navigator.5"`) alongside `openai-cua`, `anthropic-cua`, and `ui-tars`. The integration uses the Yutori SDK for API calls, key mapping, coordinate conversion, screenshot encoding, payload management, and expanded tool execution.

### Key files

| File | Purpose |
|------|---------|
| `skyvern/forge/sdk/api/llm/yutori_navigator_llm_caller.py` | Multi-turn conversation manager using `AsyncYutoriClient` |
| `skyvern/forge/sdk/api/llm/yutori_navigator_response.py` | Converts Navigator tool_calls into Skyvern action objects |
| `skyvern/forge/agent.py` | Agent loop integration, expanded tool execution |
| `skyvern/config.py` | Config: API key, model, tool set |

### What the SDK handles

- **Client**: `AsyncYutoriClient` with `tool_set`, `disable_tools`, `json_schema` support
- **Screenshots**: `screenshot_to_data_url()` -- resizes to 1280x800, converts to optimized WebP
- **Coordinates**: `denormalize_coordinates()` -- 1000x1000 normalized space to viewport pixels with clamping
- **Key mapping**: `map_key_to_playwright()` -- Navigator lowercase keys (e.g. `ctrl+c`, `down down enter`) to Playwright format
- **Payload management**: `trimmed_messages_to_fit()` -- trims old screenshots when payload exceeds size limit
- **Expanded tools**: `evaluate_tool_script()` with bundled JS scripts for DOM operations

---

## How Navigator Is Invoked

1. User submits a task to `POST /v1/run/tasks` with `engine: "yutori-navigator.5"`.
2. Skyvern creates a browser session and starts the agentic loop.
3. Each step: screenshot -> `screenshot_to_data_url()` -> Navigator API call -> tool_calls response.
4. **Browser actions** (click, scroll, type, etc.) -> converted to Skyvern actions and executed by Playwright.
5. **Expanded DOM tools** (extract_elements, find, set_element_value, execute_js) -> executed inline via `evaluate_tool_script()`, results fed back to Navigator, which may issue more tool calls before returning a browser action.
6. When Navigator returns no tool_calls (`finish_reason: stop`), task is marked complete.

### Conversation format

Per [Navigator docs](https://docs.yutori.com/reference/browser-use):

```
user (task text + screenshot)
  -> assistant (tool_calls)
  -> tool (Current URL + screenshot)
  -> assistant (tool_calls)
  -> tool (Current URL + screenshot)
  -> ...
```

No system prompt is sent.

### Tool sets

- **Core** (`browser_tools_core-20260403`): 18 coordinate-based visual tools (default)
- **Expanded** (`browser_tools_expanded-20260403`): Core + DOM tools (extract_elements, find, set_element_value, execute_js)

Set via `YUTORI_TOOL_SET` in `.env`.

---

## Action Mapping

| Navigator action | Skyvern action | Notes |
|-------------|----------------|-------|
| `left_click`, `double_click`, `right_click`, `triple_click`, `middle_click` | `ClickAction` | button + repeat mapped correctly |
| `mouse_move` | `MoveAction` | |
| `mouse_down` / `mouse_up` | `LeftMouseAction` | direction="down"/"up" |
| `drag` | `DragAction` | start + end coordinates |
| `type` | `InputTextAction` | |
| `key_press` | `KeypressAction` | SDK `map_key_to_playwright()` handles lowercase -> Playwright |
| `hold_key` | `KeypressAction` | `hold=True`, SDK `map_keys_individual()` |
| `scroll` | `ScrollAction` | amount * 100 px |
| `go_back` | `GoBackAction` | `page.go_back()` |
| `go_forward` | `GoForwardAction` | `page.go_forward()` (new) |
| `refresh` | `ReloadPageAction` | `page.reload()` (new handler) |
| `goto_url` | `GotoUrlAction` | |
| `stop` | `CompleteAction` | |
| `wait` | `WaitAction` | |
| `extract_elements` | Inline via SDK JS | Returns DOM tree text to Navigator |
| `find` | Inline via SDK JS | Text search in DOM |
| `set_element_value` | Inline via SDK JS | Sets form input value |
| `execute_js` | Inline via `page.evaluate()` | Direct JS execution |

---

## Debugging

### Navigator request logs

- **Log file**: `/tmp/yutori_skyvern.log` -- `task_id`, `step_order`, `request_id`, `finish_reason`, `tool_calls` per API call

### Evaluation

`evaluation/run_n1_eval.py` submits tasks from the WebVoyager CUA benchmark against Navigator and polls for results.

---

## Running Locally

```bash
# .env in repo root:
ENABLE_YUTORI=true
YUTORI_API_KEY=<your-key>
ENABLE_ANTHROPIC=true
ANTHROPIC_API_KEY=<your-key>
LLM_KEY=ANTHROPIC_CLAUDE3.7_SONNET
BROWSER_WIDTH=1280
BROWSER_HEIGHT=800
# Optional: use expanded tool set
# YUTORI_TOOL_SET=browser_tools_expanded-20260403
```

```bash
# Setup
uv venv .venv --python 3.12
source .venv/bin/activate
uv sync
playwright install chromium

# Run
skyvern run server          # API on :8000
cd skyvern-frontend && npm install && npm run dev  # UI on :8080
node skyvern-frontend/artifactServer.js            # Screenshots on :9090
```

Submit a task:

```bash
curl -X POST http://localhost:8000/v1/run/tasks \
  -H "Content-Type: application/json" \
  -H "x-api-key: <token>" \
  -d '{"prompt": "Your task here", "engine": "yutori-navigator.5", "url": "https://example.com"}'
```

Check status: `curl http://localhost:8000/v1/runs/<run_id> -H "x-api-key: <token>"`
