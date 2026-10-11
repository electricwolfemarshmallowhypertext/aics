"""Interrupt a real Codex tool write and verify fresh-session disk recovery."""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from process_control_test import alive
from relayd import spawn, kill
from tailer import bind_rollout


WRITER = '''import json,os,subprocess,sys,time
from pathlib import Path
root=Path(__file__).resolve().parent
draft=root/'checkpoint.tmp'
draft.write_text(json.dumps({'item':1,'valid':True}))
os.replace(draft,root/'checkpoint.json')
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)',str(root)])
root.joinpath('pids.json').write_text(json.dumps([os.getpid(),child.pid]))
with draft.open('w') as file:
    file.write('{"item":2,'); file.flush(); os.fsync(file.fileno())
    root.joinpath('WRITING').write_text('ready')
    time.sleep(300)
'''
RECOVER = '''import json,os
from pathlib import Path
root=Path(__file__).resolve().parent
assert json.loads(root.joinpath('checkpoint.json').read_text())=={'item':1,'valid':True}
draft=root/'checkpoint.tmp'
with draft.open('w') as file:
    json.dump({'item':2,'valid':True},file); file.flush(); os.fsync(file.fileno())
os.replace(draft,root/'checkpoint.json')
root.joinpath('DONE').write_text('RECOVERED')
'''


def host_tool_pids(root):
    """Codex's Linux sandbox reports namespace PIDs; check host processes."""
    result = []
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            args = (directory / "cmdline").read_bytes().decode().rstrip("\0").split("\0")
            in_workspace = (directory / "cwd").resolve() == root
            writer = in_workspace and len(args) == 2 and args[-1] == "writer.py"
            child = len(args) == 4 and args[1:] == ["-c", "import time; time.sleep(300)", str(root)]
            if writer or child:
                result.append(int(directory.name))
        except (OSError, UnicodeError):
            continue
    return result


def main():
    root = Path(__file__).resolve().parent / f"shutdown-run-{os.name}-{time.time_ns()}"
    root.mkdir()
    (root / "writer.py").write_text(WRITER, encoding="utf-8")
    (root / "recover.py").write_text(RECOVER, encoding="utf-8")
    python = "python" if os.name == "nt" else "python3"
    proc = spawn(f"Run `{python} writer.py` as a shell tool in this directory. Do not inspect or change either Python file. Wait for the command to finish. Do not run other commands.", root)
    report = {"platform": os.name, "workspace": str(root), "passed": False}
    try:
        identity = proc._relay_identity.get()
        report["source_session_id"] = identity
        report["source_rollout"] = str(bind_rollout(identity, root))
        deadline = time.monotonic() + 180
        while not (root / "WRITING").exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"Writer never started; child exit {proc.poll()}")
            time.sleep(.2)
        pids = json.loads((root / "pids.json").read_text())
        if os.name != "nt":
            report["tool_namespace_pids"] = pids
            pids = host_tool_pids(root)
            assert len(pids) == 2, f"Cannot identify both live tool descendants: {pids}"
        report["tool_pids"] = pids
        report["shutdown"] = kill(proc)
        deadline = time.monotonic() + 5
        while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(.1)
        report["surviving_tool_pids"] = [pid for pid in pids if alive(pid)]
        assert not report["surviving_tool_pids"], "Owned tool descendants survived shutdown"
        assert json.loads((root / "checkpoint.json").read_text()) == {"item": 1, "valid": True}
        report["committed_checkpoint_preserved"] = True
        fresh = spawn(f"Run `{python} recover.py` as a shell tool in this directory. Do not inspect or change either Python file. Once it succeeds, reply RECOVERED.", root)
        try:
            report["replacement_session_id"] = fresh._relay_identity.get()
            report["replacement_rollout"] = str(bind_rollout(report["replacement_session_id"], root))
            fresh.wait(timeout=180)
            assert fresh.returncode == 0
            assert (root / "DONE").read_text() == "RECOVERED"
            assert json.loads((root / "checkpoint.json").read_text()) == {"item": 2, "valid": True}
        finally:
            kill(fresh)
        report["passed"] = True
    finally:
        kill(proc)
        (root / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
