---
name: scoop-add
description: Use when adding a new item to this Scoop bucket — packaging a PyPI Python CLI as a uv/pipx shim manifest, a prebuilt Windows .zip bundle as a binary manifest, or a project that ships both (like keycast). Triggers include "add <package> as a shim", "add <repo> as a binary", "scoop-add <url>", and "/scoop-add".
---

# Adding a Manifest to the Bucket

## Overview

One scaffolding script does the tedious, error-prone part (sha256s, `checkver`/
`autoupdate` blocks, peeking inside the release `.zip` for `extract_dir` + `bin`);
you make the judgment calls it can't infer, then open a one-item-per-PR change.
**Always scaffold with `scripts/add_manifest.py` — never hand-write a manifest from
scratch.** It mirrors the tap's `add_formula.py` / `add_cask.py` split.

## Route by what upstream ships on Windows

Decide from a single question: **is the thing installed a Python package from PyPI,
or a prebuilt Windows `.zip` bundle downloaded from GitHub Releases?**

| Upstream artifact | Route | Command | Output |
|---|---|---|---|
| Python CLI on PyPI (installed via uv/pipx) | **shim** | `mise run add-manifest shim <package>` | `bucket/<name>.json` |
| Prebuilt Windows `.zip` on GitHub Releases | **binary** | `mise run add-manifest binary <owner/repo>` | `bucket/<name>.json` |
| **Both** — a PyPI CLI *and* a prebuilt bundle (e.g. `keycast`) | both | both commands | two manifests |

The routes mirror the tap: **shim ≈ formula**, **binary ≈ cask**. If the user says
"as a shim"/"as a binary", follow that. If they don't and upstream ships both, make
both (see *Ships both* below).

## Shim (PyPI → `bucket/<name>.json`)

```bash
mise run add-manifest shim cobo                 # uv-tool shim (default), bare name
mise run add-manifest shim keycast --via pipx   # pipx shim, named keycast-pipx
```

The script reads PyPI for `version`/`description`/`homepage`/`license`, then emits a
manifest whose `url`/`hash` are the static `noop.ps1` and whose `installer` runs
`uv tool install` (default) or `pipx install`. There is **no** dependency
resolution — uv/pipx resolve the tree at install time. Pick the backend:

- **`uv` (default)** — for the tap's formula-only tools (`cobo`, `hwid`, …). Uses
  the **bare** tool name; uv fetches its own Python, so nothing else is needed.
- **`--via pipx`** — names the manifest `<package>-pipx`. Use it for the pipx
  sibling of a package that *also* ships a binary manifest of the same name (the
  `keycast` / `keycast-pipx` pattern), so the two don't collide on one name.

Then verify the touch-ups it **can't** infer:

- **Description** — pulled raw from PyPI; tighten it to match the curated phrasing
  of the other manifests. The updater never rewrites it, so what you commit sticks.
- **Smoke-test command** — the CI map assumes `<package> version`. If the CLI uses
  `--version` or another form, add it to the map in `.github/workflows/tests.yml`.

## Binary (GitHub release `.zip` → `bucket/<name>.json`)

```bash
GITHUB_TOKEN=$(gh auth token) mise run add-manifest binary hasansezertasan/keycast
mise run add-manifest binary hasansezertasan/keycast --seed --bin keycast.exe   # no release yet
```

The script reads the latest release, picks the `.zip` asset (override with
`--artifact`), downloads it to compute the sha256, and **inspects the archive** to
infer `extract_dir` (the single wrapping folder Scoop strips) and `bin` (the `.exe`
to shim). It writes a version-templated `autoupdate` URL and a `checkver.github`
block. `--seed` writes a valid `0.0.0` placeholder (zero sha) that the first
`update_manifests.py` run fills — use it to seed a manifest **before** the producer
ships its first Windows build (that is why `bucket/keycast.json` started at `0.0.0`).
`--seed` can't inspect an archive, so it **requires `--bin`** (and `--extract-dir`
if the zip wraps its contents). Then verify:

- **`bin` / `extract_dir`** — inferred from the archive, but the script flags them
  as a guess when there's more than one `.exe`. Confirm against the real bundle.
- **`shortcuts`** — defaults to `[["<exe>", "<name>"]]`; adjust the Start-menu
  label if needed.

## Ships both (the keycast pattern)

A project with a PyPI CLI **and** a prebuilt Windows bundle gets a **binary**
manifest (bare name, e.g. `keycast`) **and** a **pipx shim** (`keycast-pipx`). Both
provide the same command, so a user installs **one or the other** — keep the
disambiguation note in `README.md`. This mirrors the tap, where `keycast` is both a
cask and a formula.

## Open the PR

- **One manifest per PR** (a "ships both" addition may add its two manifests
  together, since they're one logical package).
- **Branch:** Conventional Branch, e.g. `feat-<name>-shim` or `feat/add-<name>-binary`.
- **Commit + PR title:** Conventional Commits, e.g. `feat: add <name> shim`.
- **PR body:** what it packages + a **Verification** section (the JSON is valid, and
  ideally a Windows `scoop install` + smoke-test result).
- **Never push straight to `main`** — always via PR.

## Verify before claiming done

Do not report the item as added until you have run `mise run style` (JSON + workflow
lint) and, ideally, installed it on Windows and smoke-tested the command. CI
(`tests.yml`) installs every non-placeholder manifest on a Windows runner and
smoke-tests it, but the JSON must be valid locally first.

## Common mistakes

| Mistake | Fix |
|---|---|
| Hand-writing a manifest | Run `scripts/add_manifest.py` |
| pipx shim with the bare name when a binary of that name exists | Use `--via pipx` (names it `<name>-pipx`) so they don't collide |
| Leaving the raw PyPI/GitHub `description` | Curate it to match the other manifests |
| Removing the `checkver`/`autoupdate` block | It powers auto-updates; keep it |
| Guessed binary `bin` when the zip has several `.exe`s | Pass `--bin <name>.exe`; verify against the archive |
| `--seed` without `--bin` | Seed can't inspect the archive; pass `--bin` (and `--extract-dir` if wrapped) |
| Smoke-test left as `<package> version` when the CLI differs | Add the real command to the map in `tests.yml` |
| Multiple items in one PR, or pushing to `main` | One package per PR; always via PR |
