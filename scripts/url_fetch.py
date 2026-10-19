"""Shared HTTP request helpers for the bucket maintenance scripts."""

from __future__ import annotations

import json
import os
import urllib.request
from urllib.parse import urlsplit

USER_AGENT = "scoop-bucket-maintenance"


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Strip Authorization headers on redirects away from https://api.github.com."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            parts = urlsplit(newurl)
            if parts.scheme != "https" or parts.hostname != "api.github.com":
                for h in list(new_req.headers):
                    if h.lower() == "authorization":
                        del new_req.headers[h]
                for h in list(new_req.unredirected_hdrs):
                    if h.lower() == "authorization":
                        del new_req.unredirected_hdrs[h]
        return new_req


urllib.request.install_opener(urllib.request.build_opener(SafeRedirectHandler()))


def request(url: str, *, accept: str = "application/json") -> urllib.request.Request:
    """Build a request and send credentials only to GitHub's exact API host."""
    req = urllib.request.Request(url)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", USER_AGENT)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    parts = urlsplit(url)
    if token and parts.scheme == "https" and parts.hostname == "api.github.com":
        req.add_header("Authorization", f"Bearer {token}")
    return req


def get_json(url: str) -> dict:
    """Fetch and decode a JSON document."""
    with urllib.request.urlopen(request(url), timeout=30) as response:  # noqa: S310
        body = response.read()
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        preview = body[:200].decode("utf-8", errors="replace")
        raise ValueError(f"invalid JSON from {url}: {preview!r}") from exc
