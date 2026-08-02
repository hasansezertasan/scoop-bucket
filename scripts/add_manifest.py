#!/usr/bin/env python3
"""Scaffold a new Scoop manifest for this bucket.

Two routes, mirroring the Homebrew tap's ``add_cask.py`` / ``add_formula.py``
split (Scoop has no cask/formula namespace, so each is a distinct manifest):

- **binary** (the tap's *cask* analogue): package a prebuilt Windows ``.zip``
  from a GitHub release. Reads the latest release, downloads the asset to
  compute its sha256, peeks inside the archive for the wrapping folder and the
  ``.exe`` to shim, and writes ``bucket/<name>.json`` with a version-templated
  ``autoupdate`` URL and a ``checkver.github`` block. ``--seed`` writes a
  ``0.0.0`` placeholder for a repo whose Windows build doesn't exist yet.
- **shim** (the tap's *formula* analogue): package a PyPI CLI as a
  ``uv tool install`` (default) or ``pipx install`` shim. There is **no**
  dependency resolution — uv/pipx resolve the tree at install time — so this is
  far simpler than ``add_formula.py``: the manifest's ``url``/``hash`` are the
  static ``noop.ps1`` (its hash never moves) and only ``version`` ever changes.

Standard library only — no third-party dependencies. Companion to
``update_manifests.py``. See the "Adding a manifest" section of CONTRIBUTING.md
for usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

GITHUB_API = "https://api.github.com"
PYPI = "https://pypi.org/pypi"
BUCKET = Path(__file__).resolve().parent.parent / "bucket"

# The static placeholder every shim manifest points its `url` at. Scoop requires
# a URL + hash, but the real install is done by the installer script, so the URL
# is a no-op PowerShell file whose sha256 never changes. Keep these in sync with
# scripts/noop.ps1 and the templates in CONTRIBUTING.md.
NOOP_URL = ("https://raw.githubusercontent.com/hasansezertasan/scoop-bucket/"
            "main/scripts/noop.ps1")
NOOP_HASH = "fdcbbea851292d9aa67f598bc6f1ab96e58873385972cd3d209ccab239cbad87"

# 64 zero hex digits: a syntactically valid placeholder sha256 for --seed mode.
# The first `scripts/update_manifests.py` run (autoupdate rewrites url + hash)
# overwrites it once the release ships the .zip.
_PLACEHOLDER_SHA = "0" * 64
_PLACEHOLDER_VERSION = "0.0.0"


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _request(url: str, *, accept: str = "application/json") -> urllib.request.Request:
    """Build an HTTP request, authenticating GitHub calls from the environment."""
    req = urllib.request.Request(url)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", "scoop-bucket-add-manifest")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    # Match the host EXACTLY — a substring test would leak the token to a URL like
    # https://evil.example.com/api.github.com/x. Asset URLs come from API responses,
    # so the value isn't fully under our control.
    if token and urlsplit(url).hostname == "api.github.com":
        req.add_header("Authorization", f"Bearer {token}")
    return req


def fetch_json(url: str) -> dict:
    """Fetch and decode a JSON document, failing loudly on HTTP errors."""
    with urllib.request.urlopen(_request(url)) as response:  # noqa: S310 - trusted host
        return json.load(response)


def normalize(name: str) -> str:
    """Normalize a name to a Scoop manifest token (lowercase, hyphen-separated)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def clean_desc(summary: str) -> str:
    """Format an upstream summary as a manifest ``description``.

    Strips a leading article and trailing period, then capitalizes the first
    letter — matching the phrasing of the hand-written manifests in ``bucket/``.
    """
    desc = re.sub(r"^(?:A|An|The)\s+", "", (summary or "").strip().rstrip("."))
    return desc[:1].upper() + desc[1:] if desc else desc


def _write_manifest(name: str, data: dict) -> Path:
    """Serialize a manifest to ``bucket/<name>.json`` (4-space indent, trailing NL)."""
    if "/" in name or "\\" in name:
        sys.exit(f"error: name {name!r} must not contain path separators")
    out = BUCKET / f"{name}.json"
    if out.exists():
        sys.exit(f"error: {out.relative_to(BUCKET.parent)} already exists; edit it "
                 "directly or pick another --name")
    out.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
# binary route (GitHub release .zip → cask analogue)
# --------------------------------------------------------------------------- #
def parse_repo(ref: str) -> tuple[str, str]:
    """Parse ``owner/repo`` or a GitHub URL into an ``(owner, repo)`` pair."""
    match = re.search(r"github\.com[/:]([^/]+)/([^/#?]+)", ref)
    if match:
        owner, repo = match.group(1), match.group(2)
    elif ref.count("/") == 1:
        owner, repo = ref.split("/", 1)
    else:
        sys.exit(f"error: cannot parse GitHub repo from {ref!r}; pass 'owner/repo' "
                 "or a github.com URL")
    return owner, repo.removesuffix(".git")


