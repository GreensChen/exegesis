#!/usr/bin/env python3
"""digest_to_kepub.py — Digest → 繁中 kepub → /應用程式/Rakuten Kobo/。

⚠️ 本檔的 EPUB_CSS 與 generate_text_cover() 抄自 Pharos：
  - Pharos repo: https://github.com/GreensChen/pharos
  - Pharos file: yt2epub.py
  - 抄寫時的 Pharos commit: (見 PHAROS_EPUB_CSS_SHA256)

修改 Pharos 對應內容後，**必須** 同步本檔。
驗證方式：啟動 anamnesis bot 時會自動跑 _verify_pharos_drift() hash 比對。
"""

import hashlib
import logging
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

logger = logging.getLogger("digest_to_kepub")

PHAROS_EPUB_CSS_SHA256 = "17f1a3aaf59f82469bb516ad0dfbae84f70d56b79b3f74b714ac9cbe42355ca5"

EPUB_CSS = """
body {
    font-family: "Noto Serif CJK TC", "Source Han Serif TC", "Georgia", serif;
    /* 不指定 font-size 與 padding：跟商業電子書一樣，完全交給 Kobo 的字級
       與「邊界」slider 控制，避免內文離邊太遠。*/
    line-height: 1.2;
    color: #1a1a1a;
}

a {
    text-decoration: none;
    color: inherit;
}

nav ol, nav ul {
    list-style: none;
    padding-left: 0;
    line-height: 1.2;
}

/* 目錄頁字級（Kobo 會用這個渲染 nav.xhtml，Books 因 linear="no" 跳過）*/
nav h2 {
    font-size: 1.1em;
    margin-bottom: 0.6em;
}
nav li {
    font-size: 0.85em;
    line-height: 1.6;
    margin: 0.3em 0;
}
nav a {
    color: #1a1a1a;
}

h1 {
    /* 預設 h1 給簡介頁用、與內文同大。章節標題另外加大。*/
    font-size: 1em;
    font-weight: bold;
    color: #222;
    border-bottom: 2px solid #c0392b;
    padding-bottom: 0.3em;
    margin-top: 1.5em;
    margin-bottom: 0.3em;
}

/* 章節標題：在 Kobo 上強制換頁。字級不要太大，與內文層級感由副標
   （chapter-zh）下沉到內文等高、英文標題用 1.3em 微大即可。*/
h1.chapter-start {
    font-size: 1.3em;
    page-break-before: always;
    break-before: page;
}
h1.chapter-start:first-of-type {
    page-break-before: avoid;
    break-before: avoid;
}

h1 .chapter-zh {
    display: block;
    /* 用 rem 直接對齊 root（=body）字級，不會因 h1 放大而被 cascade 拉大 */
    font-size: 1rem;
    color: #555;
    margin-top: 0.2em;
    font-weight: normal;
}

.speaker {
    font-weight: bold;
    color: #c0392b;
    margin-top: 1.5em;
    margin-bottom: 0.2em;
    border-left: 3px solid #c0392b;
    padding-left: 0.6em;
}

.speaker-b { color: #2c3e50; border-left-color: #2c3e50; }
.speaker-c { color: #27ae60; border-left-color: #27ae60; }
.speaker-d { color: #8e44ad; border-left-color: #8e44ad; }

.timestamp {
    /* 視覺上隱藏；保留 koboSpan 結構讓 Cat Wu 等老書重建時 highlight 不破 */
    display: none;
    font-size: smaller;
    color: #999;
    font-family: monospace;
    margin-left: 0.5em;
    font-weight: normal;
}

.zh {
    color: #1a1a1a;
    line-height: 1.4;
    margin: 0.3em 0 1em 0;
}

.segment {
    /* 不指定 font-size：跟商業電子書一樣交給 Kobo 字級 slider 控制，
       使用者調整字級時，逐字稿才會跟著縮放、不會永遠超大。*/
    margin-bottom: 1em;
}

.cover-title {
    font-size: 1.3em;
    font-weight: bold;
    text-align: center;
    margin-top: 2em;
    line-height: 1.3;
    color: #c0392b;
}

.cover-subtitle {
    font-size: 1em;
    text-align: center;
    color: #555;
    margin-top: 0.8em;
}

.cover-meta {
    text-align: center;
    margin-top: 2em;
    color: #777;
    line-height: 1.4;
}

.episode-info {
    border: 1px solid #ddd;
    padding: 1em;
    margin: 1.5em 0;
    background-color: #fafafa;
}

.episode-info h2 {
    /* 簡介頁的「對談人」「簡介」副標題：與內文同大，靠粗體 + 顏色區隔 */
    font-size: 1em;
    font-weight: bold;
    margin-top: 0;
    margin-bottom: 0.5em;
    color: #c0392b;
}

/* 對談人 / 簡介 區塊內的條目（姓名、角色、簡介內容）字級壓到跟
   "Stripe · 2026-04-30" 副標一致，視覺上比標題低一階 */
.episode-info p {
    font-size: 0.85em;
}

.source-link {
    text-align: center;
    margin-top: 1em;
    font-size: smaller;
    color: #999;
}

/* 簡介頁字級規則：
   - "簡介" h1 與 "對談人" h2 同高（h1 1.05em / h2 1.05em，預設值已設）
   - "Stripe · 2026-04-30" 副標：比內文再小一階
   - 對談人姓名 + 角色標籤：內文同大（不另外縮小）
*/
body.info-page h1 .chapter-zh {
    font-size: 0.85em;
}
"""


