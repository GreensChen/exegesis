#!/usr/bin/env python3
"""paper_discovery.py — arXiv + Semantic Scholar 抓取與排序（納入興趣模型加權）。"""

import difflib
import logging
import math
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

logger = logging.getLogger("paper_discovery")

ARXIV_CATEGORY_TAG_MAP = {
    "cs.CL": "nlp",
    "cs.AI": "artificial-intelligence",
    "cs.LG": "machine-learning",
    "cs.CV": "computer-vision",
    "cs.IR": "information-retrieval",
    "cs.SE": "software-engineering",
    "cs.CR": "cybersecurity",
    "cs.RO": "robotics",
    "stat.ML": "machine-learning",
    "q-bio.NC": "neuroscience",
    "econ.GN": "economics",
    "econ.TH": "economics",
}


def discover_papers(
    topic: dict,
    days_back: int = 14,
    limit: int = 25,
    interest_model: dict = None,
) -> list[dict]:
    """回傳排序後的 paper candidate 列表。"""
    now = datetime.now()
    since = now - timedelta(days=days_back)

    arxiv_papers = _fetch_arxiv(topic, since, now)
    ss_papers = _fetch_semantic_scholar(topic, since, now)

    all_papers = arxiv_papers + ss_papers
    deduped = _dedupe(all_papers)

    for p in deduped:
        p["candidate_tags"] = _generate_candidate_tags(p)

    for p in deduped:
        p["scores"] = _score(p, topic, now, interest_model)

    deduped.sort(key=lambda p: -p["scores"]["final"])
    result = deduped[:limit]
    for i, p in enumerate(result, 1):
        p["rank"] = i

    logger.info(f"discovery: {len(arxiv_papers)} arxiv + {len(ss_papers)} ss → {len(deduped)} deduped → top {len(result)}")
    return result


def _fetch_arxiv(topic: dict, since: datetime, now: datetime) -> list[dict]:
    """Weekly Paper 用：依 topic 的 categories / keywords 抓 arXiv。"""
    categories = topic.get("arxiv_categories", [])
    keywords = topic.get("arxiv_keywords", [])
    if not categories and not keywords:
        return []

    query_parts = [f"cat:{c}" for c in categories]
    query = " OR ".join(query_parts)
    if keywords and not query_parts:
        query = " OR ".join(f'"{kw}"' for kw in keywords)

    return _fetch_arxiv_query(query, since=since, max_results=50)


def _fetch_arxiv_query(
    query: str,
    since: datetime = None,
    max_results: int = 50,
) -> list[dict]:
    """主動查詢用：接受任意 arxiv query 字串、可選日期下限。

    since=None 表示不限時間範圍（適合 /paper LaMDA 查經典 paper）。
    """
    try:
        import arxiv
    except ImportError:
        logger.warning("arxiv 套件未安裝，跳過 arXiv 來源")
        return []

    try:
        client = arxiv.Client(page_size=max_results, delay_seconds=3, num_retries=4)
        # 主動查詢用 relevance 排序（拿到經典 + 高引用）；topic 模式用 submitted-date
        sort_by = arxiv.SortCriterion.Relevance if since is None else arxiv.SortCriterion.SubmittedDate
        search = arxiv.Search(
            query=query,
            sort_by=sort_by,
            sort_order=arxiv.SortOrder.Descending,
            max_results=max_results,
        )

        papers = []
        for result in client.results(search):
            effective_date = max(result.published, result.updated)
            if since is not None and effective_date.replace(tzinfo=None) < since:
                continue

            arxiv_id = re.search(r"(\d+\.\d+)", result.entry_id)
            arxiv_id_str = arxiv_id.group(1) if arxiv_id else ""

            papers.append({
                "id": f"arxiv:{arxiv_id_str}",
                "source": "arxiv",
                "title": result.title.replace("\n", " ").strip(),
                "authors": [a.name for a in result.authors[:10]],
                "abstract": result.summary.replace("\n", " ").strip(),
                "published_date": effective_date.strftime("%Y-%m-%d"),
                "categories": [c for c in result.categories],
                "pdf_url": result.pdf_url,
                "external_ids": {"arxiv": arxiv_id_str, "DOI": None},
                "citation_count": 0,
                "venue": None,
            })

        logger.info(f"arXiv query={query[:50]!r}: {len(papers)} 篇")
        return papers

    except Exception as e:
        logger.error(f"arXiv 抓取失敗: {e}")
        return []


