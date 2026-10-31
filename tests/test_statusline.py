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

CALM = dict(SAMPLE, rate_limits={"five_hour": {"used_percentage": 23}})


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
        # 2026-10-30 rename: Max 5x -> Max, Max 20x -> Ultra (account codes unchanged)
        self.assertEqual(sl.plan_tier(claude_cfg()), "Max")
        max20 = {"oauthAccount": {"organizationType": "claude_max",
                                  "organizationRateLimitTier": "default_claude_max_20x"}}
        self.assertEqual(sl.plan_tier(max20), "Ultra")
        self.assertEqual(sl.plan_tier({"oauthAccount": {"organizationType": "claude_pro"}}), "Pro")
        self.assertEqual(sl.plan_tier({"oauthAccount": {"organizationType": "claude_ultra"}}), "Ultra")
        self.assertEqual(sl.plan_tier({}), "")

    def test_tier_prefers_plan_display_name(self):
        def acct(**kw):
            return {"oauthAccount": dict({"organizationType": "claude_max",
                                          "organizationRateLimitTier": "default_claude_max_20x"}, **kw)}
        self.assertEqual(sl.plan_tier(acct(planDisplayName="Ultra")), "Ultra")
        self.assertEqual(sl.plan_tier(acct(planDisplayName="Claude Ultra")), "Ultra")
        self.assertEqual(sl.plan_tier(acct(planDisplayName="Max 20x")), "Ultra")
        self.assertEqual(sl.plan_tier(acct(planDisplayName="")), "Ultra")
        self.assertEqual(sl.plan_tier(acct(planDisplayName=None)), "Ultra")

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
        self.assertIn("· Max │", out)
        self.assertIn("Fable wk 42%", out)


