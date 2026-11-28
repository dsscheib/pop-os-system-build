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

**In the Pop!_OS installer,** choose a custom install with these partitions, in this order:

| Partition | Format | Size | Mount point |
| :--- | :--- | :--- | :--- |
| EFI System Partition | FAT32 | 4 GB | `/boot/efi` |
| Recovery | FAT32 | 4 GB | `/recovery` |
| Root | Btrfs | the rest of the disk | `/` |

Both 4 GB sizes match Pop!_OS's automatic install. The EFI partition holds the kernels and their initramfs images. The installer copies itself onto the recovery partition, so you can boot it from the boot menu (hold Space while the computer starts) without a USB stick. The recovery partition is optional; the script leaves its `/recovery` entry as it is. Don't create a separate `/home` or `/boot`; the script refuses those.

Do this right after the Pop!_OS installer finishes and **before you reboot**. The script reorganizes the Btrfs root into `@`, `@home` and `@snapshots` subvolumes so Snapper can take and roll back snapshots. Boot the live USB in UEFI mode, and get this repo onto it: clone it (if the live session has no git, run `sudo apt install git` first), or copy it from a USB stick.

```bash
lsblk                                                   # find your root (Btrfs) and EFI partitions
sudo ./preboot/setup_btrfs_live_install.sh /dev/nvme0n1p3
# If the EFI partition isn't detected, name it too:
sudo ./preboot/setup_btrfs_live_install.sh /dev/nvme0n1p3 /dev/nvme0n1p1
```

- **Encrypted root:** the script needs the unlocked Btrfs filesystem, not the encrypted partition. Unlock it first, then pass the mapped device and the EFI partition:
  ```bash
  sudo cryptsetup open /dev/nvme0n1p3 cryptroot   # add `sudo vgchange -ay` if the installer used LVM
  sudo ./preboot/setup_btrfs_live_install.sh /dev/mapper/cryptroot /dev/nvme0n1p1
  ```
- **Copied from a USB stick?** FAT sticks drop the execute bit, so run it as `sudo bash ./preboot/setup_btrfs_live_install.sh …`.
- If the script reports that the root filesystem is already mounted, unmount the installer's mounts (`findmnt` lists them) and run it again.
- To repair fstab, kernelstub and initramfs on a disk that's already converted, add `--repair-boot` before the device names.

Skip this step if you don't want snapshots.

### 2. Clone and bootstrap

After rebooting into the installed system, bring it fully up to date first. Right after the first login, Pop!_OS's own updater may be holding the package manager, and the first `apply` shouldn't be the run that replaces the kernel and desktop under you.

```bash
sudo apt update && sudo apt full-upgrade -y && sudo reboot
```

Then clone the repo into `~/github`. The `update` shell function in the dotfiles and the STM32 download folder both expect that location.