def spdx_license(meta: dict) -> str:
    """Best-effort SPDX license id from the GitHub repo metadata."""
    license_info = meta.get("license") or {}
    spdx = (license_info.get("spdx_id") or "").strip()
    # GitHub returns "NOASSERTION" when it can't map the LICENSE file.
    if spdx and spdx != "NOASSERTION":
        return spdx
    return "TODO-set-SPDX-license"


def select_zip(assets: list[dict], wanted: str | None) -> dict:
    """Pick the release asset to package, honoring --artifact or preferring a .zip."""
    if not assets:
        sys.exit("error: the latest release has no downloadable assets; pass --seed "
                 "to scaffold a placeholder, or --artifact once a release exists")
    if wanted:
        for asset in assets:
            if asset["name"] == wanted:
                return asset
        names = ", ".join(a["name"] for a in assets)
        sys.exit(f"error: no asset named {wanted!r}; available: {names}")
    zips = [a for a in assets if a["name"].lower().endswith(".zip")]
    if len(zips) == 1:
        return zips[0]
    if len(zips) > 1:
        # Multiple .zip assets usually means per-architecture builds. One 64bit
        # manifest can't serve both, so make the choice explicit instead of guessing.
        names = ", ".join(a["name"] for a in zips)
        sys.exit(f"error: multiple .zip assets ({names}); pass --artifact to pick one. "
                 "Per-architecture builds need a hand-written `architecture` block this "
                 "scaffolder does not emit")
    names = ", ".join(a["name"] for a in assets)
    sys.exit(f"error: no .zip asset found; pass --artifact to choose one of: {names}")


def download_zip(url: str) -> str:
    """Download a release asset to a temp file and return its path (caller unlinks)."""
    print(f"==> Downloading {url} to compute sha256 and inspect the archive",
          file=sys.stderr)
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    with os.fdopen(fd, "wb") as out:
        with urllib.request.urlopen(_request(url, accept="application/octet-stream")) as resp:  # noqa: S310
            for chunk in iter(lambda: resp.read(1 << 20), b""):
                out.write(chunk)
    return tmp


def sha256_of_file(path: str) -> str:
    """Return the sha256 of a file, reading it in bounded chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_zip(path: str, token: str) -> tuple[str | None, str | None, str]:
    """Infer ``(extract_dir, bin, hint)`` by peeking inside the downloaded archive.

    ``extract_dir`` is the single wrapping folder every entry shares (Scoop strips
    it), or None. ``bin`` is the ``.exe`` to shim: the lone executable, else the
    one whose stem matches ``token``, else None (the caller must be told via
    ``--bin``). ``hint`` warns when either was guessed.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n and not n.endswith("/")]
    except zipfile.BadZipFile:
        return None, None, f"{path} is not a valid .zip; verify the asset by hand"

    tops = {n.split("/", 1)[0] for n in names}
    nested = any("/" in n for n in names)
    extract_dir = tops.pop() if len(tops) == 1 and nested else None

    exes = [n for n in names if n.lower().endswith(".exe")]

    def _rel(entry: str) -> str:
        # Scoop strips `extract_dir`, so `bin` must be the executable's path
        # relative to that wrapper — not just its basename. `keycast/keycast.exe`
        # -> `keycast.exe`, but `tool/bin/tool.exe` -> `bin/tool.exe`.
        if extract_dir and entry.startswith(f"{extract_dir}/"):
            return entry[len(extract_dir) + 1:]
        return entry

    def _hint(rel: str) -> str:
        # A nested executable yields a `bin` path with a subfolder; flag it so the
        # maintainer confirms Scoop resolves it (most bundles ship the .exe flat).
        return (f'the executable is nested ("{rel}"); verify Scoop resolves that '
                "bin path") if "/" in rel else ""

    if len(exes) == 1:
        rel = _rel(exes[0])
        return extract_dir, rel, _hint(rel)
    matches = [e for e in exes if Path(e).stem.lower() == token]
    if len(matches) == 1:
        rel = _rel(matches[0])
        return extract_dir, rel, _hint(rel)
    if not exes:
        return extract_dir, None, ("no .exe found in the archive; set --bin to the "
                                   "executable Scoop should shim")
    names_str = ", ".join(exes)
    return extract_dir, None, (f"multiple .exe entries ({names_str}); set --bin to the "
                               "one to shim")


