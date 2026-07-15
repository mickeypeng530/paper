#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把前端標 ⭐(userState.star=true)的論文抓 OA 全文 PDF,丟進 LLM wiki 的 new/ 收件匣。
之後在 LLM wiki 專案說「整理 new/」即進它的 ingest 流程(auto-classify PAPER/BOOK → 寫 wiki 頁)。

定位:radar 是「發現 + 下載」端,抽知識是 LLM wiki 專案的事,兩邊在 new/ 收件匣交接。
只抓 OA(無機構帳號);非 OA / 無 DOI → 報「缺全文」讓你手動補 PDF 進 new/。

OA 連結來源:優先讀 paper_radar.db 的 oa_pdf_url(enrich.py 每日已算);查不到再 live 跑
resolve_oa 兜底(太新、enrich 還沒輪到的 star)。下載後驗 %PDF magic byte —— 下載端要拿到
真 bytes,故嚴格驗證(不同於 enrich.py 的寬鬆「可達性」判定,那層只要可展示連結)。

去重:download_state.json 記已下載的 DOI(wiki 把 new/→raw/ 封存後也不會被重抓)。

Usage:
    python download_starred.py [--db paper_radar.db] [--config config.yaml]
                               [--wiki-new "<path>"] [--limit N] [--dry-run] [--redo]
"""
import argparse, json, os, re, sqlite3, sys, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
import firebase_admin
from firebase_admin import credentials, db

from enrich import resolve_oa      # 重用 enrich 的多來源 OA 解析(live fallback)

SCRIPT_DIR = Path(__file__).parent
DEFAULT_DB_URL = "https://income-41a40-default-rtdb.firebaseio.com"
DEFAULT_WIKI_NEW = Path.home() / "Claude_Work" / "LLM wiki" / "new"
STATE_FILE = SCRIPT_DIR / "download_state.json"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def load_credentials():
    """與 push_to_rtdb.py 同一套:env FIREBASE_SA_JSON / GOOGLE_APPLICATION_CREDENTIALS /
       本地 *firebase-adminsdk*.json。"""
    sa = os.environ.get("FIREBASE_SA_JSON")
    if sa:
        return credentials.Certificate(json.loads(sa))
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if path and Path(path).exists():
        return credentials.Certificate(path)
    for p in SCRIPT_DIR.glob("*firebase-adminsdk*.json"):
        return credentials.Certificate(str(p))
    sys.exit("✗ 找不到 service account 憑證(設 FIREBASE_SA_JSON 或 GOOGLE_APPLICATION_CREDENTIALS)")


def is_pdf(b):
    return len(b) > 1000 and b[:4] == b"%PDF"


def grab_pdf(url):
    """下載並驗 %PDF magic byte。付費牆/Cloudflare 常回 200 text/html 冒充 → 一律驗 bytes。
       成功回 bytes,否則回 None。"""
    try:
        r = requests.get(url, headers={"User-Agent": BROWSER_UA, "Accept": "application/pdf,*/*"},
                         timeout=90, allow_redirects=True)
    except Exception as e:
        print(f"      下載失敗: {e}")
        return None
    if r.status_code == 200 and is_pdf(r.content):
        return r.content
    print(f"      非有效 PDF(HTTP {r.status_code} · {len(r.content)} bytes,多半付費牆回 HTML)")
    return None


def safe_name(doi):
    """DOI → 檔名。保留 DOI-like 形狀,方便 wiki auto-classify 判成 PAPER(見其 CLAUDE.md Step 0)。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", doi) + ".pdf"


def collect_starred(db_url):
    firebase_admin.initialize_app(load_credentials(), {"databaseURL": db_url})
    us = db.reference("paperRadar/userState").get() or {}
    out = []
    for uid, items in (us or {}).items():
        for key, v in (items or {}).items():
            if isinstance(v, dict) and v.get("star"):
                out.append(dict(doi=(v.get("doi") or "").strip(),
                                title=v.get("title") or "", key=key))
    return out


