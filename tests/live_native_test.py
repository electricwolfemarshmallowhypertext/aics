"""Exercise the actual native TUI in a terminal, with a controlled 15% trigger."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from corpus_test import prepare
from handoff_check import checkpoint_state
from tailer import parse_token_count


def main():
    project = Path(__file__).resolve().parents[1]
    binary = project / '.relay/codex-source/codex-rs/target/debug/codex'
    root = project / 'tests' / f'native-run-{time.time_ns()}'
    goal = prepare(root, 'audit', 4)
    wrapper = root / 'native_test_helper.py'
    wrapper.write_text(f"import sys;sys.path.insert(0,{str(project)!r});import config;config.THRESHOLD=.15;import native_relay;native_relay.main()\n")
    preserved = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('item-*.jsonl')}
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH',40,140,0,0))
    env = {**os.environ, 'TERM':'xterm-256color', 'AICS_RELAY_PYTHON':sys.executable, 'AICS_RELAY_HELPER':str(wrapper)}
    proc = subprocess.Popen([str(binary),'--model','gpt-6-sol','-c','tool_output_token_limit=8000','--sandbox','workspace-write','--ask-for-approval','never','--no-alt-screen',goal],cwd=root,env=env,stdin=slave,stdout=slave,stderr=slave,start_new_session=True)
    os.close(slave)
    print(json.dumps({'workspace':str(root),'tui_pid':proc.pid,'controlled_threshold':.15}),flush=True)
    start = time.monotonic()
    trusted = False
    draft_sent = False
    done_seen = None
    redraw_start = None
    output = bytearray()
    try:
        with (root/'terminal.log').open('wb') as log:
            while proc.poll() is None:
                if select.select([master],[],[],.5)[0]:
                    try: chunk=os.read(master,65536)
                    except OSError: break
                    output.extend(chunk)
                    log.write(chunk);log.flush()
                    if b'\x1b[6n' in chunk: os.write(master,b'\x1b[1;1R')
                    if b'\x1b[c' in chunk: os.write(master,b'\x1b[?1;2c')
                    if b'\x1b[>c' in chunk: os.write(master,b'\x1b[>0;0;0c')
                    if b'\x1b]10;?' in chunk: os.write(master,b'\x1b]10;rgb:dddd/dddd/dddd\x1b\\')
                    if b'\x1b]11;?' in chunk: os.write(master,b'\x1b]11;rgb:0000/0000/0000\x1b\\')
                    recent=bytes(output[-24000:]).lower()
                    if not trusted and (b'trust this' in recent or b'trust the' in recent) and b'yes' in recent:
                        os.write(master,b'\r');trusted=True
                if not draft_sent and (root/'.relay/goal.txt').exists():
                    os.write(master,b'\x1b[200~NATIVE_DRAFT_SENTINEL\x1b[201~')
                    draft_sent = True
                if (root/'DONE.txt').exists():
                    if done_seen is None:
                        done_seen = time.monotonic()
                        redraw_start = len(output)
                        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH',41,141,0,0))
                        os.killpg(proc.pid, signal.SIGWINCH)
                    elif time.monotonic()-done_seen > 3:
                        os.write(master,b'\x03')
                        time.sleep(.5)
                        os.write(master,b'/quit')
                        time.sleep(.3)
                        os.write(master,b'\r')
                        break
                if time.monotonic()-start>600:
                    raise TimeoutError('Native interactive test did not finish in ten minutes')
            deadline=time.monotonic()+30
            while proc.poll() is None and time.monotonic()<deadline:
                if select.select([master],[],[],.2)[0]:
                    try: chunk=os.read(master,65536)
                    except OSError: break
                    output.extend(chunk); log.write(chunk); log.flush()
            proc.wait(timeout=1)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid,signal.SIGTERM)
            try: proc.wait(timeout=20)
            except subprocess.TimeoutExpired: os.killpg(proc.pid,signal.SIGKILL);proc.wait()
        os.close(master)
    assert (root/'DONE.txt').read_text().strip()=='CORPUS_COMPLETE'
    assert draft_sent and redraw_start is not None
    assert b'NATIVE_DRAFT_SENTINEL' in output[redraw_start:], 'Composer draft missing after native relay'
    state=checkpoint_state(root)
    assert len(state['completed'])==4 and not state['remaining']
    ordered=sorted((root/'checkpoints').glob('*.json'),key=lambda p:p.stat().st_mtime_ns)
    assert [json.loads(p.read_text())['item'] for p in ordered]==list(range(1,5))
    assert all(hashlib.sha256((root/name).read_bytes()).hexdigest()==sha for name,sha in preserved.items())
    records=[json.loads(line) for line in (root/'receipts.jsonl').read_text().splitlines()]
    assert records and all(r['native_tui'] and r['outcome']=='handoff_verified' and r['utilization']>=.15 for r in records)
    for r in records:
        events=[json.loads(line) for line in Path(r['session_rollout']).read_text().splitlines()]
        assert events[0]['payload']['id']==r['session_id']
        assert r['replacement_session_id'] != r['session_id']
        replacements = list((Path.home()/'.codex/sessions').rglob(f"rollout-*{r['replacement_session_id']}.jsonl"))
        assert len(replacements)==1, 'Replacement rollout missing or ambiguous'
        meta=json.loads(replacements[0].read_text().splitlines()[0])['payload']
        assert meta['id']==r['replacement_session_id'] and Path(meta['cwd']).resolve()==root.resolve()
        assert any((u:=parse_token_count(json.dumps(e))) and u['occupancy']==r['occupancy'] and u['window']==r['window'] for e in events)
    report={'passed':True,'native_tui':True,'controlled_threshold':.15,'production_threshold_unchanged':.65,'completed_checkpoints':4,'ordered':True,'inputs_preserved':True,'composer_draft_preserved':True,'real_relays':len(records),'source_sessions':[r['session_id'] for r in records],'final_marker':'CORPUS_COMPLETE'}
    (root/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