def _safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[/\\:*?"<>|\r\n\t]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:max_len].rstrip()


def generate_text_cover(title: str, subtitle: str = "") -> bytes:
    """畫乾淨的純文字書封：白底、深灰標題置中、副標題。

    ⚠️ 此函式 Pharos / Anamnesis 兩邊必須 byte-for-byte 一致。
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    # 4:3 直立比例，貼合 Kobo 螢幕原生比例，避免休眠時 letterbox 邊框
    W, H = 1500, 2000
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    font_paths = [
        # macOS（PingFang 預設是 Regular 粗細）
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        # Linux：Regular 優先，避免吃到 Bold 讓封面字過粗
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Medium.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        # 最後才 fallback 到 Bold
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ]
    title_font = None
    sub_font = None
    for fp in font_paths:
        try:
            title_font = ImageFont.truetype(fp, 66)
            sub_font = ImageFont.truetype(fp, 38)
            break
        except Exception:
            continue
    if not title_font:
        title_font = ImageFont.load_default()
        sub_font = ImageFont.load_default()

    # 標題自動換行：英文/混合語言依字元寬度斷詞；純中文段落每行 20 字
    def wrap(text, font, max_w):
        words = text.split()
        lines, cur = [], ""
        for w in words:
            test = (cur + " " + w).strip()
            if draw.textlength(test, font=font) <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    if any('一' <= c <= '鿿' for c in title):
        per_line = 20
        title_lines = [title[i:i + per_line] for i in range(0, len(title), per_line)]
    else:
        title_lines = wrap(title, title_font, W - 140)

    line_h = 90
    total_h = len(title_lines) * line_h

    # 整體（標題 + 副標題）置中，無分隔線
    sub_gap = 60
    sub_h = 40 if subtitle else 0
    block_h = total_h + (sub_gap + sub_h if subtitle else 0)
    start_y = (H - block_h) // 2

    for i, line in enumerate(title_lines):
        tw = draw.textlength(line, font=title_font)
        draw.text(((W - tw) / 2, start_y + i * line_h), line, fill="#1a1a1a", font=title_font)

    if subtitle:
        sub_y = start_y + total_h + sub_gap
        sw = draw.textlength(subtitle, font=sub_font)
        draw.text(((W - sw) / 2, sub_y), subtitle, fill="#444444", font=sub_font)

    import io
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════
# Digest Markdown → Segments
# ═══════════════════════════════════════════════════════

def parse_digest_markdown(digest_content: str) -> tuple[dict, list[dict]]:
    """把 Digest .md 解析成 (meta, segments)。"""
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", digest_content, re.DOTALL)
    if not fm_match:
        raise ValueError("Digest 缺 frontmatter")
    fm_text = fm_match.group(1)
    body = digest_content[fm_match.end():]

    def _fm(key):
        m = re.search(rf'^{key}:\s*"?(.*?)"?\s*$', fm_text, re.MULTILINE)
        return m.group(1).strip().strip('"') if m else ""

    iso_week = _fm("week")  # e.g. "2026-W21"
    # 副標格式（英文，跟 Pharos 逐字稿封面風格一致）：
    # "2026 · W21 · Large Language Models"
    readable_week_en = iso_week
    wm = re.match(r"(\d{4})-W(\d+)", iso_week)
    if wm:
        readable_week_en = f"{wm.group(1)} · W{int(wm.group(2)):02d}"

    title_zh = _fm("title")
    title_en = _fm("title_en") or title_zh  # 舊 digest 沒 title_en 時 fallback
    topic_zh = _fm("topic_zh")
    topic_en = _fm("topic_en") or topic_zh  # 舊 digest 沒 topic_en 時 fallback

    meta = {
        # 封面 / OPF dc:title 用英文
        "title": title_en,
        "title_zh": title_zh,
        "iso_week": iso_week,
        "topic_zh": topic_zh,
        "topic_en": topic_en,
        "subtitle": f"{readable_week_en} · {topic_en}",
        "date": _fm("generated_at")[:10] if _fm("generated_at") else "",
    }

    chapters = []
    current_chapter = None

    for line in body.split("\n"):
        # 每個 `## ` 都是一章，包括第 6 章之後的延伸章節（相關書籍 / 訪談 /
        # 文章 / 名詞定義，由系統拼接，動態編號）。
        # 只排除 `## 📖 本週收錄`，那是 H1 標題下面的概要清單，不算章節。
        if line.startswith("## ") and not line.startswith("## 📖"):
            if current_chapter:
                chapters.append(current_chapter)
            title = line[3:].strip()
            current_chapter = {"type": "h1_chapter", "title": title, "content_lines": []}
        elif current_chapter is not None:
            current_chapter["content_lines"].append(line)

    if current_chapter:
        chapters.append(current_chapter)

    for ch in chapters:
        ch["content"] = _markdown_to_xhtml("\n".join(ch["content_lines"]))
        del ch["content_lines"]

    return meta, chapters


def _markdown_to_xhtml(md: str) -> str:
    """簡易 markdown → XHTML 轉換。"""
    import html as html_lib

    lines = md.split("\n")
    out = []
    in_list = False
    in_blockquote = False

    for line in lines:
        line = line.rstrip()

        if line.startswith(">"):
            if not in_blockquote:
                out.append('<blockquote>')
                in_blockquote = True
            content = line[1:].strip()
            content = _inline_md(content)
            out.append(f'<p>{content}</p>')
            continue
        elif in_blockquote:
            out.append('</blockquote>')
            in_blockquote = False

        if line.startswith("### "):
            if in_list:
                out.append('</ul>')
                in_list = False
            content = _inline_md(line[4:].strip())
            out.append(f'<h2>{content}</h2>')
            continue

        if line.startswith("- "):
            if not in_list:
                out.append('<ul>')
                in_list = True
            content = _inline_md(line[2:].strip())
            out.append(f'<li>{content}</li>')
            continue
        elif in_list and line.strip() == "":
            out.append('</ul>')
            in_list = False
            continue
        elif in_list:
            out.append('</ul>')
            in_list = False

        if line.strip():
            content = _inline_md(line)
            out.append(f'<p>{content}</p>')

    if in_list:
        out.append('</ul>')
    if in_blockquote:
        out.append('</blockquote>')

    return "\n".join(out)


def _inline_md(text: str) -> str:
    """處理 inline markdown。"""
    import html as html_lib

    text = html_lib.escape(text)

    text = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'<em>\2</em>', text)

    def _bare_wikilink(m):
        path = m.group(1)
        base = path.split("/")[-1]
        return f'<em>{base}</em>'
    text = re.sub(r'\[\[([^\]]+)\]\]', _bare_wikilink, text)

    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<em>\1</em>', text)

    return text


# ═══════════════════════════════════════════════════════
# Build EPUB
# ═══════════════════════════════════════════════════════

def build_epub(meta: dict, chapters: list[dict], output_path: str) -> None:
    """組裝 epub。"""
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier(str(uuid.uuid4()))
    book.set_title(meta["title"])
    book.set_language("zh-TW")
    book.add_author("Weekly Paper · Anamnesis")
    book.add_metadata("DC", "date", meta.get("date") or datetime.now().strftime("%Y-%m-%d"))

    cover_img = generate_text_cover(meta["title"], meta.get("subtitle", ""))
    if cover_img:
        book.set_cover("cover.jpg", cover_img, create_page=False)

    css = epub.EpubItem(
        uid="style", file_name="style/main.css",
        media_type="text/css", content=EPUB_CSS.encode("utf-8"),
    )
    book.add_item(css)

    cover_html = f"""<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-TW">
<head><title>封面</title></head>
<body style="margin:0;padding:0;text-align:center;">
<img src="cover.jpg" alt="cover" style="max-width:100%;max-height:100%;display:block;margin:0 auto;"/>
</body>
</html>"""
    cover_page = epub.EpubHtml(title="封面", file_name="cover.xhtml", lang="zh-TW")
    cover_page.content = cover_html.encode("utf-8")
    book.add_item(cover_page)

    # 每章寫成獨立的 xhtml 檔。Kobo 在 spine 上的檔案邊界會自動斷頁，
    # 比 CSS page-break-before 可靠。先前曾改成單檔 + 錨點導航的版本，
    # 結果章節在 Kobo 上連在一起不換頁，又改回來。
    spine = [cover_page]
    toc = []
    for i, ch in enumerate(chapters, 1):
        ch_id = f"chapter_{i}"
        ch_filename = f"chapter_{i}.xhtml"
        ch_html = f"""<?xml version='1.0' encoding='utf-8'?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-TW">
<head>
<title>{ch['title']}</title>
<link rel="stylesheet" type="text/css" href="style/main.css"/>
</head>
<body>
<h1 class="chapter-start">{ch['title']}</h1>
{ch['content']}
</body>
</html>"""
        ch_page = epub.EpubHtml(title=ch["title"], file_name=ch_filename, lang="zh-TW")
        ch_page.content = ch_html.encode("utf-8")
        ch_page.add_item(css)
        book.add_item(ch_page)
        spine.append(ch_page)
        toc.append(epub.Link(ch_filename, ch["title"], ch_id))

    book.toc = toc
    book.add_item(epub.EpubNcx())
    nav_item = epub.EpubNav()
    book.add_item(nav_item)
    # nav 用 linear="no" 進 spine，讓 Kobo 識別 EPUB 結構完整
    book.spine = [cover_page, (nav_item, "no")] + spine[1:]

    epub.write_epub(output_path, book)


def convert_to_kepub(epub_path: str) -> str:
    """用 kepubify 把標準 EPUB 轉成 Kobo 的 .kepub.epub。

    kepub 在每個句子外包 <span class="koboSpan">，這是 Kobo 跨頁畫重點、
    閱讀統計、書籤精準度等功能仰賴的標記。沒有 kepubify 就拋例外。

    ⚠️ 此函式抄自 Pharos repo yt2epub.py 的 convert_to_kepub()，必須跟
    Pharos 端保持邏輯一致。
    """
    import subprocess

    kepubify = shutil.which("kepubify")
    if not kepubify:
        for candidate in ("/opt/homebrew/bin/kepubify", "/usr/local/bin/kepubify", "/usr/bin/kepubify"):
            if Path(candidate).exists():
                kepubify = candidate
                break
    if not kepubify:
        raise RuntimeError(
            "找不到 kepubify，無法產生 Kobo 原生格式（brew install kepubify / apt install kepubify）"
        )

    src = Path(epub_path)
    out_dir = src.parent
    proc = subprocess.run(
        [kepubify, "-o", str(out_dir), str(src)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"kepubify 失敗（rc={proc.returncode}）: {proc.stderr.strip()[-200:]}")

    converted = out_dir / f"{src.stem}_converted.kepub.epub"
    if not converted.exists():
        raise RuntimeError(f"kepubify 沒輸出預期檔案：{converted.name}")

    final = out_dir / f"{src.stem}.kepub.epub"
    converted.replace(final)
    src.unlink(missing_ok=True)
    logger.info(f"已轉成 kepub: {final.name}")
    return str(final)


def build_digest_kepub(
    digest_filename_stem: str,
    digest_content: str,
    paper_metas: list[dict],
) -> str:
    """完整流程：Digest .md → epub → kepubify → .kepub.epub。"""
    import tempfile

    meta, chapters = parse_digest_markdown(digest_content)

    tmp_dir = Path(tempfile.gettempdir()) / "anamnesis_kepub"
    tmp_dir.mkdir(exist_ok=True)
    safe_name = _safe_filename(digest_filename_stem, max_len=100)
    epub_path = tmp_dir / f"{safe_name}.epub"

    build_epub(meta, chapters, str(epub_path))

    # 真正轉成 Kobo 原生格式（加 koboSpan，讓 Kobo 識別為書）
    return convert_to_kepub(str(epub_path))


def upload_to_kobo(kepub_path: str) -> str:
    """上傳到 Pharos 同款 Kobo 同步資料夾。"""
    from vocabulary_manager import USE_DROPBOX_API

    filename = Path(kepub_path).name

    if USE_DROPBOX_API:
        from dropbox_uploader import _get_client
        from dropbox.files import WriteMode

        kobo_dropbox_path = "/應用程式/Rakuten Kobo"
        remote = f"{kobo_dropbox_path}/{filename}"

        dbx = _get_client()
        with open(kepub_path, "rb") as f:
            content = f.read()
        dbx.files_upload(
            content, remote,
            mode=WriteMode("overwrite"),
            autorename=False, mute=True,
        )
        return remote

    else:
        local_kobo = Path(os.environ.get(
            "DROPBOX_KOBO_LOCAL_PATH",
            str(Path.home() / "Dropbox" / "應用程式" / "Rakuten Kobo"),
        ))
        local_kobo.mkdir(parents=True, exist_ok=True)
        dest = local_kobo / filename
        shutil.copy2(kepub_path, dest)
        return str(dest)


# ═══════════════════════════════════════════════════════
# SHA256 漂移偵測
# ═══════════════════════════════════════════════════════

def _verify_pharos_drift() -> None:
    """檢查 Pharos 的 EPUB_CSS 是否跟 Anamnesis 端一致。"""
    pharos_path = Path(os.environ.get(
        "PHAROS_REPO_PATH",
        str(Path.home() / "yt2epub")
    ))
    pharos_yt2epub = pharos_path / "yt2epub.py"

    if not pharos_yt2epub.exists():
        # 也檢查 ~/pharos
        alt_path = Path.home() / "pharos" / "yt2epub.py"
        if alt_path.exists():
            pharos_yt2epub = alt_path
        else:
            logging.getLogger("pharos_drift").debug(
                f"Pharos 不在 {pharos_yt2epub}，跳過漂移檢查"
            )
            return

    try:
        src = pharos_yt2epub.read_text(encoding="utf-8")
    except Exception as e:
        logging.getLogger("pharos_drift").warning(f"Pharos yt2epub.py 讀取失敗: {e}")
        return

    m = re.search(r'EPUB_CSS\s*=\s*"""(.*?)"""', src, re.DOTALL)
    if not m:
        logging.getLogger("pharos_drift").warning(
            "Pharos yt2epub.py 找到但 EPUB_CSS 抓不到"
        )
        return

    pharos_css = m.group(1)
    pharos_hash = hashlib.sha256(pharos_css.encode("utf-8")).hexdigest()

    if pharos_hash == PHAROS_EPUB_CSS_SHA256:
        logging.getLogger("pharos_drift").debug(
            f"Pharos EPUB_CSS hash 一致 ({pharos_hash[:12]}...)"
        )
        return

    if PHAROS_EPUB_CSS_SHA256 == "BOOTSTRAP_WILL_FILL_THIS":
        logging.getLogger("pharos_drift").info(
            "Pharos hash 尚未 bootstrap，跳過漂移檢查"
        )
        return

    drift_logger = logging.getLogger("pharos_drift")
    drift_logger.error("=" * 60)
    drift_logger.error("🚨 PHAROS EPUB_CSS DRIFT DETECTED")
    drift_logger.error(f"Pharos 當前 hash:    {pharos_hash}")
    drift_logger.error(f"Anamnesis 紀錄 hash: {PHAROS_EPUB_CSS_SHA256}")
    drift_logger.error("")
    drift_logger.error("修復：python3 init_exegesis.py --refresh-pharos-hash")
    drift_logger.error("=" * 60)

    try:
        _push_drift_telegram_warning(pharos_hash, PHAROS_EPUB_CSS_SHA256)
    except Exception as e:
        drift_logger.warning(f"Telegram 警告推送失敗: {e}")

    try:
        from vault_writer import write_state_json
        write_state_json("state/drift_alert.json", {
            "alerted_at": datetime.now().isoformat(),
            "pharos_current_hash": pharos_hash,
            "anamnesis_recorded_hash": PHAROS_EPUB_CSS_SHA256,
            "resolved": False,
        })
    except Exception:
        pass


def _push_drift_telegram_warning(pharos_hash: str, anamnesis_hash: str) -> None:
    """推 Telegram 訊息提醒漂移。"""
    import requests

    token = os.environ.get("EXEGESIS_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    msg = (
        "🚨 <b>Pharos EPUB_CSS Drift Detected</b>\n\n"
        f"Pharos 當前: <code>{pharos_hash[:16]}...</code>\n"
        f"Anamnesis 紀錄: <code>{anamnesis_hash[:16]}...</code>\n\n"
        "Digest kepub 排版會跟訪談 kepub 不一致。\n"
        "修復：<code>python3 init_exegesis.py --refresh-pharos-hash</code>"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "HTML",
    }, timeout=5)


def refresh_pharos_css_hash() -> dict:
    """重新計算 Pharos 當前 EPUB_CSS hash，並更新到本檔案。"""
    pharos_path = Path(os.environ.get(
        "PHAROS_REPO_PATH",
        str(Path.home() / "yt2epub")
    ))
    pharos_yt2epub = pharos_path / "yt2epub.py"

    if not pharos_yt2epub.exists():
        alt_path = Path.home() / "pharos" / "yt2epub.py"
        if alt_path.exists():
            pharos_yt2epub = alt_path
        else:
            raise FileNotFoundError(f"Pharos 不在 {pharos_yt2epub}")

    src = pharos_yt2epub.read_text(encoding="utf-8")
    m = re.search(r'EPUB_CSS\s*=\s*"""(.*?)"""', src, re.DOTALL)
    if not m:
        raise RuntimeError("Pharos yt2epub.py 找不到 EPUB_CSS 常數")

    pharos_css = m.group(1)
    new_hash = hashlib.sha256(pharos_css.encode("utf-8")).hexdigest()

    this_file = Path(__file__)
    content = this_file.read_text(encoding="utf-8")
    new_content = re.sub(
        r'PHAROS_EPUB_CSS_SHA256 = "[^"]*"',
        f'PHAROS_EPUB_CSS_SHA256 = "{new_hash}"',
        content,
        count=1,
    )

    if new_content == content:
        return {"old_hash": PHAROS_EPUB_CSS_SHA256, "new_hash": new_hash, "noop": True}

    this_file.write_text(new_content, encoding="utf-8")

    new_content_2 = re.sub(
        r'EPUB_CSS\s*=\s*"""(.*?)"""',
        f'EPUB_CSS = """{pharos_css}"""',
        new_content,
        count=1,
        flags=re.DOTALL,
    )
    if new_content_2 != new_content:
        this_file.write_text(new_content_2, encoding="utf-8")

    return {
        "old_hash": PHAROS_EPUB_CSS_SHA256,
        "new_hash": new_hash,
        "pharos_path": str(pharos_yt2epub),
        "css_synced": new_content_2 != new_content,
    }
