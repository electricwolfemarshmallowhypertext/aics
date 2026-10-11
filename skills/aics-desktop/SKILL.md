---
name: aics-desktop
description: Carry a user-authorized AICS desktop task into a fresh native Codex thread after the configured context threshold is reached.
---

Use only in native Codex desktop with its codex-app-tools available.
Enable only when the human explicitly authorizes automatic creation and continuation of replacement desktop tasks. Plugin installation alone is not authorization.

For enabling, obtain the current session identity, transcript path, model and permission mode from actual session metadata, and the exact project ID and host from list_projects. Require the project path to match the source cwd exactly; if it does not, stop rather than guess a target checkout. Call enable_desktop with those values, cwd, and the current model. Never guess identities or change global settings.

When AICS_DESKTOP_HANDOFF arrives:
1. Finish the already running operation. Do not start more project edits in the source task.
2. Call prepare_desktop_handoff with the supplied cwd and source session ID. If it fails, report the failure and stop the transfer.
3. Confirm native tools are available and the target project uses the same host. Use native create_thread with target type project, returned project_id, environment type local, returned model and returned prompt. Keep the same project checkout. Let Codex apply its normal project settings and approvals; do not change them or claim that chat-specific setting overrides are copied. Do not fork old conversation history. Do not invoke create_thread a second time if its outcome is uncertain; inspect native threads first.
4. Require a ready threadId and hostId; clientThreadId is not a ready thread identity. If setup is pending, wait for readiness without creating another task. Read the returned thread with read_thread using its hostId. Call confirm_desktop_handoff with cwd, source_session_id and replacement_session_id set to the returned threadId. The server locates the replacement rollout by exact ID, verifies its workspace and checks the supplied continuation prompt. Do not guess a rollout path or invoke a Python helper separately. A successful process exit or handoff file alone is not evidence of a transfer.
5. After confirmation, navigate_to_codex_page for the replacement thread. End this source turn without additional project work. The replacement starts from the supplied prompt and continues the original goal.

If creation succeeds but confirmation fails, report the replacement ID and the failure. Do not spawn another replacement or resume source edits while the replacement may be working. Never terminate the desktop app or change native memory settings.
