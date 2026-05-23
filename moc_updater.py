#!/usr/bin/env python3
"""moc_updater.py — vault MOCs 維護。

三組 MOC:
- Weekly Papers MOC: /brief + /paper 的 digest 統一索引(weekly + search 分段)
- Translated Papers MOC: 所有升級全文中譯的 paper 索引
- Tag MOCs: 出現次數 ≥ 3 的 tag 各別產一個 3 MOCs/Tag - X.md
"""

import logging
import re
from datetime import datetime

logger = logging.getLogger("moc_updater")


# ═══════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════

def update_moc() -> str:
    """重新生成 3 MOCs/Weekly Papers MOC.md(weekly + search 分段統一索引)。"""
    from vault_writer import list_vault_folder, read_from_vault, write_to_vault, read_state_json

    digests = []
    for filename in list_vault_folder("1 Sources/Digests"):
        if filename.startswith("_"):
            continue
        try:
            content = read_from_vault(f"1 Sources/Digests/{filename}")
            meta = _parse_digest_meta(content, filename)
            if meta:
                digests.append(meta)
        except Exception as e:
            logger.warning(f"讀 {filename} 失敗: {e}")

    digests.sort(key=lambda d: (d["week"], d.get("generated_at", "")), reverse=True)

    topics_cfg = read_state_json("config/topics.json")
    all_topics = topics_cfg.get("topics", [])

    content = _build_unified_papers_moc(digests, all_topics)
    path = write_to_vault("3 MOCs/Weekly Papers MOC.md", content)

    weekly_count = sum(1 for d in digests if d["kind"] == "weekly")
    search_count = sum(1 for d in digests if d["kind"] == "search")
    logger.info(f"MOC 更新: {weekly_count} weekly + {search_count} search → {path}")
    return path


def update_translated_papers_moc() -> str:
    """掃 1 Sources/Papers Translated/,重建 3 MOCs/Translated Papers MOC.md。"""
    from vault_writer import list_vault_folder, read_from_vault, write_to_vault

    translations = []
    for filename in list_vault_folder("1 Sources/Papers Translated"):
        if filename.startswith("_"):
            continue
        try:
            content = read_from_vault(f"1 Sources/Papers Translated/{filename}")
            meta = _parse_translation_meta(content, filename)
            if meta:
                translations.append(meta)
        except Exception as e:
            logger.warning(f"讀 translation {filename} 失敗: {e}")

    # 按 generated time / filename 反序(新的在前)
    translations.sort(key=lambda t: (t.get("generated_at", ""), t["filename"]), reverse=True)

    content = _build_translated_papers_moc(translations)
    path = write_to_vault("3 MOCs/Translated Papers MOC.md", content)
    logger.info(f"Translated Papers MOC 更新: {len(translations)} 篇 → {path}")
    return path


# system tags 不建專屬 MOC(這些是元資料 tag,本身沒探索價值)
_SYSTEM_TAGS = {
    "paper", "paper-digest", "paper-translation", "moc",
    "weekly", "weekly-papers", "search",
}


