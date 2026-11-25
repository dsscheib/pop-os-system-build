# Open work

Single live register of unresolved decisions, defects, blockers, and gaps (150-line budget; keep current state only—git keeps history). The supervisor updates rows in place and shortens or drops closed entries once recorded in git or release notes. Never invent owners, deadlines, or decisions.

| ID | Status / owner | Impact / requirement | Next action / decision | Closure evidence / limits |
|---|---|---|---|---|
| OW-1 | Open / unassigned | The hardware-only rebuild steps haven't been verified (`docs/v2-migration-plan.md` Phase 5): converting the root disk from a live USB with `preboot/setup_btrfs_live_install.sh`, Snapper on a real `@`/`@snapshots` layout, a hibernate/resume cycle with `enable_hibernation: true`, and the Flatpak user install in a real desktop session (the container has no D-Bus user session). | Check these at the next physical rebuild of a Pop!_OS 24.04 machine. | Everything else is covered by `scripts/e2e_container_test.sh` (fresh `ubuntu:24.04`, including `desktop_apps`, `auth.sh`, Day-2 and `stm32`) and CI's `ansible-integration` job. |
