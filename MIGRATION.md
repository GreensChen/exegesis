# Exegesis Migration Checklist

從 Anamnesis 拆出 Weekly Paper 模組成 Exegesis 獨立 repo。
姊妹 prompt：`anamnesis-cleanup-prompt.md`、`pharos-verification-prompt.md`。

---

## ✅ 已完成（本次 migration）

### 程式碼遷移

- [x] 10 個檔案從 Anamnesis copy 進來：
  - `weekly_paper.py` → `exegesis.py`（CLI 主入口，rename）
  - `init_weekly_paper.py` → `init_exegesis.py`（rename）
  - `paper_discovery.py` / `paper_curator.py` / `paper_reader.py` / `paper_writer.py` /
    `paper_translator.py` / `moc_updater.py`（內容不動）
  - `digest_to_kepub.py` / `patch_kepub_css.py`（內容不動）
- [x] `from weekly_paper_writer` → `from vault_writer`（14 處 import）
- [x] 環境變數重新命名：
  - `OBSIDIAN_BOT_TOKEN` → `EXEGESIS_BOT_TOKEN`
  - `WEEKLY_PAPER_DAYS_BACK` → `EXEGESIS_DAYS_BACK`
  - `WEEKLY_PAPER_CANDIDATES_LIMIT` → `EXEGESIS_CANDIDATES_LIMIT`
  - `WEEKLY_PAPER_DOW` → `EXEGESIS_DOW`（bot 端）
  - `WEEKLY_PAPER_HOUR` → `EXEGESIS_HOUR`（bot 端）
- [x] Entry point 自我參照（filename / CLI description / logger name）更新

### 獨立 Bot

- [x] `exegesis_bot.py` 新建（從 obsidian_bot.py 拆出 weekly + paper handlers）
- [x] Callback namespace `wk:` → `ex:`（避免跟 Anamnesis bot 衝突）
- [x] BotCommand 註冊：`brief` / `topics` / `upgrade` / `paper` / `start` / `help`
- [x] `cmd_weekly` → `cmd_brief`，`weekly_brief_loop` → `exegesis_brief_loop`
- [x] 保留 `cmd_paper` + `_push_paper_search_results`（主動查詢 paper 功能）

### 跨 Repo Drift 偵測

- [x] `anamnesis_drift.py` 新增，監控 16 個 Anamnesis API 簽名
  - interest_model: record_pharos_signal / record_user_query_signal /
    load_interest_model / save_interest_model / refresh_interest_model /
    record_direction_selection / ensure_edge_zones / bootstrap_interest_model
  - vocabulary_manager: apply_tags_to_capture
  - vault_writer: write_to_vault / read_from_vault / list_vault_folder /
    read_state_json / write_state_json / read_vault_dotfile / write_vault_dotfile
- [x] `init_exegesis.py` 加 `--refresh-anamnesis-hash` flag
- [x] `init_exegesis.py` bootstrap 結尾自動跑 `_try_refresh_anamnesis_hash()`
- [x] `exegesis_bot.py` 啟動時 `_check_drift_on_startup` 跑 Pharos + Anamnesis 兩個漂移檢查
- [x] Pharos EPUB_CSS drift 機制完全保留（`digest_to_kepub._verify_pharos_drift`）

### 部署資產

- [x] `requirements.txt`（Exegesis 自己的依賴子集）
- [x] `.env.example`（含 EXEGESIS_* 參數說明）
- [x] `.gitignore`
- [x] `server/exegesis-bot.service`（systemd, 含 PYTHONPATH=/home/yt2epub/anamnesis）
- [x] `server/exegesis-brief.service`（oneshot fallback）
- [x] `server/exegesis-brief.timer`（fallback timer, 預設不 enable）
- [x] `server/install_services.sh`
- [x] `server/deploy.sh`
- [x] `README.md`（含姊妹專案說明 / Quick start / Telegram 指令 / drift 機制）

### 整合測試

- [x] 13 個 Python 模組 syntax 全 compile ✓
- [x] 跨 repo import 透過 PYTHONPATH 通（用 `weekly_paper_writer` symlink 為 `vault_writer` 模擬）
- [x] `anamnesis_drift._compute_current_signatures_hash` 跑出 16 API 的 hash
- [x] `init_exegesis.py --refresh-anamnesis-hash` 確認可寫入 hash
- [x] `verify_anamnesis_drift()` 在 hash 一致時 silent，不誤報

---

## ⏳ 待做（部署前）

### 1. Anamnesis 端 cleanup（姊妹 prompt）

**必須先跑**：`anamnesis-cleanup-prompt.md` 在 `../anamnesis/` 執行。
關鍵步驟：

- `git mv weekly_paper_writer.py vault_writer.py` ← Exegesis import 依賴這個 rename
- 移除 Anamnesis 已搬走的 10 個檔案
- `obsidian_bot.py` 移除 `cmd_weekly` / `cmd_topics` / `cmd_upgrade` / `cmd_paper` /
  `cb_weekly` / `weekly_brief_loop` / `_check_pharos_drift_on_startup` 等 handler
