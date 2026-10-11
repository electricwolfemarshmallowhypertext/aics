"""Expose the existing portable handoff through MCP over stdio."""
import argparse
from pathlib import Path
import tempfile

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from handoff import atomic_write, boot_prompt
from handoff_check import verification_scope, verify


def create_server(cwd):
    root = Path(cwd).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('MCP workspace must be a directory')
    server = MCPServer('aics-handoff')

    @server.tool()
    def read_handoff() -> dict:
        """Read HANDOFF.md, the original goal and existing verification results."""
        path = root / 'HANDOFF.md'
        if not path.exists():
            raise ToolError('No HANDOFF.md is saved in this workspace')
        goal = root / '.relay' / 'goal.txt'
        return {
            'handoff': path.read_text(encoding='utf-8'),
            'goal': goal.read_text(encoding='utf-8') if goal.exists() else '',
            'boot_prompt': boot_prompt(),
            'verification_scope': verification_scope(root),
            'verification_errors': verify(path, root),
        }

    @server.tool()
    def write_handoff(goal: str, handoff: str) -> dict:
        """Save a portable handoff and goal after existing checks pass. Checks do not certify arbitrary prose."""
        if not goal.strip():
            raise ToolError('Original goal must not be empty')
        directory = root / '.relay'
        directory.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=directory, suffix='.md', delete=False) as draft:
            draft.write(handoff)
            path = Path(draft.name)
        try:
            errors = verify(path, root)
            if errors:
                raise ToolError('Refusing invalid handoff: ' + '; '.join(errors))
            atomic_write(directory / 'goal.txt', goal)
            atomic_write(root / 'HANDOFF.md', handoff)
        finally:
            path.unlink(missing_ok=True)
        return {'saved': True, 'verification_scope': verification_scope(root),
                'verification_errors': []}

    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cwd', required=True, type=Path)
    args = parser.parse_args()
    create_server(args.cwd).run(transport='stdio')


if __name__ == '__main__':
    main()
