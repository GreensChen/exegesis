# Exegesis

> **Exegesis** — ἐξήγησις, deep commentary.
> AI 每週對你選定的學術領域做深度導讀，產出可在 Kobo 上慢讀的中文長文。

每週六晚上，bot 從 arXiv / Semantic Scholar 抓近兩週相關 paper、curate 出
3 個閱讀方向（核心 / 另一角度 / 邊緣進取），你按一鍵選定方向後，
Gemini 寫出 6000-8000 字中文 digest（含原文 paper cards、跨領域連結），
轉成 kepub 推到 Kobo 排程同步資料夾。

---

## 姊妹專案

Exegesis 不是孤立 product — 跟以下兩個 repo 共組一個 reading stack：

- **[Anamnesis](https://github.com/GreensChen/anamnesis)** — Kobo highlights + Obsidian vault
  + Telegram bot 的閱讀記憶系統。Exegesis 透過 PYTHONPATH 跨 repo
  import `interest_model.py`（興趣模型）、`vault_writer.py`（vault 讀寫）、
  `vocabulary_manager.py`（tag 套用）、`dropbox_uploader.py`（Dropbox 客戶端）。
- **[Pharos](https://github.com/GreensChen/yt2epub)** — YouTube 訪談轉繁中 kepub 推到 Kobo。
  Pharos 訊號透過 `interest_model.record_pharos_signal()` 回流到 Anamnesis 興趣模型，
  影響 Exegesis 的 paper 推薦排序。

```
Pharos (YT → kepub) ─┐
                     ├──► Anamnesis interest_model ──► Exegesis paper discovery
Anamnesis highlights ┘                                       │
                                                             ▼
                                            Exegesis digest + cards + kepub
                                                       (寫進 Anamnesis vault)
```

---

## Quick start

### Mac local（試跑 + dry-run）

```bash
git clone https://github.com/GreensChen/exegesis.git
cd exegesis

# 同層放 anamnesis（提供跨 repo dependency）
git clone https://github.com/GreensChen/anamnesis.git ../anamnesis

pip3 install -r requirements.txt

cp .env.example .env
# 填入 EXEGESIS_BOT_TOKEN（@BotFather 申請）/ TELEGRAM_CHAT_ID /
# GEMINI_API_KEY / DROPBOX_* / SEMANTIC_SCHOLAR_API_KEY

# Bootstrap 興趣模型 + Pharos hash + Anamnesis API hash
PYTHONPATH=../anamnesis python3 init_exegesis.py --dry-run
PYTHONPATH=../anamnesis python3 init_exegesis.py

# 啟動 bot
PYTHONPATH=../anamnesis python3 exegesis_bot.py
```

### Server（Hetzner VPS）

跟 Anamnesis 同台部署，藉由 `PYTHONPATH=/home/yt2epub/anamnesis` 共享資料層：

```bash
# 1. Mac 端 push 程式碼
export SERVER_IP=1.2.3.4
./server/deploy.sh

# 2. Server 端首次安裝
ssh root@$SERVER_IP
cd /home/yt2epub/exegesis/server
./install_services.sh

# 3. 確認 service
systemctl status exegesis-bot.service
journalctl -u exegesis-bot.service -f
```

---

## Telegram 指令

| 指令 | 用途 |
|------|------|
| `/brief` | 立刻產生本週導讀（不等到週六）|
| `/topics` | 主題輪替狀態 |
| `/paper <關鍵字>` | 主動查詢 5 篇相關 paper（會餵回興趣模型）|
| `/upgrade <arxiv_id>` | 升級過去某週的 paper 為全文中譯 |
| `/start` / `/help` | 用法說明 |

每週六 21:00 (Asia/Taipei) 自動推送主題選擇（透過 bot in-process loop）。

---

## 檔案結構

```
exegesis/
├── exegesis.py              # CLI 主入口（prepare / generate / upgrade / refresh-interest）
├── exegesis_bot.py          # Telegram bot
├── init_exegesis.py         # 首次部署 bootstrap
│
├── paper_discovery.py       # arXiv + Semantic Scholar 抓 paper
├── paper_curator.py         # Gemini curate 出 3 個閱讀方向
├── paper_reader.py          # PDF → 摘要 / 核心發現
├── paper_writer.py          # 寫 digest 長文 + paper card
├── paper_translator.py      # /upgrade 全文中譯
├── moc_updater.py           # MOC 更新
│
├── digest_to_kepub.py       # Digest .md → .kepub.epub（含 Pharos drift 偵測）
├── patch_kepub_css.py       # 已上 Kobo 的書批次 patch CSS
│
├── anamnesis_drift.py       # 監控 Anamnesis 跨 repo API 簽名漂移
│
├── requirements.txt
├── .env.example
└── server/
    ├── exegesis-bot.service
    ├── exegesis-brief.service     # oneshot fallback
    ├── exegesis-brief.timer       # MVP 不啟用
    ├── install_services.sh
    └── deploy.sh
```

跨 repo import（透過 `PYTHONPATH=/home/yt2epub/anamnesis`）：

- `interest_model` — 興趣模型黑箱
- `vault_writer` — vault 讀寫 + state JSON（Anamnesis 端原 `weekly_paper_writer.py` 改名）
- `vocabulary_manager` — paper card / digest 的 tag 套用
- `dropbox_uploader` — Dropbox 客戶端

---

## 漂移偵測（cross-repo drift）

Exegesis 對兩個 source-of-truth 做啟動時自動偵測：

1. **Pharos EPUB_CSS hash**（`digest_to_kepub._verify_pharos_drift`）
   — Digest kepub 的排版要跟訪談 epub 一致；Pharos 端改了 CSS 但 Exegesis
   沒跟上會視覺漂移。修復：`python3 init_exegesis.py --refresh-pharos-hash`

2. **Anamnesis API 簽名 hash**（`anamnesis_drift.verify_anamnesis_drift`）
   — 監控 `interest_model` / `vault_writer` / `vocabulary_manager` 的函式簽名。
   Anamnesis 端改 API 但 Exegesis 沒跟上會 ImportError 或 TypeError。
   修復：`python3 init_exegesis.py --refresh-anamnesis-hash`

兩個偵測都是降級（log + Telegram 警告，不 raise），保證 bot 不會因為
姊妹 repo 改動而當掉。

---

## 成本

每週運行一次完整 pipeline 約消耗：
- Gemini（discovery 評分 / curation / digest 寫作 / paper card）≈ 200-300k tokens
- Semantic Scholar / arXiv API 免費

每月約 **$1-1.5 USD**（以 gemini-3-flash-preview 計）。

`/paper <query>` 每次查詢約 5-10k tokens（候選評分 + tag 生成），輕量。

---

## License

MIT。跟 Pharos / Anamnesis 一致。
