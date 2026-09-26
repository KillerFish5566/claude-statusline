# claude-statusline

A status line for [Claude Code](https://code.claude.com/docs/en/statusline) that shows your model, plan, and usage limits, and also shows [Codex](https://github.com/openai/codex) jobs running in the background.

[繁體中文說明](README.zh-TW.md)

```
Opus 5.5 · high · Max 5x │ my-project (main*) │ ctx ▰▰▰▱▱▱▱▱▱▱ 34%
5h 23% resets 23:00 │ 7d 81% resets Tue 10:46 │ Fable wk 0% (5h ago) │ $1.23 · 42m │ +120 -30
◐ Codex · my-project · review running 2m · 3 cmds
✓ Codex · other-repo · review done in 5m · 1 cmds
```

- One Python file, standard library only, no network access.
- Runs in about 0.1 s. Handles missing fields and bad input, and always prints something.
- Fits the terminal width. When space runs out it first shortens segments, then drops the least important ones.
- English and Traditional Chinese labels (`--lang en` / `--lang zh-TW`).

## What each part means

| Segment | Source | Notes |
|---|---|---|
| Model · effort | stdin `model.id`, `effort.level` | `fast` appears when fast mode is on |
| Plan tier (`Max 5x`, `Pro`, …) | `oauthAccount` in Claude Code's `.claude.json` | Not provided on stdin |
| Folder (branch) | stdin `workspace.current_dir` + `git status` | `*` = uncommitted changes |
| `ctx` bar | stdin `context_window.used_percentage` | Green below 50 %, yellow below 80 %, red at 80 % and above |
| `5h` / `7d` | stdin `rate_limits` | Only for Pro/Max subscribers, after the first reply |
| `Fable wk` (per-model weekly) | `cachedUsageUtilization` in `.claude.json` | See the caveat below |
| `$` · duration · `+/-` | stdin `cost.*` | The cost is an estimate at list price, not your bill |
| Codex rows | `~/.codex/sessions/**/rollout-*.jsonl` | Running (◐), finished within 3 min (✓), no activity for 15 min (⚠) |

### Caveat: per-model weekly limits

Claude Code does not send per-model limits (for example the separate Fable weekly limit on Max plans) to status line scripts. This script reads the copy that Claude Code caches in `.claude.json` instead. That cache is refreshed only when Claude Code itself fetches usage, for example when you open `/usage`, so it can be hours old. The age is always shown, e.g. `(5h ago)`. The segment disappears once the weekly window has reset.

The script deliberately does **not** read your login token to query usage live.

### Caveat: undocumented formats

The `.claude.json` usage cache and the Codex session log format are internal and may change without notice. If they change, the affected segment disappears and the rest keeps working.

## Requirements

- Claude Code (tested with 2.1.283)
- Python 3.8 or newer
- Optional: `git` on `PATH`, and the Codex CLI

## Install

1. Put `statusline.py` somewhere permanent, or clone this repo.
2. Add this to `~/.claude/settings.json`:

   ```json
   "statusLine": {
     "type": "command",
     "command": "python /absolute/path/to/statusline.py --lang en",
     "padding": 0,
     "refreshInterval": 5
   }
   ```

   - **Windows:** use forward slashes in paths, and a full path to `python.exe` if `python` opens the Microsoft Store.
   - **`refreshInterval`** keeps the Codex rows and timers updating while Claude is waiting in the background. Remove it if you don't use Codex.
3. The status line shows up on the next message; no restart needed.

### Options

| Flag / variable | Effect |
|---|---|
| `--lang en\|zh-TW` | Label language (default `en`) |
| `--no-codex` | Never show Codex rows |
| `CLAUDE_CONFIG_DIR` | Read `.claude.json` from this folder instead of your home folder (same variable Claude Code uses) |
| `CODEX_HOME` | Read Codex sessions from `$CODEX_HOME/sessions` instead of `~/.codex/sessions` |

A Codex job counts as a **review** when its prompt contains `$independent-review`, the name of a Codex review skill. Any other job shows as a **task**.

## Privacy

The script only reads files:

- the JSON Claude Code sends it
- `.claude.json`: just `oauthAccount.organizationType`, `organizationRateLimitTier`, `accountUuid`, and `cachedUsageUtilization`
- Codex session logs from the last day: only the working folder, event types, and timestamps

It never reads credential files, writes nothing, and makes no network calls. Your email address and account IDs are never printed.

## Development

```
python -m unittest discover -s tests
```

The tests build fake `.claude.json` and Codex logs in a temporary folder. They never read your real data.

## License

[MIT](LICENSE)
