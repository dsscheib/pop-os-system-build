#!/usr/bin/env bash
set -euo pipefail

# Define target installation directory
TARGET_DIR="$HOME/STMicroelectronics/STM32Cube/STM32CubeMX"

# 1. Locate the downloaded zip archive
ARCHIVE_ZIP=$(ls SetupSTM32CubeMX-*.zip 2>/dev/null | head -n 1 || true)

if [ -z "$ARCHIVE_ZIP" ]; then
  echo "Error: No 'en.stm32cubemx-*.zip' file found in current directory."
  exit 1
fi

echo "==> Extracting $ARCHIVE_ZIP..."
unzip -q -o "$ARCHIVE_ZIP"

# 2. Identify the Linux installer binary
INSTALLER=$(find . -maxdepth 2 \( -name "SetupSTM32CubeMX-*.linux" -o -name "SetupSTM32CubeMX-*.bin" -o -name "SetupSTM32CubeMX*" \) ! -name "*.zip" -type f 2>/dev/null | head -n 1 || true)

if [ -z "$INSTALLER" ]; then
  echo "Error: SetupSTM32CubeMX binary not found after extraction."
  exit 1
fi

chmod +x "$INSTALLER"

# 3. Create the IzPack response file
echo "==> Generating response configuration..."
cat <<'EOF' >auto-install.xml
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
    <com.st.microxplorer.install.MXHTMLHelloPanel id="readme"/>
    <com.st.microxplorer.install.MXLicensePanel id="licence.panel"/>
    <com.st.microxplorer.install.MXAnalyticsPanel id="analytics.panel"/>
    <com.st.microxplorer.install.MXTargetPanel id="target.panel">
        <installpath>${TARGET_DIR}</installpath>
    </com.st.microxplorer.install.MXTargetPanel>
    <com.st.microxplorer.install.MXShortcutPanel id="shortcut.panel"/>
    <com.st.microxplorer.install.MXInstallPanel id="install.panel"/>
    <com.st.microxplorer.install.MXFinishPanel id="finish.panel"/>
</AutomatedInstallation>
EOF

# 4. Execute the silent installer with elevated privileges
echo "==> Installing STM32CubeMX silently..."
sudo ./"$INSTALLER" auto-install.xml

# 5. Clean up temporary installer artifacts
rm -f "$INSTALLER" auto-install.xml

# 6. Add bin directory to user path or create local symlink
mkdir -p "$HOME/.local/bin"
ln -sf "$TARGET_DIR/STM32CubeMX" "$HOME/.local/bin/stm32cubemx"

echo "==> Installation complete!"
echo "==> Symlinked to $HOME/.local/bin/stm32cubemx"
