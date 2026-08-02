"""Tests for ``scripts/add_manifest.py`` (stdlib ``unittest``, no network).

Network calls (PyPI, the GitHub API, the asset download) are mocked, so the
suite exercises the pure rendering/inference logic offline. Run with
``python -m unittest discover -s tests`` from the repo root.
"""

from __future__ import annotations

import io
import sys
import types
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import add_manifest as am  # noqa: E402


class HelperTest(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual(am.normalize("Foo_Bar.Baz"), "foo-bar-baz")

    def test_clean_desc_strips_article_and_period(self) -> None:
        self.assertEqual(am.clean_desc("A neat tool."), "Neat tool")

    def test_clean_desc_capitalizes(self) -> None:
        self.assertEqual(am.clean_desc("generic CLI"), "Generic CLI")

    def test_parse_repo_owner_repo(self) -> None:
        self.assertEqual(am.parse_repo("o/r"), ("o", "r"))

    def test_parse_repo_url_strips_git(self) -> None:
        self.assertEqual(am.parse_repo("https://github.com/o/r.git"), ("o", "r"))

    def test_parse_repo_rejects_garbage(self) -> None:
        with self.assertRaises(SystemExit):
            am.parse_repo("not-a-repo")

    def test_spdx_from_github_meta(self) -> None:
        self.assertEqual(am.spdx_license({"license": {"spdx_id": "MIT"}}), "MIT")

    def test_spdx_noassertion_falls_back(self) -> None:
        self.assertEqual(
            am.spdx_license({"license": {"spdx_id": "NOASSERTION"}}),
            "TODO-set-SPDX-license",
        )

    def test_pypi_homepage_prefers_case_insensitive_homepage(self) -> None:
        info = {"project_urls": {"homepage": "https://github.com/o/r",
                                 "source": "https://github.com/o/r.git"}}
        self.assertEqual(am.pypi_homepage(info, "r"), "https://github.com/o/r")

    def test_pypi_homepage_falls_back_to_source_without_git(self) -> None:
        info = {"project_urls": {"source": "https://github.com/o/r.git"}}
        self.assertEqual(am.pypi_homepage(info, "r"), "https://github.com/o/r")

    def test_pypi_homepage_default_is_pypi(self) -> None:
        self.assertEqual(am.pypi_homepage({}, "r"), "https://pypi.org/project/r/")

    def test_pypi_license_prefers_expression(self) -> None:
        self.assertEqual(am.pypi_license({"license_expression": "MIT"}), "MIT")

    def test_templatize_replaces_version(self) -> None:
        self.assertEqual(am.templatize("v1.2.3", "1.2.3"), "v$version")

    def test_templatize_warns_when_absent(self) -> None:
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            self.assertEqual(am.templatize("static.zip", "1.2.3"), "static.zip")
        self.assertIn("won't auto-update", buf.getvalue())


def _zip_bytes(entries: list[str]) -> str:
    """Write a throwaway .zip containing ``entries`` and return its path."""
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".zip")
    import os

    os.close(fd)
    with zipfile.ZipFile(path, "w") as archive:
        for name in entries:
            archive.writestr(name, b"x")
    return path


class InspectZipTest(unittest.TestCase):
    def _inspect(self, entries: list[str], token: str):
        import os

        path = _zip_bytes(entries)
        self.addCleanup(lambda: os.unlink(path))
        return am.inspect_zip(path, token)

    def test_single_wrapping_folder_and_lone_exe(self) -> None:
        extract_dir, exe, hint = self._inspect(
            ["keycast/keycast.exe", "keycast/data.bin"], "keycast")
        self.assertEqual(extract_dir, "keycast")
        self.assertEqual(exe, "keycast.exe")
        self.assertEqual(hint, "")

    def test_no_wrapping_folder(self) -> None:
        extract_dir, exe, _ = self._inspect(["tool.exe", "readme.txt"], "tool")
        self.assertIsNone(extract_dir)
        self.assertEqual(exe, "tool.exe")

    def test_multiple_exes_matches_token(self) -> None:
        extract_dir, exe, hint = self._inspect(
            ["app/app.exe", "app/helper.exe"], "app")
        self.assertEqual(exe, "app.exe")
        self.assertEqual(hint, "")

    def test_multiple_exes_no_match_returns_hint(self) -> None:
        _, exe, hint = self._inspect(["a.exe", "b.exe"], "c")
        self.assertIsNone(exe)
        self.assertIn("multiple .exe", hint)

    def test_no_exe_returns_hint(self) -> None:
        _, exe, hint = self._inspect(["readme.txt"], "tool")
        self.assertIsNone(exe)
        self.assertIn("no .exe", hint)


