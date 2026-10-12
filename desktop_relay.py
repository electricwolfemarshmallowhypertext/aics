"""Desktop-only hook and handoff adapter. Never controls or kills processes."""
import hashlib
import json
from pathlib import Path
import sys

import config
from handoff import atomic_write, boot_prompt, build_handoff
from handoff_check import verify, verification_scope
from desktop_context import read_context
import receipts

NOTICE = 'AICS_DESKTOP_HANDOFF: '


def user_messages(events):
    messages = []
    for event in events:
        payload = event.get('payload', {})
        if event.get('type') == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'user':
            text = '\n'.join(part.get('text', '') for part in payload.get('content', []) if isinstance(part, dict))
            if (text and not text.startswith(NOTICE) and text != boot_prompt()
                    and not text.startswith(('AICS_DESKTOP_CONTINUATION:', '<environment_context>'))):
                messages.append(text)
    return messages


def messages_hash(messages):
    return hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode('utf-8')).hexdigest()


def session_dir(cwd, session_id):
    if not session_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in session_id):
        raise ValueError('Invalid desktop session ID')
    return Path(cwd).resolve() / '.relay' / 'desktop' / session_id


def enable(cwd, session_id, rollout, project_id, model):
    directory = session_dir(cwd, session_id)
    if not read_context(rollout, session_id, cwd, directory, user_messages):
        raise ValueError('Source rollout metadata is not available')
    if not project_id or not model:
        raise ValueError('Desktop project and model are required')
    directory = session_dir(cwd, session_id)
    atomic_write(directory / 'enabled.json', json.dumps({'project_id': project_id, 'model': model}))
    return {'enabled': True, 'session_id': session_id, 'threshold': config.THRESHOLD}


def hook(event):
    root = Path(event['cwd']).resolve()
    session_id = event['session_id']
    directory = session_dir(root, session_id)
    if event.get('hook_event_name') == 'SessionStart':
        return {'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext':
                'AICS desktop session metadata: ' + json.dumps({'session_id': session_id, 'cwd': str(root), 'rollout': event.get('transcript_path'),
                                                              'model': event.get('model'), 'permission_mode': event.get('permission_mode'),
                                                              'enabled': (directory / 'enabled.json').exists()}) +
                '. Enable only with the human\'s explicit authorization for automatic replacement tasks.'}}
    if not (directory / 'enabled.json').exists() or (directory / 'confirmed.json').exists():
        return {}
    if event.get('stop_hook_active') or event.get('hook_event_name') not in ('PostToolUse', 'Stop'):
        return {}
    pending = directory / 'pending.json'
    if pending.exists():
        if event['hook_event_name'] != 'Stop':
            return {}
    else:
        rollout = event.get('transcript_path')
        if not rollout:
            return {}
        state = read_context(rollout, session_id, root, directory, user_messages)
        usage = state['usage'] if state else None
        if not usage or usage['occupancy'] / usage['window'] < config.THRESHOLD:
            return {}
        measurement = {'fire': True, 'session_id': session_id, 'session_rollout': str(rollout),
                       **usage, 'utilization': usage['occupancy'] / usage['window'], 'trigger_reason': 'occupancy'}
        atomic_write(pending, json.dumps({'rollout': rollout, 'measurement': measurement}))
    boundary = 'The current turn has finished' if event['hook_event_name'] == 'Stop' else 'The current tool operation has finished'
    instruction = (NOTICE + boundary + ' and the configured context threshold was reached. '
                   'Use the aics-desktop skill now. Do not start further project edits in this source chat. '
                   f'Source session: {session_id}. Workspace: {root}. '
                   'If handoff preparation or native thread creation fails, report the error; do not claim a transfer.')
    if event['hook_event_name'] == 'Stop':
        return {'decision': 'block', 'reason': instruction}
    return {'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': instruction}}


