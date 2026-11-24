# Pop!_OS Workstation Setup

Turn a fresh **Pop!_OS or Ubuntu 24.04+ (x86_64)** install into a ready-to-use development workstation with a handful of commands, and keep it in that state afterwards.

Three tools split the work:

- **Ansible** (this repo) sets up the system: packages, compilers and cross-compilers, Podman, browsers, desktop and vendor apps, AI coding CLIs, and Snapper snapshots.
- **Chezmoi** restores your personal dotfiles from a private Git repo, with secrets encrypted by `age`.
- **Mise** installs the language runtimes and developer CLIs pinned in those dotfiles (Go, Node, Rust, Neovim, ripgrep, and so on).

Everything is safe to re-run. A second run changes nothing unless something has drifted.

> Looking for the old single-script installer (`pop_os_config.sh`)? It's preserved at git tag `v1`.

## Before you start

- **Pop!_OS or Ubuntu 24.04 or newer, on x86_64.**
- **A normal desktop user with `sudo` rights.** Run every command below as that user, *not* with `sudo` (except the `preboot/` scripts). You'll be asked for your password when it's needed.
- **`git`**, to clone this repo (`sudo apt install git` if it's missing).
- *Optional:* a **private Chezmoi dotfiles repo** plus a backup of its **age key** (`key.txt`), if you want your personal setup restored.
- *Optional:* a **Btrfs root** if you want Snapper snapshots (see step 1). On other filesystems, turn Snapper off in step 3.

## Set up a new machine

### 1. Prepare the disk for snapshots (optional, from the live USB)

Do this right after the Pop!_OS installer finishes and **before you reboot**. The script reorganizes the Btrfs root into `@`, `@home` and `@snapshots` subvolumes so Snapper can take and roll back snapshots. Boot the live USB in UEFI mode, and get this repo onto it (clone it or copy it from a USB stick).

```bash
lsblk                                                   # find your root (Btrfs) and EFI partitions
sudo ./preboot/setup_btrfs_live_install.sh /dev/nvme0n1p3
# If the EFI partition isn't detected (for example with an encrypted root), name it too:
sudo ./preboot/setup_btrfs_live_install.sh /dev/nvme0n1p3 /dev/nvme0n1p1
```

If the script reports that the root filesystem is already mounted, unmount the installer's mounts (`findmnt` lists them) and run it again. To repair fstab, kernelstub and initramfs on a disk that's already converted, add `--repair-boot` before the device names.

Skip this step if you don't want snapshots.

### 2. Clone and bootstrap

After rebooting into the installed system:

```bash
git clone https://github.com/dsscheib/pop-os-system-build.git
cd pop-os-system-build
./bootstrap.sh
```

This installs a few APT prerequisites, then `uv`, `mise`, and pinned versions of `ansible-core` and `ansible-lint` into `~/.local/bin` (downloads are checksum-verified). If your shell then says `mise: command not found`, run `export PATH="$HOME/.local/bin:$PATH"` or log out and back in.

### 3. Choose your settings

```bash
cp ansible/group_vars/all/local.yml.example ansible/group_vars/all/local.yml
```

Edit `local.yml` (it's gitignored, so it stays on this machine). The settings you're most likely to change:

| Setting | Default | Change it when… |
| :--- | :--- | :--- |
| `dotfiles_repo` | empty | you want `mise run auth` to restore your private Chezmoi dotfiles |
| `nvim_config_repo`, `nvim_config_ref` | empty | you want your Neovim config cloned into `~/.config/nvim` (`ref` is an optional branch, tag or commit) |
| `enable_snapper` | `true` | set `false` if you skipped step 1; otherwise `apply` stops with a Btrfs layout error |
| `enable_hibernation` | `false` | set `true` on laptops to create a Btrfs swapfile and resume settings |
| `install_samsung_970_evo_nvme_rule` | `false` | only on the machine with that Samsung drive at PCI `0000:00:1d.0` |
| `install_dropbox` | `true` | set `false` to skip `nautilus-dropbox` |
| `install_appimagelauncher` | `false` | set `true` to install AppImageLauncher |

Everything else (package lists, Flatpak apps, the LLVM version, which STM32 tools to install, and so on) lives in `ansible/group_vars/all/00-defaults.yml`. Any of it can be overridden in `local.yml`.

**Restoring encrypted dotfiles?** Put your age key in place now, before step 5:

```bash
install -d -m 0700 ~/.config/chezmoi
install -m 0600 /path/to/backup/key.txt ~/.config/chezmoi/key.txt
```

### 4. First apply

```bash
mise run plan    # optional: preview what will change, without changing anything
mise run apply
```

When Ansible asks for `BECOME password:`, enter your sudo password. The first run downloads and installs a lot, so expect it to take a while.

### 5. Sign in

```bash
mise run auth
```

This walks through each interactive sign-in and skips any step that doesn't apply:

1. **GitHub CLI**: signs in through your browser.
2. **Dotfiles**: runs Chezmoi with `dotfiles_repo`, which also installs your Mise runtimes and CLIs.
3. **Google Drive**: if your dotfiles provide the OAuth client, signs in to rclone and asks before the first full two-way sync.
4. **Herdr**: hooks Herdr into whichever agent CLIs are installed.

To redo a single step later: `mise run auth --step gdrive` (or `github`, `chezmoi`, `herdr`).

### 6. Apply again

```bash
mise run apply
```

Now that your dotfiles and Mise tools are in place, this second run sets up everything that depends on them: Neovim and tmux plugins, shell completions, and Herdr skill files.

### 7. Finish up

- **Log out and back in** so your new `dialout` and `plugdev` group memberships apply (needed for serial ports and debug probes).
- **Laptops with hibernation:** reboot, then test with `systemctl hibernate`.
- **Sign in to the AI CLIs** you use: `agy`, `claude`, `codex`, `copilot`, and `:Copilot auth` inside Neovim.

## Everyday use

| I want to… | Run |
| :--- | :--- |
| See what has drifted from the configuration | `mise run plan` |
| Bring the system back in line, or pick up changes to this repo | `mise run apply` |
| Run just one area | `mise run role browsers` (several at once: `mise run role gcc,llvm`) |
| Pull dotfile changes (also installs updated Mise tools) | `chezmoi update` |
| Upgrade runtimes and CLIs | `mise upgrade --bump`, then `chezmoi re-add ~/.config/mise/config.toml ~/.config/mise/mise.lock` and commit and push your dotfiles |

Extra Ansible options go after the task name, for example `mise run apply --diff` or `mise run plan --tags system`.

### Roles and tags

Use any of these names with `mise run role <name>`:

| Role (extra tags) | What it sets up |
| :--- | :--- |
| `system` | APT upgrades, UFW firewall, `dialout`/`plugdev` groups, `systemd-oomd`, journald retention, GitHub CLI, optional NVMe udev rule |
| `snapper` | Snapper root snapshots, `snapper-rollback`, snapshots around APT upgrades, optional hibernation |
| `toolchains` (`gcc`, `llvm`, `arm`, `avr`, `riscv`) | Newest GCC/G++ and LLVM/Clang (from `apt.llvm.org`), linked into `~/.local/bin`, plus ARM, AVR and RISC-V cross-compilers |
| `containers` (`podman`) | Rootless Podman with `docker.io` as the default search registry, plus Fish completion |
| `browsers` (`firefox`, `chrome`) | Firefox Developer Edition (Mozilla's APT repo) and Google Chrome |
| `desktop_apps` (`fonts`, `vscode`, `kicad`, `flatpak`, `arduino`, `saleae`, `synology`, `hyper_backup`) | JetBrainsMono Nerd Font, VS Code, KiCad 10 with `kicad-python`, FreeCAD and OrcaSlicer (Flathub), Arduino IDE 2, Saleae Logic 2, Synology Drive, Hyper Backup Explorer |
| `ai_agents` (`claudecode`, `antigravity`) | Claude Code and the Antigravity CLI (`agy`), with Antigravity set to ask before destructive git commands |
| `workstation` (`chezmoi`, `mise`, `neovim`, `tmux`) | Applies dotfile changes, installs missing Mise tools, sets up the Neovim Python provider and config, and installs tmux plugins |
| `completions` | Shell completions for `codex`, `herdr`, `rustup` and `cargo` |
| `stm32` | ST-LINK udev rules and STMicroelectronics tools (opt-in; see below) |

## Optional extras

### STMicroelectronics tools (STM32)

ST requires a sign-in to download its tools, so they're never part of a normal `apply`.

1. Download the installers from st.com. Only the exact versions listed in `resources/st-installers.sha256` are accepted:
   - CubeIDE 2.2.0
   - CubeMX 6.18.1
   - CubeProgrammer
   - System Workbench (SW4STM32)
2. Put them in `~/Downloads`, in `$ST_DOWNLOAD_DIR` (default `~/github/st-microelectronics`), or in the repo folder.
3. Run `mise run role stm32`.

Tools are installed under `~/STMicroelectronics` with launchers in your app menu, and previous versions are kept. If an installer isn't found, that tool is skipped with a warning. To install only some of the tools, set `stm32_tools` in `local.yml` to a subset of `cubeide`, `cubemx`, `cubeprog` and `sw4stm32`.

### Secondary Btrfs data drive

```bash
sudo ./preboot/setup_secondary_btrfs_drive.sh                 # reattach an existing data pool
sudo ./preboot/setup_secondary_btrfs_drive.sh --format        # ERASES the drive and creates a new pool
sudo ./preboot/setup_secondary_btrfs_drive.sh /dev/disk/by-id/<your-drive>
```

This mounts the drive's `@data` and `@snapshots` subvolumes at `/mnt/data` and links `~/data` and `~/Downloads` to it. It refuses to touch the system disk.

The script was written for one machine's Samsung 970 EVO. With no drive given, it looks for that drive; for any other drive, pass its `/dev/disk/by-id/…` path. It always installs the Samsung power-management udev rule. That rule matches Samsung 970 EVO controllers, but one line also keeps the device at PCI address `0000:00:1d.0` powered on.

### Snapper without Ansible

```bash
sudo ./preboot/setup_btrfs_snapper.sh                     # Snapper plus hibernation
sudo ./preboot/setup_btrfs_snapper.sh --skip-hibernation  # Snapper only
```

This does the same job as the `snapper` role, for a machine where you don't want to run the full playbook.

## Troubleshooting

| Problem | Fix |
| :--- | :--- |
| `mise: command not found` right after bootstrap | `export PATH="$HOME/.local/bin:$PATH"`, or log out and back in |
| `apply` stops with "Root must mount /@ and /.snapshots must mount /@snapshots…" | Your root isn't the Btrfs layout from step 1. Set `enable_snapper: false` in `local.yml`, or convert the disk from a live USB. |
| The containers role asks for subordinate IDs | Run the `sudo usermod --add-subuids … --add-subgids …` command it prints, then `mise run role containers` |
| Chezmoi can't decrypt files during `mise run auth` | Your age key isn't at `~/.config/chezmoi/key.txt` (see step 3) |
| "Permission denied" on a serial port or debug probe | Log out and back in to pick up the new groups, then reconnect the device |
| GitHub CLI suddenly signed out after restoring a backup | Don't restore an old `~/.local/share/keyrings/` over a new install, because it replaces the GitHub CLI token. Sign in again with `mise run auth --step github`. |

## How the repo is organized

```text
bootstrap.sh            Installs uv, mise, ansible-core and Galaxy collections
mise.toml               The `mise run …` tasks
ansible/                Playbook, roles, and settings (group_vars/all/)
preboot/                Standalone disk and Snapper scripts (run as root)
install_stm32_*.sh      STMicroelectronics installers used by the stm32 role
scripts/                Helpers used by the roles, plus auth.sh, check.sh and container tests
resources/              Static files: udev rules, journald config, checksums, icons
tests/                  Unit tests for the scripts
docs/                   Design notes: v2-migration-plan.md, phase4-chezmoi-guide.md
```

Runtimes and developer CLIs (`go`, `node`, `rust`, `uv`, `neovim`, `starship`, `ripgrep`, `fd`, `bat`, `fzf`, `zoxide`, `herdr`, `codex`, `copilot` and more) are deliberately **not** managed here. They live in your private dotfiles repo as `~/.config/mise/config.toml` and `mise.lock`. See [docs/phase4-chezmoi-guide.md](docs/phase4-chezmoi-guide.md) for how that repo is laid out.

## Checking changes to this repo

```bash
mise run check    # or: bash scripts/check.sh
```

This runs `shellcheck`, Bash syntax checks, Python and YAML parsing, `ansible-playbook --syntax-check`, `ansible-lint`, and the unit tests. CI runs the same checks, plus a full playbook test in an `ubuntu:24.04` container. That test previews the changes, applies them, then confirms that both a second preview and a second apply report no changes.

To run the full greenfield end-to-end test locally in a disposable `ubuntu:24.04` container (requires Podman and network access; exercises `bootstrap.sh`, `desktop_apps`, `auth.sh` with ephemeral fixtures, Day-2 convergence, `lazy-lock.json` merge, `stm32`, and `mise run role`):

```bash
podman run --rm -v "$PWD:/workspace:ro" docker.io/library/ubuntu:24.04 \
  bash /workspace/scripts/e2e_container_test.sh /workspace
```

For the faster CI-only container test (skips `desktop_gui`, `auth.sh`, and `stm32`), substitute `scripts/ci_container_test.sh`.
