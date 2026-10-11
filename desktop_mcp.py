"""Desktop adapter tools; native thread actions remain with Codex app tools."""
import os

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
import desktop_relay
from tailer import bind_rollout

server = MCPServer('aics-desktop')


def run_checked(function, *args):
    try:
        return function(*args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        raise ToolError(str(error)) from error


@server.tool()
def enable_desktop(cwd: str, session_id: str, rollout: str, project_id: str, model: str) -> dict:
    """Enable automatic handoffs only for this exact session after explicit user authorization."""
    return run_checked(desktop_relay.enable, cwd, session_id, rollout, project_id, model)


@server.tool()
def prepare_desktop_handoff(cwd: str, session_id: str) -> dict:
    """Prepare and verify a triggered handoff. This does not create or interrupt any native thread."""
    return run_checked(desktop_relay.prepare, cwd, session_id)


@server.tool()
def confirm_desktop_handoff(cwd: str, source_session_id: str, replacement_session_id: str, replacement_rollout: str | None = None) -> dict:
    """Record a transfer only after the fresh thread's own rollout contains the exact continuation prompt."""
    if replacement_rollout is None:
        replacement_rollout = str(run_checked(bind_rollout, replacement_session_id, cwd, 30, os.environ.get('CODEX_HOME')))
    return run_checked(desktop_relay.confirm, cwd, source_session_id, replacement_session_id, replacement_rollout)


if __name__ == '__main__':
    server.run(transport='stdio')
