# Keeping the workstation up to date

Updates come in two kinds:

- **Floating software** (Ubuntu and vendor APT repos, Flatpaks, Neovim and tmux plugins) always takes the newest release. `mise run update` handles it.
- **Pinned versions** stay exactly where they are until you bump them on purpose. That covers your dev tools in the dotfiles' Mise config, and the vendor downloads and other pins in this repo. The pins are what let a rebuild reproduce this machine exactly, and they are the only integrity check for downloads that don't come from a signed APT repo.

A good rhythm: run `mise run update` weekly, and bump pins about once a month.

## Everyday: `mise run update`

| Step | What it does |
| :--- | :--- |
| `apt` | `sudo apt-get update`, then `sudo apt-get full-upgrade`. It shows the plan and asks first; `--yes` skips the question. Unlike `apt upgrade`, it also installs updates that need new packages, which `apt upgrade` holds back. |
| `flatpak` | `flatpak update` for user and system installs |
| `firmware` | Lists available firmware updates. It never installs them; run `fwupdmgr update` yourself when you're ready to reboot. |
| `nvim` | `Lazy! sync`: updates Neovim plugins and `~/.config/nvim/lazy-lock.json` |
| `tmux` | Updates tmux plugins through TPM |
| `mise` | Lists dev tools with newer releases than your pinned versions (report only) |

Options: `--yes`, `--skip STEP[,STEP]`, `--only STEP[,STEP]`, `--bump-mise`. For example, `mise run update --skip firmware,nvim`. Run `mise run update -- --help` for the full list (without the `--`, Mise shows its own task help instead).

APT upgrades are covered by Snapper: the APT hook takes a snapshot before and after every run (`snapper list` shows them, and `snapper-rollback` restores one).

To make your existing `update` alias use this, change it in your dotfiles' fish config:

```fish
alias update="mise -C ~/github/pop-os-system-build run update"
```

## Bumping your dev tools (Mise, in your dotfiles)

```bash
mise run update --only mise                # see what's behind
mise run update --only mise --bump-mise    # bump everything, then re-add the config to Chezmoi
chezmoi git -- diff                        # review
chezmoi git -- commit -am "Bump Mise tools" && chezmoi git -- push
```

`--bump-mise` moves every tool to its newest release, **including new major versions** (Node 24 → 26, for example). To bump only some tools, run it by hand from your home directory: `mise upgrade --bump uv rust`, then `chezmoi re-add ~/.config/mise/config.toml ~/.config/mise/mise.lock`. If a bump breaks something, revert the dotfiles commit and run `chezmoi apply`; Mise reinstalls the previous versions.

## Bumping this repo's pins

```bash
mise run pins                    # report every pin: current, latest, and what to do
mise run pins --bump saleae uv   # update named pins in place
mise run pins --bump-all         # update every pin that can be bumped automatically
```

For each download, a bump fetches the new release, computes its SHA-256 (and cross-checks published upstream digests when available, such as GitHub SHA-256 or Antigravity SHA-512), and edits `downloads.yml`, `00-defaults.yml`, `bootstrap.sh` or `requirements.yml`.

Some pins are report-only, because a safe bump needs a decision or a value the checker can't find:

| Pin | Why it's manual |
| :--- | :--- |
| `llvm` | apt.llvm.org also carries pre-release branches; confirm the version is released first |
| `kicad` | A new KiCad series uses a new PPA with its own signing key |
| STM32 installers | ST requires a sign-in to download them |

After bumping, do it like any other change:

1. Work on a branch.
2. Review `git diff`.
3. Run `mise run check`.
4. CI's container test skips the desktop apps, so for pins in `downloads.yml` also run the full end-to-end test, which installs them in a fresh container. It takes about 30 minutes, and it needs a normal clone: it runs `git diff --check`, which fails in a git worktree.
   `podman run --rm -v "$PWD:/workspace:ro,Z" docker.io/library/ubuntu:24.04 bash /workspace/scripts/e2e_container_test.sh /workspace`
5. Open a PR so CI runs the container test.
6. Check with `mise run plan`, then `mise run apply`.

## Antigravity CLI (`agy`)

`agy` updates itself automatically in the background during normal runs (opt out with `export AGY_CLI_DISABLE_AUTO_UPDATE=true`). You can also trigger an update immediately anytime with `agy update` (and view release notes with `agy changelog` or `/changelog`). `mise run update` does not upgrade `agy`.

Self-updates replace the binary in place inside `~/.local/share/pop-workstation/.antigravity-cli.versions/<pinned-version>/`. Future `mise run apply` runs only check that the binary exists, so they won't revert an in-place self-update while the `antigravity_cli` pin in `ansible/group_vars/all/downloads.yml` stays the same.

To update the baseline version installed on fresh machines, run `mise run pins --bump antigravity_cli` (which reads Google's Linux x64 release manifest and cross-checks its SHA-512 digest), then apply normally—changing the pin switches the `antigravity-cli` symlink to the newly pinned version directory.
