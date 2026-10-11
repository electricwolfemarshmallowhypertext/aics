"""Launch the patched native Codex TUI with relay enabled; stdin stays native."""
import argparse
import os
from pathlib import Path
import sys

import config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, default=Path(__file__).resolve().parent / '.relay/codex-source/codex-rs/target/debug/codex')
    args, native_args = parser.parse_known_args()
    if not args.binary.is_file():
        parser.error('Build the native fork first, or provide --binary PATH')
    env = os.environ.copy()
    env['AICS_RELAY_PYTHON'] = sys.executable
    env['AICS_RELAY_HELPER'] = str(Path(__file__).resolve().with_name('native_relay.py'))
    os.execve(str(args.binary), [str(args.binary), '--model', config.MODEL, '-c', f'tool_output_token_limit={config.TOOL_OUTPUT_TOKEN_LIMIT}', *native_args], env)


if __name__ == '__main__':
    main()
