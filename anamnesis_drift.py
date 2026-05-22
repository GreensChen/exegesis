"""anamnesis_drift.py — 監控 Anamnesis 跨 repo API 的函式簽名漂移。

Exegesis 依賴 Anamnesis 的 interest_model / vault_writer / vocabulary_manager 的特定 API。
如果 Anamnesis 端改了函式簽名（增刪參數、改 return type），Exegesis 會壞掉。
這個模組在啟動時用 inspect 抓 Anamnesis 端函式簽名的 hash，跟記錄值比對。

修復流程：
- 觀察到 drift 時，先確認 Exegesis 端的呼叫處還相容
- 若相容，跑 `python3 init_exegesis.py --refresh-anamnesis-hash` 更新 hash
- 若不相容，Exegesis 端對齊改動再 refresh hash
"""

import hashlib
import inspect
import logging
import os
from pathlib import Path

# 由 init_exegesis.py --refresh-anamnesis-hash 在 bootstrap 時自動填入。
ANAMNESIS_API_SIGNATURES_SHA256 = "BOOTSTRAP_WILL_FILL_THIS"

# 監控的 API 清單（若 Anamnesis 端加新函式，這裡不用更新；只監控既有的）
_MONITORED_APIS = [
    ("interest_model", "record_pharos_signal"),
    ("interest_model", "record_user_query_signal"),
    ("interest_model", "load_interest_model"),
    ("interest_model", "save_interest_model"),
    ("interest_model", "refresh_interest_model"),
    ("interest_model", "record_direction_selection"),
    ("interest_model", "ensure_edge_zones"),
    ("interest_model", "bootstrap_interest_model"),
    ("vocabulary_manager", "apply_tags_to_capture"),
    ("vault_writer", "write_to_vault"),
    ("vault_writer", "read_from_vault"),
    ("vault_writer", "list_vault_folder"),
    ("vault_writer", "read_state_json"),
    ("vault_writer", "write_state_json"),
    ("vault_writer", "read_vault_dotfile"),
    ("vault_writer", "write_vault_dotfile"),
]


def _compute_current_signatures_hash() -> str:
    """抓所有監控 API 的簽名，串起來算 SHA256。"""
    parts = []
    for module_name, func_name in _MONITORED_APIS:
        try:
            module = __import__(module_name)
            func = getattr(module, func_name)
            sig = str(inspect.signature(func))
            parts.append(f"{module_name}.{func_name}{sig}")
        except (ImportError, AttributeError) as e:
            parts.append(f"{module_name}.{func_name}::MISSING")
            logging.getLogger("anamnesis_drift").warning(
                f"Anamnesis API {module_name}.{func_name} 不存在或無法 import: {e}"
            )
    combined = "\n".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def verify_anamnesis_drift() -> None:
    """檢查 Anamnesis API 簽名是否跟 Exegesis 記錄的一致。

    觸發時機：
    - exegesis_bot.py 啟動時（背景 task，不阻擋啟動）
    - exegesis.py 的 prepare/generate 主流程開頭

    行為：
    - 一致 → debug log
    - 漂移 → ERROR log + 推 Telegram 警告
    - 不 raise（降級而非中斷）
    """
    current_hash = _compute_current_signatures_hash()

    if current_hash == ANAMNESIS_API_SIGNATURES_SHA256:
        logging.getLogger("anamnesis_drift").debug(
            f"Anamnesis API signatures 一致 ({current_hash[:12]}...)"
        )
        return

    logger = logging.getLogger("anamnesis_drift")
    logger.error("=" * 60)
    logger.error("🚨 ANAMNESIS API DRIFT DETECTED")
    logger.error(f"當前 hash:     {current_hash}")
    logger.error(f"Exegesis 紀錄: {ANAMNESIS_API_SIGNATURES_SHA256}")
    logger.error("")
    logger.error("Anamnesis 端某個函式簽名變了，Exegesis 可能會壞掉。")
    logger.error("修復步驟：")
    logger.error("1. 檢視 ../anamnesis 端最近 commit 對 interest_model / vault_writer / vocabulary_manager 的改動")
    logger.error("2. 確認 Exegesis 端的呼叫處還相容")
    logger.error("3. 若相容，跑 python3 init_exegesis.py --refresh-anamnesis-hash 更新 hash")
    logger.error("=" * 60)

    try:
        _push_drift_telegram_warning(current_hash, ANAMNESIS_API_SIGNATURES_SHA256)
    except Exception as e:
        logger.warning(f"Telegram drift 警告推送失敗: {e}")


def _push_drift_telegram_warning(current_hash: str, recorded_hash: str) -> None:
    import requests

    token = os.environ.get("EXEGESIS_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    msg = (
        "🚨 <b>Anamnesis API Drift Detected</b>\n\n"
        f"當前 hash: <code>{current_hash[:16]}...</code>\n"
        f"Exegesis 紀錄: <code>{recorded_hash[:16]}...</code>\n\n"
        "Anamnesis 端的函式簽名變了。請確認 Exegesis 端的呼叫還相容，"
        "確認後跑 <code>python3 init_exegesis.py --refresh-anamnesis-hash</code>。"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={
        "chat_id": chat_id, "text": msg, "parse_mode": "HTML",
    }, timeout=5)


def refresh_anamnesis_signatures_hash() -> dict:
    """重新計算當前 Anamnesis API hash 並更新到本檔。
    由 init_exegesis.py --refresh-anamnesis-hash 觸發。"""
    import re

    new_hash = _compute_current_signatures_hash()

    this_file = Path(__file__)
    content = this_file.read_text(encoding="utf-8")
    new_content = re.sub(
        r'ANAMNESIS_API_SIGNATURES_SHA256 = "[^"]*"',
        f'ANAMNESIS_API_SIGNATURES_SHA256 = "{new_hash}"',
        content, count=1,
    )

    if new_content == content:
        return {"old_hash": ANAMNESIS_API_SIGNATURES_SHA256, "new_hash": new_hash, "noop": True}

    this_file.write_text(new_content, encoding="utf-8")
    return {
        "old_hash": ANAMNESIS_API_SIGNATURES_SHA256,
        "new_hash": new_hash,
    }
