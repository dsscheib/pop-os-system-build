#!/usr/bin/env bash
set -euo pipefail

# Target installation directory
TARGET_DIR="$HOME/STMicroelectronics/STM32Cube/STM32CubeProgrammer"
DESKTOP_DIR="$HOME/.local/share/applications"

# 1. Clean up old artifacts and extract zip archive
rm -f SetupSTM32CubeProgrammer-*.linux SetupSTM32CubeProgrammer-*.bin SetupSTM32CubeProgrammer-*.exe 2>/dev/null || true

ARCHIVE_ZIP=$(ls SetupSTM32CubeProgrammer_*.zip SetupSTM32CubeProgrammer-*.zip 2>/dev/null | head -n 1 || true)

if [ -z "$ARCHIVE_ZIP" ]; then
  echo "Error: No 'SetupSTM32CubeProgrammer_*.zip' file found in current directory."
  exit 1
fi

echo "==> Extracting $ARCHIVE_ZIP..."
unzip -q -o "$ARCHIVE_ZIP"

# 2. Identify the Linux installer binary (explicitly ignoring Windows .exe installers)
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
rm -f "$INSTALLER" "$ARCHIVE_ZIP" installer.auto *.exe
rm -rf jre

# 6. Add CLI binaries and GUI wrapper to ~/.local/bin
mkdir -p "$HOME/.local/bin"

if [ -d "$TARGET_DIR/bin" ]; then
  # Symlink the CLI tool and Trusted Package Creator
  ln -sf "$TARGET_DIR/bin/STM32_Programmer_CLI" "$HOME/.local/bin/STM32_Programmer_CLI"
  
  if [ -f "$TARGET_DIR/bin/STM32TrustedPackageCreator_CLI" ]; then
    ln -sf "$TARGET_DIR/bin/STM32TrustedPackageCreator_CLI" "$HOME/.local/bin/STM32TrustedPackageCreator_CLI"
  fi
fi

# Create GUI wrapper script with Java Wayland/GTK compatibility flags
cat << 'EOF' > "$HOME/.local/bin/stm32cubeprogrammer"
#!/usr/bin/env bash
export GDK_BACKEND=x11
export _JAVA_OPTIONS="-Djdk.gtk.version=2"
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
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
TMP_PROG_ICON="/tmp/prog_icon_extract"

mkdir -p "$ICON_DIR" "$DESKTOP_DIR" "$TMP_PROG_ICON"

# Target ONLY Programmer launchers to avoid deleting CubeIDE or CubeMX
rm -f "$DESKTOP_DIR"/st-com-stm32cubeprogrammer.desktop "$DESKTOP_DIR"/STM32CubeProgrammer*.desktop

# Locate the primary application JAR and extract internal PNG icons
PROG_JAR=$(find "$TARGET_DIR" -type f -name "*.jar" ! -path "*/jre/*" 2>/dev/null | head -n 1 || true)

if [ -n "$PROG_JAR" ]; then
  unzip -q -o "$PROG_JAR" "*icon*.png" "*Programmer*.png" "*logo*.png" -d "$TMP_PROG_ICON" 2>/dev/null || true
  
  # Select the largest extracted PNG image
  PROG_EXTRACTED=$(find "$TMP_PROG_ICON" -type f -name "*.png" -exec ls -s {} + 2>/dev/null | sort -nr | head -n 1 | awk '{print $2}' || true)
  if [ -n "$PROG_EXTRACTED" ]; then
    cp -f "$PROG_EXTRACTED" "$ICON_DIR/stm32cubeprogrammer.png"
    echo "✔ STM32CubeProgrammer icon extracted and installed."
  fi
fi

rm -rf "$TMP_PROG_ICON"

# Fallback branding image if archive extraction fails
if [ ! -f "$ICON_DIR/stm32cubeprogrammer.png" ]; then
  curl -sSL "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e7/STMicroelectronics_logo.svg/512px-STMicroelectronics_logo.svg.png" -o "$ICON_DIR/stm32cubeprogrammer.png" 2>/dev/null || true
fi

# Generate launcher with absolute icon path
cat << EOF > "$DESKTOP_DIR/st-com-stm32cubeprogrammer.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=STM32CubeProgrammer
Comment=STMicroelectronics Flash Programming Tool for STM32
Exec=env GDK_BACKEND=x11 _JAVA_OPTIONS="-Djdk.gtk.version=2" $TARGET_DIR/bin/STM32CubeProgrammerLauncher %F
Path=$TARGET_DIR/bin
Icon=$ICON_DIR/stm32cubeprogrammer.png
Terminal=false
Categories=Development;IDE;
StartupWMClass=com-st-stm32cube-programmer-STM32CubeProgrammer
EOF

chmod +x "$DESKTOP_DIR/st-com-stm32cubeprogrammer.desktop"

# Refresh icon cache & restart COSMIC app library service
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
if command -v update-desktop-database &>/dev/null; then
  update-desktop-database "$DESKTOP_DIR" || true
fi
killall cosmic-app-library 2>/dev/null || true

echo "==> Installation complete!"
echo "==> CLI linked to $HOME/.local/bin/STM32_Programmer_CLI"
echo "==> GUI Wrapper created at $HOME/.local/bin/stm32cubeprogrammer"
echo "==> Launcher created at $DESKTOP_DIR/st-com-stm32cubeprogrammer.desktop"
