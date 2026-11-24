# Model escalation: Claude Code

How to apply the "Model escalation" rules in `AGENTS.md` when you run in Claude Code. Those rules decide when to escalate; this file gives the settings. In the tables below, `A → B` means step 1 `A`, then step 2 `B`; one setting means one step. "Default" means the role's default is already suited, so it does not escalate in this tool; a role not installed in this project does not apply.

Set the subagent's `model` and `effort` to the listed values; they override the agent file. This is an explicit instruction to run that subagent at the listed effort.

## Software roles

| Role | Claude Code |
|---|---|
| `frontend` | `sonnet`, high → `opus`, high |
| `integration-tester` | `sonnet`, high → `opus`, high |
| `security` | `fable`, high |
| `auditor-advisor` | `opus`, xhigh → `fable`, high |
