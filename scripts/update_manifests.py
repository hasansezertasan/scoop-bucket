#!/usr/bin/env python3
"""Update Scoop manifests in ``bucket/`` from their upstream sources.

Each manifest declares its own source via ``checkver``. Two source types are
supported:

- **PyPI manifest** (``checkver.url`` on ``pypi.org``) is a Python shim: only
  ``version`` changes, because its ``url`` is the static
  ``noop.ps1`` whose hash never moves.
- **GitHub-release manifest** (``checkver.github``) is a binary download:
  ``version``, each per-arch ``url`` (rebuilt from the
  ``autoupdate`` template), and the ``hash`` (sha256 of the freshly downloaded
  asset) all change.

Run with no args to check every manifest; pass a package name to limit the run
to that family — ``example`` matches both ``example`` and ``example-pipx`` (the
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
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

from url_fetch import get_json as _get_json
from url_fetch import request

BUCKET = Path(__file__).resolve().parent.parent / "bucket"


def _latest_pypi(checkver: dict) -> str:
    # NB: the manifest's checkver.jsonpath is for Scoop's native checkver; this
    # updater assumes it's always $.info.version and reads that field directly.
    return _get_json(checkver["url"])["info"]["version"]


def _latest_github(checkver: dict) -> str:
    # checkver.github is the repo homepage; the latest *release* tag is the
    # version (drafts are excluded by the API, which is what we want).
    repo = checkver["github"].rstrip("/").removeprefix("https://github.com/")
    data = _get_json(f"https://api.github.com/repos/{repo}/releases/latest")
    if "tag_name" not in data:
        msg = data.get("message", "unknown error")
        raise ValueError(f"GitHub API error for {repo}: {msg}")
    tag = data["tag_name"]
    return tag[1:] if re.fullmatch(r"v\d.*", tag) else tag


def _sha256(url: str) -> str:
    req = request(url, accept="application/octet-stream")
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        for chunk in iter(lambda: resp.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_tuple(version: str) -> tuple[int, ...]:
    """The leading numeric release of a version, e.g. ``1.2.3rc1`` -> ``(1, 2, 3)``."""
    match = re.match(r"\d+(?:\.\d+)*", version)
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def _parse_version(
    version: str,
) -> tuple[int, tuple[int, ...], tuple[int, int, int], tuple[int, int], tuple[int, int]] | None:
    """Parse a version into comparable tuples covering epoch, release, pre, post, and dev.

    Ordering semantics conform to PEP 440:
    dev < pre < final < post.
    """
    v = version.strip()
    epoch = 0
    if "!" in v:
        epoch_str, v = v.split("!", 1)
        if not epoch_str.isdigit():
            return None
        epoch = int(epoch_str)

    if "+" in v:
        v, _local = v.split("+", 1)

    match = re.fullmatch(
        r"^v?(\d+(?:\.\d+)*)"
        r"(?:[-._]?(a|alpha|b|beta|rc|c|pre|preview)[-._]?(\d*))?"
        r"(?:[-._]?(post|rev|r)[-._]?(\d*)|-(\d+))?"
        r"(?:[-._]?(dev)[-._]?(\d*))?$",
        v,
        re.IGNORECASE,
    )
    if not match:
        return None
    base_str, pre_tag, pre_num, post_tag, post_num, implicit_post, dev_tag, dev_num = (
        match.groups()
    )
    base = tuple(int(part) for part in base_str.split("."))

    tag_order = {
        "a": 1,
        "alpha": 1,
        "b": 2,
        "beta": 2,
        "rc": 3,
        "c": 3,
        "pre": 3,
        "preview": 3,
    }
    if pre_tag:
        pre_val = (
            -1,
            tag_order.get(pre_tag.lower(), 0),
            int(pre_num) if pre_num else 0,
        )
    elif dev_tag and not (post_tag or implicit_post):
        pre_val = (-2, 0, 0)
    else:
        pre_val = (0, 0, 0)

    if post_tag or implicit_post:
        post_number = implicit_post or post_num
        post_val = (1, int(post_number) if post_number else 0)
    else:
        post_val = (0, 0)

    dev_val = (-1, int(dev_num) if dev_num else 0) if dev_tag else (0, 0)
    return epoch, base, pre_val, post_val, dev_val


def _is_placeholder(data: dict) -> bool:
    """Return True if the manifest still carries the all-zero placeholder SHA."""
    placeholder_sha = "0" * 64
    if data.get("hash") == placeholder_sha:
        return True
    arch = data.get("architecture", {})
    if isinstance(arch, dict):
        for spec in arch.values():
            if isinstance(spec, dict) and spec.get("hash") == placeholder_sha:
                return True
    return False


def _is_shim(data: dict) -> bool:
    """True when the manifest is a Python package shim (pipx or uv tool)."""
    checkver_url = data.get("checkver", {}).get("url", "")
    depends = data.get("depends")
    is_pip_dep = depends in ("pipx", "uv") or (
        isinstance(depends, list) and any(d in ("pipx", "uv") for d in depends)
    )
    return is_pip_dep or (bool(checkver_url) and urlsplit(checkver_url).hostname == "pypi.org")


def _is_downgrade(latest: str, current: str) -> bool:
    """True only when ``latest`` is unambiguously an older release than ``current``.

    Compares parsed version tuples including epoch, pre-releases, post-releases,
    and dev-releases. Returns False when either side has no parseable release,
    so anything ambiguous proceeds and is caught in PR review rather than silently
    skipped. Guards against a yanked PyPI release or a deleted GitHub release
    making "latest" move backward.
    """
    latest_parsed = _parse_version(latest)
    current_parsed = _parse_version(current)
    if not latest_parsed or not current_parsed:
        return False
    l_epoch, l_base, l_pre, l_post, l_dev = latest_parsed
    c_epoch, c_base, c_pre, c_post, c_dev = current_parsed
    width = max(len(l_base), len(c_base))
    l_base += (0,) * (width - len(l_base))
    c_base += (0,) * (width - len(c_base))
    return (l_epoch, l_base, l_pre, l_post, l_dev) < (
        c_epoch,
        c_base,
        c_pre,
        c_post,
        c_dev,
    )


def _update_manifest(path: Path) -> str | None:
    """Bump one manifest in place; return a ``"old → new"`` note or None."""
    data = json.loads(path.read_text(encoding="utf-8"))
    checkver = data.get("checkver", {})
    if "version" not in data:
        raise KeyError(f"{path.name}: manifest has no 'version' field")
    current = data["version"]

    if "github" in checkver:
        latest = _latest_github(checkver)
    elif "url" in checkver and urlsplit(checkver["url"]).hostname == "pypi.org":
        latest = _latest_pypi(checkver)
    else:
        print(f"::warning::{path.name}: unrecognized checkver, skipping", file=sys.stderr)
        return None

    if latest == current and not _is_placeholder(data):
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
        manifest_arch = data.get("architecture")
        has_manifest_arch = isinstance(manifest_arch, dict) and bool(manifest_arch)
        if "architecture" in autoupdate or has_manifest_arch:
            autoupdate_arch = autoupdate.get("architecture")
            if not isinstance(autoupdate_arch, dict) or not autoupdate_arch:
                raise KeyError(
                    f"{path.name}: manifest has architecture specs but autoupdate.architecture is missing"
                )
            manifest_arch_keys = set(manifest_arch.keys()) if has_manifest_arch else set()
            autoupdate_arch_keys = set(autoupdate_arch.keys())
            missing = manifest_arch_keys - autoupdate_arch_keys
            if missing:
                raise KeyError(
                    f"architecture(s) {sorted(missing)} in manifest missing from autoupdate.architecture"
                )
            extra = autoupdate_arch_keys - manifest_arch_keys
            if extra:
                raise KeyError(
                    f"architecture(s) {sorted(extra)} in autoupdate but not in manifest"
                )
            new_arch = {}
            for arch, spec in autoupdate_arch.items():
                if "url" not in spec:
                    raise KeyError(f"autoupdate.architecture.{arch} missing 'url'")
                if "$version" not in spec["url"]:
                    raise ValueError(
                        f"autoupdate.architecture.{arch}.url has no '$version' placeholder"
                    )
                url = spec["url"].replace("$version", quote(latest, safe=""))
                new_arch[arch] = (url, _sha256(url))
            for arch, (url, digest) in new_arch.items():
                data["architecture"][arch]["url"] = url
                data["architecture"][arch]["hash"] = digest
                if "extract_dir" in autoupdate_arch[arch]:
                    data["architecture"][arch]["extract_dir"] = (
                        autoupdate_arch[arch]["extract_dir"].replace("$version", latest)
                    )
                elif "extract_dir" in data["architecture"][arch]:
                    current_extract = data["architecture"][arch]["extract_dir"]
                    if isinstance(current_extract, str) and current in current_extract:
                        data["architecture"][arch]["extract_dir"] = current_extract.replace(
                            current, latest
                        )
                if "bin" in autoupdate_arch[arch]:
                    data["architecture"][arch]["bin"] = (
                        autoupdate_arch[arch]["bin"].replace("$version", latest)
                    )
        elif "$version" in autoupdate.get("url", ""):
            url = autoupdate["url"].replace("$version", quote(latest, safe=""))
            data["url"], data["hash"] = url, _sha256(url)
        elif not _is_shim(data):
            raise ValueError(f"{path.name}: binary manifest has no autoupdate URL with '$version'")

        if "extract_dir" in autoupdate:
            data["extract_dir"] = autoupdate["extract_dir"].replace("$version", latest)
        elif "extract_dir" in data:
            current_extract = data["extract_dir"]
            if isinstance(current_extract, str) and current in current_extract:
                data["extract_dir"] = current_extract.replace(current, latest)

        if "bin" in autoupdate:
            old_bin = data.get("bin")
            new_bin = autoupdate["bin"].replace("$version", latest)
            data["bin"] = new_bin
            if "shortcuts" in autoupdate:
                data["shortcuts"] = [
                    [
                        part.replace("$version", latest) if isinstance(part, str) else part
                        for part in item
                    ]
                    if isinstance(item, list)
                    else item
                    for item in autoupdate["shortcuts"]
                ]
            elif "shortcuts" in data and old_bin:
                data["shortcuts"] = [
                    [new_bin if part == old_bin else part for part in item]
                    if isinstance(item, list)
                    else item
                    for item in data["shortcuts"]
                ]
        elif "bin" in data:
            old_bin = data["bin"]
            if isinstance(old_bin, str) and current in old_bin:
                new_bin = old_bin.replace(current, latest)
                data["bin"] = new_bin
                if "shortcuts" in data:
                    data["shortcuts"] = [
                        [new_bin if part == old_bin else part for part in item]
                        if isinstance(item, list)
                        else item
                        for item in data["shortcuts"]
                    ]
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
    """Match an exact manifest or its explicit pipx sibling."""
    return stem == target or stem == f"{target}-pipx"


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
