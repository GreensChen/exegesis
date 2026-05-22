#!/usr/bin/env python3
"""init_exegesis.py — 首次部署 script（建目錄 + bootstrap 興趣模型 + Pharos hash + Anamnesis API hash）。

用法：
    python3 init_exegesis.py              # 正式 bootstrap
    python3 init_exegesis.py --dry-run    # 不寫檔，只印結果
    python3 init_exegesis.py --re-bootstrap  # 保留互動訊號，重算 article_frequency + edge_zones
    python3 init_exegesis.py --refresh-pharos-hash    # 重算 Pharos EPUB_CSS hash
    python3 init_exegesis.py --refresh-anamnesis-hash # 重算 Anamnesis API 簽名 hash
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("init_exegesis")


INITIAL_TOPICS = {
    "version": 1,
    "rotation_strategy": "longest_unused",
    "topics": [
        {
            "id": "llm",
            "code": "LLM",
            "name_zh": "大型語言模型",
            "name_en": "Large Language Models",
            "enabled": True,
            "priority": 1.0,
            "arxiv_categories": ["cs.CL"],
            "arxiv_keywords": ["large language model", "LLM", "transformer"],
            "semantic_scholar_query": "large language models",
            "semantic_scholar_fields": ["Computer Science"],
            "last_published_week": None,
            "pair_with": None,
        },
        {
            "id": "brain-science",
            "code": "BRAIN",
            "name_zh": "大腦科學",
            "name_en": "Neuroscience",
            "enabled": False,
            "priority": 1.0,
            "arxiv_categories": ["q-bio.NC"],
            "arxiv_keywords": ["neuroscience", "cognition", "neural"],
            "semantic_scholar_query": "cognitive neuroscience",
            "semantic_scholar_fields": ["Neuroscience", "Psychology"],
            "last_published_week": None,
            "pair_with": "behavioral-psychology",
        },
        {
            "id": "behavioral-psychology",
            "code": "PSY",
            "name_zh": "行為心理學",
            "name_en": "Behavioral Psychology",
            "enabled": False,
            "priority": 1.0,
            "arxiv_categories": [],
            "arxiv_keywords": [],
            "semantic_scholar_query": "behavioral psychology decision making cognitive bias",
            "semantic_scholar_fields": ["Psychology"],
            "last_published_week": None,
            "pair_with": "brain-science",
        },
        {
            "id": "behavioral-economics",
            "code": "BEHAV",
            "name_zh": "行為經濟學",
            "name_en": "Behavioral Economics",
            "enabled": False,
            "priority": 1.0,
            "arxiv_categories": ["econ.GN"],
            "arxiv_keywords": ["behavioral economics", "nudge", "prospect theory"],
            "semantic_scholar_query": "behavioral economics",
            "semantic_scholar_fields": ["Economics"],
            "last_published_week": None,
            "pair_with": None,
        },
        {
            "id": "economics",
            "code": "ECON",
            "name_zh": "經濟學",
            "name_en": "Economics",
            "enabled": False,
            "priority": 1.0,
            "arxiv_categories": ["econ.GN", "econ.TH"],
            "arxiv_keywords": ["macroeconomics", "monetary policy"],
            "semantic_scholar_query": "macroeconomics monetary policy",
            "semantic_scholar_fields": ["Economics"],
            "last_published_week": None,
            "pair_with": None,
        },
        {
            "id": "ai",
            "code": "AI",
            "name_zh": "AI 科技",
            "name_en": "AI Technology",
            "enabled": False,
            "priority": 1.0,
            "arxiv_categories": ["cs.AI"],
            "arxiv_keywords": ["AI applications", "AI systems"],
            "semantic_scholar_query": "artificial intelligence systems",
            "semantic_scholar_fields": ["Computer Science"],
            "last_published_week": None,
            "pair_with": None,
        },
        {
            "id": "ml",
            "code": "ML",
            "name_zh": "機器學習",
            "name_en": "Machine Learning",
            "enabled": False,
            "priority": 1.0,
            "arxiv_categories": ["cs.LG"],
            "arxiv_keywords": ["machine learning", "deep learning"],
            "semantic_scholar_query": "machine learning",
            "semantic_scholar_fields": ["Computer Science"],
            "last_published_week": None,
            "pair_with": None,
        },
    ],
}


def _create_directories(dry_run: bool) -> None:
    """建立 Dropbox 目錄結構。"""
    from vault_writer import write_to_vault, write_state_json

    vault_folders = [
        "1 Sources/Digests",
        "1 Sources/Papers",
        "1 Sources/Papers Translated",
    ]
    placeholder = "---\ntype: index\n---\n\n此資料夾由 Weekly Paper 系統自動建立。\n"

    for folder in vault_folders:
        index_path = f"{folder}/_index.md"
        if dry_run:
            logger.info(f"[DRY-RUN] 會建立: vault/{index_path}")
        else:
            try:
                write_to_vault(index_path, placeholder)
                logger.info(f"✅ 建立: vault/{index_path}")
            except Exception as e:
                logger.warning(f"⚠️ 建立 {index_path} 失敗（可能已存在）: {e}")

    state_dirs = ["config", "state", "archive"]
    for d in state_dirs:
        path = f"{d}/.keep"
        if dry_run:
            logger.info(f"[DRY-RUN] 會建立: WeeklyPaperState/{path}")
        else:
            try:
                write_state_json(path, {"created": datetime.now().isoformat()})
                logger.info(f"✅ 建立: WeeklyPaperState/{path}")
            except Exception as e:
                logger.warning(f"⚠️ 建立 {path} 失敗: {e}")


def _write_topics_config(dry_run: bool) -> None:
    """寫入初始 topics.json。"""
    from vault_writer import write_state_json, read_state_json

    existing = read_state_json("config/topics.json")
    if existing and existing.get("version"):
        logger.info("topics.json 已存在，跳過")
        return

    if dry_run:
        logger.info(f"[DRY-RUN] 會寫入 topics.json，{len(INITIAL_TOPICS['topics'])} 個主題")
    else:
        write_state_json("config/topics.json", INITIAL_TOPICS)
        logger.info(f"✅ 寫入 topics.json，{len(INITIAL_TOPICS['topics'])} 個主題")


def _bootstrap_interest(dry_run: bool, re_bootstrap: bool) -> dict:
    """Bootstrap 興趣模型。"""
    from interest_model import (
        load_interest_model,
        save_interest_model,
        bootstrap_interest_model,
        ensure_edge_zones,
        _scan_articles_frequency,
        _compute_score,
        _empty_tag_entry,
    )

    if re_bootstrap:
        model = load_interest_model()
        interests = model.setdefault("interests", {})
        now = datetime.now()
        today = now.isoformat()[:10]

        article_counts = _scan_articles_frequency()
        for tag, count in article_counts.items():
            entry = interests.setdefault(tag, _empty_tag_entry())
            entry["signal_breakdown"]["article_frequency"] = count
            if entry["last_engaged"] < today:
                entry["last_engaged"] = today

        for tag, entry in interests.items():
            entry["score"] = round(_compute_score(entry, now), 2)

        if dry_run:
            logger.info(f"[DRY-RUN] re-bootstrap: {len(interests)} interests")
        else:
            save_interest_model(model)
            logger.info(f"✅ re-bootstrap: {len(interests)} interests")
    else:
        if dry_run:
            article_counts = _scan_articles_frequency()
            logger.info(f"[DRY-RUN] bootstrap: 會建立 {len(article_counts)} 個 interest entries")
            top5 = sorted(article_counts.items(), key=lambda x: -x[1])[:5]
            for tag, count in top5:
                logger.info(f"  {tag}: article_frequency={count}")
            return {"interests": {t: {"score": c / 5.0} for t, c in article_counts.items()}}
        else:
            model = bootstrap_interest_model()
            logger.info(f"✅ bootstrap: {len(model.get('interests', {}))} interests")

    if not dry_run:
        from vault_writer import read_state_json
        topics_cfg = read_state_json("config/topics.json")
        if not topics_cfg:
            topics_cfg = INITIAL_TOPICS
        model = ensure_edge_zones(topics_cfg)
        for tid, zones in model.get("edge_zones", {}).items():
            logger.info(f"  edge_zones[{tid}]: {zones}")
        return model
    else:
        logger.info("[DRY-RUN] 會用 LLM 預生 edge_zones（只 enabled topics）")
        for t in INITIAL_TOPICS["topics"]:
            if t["enabled"]:
                logger.info(f"  → {t['id']}: 預生 4-6 個相鄰領域")
        return {}


def _try_refresh_pharos_hash(dry_run: bool) -> None:
    """如果 Pharos 在預設位置，自動跑 hash。"""
    pharos_path = Path(os.environ.get("PHAROS_REPO_PATH", str(Path.home() / "yt2epub")))
    pharos_yt2epub = pharos_path / "yt2epub.py"

    if not pharos_yt2epub.exists():
        logger.info(f"Pharos 不在 {pharos_yt2epub}，跳過 hash 同步")
        return

    if dry_run:
        logger.info(f"[DRY-RUN] 會從 {pharos_yt2epub} 計算 EPUB_CSS hash 並更新到 digest_to_kepub.py")
        return

    try:
        from digest_to_kepub import refresh_pharos_css_hash
        result = refresh_pharos_css_hash()
        logger.info(f"✅ Pharos hash 更新: {result['old_hash'][:16]}... → {result['new_hash'][:16]}...")
    except Exception as e:
        logger.warning(f"Pharos hash 更新失敗（不影響核心功能）: {e}")


def _try_refresh_anamnesis_hash(dry_run: bool) -> None:
    """Bootstrap 時自動把當前 Anamnesis API 簽名 hash 寫進 anamnesis_drift.py。"""
    if dry_run:
        logger.info("[DRY-RUN] 會計算 Anamnesis API 簽名 hash 並更新到 anamnesis_drift.py")
        return

    try:
        from anamnesis_drift import refresh_anamnesis_signatures_hash
        result = refresh_anamnesis_signatures_hash()
        if result.get("noop"):
            logger.info(f"✅ Anamnesis API hash 已是最新 ({result['new_hash'][:16]}...)")
        else:
            logger.info(
                f"✅ Anamnesis API hash 更新: {result['old_hash'][:16]}... → {result['new_hash'][:16]}..."
            )
    except Exception as e:
        logger.warning(f"Anamnesis hash 更新失敗（不影響核心功能）: {e}")


def main():
    parser = argparse.ArgumentParser(description="Exegesis 首次部署")
    parser.add_argument("--dry-run", action="store_true", help="不寫檔，只印結果")
    parser.add_argument("--re-bootstrap", action="store_true",
                        help="保留互動訊號，重算 article_frequency + edge_zones")
    parser.add_argument("--refresh-pharos-hash", action="store_true",
                        help="重新計算 Pharos EPUB_CSS hash 並更新到 digest_to_kepub.py")
    parser.add_argument("--refresh-anamnesis-hash", action="store_true",
                        help="重新計算 Anamnesis API 簽名 hash 並更新到 anamnesis_drift.py")
    args = parser.parse_args()

    if args.refresh_pharos_hash:
        from digest_to_kepub import refresh_pharos_css_hash
        result = refresh_pharos_css_hash()
        print(f"✅ Pharos hash 更新：")
        print(f"  舊: {result['old_hash']}")
        print(f"  新: {result['new_hash']}")
        print(f"  CSS 內容也已同步: {result.get('css_synced', False)}")
        sys.exit(0)

    if args.refresh_anamnesis_hash:
        from anamnesis_drift import refresh_anamnesis_signatures_hash
        result = refresh_anamnesis_signatures_hash()
        print(f"✅ Anamnesis API hash 更新：")
        print(f"  舊: {result['old_hash']}")
        print(f"  新: {result['new_hash']}")
        if result.get("noop"):
            print("  (已是最新，無變動)")
        sys.exit(0)

    logger.info("=" * 50)
    logger.info("Exegesis Bootstrap" + (" [DRY-RUN]" if args.dry_run else ""))
    logger.info("=" * 50)

    _create_directories(args.dry_run)
    _write_topics_config(args.dry_run)
    model = _bootstrap_interest(args.dry_run, args.re_bootstrap)
    _try_refresh_pharos_hash(args.dry_run)
    _try_refresh_anamnesis_hash(args.dry_run)

    if not args.dry_run and model:
        interests = model.get("interests", {})
        top5 = sorted(interests.items(), key=lambda x: -x[1].get("score", 0))[:5]
        logger.info("")
        logger.info(f"Bootstrap 完成。")
        logger.info(f"  Interests: {len(interests)} 個 tags")
        logger.info(f"  Top 5 (by score): {[t for t, _ in top5]}")
        for tid, zones in model.get("edge_zones", {}).items():
            logger.info(f"  Edge zones for '{tid}': {zones}")


if __name__ == "__main__":
    main()
