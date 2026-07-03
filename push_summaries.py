#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把摘要合併進 RTDB 的 paperRadar/papers。

摘要由 Claude 在對話中手動產生(讀標題+abstract 寫 50 字中文重點),
不進 cron、不用 API key。流程:
  1. Claude 產出 summaries.json = { "<item_id>": "50字摘要", ... }
  2. 跑 python push_summaries.py summaries.json
  3. 本工具讀 paperRadar/papers,把 summary 欄填進對應論文,寫回

摘要存在論文物件的 summary 欄;push_to_rtdb.py 的每日 cron 會保留它。

Usage:
    python push_summaries.py summaries.json
"""
import argparse, glob, json, os, sys
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, db

SCRIPT_DIR = Path(__file__).parent
DEFAULT_DB_URL = "https://income-41a40-default-rtdb.firebaseio.com"


def load_credentials():
    sa_json = os.environ.get("FIREBASE_SA_JSON")
    if sa_json:
        return credentials.Certificate(json.loads(sa_json))
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if path and Path(path).exists():
        return credentials.Certificate(path)
    for p in SCRIPT_DIR.glob("*firebase-adminsdk*.json"):
        return credentials.Certificate(str(p))
    sys.exit("✗ 找不到 service account 憑證")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("summaries", help="{item_id: 摘要} 的 JSON 檔")
    args = ap.parse_args()

    summaries = json.load(open(args.summaries, encoding="utf-8"))
    db_url = os.environ.get("FIREBASE_DB_URL", DEFAULT_DB_URL)

    firebase_admin.initialize_app(load_credentials(), {"databaseURL": db_url})
    ref = db.reference("paperRadar/papers")
    payload = ref.get() or {}
    papers = payload.get("papers", [])

    hit = 0
    for p in papers:
        s = summaries.get(p.get("item_id"))
        if s:
            p["summary"] = s; hit += 1

    ref.set(payload)
    print(f"✓ 合併摘要 {hit}/{len(summaries)} 筆 → {db_url}/paperRadar/papers "
          f"(全庫 {len(papers)} 篇,已有摘要 {sum(1 for p in papers if p.get('summary'))} 篇)")
    missing = [k for k in summaries if k not in {p.get('item_id') for p in papers}]
    if missing:
        print(f"⚠ {len(missing)} 個 item_id 在 RTDB 找不到(可能已被新一批洗掉):{missing[:3]}…")


if __name__ == "__main__":
    main()
