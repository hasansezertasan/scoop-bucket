"""Tests for ``scripts/gen_readme_packages.py`` (stdlib ``unittest``, no network).

Exercises the pure classify/parse/render helpers and asserts the checked-in
README.md is up to date, so a manifest added without regenerating the table
fails CI. Run with ``python -m unittest discover -s tests`` from the repo root.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import gen_readme_packages as gen  # noqa: E402


class RouteTest(unittest.TestCase):
    def test_classifies_by_depends(self) -> None:
        self.assertEqual(gen.route({"depends": "pipx"}), "pipx")
        self.assertEqual(gen.route({"depends": "uv"}), "uv")

    def test_classifies_by_depends_list(self) -> None:
        self.assertEqual(gen.route({"depends": ["python", "pipx"]}), "pipx")
        self.assertEqual(gen.route({"depends": ["python", "uv"]}), "uv")
        self.assertEqual(gen.route({"depends": ["git"]}), "binary")

    def test_binary_when_no_depends(self) -> None:
        self.assertEqual(gen.route({"bin": "widget.exe"}), "binary")


class PypiNameTest(unittest.TestCase):
    def test_parses_installed_package_from_script(self) -> None:
        manifest = {"installer": {"script": "pipx install widget==0.2.0 --force"}}
        self.assertEqual(gen.pypi_name(manifest, "widget-pipx"), "widget")

    def test_falls_back_to_stem(self) -> None:
        self.assertEqual(gen.pypi_name({}, "widget"), "widget")


class LinksTest(unittest.TestCase):
    def test_binary_gets_repo_only(self) -> None:
        out = gen.links({"homepage": "https://example.com"}, "binary", "widget")
        self.assertEqual(out, " ([repo](https://example.com))")

    def test_shim_gets_repo_and_pypi(self) -> None:
        out = gen.links({"homepage": "https://example.com"}, "uv", "widget")
        self.assertIn("[repo](https://example.com)", out)
        self.assertIn("[PyPI](https://pypi.org/project/widget/)", out)


class CellTest(unittest.TestCase):
    def test_escapes_pipe(self) -> None:
        self.assertEqual(gen.cell("a | b"), "a \\| b")

    def test_collapses_newlines_and_whitespace(self) -> None:
        self.assertEqual(gen.cell("first\nsecond   third"), "first second third")


class SpliceTest(unittest.TestCase):
    def test_replaces_only_marked_region(self) -> None:
        readme = f"intro\n{gen.BEGIN}\nold\n{gen.END}\noutro\n"
        result = gen.splice(readme, f"{gen.BEGIN}\nnew\n{gen.END}")
        self.assertEqual(result, f"intro\n{gen.BEGIN}\nnew\n{gen.END}\noutro\n")

    def test_missing_markers_exits(self) -> None:
        with self.assertRaises(SystemExit):
            gen.splice("no markers here", "table")


class TableTest(unittest.TestCase):
    def test_strips_route_suffix_from_description(self) -> None:
        table = gen.build_table()
        # The "(uv tool install)" / "(pipx install)" hints must not leak in.
        self.assertNotIn("(uv tool install)", table)
        self.assertNotIn("(pipx install)", table)

    def test_binary_precedes_shims(self) -> None:
        with TemporaryDirectory() as tmp:
            bucket = Path(tmp)
            (bucket / "binary.json").write_text(
                '{"description":"Binary","homepage":"https://example.com","bin":"tool.exe"}'
            )
            (bucket / "shim.json").write_text(
                '{"description":"Shim","homepage":"https://example.com","depends":"uv"}'
            )
            with patch.object(gen, "BUCKET", bucket):
                table = gen.build_table()
        self.assertLess(table.find("`binary`"), table.find("`shim`"))

    def test_empty_bucket_has_only_headers(self) -> None:
        with TemporaryDirectory() as tmp, patch.object(gen, "BUCKET", Path(tmp)):
            table = gen.build_table()
        self.assertIn("| Package | What it does | Route | Needs Python? | Source |", table)
        self.assertEqual(table.count("\n"), 3)


class ReadmeFreshnessTest(unittest.TestCase):
    def test_readme_table_is_current(self) -> None:
        current = gen.README.read_text(encoding="utf-8")
        updated = gen.splice(current, gen.build_table())
        self.assertEqual(current, updated,
                         "README.md packages table is stale; run "
                         "`mise run generate-readme`")


if __name__ == "__main__":
    unittest.main()
