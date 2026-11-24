# Open work

Single live register of unresolved decisions, defects, blockers, and gaps (150-line budget; keep current state only—git keeps history). The supervisor updates rows in place and shortens or drops closed entries once recorded in git or release notes. Never invent owners, deadlines, or decisions.

| ID | Status / owner | Impact / requirement | Next action / decision | Closure evidence / limits |
|---|---|---|---|---|
| OW-1 | Open / unassigned | v2 never ran end to end on a fresh machine (`docs/v2-migration-plan.md` Phase 5). | Run `preboot/` → `./bootstrap.sh` → `mise run apply` → `mise run auth` → `mise run apply` on a disposable Pop!_OS 24.04 VM. | None |
| OW-2 | In progress on `v2-review-fixes` / unassigned | v2 review findings: `mise run role` ignores its arguments, the `stm32` role's `sudo` calls can't prompt under Ansible, and first-install steps are skipped. The fix branch's container CI fails (`ansible-core` 2.22 needs Python 3.13), and a fresh `mise run plan` fails on third-party repo packages in check mode. | Finish and merge `v2-review-fixes`; the container release gate must pass, including the fresh `--check` pass. | None |
