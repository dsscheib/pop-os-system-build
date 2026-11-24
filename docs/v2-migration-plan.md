# Pop!_OS System Build — v2 Migration Plan

This document captures the locked architecture, ownership boundaries, and phased migration plan from the v1 Bash installer (`pop_os_config.sh`) to **v2** (**Ansible + Mise + Chezmoi**).

## Locked Design Decisions

1. **Tooling Stack**: **Ansible** (`ansible-core` installed via `uv`) + **Mise** + **Chezmoi**.
2. **Dotfiles & Secrets (Option B)**: Single **private** Chezmoi `dotfiles` repository.
   - Shell configs, prompts, Neovim/tmux/Herdr configs, and personal agent instructions/memories stay plaintext in the private repository for clean `git diff` history.
   - True secrets (such as `GDRIVE_CLIENT_ID` and `GDRIVE_CLIENT_SECRET`) use Chezmoi's native **`age` encryption** for defense-in-depth.
   - Retires the separate `dotfiles-private` repository and `config.secrets.env`.
   - `pop-os-system-build` holds **zero secrets**. (If OS-level secrets are ever added later, prefer `sops` + `age` using the same `age` key.)
3. **Mise Boundary**:
   - **Global** `~/.config/mise/config.toml` and `~/.config/mise/mise.lock` live in the private Chezmoi `dotfiles` repository and are auto-converged on dotfile updates via a Chezmoi `run_onchange_after_mise-install.sh.tmpl` hook.
   - **Repo-local** `mise.toml` in `pop-os-system-build` defines operator tasks (`bootstrap`, `plan`, `apply`, `role`, `auth`, `update`, `pins`, `lint`, `check`).
4. **Target OS**: **Pop!_OS / Ubuntu 24.04+ only (`x86_64`)**.
   - Drops Ubuntu 22.04 compatibility shims, legacy `.list`/GPG key workarounds (adopts `ansible.builtin.deb822_repository`), `btrfs-progs < 6.1` caveats, and optional fallback branches for packages native to 24.04+.
5. **Pre-boot & Disk Scripts**:
   - `setup_btrfs_live_install.sh` and `setup_secondary_btrfs_drive.sh` remain standalone imperative Bash/Python scripts (grouped under `preboot/` in Phase 5) because live USBs lack Ansible and destructive disk partitioning should not run inside routine playbooks.

---

## Layer Ownership Matrix

| Layer | Owner | Responsibilities |
| :--- | :--- | :--- |
| **Pre-boot & Disks** | `pop-os-system-build` (`preboot/` in Phase 5) | `setup_btrfs_live_install.sh`, `setup_secondary_btrfs_drive.sh`, `scripts/configure_hibernation.py`, `scripts/configure_snapper.py` |
| **OS, Toolchains & Desktop Apps** | `pop-os-system-build/ansible/` (Ansible) | `deb822_repository` APT sources, system packages, UFW, user groups (`dialout`, `plugdev`), `systemd-oomd`, `journald` drop-in, udev rules, Snapper/hibernation (post-boot), latest-major GCC/G++ & LLVM selection + `~/.local/bin` links, ARM/AVR/RISC-V toolchains, rootless Podman, Firefox Dev, Chrome, VS Code, KiCad 10 PPA + `kicad-python` venv, Flatpaks (`FreeCAD`, `OrcaSlicer`), JetBrainsMono font, pinned vendor apps (Arduino IDE 2, Saleae Logic, Synology Drive, Hyper Backup Explorer), `claude-code` APT, `antigravity-cli`, opt-in `stm32` role, Neovim `pynvim` venv + `astronvim_v6` sync, TPM, final `herdr` skill & integrations |
| **Dev Runtimes & CLIs** | Chezmoi → `~/.config/mise/{config.toml,mise.lock}` (Mise) | Runtimes, CLI utilities, and ecosystem CLIs listed below |
| **User Configs & Secrets** | Private `dotfiles` repo (Chezmoi + `age`) | `.bashrc`, `.zshenv`, `.zshrc`, Fish config (`mise activate`, `PATH`, `agent-scope` wrappers—retiring `scripts/shell_integration.py`), static completions (`agy`, `claude`, `django-admin`), `.gitconfig.tmpl` (identity + `gh` helper), `herdr`/`tmux`/`kitty` configs, `rclone-gdrive-bisync.{service,timer}.tmpl`, `modify_` scripts for Antigravity/Claude/Codex JSON/TOML guardrails (keeping `scripts/antigravity_permissions.py` only for Day-1 standalone `ai_agents` safety), `age`-encrypted `gdrive-oauth.env`, `gdrive-filters.txt`, and personal agent prompts/memories |
| **Interactive Auth** | `pop-os-system-build` (`mise run auth`) | Interactive `gh auth login --scopes workflow`, `rclone` Google Drive OAuth + initial `--resync` confirmation, sign-in checklist for `agy`/`claude`/`codex`/`copilot`/`:Copilot auth` |

