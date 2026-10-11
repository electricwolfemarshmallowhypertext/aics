"""Check handoff shape and supported audit claims, without owning task execution."""
import hashlib
import json
from pathlib import Path
import re


def checkpoint_state(cwd):
    root = Path(cwd)
    inventory = {int(path.stem.split("-")[-1]):path for path in root.glob("item-*.jsonl")
                 if path.stem.split("-")[-1].isdigit()}
    completed = {}
    records = [json.loads(path.read_text(encoding="utf-8")) for path in (root/"checkpoints").glob("*.json")]
    progress = root/"progress.jsonl"
    if progress.exists():
        records += [json.loads(line) for line in progress.read_text(encoding="utf-8").splitlines() if line.strip()]
    for record in records:
        item = record["item"]
        if type(item) is not int or item in completed:
            raise ValueError("Duplicate or invalid audit checkpoint")
        if item in inventory:
            path = inventory[item]
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            count = sum(row["amount"] != row["expected"] for row in rows)
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if type(record.get("mismatches")) is not int or record["mismatches"] != count or record.get("sha256") != sha:
                raise ValueError(f"False checkpoint count or hash for item {item}")
        completed[item] = record["mismatches"]
    if inventory and any(item not in inventory for item in completed):
        raise ValueError("Checkpoint refers to an unknown audit item")
    return {"completed":completed,"remaining":sorted(set(inventory)-set(completed)),
            "has_inventory":bool(inventory)}


def verification_scope(cwd):
    return "audit_counts_hashes_queue" if list(Path(cwd).glob("item-*.jsonl")) else "handoff_format_only"


def verify(handoff_path, cwd):
    try:
        text = Path(handoff_path).read_text(encoding="utf-8")
        state = checkpoint_state(cwd) if verification_scope(cwd) != "handoff_format_only" else None
    except (OSError, ValueError, TypeError, KeyError) as error:
        return [f"Cannot verify handoff: {error}"]
    errors = []
    for heading in ("# HANDOFF","## Goal","## Ordered queue of remaining items","## Constraints (never violate)"):
        if heading not in text:
            errors.append(f"Missing heading: {heading}")
    if state is None:
        return errors
    for item,count in re.findall(r"\bitem[ -]*(\d+)\b[^\n.]*?\b(\d+)\s+mismatches?\b",text,re.I):
        if state["completed"].get(int(item)) != int(count):
            errors.append(f"False mismatch claim for item {item}: {count}")
    section = re.search(r"## Ordered queue of remaining items\s*\n(.*?)(?:\n## |\Z)",text,re.S)
    queue = [int(item) for item in re.findall(r"^\s*\d+[.)]\s+item[ -]*(\d+)\b",section.group(1),re.I|re.M)] if section else []
    if queue != state["remaining"]:
        errors.append(f"Queue {queue} differs from unfinished disk items {state['remaining']}")
    return errors

