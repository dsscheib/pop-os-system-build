#!/usr/bin/env bash
# Selectable workstation setup. User files stay in the desktop user's account.
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
# shellcheck source=resources/tool-versions.env
source "$SCRIPT_DIR/resources/tool-versions.env"
OPTIONAL_FAILURES=()
STAGES=(system fonts git dependencies dotfiles tmux languages neovim firefox applications arm-avr riscv)

optional() {
  local description=$1
  shift
  if "$@"; then return 0; fi
  log_warn "$description failed or is unavailable."
  OPTIONAL_FAILURES+=("$description")
  return 0
}

load_config() {
  if [[ -f "$SCRIPT_DIR/config.env" ]]; then
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/config.env"
  elif [[ -f "$SCRIPT_DIR/.env" ]]; then
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/.env"
  fi
}
validate_config() {
  local stages=$1 key
  if [[ ,$stages, == *,git,* ]]; then
    for key in GIT_NAME GIT_EMAIL GITHUB_USER; do
      [[ -n ${!key:-} && ${!key} != 'Your Full Name' && ${!key} != your.email@example.com && ${!key} != your-github-username ]] || die "Set $key in config.env before selecting the git stage."
      [[ ${!key} != *$'\n'* && ${!key} != *$'\r'* ]] || die "Invalid multiline identity value: $key"
    done
  fi
  if [[ ,$stages, == *,dotfiles,* ]]; then [[ -n ${DOTFILES_REPO:-} && $DOTFILES_REPO != *your-username* ]] || die "Set DOTFILES_REPO in config.env."; fi
  if [[ ,$stages, == *,neovim,* ]]; then [[ -n ${NVIM_CONFIG_REPO:-} && $NVIM_CONFIG_REPO != *your-username* ]] || die "Set NVIM_CONFIG_REPO in config.env."; fi
}

install_prerequisites() {
  sudo -v
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl git gnupg jq unzip tar xz-utils fontconfig python3 python3-venv python3-pil ufw bash-completion desktop-file-utils
}
setup_system() {
  sudo apt-get upgrade -y
  sudo ufw --force enable
  sudo usermod -aG dialout,plugdev "$(id -un)"
}

install_fonts() {
  fetch_locked font "$WORK_DIR/font.zip"
  begin_install "$HOME/.local/share/fonts/JetBrainsMono"
  python3 "$SCRIPT_DIR/scripts/st_assets.py" zip "$WORK_DIR/font.zip" "$INSTALL_DIR"
  [[ -n $(find "$INSTALL_DIR" -type f -name '*.ttf' -print -quit) ]] || die "Font archive contains no TTF files."
  publish_install
  fc-cache -f "$HOME/.local/share/fonts"
}

