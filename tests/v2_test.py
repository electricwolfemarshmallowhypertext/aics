"""Regressions for the tech-map relay: occupancy, bounded stdin and safe handoff."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config
import handoff
import relayd
from handoff_check import verify,verification_scope
from tailer import parse_token_count,read_utilization

VALID="# HANDOFF\n## Goal\nAudit disk inputs.\n## Ordered queue of remaining items\n1. item 2: audit and checkpoint.\n## Constraints (never violate)\nPreserve inputs.\n"


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/"TASK.md").write_text("Audit in order. Preserve input bytes.")
        for item in (1,2):
            (self.root/f"item-{item:02}.jsonl").write_text('{"amount":1,"expected":2}\n')
        (self.root/"checkpoints").mkdir()
        self.checkpoint=self.root/"checkpoints/item-01.json"
        self.truth={"item":1,"mismatches":1,"sha256":hashlib.sha256((self.root/"item-01.jsonl").read_bytes()).hexdigest()}
        self.checkpoint.write_text(json.dumps(self.truth))
        self.path=self.root/"HANDOFF.md"

    def test_cumulative_growth_does_not_trigger_occupancy(self):
        event={"payload":{"type":"token_count","info":{
            "total_token_usage":{"total_tokens":196437},
            "last_token_usage":{"total_tokens":33929},"model_context_window":258400}}}
        path=self.root/"rollout.jsonl"
        path.write_text(json.dumps(event)+"\n")
        self.assertEqual(parse_token_count(json.dumps(event))["cumulative"],196437)
        self.assertAlmostEqual(read_utilization(path),33929/258400)
        self.assertLess(read_utilization(path),.65)
        del event["payload"]["info"]["last_token_usage"]
        self.assertIsNone(parse_token_count(json.dumps(event)))

    def test_false_checkpoint_count_hash_and_summary_queue_rejected(self):
        self.path.write_text(VALID)
        self.assertEqual(verify(self.path,self.root),[])
        for fields in ({"mismatches":120},{"sha256":"0"*64}):
            self.checkpoint.write_text(json.dumps({**self.truth,**fields}))
            self.assertTrue(verify(self.path,self.root))
        self.checkpoint.write_text(json.dumps(self.truth))
        self.path.write_text(VALID+"Item 1 has 120 mismatches.\n")
        self.assertTrue(verify(self.path,self.root))
        self.path.write_text(VALID.replace("item 2","item 1"))
        self.assertTrue(verify(self.path,self.root))

    def test_unicode_transcript_is_byte_bounded_and_preserves_task(self):
        rollout=self.root/"large.jsonl"
        rollout.write_text(("🔧"*1000+"\n")*1500,encoding="utf-8")
        proc=Mock(returncode=0)
        def spawned(command,**kwargs):
            Path(command[command.index("--output-last-message")+1]).write_text(VALID)
            return proc
        def communicate(**kwargs):
            payload=kwargs["input"]
            self.assertLessEqual(len(payload.encode("utf-8")),config.TRANSCRIPT_TAIL_CHARS)
            self.assertIn("Preserve input bytes",payload)
            self.assertIn('"remaining": [2]',payload)
            return "",""
        proc.communicate.side_effect=communicate
        with patch.object(handoff,"spawn_owned",side_effect=spawned),patch.object(handoff,"stop_owned"):
            self.assertEqual(handoff.build_handoff(rollout,self.root,"original audit goal"),self.path)

    def run_case(self,summaries,succeeds):
        original,fresh=Mock(),Mock()
        original._relay_identity.get.return_value="original-session"
        fresh._relay_identity.get.return_value="fresh-session"
        original.poll.return_value=None
        fresh.poll.return_value=0
        fresh.returncode=0
        def build(*args):
            self.path.write_text(next(summaries))
            return self.path
        with patch.object(relayd,"spawn",side_effect=[original,fresh]) as spawn, \
             patch.object(relayd,"bind_rollout",return_value=self.root/"rollout.jsonl"), \
             patch.object(relayd.time,"sleep"),patch.object(relayd,"kill"),patch.object(relayd,"stop_owned"), \
             patch.object(relayd,"read_token_count",return_value={"occupancy":170000,"cumulative":900000,"window":258400}), \
             patch.object(relayd,"build_handoff",side_effect=build) as summary,patch.object(relayd.receipts,"log") as log:
            if succeeds:
                relayd.run("audit",self.root)
            else:
                with self.assertRaisesRegex(RuntimeError,"Refusing unverified relay"):
                    relayd.run("audit",self.root)
        return spawn,summary,log

    def test_retry_then_start_fresh_session(self):
        spawn,summary,log=self.run_case(iter([VALID+"Item 1 has 120 mismatches.",VALID]),True)
        self.assertEqual(summary.call_count,2)
        self.assertEqual(spawn.call_count,2)
        self.assertEqual(log.call_args.args[0]["verification_attempts"],2)

    def test_two_rejections_refuse_replacement(self):
        spawn,summary,log=self.run_case(iter([VALID+"Item 1 has 120 mismatches."]*2),False)
        self.assertEqual(summary.call_count,2)
        self.assertEqual(spawn.call_count,1)
        self.assertEqual(log.call_args.args[0]["outcome"],"handoff_failed")

    def test_ordinary_project_needs_no_task_manifest_or_scheduler(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            proc=Mock(returncode=0)
            proc.poll.return_value=0
            with patch.object(relayd,"spawn",return_value=proc) as spawn,patch.object(relayd,"bind_rollout"),patch.object(relayd,"stop_owned"):
                relayd.run("Fix the app",root)
            spawn.assert_called_once_with("Fix the app",root)
            self.assertEqual((root/".relay/goal.txt").read_text(),"Fix the app")
            self.assertFalse((root/"ACTIVE_TASK.json").exists())
            self.assertFalse((root/"RELAY_TASK.json").exists())
            self.assertEqual(verification_scope(root),"handoff_format_only")


if __name__=="__main__":
    unittest.main()

