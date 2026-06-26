# Contributing

Thanks for your interest in contributing! This bucket exists to distribute
[keycast](https://github.com/hasansezertasan/keycast) to Windows users via
[Scoop](https://scoop.sh). It carries two manifests for the same app:

- `bucket/keycast.json` — the prebuilt `keycast-windows.zip` bundle (no Python).
- `bucket/keycast-pipx.json` — a `pipx install keycast` shim (needs Python 3.14+).

Most changes are automated version bumps (see [Version updates](#updating-a-version)).
Manual contributions are usually fixes to a manifest or a workflow.

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

The `hash` above is `scripts/noop.ps1` — reuse it as-is for any pipx shim. After
editing, update `README.md`'s package table and run `mise run style`.

## Testing locally (Windows)

```powershell
# Add this checkout as a local bucket
scoop bucket add keycast C:\path\to\this\repo

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
`auto-update.yml` (weekly cron + manual) and `update-manifest-dispatch.yml`
(fired by keycast's release pipeline) workflows, which open a PR with the bump.

To do it by hand: edit `version` (and, for the binary manifest, the `url` and
`hash`), then open a PR. CI installs the manifest and smoke-tests it.

See [`CLAUDE.md`](CLAUDE.md) for the full architecture notes.

## Questions?

Open an issue on this repository or on
[keycast](https://github.com/hasansezertasan/keycast/issues).
