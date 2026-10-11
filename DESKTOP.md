# Desktop upgrade

The terminal launcher and backend remain available; desktop uses separate handoff storage and preserves terminal defaults. This additive plugin uses the existing occupancy sensor, handoff writer, verifier and receipts. Native thread creation and navigation use Codex's bundled codex-app-tools, with desktop-supplied caller identity. No raw app pipe access is implemented.

This package is not installed, enabled, or trusted globally. Desktop end-to-end acceptance has not passed.

The plugin is local-only. Its root plugin.json, mcp.json, hooks/hooks.json and skills/aics-desktop/SKILL.md follow the documented package layout. The development package uses this project's existing MCP Python environment; another machine must supply its own Python environment with requirements-mcp.txt installed.

SessionStart supplies the actual session identity and transcript path to Codex without enabling relay. After local installation and hook trust review, the user must explicitly authorize automatic replacement tasks. enable_desktop enables only the exact selected session. It does not enable terminal sessions or every desktop chat.

At 65% recorded occupancy, PostToolUse requests a handoff after the current tool returns. Stop covers turn completion without a final tool call. Codex then prepares the verified handoff and invokes native create_thread with the original project, model and continuation prompt. A replacement receipt requires its own exact-identity rollout to contain that prompt. The skill instructs the source to stop project edits and requests continuation in the replacement; this behavior has not been verified in desktop. Native memory settings are not modified.

The handoff request is delivered as model instructions; it is not a forced pause. This adapter does not yet prove that the desktop completes this sequence automatically or that a long turn always transfers before native compaction. Those are the live desktop acceptance checks, not completed claims.

References:
- https://developers.openai.com/plugins/build/plugins
- https://learn.chatgpt.com/docs/hooks
- https://learn.chatgpt.com/docs/app-server

Native thread tools can require desktop approval. This plugin does not bypass that approval or alter global approval policy.

Current validation: ten focused adapter checks passed. The exact configured Windows commands for SessionStart, PostToolUse and Stop ran successfully; the configured MCP server started and exposed all three tools. Disabled sessions wrote no state. Evidence: tests/desktop-setup-verification.json. These startup checks did not create model sessions or desktop chats. The current code completed a clean real CLI handoff and continuation through MCP in tests/desktop-backend-run-1791659357609412000/verification.json. Earlier failures and saved-session recovery reports remain unchanged. Native desktop transfer remains unverified.

Static audit: DESKTOP-AUDIT.md. Desktop output is kept in its own session directory. Full source permission/settings preservation and unattended native approvals are not verified. The adapter does not freeze the source; its handoff request is model context.


Repository-scoped deployment is documented: a repo marketplace makes the plugin discoverable, and a trusted project's .codex/config.toml can enable plugin-name@marketplace-name for that project. A separate desktop profile is not a documented prerequisite. This repository has not been registered or enabled. Installation uses the user's Codex plugin cache even for a repo marketplace, and hooks need a separate trust review; repository-scoped enablement does not mean installation writes only repository files.

Setup review corrections: Windows hooks use the known project Python environment instead of PATH; SessionStart supplies the actual model and permission mode; generated environment_context messages are excluded from the original user goal. Native task creation remains a Codex app-tool call made by the skill, with executor-supplied caller identity. The standalone Python server does not create chats or supply that identity.


Use package_desktop.py to stage the local plugin before installation. It copies only 14 required files into .relay/desktop-package/aics-desktop and writes .relay/desktop-package/.agents/plugins/marketplace.json. This staging directory is not registered or enabled. Do not install the repository root as the plugin source: it also holds compiler caches and test workspaces. Staging copies no credentials, caches or test receipts. The package still depends on this machine's existing project Python environment.

The continuation skill now uses confirm_desktop_handoff with the returned replacement thread ID; the MCP server performs exact rollout binding itself. The tool's optional explicit rollout path is retained for compatibility. Native creation uses documented project/model inputs and normal Codex project settings/approvals; the plugin does not invent or bypass settings controls.


Latest wiring revision (plugin 0.1.1): explicit hooks and onboarding skill paths; captured-trigger validation survives later compaction; cached preparation and new confirmation reject changed source instructions; SessionStart reports actual enablement. Fourteen focused checks and current staged hook/MCP startup/path checks passed. tests/desktop-wiring-review.json records the scope and terminal preservation evidence. Earlier live model acceptance predates these latest checks; no new model run or native desktop transfer was performed. Installation, project enablement and hook trust have not occurred. Native desktop acceptance remains open.
