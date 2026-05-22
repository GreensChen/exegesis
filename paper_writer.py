#!/usr/bin/env python3
"""paper_writer.py — Digest 長文 + Paper Card 寫作 + Cross-Reference。"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

GEMINI_MODEL = "gemini-3-flash-preview"

logger = logging.getLogger("paper_writer")


def _yaml_str(s) -> str:
    if s is None:
        return '""'
    s = str(s).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[/\\:*?"<>|\r\n\t]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:max_len].rstrip()


# ═══════════════════════════════════════════════════════
# Short Title Prompt
# ═══════════════════════════════════════════════════════

TITLE_EN_PROMPT = """You are a senior science editor who writes English titles for long-form articles aimed at a thoughtful general reader.

Input: weekly topic, direction narrative (Chinese), the Chinese title we will use, and the list of paper titles.

Output: a complete, descriptive English title that mirrors the spirit of the Chinese title. Length is not constrained — write whatever length you need to fully convey the idea.

Style rules:
- Title Case.
- No trailing punctuation.
- The English title should communicate what the Chinese title communicates — same arc, same scope, comparable specificity.
- Concrete and direct. Avoid empty verbs like "Exploring", "Towards", "On the".
- Avoid academic-paper register like "A Survey of", "Recent Advances in", "Recent Progress on".

Good examples:
- Building AI Agents That Have Real, Lasting Memory
- Crossing the Line: When Large Language Models Begin to Mirror Human Cognition
- The Quiet Collapse of Chain-of-Thought as a Faithful Reasoning Trace
- What 50,000 Multilingual Probes Reveal About How LLMs Actually Think

Reply with the title only. No preface, no quotes, no explanation."""


# ═══════════════════════════════════════════════════════
# Writing System Prompt
# ═══════════════════════════════════════════════════════

WRITING_SYSTEM_PROMPT = """你是一位寫作風格介於 Quanta Magazine 與經濟學人科技版之間的資深科普作者，正在為一位特定讀者寫週刊式的「主題式論文導讀」。

== 你的讀者 ==

這位讀者：
- 不是該領域的學者，但對思考很認真
- 想學到真正的專業概念，不要被簡化成 buzzword
- 通勤時間閱讀（約 45-60 分鐘），需要節奏感
- 已在自己的知識庫中累積過大量讀書筆記，對跨領域連結有強烈興趣
- 討厭套話、空泛的開場與結尾

== 寫作鐵則 ==

長度：6000-8000 中文字（嚴格）。

語言：全部繁體中文（台灣用語，例如「資訊」不是「信息」，「軟體」不是「軟件」，「網路」不是「網絡」）。

專業名詞處理：
- 第一次出現時：中文（English Term） 格式
  - 例：「思考鏈（Chain-of-Thought，以下用英文簡寫 CoT）」
  - 例：「參數忠實度（parametric faithfulness）」
- 第二次起：用熟悉版本（中文簡稱或英文縮寫）
- 公認的縮寫（LLM、AI、CoT）可以直接用，但首次出現仍展開一次

引用規則：
- 引用 paper 時：用 [[1 Sources/Papers/{paper_filename_stem}|{paper_short_title}]] 的 wikilink（filename_stem 不含 .md）
- 引用 vault 既有 notes（來自 cross_refs）：用 [[1 Sources/Books/xxx|alias]] 完整路徑
- 引用具體數字一定要有出處（從哪篇 paper 的哪段）

禁止用語（嚴格）：
- 「這是一個很有趣的問題」
- 「綜上所述」「總結來說」「最後我們可以看到」
- 「值得我們深入思考」
- 「在這個快速發展的時代」
- 「眾所周知」「不言而喻」
- 任何形式的 ChatGPT 式套話

== 五段固定結構 ==

每段標題必須完全照下列文字（不要加編號、不要加「目標字數」括號）。字數是目標值，允許 ±15% 浮動，但總長須在 6000-8000 字。

整體行文以**研究報告**為主，平實準確，避免新聞報導或部落格式的渲染語氣。

## 前言（目標 500 字）

用一個具體可感知的情境切入，讓讀者感受到本週主題在實務上的存在。可以是：
- 日常使用某產品時碰到的具體狀況
- 近期新聞或社群熱議
- 某個讓人困惑的觀察

