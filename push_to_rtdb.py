#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 papers.json 推進 Firebase RTDB 的 paperRadar/feed 節點。

用 Admin SDK(service account)寫入 → 繞過 security rules(rules 對 feed 設 .write:false,
只有這支後端能寫;前端只讀)。

憑證來源(擇一):
  1) 環境變數 FIREBASE_SA_JSON = service account 整份 JSON 字串(GitHub Actions 用這個)
  2) 環境變數 GOOGLE_APPLICATION_CREDENTIALS = 金鑰檔路徑(本地測試用)

DB URL:環境變數 FIREBASE_DB_URL,預設 income-41a40 的 default 實例。
若你的 RTDB 是別的區域(如 asia-southeast1),請設 FIREBASE_DB_URL 覆寫。

Usage:
    python push_to_rtdb.py [--in papers.json]
"""
import argparse, json, os, sys
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
    # 本地 fallback:資料夾內的 *firebase-adminsdk*.json
    for p in SCRIPT_DIR.glob("*firebase-adminsdk*.json"):
        return credentials.Certificate(str(p))
    sys.exit("✗ 找不到 service account 憑證(設 FIREBASE_SA_JSON 或 GOOGLE_APPLICATION_CREDENTIALS)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", default=str(SCRIPT_DIR / "papers.json"))
    args = ap.parse_args()

    db_url = os.environ.get("FIREBASE_DB_URL", DEFAULT_DB_URL)
    payload = json.load(open(args.infile, encoding="utf-8"))

    firebase_admin.initialize_app(load_credentials(), {"databaseURL": db_url})
    ref = db.reference("paperRadar/papers")
    ref.set(payload)

    n = len(payload.get("papers", []))
    print(f"✓ 推送 {n} 篇 → {db_url}/paperRadar/papers  (updated={payload.get('updated')})")


if __name__ == "__main__":
    main()
