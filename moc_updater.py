#!/usr/bin/env python3
"""moc_updater.py — Weekly Papers MOC 維護。"""

import logging
import re
from datetime import datetime

logger = logging.getLogger("moc_updater")


def update_moc() -> str:
    """重新生成 3 MOCs/Weekly Papers MOC.md。"""
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

    digests.sort(key=lambda d: d["week"], reverse=True)

    topics_cfg = read_state_json("config/topics.json")
    all_topics = topics_cfg.get("topics", [])

    content = _build_moc(digests, all_topics)
    path = write_to_vault("3 MOCs/Weekly Papers MOC.md", content)
    logger.info(f"MOC 更新: {len(digests)} digests → {path}")
    return path


def _parse_digest_meta(content: str, filename: str) -> dict:
    """從 Digest frontmatter 解析 meta。"""
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

    return {
        "filename": filename,
        "stem": filename.replace(".md", ""),
        "week": week,
        "topic": _get("topic"),
        "topic_zh": _get("topic_zh"),
        "title": _get("title"),
        "papers_count": _get("papers_count"),
        "direction_category": _get("direction_category"),
    }


def _build_moc(digests: list[dict], all_topics: list[dict]) -> str:
    """組 MOC markdown。"""
    timestamp = datetime.now().isoformat()

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
    parts.append("每週一篇主題論文導讀的索引。")
    parts.append("")

    # 依時間
    parts.append("## 依時間")
    parts.append("")
    by_year: dict[str, list] = {}
    for d in digests:
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

    # 依主題
    parts.append("## 依主題")
    parts.append("")
    topic_map = {t["id"]: t for t in all_topics}
    by_topic: dict[str, list] = {}
    for d in digests:
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

    # 依方向類別
    parts.append("## 依方向類別")
    parts.append("")
    for cat, label in [("core", "核心 (core)"), ("alt_core", "另一角度 (alt_core)"), ("edge", "邊緣進取 (edge)")]:
        parts.append(f"### {label}")
        cat_digests = [d for d in digests if d["direction_category"] == cat]
        if cat_digests:
            for d in cat_digests:
                parts.append(f"- [[1 Sources/Digests/{d['stem']}|{d['title']}]]")
        else:
            parts.append("- *(尚無)*")
        parts.append("")

    # 待補主題
    parts.append("## 待補主題")
    parts.append("")
    disabled = [t for t in all_topics if not t.get("enabled")]
    if disabled:
        for t in disabled:
            parts.append(f"- {t['name_zh']} ({t['code']})")
    else:
        parts.append("- *(所有主題都已啟用)*")
    parts.append("")

    return "\n".join(parts)
