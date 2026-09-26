# claude-statusline

給 [Claude Code](https://code.claude.com/docs/en/statusline) 用的狀態列：顯示模型、方案、用量上限，同時看得到背景跑的 [Codex](https://github.com/openai/codex) 工作。

[English](README.md)

```
Opus 5.5 · high · Max 5x │ my-project (main*) │ 記憶 ▰▰▰▱▱▱▱▱▱▱ 34%
5小時 23% 23:00重置 │ 7天 81% 週二 10:46重置 │ Fable 週 0% (5小時前) │ $1.23 · 42分 │ +120 -30
◐ Codex · my-project · 審查中 2分 · 指令 3 次
✓ Codex · other-repo · 審查完成 用時 5分 · 指令 1 次
```

## 各段意思

| 段落 | 來源 | 說明 |
|---|---|---|
| 模型・推理檔位 | Claude Code 傳入的資料 | 開快速模式時多一個 `fast` |
| 方案（`Max 5x`、`Pro`…） | Claude Code 的 `.claude.json` | 傳入的資料沒有這項 |
| 資料夾（分支） | 目前資料夾＋`git status` | `*` 表示有還沒提交的修改 |
| 記憶 | 對話記憶用量 | 未滿 50% 綠、未滿 80% 黃、80% 以上紅 |
| 5小時／7天 | 用量上限 | 只有 Pro／Max 訂閱才有，第一次回覆後才出現 |
| Fable 週 | `.claude.json` 的用量快取 | 見下方注意事項 |
| 花費・時長・增刪行 | 本次對話 | 花費是按定價估算，不是實際帳單 |
| Codex 行 | `~/.codex/sessions` 的執行紀錄 | ◐ 跑中／✓ 3 分鐘內完成／⚠ 15 分鐘沒動靜 |

**注意：Fable 的數字可能是舊的。**
- Claude Code 不會把各模型的週用量傳給狀態列，這裡改讀它自己存在 `.claude.json` 的快取。
- 那份快取只在 Claude Code 自己查用量時更新（例如打開 `/usage`），所以可能是幾小時前的資料。
- 旁邊一定會標出資料有多舊；週額度重置後這段會自動消失。
- 本腳本**刻意不讀**登入憑證去連網查即時數字。

**注意：部分格式沒有官方文件。** 用量快取和 Codex 紀錄檔都是內部格式，隨時可能改版。改版時只有那一段會消失，其他部分照常顯示。

## 安裝

需求：Claude Code、Python 3.8 以上；`git` 和 Codex 是選用的。

在 `~/.claude/settings.json` 加上下面這段：

```json
"statusLine": {
  "type": "command",
  "command": "python C:/完整/路徑/statusline.py --lang zh-TW",
  "padding": 0,
  "refreshInterval": 5
}
```

- Windows 路徑請用正斜線 `/`。
- 如果打 `python` 會跳出 Microsoft Store，就改填 `python.exe` 的完整路徑。
- `refreshInterval` 讓 Claude 在背景等 Codex 時，畫面也會繼續更新。

其他選項（`--no-codex`、`CLAUDE_CONFIG_DIR`、`CODEX_HOME`）與隱私說明，見[英文版](README.md)。

## 授權

[MIT](LICENSE)
