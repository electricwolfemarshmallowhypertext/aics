"""Exercise real process trees, forced shutdown, and exact rollout ownership."""
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import relayd
import tailer
from process_control import spawn_owned, stop_owned


def alive(pid):
    if os.name == "nt":
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        api.OpenProcess.restype = ctypes.c_void_p
        api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = api.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 258
        finally:
            api.CloseHandle(handle)
    status = Path(f"/proc/{pid}/stat")
    return status.exists() and status.read_text().rsplit(")", 1)[1].split()[0] != "Z"


CHILD = "import signal,time; signal.signal(getattr(signal,'SIGBREAK',signal.SIGTERM),signal.SIG_IGN); time.sleep(120)"
PARENT = """import json,os,signal,subprocess,sys,time
from pathlib import Path
root=Path(sys.argv[1])
if sys.argv[2]=='ignore':
    signal.signal(getattr(signal,'SIGBREAK',signal.SIGTERM),signal.SIG_IGN)
child=subprocess.Popen([sys.executable,'-c',sys.argv[3]])
root.joinpath('pids.json').write_text(json.dumps([os.getpid(),child.pid]))
root.joinpath('checkpoint.json').write_text('{"item":1,"valid":true}')
with root.joinpath('checkpoint.tmp').open('w') as file:
    file.write('{"item":2,'); file.flush(); os.fsync(file.fileno())
    root.joinpath('READY').write_text('writing')
    if sys.argv[2]=='exit':
        sys.exit(0)
    time.sleep(120)
"""


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.doCleanups()
        self.temp.cleanup()

    def launch(self, mode):
        proc = spawn_owned([sys.executable, "-c", PARENT, str(self.root), mode, CHILD],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(stop_owned, proc, .1)
        deadline = time.monotonic() + 10
        while not (self.root / "READY").exists():
            if time.monotonic() >= deadline:
                self.fail(f"Child never became ready: {proc.poll()}")
            time.sleep(.02)
        return proc

    def assert_tree_dead(self):
        pids = json.loads((self.root / "pids.json").read_text())
        deadline = time.monotonic() + 5
        while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(any(alive(pid) for pid in pids), pids)
        self.assertEqual(json.loads((self.root / "checkpoint.json").read_text()), {"item": 1, "valid": True})
        # Recovery commits the next checkpoint atomically; a torn draft is never truth.
        draft = self.root / "checkpoint.tmp"
        draft.write_text('{"item":2,"valid":true}')
        os.replace(draft, self.root / "checkpoint.json")
        self.assertEqual(json.loads((self.root / "checkpoint.json").read_text())["item"], 2)

    def test_forced_shutdown_during_checkpoint_write(self):
        proc = self.launch("ignore")
        time.sleep(.2)  # Allow the child's signal handler to be installed.
        result = stop_owned(proc, grace=.1)
        self.assertTrue(result["forced_parent_shutdown"])
        self.assert_tree_dead()
        self.assertEqual(stop_owned(proc), result)

    def test_graceful_parent_shutdown_removes_tool_child(self):
        proc = self.launch("default")
        stop_owned(proc, grace=1)
        self.assert_tree_dead()

    def test_natural_parent_exit_removes_tool_child(self):
        proc = self.launch("exit")
        proc.wait(timeout=5)
        stop_owned(proc, grace=.1)
        self.assert_tree_dead()

    def test_bind_failure_cleans_owned_tree(self):
        proc = self.launch("default")
        proc._relay_identity = Mock()
        with patch.object(relayd, "spawn", return_value=proc), patch.object(relayd, "bind_rollout", side_effect=RuntimeError("bind failed")):
            with self.assertRaisesRegex(RuntimeError, "bind failed"):
                relayd.run("goal", self.root)
        self.assert_tree_dead()

    def test_exact_id_ignores_other_active_session_and_rejects_ambiguity(self):
        sessions = self.root / "sessions"
        sessions.mkdir()
        for identity in ("ours", "other"):
            (sessions / f"rollout-time-{identity}.jsonl").write_text(json.dumps({"type": "session_meta", "payload": {"id": identity, "cwd": str(self.root)}}) + "\n")
        with patch.object(tailer, "CODEX_HOME", self.root):
            self.assertEqual(tailer.bind_rollout("ours", self.root).name, "rollout-time-ours.jsonl")
            (sessions / "rollout-another-ours.jsonl").write_text((sessions / "rollout-time-ours.jsonl").read_text())
            with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
                tailer.bind_rollout("ours", self.root)
            with self.assertRaises(ValueError):
                tailer.bind_rollout(time.time(), self.root)


if __name__ == "__main__":
    unittest.main()
