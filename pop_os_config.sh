#!/usr/bin/env bash

# ==============================================================================
# POP!_OS POST-INSTALLATION AUTOMATION SCRIPT
# Optimized & Fully Automated Setup
# ==============================================================================

set -euo pipefail

# Visual log helpers
log_info()  { echo -e "\033[0;34m[INFO]\033[0m $1"; }
log_warn()  { echo -e "\033[0;33m[WARN]\033[0m $1"; }
log_error() { echo -e "\033[0;31m[ERROR]\033[0m $1"; }

# Elevate privileges up front if needed
if [[ $EUID -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

# ------------------------------------------------------------------------------
# 1. SYSTEM UPDATE & FIREWALL
# ------------------------------------------------------------------------------
setup_system() {
  log_info "Updating system packages..."
  $SUDO apt update && $SUDO apt upgrade -y

  log_info "Enabling UFW Firewall..."
  $SUDO ufw enable
}

# ------------------------------------------------------------------------------
# 2. FONTS (JetBrainsMono Nerd Font)
# ------------------------------------------------------------------------------
install_fonts() {
  log_info "Installing JetBrainsMono Nerd Font..."
  mkdir -p ~/.local/share/fonts
  pushd ~/.local/share/fonts >/dev/null
  
  curl -fLO https://github.com/ryanoasis/nerd-fonts/releases/latest/download/JetBrainsMono.zip
  unzip -o JetBrainsMono.zip -d JetBrainsMono
  rm -f JetBrainsMono.zip
  fc-cache -fv
  
  popd >/dev/null
}

# ------------------------------------------------------------------------------
# 3. GIT & GIT CREDENTIAL MANAGER
# ------------------------------------------------------------------------------
setup_git() {
  log_info "Configuring Git and Git Credential Manager..."
  
  $SUDO apt update && $SUDO apt install -y pass gpg wget jq

  local NAME="Daniel Scheib"
  local EMAIL="dsscheib@gmail.com"
  local UNAME="dsscheib"
  local GPG_UID="$NAME <$EMAIL>"

  git config --global credential.credentialStore gpg
  git config --global user.email "$EMAIL"
  git config --global user.name "$NAME"
  git config --global credential.https://github.com.username "$UNAME"
  git config --global push.autoSetupRemote true

  # Generate GPG key non-interactively if missing
  if ! gpg --list-secret-keys "$GPG_UID" &>/dev/null; then
    log_info "Generating non-interactive GPG key..."
    gpg --batch --gen-key <<EOF
Key-Type: RSA
Key-Length: 4096
Subkey-Type: RSA
Subkey-Length: 4096
Name-Real: $NAME
Name-Email: $EMAIL
Expire-Date: 0
%no-protection
%commit
EOF
  fi

  # Initialize pass store
  pass init "$GPG_UID"

  # Install Git Credential Manager
  log_info "Installing Git Credential Manager..."
  GCM_DEB_URL=$(curl -s https://api.github.com/repos/git-ecosystem/git-credential-manager/releases/latest \
    | jq -r '.assets[] | select(.name | test("gcm-linux_amd64.*\\.deb$")) | .browser_download_url')

  # Fallback URL if API call returned empty/failed
  if [[ -z "$GCM_DEB_URL" || "$GCM_DEB_URL" == "null" ]]; then
    log_warn "Failed to query GitHub API for GCM. Falling back to static release package..."
    GCM_DEB_URL="https://github.com/git-ecosystem/git-credential-manager/releases/download/v2.6.0/gcm-linux_amd64.2.6.0.deb"
  fi

  wget "$GCM_DEB_URL" -O /tmp/gcm.deb
  $SUDO dpkg -i /tmp/gcm.deb || $SUDO apt-get install -f -y
  rm -f /tmp/gcm.deb

  git-credential-manager configure
  log_warn "Starting GitHub interactive login for Git Credential Manager..."
  git-credential-manager github login --no-ui || true
}

# ------------------------------------------------------------------------------
# 4. PACKAGE DEPENDENCIES & SHELLS
# ------------------------------------------------------------------------------
install_dependencies() {
  log_info "Adding PPAs for extra dependencies..."
  $SUDO add-apt-repository -y ppa:zhangsongcui3371/fastfetch
  $SUDO apt update

  log_info "Installing core APT dependencies..."
  $SUDO apt install -y \
    zsh \
    zsh-autosuggestions \
    zsh-syntax-highlighting \
    fzf \
    ripgrep \
    bat \
    eza \
    fastfetch \
    fish \
    tmux \
    python3-gpg \
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

  # Install bottom (btm) via latest GitHub release
  log_info "Installing bottom (btm)..."
  BTM_DEB_URL=$(curl -s https://api.github.com/repos/ClementTsang/bottom/releases/latest \
    | jq -r '.assets[] | select(.name | test("bottom_.*_amd64\\.deb$")) | .browser_download_url' 2>/dev/null || true)

  if [[ -z "$BTM_DEB_URL" || "$BTM_DEB_URL" == "null" ]]; then
    BTM_DEB_URL="https://github.com/ClementTsang/bottom/releases/download/0.10.2/bottom_0.10.2-1_amd64.deb"
  fi

  wget "$BTM_DEB_URL" -O /tmp/bottom.deb
  $SUDO dpkg -i /tmp/bottom.deb || $SUDO apt-get install -f -y
  rm -f /tmp/bottom.deb

  # Link fd-find to fd if necessary
  if command -v fdfind &>/dev/null && ! command -v fd &>/dev/null; then
    $SUDO ln -s "$(which fdfind)" /usr/local/bin/fd
  fi

  # Starship Prompt
  if ! command -v starship &>/dev/null; then
    log_info "Installing Starship Prompt..."
    curl -sS https://starship.rs/install.sh | sh -s -- -y
  fi
}

# ------------------------------------------------------------------------------
# 5. DOTFILES
# ------------------------------------------------------------------------------
setup_dotfiles() {
  log_info "Setting up Dotfiles..."
  mkdir -p "$HOME/github"
  
  if [[ ! -d "$HOME/.dotfiles" ]]; then
    git clone https://github.com/dsscheib/dotfiles.git "$HOME/.dotfiles"
  fi

  pushd "$HOME/.dotfiles" >/dev/null
  if [[ -x "./bootstrap.sh" ]]; then
    ./bootstrap.sh
  else
    chmod +x ./bootstrap.sh
    ./bootstrap.sh
  fi
  popd >/dev/null
}

# ------------------------------------------------------------------------------
# SETUP TMUX & TPM
# ------------------------------------------------------------------------------
setup_tmux() {
  log_info "Configuring Tmux and Tmux Plugin Manager (TPM)..."

  # Define XDG plugin path
  local TMUX_DIR="$HOME/.config/tmux"
  export TMUX_PLUGIN_MANAGER_PATH="$TMUX_DIR/plugins"

  mkdir -p "$TMUX_DIR/plugins"

  # Clone TPM into .config/tmux/plugins/tpm
  if [[ ! -d "$TMUX_DIR/plugins/tpm" ]]; then
    log_info "Cloning Tmux Plugin Manager to $TMUX_DIR/plugins/tpm..."
    git clone https://github.com/tmux-plugins/tpm "$TMUX_DIR/plugins/tpm"
  fi

  # Run plugin installer
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
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  fi
  source "$HOME/.cargo/env"
  rustup component add rust-analyzer
  install_rust_completions

  log_info "Installing asm-lsp..."
  cargo install asm-lsp

  log_info "Installing Mise and configuring Go..."
  curl https://mise.run | sh
  
  # Ensure Mise binaries and shims are added to current execution PATH
  export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
  eval "$("$HOME/.local/bin/mise" activate bash)"
  
  # Install latest stable Go using Mise
  mise use --global go@latest

  # Verify Go binary path before running completions
  if command -v go &>/dev/null; then
    install_go_completions
  else
    log_error "Go binary not found in PATH after Mise installation."
  fi
  
  log_info "Installing Node.js LTS (Required for Neovim LSPs)..."
  if ! command -v node &>/dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_lts.x | $SUDO -E bash -
    $SUDO apt install -y nodejs
  fi

  log_info "Installing uv and Django..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  uv tool install django
  install_django_completions
}

# ------------------------------------------------------------------------------
# 7. NEOVIM & ASTRONVIM INSTALLATION
# ------------------------------------------------------------------------------
install_neovim_and_astronvim() {
  log_info "Downloading and installing latest stable Neovim release..."

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
    log_error "Unsupported architecture: $ARCH"
    return 1
  fi

  local NVIM_URL="https://github.com/neovim/neovim/releases/latest/download/${TAR_NAME}"
  local TEMP_TAR="/tmp/${TAR_NAME}"

  # Download latest release tarball directly
  wget -q --show-progress "$NVIM_URL" -O "$TEMP_TAR"

  if [[ -f "$TEMP_TAR" && -s "$TEMP_TAR" ]]; then
    log_info "Extracting Neovim to /opt..."
    $SUDO rm -rf "/opt/${EXTRACT_DIR}"
    $SUDO tar -C /opt -xzf "$TEMP_TAR"
    $SUDO ln -sf "/opt/${EXTRACT_DIR}/bin/nvim" /usr/local/bin/nvim
    rm -f "$TEMP_TAR"
  else
    log_error "Failed to download valid Neovim package."
    return 1
  fi

  log_info "Configuring Neovim Python, Node, and Treesitter providers..."
  python3 -m pip install --user --upgrade pynvim --break-system-packages 2>/dev/null || \
  python3 -m pip install --user --upgrade pynvim || true

  if command -v npm &>/dev/null; then
    $SUDO npm install -g neovim tree-sitter-cli || true
  fi

  log_info "Deploying AstroNvim configuration..."
  rm -rf "$HOME/.local/share/nvim" "$HOME/.local/state/nvim" "$HOME/.cache/nvim" "$HOME/.config/nvim"
  git clone https://github.com/dsscheib/astronvim_v6 "$HOME/.config/nvim"

  if command -v nvim &>/dev/null; then
    log_info "Initializing Neovim headless setup..."
    nvim --headless -c 'quitall' || true
  fi
}

# ------------------------------------------------------------------------------
# 8. THIRD-PARTY APPLICATIONS & OFFICIAL REPOSITORIES
# ------------------------------------------------------------------------------
install_applications() {
  log_info "Creating workspace and downloads folders..."
  mkdir -p "$HOME/Downloads" "$HOME/Applications" "$HOME/Projects"

  # Dropbox via Nautilus APT Package
  log_info "Installing Dropbox..."
  $SUDO apt install -y nautilus-dropbox

  # VS Code via Official Microsoft Repository
  log_info "Installing VS Code via official repository..."
  $SUDO apt install -y gpg wget apt-transport-https
  wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /tmp/packages.microsoft.gpg
  $SUDO install -D -o root -g root -m 644 /tmp/packages.microsoft.gpg /etc/apt/keyrings/packages.microsoft.gpg
  echo "deb [arch=amd64,arm64,armhf signed-by=/etc/apt/keyrings/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main" | $SUDO tee /etc/apt/sources.list.d/vscode.list > /dev/null
  rm -f /tmp/packages.microsoft.gpg
  $SUDO apt update && $SUDO apt install -y code

  # AppImageLauncher
  log_info "Installing AppImageLauncher..."
  wget "https://github.com/TheAssassin/AppImageLauncher/releases/download/v2.2.0/appimagelauncher_2.2.0-travis995.0f91801.bionic_amd64.deb" -O "$HOME/Downloads/appimagelauncher.deb"
  $SUDO dpkg -i "$HOME/Downloads/appimagelauncher.deb" || $SUDO apt-get install -f -y
  rm -f "$HOME/Downloads/appimagelauncher.deb"

  # Arduino Toolchain
  log_info "Installing Arduino environment..."
  $SUDO apt install -y arduino arduino-builder avra avrdude-doc gdb-avr

  # Saleae Logic 2
  log_info "Downloading Saleae Logic 2..."
  wget "https://logic2api.saleae.com/download?os=linux&arch=x64" -O "$HOME/Applications/Logic2-linux-x64.AppImage"
  chmod +x "$HOME/Applications/Logic2-linux-x64.AppImage"

  # FreeCAD Flatpak
  log_info "Installing FreeCAD Flatpak..."
  flatpak install -y org.freecad.FreeCAD

  # Synology Drive Client
  log_info "Installing Synology Drive Client..."
  wget "https://global.synologydownload.com/download/Utility/SynologyDriveClient/4.0.1-17885/Ubuntu/Installer/synology-drive-client-17885.x86_64.deb" -O "$HOME/Downloads/synology-drive.deb"
  $SUDO dpkg -i "$HOME/Downloads/synology-drive.deb" || $SUDO apt-get install -f -y
  rm -f "$HOME/Downloads/synology-drive.deb"

  $SUDO apt update && $SUDO apt install -y \
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
  log_info "Setting up Mozilla APT repository for Firefox Developer Edition..."

  # Ensure keyrings directory exists
  $SUDO install -d -m 0755 /etc/apt/keyrings

  # Import official Mozilla signing key
  wget -q https://packages.mozilla.org/apt/repo-signing-key.gpg -O- | $SUDO tee /etc/apt/keyrings/packages.mozilla.org.asc > /dev/null

  # Add repository sources list
  echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" | $SUDO tee /etc/apt/sources.list.d/mozilla.list > /dev/null

  # Configure APT pinning to prioritize Mozilla's repository
  echo '
Package: *
Pin: origin packages.mozilla.org
Pin-Priority: 1000
' | $SUDO tee /etc/apt/preferences.d/mozilla > /dev/null

  # Update package list and install Firefox Developer Edition
  log_info "Installing Firefox Developer Edition..."
  $SUDO apt update && $SUDO apt install -y firefox-devedition
}

# ------------------------------------------------------------------------------
# 9. CONFIGURE AVR AND ARM TOOLCHAINS
# ------------------------------------------------------------------------------
install_avr_and_arm_toolchain() {
  log_info "Updating package lists and installing AVR and ARM toolchains..."
  
  $SUDO apt update
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
    build-essential \
    cmake \
    ninja-build \
    picocom
}

# ------------------------------------------------------------------------------
# 10. CONFIGURE RISC-V TOOLCHAIN
# ------------------------------------------------------------------------------
install_riscv_toolchain() {
  log_info "Updating package lists and installing RISC-V toolchain..."
  
  $SUDO apt update
  $SUDO apt install -y \
    gcc-riscv64-unknown-elf \
    binutils-riscv64-unknown-elf \
    picolibc-riscv64-unknown-elf \
    gdb-multiarch \
    openocd \
    qemu-system-misc \
    libusb-1.0-0-dev \
    build-essential \
    cmake \
    ninja-build \
    picocom
}

# ------------------------------------------------------------------------------
# 11. RUST COMPLETIONS
# ------------------------------------------------------------------------------
install_rust_completions() {
  log_info "Installing Rust completions..."

  # Bash
  mkdir -p $HOME/.local/share/bash-completion/completions
  rustup completions bash > $HOME/.local/share/bash-completion/completions/rustup
  rustup completions bash cargo > $HOME/.local/share/bash-completion/completions/cargo

  #Zsh
  mkdir -p ~/.zsh/completion
  rustup completions zsh > ~/.zsh/completion/_rustup
  rustup completions zsh cargo > ~/.zsh/completion/_cargo

  # Add to ~/.zshrc before compinit:
  # fpath=(~/.zsh/completion $fpath)

  # Fish
  mkdir -p ~/.config/fish/completions
  rustup completions fish > ~/.config/fish/completions/rustup.fish
}

# ------------------------------------------------------------------------------
# 12. GO COMPLETIONS
# ------------------------------------------------------------------------------
install_go_completions() {
  log_info "Installing Go completions..."
  
  go install github.com/posener/complete/gocomplete@latest

  # Pass 'y' non-interactively and suppress exit errors if completions already exist
  if command -v gocomplete &>/dev/null; then
    yes | gocomplete -install 2>/dev/null || true
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
  curl -sSL "https://raw.githubusercontent.com/django/django/main/extras/django_zsh_completion" \
    -o "$HOME/.zsh/completion/_django"

  # 3. Bash
  local BASH_COMP_DIR="$HOME/.local/share/bash-completion/completions"
  mkdir -p "$BASH_COMP_DIR"
  curl -sSL "https://raw.githubusercontent.com/django/django/main/extras/django_bash_completion" \
    -o "$BASH_COMP_DIR/django-admin"
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
