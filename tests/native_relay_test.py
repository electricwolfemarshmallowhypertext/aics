"""Native bridge checks: ownership, threshold, goal inheritance and verification."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_relay
from handoff import boot_prompt


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'rollout.jsonl'
        self.events = [
            {'type':'session_meta','payload':{'id':'source','cwd':str(self.root)}},
            {'type':'response_item','payload':{'type':'message','role':'user','content':[{'text':'Build the app; preserve inputs.'}]}},
            {'type':'event_msg','payload':{'type':'token_count','info':{'model_context_window':100000,'last_token_usage':{'total_tokens':70000},'total_token_usage':{'total_tokens':900000}}}},
        ]
        self.save()

    def save(self):
        self.path.write_text(''.join(json.dumps(e)+'\n' for e in self.events))

    def test_exact_owner_and_occupancy(self):
        result = native_relay.inspect(self.path,'source',self.root)
        self.assertTrue(result['fire'])
        self.assertEqual(result['utilization'],.7)
        with self.assertRaisesRegex(ValueError,'identity'):
            native_relay.inspect(self.path,'other',self.root)
        with self.assertRaisesRegex(ValueError,'workspace'):
            native_relay.inspect(self.path,'source',self.root/'other')
        self.events[-1]['payload']['info']['last_token_usage']['total_tokens']=10000
        self.save()
        self.assertFalse(native_relay.inspect(self.path,'source',self.root)['fire'])

    def test_inherited_goal_retains_new_user_steering(self):
        inherited=self.root/'.relay/tui/source/goal.txt'
        inherited.parent.mkdir(parents=True)
        inherited.write_text('Original goal')
        self.events[1]['payload']['content']=[{'text':boot_prompt()}]
        self.events.insert(2,{'type':'response_item','payload':{'type':'message','role':'user','content':[{'text':'Also preserve the public API.'}]}})
        self.save()
        handoff=self.root/'HANDOFF.md'
        handoff.write_text('# HANDOFF\n## Goal\nBuild\n## Ordered queue of remaining items\nContinue\n## Constraints (never violate)\nPreserve API\n')
        measurement=native_relay.inspect(self.path,'source',self.root)
        with patch.object(native_relay,'build_handoff',return_value=handoff),patch.object(native_relay.receipts,'log'):
            result=native_relay.prepare(self.path,'source',self.root,measurement)
        goal=Path(result['goal_file']).read_text()
        self.assertIn('Original goal',goal)
        self.assertIn('Also preserve the public API.',goal)
        self.assertNotIn(boot_prompt(),goal)
        self.assertLess(len(json.dumps(result).encode()),4096)
        self.assertTrue(Path(result['handoff']).exists())

    def test_unflushed_initial_rollout_does_not_stop_native_startup(self):
        self.path.write_text('')
        self.assertFalse(native_relay.inspect(self.path,'source',self.root)['fire'])
        self.path.write_text('{"type":"session_meta"')
        self.assertFalse(native_relay.inspect(self.path,'source',self.root)['fire'])
        self.path.unlink()
        self.assertFalse(native_relay.inspect(self.path,'source',self.root)['fire'])

    def test_unobserved_trigger_snapshot_is_rejected(self):
        measurement=native_relay.inspect(self.path,'source',self.root)
        measurement['occupancy']=80000
        with self.assertRaisesRegex(ValueError,'snapshot missing'):
            native_relay.prepare(self.path,'source',self.root,measurement)


if __name__ == '__main__':
    unittest.main()