- 從 `requirements.txt` 移除 `arxiv` / `ebooklib` / `Pillow`（確認 Anamnesis 不再用）

Anamnesis cleanup 跑完後，本 repo 的 `from vault_writer import` 才會在 server 上解析得到。

### 2. Bot token 設定

- [ ] `@BotFather` 申請新 bot（建議名稱 `@ExegesisBot`）拿 token
- [ ] 把 token 寫入本 repo 的 `.env`（**注意：不是 .env.example**）：

  ```
  EXEGESIS_BOT_TOKEN=<token>
  TELEGRAM_CHAT_ID=<chat_id>
  ```

### 3. 本機驗證（順序）

```bash
# 假設 ../anamnesis 已跑完 cleanup（weekly_paper_writer.py 已 git mv 成 vault_writer.py）

cd ~/exegesis
cp .env.example .env  # 填入 token + Dropbox + Gemini key

# (1) 跨 repo import 通
PYTHONPATH=../anamnesis python3 -c "import vault_writer, interest_model, vocabulary_manager; print('OK')"

# (2) Bootstrap dry-run（不寫檔）
PYTHONPATH=../anamnesis python3 init_exegesis.py --dry-run

# (3) 正式 bootstrap（會在 vault 建 placeholder + 算 hash）
PYTHONPATH=../anamnesis python3 init_exegesis.py

# (4) Bot 啟動測試
PYTHONPATH=../anamnesis python3 exegesis_bot.py
# 在 Telegram 試 /start /topics /brief
```

### 4. Server 部署

```bash
# Mac 端：
export SERVER_IP=<vps_ip>
./server/deploy.sh

# Server 端：
ssh root@$SERVER_IP
cd /home/yt2epub/exegesis
# 把本機 .env 內容貼到 server 的 .env
nano .env
chmod 600 .env

cd server
./install_services.sh

systemctl status exegesis-bot.service
journalctl -u exegesis-bot.service -f
```

### 5. Pharos 端驗證（姊妹 prompt）

跑 `pharos-verification-prompt.md`，確認：

- Pharos 沒有用到 `weekly_paper_writer` 或 `vault_writer`
- `record_pharos_signal` 呼叫處仍正確
- Pharos systemd service 的 PYTHONPATH 設定正確

---

## 環境變數對照表

| 舊（Anamnesis）| 新（Exegesis）|
|----------------|---------------|
| `OBSIDIAN_BOT_TOKEN` | `EXEGESIS_BOT_TOKEN` |
| `WEEKLY_PAPER_DOW` | `EXEGESIS_DOW` |
| `WEEKLY_PAPER_HOUR` | `EXEGESIS_HOUR` |
| `WEEKLY_PAPER_DAYS_BACK` | `EXEGESIS_DAYS_BACK` |
| `WEEKLY_PAPER_CANDIDATES_LIMIT` | `EXEGESIS_CANDIDATES_LIMIT` |
| `TELEGRAM_CHAT_ID` | `TELEGRAM_CHAT_ID`（不變，同一個 chat）|
| `GEMINI_API_KEY` | `GEMINI_API_KEY`（不變）|
| `DROPBOX_APP_KEY` / `_SECRET` / `_REFRESH_TOKEN` | 不變 |
| `SEMANTIC_SCHOLAR_API_KEY` | 不變 |
| `PHAROS_REPO_PATH` | 不變 |
| `DROPBOX_KOBO_LOCAL_PATH` | 不變 |

---

## Callback 命名空間對照

| 舊（Anamnesis bot）| 新（Exegesis bot）|
|---------------------|---------------------|
| `wk:dir:<id>` | `ex:dir:<id>` |
| `wk:upgrade:<arxiv>` | `ex:upgrade:<arxiv>` |
| `wk:skip:_` | `ex:skip:_` |
| `wk:reroll:_` | `ex:reroll:_` |
| `wk:noop:_` | `ex:noop:_` |

舊 callback data 若存於舊訊息中，Exegesis bot 不會接（兩個 bot token 不同，不會收到對方訊息）。

---

## PYTHONPATH 設定

- Mac local：`PYTHONPATH=../anamnesis python3 ...`
- Server：`server/exegesis-bot.service` 已內含 `Environment="PYTHONPATH=/home/yt2epub/anamnesis"`
- Pharos：對應 Pharos 自己的 systemd service（驗證見 pharos-verification-prompt.md）

---

## 已知限制

- 本次 migration 不處理 Dropbox 上的 `/WeeklyPaperState/` 目錄改名（Exegesis 暫時繼續用同一個 state 目錄）。等 Exegesis server 跑通後可選擇手動搬到 `/ExegesisState/`。
- `3 MOCs/Weekly Papers MOC.md` 檔名保留（Anamnesis cleanup prompt 也說不主動改）。
- Anamnesis cleanup 之前，本 repo 的 import 會失敗 — 這是預期行為。
