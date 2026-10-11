"""Check the wrapper through two real, independent stdio MCP connections."""
import asyncio
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
import time

from mcp import Client
from mcp.client.stdio import StdioServerParameters


def data(result):
    return result.structured_content or json.loads(result.content[0].text)


async def main():
    project = Path(__file__).resolve().parents[1]
    root = project / 'tests' / f'mcp-run-{time.time_ns()}'
    root.mkdir()
    params = StdioServerParameters(command=sys.executable,
                                   args=[str(project/'mcp_server.py'), '--cwd', str(root)])
    goal = 'Finish the app; preserve the public API and caf\u00e9 text.'
    handoff = ('# HANDOFF\n## Goal\nFinish the app.\n'
               '## Ordered queue of remaining items\n1. Continue the implementation.\n'
               '## Constraints (never violate)\nPreserve the public API and caf\u00e9 text.\n')
    # A separate process and real protocol handshake, not an in-process call.
    async with Client(params, mode='legacy', read_timeout_seconds=20) as client:
        listing = await client.list_tools()
        assert {tool.name for tool in listing.tools} == {'read_handoff', 'write_handoff'}
        missing = await client.call_tool('read_handoff')
        assert missing.is_error
        invalid = await client.call_tool('write_handoff', {'goal':goal, 'handoff':'Wrong format'})
        assert invalid.is_error
        assert not (root/'HANDOFF.md').exists() and not (root/'.relay/goal.txt').exists()
        saved = await client.call_tool('write_handoff', {'goal':goal, 'handoff':handoff})
        assert not saved.is_error and data(saved)['saved']
        loaded = await client.call_tool('read_handoff')
        assert not loaded.is_error
        assert data(loaded)['goal']==goal
        assert data(loaded)['handoff']==handoff
        assert data(loaded)['verification_errors']==[]
        assert data(loaded)['verification_scope']=='handoff_format_only'
    # A receiving client launches its own server and reads the same portable files.
    async with Client(params, mode='auto', read_timeout_seconds=20) as client:
        await client.list_tools()
        loaded = await client.call_tool('read_handoff')
        assert not loaded.is_error and data(loaded)['handoff']==handoff
        assert data(loaded)['goal']==goal
        fixture = root/'item-001.jsonl'
        fixture.write_text('{"amount":1,"expected":2}\n', encoding='utf-8')
        (root/'checkpoints').mkdir()
        checkpoint = {'item':1, 'mismatches':1, 'sha256':hashlib.sha256(fixture.read_bytes()).hexdigest()}
        (root/'checkpoints/item-001.json').write_text(json.dumps(checkpoint), encoding='utf-8')
        false_claim = handoff.replace('Finish the app.', 'item 1: 99 mismatches.')
        refused = await client.call_tool('write_handoff', {'goal':'Changed goal', 'handoff':false_claim})
        assert refused.is_error
        assert (root/'HANDOFF.md').read_text(encoding='utf-8')==handoff
        assert (root/'.relay/goal.txt').read_text(encoding='utf-8')==goal
        (root/'item-002.jsonl').write_text('{"amount":2,"expected":2}\n', encoding='utf-8')
        wrong_queue = await client.call_tool('write_handoff', {'goal':goal, 'handoff':handoff})
        assert wrong_queue.is_error
        assert (root/'HANDOFF.md').read_text(encoding='utf-8')==handoff
        correct_queue = handoff.replace('1. Continue the implementation.', '1. item 2 - finish the remaining case.')
        saved = await client.call_tool('write_handoff', {'goal':goal, 'handoff':correct_queue})
        assert not saved.is_error
        loaded = await client.call_tool('read_handoff')
        assert not loaded.is_error
        assert data(loaded)['verification_scope']=='audit_counts_hashes_queue'
        assert data(loaded)['verification_errors']==[]
        assert data(loaded)['handoff']==correct_queue
    report = {'passed':True, 'sdk_version':version('mcp'), 'transport':'stdio',
              'independent_clients':2, 'legacy_handshake':True, 'modern_connection':True,
              'portable_goal_and_handoff':True, 'unicode_preserved':True,
              'invalid_format_rejected':True, 'false_audit_claim_rejected':True,
              'wrong_audit_queue_rejected':True, 'rejected_write_preserved_files':True,
              'client_shutdown_completed':True}
    (root/'verification.json').write_text(json.dumps(report,indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'workspace':str(root), **report}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
