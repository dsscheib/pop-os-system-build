# Model escalation: Antigravity

How to apply the "Model escalation" rules in `AGENTS.md` when you run in Antigravity. Those rules decide when to escalate; this file gives the settings.

Antigravity subagents inherit the session model (`model: inherit`). For everyday work, run `Orchestrator (Neon)` as the primary (or `Orchestrator (Carbon)` for complex long-horizon architecture). For escalated tasks, switch to a frontier session model directly (`Gemini 4 Neon` High/XHigh or `Gemini 4 Carbon` High), or use Swarm (`/teamwork`) when you need to pin individual roles to exact model IDs (e.g., `gemini-4-neon-high`, `gemini-4-neon-xhigh`, `gemini-4-carbon-high`, or `claude-opus-5-5-high` for auditors alongside `gemini-4-hydrogen-high` or `gemini-pro-orchestrator-neon` for implementers). When delegating to a read-only reviewer on Antigravity CLI, pass explicit file paths or diffs because reviewers only receive `read_file`.
