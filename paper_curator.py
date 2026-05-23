#!/usr/bin/env python3
"""paper_curator.py — Curation Agent：從候選生成 3 個敘事方向（含邊緣進取）。"""

import json
import logging
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

GEMINI_MODEL = "gemini-3-flash-preview"

logger = logging.getLogger("paper_curator")


CURATOR_SYSTEM_PROMPT = """你是一位資深的學術編輯，專長是把當週的論文研究 curate 成有 narrative 的閱讀方向，讓非該領域專業的讀者能從中學到深度概念。

你的任務：給定本週某主題的 15-20 篇候選論文，挑出 3 個截然不同的「閱讀方向」（directions），每個方向選 3-5 篇能彼此對話的論文。

== 三個 direction 的位置 ==

1. **核心方向（category: "core"）**
   本週候選中最熱門、最重要的議題。讀者讀完應該能掌握「本週這個主題最該關注的進展是什麼」。
   選的 paper 通常是引用數高、來自 top venue、或在社群引發大量討論的。

2. **另一角度（category: "alt_core"）**
   跟核心方向**明顯不同**的子議題角度。不是核心的子集或變奏，而是同一主題下的另一個重要切面。
   例如核心方向講「LLM 推理可信度」，另一角度可以是「LLM 的多語言能力」或「LLM 應用於程式碼生成」。
   ranking 不必比 core 低，可以是同等重要但不同切角的議題。

3. **邊緣進取（category: "edge"）** ⭐ 這是關鍵位置 ⭐
   從候選中找一篇跨界到相鄰領域的 paper，讓讀者「在主題深度之外也能拓寬視野」。
   你會收到一份 edge_zones 列表（本週主題的相鄰領域），邊緣方向必須跨到列表中的某一個領域。
   例如 LLM 主題的 edge_zones 可能是 ["neuroscience", "behavioral-economics", "linguistics", "education", "ai-safety"]，你的邊緣方向就要選一篇有 LLM × 上述某領域元素的 paper。

   找不到完美跨界 paper 時的策略：
   a) 從候選中挑一個冷門但有潛力的 paper，reframe 成「從 X 角度看 Y」
   b) 即使該 paper 不是「跨界寫作」，但內容能引出跨界討論，也算合格
   c) 必須填 edge_zone 欄位，標示跨到哪個相鄰領域（從給定列表中選一個）

   若 edge_zones 是空的（系統初運行尚未生成），自由發揮：找候選中最跳脫主流的角度，edge_zone 填 null。

== 每個 direction 內部要求 ==

- **paper 之間要彼此對話**：同意、補充、或衝突。3-5 篇不是堆疊，是一場小型辯論或共同探索。
- **narrative 是 hook 而非總結**：1-2 句，讓人想點進去讀。
  - 好：「近期 3 篇研究都指向同一個問題：CoT 看起來像在思考，但實際與模型決策無關。」
  - 差：「這個方向收錄了 4 篇關於 chain-of-thought 的論文。」（這是描述，不是 hook）
- **title 是中文短句**：≤ 18 字，能立刻傳達方向。
  - 好：「推理鏈的可信度危機」、「當 LLM 學會說中文之後變笨了嗎」
  - 差：「Chain-of-Thought 相關研究」、「LLM 多語言能力探討」
- **estimated_difficulty**：
  - easy：概念導向，讀完不用懂任何技術細節
  - medium：會涉及方法層面的討論，但用比喻說得清楚
  - hard：涉及數學、實驗設計細節，需要讀者有一定背景

== 三個 direction 之間的關係 ==

- 三個方向**互斥**：讀者看到三個方向，應該能直覺判斷「啊這三個是完全不同的東西」
- 三個方向的 paper **不重疊**：同一篇 paper 不能出現在兩個方向中
- 三個方向的 difficulty 不必相同，可以是 easy/medium/hard 任意組合

== 輸出格式 ==

只回 JSON，結構如下，順序固定為 core → alt_core → edge：

{
  "directions": [
    {
      "id": "dir_1",
      "category": "core",
      "title": "中文短標題（≤18字）",
      "narrative": "一句話 hook",
      "paper_ids": ["arxiv:xxxxx", "arxiv:yyyyy", "arxiv:zzzzz"],
      "estimated_difficulty": "medium",
      "edge_zone": null
    },
    {
      "id": "dir_2",
      "category": "alt_core",
      "title": "...",
      "narrative": "...",
      "paper_ids": ["..."],
      "estimated_difficulty": "...",
      "edge_zone": null
    },
    {
      "id": "dir_3",
      "category": "edge",
      "title": "...",
      "narrative": "...",
      "paper_ids": ["..."],
      "estimated_difficulty": "...",
      "edge_zone": "neuroscience"
    }
  ]
}

不要前言、不要結語、不要 ```json``` 包裝。直接純 JSON。"""


