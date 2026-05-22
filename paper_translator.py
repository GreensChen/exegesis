#!/usr/bin/env python3
"""paper_translator.py — 全文中譯升級（by demand，Telegram /upgrade 觸發）。"""

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

GEMINI_MODEL = "gemini-3-flash-preview"

logger = logging.getLogger("paper_translator")


TRANSLATION_SYSTEM_PROMPT = """你正在把一篇學術論文從英文翻譯成繁體中文（台灣用語）。

翻譯要求：
- 全文翻譯，保留章節結構
- 學術風格但流暢可讀
- 專業名詞：首次出現中文（English），之後一律用中文
- References 翻譯但可摘要（不需逐字）
- Appendix 翻譯但可摘要
- 圖表 caption 翻，圖本身用 [圖 X：caption 中文] 占位
- 使用台灣繁體中文用語（資訊、軟體、網路）

直接輸出翻譯後的全文 markdown，不要前言、不要結語。"""


class AlreadyTranslatedError(Exception):
    pass


def translate_paper(arxiv_id: str) -> dict:
    """把指定 paper 全文翻成繁體中文。"""
    from vault_writer import list_vault_folder, read_from_vault, write_to_vault
    import requests

    paper_card = _find_paper_card(arxiv_id)
    if not paper_card:
        raise FileNotFoundError(
            f"Paper card for {arxiv_id} not found in vault. "
            f"Upgrade requires the paper to have been included in a Weekly Paper digest first."
        )

    card_filename = paper_card["filename"]
    card_content = paper_card["content"]
    card_meta = _parse_card_frontmatter(card_content)

    if card_meta.get("translated") == "true":
        raise AlreadyTranslatedError(f"{arxiv_id} 已升級過")

    pdf_url = card_meta.get("pdf_url", "")
    if not pdf_url:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

    logger.info(f"下載 PDF: {pdf_url}")
    resp = requests.get(pdf_url, timeout=120)
    resp.raise_for_status()
    pdf_bytes = resp.content

    from google.genai import types
    client = _get_gemini_client()

    config = types.GenerateContentConfig(
        system_instruction=TRANSLATION_SYSTEM_PROMPT,
        max_output_tokens=65536,
        thinking_config=types.ThinkingConfig(thinking_budget=8192),
    )

    logger.info(f"翻譯中（{len(pdf_bytes)} bytes PDF）...")
    gemini_resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            "請把這篇論文完整翻譯成繁體中文。",
        ],
        config=config,
    )
    translated_body = gemini_resp.text

    title_en = card_meta.get("title", "")
    title_zh = _extract_zh_title(translated_body, title_en)
    appeared_in = card_meta.get("appeared_in", "")
    url = card_meta.get("url", f"https://arxiv.org/abs/{arxiv_id}")
    authors = card_meta.get("authors", "[]")

    card_stem = card_filename.replace(".md", "")

    digest_stem = ""
    if appeared_in:
        m = re.search(r"\[\[1 Sources/Digests/([^\]]+)\]\]", appeared_in)
        if m:
            digest_stem = m.group(1)

    short_title_words = title_en.split()[:5]
    safe_title = re.sub(r'[/\\:*?"<>|\r\n\t]', "", " ".join(short_title_words))
    trans_filename = f"{arxiv_id} {safe_title}.md"
    trans_stem = trans_filename.replace(".md", "")

    tags_line = "tags: [paper-translation]"
    char_count = len(translated_body)

    trans_parts = []
    trans_parts.append("---")
    trans_parts.append("type: paper-translation")
    trans_parts.append(f'arxiv_id: "{arxiv_id}"')
    trans_parts.append(f'title: "{title_en}"')
    trans_parts.append(f'title_zh: "{title_zh}"')
    trans_parts.append(f"authors: {authors}")
    trans_parts.append(f'url: "{url}"')
    if digest_stem:
        trans_parts.append(f'appeared_in: "[[1 Sources/Digests/{digest_stem}]]"')
    trans_parts.append(f'paper_card: "[[1 Sources/Papers/{card_stem}]]"')
    trans_parts.append(tags_line)
    trans_parts.append(f"generated_by: {GEMINI_MODEL}")
    trans_parts.append(f"char_count: {char_count}")
    trans_parts.append("---")
    trans_parts.append("")
    trans_parts.append(f"# {title_en} / {title_zh}")
    trans_parts.append("")
    trans_parts.append(f"> 📄 [arXiv 原文]({url})")
    trans_parts.append(f"> 📰 [[1 Sources/Papers/{card_stem}|精華卡片]]")
    if digest_stem:
        trans_parts.append(f"> 📰 [[1 Sources/Digests/{digest_stem}|本週導讀]]")
    trans_parts.append("")
    trans_parts.append("---")
    trans_parts.append("")
    trans_parts.append(translated_body)

    trans_content = "\n".join(trans_parts)
    write_to_vault(f"1 Sources/Papers Translated/{trans_filename}", trans_content)
    logger.info(f"翻譯寫入: 1 Sources/Papers Translated/{trans_filename} ({char_count} 字)")

    _update_paper_card_for_translation(card_filename, card_content, trans_stem)

    return {
        "filename": trans_filename,
        "paper_card_filename": card_filename,
        "char_count": char_count,
    }


