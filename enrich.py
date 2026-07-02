#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""加值層 v1：
   ① Unpaywall → OA 狀態 + PDF URL（可靠，已驗證）
   ② SFX 深連結 → 每篇都產一個一定有效的機構 SFX URL（瀏覽器點開即解析全文）
   ③ inst_subscribed 自動判定：暫緩（SFX XML 雙層編碼難穩定解析，見 sign-off 待決）
   完成後重新匯出 papers.json。

Usage:
    python enrich.py [--db paper_radar.db] [--config config.yaml] [--limit N] [--redo]
"""
import argparse, html, json, re, sqlite3, time, urllib.parse
from datetime import date, datetime
from pathlib import Path
import requests
import yaml

SCRIPT_DIR = Path(__file__).parent
UA = "Mozilla/5.0 (paper-radar)"

# 機構訂閱全文 target 的品牌字串 → 平台標籤。這些品牌名只出現在訂閱全文 target，
# 不與 OA target(Unpaywall/PMC)混。逐篇判定 = 此篇現在能否經機構訂閱取得全文。
SUB_BRANDS = {
    "LWW Total Access": "Ovid-LWW", "Ovid": "Ovid",
    "Wiley Online Library": "Wiley", "ClinicalKey": "ClinicalKey",
    "SpringerLink": "Springer", "New England Journal of Medicine": "NEJM",
    "JAMA Network": "JAMA", "American Medical Association Journals": "JAMA",
    "ScienceDirect": "ScienceDirect", "EBSCOhost": "EBSCO",
}


def unpaywall(doi, email):
    """→ (oa_status, pdf_url)。closed/錯誤分別回 ('closed',None)/(None,None)。"""
    try:
        r = requests.get(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}",
                         params={"email": email}, headers={"User-Agent": UA}, timeout=20)
        if r.status_code != 200:
            return None, None
        u = r.json()
        if u.get("is_oa"):
            loc = u.get("best_oa_location") or {}
            return u.get("oa_status", "oa"), (loc.get("url_for_pdf") or loc.get("url"))
        return "closed", None
    except Exception:
        return None, None


def pdf_reachable(url):
    """OA 全文是否『實際取得到』。只在明確找不到時回 False：
       URL 缺、或 HTTP 404/410。其餘（200、403 被擋機器人、逾時等暫時性）一律視為可取(True)，
       避免把正常 OA 誤藏（出版社常擋自動請求）。隔天 recheck 會再驗一次。"""
    if not url:
        return False
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15,
                         stream=True, allow_redirects=True)
        code = r.status_code
        r.close()
        return code not in (404, 410)
    except Exception:
        return True


def sfx_link(doi, cfg):
    sx = cfg["enrich"]["inst_sfx"]
    return f"{sx['base']}?sid={sx['sid']}&id=doi:{urllib.parse.quote(doi)}"


def sfx_subscription(doi, cfg):
    """抓 SFX detailed XML，雙層 unescape 後找訂閱品牌字串。
       → (subscribed:0/1/None, platforms:str)。網路錯誤回 (None,'')。"""
    sx = cfg["enrich"]["inst_sfx"]
    url = (f"{sx['base']}?sid={sx['sid']}&id=doi:{urllib.parse.quote(doi)}"
           f"&sfx.response_type=multi_obj_detailed_xml")
    try:
        h = requests.get(url, headers={"User-Agent": UA}, timeout=30).text
        dec = html.unescape(html.unescape(h))
        plats = sorted({lbl for k, lbl in SUB_BRANDS.items() if k in dec})
        return (1 if plats else 0), ",".join(plats)
    except Exception:
        return None, ""


def export_json(con, cfg, out):
    new_days = cfg.get("defaults", {}).get("new_days", 5)
    cols = ["item_id","title","source","source_name","group","authors","url","doi","abstract",
            "pub_date","score","tags","category","oa_status","oa_pdf_url","oa_first_date",
            "inst_subscribed","inst_platforms","sfx_url","first_seen","last_seen"]
    rows = con.execute(f"""SELECT {','.join(c if c!='group' else 'grp' for c in cols)}
                           FROM papers WHERE category!='skipped'
                           ORDER BY score DESC, first_seen DESC""").fetchall()
    papers = []
    for r in rows:
        d = dict(zip(cols, r))
        # OA 但實際抓不到全文（oa_status 標 OA 卻無可用 PDF）→ 先不顯示這篇。非 OA 照常顯示。
        if d["oa_status"] and d["oa_status"] != "closed" and not d["oa_pdf_url"]:
            continue
        d["tags"] = json.loads(d["tags"] or "[]")
        d["isNew"] = (date.fromisoformat(d["first_seen"]) - date.today()).days >= -new_days
        # OA 剛被機械重抓到（first_seen 以後才開放全文）→ 前端可單獨顯示「新開放」
        d["oaNew"] = bool(d["oa_first_date"]) and \
            (date.fromisoformat(d["oa_first_date"]) - date.today()).days >= -new_days
        papers.append(d)
    total = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    payload = dict(updated=datetime.now().strftime("%Y-%m-%d %H:%M"),
                   topic_groups=cfg["topic_groups"],
                   counts=dict(total_db=total, exported=len(papers)),
                   papers=papers)
    json.dump(payload, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return len(papers)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(SCRIPT_DIR / "paper_radar.db"))
    ap.add_argument("--config", default=str(SCRIPT_DIR / "config.yaml"))
    ap.add_argument("--out", default=str(SCRIPT_DIR / "papers.json"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--recheck", type=int, default=0, metavar="DAYS",
                    help="機械重抓：對「尚無 OA 全文、first_seen 在 DAYS 天內」的論文重跑 "
                         "Unpaywall/SFX，捕捉太新而當時抓不到、之後才開放的全文")
    ap.add_argument("--workers", type=int, default=6, help="並行網路請求數")
    ap.add_argument("--delay", type=float, default=0.15)
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    email = cfg["enrich"]["unpaywall"]["email"]

    con = sqlite3.connect(args.db)
    # 舊 DB 補欄位（idempotent）
    if not any(c[1] == "oa_first_date" for c in con.execute("PRAGMA table_info(papers)")):
        con.execute("ALTER TABLE papers ADD COLUMN oa_first_date TEXT")
        con.commit()

    if args.recheck:
        # 機械重抓：只挑「目前無 OA PDF」且夠新的論文（太老不太可能再開放，省請求）
        where = (f"doi != '' AND (oa_pdf_url IS NULL OR oa_pdf_url='') "
                 f"AND first_seen >= date('now', '-{args.recheck} days')")
    else:
        where = "doi != ''" + ("" if args.redo else " AND enriched=0")
    q = f"SELECT item_id, doi FROM papers WHERE {where} ORDER BY score DESC"
    if args.limit:
        q += f" LIMIT {args.limit}"
    rows = con.execute(q).fetchall()
    print(f"待加值: {len(rows)} 筆")

    do_sfx = cfg["enrich"]["inst_sfx"].get("enabled", True)

    def work(row):
        """純網路（thread 內）→ 回傳要寫的值，不碰 DB。"""
        iid, doi = row
        oa_status, oa_pdf = unpaywall(doi, email)
        # 標成 OA 但實際抓不到（URL 缺/404）→ 清掉 oa_pdf；匯出時這篇會被隱藏，
        # 等 recheck 哪天真的抓到才回來顯示。非 OA(closed/None) 不受影響。
        if oa_status and oa_status != "closed" and not pdf_reachable(oa_pdf):
            oa_pdf = None
        inst_sub, inst_plat = sfx_subscription(doi, cfg) if do_sfx else (None, "")
        return iid, oa_status, oa_pdf, inst_sub, inst_plat, sfx_link(doi, cfg)

    from concurrent.futures import ThreadPoolExecutor
    n_oa = n_inst = done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for iid, oa_status, oa_pdf, inst_sub, inst_plat, sfx in ex.map(work, rows):
            if oa_status and oa_status != "closed":
                n_oa += 1
            if inst_sub:
                n_inst += 1
            # 首次出現 OA PDF 時記下開放日（COALESCE 不覆寫舊值）→ 前端 oaNew 徽章用
            oa_stamp = date.today().isoformat() if oa_pdf else None
            con.execute("""UPDATE papers SET oa_status=?, oa_pdf_url=?,
                           oa_first_date=COALESCE(oa_first_date, ?), inst_subscribed=?,
                           inst_platforms=?, sfx_url=?, enriched=1 WHERE item_id=?""",
                        (oa_status, oa_pdf, oa_stamp, inst_sub, inst_plat, sfx, iid))
            done += 1
            if done % 50 == 0:
                con.commit(); print(f"  {done}/{len(rows)}  OA={n_oa}  機構={n_inst}", flush=True)
    con.commit()

    n_exported = export_json(con, cfg, args.out)
    from collections import Counter
    plats = Counter()
    for (p,) in con.execute("SELECT inst_platforms FROM papers WHERE inst_platforms!='' AND inst_platforms IS NOT NULL"):
        for x in p.split(","):
            if x: plats[x] += 1
    print(f"\n{'='*56}")
    print(f"加值完成: {len(rows)} 筆  🟢 OA 可取={n_oa}  🏥 機構可取={n_inst}")
    print(f"機構平台分布: {dict(plats)}")
    print(f"重新匯出: {n_exported} 篇 → {args.out}")
    con.close()


if __name__ == "__main__":
    main()
