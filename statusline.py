# -*- coding: utf-8 -*-
"""claude-statusline — a status line for Claude Code that also watches Codex.

Line 1: model · effort · plan tier │ folder (git branch) │ context usage
Line 2: 5-hour / 7-day limits │ per-model weekly limits (e.g. Fable) │ cost · duration │ lines changed
Line 3+: Codex jobs that are running or just finished (only when there are any)

Reads only local data: the JSON Claude Code pipes to stdin, `git status`,
Claude Code's own cache in `.claude.json`, and Codex session logs.
Never reads credentials and never touches the network.

Usage (settings.json):
    "statusLine": {"type": "command",
                   "command": "python /path/to/statusline.py --lang en",
                   "refreshInterval": 5}
"""
import argparse
import calendar
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

__version__ = "0.1.0"

# ── Strings ─────────────────────────────────────────────
STRINGS = {
    "en": {
        "ctx": "ctx", "h5": "5h", "d7": "7d", "week": "wk", "reset": "resets {}",
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "sec": "{}s", "min": "{}m", "hour": "{}h{:02d}m", "hours": "{}h", "ago": "{} ago",
        "worktree": "worktree", "review": "review", "job": "task",
        "running": "{job} running {t}", "done": "{job} done in {t}",
        "stale": "no activity for {t}", "cmds": "{} cmds",
        "error": "status line error (statusline.py)",
    },
    "zh-TW": {
        "ctx": "記憶", "h5": "5小時", "d7": "7天", "week": "週", "reset": "{}重置",
        "weekdays": ["週一", "週二", "週三", "週四", "週五", "週六", "週日"],
        "sec": "{}秒", "min": "{}分", "hour": "{}時{:02d}分", "hours": "{}小時", "ago": "{}前",
        "worktree": "工作樹", "review": "審查", "job": "工作",
        "running": "{job}中 {t}", "done": "{job}完成 用時 {t}",
        "stale": "無回應 {t}", "cmds": "指令 {} 次",
        "error": "狀態列腳本出錯（statusline.py）",
    },
}
T = STRINGS["en"]

# ── Colors (24-bit ANSI, tuned for dark themes) ─────────
RESET = "\x1b[0m"


def rgb(r, g, b):
    return f"\x1b[38;2;{r};{g};{b}m"


C_MODEL = rgb(217, 119, 87)
C_TIER = rgb(200, 160, 110)
C_DIR = rgb(120, 170, 255)
C_GIT = rgb(190, 140, 255)
C_DIM = rgb(128, 128, 128)
C_OK = rgb(110, 200, 120)
C_WARN = rgb(230, 190, 80)
C_BAD = rgb(235, 90, 90)
C_CODEX = rgb(90, 200, 200)

SEP = f" {C_DIM}│{RESET} "
DOT = f" {C_DIM}·{RESET} "
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

CODEX_LOOKBACK_S = 6 * 3600     # only look at Codex jobs started in the last 6 h
CODEX_DONE_SHOW_S = 180         # keep a finished job visible for 3 min
CODEX_STALE_S = 15 * 60         # no new events for 15 min -> "no activity"
CODEX_STALE_HIDE_S = 60 * 60    # hide stale jobs after 1 h
CODEX_MAX_ROWS = 3
USAGE_CACHE_AGE_SHOW_S = 10 * 60   # show "(3h ago)" once the cache is older than this


