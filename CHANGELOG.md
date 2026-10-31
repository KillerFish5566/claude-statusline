# Changelog

## Unreleased

- Plan names follow Anthropic's 2026-10-30 rename: Max 5x shows as **Max**, Max 20x as **Ultra**. Reads `planDisplayName` from `.claude.json` when Claude Code provides it.
- `--mascot`: Clawd, 3 rows tall, at the right end of the status line (off by default). Its face follows the session data: tense (5-hour or 7-day usage at 80 %+), sweating (context at 80 %+), proud (300+ changed lines or $20+ session cost), normal otherwise.

## 0.1.0 — 2026-09-26

First release.

- Line 1: model, reasoning effort, plan tier, folder with git branch and dirty mark, context usage bar.
- Line 2: 5-hour and 7-day limits with reset times, per-model weekly limits from Claude Code's usage cache (always shown with the cache's age), session cost, duration, lines changed.
- Codex rows: running, recently finished, and stalled jobs from Codex session logs.
- Width-aware layout that shortens segments before dropping them.
- `--lang en|zh-TW`, `--no-codex`, `CLAUDE_CONFIG_DIR`, `CODEX_HOME`.
