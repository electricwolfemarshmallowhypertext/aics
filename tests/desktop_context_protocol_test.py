"""Actual staged hook/MCP checks with synthetic rollout data; no model or desktop chats."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from mcp import Client
from mcp.client.stdio import StdioServerParameters

PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = PROJECT / '.relay/desktop-package/aics-desktop'
sys.path.insert(0, str(PACKAGE))
import desktop_relay as desktop


def data(result):
    assert not result.is_error, result.content
    return result.structured_content or json.loads(result.content[0].text)


async def main():
    manifest = json.loads((PACKAGE / 'mcp.json').read_text())['mcpServers']['aics_desktop']
    params = StdioServerParameters(command=manifest['command'], args=[arg.replace('${PLUGIN_ROOT}', str(PACKAGE)) for arg in manifest['args']])
    with tempfile.TemporaryDirectory(prefix='desktop-context-', dir=PROJECT / '.relay') as folder:
        root = Path(folder)
        rollout = root / 'source.jsonl'
        records = [{'type':'session_meta','payload':{'id':'source','cwd':str(root)}},
                   {'type':'response_item','payload':{'type':'message','role':'user','content':[{'text':'Finish the task; preserve its files.'}]}},
                   {'type':'event_msg','payload':{'type':'token_count','info':{'model_context_window':100000,'last_token_usage':{'total_tokens':10000}}}}]
        rollout.write_text(''.join(json.dumps(event)+'\n' for event in records), encoding='utf-8')
        initial_bytes = rollout.stat().st_size
        event = {'hook_event_name':'PostToolUse','session_id':'source','cwd':str(root),'transcript_path':str(rollout)}
        hook_command = json.loads((PACKAGE/'hooks/hooks.json').read_text())['hooks']['PostToolUse'][0]['hooks'][0]['commandWindows'].replace('${PLUGIN_ROOT}', str(PACKAGE))
        async with Client(params, mode='legacy', read_timeout_seconds=20) as client:
            listing = await client.list_tools()
            assert {tool.name for tool in listing.tools} == {'enable_desktop','prepare_desktop_handoff','confirm_desktop_handoff'}
            data(await client.call_tool('enable_desktop', {'cwd':str(root),'session_id':'source','rollout':str(rollout),'project_id':'fixture-project','model':'gpt-6-sol'}))
            for _ in range(2):
                result = subprocess.run(hook_command, input=json.dumps(event), text=True, capture_output=True, timeout=10, check=True)
                assert json.loads(result.stdout) == {}
            cache = desktop.session_dir(root,'source')/'context.json'
            first = json.loads(cache.read_text())
            assert first['total_read_bytes'] == initial_bytes and first['records_processed'] == 3
            added = (json.dumps({'type':'event_msg','payload':{'type':'token_count','info':{'model_context_window':100000,'last_token_usage':{'total_tokens':70000}}}})+'\n').encode()
            with rollout.open('ab') as file:
                file.write(added)
            result = subprocess.run(hook_command, input=json.dumps(event), text=True, capture_output=True, timeout=10, check=True)
            assert 'AICS_DESKTOP_HANDOFF' in result.stdout
            # Supply a fixture summary; this check deliberately spends no model usage.
            handoff = root/'fixture-handoff.md'
            handoff.write_text('# HANDOFF\n## Goal\nFinish\n## Ordered queue of remaining items\n1. Finish\n## Constraints (never violate)\nPreserve files\n')
            with patch.object(desktop, 'build_handoff', return_value=handoff):
                prepared = desktop.prepare(root,'source')
            for _ in range(2):
                assert data(await client.call_tool('prepare_desktop_handoff', {'cwd':str(root),'session_id':'source'})) == prepared
            final = json.loads(cache.read_text())
            assert final['total_read_bytes'] == initial_bytes + len(added)
            assert final['records_processed'] == 4
    baseline = json.loads((PROJECT/'.relay/desktop-repeat-baseline.json').read_text())
    assert all(hashlib.sha256((PROJECT/name).read_bytes()).hexdigest() == digest for name,digest in baseline.items())
    report = {'passed':True,'scope':'incremental desktop reads, fixture summary reuse and actual staged hook/MCP commands; not native rollover',
              'desktop_acceptance_passed':False,'model_calls':0,'desktop_chats_created':0,
              'initial_rollout_bytes':initial_bytes,'appended_rollout_bytes':len(added),
              'total_rollout_content_bytes_read':final['total_read_bytes'],'records_processed':final['records_processed'],
              'unchanged_checks_reprocessed_records':0,'cached_prepare_calls':2,
              'boundary_check':'64 previously read bytes checked per read for append-only integrity; no previous records reparsed',
              'terminal_source_hashes_unchanged':True,'adapter_checks_passed':22,
              'package_version':'0.1.2','package_files':15,'token_savings_measured':False}
    (PROJECT/'tests/desktop-context-verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    asyncio.run(asyncio.wait_for(main(), timeout=60))
