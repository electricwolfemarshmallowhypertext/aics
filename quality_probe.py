"""Run an optional task verifier; only an explicit failed completed sample triggers.

Usage: python relayd.py "goal" --quality-check verify_task.py
The trusted read-only verifier exits 0 and emits one JSON object:
{"status": "pass" or "fail", "sample_id": "artifact content hash", "reason": "check"}
Emit {"status": "not_ready"} for unfinished work. Never grade partial edits.
Timeouts/errors are unavailable, not task failures. A failed sample relays once.
"""
import json
from pathlib import Path
import subprocess
import time

from process_control import spawn_owned, stop_owned


class QualityProbe:
    def __init__(self, command, cwd, timeout=10):
        self.command = command
        self.cwd = Path(cwd)
        self.timeout = timeout
        self.seen = set()
        self.log_path = self.cwd / '.relay' / 'quality-probes.jsonl'

    def poll(self, session_id, usage):
        proc = None
        try:
            proc = spawn_owned(self.command, cwd=self.cwd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8')
            stdout, stderr = proc.communicate(timeout=self.timeout)
            if proc.returncode:
                raise ValueError(f'Verifier exited {proc.returncode}: {stderr[-500:]}')
            result = json.loads(stdout)
            if not isinstance(result, dict) or result.get('status') not in {'pass', 'fail', 'not_ready'}:
                raise ValueError('Verifier must return pass, fail, or not_ready')
            if result['status'] == 'not_ready':
                return None
            if not isinstance(result.get('sample_id'), str) or not result['sample_id']:
                raise ValueError('A stable completed-sample identity is required')
            if not isinstance(result.get('reason'), str):
                raise ValueError('A checkable reason is required')
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            result = {'status': 'unavailable', 'reason': str(error)}
        finally:
            if proc is not None:
                stop_owned(proc, grace=0)
                for stream in (proc.stdout, proc.stderr):
                    if stream is not None:
                        stream.close()
        # Include the verdict: a corrected artifact can pass after its failed version.
        key = (result['sample_id'], result['status']) if result['status'] in {'pass', 'fail'} else ('unavailable', result['reason'])
        if key in self.seen:
            return None
        self.seen.add(key)
        record = {'timestamp': time.time(), 'session_id': session_id, **result,
                  'occupancy': usage['occupancy'], 'window': usage['window'],
                  'utilization': usage['occupancy'] / usage['window']}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open('a', encoding='utf-8') as output:
            output.write(json.dumps(record) + '\n')
        return record
