# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is hasansezertasan's multi-project [Scoop](https://scoop.sh) bucket (Windows
package manager) — the Windows counterpart to the
[Homebrew tap](https://github.com/hasansezertasan/homebrew-tap), which it mirrors
in both functionality and philosophy. It packages the maintainer's projects and
tools for Windows users.

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
- `bucket/cobo.json`, `bucket/hwid.json`, `bucket/olink.json`, `bucket/ocom.json`, `bucket/nur.json` (and future siblings) — **uv-tool shims for the tap's
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
- `scripts/add_manifest.py` — the **scaffolder** (mirrors the tap's
  `add_formula.py` + `add_cask.py`). Two subcommands: `shim <pypi-package>`
  (uv-tool by default, `--via pipx` for the `-pipx` sibling) emits a shim manifest;
  `binary <owner/repo>` reads the latest GitHub release, downloads the `.zip` to
  hash it, and **peeks inside the archive** to infer `extract_dir` + `bin`. Far
  simpler than `add_formula.py` — uv/pipx resolve dependencies at install time, so
  there are no `resource` blocks to compute. Stdlib only; `mise run add-manifest`.
- `.claude/skills/scoop-add/SKILL.md` — the routing skill for "add a manifest",
  mirroring the tap's `homebrew-add`.
- `scripts/noop.ps1` — placeholder for the pipx manifest. Its sha256 is
  `fdcbbea851292d9aa67f598bc6f1ab96e58873385972cd3d209ccab239cbad87`; reuse it for
  any future pipx-based manifest.

> The `keycast-pipx` route installs *through* pipx, so a keycast launched that way
> reports `Install source: pipx` (not `scoop`) — exactly as the tap's formula
> reports `homebrew-formula`, not a cask. Only the binary `keycast` manifest is
> detected as `scoop`. This is intended.

## CI/CD

`.github/workflows/tests.yml` (the cask-agnostic analogue of the tap's
`tests.yml`; there is no Scoop equivalent of the tap's bottle-building or
`brew pr-pull`/`publish.yml`) runs on every PR:

- **lint** — validates each manifest's JSON syntax.
- **unit** — runs the `scripts/` unit tests (`python -m unittest discover -s tests`;
  covers `update_manifests.py` and `add_manifest.py`).
- **discover** / **test** — for any manifest past its `0.0.0` placeholder,
  installs it on a Windows runner (with Python 3.14 for the pipx route) and
  smoke-tests the installed command via a per-package map (`keycast version`,
  `cobo version`, `ocom --version`, …). Manifests still at `0.0.0` are skipped, so CI is
  green on a freshly seeded bucket and activates automatically on the first bump.

## Version updates

All manifests are kept current automatically (mirroring the tap):

**Scheduled** (`.github/workflows/update-manifests.yml`, name "Update Manifests"):

- Runs weekly on Mondays at 09:00 UTC, and on `workflow_dispatch`.
- Runs `scripts/update_manifests.py` over all manifests and opens a PR
  (`peter-evans/create-pull-request`) if anything changed.
- The `workflow_dispatch` `package` input targets a single family (e.g. `keycast`).

**Push-based** (`.github/workflows/update-manifest-dispatch.yml`):

- Triggered via `repository_dispatch` (`update-manifest`) from the package repo —
  keycast's `release.yml` fires it right after publishing, for a prompt bump.
- The `client_payload` is attacker-controllable, so it's read only through `env`
  and validated against a strict token regex (`^[a-z0-9][a-z0-9-]*$`) before use;
  the validated value is persisted to `$GITHUB_ENV` and consumed downstream (never
  the raw payload). On any failure the job opens an issue on this bucket
  (`if: failure()`, `issues: write`) — mirroring the tap's `update-cask-dispatch.yml`.
- Package repos trigger it with (the keycast pipeline uses `gh api`):
  ```bash
  gh api repos/hasansezertasan/scoop-bucket/dispatches \
    --method POST \
    -f event_type=update-manifest \
    -f 'client_payload[package]=keycast'
  ```

**Manual**: run the "Update Manifests" workflow from the Actions tab, or edit a
manifest's `version` (and, for the binary manifest, `url` + `hash`) directly.