接著提出「為什麼」的問題，而不是直接回答。

最後一段自然帶到本週要追蹤的研究方向——告訴讀者「本週 N 篇 paper 從不同角度回應了這個問題」，不要劇透結論。

## 背景（目標 1500 字）

這段是「概念建構期」，讀者要在這段建立後續討論需要的詞彙庫。

回答：
- 這個領域過去怎麼回答這個問題？有哪些主流方法或理論？
- 既有答案的盲點 / 限制 / 矛盾在哪？
- 為什麼這些問題到現在還沒被解決？

寫作節奏：
- 2-3 個段落，每段聚焦一個關鍵概念
- 每段第一次出現的專業名詞用「中文（English）」格式
- 用比喻或日常類比解釋抽象概念，但不要過度簡化失去精確

不要在這段引用具體 paper，要建立的是「歷史脈絡」而非「最新研究」。

## 研究報告（目標 3000 字）

本週收錄的 N 篇 paper 如何各自回應第 1-2 段提出的問題。

結構：每篇 paper 一個 H3 小節：

### [[1 Sources/Papers/{paper_filename_stem}|{paper_short_title}]]

每個小節 700-1000 字，平實陳述：
- 作者背景（1 句即可）：來自哪個機構 / 哪個團隊，讓讀者能 anchor
- 該 paper 的**核心方法**：用易懂方式重述（不要逐句翻譯 abstract）
- **關鍵發現**（含具體數據）：有幾個百分點、跑了什麼基準、樣本多大
- 在整體議題中的角色定位：核心 / 補充 / 反方 / 延伸
- 跟其他本週收錄 paper 的對話：「跟前面 X 那篇形成對照」「補充了 Y 那篇的限制」

**重點**：不是逐篇摘要，而是讓 N 篇 paper 之間形成清楚的對照關係。讀者讀完應該能整理出這幾篇研究在「同一個問題上的不同切角」。

## 限制與爭議（目標 1000 字）

這段是培養 user 批判視角的關鍵段。

分項討論：
- **每篇 paper 自身的盲點**：作者承認的 + 評審/社群質疑的
- **整個議題的根本爭議**：方法論層次的——例如測量方式本身是否有效、概念定義是否前提錯誤
- **被忽略的角度**：地域（英語以外）、應用場景（學術以外）、利害關係（誰會獲利 / 受損）

寫作要求：
- 不要寫成「但是...」「然而...」這種反轉句堆疊
- 每個批評都要具體：不能只說「方法有限制」，要說清楚什麼限制
- 可以表明立場，但要區分「客觀限制」跟「個人判斷」

## 結語（目標 500 字）

讀者讀完該帶走什麼？三層收穫：

1. **新的概念詞彙**：本週引入了哪些可以日常使用的詞？
2. **新的觀察框架**：有了這些詞，讀者可以怎麼看待類似情境？
3. **未解問題**：這個議題還有什麼開放？讀者可以追蹤什麼線索？

不要寫「相關書籍」、「相關文章」或「新詞清單」——這些由系統在第 6 章另外拼接，這段純粹做思考收尾。

== 輸出格式 ==

只回 Markdown 純文字內容，從「## 前言」開始，到「## 結語」最後一句結束。

不要包含 frontmatter（由系統另外組裝）。
不要包含 H1 標題（由系統另外組裝）。
不要包含「本週收錄」清單（由系統另外組裝）。
不要包含延伸章節（相關書籍 / 相關訪談 / 相關文章 / 名詞定義，由系統在文末拼接）。
不要前言、不要結語、不要 ```markdown``` 包裝。"""


WRITING_USER_PROMPT_TEMPLATE = """本週主題：{topic_name_zh}（{topic_name_en}）
本週 ISO 週次：{iso_week}
方向類型：{direction_category}{edge_zone_note}

方向 narrative（由 Curator 產出，提供寫作主軸參考）：
{direction_narrative}

== 本週收錄的 {n_papers} 篇 paper（已結構化） ==

{papers_block}

== 跨領域連結候選（由 cross_reference 篩出，僅供你寫作時參考；最終區塊由系統拼接，你不需要寫） ==

