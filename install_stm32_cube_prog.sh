#!/usr/bin/env bash
set -euo pipefail

ARCHIVE_ZIP=$(ls SetupSTM32CubeProgrammer_*.zip | head -n 1)

echo "==> Extracting $ARCHIVE_ZIP..."
unzip -q "$ARCHIVE_ZIP"

INSTALLER=$(ls SetupSTM32CubeProgrammer-*.linux | head -n 1)
chmod +x "$INSTALLER"

echo "==> Generating response configuration..."
cat <<'EOF' >installer.auto
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
<com.st.CustomPanels.CheckedHelloPorgrammerPanel id="Hello.panel"/>
<com.izforge.izpack.panels.info.InfoPanel id="Info.panel"/>
<com.izforge.izpack.panels.licence.LicencePanel id="Licence.panel"/>
<com.st.CustomPanels.TargetProgrammerPanel id="target.panel">
<installpath>/home/dsscheib/STMicroelectronics/STM32Cube/STM32CubeProgrammere</installpath>
</com.st.CustomPanels.TargetProgrammerPanel>
<com.st.CustomPanels.AnalyticsPanel id="analytics.panel"/>
<com.st.CustomPanels.PacksProgrammerPanel id="Packs.panel">
<pack index="0" name="Core Files" selected="true"/>
<pack index="1" name="STM32CubeProgrammer" selected="true"/>
<pack index="2" name="STM32TrustedPackageCreator" selected="true"/>
</com.st.CustomPanels.PacksProgrammerPanel>
<com.izforge.izpack.panels.install.InstallPanel id="Install.panel"/>
<com.izforge.izpack.panels.shortcut.ShortcutPanel id="Shortcut.panel">
<createMenuShortcuts>true</createMenuShortcuts>
<programGroup>STMicroelectronics\STM32CubeProgrammer</programGroup>
<createDesktopShortcuts>true</createDesktopShortcuts>
<createStartupShortcuts>false</createStartupShortcuts>
<shortcutType>user</shortcutType>
</com.izforge.izpack.panels.shortcut.ShortcutPanel>
<com.st.CustomPanels.FinishProgrammerPanel id="finish.panel"/>
</AutomatedInstallation>
EOF

echo "==> Installing STM32CubeProgrammer silently..."
sudo ./"$INSTALLER" -f installer.auto

# Clean up installer files
rm -f "$INSTALLER" installer.auto

echo "==> Adding CLI tool to PATH..."
CLI_PATH="/usr/local/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin"

if [ -d "$CLI_PATH" ]; then
    # Add link to system path for easy CLI execution
    sudo ln -sf "$CLI_PATH/STM32_Programmer_CLI" /usr/local/bin/STM32_Programmer_CLI
    echo "==> STM32_Programmer_CLI successfully linked to /usr/local/bin!"
fi
