# 📡 放射科論文雷達 (paper-radar / radiology fork)

個人論文追蹤:自動抓 IR / MSK / Spine 相關期刊 + PubMed → 依興趣模型評分排序 →
Firebase RTDB → 手機/電腦網頁看,標記已讀 / 投票 / 🚩 / ⭐(收進 LLM wiki 佇列)。
fork 自 [drpwchen/paper-radar](https://github.com/drpwchen/paper-radar),**後端改成 Firebase + GitHub Actions**(原版是 Cloudflare)。

---

## 1. 這專案在做什麼

把幾十個放射科 feed 的洪流,在到你眼前前先過濾排序。每天自動跑一次,只把高分新論文推到你的私人網頁。興趣模型可調(`interest_model.json`),投票之後可回頭再訓練。

## 2. 現在進度到哪

| 模組 | 狀態 |
|---|---|
| 抓取 + 評分(`fetch_and_score.py`) | ✅ 移植原版(含 DOI 誤標修復) |
| OA 全文加值(`enrich.py`) | ✅ 多來源 route ladder:Unpaywall(遍歷 oa_locations)+ idconv + Semantic Scholar,PMC→Europe PMC render(SFX 關閉) |
| 推 RTDB(`push_to_rtdb.py`) | ✅ 新寫(Admin SDK) |
| 前端 SPA(`site/index.html`) | ✅ 新寫(需填 firebaseConfig) |
| GitHub Actions cron | ✅ 每日 UTC 23:00 |
| RTDB rules | ✅ `database.rules.json`(需合併到 income) |
| 50 字中文摘要(on-demand) | ✅ 由 Claude 手動跑,非自動化(見 §5) |
| ⭐→PDF 下載(`download_starred.py`) | ✅ 本機:讀 Firebase star 佇列 → 抓 OA PDF → 丟進 LLM wiki `new/` 收件匣(見 §6) |

**⏳ 上線前待辦(見下方 §5 checklist)**:填 firebaseConfig、設 GitHub Secrets、合併 rules、部署 Pages、跑一次驗證 feeds。

## 3. 架構速覽

```
GitHub Actions (每日 cron)
  ├─ fetch_and_score.py  → 抓 feeds、去重(SQLite 保 first_seen)、評分 → papers.json
  ├─ enrich.py           → 多來源查 OA(Unpaywall→idconv→Semantic Scholar),補可達的全文 PDF 連結
  └─ push_to_rtdb.py     → 寫 paperRadar/feed(Admin SDK,繞過 rules)
        │
        ▼
Firebase RTDB (income-41a40 專案,與收入 app 共用,節點隔離在 paperRadar/)
  ├─ feed              ← 論文清單(唯讀,後端寫)
  └─ userState/{uid}   ← 已讀/vote/flag/star(前端讀寫;每筆含 doi,star=收進 wiki 佇列)
        │
        ▼
GitHub Pages (site/index.html) ── Google 登入,手機/電腦開;每篇可標 已讀/vote/flag/⭐
        │
        ▼ (⭐ = 收進 LLM wiki 佇列)
本機手動:download_starred.py ── 讀 Firebase star → 抓 OA PDF → 丟進 LLM wiki 的 new/ 收件匣
                                  └─ 之後在 LLM wiki 專案說「整理 new/」→ 那邊自己 ingest
```

> **三段 pipeline**:radar(發現+評分+下載)→ **new/ 收件匣交接** → LLM wiki 專案(抽知識)。
> radar 只負責把 OA PDF 送進收件匣;抽取/寫 wiki 頁是 LLM wiki 專案的 Ingest 流程,兩邊職責不重疊。

技術棧:Python 3.11(feedparser / requests / firebase-admin)、Firebase RTDB + Auth、vanilla single-file SPA、GitHub Pages + Actions。**全程 $0。**

## 4. 常見坑 / 防雷

- 🔴 **service account 金鑰(`*firebase-adminsdk*.json`)絕不可進 repo**。已列入 `.gitignore`;金鑰走 GitHub Secrets `FIREBASE_SA_JSON`。
- **RTDB key 非法字元**:`item_id`(如 `doi:10.x/...`)含 `. / :`,前端用 `safeKey()` 轉底線才能當 userState key。
- **rules 是整專案共用一份**:部署時**合併**進 income 現有的 `users/` 規則,別覆蓋(見 `database.rules.json`)。
- **`feed` 節點 `.write:false` 是故意的**:只有後端 service account 能寫,前端手滑改不到。
- **databaseURL 區域**:`push_to_rtdb.py` 與前端都預設 `income-41a40-default-rtdb.firebaseio.com`。若 income RTDB 在別區(如 asia-southeast1),要改 `FIREBASE_DB_URL` secret 與前端 config。
- **PubMed feed 用 `[ta]` 最穩**;RSS(尤其 LWW/Springer)常壞。EuroRad 是 case 庫非期刊,已用 European Radiology 代替。
- **paper_radar.db 要留在 repo**:Actions commit 回來保存 `first_seen`(NEW 判定靠它),不要 gitignore。
- **OA 解析是「發現端」語意,不是「下載端」**:`enrich.py` 的 `pdf_reachable` **故意寬鬆**(403/逾時當可取,只擋 404/410),因為我們只要一條可展示的連結,不要把被 bot-block 的正版 OA 誤藏——**別**照 [paper-fetch](https://github.com/drpwchen/paper-fetch) 那樣加 `%PDF` magic-byte 驗證(那是真的要下載 PDF bytes 才需要,屬未來 ⭐→wiki 下載器的事)。
- **PMC 連結一律轉 Europe PMC `?pdf=render`**:PMC 落地頁已上 reCAPTCHA 擋 bot,原連結點開常卡;`_pmc_render_url` 把任何帶 PMCxxx 的 URL 轉成直出 PDF 的 render 端點。
- **OA 資料是公開的**:`papers.json` / `paper_radar.db` 被 Actions commit 進 **public** repo(登入只保護 Firebase 那份即時副本);個人 star/vote/flag 存 Firebase `userState`(有 rules 保護),**不會**進公開檔。

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

**⚠️ 摘要檔必須帶 title(防「放錯篇」):**
```json
{ "doi:10.1016/j.ejrad.2026.112968": {
    "title": "Permanent vs. Temporary embolic agents in genicular artery ...",
    "summary": "統合 22 篇… ==重點== …" } }
```
`push_summaries.py` 兩層驗證(靈感來自 [drpwchen/claude-paper-tools](https://github.com/drpwchen/claude-paper-tools) 的 CrossRef gate):
- **第一層(離線、硬擋)**:你宣稱的 title vs RTDB 實際 title 做 **Jaccard** 比對(門檻 0.75)。對不上 → **整批拒推**。
  ⚠️ 用 Jaccard(交集/聯集)不能用 `交集/較短者` —— 同主題不同論文共享大量領域詞會誤放行(實測 71% vs 45%)。
- **第二層(CrossRef、只警示不阻擋)**:查 DOI 是否存在、標題是否吻合。我們的 DOI 來自 PubMed(非捏造)、且部分註冊在 DataCite → 404/不符只代表**上游 DOI 可能有誤**,不代表摘要放錯篇。網路掛 → 標「未驗證」放行。
- 旗標:`--dry-run`(只驗證)、`--skip-crossref`(離線)。

**曾踩的坑:** 使用者只貼「Conclusions 段落」要我更新摘要,我**靠內容猜是哪篇**,把 ejrad 那篇的結論寫進 s00270 那篇(兩篇都是 GAE 統合分析)。→ **要改哪篇,一律以 DOI/標題為錨,絕不靠內容猜。**

**GRADE 證據等級(選填,實證研究才給):**
摘要條目可加 `grade` 區塊,Claude **只評 5 個 domain 的嚴重度 + 寫理由**,最終等級由 `grade_judge.py` 依 GRADE 規則**算**出來 —— 模型自報值若不符會被警告並以計算值為準(防模型喊一個聽起來很確定的等級)。
```json
"grade": {
  "outcome": "GAE vs 假手術的止痛效果",
  "starting_level": "high",        // RCT 為主→high;觀察性→low
  "domains": [{"name":"risk_of_bias","rating":"serious","justification":"…"}, …]
}
```
- 5 domain:`risk_of_bias / inconsistency / indirectness / imprecision / publication_bias`,`not_serious`(0)/`serious`(−1)/`very_serious`(−2)
- 升級(`large_effect`/`dose_response`/`opposing_confounding`)**僅觀察性研究、且完全沒有任何降級時**才允許 —— 腳本會擋掉違規升級
- 前端顯示徽章 `⊕⊕◯◯ 低` + 降級理由。單獨用:`python grade_judge.py input.json`
- 綜論/個案/指引/技術描述**不給 grade**(GRADE 是評「證據體」的,不適用)

**摘要格式慣例(重要):**
- **實證研究(RCT/世代/回溯/診斷/統合)必寫「結果/結論」**,不能只寫「比較什麼」。⚠️ **abstract 的 Results/Conclusions 在結尾**,讀 `papers.json` 全文(勿截斷,或看 `abstract[-800:]`);要準確結論可用 PubMed efetch 抓全文(見 git 歷史)。綜論/指引/個案/技術描述內容即可。
- **重點用 `==重點==` 標記** → 前端 `hl()` 渲染成螢光筆(`<mark>`)。每篇一對、務必成對。
- 療效類研究**帶證據等級判斷**(如「無對照」「sham RCT 無組間差異」「⚠️潛力大但未證實」),別被單組前後數字誤導。

---

## 6. ⭐→PDF 下載 → LLM wiki 收件匣(本機手動)

在前端把想收進知識庫的論文按 **⭐(收wiki)**,寫進 Firebase `userState.star`。之後本機跑:

```bash
python download_starred.py                 # 讀 star 佇列 → 抓 OA PDF → 丟進 LLM wiki/new/
python download_starred.py --dry-run       # 只列會抓什麼,不下載
python download_starred.py --wiki-new "D:/別的收件匣"   # 覆寫收件匣路徑
```

- **OA 連結**優先讀 `paper_radar.db` 的 `oa_pdf_url`(enrich 每日已算),查不到再 live 跑 `resolve_oa` 兜底。
- **下載端嚴格驗 `%PDF` magic byte**(付費牆常回 200 text/html 冒充);非 OA / 無 DOI → 報「缺全文」,需你手動下載後自己放進 `new/`(無機構帳號抓不到付費牆)。
- **去重** `download_state.json`(gitignore,單機):記已下載 DOI,LLM wiki 把 `new/`→`raw/` 封存後也不會被重抓;`--redo` 可強制重抓。
- 檔名用 DOI-like(`10.1007_xxx.pdf`),讓 LLM wiki 的 auto-classify 判成 PAPER。
- 憑證與 `push_to_rtdb.py` 同一套 service account;Firebase admin 讀 `userState`(繞 rules)。
- **交接點就是 `new/`**:PDF 進去後,到 LLM wiki 專案說「整理 new/」由那邊 ingest,radar 這邊不碰抽取。

---

### 維護規則(給下一個接手者)
- 只記「現況」,不寫 changelog(git 有)。數字要跟 reality 同步。
- 改結構/schema/介面 → 順手更新對應段落。抓到新坑 → 加進 §4。
