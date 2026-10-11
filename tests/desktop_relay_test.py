"""Desktop adapter checks. Native desktop execution is a separate live gate."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import desktop_relay as desktop


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rollout = self.root / 'source.jsonl'
        self.events = [
            {'type': 'session_meta', 'payload': {'id': 'source', 'cwd': str(self.root)}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'text': 'Finish the original app; preserve the public API.'}]}},
            {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'model_context_window': 100000, 'last_token_usage': {'total_tokens': 70000}, 'total_token_usage': {'total_tokens': 900000}}}},
        ]
        self.rollout.write_text(''.join(json.dumps(e)+'\n' for e in self.events), encoding='utf-8')
        self.event = {'hook_event_name': 'PostToolUse', 'session_id': 'source', 'cwd': str(self.root), 'transcript_path': str(self.rollout)}

    def enable(self):
        desktop.enable(self.root, 'source', self.rollout, 'project-id', 'gpt-6-sol')

    def prepare(self):
        self.enable()
        desktop.hook(self.event)
        handoff = self.root / 'HANDOFF.md'
        handoff.write_text('# HANDOFF\n## Goal\nFinish the app\n## Ordered queue of remaining items\n1. Finish remaining work\n## Constraints (never violate)\nPreserve API\n', encoding='utf-8')
        with patch.object(desktop, 'build_handoff', return_value=handoff):
            return desktop.prepare(self.root, 'source')

    def test_disabled_sessions_and_terminal_are_unchanged(self):
        self.assertEqual(desktop.hook(self.event), {})
        self.assertFalse((self.root / '.relay').exists())
        self.enable()
        other = dict(self.event, session_id='terminal')
        self.assertEqual(desktop.hook(other), {})

    def test_session_metadata_is_not_carried_as_user_task_constraints(self):
        self.events.insert(1, {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                           'content': [{'text': '<environment_context>read-only machine metadata</environment_context>'}]}})
        self.rollout.write_text(''.join(json.dumps(e)+'\n' for e in self.events), encoding='utf-8')
        prepared = self.prepare()
        self.assertNotIn('<environment_context>', prepared['prompt'])
        self.assertIn('preserve the public API', prepared['prompt'])
        output = desktop.hook(dict(self.event, hook_event_name='SessionStart', model='gpt-6-sol', permission_mode='default'))
        context = output['hookSpecificOutput']['additionalContext']
        self.assertIn('"model": "gpt-6-sol"', context)
        self.assertIn('"permission_mode": "default"', context)

    def test_exact_identity_and_path_safety(self):
        with self.assertRaisesRegex(ValueError, 'identity'):
            desktop.enable(self.root, 'wrong', self.rollout, 'project-id', 'gpt-6-sol')
        with self.assertRaisesRegex(ValueError, 'Invalid desktop session'):
            desktop.session_dir(self.root, '../escape')

    def test_trigger_is_occupancy_at_tool_completion_and_once(self):
        self.enable()
        result = desktop.hook(self.event)
        self.assertIn('current tool operation has finished', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(desktop.hook(self.event), {})
        self.assertFalse((self.root / 'receipts.jsonl').exists())

    def test_pending_request_reaches_stop_without_recursion(self):
        self.enable()
        desktop.hook(self.event)
        result = desktop.hook(dict(self.event, hook_event_name='Stop'))
        self.assertEqual(result['decision'], 'block')
        self.assertEqual(desktop.hook(dict(self.event, hook_event_name='Stop', stop_hook_active=True)), {})

    def test_pending_handoff_survives_a_later_compaction_snapshot(self):
        self.enable()
        desktop.hook(self.event)
        self.events.append({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'model_context_window': 100000, 'last_token_usage': {'total_tokens': 10000}}}})
        self.rollout.write_text(''.join(json.dumps(e)+'\n' for e in self.events), encoding='utf-8')
        self.assertEqual(desktop.hook(dict(self.event, hook_event_name='Stop'))['decision'], 'block')
        prepared = self.prepare()
        self.assertEqual(prepared['measurement']['occupancy'], 70000)

    def test_unobserved_trigger_is_rejected(self):
        self.enable()
        desktop.hook(self.event)
        path = desktop.session_dir(self.root, 'source') / 'pending.json'
        pending = json.loads(path.read_text())
        pending['measurement']['occupancy'] = 71000
        path.write_text(json.dumps(pending))
        with self.assertRaisesRegex(ValueError, 'snapshot missing'):
            desktop.prepare(self.root, 'source')

    def test_session_start_reports_actual_enabled_state(self):
        event = dict(self.event, hook_event_name='SessionStart')
        context = desktop.hook(event)['hookSpecificOutput']['additionalContext']
        self.assertIn('"enabled": false', context)
        self.enable()
        context = desktop.hook(event)['hookSpecificOutput']['additionalContext']
        self.assertIn('"enabled": true', context)

    def test_modified_saved_handoff_is_rejected_before_confirmation(self):
        prepared = self.prepare()
        Path(prepared['handoff']).write_text('Corrupted after preparation')
        with self.assertRaisesRegex(ValueError, 'Handoff changed'):
            desktop.confirm(self.root, 'source', 'replacement', self.root/'missing.jsonl')
        self.assertFalse((self.root/'receipts.jsonl').exists())

    def test_new_user_instructions_reject_a_stale_cached_handoff(self):
        self.prepare()
        self.events.append({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                           'content': [{'text': 'Preserve the database too.'}]}})
        self.rollout.write_text(''.join(json.dumps(e)+'\n' for e in self.events), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Source instructions changed'):
            desktop.prepare(self.root, 'source')

    def test_confirmation_allows_replacement_to_complete_the_queue(self):
        for item in (1, 2):
            (self.root / f'item-{item:02}.jsonl').write_text('{"amount":1,"expected":2}\n')
        (self.root / 'checkpoints').mkdir()
        def checkpoint(item):
            record = {'item': item, 'mismatches': 1,
                      'sha256': hashlib.sha256((self.root / f'item-{item:02}.jsonl').read_bytes()).hexdigest()}
            (self.root / f'checkpoints/item-{item:02}.json').write_text(json.dumps(record))
        checkpoint(1)
        self.enable()
        desktop.hook(self.event)
        handoff = self.root / 'HANDOFF.md'
        handoff.write_text('# HANDOFF\n## Goal\nAudit files\n## Ordered queue of remaining items\n1. item 2: Finish audit\n## Constraints (never violate)\nPreserve inputs\n')
        with patch.object(desktop, 'build_handoff', return_value=handoff):
            prepared = desktop.prepare(self.root, 'source')
        checkpoint(2)
        replacement = self.root / 'replacement.jsonl'
        records = [{'type': 'session_meta', 'payload': {'id': 'replacement', 'cwd': str(self.root)}},
                   {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                    'content': [{'text': prepared['prompt']}]}}]
        replacement.write_text(''.join(json.dumps(e)+'\n' for e in records))
        result = desktop.confirm(self.root, 'source', 'replacement', replacement)
        self.assertEqual(result['outcome'], 'relay_started')

    def test_large_cumulative_counter_does_not_trigger(self):
        self.enable()
        self.events[-1]['payload']['info']['last_token_usage']['total_tokens'] = 10000
        self.rollout.write_text(''.join(json.dumps(e)+'\n' for e in self.events), encoding='utf-8')
        self.assertEqual(desktop.hook(self.event), {})

    def test_bad_handoff_does_not_create_transfer_receipt(self):
        self.enable()
        desktop.hook(self.event)
        handoff = self.root / 'HANDOFF.md'
        handoff.write_text('Wrong format')
        with patch.object(desktop, 'build_handoff', return_value=handoff):
            with self.assertRaisesRegex(ValueError, 'Refusing invalid'):
                desktop.prepare(self.root, 'source')
        self.assertFalse((self.root / 'receipts.jsonl').exists())
        self.assertFalse((desktop.session_dir(self.root, 'source') / 'prepared.json').exists())

    def test_confirmation_requires_fresh_exact_prompt_on_disk(self):
        prepared = self.prepare()
        self.assertIn('preserve the public API', prepared['prompt'])
        replacement = self.root / 'replacement.jsonl'
        records = [{'type': 'session_meta', 'payload': {'id': 'replacement', 'cwd': str(self.root)}},
                   {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'text': 'Wrong prompt'}]}}]
        replacement.write_text(''.join(json.dumps(e)+'\n' for e in records))
        with self.assertRaisesRegex(ValueError, 'fresh native thread'):
            desktop.confirm(self.root, 'source', 'source', self.rollout)
        with self.assertRaisesRegex(ValueError, 'continuation prompt'):
            desktop.confirm(self.root, 'source', 'replacement', replacement)
        self.assertFalse((self.root / 'receipts.jsonl').exists())
        records[1]['payload']['content'][0]['text'] = prepared['prompt'].replace('\n', '\r\n')
        replacement.write_text(''.join(json.dumps(e)+'\n' for e in records))
        with patch.object(desktop.receipts, 'RECEIPTS_PATH', self.root / 'receipts.jsonl'):
            result = desktop.confirm(self.root, 'source', 'replacement', replacement)
            self.assertEqual(desktop.confirm(self.root, 'source', 'replacement', replacement), result)
        self.assertEqual(result['outcome'], 'relay_started')
        self.assertEqual(len((self.root / 'receipts.jsonl').read_text().splitlines()), 1)
        self.assertTrue((desktop.session_dir(self.root, 'replacement') / 'enabled.json').exists())


if __name__ == '__main__':
    unittest.main()