{cross_refs_block}

請依 system prompt 的五段框架寫一篇 6000-8000 字的科普導讀。
只輸出五段內文，不包含 frontmatter、H1、跨領域連結區塊、新詞清單。"""


# ═══════════════════════════════════════════════════════
# write_digest
# ═══════════════════════════════════════════════════════

def write_digest(
    iso_week: str,
    topic: dict,
    direction: dict,
    paper_contents: list[dict],
    paper_metas: list[dict],
    cross_refs: list[dict],
) -> dict:
    """寫一篇 digest 長文（6000-8000 中文字），用五段科普框架。"""
    from vocabulary_manager import apply_tags_to_capture

    paper_card_filenames = []
    for meta in paper_metas:
        card_fn = _make_paper_card_filename(meta)
        paper_card_filenames.append(card_fn)

    short_title_pair = _generate_short_title(topic, direction, paper_metas)
    short_title = short_title_pair["zh"]
    short_title_en = short_title_pair["en"]
    # 檔名用英文 stem（ASCII 乾淨、跟 Pharos 逐字稿一致）
    title_slug = short_title_en

    papers_block = _render_papers_block(paper_contents, paper_metas, paper_card_filenames)
    cross_refs_block = _render_cross_refs_block(cross_refs)

    edge_zone_note = ""
    if direction.get("category") == "edge" and direction.get("edge_zone"):
        edge_zone_note = f" （跨界至 {direction['edge_zone']}）"

    user_prompt = WRITING_USER_PROMPT_TEMPLATE.format(
        topic_name_zh=topic["name_zh"],
        topic_name_en=topic["name_en"],
        iso_week=iso_week,
        direction_category=direction.get("category", "core"),
        edge_zone_note=edge_zone_note,
        direction_narrative=direction.get("narrative", ""),
        n_papers=len(paper_metas),
        papers_block=papers_block,
        cross_refs_block=cross_refs_block,
    )

    from google.genai import types
    client = _get_gemini_client()

    config = types.GenerateContentConfig(
        system_instruction=WRITING_SYSTEM_PROMPT,
        max_output_tokens=20000,
        thinking_config=types.ThinkingConfig(thinking_budget=8192),
    )

    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_prompt,
        config=config,
    )
    body = resp.text
    body = _validate_and_retry(body, user_prompt, config, client)

    digest_filename_stem = f"{iso_week}_{topic['code']}_{_safe_filename(title_slug, 60)}"
    digest_filename = f"{digest_filename_stem}.md"

    tags_from_vocab = apply_tags_to_capture(body, digest_filename)

    reading_time_min = max(len(body) // 400, 5)

    dates = [m.get("published_date", "") for m in paper_metas if m.get("published_date")]
    date_from = min(dates) if dates else ""
    date_to = max(dates) if dates else ""

    new_terms = []
    for pc in paper_contents:
        for t in pc.get("key_terms", []):
            if t not in new_terms:
                new_terms.append(t)

    paper_card_stems = [fn.replace(".md", "") for fn in paper_card_filenames]

    content = _assemble_digest(
        short_title=short_title,
        short_title_en=short_title_en,
        iso_week=iso_week,
        topic=topic,
        direction=direction,
        paper_card_filenames=paper_card_filenames,
        paper_card_stems=paper_card_stems,
        paper_metas=paper_metas,
        body=body,
        cross_refs=cross_refs,
        new_terms=new_terms,
        tags_from_vocab=tags_from_vocab,
        reading_time_min=reading_time_min,
        date_from=date_from,
        date_to=date_to,
    )

    return {
        "title": short_title,
        "title_en": short_title_en,
        "title_slug": title_slug,
        "filename": digest_filename,
        "filename_stem": digest_filename_stem,
        "content": content,
        "new_terms": new_terms,
        "tags_from_vocab": tags_from_vocab,
        "paper_card_filenames": paper_card_filenames,
    }


def _generate_short_title(topic: dict, direction: dict, paper_metas: list[dict]) -> dict:
    """產出 {"zh", "en"} 標題對。

    中文：直接用 direction.title（curator 已經產出的描述性標題，user 偏好的風格）。
    英文：用 Gemini 生成完整描述的對應英文標題，不限長度。
    """
    paper_titles = [m.get("title", "") for m in paper_metas]
    title_zh = direction.get("title", "").strip() or "本週導讀"

    user_msg = (
        f"Weekly topic: {topic['name_zh']} / {topic['name_en']}\n"
        f"Direction narrative (Chinese): {direction.get('narrative', '')}\n"
        f"Chinese title we will use: {title_zh}\n"
        f"Papers:\n" + "\n".join(f"- {t}" for t in paper_titles)
    )

    from google.genai import types
    client = _get_gemini_client()

    fallback_en = re.sub(r"[^A-Za-z0-9 ]", " ", direction.get("title", "")).strip() or topic.get("code", "Weekly")

    try:
        config = types.GenerateContentConfig(
            system_instruction=TITLE_EN_PROMPT,
            max_output_tokens=2000,
            temperature=0.7,
        )
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=user_msg, config=config,
        )
        title_en = (resp.text or "").strip().strip('"').strip("'").strip()
        # 拿第一行（避免 Gemini 多行解釋）
        title_en = title_en.split("\n")[0].strip().strip('"').strip("'").strip()
    except Exception as e:
        logger.warning(f"英文標題生成失敗: {e}")
        title_en = fallback_en

    if not title_en or len(title_en) < 3:
        logger.warning(f"英文標題品質不佳 '{title_en}'，fallback")
        title_en = fallback_en

    return {"zh": title_zh, "en": title_en}


def _validate_and_retry(content: str, user_prompt: str, config, client, retry_count: int = 0) -> str:
    char_count = len(content)
    if 5500 <= char_count <= 9000:
        return content
    if retry_count >= 1:
        logger.warning(f"digest 字數 {char_count} 不在 6000-8000 範圍，但已重試，接受結果")
        return content

    logger.info(f"digest 字數 {char_count}，重試調整...")
    if char_count < 5500:
        hint = "請大幅展開內容，特別是第 2 段問題地景跟第 3 段本週現場。"
    else:
        hint = "請精簡套話與重複，保留實質內容。"

    retry_prompt = user_prompt + f"\n\n== 重試說明 ==\n上次寫了 {char_count} 字，請調整到 6500-7500 字。{hint}"

    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=retry_prompt,
        config=config,
    )
    return _validate_and_retry(resp.text, user_prompt, config, client, retry_count + 1)


def _assemble_digest(
    short_title, short_title_en, iso_week, topic, direction, paper_card_filenames,
    paper_card_stems, paper_metas, body, cross_refs, new_terms,
    tags_from_vocab, reading_time_min, date_from, date_to,
) -> str:
    """組裝完整 Digest markdown。"""
    n_papers = len(paper_metas)
    timestamp = datetime.now().isoformat()

    all_tags = ["paper-digest", "weekly", topic["id"]] + tags_from_vocab
    tags_str = ", ".join(all_tags)

    papers_fm = json.dumps(paper_card_stems, ensure_ascii=False)

    edge_zone_val = direction.get("edge_zone") or ""

    parts = []
    parts.append("---")
    parts.append("type: paper-digest")
    parts.append(f"title: {_yaml_str(short_title)}")
    parts.append(f"title_en: {_yaml_str(short_title_en)}")
    parts.append(f'week: "{iso_week}"')
    parts.append(f'topic: "{topic["id"]}"')
    parts.append(f'topic_zh: "{topic["name_zh"]}"')
    parts.append(f'topic_en: "{topic["name_en"]}"')
    parts.append(f'direction_category: "{direction.get("category", "core")}"')
    parts.append(f'edge_zone: "{edge_zone_val}"')
    parts.append(f"papers: {papers_fm}")
    parts.append(f"papers_count: {n_papers}")
    parts.append(f'generated_at: "{timestamp}"')
    parts.append(f"tags: [{tags_str}]")
    parts.append(f"generated_by: {GEMINI_MODEL}")
    parts.append(f"reading_time_min: {reading_time_min}")
    parts.append("---")
    parts.append("")
    parts.append(f"# {short_title}")
    parts.append(f"*第 {iso_week} 週 · {topic['name_zh']}*")
    parts.append("")
    parts.append(f"> 🎯 本週導讀基於 {n_papers} 篇近期論文，閱讀時間約 {reading_time_min} 分鐘")
    parts.append(">")
    parts.append(f"> 📅 涵蓋時間範圍：{date_from} ~ {date_to}")
    parts.append("")
    parts.append("## 📖 本週收錄")
    parts.append("")
    for fn_stem, meta in zip(paper_card_stems, paper_metas):
        short = meta.get("title", "")[:30]
        parts.append(f"- [[1 Sources/Papers/{fn_stem}|{short}]]")
    parts.append("")
    parts.append("---")
    parts.append("")

    parts.append(body)

    parts.append("")
    parts.append("---")
    parts.append("")

    books = [c for c in cross_refs if c.get("type") == "book"]
    interviews = [c for c in cross_refs if c.get("type") == "interview"]
    articles = [c for c in cross_refs if c.get("type") == "article"]

    # Cross-ref 用「」純文字格式，避免 wikilink [[path|alias]] 在 Kobo 上殘留
    # 「|alias」「]]」這類符號（_markdown_to_xhtml 的 wikilink regex 偶爾抓不
    # 到 path 內的特殊字元）。Obsidian 端會失去 click-to-open，但 vault 既有
    # 的反向連結機制仍能透過 tag 共現找到。
    def _xref_line(c):
        title = (c.get("title", "") or "").strip()
        reason = (c.get("relevance_reason", "") or "").strip()
        return f"- 「{title}」{reason}"

    # 動態編號的延伸章節（6+）。Empty 直接跳過、號碼順移。
    dynamic_chapters = []
    if books:
        dynamic_chapters.append(("相關書籍", [_xref_line(c) for c in books]))
    if interviews:
        dynamic_chapters.append(("相關訪談", [_xref_line(c) for c in interviews]))
    if articles:
        dynamic_chapters.append(("相關文章 / 筆記", [_xref_line(c) for c in articles]))
    if new_terms:
        term_lines = [
            f"- **{t.get('term_en', '')} ({t.get('term_zh', '')})** — {t.get('definition', '')}"
            for t in new_terms
        ]
        dynamic_chapters.append(("名詞定義", term_lines))

    for name, lines in dynamic_chapters:
        parts.append(f"## {name}")
        parts.append("")
        parts.extend(lines)
        parts.append("")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════
# write_paper_card — 中間態 Paper Card
# ═══════════════════════════════════════════════════════

def write_paper_card(
    paper_meta: dict,
    paper_content: dict,
    digest_filename_stem: str,
    digest_short_title: str,
) -> dict:
    """寫一篇 Paper Card 中間態（~1500 字）。

    這份卡片的定位：不是 self-contained 內容，而是 Digest 的補充參考點。
    保留的內容：Digest 沒展開的核心發現、方法精要、限制、術語表。
    砍掉的內容：重複 Digest 已有的論證脈絡與跨研究關係。
    """
    from vocabulary_manager import apply_tags_to_capture

    filename = _make_paper_card_filename(paper_meta)
    topic_id = paper_meta.get("_topic_id", "")

    content = _build_paper_card_content(
        paper_meta, paper_content, digest_filename_stem, digest_short_title, topic_id,
    )

    tags_from_vocab = apply_tags_to_capture(content, filename)

    # 在 frontmatter 中補 tag
    tag_line = f"tags: [paper, {topic_id}" + (", " + ", ".join(tags_from_vocab) if tags_from_vocab else "") + "]"
    content = content.replace("tags: [paper, {topic_id}]", tag_line, 1)

    return {
        "filename": filename,
        "filename_stem": filename.replace(".md", ""),
        "content": content,
        "tags_from_vocab": tags_from_vocab,
    }


def _make_paper_card_filename(paper_meta: dict) -> str:
    """產 Paper Card 檔名。"""
    arxiv_id = paper_meta.get("external_ids", {}).get("arxiv", "")
    if not arxiv_id:
        arxiv_id = paper_meta.get("id", "").replace("arxiv:", "").replace("ss:", "")

    title = paper_meta.get("title", "")
    words = title.split()
    stop_words = {"the", "a", "an", "of", "in", "for", "on", "to", "and", "is", "with", "by", "from", "at", "as"}
    meaningful = [w for w in words if w.lower() not in stop_words][:5]
    short_title = " ".join(meaningful)
    safe_title = _safe_filename(short_title, max_len=50)

    return f"{arxiv_id} {safe_title}.md"


def write_paper_stub(paper_meta: dict, query: str) -> dict:
    """寫一個最小 paper card stub 到 1 Sources/Papers/。

    用途：/paper <query> 找到的 paper 還沒被 Weekly Paper 收錄過時，
    寫個 frontmatter-only stub 讓既有 /upgrade 流程能 _find_paper_card → 翻譯。

    若同名檔已存在（Weekly Paper 已寫過更完整的 card），跳過、不覆寫。
    回傳 {"filename": ..., "wrote": True/False}。
    """
    from vault_writer import list_vault_folder, write_to_vault

    filename = _make_paper_card_filename(paper_meta)
    # 檢查同 arxiv_id prefix 是否已有 card
    arxiv_id = paper_meta.get("external_ids", {}).get("arxiv", "")
    existing = list_vault_folder("1 Sources/Papers")
    for existing_fn in existing:
        if arxiv_id and existing_fn.startswith(arxiv_id):
            return {"filename": existing_fn, "wrote": False}

    content = _build_paper_stub_content(paper_meta, query)
    write_to_vault(f"1 Sources/Papers/{filename}", content)
    return {"filename": filename, "wrote": True}


def _build_paper_stub_content(paper_meta: dict, query: str) -> str:
    """最小 paper stub markdown：純 frontmatter + 一行說明、無 body。"""
    arxiv_id = paper_meta.get("external_ids", {}).get("arxiv", "")
    url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
    pdf_url = paper_meta.get("pdf_url", "")
    venue = paper_meta.get("venue") or "arXiv preprint"
    authors_list = paper_meta.get("authors", [])
    today = datetime.now().strftime("%Y-%m-%d")

    parts = []
    parts.append("---")
    parts.append("type: paper")
    parts.append(f'arxiv_id: "{arxiv_id}"')
    parts.append(f"title: {_yaml_str(paper_meta.get('title', ''))}")
    parts.append(f"authors: {json.dumps(authors_list[:5], ensure_ascii=False)}")
    parts.append(f'published: "{paper_meta.get("published_date", "")}"')
    parts.append(f"venue: {_yaml_str(venue)}")
    parts.append(f'url: "{url}"')
    parts.append(f'pdf_url: "{pdf_url}"')
    parts.append(f"citation_count: {paper_meta.get('citation_count', 0)}")
    parts.append('source: search')
    parts.append(f"search_query: {_yaml_str(query)}")
    parts.append(f'searched_at: "{today}"')
    parts.append("translated: false")
    parts.append("tags: [paper, search]")
    parts.append("---")
    parts.append("")
    parts.append(f"# {paper_meta.get('title', '')}")
    parts.append("")
    parts.append(f"> 🔍 由 `/paper {query}` 搜尋發現")
    parts.append("")
    if url:
        parts.append(f"📄 [arXiv 原文]({url})")
        parts.append("")
    abstract = paper_meta.get("abstract", "")
    if abstract:
        parts.append("## Abstract")
        parts.append("")
        parts.append(abstract)
        parts.append("")
    return "\n".join(parts)


def _build_paper_card_content(
    paper_meta: dict,
    paper_content: dict,
    digest_filename_stem: str,
    digest_short_title: str,
    topic_id: str,
) -> str:
    """純字串拼裝，不 call LLM。"""
    arxiv_id = paper_meta.get("external_ids", {}).get("arxiv", "")
    url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
    pdf_url = paper_meta.get("pdf_url", "")
    venue = paper_meta.get("venue") or "arXiv preprint"

    authors_list = paper_meta.get("authors", [])
    authors_joined = ", ".join(authors_list[:3])
    if len(authors_list) > 3:
        authors_joined += " et al."

    parts = []
    parts.append("---")
    parts.append("type: paper")
    parts.append(f'arxiv_id: "{arxiv_id}"')
    parts.append(f"title: {_yaml_str(paper_meta.get('title', ''))}")
    parts.append(f"authors: {json.dumps(authors_list[:5], ensure_ascii=False)}")
    parts.append(f'published: "{paper_meta.get("published_date", "")}"')
    parts.append(f"venue: {_yaml_str(venue)}")
    parts.append(f'url: "{url}"')
    parts.append(f'pdf_url: "{pdf_url}"')
    parts.append(f"citation_count: {paper_meta.get('citation_count', 0)}")
    parts.append(f'appeared_in: "[[1 Sources/Digests/{digest_filename_stem}]]"')
    parts.append("translated: false")
    parts.append(f"tags: [paper, {'{topic_id}'}]")
    parts.append(f"generated_by: {GEMINI_MODEL}")
    parts.append("---")
    parts.append("")
    parts.append(f"# {paper_meta.get('title', '')}")
    parts.append(f"*{authors_joined} · {venue}*")
    parts.append("")
    if url:
        parts.append(f"> 📄 [arXiv 原文]({url})")
    parts.append(f"> 📰 收錄於本週導讀: [[1 Sources/Digests/{digest_filename_stem}|{digest_short_title}]]")
    parts.append("")
    parts.append("## 一句話摘要")
    parts.append("")
    parts.append(paper_content.get("one_liner", ""))
    parts.append("")
    parts.append("## 核心發現")
    parts.append("")
    for finding in paper_content.get("core_findings", []):
        parts.append(f"- {finding}")
    parts.append("")
    parts.append("## 方法精要")
    parts.append("")
    parts.append(paper_content.get("method_summary", ""))
    parts.append("")
    parts.append("## 限制")
    parts.append("")
    for limitation in paper_content.get("limitations", [])[:3]:
        parts.append(f"- {limitation}")
    parts.append("")
    parts.append("## 關鍵術語")
    parts.append("")
    for term in paper_content.get("key_terms", [])[:7]:
        parts.append(f"- **{term.get('term_en', '')}（{term.get('term_zh', '')}）** — {term.get('definition', '')}")
    parts.append("")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════
# Cross-Reference
# ═══════════════════════════════════════════════════════

def find_related_notes(
    direction: dict,
    paper_contents: list[dict],
    interest_model: dict = None,
    max_results: int = 8,
) -> list[dict]:
    """從 vault 既有 notes 找跟本週主題相關的。"""
    from vault_writer import list_vault_folder, read_from_vault

    candidates = []
    for folder, note_type in [
        ("1 Sources/Books", "book"),
        ("1 Sources/Interviews", "interview"),
        ("1 Sources/Articles", "article"),
    ]:
        for filename in list_vault_folder(folder):
            try:
                content = read_from_vault(f"{folder}/{filename}")
                summary = _read_note_summary(content, filename, folder, note_type)
                if summary and summary.get("type") not in ("paper-digest", "paper", "paper-translation"):
                    candidates.append(summary)
            except Exception:
                continue

    if not candidates:
        return []

    one_liners = [pc.get("one_liner", "") for pc in paper_contents]
    narrative = direction.get("narrative", "")

    hint = ""
    if interest_model:
        top_tags = sorted(
            interest_model.get("interests", {}).items(),
            key=lambda x: -x[1].get("score", 0)
        )[:5]
        hint = f"\nuser 最近高度關注的 tag（優先連到含這些 tag 的 notes）：\n{[t for t, _ in top_tags]}"

    candidates_str = "\n".join(
        f"- type={c['type']}, file={c['wikilink']}, title={c['title']}, tags={c.get('tags', [])}"
        for c in candidates[:50]
    )

    papers_str = "\n".join(f"- {ol}" for ol in one_liners)

    user_prompt = (
        f"本週方向 narrative：{narrative}\n\n"
        f"本週 paper 一句話摘要：\n{papers_str}\n\n"
        f"vault 既有筆記候選（最多挑 {max_results} 個，寧少不濫）：\n{candidates_str}"
        f"{hint}\n\n"
        f"請從候選中挑出真正相關的，每個給 relevance_reason。三類各最多 3 個，總共 ≤ {max_results}。\n"
        f"只回 JSON：{{\"related\": [{{\"type\": \"book\", \"filename\": \"...\", \"wikilink\": \"...\", \"title\": \"...\", \"relevance_reason\": \"...\"}}]}}"
    )

    system_prompt = (
        "你是 vault cross-reference 專家。從候選筆記中找出跟本週論文主題真正相關的。"
        "門檻要高——寧可少不要濫。relevance_reason 必須具體（「該書討論 X，跟本週 Y 相連」），不可空泛（「都跟 AI 有關」）。"
    )

    from google.genai import types
    client = _get_gemini_client()

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=4096,
        thinking_config=types.ThinkingConfig(thinking_budget=2048),
        response_mime_type="application/json",
    )

    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_prompt,
            config=config,
        )
        data = json.loads(resp.text)
        results = data.get("related", [])
        logger.info(f"cross_reference: 找到 {len(results)} 個相關 notes")
        return results[:max_results]
    except Exception as e:
        logger.error(f"cross_reference 失敗: {e}")
        return []


def _read_note_summary(content: str, filename: str, folder: str, note_type: str) -> dict:
    """Parse frontmatter + H1 做輕量 summary。"""
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not fm_match:
        return None

    fm_text = fm_match.group(1)
    body_start = content[fm_match.end():]

    def _fm(key):
        m = re.search(rf'^{key}:\s*"?(.*?)"?\s*$', fm_text, re.MULTILINE)
        return m.group(1).strip().strip('"') if m else ""

    fm_type = _fm("type")

    title = _fm("title") or filename.replace(".md", "")

    tags_match = re.search(r"^tags:\s*\[(.*?)\]", fm_text, re.MULTILINE)
    tags = []
    if tags_match:
        tags = [t.strip().strip('"').strip("'") for t in tags_match.group(1).split(",") if t.strip()]

    stem = filename.replace(".md", "")
    wikilink = f"{folder}/{stem}"

    return {
        "type": fm_type or note_type,
        "filename": filename,
        "wikilink": wikilink,
        "title": title,
        "tags": tags,
    }


# ═══════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════

def _render_papers_block(paper_contents: list[dict], paper_metas: list[dict], paper_card_filenames: list[str]) -> str:
    """渲染 papers_block 給 Writing Agent。"""
    blocks = []
    for i, (pc, meta, fn) in enumerate(zip(paper_contents, paper_metas, paper_card_filenames), 1):
        fn_stem = fn.replace(".md", "")
        venue = meta.get("venue") or "arXiv preprint"
        authors = ", ".join(meta.get("authors", [])[:3])
        if len(meta.get("authors", [])) > 3:
            authors += ", et al."

        arxiv_id = meta.get("external_ids", {}).get("arxiv", meta.get("id", ""))

        terms_str = "\n".join(
            f"- {t.get('term_en', '')} ({t.get('term_zh', '')}): {t.get('definition', '')}"
            for t in pc.get("key_terms", [])
        )

        block = (
            f"[Paper {i}] filename_stem: {fn_stem}\n"
            f"arXiv: {arxiv_id} | Authors: {authors} | Venue: {venue} | Published: {meta.get('published_date', '')}\n\n"
            f"一句話摘要：{pc.get('one_liner', '')}\n\n"
            f"核心發現：\n" + "\n".join(f"- {f}" for f in pc.get("core_findings", [])) + "\n\n"
            f"方法：{pc.get('method_summary', '')}\n\n"
            f"關鍵結果：\n" + "\n".join(f"- {r}" for r in pc.get("key_results", [])) + "\n\n"
            f"限制：\n" + "\n".join(f"- {l}" for l in pc.get("limitations", [])) + "\n\n"
            f"關鍵術語（可在文中引入）：\n{terms_str}\n\n"
            f"關聯（跟其他研究的關係）：{pc.get('connections', '')}"
        )
        blocks.append(block)

    return "\n\n---\n\n".join(blocks)


def _render_cross_refs_block(cross_refs: list[dict]) -> str:
    if not cross_refs:
        return "（無候選）"
    lines = []
    for c in cross_refs:
        lines.append(
            f"- type={c.get('type', '')}, file={c.get('wikilink', c.get('filename', ''))}, "
            f"title={c.get('title', '')}, reason={c.get('relevance_reason', '')}"
        )
    return "\n".join(lines)


def _get_gemini_client():
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("缺 GEMINI_API_KEY")
    return genai.Client(api_key=api_key)
