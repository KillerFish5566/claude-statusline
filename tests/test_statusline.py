# -*- coding: utf-8 -*-
"""Run: python -m unittest discover -s tests"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import statusline as sl  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")
NOW = time.time()


def plain(s):
    return ANSI.sub("", s)


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


SAMPLE = {
    "workspace": {"current_dir": str(ROOT)},
    "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
    "effort": {"level": "high"},
    "context_window": {"used_percentage": 34.2},
    "rate_limits": {
        "five_hour": {"used_percentage": 23, "resets_at": NOW + 3600},
        "seven_day": {"used_percentage": 81, "resets_at": NOW + 3 * 86400},
    },
    "cost": {"total_cost_usd": 1.234, "total_duration_ms": 2520000,
             "total_lines_added": 120, "total_lines_removed": 30},
}


def claude_cfg(fetched_ago=3 * 3600, resets_in=2 * 86400, account="acct-1", cache_account="acct-1"):
    return {
        "oauthAccount": {"accountUuid": account, "organizationType": "claude_max",
                         "organizationRateLimitTier": "default_claude_max_5x",
                         "emailAddress": "someone@example.com"},
        "cachedUsageUtilization": {
            "fetchedAtMs": (NOW - fetched_ago) * 1000,
            "accountUuid": cache_account,
            "utilization": {"limits": [
                {"kind": "session", "percent": 7},
                {"kind": "weekly_scoped", "percent": 42,
                 "resets_at": iso(NOW + resets_in).replace("Z", "+00:00"),
                 "scope": {"model": {"display_name": "Fable"}}},
            ]},
        },
    }


class Env(unittest.TestCase):
    """Points CLAUDE_CONFIG_DIR / CODEX_HOME at a temp folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self._env = {k: os.environ.get(k) for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.dir / "claude")
        os.environ["CODEX_HOME"] = str(self.dir / "codex")
        (self.dir / "claude").mkdir()
        sl.T = sl.STRINGS["en"]

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def write_cfg(self, cfg):
        (self.dir / "claude" / ".claude.json").write_text(json.dumps(cfg), encoding="utf-8")

    def codex_job(self, started_ago, events, cwd, review=True):
        st = NOW - started_ago
        lt = time.localtime(st)
        day = self.dir / "codex" / "sessions" / time.strftime("%Y/%m/%d", lt)
        day.mkdir(parents=True, exist_ok=True)
        rows = [{"timestamp": iso(st), "type": "session_meta", "payload": {"cwd": cwd}},
                {"timestamp": iso(st), "type": "event_msg", "payload": {"type": "task_started"}}]
        if review:
            rows.append({"timestamp": iso(st), "type": "event_msg",
                         "payload": {"type": "item_completed", "text": "$independent-review"}})
        for ago, kind in events:
            payload = ({"type": "task_complete"} if kind == "done" else
                       {"type": "item_completed", "item": {"type": "CommandExecution"}})
            rows.append({"timestamp": iso(NOW - ago), "type": "event_msg", "payload": payload})
        name = time.strftime("rollout-%Y-%m-%dT%H-%M-%S-", lt) + f"{started_ago}.jsonl"
        (day / name).write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows),
                                encoding="utf-8")


class Formatting(unittest.TestCase):
    def test_model_names(self):
        cases = {"claude-opus-5-5": "Opus 5.5", "claude-fable-5-1": "Fable 5.1",
                 "claude-haiku-4-5-20251001": "Haiku 4.5", "claude-sonnet-5": "Sonnet 5",
                 "claude-opus-5-5[1m]": "Opus 5.5 1M"}
        for mid, want in cases.items():
            self.assertEqual(sl.model_name({"model": {"id": mid}}), want, mid)
        self.assertEqual(sl.model_name({"model": {"id": "x", "display_name": "X"}}), "X")

    def test_width_counts_cjk_as_two(self):
        self.assertEqual(sl.width("\x1b[0mab記憶"), 6)

    def test_fit_shrinks_before_dropping(self):
        segs = [(["AAAA long", "AAAA"], 0), (["BBBB long", "BBBB"], 1)]
        self.assertEqual(plain(sl.fit(segs, 100)), "AAAA long │ BBBB long")
        self.assertEqual(plain(sl.fit(segs, 16)), "AAAA long │ BBBB")
        self.assertEqual(plain(sl.fit(segs, 11)), "AAAA │ BBBB")
        self.assertEqual(plain(sl.fit(segs, 5)), "AAAA")

    def test_iso_to_epoch(self):
        self.assertEqual(sl.iso_to_epoch("1970-01-01T00:01:00.5+00:00"), 60)
        self.assertIsNone(sl.iso_to_epoch(None))