> **Note on `rclone.conf`**: `~/.config/rclone/rclone.conf` is **not** managed as a static file by Chezmoi because `rclone` mutates it during OAuth token refreshes. Instead, Chezmoi deploys `age`-encrypted `~/.config/rclone/gdrive-oauth.env` (`0600`), and `mise run auth` feeds `GDRIVE_CLIENT_ID` / `GDRIVE_CLIENT_SECRET` to `rclone config create` / `update`.

---

## Asset & Tool Migration Mapping

### Moving from `resources/downloads.json` & `resources/tool-versions.env` to Global Mise (`~/.config/mise/config.toml` + `mise.lock`)

- **Runtimes**: `go` (`1.27.2`), `node` (`24.21.0`), `rust` (`1.99.0` with `components = "rust-analyzer,rustfmt,clippy,rust-src"`), `uv` (`0.13.0`)
- **CLI binaries previously hand-pinned or APT-fallback**: `neovim` (`v0.12.6`), `starship` (`v1.26.0`), `bottom` (`0.14.9`), `fastfetch` (`2.69.0`), `rclone` (`v1.75.2`), `eza`, `ripgrep`, `fd`, `bat`, `fzf`, `zoxide`, `gdu`, `github:herdrdev/herdr` (`v0.9.5`)
- **Ecosystem CLIs**: `npm:@openai/codex` (`0.162.1`), `npm:@github/copilot` (`1.0.97`), `npm:neovim` (`5.5.0`), `npm:tree-sitter-cli` (`0.27.1`), `go:github.com/yamnikov-oleg/nasmfmt`, `go:github.com/posener/complete/gocomplete` (`v1.2.3`), `cargo:asm-lsp` (`0.10.1`), `pipx:django` (`6.1.2`)

### Remaining in `ansible/group_vars/all/downloads.yml`

Only GUI/vendor downloads and signed APT repository keys remain pinned in Ansible:
- **Downloads (7)**: `font` (JetBrainsMono Nerd Font), `arduino_ide`, `saleae`, `synology`, `hyper_backup_explorer`, `antigravity_cli`, `appimagelauncher`
- **APT signing keys (7)**: `githubcli_key`, `llvm_key`, `mozilla_key`, `google_chrome_key`, `vscode_key`, `claude_code_key`, `kicad_key`

*(And `llvm_version`, `kicad_python_version`, `pynvim_version`, and `tpm_commit` live in `ansible/group_vars/all/00-defaults.yml`.)*

---

## Target Repository Layout (`pop-os-system-build`)

