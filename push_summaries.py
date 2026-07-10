#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把摘要合併進 RTDB 的 paperRadar/papers,附「放錯篇」防呆。

摘要由 Claude 在對話中手動產生(讀 abstract 寫中文重點),不進 cron、不用 API key。

⚠️ 為何有防呆:曾發生 Claude 只憑「結論內容」猜論文、把 A 篇的結論寫到 B 篇
   (兩篇都是 GAE 統合分析)。因此摘要檔**必須帶標題**,推送前做兩層驗證。

輸入格式(summaries.json):
    {
      "doi:10.1016/j.ejrad.2026.112968": {
        "title": "Permanent vs. Temporary embolic agents in genicular artery ...",
        "summary": "統合 22 篇... ==重點== ..."
      }, ...
    }

驗證兩層:
  第一層(離線、主防線、硬擋):比對你宣稱的 title vs RTDB 實際 title。
      對不上 → 拒推整批(不做部分寫入)。
  第二層(CrossRef、資料品質警示、永不阻擋):查 DOI 是否存在、標題是否吻合。
      我們的 DOI 來自 PubMed(非 LLM 捏造),且部分 DOI 註冊在 DataCite 而非 CrossRef
      → 404/對不上多半代表「DB 的 DOI 有誤」,不代表摘要放錯篇,故只警示不阻擋。

Usage:
    python push_summaries.py summaries.json
    python push_summaries.py summaries.json --skip-crossref   # 只跑第一層
    python push_summaries.py summaries.json --dry-run         # 只驗證,不寫入