CURATOR_USER_PROMPT_TEMPLATE = """本週主題：{topic_name_zh}（{topic_name_en}）

本週主題的相鄰領域（edge_zones，給邊緣方向選用）：
{edge_zones_str}

以下是本週 {n_candidates} 篇候選 paper，已依綜合分數排序：

{candidates_str}

請依規則 curate 3 個 directions（core / alt_core / edge 各一個）。"""


def curate_directions(
    candidates: list[dict],
    topic: dict,
    num_directions: int = 3,
    interest_model: dict = None,
) -> list[dict]:
    """用 Gemini 從候選 paper 中萃取 3 個主題敘事方向。"""
    edge_zones = get_edge_zones_for_topic(topic["id"], interest_model or {})
    if edge_zones:
        edge_zones_str = "\n".join(f"- {z}" for z in edge_zones)
    else:
        edge_zones_str = "（尚未生成，自由發揮）"

    top_candidates = candidates[:15]
    candidates_str = _render_candidates(top_candidates)

    user_prompt = CURATOR_USER_PROMPT_TEMPLATE.format(
        topic_name_zh=topic["name_zh"],
        topic_name_en=topic["name_en"],
        edge_zones_str=edge_zones_str,
        n_candidates=len(top_candidates),
        candidates_str=candidates_str,
    )

    from google.genai import types
    client = _get_gemini_client()

    config = types.GenerateContentConfig(
        system_instruction=CURATOR_SYSTEM_PROMPT,
        max_output_tokens=4096,
        thinking_config=types.ThinkingConfig(thinking_budget=4096),
        response_mime_type="application/json",
    )

    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_prompt,
            config=config,
        )
        data = json.loads(resp.text)
        directions = data.get("directions", [])
    except Exception as e:
        logger.error(f"Curation 失敗: {e}")
        directions = []

    if not _validate_directions(directions):
        logger.warning("Curation 輸出不合規，使用 fallback")
        directions = _fallback_directions(top_candidates)

    # 把 candidates 的 candidate_tags 附加到每個 direction
    candidate_map = {p["id"]: p for p in candidates}
    for d in directions:
        all_tags = set()
        for pid in d.get("paper_ids", []):
            paper = candidate_map.get(pid)
            if paper:
                all_tags.update(paper.get("candidate_tags", []))
        d["candidate_tags"] = list(all_tags)

    return directions


SEARCH_CURATOR_SYSTEM_PROMPT = """你是資深學術編輯。User 主動搜尋某個主題,
系統已用「年齡 + citation」規則把候選 paper 分成 3 個桶:

- canonical (經典,> 2 年): 領域奠基性研究或穩定成熟成果
- established (中堅,6 個月 - 2 年): 研究已成形、有時間累積引用驗證
- latest (最新,< 6 個月): 前沿動向,引用尚少但代表方向

你的任務:為每個桶寫一個敘事性 title + narrative,讓 user 能直覺判斷
「這個角度我有沒有興趣讀」。

== 寫作要求 ==

- **title (≤ 18 字繁中)**: 反映該桶 paper 的**共同主題或議題**,不是描述
  桶子本身。
  - 好:「從對話模型到通用 LLM 的奠基轉折」「RAG 系統的幻覺與抑制」
        「Transformer Attention 機制的本質爭議」
  - 差:「LaMDA 的經典研究」「最新進展」「重要 paper」(沒資訊量)
- **narrative (50-80 字繁中)**: 1-2 句 hook,描述這些 paper 共同在問什麼
  問題、彼此呼應或對比的地方。要讓 user 想點進去讀。
  - 好:「Google 從 LaMDA 到 PaLM 的對話模型路線跟 OpenAI 的 GPT 路線
        在 grounded factuality 上有根本分歧,這幾篇 paper 是辯論的根。」
  - 差:「這個桶子收錄了 X 篇 paper」(描述不是 hook)
- 不要寫「經典」「最新」「中堅」之類的字眼(那是 category label,系統會
  自動加)
- 若某桶為空陣列,該桶回 {"title": "", "narrative": ""}

== 輸出格式 ==

只回 JSON:

{
  "canonical": {"title": "...", "narrative": "..."},
  "established": {"title": "...", "narrative": "..."},
  "latest": {"title": "...", "narrative": "..."}
}

不要前言、不要結語、不要 ```json``` 包裝。"""


