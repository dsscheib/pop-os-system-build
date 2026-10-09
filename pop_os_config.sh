#!/usr/bin/env bash

# ==============================================================================
# POP!_OS POST-INSTALLATION AUTOMATION SCRIPT
# Hardened & Fully Automated Setup
# ==============================================================================

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

# Load local environment configuration if present
if [[ -f "$SCRIPT_DIR/config.env" ]]; then
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/config.env"
elif [[ -f "$SCRIPT_DIR/.env" ]]; then
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/.env"
fi

# Identity & Repository Configurations (with defaults)
GIT_NAME="${GIT_NAME:-Daniel Scheib}"
GIT_EMAIL="${GIT_EMAIL:-dsscheib@gmail.com}"
GITHUB_USER="${GITHUB_USER:-dsscheib}"
DOTFILES_REPO="${DOTFILES_REPO:-https://github.com/dsscheib/dotfiles.git}"
NVIM_CONFIG_REPO="${NVIM_CONFIG_REPO:-https://github.com/dsscheib/astronvim_v6}"

# Elevate privileges up front if needed
if [[ $EUID -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

# ------------------------------------------------------------------------------
# 1. SYSTEM UPDATE, FIREWALL & HARDWARE GROUPS
# ------------------------------------------------------------------------------
setup_system() {
  log_info "Updating system packages..."
  $SUDO apt update && $SUDO apt upgrade -y

  log_info "Enabling UFW Firewall..."
  $SUDO ufw --force enable || true

  log_info "Adding current user ($USER) to 'dialout' and 'plugdev' for serial/embedded hardware..."
  $SUDO usermod -aG dialout,plugdev "$USER" || true
}

# ------------------------------------------------------------------------------
# 2. FONTS (JetBrainsMono Nerd Font)
# ------------------------------------------------------------------------------
install_fonts() {
  local FONT_DIR="$HOME/.local/share/fonts/JetBrainsMono"
  if [[ -d "$FONT_DIR" && "$(ls -A "$FONT_DIR" 2>/dev/null)" ]]; then
    log_info "JetBrainsMono Nerd Font already installed. Skipping download."
    return 0
  fi

  log_info "Installing JetBrainsMono Nerd Font..."
  mkdir -p "$HOME/.local/share/fonts"
  
  local FONT_TMP
  FONT_TMP=$(create_secure_tmpdir "font")
  local ZIP_FILE="$FONT_TMP/JetBrainsMono.zip"

  if curl -fsSL "https://github.com/ryanoasis/nerd-fonts/releases/latest/download/JetBrainsMono.zip" -o "$ZIP_FILE"; then
    mkdir -p "$FONT_DIR"
    unzip -q -o "$ZIP_FILE" -d "$FONT_DIR"
    fc-cache -f "$HOME/.local/share/fonts"
    log_info "JetBrainsMono Nerd Font installed successfully."
  else
    log_warn "Failed to download JetBrainsMono Nerd Font."
  fi
  rm -rf "$FONT_TMP"
}

# ------------------------------------------------------------------------------
# 3. GIT & GIT CREDENTIAL MANAGER (Secured GPG key generation)
# ------------------------------------------------------------------------------
setup_git() {
  log_info "Configuring Git and Git Credential Manager..."
  
  $SUDO apt install -y pass gpg wget jq

  local GPG_UID="$GIT_NAME <$GIT_EMAIL>"

  git config --global credential.credentialStore gpg
  git config --global user.name "$GIT_NAME"
  git config --global user.email "$GIT_EMAIL"
  git config --global credential.https://github.com.username "$GITHUB_USER"
  git config --global push.autoSetupRemote true

  # Generate GPG key with passphrase protection
  if ! gpg --list-secret-keys "$GPG_UID" &>/dev/null; then
    log_info "Generating passphrase-protected GPG key for $GPG_UID..."
    local PASSPHRASE="${GPG_PASSPHRASE:-}"
    
    if [[ -z "$PASSPHRASE" ]]; then
      read -r -s -p "Enter passphrase for new GPG secret key (or press Enter to omit): " PASSPHRASE
      echo
    fi

    local PASS_DIRECTIVE=""
    if [[ -n "$PASSPHRASE" ]]; then
      PASS_DIRECTIVE="Passphrase: $PASSPHRASE"
    else
      PASS_DIRECTIVE="%no-protection"
      log_warn "Notice: Creating unencrypted GPG key (%no-protection). Ensure full disk encryption is active."
    fi

    gpg --batch --gen-key <<EOF
Key-Type: RSA
Key-Length: 4096
Subkey-Type: RSA
Subkey-Length: 4096
Name-Real: $GIT_NAME
Name-Email: $GIT_EMAIL
$PASS_DIRECTIVE
Expire-Date: 0
%commit
EOF
  fi

  # Initialize pass store if uninitialized
  if [[ ! -d "$HOME/.password-store" ]]; then
    log_info "Initializing pass password store..."
    pass init "$GPG_UID"
  fi

  # Install Git Credential Manager if missing
  if ! command -v git-credential-manager &>/dev/null; then
    log_info "Installing Git Credential Manager via secure temporary directory..."
    local GCM_DEB_URL
    GCM_DEB_URL=$(curl -s https://api.github.com/repos/git-ecosystem/git-credential-manager/releases/latest \
      | jq -r '.assets[] | select(.name | test("gcm-linux_amd64.*\\.deb$")) | .browser_download_url' 2>/dev/null || true)

    if [[ -z "$GCM_DEB_URL" || "$GCM_DEB_URL" == "null" ]]; then
      log_warn "Failed to query GitHub API for GCM. Using static release package..."
      GCM_DEB_URL="https://github.com/git-ecosystem/git-credential-manager/releases/download/v2.6.0/gcm-linux_amd64.2.6.0.deb"
    fi

    local GCM_TMP
    GCM_TMP=$(create_secure_tmpdir "gcm")
    wget -q "$GCM_DEB_URL" -O "$GCM_TMP/gcm.deb"
    $SUDO dpkg -i "$GCM_TMP/gcm.deb" || $SUDO apt-get install -f -y
    rm -rf "$GCM_TMP"

    git-credential-manager configure
    log_warn "Starting GitHub authentication for Git Credential Manager..."
    git-credential-manager github login --no-ui || true
  else
    log_info "Git Credential Manager already installed."
  fi
}

# ------------------------------------------------------------------------------
# 4. PACKAGE DEPENDENCIES & SHELLS
# ------------------------------------------------------------------------------
install_dependencies() {
  log_info "Installing core APT dependencies..."
  $SUDO apt install -y \
    zsh \
    zsh-autosuggestions \
    zsh-syntax-highlighting \
    fzf \
    zoxide \
    ripgrep \
    bat \
    eza \
    fastfetch \
    fish \
    tmux \
    python3-gpg \
    python3-pil \
    flatpak \
    build-essential \
    cmake \
    ninja-build \
    gettext \
    unzip \
    tar \
    fd-find \
    xclip \
    wl-clipboard \
    python3-pip \
    python3-venv \
    luarocks \
    gdu \
    pkg-config \
    libssl-dev

  # Install bottom (btm) via secure temporary directory
  if ! command -v btm &>/dev/null; then
    log_info "Installing bottom (btm)..."
    local BTM_DEB_URL
    BTM_DEB_URL=$(curl -s https://api.github.com/repos/ClementTsang/bottom/releases/latest \
      | jq -r '.assets[] | select(.name | test("bottom_.*_amd64\\.deb$")) | .browser_download_url' 2>/dev/null || true)

    if [[ -z "$BTM_DEB_URL" || "$BTM_DEB_URL" == "null" ]]; then
      BTM_DEB_URL="https://github.com/ClementTsang/bottom/releases/download/0.10.2/bottom_0.10.2-1_amd64.deb"
    fi

    local BTM_TMP
    BTM_TMP=$(create_secure_tmpdir "bottom")
    wget -q "$BTM_DEB_URL" -O "$BTM_TMP/bottom.deb"
    $SUDO dpkg -i "$BTM_TMP/bottom.deb" || $SUDO apt-get install -f -y
    rm -rf "$BTM_TMP"
  fi

  # Symlink fd-find to fd if necessary
  if command -v fdfind &>/dev/null && ! command -v fd &>/dev/null; then
    $SUDO ln -s "$(command -v fdfind)" /usr/local/bin/fd
  fi

  # Starship Prompt (avoid pipe-to-shell)
  if ! command -v starship &>/dev/null; then
    log_info "Installing Starship Prompt..."
    local STAR_TMP
    STAR_TMP=$(create_secure_tmpdir "starship")
    curl -fsSL https://starship.rs/install.sh -o "$STAR_TMP/starship_install.sh"
    sh "$STAR_TMP/starship_install.sh" -y
    rm -rf "$STAR_TMP"
  fi
}

# ------------------------------------------------------------------------------
# 5. DOTFILES
# ------------------------------------------------------------------------------
setup_dotfiles() {
  log_info "Setting up Dotfiles from $DOTFILES_REPO..."
  mkdir -p "$HOME/github"
  
  if [[ ! -d "$HOME/.dotfiles" ]]; then
    git clone "$DOTFILES_REPO" "$HOME/.dotfiles"
  else
    log_info "Dotfiles directory exists; updating..."
    git -C "$HOME/.dotfiles" pull --ff-only 2>/dev/null || true
  fi

  if [[ -f "$HOME/.dotfiles/bootstrap.sh" ]]; then
    chmod +x "$HOME/.dotfiles/bootstrap.sh"
    "$HOME/.dotfiles/bootstrap.sh"
  fi
}

# ------------------------------------------------------------------------------
# SETUP TMUX & TPM
# ------------------------------------------------------------------------------
setup_tmux() {
  log_info "Configuring Tmux and Tmux Plugin Manager (TPM)..."

  local TMUX_DIR="$HOME/.config/tmux"
  export TMUX_PLUGIN_MANAGER_PATH="$TMUX_DIR/plugins"

  mkdir -p "$TMUX_DIR/plugins"

  if [[ ! -d "$TMUX_DIR/plugins/tpm" ]]; then
    log_info "Cloning Tmux Plugin Manager to $TMUX_DIR/plugins/tpm..."
    git clone https://github.com/tmux-plugins/tpm "$TMUX_DIR/plugins/tpm"
  fi

  if [[ -f "$TMUX_DIR/plugins/tpm/bin/install_plugins" ]]; then
    log_info "Installing Tmux plugins..."
    "$TMUX_DIR/plugins/tpm/bin/install_plugins" || true
  fi
}

# ------------------------------------------------------------------------------
# 6. PROGRAMMING LANGUAGES (Rust, Go via Mise, Python/uv)
# ------------------------------------------------------------------------------
install_languages_and_tools() {
  log_info "Installing Rust toolchain..."
  if ! command -v rustc &>/dev/null; then
    local RUST_TMP
    RUST_TMP=$(create_secure_tmpdir "rustup")
    curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs -o "$RUST_TMP/rustup.sh"
    sh "$RUST_TMP/rustup.sh" -y
    rm -rf "$RUST_TMP"
  fi
  if [[ -f "$HOME/.cargo/env" ]]; then
    # shellcheck source=/dev/null
    source "$HOME/.cargo/env"
  fi
  rustup component add rust-analyzer 2>/dev/null || true
  install_rust_completions

  if ! command -v asm-lsp &>/dev/null; then
    log_info "Installing asm-lsp via Cargo..."
    cargo install asm-lsp
  else
    log_info "asm-lsp already installed."
  fi

  log_info "Installing Mise and configuring Go..."
  if ! command -v mise &>/dev/null; then
    local MISE_TMP
    MISE_TMP=$(create_secure_tmpdir "mise")
    curl -fsSL https://mise.run -o "$MISE_TMP/mise.sh"
    sh "$MISE_TMP/mise.sh"
    rm -rf "$MISE_TMP"
  fi
  
  export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
  eval "$("$HOME/.local/bin/mise" activate bash)"
  
  mise use --global go@latest

  if command -v go &>/dev/null; then
    install_go_completions
  else
    log_warn "Go binary not found in PATH after Mise setup."
  fi
  
  log_info "Installing Node.js LTS (Required for Neovim LSPs)..."
  if ! command -v node &>/dev/null; then
    local NODE_TMP
    NODE_TMP=$(create_secure_tmpdir "nodesource")
    curl -fsSL https://deb.nodesource.com/setup_lts.x -o "$NODE_TMP/setup_lts.sh"
    $SUDO bash "$NODE_TMP/setup_lts.sh"
    rm -rf "$NODE_TMP"
    $SUDO apt install -y nodejs
  fi

  log_info "Installing uv and Django..."
  if ! command -v uv &>/dev/null; then
    local UV_TMP
    UV_TMP=$(create_secure_tmpdir "uv")
    curl -fsSL https://astral.sh/uv/install.sh -o "$UV_TMP/uv_install.sh"
    sh "$UV_TMP/uv_install.sh"
    rm -rf "$UV_TMP"
  fi
  "$HOME/.local/bin/uv" tool install --upgrade django 2>/dev/null || true
  install_django_completions
}

# ------------------------------------------------------------------------------
# 7. NEOVIM & ASTRONVIM INSTALLATION
# ------------------------------------------------------------------------------
install_neovim_and_astronvim() {
  log_info "Checking Neovim installation..."

  local ARCH
  ARCH=$(uname -m)

  local TAR_NAME EXTRACT_DIR
  if [[ "$ARCH" == "x86_64" ]]; then
    TAR_NAME="nvim-linux-x86_64.tar.gz"
    EXTRACT_DIR="nvim-linux-x86_64"
  elif [[ "$ARCH" == "aarch64" ]]; then
    TAR_NAME="nvim-linux-arm64.tar.gz"
    EXTRACT_DIR="nvim-linux-arm64"
  else
    log_error "Unsupported architecture for Neovim: $ARCH"
    return 1
  fi

  if ! command -v nvim &>/dev/null; then
    local NVIM_URL="https://github.com/neovim/neovim/releases/latest/download/${TAR_NAME}"
    local NVIM_TMP
    NVIM_TMP=$(create_secure_tmpdir "nvim")
    local TEMP_TAR="$NVIM_TMP/${TAR_NAME}"

    log_info "Downloading latest Neovim release..."
    wget -q --show-progress "$NVIM_URL" -O "$TEMP_TAR"

    if [[ -f "$TEMP_TAR" && -s "$TEMP_TAR" ]]; then
      log_info "Extracting Neovim to /opt..."
      $SUDO rm -rf "/opt/${EXTRACT_DIR}"
      $SUDO tar -C /opt -xzf "$TEMP_TAR"
      $SUDO ln -sf "/opt/${EXTRACT_DIR}/bin/nvim" /usr/local/bin/nvim
    fi
    rm -rf "$NVIM_TMP"
  else
    log_info "Neovim binary is already installed."
  fi

  log_info "Configuring Neovim Python, Node, and Treesitter providers..."
  python3 -m pip install --user --upgrade pynvim --break-system-packages 2>/dev/null || \
  python3 -m pip install --user --upgrade pynvim 2>/dev/null || true

  if command -v npm &>/dev/null; then
    $SUDO npm install -g neovim tree-sitter-cli || true
  fi

  log_info "Deploying AstroNvim configuration..."
  if [[ -d "$HOME/.config/nvim/.git" ]]; then
    log_info "Existing AstroNvim Git repository detected; pulling latest changes..."
    git -C "$HOME/.config/nvim" pull --ff-only 2>/dev/null || log_warn "Local changes present; skipped pull."
  elif [[ -d "$HOME/.config/nvim" ]]; then
    log_warn "Non-git ~/.config/nvim detected; backing up to ~/.config/nvim.bak..."
    mv "$HOME/.config/nvim" "$HOME/.config/nvim.bak_$(date +%Y%m%d_%H%M%S)"
    git clone "$NVIM_CONFIG_REPO" "$HOME/.config/nvim"
  else
    git clone "$NVIM_CONFIG_REPO" "$HOME/.config/nvim"
  fi

  if command -v nvim &>/dev/null; then
    log_info "Initializing Neovim headless plugins..."
    nvim --headless -c 'quitall' 2>/dev/null || true
  fi
}

# ------------------------------------------------------------------------------
# 8. THIRD-PARTY APPLICATIONS & REPOSITORIES (Secure temp handling)
# ------------------------------------------------------------------------------
install_applications() {
  log_info "Creating workspace and application folders..."
  mkdir -p "$HOME/Downloads" "$HOME/Applications" "$HOME/Projects"

  # Dropbox
  log_info "Installing Dropbox..."
  $SUDO apt install -y nautilus-dropbox

  # VS Code via Official Microsoft Repository
  if [[ ! -f /etc/apt/sources.list.d/vscode.list ]]; then
    log_info "Setting up VS Code repository..."
    $SUDO apt install -y gpg wget apt-transport-https
    
    local VSC_TMP
    VSC_TMP=$(create_secure_tmpdir "vscode")
    wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > "$VSC_TMP/packages.microsoft.gpg"
    $SUDO install -D -o root -g root -m 644 "$VSC_TMP/packages.microsoft.gpg" /etc/apt/keyrings/packages.microsoft.gpg
    echo "deb [arch=amd64,arm64,armhf signed-by=/etc/apt/keyrings/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main" | $SUDO tee /etc/apt/sources.list.d/vscode.list > /dev/null
    rm -rf "$VSC_TMP"
    $SUDO apt update
  fi
  $SUDO apt install -y code

  # AppImageLauncher (if not already installed)
  if ! dpkg -s appimagelauncher &>/dev/null; then
    log_info "Installing AppImageLauncher via secure temporary directory..."
    local AIL_TMP
    AIL_TMP=$(create_secure_tmpdir "appimagelauncher")
    wget -q "https://github.com/TheAssassin/AppImageLauncher/releases/download/v2.2.0/appimagelauncher_2.2.0-travis995.0f91801.bionic_amd64.deb" -O "$AIL_TMP/appimagelauncher.deb"
    $SUDO dpkg -i "$AIL_TMP/appimagelauncher.deb" || $SUDO apt-get install -f -y
    rm -rf "$AIL_TMP"
  fi

  # Arduino Toolchain
  log_info "Installing Arduino environment..."
  $SUDO apt install -y arduino arduino-builder avra avrdude-doc gdb-avr

  # Saleae Logic 2 AppImage & Udev Rules
  log_info "Setting up Saleae Logic 2..."
  if [[ ! -f "$HOME/Applications/Logic2-linux-x64.AppImage" ]]; then
    wget -q "https://logic2api.saleae.com/download?os=linux&arch=x64" -O "$HOME/Applications/Logic2-linux-x64.AppImage"
    chmod +x "$HOME/Applications/Logic2-linux-x64.AppImage"
  fi

  if [[ ! -f /etc/udev/rules.d/99-SaleaeLogic.rules ]]; then
    log_info "Installing verified Saleae Logic udev rules..."
    local SAL_TMP
    SAL_TMP=$(create_secure_tmpdir "saleae")
    if curl -fsSL "https://raw.githubusercontent.com/saleae/saleae-logic-udev/master/99-SaleaeLogic.rules" -o "$SAL_TMP/99-SaleaeLogic.rules"; then
      # Security check: verify rule does not contain dangerous RUN directives
      if ! grep -qE '\b(RUN|PROGRAM)\b' "$SAL_TMP/99-SaleaeLogic.rules"; then
        $SUDO install -o root -g root -m 0644 "$SAL_TMP/99-SaleaeLogic.rules" /etc/udev/rules.d/
        $SUDO udevadm control --reload-rules 2>/dev/null || true
        $SUDO udevadm trigger 2>/dev/null || true
      else
        log_error "Security alert: Saleae udev rule contained RUN directives. Refusing to install."
      fi
    fi
    rm -rf "$SAL_TMP"
  fi

  # FreeCAD Flatpak
  if command -v flatpak &>/dev/null; then
    log_info "Installing FreeCAD Flatpak..."
    flatpak install -y org.freecad.FreeCAD 2>/dev/null || true
  fi

  # Synology Drive Client
  if ! dpkg -s synology-drive &>/dev/null; then
    log_info "Installing Synology Drive Client via secure temporary directory..."
    local SYN_TMP
    SYN_TMP=$(create_secure_tmpdir "synology")
    wget -q "https://global.synologydownload.com/download/Utility/SynologyDriveClient/4.0.1-17885/Ubuntu/Installer/synology-drive-client-17885.x86_64.deb" -O "$SYN_TMP/synology-drive.deb"
    $SUDO dpkg -i "$SYN_TMP/synology-drive.deb" || $SUDO apt-get install -f -y
    rm -rf "$SYN_TMP"
  fi

  # Database CLI tools, CAD, and Document tools
  $SUDO apt install -y \
    litecli \
    pgcli \
    mycli \
    kicad \
    openscad \
    libreoffice \
    evince
}

# ------------------------------------------------------------------------------
# FIREFOX DEVELOPER EDITION (OFFICIAL MOZILLA REPOSITORY)
# ------------------------------------------------------------------------------
install_firefox_dev() {
  if ! dpkg -s firefox-devedition &>/dev/null; then
    log_info "Setting up Mozilla APT repository for Firefox Developer Edition..."

    $SUDO install -d -m 0755 /etc/apt/keyrings
    wget -q https://packages.mozilla.org/apt/repo-signing-key.gpg -O- | $SUDO tee /etc/apt/keyrings/packages.mozilla.org.asc > /dev/null

    echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" | $SUDO tee /etc/apt/sources.list.d/mozilla.list > /dev/null

    # Scope APT pinning to Firefox packages
    echo '
Package: firefox*
Pin: origin packages.mozilla.org
Pin-Priority: 1000
' | $SUDO tee /etc/apt/preferences.d/mozilla > /dev/null

    $SUDO apt update && $SUDO apt install -y firefox-devedition
  else
    log_info "Firefox Developer Edition already installed."
  fi
}

# ------------------------------------------------------------------------------
# 9. CONFIGURE AVR AND ARM TOOLCHAINS
# ------------------------------------------------------------------------------
install_avr_and_arm_toolchain() {
  log_info "Installing AVR and ARM toolchains..."
  
  $SUDO apt install -y \
    gcc-arm-none-eabi \
    binutils-arm-none-eabi \
    gdb-multiarch \
    openocd \
    stlink-tools \
    gcc-avr \
    binutils-avr \
    avr-libc \
    avrdude \
    libusb-1.0-0-dev \
    libftdi1-2 \
    picocom
}

# ------------------------------------------------------------------------------
# 10. CONFIGURE RISC-V TOOLCHAIN
# ------------------------------------------------------------------------------
install_riscv_toolchain() {
  log_info "Installing RISC-V toolchain..."
  
  $SUDO apt install -y \
    gcc-riscv64-unknown-elf \
    binutils-riscv64-unknown-elf \
    picolibc-riscv64-unknown-elf \
    qemu-system-misc
}

# ------------------------------------------------------------------------------
# 11. RUST COMPLETIONS
# ------------------------------------------------------------------------------
install_rust_completions() {
  log_info "Configuring Rust shell completions..."

  # Bash
  mkdir -p "$HOME/.local/share/bash-completion/completions"
  rustup completions bash > "$HOME/.local/share/bash-completion/completions/rustup" 2>/dev/null || true
  rustup completions bash cargo > "$HOME/.local/share/bash-completion/completions/cargo" 2>/dev/null || true

  # Zsh
  mkdir -p "$HOME/.zsh/completion"
  rustup completions zsh > "$HOME/.zsh/completion/_rustup" 2>/dev/null || true
  rustup completions zsh cargo > "$HOME/.zsh/completion/_cargo" 2>/dev/null || true

  # Fish
  mkdir -p "$HOME/.config/fish/completions"
  rustup completions fish > "$HOME/.config/fish/completions/rustup.fish" 2>/dev/null || true
}

# ------------------------------------------------------------------------------
# 12. GO COMPLETIONS
# ------------------------------------------------------------------------------
install_go_completions() {
  log_info "Configuring Go completions..."
  
  go install github.com/posener/complete/gocomplete@latest 2>/dev/null || true

  # Ensure GOPATH/bin is in PATH and hook gocomplete
  local GO_HOOK='export PATH="$HOME/go/bin:$PATH"
if command -v gocomplete &>/dev/null; then complete -o nospace -C gocomplete go; fi'

  if [[ -f "$HOME/.bashrc" ]] && ! grep -q "gocomplete" "$HOME/.bashrc"; then
    log_info "Adding gocomplete and Go bin path to .bashrc..."
    echo -e "\n# Go Completion & Path Integration\n$GO_HOOK" >> "$HOME/.bashrc"
  fi
}

# ------------------------------------------------------------------------------
# 13. DJANGO COMPLETIONS
# ------------------------------------------------------------------------------
install_django_completions() {
  log_info "Installing Django completions for Fish, Zsh, and Bash..."

  # 1. Fish
  mkdir -p "$HOME/.config/fish/completions"
  cat << 'EOF' > "$HOME/.config/fish/completions/django-admin.fish"
function __fish_django_complete
    set -l cmd (commandline -o)
    set -l current (commandline -ct)
    eval $cmd[1] tabcomplete -- $cmd[2..-1] $current 2>/dev/null
end

complete -c django-admin -f -a "(__fish_django_complete)"
complete -c manage.py -f -a "(__fish_django_complete)"
EOF

  # 2. Zsh
  mkdir -p "$HOME/.zsh/completion"
  curl -fsSL "https://raw.githubusercontent.com/django/django/main/extras/django_zsh_completion" \
    -o "$HOME/.zsh/completion/_django" 2>/dev/null || true

  # 3. Bash
  local BASH_COMP_DIR="$HOME/.local/share/bash-completion/completions"
  mkdir -p "$BASH_COMP_DIR"
  curl -fsSL "https://raw.githubusercontent.com/django/django/main/extras/django_bash_completion" \
    -o "$BASH_COMP_DIR/django-admin" 2>/dev/null || true
  ln -sf "$BASH_COMP_DIR/django-admin" "$BASH_COMP_DIR/manage.py"
}

# ------------------------------------------------------------------------------
# MAIN EXECUTION FLOW
# ------------------------------------------------------------------------------
main() {
  log_info "Starting Pop!_OS Post-Installation Setup..."

  setup_system
  install_fonts
  setup_git
  install_dependencies
  setup_dotfiles
  setup_tmux
  install_languages_and_tools
  install_neovim_and_astronvim
  install_firefox_dev
  install_applications
  install_avr_and_arm_toolchain
  install_riscv_toolchain

  log_info "Setup complete! Please restart your shell or log out and back in."
}

main "$@"
