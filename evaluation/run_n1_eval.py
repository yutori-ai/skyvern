"""Run a subset of WebVoyager CUA tasks against the Yutori Navigator engine and print results."""

import asyncio
import json
import sys
import time

import httpx

BASE_URL = "http://localhost:8000"
API_KEY = ""  # filled at runtime from DB

TASKS_FILE = "evaluation/datasets/webvoyager_compute_use_tasks.jsonl"
NUM_TASKS = 10
POLL_INTERVAL = 10  # seconds
MAX_WAIT = 300  # seconds per task


def load_tasks(path: str, n: int) -> list[dict]:
    tasks = []
    with open(path) as f:
        for line in f:
            tasks.append(json.loads(line))
            if len(tasks) >= n:
                break
    return tasks


async def get_api_key() -> str:
    """Read the local org API key from the SQLite DB."""
    import sqlite3
    from pathlib import Path

    db = Path.home() / ".skyvern" / "data.db"
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT token FROM organization_auth_tokens LIMIT 1").fetchone()
    conn.close()
    if not row:
        raise RuntimeError("No API key found in local DB")
    return row[0]


async def submit_task(client: httpx.AsyncClient, api_key: str, task: dict) -> str:
    resp = await client.post(
        f"{BASE_URL}/v1/run/tasks",
        headers={"Content-Type": "application/json", "x-api-key": api_key},
        json={
            "prompt": task["ques"],
            "engine": "yutori-navigator",
            "url": task["web"],
        },
    )
    resp.raise_for_status()
    return resp.json()["run_id"]


async def poll_until_done(client: httpx.AsyncClient, api_key: str, run_id: str) -> dict:
    start = time.time()
    while time.time() - start < MAX_WAIT:
        resp = await client.get(
            f"{BASE_URL}/v1/runs/{run_id}",
            headers={"x-api-key": api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "")
        if status in ("completed", "failed", "terminated", "timed_out", "cancelled"):
            return data
        await asyncio.sleep(POLL_INTERVAL)
    return {"status": "timeout", "run_id": run_id}


async def main():
    api_key = await get_api_key()
    tasks = load_tasks(TASKS_FILE, NUM_TASKS)
    print(f"Submitting {len(tasks)} tasks...\n")

    async with httpx.AsyncClient(timeout=30) as client:
        # Submit all tasks
        runs = []
        for task in tasks:
            run_id = await submit_task(client, api_key, task)
            print(f"  [{task['id']}] submitted -> {run_id}")
            runs.append({"task": task, "run_id": run_id})

        print(f"\nAll {len(runs)} tasks submitted. Polling for completion...\n")

        # Poll all in parallel
        async def poll_one(run):
            result = await poll_until_done(client, api_key, run["run_id"])
            return {**run, "result": result}

        results = await asyncio.gather(*[poll_one(r) for r in runs])

    # Print summary
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)
    for r in results:
        task = r["task"]
        res = r["result"]
        status = res.get("status", "unknown")
        steps = res.get("step_count", "?")
        output = (res.get("output") or "")[:120]
        print(f"\n[{task['id']}] {status} ({steps} steps)")
        print(f"  Question: {task['ques'][:100]}")
        print(f"  Output:   {output}...")
        print(f"  URL:      http://localhost:8080/runs/{r['run_id']}")

    print("\n" + "=" * 80)
    completed = sum(1 for r in results if r["result"].get("status") == "completed")
    failed = sum(1 for r in results if r["result"].get("status") == "failed")
    other = len(results) - completed - failed
    print(f"Completed: {completed}/{len(results)}  |  Failed: {failed}  |  Other: {other}")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