def _fetch_semantic_scholar(topic: dict, since: datetime, now: datetime) -> list[dict]:
    """Weekly Paper 用：依 topic.semantic_scholar_query + 日期範圍抓。"""
    query = topic.get("semantic_scholar_query")
    if not query:
        return []
    return _fetch_semantic_scholar_query(query, since=since, now=now, max_results=50)


def _fetch_semantic_scholar_query(
    query: str,
    since: datetime = None,
    now: datetime = None,
    max_results: int = 50,
) -> list[dict]:
    """主動查詢用：任意 Semantic Scholar query 字串，可選日期範圍。

    since=None：用 /paper/search 端點（按 relevance ranking、≤ 100 篇），
                適合查詢經典 paper 的場景。
    since=有值：用 /paper/search/bulk 端點（按 paperId、≤ 1000 篇），
                適合 Weekly Paper 抓某時段內所有 paper。
    """
    import requests

    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    params = {
        "query": query,
        "fields": "paperId,title,abstract,authors,year,publicationDate,citationCount,venue,externalIds,openAccessPdf",
        "limit": max_results,
    }
    if since is not None:
        endpoint = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
        since_str = since.strftime("%Y-%m-%d")
        until_str = (now or datetime.now()).strftime("%Y-%m-%d")
        params["publicationDateOrYear"] = f"{since_str}:{until_str}"
    else:
        endpoint = "https://api.semanticscholar.org/graph/v1/paper/search"
        params["limit"] = min(max_results, 100)

    try:
        # 強化 retry：429 / 5xx 用 exp backoff 重試 3 次（5s, 15s, 45s）
        # SS 沒 API key 時限流很兇,3 次 backoff 大約能熬過短暫的 burst limit
        backoffs = [5, 15, 45]
        resp = None
        for attempt, wait in enumerate(backoffs + [0]):
            resp = requests.get(
                endpoint,
                params=params,
                headers=headers,
                timeout=30,
            )
            if resp.status_code not in (429, 500, 502, 503, 504):
                break
            if attempt >= len(backoffs):
                break  # 用完 retries
            logger.warning(
                f"Semantic Scholar {resp.status_code}, retry {attempt + 1}/{len(backoffs)} in {wait}s..."
            )
            time.sleep(wait)
        resp.raise_for_status()
        data = resp.json()

        papers = []
        for item in data.get("data", []):
            if not item.get("abstract"):
                continue

            external_ids = item.get("externalIds") or {}
            arxiv_id = external_ids.get("ArXiv", "")
            doi = external_ids.get("DOI")

            pdf_url = None
            oap = item.get("openAccessPdf")
            if oap and oap.get("url"):
                pdf_url = oap["url"]
            elif arxiv_id:
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

            authors = [a.get("name", "") for a in (item.get("authors") or [])[:10]]

            papers.append({
                "id": f"ss:{item['paperId']}",
                "source": "semantic_scholar",
                "title": (item.get("title") or "").replace("\n", " ").strip(),
                "authors": authors,
                "abstract": (item.get("abstract") or "").replace("\n", " ").strip(),
                "published_date": item.get("publicationDate") or f"{item.get('year', 2026)}-01-01",
                "categories": [],
                "pdf_url": pdf_url,
                "external_ids": {"arxiv": arxiv_id, "DOI": doi},
                "citation_count": item.get("citationCount") or 0,
                "venue": item.get("venue") or None,
            })

        logger.info(f"Semantic Scholar query={query[:50]!r}: {len(papers)} 篇")
        return papers

    except Exception as e:
        logger.error(f"Semantic Scholar 抓取失敗: {e}")
        return []