# ── Helpers ─────────────────────────────────────────────
def get(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def width(s):
    s = ANSI_RE.sub("", s)
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def pct_color(p):
    return C_OK if p < 50 else C_WARN if p < 80 else C_BAD


def fmt_dur(sec):
    sec = int(max(sec, 0))
    if sec < 60:
        return T["sec"].format(sec)
    m = sec // 60
    if m < 60:
        return T["min"].format(m)
    return T["hour"].format(m // 60, m % 60)


def fmt_reset(epoch, with_day):
    t = time.localtime(epoch)
    hm = f"{t.tm_hour:02d}:{t.tm_min:02d}"
    return f"{T['weekdays'][t.tm_wday]} {hm}" if with_day else hm


def iso_to_epoch(s):
    """'2026-10-02T07:59:59.784180+00:00' or '...Z' -> epoch seconds (UTC)."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", s or "")
    return calendar.timegm(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")) if m else None


def fit(segments, cols):
    """Join segments with SEP so the line fits `cols`.

    segments: list of (variants, priority). variants is a list of strings from
    longest to shortest. Higher priority number = shortened / dropped first.
    """
    segs = [[list(v for v in variants if v), prio] for variants, prio in segments]
    segs = [s for s in segs if s[0]]

    def line():
        return SEP.join(s[0][0] for s in segs)

    while segs and width(line()) > cols:
        shrinkable = [i for i, s in enumerate(segs) if len(s[0]) > 1]
        if shrinkable:
            i = max(shrinkable, key=lambda i: (segs[i][1], i))
            segs[i][0].pop(0)
        elif len(segs) > 1:
            i = max(range(len(segs)), key=lambda i: (segs[i][1], i))
            segs.pop(i)
        else:
            break
    return line() if segs else ""


# ── Local data sources ──────────────────────────────────
def claude_json_path():
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) / ".claude.json" if base else Path.home() / ".claude.json"


def codex_sessions_dir():
    base = os.environ.get("CODEX_HOME")
    return (Path(base) if base else Path.home() / ".codex") / "sessions"


def load_claude_json():
    """Claude Code's own config/cache file. Only a few non-secret keys are used."""
    try:
        with open(claude_json_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def plan_tier(cfg):
    acct = cfg.get("oauthAccount") or {}
    org = acct.get("organizationType") or ""
    tier = acct.get("organizationRateLimitTier") or acct.get("userRateLimitTier") or ""
    if not org:
        return ""
    name = org.replace("claude_", "").replace("_", " ").title()
    mult = re.search(r"(\d+x)$", tier)
    return f"{name} {mult.group(1)}" if mult else name


def scoped_weekly_limits(cfg, now):
    """Per-model weekly limits (e.g. Fable) from Claude Code's usage cache.

    The cache is refreshed by Claude Code itself (e.g. when /usage is opened),
    so it can be hours old; the age is returned so the caller can show it.
    """
    cache = cfg.get("cachedUsageUtilization") or {}
    acct = get(cfg, "oauthAccount", "accountUuid")
    if acct and cache.get("accountUuid") and cache["accountUuid"] != acct:
        return []
    fetched = (cache.get("fetchedAtMs") or 0) / 1000
    out = []
    for lim in get(cache, "utilization", "limits") or []:
        if not isinstance(lim, dict) or lim.get("kind") != "weekly_scoped":
            continue
        name = get(lim, "scope", "model", "display_name")
        pct = lim.get("percent")
        if not name or pct is None:
            continue
        resets = iso_to_epoch(lim.get("resets_at"))
        if resets and resets <= now:
            continue    # the window has rolled over; the cached number is void
        out.append({"name": name, "pct": float(pct), "resets": resets,
                    "age": now - fetched if fetched else None})
    return out


def git_info(cwd):
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "--no-optional-locks", "status",
             "--porcelain=v2", "--branch"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=1.5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    branch, dirty = None, 0
    for ln in r.stdout.splitlines():
        if ln.startswith("# branch.head "):
            branch = ln[len("# branch.head "):]
        elif ln and not ln.startswith("#"):
            dirty += 1
    return branch, dirty


# ── Line 1 ──────────────────────────────────────────────
def model_name(data):
    mid = get(data, "model", "id") or ""
    m = re.match(r"(?:claude-)?([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?(\[1m\])?$", mid)
    if m:
        name = m.group(1).capitalize() + " " + m.group(2)
        if m.group(3):
            name += "." + m.group(3)
        if m.group(4):
            name += " 1M"
        return name
    return get(data, "model", "display_name") or "?"


def seg_model(data, tier):
    s = f"{C_MODEL}{model_name(data)}{RESET}"
    effort = get(data, "effort", "level")
    if effort:
        s += DOT + str(effort)
    if data.get("fast_mode"):
        s += f" {C_WARN}fast{RESET}"
    full = s + (DOT + f"{C_TIER}{tier}{RESET}" if tier else "")
    return [full, s]


def seg_dir(data):
    cwd = get(data, "workspace", "current_dir") or data.get("cwd") or ""
    if not cwd:
        return []
    name = os.path.basename(cwd.rstrip("\\/")) or cwd
    s = f"{C_DIR}{name}{RESET}"
    gi = git_info(cwd)
    if gi and gi[0]:
        branch, dirty = gi
        s += f" {C_GIT}({branch}{'*' if dirty else ''}){RESET}"
    wt = get(data, "worktree", "name") or get(data, "workspace", "git_worktree")
    return [s + f" {C_DIM}[{T['worktree']} {wt}]{RESET}", s] if wt else [s]


def bar(p, n=10):
    filled = max(0, min(n, round(p / 100 * n)))
    return "▰" * filled + "▱" * (n - filled)


def seg_context(data):
    p = get(data, "context_window", "used_percentage")
    if p is None:
        return []
    p = float(p)
    c = pct_color(p)
    return [f"{T['ctx']} {c}{bar(p)}{RESET} {c}{p:.0f}%{RESET}",
            f"{T['ctx']} {c}{p:.0f}%{RESET}"]


# ── Line 2 ──────────────────────────────────────────────
def seg_limit(data, key, label, with_day):
    p = get(data, "rate_limits", key, "used_percentage")
    if p is None:
        return []
    p = float(p)
    s = f"{label} {pct_color(p)}{p:.0f}%{RESET}"
    reset = get(data, "rate_limits", key, "resets_at")
    if reset:
        return [s + f" {C_DIM}{T['reset'].format(fmt_reset(float(reset), with_day))}{RESET}", s]
    return [s]


def seg_scoped(lim):
    s = f"{lim['name']} {T['week']} {pct_color(lim['pct'])}{lim['pct']:.0f}%{RESET}"
    age = lim["age"]
    if age is not None and age > USAGE_CACHE_AGE_SHOW_S:
        # Never drop the age tag: without it a day-old number looks live.
        short = fmt_dur(age) if age < 3600 else T["hours"].format(int(age // 3600))
        return [s + f" {C_DIM}({T['ago'].format(short)}){RESET}"]
    return [s]


def seg_cost(data):
    cost = get(data, "cost", "total_cost_usd")
    dur = get(data, "cost", "total_duration_ms")
    parts = []
    if cost is not None:
        parts.append(f"${float(cost):.2f}")
    if dur is not None:
        parts.append(fmt_dur(float(dur) / 1000))
    return [DOT.join(parts)] if parts else []


def seg_lines(data):
    a = get(data, "cost", "total_lines_added")
    r = get(data, "cost", "total_lines_removed")
    if not a and not r:
        return []
    return [f"{C_OK}+{a or 0}{RESET} {C_BAD}-{r or 0}{RESET}"]


# ── Codex rows ──────────────────────────────────────────
RE_CWD = re.compile(rb'"cwd":"((?:[^"\\]|\\.)*)"')
RE_TS = re.compile(rb'"timestamp":"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)')
RE_FNAME = re.compile(r"rollout-(\d{4})-(\d\d)-(\d\d)T(\d\d)-(\d\d)-(\d\d)-")


def codex_runs(now):
    root = codex_sessions_dir()
    files = []
    for t in {time.localtime(now), time.localtime(now - 86400)}:
        d = root / f"{t.tm_year:04d}" / f"{t.tm_mon:02d}" / f"{t.tm_mday:02d}"
        try:
            files.extend(d.glob("rollout-*.jsonl"))
        except Exception:
            pass
    runs = []
    for f in files:
        m = RE_FNAME.match(f.name)
        if not m:
            continue
        started = time.mktime(tuple(int(x) for x in m.groups()) + (0, 0, -1))
        if now - started > CODEX_LOOKBACK_S:
            continue
        try:
            with open(f, "rb") as fh:
                if f.stat().st_size <= 20 * 1024 * 1024:
                    data = fh.read()
                else:
                    head = fh.read(64 * 1024)
                    fh.seek(-512 * 1024, 2)
                    data = head + fh.read()
        except Exception:
            continue
        i_start = data.rfind(b'"type":"task_started"')
        i_done = data.rfind(b'"type":"task_complete"')
        ts = RE_TS.findall(data[-8192:])
        last = iso_to_epoch(ts[-1].decode()) if ts else started
        running = i_start > i_done
        idle = now - last
        if running and idle > CODEX_STALE_HIDE_S:
            continue
        if not running and idle > CODEX_DONE_SHOW_S:
            continue
        cwd = ""
        cm = RE_CWD.search(data[:65536])
        if cm:
            try:
                cwd = json.loads(b'"' + cm.group(1) + b'"')
            except Exception:
                pass
        runs.append({
            "project": os.path.basename(cwd.rstrip("\\/")) or "?",
            "review": b"$independent-review" in data[:512 * 1024],
            "running": running,
            "stale": running and idle > CODEX_STALE_S,
            "started": started,
            "last": last,
            "idle": idle,
            "steps": data.count(b'"type":"CommandExecution"', max(i_start, 0)),
        })
    runs.sort(key=lambda r: (not r["running"], -r["started"]))
    return runs[:CODEX_MAX_ROWS]


def codex_line(r, now):
    job = T["review"] if r["review"] else T["job"]
    if r["stale"]:
        head = f"{C_BAD}⚠ Codex{RESET}"
        state = f"{C_BAD}{T['stale'].format(t=fmt_dur(r['idle']))}{RESET}"
    elif r["running"]:
        head = f"{C_CODEX}◐ Codex{RESET}"
        state = T["running"].format(job=job, t=fmt_dur(now - r["started"]))
    else:
        head = f"{C_OK}✓ Codex{RESET}"
        state = T["done"].format(job=job, t=fmt_dur(r["last"] - r["started"]))
    return DOT.join([head, f"{C_DIR}{r['project']}{RESET}", state,
                     f"{C_DIM}{T['cmds'].format(r['steps'])}{RESET}"])


# ── Main ────────────────────────────────────────────────
def render(data, cols, now, codex=True):
    cfg = load_claude_json()
    lines = [fit([
        (seg_model(data, plan_tier(cfg)), 0),
        (seg_dir(data), 2),
        (seg_context(data), 1),
    ], cols)]

    segs2 = [
        (seg_limit(data, "five_hour", T["h5"], False), 1),
        (seg_limit(data, "seven_day", T["d7"], True), 2),
    ]
    segs2 += [(seg_scoped(lim), 3) for lim in scoped_weekly_limits(cfg, now)]
    segs2 += [(seg_cost(data), 4), (seg_lines(data), 5)]
    line2 = fit(segs2, cols)
    if line2:
        lines.append(line2)

    if codex:
        try:
            lines += [codex_line(r, now) for r in codex_runs(now)]
        except Exception:
            pass
    return "\n".join(ln for ln in lines if ln)


def main(argv=None):
    global T
    ap = argparse.ArgumentParser(description="Claude Code status line")
    ap.add_argument("--lang", choices=sorted(STRINGS), default="en")
    ap.add_argument("--no-codex", action="store_true", help="hide Codex rows")
    args = ap.parse_args(argv)
    T = STRINGS[args.lang]
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
        try:
            cols = int(os.environ.get("COLUMNS") or 0) or 120
        except ValueError:
            cols = 120
        print(render(data, max(cols - 4, 30), time.time(), codex=not args.no_codex))
    except Exception:
        # Last line of defence: a non-zero exit or empty output blanks the status line.
        print(T["error"])


if __name__ == "__main__":
    main()
