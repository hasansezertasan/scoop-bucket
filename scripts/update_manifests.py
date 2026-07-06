#!/usr/bin/env python3
"""Update Scoop manifests in ``bucket/`` from their upstream sources.

Each manifest declares its own source via ``checkver``, and the two keycast
manifests deliberately use different ones — mirroring the Homebrew tap's
formula/cask split:

- **PyPI manifest** (``checkver.url`` on ``pypi.org``) is a *pipx shim*
  (``keycast-pipx``): only ``version`` changes, because its ``url`` is the static
  ``noop.ps1`` whose hash never moves.
- **GitHub-release manifest** (``checkver.github``) is a *binary download*
  (``keycast``): ``version``, each per-arch ``url`` (rebuilt from the
  ``autoupdate`` template), and the ``hash`` (sha256 of the freshly downloaded
  asset) all change.

Run with no args to check every manifest; pass a package name to limit the run
to that family — ``keycast`` matches both ``keycast`` and ``keycast-pipx`` (the
``repository_dispatch`` from the package repo passes the bare package name).

Upstream can move a version *backward* — a yanked PyPI release or a deleted
GitHub release — so the script refuses to roll a manifest back: a lower version
is reported as a ``::warning::`` and skipped.

Prints a Markdown summary of what changed to stdout (consumed as a PR body). The
script never commits — ``peter-evans/create-pull-request`` opens the PR from the
diff. Exits 0 whether or not anything changed; nonzero only if a manifest fails
to update (the rest are still attempted).

Set ``GITHUB_TOKEN`` in the environment to authenticate the GitHub API call and
avoid the low unauthenticated rate limit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

BUCKET = Path(__file__).resolve().parent.parent / "bucket"
_UA = "keycast-scoop-bucket-updater"


def _get_json(url: str) -> dict:
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    # Authenticate GitHub API calls when a token is available (rate limits).
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.load(resp)
    except Exception as exc:  # noqa: BLE001 — re-raise with the URL for context
        raise RuntimeError(f"fetching {url}: {exc}") from exc


def _latest_pypi(checkver: dict) -> str:
    # NB: the manifest's checkver.jsonpath is for Scoop's native checkver; this
    # updater assumes it's always $.info.version and reads that field directly.
    return _get_json(checkver["url"])["info"]["version"]


def _latest_github(checkver: dict) -> str:
    # checkver.github is the repo homepage; the latest *release* tag is the
    # version (drafts are excluded by the API, which is what we want).
    repo = checkver["github"].rstrip("/").removeprefix("https://github.com/")
    tag = _get_json(f"https://api.github.com/repos/{repo}/releases/latest")["tag_name"]
    return tag.removeprefix("v")


def _sha256(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        for chunk in iter(lambda: resp.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_tuple(version: str) -> tuple[int, ...]:
    """The leading numeric release of a version, e.g. ``1.2.3rc1`` -> ``(1, 2, 3)``."""
    match = re.match(r"\d+(?:\.\d+)*", version)
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def _is_downgrade(latest: str, current: str) -> bool:
    """True only when ``latest`` is unambiguously an older release than ``current``.

    Compares numeric release tuples (zero-padded to equal length). Returns False
    when either side has no parseable release, so anything ambiguous proceeds and
    is caught in PR review rather than silently skipped. Guards against a yanked
    PyPI release or a deleted GitHub release making "latest" move backward.

    Contract: numeric release only. Prerelease suffixes are stripped by
    ``_release_tuple`` (``1.2.3rc1`` -> ``(1, 2, 3)``), so a move from ``1.2.3``
    to ``1.2.3rc1`` is *not* flagged as a downgrade. This can't fire in practice —
    both sources exclude prereleases (PyPI ``info.version``, GitHub
    ``releases/latest``) — but a checkver that started surfacing them would need
    its own handling here.
    """
    latest_tuple, current_tuple = _release_tuple(latest), _release_tuple(current)
    if not latest_tuple or not current_tuple:
        return False
    width = max(len(latest_tuple), len(current_tuple))
    latest_tuple += (0,) * (width - len(latest_tuple))
    current_tuple += (0,) * (width - len(current_tuple))
    return latest_tuple < current_tuple


def _update_manifest(path: Path) -> str | None:
    """Bump one manifest in place; return a ``"old → new"`` note or None."""
    data = json.loads(path.read_text(encoding="utf-8"))
    checkver = data.get("checkver", {})
    if "version" not in data:
        raise KeyError(f"{path.name}: manifest has no 'version' field")
    current = data["version"]

    if "github" in checkver:
        latest = _latest_github(checkver)
    elif "url" in checkver and "pypi.org" in checkver["url"]:
        latest = _latest_pypi(checkver)
    else:
        print(f"::warning::{path.name}: unrecognized checkver, skipping", file=sys.stderr)
        return None

    if latest == current:
        return None
    if _is_downgrade(latest, current):
        print(
            f"::warning::{path.name}: upstream reports a lower version "
            f"({latest} < {current}); possible yank/deleted release, skipping",
            file=sys.stderr,
        )
        return None

    # Binary manifests re-template the download URL and recompute its hash; the
    # pipx shim has no autoupdate URL with $version, so this block is skipped.
    # If the asset for `latest` is not published yet — e.g. a release that
    # predates the Windows build — skip this manifest with a warning rather than
    # writing a version whose download 404s (and don't fail the whole run).
    autoupdate = data.get("autoupdate", {})
    try:
        if "architecture" in autoupdate:
            new_arch = {}
            for arch, spec in autoupdate["architecture"].items():
                url = spec["url"].replace("$version", latest)
                new_arch[arch] = (url, _sha256(url))
            for arch, (url, digest) in new_arch.items():
                data["architecture"][arch]["url"] = url
                data["architecture"][arch]["hash"] = digest
        elif "$version" in autoupdate.get("url", ""):
            url = autoupdate["url"].replace("$version", latest)
            data["url"], data["hash"] = url, _sha256(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(
                f"::warning::{path.name}: asset for v{latest} not found yet, skipping",
                file=sys.stderr,
            )
            return None
        raise

    data["version"] = latest
    path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
    return f"`{current}` → `{latest}`"


def _matches(stem: str, target: str) -> bool:
    # "keycast" should match both "keycast" and the "keycast-pipx" sibling.
    # The hyphen split means a target sweeps in every "<target>-*" manifest, so
    # keep a family's variants under a shared prefix (and unrelated tools under a
    # bare name, as the uv tools do) to avoid an accidental over-match.
    return stem == target or stem.split("-", 1)[0] == target


def main(argv: list[str]) -> int:
    target = argv[0] if argv else None
    changes: dict[str, str] = {}
    failures: dict[str, str] = {}
    for path in sorted(BUCKET.glob("*.json")):
        if target and not _matches(path.stem, target):
            continue
        try:
            note = _update_manifest(path)
        except Exception as exc:  # noqa: BLE001 — record and continue; one bad manifest must not block the rest
            print(f"::error::{path.name}: {exc}", file=sys.stderr)
            failures[path.stem] = str(exc)
            continue
        if note:
            changes[path.stem] = note

    if changes:
        for name, note in changes.items():
            print(f"- **{name}**: {note}")
    else:
        print("No updates available.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
