# Pop!_OS Installation and Workstation Setup

Scripts for a UEFI Btrfs installation, Snapper snapshots, workstation tools, and
user-space STMicroelectronics applications. Workstation and STM32 setup support
**Pop!_OS/Ubuntu 22.04 and 24.04 on x86_64**. Snapper setup also supports Debian
with the required packages and the exact subvolume layout described below.

## 1. Install Pop!_OS

Use Advanced (Custom) partitioning on a GPT disk. Example layout:

| Device | Size | Filesystem | Mount point | Label |
| :--- | :--- | :--- | :--- | :--- |
| `/dev/nvme0n1p1` | 2 GiB | FAT32, EFI System Partition | `/boot/efi` | `EFI` |
| `/dev/nvme0n1p2` | 4 GiB | FAT32 | `/recovery` | `RECOVERY` |
| `/dev/nvme0n1p3` | Remaining space | Btrfs | `/` | `ROOT` |

After the installer finishes, **stay in the live environment before the first
reboot**. Unmount the installer's target root and EFI mounts, and run:

```bash
sudo ./setup_btrfs_live_install.sh /dev/nvme0n1p3
```

The script accepts a fresh installation directly in Btrfs subvolume ID 5. It
rejects existing subvolumes, separate `/home` or `/boot` filesystems, and existing
snapshot data. It resolves `os-release` inside the target, validates kernel and
initramfs pairs, and conservatively requires enough free space for an additional
installation-sized copy plus 256 MiB of metadata headroom.

EFI is resolved from the installed `fstab`, or from a unique ESP on the root
partition's disk. To specify it explicitly, including mapped/encrypted roots:

```bash
sudo ./setup_btrfs_live_install.sh /dev/nvme0n1p3 /dev/nvme0n1p1
```

The conversion creates `@`, `@home`, and `@snapshots`, preserves directory
permissions/ACLs/xattrs, backs up the old configuration, and validates the updated
boot files. It selects `Pop_OS-current.conf` in the target ESP's `loader.conf`.
It does not require the live USB to have booted through systemd-boot. Configuration
backups are stored under `/etc/fstab.pre-btrfs.*` and
`/etc/kernelstub/*.pre-btrfs.*` in the installed root.

Both conversion and boot repair ensure the installed system has `btrfs` and
`fsck.btrfs` before rebuilding initramfs. If either utility is missing, the script
updates APT indexes and installs (or reinstalls) `btrfs-progs` inside the target
system. This requires working networking and package repositories in the chroot;
package failures stop the script. If both utilities already exist, APT is skipped.

Reboot only after success. If conversion fails after moving data, stay in the live
environment, mount the filesystem with `subvolid=5`, and inspect/repair the partial
layout and boot configuration before rerunning or rebooting. Existing layouts are
never automatically merged. No destructive automatic rollback is attempted.

If the conversion finished but boot configuration failed (including the former
`Not booted with a supported boot loader.` error), update this repository and run
boot repair from the UEFI live environment, using the same devices as before:

```bash
sudo ./setup_btrfs_live_install.sh --repair-boot /dev/nvme0n1p3 /dev/nvme0n1p1
```

Use your actual root and EFI devices. This mode requires existing `@`, `@home`,
and `@snapshots` subvolumes with no leftover top-level data. It backs up and
repairs fstab and boot configuration, rebuilds initramfs, and verifies the boot
files without moving data or recreating subvolumes. It rejects partial layouts
requiring manual inspection. Reboot only after it reports success. A previously
saved systemd-boot EFI default can override `loader.conf`; if needed, choose
`Pop_OS-current` in the boot menu and make it the default from the installed OS.

## 2. Configure Snapper after boot

```bash
sudo ./setup_btrfs_snapper.sh
```

This requires `/` to mount `/@` and `/.snapshots` to mount `/@snapshots` from the
same Btrfs filesystem, before any packages or configuration are changed. Setup
preserves other registered Snapper configs and existing retention settings,
configures ACL access for administrators, and creates/deletes a validation
snapshot. New root configs retain 10 ordinary and 5 important numbered snapshots
with a minimum age of 1800 seconds; timeline creation is disabled.

