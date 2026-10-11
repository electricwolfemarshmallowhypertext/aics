"""Real CLI relay corpus; independent final graders, no product task controller."""
import hashlib
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from tailer import parse_token_count


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected(order):
    net = max(0, order['quantity'] * order['unit_cents'] - order.get('discount_cents', 0))
    return (net * (10000 + order.get('tax_bps', 0)) + 5000) // 10000


def order_for(case, row):
    result = {'quantity': row % 7 + 1, 'unit_cents': case * 17 + row * 13}
    if row % 3:
        result['discount_cents'] = row * (case % 5 + 1)
    if row % 4:
        result['tax_bps'] = (row * 37 + case) % 1500
    if row % 19 == 0:
        result['discount_cents'] = result['quantity'] * result['unit_cents'] + 1
    return result


def prepare(root, kind, count):
    root.mkdir()
    (root / 'completed').mkdir()
    if kind == 'audit':
        (root / 'checkpoints').mkdir()
    for case in range(1, count + 1):
        rows = []
        for row in range(1, 181):
            if kind == 'audit':
                rows.append({'item': case, 'row': row, 'amount': row * 7,
                             'expected': row * 7 + (row % 19 == 0),
                             'note': f'Reconcile ledger entry {case:03}/{row:04}; preserve the input and verify its saved count and hash.'})
            else:
                order = order_for(case, row)
                rows.append({'order': order, 'expected_cents': expected(order),
                             'note': f'Case {case:03} invoice row {row:04}: verify integer arithmetic, optional discount, tax rounding and nonnegative net.'})
        name = f'item-{case:03}.jsonl' if kind == 'audit' else f'fixture-{case:03}.jsonl'
        (root / name).write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        if kind == 'bugfix':
            (root / f'invoice_{case:03}.py').write_text('def total_cents(order):\n    return order["quantity"] + order["unit_cents"]\n')
    if kind == 'feature':
        (root / 'money.py').write_text('def format_cents(value):\n    return f"{value // 100}.{value % 100:02d}"\n')
    rules = f'''Work only in this directory. There are {count} numbered cases. Process them strictly ascending, one case at a time.
For EVERY case first read the entire numbered JSONL fixture into shell tool responses: read rows 1-90, then rows 91-180 in a separate call. Request max_output_tokens=8000 per call. Do not batch fixtures, hide their contents, or bulk-process cases. If a response is truncated, page the missing portion before continuing.
After completing a case, run its check, then atomically write its completion JSON using json.dump, flush, and os.replace. Reread and verify it before moving on. Sleep 3 seconds between cases.
On restart inspect completion files and continue at the first missing case. Never overwrite completed cases. Preserve TASK.md, all input fixtures, and all parent-directory files.
After ALL {count} cases pass, write DONE.txt containing CORPUS_COMPLETE. This final file is required even if all completion files already exist.
Continue until DONE.txt exists. Do not finish with a progress report, stop after an arbitrary number of cases, or ask for another turn. The daemon handles context restarts; your task is the complete workload.
'''
    if kind == 'audit':
        rules += '''Inputs are item-NNN.jsonl. Count rows where amount != expected and compute SHA256 of the exact input bytes. Completion files are checkpoints/item-NNN.json with {item, mismatches, sha256}. Every input must be read into the conversation before its checkpoint is saved. No other source files need changing.
'''
    else:
        rules += '''Inputs are fixture-NNN.jsonl. The invoice contract: net=max(0, quantity*unit_cents-discount_cents); discount_cents defaults to 0. tax_bps defaults to 0. Return the integer cents result (net*(10000+tax_bps)+5000)//10000. Inputs and results use integers. Do not use floating point, read expected_cents from within production functions, or hardcode fixture results.
Completion files are completed/case-NNN.json with {case, checked_rows:180}; save only after an executable Python check imports the production module and checks every fixture row. Hidden inputs will independently test the same contract.
'''
        if kind == 'bugfix':
            rules += '''Fix total_cents(order) in each existing invoice_NNN.py. Preserve its function signature. Read the source before fixing it. Test optional fields, clamped net, and half-up tax rounding. Do not create other production modules.
'''
        else:
            rules += '''Implement a multi-file invoice reporting feature for each case: create invoice_NNN.py with total_cents(order) and report_NNN.py with render(order). report_NNN imports that case's total_cents and shared money.format_cents; render returns exactly "TOTAL $" plus the formatted cents result. Preserve money.py unchanged. Verify both numeric and rendered outputs for all fixture rows before saving completion.
'''
    (root / 'TASK.md').write_text(rules, encoding='utf-8')
    return rules


