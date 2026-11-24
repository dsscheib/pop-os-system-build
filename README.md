# Pop!_OS Workstation Setup (v2)

Declarative workstation automation for **Pop!_OS / Ubuntu 24.04+ on `x86_64`** using **Ansible**, **Mise**, and **Chezmoi**, plus standalone pre-boot Btrfs and STMicroelectronics installers.

> The legacy Bash workstation setup (`pop_os_config.sh`) is preserved at git tag `v1`. See [docs/v2-migration-plan.md](docs/v2-migration-plan.md) and [docs/phase4-chezmoi-guide.md](docs/phase4-chezmoi-guide.md) for the v2 architecture.

## 1. Pre-Boot Btrfs Setup (Live USB)

After installing Pop!_OS onto a GPT disk (`EFI`, `RECOVERY`, `ROOT` Btrfs) and **before the first reboot**, unmount the installer's target mounts and convert the root filesystem into `@`, `@home`, and `@snapshots` subvolumes:

```bash
sudo ./preboot/setup_btrfs_live_install.sh /dev/nvme0n1p3
# Or specify EFI explicitly (including mapped/encrypted roots):
sudo ./preboot/setup_btrfs_live_install.sh /dev/nvme0n1p3 /dev/nvme0n1p1
# Repair fstab/kernelstub/initramfs on an existing @/@home/@snapshots layout:
sudo ./preboot/setup_btrfs_live_install.sh --repair-boot /dev/nvme0n1p3 /dev/nvme0n1p1
```

### Optional: Secondary NVMe Btrfs Data Drive

To install the Samsung 970 EVO PCIe ASPM/APST udev rule and mount/create `@data` + `@snapshots` at `/mnt/data` (with `~/data` and `~/Downloads` symlinks):

```bash
sudo ./preboot/setup_secondary_btrfs_drive.sh           # Reattach existing pool
sudo ./preboot/setup_secondary_btrfs_drive.sh --format  # Partition & format from scratch
```

## 2. Optional: Standalone Post-Boot Snapper & Hibernation

To configure Snapper root snapshots, `snapper-rollback`, `80pop-snapper` APT hooks, and a `/swap/hibernate.swap` Btrfs swapfile outside Ansible (or set `enable_snapper: true` in `ansible/group_vars/all/local.yml`):

```bash
sudo ./preboot/setup_btrfs_snapper.sh
# Snapper without hibernation/swap (e.g. non-systemd-boot Debian/Ubuntu):
sudo ./preboot/setup_btrfs_snapper.sh --skip-hibernation
```

Reboot after hibernation setup and test with `systemctl hibernate`.

## 3. Bootstrap and Converge the Workstation

Run as your **desktop user** (never with `sudo`):

```bash
./bootstrap.sh
cp ansible/group_vars/all/local.yml.example ansible/group_vars/all/local.yml
# Edit local.yml with your private dotfiles_repo / nvim_config_repo if desired.

mise run plan    # Dry-run with --check --diff
mise run apply   # Converge OS, toolchains, containers, browsers, apps, AI CLIs, workstation
mise run auth    # Interactive gh login, Chezmoi init/apply, Google Drive rclone OAuth, Herdr hooks
```

Run a single role/tag with `mise run role <tag>`:

| Role / Tag | Scope |
| :--- | :--- |
| `system` | APT upgrades, UFW, `dialout`/`plugdev`, `systemd-oomd`, `journald` retention, `gh` CLI, NVMe udev rule |
| `snapper` | Opt-in (`enable_snapper: true`): Snapper, `snapper-rollback`, APT hooks, hibernation |
| `toolchains` (`gcc`, `llvm`, `arm`, `avr`, `riscv`) | Latest complete GCC/G++ & LLVM (`apt.llvm.org`) majors linked in `~/.local/bin`, cross-compilers |
| `containers` (`podman`) | Rootless Podman (`subuid`/`subgid` validated), `docker.io` search registry, Fish completion |
| `browsers` (`firefox`, `chrome`) | Firefox Developer Edition (Mozilla APT repo + pin) and Google Chrome stable |
| `desktop_apps` (`fonts`, `vscode`, `kicad`, `flatpak`, `arduino`, `saleae`, `synology`, `hyper_backup`) | JetBrainsMono Nerd Font, VS Code, KiCad 10 + `kicad-python`, Flathub apps, vendor GUI apps |
| `ai_agents` (`claudecode`, `antigravity`, `completions`) | `claude-code` APT, pinned `antigravity-cli` (`agy`) + guardrails, dynamic completions |
| `workstation` (`chezmoi`, `mise`, `neovim`, `tmux`) | Chezmoi drift apply, Mise tool sync, Neovim `pynvim` venv + `lazy-lock.json` merge, TPM |
| `stm32` *(opt-in: `mise run role stm32`)* | ST-LINK udev rules + verified CubeIDE, CubeMX, CubeProgrammer, SW4STM32 installers |

### Developer Runtimes, CLIs & Secrets

Runtimes (`go`, `node`, `rust`, `uv`) and developer CLIs (`neovim`, `starship`, `bottom`, `fastfetch`, `rclone`, `eza`, `ripgrep`, `fd`, `bat`, `fzf`, `zoxide`, `gdu`, `herdr`, `codex`, `copilot`, `asm-lsp`, `nasmfmt`, `gocomplete`, `django`) live in your private Chezmoi `dotfiles` repository (`~/.config/mise/config.toml` + `mise.lock`) with `age` encryption for OAuth secrets.

## 4. Day-2 Management

- **Preview OS/desktop drift**: `mise run plan`
- **Converge OS/desktop state**: `mise run apply`
- **Update dev runtimes & CLIs**: `mise upgrade --bump && chezmoi re-add ~/.config/mise`
- **Pull dotfiles & auto-sync Mise**: `chezmoi update`

## 5. Validation

```bash
mise run check   # or: bash scripts/check.sh
```

Runs `shellcheck`, Bash syntax checks, Python AST & YAML validation, `ansible-playbook --syntax-check`, `ansible-lint`, and `unittest` regressions. CI runs `check` plus an `ubuntu:24.04` container two-pass convergence/idempotency test (`changed=0` on pass 2).
