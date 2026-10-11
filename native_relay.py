"""Disk sensor and handoff helper for the native Codex TUI fork."""
import argparse
import json
from pathlib import Path
import signal
import time

import config
from handoff import atomic_write, boot_prompt, build_handoff
from handoff_check import verify, verification_scope
import receipts
from tailer import read_token_count


def source_events(rollout, session_id, cwd):
    lines = Path(rollout).read_text(encoding='utf-8').splitlines(keepends=True)
    events = []
    for index, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            if index != len(lines)-1 or line.endswith('\n'):
                raise
    if not events:
        return []
    meta = events[0]
    if meta['type'] != 'session_meta' or meta['payload']['id'] != session_id:
        raise ValueError('Native relay source session identity mismatch')
    if Path(meta['payload']['cwd']).resolve() != Path(cwd).resolve():
        raise ValueError('Native relay source workspace mismatch')
    return events


def inspect(rollout, session_id, cwd):
    try:
        events = source_events(rollout, session_id, cwd)
    except FileNotFoundError:
        return {'fire': False}
    if not events:
        return {'fire': False}
    usage = read_token_count(rollout)
    if usage is None:
        return {'fire': False}
    return {'fire': usage['occupancy']/usage['window'] >= config.THRESHOLD,
            'session_id': session_id, 'session_rollout': str(rollout), **usage,
            'utilization': usage['occupancy']/usage['window'], 'trigger_reason': 'occupancy'}


def prepare(rollout, session_id, cwd, measurement):
    root = Path(cwd)
    events = source_events(rollout, session_id, root)
    if measurement['session_id'] != session_id or not measurement['fire']:
        raise ValueError('Invalid native relay measurement')
    if not any(event.get('payload', {}).get('type') == 'token_count'
               and (event['payload'].get('info', {}).get('last_token_usage') or {}).get('total_tokens') == measurement['occupancy']
               and event['payload']['info'].get('model_context_window') == measurement['window'] for event in events):
        raise ValueError('Native trigger snapshot missing from source rollout')
    messages = []
    for event in events:
        payload = event.get('payload', {})
        if event.get('type') == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'user':
            text = '\n'.join(part.get('text', '') for part in payload.get('content', []) if isinstance(part, dict))
            if text != boot_prompt():
                messages.append(text)
    inherited = root / '.relay' / 'tui' / session_id / 'goal.txt'
    if inherited.exists():
        goal = inherited.read_text(encoding='utf-8')
        if messages:
            goal += '\n\nCURRENT_SESSION_USER_INSTRUCTIONS:\n' + '\n\n'.join(messages)
    else:
        if not messages:
            raise ValueError('Native session has no saved user instructions')
        goal = '\n\n'.join(messages)
    atomic_write(root / '.relay' / 'goal.txt', goal)
    receipts.RECEIPTS_PATH = root / 'receipts.jsonl'
    try:
        for attempt in (1, 2):
            handoff = build_handoff(rollout, root, goal)
            errors = verify(handoff, root)
            if not errors:
                break
        if errors:
            raise ValueError('Refusing invalid native handoff: ' + '; '.join(errors))
        saved = root / '.relay' / 'handoffs' / f'{session_id}-{time.time_ns()}.md'
        atomic_write(saved, handoff.read_text(encoding='utf-8'))
        receipt = {**measurement, 'native_tui': True, 'handoff': str(saved),
                   'verification_scope': verification_scope(root), 'verification_errors': [],
                   'verification_attempts': attempt, 'outcome': 'handoff_verified'}
    except Exception as error:
        receipts.log({**measurement, 'native_tui': True, 'outcome': 'handoff_failed', 'error': str(error)})
        raise
    goal_file = root / '.relay' / 'tui' / session_id / 'relay-goal.txt'
    atomic_write(goal_file, goal)
    return {'prompt': boot_prompt(), 'goal_file': str(goal_file), 'handoff': str(saved), 'receipt': receipt}


def stop_helper(*_):
    raise KeyboardInterrupt()


def main():
    signal.signal(signal.SIGTERM, stop_helper)
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('inspect', 'prepare'))
    parser.add_argument('--rollout', required=True, type=Path)
    parser.add_argument('--session', required=True)
    parser.add_argument('--cwd', required=True, type=Path)
    parser.add_argument('--measurement')
    args = parser.parse_args()
    result = inspect(args.rollout, args.session, args.cwd) if args.action == 'inspect' else prepare(args.rollout, args.session, args.cwd, json.loads(args.measurement))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