secret_fingerprints() {
  gpg --batch --with-colons --list-secret-keys --fingerprint "$1" |
    awk -F: '$1 == "sec" {primary=1; next} $1 == "fpr" && primary {print $10; primary=0}'
}
setup_git() {
  sudo apt-get install -y pass gpg python3-gpg
  local uid="$GIT_NAME <$GIT_EMAIL>" fingerprints fingerprint
  fingerprints=$(secret_fingerprints "$uid" 2>/dev/null || true)
  if [[ -z "$fingerprints" ]]; then
    log_info "Generating a GPG key for $uid (GPG will request its passphrase)."
    if [[ -n ${GPG_PASSPHRASE:-} ]]; then
      gpg --batch --pinentry-mode loopback --passphrase-fd 3 --quick-generate-key "$uid" default default 1y 3<<<"$GPG_PASSPHRASE"
    else
      [[ -t 0 ]] || die "Interactive GPG setup requires a terminal or GPG_PASSPHRASE."
      export GPG_TTY
      GPG_TTY=$(tty)
      gpg --quick-generate-key "$uid" default default 1y
    fi
    fingerprints=$(secret_fingerprints "$uid")
  fi
  fingerprint=${GPG_KEY_FINGERPRINT:-$fingerprints}
  fingerprint=${fingerprint^^}
  [[ $fingerprint != *$'\n'* && $fingerprint =~ ^[[:xdigit:]]{40,64}$ ]] || die "Multiple matching keys: set GPG_KEY_FINGERPRINT explicitly."
  grep -Fxq "$fingerprint" <<< "$fingerprints" || die "GPG_KEY_FINGERPRINT does not match a secret key for the configured identity."
  local store=${PASSWORD_STORE_DIR:-$HOME/.password-store}
  export PASSWORD_STORE_DIR="$store"
  if [[ ! -f "$store/.gpg-id" ]]; then
    [[ ! -d "$store" || -z $(find "$store" -mindepth 1 -maxdepth 1 -print -quit) ]] || die "Existing password store has no recipient configuration; repair it before continuing."
    pass init "$fingerprint"
  else
    grep -Fxq "$fingerprint" "$store/.gpg-id" || die "Existing pass store uses different recipients; select the matching key or update the store manually."
  fi
  if ! command -v git-credential-manager >/dev/null; then
    fetch_locked gcm "$WORK_DIR/gcm.deb"
    install_deb "$WORK_DIR/gcm.deb"
  fi
  git config --global user.name "$GIT_NAME"
  git config --global user.email "$GIT_EMAIL"
  git config --global credential.credentialStore gpg
  git config --global credential.https://github.com.username "$GITHUB_USER"
  git config --global push.autoSetupRemote true
  git-credential-manager configure
  # Authentication remains an explicit interactive operation, independent of setup.
  log_info "Git credentials configured. Authenticate when ready with: git-credential-manager github login"
}

install_dependencies() {
  sudo apt-get install -y zsh zsh-autosuggestions zsh-syntax-highlighting fish fzf zoxide ripgrep bat tmux flatpak \
    build-essential cmake ninja-build gettext fd-find xclip wl-clipboard python3-pip luarocks gdu pkg-config libssl-dev
  local package
  for package in eza fastfetch; do
    if apt-cache show "$package" >/dev/null 2>&1; then optional "$package" sudo apt-get install -y "$package";
    else log_warn "$package is unavailable in the configured repositories."; OPTIONAL_FAILURES+=("$package"); fi
  done
  if ! command -v btm >/dev/null; then fetch_locked bottom "$WORK_DIR/bottom.deb"; install_deb "$WORK_DIR/bottom.deb"; fi
  if command -v fdfind >/dev/null && ! command -v fd >/dev/null; then ln -s "$(command -v fdfind)" "$HOME/.local/bin/fd"; fi
  if ! command -v starship >/dev/null; then
    fetch_locked starship "$WORK_DIR/starship.tar.gz"
    python3 "$SCRIPT_DIR/scripts/st_assets.py" tar "$WORK_DIR/starship.tar.gz" "$WORK_DIR/starship"
    install -m 0755 "$WORK_DIR/starship/starship" "$HOME/.local/bin/starship"
  fi
}

update_repository() {
  local repo=$1 directory=$2 ref=${3:-}
  if [[ ! -e "$directory" && ! -L "$directory" ]]; then
    begin_install "$directory"
    git clone "$repo" "$INSTALL_DIR"
    [[ -z "$ref" ]] || git -C "$INSTALL_DIR" checkout --detach "$ref"
    publish_install
  else
    [[ -d "$directory/.git" ]] || die "$directory is not a Git checkout; preserve or relocate it before retrying."
    [[ $(git -C "$directory" remote get-url origin) == "$repo" ]] || die "Existing repository has a different origin: $directory"
    [[ -z $(git -C "$directory" status --porcelain) ]] || die "Local changes in $directory; commit or stash them before updating."
    if [[ -n "$ref" ]]; then
      git -C "$directory" fetch origin
      git -C "$directory" checkout --detach "$ref"
    else git -C "$directory" pull --ff-only; fi
  fi
  log_info "$directory is at $(git -C "$directory" rev-parse HEAD)."
}
setup_dotfiles() {
  update_repository "$DOTFILES_REPO" "$HOME/.dotfiles" "${DOTFILES_REF:-}"
  if [[ -f "$HOME/.dotfiles/bootstrap.sh" ]]; then bash "$HOME/.dotfiles/bootstrap.sh"; fi
}
setup_tmux() {
  export TMUX_PLUGIN_MANAGER_PATH="$HOME/.config/tmux/plugins"
  update_repository https://github.com/tmux-plugins/tpm "$TMUX_PLUGIN_MANAGER_PATH/tpm" "$TPM_COMMIT"
  optional 'Tmux plugins' "$TMUX_PLUGIN_MANAGER_PATH/tpm/bin/install_plugins"
}