def refresh_tag_mocs(min_count: int = 3) -> dict:
    """掃 Papers + Digests 統計 tag 出現次數,count ≥ min_count 的 tag
    各建 3 MOCs/Tag - X.md。回傳 {tag: count} 給 logging 用。

    現有但 count 跌破 min_count 的 Tag MOC 不會被刪(保留歷史)——
    user 想清空可以手動刪或調 min_count 重跑。
    """
    from vault_writer import list_vault_folder, read_from_vault, write_to_vault

    # 收集:每個 tag → [(kind, stem, title, link_path)]
    tag_to_items: dict[str, list] = {}

    # Papers
    for filename in list_vault_folder("1 Sources/Papers"):
        if filename.startswith("_"):
            continue
        try:
            content = read_from_vault(f"1 Sources/Papers/{filename}")
            tags = _parse_tags_from_frontmatter(content)
            title = _parse_title_from_frontmatter(content)
            stem = filename.replace(".md", "")
            for tag in tags:
                if tag in _SYSTEM_TAGS or tag.startswith("search:"):
                    continue
                tag_to_items.setdefault(tag, []).append({
                    "kind": "paper",
                    "stem": stem,
                    "title": title or stem,
                    "folder": "1 Sources/Papers",
                })
        except Exception as e:
            logger.warning(f"refresh_tag_mocs: 讀 paper {filename} 失敗: {e}")

    # Digests
    for filename in list_vault_folder("1 Sources/Digests"):
        if filename.startswith("_"):
            continue
        try:
            content = read_from_vault(f"1 Sources/Digests/{filename}")
            tags = _parse_tags_from_frontmatter(content)
            title = _parse_title_from_frontmatter(content)
            stem = filename.replace(".md", "")
            for tag in tags:
                if tag in _SYSTEM_TAGS or tag.startswith("search:"):
                    continue
                tag_to_items.setdefault(tag, []).append({
                    "kind": "digest",
                    "stem": stem,
                    "title": title or stem,
                    "folder": "1 Sources/Digests",
                })
        except Exception as e:
            logger.warning(f"refresh_tag_mocs: 讀 digest {filename} 失敗: {e}")

    eligible = {tag: items for tag, items in tag_to_items.items() if len(items) >= min_count}

    for tag, items in eligible.items():
        try:
            content = _build_tag_moc(tag, items)
            write_to_vault(f"3 MOCs/Tag - {tag}.md", content)
        except Exception as e:
            logger.warning(f"寫 Tag MOC '{tag}' 失敗: {e}")

    logger.info(
        f"Tag MOCs 更新: {len(eligible)} 個 tag 達 ≥{min_count} 門檻 "
        f"(共 {len(tag_to_items)} 個 tag 出現過)"
    )
    return {tag: len(items) for tag, items in eligible.items()}


# ═══════════════════════════════════════════════════════
# Parsing helpers
# ═══════════════════════════════════════════════════════

def _parse_digest_meta(content: str, filename: str) -> dict:
    """從 Digest frontmatter 解析 meta;同時判斷 weekly vs search digest。"""
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not fm_match:
        return None

    fm = fm_match.group(1)

    def _get(key):
        m = re.search(rf'^{key}:\s*"?(.*?)"?\s*$', fm, re.MULTILINE)
        return m.group(1).strip().strip('"') if m else ""

    week = _get("week")
    if not week:
        return None

    topic = _get("topic")
    # search digest 的 topic id 以 "search:" 開頭(由 _make_synthetic_topic 設定)
    kind = "search" if topic.startswith("search:") else "weekly"

    return {
        "filename": filename,
        "stem": filename.replace(".md", ""),
        "week": week,
        "topic": topic,
        "topic_zh": _get("topic_zh"),
        "title": _get("title"),
        "papers_count": _get("papers_count"),
        "direction_category": _get("direction_category"),
        "generated_at": _get("generated_at"),
        "kind": kind,
    }


def _parse_translation_meta(content: str, filename: str) -> dict:
    """從 Translation frontmatter 解析 meta。"""
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not fm_match:
        return None
    fm = fm_match.group(1)

    def _get(key):
        m = re.search(rf'^{key}:\s*"?(.*?)"?\s*$', fm, re.MULTILINE)
        return m.group(1).strip().strip('"') if m else ""

    arxiv_id = _get("arxiv_id")
    if not arxiv_id:
        return None

    # appeared_in 跟 paper_card 是 wikilink 格式,抽出來
    appeared_in = _get("appeared_in")
    paper_card = _get("paper_card")
    digest_link = _extract_wikilink_target(appeared_in) if appeared_in else ""
    card_link = _extract_wikilink_target(paper_card) if paper_card else ""

    return {
        "filename": filename,
        "stem": filename.replace(".md", ""),
        "arxiv_id": arxiv_id,
        "title": _get("title"),
        "title_zh": _get("title_zh"),
        "url": _get("url"),
        "char_count": _get("char_count"),
        "digest_link": digest_link,
        "card_link": card_link,
        "generated_at": _get("generated_at") or "",
    }


def _parse_tags_from_frontmatter(content: str) -> list[str]:
    """從 markdown frontmatter 解析 tags array。

    支援兩種格式:
      tags: [a, b, c]
      tags:
        - a
        - b
    """
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not fm_match:
        return []
    fm = fm_match.group(1)
    # 行內格式
    m = re.search(r"^tags:\s*\[(.*?)\]", fm, re.MULTILINE)
    if m:
        return [t.strip().strip('"').strip("'") for t in m.group(1).split(",") if t.strip()]
    # 多行格式
    m = re.search(r"^tags:\s*\n((?:\s*-\s*.+\n?)+)", fm, re.MULTILINE)
    if m:
        return [
            line.strip().lstrip("-").strip().strip('"').strip("'")
            for line in m.group(1).split("\n") if line.strip()
        ]
    return []