```text
pop-os-system-build/
├── bootstrap.sh                  # Installs APT prereqs -> uv -> mise -> ansible-core + collections
├── mise.toml                     # Repo tasks: bootstrap, apply, plan, role, auth, update, pins, lint, check
├── docs/
│   └── v2-migration-plan.md      # This plan
├── preboot/                      # Standalone Btrfs live-install & secondary-drive scripts (Phase 5 move)
├── scripts/                      # Retained helpers (st_assets.py, configure_hibernation.py, configure_snapper.py, merge_lazy_lock.py, auth.sh, check.sh)
├── resources/                    # Static OS assets (udev/, systemd/, izpack/, 80pop-snapper, icons/, st-installers.sha256)
├── ansible/
│   ├── ansible.cfg               # connection=local, pipelining=true, yaml callback, local fact cache
│   ├── .ansible-lint             # Production profile lint rules
│   ├── inventory/hosts.yml       # localhost
│   ├── requirements.yml          # community.general, ansible.posix
│   ├── group_vars/all/
│   │   ├── 00-defaults.yml       # Non-secret defaults & package lists
│   │   ├── downloads.yml         # Remaining 7 GUI/vendor pins + 7 APT keys
│   │   └── local.yml.example     # Optional gitignored local overrides template
│   ├── playbook.yml              # Ordered roles + final post_tasks (herdr skill & integrations)
│   └── roles/
│       ├── system/               # APT upgrade, UFW, dialout/plugdev, systemd-oomd, journald, NVMe udev
│       ├── snapper/              # Post-boot Snapper, snapper-rollback, 80pop-snapper, hibernation
│       ├── toolchains/           # Latest-major GCC/G++ & LLVM + ~/.local/bin links, ARM, AVR, RISC-V
│       ├── containers/           # Rootless Podman, subuid/subgid assert, search registry, completions
│       ├── browsers/             # Firefox Dev & Google Chrome via deb822_repository
│       ├── desktop_apps/         # Fonts, VS Code, KiCad 10 + kicad-python, Flatpaks, Arduino, Saleae, Synology, Hyper Backup
│       ├── ai_agents/            # claude-code APT, antigravity-cli, completions, Herdr hooks
│       ├── workstation/          # Chezmoi init/apply, Mise fallback, Neovim pynvim + astronvim_v6, TPM
│       └── stm32/                # Opt-in (tags: [never, stm32]) ST installers & ST-LINK udev rules
└── tests/                        # Python unit tests + Ansible container idempotency test
```

---

## Operator Workflows

### Day-1 Fresh Install (after Live USB `setup_btrfs_live_install.sh` + reboot)
1. `git clone https://github.com/dsscheib/pop-os-system-build && cd pop-os-system-build`
2. `./bootstrap.sh`
3. `cp ansible/group_vars/all/local.yml.example ansible/group_vars/all/local.yml` *(set `dotfiles_repo` / `nvim_config_repo`)*
4. Restore `~/.config/chezmoi/key.txt` (`0600`) if decrypting Chezmoi secrets
5. `mise run plan` *(optional dry-run with `--check --diff`)*
6. `mise run apply` *(Pass 1: OS, toolchains, containers, browsers, apps, Antigravity/Claude CLIs)*
7. `mise run auth` *(interactive `gh auth login`, Chezmoi init/apply + Mise lock install, Google Drive `rclone`, Herdr)*
8. `mise run apply` *(Pass 2: converge Chezmoi/Mise-dependent Neovim/TPM plugins & dynamic completions)*

### Day-2 Ongoing Management
- **Upgrade dev runtimes & CLIs**: `mise upgrade --bump && chezmoi re-add ~/.config/mise` (then commit & push `dotfiles`)
- **Sync dotfile & toolchain updates**: `chezmoi update` (automatically triggers `mise install` when `mise.lock` changes)
- **Check OS/desktop drift**: `mise run plan`
- **Converge OS/desktop or a single role**: `mise run apply` or `mise run role llvm`
- **Install/upgrade STM32 tools**: `mise run role stm32`
- **Validate repository**: `mise run check`

---

## Phased Migration Roadmap

### Phase 1: Scaffold v2 Tooling & CI Foundation (non-breaking alongside v1)
- [x] Document v2 plan in `docs/v2-migration-plan.md`
- [x] Add `bootstrap.sh` (non-root & 24.04+ `x86_64` guards, verified `uv` & `mise` bootstrap, `ansible-core` via `uv`, Galaxy collections)
- [x] Add repo-local `mise.toml` tasks (`bootstrap`, `apply`, `plan`, `role`, `auth`, `lint`, `check`)
- [x] Add Ansible foundation (`ansible/ansible.cfg`, `ansible/inventory/hosts.yml`, `ansible/requirements.yml`, `ansible/.ansible-lint`, `ansible/group_vars/all/{00-defaults,downloads}.yml`, `ansible/group_vars/all/local.yml.example`, `ansible/playbook.yml`)
- [x] Update `.gitignore`, `scripts/check.sh`, and `.github/workflows/check.yml` (`ubuntu-24.04` only, YAML parse + conditional `ansible-playbook --syntax-check` & `ansible-lint`)
- [x] Add unit tests in `tests/test_bootstrap.py`
- **Verify**: `bash scripts/check.sh` and `mise tasks` pass without altering v1 behavior.

