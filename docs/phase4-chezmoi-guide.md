# Phase 4 Migration Guide: Private Chezmoi Dotfiles + Global Mise

This guide documents how to structure your **private `dotfiles` repository** under **Chezmoi (Option B)** with **`age` encryption** and **global Mise** tool management, retiring `dotfiles-private`, `config.secrets.env`, `scripts/shell_integration.py`, and `scripts/antigravity_permissions.py`.

---

## 1. Repository Layout (`dotfiles` — Private GitHub Repo)

```text
dotfiles/
├── .chezmoi.toml.tmpl                                   # Prompts once per machine + enables age encryption
├── .chezmoiignore
├── dot_config/
│   ├── mise/
│   │   ├── config.toml                                  # Global runtimes & developer CLIs
│   │   └── mise.lock                                    # Generated via `mise lock` (pins URLs + SHA-256)
│   ├── pop-workstation/
│   │   ├── executable_agent-scope                       # Moved from pop-os-system-build/resources/agent-scope
│   │   ├── bash                                         # Moved from resources/shell.bash
│   │   ├── zsh                                          # Moved from resources/shell.zsh
│   │   └── fish                                         # Moved from resources/shell.fish
│   ├── fish/
│   │   ├── conf.d/pop-workstation.fish
│   │   └── completions/{agy,claude,django-admin}.fish   # Moved from resources/completions/ & resources/
│   ├── rclone/
│   │   ├── encrypted_private_gdrive-oauth.env.age       # GDRIVE_CLIENT_ID / GDRIVE_CLIENT_SECRET (0600)
│   │   └── gdrive-filters.txt                           # Plaintext in private repo (or encrypted if preferred)
│   └── systemd/user/
│       ├── rclone-gdrive-bisync.service.tmpl
│       └── rclone-gdrive-bisync.timer.tmpl
├── dot_local/share/bash-completion/completions/
│   ├── agy
│   ├── claude
│   └── django-admin                                     # (+ symlink manage.py -> django-admin)
├── dot_zsh/completion/
│   ├── _agy
│   ├── _claude
│   └── _django
├── dot_gitconfig.tmpl                                   # Git identity + gh credential helper last
├── dot_gemini/antigravity-cli/
│   └── executable_modify_settings.json                  # Merges destructive-git ask rules without clobbering hooks
├── dot_claude/
│   ├── executable_modify_settings.json                  # Merges personal preferences while keeping Herdr hooks
│   └── CLAUDE.md                                        # Folded from dotfiles-private (plaintext in private repo)
├── dot_codex/
│   └── executable_modify_config.toml                    # Merges personal settings while keeping Herdr hooks
├── run_onchange_after_mise-install.sh.tmpl              # Runs `mise install --yes` when mise config/lock changes
└── run_onchange_after_systemd-user-reload.sh.tmpl       # Runs `systemctl --user daemon-reload` when units change
```

> **Do not manage `~/.config/rclone/rclone.conf` as a static file in Chezmoi.** `rclone` writes refreshed OAuth tokens directly into `rclone.conf`. Store only `GDRIVE_CLIENT_ID` and `GDRIVE_CLIENT_SECRET` in `encrypted_private_gdrive-oauth.env.age` (`0600`), and let `mise run auth` (`scripts/auth.sh`) configure `rclone`.

---

## 2. Age Encryption Setup (One Key for Everything)