install_uv() {
  if ! command -v uv >/dev/null; then
    fetch_locked uv "$WORK_DIR/uv.tar.gz"
    python3 "$SCRIPT_DIR/scripts/st_assets.py" tar "$WORK_DIR/uv.tar.gz" "$WORK_DIR/uv"
    install -m 0755 "$WORK_DIR/uv/uv-x86_64-unknown-linux-gnu/uv" "$HOME/.local/bin/uv"
    install -m 0755 "$WORK_DIR/uv/uv-x86_64-unknown-linux-gnu/uvx" "$HOME/.local/bin/uvx"
  fi
}
install_languages_and_tools() {
  export PATH="${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"
  if ! command -v rustup >/dev/null; then
    fetch_locked rustup "$WORK_DIR/rustup-init"
    chmod 0755 "$WORK_DIR/rustup-init"
    "$WORK_DIR/rustup-init" -y --default-toolchain "$RUST_VERSION"
  fi
  require_commands rustup cargo
  rustup toolchain install "$RUST_VERSION"
  rustup default "$RUST_VERSION"
  rustup component add --toolchain "$RUST_VERSION" rust-analyzer
  if ! command -v asm-lsp >/dev/null; then cargo install asm-lsp --version "$ASM_LSP_VERSION" --locked; fi
  install_rust_completions
  if ! command -v mise >/dev/null; then fetch_locked mise "$WORK_DIR/mise"; install -m 0755 "$WORK_DIR/mise" "$HOME/.local/bin/mise"; fi
  local mise
  mise=$(command -v mise)
  "$mise" use --global "go@$GO_VERSION" "node@$NODE_VERSION"
  local go_directory node_directory
  go_directory=$("$mise" where "go@$GO_VERSION")
  node_directory=$("$mise" where "node@$NODE_VERSION")
  export PATH="$go_directory/bin:$node_directory/bin:$PATH"
  require_commands go node npm
  install_go_completions
  install_uv
  uv tool install --python 3.13 "django==$DJANGO_VERSION"
  install_django_completions
}

install_neovim_and_astronvim() {
  install_uv
  local provider="$HOME/.local/share/nvim-python" binary
  if [[ ! -e "$provider" ]]; then uv venv "$provider"; fi
  check_executable "$provider/bin/python"
  uv pip install --python "$provider/bin/python" "pynvim==$PYNVIM_VERSION"
  # Keep this version in its permanent location, then publish a stable link.
  fetch_locked neovim "$WORK_DIR/neovim.tar.gz"
  begin_install "$HOME/.local/share/neovim"
  python3 "$SCRIPT_DIR/scripts/st_assets.py" tar "$WORK_DIR/neovim.tar.gz" "$INSTALL_DIR"
  binary="$INSTALL_DIR/nvim-linux-x86_64/bin/nvim"
  check_executable "$binary"
  check_elf_dependencies "$binary"
  "$binary" --version >/dev/null
  publish_install
  python3 "$SCRIPT_DIR/scripts/neovim_wrapper.py" "$HOME/.local/bin/nvim" "$binary" "$provider/bin/python"
  if command -v npm >/dev/null; then
    optional 'Neovim Node provider' npm install --global --prefix "$HOME/.local" "neovim@$NEOVIM_NODE_VERSION" "tree-sitter-cli@$TREE_SITTER_VERSION"
  fi
  # Preserve non-Git configs and only publish a validated clone after it succeeds.
  if [[ -e "$HOME/.config/nvim" && ! -d "$HOME/.config/nvim/.git" ]]; then
    begin_install "$HOME/.config/nvim"
    git clone "$NVIM_CONFIG_REPO" "$INSTALL_DIR"
    [[ -z ${NVIM_CONFIG_REF:-} ]] || git -C "$INSTALL_DIR" checkout --detach "$NVIM_CONFIG_REF"
    publish_install
  else update_repository "$NVIM_CONFIG_REPO" "$HOME/.config/nvim" "${NVIM_CONFIG_REF:-}"; fi
  optional 'Neovim plugin initialization' "$HOME/.local/bin/nvim" --headless -c quitall
}

