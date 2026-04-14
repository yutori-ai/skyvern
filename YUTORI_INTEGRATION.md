# Yutori Navigator x Skyvern Integration

Integration of [Yutori's Navigator](https://docs.yutori.com/reference/browser-use) computer-use model into Skyvern's browser automation framework, using the [Yutori Python SDK](https://github.com/yutori-ai/yutori-sdk-python).

---

## Architecture

Navigator is wired into Skyvern as a CUA engine (`engine: "yutori-navigator"`) following the same architecture pattern as the existing UI-TARS integration:

- **`YutoriNavigatorLLMCaller`** extends `LLMCaller` for artifact persistence, cost/token tracking, and error handling
- API calls route through `_dispatch_llm_call()` -> `_call_yutori_navigator()` in `api_handler_factory.py`
- Config registered in `config_registry.py` as `YUTORI_NAVIGATOR`
- Client initialized at startup in `forge_app.py` via `AsyncYutoriClient`

### Files added/modified

| File | Change |
|------|--------|
| `skyvern/forge/sdk/api/llm/yutori_navigator_llm_caller.py` | **New** - Multi-turn conversation manager, extends `LLMCaller` |
| `skyvern/forge/sdk/api/llm/yutori_navigator_response.py` | **New** - Converts Navigator tool_calls into Skyvern actions |
| `skyvern/forge/agent.py` | Agent loop integration, ref resolution, stop-and-summarize |
| `skyvern/forge/forge_app.py` | `YUTORI_CLIENT` initialization at startup |
| `skyvern/forge/sdk/api/llm/api_handler_factory.py` | `_call_yutori_navigator()` routing + payload trimming |
| `skyvern/forge/sdk/api/llm/config_registry.py` | `YUTORI_NAVIGATOR` LLM config registration |
| `skyvern/config.py` | `ENABLE_YUTORI`, `YUTORI_API_KEY`, `YUTORI_MODEL`, etc. |
| `skyvern/webeye/actions/action_types.py` | New: `GO_FORWARD`, `EXECUTE_JS` |
| `skyvern/webeye/actions/actions.py` | New: `GoForwardAction`, `ExecuteJsAction` |
| `skyvern/webeye/actions/handler.py` | Handlers: `go_forward`, `reload_page`, `execute_js` |
| `skyvern/schemas/runs.py` | `RunType.yutori_navigator`, `RunEngine.yutori_navigator` |
| Engine plumbing | `run_service.py`, `task_v1_service.py`, `agent_protocol.py`, `background_task_executor.py` |
| Frontend | `types.ts`, `EngineSelector.tsx` (engine option) |
| Docs | `fern/running-tasks/run-tasks.mdx`, OpenAPI schemas, SDK docs |

### What the Yutori SDK handles

| SDK utility | What it does |
|------------|-------------|
| `AsyncYutoriClient` | API client with `tool_set`, `disable_tools`, `json_schema` support |
| `screenshot_to_data_url()` | Resizes screenshots to 1280x800, converts to optimized WebP |
| `denormalize_coordinates()` | 1000x1000 normalized space to viewport pixels with clamping |
| `map_key_to_playwright()` | Navigator lowercase keys (e.g. `ctrl+c`, `down down enter`) to Playwright format |
| `map_keys_individual()` | Individual key mapping for `hold_key` (keyboard.down/up) |
| `trimmed_messages_to_fit()` | Trims old screenshots when payload exceeds ~9.5MB |
| `format_stop_and_summarize()` | Generates stop-and-summarize prompt for max-steps |
| `evaluate_tool_script()` | Executes bundled JS scripts for expanded tool set |
| `GET_ELEMENT_BY_REF_SCRIPT` | Resolves element refs to viewport coordinates |
| `EXTRACT_ELEMENTS_SCRIPT`, `FIND_SCRIPT`, `SET_ELEMENT_VALUE_SCRIPT`, `EXECUTE_JS_SCRIPT` | Bundled JS for expanded DOM tool actions |

---

## How Navigator Is Invoked

1. User submits a task to `POST /v1/run/tasks` with `engine: "yutori-navigator"`.
2. Skyvern creates a browser session and starts the agentic loop.
3. Each step:
   - Screenshot is taken and encoded via `screenshot_to_data_url()` (1280x800 WebP)
   - Previous step's action results are flushed as tool responses (with actual execution data)
   - Navigator API is called via `_call_yutori_navigator()` through the `LLMCaller` base class
   - Response is parsed into Skyvern actions
4. **Browser actions** (click, scroll, type, etc.) are converted to Skyvern actions and executed by Playwright. Action results (`ActionSuccess.data`) are stored and fed back as tool responses on the next step.
5. **Expanded DOM tools** (extract_elements, find, set_element_value, execute_js) are converted to `ExecuteJsAction` and executed via `page.evaluate()` through Skyvern's action handler. Results (JS output, DOM trees, error messages) are fed back to Navigator on the next step.
6. **Ref-based targeting**: Navigator can reference elements by ref ID instead of coordinates. Refs are resolved inline via `get_element_by_ref.js` before action parsing, converting to coordinates.
7. **Stop-and-summarize**: On the last step (max steps reached), a stop-and-summarize message is sent instead of a normal tool response, prompting Navigator to produce a progress summary.
8. When Navigator returns no tool_calls (`finish_reason: stop`), task is marked complete.

### Conversation format

Per [Navigator docs](https://docs.yutori.com/reference/browser-use):

```
user (task text + screenshot)
  -> assistant (tool_calls)
  -> tool (action result + Current URL + screenshot)
  -> assistant (tool_calls)
  -> tool (action result + Current URL + screenshot)
  -> ...
```

Tool responses include:
- **Actual execution results**: JS output for `ExecuteJsAction`, `"OK"` for successful browser actions, `"ERROR: ..."` for failures
- **Current page URL**
- **Screenshot** (on the last tool response, as an image_url content block)

No system prompt is sent — Navigator handles task instructions from the user message.

### Tool sets

| Tool set | ID | Contents |
|----------|----|----|
| **Core** (default) | `browser_tools_core-20260403` | 18 coordinate-based visual tools |
| **Expanded** | `browser_tools_expanded-20260403` | Core + DOM tools (extract_elements, find, set_element_value, execute_js) |

Set via `YUTORI_TOOL_SET` in `.env`.

---

## Action Mapping

### Browser actions

| Navigator action | Skyvern action | Result description |
|-----------------|----------------|-------------------|
| `left_click` | `ClickAction(button="left", repeat=1)` | `"Clicked 1x with left"` |
| `double_click` | `ClickAction(button="left", repeat=2)` | `"Clicked 2x with left"` |
| `right_click` | `ClickAction(button="right", repeat=1)` | `"Clicked 1x with right"` |
| `triple_click` | `ClickAction(button="left", repeat=3)` | `"Clicked 3x with left"` |
| `middle_click` | `ClickAction(button="middle", repeat=1)` | `"Clicked 1x with middle"` |
| `mouse_move` | `MoveAction` | `"Mouse moved and hovering"` |
| `mouse_down` / `mouse_up` | `LeftMouseAction(direction=...)` | `"Mouse button pressed"` / `"released"` |
| `drag` | `DragAction(start, end)` | `"Dragged successfully"` |
| `type` | `InputTextAction` | `"Typed N characters"` |
| `key_press` | `KeypressAction` | `"Pressed key: ctrl+c"` |
| `hold_key` | `KeypressAction(hold=True)` | `"Held key: shift"` |
| `scroll` | `ScrollAction` | `"Scrolled down"` |
| `go_back` | `GoBackAction` | `"Navigated back"` |
| `go_forward` | `GoForwardAction` | `"Navigated forward"` |
| `refresh` | `ReloadPageAction` | `"Refreshed the page"` |
| `goto_url` | `GotoUrlAction` | `"Navigated to https://..."` |
| `stop` | `CompleteAction` | Task completion summary |
| `wait` | `WaitAction` | `"Waited 5s"` |

### Expanded DOM tools

| Navigator action | Skyvern action | JS source |
|-----------------|----------------|-----------|
| `extract_elements` | `ExecuteJsAction` | SDK `EXTRACT_ELEMENTS_SCRIPT` |
| `find` | `ExecuteJsAction` | SDK `FIND_SCRIPT` |
| `set_element_value` | `ExecuteJsAction` | SDK `SET_ELEMENT_VALUE_SCRIPT` |
| `execute_js` | `ExecuteJsAction` | SDK `EXECUTE_JS_SCRIPT` (wraps user code in try/catch) |

All expanded tools go through Skyvern's `ExecuteJsAction` handler, which calls `page.evaluate()` and returns results via `ActionSuccess(data=...)`. Results are fed back to Navigator as tool response content on the next step.

---

## New Skyvern Action Types

This integration adds the following to Skyvern's action system:

| Action | Type | Handler | Use case |
|--------|------|---------|----------|
| `GoForwardAction` | `GO_FORWARD` | `page.go_forward()` | Browser forward navigation |
| `ExecuteJsAction` | `EXECUTE_JS` | `page.evaluate(js_code)` | Generic JS execution for expanded tools |
| `ReloadPageAction` handler | `RELOAD_PAGE` | `page.reload()` | Handler was missing for existing type |

---

## Running Locally

### Environment variables

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

### Setup

```bash
uv venv .venv --python 3.12
source .venv/bin/activate
uv sync
playwright install chromium
```

### Run

```bash
skyvern run server                                  # API on :8000
cd skyvern-frontend && npm install && npm run dev   # UI on :8080
node skyvern-frontend/artifactServer.js             # Screenshots on :9090
```

### Submit a task

```bash
curl -X POST http://localhost:8000/v1/run/tasks \
  -H "Content-Type: application/json" \
  -H "x-api-key: <token>" \
  -d '{"prompt": "Your task here", "engine": "yutori-navigator", "url": "https://example.com"}'
```

### Check status

```bash
curl http://localhost:8000/v1/runs/<run_id> -H "x-api-key: <token>"
```
