# 📡 放射科論文雷達 (paper-radar / radiology fork)

個人論文追蹤:自動抓 IR / MSK / Spine 相關期刊 + PubMed → 依興趣模型評分排序 →
Firebase RTDB → 手機/電腦網頁看,標記已讀 / 投票 / 🚩。
fork 自 [drpwchen/paper-radar](https://github.com/drpwchen/paper-radar),**後端改成 Firebase + GitHub Actions**(原版是 Cloudflare)。

---

## 1. 這專案在做什麼

把幾十個放射科 feed 的洪流,在到你眼前前先過濾排序。每天自動跑一次,只把高分新論文推到你的私人網頁。興趣模型可調(`interest_model.json`),投票之後可回頭再訓練。

## 2. 現在進度到哪

| 模組 | 狀態 |
|---|---|
| 抓取 + 評分(`fetch_and_score.py`) | ✅ 移植原版(含 DOI 誤標修復) |
| Unpaywall OA 加值(`enrich.py`) | ✅ 移植原版(SFX 關閉) |
| 推 RTDB(`push_to_rtdb.py`) | ✅ 新寫(Admin SDK) |
| 前端 SPA(`site/index.html`) | ✅ 新寫(需填 firebaseConfig) |
| GitHub Actions cron | ✅ 每日 UTC 23:00 |
| RTDB rules | ✅ `database.rules.json`(需合併到 income) |
| 50 字中文摘要(on-demand) | ✅ 由 Claude 手動跑,非自動化(見 §5) |

**⏳ 上線前待辦(見下方 §5 checklist)**:填 firebaseConfig、設 GitHub Secrets、合併 rules、部署 Pages、跑一次驗證 feeds。

## 3. 架構速覽

```
GitHub Actions (每日 cron)
  ├─ fetch_and_score.py  → 抓 feeds、去重(SQLite 保 first_seen)、評分 → papers.json
  ├─ enrich.py           → Unpaywall 查 OA,補全文 PDF 連結
  └─ push_to_rtdb.py     → 寫 paperRadar/feed(Admin SDK,繞過 rules)
        │
        ▼
Firebase RTDB (income-41a40 專案,與收入 app 共用,節點隔離在 paperRadar/)
  ├─ feed              ← 論文清單(唯讀,後端寫)
  └─ userState/{uid}   ← 已讀/vote/flag(前端讀寫)
        │
        ▼
GitHub Pages (site/index.html) ── Google 登入,手機/電腦開
```

技術棧:Python 3.11(feedparser / requests / firebase-admin)、Firebase RTDB + Auth、vanilla single-file SPA、GitHub Pages + Actions。**全程 $0。**

## 4. 常見坑 / 防雷

- 🔴 **service account 金鑰(`*firebase-adminsdk*.json`)絕不可進 repo**。已列入 `.gitignore`;金鑰走 GitHub Secrets `FIREBASE_SA_JSON`。
- **RTDB key 非法字元**:`item_id`(如 `doi:10.x/...`)含 `. / :`,前端用 `safeKey()` 轉底線才能當 userState key。
- **rules 是整專案共用一份**:部署時**合併**進 income 現有的 `users/` 規則,別覆蓋(見 `database.rules.json`)。
- **`feed` 節點 `.write:false` 是故意的**:只有後端 service account 能寫,前端手滑改不到。
- **databaseURL 區域**:`push_to_rtdb.py` 與前端都預設 `income-41a40-default-rtdb.firebaseio.com`。若 income RTDB 在別區(如 asia-southeast1),要改 `FIREBASE_DB_URL` secret 與前端 config。
- **PubMed feed 用 `[ta]` 最穩**;RSS(尤其 LWW/Springer)常壞。EuroRad 是 case 庫非期刊,已用 European Radiology 代替。
- **paper_radar.db 要留在 repo**:Actions commit 回來保存 `first_seen`(NEW 判定靠它),不要 gitignore。

## 5. 接手者 cheatsheet / 上線步驟

**本地測試抓取:**
```bash
pip install -r requirements.txt
python fetch_and_score.py --only radiology --limit 3   # 單 feed 冒煙測試
python enrich.py --limit 5
GOOGLE_APPLICATION_CREDENTIALS=./income-41a40-*.json python push_to_rtdb.py
```

**上線 checklist:**
- [ ] `site/index.html` 填入 income 的 `apiKey` / `appId`(從 income 前端複製);確認 `databaseURL` 區域
- [ ] GitHub repo → Settings → Secrets and variables → Actions → 新增:
      - `FIREBASE_SA_JSON` = service account JSON 整份內容
      - `FIREBASE_DB_URL` = 你的 RTDB URL(可選,不設走預設)
- [ ] Firebase Console → RTDB → 規則 → 把 `database.rules.json` **合併**進現有 → 發布 → 回 income app 確認沒改壞
- [ ] GitHub repo → Settings → Pages → 由 `main` branch `/site` 發佈
- [ ] Actions 頁手動 `Run workflow` 跑一次 → 檢查 RTDB `paperRadar/feed` 有資料
- [ ] 手機開 Pages 網址,Google 登入,確認看得到論文

**調興趣模型:** 改 `interest_model.json` 的 `positive`/`negative` keyword 與 weight;`thresholds.recommend`=推薦門檻。

**跑摘要(on-demand,非自動化):** 使用者喊「跑摘要」時,Claude 讀 `papers.json`(或 RTDB `paperRadar/papers`)裡還沒 `summary` 的論文,逐篇寫中文重點,產出 `summaries.json`(`{item_id: 摘要}`),再 `GOOGLE_APPLICATION_CREDENTIALS=./income-*.json python push_summaries.py summaries.json`。摘要存論文的 `summary` 欄,每日 cron(`push_to_rtdb.py`)會**保留**不洗掉。刻意不進 cron / 不用 `ANTHROPIC_API_KEY` → 零 API 費用。前端卡片自動顯示 `📝 摘要` 與「出版/收錄日」。

**摘要格式慣例(重要):**
- **實證研究(RCT/世代/回溯/診斷/統合)必寫「結果/結論」**,不能只寫「比較什麼」。⚠️ **abstract 的 Results/Conclusions 在結尾**,讀 `papers.json` 全文(勿截斷,或看 `abstract[-800:]`);要準確結論可用 PubMed efetch 抓全文(見 git 歷史)。綜論/指引/個案/技術描述內容即可。
- **重點用 `==重點==` 標記** → 前端 `hl()` 渲染成螢光筆(`<mark>`)。每篇一對、務必成對。
- 療效類研究**帶證據等級判斷**(如「無對照」「sham RCT 無組間差異」「⚠️潛力大但未證實」),別被單組前後數字誤導。

---

### 維護規則(給下一個接手者)
- 只記「現況」,不寫 changelog(git 有)。數字要跟 reality 同步。
- 改結構/schema/介面 → 順手更新對應段落。抓到新坑 → 加進 §4。
