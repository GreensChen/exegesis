#!/usr/bin/env python3
"""paper_reader.py — Gemini PDF 結構化萃取。"""

import json
import logging
import os
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

GEMINI_MODEL = "gemini-3-flash-preview"

logger = logging.getLogger("paper_reader")


PAPER_READING_PROMPT = """你正在閱讀一篇學術論文 PDF，要為非該領域專家的 user 萃取結構化摘要。

要求：
- 全部繁體中文
- one_liner：< 60 字，一句話講清楚這篇論文做了什麼貢獻
- core_findings：3-5 個 bullet，每個 < 30 字，具體不空泛
- method_summary：一段，~200 字，讓 user 不點開 arXiv 也能知道方法概念
- key_results：1-3 個 bullet，**必須帶具體數據**（實驗百分比、benchmark 分數）
- limitations：1-3 個 bullet，作者承認的或審稿可能質疑的
- key_terms：本篇引入或核心使用的 3-7 個關鍵術語
  - term_en：英文原詞
  - term_zh：中文翻譯（沒共識的翻譯就直接用 term_en）
  - definition：< 50 字，該論文如何使用此術語
- connections：1-2 句，描述跟其他相關研究的關係（若論文有討論）

只回 JSON，格式如下：
{
  "one_liner": "...",
  "core_findings": ["...", "..."],
  "method_summary": "...",
  "key_results": ["...", "..."],
  "limitations": ["...", "..."],
  "key_terms": [
    {"term_en": "...", "term_zh": "...", "definition": "..."}
  ],
  "connections": "..."
}"""


def read_paper(pdf_bytes: bytes, paper_meta: dict) -> dict:
    """把一篇 paper PDF 餵給 Gemini，回傳結構化內容。"""
    if len(pdf_bytes) > 20 * 1024 * 1024:
        logger.warning(f"PDF 太大 ({len(pdf_bytes)} bytes)，fallback 用 abstract")
        return _fallback_from_abstract(paper_meta)

    from google.genai import types
    client = _get_gemini_client()

    config = types.GenerateContentConfig(
        system_instruction=PAPER_READING_PROMPT,
        max_output_tokens=8192,
        thinking_config=types.ThinkingConfig(thinking_budget=4096),
        response_mime_type="application/json",
    )

    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                    "請依以上 JSON schema 結構化萃取這篇論文。",
                ],
                config=config,
            )
            data = json.loads(resp.text)
            _validate_paper_content(data)
            logger.info(f"paper_reader: 成功萃取 {paper_meta.get('title', '')[:40]}")
            return data
        except Exception as e:
            logger.warning(f"paper_reader attempt {attempt+1} 失敗: {e}")
            if attempt < 2:
                time.sleep(3 * (attempt + 1))

    logger.error(f"paper_reader: 3 次都失敗，fallback 用 abstract")
    return _fallback_from_abstract(paper_meta)


def _validate_paper_content(data: dict) -> None:
    """驗證必要欄位存在。"""
    required = ["one_liner", "core_findings", "method_summary", "key_results", "limitations", "key_terms"]
    for key in required:
        if key not in data:
            raise ValueError(f"缺少必要欄位: {key}")


def _fallback_from_abstract(paper_meta: dict) -> dict:
    """PDF 太大或讀取失敗時的 fallback。"""
    abstract = paper_meta.get("abstract", "")
    one_liner = abstract[:60] if abstract else "(PDF too large to read)"
    return {
        "one_liner": one_liner,
        "core_findings": ["(PDF too large to read, fallback to abstract)"],
        "method_summary": abstract[:200] if abstract else "(unavailable)",
        "key_results": ["(unavailable)"],
        "limitations": ["(unavailable)"],
        "key_terms": [],
        "connections": "",
    }


def _get_gemini_client():
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("缺 GEMINI_API_KEY")
    return genai.Client(api_key=api_key)
