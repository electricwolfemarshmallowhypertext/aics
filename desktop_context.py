"""Persist desktop context and read only complete records appended since the last read."""
import json
import os
import time
from pathlib import Path

import config
from handoff import atomic_write
from tailer import parse_token_count


def read_context(rollout, session_id, cwd, directory, extract_messages):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'context.lock').open('a+b') as lock:
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b'0')
            lock.flush()
        deadline = time.monotonic() + 5
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError('Desktop context is busy; saved read position was not changed')
                time.sleep(0.05)
        try:
            return _read_context(rollout, session_id, cwd, directory, extract_messages)
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read_context(rollout, session_id, cwd, directory, extract_messages):
    path = Path(rollout).resolve()
    cache = directory / 'context.json'
    state = json.loads(cache.read_text(encoding='utf-8')) if cache.exists() else {
        'rollout': str(path), 'offset': 0, 'messages': [], 'snapshots': [],
        'tail': [], 'continuation_prompts': [], 'usage': None, 'total_read_bytes': 0, 'records_processed': 0}
    if state['rollout'] != str(path):
        raise ValueError('Desktop source rollout path changed')
    with path.open('rb') as file:
        stat = os.fstat(file.fileno())
        identity = [stat.st_dev, stat.st_ino]
        if state.get('identity', identity) != identity or stat.st_size < state['offset']:
            raise ValueError('Desktop rollout was replaced or truncated')
        if state['offset']:
            file.seek(max(0, state['offset'] - 64))
            boundary = file.read(min(64, state['offset'])).hex()
            if boundary != state['boundary']:
                raise ValueError('Desktop rollout changed before the saved read position')
        file.seek(state['offset'])
        new_records = []
        read_bytes = 0
        while True:
            start = file.tell()
            raw = file.readline()
            read_bytes += len(raw)
            if not raw or not raw.endswith(b'\n'):
                file.seek(start)
                break
            try:
                event = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as error:
                raise ValueError('Corrupt completed desktop rollout record') from error
            if state['offset'] == 0:
                meta = event.get('payload') or {}
                if event.get('type') != 'session_meta' or meta.get('id') != session_id:
                    raise ValueError('Native relay source session identity mismatch')
                if Path(meta.get('cwd', '')).resolve() != Path(cwd).resolve():
                    raise ValueError('Native relay source workspace mismatch')
                state['metadata'] = meta
            state['offset'] = file.tell()
            new_records.append(event)
            state['tail'].append(raw.decode('utf-8').rstrip('\r\n'))
            usage = parse_token_count(raw)
            if usage:
                state['usage'] = usage
                snapshot = [usage['occupancy'], usage['window']]
                if snapshot not in state['snapshots']:
                    state['snapshots'].append(snapshot)
            state['records_processed'] += 1
        if not state.get('metadata'):
            return None
        file.seek(max(0, state['offset'] - 64))
        state['boundary'] = file.read(min(64, state['offset'])).hex()
        state['identity'] = identity
    state['messages'].extend(extract_messages(new_records))
    state['continuation_prompts'].extend(part.get('text', '') for event in new_records
        if event.get('type') == 'response_item' and event.get('payload', {}).get('role') == 'user'
        for part in event['payload'].get('content', [])
        if isinstance(part, dict) and part.get('text', '').startswith('AICS_DESKTOP_CONTINUATION:'))
    tail = state['tail'][-config.TRANSCRIPT_TAIL_LINES:]
    sizes = [len(line.encode('utf-8')) + 1 for line in tail]
    total = sum(sizes)
    first = 0
    while first < len(tail) and total > config.TRANSCRIPT_TAIL_CHARS:
        total -= sizes[first]
        first += 1
    state['tail'] = tail[first:]
    state['last_read_bytes'] = read_bytes
    state['total_read_bytes'] += read_bytes
    atomic_write(cache, json.dumps(state, ensure_ascii=False))
    return state