### Phase 2: Core OS, Snapper, Toolchains, Containers & Browsers Roles
- [x] Implement `roles/system`, `roles/snapper`, `roles/toolchains`, `roles/containers`, and `roles/browsers` using `ansible.builtin.deb822_repository`.
- [x] Preserve safe `~/.local/bin` symlink checks for GCC/G++ and LLVM majors (`scripts/toolchain_links.py`).
- [x] Add an `ubuntu:24.04` container CI job running fresh `--check`, initial convergence, post-convergence `--check` (`changed=0`), and repeat apply (`changed=0`).
- **Verify**: Fresh `--check` succeeds; Pass 1 converges; post-convergence `--check` and Pass 2 apply report `changed=0` and `failed=0`.

### Phase 3: Desktop Apps, AI Agents, Workstation, STM32 & Auth
- [x] Implement `roles/desktop_apps`, `roles/ai_agents`, `roles/workstation` (including extracted `scripts/merge_lazy_lock.py`), and `roles/stm32` (`tags: [never, stm32]`).
- [x] Move dynamic CLI completions + Herdr global skill + `herdr integration install` into `playbook.yml` `post_tasks`.
- [x] Implement `scripts/auth.sh` for interactive `gh`, Chezmoi, and `rclone` Google Drive OAuth/bisync flows (`mise run auth`).
- **Verify**: Container CI passes for non-GUI roles; unit tests cover `merge_lazy_lock.py`, `auth.sh`, `st_assets.py`, `configure_hibernation.py`, and pre-boot scripts.

### Phase 4: Chezmoi + Global Mise Migration (in private `dotfiles` repo)
- [x] Initialize Chezmoi with `age` encryption support in the private `dotfiles` repository (`main`), folded with `dotfiles-private` content.
- [x] Add `dot_config/mise/config.toml` and `run_onchange_after_mise-install.sh.tmpl` (`private_mise.lock` committed via `mise lock --global`).
- [x] Migrate `agent-scope`, shell startup files, completions, `.gitconfig.tmpl`, `rclone-gdrive-bisync.{service,timer}.tmpl`, and `modify_` scripts for agent JSON/TOML files (`gdrive-oauth.env` encrypted with `age`).
- **Verify**: `chezmoi diff`, `chezmoi apply`, `mise doctor`, and `herdr config check` succeed cleanly.

### Phase 5: End-to-End Validation & v1 Retirement
- [x] Validate upgrade-in-place convergence and post-convergence `changed=0` idempotency on live Pop!_OS 24.04.
- [x] Verify greenfield Ubuntu 24.04 end-to-end lifecycle in rootless Podman (`./bootstrap.sh` → fresh `--check` → Pass 1 `apply` incl. `desktop_apps` → `auth.sh` + `age` decrypt + Chezmoi + Mise → Pass 2 `apply` → post-converge `--check` / Pass 3 `changed=0` → `stm32`) and confirm CI `ansible-integration` passes all 4 stages (`Pass 0`–`Pass 3`).
- [ ] Verify hardware-only rebuild steps on physical Pop!_OS hardware (Live USB `preboot/setup_btrfs_live_install.sh` → reboot → `snapper` + `enable_hibernation` resume cycle + desktop Flatpak user session install).
- [x] Move live-install / secondary-drive scripts under `preboot/` and remove retired v1 files (`pop_os_config.sh`, `resources/downloads.json`, `resources/tool-versions.env`, `scripts/shell_integration.py`, `scripts/neovim_wrapper.py`, `scripts/user_launcher.py`, and Chezmoi-migrated shell/completion resources).
- [x] Update `README.md` for v2.
- **Verify**: `mise run check`, `ansible-lint`, and container CI (`bootstrap.sh` + fresh `--check` + apply + post-converge `--check` + repeat apply) pass cleanly.
