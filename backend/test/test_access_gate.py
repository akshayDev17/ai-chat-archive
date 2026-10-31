"""Tests for the deploy gate that refuses to ship unless Access guards the desk.

Both deploy jobs in ``.github/workflows/ci.yml`` open with a step that asks the
Cloudflare API which Access application serves ``/api/desk/whoami``. If that
step passes while the path is public, the write API goes on the open internet
with a green tick. This file runs the step's own Python, taken from the
workflow's heredoc the way bash hands it to ``python3``, against a fake
Cloudflare API, and checks the verdict for each Access shape.

Runs offline.

Run:  python3 backend/test/test_access_gate.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import textwrap
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
STEP = "- name: Check the path is behind Cloudflare Access"
HOST = "akshayprabhakant.com"


def access_check_scripts() -> list[str]:
    lines = WORKFLOW.read_text().splitlines()
    scripts = []
    for i, line in enumerate(lines):
        if line.strip() != STEP:
            continue
        start = next(j for j in range(i, len(lines)) if lines[j].strip() == "python3 - <<'PY'") + 1
        end = next(j for j in range(start, len(lines)) if lines[j].strip() == "PY")
        scripts.append(textwrap.dedent("\n".join(lines[start:end])))
    return scripts


def fake_cloudflare(apps: list[dict], policies: dict[str, dict] | None = None):
    def urlopen(req, timeout=None):
        url = req.full_url
        if "/access/apps?" in url:
            body = {"success": True, "result": apps, "result_info": {"total_pages": 1}}
        elif "/access/policies/" in url:
            body = {"success": True, "result": (policies or {})[url.rsplit("/", 1)[1]]}
        else:
            raise AssertionError(f"unexpected request {url}")
        return io.BytesIO(json.dumps(body).encode())

    return urlopen


def run_check(script: str, urlopen) -> tuple[int, str]:
    env = {
        "ACCESS_PATH": "/api/desk/whoami",
        "CLOUDFLARE_API_TOKEN": "token",
        "CLOUDFLARE_ACCOUNT_ID": "account",
    }
    out = io.StringIO()
    code = 0
    with mock.patch.dict(os.environ, env), mock.patch("urllib.request.urlopen", urlopen):
        with contextlib.redirect_stdout(out):
            try:
                exec(compile(script, str(WORKFLOW), "exec"), {"__name__": "__main__"})
            except SystemExit as e:
                code = e.code or 0
    return code, out.getvalue()


def app(name: str, uri: str, *decisions: str) -> dict:
    return {
        "name": name,
        "type": "self_hosted",
        "destinations": [{"type": "public", "uri": uri}],
        "policies": [{"id": f"{name}-{d}", "decision": d} for d in decisions],
    }


class TestAccessGate(unittest.TestCase):
    def setUp(self):
        self.scripts = access_check_scripts()
        self.assertEqual(len(self.scripts), 2)

    def verdicts(self, urlopen) -> list[tuple[int, str]]:
        return [run_check(s, urlopen) for s in self.scripts]

    def assert_passes(self, urlopen):
        for code, out in self.verdicts(urlopen):
            self.assertEqual(code, 0, out)

    def assert_refuses(self, urlopen) -> list[str]:
        results = self.verdicts(urlopen)
        for code, out in results:
            self.assertEqual(code, 1, out)
            self.assertIn("::error::", out)
        return [out for _, out in results]

    def test_passes_when_the_desk_app_only_allows(self):
        self.assert_passes(fake_cloudflare([app("desk", f"{HOST}/api/desk/*", "allow")]))

    def test_passes_with_a_reusable_allow_policy_given_by_id(self):
        desk = app("desk", f"{HOST}/api/desk/*")
        desk["policies"] = ["reusable"]
        self.assert_passes(fake_cloudflare([desk], {"reusable": {"id": "reusable", "decision": "allow"}}))

    def test_refuses_when_nothing_covers_the_path(self):
        self.assert_refuses(fake_cloudflare([app("reader", f"{HOST}/chat-archives/*", "allow")]))

    def test_refuses_bypass_everyone_next_to_allow_on_the_covering_app(self):
        outs = self.assert_refuses(fake_cloudflare([app("desk", f"{HOST}/api/desk/*", "allow", "bypass")]))
        for out in outs:
            self.assertIn("Bypass", out)

    def test_refuses_a_broad_allow_shadowed_by_a_more_specific_app_without_allow(self):
        for decisions in (("bypass",), ("deny",), ()):
            with self.subTest(decisions=decisions):
                self.assert_refuses(fake_cloudflare([
                    app("site", f"{HOST}/*", "allow"),
                    app("desk", f"{HOST}/api/desk/*", *decisions),
                ]))

    def test_refuses_a_bare_domain_allow_shadowed_by_a_desk_bypass(self):
        self.assert_refuses(fake_cloudflare([
            app("site", HOST, "allow"),
            app("desk", f"{HOST}/api/desk/*", "bypass"),
        ]))

    def test_refuses_a_public_override_on_the_covering_destination(self):
        desk = app("desk", f"{HOST}/api/desk/*", "allow")
        desk["destinations"][0]["overrides"] = [{"behavior": "public", "path_pattern": "/api/desk/whoami"}]
        for out in self.assert_refuses(fake_cloudflare([desk])):
            self.assertIn("overrides", out)

    def test_an_exact_destination_beats_a_wildcard(self):
        self.assert_refuses(fake_cloudflare([
            app("desk", f"{HOST}/api/desk/*", "allow"),
            app("whoami", f"{HOST}/api/desk/whoami", "bypass"),
        ]))
        self.assert_passes(fake_cloudflare([
            app("desk", f"{HOST}/api/desk/*", "bypass"),
            app("whoami", f"https://{HOST}/api/desk/whoami", "allow"),
        ]))

    def test_a_path_without_a_wildcard_covers_what_sits_under_it(self):
        self.assert_passes(fake_cloudflare([app("desk", f"{HOST}/api/desk", "allow")]))
        self.assert_refuses(fake_cloudflare([
            app("site", f"{HOST}/*", "allow"),
            app("desk", f"{HOST}/api/desk", "bypass"),
        ]))

    def test_a_trailing_slash_on_a_path_without_a_wildcard_still_covers(self):
        self.assert_passes(fake_cloudflare([app("desk", f"{HOST}/api/desk/", "allow")]))
        self.assert_refuses(fake_cloudflare([
            app("site", f"{HOST}/*", "allow"),
            app("desk", f"{HOST}/api/desk/", "bypass"),
        ]))

    def test_a_mid_path_wildcard_does_not_hide_the_top_tier(self):
        self.assert_refuses(fake_cloudflare([
            app("any-whoami", f"{HOST}/api/*/whoami", "allow"),
            app("desk", f"{HOST}/api/desk/*", "bypass"),
        ]))
        self.assert_passes(fake_cloudflare([
            app("any-whoami", f"{HOST}/api/*/whoami", "allow"),
            app("desk", f"{HOST}/api/desk/*", "allow"),
        ]))

    def test_a_mid_path_wildcard_spans_segments_and_is_always_judged(self):
        self.assert_refuses(fake_cloudflare([
            app("whoami", f"{HOST}/api/desk/whoami", "allow"),
            app("any-whoami", f"{HOST}/api*/whoami", "bypass"),
        ]))
        self.assert_refuses(fake_cloudflare([
            app("desk", f"{HOST}/api/desk/*", "allow"),
            app("any-whoami", f"{HOST}/*/whoami", "bypass"),
        ]))

    def test_a_tie_judges_every_app_in_it(self):
        self.assert_refuses(fake_cloudflare([
            app("site", HOST, "allow"),
            app("everything", f"{HOST}/*", "bypass"),
        ]))

    def test_refuses_and_names_both_token_locations_when_the_token_lacks_access_read(self):
        def urlopen(req, timeout=None):
            body = io.BytesIO(b'{"success": false, "errors": [{"code": 10000, "message": "Authentication error"}]}')
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, body)

        for out in self.assert_refuses(urlopen):
            self.assertIn("My Profile → API Tokens", out)
            self.assertIn("Manage Account → Account API Tokens", out)


if __name__ == "__main__":
    unittest.main()
