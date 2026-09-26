# Pop! OS Installation and Setup

> ### This is my personal set of instructions and scripts to rebuild my computers

## Base Install

Install Pop! OS using advanced options:
#### Partitioning:
    Device           Size           Type        Mount Point    Label
    /dev/nvme0n1p1   2G             boot/efi                   EFI
    /dev/nvme0n1p2   4G             fat32       /recovery      RECOVERY
    /dev/nvme0n1p3   <remaining>    btrfs       /              ROOT

>After installation finishes **DO NOT REBOOT!**

Create the Btrfs subvolumes '/@', '/@home', and '/@snapshots' and migrate to them
```bash
chmod +x setup_btrfs_live_install.sh && sudo ./setup_btrfs_live_install.sh /dev/nvme0n1p3
```
Reboot the system
```bash
sudo reboot
```

## Configure Btrfs and Snapper

```bash
chmod +x setup_btrfs_snapper.sh && sudo ./setup_btrfs_snapper.sh
```
## Finalize Configuration and Install Software

```bash
chmod +x pop_os_config.sh && ./pop_os_config.sh
```

## Install STMicroelectronics Dev Tools

Download Files:
- <https://www.st.com/en/development-tools/stm32cubeide.html>
- <https://www.st.com/en/development-tools/stm32cubemx.html>
- <https://www.st.com/en/development-tools/stm32cubeprog.html>

Install STM32CubeIDE
```bash
chmod +x install_stm32_cube_ide.sh && ./install_stm32_cube_ide.sh
```
Install STM32CubeMX
```bash
chmod +x install_stm32_cube_mx.sh && ./install_stm32_cube_mx.sh
```
Install STM32CubeProgrammer
```bash
chmod +x install_stm32_cube_prog.sh && ./install_stm32_cube_prog.sh
```
