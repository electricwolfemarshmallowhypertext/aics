# Desktop adapter audit

Scope: static review of repository code against the installed native desktop tool contracts and official hook documentation. No desktop actions were executed for this audit.

| Finding | Evidence | Action |
| --- | --- | --- |
| Native creation/navigation are instructions to Codex, not calls implemented by the Python server. | desktop_mcp.py exposes enable, prepare and confirm only; SKILL.md invokes create_thread and navigate_to_codex_page. | Do not claim autonomous desktop control is verified. |
| A PostToolUse hook supplies context; it does not freeze other work or enforce a pause. | Official PostToolUse behavior and hook() output. | Describe coordinated handoff as requested behavior, not an enforced guarantee. |
| A pending PostToolUse request suppresses the later Stop request. | hook() previously returned immediately when pending.json existed. | Allow one Stop continuation for a pending, unconfirmed handoff; retain stop_hook_active recursion guard. |
| Desktop summarization writes terminal HANDOFF.md and .handoff_draft.md. | build_handoff() writes both into cwd before desktop copies its output. | Add an optional output directory, defaulting to the original terminal behavior; desktop uses its session directory. |
| Confirmation trusts earlier handoff verification even if the file subsequently changes. | confirm() reads prepared.json but does not verify the saved handoff again. | Store the verified handoff SHA256 at preparation; confirmation checks the saved bytes. Rechecking the remaining queue after replacement progress incorrectly rejects completed work. |
| Desktop confirmation mutates receipts.RECEIPTS_PATH process-wide. | confirm() assigns the module global before logging. | Add an optional receipt destination; keep the terminal default unchanged. |
| Project, host and creation readiness were insufficiently checked in skill instructions. | create_thread can return setup-in-progress; projects carry path/host; read_thread accepts hostId. | Require exact project cwd and host, a ready threadId rather than clientThreadId, and the supported project/model inputs. Use normal native project settings/approvals rather than an unsupported settings-copy prerequisite. |
| The adapter carries project and model, but not a complete desktop permission/settings snapshot. | enabled.json stores only project_id/model. | Mark full settings preservation unsupported; do not silently claim it. |
| Automatic native approvals are not controlled by AICS. | Installed native create_thread tool is approval-controlled. | Do not bypass approval or claim unattended rollover. |
| Latest failed rehearsal never reached the adapter. | Saved checkpoint has item="item-01"; harness expects integer 1. No prepared.json exists. | Explicitly specify numeric checkpoint IDs in the fixture; preserve the failed report. |

| Windows stdin changes LF to CRLF in the recorded continuation prompt. | Real replacement rollout differs only in line endings. | Normalize CRLF for prompt comparison; retain all other content checks. |

Desktop rollover remains unverified. The repository-only backend rehearsal cannot verify desktop UI selection, native caller identity propagation, approvals or automatic handoff timing.

Sources: https://learn.chatgpt.com/docs/hooks and installed codex-app-tools contracts for create_thread, read_thread and navigate_to_codex_page.

Repository rehearsal outcome: the real replacement completed both checkpoint files and DONE.txt, but original confirmation failed. The unchanged original verification.json records passed=false. After the confirmation fixes, real MCP confirmation of those saved sessions succeeded; verification-recovery.json records the separate recovery. The missing preparation hash in this older fixture was recovered only after exact comparison with the independently saved real summarizer output. No new model sessions were used for recovery. This older run did not establish a clean integrated pass. The current corrected code subsequently passed the clean integrated backend run in tests/desktop-backend-run-1791659357609412000/verification.json. Evidence: tests/desktop-backend-run-1791653898236122600/.


## Setup verification against current desktop workflow

Official plugin packaging documents root plugin.json, root mcp.json, default hooks/hooks.json discovery and skills discovery. The current layout matches those documented paths. Native create_thread, read_thread and navigate_to_codex_page are supplied by the installed codex-app-tools; its server requires executor thread metadata. No standalone app-pipe control was added.

Concrete setup fixes: Windows commands now use the existing project Python environment; SessionStart exposes actual model and permission mode; generated environment context is excluded from user instructions. Ten focused adapter regressions passed. The actual configured Windows hook commands and configured real stdio MCP startup passed without a model session or desktop chat. Evidence: tests/desktop-setup-verification.json.

Official repo-scoped enablement removes the need to assume a separate desktop profile. It requires a trusted project's repo marketplace/configuration, installation into the existing plugin cache, and hook trust review. None was performed. Repo-scoped enablement is not a claim of zero global cache writes. The native transfer itself remains unverified.

Sources checked: https://developers.openai.com/plugins/build/plugins#enable-or-disable-a-plugin-for-a-repo and https://learn.chatgpt.com/docs/hooks#common-input-fields. Local implementation evidence: codex-app-tools/0.1.6/server.mjs and the bundled Codex source's hooks engine discovery/command runner. The terminal fork source is supporting implementation evidence; it is not proof that the running desktop loaded this plugin.


Latest concrete fixes: the skill previously required invoking a Python helper that was not exposed as an MCP tool. Replacement rollout lookup now runs within the existing confirmation tool. Its exact-ID/cwd binding and timeout are retained; a per-call Codex home supports the isolated runtime without changing terminal defaults. The inferred settings-preservation prerequisite was removed because native create_thread does not expose that parameter. A clean real backend continuation passed with the lookup performed by MCP itself. The ten adapter checks and existing exact-session binding regression passed.

A small explicit-file package was staged and its actual hooks/MCP startup passed. Its local marketplace catalog has the documented nested .agents/plugins/marketplace.json location. No marketplace registration, global plugin installation, project enablement, native desktop chat creation or trust change was performed. Native desktop acceptance remains open.


Latest wiring revision (plugin 0.1.1): explicit hooks and onboarding skill paths; captured-trigger validation survives later compaction; cached preparation and new confirmation reject changed source instructions; SessionStart reports actual enablement. Fourteen focused checks and current staged hook/MCP startup/path checks passed. tests/desktop-wiring-review.json records the scope and terminal preservation evidence. Earlier live model acceptance predates these latest checks; no new model run or native desktop transfer was performed. Installation, project enablement and hook trust have not occurred. Native desktop acceptance remains open.
