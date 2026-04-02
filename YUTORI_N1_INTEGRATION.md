# Yutori N1 × Skyvern Integration

This document describes the changes made to integrate Yutori's N1 computer-use model into Skyvern's open-source browser automation framework, and the issues discovered during local testing.

---

## What Was Added

### Backend / Model Integration

These are the files that matter for the modeling team.

**New files:**

- `skyvern/forge/sdk/api/llm/yutori_n1_llm_caller.py`
  Multi-turn conversation manager. Each Skyvern step: takes a screenshot, appends it as a `user` message with the task description, calls `POST /v1/chat/completions`, appends the assistant reply, and repeats. Uses the OpenAI-compatible SDK pointed at `https://api.yutori.com/v1`.

- `skyvern/forge/sdk/api/llm/yutori_n1_response.py`
  Converts N1's `tool_calls` response into Skyvern action objects. N1 returns actions as function calls (e.g. `left_click`, `type`, `scroll`, `go_back`, `stop`). This file maps each to the Skyvern equivalent. When N1 returns no tool calls (finish), it's treated as task completion.

- `skyvern/forge/prompts/skyvern/yutori-n1-system-prompt.j2`
  Currently empty — N1 is trained to respond to task instructions in the user message, not a system prompt.

**Modified files (backend):**

- `skyvern/forge/agent.py`
  Wires up the N1 engine: initializes `YutoriN1LLMCaller`, routes `RunEngine.yutori_n1` steps to `_generate_yutori_n1_actions()`, and adds a fallback in `get_extracted_information_for_task` so that N1's `stop` call content surfaces as `extracted_information` in the task result.

- `skyvern/webeye/actions/action_types.py`
  Added `GO_BACK = "go_back"` to `ActionType`.

- `skyvern/webeye/actions/actions.py`
  Added `GoBackAction` class and its parser entry.

- `skyvern/webeye/actions/handler.py`
  Added `handle_go_back_action` which calls `page.go_back()`, and registered it with `ActionHandler`.

- `skyvern/forge/sdk/db/utils.py`
  Added `GoBackAction` to the `ACTION_TYPE_TO_CLASS` map so the DB layer can persist it.

- `skyvern/forge/sdk/routes/agent_protocol.py`
  Added `yutori-n1` as a recognized engine in `/v1/run/tasks`.

- `skyvern/services/task_v1_service.py`, `skyvern/services/run_service.py`
  Minor plumbing to pass the engine through task creation.

### Frontend

These changes are cosmetic / for local testing convenience and are not part of the model integration:

- `skyvern-frontend/src/api/types.ts` — Added `YutoriN1 = "yutori-n1"` to `RunEngine` enum.
- `skyvern-frontend/src/components/EngineSelector.tsx` — Added N1 as a selectable option in the UI.
- `skyvern-frontend/src/routes/tasks/create/CreateNewTaskForm.tsx` — Changed default engine to N1 for local testing.
- `skyvern-frontend/src/util/taskRunPayload.ts` — Fixed a bug where `"null"` string was being appended to the navigation goal.

**Known issue:** The Run button in the frontend does not currently work for N1 tasks. Tasks must be submitted via the API directly (see Running Locally below). The button either submits with the wrong engine or does nothing — this has not been fully debugged.

---

## How N1 Is Invoked

1. User submits a task to `POST /v1/run/tasks` with `engine: "yutori-n1"`.
2. Skyvern creates a browser session and starts the agentic loop.
3. Each step: Skyvern takes a screenshot → sends it to N1 as a base64 image with the task description → N1 returns tool calls.
4. `yutori_n1_response.py` converts tool calls to Skyvern actions, which are executed by Playwright.
5. When N1 calls `stop` (or returns no tool calls), Skyvern marks the task complete and surfaces the `stop` message as `extracted_information`.

N1 coordinates are in a **1000×1000 normalized space** and are denormalized to actual viewport pixels before execution.

---

## Issues Found During Testing

### Issue 1: N1's address-bar navigation doesn't work in Skyvern

**What N1 does:** To navigate to a URL, N1 clicks the address bar (around y=38 in its 1000-unit space), types the URL, and presses Enter — the standard browser interaction.

**What Skyvern does:** Skyvern's Playwright page only controls page content. The browser chrome (address bar, tabs) is not accessible via page-level actions. So N1's click lands on the page body, the typed URL goes nowhere, and Enter submits whatever form is focused.

**Result:** N1 gets stuck trying to navigate, burning steps.

**N1 has a `goto_url` action type** which we correctly map to Skyvern's `GotoUrlAction` (calls `page.goto(url)` directly). The problem is N1 sometimes chooses the manual address-bar approach instead. **Question for the N1 team:** Is there a way to encourage N1 to prefer `goto_url` over the manual address-bar sequence when it needs to navigate? Or should Skyvern expose a way for N1 to do address-bar navigation?

### Issue 2: `go_back` was missing from Skyvern

N1 calls `go_back` naturally (e.g., after visiting an external link, to return to the referrer). Skyvern had no equivalent action — the original workaround was `KeypressAction(["Alt+ArrowLeft"])` which doesn't work because keyboard shortcuts for browser navigation require the browser chrome to have focus.

**Fix included in this PR:** Added `GoBackAction` backed by Playwright's `page.go_back()`. This works correctly and is a clean addition to Skyvern's action set.

### Issue 3: Skyvern sends annotated screenshots; N1 expects raw

Skyvern overlays its own element-detection annotations (bounding boxes, labels) on screenshots before sending them to the model. N1 was trained on raw browser screenshots. The effect on coordinate accuracy is unknown but worth investigating — especially whether N1's clicks land in the right place when the screenshot has Skyvern's visual overlay. Should verify with the Skyvern team whether raw screenshots can be requested for CUA engines.

**Question for the N1 team:** Has N1 been tested with annotated screenshots? Does it handle them gracefully or should Skyvern send raw screenshots when using N1?

### Issue 4: Coordinate space — full browser window vs. page content

N1 predicts coordinates over the full browser window including chrome (address bar ≈ top 38/1000 units). Skyvern's viewport may be scoped to page content only. This could cause systematic offset errors for actions near the top of the screen.

**Question for Skyvern / N1 teams:** What viewport dimensions and origin does Skyvern use when taking screenshots? Is the browser chrome included?

---

## Running Locally

```bash
export ENABLE_YUTORI_N1=true
export YUTORI_N1_API_KEY=<your-key>
export LLM_KEY=ANTHROPIC_CLAUDE3.7_SONNET   # used for non-N1 Skyvern steps
export DATABASE_STRING=postgresql+psycopg://...
export ANTHROPIC_API_KEY=<your-key>

python -m uvicorn skyvern.forge.api_app:create_api_app \
  --host 0.0.0.0 --port 8000 --factory \
  --app-dir /path/to/skyvern
```

Submit a task (use the API directly — the frontend Run button is not working):

```bash
curl -X POST http://localhost:8000/v1/run/tasks \
  -H "Content-Type: application/json" \
  -H "x-api-key: <token>" \
  -d '{
    "prompt": "Your task here",
    "engine": "yutori-n1",
    "url": "https://example.com"
  }'
```

View task at `http://localhost:8080/runs/<task_id>`.
