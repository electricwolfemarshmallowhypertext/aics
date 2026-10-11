"""Summarize a bounded rollout tail, preserving the original goal and disk references."""
import os
from pathlib import Path
import subprocess
import uuid

import config
from handoff_check import checkpoint_state, verification_scope
from process_control import spawn_owned, stop_owned
import json


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    draft = path.with_name(f".{path.name}.{uuid.uuid4()}.tmp")
    try:
        with draft.open("x",encoding="utf-8",newline="\n") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(draft,path)
    finally:
        draft.unlink(missing_ok=True)


def build_handoff(rollout_path, cwd, goal=None, output_dir=None, timeout=600):
    root = Path(cwd)
    task_path = root/"TASK.md"
    task = task_path.read_text(encoding="utf-8") if task_path.exists() else (goal or "")
    disk_state = checkpoint_state(root) if verification_scope(root) != "handoff_format_only" else None
    prefix = f"ORIGINAL_GOAL:\n{goal or task}\nORIGINAL_TASK:\n{task}\nTRANSCRIPT_SOURCE: {rollout_path}\nDISK_STATE:\n{json.dumps(disk_state)}\nTRANSCRIPT:\n"
    remaining = config.TRANSCRIPT_TAIL_CHARS-len(prefix.encode("utf-8"))
    if remaining <= 0:
        raise ValueError("Original instructions exceed the summarizer input budget")
    tail = "\n".join(Path(rollout_path).read_text(encoding="utf-8").splitlines()[-config.TRANSCRIPT_TAIL_LINES:])
    encoded = tail.encode("utf-8")
    if len(encoded)>remaining:
        tail = encoded[-remaining:].decode("utf-8",errors="ignore").partition("\n")[2]
    output_root = Path(output_dir) if output_dir is not None else root
    output_root.mkdir(parents=True,exist_ok=True)
    draft = output_root/".handoff_draft.md"
    proc = spawn_owned([config.CODEX_EXECUTABLE,"exec","--model",config.MODEL,
                        "--sandbox","read-only","--output-last-message",str(draft),config.HANDOFF_PROMPT],
                       cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                       text=True,encoding="utf-8")
    try:
        stdout,stderr=proc.communicate(input=prefix+tail,timeout=timeout)
        if proc.returncode:
            raise RuntimeError(f"Handoff summarizer exited {proc.returncode}: {stderr[-2000:]}")
        output=output_root/"HANDOFF.md"
        atomic_write(output,draft.read_text(encoding="utf-8"))
        draft.unlink()
        return output
    finally:
        stop_owned(proc)


def boot_prompt():
    return ("Read .relay/goal.txt for the original goal, HANDOFF.md for the handoff, and TASK.md if present. "
            "Verify any result claims against files before relying on them. Disk is the source of truth. "
            "Continue unfinished work in the handoff's order. Do not repeat saved checkpoints. "
            "Preserve the original instructions, including final deliverables after the last checkpoint. "
            "Continue until the original goal and its final deliverables are complete. "
            "Do not stop at an arbitrary checkpoint or finish with only a progress report. "
            "If genuinely blocked, report the concrete blocker.")

