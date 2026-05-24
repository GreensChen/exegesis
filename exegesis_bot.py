#!/usr/bin/env python3
"""exegesis_bot.py — Exegesis Telegram bot（獨立於 Anamnesis 的 obsidian_bot）

職責：
- /brief：立刻產生本週導讀（prepare 階段）
- /topics：顯示主題輪替狀態
- /translation:翻譯指定 paper 為全文中文
- /paper：主動查詢 3-5 篇相關 paper
- 每週 EXEGESIS_DOW / EXEGESIS_HOUR 點自動推送主題選擇

啟動：
    python3 exegesis_bot.py
（一般由 systemd 自動啟動）

需要 .env：
- EXEGESIS_BOT_TOKEN（去 @BotFather 建獨立 bot 拿 token）
- TELEGRAM_CHAT_ID（共用 Anamnesis 同一個 chat）
- Dropbox / Gemini 認證

跨 repo 依賴 Anamnesis 的 interest_model / vocabulary_manager / vault_writer / dropbox_uploader
（透過 PYTHONPATH 載入）。
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass


# ─────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "exegesis_bot.log"

EXEGESIS_DOW = int(os.environ.get("EXEGESIS_DOW", "5"))  # 0=Mon, 5=Sat
EXEGESIS_HOUR = int(os.environ.get("EXEGESIS_HOUR", "21"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
logger = logging.getLogger("exegesis_bot")


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ─────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start — 介紹 Exegesis bot。"""
    await update.message.reply_html(
        "📚 <b>Exegesis</b> — ἐξήγησις, deep commentary.\n\n"
        "AI 每週對你選定的學術領域做深度導讀，產出可在 Kobo 上慢讀的中文長文。\n\n"
        "<b>指令：</b>\n"
        "/brief — 立刻產生本週導讀（不等到週六）\n"
        "/topics — 主題輪替狀態\n"
        "/paper &lt;關鍵字&gt; — 主動查詢相關 paper(主題式)\n"
        "/author &lt;人名&gt; — 查指定作者的 paper(精準 by author_id)\n"
        "/translation &lt;arxiv_id&gt; — 翻譯為全文中文\n"
        "/help — 用法說明\n\n"
        f"每週 DOW={EXEGESIS_DOW} {EXEGESIS_HOUR}:00 自動推送論文導讀主題。"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_brief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/brief — 立刻跑階段 1：prepare + 推 Telegram 主題選項。"""
    chat_id = update.effective_chat.id
    msg = await update.message.reply_text("📚 開始抓本週 paper...")
    try:
        from exegesis import prepare_weekly
        result = await asyncio.to_thread(prepare_weekly)
    except Exception as e:
        logger.exception("brief prepare failed")
        await msg.edit_text(f"❌ 失敗：{e}")
        return
    await msg.delete()
    await _push_topic_directions(chat_id, context.bot, result)


async def cmd_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/topics — 顯示主題輪替狀態。"""
    try:
        from vault_writer import read_state_json
        cfg = await asyncio.to_thread(read_state_json, "config/topics.json")
    except Exception as e:
        await update.message.reply_text(f"❌ 讀取失敗：{e}")
        return
    lines = ["📚 <b>主題輪替狀態</b>", ""]
    for t in cfg.get("topics", []):
        emoji = "✅" if t.get("enabled") else "⏸"
        last = t.get("last_published_week") or "(從未)"
        lines.append(f"{emoji} <b>{html_escape(t['name_zh'])}</b> ({html_escape(t['code'])}) — 上次：{html_escape(last)}")
    await update.message.reply_html("\n".join(lines))


async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/translation <arxiv_id> — 翻譯某篇 paper 為全文中文。"""
    if not context.args:
        await update.message.reply_text(
            "用法：/translation 2405.12345\n\n"
            "💡 本週的 paper 可以直接按本週導讀完成訊息上的翻譯按鈕,不用記 arxiv_id。\n"
            "此指令用於翻譯過去某週的 paper(需要該 paper card 仍存在於 vault)。"
        )
        return
    arxiv_id = context.args[0].strip()
    msg = await update.message.reply_text(f"🌐 翻譯 {html_escape(arxiv_id)} 中（約 1-3 分鐘）...")
    try:
        from exegesis import upgrade_paper
        result = await asyncio.to_thread(upgrade_paper, arxiv_id)
    except FileNotFoundError as e:
        await msg.edit_text(
            f"❌ 找不到 paper card：\n<code>{html_escape(arxiv_id)}</code>\n\n"
            f"翻譯要求該 paper 已在某週 Exegesis digest 中出現過。\n"
            f"請確認 <code>1 Sources/Papers/</code> 下有對應檔案。",
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception as e:
        logger.exception("upgrade failed")
        await msg.edit_text(f"❌ 翻譯失敗：{e}")
        return
    kepub_line = "🎧 Paper kepub 已上傳 Kobo\n" if result.get("kepub_path") else ""
    await msg.edit_text(
        f"✅ 中文全譯版已存：\n"
        f"<code>1 Sources/Papers Translated/{html_escape(result['filename'])}</code>\n"
        f"{kepub_line}\n"
        f"原 Paper Card 已自動加上連結。",
        parse_mode=ParseMode.HTML,
    )


async def cmd_author(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/author <name> — 找指定作者的 paper,分 3 角度給你選。

    流程跟 /paper 對等,但:
    - 透過 OpenAlex /authors 查作者 → 列候選人(處理多人同名)
    - 你選定後抓「該作者作品」(精確 by author_id),不是全文搜尋
    - 之後流程跟 /paper 一樣:3 角度 → 選 → digest + kepub
    """
    if not context.args:
        await update.message.reply_text(
            "用法:/author <作者英文名>\n\n"
            "例:\n"
            "  /author Demis Hassabis\n"
            "  /author Yoshua Bengio\n"
            "  /author Geoffrey Hinton\n\n"
            "若有 typo,系統會用 Gemini 試著修正(僅限知名研究者)。\n"
            "搜尋後會列候選人(含機構 + 論文數 + 引用)讓你挑對的。"
        )
        return
    name = " ".join(context.args).strip()
    if len(name) > 100:
        await update.message.reply_text("名字過長(請 ≤ 100 字)")
        return

    chat_id = update.effective_chat.id
    progress = await update.message.reply_text(f"🔍 查作者「{html_escape(name)}」...")
    try:
        from exegesis import prepare_author_search
        result = await asyncio.to_thread(prepare_author_search, name)
        candidates = result["candidates"]

        if not candidates:
            corrected_hint = ""
            if result["was_corrected"]:
                corrected_hint = (
                    f"\n\n💡 你打了「{html_escape(result['query'])}」,我試了"
                    f"「{html_escape(result['corrected_query'])}」也沒結果。"
                )
            await progress.edit_text(
                f"😔 找不到作者「{html_escape(name)}」{corrected_hint}\n\n"
                "確認拼法,或用全名(First Last)再試一次。"
            )
            return

        await progress.delete()
        await _push_author_candidates(chat_id, context.bot, result)
    except Exception as e:
        logger.exception("cmd_author failed")
        await progress.edit_text(f"❌ 查詢失敗:{e}")


async def _push_author_candidates(chat_id: int, bot, result: dict):
    """推作者候選人清單給 user 選。"""
    query = result["query"]
    candidates = result["candidates"]
    was_corrected = result.get("was_corrected", False)
    corrected_query = result.get("corrected_query", query)

    lines = [f"👤 <b>找到 {len(candidates)} 個「{html_escape(query)}」候選人</b>"]
    if was_corrected:
        lines.append(
            f"<i>💡 偵測到 typo,自動改成「{html_escape(corrected_query)}」搜尋</i>"
        )
    lines.append("")
    for i, c in enumerate(candidates, 1):
        emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"][i - 1] if i <= 5 else f"{i}."
        inst = c.get("institution") or "—"
        lines.append(
            f"{emoji} <b>{html_escape(c['display_name'])}</b>"
        )
        lines.append(
            f"   {html_escape(inst)} · {c['works_count']} 篇 · "
            f"引用 {c['cited_by_count']:,}"
        )
        lines.append("")
    lines.append("選一個,會抓該作者全部 paper 分 3 個閱讀角度(經典 / 中堅 / 最新)。")

    buttons = []
    row = []
    for i, c in enumerate(candidates, 1):
        emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"][i - 1] if i <= 5 else str(i)
        row.append(InlineKeyboardButton(
            emoji, callback_data=f"ex:author:pick:{c['openalex_id']}"
        ))
    buttons.append(row)
    buttons.append([InlineKeyboardButton("⏭ 取消", callback_data="ex:author:skip:_")])
    kb = InlineKeyboardMarkup(buttons)

    await bot.send_message(
        chat_id, "\n".join(lines),
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )


def _looks_like_author_name(query: str) -> bool:
    """Heuristic:判斷 query 看起來像不像 First Last 人名。

    規則:2-4 個 token、每個 token 首字母大寫、只含字母 / - / . / '。
    例:
      "Demis Hassabis" → True
      "Yann LeCun" → True
      "Andrej Karpathy" → True
      "Hinton" → False(單字,得用 /author Hinton 強制)
      "LaMDA" → False(單字)
      "transformer attention" → False(小寫)
      "Attention Is All You Need" → True(false positive,但 author 搜尋會 0
         結果再 fallback 到 topic 搜尋,沒實際影響)
    """
    q = (query or "").strip()
    words = q.split()
    if len(words) < 2 or len(words) > 4:
        return False
    for w in words:
        if not w or not w[0].isupper():
            return False
        if not all(c.isalpha() or c in "-.'" for c in w):
            return False
    return True


async def cmd_paper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/paper <query> — 主動搜尋並分成 3 個角度給你選哪個產 digest + kepub。

    智能路由:
    - 看起來像人名(2-4 字、首字大寫) → 先試 OpenAlex /authors
      - 找到候選人 → 走 author flow(show candidate list)
      - 0 結果 → fallback 到 topic 搜尋
    - 否則 → 直接 topic 搜尋
    - 強制 author 模式可用 /author <name>
    """
    if not context.args:
        await update.message.reply_text(
            "用法:/paper <關鍵字>\n\n"
            "主題搜尋例:\n"
            "  /paper LaMDA\n"
            "  /paper retrieval augmented generation\n"
            "  /paper chain of thought\n\n"
            "作者搜尋(自動偵測):\n"
            "  /paper Demis Hassabis\n"
            "  /paper Yoshua Bengio\n\n"
            "結果分 3 角度(🏛 經典 / 🌱 中堅 / 🆕 最新)給你選,"
            "選定後產出中文 digest + paper cards + Kobo kepub。\n\n"
            "若 query 是單字研究者姓氏(如 Hinton),用 /author Hinton 強制作者模式。"
        )
        return
    query = " ".join(context.args).strip()
    if len(query) > 200:
        await update.message.reply_text("查詢過長（請 ≤ 200 字）")
        return

    chat_id = update.effective_chat.id

    # 看起來像人名 → 先試 author 路由
    if _looks_like_author_name(query):
        progress = await update.message.reply_text(
            f"🔍 「{html_escape(query)}」看起來像作者名,先試 author 搜尋..."
        )
        try:
            from exegesis import prepare_author_search
            author_result = await asyncio.to_thread(prepare_author_search, query)
            if author_result["candidates"]:
                await progress.delete()
                await _push_author_candidates(chat_id, context.bot, author_result)
                return
            # 沒 author 候選人 → 訊息提示後 fallthrough 到 topic 搜尋
            await progress.edit_text(
                f"🔍 「{html_escape(query)}」不像已知作者,改用主題搜尋..."
            )
        except Exception as e:
            logger.warning(f"auto-author 嘗試失敗,fallthrough topic: {e}")
            await progress.edit_text(
                f"🔍 搜尋並分類「{html_escape(query)}」..."
            )
    else:
        progress = await update.message.reply_text(
            f"🔍 搜尋並分類「{html_escape(query)}」..."
        )

    try:
        from exegesis import prepare_search
        from paper_writer import write_paper_stub

        result = await asyncio.to_thread(prepare_search, query)
        directions = result["directions"]
        sc = result["source_counts"]
        candidates = result.get("candidates_count", 0)

        if not directions:
            # 沒角度分類得出 → 要嘛 0 篇候選、要嘛太少
            all_failed = (sc.get("arxiv", 0) == 0
                          and sc.get("openalex", 0) == 0
                          and sc.get("ss_match", 0) == 0)
            if candidates == 0 and all_failed:
                await progress.edit_text(
                    f"😔「{html_escape(query)}」沒找到論文\n\n"
                    "三個來源 (arXiv / OpenAlex / SS match) 都拿不到結果,"
                    "推測是網路問題或全部 API 暫時失效。等 1-2 分鐘後再試一次。"
                )
            elif candidates == 0:
                await progress.edit_text(
                    f"😔「{html_escape(query)}」沒找到論文\n\n"
                    f"來源: arXiv={sc.get('arxiv', 0)} / "
                    f"OpenAlex={sc.get('openalex', 0)} / SS match={sc.get('ss_match', 0)}。\n"
                    "查詢字串可能太冷僻,試別的關鍵字。"
                )
            else:
                await progress.edit_text(
                    f"😔「{html_escape(query)}」找到 {candidates} 篇但沒法分成 3 角度。\n"
                    "可能 paper 太少或都在同一時期。試更廣泛的查詢字串。"
                )
            return

        # 為 candidates 寫 paper stubs(讓 /translation 隨時找得到)
        # 只 stub 「會出現在 3 個 directions 裡」的 paper,避免汙染 vault
        used_ids = set()
        for d in directions:
            for pid in d.get("paper_ids", []):
                used_ids.add(pid)
        # 從 search_pending_choice 的 candidates 找出對應 meta
        from vault_writer import read_state_json
        pending = await asyncio.to_thread(read_state_json, "state/search_pending_choice.json")
        cand_map = {p["id"]: p for p in pending.get("candidates", [])}
        for pid in used_ids:
            meta = cand_map.get(pid)
            if not meta:
                continue
            try:
                await asyncio.to_thread(write_paper_stub, meta, query)
            except Exception as e:
                logger.warning(f"stub write failed for {meta.get('id')}: {e}")

        await progress.delete()
        await _push_search_directions(chat_id, context.bot, query, result)
    except Exception as e:
        logger.exception("cmd_paper failed")
        await progress.edit_text(f"❌ 搜尋失敗：{e}")


async def _push_paper_search_results(chat_id: int, bot, query: str, papers: list, source_counts: dict = None):
    """推搜尋結果清單 + 每篇 paper 一顆翻譯按鈕（複用 ex:upgrade callback）。"""
    header_lines = [f"🔍 <b>搜尋結果：{html_escape(query)}</b>"]
    # SS 全失敗時加診斷訊息（讓使用者知道結果只來自 arXiv）
    if source_counts:
        ss_total = source_counts.get("ss_search", 0) + source_counts.get("ss_match", 0)
        if ss_total == 0 and source_counts.get("arxiv", 0) > 0:
            header_lines.append(
                "<i>⚠️ Semantic Scholar 暫時拿不到（限流），以下只是 arXiv 結果，"
                "可能缺漏知名 paper（如 Google LaMDA）。1-2 分鐘後再試會比較完整。</i>"
            )
    header_lines.append("")
    lines = header_lines
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    buttons = []
    for i, p in enumerate(papers):
        emoji = emojis[i] if i < len(emojis) else f"{i+1}."
        title = p.get("title", "(untitled)")
        authors = p.get("authors", [])
        author_str = ", ".join(authors[:2]) + (" et al." if len(authors) > 2 else "")
        venue = p.get("venue") or "arXiv"
        year = (p.get("published_date") or "")[:4]
        cites = p.get("citation_count", 0)
        cites_str = f"📈 {cites:,}" if cites else ""
        meta_bits = [b for b in [author_str, venue, year, cites_str] if b]
        meta_line = " · ".join(meta_bits)

        abstract = p.get("abstract", "")
        snippet = ""
        if abstract:
            sent = abstract.split(". ")[0].strip()
            if len(sent) > 130:
                sent = sent[:128] + "…"
            snippet = sent

        lines.append(f"{emoji} <b>{html_escape(title)}</b>")
        if meta_line:
            lines.append(f"   <i>{html_escape(meta_line)}</i>")
        if snippet:
            lines.append(f"   {html_escape(snippet)}")
        lines.append("")

        arxiv_id = p.get("external_ids", {}).get("arxiv")
        if arxiv_id:
            buttons.append([
                InlineKeyboardButton(
                    f"{emoji} 翻譯全文",
                    callback_data=f"ex:upgrade:{arxiv_id}",
                )
            ])

    lines.append("💡 想深讀哪篇，按下方按鈕翻譯全文：")
    kb = InlineKeyboardMarkup(buttons) if buttons else None
    await bot.send_message(
        chat_id,
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def _push_topic_directions(chat_id: int, bot, prepare_result: dict):
    """推主題確認訊息（含 inline button）。"""
    iso_week = prepare_result["iso_week"]
    topic = prepare_result["topic"]
    directions = prepare_result["directions"]

    lines = [
        f"📚 <b>本週 {html_escape(iso_week)} · {html_escape(topic['name_zh'])}</b>",
        "",
    ]

    category_label = {
        "core": "🎯 核心",
        "alt_core": "🔀 另一角度",
        "edge": "🌐 邊緣進取",
    }

    for i, d in enumerate(directions, 1):
        emoji = ["1️⃣", "2️⃣", "3️⃣"][i - 1]
        cat = category_label.get(d.get("category"), "")
        diff_label = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}.get(
            d.get("estimated_difficulty", "medium"), "🟡"
        )
        edge_zone_label = f"(× {d['edge_zone']})" if d.get("category") == "edge" and d.get("edge_zone") else ""

        lines.append(f"{emoji} {cat} {edge_zone_label} <b>{html_escape(d['title'])}</b> {diff_label}")
        lines.append(f"   {html_escape(d['narrative'])}")
        lines.append(f"   收錄 {len(d['paper_ids'])} 篇")
        lines.append("")

    buttons = [
        [InlineKeyboardButton(["1️⃣", "2️⃣", "3️⃣"][i - 1], callback_data=f"ex:dir:{d['id']}") for i, d in enumerate(directions, 1)],
        [InlineKeyboardButton("⏭ 跳過本週", callback_data="ex:skip:_"),
         InlineKeyboardButton("🔄 換一批", callback_data="ex:reroll:_")],
    ]
    kb = InlineKeyboardMarkup(buttons)

    await bot.send_message(
        chat_id, "\n".join(lines),
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )


async def _push_search_directions(chat_id: int, bot, query: str, prepare_result: dict):
    """推 /paper 或 /author 的 3 個角度方向 + inline button 給 user 選。"""
    directions = prepare_result["directions"]
    candidates = prepare_result.get("candidates_count", 0)
    sc = prepare_result.get("source_counts", {})
    kind = prepare_result.get("kind", "search")

    if kind == "author":
        lines = [f"👤 <b>作者:{html_escape(query)}</b>"]
    else:
        lines = [f"🔍 <b>主動搜尋:{html_escape(query)}</b>"]
    # citation 來源:OpenAlex(主力)+ SS match(canonical 特技)
    cited_sources = sc.get("openalex", 0) + sc.get("ss_match", 0)
    if cited_sources == 0 and sc.get("arxiv", 0) > 0:
        lines.append(
            "<i>⚠️ OpenAlex 跟 SS match 都暫時拿不到 citation 訊號,"
            "角度分類只用 arXiv 結果(可能缺漏知名 paper)。1-2 分鐘後再試會比較完整。</i>"
        )
    lines.append(f"<i>共 {candidates} 篇候選,分 {len(directions)} 個角度給你選:</i>")
    lines.append("")

    category_label = {
        "canonical": "🏛 經典",
        "established": "🌱 中堅",
        "latest": "🆕 最新",
    }

    for i, d in enumerate(directions, 1):
        emoji = ["1️⃣", "2️⃣", "3️⃣"][i - 1] if i <= 3 else f"{i}."
        cat = category_label.get(d.get("category"), "")
        diff_label = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}.get(
            d.get("estimated_difficulty", "medium"), "🟡"
        )
        lines.append(f"{emoji} {cat} <b>{html_escape(d['title'])}</b> {diff_label}")
        lines.append(f"   {html_escape(d['narrative'])}")
        lines.append(f"   收錄 {len(d['paper_ids'])} 篇")
        lines.append("")

    lines.append("選一個角度按按鈕,會跑 5-10 分鐘生 digest + paper cards + kepub 推 Kobo。")

    button_rows = []
    direction_row = []
    for i, d in enumerate(directions, 1):
        emoji = ["1️⃣", "2️⃣", "3️⃣"][i - 1] if i <= 3 else str(i)
        direction_row.append(
            InlineKeyboardButton(emoji, callback_data=f"ex:search:dir:{d['id']}")
        )
    button_rows.append(direction_row)
    button_rows.append([InlineKeyboardButton("⏭ 取消", callback_data="ex:search:skip:_")])
    kb = InlineKeyboardMarkup(button_rows)

    await bot.send_message(
        chat_id, "\n".join(lines),
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

GEMINI_TRANSLATE_MODEL = "gemini-3-flash-preview"


def _translate_paper_metas_to_zh(papers: list[dict]) -> list[dict]:
    """把 paper 英文 title + abstract 第一句翻成簡短繁中。

    失敗 fallback 回 fallback dict（用截斷的英文 title）。
    回傳跟 input 同樣順序的 list[dict]，每筆 {"title_zh": str, "one_liner_zh": str}。
    """
    items = []
    for i, p in enumerate(papers, 1):
        title = (p.get("title") or "(untitled)").strip()
        abstract = (p.get("abstract") or "").strip()
        first_sent = ""
        if abstract:
            first_sent = abstract.split(". ")[0].strip()
            if len(first_sent) > 240:
                first_sent = first_sent[:240]
        items.append({"i": i, "title_en": title, "abstract_opening": first_sent})

    fallback = []
    for it in items:
        t = it["title_en"]
        if len(t) > 60:
            t = t[:58] + "…"
        s = it["abstract_opening"]
        if len(s) > 70:
            s = s[:68] + "…"
        fallback.append({"title_zh": t, "one_liner_zh": s})

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key or not items:
        return fallback

    try:
        from google import genai
        from google.genai import types
        import json

        payload = json.dumps(items, ensure_ascii=False)
        prompt = (
            "你是學術論文導讀編輯。把以下英文 paper 列表的 title 跟 abstract 第一句翻成簡短繁體中文。\n\n"
            "規則:\n"
            "- title_zh: 描述性繁中標題,12-22 字,不要直譯,抓主旨\n"
            "- one_liner_zh: 一句話描述這篇 paper 在做什麼,20-35 字\n"
            "- 學術術語(LLM / Transformer / RAG 等)保留英文不翻\n"
            "- 不要冗詞如「本研究」「本論文」\n\n"
            f"輸入(JSON):\n{payload}\n\n"
            'Schema: {"items": [{"i": int, "title_zh": str, "one_liner_zh": str}, ...]}'
        )
        client = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            max_output_tokens=2048,
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            response_mime_type="application/json",
        )
        resp = client.models.generate_content(
            model=GEMINI_TRANSLATE_MODEL,
            contents=prompt,
            config=config,
        )
        data = json.loads(resp.text)
        translated = {it["i"]: it for it in data.get("items", [])}
        out = []
        for idx, fb in enumerate(fallback, 1):
            t = translated.get(idx, {})
            title_zh = (t.get("title_zh") or "").strip() or fb["title_zh"]
            one_liner_zh = (t.get("one_liner_zh") or "").strip() or fb["one_liner_zh"]
            out.append({"title_zh": title_zh, "one_liner_zh": one_liner_zh})
        return out
    except Exception as e:
        logger.warning(f"Paper meta 中譯失敗，fallback 用英文截斷: {e}")
        return fallback


# ─────────────────────────────────────────────
# Callback handlers (button presses)
# ─────────────────────────────────────────────

async def cb_exegesis(query, action: str, payload: str):
    """callback dispatch：ex:dir / ex:upgrade / ex:noop / ex:skip / ex:reroll"""
    chat_id = query.message.chat_id

    if action == "dir":
        direction_id = payload
        await query.answer("收到，開始生成（5-10 分鐘）")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.message.reply_text("🚀 開始生成本週導讀...")
        try:
            from exegesis import generate_weekly
            result = await asyncio.to_thread(generate_weekly, direction_id)
        except Exception as e:
            logger.exception("generate failed")
            await query.message.reply_text(f"❌ 生成失敗：{e}")
            return

        # Gemini batch 翻譯成中文（一次呼叫，~5 秒，幾百 tokens）
        zh_metas = await asyncio.to_thread(
            _translate_paper_metas_to_zh, result["paper_metas"]
        )

        # 組裝每篇 paper 的中文摘要 + 升級按鈕
        paper_lines = []
        paper_buttons = []
        for i, card_path in enumerate(result["paper_card_paths"], 1):
            paper_meta = result["paper_metas"][i - 1]
            arxiv_id = paper_meta["external_ids"].get("arxiv") or paper_meta["id"].replace("arxiv:", "")
            zh = zh_metas[i - 1] if i - 1 < len(zh_metas) else {}
            title_zh = zh.get("title_zh") or paper_meta.get("title", "(untitled)")
            one_liner_zh = zh.get("one_liner_zh") or ""
            paper_lines.append(f"<b>P{i}.</b> {html_escape(title_zh)}")
            if one_liner_zh:
                paper_lines.append(f"   <i>{html_escape(one_liner_zh)}</i>")
            paper_buttons.append([
                InlineKeyboardButton(
                    f"翻譯 P{i} 全文",
                    callback_data=f"ex:upgrade:{arxiv_id}",
                )
            ])

        kb = InlineKeyboardMarkup(paper_buttons) if paper_buttons else None

        papers_block = "\n".join(paper_lines)
        await query.message.reply_html(
            f"✅ <b>本週導讀完成</b>\n\n"
            f"📖 <code>{html_escape(result['digest_path'])}</code>\n"
            f"🎧 Digest kepub 已上傳 Kobo\n"
            f"🗺 MOC 已更新\n\n"
            f"📄 <b>收錄 {len(result['paper_card_paths'])} 篇 paper：</b>\n"
            f"{papers_block}\n\n"
            f"💡 看完導讀後想深讀哪篇，按下方按鈕翻譯全文：",
            reply_markup=kb,
        )
        return

    if action == "upgrade":
        arxiv_id = payload
        await query.answer("開始翻譯全文（1-3 分鐘）")

        try:
            orig_kb = query.message.reply_markup
            new_rows = []
            for row in orig_kb.inline_keyboard:
                new_row = []
                for btn in row:
                    if btn.callback_data == f"ex:upgrade:{arxiv_id}":
                        new_row.append(InlineKeyboardButton(
                            "⏳ 翻譯中...",
                            callback_data="ex:noop:_",
                        ))
                    else:
                        new_row.append(btn)
                new_rows.append(new_row)
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_rows))
        except Exception:
            pass

        try:
            from exegesis import upgrade_paper
            result = await asyncio.to_thread(upgrade_paper, arxiv_id)
        except FileNotFoundError as e:
            await query.message.reply_html(
                f"❌ 找不到 paper card,無法翻譯：\n<code>{html_escape(str(e))}</code>\n\n"
                f"可能原因：該 paper 從未在 Exegesis digest 中產出過。"
            )
            return
        except Exception as e:
            logger.exception("upgrade failed")
            await query.message.reply_text(f"❌ 翻譯失敗：{e}")
            return

        try:
            orig_kb = query.message.reply_markup
            new_rows = []
            for row in orig_kb.inline_keyboard:
                new_row = []
                for btn in row:
                    if btn.callback_data == "ex:noop:_" and "翻譯中" in btn.text:
                        new_row.append(InlineKeyboardButton(
                            "✅ 已升級",
                            callback_data="ex:noop:_",
                        ))
                    else:
                        new_row.append(btn)
                new_rows.append(new_row)
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_rows))
        except Exception:
            pass

        kepub_line = "🎧 Paper kepub 已上傳 Kobo\n\n" if result.get("kepub_path") else ""
        await query.message.reply_html(
            f"✅ <b>中文全譯版已存</b>\n\n"
            f"<code>1 Sources/Papers Translated/{html_escape(result['filename'])}</code>\n\n"
            f"{kepub_line}"
            f"原 Paper Card 已自動加上連結。"
        )
        return

    if action == "noop":
        await query.answer()
        return

    if action == "skip":
        await query.answer("已跳過本週")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        from vault_writer import write_state_json
        await asyncio.to_thread(write_state_json, "state/pending_choice.json", {})
        await query.message.reply_text("⏭ 本週導讀已跳過。")
        return

    if action == "reroll":
        await query.answer("重新 curate 中...")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        progress = await query.message.reply_text("🎲 重新 curate 主題方向（30-60 秒）...")
        try:
            from exegesis import prepare_weekly
            result = await asyncio.to_thread(prepare_weekly)
        except Exception as e:
            await progress.edit_text(f"❌ 失敗：{e}")
            return
        await progress.delete()
        await _push_topic_directions(chat_id, query.get_bot(), result)
        return

    if action == "search":
        # /paper 的 sub-routing:payload 像 "dir:search_canonical" 或 "skip:_"
        parts = payload.split(":", 1)
        sub_action = parts[0]
        sub_payload = parts[1] if len(parts) > 1 else ""
        await cb_search(query, sub_action, sub_payload)
        return

    if action == "author":
        # /author 的 sub-routing:payload 像 "pick:A5005349213" 或 "skip:_"
        parts = payload.split(":", 1)
        sub_action = parts[0]
        sub_payload = parts[1] if len(parts) > 1 else ""
        await cb_author(query, sub_action, sub_payload)
        return

    await query.answer(f"未知 ex 動作：{action}")


async def cb_author(query, sub_action: str, sub_payload: str):
    """callback dispatch for /author:ex:author:pick:<openalex_id> / ex:author:skip:_"""
    chat_id = query.message.chat_id

    if sub_action == "pick":
        openalex_id = sub_payload
        await query.answer("收到,抓取該作者的 paper...")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        progress = await query.message.reply_text(
            "🔍 抓取該作者的 paper 並分桶(~10-30 秒)..."
        )
        try:
            from exegesis import generate_author_directions
            result = await asyncio.to_thread(generate_author_directions, openalex_id)
        except Exception as e:
            logger.exception("generate_author_directions failed")
            await progress.edit_text(f"❌ 抓取失敗:{e}")
            return

        if not result["directions"]:
            await progress.edit_text(
                f"😔 找不到該作者的 paper "
                f"(候選人 ID {openalex_id}, papers={result['candidates_count']})"
            )
            return

        await progress.delete()
        # 沿用 /paper 的 _push_search_directions(state 已寫進 search_pending_choice)
        await _push_search_directions(chat_id, query.get_bot(), result.get("author_name", ""), result)
        return

    if sub_action == "skip":
        await query.answer("已取消")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        from vault_writer import write_state_json
        await asyncio.to_thread(write_state_json, "state/author_pending.json", {})
        await query.message.reply_text("⏭ 作者搜尋已取消。")
        return

    await query.answer(f"未知 author 動作:{sub_action}")


async def cb_search(query, sub_action: str, sub_payload: str):
    """callback dispatch for /paper:ex:search:dir / ex:search:skip"""
    chat_id = query.message.chat_id

    if sub_action == "dir":
        direction_id = sub_payload
        await query.answer("收到，開始生成（5-10 分鐘）")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.message.reply_text("🚀 開始生成搜尋導讀...")
        try:
            from exegesis import generate_search
            result = await asyncio.to_thread(generate_search, direction_id)
        except Exception as e:
            logger.exception("search generate failed")
            await query.message.reply_text(f"❌ 生成失敗：{e}")
            return

        # Gemini batch 翻譯成中文(沿用 /brief 的 helper)
        zh_metas = await asyncio.to_thread(
            _translate_paper_metas_to_zh, result["paper_metas"]
        )

        paper_lines = []
        paper_buttons = []
        for i, card_path in enumerate(result["paper_card_paths"], 1):
            paper_meta = result["paper_metas"][i - 1]
            arxiv_id = paper_meta["external_ids"].get("arxiv") or paper_meta["id"].replace("arxiv:", "")
            zh = zh_metas[i - 1] if i - 1 < len(zh_metas) else {}
            title_zh = zh.get("title_zh") or paper_meta.get("title", "(untitled)")
            one_liner_zh = zh.get("one_liner_zh") or ""
            paper_lines.append(f"<b>P{i}.</b> {html_escape(title_zh)}")
            if one_liner_zh:
                paper_lines.append(f"   <i>{html_escape(one_liner_zh)}</i>")
            paper_buttons.append([
                InlineKeyboardButton(
                    f"翻譯 P{i} 全文",
                    callback_data=f"ex:upgrade:{arxiv_id}",
                )
            ])

        kb = InlineKeyboardMarkup(paper_buttons) if paper_buttons else None
        papers_block = "\n".join(paper_lines)
        await query.message.reply_html(
            f"✅ <b>搜尋導讀完成</b>\n\n"
            f"📖 <code>{html_escape(result['digest_path'])}</code>\n"
            f"🎧 Digest kepub 已上傳 Kobo\n\n"
            f"📄 <b>收錄 {len(result['paper_card_paths'])} 篇 paper：</b>\n"
            f"{papers_block}\n\n"
            f"💡 看完導讀後想深讀哪篇,按下方按鈕翻譯全文：",
            reply_markup=kb,
        )
        return

    if sub_action == "skip":
        await query.answer("已取消")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        from vault_writer import write_state_json
        await asyncio.to_thread(write_state_json, "state/search_pending_choice.json", {})
        await query.message.reply_text("⏭ 搜尋已取消。")
        return

    await query.answer(f"未知 search 動作：{sub_action}")


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = query.data or ""
    if ":" not in data:
        await query.answer()
        return
    action, payload = data.split(":", 1)
    logger.info(f"按鈕點擊: action={action}  payload={payload}")
    try:
        if action == "ex":
            parts = payload.split(":", 1)
            sub_action = parts[0]
            sub_payload = parts[1] if len(parts) > 1 else ""
            await cb_exegesis(query, sub_action, sub_payload)
        else:
            await query.answer()
    except Exception as e:
        logger.exception(f"button {data} failed: {e}")


# ─────────────────────────────────────────────
# Exegesis 排程
# ─────────────────────────────────────────────

async def exegesis_brief_loop(bot, chat_id: int):
    """每週 EXEGESIS_DOW 的 EXEGESIS_HOUR 點推送主題選擇。"""
    while True:
        now = datetime.now()
        days_ahead = (EXEGESIS_DOW - now.weekday()) % 7
        target = now.replace(hour=EXEGESIS_HOUR, minute=0, second=0, microsecond=0)
        if days_ahead == 0 and now >= target:
            days_ahead = 7
        target = target + timedelta(days=days_ahead)
        sleep_secs = (target - now).total_seconds()
        logger.info(f"exegesis brief 下次觸發於 {target}（{int(sleep_secs)}s 後）")
        await asyncio.sleep(sleep_secs)

        try:
            from exegesis import prepare_weekly
            result = await asyncio.to_thread(prepare_weekly)
            await _push_topic_directions(chat_id, bot, result)
        except Exception as e:
            logger.exception("exegesis brief failed")
            try:
                await bot.send_message(chat_id, f"⚠️ 週六 exegesis brief 失敗：{e}")
            except Exception:
                pass


async def _check_drift_on_startup(bot):
    """啟動時跑 Pharos EPUB_CSS + Anamnesis API 漂移檢查。背景 task，失敗不阻擋 bot。"""
    try:
        from digest_to_kepub import _verify_pharos_drift
        await asyncio.to_thread(_verify_pharos_drift)
    except Exception as e:
        logger.warning(f"pharos drift check failed: {e}")
    try:
        from anamnesis_drift import verify_anamnesis_drift
        await asyncio.to_thread(verify_anamnesis_drift)
    except Exception as e:
        logger.warning(f"anamnesis drift check failed: {e}")


# ─────────────────────────────────────────────
# 主程式
# ─────────────────────────────────────────────

def main():
    token = os.environ.get("EXEGESIS_BOT_TOKEN")
    if not token:
        logger.error("❌ 缺少 EXEGESIS_BOT_TOKEN（去 @BotFather 建獨立 bot 拿 token）")
        sys.exit(1)

    async def post_init(application: Application):
        await application.bot.set_my_commands([
            BotCommand("brief", "📚 立刻產生本週導讀"),
            BotCommand("topics", "📊 主題輪替狀態"),
            BotCommand("paper", "🔍 主動查詢 paper（例：/paper LaMDA）"),
            BotCommand("author", "👤 查指定作者的 paper（例：/author Demis Hassabis）"),
            BotCommand("translation", "📄 翻譯 paper 為全文中文"),
            BotCommand("start", "👋 介紹 Exegesis"),
            BotCommand("help", "❓ 用法說明"),
        ])

        # 啟動 brief loop
        chat_id_str = os.environ.get("TELEGRAM_CHAT_ID")
        if chat_id_str:
            try:
                chat_id = int(chat_id_str)
                asyncio.create_task(exegesis_brief_loop(application.bot, chat_id))
                logger.info(f"✅ exegesis brief task 啟動（每週 DOW={EXEGESIS_DOW} {EXEGESIS_HOUR}:00 推送）")
            except ValueError:
                logger.warning(f"TELEGRAM_CHAT_ID 不是有效整數：{chat_id_str}，brief loop 跳過")
        else:
            logger.warning("沒有 TELEGRAM_CHAT_ID，brief loop 跳過")

        asyncio.create_task(_check_drift_on_startup(application.bot))

    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CallbackQueryHandler(handle_button))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("brief", cmd_brief))
    app.add_handler(CommandHandler("topics", cmd_topics))
    app.add_handler(CommandHandler("translation", cmd_upgrade))
    app.add_handler(CommandHandler("paper", cmd_paper))
    app.add_handler(CommandHandler("author", cmd_author))

    logger.info("✅ exegesis_bot 啟動")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
