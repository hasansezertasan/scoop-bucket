"""Tests for ``scripts/update_manifests.py`` (stdlib ``unittest``, no network).

Run with ``python -m unittest discover -s tests`` from the repo root.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import update_manifests as um  # noqa: E402

# A pipx-shim manifest (like keycast-pipx): static noop URL, PyPI checkver.
PYPI = {
    "version": "0.1.0",
    "description": "demo",
    "url": "https://example/noop.ps1",
    "hash": "deadbeef",
    "checkver": {"url": "https://pypi.org/pypi/keycast/json", "jsonpath": "$.info.version"},
    "autoupdate": {"url": "https://example/noop.ps1"},
}

# A binary manifest (like keycast): GitHub checkver, per-arch url + hash.
GITHUB = {
    "version": "0.1.0",
    "description": "demo",
    "architecture": {
        "64bit": {
            "url": "https://github.com/o/r/releases/download/v0.1.0/keycast-windows.zip",
            "hash": "0" * 64,
            "extract_dir": "keycast",
        }
    },
    "bin": "keycast.exe",
    "checkver": {"github": "https://github.com/o/r"},
    "autoupdate": {
        "architecture": {
            "64bit": {
                "url": "https://github.com/o/r/releases/download/v$version/keycast-windows.zip"
            }
        }
    },
}


def _write(dir_path: Path, name: str, data: dict) -> Path:
    path = dir_path / f"{name}.json"
    path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
    return path


class DowngradeTest(unittest.TestCase):
    def test_forward_is_not_downgrade(self) -> None:
        self.assertFalse(um._is_downgrade("0.2.0", "0.1.0"))

    def test_lower_is_downgrade(self) -> None:
        self.assertTrue(um._is_downgrade("0.1.0", "0.2.0"))

    def test_padding(self) -> None:
        self.assertFalse(um._is_downgrade("1.2", "1.2.0"))

    def test_unparseable_proceeds(self) -> None:
        self.assertFalse(um._is_downgrade("weird", "0.1.0"))


class UpdateManifestTest(unittest.TestCase):
    def _tmp(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def test_pypi_bump_preserves_shape(self) -> None:
        path = _write(self._tmp(), "keycast-pipx", PYPI)
        with mock.patch.object(um, "_latest_pypi", return_value="0.2.0"):
            note = um._update_manifest(path)
        self.assertEqual(note, "`0.1.0` → `0.2.0`")
        written = json.loads(path.read_text())
        self.assertEqual(written["version"], "0.2.0")
        # the static noop url/hash are untouched, and key order is preserved
        self.assertEqual(written["url"], PYPI["url"])
        self.assertEqual(written["hash"], PYPI["hash"])
        self.assertEqual(list(written), list(PYPI))

    def test_github_bump_rewrites_url_and_hash(self) -> None:
        path = _write(self._tmp(), "keycast", GITHUB)
        with (
            mock.patch.object(um, "_latest_github", return_value="0.2.0"),
            mock.patch.object(um, "_sha256", return_value="f" * 64),
        ):
            note = um._update_manifest(path)
        self.assertEqual(note, "`0.1.0` → `0.2.0`")
        arch = json.loads(path.read_text())["architecture"]["64bit"]
        self.assertIn("v0.2.0", arch["url"])
        self.assertEqual(arch["hash"], "f" * 64)

    def test_github_skips_when_asset_missing(self) -> None:
        # A release whose zip isn't published yet 404s on hashing; skip, no write.
        path = _write(self._tmp(), "keycast", GITHUB)
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)  # type: ignore[arg-type]
        with (
            mock.patch.object(um, "_latest_github", return_value="0.2.0"),
            mock.patch.object(um, "_sha256", side_effect=err),
        ):
            note = um._update_manifest(path)
        self.assertIsNone(note)
        self.assertEqual(json.loads(path.read_text())["version"], "0.1.0")

    def test_no_change(self) -> None:
        path = _write(self._tmp(), "keycast-pipx", PYPI)
        with mock.patch.object(um, "_latest_pypi", return_value="0.1.0"):
            self.assertIsNone(um._update_manifest(path))

    def test_downgrade_skipped(self) -> None:
        path = _write(self._tmp(), "keycast-pipx", PYPI)
        with mock.patch.object(um, "_latest_pypi", return_value="0.0.9"):
            note = um._update_manifest(path)
        self.assertIsNone(note)
        self.assertEqual(json.loads(path.read_text())["version"], "0.1.0")

    def test_missing_version_raises(self) -> None:
        bad = {k: v for k, v in PYPI.items() if k != "version"}
        path = _write(self._tmp(), "keycast-pipx", bad)
        with self.assertRaises(KeyError):
            um._update_manifest(path)


class MainTest(unittest.TestCase):
    def _run(self, manifests: dict[str, dict], argv: list[str], **patches):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bucket = Path(tmp.name)
        for name, data in manifests.items():
            _write(bucket, name, data)
        buf = io.StringIO()
        with mock.patch.object(um, "BUCKET", bucket), redirect_stdout(buf):
            with mock.patch.multiple(um, **patches):
                code = um.main(argv)
        return code, buf.getvalue()

    def test_summary_format(self) -> None:
        code, out = self._run(
            {"keycast-pipx": PYPI}, [], _latest_pypi=mock.Mock(return_value="0.2.0")
        )
        self.assertEqual(code, 0)
        self.assertIn("- **keycast-pipx**: `0.1.0` → `0.2.0`", out)

    def test_no_updates_message(self) -> None:
        code, out = self._run(
            {"keycast-pipx": PYPI}, [], _latest_pypi=mock.Mock(return_value="0.1.0")
        )
        self.assertEqual(code, 0)
        self.assertIn("No updates available.", out)

    def test_family_filter_matches_both_keycast_manifests(self) -> None:
        # `keycast` targets the family: both `keycast` and `keycast-pipx`, not `other`.
        other = {**PYPI, "checkver": {"url": "https://pypi.org/pypi/other/json"}}
        code, out = self._run(
            {"keycast": GITHUB, "keycast-pipx": PYPI, "other": other},
            ["keycast"],
            _latest_github=mock.Mock(return_value="0.2.0"),
            _latest_pypi=mock.Mock(return_value="0.2.0"),
            _sha256=mock.Mock(return_value="a" * 64),
        )
        self.assertEqual(code, 0)
        self.assertIn("keycast-pipx", out)
        self.assertNotIn("other", out)

    def test_one_failure_does_not_abort_rest(self) -> None:
        boom = {**PYPI, "checkver": {"url": "https://pypi.org/pypi/boom/json"}}

        def latest_pypi(checkver: dict) -> str:
            if "boom" in checkver["url"]:
                raise RuntimeError("network down")
            return "0.2.0"

        code, out = self._run(
            {"aaa": PYPI, "boom": boom}, [], _latest_pypi=mock.Mock(side_effect=latest_pypi)
        )
        self.assertEqual(code, 1)  # nonzero because one failed
        self.assertIn("- **aaa**: `0.1.0` → `0.2.0`", out)  # healthy one still bumped


if __name__ == "__main__":
    unittest.main()
