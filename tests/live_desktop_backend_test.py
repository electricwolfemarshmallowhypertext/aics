"""Real adapter rehearsal through MCP and isolated Codex CLI sessions, not desktop UI."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

from mcp import Client
from mcp.client.stdio import StdioServerParameters

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import config
from process_control import spawn_owned, stop_owned
from handoff_check import verify


THRESHOLD = 0.01


def data(result):
    if result.is_error:
        raise RuntimeError(str(result.content))
    return result.structured_content or json.loads(result.content[0].text)


def execute(workspace, runtime, prompt, label):
    env = dict(os.environ, CODEX_HOME=str(runtime))
    proc = spawn_owned([config.CODEX_EXECUTABLE, 'exec', '--json', '--model', config.MODEL,
                        '--approve-for-me', '-'], cwd=workspace, env=env,
                       stdin=__import__('subprocess').PIPE, stdout=__import__('subprocess').PIPE,
                       stderr=__import__('subprocess').PIPE, text=True, encoding='utf-8')
    try:
        out, err = proc.communicate(prompt, timeout=240)
        (workspace / f'{label}.stdout.jsonl').write_text(out, encoding='utf-8')
        (workspace / f'{label}.stderr.log').write_text(err, encoding='utf-8')
        if proc.returncode:
            raise RuntimeError(f'{label} exited {proc.returncode}; inspect its stderr log')
        events = [json.loads(line) for line in out.splitlines() if line.strip()]
        session_id = next(e['thread_id'] for e in events if e.get('type') == 'thread.started')
        rollouts = list((runtime/'sessions').rglob(f'rollout-*{session_id}.jsonl'))
        assert len(rollouts) == 1, rollouts
        return session_id, rollouts[0]
    finally:
        stop_owned(proc)


async def main():
    workspace = PROJECT / 'tests' / f'desktop-backend-run-{time.time_ns()}'
    workspace.mkdir()
    runtime = workspace / '.codex-runtime'
    runtime.mkdir()
    auth = config.CODEX_HOME / 'auth.json'
    if not auth.exists():
        raise RuntimeError('Existing file-based Codex login unavailable; no login/global settings modified')
    # Isolate CLI writes from the user's global Codex home. Remove the temporary
    # credential copy even when the rehearsal fails; never include it in evidence.
    shutil.copyfile(auth, runtime/'auth.json')
    report = {'passed': False, 'scope': 'real MCP + real CLI backend rehearsal; not desktop rollover',
              'controlled_threshold': THRESHOLD, 'production_threshold': config.THRESHOLD,
              'global_settings_changed': False, 'desktop_tasks_created': 0}
    print(json.dumps({'workspace':str(workspace), 'stage':'source session'}), flush=True)
    try:
        (workspace/'checkpoints').mkdir()
        terminal_handoff = b'TERMINAL_HANDOFF_MUST_REMAIN_UNCHANGED\n'
        (workspace/'HANDOFF.md').write_bytes(terminal_handoff)
        truths = []
        for item in (1,2):
            rows=[{'amount':n, 'expected':n+(n%7==0)} for n in range(1,31)]
            raw=''.join(json.dumps(row)+'\n' for row in rows).encode()
            path=workspace/f'item-{item:02}.jsonl'
            path.write_bytes(raw)
            truths.append({'item':item,'mismatches':4,'sha256':hashlib.sha256(raw).hexdigest()})
        goal=('Audit item-01.jsonl and item-02.jsonl in ascending order. '
              'For each, calculate SHA256 of the raw file and count amount != expected. '
              'Save {item, mismatches, sha256} as checkpoints/item-NN.json atomically and reread it. The item field MUST be the integer 1 or 2, never a string or filename. '
              'Never rewrite an existing checkpoint or input file. After both items, write DONE.txt containing ADAPTER_COMPLETE. '
              'Work only in this directory. SOURCE SESSION ONLY: process item 1 and stop for the AICS handoff. '
              'THE REPLACEMENT SESSION must process item 2 and write DONE.txt.')
        (workspace/'TASK.md').write_text(goal,encoding='utf-8')
        source, source_rollout = await asyncio.to_thread(execute, workspace, runtime, goal, 'source')
        first=(workspace/'checkpoints/item-01.json').read_bytes()
        assert json.loads(first)==truths[0]
        assert not (workspace/'checkpoints/item-02.json').exists()
        (workspace/'source.identity.json').write_text(json.dumps({'session_id':source,'rollout':str(source_rollout)}))
        launcher=(f"import sys; sys.path.insert(0,{str(PROJECT)!r}); import config; config.THRESHOLD={THRESHOLD}; "
                  "import desktop_mcp; desktop_mcp.server.run(transport='stdio')")
        params=StdioServerParameters(command=sys.executable,args=['-B','-c',launcher],env=dict(os.environ,CODEX_HOME=str(runtime)))
        async with Client(params,mode='legacy',read_timeout_seconds=300) as client:
            await client.list_tools()
            enabled=data(await client.call_tool('enable_desktop',{'cwd':str(workspace),'session_id':source,
                'rollout':str(source_rollout),'project_id':'bcac5bfa-efad-4f83-b9b4-994868e09b54','model':config.MODEL}))
            assert enabled['enabled']
            hook_script=(f"import sys,json; sys.path.insert(0,{str(PROJECT)!r}); import config; config.THRESHOLD={THRESHOLD}; "
                         "import desktop_relay; desktop_relay.main()")
            import subprocess
            hook=subprocess.run([sys.executable,'-B','-c',hook_script],input=json.dumps({'hook_event_name':'Stop',
                'cwd':str(workspace),'session_id':source,'transcript_path':str(source_rollout)}),
                text=True,capture_output=True,check=True,timeout=30)
            trigger=json.loads(hook.stdout)
            assert trigger.get('decision')=='block', trigger
            print('Real source checkpoint and occupancy trigger verified; generating handoff through MCP',flush=True)
            prepared=data(await client.call_tool('prepare_desktop_handoff',{'cwd':str(workspace),'session_id':source}))
            assert not verify(prepared['handoff'],workspace)
            assert not prepared['verification_errors']
            print('Real generated handoff verified; starting isolated replacement CLI session',flush=True)
            replacement, replacement_rollout = await asyncio.to_thread(execute,workspace,runtime,prepared['prompt'],'replacement')
            assert replacement != source
            receipt=data(await client.call_tool('confirm_desktop_handoff',{'cwd':str(workspace),'source_session_id':source,
                'replacement_session_id':replacement}))
            assert receipt['outcome']=='relay_started'
            again=data(await client.call_tool('confirm_desktop_handoff',{'cwd':str(workspace),'source_session_id':source,
                'replacement_session_id':replacement}))
            assert again==receipt
        assert (workspace/'HANDOFF.md').read_bytes()==terminal_handoff
        assert (workspace/'checkpoints/item-01.json').read_bytes()==first
        for item,truth in enumerate(truths,1):
            assert json.loads((workspace/f'checkpoints/item-{item:02}.json').read_text())==truth
            assert hashlib.sha256((workspace/f'item-{item:02}.jsonl').read_bytes()).hexdigest()==truth['sha256']
        assert (workspace/'DONE.txt').read_text().strip()=='ADAPTER_COMPLETE'
        assert len((workspace/'receipts.jsonl').read_text().splitlines())==1
        report.update(passed=True,source_session_id=source,replacement_session_id=replacement,
                      actual_handoff=str(prepared['handoff']),original_checkpoint_preserved=True,
                      completed_checkpoints=2,inputs_preserved=True,completion_marker='ADAPTER_COMPLETE',
                      real_mcp_prepare_and_confirm=True,terminal_handoff_preserved=True,duplicate_confirmation_did_not_duplicate_receipt=True)
    except Exception as error:
        report['error']=f'{type(error).__name__}: {error}'
        raise
    finally:
        for name in ('auth.json','auth.json.lock'):
            (runtime/name).unlink(missing_ok=True)
        (workspace/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report),flush=True)


if __name__=='__main__':
    asyncio.run(main())