class Mascot(Env):
    def rows(self, cols, mascot=True, data=SAMPLE):
        return plain(sl.render(data, cols, NOW, codex=False, mascot=mascot)).split("\n")

    def test_off_by_default(self):
        out = plain(sl.render(SAMPLE, 120, NOW, codex=False))
        self.assertNotIn("▛", out)
        self.assertNotIn(sl.NBSP, out)

    def art(self, mood):
        return [(a + m).replace(" ", sl.NBSP) for a, m, _ in sl.mascot_rows(mood)]

    def test_three_rows_right_aligned(self):
        for data in (SAMPLE, CALM):
            rows = self.rows(120, data=data)
            self.assertEqual(len(rows), 3)
            start = 120 - sl.MASCOT_W
            for row, art in zip(rows, self.art(sl.mascot_mood(data))):
                self.assertTrue(row.endswith(art), row)
                self.assertEqual(sl.width(row) - sl.width(art), start, row)
                self.assertNotIn("  ", row)   # Claude Code collapses plain spaces

    def test_text_keeps_clear_of_mascot(self):
        rows = self.rows(70)
        for row, art in zip(rows, self.art(sl.mascot_mood(SAMPLE))):
            text = row[:-len(art)].rstrip(sl.NBSP)
            self.assertLessEqual(sl.width(text) + sl.MASCOT_GAP, 70 - sl.MASCOT_W, row)

    def test_mood_rules(self):
        def d(h5=None, d7=None, ctx=None, added=None, removed=None, cost=None):
            out = {"rate_limits": {}, "context_window": {}, "cost": {}}
            if h5 is not None:
                out["rate_limits"]["five_hour"] = {"used_percentage": h5}
            if d7 is not None:
                out["rate_limits"]["seven_day"] = {"used_percentage": d7}
            if ctx is not None:
                out["context_window"]["used_percentage"] = ctx
            for k, v in (("total_lines_added", added), ("total_lines_removed", removed),
                         ("total_cost_usd", cost)):
                if v is not None:
                    out["cost"][k] = v
            return out
        cases = [
            ({}, "normal"),
            (d(h5=79.9, d7=79.9, ctx=79.9, added=200, removed=99, cost=19.99), "normal"),
            (d(h5=80), "tense"),
            (d(d7=95.5), "tense"),
            (d(ctx=80), "sweat"),
            (d(added=300), "proud"),
            (d(added=150, removed=150), "proud"),
            (d(cost=20), "proud"),
            # priority: usage > context > proud
            (d(h5=90, ctx=95, added=999, cost=99), "tense"),
            (d(ctx=95, added=999, cost=99), "sweat"),
        ]
        for data, want in cases:
            self.assertEqual(sl.mascot_mood(data), want, data)

    def test_mood_with_missing_or_odd_fields(self):
        odd = [
            {"rate_limits": None, "context_window": None, "cost": None},
            {"rate_limits": {"five_hour": None, "seven_day": {}}},
            {"rate_limits": {"five_hour": {"used_percentage": "abc"}}},
            {"rate_limits": {"five_hour": {"used_percentage": True}}},
            {"context_window": {"used_percentage": None}},
            {"context_window": {"used_percentage": float("nan")}},
            {"cost": {"total_lines_added": "lots", "total_cost_usd": [1]}},
            {"rate_limits": [], "context_window": "x", "cost": 5},
        ]
        for data in odd:
            self.assertEqual(sl.mascot_mood(data), "normal", data)
        self.assertEqual(sl.mascot_mood({"context_window": {"used_percentage": "85"}}), "sweat")

    def test_every_face_same_place_and_width(self):
        # Switching faces between redraws must not shift anything.
        datas = {
            "normal": CALM,
            "tense": dict(CALM, rate_limits={"five_hour": {"used_percentage": 88}}),
            "sweat": dict(CALM, context_window={"used_percentage": 91}),
            "proud": dict(CALM, cost={"total_lines_added": 400}),
        }
        self.assertEqual(set(datas), set(sl.MASCOT_FACES))
        shapes = {}
        for mood, data in datas.items():
            self.assertEqual(sl.mascot_mood(data), mood)
            rows = self.rows(100, data=data)
            for row, art in zip(rows, self.art(mood)):
                self.assertTrue(row.endswith(art), (mood, row))
            shapes[mood] = [(sl.width(r), sl.width(r) - sl.MASCOT_W) for r in rows]
            for a, m, _ in sl.mascot_rows(mood):
                self.assertLessEqual(sl.width(a + m), sl.MASCOT_W)
            self.assertEqual(sl.width(rows[0]), 100, mood)
        self.assertEqual(len(set(map(tuple, shapes.values()))), 1, shapes)

    def test_face_marks(self):
        self.assertIn("!", self.rows(120, data=SAMPLE)[0])          # SAMPLE: 7-day 81 %
        self.assertTrue(self.rows(120, data=CALM)[0].endswith("█" + sl.NBSP))
        out = sl.render(dict(CALM, context_window={"used_percentage": 90}), 120, NOW,
                        codex=False, mascot=True)
        self.assertIn(sl.C_SWEAT + "▖", out)

    def test_narrow_terminal_drops_mascot(self):
        rows = self.rows(sl.MASCOT_MIN_COLS - 1)
        self.assertNotIn("▛", "\n".join(rows))
        self.assertEqual(rows, self.rows(sl.MASCOT_MIN_COLS - 1, mascot=False))

    def test_first_row_only(self):
        rows = self.rows(120, data={"model": {"id": "claude-opus-5-5"}})
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[2].startswith(sl.NBSP))

    def test_cli_flag(self):
        env = dict(os.environ, COLUMNS="120", PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "statusline.py"), "--mascot"],
                           input=json.dumps(SAMPLE).encode("utf-8"), capture_output=True,
                           env=env, timeout=20)
        self.assertEqual(r.returncode, 0)
        out = plain(r.stdout.decode("utf-8"))
        self.assertIn(sl.MASCOT[1], out)
        self.assertIn("!", out.splitlines()[0])    # SAMPLE: 7-day usage 81 % -> tense


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
        self.assertTrue(l1.startswith("Opus 5.5 · high · Max │ claude-statusline"))
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
