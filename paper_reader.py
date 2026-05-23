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


PAPER_READING_PROMPT = """你正在閱讀一篇學術論文 PDF,要為非該領域專家的 user 萃取**深度**結構化內容。

下游的 digest writer 會用你抽取的內容寫 1500-2000 字/篇的深度導讀,所以
你必須提供豐富的 raw material——**寧可詳細冗餘,不要精簡乾澀**。空白
section 或一句話帶過會直接讓 digest 變淺。

要求:
- 全部繁體中文(專業術語第一次出現用「中文(English)」格式)
- **one_liner**: < 60 字,一句話講清楚這篇論文做了什麼貢獻
- **author_context**: 100-200 字。作者/機構背景 + 這篇 paper 的動機。
  例:「來自 DeepMind dialog team,跟過去的 Meena 是同一系。本文要回應
  的是『大規模對話模型 hallucination 嚴重』這個問題,前作 Meena 強調
  自然性,本文要把焦點轉到 grounded factuality。」
- **core_findings**: 3-6 個 bullet,每個 30-80 字。**具體可引用**——
  不要寫「方法有效」,要寫「在 X benchmark 上對 Y baseline 提升 N%」
- **method_summary**: **400-600 字段落**(這個關鍵,要詳細)。
  必須包含:
    a) 整體方法輪廓(架構/演算法核心想法)
    b) 關鍵設計決策的 trade-off(為什麼選 A 不選 B)
    c) 訓練 / 評估 setup(資料量、模型規模、baseline、metric)
    d) 跟 baseline 方法的具體差異點
- **key_results**: 3-5 個 bullet,每個 50-100 字。
  必須包含:benchmark 名稱 + baseline + 我方 + 改進幅度 + 樣本/實驗條件。
  例:「在 MMLU 上 5-shot 從 Llama-3-70B 的 79.5% 提升到 82.1%
  (+2.6pp),但在 HumanEval 上只小幅提升(+0.8pp),作者解釋為
  程式推理涉及 token-level distillation 訊號弱」
- **ablation_insights**: 100-300 字。作者的 ablation study 或 setup
  variation 揭露了什麼?哪個元件最關鍵?哪個其實不重要?
  如果 paper 沒做 ablation,填「(本文無 ablation study,主要結果為 end-to-end 對比)」。
- **limitations**: 2-4 個 bullet。**作者承認的 + 你讀完隱含發現的**。
  每個 50-100 字,要具體到 setup 限制(例:「只在英文 benchmark 測,
  多語言泛化未知」「樣本只有 8k,跟主流 RLHF 規模 100k+ 有差距」)。
- **paper_position**: 100-200 字。這篇 paper 在領域研究脈絡的位置。
  - 它接續了誰的工作?(若有提到 prior art)
  - 它挑戰了哪個既有假設?
  - 它開啟了什麼下一步可能?
- **key_terms**: 本篇引入或核心使用的 4-8 個關鍵術語
  - term_en: 英文原詞
  - term_zh: 中文翻譯(沒共識的翻譯就直接用 term_en)
  - definition: 50-100 字,該論文如何使用此術語(不是 wikipedia 通用定義,
    是「在這篇 paper 的脈絡下這個詞指什麼」)
- **connections**: 2-3 句,描述跟其他相關研究的關係(同流派、競爭、衍生)

只回 JSON,格式如下:
{
  "one_liner": "...",
  "author_context": "...",
  "core_findings": ["...", "..."],
  "method_summary": "...",
  "key_results": ["...", "..."],
  "ablation_insights": "...",
  "limitations": ["...", "..."],
  "paper_position": "...",
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
        max_output_tokens=16384,
        thinking_config=types.ThinkingConfig(thinking_budget=6144),
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
        "author_context": "(PDF 讀取失敗,無法獲取作者背景)",
        "core_findings": ["(PDF 讀取失敗,以 abstract 為主)"],
        "method_summary": abstract[:400] if abstract else "(unavailable)",
        "key_results": ["(unavailable)"],
        "ablation_insights": "(PDF 讀取失敗,無 ablation 資料)",
        "limitations": ["(unavailable)"],
        "paper_position": "(PDF 讀取失敗,無 context 資料)",
        "key_terms": [],
        "connections": "",
    }


def _get_gemini_client():
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("缺 GEMINI_API_KEY")
    return genai.Client(api_key=api_key)
