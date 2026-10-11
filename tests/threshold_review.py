"""Replay stopped corpus rollouts; do not infer unrecorded higher-threshold outcomes."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from tailer import parse_token_count


def main():
    status = json.loads(Path(__file__).with_name('corpus-status.json').read_text())
    sessions = []
    seen = set()
    for run in status['runs']:
        if not run['real_relays']:
            continue
        records = [json.loads(line) for line in
                   (Path(run['workspace']) / 'receipts.jsonl').read_text().splitlines()]
        assert len(records) == run['real_relays']
        for receipt in records:
            session_id = receipt['session_id']
            assert session_id not in seen
            seen.add(session_id)
            events = [json.loads(line) for line in
                      Path(receipt['session_rollout']).read_text().splitlines() if line.strip()]
            assert events[0]['type'] == 'session_meta'
            assert events[0]['payload']['id'] == session_id
            assert Path(events[0]['payload']['cwd']).resolve() == Path(run['workspace']).resolve()
            matches = [i for i, event in enumerate(events)
                       if (usage := parse_token_count(json.dumps(event)))
                       and usage['occupancy'] == receipt['occupancy']
                       and usage['window'] == receipt['window']
                       and (not receipt.get('snapshot_timestamp')
                            or event.get('timestamp') == receipt['snapshot_timestamp'])]
            assert matches, f'Receipt has no matching snapshot: {session_id}'
            prefix = events[:max(matches) + 1]
            samples = [usage for event in prefix
                       if (usage := parse_token_count(json.dumps(event))) is not None]
            ratios = [sample['occupancy'] / sample['window'] for sample in samples]
            jumps = [(b['occupancy'] - a['occupancy']) / b['window']
                     for a, b in zip(samples, samples[1:]) if a['window'] == b['window']]
            assert abs(receipt['utilization'] - receipt['occupancy'] / receipt['window']) < 1e-12
            assert receipt['utilization'] >= 0.65
            models = sorted({event['payload']['model'] for event in prefix
                             if event['type'] == 'turn_context' and event['payload'].get('model')})
            sessions.append({
                'session_id': session_id, 'task_status': run['status'], 'models': models,
                'fire_utilization': receipt['utilization'], 'window': receipt['window'],
                'max_snapshot_growth_fraction': max([0, *jumps]),
                'compactions_before_fire': sum(event['type'] == 'compacted'
                    or event.get('payload', {}).get('type') == 'context_compacted' for event in prefix),
                'first_observed_crossing': {
                    str(t): next((ratio for ratio in ratios if ratio >= t), None)
                    for t in (0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.93)},
            })
    assert len(sessions) == status['inspected_real_receipts'] == 24
    sol = [session for session in sessions if session['models'] == [config.MODEL]]
    assert len(sol) == 3, 'Current-model proof must contain three source sessions'
    max_growth = max(session['max_snapshot_growth_fraction'] for session in sol)
    candidates = [{
        'threshold': threshold, 'nominal_headroom_fraction': 1 - threshold,
        'headroom_minus_largest_observed_sol_jump': 1 - threshold - max_growth,
        'recorded_prefixes_crossing': sum(session['first_observed_crossing'][str(threshold)]
                                          is not None for session in sessions),
        'unobserved_beyond_recorded_prefix': sum(session['first_observed_crossing'][str(threshold)]
                                                is None for session in sessions),
        'live_task_acceptance_available': False if threshold > 0.65 else True,
    } for threshold in (0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.93)]
    assert config.THRESHOLD == 0.65
    assert 1 - config.THRESHOLD > max_growth > 1 - 0.93
    report = {
        'evidence_checks_passed': True, 'inspected_receipts': len(sessions),
        'completed_task_receipts': sum(s['task_status'] == 'completed_task_relays' for s in sessions),
        'failed_task_receipts': sum(s['task_status'] == 'failed_task_relays' for s in sessions),
        'selected_threshold': config.THRESHOLD, 'model': config.MODEL,
        'current_model_source_sessions': len(sol),
        'largest_observed_sol_snapshot_growth_fraction': max_growth,
        'largest_observed_sol_fire_utilization': max(s['fire_utilization'] for s in sol),
        'decision': 'Retain 0.65. Higher thresholds have no live acceptance evidence; '
                    '0.75 and above leave less nominal headroom than one observed Sol snapshot jump.',
        'limits': ['This is an initial calibration decision, not an optimal threshold or a safety guarantee.',
                   'Stopped rollouts cannot reveal later crossings or outcomes at higher thresholds.',
                   'Snapshot growth is observed context change, not continuous peak occupancy.',
                   'The 17 passing-task receipts include different executor models; Sol has three.'],
        'candidates': candidates, 'sessions': sessions,
    }
    out = Path(__file__).with_name('threshold-review.json')
    out.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'sessions'}, indent=2))


if __name__ == '__main__':
    main()
