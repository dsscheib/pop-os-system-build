#!/usr/bin/env bash
set -euo pipefail

# Target installation directory
TARGET_DIR="$HOME/STMicroelectronics/STM32Cube/STM32CubeProgrammer"
DESKTOP_DIR="$HOME/.local/share/applications"

# 1. Locate the downloaded zip archive
ARCHIVE_ZIP=$(ls SetupSTM32CubeProgrammer_*.zip SetupSTM32CubeProgrammer-*.zip 2>/dev/null | head -n 1 || true)

if [ -z "$ARCHIVE_ZIP" ]; then
  echo "Error: No 'SetupSTM32CubeProgrammer_*.zip' file found in current directory."
  exit 1
fi

echo "==> Extracting $ARCHIVE_ZIP..."
unzip -q -o "$ARCHIVE_ZIP"

# 2. Identify the Linux installer binary
INSTALLER=$(find . -maxdepth 2 \( -name "SetupSTM32CubeProgrammer-*.linux" -o -name "SetupSTM32CubeProgrammer-*.bin" -o -name "*.linux" \) ! -name "*.exe" ! -name "*.zip" -type f 2>/dev/null | head -n 1 || true)

if [ -z "$INSTALLER" ]; then
  echo "Error: SetupSTM32CubeProgrammer Linux binary not found after extraction."
  exit 1
fi

chmod +x "$INSTALLER"

# 3. Generate response configuration (unquoted EOF allows ${TARGET_DIR} expansion)
echo "==> Generating response configuration..."
mkdir -p "$TARGET_DIR"

cat <<EOF >installer.auto
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
<com.st.CustomPanels.CheckedHelloPorgrammerPanel id="Hello.panel"/>
<com.izforge.izpack.panels.info.InfoPanel id="Info.panel"/>
<com.izforge.izpack.panels.licence.LicencePanel id="Licence.panel"/>
<com.st.CustomPanels.TargetProgrammerPanel id="target.panel">
<installpath>${TARGET_DIR}</installpath>
</com.st.CustomPanels.TargetProgrammerPanel>
<com.st.CustomPanels.AnalyticsPanel id="analytics.panel"/>
<com.st.CustomPanels.PacksProgrammerPanel id="Packs.panel">
<pack index="0" name="Core Files" selected="true"/>
<pack index="1" name="STM32CubeProgrammer" selected="true"/>
<pack index="2" name="STM32TrustedPackageCreator" selected="true"/>
</com.st.CustomPanels.PacksProgrammerPanel>
<com.izforge.izpack.panels.install.InstallPanel id="Install.panel"/>
<com.izforge.izpack.panels.shortcut.ShortcutPanel id="Shortcut.panel">
<createMenuShortcuts>false</createMenuShortcuts>
<programGroup>STMicroelectronics\STM32CubeProgrammer</programGroup>
<createDesktopShortcuts>false</createDesktopShortcuts>
<createStartupShortcuts>false</createStartupShortcuts>
<shortcutType>user</shortcutType>
</com.izforge.izpack.panels.shortcut.ShortcutPanel>
<com.st.CustomPanels.FinishProgrammerPanel id="finish.panel"/>
</AutomatedInstallation>
EOF

# 4. Execute the silent installer in user space
echo "==> Installing STM32CubeProgrammer silently..."
./"$INSTALLER" -f installer.auto

# 5. Clean up temporary installer artifacts
rm -f "$INSTALLER" installer.auto

# 6. Add CLI binaries and GUI wrapper to ~/.local/bin
mkdir -p "$HOME/.local/bin"

if [ -d "$TARGET_DIR/bin" ]; then
  # Symlink the CLI tool and Trusted Package Creator
  ln -sf "$TARGET_DIR/bin/STM32_Programmer_CLI" "$HOME/.local/bin/STM32_Programmer_CLI"
  
  if [ -f "$TARGET_DIR/bin/STM32TrustedPackageCreator_CLI" ]; then
    ln -sf "$TARGET_DIR/bin/STM32TrustedPackageCreator_CLI" "$HOME/.local/bin/STM32TrustedPackageCreator_CLI"
  fi
fi

# Create GUI wrapper script in ~/.local/bin
cat << 'EOF' > "$HOME/.local/bin/stm32cubeprogrammer"
#!/usr/bin/env bash
cd "$HOME/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin" && ./STM32CubeProgrammerLauncher "$@"
EOF
chmod +x "$HOME/.local/bin/stm32cubeprogrammer"

# 7. Install udev rules if present inside the installation directory
UDEV_RULE=$(find "$TARGET_DIR" -name "*stlink*.rules" 2>/dev/null | head -n 1 || true)
if [ -n "$UDEV_RULE" ]; then
  echo "==> Installing ST-LINK udev rules..."
  sudo cp "$UDEV_RULE" /etc/udev/rules.d/
  sudo udevadm control --reload-rules || true
  sudo udevadm trigger || true
fi

# 8. Create desktop launcher (.desktop) & set up icon
echo "==> Creating .desktop launcher..."
mkdir -p "$DESKTOP_DIR"
mkdir -p "$HOME/.local/share/icons/hicolor/256x256/apps"

ICON_SRC=$(find "$TARGET_DIR" -type f \( -name "STM32CubeProgrammer.png" -o -name "icon.png" -o -name "logo.png" \) 2>/dev/null | head -n 1 || true)

if [ -n "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$HOME/.local/share/icons/hicolor/256x256/apps/stm32cubeprogrammer.png"
fi

cat << EOF > "$DESKTOP_DIR/st-com-stm32cubeprogrammer.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=STM32CubeProgrammer
Comment=STMicroelectronics Flash Programming Tool for STM32
Exec=$TARGET_DIR/bin/STM32CubeProgrammerLauncher %F
Path=$TARGET_DIR/bin
Icon=stm32cubeprogrammer
Terminal=false
Categories=Development;IDE;
StartupWMClass=com-st-stm32cube-programmer-STM32CubeProgrammer
EOF

chmod +x "$DESKTOP_DIR/st-com-stm32cubeprogrammer.desktop"

# Refresh icon cache & application list
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
if command -v update-desktop-database &>/dev/null; then
  update-desktop-database "$DESKTOP_DIR" || true
fi

echo "==> Installation complete!"
echo "==> CLI linked to $HOME/.local/bin/STM32_Programmer_CLI"
echo "==> GUI Launcher created at $DESKTOP_DIR/st-com-stm32cubeprogrammer.desktop"#!/usr/bin/env bash
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
rm -f "$INSTALLER" installer.auto *.exe

echo "==> Adding CLI tool to PATH..."
CLI_PATH="/usr/local/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin"

if [ -d "$CLI_PATH" ]; then
    # Add link to system path for easy CLI execution
    sudo ln -sf "$CLI_PATH/STM32_Programmer_CLI" /usr/local/bin/STM32_Programmer_CLI
    echo "==> STM32_Programmer_CLI successfully linked to /usr/local/bin!"
fi
