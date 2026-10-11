import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

import config
import receipts
from handoff import boot_prompt, build_handoff, atomic_write
from handoff_check import verify, verification_scope
from process_control import spawn_owned, stop_owned
from quality_probe import QualityProbe
from session_identity import SessionIdentity
from tailer import bind_rollout, read_token_count


def spawn(prompt, cwd):
    logs = Path(cwd, ".relay", "sessions")
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"{uuid.uuid4()}.jsonl"
    with log_path.with_suffix(".stderr.log").open("w", encoding="utf-8") as stderr:
        proc = spawn_owned(
            [config.CODEX_EXECUTABLE, "exec", "--json", "--model", config.MODEL,
             "-c", f"tool_output_token_limit={config.TOOL_OUTPUT_TOKEN_LIMIT}",
             "--sandbox", "workspace-write", prompt],
            cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=stderr, text=True, encoding="utf-8",
        )
    try:
        proc._relay_identity = SessionIdentity(proc, log_path)
        return proc
    except BaseException:
        stop_owned(proc)
        raise


def kill(proc):
    return stop_owned(proc)


def run(goal, cwd, quality_command=None):
    cwd = Path(cwd)
    atomic_write(cwd / ".relay" / "goal.txt", goal)
    prompt = goal
    probe = QualityProbe(quality_command, cwd) if quality_command else None
    owned = []
    try:
        while True:
            proc = spawn(prompt, cwd)
            owned.append(proc)
            session_id = proc._relay_identity.get()
            rollout = bind_rollout(session_id, cwd)
            fired = False
            while proc.poll() is None or probe is not None:
                finished = proc.poll() is not None
                if finished and proc.returncode:
                    break
                if not finished:
                    time.sleep(config.POLL_INTERVAL)
                usage = read_token_count(rollout)
                quality = probe.poll(session_id, usage) if probe and usage else None
                quality_failed = quality is not None and quality['status'] == 'fail'
                occupancy_fired = usage is not None and usage['occupancy'] / usage['window'] >= config.THRESHOLD
                if not quality_failed and (finished or not occupancy_fired):
                    if finished:
                        break
                    continue
                event = {"session_id":session_id,"session_rollout":str(rollout),
                         **usage,"utilization":usage["occupancy"]/usage["window"],
                         "verification_scope":verification_scope(cwd),
                         "trigger_reason":"quality" if quality_failed else "occupancy"}
                if quality_failed:
                    event["quality_probe"] = quality
                try:
                    kill(proc)
                    for attempt in range(1, 3):
                        handoff_path = build_handoff(rollout, cwd, goal)
                        errors = verify(handoff_path, cwd)
                        if not errors:
                            break
                    if errors:
                        raise RuntimeError("Refusing unverified relay after two summaries: " + "; ".join(errors))
                    if (cwd / ".relay" / "goal.txt").read_text(encoding="utf-8") != goal:
                        raise RuntimeError("Original goal file was altered")
                    saved_handoff = cwd / ".relay" / "handoffs" / f"{session_id}.md"
                    atomic_write(saved_handoff, handoff_path.read_text(encoding="utf-8"))
                except Exception as error:
                    receipts.log({**event,"outcome":"handoff_failed","error":str(error)})
                    raise
                receipts.log({**event,"handoff":str(saved_handoff),"verification_attempts":attempt,
                              "verification_errors":[],"outcome":"handoff_verified"})
                prompt = boot_prompt()
                if quality_failed:
                    prompt += " The task verifier rejected a saved result. Fix that result before continuing: " + json.dumps(quality)
                fired = True
                break
            if not fired:
                if proc.returncode:
                    raise RuntimeError(f"Codex exited {proc.returncode}")
                return
    finally:
        for proc in owned:
            stop_owned(proc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Relay Codex at occupancy or a verified task failure.")
    parser.add_argument("goal")
    parser.add_argument("--quality-check", type=Path, help="Trusted read-only Python task verifier returning JSON")
    args = parser.parse_args()
    command = [sys.executable, str(args.quality_check.resolve())] if args.quality_check else None
    run(args.goal, Path.cwd(), quality_command=command)
