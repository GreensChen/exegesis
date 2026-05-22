#!/usr/bin/env python3
"""exegesis.py — Exegesis 主入口。

CLI 用法：
    python3 exegesis.py prepare              # 階段 1
    python3 exegesis.py prepare --topic llm   # 指定主題
    python3 exegesis.py prepare --dry-run     # 不推 Telegram
    python3 exegesis.py generate              # 階段 2
    python3 exegesis.py generate --direction-id dir_1
    python3 exegesis.py generate --dry-run
    python3 exegesis.py upgrade --arxiv-id 2405.12345
    python3 exegesis.py upgrade --arxiv-id 2405.12345 --dry-run
    python3 exegesis.py update-moc
    python3 exegesis.py refresh-interest
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

import requests

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("exegesis")


def _get_iso_week() -> str:
    """算本週 ISO 週次（Asia/Taipei）。"""
    now = datetime.now()
    return now.strftime("%G-W%V")


def _select_topic(topics_config: dict, forced_topic_id: str = None) -> dict:
    """從 topics 中選 enabled + last_published_week 最舊的。"""
    topics = [t for t in topics_config.get("topics", []) if t.get("enabled")]
    if not topics:
        raise RuntimeError("沒有 enabled 的主題")

    if forced_topic_id:
        for t in topics:
            if t["id"] == forced_topic_id:
                return t
        raise RuntimeError(f"主題 '{forced_topic_id}' 不存在或未 enabled")

    topics.sort(key=lambda t: t.get("last_published_week") or "0000-W00")
    return topics[0]


def _verify_drift_silent():
    """跑 Pharos 漂移檢查（失敗不阻擋）。"""
    try:
        from digest_to_kepub import _verify_pharos_drift
        _verify_pharos_drift()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
# 階段 1: prepare
# ═══════════════════════════════════════════════════════

def prepare_weekly(topic_id: str = None, dry_run: bool = False) -> dict:
    """階段 1：選主題、抓 paper、curate directions、寫 pending_choice.json。"""
    from vault_writer import read_state_json, write_state_json
    from paper_discovery import discover_papers
    from paper_curator import curate_directions
    from interest_model import load_interest_model

    _verify_drift_silent()

    topics_cfg = read_state_json("config/topics.json")
    if not topics_cfg or not topics_cfg.get("topics"):
        raise RuntimeError("topics.json 不存在或為空，請先跑 init_exegesis.py")

    topic = _select_topic(topics_cfg, topic_id)
    iso_week = _get_iso_week()

    logger.info(f"=== prepare_weekly: {iso_week} / {topic['name_zh']} ===")

    im = load_interest_model()

    logger.info("開始 paper discovery...")
    candidates = discover_papers(
        topic,
        days_back=int(os.environ.get("EXEGESIS_DAYS_BACK", "14")),
        limit=int(os.environ.get("EXEGESIS_CANDIDATES_LIMIT", "25")),
        interest_model=im,
    )
    logger.info(f"  → {len(candidates)} candidates")

    if not candidates:
        raise RuntimeError("沒有抓到任何 paper candidates")

    logger.info("開始 curation...")
    directions = curate_directions(candidates, topic, interest_model=im)
    logger.info(f"  → {len(directions)} directions")

    for d in directions:
        logger.info(f"  [{d.get('category', '?')}] {d.get('title', '')} — {d.get('narrative', '')[:50]}")

    if not dry_run:
        archive_path = f"archive/{iso_week}_{topic['id']}"
        write_state_json(f"{archive_path}/candidates.json", candidates)
        write_state_json(f"{archive_path}/directions.json", directions)

        pending = {
            "iso_week": iso_week,
            "topic_id": topic["id"],
            "topic": topic,
            "directions": directions,
            "candidates": candidates,
            "prepared_at": datetime.now().isoformat(),
            "status": "awaiting_selection",
        }
        write_state_json("state/pending_choice.json", pending)
        logger.info("  pending_choice.json 已寫入")

    return {
        "topic": topic,
        "directions": directions,
        "iso_week": iso_week,
        "candidates_count": len(candidates),
    }


# ═══════════════════════════════════════════════════════
# 階段 2: generate
# ═══════════════════════════════════════════════════════

def generate_weekly(direction_id: str, dry_run: bool = False) -> dict:
    """階段 2：跑完整 pipeline 生成 digest + cards + kepub + MOC。"""
    from vault_writer import read_state_json, write_state_json, write_to_vault
    from paper_reader import read_paper
    from paper_writer import write_digest, write_paper_card, find_related_notes
    from moc_updater import update_moc
    from interest_model import load_interest_model, record_direction_selection, refresh_interest_model

    _verify_drift_silent()

    pending = read_state_json("state/pending_choice.json")
    if not pending or pending.get("status") != "awaiting_selection":
        raise RuntimeError("沒有 pending_choice 或已處理過。請先跑 prepare。")

    iso_week = pending["iso_week"]
    topic = pending["topic"]
    directions = pending["directions"]
    candidates = pending.get("candidates", [])

    direction = None
    for d in directions:
        if d["id"] == direction_id:
            direction = d
            break
    if not direction:
        raise RuntimeError(f"找不到 direction_id={direction_id}，可選: {[d['id'] for d in directions]}")

    logger.info(f"=== generate_weekly: {iso_week} / {topic['name_zh']} / {direction['title']} ===")

    candidate_map = {p["id"]: p for p in candidates}
    selected_metas = []
    for pid in direction.get("paper_ids", []):
        meta = candidate_map.get(pid)
        if meta:
            selected_metas.append(meta)
    if not selected_metas:
        raise RuntimeError("選定 direction 裡的 paper_ids 在 candidates 中找不到")

    logger.info(f"下載 & 讀取 {len(selected_metas)} 篇 paper...")
    paper_contents = []
    for i, meta in enumerate(selected_metas, 1):
        pdf_url = meta.get("pdf_url")
        if not pdf_url:
            logger.warning(f"  [{i}] 無 PDF URL，fallback 用 abstract")
            paper_contents.append({
                "one_liner": meta.get("abstract", "")[:60],
                "core_findings": ["(no PDF available)"],
                "method_summary": meta.get("abstract", "")[:200],
                "key_results": ["(unavailable)"],
                "limitations": ["(unavailable)"],
                "key_terms": [],
                "connections": "",
            })
            continue

        try:
            logger.info(f"  [{i}] 下載 {pdf_url[:60]}...")
            pdf_resp = requests.get(pdf_url, timeout=120)
            pdf_resp.raise_for_status()
            pdf_bytes = pdf_resp.content

            if not dry_run:
                archive_path = f"archive/{iso_week}_{topic['id']}/pdfs"
                arxiv_id = meta.get("external_ids", {}).get("arxiv", str(i))
                write_state_json(f"{archive_path}/{arxiv_id}.meta.json", {"size": len(pdf_bytes)})

            logger.info(f"  [{i}] 讀取 PDF ({len(pdf_bytes)} bytes)...")
            content = read_paper(pdf_bytes, meta)
            paper_contents.append(content)
        except Exception as e:
            logger.error(f"  [{i}] PDF 讀取失敗: {e}")
            paper_contents.append({
                "one_liner": meta.get("abstract", "")[:60],
                "core_findings": [f"(PDF read failed: {e})"],
                "method_summary": meta.get("abstract", "")[:200],
                "key_results": ["(unavailable)"],
                "limitations": ["(unavailable)"],
                "key_terms": [],
                "connections": "",
            })

    im = load_interest_model()

    logger.info("找跨領域連結...")
    cross_refs = find_related_notes(direction, paper_contents, interest_model=im)
    logger.info(f"  → {len(cross_refs)} 個跨領域連結")

    for meta in selected_metas:
        meta["_topic_id"] = topic["id"]

    logger.info("寫 digest 長文...")
    digest = write_digest(iso_week, topic, direction, paper_contents, selected_metas, cross_refs)
    logger.info(f"  → {digest['filename']} ({len(digest['content'])} chars)")

    if dry_run:
        tmp_path = "/tmp/digest_preview.md"
        Path(tmp_path).write_text(digest["content"], encoding="utf-8")
        logger.info(f"  [DRY-RUN] digest 存到 {tmp_path}")
    else:
        digest_path = write_to_vault(f"1 Sources/Digests/{digest['filename']}", digest["content"])
        logger.info(f"  → {digest_path}")

    logger.info("寫 paper cards...")
    paper_card_paths = []
    paper_card_filenames = []
    for i, (meta, pc) in enumerate(zip(selected_metas, paper_contents), 1):
        card = write_paper_card(meta, pc, digest["filename_stem"], digest["title"])
        paper_card_filenames.append(card["filename"])
        if dry_run:
            if i == 1:
                tmp_card = "/tmp/paper_card_preview.md"
                Path(tmp_card).write_text(card["content"], encoding="utf-8")
                logger.info(f"  [DRY-RUN] card 1 存到 {tmp_card}")
        else:
            card_path = write_to_vault(f"1 Sources/Papers/{card['filename']}", card["content"])
            paper_card_paths.append(card_path)
            logger.info(f"  [{i}] → {card_path}")

    # kepub
    kepub_path = None
    if not dry_run:
        try:
            from digest_to_kepub import build_digest_kepub, upload_to_kobo
            logger.info("生成 Digest kepub...")
            kepub_path = build_digest_kepub(digest["filename_stem"], digest["content"], selected_metas)
            logger.info(f"  → {kepub_path}")
            kobo_path = upload_to_kobo(kepub_path)
            logger.info(f"  → 上傳 Kobo: {kobo_path}")
        except Exception as e:
            logger.error(f"kepub 生成/上傳失敗（不影響其他輸出）: {e}")

    # MOC
    if not dry_run:
        logger.info("更新 MOC...")
        moc_path = update_moc()
    else:
        moc_path = None
        logger.info("[DRY-RUN] 跳過 MOC 更新")

    # 更新 topics.json
    if not dry_run:
        topics_cfg = read_state_json("config/topics.json")
        for t in topics_cfg.get("topics", []):
            if t["id"] == topic["id"]:
                t["last_published_week"] = iso_week
                break
        write_state_json("config/topics.json", topics_cfg)

    # 興趣模型更新
    if not dry_run:
        logger.info("更新興趣模型...")
        record_direction_selection(direction, iso_week)
        refresh_interest_model()

    # 寫 last_run + selected_direction + 清 pending
    if not dry_run:
        write_state_json("state/last_run.json", {
            "iso_week": iso_week,
            "topic_id": topic["id"],
            "direction_id": direction_id,
            "status": "success",
            "generated_at": datetime.now().isoformat(),
            "digest_filename": digest["filename"],
            "paper_card_count": len(paper_card_paths),
            "kepub_path": kepub_path,
        })
        write_state_json(f"archive/{iso_week}_{topic['id']}/selected_direction.json", direction)
        write_state_json("state/pending_choice.json", {})

    logger.info(f"=== generate_weekly 完成 ===")

    return {
        "digest_path": f"1 Sources/Digests/{digest['filename']}",
        "digest_filename_stem": digest["filename_stem"],
        "digest_title": digest["title"],
        "paper_card_paths": paper_card_paths or [f"1 Sources/Papers/{fn}" for fn in paper_card_filenames],
        "paper_metas": selected_metas,
        "kepub_path": kepub_path,
        "moc_path": moc_path,
        "cross_refs_count": len(cross_refs),
    }


# ═══════════════════════════════════════════════════════
# 升級
# ═══════════════════════════════════════════════════════

def upgrade_paper(arxiv_id: str, dry_run: bool = False) -> dict:
    """升級指定 paper 為全文中譯。"""
    from paper_translator import translate_paper

    if dry_run:
        logger.info(f"[DRY-RUN] 會翻譯 {arxiv_id} 並存到 1 Sources/Papers Translated/")
        result = translate_paper(arxiv_id)
        tmp_path = "/tmp/translation_preview.md"
        from vault_writer import read_from_vault
        content = read_from_vault(f"1 Sources/Papers Translated/{result['filename']}")
        Path(tmp_path).write_text(content, encoding="utf-8")
        logger.info(f"  [DRY-RUN] 存到 {tmp_path}")
        return result

    return translate_paper(arxiv_id)


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Exegesis 主入口")
    subparsers = parser.add_subparsers(dest="command")

    p_prepare = subparsers.add_parser("prepare", help="階段 1：discovery + curation")
    p_prepare.add_argument("--topic", default=None, help="指定主題 ID")
    p_prepare.add_argument("--dry-run", action="store_true")

    p_generate = subparsers.add_parser("generate", help="階段 2：產出 digest + cards")
    p_generate.add_argument("--direction-id", default=None, help="略過 Telegram 確認")
    p_generate.add_argument("--dry-run", action="store_true")

    p_upgrade = subparsers.add_parser("upgrade", help="升級 paper 為全文中譯")
    p_upgrade.add_argument("--arxiv-id", required=True)
    p_upgrade.add_argument("--dry-run", action="store_true")

    subparsers.add_parser("update-moc", help="重新整理 MOC")
    subparsers.add_parser("refresh-interest", help="重算興趣模型")

    args = parser.parse_args()

    if args.command == "prepare":
        result = prepare_weekly(topic_id=args.topic, dry_run=args.dry_run)
        print(json.dumps({
            "iso_week": result["iso_week"],
            "topic": result["topic"]["name_zh"],
            "directions": [
                {"id": d["id"], "category": d.get("category"), "title": d.get("title"),
                 "narrative": d.get("narrative"), "edge_zone": d.get("edge_zone")}
                for d in result["directions"]
            ],
            "candidates_count": result["candidates_count"],
        }, ensure_ascii=False, indent=2))

    elif args.command == "generate":
        if not args.direction_id:
            from vault_writer import read_state_json
            pending = read_state_json("state/pending_choice.json")
            if not pending:
                print("❌ 沒有 pending_choice，請先跑 prepare")
                sys.exit(1)
            print("可選 direction_id:")
            for d in pending.get("directions", []):
                print(f"  {d['id']} [{d.get('category')}] {d.get('title')}")
            sys.exit(0)

        result = generate_weekly(args.direction_id, dry_run=args.dry_run)
        print(f"✅ digest: {result['digest_path']}")
        print(f"   cards: {len(result['paper_card_paths'])} 篇")
        if result.get("kepub_path"):
            print(f"   kepub: {result['kepub_path']}")

    elif args.command == "upgrade":
        result = upgrade_paper(args.arxiv_id, dry_run=args.dry_run)
        print(f"✅ 翻譯完成: {result['filename']} ({result['char_count']} 字)")

    elif args.command == "update-moc":
        from moc_updater import update_moc
        path = update_moc()
        print(f"✅ MOC 更新: {path}")

    elif args.command == "refresh-interest":
        from interest_model import refresh_interest_model
        model = refresh_interest_model()
        interests = model.get("interests", {})
        top5 = sorted(interests.items(), key=lambda x: -x[1].get("score", 0))[:5]
        print(f"✅ 興趣模型重算完成，{len(interests)} 個 tags")
        for tag, entry in top5:
            print(f"  {tag}: score={entry['score']}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