install_repository() {
  local name=$1 key=$2 source=$3
  fetch_locked "$key" "$WORK_DIR/$name.key"
  if [[ $name == vscode ]]; then
    gpg --batch --yes --dearmor -o "$WORK_DIR/$name.gpg" "$WORK_DIR/$name.key"
    install_system_file "$WORK_DIR/$name.gpg" "/etc/apt/keyrings/$name.gpg"
  else install_system_file "$WORK_DIR/$name.key" "/etc/apt/keyrings/$name.asc"; fi
  printf '%s\n' "$source" > "$WORK_DIR/$name.list"
  install_system_file "$WORK_DIR/$name.list" "/etc/apt/sources.list.d/$name.list"
  sudo apt-get update
}
install_firefox_dev() {
  if ! package_installed firefox-devedition; then
    install_repository mozilla mozilla-key 'deb [signed-by=/etc/apt/keyrings/mozilla.asc] https://packages.mozilla.org/apt mozilla main'
    printf 'Package: firefox*\nPin: origin packages.mozilla.org\nPin-Priority: 1000\n' > "$WORK_DIR/mozilla.preferences"
    install_system_file "$WORK_DIR/mozilla.preferences" /etc/apt/preferences.d/mozilla
    sudo apt-get install -y firefox-devedition
  fi
}
install_freecad() {
  flatpak --user remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo || return
  flatpak --user install -y flathub org.freecad.FreeCAD
}
install_applications() {
  mkdir -p "$HOME/Applications" "$HOME/Projects" "$HOME/Downloads"
  optional Dropbox sudo apt-get install -y nautilus-dropbox
  if ! package_installed code; then
    install_repository vscode vscode-key 'deb [arch=amd64 signed-by=/etc/apt/keyrings/vscode.gpg] https://packages.microsoft.com/repos/code stable main'
    sudo apt-get install -y code
  fi
  if ! package_installed appimagelauncher; then
    fetch_locked appimagelauncher "$WORK_DIR/appimagelauncher.deb"
    optional AppImageLauncher install_deb "$WORK_DIR/appimagelauncher.deb"
  fi
  optional 'Arduino packages' sudo apt-get install -y arduino arduino-builder avra avrdude-doc gdb-avr
  fetch_locked saleae "$HOME/Applications/Logic2-linux-x64.AppImage"
  chmod 0755 "$HOME/Applications/Logic2-linux-x64.AppImage"
  install_system_file "$SCRIPT_DIR/resources/udev/99-SaleaeLogic.rules" /etc/udev/rules.d/99-SaleaeLogic.rules
  sudo udevadm control --reload-rules
  optional FreeCAD install_freecad
  if ! package_installed synology-drive; then
    fetch_locked synology "$WORK_DIR/synology.deb"
    optional 'Synology Drive' install_deb "$WORK_DIR/synology.deb"
  fi
  sudo apt-get install -y litecli pgcli mycli kicad openscad libreoffice evince
}
install_avr_and_arm_toolchain() {
  sudo apt-get install -y gcc-arm-none-eabi binutils-arm-none-eabi gdb-multiarch openocd stlink-tools gcc-avr binutils-avr avr-libc avrdude libusb-1.0-0-dev libftdi1-2 picocom
}
install_riscv_toolchain() {
  sudo apt-get install -y gcc-riscv64-unknown-elf binutils-riscv64-unknown-elf picolibc-riscv64-unknown-elf qemu-system-misc
}

