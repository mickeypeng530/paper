#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""grade_judge.py — 決定性(deterministic)GRADE 證據等級計算。

核心理念(移植自 drpwchen/claude-paper-tools,其再移植自 htlin222/robust-lit-review):
    **把「語意判斷」和「決定性計算」分開。**
    Claude 只負責評每個 domain 的嚴重度(serious / very_serious …)並寫理由;
    最終等級由本腳本用 GRADE 規則「算」出來,不讓模型自己喊一個聽起來很確定的答案。
    模型若自報 final_certainty 與計算值不符 → 警告,以計算值為準。

GRADE 規則:
    起始等級:RCT 為主 → high;觀察性研究 → low
    5 個降級 domain:risk_of_bias / inconsistency / indirectness / imprecision / publication_bias
        not_serious 0 | serious −1 | very_serious −2
    升級(僅觀察性研究、且完全沒有任何降級時才允許):
        large_effect / dose_response / opposing_confounding,各 0–2 分
    最終 = clamp(起始 + 淨變化) 於 [very_low, low, moderate, high]

零依賴(只用 stdlib)。可當 CLI,也可 `from grade_judge import grade`。

Usage:
    python grade_judge.py input.json          # 人類可讀報告
    python grade_judge.py - --json            # 從 stdin 讀,輸出機器可讀
"""
from __future__ import annotations

import argparse, json, sys

_LEVELS = ("very_low", "low", "moderate", "high")
_LEVEL_INDEX = {n: i for i, n in enumerate(_LEVELS)}
_SYMBOL = {"very_low": "⊕◯◯◯", "low": "⊕⊕◯◯", "moderate": "⊕⊕⊕◯", "high": "⊕⊕⊕⊕"}
_ZH = {"very_low": "極低", "low": "低", "moderate": "中等", "high": "高"}

_RATING_DOWNGRADE = {"not_serious": 0, "serious": -1, "very_serious": -2}
_REQUIRED_DOMAINS = ("risk_of_bias", "inconsistency", "indirectness",
                     "imprecision", "publication_bias")
_VALID_UPGRADES = ("large_effect", "dose_response", "opposing_confounding")


def compute_final_certainty(starting_level: str, total_change: int) -> str:
    start = _LEVEL_INDEX.get(str(starting_level).lower(), _LEVEL_INDEX["high"])
    return _LEVELS[max(0, min(len(_LEVELS) - 1, start + total_change))]


def grade(data: dict) -> dict:
    warnings: list[str] = []

    start = str(data.get("starting_level", "")).lower()
    if start not in ("high", "low"):
        warnings.append(f"starting_level '{data.get('starting_level')}' 無效 → 預設 high(RCT 為主)")
        start = "high"

    domains = data.get("domains") or []
    seen = {d.get("name") for d in domains}
    for req in _REQUIRED_DOMAINS:
        if req not in seen:
            warnings.append(f"缺 domain '{req}' → 視為 not_serious(0)")

    downgrade_sum, domain_rows = 0, []
    for d in domains:
        name = d.get("name", "?")
        rating = str(d.get("rating", "not_serious")).lower()
        if rating not in _RATING_DOWNGRADE:
            warnings.append(f"domain '{name}' rating '{rating}' 無效 → 視為 not_serious")
            rating = "not_serious"
        pts = _RATING_DOWNGRADE[rating]
        downgrade_sum += pts
        domain_rows.append((name, rating, pts, d.get("justification", "")))

    any_downgrade = downgrade_sum < 0

    upgrade_sum, upgrade_rows = 0, []
    for u in (data.get("upgrades") or []):
        name = u.get("name", "?")
        if name not in _VALID_UPGRADES:
            warnings.append(f"upgrade '{name}' 非 GRADE 認可標準 → 忽略"); continue
        if start != "low":
            warnings.append(f"upgrade '{name}' 忽略 — 升級僅適用觀察性研究(starting_level=low)"); continue
        if any_downgrade:
            warnings.append(f"upgrade '{name}' 忽略 — GRADE 禁止在有任何降級時升級"); continue
        pts = max(0, min(2, int(u.get("points", 0) or 0)))
        upgrade_sum += pts
        upgrade_rows.append((name, pts, u.get("justification", "")))

    total_change = downgrade_sum + upgrade_sum
    final = compute_final_certainty(start, total_change)

    llm_label = str(data.get("final_certainty", "")).lower().replace(" ", "_")
    if llm_label in _LEVEL_INDEX and llm_label != final:
        warnings.append(f"模型自報 '{llm_label}' 與計算值 '{final}' 不符 —— 以計算值為準,"
                        "若你認為模型對,請回頭檢查某個 domain 的 rating")

    why = [f"{n}({r}): {j}" for n, r, p, j in domain_rows if p < 0]
    return {
        "outcome": data.get("outcome", ""),
        "starting_level": start,
        "domain_rows": domain_rows,
        "downgrade_sum": downgrade_sum,
        "upgrade_rows": upgrade_rows,
        "upgrade_sum": upgrade_sum,
        "total_change": total_change,
        "final_certainty": final,
        "symbol": _SYMBOL[final],
        "label_zh": _ZH[final],
        "why": why,
        "warnings": warnings,
    }


def compact(res: dict) -> dict:
    """壓成要存進 RTDB 的小物件。"""
    return {"level": res["final_certainty"], "sym": res["symbol"],
            "zh": res["label_zh"], "net": res["total_change"],
            "start": res["starting_level"], "why": res["why"]}


def render(res: dict) -> str:
    L = []
    if res["outcome"]:
        L.append(f"Outcome: {res['outcome']}")
    L.append(f"起始等級: {res['starting_level'].upper()}")
    L.append("")
    L.append("Domain            | Rating        | Δ")
    L.append("------------------|---------------|---")
    for n, r, p, _ in res["domain_rows"]:
        L.append(f"{n:<17} | {r:<13} | {p:+d}")
    L.append(f"{'降級合計':<15} | {'':<13} | {res['downgrade_sum']:+d}")
    if res["upgrade_rows"]:
        L.append("\n升級(觀察性研究且無任何降級):")
        for n, p, _ in res["upgrade_rows"]:
            L.append(f"  {n:<20} +{p}")
        L.append(f"  升級合計              +{res['upgrade_sum']}")
    L.append(f"\n淨變化: {res['total_change']:+d}")
    L.append(f"==> 最終證據等級(權威值): {res['symbol']} {res['label_zh']} ({res['final_certainty']})")
    if res["warnings"]:
        L.append("\n警告:")
        L += [f"  ⚠️ {w}" for w in res["warnings"]]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="決定性 GRADE 證據等級計算")
    ap.add_argument("input", help="JSON 檔路徑,或 '-' 讀 stdin")
    ap.add_argument("--json", action="store_true", help="輸出機器可讀 JSON")
    a = ap.parse_args()
    raw = sys.stdin.read() if a.input == "-" else open(a.input, encoding="utf-8").read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"invalid JSON: {e}", file=sys.stderr); return 2
    res = grade(data)
    print(json.dumps(res, ensure_ascii=False, indent=2) if a.json else render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