"""
import argparse, json, os, re, sys, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, db

SCRIPT_DIR = Path(__file__).parent
DEFAULT_DB_URL = "https://income-41a40-default-rtdb.firebaseio.com"
CONTACT_EMAIL = "deer530530@gmail.com"          # CrossRef polite pool(免註冊)
UA = f"paper-radar/1.0 (mailto:{CONTACT_EMAIL})"
# Jaccard 門檻。用交集/聯集(而非交集/較短者)——同主題不同論文會共享大量領域詞
# (如 genicular artery embolization knee osteoarthritis),min() 當分母會誤放行。
LOCAL_THRESHOLD = 0.75      # 我宣稱的 title vs RTDB:我是複製的,應近乎 1.0
CROSSREF_THRESHOLD = 0.55   # CrossRef vs RTDB:期刊改寫標題,放寬一點

_STOP = {"the","and","for","with","from","that","this","are","was","were","its",
         "using","versus","between","after","before","study","trial","results",
         "analysis","review","systematic","meta"}


# --------------------------------------------------------------------------- #
# 標題比對(離線)
# --------------------------------------------------------------------------- #
def tokens(title):
    t = re.sub(r"[^a-z0-9一-鿿]+", " ", (title or "").lower())
    return {w for w in t.split() if len(w) > 2 and w not in _STOP}


def title_overlap(a, b):
    """Jaccard 相似度(交集/聯集)。同主題不同論文會落到 ~0.4,同一篇 ~1.0。"""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# --------------------------------------------------------------------------- #
# CrossRef 存在性閘(fail-open)
# --------------------------------------------------------------------------- #
def crossref_check(item_id, rtdb_title):
    """→ (status, detail)。status: ok | missing | mismatch | unverified"""
    if not item_id.startswith("doi:"):
        return "unverified", "無 DOI(hash id)"
    doi = item_id[4:]
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            msg = json.loads(r.read().decode("utf-8", "replace")).get("message") or {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "missing", "CrossRef 查無此 DOI"
        return "unverified", f"HTTP {e.code}"
    except Exception as e:                        # 網路/逾時/CrossRef 掛 → 放行
        return "unverified", f"{type(e).__name__}"
    cr_title = (msg.get("title") or [""])[0]
    if not cr_title:
        return "unverified", "CrossRef 無標題欄"
    ov = title_overlap(cr_title, rtdb_title)
    if ov < CROSSREF_THRESHOLD:
        return "mismatch", f"DOI 指向不同論文(重疊 {ov:.0%}):{cr_title[:70]}"
    return "ok", f"重疊 {ov:.0%}"


# --------------------------------------------------------------------------- #
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
    ap.add_argument("summaries", help='{item_id: {title, summary}} 的 JSON 檔')
    ap.add_argument("--skip-crossref", action="store_true", help="只跑第一層(離線)")
    ap.add_argument("--dry-run", action="store_true", help="只驗證,不寫入 RTDB")
    args = ap.parse_args()

    raw = json.load(open(args.summaries, encoding="utf-8"))

    # --- 格式檢查:一律要求帶 title ---
    untitled = [k for k, v in raw.items() if not isinstance(v, dict) or "title" not in v]
    if untitled:
        print("✗ 以下條目沒有帶 title,拒絕推送(防『放錯篇』):", file=sys.stderr)
        for k in untitled[:10]:
            print(f"   - {k}", file=sys.stderr)
        sys.exit(1)

    db_url = os.environ.get("FIREBASE_DB_URL", DEFAULT_DB_URL)
    firebase_admin.initialize_app(load_credentials(), {"databaseURL": db_url})
    ref = db.reference("paperRadar/papers")
    payload = ref.get() or {}
    papers = payload.get("papers", [])
    by_id = {p.get("item_id"): p for p in papers}

    hard_fail, skipped, unverified, warned, passed = [], [], [], [], []

    # --- 第一層:離線 title 比對 ---
    for iid, entry in raw.items():
        p = by_id.get(iid)
        if not p:
            skipped.append((iid, "RTDB 找不到此 item_id"))
            continue
        ov = title_overlap(entry["title"], p.get("title", ""))
        if ov < LOCAL_THRESHOLD:
            hard_fail.append((iid, f"標題對不上(重疊 {ov:.0%})\n"
                                   f"       你說 : {entry['title'][:75]}\n"
                                   f"       實際 : {p.get('title','')[:75]}"))
        else:
            passed.append(iid)

    # --- 第二層:CrossRef(fail-open) ---
    if passed and not args.skip_crossref:
        def check(iid):
            return iid, crossref_check(iid, by_id[iid].get("title", ""))
        with ThreadPoolExecutor(max_workers=6) as ex:
            for iid, (status, detail) in ex.map(check, passed):
                if status in ("missing", "mismatch"):
                    warned.append((iid, detail))      # 資料品質問題,不阻擋
                elif status == "unverified":
                    unverified.append((iid, detail))

    # --- 報告 ---
    print(f"驗證:{len(raw)} 條 | 通過 {len(passed)} | 硬擋 {len(hard_fail)} | "
          f"略過 {len(skipped)} | DOI 警示 {len(warned)} | 未驗證 {len(unverified)}")
    for iid, why in skipped:
        print(f"  ⏭  {iid}: {why}")
    for iid, why in unverified:
        print(f"  🟡 {iid}: CrossRef 未驗證({why})— 已放行")
    for iid, why in warned:
        print(f"  🟠 {iid}: DOI 資料可疑 — {why}(仍放行,建議查上游 DOI)")
    if hard_fail:
        print("\n🔴 以下條目未通過驗證,整批拒推(不做部分寫入):", file=sys.stderr)
        for iid, why in hard_fail:
            print(f"  ✗ {iid}: {why}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("\n✓ dry-run:全部通過驗證,未寫入。")
        return

    # --- 寫入 ---
    ok_ids = {i for i in passed}
    hit = 0
    for p in papers:
        if p.get("item_id") in ok_ids:
            p["summary"] = raw[p["item_id"]]["summary"]; hit += 1
    ref.set(payload)
    total_sum = sum(1 for p in papers if p.get("summary"))
    print(f"\n✓ 合併摘要 {hit} 筆 → {db_url}/paperRadar/papers "
          f"(全庫 {len(papers)} 篇,已有摘要 {total_sum} 篇)")


if __name__ == "__main__":
    main()