def _parse_title_from_frontmatter(content: str) -> str:
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not fm_match:
        return ""
    fm = fm_match.group(1)
    m = re.search(r'^title:\s*"?(.*?)"?\s*$', fm, re.MULTILINE)
    return m.group(1).strip().strip('"') if m else ""


def _extract_wikilink_target(wikilink_str: str) -> str:
    """`[[1 Sources/Digests/2026-W21_...]]` → `1 Sources/Digests/2026-W21_...`"""
    m = re.search(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", wikilink_str)
    return m.group(1).strip() if m else ""


# ═══════════════════════════════════════════════════════
# Builders
# ═══════════════════════════════════════════════════════

def _build_unified_papers_moc(digests: list[dict], all_topics: list[dict]) -> str:
    """組統一 Papers MOC:weekly + search 分段。"""
    timestamp = datetime.now().isoformat()
    weekly_digests = [d for d in digests if d["kind"] == "weekly"]
    search_digests = [d for d in digests if d["kind"] == "search"]

    parts = []
    parts.append("---")
    parts.append("type: moc")
    parts.append('title: "Weekly Papers MOC"')
    parts.append("tags: [moc, weekly-papers]")
    parts.append(f'updated_at: "{timestamp}"')
    parts.append("generated_by: weekly-paper-system")
    parts.append("---")
    parts.append("")
    parts.append("# Weekly Papers MOC")
    parts.append("")
    parts.append("Exegesis 系統產出的所有 paper digest 索引。")
    parts.append(f"📚 週期導讀 {len(weekly_digests)} 篇 · 🔍 主動搜尋導讀 {len(search_digests)} 篇")
    parts.append("")

    # ── 週期導讀(/brief)──────────────────────
    parts.append("## 📚 週期導讀(/brief)")
    parts.append("")
    if weekly_digests:
        by_year: dict[str, list] = {}
        for d in weekly_digests:
            year = d["week"][:4]
            by_year.setdefault(year, []).append(d)
        for year in sorted(by_year.keys(), reverse=True):
            parts.append(f"### {year}")
            for d in by_year[year]:
                cat_emoji = {"core": "🟢", "alt_core": "🔵", "edge": "🌐"}.get(d["direction_category"], "⚪")
                week_short = d["week"].split("-")[-1] if "-" in d["week"] else d["week"]
                topic_code = d["topic"].upper() if d["topic"] else ""
                parts.append(
                    f"- **{week_short}** [[1 Sources/Digests/{d['stem']}|{d['title']}]] — "
                    f"{topic_code} · {d['papers_count']} 篇 · {cat_emoji} {d['direction_category']}"
                )
            parts.append("")
    else:
        parts.append("- *(尚無)*")
        parts.append("")

    # ── 主動搜尋導讀(/paper)──────────────────
    parts.append("## 🔍 主動搜尋導讀(/paper)")
    parts.append("")
    if search_digests:
        cat_emoji_search = {"canonical": "🏛", "established": "🌱", "latest": "🆕"}
        for d in search_digests:
            cat_emoji = cat_emoji_search.get(d["direction_category"], "⚪")
            # search digest 的 topic_zh 通常是「主動搜尋:<query>」
            query_label = d["topic_zh"].replace("主動搜尋:", "").strip() or d["topic"]
            parts.append(
                f"- **{d['week']}** [[1 Sources/Digests/{d['stem']}|{d['title']}]] — "
                f"🔍 {query_label} · {d['papers_count']} 篇 · {cat_emoji} {d['direction_category']}"
            )
        parts.append("")
    else:
        parts.append("- *(尚無)*")
        parts.append("")

    # ── 依主題(/brief 用)──────────────────────
    parts.append("## 依主題 (/brief 主題)")
    parts.append("")
    by_topic: dict[str, list] = {}
    for d in weekly_digests:
        by_topic.setdefault(d["topic"], []).append(d)
    for t in all_topics:
        tid = t["id"]
        parts.append(f"### {t['name_zh']} ({t['code']})")
        if tid in by_topic:
            for d in by_topic[tid]:
                parts.append(f"- [[1 Sources/Digests/{d['stem']}|{d['title']}]]")
        else:
            parts.append("- *(尚未產出第一篇)*")
        parts.append("")

    # ── 依方向類別 ──────────────────────────
    parts.append("## 依方向類別")
    parts.append("")
    parts.append("### /brief 方向")
    parts.append("")
    for cat, label in [("core", "🟢 核心"), ("alt_core", "🔵 另一角度"), ("edge", "🌐 邊緣進取")]:
        parts.append(f"**{label}**")
        cat_digests = [d for d in weekly_digests if d["direction_category"] == cat]
        if cat_digests:
            for d in cat_digests:
                parts.append(f"- [[1 Sources/Digests/{d['stem']}|{d['title']}]]")
        else:
            parts.append("- *(尚無)*")
        parts.append("")
    parts.append("### /paper 角度")
    parts.append("")
    for cat, label in [("canonical", "🏛 經典"), ("established", "🌱 中堅"), ("latest", "🆕 最新")]:
        parts.append(f"**{label}**")
        cat_digests = [d for d in search_digests if d["direction_category"] == cat]
        if cat_digests:
            for d in cat_digests:
                parts.append(f"- [[1 Sources/Digests/{d['stem']}|{d['title']}]]")
        else:
            parts.append("- *(尚無)*")
        parts.append("")

    return "\n".join(parts)


def _build_translated_papers_moc(translations: list[dict]) -> str:
    """組 Translated Papers MOC。"""
    timestamp = datetime.now().isoformat()
    parts = []
    parts.append("---")
    parts.append("type: moc")
    parts.append('title: "Translated Papers MOC"')
    parts.append("tags: [moc, translated-papers]")
    parts.append(f'updated_at: "{timestamp}"')
    parts.append("generated_by: weekly-paper-system")
    parts.append("---")
    parts.append("")
    parts.append("# Translated Papers MOC")
    parts.append("")
    parts.append(f"已升級成全文中譯的 paper 索引,共 {len(translations)} 篇。")
    parts.append("")

    if not translations:
        parts.append("*(尚未升級任何 paper)*")
        return "\n".join(parts)

    parts.append("## 依時間")
    parts.append("")
    for t in translations:
        title_label = t["title_zh"] or t["title"] or t["arxiv_id"]
        arxiv_url = t["url"] or (f"https://arxiv.org/abs/{t['arxiv_id']}" if t["arxiv_id"] else "")
        line = f"- [[1 Sources/Papers Translated/{t['stem']}|{title_label}]]"
        line += f" — `{t['arxiv_id']}`"
        if t["char_count"]:
            line += f" · {t['char_count']} 字"
        if arxiv_url:
            line += f" · [原文]({arxiv_url})"
        parts.append(line)
        # 子行:卡片 + 導讀
        sub = []
        if t.get("card_link"):
            sub.append(f"[[{t['card_link']}|精華卡片]]")
        if t.get("digest_link"):
            sub.append(f"[[{t['digest_link']}|本週導讀]]")
        if sub:
            parts.append(f"   ↳ " + " · ".join(sub))
    parts.append("")

    return "\n".join(parts)


def _build_tag_moc(tag: str, items: list[dict]) -> str:
    """組單一 Tag MOC。items 是 [{kind, stem, title, folder}, ...]。"""
    timestamp = datetime.now().isoformat()
    papers = [i for i in items if i["kind"] == "paper"]
    digests = [i for i in items if i["kind"] == "digest"]

    parts = []
    parts.append("---")
    parts.append("type: moc")
    parts.append(f'title: "Tag - {tag}"')
    parts.append(f"tags: [moc, tag-index, {tag}]")
    parts.append(f'updated_at: "{timestamp}"')
    parts.append("generated_by: weekly-paper-system")
    parts.append("---")
    parts.append("")
    parts.append(f"# Tag - {tag}")
    parts.append("")
    parts.append(
        f"含 `#{tag}` 的所有 paper 與 digest。共 {len(items)} 項"
        f"(paper {len(papers)} · digest {len(digests)})。"
    )
    parts.append("")

    if digests:
        parts.append("## Digests")
        parts.append("")
        for d in digests:
            parts.append(f"- [[{d['folder']}/{d['stem']}|{d['title']}]]")
        parts.append("")

    if papers:
        parts.append("## Papers")
        parts.append("")
        for p in papers:
            parts.append(f"- [[{p['folder']}/{p['stem']}|{p['title']}]]")
        parts.append("")

    return "\n".join(parts)
