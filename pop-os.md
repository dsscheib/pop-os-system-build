# Pop! OS Installation and Setup

## Base Install
1. Install using advanced options
#### Partitioning:
    Device           Size           Type        Mount Point    Label
    /dev/nvme0n1p1   2G             boot/efi                   EFI
    /dev/nvme0n1p2   4G             fat32       /recovery      RECOVERY
    /dev/nvme0n1p3   <remaining>    btrfs       /              ROOT

2. After installation finishes **DO NOT REBOOT!**
3. Download `setup_btrfs_live_install.sh`
4. ```bash
   chmod +x setup_btrfs_live_install.sh && sudo ./setup_btrfs_live_install.sh /dev/nvme0n1p3
   ```
5. Reboot

## Configure Btrfs and Snapper
1. Download `setup_btrfs_snapper.sh`
2. ```bash
   chmod +x setup_btrfs_snapper.sh && sudo ./setup_btrfs_snapper.sh
   ```
## Finalize Configuration and Install Software
1. Download `pop_os_config.sh`
2. ```bash
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
xhost +local:root
sudo -E ./SetupSTM32CubeMX-* auto_install.xml
```

Silent Installation of STM32CubeIDE
```bash
unzip stm32cubeide_*.zip
chmod +x stm32cubeide_*.sh

# Run shell script installer headlessly
sudo apt update && sudo apt install -y zenity
yes | sudo ./stm32cubeide_*.sh --quiet --target /usr/local/STMicroelectronics/stm32cubeide
```

Fix the permissions
```bash
sudo chmod -R a+rX /usr/local/STMicroelectronics/stm32cubeide
sudo apt update && sudo apt install -y libcanberra-gtk-module libcanberra-gtk3-module libgtk-3-0 zenity
sudo chown -R $USER:$USER ~/.stmcubeide 2>/dev/null || true
sudo chown -R $USER:$USER ~/.eclipse 2>/dev/null || true
sudo ln -sf /usr/local/STMicroelectronics/stm32cubeide/stm32cubeide /usr/local/bin/stm32cubeide
```
