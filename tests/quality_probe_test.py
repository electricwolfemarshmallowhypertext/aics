"""Quality trigger controls: bad completed results, unavailable checks, and recovery."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quality_probe import QualityProbe
import relayd


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.usage = {'occupancy': 1000, 'window': 100000, 'cumulative': 900000}

    def checker(self, body, timeout=10):
        path = self.root / 'check.py'
        path.write_text(body, encoding='utf-8')
        return QualityProbe([sys.executable, str(path)], self.root, timeout)

    def test_pass_fail_and_repeated_failed_sample(self):
        for status in ('pass', 'fail'):
            probe = self.checker('print(' + repr(json.dumps({'status': status, 'sample_id': 'artifact-sha', 'reason': 'count check'})) + ')')
            result = probe.poll('worker', self.usage)
            self.assertEqual(result['status'], status)
            self.assertEqual(result['utilization'], .01)
            self.assertIsNone(probe.poll('replacement', self.usage))

    def test_changed_failure_wording_does_not_relay_same_sample_again(self):
        path = self.root / 'check.py'
        probe = self.checker('print(\'{"status":"fail","sample_id":"same-sha","reason":"first explanation"}\')')
        self.assertEqual(probe.poll('worker', self.usage)['status'], 'fail')
        path.write_text('print(\'{"status":"fail","sample_id":"same-sha","reason":"different explanation"}\')')
        self.assertIsNone(probe.poll('replacement', self.usage))

    def test_missing_incomplete_malformed_and_nonzero_are_not_failures(self):
        for body in ('print(\'{"status":"not_ready"}\')', 'print("bad json")',
                     'raise SystemExit(1)', 'print(\'{"status":"fail"}\')'):
            probe = self.checker(body)
            result = probe.poll('worker', self.usage)
            self.assertTrue(result is None or result['status'] == 'unavailable')

    def test_timeout_is_unavailable_and_child_reaped(self):
        probe = self.checker('import time; time.sleep(60)', timeout=.1)
        result = probe.poll('worker', self.usage)
        self.assertEqual(result['status'], 'unavailable')

    def test_quality_failure_relays_below_occupancy_and_does_not_repeat(self):
        probe = self.checker('print(\'{"status":"fail","sample_id":"bad-result","reason":"wrong answer"}\')')
        original, fresh = Mock(), Mock()
        original._relay_identity.get.return_value = 'original'
        original.poll.return_value = None
        fresh._relay_identity.get.return_value = 'fresh'
        fresh.poll.return_value = 0
        fresh.returncode = 0
        path = self.root / 'HANDOFF.md'
        path.write_text('# HANDOFF\n## Goal\nFix result\n## Ordered queue of remaining items\n1. Fix result\n## Constraints (never violate)\nPreserve inputs\n')
        with patch.object(relayd, 'spawn', side_effect=[original, fresh]) as spawn, \
             patch.object(relayd, 'bind_rollout'), patch.object(relayd, 'kill'), \
             patch.object(relayd, 'stop_owned'), patch.object(relayd.time, 'sleep'), \
             patch.object(relayd, 'read_token_count', return_value=self.usage), \
             patch.object(relayd, 'build_handoff', return_value=path), \
             patch.object(relayd.receipts, 'log') as log:
            relayd.run('Fix result', self.root, probe.command)
        self.assertEqual(spawn.call_count, 2)
        receipt = log.call_args.args[0]
        self.assertEqual(receipt['trigger_reason'], 'quality')
        self.assertEqual(receipt['utilization'], .01)
        self.assertIn('wrong answer', spawn.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