def _fetch_ss_match(query: str) -> list[dict]:
    """SS /paper/search/match —— 精準 lookup canonical paper（給定 title-like query 找最匹配的一篇）。

    對 /paper LaMDA 這種「找知名論文」的場景特別好用——/paper/search 用相關度
    排序、有時 LaMDA 排不到前 5；match 端點專做「title 模糊比對」、就是要回那篇
    canonical 的。回傳 0 或 1 篇。

    限流策略：失敗 silent，因為這只是 enrichment、不是主要來源。
    """
    if not query or len(query.strip()) < 2:
        return []
    import requests

    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    params = {
        "query": query.strip(),
        "fields": "paperId,title,abstract,authors,year,publicationDate,citationCount,venue,externalIds,openAccessPdf",
    }
    endpoint = "https://api.semanticscholar.org/graph/v1/paper/search/match"

    try:
        backoffs = [5, 15]
        resp = None
        for attempt, wait in enumerate(backoffs + [0]):
            resp = requests.get(endpoint, params=params, headers=headers, timeout=15)
            if resp.status_code not in (429, 500, 502, 503, 504):
                break
            if attempt >= len(backoffs):
                break
            logger.warning(
                f"SS match {resp.status_code}, retry {attempt + 1}/{len(backoffs)} in {wait}s..."
            )
            time.sleep(wait)
        if resp.status_code == 404:
            # match 找不到 canonical paper 是正常情況
            logger.info(f"SS match query={query[:50]!r}: no canonical paper")
            return []
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])
        if not items:
            return []

        papers = []
        for item in items:
            if not item.get("abstract"):
                continue
            external_ids = item.get("externalIds") or {}
            arxiv_id = external_ids.get("ArXiv", "")
            doi = external_ids.get("DOI")
            pdf_url = None
            oap = item.get("openAccessPdf")
            if oap and oap.get("url"):
                pdf_url = oap["url"]
            elif arxiv_id:
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            authors = [a.get("name", "") for a in (item.get("authors") or [])[:10]]
            papers.append({
                "id": f"ss:{item['paperId']}",
                "source": "semantic_scholar_match",
                "title": (item.get("title") or "").replace("\n", " ").strip(),
                "authors": authors,
                "abstract": (item.get("abstract") or "").replace("\n", " ").strip(),
                "published_date": item.get("publicationDate") or f"{item.get('year', 2026)}-01-01",
                "categories": [],
                "pdf_url": pdf_url,
                "external_ids": {"arxiv": arxiv_id, "DOI": doi},
                "citation_count": item.get("citationCount") or 0,
                "venue": item.get("venue") or None,
            })
        logger.info(f"SS match query={query[:50]!r}: {len(papers)} canonical")
        return papers
    except Exception as e:
        logger.warning(f"SS match 失敗（不影響其他來源）: {e}")
        return []


def _dedupe(papers: list[dict]) -> list[dict]:
    """去重。"""
    seen_arxiv: dict[str, dict] = {}
    seen_doi: dict[str, dict] = {}
    unique = []

    for p in papers:
        arxiv_id = p["external_ids"].get("arxiv")
        doi = p["external_ids"].get("DOI")

        if arxiv_id and arxiv_id in seen_arxiv:
            existing = seen_arxiv[arxiv_id]
            if p["source"] == "arxiv":
                unique.remove(existing)
                unique.append(p)
                seen_arxiv[arxiv_id] = p
            continue

        if doi and doi in seen_doi:
            existing = seen_doi[doi]
            if p["citation_count"] > existing["citation_count"]:
                unique.remove(existing)
                unique.append(p)
                seen_doi[doi] = p
            continue

        is_dup = False
        for existing in unique:
            ratio = difflib.SequenceMatcher(None, p["title"].lower(), existing["title"].lower()).ratio()
            if ratio > 0.92:
                if p["citation_count"] > existing["citation_count"]:
                    unique.remove(existing)
                    unique.append(p)
                is_dup = True
                break

        if not is_dup:
            unique.append(p)
            if arxiv_id:
                seen_arxiv[arxiv_id] = p
            if doi:
                seen_doi[doi] = p

    return unique


