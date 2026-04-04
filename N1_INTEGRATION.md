# Yutori N1 x Skyvern Integration

This document describes the changes made to integrate Yutori's N1 computer-use model into Skyvern's open-source browser automation framework, and the issues discovered during local testing.

---

## What Was Added

### Backend / Model Integration

These are the files that matter for the modeling team.

**New files:**

- `skyvern/forge/sdk/api/llm/yutori_n1_llm_caller.py`
  Multi-turn conversation manager following the [N1 multi-turn format](https://docs.yutori.com/reference/n1#multi-turn-conversations). First turn sends a `user` message with the task + screenshot. Subsequent turns embed the new screenshot and current page URL into `tool`-role messages. No system prompt is sent (N1 handles this internally). Logs `task_id` and `request_id` per step to `/tmp/n1_skyvern.log`.

- `skyvern/forge/sdk/api/llm/yutori_n1_response.py`
  Converts N1's `tool_calls` response into Skyvern action objects. N1 returns actions as function calls (e.g. `left_click`, `type`, `scroll`, `go_back`, `stop`). This file maps each to the Skyvern equivalent. When N1 returns no tool calls (finish), it's treated as task completion.

- `skyvern/forge/prompts/skyvern/yutori-n1-system-prompt.j2`
  Template exists but is unused -- N1 handles system prompts internally, so we do not send one.

**Modified files (backend):**

- `skyvern/forge/agent.py`
  Wires up the N1 engine: initializes `YutoriN1LLMCaller`, routes `RunEngine.yutori_n1` steps to `_generate_yutori_n1_actions()`, passes `scraped_page.url` for tool responses, and adds a fallback in `get_extracted_information_for_task` so that N1's `stop` call content surfaces as `extracted_information` in the task result.

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

- `skyvern-frontend/src/api/types.ts` -- Added `YutoriN1 = "yutori-n1"` to `RunEngine` enum.
- `skyvern-frontend/src/components/EngineSelector.tsx` -- Added N1 as a selectable option in the UI.
- `skyvern-frontend/src/routes/tasks/create/CreateNewTaskForm.tsx` -- Changed default engine to N1 for local testing.
- `skyvern-frontend/src/util/taskRunPayload.ts` -- Fixed a bug where `"null"` string was being appended to the navigation goal.

**Known issue:** The Run button in the frontend does not currently work for N1 tasks. Tasks must be submitted via the API directly (see Running Locally below). The button either submits with the wrong engine or does nothing -- this has not been fully debugged.

---

## How N1 Is Invoked

1. User submits a task to `POST /v1/run/tasks` with `engine: "yutori-n1"`.
2. Skyvern creates a browser session and starts the agentic loop.
3. Each step: Skyvern takes a screenshot -> sends it to N1 with the current URL -> N1 returns tool calls.
4. `yutori_n1_response.py` converts tool calls to Skyvern actions, which are executed by Playwright.
5. When N1 calls `stop` (or returns no tool calls), Skyvern marks the task complete and surfaces the `stop` message as `extracted_information`.

### Conversation format

Per the [N1 multi-turn docs](https://docs.yutori.com/reference/n1#multi-turn-conversations):

```
user (task text + screenshot)
  -> assistant (tool_calls)
  -> tool (Current URL + screenshot)   # last tool_call gets the screenshot
  -> assistant (tool_calls)
  -> tool (Current URL + screenshot)
  -> ...
```

No system prompt is sent -- N1 handles system prompts internally.

N1 coordinates are in a **1000x1000 normalized space** and are denormalized to actual viewport pixels before execution. N1 expects **page content only** (no browser chrome) and a recommended viewport of **1280x800**.

---

## Issues Found and Fixed

### Issue 1: Viewport size (fixed)

N1 is trained on and expects a 1280x800 viewport. Skyvern defaults to 1920x1080.

**Fix:** Set `BROWSER_WIDTH=1280 BROWSER_HEIGHT=800` in `.env`. This is a global setting that applies at browser launch.

### Issue 2: Message history format (fixed)

The initial implementation sent messages as `user -> assistant -> user -> ...`, missing the required `tool`-role messages between assistant responses and the next turn.

**Fix:** Restructured `YutoriN1LLMCaller` to follow the N1 multi-turn format. Tool responses now include the current page URL and the new screenshot, per the N1 docs. Screenshots are no longer sent as separate `user` messages.

### Issue 3: `go_back` was missing from Skyvern (fixed)

N1 calls `go_back` naturally. Skyvern had no equivalent action.

**Fix:** Added `GoBackAction` backed by Playwright's `page.go_back()`.

### Issue 4: Task continues running after N1 signals completion (open)

During testing, the HN task had `finish_reason: stop` at step 10 but Skyvern continued running through step 24. This suggests that Skyvern's `CompleteAction` is being rejected by a verification step, causing the task to re-enter the agent loop.

### Issue 5: Annotated screenshots (needs verification)

Skyvern overlays element-detection annotations on screenshots for its own models. N1 was trained on raw screenshots. Other CUA integrations (OpenAI, Anthropic) would have the same problem, so Skyvern likely already sends raw screenshots for CUA engines. Needs verification.

---

## Non-Issues (initially flagged, since clarified)

### Address-bar navigation

N1 sometimes clicks the browser address bar to navigate. This is **not a Skyvern compatibility issue** -- it's a known N1 model artifact from old computer-use teacher models that occurs in our own harness too.

### Coordinate space includes browser chrome

N1 expects **page content only**, not the full browser window. The 1000x1000 normalized coordinate space maps to the page viewport.

---

## Debugging

### N1 request logs

- Log file: `/tmp/n1_skyvern.log` -- contains `task_id`, `step_order`, `request_id`, `finish_reason`, and `tool_calls` per N1 API call
- Use the `/n1-request-id-to-dashboard` skill to get dashboard links from request IDs
- Dashboard URL pattern: `https://dashboard.yutori.ai/n1/{date}/{request_id}/`
- S3 bucket `n1-api-logs-prod` contains `request.json` and `response.json` per request, organized as `{date}/{request_id}/`
- S3 login: `https://d-91671fa9fb.awsapps.com/start/#/console?account_id=022499044652`

### Evaluation

An evaluation script at `evaluation/run_n1_eval.py` submits tasks from the WebVoyager CUA benchmark (`evaluation/datasets/webvoyager_compute_use_tasks.jsonl`, 49 tasks) against the N1 engine and polls for results.

---

## Status

**On hold** pending n1.5 readiness. The current implementation has some confusion between n1 and n1.5 action spaces that should be resolved once n1.5 is finalized. Core plumbing is working and tested end-to-end.

**Fixed:**
- Viewport sizing (set `BROWSER_WIDTH=1280 BROWSER_HEIGHT=800` in `.env`)
- Message history format (tool-role messages with URL + screenshot per N1 multi-turn docs)
- N1 request logging now includes `task_id` for debugging

**Remaining:**
- Task continuing past N1's `finish_reason: stop` (Issue 4)
- Verify annotated vs raw screenshots for CUA engines (Issue 5)
- Validate against n1.5 action space once available

---

## Running Locally

```bash
# Create .env in the repo root:
ENABLE_YUTORI_N1=true
YUTORI_N1_API_KEY=<your-key>
ENABLE_ANTHROPIC=true
ANTHROPIC_API_KEY=<your-key>
LLM_KEY=ANTHROPIC_CLAUDE3.7_SONNET
BROWSER_WIDTH=1280
BROWSER_HEIGHT=800
```

```bash
# Install and run (SQLite, no Postgres needed):
uv sync
playwright install chromium
skyvern run server

# In another terminal, for the UI:
cd skyvern-frontend && npm install && npm run dev

# For screenshot viewing, start the artifact server:
node skyvern-frontend/artifactServer.js
```

Submit a task:

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

Check status: `curl http://localhost:8000/v1/runs/<run_id> -H "x-api-key: <token>"`

View in UI: `http://localhost:8080/runs/<run_id>`
