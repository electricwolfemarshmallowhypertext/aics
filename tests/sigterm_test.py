"""Step 1: terminate a live CLI session, validate its rollout, resume its ID."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relayd import spawn, kill
from tailer import bind_rollout
from config import CODEX_EXECUTABLE, MODEL

LONG_TASK = (
    "Write a 2000 line design document for a distributed cache. Do not stop early. "
    "Reply directly; do not use tools or write files."
)


def main():
    cwd = Path(__file__).resolve().parents[1]
    codex = shutil.which(CODEX_EXECUTABLE)
    assert codex, "FAIL: codex executable missing"
    with tempfile.TemporaryDirectory(prefix="aics-termination-") as temp:
        temp = Path(temp)
        with (temp / "session.log").open("w", encoding="utf-8") as log:
            proc = spawn(LONG_TASK, cwd)
            session_id = proc._relay_identity.get()
            try:
                time.sleep(20)
                assert proc.poll() is None, (
                    "FAIL: session exited before termination: "
                    + (temp / "session.log").read_text(encoding="utf-8")[-2000:]
                )
            finally:
                if proc.poll() is None:
                    kill(proc)
        rollout = bind_rollout(session_id, cwd)
        first = json.loads(rollout.open(encoding="utf-8").readline())
        session_id = first["payload"]["id"]
        bad_lines, events = [], []
        for number, line in enumerate(rollout.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                bad_lines.append(number)
        payload_types = [e.get("payload", {}).get("type") for e in events if e.get("type") == "event_msg"]
        assert "task_started" in payload_types, "FAIL: no active turn recorded"
        assert "task_complete" not in payload_types, "FAIL: turn completed before termination"
        result = {
            "platform": os.name,
            "termination": "CTRL_BREAK_EVENT" if os.name == "nt" else "SIGTERM",
            "session_id": session_id,
            "rollout": str(rollout),
            "bytes_after_termination": rollout.stat().st_size,
            "bad_lines": bad_lines,
            "terminated_mid_turn": True,
        }
        print(json.dumps(result), flush=True)
        assert not bad_lines, "FAIL: torn JSONL; stop before L3"
        out = temp / "resume.txt"
        probe = subprocess.run(
            [codex, "exec", "resume", "--ignore-user-config", "--model", MODEL,
             session_id, "Reply with exactly: RESUMED. Do not use tools.",
             "--output-last-message", str(out)],
            cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        response = out.read_text(encoding="utf-8").strip() if out.exists() else ""
        result.update(resume_exit_code=probe.returncode, resume_response=response)
        print(json.dumps(result), flush=True)
        assert probe.returncode == 0 and response in {"RESUMED", "RESUMED."}, (
            "FAIL: killed session did not resume: " + probe.stderr[-2000:]
        )
        print(f"PASS: clean rollout and exact-session resume after {result['termination']}.")


if __name__ == "__main__":
    main()