def _generate_candidate_tags(paper: dict) -> list[str]:
    """從 arxiv categories + abstract 推粗略 tag。"""
    tags = set()

    for cat in paper.get("categories", []):
        mapped = ARXIV_CATEGORY_TAG_MAP.get(cat)
        if mapped:
            tags.add(mapped)

    abstract_lower = paper.get("abstract", "").lower()
    tech_terms = [
        ("chain-of-thought", "chain-of-thought"),
        ("reasoning", "reasoning"),
        ("retrieval-augmented", "retrieval-augmented-generation"),
        ("rag", "retrieval-augmented-generation"),
        ("reinforcement learning", "reinforcement-learning"),
        ("fine-tuning", "fine-tuning"),
        ("alignment", "ai-alignment"),
        ("safety", "ai-safety"),
        ("multimodal", "multimodal"),
        ("vision", "computer-vision"),
        ("code generation", "code-generation"),
        ("agent", "agentic-engineering"),
        ("benchmark", "benchmarking"),
        ("attention", "attention-mechanism"),
        ("transformer", "transformer"),
        ("diffusion", "diffusion-models"),
        ("prompt", "prompting"),
        ("in-context learning", "in-context-learning"),
        ("evaluation", "evaluation"),
    ]
    for keyword, tag in tech_terms:
        if keyword in abstract_lower:
            tags.add(tag)

    return list(tags)


def search_papers(
    query: str,
    limit: int = 5,
    interest_model: dict = None,
) -> dict:
    """主動查詢 paper（無 topic、無時間限制）。

    跟 discover_papers 不同：
      - 接受 free-form query 字串
      - 不限時間範圍（撈到 LaMDA 2022 等經典 paper）
      - 用 search-mode scoring（少 recency、重 citation/keyword/title-match）

    三個來源並列：
      - arXiv relevance search（max 30）
      - Semantic Scholar /paper/search（max 30，相關度排序）
      - Semantic Scholar /paper/search/match（top 1，canonical 精準 lookup）

    回傳：
      {
        "papers": list[dict]，排序後的 top N，
        "source_counts": {"arxiv": int, "ss_search": int, "ss_match": int},
      }
    """
    if not query or not query.strip():
        return {"papers": [], "source_counts": {"arxiv": 0, "ss_search": 0, "ss_match": 0}}
    query = query.strip()
    now = datetime.now()

    # arXiv：短 query（≤ 3 詞）優先 title 比對，否則 all-fields
    has_op = any(op in query for op in (":", " AND ", " OR "))
    if has_op:
        arxiv_query = query
    elif len(query.split()) <= 3:
        # ti 提高 title 比對權重，all 還原候補
        arxiv_query = f'ti:"{query}" OR all:"{query}"'
    else:
        arxiv_query = f'all:"{query}"'
    arxiv_papers = _fetch_arxiv_query(arxiv_query, since=None, max_results=30)

    # SS broad relevance search
    ss_papers = _fetch_semantic_scholar_query(query, since=None, now=None, max_results=30)

    # SS canonical match（補 LaMDA 這種知名 paper）
    ss_match_papers = _fetch_ss_match(query)

    deduped = _dedupe(arxiv_papers + ss_papers + ss_match_papers)

    for p in deduped:
        p["candidate_tags"] = _generate_candidate_tags(p)
        p["scores"] = _score_search(p, query, now, interest_model)

    deduped.sort(key=lambda p: -p["scores"]["final"])
    result = deduped[:limit]
    for i, p in enumerate(result, 1):
        p["rank"] = i

    logger.info(
        f"search '{query}': {len(arxiv_papers)} arxiv + {len(ss_papers)} ss + "
        f"{len(ss_match_papers)} ss_match → {len(deduped)} deduped → top {len(result)}"
    )
    return {
        "papers": result,
        "source_counts": {
            "arxiv": len(arxiv_papers),
            "ss_search": len(ss_papers),
            "ss_match": len(ss_match_papers),
        },
    }