```bash
mkdir -p ~/github && cd ~/github
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
| `dotfiles_repo` | empty | you want `mise run auth` to restore your private Chezmoi dotfiles. Use the HTTPS URL: SSH keys don't exist yet on a fresh machine. |
| `nvim_config_repo`, `nvim_config_ref` | empty | you want your Neovim config cloned into `~/.config/nvim` (`ref` is an optional branch, tag or commit). Use the HTTPS URL. If the repo is private, leave this empty until after step 5, because there are no GitHub credentials before then. |
| `enable_snapper` | `true` | set `false` if you skipped step 1; otherwise `apply` stops with a Btrfs layout error |
| `enable_hibernation` | `false` | set `true` on laptops to create a Btrfs swapfile and resume settings. Closing the lid on battery then suspends, and hibernates after 1 hour (`HIBERNATE_DELAY` in `scripts/configure_hibernation.py`); on AC it only suspends. It also adds a polkit rule that lets your desktop session hibernate, which Ubuntu disables by default, and a sleep hook (`/usr/lib/systemd/system-sleep/pop-workstation-s2h-timer`) that works around a systemd bug where the laptop wakes after the hour and stays on. Works with initramfs-tools (Pop!_OS 24.04) and `dracut` (26.04). |
| `install_samsung_970_evo_nvme_rule` | `false` | only on a machine with a Samsung 970 EVO drive |
| `install_dropbox` | `true` | set `false` to skip Dropbox. The `nautilus-dropbox` package is Dropbox's Linux client; its Nautilus plugin goes unused on COSMIC, and syncing works without it. See step 7 for the one-time setup. |
| `install_appimagelauncher` | `false` | set `true` to install AppImageLauncher |
| `install_cosmic_swipe` | `true` | set `false` to skip [cosmic-swipe](https://github.com/dsscheib/cosmic-swipe), which adds four-finger up (Workspaces overview) and down (App Library) touchpad gestures to COSMIC. It's skipped automatically on desktops without COSMIC. Set it `false` everywhere once COSMIC ships these gestures itself, or each swipe acts twice; turning it off doesn't uninstall it (see [its README](https://github.com/dsscheib/cosmic-swipe#install) for `uninstall.sh`). |

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

When it finishes, close every terminal window and open a new one. A terminal that was already open doesn't see the fonts this run installed. COSMIC Terminal then resets its font setting as soon as your dotfiles change it, and the next apply stops on that change.

### 5. Sign in

**Restoring a backup of your home directory?** Do it before this step, and leave `~/.local/share/keyrings/` out of it. An old keyring replaces the new one and wipes the GitHub CLI login this step creates.

```bash
mise run auth
```

This walks through each interactive sign-in and skips any step that doesn't apply:

1. **GitHub CLI**: signs in through your browser.
2. **Dotfiles**: runs Chezmoi with `dotfiles_repo`, which also installs your Mise runtimes and CLIs.
3. **Google Drive**: if your dotfiles provide the OAuth client, signs in to rclone and asks before the first full two-way sync.
4. **Herdr**: hooks Herdr into whichever agent CLIs are installed.

To redo a single step later: `mise run auth -- --step gdrive` (or `github`, `chezmoi`, `herdr`). Every step is safe to rerun if one stops partway, for example on a network error or a closed browser tab.

### 6. Apply again

```bash
mise run apply
```

Now that your dotfiles and Mise tools are in place, this second run sets up everything that depends on them: Neovim and tmux plugins, shell completions, and Herdr skill files.

### 7. Finish up

- **Log out and back in** so your new `dialout` and `plugdev` group memberships apply (needed for serial ports and debug probes).
- **Laptops with hibernation:** reboot, then test with `systemctl hibernate`, and once more with `systemctl suspend-then-hibernate`.
- **Sign in to the AI CLIs** you use: `agy`, `claude`, `codex`, `copilot`, and `:Copilot auth` inside Neovim.
- **Dropbox:** run `dropbox start -i` once. It downloads the Dropbox program and prints a link to sign this computer in. Then run `dropbox autostart y` so it starts at every login.

## Everyday use

| I want to… | Run |
| :--- | :--- |
| See what has drifted from the configuration | `mise run plan` |
| Bring the system back in line, or pick up changes to this repo | `mise run apply` |
| Run just one area | `mise run role browsers` (several at once: `mise run role gcc,llvm`) |
| Update installed software (APT, Flatpak, Neovim and tmux plugins; reports firmware and Mise updates) | `mise run update` |
| Pull dotfile changes (also installs updated Mise tools) | `chezmoi update` |
| Upgrade runtimes and CLIs pinned in your dotfiles | `mise run update -- --only mise --bump-mise`, then review and commit your dotfiles |
| Check this repo's pinned downloads for new releases | `mise run pins` (update one with `mise run pins -- --bump NAME`) |

Extra Ansible options go after `--`, for example `mise run apply -- --diff` or `mise run plan -- --tags system`. See [docs/updating.md](docs/updating.md) for what each update covers and how to roll back.

### Roles and tags

Use any of these names with `mise run role <name>`:

| Role (extra tags) | What it sets up |
| :--- | :--- |
| `system` | APT upgrades, UFW firewall, `dialout`/`plugdev` groups, `systemd-oomd`, journald retention, GitHub CLI, optional NVMe udev rule |
| `snapper` | Snapper root snapshots, `snapper-rollback`, snapshots around APT upgrades, optional hibernation |
| `toolchains` (`gcc`, `llvm`, `arm`, `avr`, `riscv`) | Newest GCC/G++ and LLVM/Clang (from `apt.llvm.org`), linked into `~/.local/bin`, plus ARM, AVR and RISC-V cross-compilers |
| `containers` (`podman`) | Rootless Podman with `docker.io` as the default search registry, plus Fish completion |
| `browsers` (`firefox`, `chrome`, `brave`) | Firefox Developer Edition (Mozilla's APT repo) and privacy policies for it and Pop's Firefox (Strict tracking protection, no telemetry, uBlock Origin; change them with `firefox_policies` in `local.yml`), Google Chrome, and Brave with its Rewards, Wallet, VPN, Leo AI, Talk, News and usage reporting switched off by policy (change the list with `brave_policies` in `local.yml`) |
| `desktop_apps` (`fonts`, `vscode`, `kicad`, `flatpak`, `arduino`, `saleae`, `synology`, `hyper_backup`, `cosmic_swipe`) | JetBrainsMono Nerd Font, VS Code, KiCad 10 with `kicad-python`, FreeCAD and OrcaSlicer (Flathub), Arduino IDE 2, Saleae Logic 2, Synology Drive, Hyper Backup Explorer, and on COSMIC the cosmic-swipe four-finger gestures (with a touchpad-only udev rule, so no `input` group) |
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

The script was written for one machine's Samsung 970 EVO. With no drive given, it looks for that drive; for any other drive, pass its `/dev/disk/by-id/…` path. It always installs the Samsung power-management udev rule. That rule only affects Samsung 970 EVO controllers and the PCIe port each one is attached to, so it does nothing on a machine without one.

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
| `apply` stops with "These files changed since Chezmoi last wrote them…" | An app rewrote a dotfile, for example a COSMIC Terminal window opened before the fonts were installed resets `font_name`. Close and reopen that app. Then, for each listed path, check `chezmoi diff ~/<path>` and either keep the dotfiles version (`chezmoi apply --force ~/<path>`) or keep yours (`chezmoi re-add ~/<path>`) |
| `mise run auth` says `GDRIVE_CLIENT_ID` isn't a Google OAuth client ID, or the browser says "The OAuth client was not found" | `~/.config/rclone/gdrive-oauth.env` in your dotfiles still holds placeholder values, or the client was deleted. Put your Google Cloud OAuth client ID and secret in it, run `chezmoi add --encrypt ~/.config/rclone/gdrive-oauth.env`, then `mise run auth -- --step gdrive` |
| "Permission denied" on a serial port or debug probe | Log out and back in to pick up the new groups, then reconnect the device |
| After `systemctl suspend-then-hibernate`, the laptop wakes after an hour and stays on instead of hibernating | A known systemd bug ([systemd#38193](https://github.com/systemd/systemd/issues/38193)) that hits some cycles, not all. `enable_hibernation` installs a sleep hook that works around it; rerun `mise run role snapper` if the laptop was set up before the hook existed. `journalctl -b -u systemd-suspend-then-hibernate.service | grep pop-workstation-s2h-timer` shows, for each wake, how far it was from the deadline and whether the hook waited. If it still stays on, suspend again, or use `systemctl hibernate` when the laptop goes in a bag. |
| GitHub CLI suddenly signed out after restoring a backup | Don't restore an old `~/.local/share/keyrings/` over a new install, because it replaces the GitHub CLI token. Sign in again with `mise run auth -- --step github`. |

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
docs/                   Guides and design notes: updating.md, v2-migration-plan.md, phase4-chezmoi-guide.md
```

