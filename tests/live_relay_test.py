"""Run a real 65% relay against a checkpointed, numbered audit workload."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from tailer import parse_token_count


def main():
    verify_only = len(sys.argv) > 1
    workspace = Path(sys.argv[1]).resolve() if verify_only else Path(__file__).resolve().parent / f"live-relay-run-{time.time_ns()}"
    if not verify_only:
        workspace.mkdir(exist_ok=False)
        (workspace / "checkpoints").mkdir()
    rows_per_item = len((workspace / "item-01.jsonl").read_text().splitlines()) if verify_only else 180
    expected = []
    for item in range(1, 25):
        records = []
        for row in range(1, rows_per_item + 1):
            records.append({
                "item": item, "row": row, "account": f"audit-{item:02}-{row:04}",
                "amount": row * 7, "expected": row * 7 + (1 if row % 19 == 0 else 0),
                "note": f"Reconcile numbered ledger entry {item:02}/{row:04} against its expected amount.",
            })
        data = "\n".join(json.dumps(record) for record in records) + "\n"
        if not verify_only:
            (workspace / f"item-{item:02}.jsonl").write_text(data, encoding="utf-8")
        expected.append({"item": item, "mismatches": rows_per_item // 19, "sha256": hashlib.sha256(data.encode()).hexdigest()})
    prompt = """Complete this controlled ledger audit. Work only in this directory.
There are 24 numbered item-NN.jsonl files. For each item in ascending order:
1. Read its entire contents into a shell tool response, one file per call; do not combine files.
   Request a max_output_tokens budget of 20000 for the read so it is not truncated.
2. Compute its SHA256 and count rows where amount != expected using Python.
3. Atomically save {item, mismatches, sha256} to checkpoints/item-NN.json.
   Use json.dump to a temporary file, flush it, then os.replace into the checkpoint
   path. Reread with json.load and verify the saved result before moving on.
4. Wait 3 seconds using the shell before reading the next file.
Do not bulk-process the audit. Every item must be read separately into the conversation
before its checkpoint is written. If interrupted, inspect checkpoints/ and start at
the first missing item. Never repeat a checkpoint. After all 24 items, write DONE.txt
containing AUDIT_COMPLETE. Do not modify the input files or any parent-directory files.
"""
    if not verify_only:
        (workspace / "TASK.md").write_text(prompt, encoding="utf-8")
    launcher = (
        "import sys; from pathlib import Path; "
        f"sys.path.insert(0, {str(workspace.parents[1])!r}); "
        "import receipts,relayd; receipts.RECEIPTS_PATH=Path.cwd()/'receipts.jsonl'; "
        f"relayd.run({prompt!r}, Path.cwd())"
    )
    started = 0 if verify_only else time.time()
    if not verify_only:
        with (workspace / "daemon.log").open("w", encoding="utf-8") as log:
            proc = subprocess.Popen([sys.executable, "-c", launcher], cwd=workspace, stdout=log, stderr=subprocess.STDOUT)
            print(json.dumps({"workspace": str(workspace), "daemon_pid": proc.pid, "threshold": config.THRESHOLD}), flush=True)
            try:
                code = proc.wait(timeout=900)
            except subprocess.TimeoutExpired:
                proc.terminate()
                raise RuntimeError("Live audit timed out; inspect daemon.log and child processes")
        assert code == 0, f"Daemon failed ({code}); inspect {workspace / 'daemon.log'}"
    records = [json.loads(line) for line in (workspace / "receipts.jsonl").read_text().splitlines()]
    assert records, "No real relay occurred"
    for record in records:
        assert record["utilization"] >= 0.65
        if "occupancy" in record:
            assert record["utilization"] == record["occupancy"] / record["window"]
            assert record["outcome"] == "handoff_verified"
            assert not record["verification_errors"]
        path = Path(record["session_rollout"])
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        session_id = events[0]["payload"]["id"]
        assert any(parse_token_count(json.dumps(event)) for event in events), "No real token event"
        print(json.dumps({"trigger_session": session_id, **record}), flush=True)
    if (workspace / "checkpoints").exists():
        checkpoints = sorted((workspace / "checkpoints").glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
        progress = [{key: record[key] for key in ("item", "mismatches", "sha256")}
                    for path in checkpoints for record in [json.loads(path.read_text())]]
    else:
        progress = [json.loads(line) for line in (workspace / "progress.jsonl").read_text().splitlines()]
    continuity_passed = progress == expected
    assert (workspace / "DONE.txt").read_text().strip() == "AUDIT_COMPLETE"
    handoff = (workspace / "HANDOFF.md").read_text()
    headings = ("## Goal", "## Ordered queue of remaining items", "## Constraints") if "occupancy" in records[0] else ("## Goal", "## Progress", "## Decisions made", "## Constraints", "## Next steps")
    for heading in headings:
        assert heading in handoff, f"Missing handoff heading {heading}"
    sessions = []
    for path in (config.CODEX_HOME / "sessions").rglob("rollout-*.jsonl"):
        if path.stat().st_mtime < started:
            continue
        with path.open() as file:
            meta = json.loads(file.readline()).get("payload", {})
        if Path(meta.get("cwd", "")).resolve() == workspace:
            sessions.append({"id": meta["id"], "path": str(path)})
    assert len({session["id"] for session in sessions}) >= 3, "Expected original, summarizer, fresh session"
    result = {
        "passed": continuity_passed, "real_relays": len(records),
        "completed_items": len(progress),
        "checkpoint_item_order": [record["item"] for record in progress],
        "all_results_correct_when_sorted": sorted(progress, key=lambda record: record["item"]) == expected,
        "sessions": sessions,
    }
    (workspace / "verification.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
    assert continuity_passed, "Lost, repeated, out-of-order, or incorrect audit checkpoints; evidence saved in verification.json"


if __name__ == "__main__":
    main()
