# scoop-bucket

My [Scoop](https://scoop.sh) bucket — the Windows counterpart to my
[Homebrew tap](https://github.com/hasansezertasan/homebrew-tap). It packages the
projects and tools I maintain for Windows users via Scoop, mirroring the tap in
both what it ships and how it stays current.

## Install

```powershell
scoop bucket add hasansezertasan https://github.com/hasansezertasan/scoop-bucket
scoop install <package>
```

## Packages

Each package here mirrors an entry in the tap. The tap pairs a **cask** (a
prebuilt bundle) with a **formula** (a Python CLI); Scoop has no cask/formula
namespace, so every installable is its own name. Three install routes are used:

- **Binary** — downloads a prebuilt `.zip` bundle and shims the `.exe`. No Python
  required. Mirrors a **cask**.
- **pipx shim** — `"depends": "pipx"`; runs `pipx install <pkg>`. Mirrors a
  **formula**.
- **uv-tool shim** — `"depends": "uv"`; runs `uv tool install <pkg>`. uv fetches
  its own Python on demand, so no separate Python install is required, and Scoop's
  `uv` package keeps uv's tool directory on your PATH. Mirrors a formula-only tool.

| Package | What it does | Route | Needs Python? | Source |
|---|---|---|---|---|
| `keycast` | keystroke & mouse-click visualizer ([repo](https://github.com/hasansezertasan/keycast)) | binary | No | GitHub Releases |
| `keycast-pipx` | the same app, installed from PyPI | pipx shim | Yes (3.14+) | PyPI |
| `cobo` | fetches boilerplate files from configurable git repositories ([repo](https://github.com/hasansezertasan/cobo) · [PyPI](https://pypi.org/project/cobo/)) | uv-tool shim | No¹ | PyPI |
| `hwid` | extracts a cross-platform hardware ID using native OS detection ([repo](https://github.com/hasansezertasan/hwid) · [PyPI](https://pypi.org/project/hwid/)) | uv-tool shim | No¹ | PyPI |
| `olink` | opens external URLs related to your project ([repo](https://github.com/hasansezertasan/olink) · [PyPI](https://pypi.org/project/olink/)) | uv-tool shim | No¹ | PyPI |
| `ocom` | TUI for managing network/privacy tools (OpenVPN, SpoofDPI, WARP) ([repo](https://github.com/hasansezertasan/ocom) · [PyPI](https://pypi.org/project/ocom/)) | uv-tool shim | No¹ | PyPI |
| `nur` | discovers and runs project tasks across npm, Make, PDM/poe, just, Taskfile ([repo](https://github.com/hasansezertasan/nur) · [PyPI](https://pypi.org/project/nur/)) | uv-tool shim | No¹ | PyPI |

¹ uv fetches its own Python, so nothing else needs to be installed.

```powershell
scoop install keycast        # prebuilt bundle, no Python required
scoop install keycast-pipx   # the same app, installed via pipx from PyPI
scoop install cobo           # (and hwid / olink / ocom / nur) via uv tool install
```

> **keycast ships two ways** — install **one or the other**, not both; they both
> provide the `keycast` command. Most users want `keycast` (no Python needed);
> choose `keycast-pipx` if you already use pipx and prefer the PyPI package. This
> mirrors the tap, where keycast is both a cask and a formula.

> `ocom` installs cleanly via uv, but it drives Unix-centric network tools, so
> its runtime usefulness on Windows is limited — it's provided for parity with
> the tap.

> **On "install source":** every package except the binary `keycast` installs
> *through* pipx or uv, so Scoop isn't the real installer. `scoop list` shows the
> shim, but the actual tool lives in pipx/uv's directory — e.g. a keycast set up
> via `keycast-pipx` reports `Install source: pipx`, not `scoop`. Only the binary
> `keycast` manifest is detected as a native Scoop install. This is intended, and
> mirrors the tap (where the formula reports `homebrew-formula`, not a cask).

## How updates work

All manifests are kept current automatically — there is nothing to edit by hand:

- **Scheduled** (`.github/workflows/auto-update.yml`): a weekly cron runs
  `scripts/update_manifests.py`, which re-derives each manifest's version from its
  own source (GitHub Releases for `keycast`, PyPI for `keycast-pipx` and the uv
  tools), recomputes the `.zip` hash for the binary manifest, and opens a PR.
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
smoke-tests the installed command (e.g. `keycast version`, `ocom --version`).

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for manifest templates and local testing,
and [`CLAUDE.md`](CLAUDE.md) for the architecture notes.

## License

[MIT](LICENSE)