def db_oa_url(con, doi):
    row = con.execute("SELECT oa_pdf_url FROM papers WHERE doi=?", (doi,)).fetchone()
    return row[0] if row and row[0] else None


def load_state():
    if STATE_FILE.exists():
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state):
    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def main():
    # 本機 Windows console 預設 cp950,印 emoji(⭐✓)會 UnicodeEncodeError → 強制 UTF-8 輸出
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(SCRIPT_DIR / "paper_radar.db"))
    ap.add_argument("--config", default=str(SCRIPT_DIR / "config.yaml"))
    ap.add_argument("--wiki-new", default=str(DEFAULT_WIKI_NEW),
                    help="LLM wiki 收件匣資料夾(PDF 丟這裡)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="只列要抓什麼,不下載")
    ap.add_argument("--redo", action="store_true", help="忽略 state,已下載的也重抓")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    email = cfg.get("enrich", {}).get("unpaywall", {}).get("email")
    db_url = os.environ.get("FIREBASE_DB_URL", DEFAULT_DB_URL)
    inbox = Path(args.wiki_new)
    if not inbox.exists():
        sys.exit(f"✗ 收件匣不存在: {inbox}(確認 LLM wiki 專案路徑,或用 --wiki-new 指定)")

    starred = collect_starred(db_url)
    if args.limit:
        starred = starred[: args.limit]
    print(f"⭐ 佇列: {len(starred)} 篇")
    if not starred:
        print("(前端還沒有標 ⭐ 的論文)")
        return

    state = load_state()
    con = sqlite3.connect(args.db)
    stats = dict(downloaded=0, already=0, no_oa=0, no_doi=0, failed=0)

    for i, s in enumerate(starred, 1):
        doi, title = s["doi"], s["title"]
        head = f"[{i}/{len(starred)}] {title[:60]}"
        if not doi:
            print(f"{head}\n      ⚠ 無 DOI,無法 OA 下載 → 手動補")
            stats["no_doi"] += 1
            continue
        if not args.redo and doi in state:
            stats["already"] += 1
            continue
        target = inbox / safe_name(doi)
        if not args.redo and target.exists():
            state.setdefault(doi, dict(at=None, file=target.name))
            stats["already"] += 1
            continue

        # OA 連結:先 DB(enrich 已算),再 live 兜底
        url = db_oa_url(con, doi)
        if not url and email:
            _, url = resolve_oa(doi, email)
        print(f"{head}\n      DOI {doi}")
        if not url:
            print("      ✗ 缺全文(非 OA 或無可達 PDF)→ 手動補進 new/")
            stats["no_oa"] += 1
            continue
        if args.dry_run:
            print(f"      [dry-run] 會抓: {url}")
            continue

        pdf = grab_pdf(url)
        if not pdf:
            stats["failed"] += 1
            continue
        target.write_bytes(pdf)
        state[doi] = dict(at=datetime.now(timezone.utc).isoformat(), file=target.name)
        print(f"      ✓ 存 → new/{target.name} ({len(pdf)} bytes)")
        stats["downloaded"] += 1

    if not args.dry_run:
        save_state(state)
    con.close()

    print(f"\n{'='*56}")
    print(f"⬇ 下載 {stats['downloaded']} · 已有 {stats['already']} · "
          f"缺全文 {stats['no_oa']} · 無DOI {stats['no_doi']} · 失敗 {stats['failed']}")
    if stats["downloaded"]:
        print(f"→ PDF 已進收件匣: {inbox}")
        print("  下一步:到 LLM wiki 專案說「整理 new/」即進 ingest 流程。")
    if stats["no_oa"] or stats["no_doi"]:
        print("  缺全文的:非 OA(無機構帳號抓不到),需你手動下載後放進 new/。")


if __name__ == "__main__":
    main()