def _score_search(paper: dict, query: str, now: datetime, interest_model: dict = None) -> dict:
    """主動查詢專用 scoring：少 recency、重 citation + keyword match。
    這樣經典 paper（如 LaMDA 2022）不會被 2 週內的小 preprint 蓋過。
    """
    try:
        pub_date = datetime.strptime(paper["published_date"][:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        pub_date = now
    days_since = max((now - pub_date).days, 0)

    # 老 paper 不扣分，新 paper 給小幅 bonus
    recency = math.exp(-days_since / 365)  # 一年才衰減一半

    # Citation 總量（不除以天數，避免老 paper 被低估）
    citation_total = min(paper.get("citation_count", 0) / 1000.0, 1.0)

    top_venues = {"NeurIPS", "ICML", "ICLR", "ACL", "EMNLP", "NAACL", "Nature", "Science"}
    venue = paper.get("venue")
    venue_quality = 1.0 if venue in top_venues else (0.5 if venue else 0.3)

    # Query keyword match（split on whitespace）
    title_lower = paper.get("title", "").lower()
    abstract_lower = paper.get("abstract", "").lower()
    query_lower = query.lower().strip()
    km = 0.0
    for kw in query_lower.split():
        if len(kw) < 2:
            continue
        if kw in title_lower:
            km += 0.4
        if kw in abstract_lower:
            km += 0.1
    keyword_match = min(km, 1.0)

    # Title exact match bonus —— 整個 query 出現在 title 是強訊號
    # 對 /paper LaMDA 這類找 canonical paper 的查詢特別有用
    title_exact = 0.0
    if query_lower and len(query_lower) >= 3 and query_lower in title_lower:
        # 額外加權當 title 短(query 佔 title 大部分)時更強
        coverage = len(query_lower) / max(len(title_lower), 1)
        title_exact = 0.7 + 0.3 * min(coverage * 3, 1.0)  # 0.7-1.0

    # SS match 來源本身就是 canonical lookup，給小幅信任 bonus
    source_bonus = 0.0
    if paper.get("source") == "semantic_scholar_match":
        source_bonus = 0.3

    interest_boost = 0.0
    if interest_model and paper.get("candidate_tags"):
        boost_sum = 0.0
        hit_count = 0
        for tag in paper["candidate_tags"]:
            t = interest_model.get("interests", {}).get(tag)
            if t:
                boost_sum += min(t["score"] / 10.0, 1.0)
                hit_count += 1
        interest_boost = boost_sum / max(hit_count, 1)

    final = (
        0.05 * recency +
        0.25 * citation_total +
        0.10 * venue_quality +
        0.20 * keyword_match +
        0.25 * title_exact +
        0.05 * source_bonus +
        0.10 * interest_boost
    )

    return {
        "recency": round(recency, 3),
        "citation_total": round(citation_total, 3),
        "venue_quality": round(venue_quality, 3),
        "keyword_match": round(keyword_match, 3),
        "title_exact": round(title_exact, 3),
        "source_bonus": round(source_bonus, 3),
        "interest_boost": round(interest_boost, 3),
        "final": round(final, 3),
    }


def _score(paper: dict, topic: dict, now: datetime, interest_model: dict = None) -> dict:
    """評分。"""
    try:
        pub_date = datetime.strptime(paper["published_date"][:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        pub_date = now
    days_since = max((now - pub_date).days, 0)

    recency = math.exp(-days_since / 10)

    cv_raw = paper.get("citation_count", 0) / max(days_since, 1)
    citation_velocity = min(cv_raw / 1.0, 1.0)

    top_venues = {"NeurIPS", "ICML", "ICLR", "ACL", "EMNLP", "NAACL", "Nature", "Science"}
    venue = paper.get("venue")
    if venue in top_venues:
        venue_quality = 1.0
    elif venue:
        venue_quality = 0.5
    else:
        venue_quality = 0.3

    text = (paper.get("title", "") + " " + paper.get("abstract", "")).lower()
    km = 0.0
    for kw in topic.get("arxiv_keywords", []):
        if kw.lower() in paper.get("title", "").lower():
            km += 0.3
        if kw.lower() in paper.get("abstract", "").lower():
            km += 0.1
    keyword_match = min(km, 1.0)

    interest_boost = 0.0
    if interest_model and paper.get("candidate_tags"):
        boost_sum = 0.0
        hit_count = 0
        for tag in paper["candidate_tags"]:
            t = interest_model.get("interests", {}).get(tag)
            if t:
                boost_sum += min(t["score"] / 10.0, 1.0)
                hit_count += 1
        interest_boost = boost_sum / max(hit_count, 1)

    final = (
        0.35 * recency +
        0.25 * citation_velocity +
        0.10 * venue_quality +
        0.15 * keyword_match +
        0.15 * interest_boost
    )

    return {
        "recency": round(recency, 3),
        "citation_velocity": round(citation_velocity, 3),
        "venue_quality": round(venue_quality, 3),
        "keyword_match": round(keyword_match, 3),
        "interest_boost": round(interest_boost, 3),
        "final": round(final, 3),
    }
