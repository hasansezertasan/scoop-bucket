"""Tests for authenticated HTTP request construction."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import url_fetch  # noqa: E402


class GetJsonTest(unittest.TestCase):
    def test_invalid_json_includes_url_context(self) -> None:
        from io import BytesIO

        class FakeResponse:
            def read(self) -> bytes:
                return b"<html>Not JSON</html>"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()):
            with self.assertRaises(ValueError) as ctx:
                url_fetch.get_json("https://example.com/api")
        self.assertIn("https://example.com/api", str(ctx.exception))
        self.assertIn("<html>", str(ctx.exception))


class RequestTest(unittest.TestCase):
    def test_github_token_is_sent_only_to_exact_api_host(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "secret"}):
            api = url_fetch.request("https://api.github.com/repos/acme/widget")
            http_api = url_fetch.request("http://api.github.com/repos/acme/widget")
            spoof = url_fetch.request(
                "https://evil.example/api.github.com/pypi.org/widget"
            )
            pypi = url_fetch.request("https://pypi.org/pypi/widget/json")

        self.assertTrue(api.has_header("Authorization"))
        self.assertFalse(http_api.has_header("Authorization"))
        self.assertFalse(spoof.has_header("Authorization"))
        self.assertFalse(pypi.has_header("Authorization"))

    def test_redirect_handler_strips_authorization_on_external_or_http_redirect(self) -> None:
        import urllib.request

        handler = url_fetch.SafeRedirectHandler()
        req = urllib.request.Request(
            "https://api.github.com/repos/acme/widget/releases/assets/1",
            headers={"Authorization": "Bearer secret", "User-Agent": "test"},
        )

        # Redirect to external asset CDN (e.g. S3 / objects.githubusercontent.com)
        redir_external = handler.redirect_request(
            req, None, 302, "Found", {}, "https://objects.githubusercontent.com/asset.zip"
        )
        self.assertIsNotNone(redir_external)
        self.assertFalse(redir_external.has_header("Authorization"))

        # Redirect to unencrypted HTTP
        redir_http = handler.redirect_request(
            req, None, 302, "Found", {}, "http://api.github.com/another"
        )
        self.assertIsNotNone(redir_http)
        self.assertFalse(redir_http.has_header("Authorization"))

        # Redirect to same host over HTTPS preserves Authorization
        redir_internal = handler.redirect_request(
            req, None, 302, "Found", {}, "https://api.github.com/repos/acme/widget/releases/assets/2"
        )
        self.assertIsNotNone(redir_internal)
        self.assertTrue(redir_internal.has_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