The rollback utility is installed from a fixed Git commit. The rollback device
uses `/dev/disk/by-uuid/…` so device renumbering does not change its target.

APT invokes `/usr/local/libexec/pop-snapper-apt-hook` using the validated
`80pop-snapper` configuration. Distribution-provided snapshot hooks are disabled
through `DISABLE_APT_SNAPSHOT=yes` to avoid duplicate snapshots. An invalid hook
written by older versions of this repository is recognized and archived under
`/etc/apt/pop-btrfs-backups` before APT is used. Snapshot failures are printed to
stderr but allow package installation to continue. Interrupted package operations
may leave an unpaired pre-snapshot; numbered cleanup handles retention.

The cleanup timer is required. A missing distribution boot-snapshot timer is
reported explicitly. Test an actual rollback and reboot in a disposable VM before
relying on rollback for recovery; root snapshots do not include the FAT EFI files
or `/home`.

## 3. Configure the workstation as your desktop user

```bash
cp config.env.example config.env
# Edit identity and repository settings before running the corresponding stages.
./pop_os_config.sh
```

Do not use `sudo` for this script. It installs user files in your account and uses
`sudo` only for individual system operations. `config.env` is trusted Bash code;
keep it private and do not store passphrases in it.

List or select stages:

```bash
./pop_os_config.sh --list-stages
./pop_os_config.sh --stages dependencies,languages,neovim
./pop_os_config.sh --stages system,arm,avr,riscv
./pop_os_config.sh --stages antigravity,claudecode,codex
```

The `arm` stage includes ARM32 bare-metal tools (`arm-none-eabi-gcc`),
the AArch64 Linux cross-compiler (`aarch64-linux-gnu-gcc`) and binutils,
`gdb-multiarch`, OpenOCD, and ST-LINK tools. This supports the corresponding
GAS assembly task targets in the AstroNvim configuration. The separate `avr`
stage installs AVR GCC/binutils, avr-libc, avrdude, and `gdb-avr`. The default setup selects
both stages. Existing `--stages arm-avr` commands still run both as a compatibility
option.

All selections install the small prerequisite package set first. The `git` stage
requires your own name, email, and GitHub username. The `dotfiles` and `neovim`
stages require the respective repository URLs. Set `DOTFILES_REF` and
`NVIM_CONFIG_REF` to commits for reproducible personal configurations; otherwise
clean checkouts update with `--ff-only`. Foreign origins and local changes are
reported as errors rather than silently ignored.

The `dependencies` stage includes NASM for x86-64 assembly tasks, ShellCheck,
DNS/Netcat tools, the NetworkManager OpenVPN GUI plugin, rsync, wget, Kitty,
and libsecret tools. It installs Fastfetch from APT when a candidate exists;
otherwise it installs the pinned upstream Debian package after checking its
SHA-256. An existing Fastfetch command is kept. Bash integration loads the
installed `bash-completion` package for APT tab completion; linked `.bashrc`
files must load completion themselves or source the managed integration file.

Native build tools use the latest candidates from the configured APT repositories.
Setup selects the highest available GCC major with an installable matching G++
package, then installs/upgrades that pair, `build-essential`, binutils, Make,
dpkg-dev, and libc development headers. It does not add a toolchain PPA or build
upstream releases from source. Repository availability and APT pinning determine
the versions; Ubuntu's default `gcc` package can select an older major than the
newest packaged compiler.

Managed commands under `~/.local/bin` select the chosen compiler for `gcc`, `g++`,
`cc`, `c++`, and the matching GCC archive/coverage tools. Keep that directory first
on `PATH`; custom compiler wrappers there require manual review. System compiler
links and existing compiler packages are preserved. Older versions may be pulled
in by `build-essential` or other package dependencies. Cross-compilers in the
ARM/AVR/RISC-V stages also use their current APT candidates and keep their target
prefixes.

