# Contributing

Thanks for your interest in contributing! This bucket distributes the projects
and tools hasansezertasan maintains to Windows users via [Scoop](https://scoop.sh),
mirroring the [Homebrew tap](https://github.com/hasansezertasan/homebrew-tap). It
carries three kinds of manifest:

- **Binary** — `bucket/keycast.json`: the prebuilt `keycast-windows.zip` bundle
  (no Python). Mirrors the tap's cask.
- **pipx shim** — `bucket/keycast-pipx.json`: a `pipx install keycast` shim
  (needs Python 3.14+). Mirrors the tap's formula.
- **uv-tool shim** — `bucket/cobo.json`, `bucket/hwid.json`, `bucket/olink.json`,
  `bucket/ocom.json`, `bucket/nur.json`: each runs `uv tool install <tool>` (uv
  fetches its own Python). Mirrors the tap's formula-only tools.

Most changes are automated version bumps (see [Version updates](#updating-a-version)).
Manual contributions are usually fixes to a manifest or a workflow.

## Adding a manifest

Scaffold a new manifest with `scripts/add_manifest.py` instead of hand-writing one.
It mirrors the tap's `add_formula.py` / `add_cask.py` split — route by **what the
package ships on Windows**:

| Upstream artifact | Route | Command |
|---|---|---|
| A PyPI CLI (installed via uv/pipx) | **shim** | `mise run add-manifest shim <package>` |
| A prebuilt Windows `.zip` on GitHub Releases | **binary** | `mise run add-manifest binary <owner/repo>` |

```bash
# uv-tool shim (default; bare tool name) — the common case for the tap's tools
mise run add-manifest shim cobo

# pipx shim (names it <package>-pipx; use when a binary of the same name exists)
mise run add-manifest shim keycast --via pipx

# binary download — reads the latest release, downloads the .zip to hash it, and
# infers `extract_dir` + `bin` by peeking inside the archive
GITHUB_TOKEN=$(gh auth token) mise run add-manifest binary hasansezertasan/keycast

# seed a binary placeholder before the first Windows release exists (--bin required)
mise run add-manifest binary hasansezertasan/keycast --seed --bin keycast.exe
```

The scaffolder fills in `version`, `description`, `homepage`, `license`, and the
`checkver`/`autoupdate` blocks. Then verify the touch-ups it **can't** infer:

- **Description** — pulled raw from PyPI/GitHub; tighten it to match the curated
  phrasing of the other manifests (the updater never rewrites it).
- **Binary `bin`/`extract_dir`** — inferred from the archive, but flagged as a
  guess when there's more than one `.exe`; confirm against the real bundle.
- **Smoke-test** — the CI map assumes `<package> version`. If the CLI differs
  (e.g. `--version`), add it to `.github/workflows/tests.yml`.

Then add the package to `README.md`'s table and run `mise run style`. The
`bucket/` manifests below are the reference shape the scaffolder emits (or a
starting point for a hand-written variant).

## Manifest templates

The manifests are already in `bucket/`; these templates are for reference (or for
adding a related tool).

**Binary download** (GitHub Releases asset, like `keycast`):

```json
{
    "version": "<version>",
    "description": "<description>",
    "homepage": "https://github.com/<owner>/<repo>",
    "license": "<license>",
    "architecture": {
        "64bit": {
            "url": "https://github.com/<owner>/<repo>/releases/download/v<version>/<asset>.zip",
            "hash": "<sha256>",
            "extract_dir": "<wrapping-folder-or-omit>"
        }
    },
    "bin": "<exe>",
    "shortcuts": [["<exe>", "<name>"]],
    "checkver": {"github": "https://github.com/<owner>/<repo>"},
    "autoupdate": {
        "architecture": {
            "64bit": {"url": "https://github.com/<owner>/<repo>/releases/download/v$version/<asset>.zip"}
        }
    }
}
```

**pipx shim** (PyPI package, like `keycast-pipx`):

```json
{
    "version": "<version>",
    "description": "<description>",
    "homepage": "https://github.com/<owner>/<repo>",
    "license": "<license>",
    "depends": "pipx",
    "url": "https://raw.githubusercontent.com/hasansezertasan/scoop-bucket/main/scripts/noop.ps1",
    "hash": "fdcbbea851292d9aa67f598bc6f1ab96e58873385972cd3d209ccab239cbad87",
    "installer": {"script": "pipx install <package>==$version --force"},
    "uninstaller": {"script": "pipx uninstall <package>"},
    "checkver": {"url": "https://pypi.org/pypi/<package>/json", "jsonpath": "$.info.version"},
    "autoupdate": {"url": "https://raw.githubusercontent.com/hasansezertasan/scoop-bucket/main/scripts/noop.ps1"}
}
```

**uv-tool shim** (PyPI package, like `cobo`/`hwid`/`olink`/`ocom`/`nur`):

```json
{
    "version": "<version>",
    "description": "<description> (uv tool install)",
    "homepage": "https://github.com/<owner>/<repo>",
    "license": "<license>",
    "depends": "uv",
    "url": "https://raw.githubusercontent.com/hasansezertasan/scoop-bucket/main/scripts/noop.ps1",
    "hash": "fdcbbea851292d9aa67f598bc6f1ab96e58873385972cd3d209ccab239cbad87",
    "installer": {"script": "uv tool install <package>==$version --force"},
    "uninstaller": {"script": "uv tool uninstall <package>"},
    "checkver": {"url": "https://pypi.org/pypi/<package>/json", "jsonpath": "$.info.version"},
    "autoupdate": {"url": "https://raw.githubusercontent.com/hasansezertasan/scoop-bucket/main/scripts/noop.ps1"}
}
```

Use a **bare tool name** (`cobo`, not `cobo-pipx`) when no Windows binary competes
for the name — this mirrors the tap's formula names and keeps the updater's family
filter from accidentally sweeping the tool into an unrelated package's dispatch.

The `hash` above is `scripts/noop.ps1` — reuse it as-is for any pipx or uv-tool
shim. After editing, update `README.md`'s package table and run `mise run style`.
If the tool's smoke-test command isn't `<package> version`, add it to the map in
`.github/workflows/tests.yml` (the "Smoke-test the installed command" step).

## Testing locally (Windows)

```powershell
# Add this checkout as a local bucket
scoop bucket add hasansezertasan C:\path\to\this\repo

# Binary route (no Python needed)
scoop install keycast
keycast version

# pipx route (needs Python 3.14+ on PATH and pipx)
scoop install keycast-pipx
keycast version
```

Install **one or the other** — both provide the `keycast` command.

## Updating a version

You normally don't have to: `scripts/update_manifests.py` is run by the
`update-manifests.yml` (weekly cron + manual) and `update-manifest-dispatch.yml`
(fired by keycast's release pipeline) workflows, which open a PR with the bump.

To do it by hand: edit `version` (and, for the binary manifest, the `url` and
`hash`), then open a PR. CI installs the manifest and smoke-tests it.

See [`CLAUDE.md`](CLAUDE.md) for the full architecture notes.

## Questions?

Open an issue on this repository or on
[keycast](https://github.com/hasansezertasan/keycast/issues).
