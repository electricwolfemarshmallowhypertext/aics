# Native Codex relay

Upstream: `openai/codex`, tag `rust-v0.162.1`, commit `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d`.

The patch adds a relay hook to the native TUI. The existing Python disk sensor,
handoff verifier, and receipt format are reused. The terminal remains Codex's.
The Cargo lock repair changes 161 local release-version entries; external pins are unchanged.

Build in WSL from a clean checkout of that commit:

```sh
git apply /mnt/e/aics/native/codex-relay.patch
cd codex-rs
cargo build --locked -p codex-cli -p codex-code-mode-host
```

The TUI also requires `codex-code-mode-host` beside the executable. The current
local install reuses that binary from the official 0.162.1 release already installed
in WSL; the command above builds it from the same pinned source.

Launch from the project you want to work in:

```sh
python3 /mnt/e/aics/interactive.py --binary /path/to/codex-rs/target/debug/codex
```

Default executor is GPT-6 Sol and the occupancy trigger remains 65%.
HANDOFF.md stays a portable file; this patch implements the Codex native lifecycle only.
Other tools can read and write the handoff through the MCP wrapper; see [MCP.md](../MCP.md).