completion_file() {
  local destination=$1 temporary
  shift
  mkdir -p "$(dirname "$destination")" || return
  temporary=$(mktemp "${destination}.tmp.XXXXXX") || return
  if ! "$@" > "$temporary"; then rm -f -- "$temporary"; return 1; fi
  mv -Tf -- "$temporary" "$destination"
}
install_rust_completions() {
  local shell
  for shell in bash zsh fish; do
    local directory destination
    case "$shell" in
      bash) directory="$HOME/.local/share/bash-completion/completions"; destination=rustup ;;
      zsh) directory="$HOME/.zsh/completion"; destination=_rustup ;;
      fish) directory="$HOME/.config/fish/completions"; destination=rustup.fish ;;
    esac
    optional "Rustup $shell completions" completion_file "$directory/$destination" rustup completions "$shell"
    if [[ $shell != fish ]]; then optional "Cargo $shell completions" completion_file "$directory/${destination/rustup/cargo}" rustup completions "$shell" cargo; fi
  done
}
install_go_completions() {
  go install "github.com/posener/complete/gocomplete@$GOCOMPLETE_VERSION"
  mkdir -p "$HOME/.local/share/bash-completion/completions"
  cat > "$HOME/.local/share/bash-completion/completions/go" <<'BASH'
export PATH="${GOBIN:-${GOPATH:-$HOME/go}/bin}:$PATH"
if command -v gocomplete >/dev/null; then complete -o nospace -C gocomplete go; fi
BASH
}
install_django_completions() {
  local bash_dir="$HOME/.local/share/bash-completion/completions"
  mkdir -p "$bash_dir" "$HOME/.zsh/completion" "$HOME/.config/fish/completions"
  install -m 0644 "$SCRIPT_DIR/resources/django-admin.bash" "$bash_dir/django-admin"
  ln -sfn django-admin "$bash_dir/manage.py"
  install -m 0644 "$SCRIPT_DIR/resources/_django" "$HOME/.zsh/completion/_django"
  install -m 0644 "$SCRIPT_DIR/resources/django-admin.fish" "$HOME/.config/fish/completions/django-admin.fish"
}

main() {
  local selected='system,fonts,git,dependencies,dotfiles,tmux,languages,neovim,firefox,applications,arm-avr,riscv' stage
  case ${1:-} in
    --list-stages) [[ $# -eq 1 ]] || die "--list-stages takes no arguments."; printf '%s\n' "${STAGES[@]}"; return ;;
    --stages) [[ $# -eq 2 && -n $2 ]] || die "Usage: $0 --stages comma,separated,stages"; selected=$2 ;;
    '') [[ $# -eq 0 ]] || die "Unexpected empty argument." ;;
    *) die "Usage: $0 [--list-stages | --stages comma,separated,stages]" ;;
  esac
  local -a chosen
  [[ $selected != ,* && $selected != *, && $selected != *,,* ]] || die "Stage names cannot be empty."
  IFS=, read -r -a chosen <<< "$selected"
  for stage in "${chosen[@]}"; do
    [[ " ${STAGES[*]} " == *" $stage "* && -n $stage ]] || die "Unknown stage: $stage"
  done
  require_user
  require_supported_system
  require_commands sudo apt-get python3
  load_config
  validate_config "$selected"
  start_workspace pop-workstation
  mkdir -p "$HOME/.local/bin"
  export PATH="$HOME/.local/bin:$PATH"
  install_prerequisites
  for stage in "${chosen[@]}"; do
    log_info "Running stage: $stage"
    case "$stage" in
      system) setup_system ;; fonts) install_fonts ;; git) setup_git ;;
      dependencies) install_dependencies ;; dotfiles) setup_dotfiles ;; tmux) setup_tmux ;;
      languages) install_languages_and_tools ;; neovim) install_neovim_and_astronvim ;;
      firefox) install_firefox_dev ;; applications) install_applications ;;
      arm-avr) install_avr_and_arm_toolchain ;; riscv) install_riscv_toolchain ;;
    esac
  done
  python3 "$SCRIPT_DIR/scripts/shell_integration.py" "$HOME" "$SCRIPT_DIR/resources"
  if (( ${#OPTIONAL_FAILURES[@]} )); then
    log_warn "Required stages completed. Optional components failed or were unavailable: ${OPTIONAL_FAILURES[*]}"
  else log_info "Selected setup stages completed."; fi
  log_info "Restart your shell or log out and back in to apply PATH and group changes."
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
