"""Real worker pass -> controlled artifact fault -> quality relay -> repaired result."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from handoff import atomic_write
from process_control import spawn_owned, stop_owned
from tailer import parse_token_count
import config


def main():
    root = Path(__file__).resolve().parent / f'quality-run-{time.time_ns()}'
    root.mkdir()
    verifier = root / 'verify_result.py'
    verifier.write_text('''import hashlib,json
from pathlib import Path
path=Path('result.json')
if not path.exists():
 print(json.dumps({'status':'not_ready'}))
else:
 try:
  raw=path.read_bytes()
  result=json.loads(raw)
 except (OSError,ValueError):
  print(json.dumps({'status':'not_ready'}))
 else:
  if result.get('completed') is not True:
   print(json.dumps({'status':'not_ready'}))
  else:
   ok=type(result.get('answer')) is int and result['answer']==19*23
   print(json.dumps({'status':'pass' if ok else 'fail','sample_id':hashlib.sha256(raw).hexdigest(),'reason':'19 times 23 must equal 437'}))
''', encoding='utf-8')
    verifier_hash = hashlib.sha256(verifier.read_bytes()).hexdigest()
    goal = '''Work only in this directory. Preserve verify_result.py and all parent files.
Compute 19 times 23 and atomically write result.json with {"completed":true,"answer":the integer product}. Verify that saved answer.
If result.json already exists, inspect it and repair any incorrect answer before continuing. Never accept an incorrect saved answer.
After saving and verifying the correct answer, run a shell sleep of 30 seconds, then verify result.json again and write DONE.txt containing QUALITY_COMPLETE. Complete this final deliverable. Do not stop with a progress report.
'''
    (root / 'TASK.md').write_text(goal)
    launcher = f"import sys;sys.path.insert(0,{str(root.parents[1])!r});import relayd,receipts;from pathlib import Path;receipts.RECEIPTS_PATH=Path.cwd()/'receipts.jsonl';relayd.run({goal!r},Path.cwd(),quality_command=[sys.executable,'verify_result.py'])"
    start = time.monotonic()
    injected = False
    with (root / 'daemon.log').open('w') as log:
        proc = spawn_owned([sys.executable, '-c', launcher], cwd=root, stdin=subprocess.DEVNULL,
                           stdout=log, stderr=subprocess.STDOUT)
        print(json.dumps({'workspace':str(root),'daemon_pid':proc.pid}), flush=True)
        try:
            while proc.poll() is None:
                observations = root / '.relay' / 'quality-probes.jsonl'
                if not injected and observations.exists():
                    records = [json.loads(line) for line in observations.read_text().splitlines()]
                    if any(record['status'] == 'pass' for record in records):
                        atomic_write(root / 'result.json', json.dumps({'completed':True,'answer':438}))
                        injected = True
                        print('Injected controlled wrong answer after a real worker pass.', flush=True)
                if time.monotonic() - start > 300:
                    raise TimeoutError('Real quality relay did not finish within five minutes')
                time.sleep(.2)
            assert proc.returncode == 0, (root / 'daemon.log').read_text()[-3000:]
        finally:
            stop_owned(proc, grace=0)
    observations = [json.loads(line) for line in (root / '.relay' / 'quality-probes.jsonl').read_text().splitlines()]
    receipts = [json.loads(line) for line in (root / 'receipts.jsonl').read_text().splitlines()]
    assert injected and len(receipts) == 1
    receipt = receipts[0]
    assert receipt['trigger_reason'] == 'quality'
    assert receipt['utilization'] < config.THRESHOLD
    assert receipt['quality_probe']['status'] == 'fail'
    assert receipt['outcome'] == 'handoff_verified' and receipt['verification_errors'] == []
    assert json.loads((root / 'result.json').read_text()) == {'completed':True,'answer':437}
    assert (root / 'DONE.txt').read_text().strip() == 'QUALITY_COMPLETE'
    assert hashlib.sha256(verifier.read_bytes()).hexdigest() == verifier_hash
    assert any(r['status']=='pass' and r['session_id'] != receipt['session_id'] for r in observations)
    events = [json.loads(line) for line in Path(receipt['session_rollout']).read_text().splitlines()]
    assert events[0]['payload']['id'] == receipt['session_id']
    assert any((u:=parse_token_count(json.dumps(e))) and u['occupancy']==receipt['occupancy'] and u['window']==receipt['window'] for e in events)
    report = {'passed':True,'controlled_fault':True,'natural_context_degradation_claimed':False,
              'real_quality_relays':len(receipts),'fire_utilization':receipt['utilization'],
              'observed_statuses':[r['status'] for r in observations],
              'source_session':receipt['session_id'],
              'replacement_sessions':sorted({r['session_id'] for r in observations if r['session_id'] != receipt['session_id']}),
              'verifier_preserved':True,'final_answer':437,'final_marker':'QUALITY_COMPLETE'}
    atomic_write(root / 'verification.json', json.dumps(report,indent=2)+'\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
