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

# A pipx-shim manifest (like widget-pipx): static noop URL, PyPI checkver.
PYPI = {
    "version": "0.1.0",
    "description": "demo",
    "url": "https://example/noop.ps1",
    "hash": "deadbeef",
    "checkver": {"url": "https://pypi.org/pypi/widget/json", "jsonpath": "$.info.version"},
    "autoupdate": {"url": "https://example/noop.ps1"},
}

# A binary manifest (like widget): GitHub checkver, per-arch url + hash.
GITHUB = {
    "version": "0.1.0",
    "description": "demo",
    "architecture": {
        "64bit": {
            "url": "https://github.com/o/r/releases/download/v0.1.0/widget-windows.zip",
            "hash": "0" * 64,
            "extract_dir": "widget",
        }
    },
    "bin": "widget.exe",
    "checkver": {"github": "https://github.com/o/r"},
    "autoupdate": {
        "architecture": {
            "64bit": {
                "url": "https://github.com/o/r/releases/download/v$version/widget-windows.zip"
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

    def test_post_release_downgrade(self) -> None:
        self.assertTrue(um._is_downgrade("1.2", "1.2.post1"))
        self.assertFalse(um._is_downgrade("1.2.post1", "1.2"))
        self.assertFalse(um._is_downgrade("1.2.post2", "1.2.post1"))
        self.assertTrue(um._is_downgrade("1.2.post1", "1.2.post2"))

    def test_implicit_post_release_downgrade(self) -> None:
        # PEP 440 implicit post release: 1.0-1 normalizes to 1.0.post1
        self.assertTrue(um._is_downgrade("1.0", "1.0-1"))
        self.assertFalse(um._is_downgrade("1.0-1", "1.0"))
        self.assertTrue(um._is_downgrade("1.0-1", "1.0-2"))
        self.assertFalse(um._is_downgrade("1.0-2", "1.0-1"))
        self.assertFalse(um._is_downgrade("1.0-1", "1.0.post1"))
        self.assertFalse(um._is_downgrade("1.0.post1", "1.0-1"))
        self.assertTrue(um._is_downgrade("1.0-1", "1.0.post2"))
        self.assertTrue(um._is_downgrade("1.0-1.dev1", "1.0-1"))

    def test_pre_release_downgrade(self) -> None:
        self.assertTrue(um._is_downgrade("1.2rc1", "1.2"))
        self.assertFalse(um._is_downgrade("1.2", "1.2rc1"))

    def test_epoch_downgrade(self) -> None:
        self.assertTrue(um._is_downgrade("1!1.9", "1!2.0"))
        self.assertFalse(um._is_downgrade("1!2.0", "1!1.9"))
        self.assertFalse(um._is_downgrade("1!1.0", "2.0"))
        self.assertTrue(um._is_downgrade("2.0", "1!1.0"))

    def test_dev_release_downgrade(self) -> None:
        # Dev release is older than its final or pre-release target
        self.assertTrue(um._is_downgrade("1.2b1.dev1", "1.2b1"))
        self.assertFalse(um._is_downgrade("1.2b1", "1.2b1.dev1"))
        # Later pre-release with dev tag is still newer than earlier pre-release
        self.assertFalse(um._is_downgrade("1.2b1.dev1", "1.2a1"))
        self.assertTrue(um._is_downgrade("1.2a1", "1.2b1.dev1"))

    def test_local_metadata_downgrade(self) -> None:
        self.assertTrue(um._is_downgrade("v1.9.0+build.2", "v2.0.0+build.1"))
        self.assertFalse(um._is_downgrade("v2.0.0+build.1", "v1.9.0+build.2"))
        self.assertFalse(um._is_downgrade("2.0.0+1", "2.0.0+2"))


class LatestGithubTest(unittest.TestCase):
    def test_tag_stripping(self) -> None:
        checkver = {"github": "https://github.com/acme/tool"}
        with mock.patch.object(um, "_get_json", return_value={"tag_name": "v1.2.3"}):
            self.assertEqual(um._latest_github(checkver), "1.2.3")

        with mock.patch.object(um, "_get_json", return_value={"tag_name": "version-1.2.3"}):
            self.assertEqual(um._latest_github(checkver), "version-1.2.3")

        with mock.patch.object(um, "_get_json", return_value={"tag_name": "1.2.3"}):
            self.assertEqual(um._latest_github(checkver), "1.2.3")

    def test_missing_tag_name_raises_with_context(self) -> None:
        checkver = {"github": "https://github.com/acme/tool"}
        with mock.patch.object(um, "_get_json", return_value={"message": "rate limited"}):
            with self.assertRaises(ValueError) as ctx:
                um._latest_github(checkver)
        self.assertIn("rate limited", str(ctx.exception))
        self.assertIn("acme/tool", str(ctx.exception))


class UpdateManifestTest(unittest.TestCase):
    def _tmp(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def test_pypi_bump_preserves_shape(self) -> None:
        path = _write(self._tmp(), "widget-pipx", PYPI)
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
        path = _write(self._tmp(), "widget", GITHUB)
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
        path = _write(self._tmp(), "widget", GITHUB)
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)  # type: ignore[arg-type]
        with (
            mock.patch.object(um, "_latest_github", return_value="0.2.0"),
            mock.patch.object(um, "_sha256", side_effect=err),
        ):
            note = um._update_manifest(path)
        self.assertIsNone(note)
        self.assertEqual(json.loads(path.read_text())["version"], "0.1.0")

    def test_no_change(self) -> None:
        path = _write(self._tmp(), "widget-pipx", PYPI)
        with mock.patch.object(um, "_latest_pypi", return_value="0.1.0"):
            self.assertIsNone(um._update_manifest(path))

    def test_downgrade_skipped(self) -> None:
        path = _write(self._tmp(), "widget-pipx", PYPI)
        with mock.patch.object(um, "_latest_pypi", return_value="0.0.9"):
            note = um._update_manifest(path)
        self.assertIsNone(note)
        self.assertEqual(json.loads(path.read_text())["version"], "0.1.0")

    def test_missing_version_raises(self) -> None:
        bad = {k: v for k, v in PYPI.items() if k != "version"}
        path = _write(self._tmp(), "widget-pipx", bad)
        with self.assertRaises(KeyError):
            um._update_manifest(path)

    def test_placeholder_manifest_updates_when_version_matches(self) -> None:
        # A seed binary manifest at 0.0.0 with placeholder hash must be updated
        # even if latest release on GitHub is also 0.0.0.
        seeded = {
            **GITHUB,
            "version": "0.0.0",
            "architecture": {
                "64bit": {
                    "url": "https://github.com/o/r/releases/download/v0.0.0/widget-windows.zip",
                    "hash": "0" * 64,
                }
            },
        }
        path = _write(self._tmp(), "widget", seeded)
        with (
            mock.patch.object(um, "_latest_github", return_value="0.0.0"),
            mock.patch.object(um, "_sha256", return_value="a" * 64),
        ):
            note = um._update_manifest(path)
        self.assertEqual(note, "`0.0.0` → `0.0.0`")
        written = json.loads(path.read_text())
        self.assertEqual(written["architecture"]["64bit"]["hash"], "a" * 64)

    def test_is_placeholder(self) -> None:
        zero_hash = "0" * 64
        real_hash = "a" * 64
        self.assertTrue(um._is_placeholder({"hash": zero_hash}))
        self.assertFalse(um._is_placeholder({"hash": real_hash}))
        self.assertTrue(
            um._is_placeholder({"architecture": {"64bit": {"hash": zero_hash}}})
        )
        self.assertFalse(
            um._is_placeholder({"architecture": {"64bit": {"hash": real_hash}}})
        )

    def test_unrecognized_checkver_is_skipped_with_warning(self) -> None:
        manifest = {"version": "1.0.0", "checkver": {"unknown": "value"}}
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _write(Path(tmp.name), "mystery", manifest)
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            note = um._update_manifest(path)
        self.assertIsNone(note)
        self.assertIn("unrecognized checkver", stderr.getvalue())

    def test_is_shim(self) -> None:
        self.assertTrue(um._is_shim({"depends": "pipx"}))
        self.assertTrue(um._is_shim({"depends": "uv"}))
        self.assertTrue(um._is_shim({"depends": ["pipx"]}))
        self.assertTrue(um._is_shim({"depends": ["python", "uv"]}))
        self.assertTrue(
            um._is_shim({"checkver": {"url": "https://pypi.org/pypi/foo/json"}})
        )
        self.assertFalse(um._is_shim({"installer": {"script": "pipx install foo"}}))
        self.assertFalse(um._is_shim({"checkver": {"github": "https://github.com/o/r"}}))

    def test_binary_manifest_with_installer_script_without_autoupdate_raises(self) -> None:
        manifest = {
            "version": "0.1.0",
            "url": "https://github.com/o/r/releases/download/v0.1.0/widget.zip",
            "hash": "0" * 64,
            "checkver": {"github": "https://github.com/o/r"},
            "installer": {"script": "echo install"},
        }
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _write(Path(tmp.name), "widget", manifest)
        with mock.patch.object(um, "_latest_github", return_value="0.2.0"):
            with self.assertRaises(ValueError) as ctx:
                um._update_manifest(path)
        self.assertIn("has no autoupdate URL with '$version'", str(ctx.exception))

    def test_update_manifest_encodes_version_in_urls(self) -> None:
        manifest = {
            **GITHUB,
            "autoupdate": {
                "architecture": {
                    "64bit": {
                        "url": "https://github.com/o/r/releases/download/$version/widget.zip"
                    }
                }
            },
        }
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _write(Path(tmp.name), "widget", manifest)
        fetched_urls = []

        def fake_sha256(url: str) -> str:
            fetched_urls.append(url)
            return "b" * 64

        with (
            mock.patch.object(um, "_latest_github", return_value="release#2"),
            mock.patch.object(um, "_sha256", side_effect=fake_sha256),
        ):
            note = um._update_manifest(path)
        self.assertEqual(note, "`0.1.0` → `release#2`")
        self.assertEqual(
            fetched_urls,
            ["https://github.com/o/r/releases/download/release%232/widget.zip"],
        )
        written = json.loads(path.read_text())
        self.assertEqual(written["version"], "release#2")
        self.assertEqual(
            written["architecture"]["64bit"]["url"],
            "https://github.com/o/r/releases/download/release%232/widget.zip",
        )

    def test_update_manifest_encodes_version_in_top_level_url(self) -> None:
        manifest = {
            "version": "0.1.0",
            "url": "https://github.com/o/r/releases/download/v0.1.0/widget.zip",
            "hash": "0" * 64,
            "checkver": {"github": "https://github.com/o/r"},
            "autoupdate": {
                "url": "https://github.com/o/r/releases/download/$version/widget.zip"
            },
        }
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _write(Path(tmp.name), "widget", manifest)
        fetched_urls = []

        def fake_sha256(url: str) -> str:
            fetched_urls.append(url)
            return "b" * 64

        with (
            mock.patch.object(um, "_latest_github", return_value="release#2"),
            mock.patch.object(um, "_sha256", side_effect=fake_sha256),
        ):
            note = um._update_manifest(path)
        self.assertEqual(note, "`0.1.0` → `release#2`")
        self.assertEqual(
            fetched_urls,
            ["https://github.com/o/r/releases/download/release%232/widget.zip"],
        )
        written = json.loads(path.read_text())
        self.assertEqual(
            written["url"],
            "https://github.com/o/r/releases/download/release%232/widget.zip",
        )

    def test_update_manifest_updates_versioned_extract_dir_and_bin_from_autoupdate(self) -> None:
        manifest = {
            "version": "1.2.3",
            "architecture": {
                "64bit": {
                    "url": "https://github.com/o/r/releases/download/v1.2.3/widget-1.2.3.zip",
                    "hash": "0" * 64,
                    "extract_dir": "widget-1.2.3",
                }
            },
            "bin": "widget-1.2.3.exe",
            "shortcuts": [["widget-1.2.3.exe", "widget"]],
            "checkver": {"github": "https://github.com/o/r"},
            "autoupdate": {
                "architecture": {
                    "64bit": {
                        "url": "https://github.com/o/r/releases/download/v$version/widget-$version.zip",
                        "extract_dir": "widget-$version",
                    }
                },
                "bin": "widget-$version.exe",
                "shortcuts": [["widget-$version.exe", "widget"]],
            },
        }
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _write(Path(tmp.name), "widget", manifest)

        with (
            mock.patch.object(um, "_latest_github", return_value="1.2.4"),
            mock.patch.object(um, "_sha256", return_value="c" * 64),
        ):
            note = um._update_manifest(path)
        self.assertEqual(note, "`1.2.3` → `1.2.4`")
        written = json.loads(path.read_text())
        self.assertEqual(written["version"], "1.2.4")
        self.assertEqual(written["architecture"]["64bit"]["extract_dir"], "widget-1.2.4")
        self.assertEqual(written["bin"], "widget-1.2.4.exe")
        self.assertEqual(written["shortcuts"], [["widget-1.2.4.exe", "widget"]])

    def test_update_manifest_updates_versioned_paths_fallback_without_autoupdate_templates(self) -> None:
        manifest = {
            "version": "1.2.3",
            "architecture": {
                "64bit": {
                    "url": "https://github.com/o/r/releases/download/v1.2.3/widget-1.2.3.zip",
                    "hash": "0" * 64,
                    "extract_dir": "widget-1.2.3",
                }
            },
            "bin": "widget-1.2.3.exe",
            "shortcuts": [["widget-1.2.3.exe", "widget"]],
            "checkver": {"github": "https://github.com/o/r"},
            "autoupdate": {
                "architecture": {
                    "64bit": {
                        "url": "https://github.com/o/r/releases/download/v$version/widget-$version.zip",
                    }
                },
            },
        }
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _write(Path(tmp.name), "widget", manifest)

        with (
            mock.patch.object(um, "_latest_github", return_value="1.2.4"),
            mock.patch.object(um, "_sha256", return_value="d" * 64),
        ):
            note = um._update_manifest(path)
        self.assertEqual(note, "`1.2.3` → `1.2.4`")
        written = json.loads(path.read_text())
        self.assertEqual(written["version"], "1.2.4")
        self.assertEqual(written["architecture"]["64bit"]["extract_dir"], "widget-1.2.4")
        self.assertEqual(written["bin"], "widget-1.2.4.exe")
        self.assertEqual(written["shortcuts"], [["widget-1.2.4.exe", "widget"]])

    def test_manifest_missing_architecture_in_autoupdate_raises(self) -> None:
        manifest = {
            "version": "0.1.0",
            "architecture": {
                "64bit": {
                    "url": "https://github.com/o/r/releases/download/v0.1.0/widget64.zip",
                    "hash": "0" * 64,
                },
                "arm64": {
                    "url": "https://github.com/o/r/releases/download/v0.1.0/widgetarm.zip",
                    "hash": "0" * 64,
                },
            },
            "checkver": {"github": "https://github.com/o/r"},
            "autoupdate": {
                "architecture": {
                    "64bit": {
                        "url": "https://github.com/o/r/releases/download/v$version/widget64.zip"
                    }
                }
            },
        }
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _write(Path(tmp.name), "widget", manifest)
        with mock.patch.object(um, "_latest_github", return_value="0.2.0"):
            with self.assertRaises(KeyError) as ctx:
                um._update_manifest(path)
        self.assertIn("missing from autoupdate.architecture", str(ctx.exception))
        self.assertIn("arm64", str(ctx.exception))

    def test_autoupdate_extra_architecture_not_in_manifest_raises(self) -> None:
        manifest = {
            **GITHUB,
            "autoupdate": {
                "architecture": {
                    "64bit": {
                        "url": "https://github.com/o/r/releases/download/v$version/widget64.zip"
                    },
                    "32bit": {
                        "url": "https://github.com/o/r/releases/download/v$version/widget32.zip"
                    },
                }
            },
        }
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _write(Path(tmp.name), "widget", manifest)
        with mock.patch.object(um, "_latest_github", return_value="0.2.0"):
            with self.assertRaises(KeyError) as ctx:
                um._update_manifest(path)
        self.assertIn("in autoupdate but not in manifest", str(ctx.exception))
        self.assertIn("32bit", str(ctx.exception))

    def test_manifest_with_arch_specs_missing_autoupdate_arch_raises(self) -> None:
        manifest = {
            **GITHUB,
            "autoupdate": {
                "url": "https://github.com/o/r/releases/download/v$version/widget.zip"
            },
        }
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _write(Path(tmp.name), "widget", manifest)
        with mock.patch.object(um, "_latest_github", return_value="0.2.0"):
            with self.assertRaises(KeyError) as ctx:
                um._update_manifest(path)
        self.assertIn(
            "manifest has architecture specs but autoupdate.architecture is missing",
            str(ctx.exception),
        )

    def test_binary_manifest_without_version_in_arch_url_raises(self) -> None:
        manifest = {
            **GITHUB,
            "autoupdate": {
                "architecture": {
                    "64bit": {
                        "url": "https://github.com/o/r/releases/download/v0.1.0/widget-windows.zip"
                    }
                }
            },
        }
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _write(Path(tmp.name), "widget", manifest)
        with mock.patch.object(um, "_latest_github", return_value="0.2.0"):
            with self.assertRaises(ValueError) as ctx:
                um._update_manifest(path)
        self.assertIn("has no '$version' placeholder", str(ctx.exception))

    def test_binary_manifest_without_version_in_top_level_url_raises(self) -> None:
        manifest = {
            "version": "0.1.0",
            "url": "https://github.com/o/r/releases/download/v0.1.0/widget.zip",
            "hash": "0" * 64,
            "checkver": {"github": "https://github.com/o/r"},
            "autoupdate": {
                "url": "https://github.com/o/r/releases/download/v0.1.0/widget.zip"
            },
        }
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _write(Path(tmp.name), "widget", manifest)
        with mock.patch.object(um, "_latest_github", return_value="0.2.0"):
            with self.assertRaises(ValueError) as ctx:
                um._update_manifest(path)
        self.assertIn("has no autoupdate URL with '$version'", str(ctx.exception))

    def test_binary_manifest_without_autoupdate_raises(self) -> None:
        manifest = {
            "version": "0.1.0",
            "url": "https://github.com/o/r/releases/download/v0.1.0/widget.zip",
            "hash": "0" * 64,
            "checkver": {"github": "https://github.com/o/r"},
        }
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _write(Path(tmp.name), "widget", manifest)
        with mock.patch.object(um, "_latest_github", return_value="0.2.0"):
            with self.assertRaises(ValueError) as ctx:
                um._update_manifest(path)
        self.assertIn("has no autoupdate URL with '$version'", str(ctx.exception))

    def test_binary_manifest_top_level_url_bump_rewrites_url_and_hash(self) -> None:
        manifest = {
            "version": "0.1.0",
            "url": "https://github.com/o/r/releases/download/v0.1.0/widget.zip",
            "hash": "0" * 64,
            "checkver": {"github": "https://github.com/o/r"},
            "autoupdate": {
                "url": "https://github.com/o/r/releases/download/v$version/widget.zip"
            },
        }
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = _write(Path(tmp.name), "widget", manifest)
        with (
            mock.patch.object(um, "_latest_github", return_value="0.2.0"),
            mock.patch.object(um, "_sha256", return_value="b" * 64),
        ):
            note = um._update_manifest(path)
        self.assertEqual(note, "`0.1.0` → `0.2.0`")
        written = json.loads(path.read_text())
        self.assertEqual(written["version"], "0.2.0")
        self.assertEqual(
            written["url"],
            "https://github.com/o/r/releases/download/v0.2.0/widget.zip",
        )
        self.assertEqual(written["hash"], "b" * 64)


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
            {"widget-pipx": PYPI}, [], _latest_pypi=mock.Mock(return_value="0.2.0")
        )
        self.assertEqual(code, 0)
        self.assertIn("- **widget-pipx**: `0.1.0` → `0.2.0`", out)

    def test_no_updates_message(self) -> None:
        code, out = self._run(
            {"widget-pipx": PYPI}, [], _latest_pypi=mock.Mock(return_value="0.1.0")
        )
        self.assertEqual(code, 0)
        self.assertIn("No updates available.", out)

    def test_spoofed_pypi_hostname_is_not_fetched(self) -> None:
        spoof = {
            **PYPI,
            "checkver": {
                "url": "https://evil.example/pypi.org/widget/json",
                "jsonpath": "$.info.version",
            },
        }
        latest = mock.Mock(return_value="0.2.0")
        code, out = self._run({"widget": spoof}, [], _latest_pypi=latest)
        self.assertEqual(code, 0)
        self.assertIn("No updates available.", out)
        latest.assert_not_called()

    def test_family_filter_matches_both_widget_manifests(self) -> None:
        # `widget` targets the family: both `widget` and `widget-pipx`, not `other`.
        other = {**PYPI, "checkver": {"url": "https://pypi.org/pypi/other/json"}}
        code, out = self._run(
            {"widget": GITHUB, "widget-pipx": PYPI, "other": other},
            ["widget"],
            _latest_github=mock.Mock(return_value="0.2.0"),
            _latest_pypi=mock.Mock(return_value="0.2.0"),
            _sha256=mock.Mock(return_value="a" * 64),
        )
        self.assertEqual(code, 0)
        self.assertIn("widget-pipx", out)
        self.assertNotIn("other", out)

    def test_hyphenated_family_matches_its_pipx_sibling(self) -> None:
        code, out = self._run(
            {"foo-bar": GITHUB, "foo-bar-pipx": PYPI},
            ["foo-bar"],
            _latest_github=mock.Mock(return_value="0.2.0"),
            _latest_pypi=mock.Mock(return_value="0.2.0"),
            _sha256=mock.Mock(return_value="a" * 64),
        )
        self.assertEqual(code, 0)
        self.assertIn("**foo-bar**", out)
        self.assertIn("**foo-bar-pipx**", out)

    def test_short_family_does_not_match_hyphenated_package(self) -> None:
        code, out = self._run(
            {"foo": PYPI, "foo-bar": PYPI},
            ["foo"],
            _latest_pypi=mock.Mock(return_value="0.2.0"),
        )
        self.assertEqual(code, 0)
        self.assertIn("**foo**", out)
        self.assertNotIn("foo-bar", out)

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