1. Generate your `age` identity key (if you don't already have one) and store a backup in your password manager:

   ```bash
   mkdir -p ~/.config/chezmoi
   age-keygen -o ~/.config/chezmoi/key.txt
   chmod 600 ~/.config/chezmoi/key.txt
   ```

2. Configure `.chezmoi.toml.tmpl` at the root of your `dotfiles` repo (replace `<YOUR_AGE_PUBLIC_KEY>` with the `age1...` public key printed by `age-keygen`):

   ```toml
   encryption = "age"

   [age]
     identity = "~/.config/chezmoi/key.txt"
     recipient = "age1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

   [data]
     git_name = {{ promptStringOnce . "git_name" "Git user.name" | quote }}
     git_email = {{ promptStringOnce . "git_email" "Git user.email" | quote }}
     gdrive_local_dir = {{ promptStringOnce . "gdrive_local_dir" "Google Drive local directory" (joinPath .chezmoi.homeDir "GoogleDrive") | quote }}
     gdrive_remote_path = {{ promptStringOnce . "gdrive_remote_path" "Google Drive remote subfolder (empty = My Drive)" "" | quote }}
     gdrive_sync_interval = {{ promptStringOnce . "gdrive_sync_interval" "Google Drive sync interval" "15min" | quote }}
   ```

3. Encrypt your OAuth credentials into Chezmoi:

   ```bash
   install -m 0600 /dev/null ~/.config/rclone/gdrive-oauth.env
   cat > ~/.config/rclone/gdrive-oauth.env <<'EOF'
   GDRIVE_CLIENT_ID="1234567890-abc.apps.googleusercontent.com"
   GDRIVE_CLIENT_SECRET="GOCSPX-..."
   EOF
   chezmoi add --encrypt ~/.config/rclone/gdrive-oauth.env
   ```

   This creates `dot_config/rclone/encrypted_private_gdrive-oauth.env.age` with mode `0600`.

---

## 3. Global Mise Configuration (`dot_config/mise/config.toml` + `mise.lock`)

Replace the CLI/runtime pins from v1 `resources/tool-versions.env` and `resources/downloads.json` with `dot_config/mise/config.toml`:

```toml
[settings]
idiomatic_version_file_enable_tools = ["node", "go", "rust"]

[tools]
chezmoi = "latest"
go = "1.27.2"
node = "24.21.0"
uv = "0.13.0"

[tools.rust]
version = "1.99.0"
components = "rust-analyzer,rustfmt,clippy,rust-src"

# CLI utilities (previously hand-pinned in downloads.json or APT)
neovim = "0.12.6"
starship = "1.26.0"
bottom = "0.14.9"
fastfetch = "2.69.0"
rclone = "1.75.2"
eza = "latest"
ripgrep = "latest"
fd = "latest"
bat = "latest"
fzf = "latest"
zoxide = "latest"
gdu = "latest"
"github:herdrdev/herdr" = "0.9.5"

# Language-ecosystem CLIs
"npm:@openai/codex" = "0.162.1"
"npm:@github/copilot" = "1.0.97"
"npm:neovim" = "5.5.0"
"npm:tree-sitter-cli" = "0.27.1"
"go:github.com/yamnikov-oleg/nasmfmt" = "v0.0.0-20230907082239-e010ffea9224"
"go:github.com/posener/complete/gocomplete" = "1.2.3"
"cargo:asm-lsp" = "0.10.1"
"pipx:django" = "6.1.2"
```

Generate the SHA-256 lockfile and commit both files to `dotfiles`:

```bash
chezmoi apply ~/.config/mise/config.toml
mise lock
chezmoi re-add ~/.config/mise/config.toml ~/.config/mise/mise.lock
```

Add `run_onchange_after_mise-install.sh.tmpl` so `chezmoi apply` / `chezmoi update` automatically converges global Mise tools whenever `config.toml` or `mise.lock` changes:

```bash
#!/usr/bin/env bash
# config.toml hash: {{ include "dot_config/mise/config.toml" | sha256sum }}
{{- if stat (joinPath .chezmoi.sourceDir "dot_config/mise/mise.lock") }}
# mise.lock hash: {{ include "dot_config/mise/mise.lock" | sha256sum }}
{{- end }}
set -euo pipefail
if command -v mise >/dev/null 2>&1; then
  mise install --yes
fi
```

---

## 4. `dot_gitconfig.tmpl` (Retiring `.gitconfig.local`)

```ini
[user]
	name = {{ .git_name }}
	email = {{ .git_email }}

[push]
	autoSetupRemote = true

# Keep gh credential helpers last after any generic helpers
[credential "https://github.com"]
	helper =
	helper = !gh auth git-credential

[credential "https://gist.github.com"]
	helper =
	helper = !gh auth git-credential
```

---

## 5. `modify_` Scripts for Agent Settings (Never Clobber Herdr Hooks)

Chezmoi pipes the **current file on disk** into `stdin` of `executable_modify_*` and writes `stdout` atomically back.

### Antigravity CLI Guardrails (`dot_gemini/antigravity-cli/executable_modify_settings.json`)

Replaces `scripts/antigravity_permissions.py`:

```python
#!/usr/bin/env python3
import json
import sys

ASK_RULES = [
    "command(git reset --hard)",
    "command(git clean)",
    "command(git branch -D)",
    "command(git rebase)",
    "command(git checkout --)",
]

raw = sys.stdin.read().strip()
settings = json.loads(raw) if raw else {}
if not isinstance(settings, dict):
    raise SystemExit("~/.gemini/antigravity-cli/settings.json is not a JSON object")

permissions = settings.setdefault("permissions", {})
ask = permissions.setdefault("ask", [])
for rule in ASK_RULES:
    if rule not in ask:
        ask.append(rule)

sys.stdout.write(json.dumps(settings, indent=2) + "\n")
```

Use the same `modify_` pattern for `dot_claude/executable_modify_settings.json` (JSON) and `dot_codex/executable_modify_config.toml` (using stdlib `tomllib` to read and merging only your personal keys while preserving `[hooks]` / `herdr` entries).

---

## 6. Google Drive Systemd User Units (`rclone-gdrive-bisync`)

`dot_config/systemd/user/rclone-gdrive-bisync.service.tmpl`:

```ini
[Unit]
Description=Two-way Google Drive sync (rclone bisync)
After=network-online.target

[Service]
Type=oneshot
ExecStart={{ .chezmoi.homeDir }}/.local/share/mise/shims/rclone bisync "gdrive:{{ .gdrive_remote_path }}" "{{ .gdrive_local_dir }}" --create-empty-src-dirs --resilient --recover --max-lock 2m --conflict-resolve newer --drive-skip-gdocs --filters-file "{{ .chezmoi.homeDir }}/.config/rclone/gdrive-filters.txt" --verbose
Nice=10
IOSchedulingClass=idle
```

`dot_config/systemd/user/rclone-gdrive-bisync.timer.tmpl`:

```ini
[Unit]
Description=Periodic Google Drive sync

[Timer]
OnBootSec=2min
OnUnitActiveSec={{ .gdrive_sync_interval }}

[Install]
WantedBy=timers.target
```

Add `run_onchange_after_systemd-user-reload.sh.tmpl`:

```bash
#!/usr/bin/env bash
# service: {{ include "dot_config/systemd/user/rclone-gdrive-bisync.service.tmpl" | sha256sum }}
# timer:   {{ include "dot_config/systemd/user/rclone-gdrive-bisync.timer.tmpl" | sha256sum }}
set -euo pipefail
if command -v systemctl >/dev/null 2>&1 && systemctl --user status >/dev/null 2>&1; then
  systemctl --user daemon-reload
fi
```

---

## 7. Shell Integration (`dot_config/pop-workstation/`)

Copy these files from `pop-os-system-build/resources/` into your Chezmoi source:
- `resources/agent-scope` → `dot_config/pop-workstation/executable_agent-scope`
- `resources/shell.bash` → `dot_config/pop-workstation/bash` (sourced from `dot_bashrc`)
- `resources/shell.zsh` → `dot_config/pop-workstation/zsh` (sourced from `dot_zshenv`)
- `resources/shell.fish` → `dot_config/pop-workstation/fish` (sourced from `dot_config/fish/conf.d/pop-workstation.fish`)
- `resources/completions/{agy,claude}.*` and `resources/django-admin.*`, `resources/_django` → respective completion directories.

---

## 8. Cutover & Verification Checklist

1. Ensure your `dotfiles` GitHub repo visibility is set to **Private** (`gh repo edit --visibility private`).
2. Fold files from `~/.dotfiles-private` (prompts, memories, `CLAUDE.md`, `gdrive-filters.txt`, etc.) into your Chezmoi source tree.
3. Dry-run and verify before applying:
   ```bash
   chezmoi diff
   chezmoi apply --dry-run --verbose
   ```
4. Apply, install global Mise tools, and check integrations:
   ```bash
   chezmoi apply
   mise doctor
   herdr config check
   ```
5. Record your private repo URL in `ansible/group_vars/all/local.yml` (`dotfiles_repo: "https://github.com/dsscheib/dotfiles.git"`) so future fresh installs converge automatically via `mise run apply` + `mise run auth`.