Runtimes and developer CLIs (`go`, `node`, `rust`, `uv`, `neovim`, `starship`, `ripgrep`, `fd`, `bat`, `fzf`, `zoxide`, `herdr`, `codex`, `copilot` and more) are deliberately **not** managed here. They live in your private dotfiles repo as `~/.config/mise/config.toml` and `mise.lock`. See [docs/phase4-chezmoi-guide.md](docs/phase4-chezmoi-guide.md) for how that repo is laid out.

## Checking changes to this repo

```bash
mise run check    # or: bash scripts/check.sh
```

This runs `shellcheck`, Bash syntax checks, Python and YAML parsing, `ansible-playbook --syntax-check`, `ansible-lint`, and the unit tests. CI runs the same checks, plus a full playbook test in `ubuntu:24.04` and `ubuntu:26.04` containers. That test previews the changes, applies them, then confirms that both a second preview and a second apply report no changes.

To run the full greenfield end-to-end test locally in a disposable `ubuntu:24.04` or `ubuntu:26.04` container (requires Podman and network access; exercises `bootstrap.sh`, `desktop_apps`, `auth.sh` with ephemeral fixtures, Day-2 convergence, `lazy-lock.json` merge, `stm32`, and `mise run role`):

```bash
podman run --rm -v "$PWD:/workspace:ro,Z" docker.io/library/ubuntu:24.04 \
  bash /workspace/scripts/e2e_container_test.sh /workspace
```

Use `ubuntu:26.04` in place of `ubuntu:24.04` to test the newer release. For the faster CI-only container test (skips `desktop_gui`, `auth.sh`, and `stm32`), substitute `scripts/ci_container_test.sh`.
