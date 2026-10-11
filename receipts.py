import json
import time
from pathlib import Path

RECEIPTS_PATH = Path("receipts.jsonl")


def log(event, path=None):
    """Append one immutable receipt for each relay."""
    timestamp = int(time.time())
    record = {"ts": timestamp, "timestamp": timestamp, **event}
    if "occupancy" in event:
        record["total_tokens"] = event["occupancy"]
    with open(path if path is not None else RECEIPTS_PATH, "a", encoding="utf-8") as receipts:
        receipts.write(json.dumps(record) + "\n")
