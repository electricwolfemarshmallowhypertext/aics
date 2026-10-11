import json
from pathlib import Path
import time
from datetime import datetime

from config import CODEX_HOME, OUTPUT_ENVELOPE_TOKENS


def bind_rollout(session_id, cwd, timeout=30, codex_home=None):
    """Bind only an exact emitted session ID, confirmed by its disk metadata."""
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("An exact CLI session ID is required")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidates = Path(codex_home or CODEX_HOME, "sessions").rglob(f"rollout-*{session_id}.jsonl")
        matches = []
        for path in candidates:
            try:
                with open(path, encoding="utf-8") as rollout:
                    first = json.loads(rollout.readline())
            except (json.JSONDecodeError, FileNotFoundError):
                continue
            metadata = first.get("payload") or {}
            if (
                first.get("type") == "session_meta"
                and metadata.get("id") == session_id
                and Path(metadata.get("cwd", "")).resolve() == Path(cwd).resolve()
            ):
                matches.append(path)
        if len(matches) > 1:
            raise RuntimeError(f"Ambiguous rollout for session {session_id}")
        if matches:
            return matches[0]
        time.sleep(0.5)
    raise RuntimeError("spawned session never produced a rollout — is codex running?")


def parse_token_count(line):
    """Return Codex token-count data or None for every other rollout event."""
    event = json.loads(line)
    payload = event.get("payload") or {}
    if payload.get("type") != "token_count":
        return None
    info = payload.get("info") or {}
    window = info.get("model_context_window")
    occupancy = (info.get("last_token_usage") or {}).get("total_tokens")
    cumulative = (info.get("total_token_usage") or {}).get("total_tokens")
    if occupancy is None or not window:
        return None
    return {"occupancy": occupancy, "cumulative": cumulative, "window": window}


def read_token_count(rollout_path):
    """Measure event snapshots plus a conservative byte bound on later text outputs."""
    latest = None
    previous_occupancy = None
    max_growth = 0
    compactions = 0
    outputs = 0
    pending_bound = 0
    peak_pending_bound = 0
    max_outputs = 0
    with open(rollout_path, encoding="utf-8") as rollout:
        lines = rollout.readlines()
        for index, line in enumerate(lines):
            try:
                event = json.loads(line)
                parsed = parse_token_count(line)
            except json.JSONDecodeError:
                if index == len(lines) - 1 and not line.endswith("\n"):
                    continue  # A concurrent writer may not have flushed its final line.
                raise ValueError(f"Corrupt completed rollout record at line {index + 1}")
            if parsed:
                if previous_occupancy is not None:
                    max_growth = max(max_growth, parsed["occupancy"] - previous_occupancy)
                previous_occupancy = parsed["occupancy"]
                latest = parsed
                latest["snapshot_timestamp"] = event.get("timestamp")
                pending_bound, outputs = 0, 0
            payload = event.get("payload") or {}
            if event.get("type") == "compacted" or payload.get("type") == "context_compacted":
                compactions += 1
            if event.get("type") == "response_item" and payload.get("type") in {
                "function_call_output", "custom_tool_call_output", "tool_call_output"
            }:
                output = payload.get("output", "")
                if not isinstance(output, str):
                    output = json.dumps(output, ensure_ascii=False)
                # UTF-8 byte count is deliberately conservative for text tokens.
                pending_bound += len(output.encode("utf-8")) + OUTPUT_ENVELOPE_TOKENS
                outputs += 1
                peak_pending_bound = max(peak_pending_bound, pending_bound)
                max_outputs = max(max_outputs, outputs)
    if latest:
        stamp = latest["snapshot_timestamp"]
        latest["snapshot_age_seconds"] = None if stamp is None else max(
            0, time.time() - datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())
        latest.update(pending_output_token_upper_bound=pending_bound,
                      tool_outputs_after_snapshot=outputs,
                      peak_pending_output_token_upper_bound=peak_pending_bound,
                      max_tool_outputs_between_snapshots=max_outputs,
                      max_observed_snapshot_growth=max_growth, compaction_events=compactions)
    return latest


def read_utilization(rollout_path):
    """Last API-call tokens / window: a snapshot proxy, not a peak measurement."""
    latest = read_token_count(rollout_path)
    return latest["occupancy"] / latest["window"] if latest else None
