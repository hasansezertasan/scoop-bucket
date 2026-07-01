# scoop-bucket

A [Scoop](https://scoop.sh) bucket for [keycast](https://github.com/hasansezertasan/keycast)
— a cross-platform keystroke and mouse-click visualizer.

## Install

```powershell
scoop bucket add keycast https://github.com/hasansezertasan/scoop-bucket
scoop install keycast
```

## Manifests

Mirroring keycast's [Homebrew tap](https://github.com/hasansezertasan/homebrew-tap)
(which pairs a **cask** with a **formula**), this bucket ships two ways to install
the same app. Scoop has no cask/formula namespace, so each is its own name:

| Package | Mirrors | What it does | Needs Python? | Version source |
|---|---|---|---|---|
| `keycast` | the **cask** | downloads the `keycast-windows.zip` bundle and shims `keycast.exe` | No | GitHub Releases |
| `keycast-pipx` | the **formula** | runs `pipx install keycast` (a pipx shim) | Yes (3.14+) | PyPI |

```powershell
scoop install keycast        # prebuilt bundle, no Python required
scoop install keycast-pipx   # installs via pipx from PyPI
```

> Install **one or the other**, not both — they both provide the `keycast`
> command. Most users want `keycast` (no Python needed); choose `keycast-pipx`
> if you already use pipx and prefer the PyPI package.

### Other tools

This bucket also mirrors the pure-Python tools from the tap's **formulas**. Each
is a *uv tool shim* (the same shim pattern as `keycast-pipx`, but backed by
[`uv`](https://docs.astral.sh/uv/) instead of pipx): `"depends": "uv"` and
`uv tool install <tool>` does the real work. uv fetches its own Python on demand,
so no separate Python install is required. Scoop's `uv` package already keeps uv's
tool directory on your PATH, so the command is available in a new terminal after
install — nothing else to configure.

| Package | What it does | Version source |
|---|---|---|
| `cobo` | fetches boilerplate files from configurable git repositories | PyPI |
| `hwid` | extracts a cross-platform hardware ID using native OS detection | PyPI |
| `olink` | opens external URLs related to your project | PyPI |
| `ocom` | TUI for managing network/privacy tools (OpenVPN, SpoofDPI, WARP) | PyPI |

```powershell
scoop install cobo
scoop install hwid
scoop install olink
scoop install ocom
```

> `ocom` installs cleanly via uv, but it drives Unix-centric network tools, so
> its runtime usefulness on Windows is limited — it's provided for parity with
> the tap.

> ⚠️ **`hwid` is currently non-functional on recent Windows.** `hwid` 0.1.0 reads
> the hardware ID via `wmic`, which Microsoft has removed from current Windows
> releases, so it crashes at runtime. It installs cleanly and is kept here for
> parity with the tap; its CI install/smoke test is skipped until upstream drops
> the `wmic` dependency.

## How updates work

Both manifests are kept current automatically — there is nothing to edit by hand:

- **Scheduled** (`.github/workflows/auto-update.yml`): a weekly cron runs
  `scripts/update_manifests.py`, which re-derives each manifest's version from its
  own source (GitHub Releases for `keycast`, PyPI for `keycast-pipx`), recomputes
  the `.zip` hash for the binary manifest, and opens a PR.
- **On release** (`.github/workflows/update-manifest-dispatch.yml`): keycast's
  release pipeline fires a `repository_dispatch` (`update-manifest`) right after
  publishing, so the bump lands promptly instead of waiting for the cron.

PRs are opened with `peter-evans/create-pull-request` using the bucket's own
`GITHUB_TOKEN`. The seeded manifests start at `0.0.0`; the first update fills in
the real version, URL, and hash. The binary `keycast` manifest only bumps once a
keycast release ships `keycast-windows.zip` (the updater skips it with a warning
until then).

## Development

```powershell
mise run style        # format + lint YAML and workflows
```

CI (`.github/workflows/ci.yml`) validates every manifest's JSON and, for any
manifest past its `0.0.0` placeholder, installs it on a Windows runner and
smoke-tests `keycast version`.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for manifest templates and local testing,
and [`CLAUDE.md`](CLAUDE.md) for the architecture notes.

## License

[MIT](LICENSE)
