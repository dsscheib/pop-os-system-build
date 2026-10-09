# Pop!_OS Installation and Setup

> ### Automated instructions and scripts for reproducible system rebuilds on Pop!_OS

---

## 1. Base Installation

Install Pop!_OS using **Advanced (Custom)** partitioning:

#### Partitioning Layout:
| Device | Size | Type | Mount Point | Label |
| :--- | :--- | :--- | :--- | :--- |
| `/dev/nvme0n1p1` | 2G | boot/efi | `/boot/efi` | `EFI` |
| `/dev/nvme0n1p2` | 4G | fat32 | `/recovery` | `RECOVERY` |
| `/dev/nvme0n1p3` | `<remaining>` | btrfs | `/` | `ROOT` |

> [!CAUTION]
> After the installer completes, **DO NOT REBOOT!**

Open a terminal in the Live USB environment:
```bash
# Create Btrfs subvolumes '/@', '/@home', and '/@snapshots', update fstab & boot flags:
sudo ./setup_btrfs_live_install.sh /dev/nvme0n1p3

# Reboot into the new installation
sudo reboot
```

---

## 2. Configure Btrfs and Snapper

After booting into your installed system, configure Snapper automatic snapshots and rollback tooling:

```bash
sudo ./setup_btrfs_snapper.sh
```

This sets up:
- Snapper configuration for `root` backing `/.snapshots` (`@snapshots` subvolume)
- Sudo group permissions for non-root inspection via `snapper-gui`
- Paired pre/post APT snapshot hooks (`/etc/apt/apt.conf.d/80snapper`)
- `snapper-rollback` utility (`/usr/local/bin/snapper-rollback`)
- Automated cleanup and boot timers

---

## 3. Post-Install Configuration & Software

### Configuration Setup
Copy the template configuration file to `config.env` and adjust your identity:
```bash
cp config.env.example config.env
# Edit config.env with your details (git name, email, dotfiles & nvim repos)
nano config.env
```
*(Note: `config.env` is ignored by Git to preserve privacy across rebuilds.)*

### Run Configuration Script
```bash
./pop_os_config.sh
```

This automates:
- UFW firewall & adding current user to `dialout` and `plugdev` groups
- JetBrainsMono Nerd Font
- Git Credential Manager with non-interactive GPG and `pass`
- Modern CLI tools (`mise`, `uv`, `starship`, `eza`, `ripgrep`, `zoxide`, `fastfetch`, `bat`, `btm`)
- Tmux & TPM (Tmux Plugin Manager)
- Rust toolchain (`rustup`, `rust-analyzer`), Go (via `mise`), Node.js LTS, and Python/Django
- Neovim with AstroNvim (preserves existing configs without destructive deletion)
- Official repositories: VS Code and Firefox Developer Edition
- Hardware toolchains: AVR, ARM (`gcc-arm-none-eabi`, `openocd`, `stlink`), and RISC-V
- Saleae Logic 2 AppImage with automatic udev rules

---

## 4. Install STMicroelectronics Dev Tools

Download the required installers from STMicroelectronics (leave them in `~/Downloads` or the script directory):
- **STM32CubeIDE**: <https://www.st.com/en/development-tools/stm32cubeide.html>
- **STM32CubeMX**: <https://www.st.com/en/development-tools/stm32cubemx.html>
- **STM32CubeProgrammer**: <https://www.st.com/en/development-tools/stm32cubeprog.html>
- **System Workbench for STM32**: <https://www.openstm32.org/System+Workbench+for+STM32>

Each script leverages [`common.sh`](file:///home/dsscheib/github/pop-os-system-build/common.sh) for shared utilities (archive search in `$PWD` and `~/Downloads`, high-resolution icon conversion, ST-LINK udev rules, Wayland compatibility flags, and desktop cache refresh):

```bash
# Install STM32CubeIDE
./install_stm32_cube_ide.sh

# Install STM32CubeMX (includes Wayland compatibility wrapper)
./install_stm32_cube_mx.sh

# Install STM32CubeProgrammer (includes CLI links & Wayland compatibility wrapper)
./install_stm32_cube_prog.sh

# Install System Workbench for STM32
./install_sw4stm32.sh
```

*(You can also pass an explicit path to any installer: e.g. `./install_stm32_cube_ide.sh /path/to/archive.zip`)*