def _find_paper_card(arxiv_id: str) -> dict:
    """在 1 Sources/Papers/ 找 frontmatter arxiv_id 匹配的卡片。"""
    from vault_writer import list_vault_folder, read_from_vault

    for filename in list_vault_folder("1 Sources/Papers"):
        if filename.startswith(arxiv_id):
            content = read_from_vault(f"1 Sources/Papers/{filename}")
            return {"filename": filename, "content": content}

        try:
            content = read_from_vault(f"1 Sources/Papers/{filename}")
            if f'arxiv_id: "{arxiv_id}"' in content[:500]:
                return {"filename": filename, "content": content}
        except Exception:
            continue

    return None


def _parse_card_frontmatter(content: str) -> dict:
    """Parse Paper Card frontmatter。"""
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not fm_match:
        return {}

    fm = fm_match.group(1)
    result = {}

    for key in ["arxiv_id", "title", "url", "pdf_url", "appeared_in", "translated", "venue"]:
        m = re.search(rf'^{key}:\s*"?(.*?)"?\s*$', fm, re.MULTILINE)
        if m:
            result[key] = m.group(1).strip().strip('"')

    m = re.search(r'^authors:\s*(\[.*?\])', fm, re.MULTILINE)
    if m:
        result["authors"] = m.group(1)

    return result


def _extract_zh_title(translated_body: str, title_en: str) -> str:
    """從翻譯結果中提取中文標題。"""
    lines = translated_body.split("\n")
    for line in lines[:10]:
        line = line.strip().lstrip("#").strip()
        if line and not line.startswith(">") and len(line) < 60:
            if any('一' <= c <= '鿿' for c in line):
                return line
    return title_en


def _update_paper_card_for_translation(card_filename: str, card_content: str, trans_stem: str) -> None:
    """更新 Paper Card 的 frontmatter + 正文。"""
    from vault_writer import write_to_vault

    updated = card_content.replace("translated: false", "translated: true", 1)

    fm_end = updated.find("\n---\n", 4)
    if fm_end > 0:
        insert_pos = fm_end + 5
        body_after_fm = updated[insert_pos:]
        link_line = f"\n> 🌐 中文全譯版: [[1 Sources/Papers Translated/{trans_stem}|完整中譯]]\n"

        if link_line.strip() not in body_after_fm:
            first_newline = body_after_fm.find("\n")
            if first_newline >= 0:
                updated = updated[:insert_pos] + body_after_fm[:first_newline + 1] + link_line + body_after_fm[first_newline + 1:]
            else:
                updated = updated + link_line

    write_to_vault(f"1 Sources/Papers/{card_filename}", updated)
    logger.info(f"Paper Card 更新: translated=true, 加連結 → {card_filename}")


def _get_gemini_client():
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("缺 GEMINI_API_KEY")
    return genai.Client(api_key=api_key)