class ClaudeCache(Env):
    def test_tier(self):
        self.assertEqual(sl.plan_tier(claude_cfg()), "Max 5x")
        self.assertEqual(sl.plan_tier({"oauthAccount": {"organizationType": "claude_pro"}}), "Pro")
        self.assertEqual(sl.plan_tier({}), "")

    def test_scoped_limit_with_age(self):
        lims = sl.scoped_weekly_limits(claude_cfg(), NOW)
        self.assertEqual([(l["name"], l["pct"]) for l in lims], [("Fable", 42.0)])
        self.assertIn("(3h ago)", plain(sl.seg_scoped(lims[0])[0]))

    def test_fresh_cache_has_no_age_tag(self):
        lim = sl.scoped_weekly_limits(claude_cfg(fetched_ago=60), NOW)[0]
        self.assertEqual(plain(sl.seg_scoped(lim)[0]), "Fable wk 42%")

    def test_expired_window_is_hidden(self):
        self.assertEqual(sl.scoped_weekly_limits(claude_cfg(resets_in=-60), NOW), [])

    def test_other_account_is_hidden(self):
        self.assertEqual(sl.scoped_weekly_limits(claude_cfg(cache_account="acct-2"), NOW), [])

    def test_render_never_shows_email(self):
        self.write_cfg(claude_cfg())
        out = plain(sl.render(SAMPLE, 200, NOW, codex=False))
        self.assertNotIn("example.com", out)
        self.assertIn("Max 5x", out)
        self.assertIn("Fable wk 42%", out)


class Codex(Env):
    def test_states(self):
        self.codex_job(130, [(100, "cmd"), (60, "cmd"), (5, "cmd")], "C:\\w\\running-proj")
        self.codex_job(400, [(300, "cmd"), (90, "done")], "/w/done-proj")
        self.codex_job(2000, [(1900, "cmd"), (1200, "cmd")], "/w/stale-proj", review=False)
        self.codex_job(3000, [(2900, "cmd"), (600, "done")], "/w/old-proj")
        runs = sl.codex_runs(NOW)
        by = {r["project"]: r for r in runs}
        self.assertEqual(set(by), {"running-proj", "done-proj", "stale-proj"})
        self.assertTrue(by["running-proj"]["running"])
        self.assertEqual(by["running-proj"]["steps"], 3)
        self.assertTrue(by["stale-proj"]["stale"])
        self.assertFalse(by["done-proj"]["running"])
        lines = [plain(sl.codex_line(r, NOW)) for r in runs]
        self.assertTrue(any(l.startswith("◐ Codex · running-proj · review running") for l in lines))
        self.assertTrue(any(l.startswith("✓ Codex · done-proj · review done") for l in lines))
        self.assertTrue(any(l.startswith("⚠ Codex · stale-proj · no activity") for l in lines))

    def test_no_codex_folder(self):
        self.assertEqual(sl.codex_runs(NOW), [])


class EndToEnd(Env):
    def run_script(self, stdin, *args, cols="120"):
        env = dict(os.environ, COLUMNS=cols, PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "statusline.py"), *args],
                           input=stdin.encode("utf-8"), capture_output=True, env=env, timeout=20)
        return r.returncode, plain(r.stdout.decode("utf-8"))

    def test_sample_zh(self):
        self.write_cfg(claude_cfg())
        code, out = self.run_script(json.dumps(SAMPLE), "--lang", "zh-TW", cols="200")
        self.assertEqual(code, 0)
        l1, l2 = out.splitlines()[:2]
        self.assertTrue(l1.startswith("Opus 5.5 · high · Max 5x │ claude-statusline"))
        self.assertIn("記憶 ▰▰▰▱▱▱▱▱▱▱ 34%", l1)
        self.assertIn("5小時 23%", l2)
        self.assertIn("Fable 週 42% (3小時前)", l2)
        self.assertIn("+120 -30", l2)

    def test_narrow_terminal(self):
        code, out = self.run_script(json.dumps(SAMPLE), cols="50")
        self.assertEqual(code, 0)
        for ln in out.splitlines():
            self.assertLessEqual(sl.width(ln), 46, ln)

    def test_bad_input_still_prints(self):
        for stdin in ("", "{}", "garbage", "[1,2]"):
            code, out = self.run_script(stdin)
            self.assertEqual(code, 0, stdin)
            self.assertTrue(out.strip(), stdin)


if __name__ == "__main__":
    unittest.main()
