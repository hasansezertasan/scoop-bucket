# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a [Scoop](https://scoop.sh) bucket (Windows package manager) for
[keycast](https://github.com/hasansezertasan/keycast), a cross-platform
keystroke and mouse-click visualizer. It is the Windows counterpart to keycast's
[Homebrew tap](https://github.com/hasansezertasan/homebrew-tap).

## Architecture

Unlike a pipx-only bucket, this one ships **two manifests** for the same app,
mirroring the tap's **cask + formula** split (Scoop has no cask/formula
namespace, so each is a distinct installable name):

- `bucket/keycast.json` — **binary** (the *cask* analogue). Downloads the
  `keycast-windows.zip` PyInstaller bundle from GitHub Releases and shims
  `keycast.exe`. `extract_dir` strips the wrapping `keycast/` folder. No Python
  required. `checkver` tracks GitHub Releases; `autoupdate` re-templates the URL.
- `bucket/keycast-pipx.json` — **pipx shim** (the *formula* analogue). `"depends":
  "pipx"`; the real install is `pipx install keycast==$version`. Its `url` is the
  static `scripts/noop.ps1` (Scoop requires a URL, but pipx does the work), so its
  hash never changes. `checkver` tracks PyPI via `jsonpath`.
- `bucket/cobo.json`, `bucket/hwid.json`, `bucket/olink.json` (and future siblings) — **uv-tool shims for the tap's
  formula-only tools**. The tap ships several pure-Python CLIs as formulas with
  no cask; each maps to a single shim manifest here (same shape as
  `keycast-pipx.json` — static `noop.ps1` `url`/hash, `checkver` tracks PyPI),
  but the install is done by `uv tool install <pkg>==$version` with `"depends":
  "uv"` rather than pipx. uv fetches its own Python (no separate Python install
  needed). No manifest PATH mutation is required: Scoop's `uv` package points
  `UV_TOOL_BIN_DIR` at `scoop\persist\uv\tools\shims` and keeps that on the
  persistent PATH, so `uv tool install` drops the executable somewhere already
  runnable. (CI prepends that dir explicitly in the smoke test, since the
  already-running job session predates the PATH change.) Because no Windows binary
  competes for the name, they use the bare tool name (`cobo`, not `cobo-pipx`),
  mirroring the formula names directly.
- `scripts/update_manifests.py` — the **dual-source updater**. Per manifest it
  reads `checkver` to pick the source (GitHub Releases vs PyPI), bumps `version`,
  and for the binary manifest re-templates the URL and recomputes the sha256. If a
  release's `keycast-windows.zip` asset is not published yet, it skips that
  manifest with a warning instead of failing.
- `scripts/noop.ps1` — placeholder for the pipx manifest. Its sha256 is
  `fdcbbea851292d9aa67f598bc6f1ab96e58873385972cd3d209ccab239cbad87`; reuse it for
  any future pipx-based manifest.

> The `keycast-pipx` route installs *through* pipx, so a keycast launched that way
> reports `Install source: pipx` (not `scoop`) — exactly as the tap's formula
> reports `homebrew-formula`, not a cask. Only the binary `keycast` manifest is
> detected as `scoop`. This is intended.

## CI/CD

`.github/workflows/ci.yml` runs on every PR:

- **lint** — validates each manifest's JSON syntax.
- **discover** / **test** — for any manifest past its `0.0.0` placeholder,
  installs it on a Windows runner (with Python 3.14 for the pipx route) and
  smoke-tests `keycast version`. Manifests still at `0.0.0` are skipped, so CI is
  green on a freshly seeded bucket and activates automatically on the first bump.

## Version updates

Both manifests are kept current automatically (mirroring the tap):

**Scheduled** (`.github/workflows/auto-update.yml`, name "Update Manifests"):

- Runs weekly on Mondays at 09:00 UTC, and on `workflow_dispatch`.
- Runs `scripts/update_manifests.py` over all manifests and opens a PR
  (`peter-evans/create-pull-request`) if anything changed.
- The `workflow_dispatch` `package` input targets a single family (e.g. `keycast`).

**Push-based** (`.github/workflows/update-manifest-dispatch.yml`):

- Triggered via `repository_dispatch` (`update-manifest`) from the package repo —
  keycast's `release.yml` fires it right after publishing, for a prompt bump.
- Package repos trigger it with (the keycast pipeline uses `gh api`):
  ```bash
  gh api repos/hasansezertasan/scoop-bucket/dispatches \
    --method POST \
    -f event_type=update-manifest \
    -f 'client_payload[package]=keycast'
  ```

**Manual**: run the "Update Manifests" workflow from the Actions tab, or edit a
manifest's `version` (and, for the binary manifest, `url` + `hash`) directly.