class SelectZipTest(unittest.TestCase):
    def test_lone_zip(self) -> None:
        asset = {"name": "app-windows.zip"}
        self.assertIs(am.select_zip([asset, {"name": "notes.txt"}], None), asset)

    def test_explicit_artifact(self) -> None:
        a, b = {"name": "x.zip"}, {"name": "y.zip"}
        self.assertIs(am.select_zip([a, b], "y.zip"), b)

    def test_multiple_zips_without_choice_exits(self) -> None:
        with self.assertRaises(SystemExit):
            am.select_zip([{"name": "a.zip"}, {"name": "b.zip"}], None)

    def test_no_assets_exits(self) -> None:
        with self.assertRaises(SystemExit):
            am.select_zip([], None)


class RenderTest(unittest.TestCase):
    def test_shim_uv_shape(self) -> None:
        data = am.render_shim("cobo", "cobo", "uv", "Demo (uv tool install)",
                              "https://github.com/o/cobo", "MIT", "1.0.0")
        self.assertEqual(
            list(data),
            ["version", "description", "homepage", "license", "depends", "url",
             "hash", "installer", "uninstaller", "checkver", "autoupdate"],
        )
        self.assertEqual(data["depends"], "uv")
        self.assertEqual(data["installer"]["script"],
                         "uv tool install cobo==$version --force")
        self.assertEqual(data["uninstaller"]["script"], "uv tool uninstall cobo")
        self.assertEqual(data["url"], am.NOOP_URL)
        self.assertEqual(data["hash"], am.NOOP_HASH)

    def test_shim_pipx_uses_pipx_commands(self) -> None:
        data = am.render_shim("keycast-pipx", "keycast", "pipx", "Demo (pipx install)",
                              "https://github.com/o/keycast", "MIT", "1.0.0")
        self.assertEqual(data["depends"], "pipx")
        self.assertEqual(data["installer"]["script"],
                         "pipx install keycast==$version --force")
        self.assertEqual(data["uninstaller"]["script"], "pipx uninstall keycast")

    def test_binary_shape_and_extract_dir(self) -> None:
        data = am.render_binary(
            "keycast", "https://github.com/o/keycast", "Demo", "MIT", "1.0.0",
            "https://github.com/o/keycast/releases/download/v1.0.0/keycast-windows.zip",
            "a" * 64, "keycast", "keycast.exe",
            "https://github.com/o/keycast/releases/download/v$version/keycast-windows.zip",
        )
        self.assertEqual(
            list(data),
            ["version", "description", "homepage", "license", "architecture",
             "bin", "shortcuts", "checkver", "autoupdate"],
        )
        self.assertEqual(data["architecture"]["64bit"]["extract_dir"], "keycast")
        self.assertEqual(data["bin"], "keycast.exe")
        self.assertEqual(data["shortcuts"], [["keycast.exe", "keycast"]])
        self.assertEqual(data["checkver"], {"github": "https://github.com/o/keycast"})

    def test_binary_omits_extract_dir_when_none(self) -> None:
        data = am.render_binary(
            "tool", "https://github.com/o/tool", "Demo", "MIT", "1.0.0",
            "u", "a" * 64, None, "tool.exe", "u")
        self.assertNotIn("extract_dir", data["architecture"]["64bit"])


class AddShimTest(unittest.TestCase):
    def _run(self, info: dict, **ns):
        captured = {}
        fields = {"package": "pkg", "via": "uv", "name": None, "homepage": None, **ns}
        args = types.SimpleNamespace(**fields)
        with (
            mock.patch.object(am, "pypi_info", return_value=info),
            mock.patch.object(am, "_write_manifest",
                              side_effect=lambda n, d: captured.update(name=n, data=d)
                              or Path(f"bucket/{n}.json")),
            mock.patch("sys.stdout", io.StringIO()),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            am.add_shim(args)
        return captured

    def test_uv_uses_bare_name(self) -> None:
        cap = self._run({"name": "cobo", "version": "1.0.0", "summary": "Demo tool",
                         "project_urls": {"homepage": "https://github.com/o/cobo"}})
        self.assertEqual(cap["name"], "cobo")
        self.assertEqual(cap["data"]["description"], "Demo tool (uv tool install)")

    def test_pipx_appends_suffix(self) -> None:
        cap = self._run({"name": "keycast", "version": "1.0.0", "summary": "Demo"},
                        via="pipx")
        self.assertEqual(cap["name"], "keycast-pipx")
        self.assertEqual(cap["data"]["depends"], "pipx")
        self.assertTrue(cap["data"]["description"].endswith("(pipx install)"))


class WriteManifestTest(unittest.TestCase):
    def test_rejects_path_separator(self) -> None:
        with self.assertRaises(SystemExit):
            am._write_manifest("../evil", {})

    def test_refuses_existing(self) -> None:
        # keycast.json already exists in the real bucket/.
        with self.assertRaises(SystemExit):
            am._write_manifest("keycast", {})


class PypiInfoTest(unittest.TestCase):
    def test_404_exits_cleanly(self) -> None:
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)  # type: ignore[arg-type]
        with mock.patch.object(am, "fetch_json", side_effect=err):
            with self.assertRaises(SystemExit):
                am.pypi_info("nope")


if __name__ == "__main__":
    unittest.main()