def verify(root, kind, count):
    receipts = [json.loads(line) for line in (root / 'receipts.jsonl').read_text().splitlines()]
    assert receipts, 'No real relays'
    seen = set()
    for record in receipts:
        assert record['session_id'] not in seen, 'Duplicate receipt'
        seen.add(record['session_id'])
        assert record['total_tokens'] == record['occupancy']
        assert record['utilization'] == record['occupancy'] / record['window'] >= config.THRESHOLD
        assert record['outcome'] == 'handoff_verified' and not record['verification_errors']
        handoff = Path(record['handoff']).read_text()
        assert all(heading in handoff for heading in ('# HANDOFF', '## Goal', '## Ordered queue of remaining items', '## Constraints (never violate)'))
        events = [json.loads(line) for line in Path(record['session_rollout']).read_text().splitlines() if line.strip()]
        assert events[0]['payload']['id'] == record['session_id']
        assert Path(events[0]['payload']['cwd']).resolve() == root.resolve()
        assert any((usage := parse_token_count(json.dumps(event))) and usage['occupancy'] == record['occupancy'] and usage['window'] == record['window'] for event in events)
        print(json.dumps({'inspected': True, 'workload': kind, **record}), flush=True)
    completed_dir = root / ('checkpoints' if kind == 'audit' else 'completed')
    completion_paths = sorted(completed_dir.glob('*.json'), key=lambda p: p.stat().st_mtime_ns)
    order = [json.loads(path.read_text())['item' if kind == 'audit' else 'case'] for path in completion_paths]
    assert order == list(range(1, count + 1)), f'Completion order wrong: {order}'
    for case in range(1, count + 1):
        if kind == 'audit':
            source = root / f'item-{case:03}.jsonl'
            rows = [json.loads(line) for line in source.read_text().splitlines()]
            checkpoint = json.loads((completed_dir / f'item-{case:03}.json').read_text())
            assert checkpoint == {'item': case, 'mismatches': sum(row['amount'] != row['expected'] for row in rows), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
        else:
            sys.path.insert(0, str(root))
            invoice = load(root / f'invoice_{case:03}.py')
            report = load(root / f'report_{case:03}.py') if kind == 'feature' else None
            for row in range(1, 201):
                order_input = order_for(case, row)
                result = expected(order_input)
                assert type(invoice.total_cents(order_input)) is int and invoice.total_cents(order_input) == result
                if report:
                    assert report.render(order_input) == f'TOTAL ${result // 100}.{result % 100:02d}'
            for name in list(sys.modules):
                if name.startswith(('invoice_', 'report_')) or name == 'money':
                    del sys.modules[name]
            sys.path.pop(0)
    assert (root / 'DONE.txt').read_text().strip() == 'CORPUS_COMPLETE'
    result = {'passed': True, 'workload': kind, 'real_relays': len(receipts), 'completed_cases': count, 'receipt_sessions': sorted(seen)}
    (root / 'verification.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


def main():
    kind = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 168
    root = Path(__file__).resolve().parent / f'corpus-{kind}-{time.time_ns()}'
    goal = prepare(root, kind, count)
    preserved = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.glob('*.jsonl')}
    preserved['TASK.md'] = hashlib.sha256((root / 'TASK.md').read_bytes()).hexdigest()
    if kind == 'feature':
        preserved['money.py'] = hashlib.sha256((root / 'money.py').read_bytes()).hexdigest()
    launcher = f"import sys;sys.path.insert(0,{str(root.parents[1])!r});import receipts,relayd;from pathlib import Path;receipts.RECEIPTS_PATH=Path.cwd()/'receipts.jsonl';relayd.run({goal!r},Path.cwd())"
    with (root / 'daemon.log').open('w') as output:
        proc = subprocess.Popen([sys.executable, '-c', launcher], cwd=root, stdout=output, stderr=subprocess.STDOUT)
        print(json.dumps({'workspace': str(root), 'workload': kind, 'cases': count, 'daemon_pid': proc.pid}), flush=True)
        try:
            code = proc.wait(timeout=14400)
        except BaseException:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=30)
            raise
    assert code == 0, f'Daemon exited {code}; inspect {root / "daemon.log"}'
    assert all(hashlib.sha256((root / name).read_bytes()).hexdigest() == sha for name, sha in preserved.items()), 'Preserved input changed'
    verify(root, kind, count)


if __name__ == '__main__':
    main()
