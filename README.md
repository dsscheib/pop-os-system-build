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

Silent Installation of STM32CubeMX

```bash
tee auto_install.xml > /dev/null <<EOF
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
    <com.st.microxplorer.install.MXHTMLHelloPanel id="readme"/>
    <com.st.microxplorer.install.MXLicensePanel id="licence.panel"/>
    <com.st.microxplorer.install.MXAnalyticsPanel id="analytics.panel"/>
    <com.st.microxplorer.install.MXTargetPanel id="target.panel">
        <installpath>/usr/local/STMicroelectronics/STM32Cube/STM32CubeMX</installpath>
    </com.st.microxplorer.install.MXTargetPanel>
    <com.st.microxplorer.install.MXShortcutPanel id="shortcut.panel"/>
    <com.st.microxplorer.install.MXInstallPanel id="install.panel"/>
    <com.st.microxplorer.install.MXFinishPanel id="finish.panel"/>
</AutomatedInstallation>
EOF
```

```bash
unzip SetupSTM32CubeMX-*.zip
rm -f SetupSTM32CubeMX-*.zip
```
```bash
xhost +local:root
sudo -E ./SetupSTM32CubeMX-* auto_install.xml
```
```bash
rm -rf SetupSTM32CubeMX-*
```

Silent Installation of STM32CubeIDE

```bash
unzip stm32cubeide_*.zip
chmod +x stm32cubeide_*.sh
rm -f stm32cubeide_*.zip
```
```bash
# Run shell script installer headlessly
sudo apt update && sudo apt install -y zenity
yes | sudo ./stm32cubeide_*.sh --quiet --target /usr/local/STMicroelectronics/stm32cubeide
rm -f stm32cubeide_*.sh
```
```diff
--Not Tested!--
```

Fix the permissions (not typically needed)
```bash
sudo chmod -R a+rX /usr/local/STMicroelectronics/stm32cubeide
sudo apt update && sudo apt install -y libcanberra-gtk-module libcanberra-gtk3-module libgtk-3-0 zenity
sudo chown -R $USER:$USER ~/.stmcubeide 2>/dev/null || true
sudo chown -R $USER:$USER ~/.eclipse 2>/dev/null || true
sudo ln -sf /usr/local/STMicroelectronics/stm32cubeide/stm32cubeide /usr/local/bin/stm32cubeide
```