The default setup includes three independent AI CLI stages:

| Stage | Command | Installation |
| :--- | :--- | :--- |
| `antigravity` | `agy` | Verified, pinned official Antigravity CLI release |
| `claudecode` | `claude` | Anthropic's signed APT repository, latest channel |
| `codex` | `codex` | Pinned official `@openai/codex` npm package |

These stages install CLIs only. Antigravity's desktop app and standalone IDE are
separate products and are not installed. Each stage checks `--version` and stops
on a missing or broken CLI. An existing CLI on `PATH` is verified and preserved.
No sign-in runs during setup; launch `agy`, `claude`, or `codex` afterward to
authenticate with your account.

Antigravity and Codex install under `~/.local/share/pop-workstation` with launchers
in `~/.local/bin`, preserving the project's working directory. Codex installs its
Node runtime through Mise when a suitable Node/npm pair is unavailable, so the
stage can run without selecting `languages`. Its launcher uses the selected Node
executable directly. Claude Code's native APT package does not require Node.
Network access is required for these downloads and package repositories.

Installation sources: [Antigravity CLI](https://www.antigravity.google/docs/cli/install/),
[Claude Code](https://code.claude.com/docs/en/setup#install-with-linux-package-managers),
and [Codex CLI](https://learn.chatgpt.com/docs/codex/cli). Antigravity's SHA-256 pin
was calculated after verifying the archive against Google's published SHA-512
manifest. Anthropic's signing key is checksum-pinned; its fingerprint is
`31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE`. Update release pins deliberately;
Claude Code's APT package follows its repository's latest channel.

The personal `dotfiles` and `astronvim_v6` repositories have recently been fixed
but still need their own validation. This repository's checks do not execute or
validate their bootstrap, plugins, or language servers. To install tools while
keeping those deployments separate, use:

```bash
./pop_os_config.sh --stages dependencies,languages,arm,avr,riscv
```

The `dotfiles` stage executes its bootstrap; the `neovim` stage deploys the selected
configuration and runs its `scripts/setup.sh --install` when the matching
`lua/setup_checks.lua` helper is present. That command waits for configured Mason
tools and reports missing packages. Other configurations receive a bounded
headless initialization only. Verify actual project-file LSP attachment and
formatter availability using the configuration's documented checks; installation
alone does not establish that the complete editor configuration works.

When the `tmux` stage is selected, the dotfiles bootstrap delegates plugin setup
to that stage. Both repositories use `~/.local/share/tmux/plugins` and the same
TPM pin. A `dotfiles`-only selection lets its bootstrap install plugins normally.

Git setup uses a specific secret-key fingerprint and a properly initialized
`pass` store. It asks GPG to obtain the key passphrase interactively. Authenticate
with GitHub separately using `git-credential-manager github login`.

Tool versions are pinned in [resources/tool-versions.env](resources/tool-versions.env).
Download URLs and SHA-256 values are pinned in
[resources/downloads.json](resources/downloads.json). Update both the version and
checksum after reviewing a new upstream release. Release asset digests were
obtained from upstream GitHub metadata; Rustup uses its published checksum. The
Saleae/Synology downloads and repository keys have SHA-256 baselines from their
HTTPS upstream endpoints, recorded on 2026-10-13. A checksum detects an unexpected
artifact; it does not independently authenticate its publisher.

Local Debian packages are copied into a temporary root-owned directory readable
by APT's `_apt` user, then removed after installation. Download workspaces remain
private. This avoids APT's “Download is performed unsandboxed as root” warning
when installing a verified package such as Fastfetch from a private workspace.

Neovim uses a separate Python virtual environment and a wrapper that selects that
provider while preserving the project working directory. npm providers install
under `~/.local`, without root. Go/Node run through Mise. Download failures cannot
replace a working application with a partial file. Optional package/plugin failures
are collected and listed at the end. The pinned AppImageLauncher release is an
upstream beta and remains an optional component.

Shell integration files are installed under `~/.config/pop-workstation`. Regular
Bash/Zsh startup files receive one source statement; linked dotfiles are preserved
and setup prints the statement to add to your own repository. Fish loads the
integration automatically. Zsh completion files use `~/.zsh/completion` and require
normal `compinit` initialization in your Zsh configuration. Restart the shell or
log out and back in after changing PATH or hardware group membership.

## 4. Install the supplied STMicroelectronics tools

The scripts automatically search these locations in order:

1. Current working directory.
2. `${ST_DOWNLOAD_DIR:-$HOME/github/st-microelectronics}`.
3. `~/Downloads`.

An explicit path takes precedence and must exist. Multiple matching downloads in
one directory require an explicit selection. The supplied installer hashes are
recorded in [resources/st-installers.sha256](resources/st-installers.sha256):

| Script | Supplied installer |
| :--- | :--- |
| `install_stm32_cube_ide.sh` | CubeIDE 2.2.0 Linux Debian archive |
| `install_stm32_cube_mx.sh` | CubeMX 6.18.1 Linux archive |
| `install_stm32_cube_prog.sh` | CubeProgrammer 2.23.0 Linux archive |
| `install_sw4stm32.sh` | Supplied SW4STM32 `.run` file with bundled JRE |

These hashes describe the supplied files, including the supplied SW4STM32 wrapper;
they are not a claim of an independently verified ST signature. The original
files in the download directory are never modified.

Install script prerequisites if the workstation dependencies have not been run:

```bash
sudo apt-get install python3 python3-pil desktop-file-utils libusb-1.0-0
```

Run without `sudo`:

```bash
./install_stm32_cube_ide.sh
./install_stm32_cube_mx.sh
./install_stm32_cube_prog.sh
./install_sw4stm32.sh
# Or select a specific file:
./install_stm32_cube_ide.sh "$HOME/github/st-microelectronics/stm32cubeide_2.2.0-Lin-Deb-x86_64.sh.zip"
```

CubeIDE selects its Debian package by metadata and extracts the ST-LINK server
alongside it. CubeMX's extensionless installer and CubeProgrammer's adjacent Java
payload are supported. The IzPack panel classes/IDs are checked against the payload
before installation, and XML paths are escaped. SW4STM32 uses its bundled JRE
directly to preserve the installer's exit status; its system driver/server packs
are deselected in favor of the reviewed repository rules.

Each version remains at its original directory under `.APPNAME.versions` so
embedded installer paths remain valid. The public application path becomes a
symlink only after required executables and runtime libraries pass validation.
Earlier directories/versions are retained. Launchers and wrappers quote paths and
forward arguments; an optional icon failure does not fail installation. Existing
unrelated desktop launchers and COSMIC caches are preserved.

The fixed ST-LINK rules in [resources/udev](resources/udev) were reviewed from the
supplied CubeIDE rules package. Saleae rules come from its official
[support asset](https://www.saleae.com/support-assets/99-SaleaeLogic.rules).
No installer rule directory or mutable remote rule is searched. Rule installation
uses `sudo`; reconnect the USB device afterward. Missing ELF libraries are reported
by name rather than claiming installation succeeded. `~/.local/bin` must be in your
PATH for CLI tools.

For another download directory, export `ST_DOWNLOAD_DIR` in your shell. A changed
installer requires an updated reviewed manifest, or an explicitly supplied hash:

```bash
ST_INSTALLER_SHA256='<reviewed SHA-256>' ./install_stm32_cube_mx.sh /path/to/new.zip
```

## Validation

```bash
bash scripts/check.sh
```

This runs ShellCheck, shell/Python syntax checks, and filesystem/command-stub
regressions. Test dependencies are ShellCheck, Python 3, `apt-config`, and
`desktop-file-validate`; Fish/Zsh syntax checks run when those shells are installed.
CI runs on Ubuntu 22.04 and 24.04.

Local checks do not run the real installers, mount disks, change system settings,
or prove that a converted system boots. Real GUI/USB checks and a disposable-VM
conversion/rollback/reboot test are still needed for those integration guarantees.