def prepare(cwd, session_id):
    root = Path(cwd).resolve()
    directory = session_dir(root, session_id)
    settings = json.loads((directory / 'enabled.json').read_text(encoding='utf-8'))
    pending = json.loads((directory / 'pending.json').read_text(encoding='utf-8'))
    measurement = pending['measurement']
    state = read_context(pending['rollout'], session_id, root, directory, user_messages)
    if (measurement['session_id'] != session_id or not measurement.get('fire')
            or measurement['occupancy'] / measurement['window'] < config.THRESHOLD):
        raise ValueError('Invalid desktop trigger measurement')
    if not state or [measurement['occupancy'], measurement['window']] not in state['snapshots']:
        raise ValueError('Desktop trigger snapshot missing from source rollout')
    messages = state['messages']
    inherited = directory / 'goal.txt'
    goal = inherited.read_text(encoding='utf-8') if inherited.exists() else ''
    goal = '\n\n'.join([goal, *messages]).strip()
    if not goal:
        raise ValueError('No original user goal is available')
    messages_sha256 = messages_hash(messages)
    prepared = directory / 'prepared.json'
    if prepared.exists():
        result = json.loads(prepared.read_text(encoding='utf-8'))
        if hashlib.sha256(Path(result['handoff']).read_bytes()).hexdigest() != result.get('handoff_sha256'):
            raise ValueError('Handoff changed after preparation')
        if result.get('source_messages_sha256') != messages_sha256:
            raise ValueError('Source instructions changed or preparation is from an older adapter; refusing the cached handoff')
        return result
    transcript = directory / 'context-tail.jsonl'
    atomic_write(transcript, '\n'.join(state['tail']) + '\n')
    errors = []
    for attempt in (1, 2):
        handoff = build_handoff(transcript, root, goal, output_dir=directory, timeout=120)
        errors = verify(handoff, root)
        if not errors:
            break
    if errors:
        raise ValueError('Refusing invalid desktop handoff: ' + '; '.join(errors))
    saved = directory / 'HANDOFF.md'
    atomic_write(saved, handoff.read_text(encoding='utf-8'))
    atomic_write(directory / 'goal.txt', goal)
    prompt = ('AICS_DESKTOP_CONTINUATION: Continue the original user task in this same project. '
              f'Read the verified handoff at {saved}. Verify result claims against saved files. '
              'Do not repeat completed work. Preserve the original constraints and finish the remaining task. '
              'Existing native memory settings should remain unchanged.\n\nORIGINAL USER GOAL:\n' + goal)
    result = {'source_session_id': session_id, 'project_id': settings['project_id'],
              'model': settings['model'], 'prompt': prompt, 'handoff': str(saved),
              'handoff_sha256': hashlib.sha256(saved.read_bytes()).hexdigest(),
              'source_messages_sha256': messages_sha256,
              'verification_scope': verification_scope(root), 'verification_errors': [],
              'measurement': measurement}
    atomic_write(prepared, json.dumps(result))
    return result


def confirm(cwd, source_session_id, replacement_session_id, replacement_rollout):
    root = Path(cwd).resolve()
    directory = session_dir(root, source_session_id)
    if replacement_session_id == source_session_id:
        raise ValueError('Replacement must be a fresh native thread')
    prepared = json.loads((directory / 'prepared.json').read_text(encoding='utf-8'))
    if hashlib.sha256(Path(prepared['handoff']).read_bytes()).hexdigest() != prepared.get('handoff_sha256'):
        raise ValueError('Handoff changed after preparation')
    target = session_dir(root, replacement_session_id)
    state = read_context(replacement_rollout, replacement_session_id, root, target, user_messages)
    supplied = state['continuation_prompts'] if state else []
    if prepared['prompt'].replace('\r\n', '\n') not in [text.replace('\r\n', '\n') for text in supplied]:
        raise ValueError('Replacement rollout does not contain the verified continuation prompt')
    if (directory / 'confirmed.json').exists():
        previous = json.loads((directory / 'confirmed.json').read_text(encoding='utf-8'))
        if previous['replacement_session_id'] != replacement_session_id:
            raise ValueError('Source already transferred to another thread')
        return previous
    source = read_context(prepared['measurement']['session_rollout'], source_session_id, root, directory, user_messages)
    if not source or messages_hash(source['messages']) != prepared.get('source_messages_sha256'):
        raise ValueError('Source instructions changed or preparation is from an older adapter; refusing confirmation')
    atomic_write(target / 'goal.txt', (directory / 'goal.txt').read_text(encoding='utf-8'))
    atomic_write(target / 'enabled.json', (directory / 'enabled.json').read_text(encoding='utf-8'))
    receipt = {**prepared['measurement'], 'desktop': True, 'handoff': prepared['handoff'],
               'replacement_session_id': replacement_session_id, 'outcome': 'relay_started',
               'verification_scope': prepared['verification_scope'], 'verification_errors': []}
    receipts.log(receipt, path=root / 'receipts.jsonl')
    atomic_write(directory / 'confirmed.json', json.dumps(receipt))
    return receipt


def main():
    try:
        result = hook(json.load(sys.stdin))
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'systemMessage': 'AICS desktop hook failed: ' + str(error)}
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