def curate_search_directions(query: str, buckets: dict) -> dict:
    """為 /paper 3 個年齡桶用 Gemini 一次性生成 title + narrative。

    Args:
        query: user 搜尋字串(顯示給 Gemini 當 context)
        buckets: {"canonical": [paper_dict, ...], "established": [...], "latest": [...]}

    Returns:
        {bucket_name: {"title": str, "narrative": str}}
        失敗時所有 bucket 回空 dict,讓呼叫端 fallback 到 rule-based。
    """
    # 構造 user prompt:把每個桶的 paper 標題 + abstract 摘要餵給 Gemini
    parts = [f'User 搜尋字串: "{query}"\n']
    for bucket_name in ("canonical", "established", "latest"):
        papers = buckets.get(bucket_name, [])
        parts.append(f"\n== {bucket_name} bucket ({len(papers)} papers) ==")
        if not papers:
            parts.append("(空,不需要產 title/narrative)")
            continue
        for p in papers[:5]:  # 限 5 篇,避免 prompt 過長
            cit = p.get("citation_count", 0)
            year = (p.get("published_date") or "")[:4]
            title = (p.get("title") or "")[:120]
            abstract = (p.get("abstract") or "")[:240]
            parts.append(f"- [{year} · {cit} cit] {title}")
            if abstract:
                parts.append(f"  abstract: {abstract}...")

    user_prompt = "\n".join(parts)

    try:
        from google.genai import types
        client = _get_gemini_client()
        config = types.GenerateContentConfig(
            system_instruction=SEARCH_CURATOR_SYSTEM_PROMPT,
            max_output_tokens=2048,
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            response_mime_type="application/json",
        )
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_prompt,
            config=config,
        )
        data = json.loads(resp.text)
        result = {}
        for bucket in ("canonical", "established", "latest"):
            b = data.get(bucket) or {}
            result[bucket] = {
                "title": (b.get("title") or "").strip(),
                "narrative": (b.get("narrative") or "").strip(),
            }
        logger.info(
            f"curate_search_directions: 為 '{query}' 產 "
            f"canonical/established/latest 3 個 narrative"
        )
        return result
    except Exception as e:
        logger.warning(f"curate_search_directions 失敗(fallback 用規則式): {e}")
        return {b: {"title": "", "narrative": ""} for b in ("canonical", "established", "latest")}


def get_edge_zones_for_topic(topic_id: str, interest_model: dict) -> list[str]:
    """從 interest_model 拿 edge_zones[topic_id]。"""
    zones = interest_model.get("edge_zones", {}).get(topic_id, [])
    if not zones:
        return []
    interests = interest_model.get("interests", {})
    zones_with_score = [(z, interests.get(z, {}).get("score", 0)) for z in zones]
    zones_with_score.sort(key=lambda x: -x[1])
    return [z for z, _ in zones_with_score]


def _validate_directions(directions: list[dict]) -> bool:
    """驗證 3 個 direction 的 category 齊全。"""
    if len(directions) != 3:
        return False
    categories = {d.get("category") for d in directions}
    return categories == {"core", "alt_core", "edge"}


def _fallback_directions(candidates: list[dict]) -> list[dict]:
    """失敗 fallback：用 top 5 paper 包成單一 core direction。"""
    top5 = candidates[:5]
    return [{
        "id": "dir_1",
        "title": "本週精選",
        "narrative": f"本週 {len(top5)} 篇高分候選論文。",
        "paper_ids": [p["id"] for p in top5],
        "estimated_difficulty": "medium",
        "category": "core",
        "edge_zone": None,
    }]


def _render_candidates(candidates: list[dict]) -> str:
    """把候選 paper 渲染成 Gemini 看得懂的文字。"""
    parts = []
    for i, p in enumerate(candidates, 1):
        venue = p.get("venue") or "arXiv preprint"
        abstract = p.get("abstract", "")
        if len(abstract) > 500:
            abstract = abstract[:500] + "..."
        authors = ", ".join(p.get("authors", [])[:3])
        if len(p.get("authors", [])) > 3:
            authors += ", et al."

        parts.append(
            f"[{i}] {p['id']} (final_score={p['scores']['final']:.2f})\n"
            f"Title: {p['title']}\n"
            f"Authors: {authors}\n"
            f"Published: {p['published_date']} | Venue: {venue} | Citations: {p.get('citation_count', 0)}\n"
            f"Abstract: {abstract}"
        )
    return "\n\n".join(parts)


def _get_gemini_client():
    from google import genai
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("缺 GEMINI_API_KEY")
    return genai.Client(api_key=api_key)
