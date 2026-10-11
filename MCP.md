# MCP handoff wrapper

The server exposes two tools for the configured project:

- `read_handoff`: reads `HANDOFF.md`, the original goal and verification results.
- `write_handoff(goal, handoff)`: checks the handoff, then saves it and the original goal.

It reuses the existing handoff format, verifier and file writer. Audit projects
receive the existing count/hash/queue checks; other projects receive format
checks. Arbitrary prose is not certified. The tools do not start model sessions.

The official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
handles the stdio protocol. No API key is needed for this file wrapper.

Install in a project-local environment:

```powershell
python -m venv E:/aics/.relay/mcp-venv
E:/aics/.relay/mcp-venv/Scripts/python.exe -m pip install -r E:/aics/requirements-mcp.txt
```

Use this configuration in an MCP client, changing `--cwd` to the project whose
handoff you want to share. The executable below is already installed locally.

```json
{
  "mcpServers": {
    "aics-handoff": {
      "command": "E:/aics/.relay/mcp-venv/Scripts/python.exe",
      "args": ["E:/aics/mcp_server.py", "--cwd", "E:/aics"]
    }
  }
}
```

Read the saved handoff from the receiving tool. To hand work to another tool,
call `write_handoff` with the original goal and Markdown containing `# HANDOFF`,
`## Goal`, `## Ordered queue of remaining items` and
`## Constraints (never violate)`.

Run the actual stdio client check:

```powershell
E:/aics/.relay/mcp-venv/Scripts/python.exe E:/aics/tests/mcp_test.py
```