def templatize(text: str, version: str) -> str:
    """Replace the literal version in a URL/tag with Scoop's ``$version`` template."""
    if not version:
        return text
    if version not in text:
        print(f"warning: version {version!r} not found in {text!r}; that part of the "
              "URL won't auto-update on release bumps — verify the manifest",
              file=sys.stderr)
    return text.replace(version, "$version")


def render_binary(token: str, repo_url: str, desc: str, license_id: str,
                  version: str, concrete_url: str, sha: str,
                  extract_dir: str | None, exe: str,
                  autoupdate_url: str) -> dict:
    """Build the ordered binary-manifest dict (key order matches bucket/keycast.json)."""
    arch: dict = {"url": concrete_url, "hash": sha}
    if extract_dir:
        arch["extract_dir"] = extract_dir
    display = token
    return {
        "version": version,
        "description": desc,
        "homepage": repo_url,
        "license": license_id,
        "architecture": {"64bit": arch},
        "bin": exe,
        "shortcuts": [[exe, display]],
        "checkver": {"github": repo_url},
        "autoupdate": {"architecture": {"64bit": {"url": autoupdate_url}}},
    }


def add_binary(args: argparse.Namespace) -> None:
    owner, repo = parse_repo(args.repo)
    token = normalize(args.name or repo)
    try:
        meta = fetch_json(f"{GITHUB_API}/repos/{owner}/{repo}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            sys.exit(f"error: repo {owner}/{repo} not found")
        raise
    repo_url = meta.get("html_url") or f"https://github.com/{owner}/{repo}"
    desc = clean_desc(meta.get("description") or "TODO-set-description")
    license_id = spdx_license(meta)

    if args.seed:
        # No release to introspect: assume this bucket's conventions (a `v<version>`
        # tag and a static asset name) and template a URL the first bump can fill.
        if not args.bin:
            sys.exit("error: --seed can't inspect the archive; pass --bin <name>.exe "
                     "(and --extract-dir if the zip wraps its contents in a folder)")
        artifact = args.artifact or f"{token}-windows.zip"
        version, sha = _PLACEHOLDER_VERSION, _PLACEHOLDER_SHA
        concrete_url = (f"https://github.com/{owner}/{repo}/releases/download/"
                        f"v{version}/{artifact}")
        autoupdate_url = (f"https://github.com/{owner}/{repo}/releases/download/"
                          f"v$version/{artifact}")
        extract_dir, exe, hint = args.extract_dir, args.bin, ""
        print("==> Seeding placeholder manifest. VERIFY these guessed conventions "
              "against the first real release:\n"
              f"      tag: v$version   artifact: {artifact}\n"
              "    update_manifests.py refreshes only version + hash from this URL; "
              "it can't fix a wrong tag/filename pattern.", file=sys.stderr)
    else:
        try:
            release = fetch_json(f"{GITHUB_API}/repos/{owner}/{repo}/releases/latest")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                sys.exit(f"error: {owner}/{repo} has no published 'latest' release; "
                         "use --seed to scaffold a placeholder for the first release")
            raise
        tag = release["tag_name"]
        version = tag[1:] if re.fullmatch(r"v\d.*", tag) else tag
        asset = select_zip(release.get("assets", []), args.artifact)
        artifact = asset["name"]
        tmp = download_zip(asset["browser_download_url"])
        try:
            sha = sha256_of_file(tmp)
            found_dir, found_exe, hint = inspect_zip(tmp, token)
        finally:
            os.unlink(tmp)
        extract_dir = args.extract_dir or found_dir
        exe = args.bin or found_exe
        if not exe:
            sys.exit(f"error: {hint or 'could not determine the executable'}")
        concrete_url = (f"https://github.com/{owner}/{repo}/releases/download/"
                        f"{tag}/{artifact}")
        autoupdate_url = (f"https://github.com/{owner}/{repo}/releases/download/"
                          f"{templatize(tag, version)}/{templatize(artifact, version)}")

    data = render_binary(token, repo_url, desc, license_id, version, concrete_url,
                         sha, extract_dir, exe, autoupdate_url)
    out = _write_manifest(token, data)
    print(f"==> Wrote {out.parent.name}/{out.name} (binary, version {version})")
    if hint:
        print(f"warning: {hint}", file=sys.stderr)
    _binary_next_steps(token, extract_dir, exe)


def _binary_next_steps(token: str, extract_dir: str | None, exe: str) -> None:
    print(f"\nNext: verify the manifest, then `scoop install <bucket>/{token}` on "
          "Windows.", file=sys.stderr)
    if extract_dir is None:
        print("      No extract_dir was set (archive has no single wrapping folder) — "
              "confirm the .exe sits at the archive root.", file=sys.stderr)
    print(f"      Shimming bin: {exe}. Add a smoke-test command to tests.yml if it "
          f"isn't `{token} version`.", file=sys.stderr)


# --------------------------------------------------------------------------- #
# shim route (PyPI package → formula analogue)
# --------------------------------------------------------------------------- #
def pypi_info(package: str) -> dict:
    """Return the ``info`` block of a PyPI package's latest release."""
    try:
        return fetch_json(f"{PYPI}/{package}/json")["info"]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            sys.exit(f"error: PyPI package {package!r} not found")
        raise


def pypi_homepage(info: dict, package: str) -> str:
    """Best-effort project homepage, preferring an explicit repo/homepage URL.

    PyPI's JSON API lowercases ``project_urls`` labels, so match case-insensitively
    and prefer the homepage, then the source/repository (``.git`` stripped so it
    reads as a browsable URL like the hand-written manifests).
    """
    urls = {key.lower(): value for key, value in (info.get("project_urls") or {}).items()}
    for key in ("homepage", "repository", "source", "source code"):
        if urls.get(key):
            return urls[key].removesuffix(".git")
    return info.get("home_page") or f"https://pypi.org/project/{package}/"


def pypi_license(info: dict) -> str:
    """Best-effort SPDX license from the PEP 639 expression or a short license field."""
    expression = (info.get("license_expression") or "").strip()
    if expression:
        return expression
    raw = (info.get("license") or "").strip()
    if raw and len(raw) <= 40 and "\n" not in raw:
        return raw
    return "TODO-set-SPDX-license"


def render_shim(token: str, package: str, via: str, desc: str, homepage: str,
                license_id: str, version: str) -> dict:
    """Build the ordered shim-manifest dict (key order matches bucket/cobo.json)."""
    return {
        "version": version,
        "description": desc,
        "homepage": homepage,
        "license": license_id,
        "depends": via,
        "url": NOOP_URL,
        "hash": NOOP_HASH,
        "installer": {"script": f"{via} tool install {package}==$version --force"
                      if via == "uv" else f"pipx install {package}==$version --force"},
        "uninstaller": {"script": f"{via} tool uninstall {package}"
                        if via == "uv" else f"pipx uninstall {package}"},
        "checkver": {"url": f"https://pypi.org/pypi/{package}/json",
                     "jsonpath": "$.info.version"},
        "autoupdate": {"url": NOOP_URL},
    }


def add_shim(args: argparse.Namespace) -> None:
    via = args.via
    info = pypi_info(args.package)
    package = normalize(info.get("name") or args.package)
    version = info["version"]
    # uv shims take the bare tool name; a pipx shim usually coexists with a
    # same-named binary manifest, so it gets a `-pipx` suffix (like keycast-pipx).
    default_name = package if via == "uv" else f"{package}-pipx"
    token = normalize(args.name or default_name)
    suffix = " (uv tool install)" if via == "uv" else " (pipx install)"
    desc = clean_desc(info.get("summary") or "TODO-set-description") + suffix
    homepage = args.homepage or pypi_homepage(info, package)
    license_id = pypi_license(info)

    data = render_shim(token, package, via, desc, homepage, license_id, version)
    out = _write_manifest(token, data)
    print(f"==> Wrote {out.parent.name}/{out.name} ({via} shim, version {version})")
    print(f"\nNext: add {token} to README.md's package table, then run `mise run style`. "
          f"If the CLI's smoke-test isn't `{package} version`, add it to the map in "
          ".github/workflows/tests.yml.", file=sys.stderr)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="route", required=True)

    binary = sub.add_parser(
        "binary", help="package a prebuilt Windows .zip from a GitHub release")
    binary.add_argument("repo", help="GitHub repo as 'owner/repo' or a github.com URL")
    binary.add_argument("--name", help="manifest token (default: normalized repo name)")
    binary.add_argument("--bin", help="the .exe to shim (default: inferred from the zip)")
    binary.add_argument("--extract-dir",
                        help="wrapping folder Scoop strips (default: inferred from the zip)")
    binary.add_argument("--artifact",
                        help="release asset filename to package (default: the lone .zip)")
    binary.add_argument("--seed", action="store_true",
                        help="write a 0.0.0 placeholder without downloading (requires --bin)")
    binary.set_defaults(func=add_binary)

    shim = sub.add_parser(
        "shim", help="package a PyPI CLI as a uv-tool (default) or pipx shim")
    shim.add_argument("package", help="PyPI package name")
    shim.add_argument("--via", choices=("uv", "pipx"), default="uv",
                      help="installer backend (default: uv)")
    shim.add_argument("--name",
                      help="manifest token (default: <package>, or <package>-pipx for pipx)")
    shim.add_argument("--homepage", help="override the homepage URL")
    shim.set_defaults(func=add_shim)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
