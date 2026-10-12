"""Exercise the actual native TUI in a terminal, with a controlled 15% trigger."""
import argparse
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--threshold', type=float, default=.15)
    parser.add_argument('--cases', type=int, default=4)
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    if not 0 < args.threshold < 1 or args.cases < 1 or args.timeout < 1:
        parser.error('Invalid threshold, case count or timeout')
    project = Path(__file__).resolve().parents[1]
    binary = project / '.relay/codex-source/codex-rs/target/debug/codex'
    root = project / 'tests' / f'native-run-{time.time_ns()}'
    goal = prepare(root, 'audit', args.cases)
    wrapper = root / 'native_test_helper.py'
    wrapper.write_text(f"import sys;sys.path.insert(0,{str(project)!r});import config;config.THRESHOLD={args.threshold!r};import native_relay;native_relay.main()\n")
    preserved = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('item-*.jsonl')}
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH',40,140,0,0))
    env = {**os.environ, 'TERM':'xterm-256color', 'AICS_RELAY_PYTHON':sys.executable, 'AICS_RELAY_HELPER':str(wrapper)}
    proc = subprocess.Popen([str(binary),'--model','gpt-6-sol','-c','tool_output_token_limit=8000','--sandbox','workspace-write','--ask-for-approval','never','--no-alt-screen',goal],cwd=root,env=env,stdin=slave,stdout=slave,stderr=slave,start_new_session=True)
    os.close(slave)
    print(json.dumps({'workspace':str(root),'tui_pid':proc.pid,'run_threshold':args.threshold}),flush=True)
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
                if time.monotonic()-start>args.timeout:
                    raise TimeoutError(f'Native interactive run did not finish in {args.timeout} seconds')
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
    assert len(state['completed'])==args.cases and not state['remaining']
    ordered=sorted((root/'checkpoints').glob('*.json'),key=lambda p:p.stat().st_mtime_ns)
    assert [json.loads(p.read_text())['item'] for p in ordered]==list(range(1,args.cases+1))
    assert all(hashlib.sha256((root/name).read_bytes()).hexdigest()==sha for name,sha in preserved.items())
    records=[json.loads(line) for line in (root/'receipts.jsonl').read_text().splitlines()]
    assert records and all(r['native_tui'] and r['outcome']=='handoff_verified' and r['utilization']>=args.threshold for r in records)
    inspected_sessions = []
    for r in records:
        events=[json.loads(line) for line in Path(r['session_rollout']).read_text().splitlines()]
        assert events[0]['payload']['id']==r['session_id']
        assert r['replacement_session_id'] != r['session_id']
        replacements = list((Path.home()/'.codex/sessions').rglob(f"rollout-*{r['replacement_session_id']}.jsonl"))
        assert len(replacements)==1, 'Replacement rollout missing or ambiguous'
        replacement_events=[json.loads(line) for line in replacements[0].read_text().splitlines()]
        meta=replacement_events[0]['payload']
        assert meta['id']==r['replacement_session_id'] and Path(meta['cwd']).resolve()==root.resolve()
        assert any(e.get('type') == 'response_item' and e.get('payload', {}).get('type') in ('function_call','custom_tool_call') for e in replacement_events), 'Replacement performed no tool work'
        inspected_sessions.append({'source':r['session_id'], 'replacement':r['replacement_session_id'], 'source_rollout':r['session_rollout'], 'replacement_rollout':str(replacements[0]), 'occupancy':r['occupancy'], 'window':r['window'], 'utilization':r['utilization']})
        assert any((u:=parse_token_count(json.dumps(e))) and u['occupancy']==r['occupancy'] and u['window']==r['window'] for e in events)
    assert proc.returncode == 0, f'Native terminal exited {proc.returncode}'
    report={'passed':True,'native_tui':True,'run_threshold':args.threshold,'production_threshold_unchanged':.65,'completed_checkpoints':args.cases,'ordered':True,'inputs_preserved':True,'composer_draft_preserved':True,'real_relays':len(records),'source_sessions':[r['session_id'] for r in records],'final_marker':'CORPUS_COMPLETE','native_exit_code':proc.returncode,'inspected_sessions':inspected_sessions,'terminal_log':str(root/'terminal.log')}
    (root/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
