# AGENTS.md — 給 AI 程式助手的專案守則

你是在協助使用者修改 **Stock Dashboard**：一個本機優先的股票研究工具（FastAPI 後端＋純 HTML／JS 前端）。使用者可能是剛開始寫程式的交易新手，請用繁體中文（台灣用語）說明你做了什麼，並盡量小步修改。

## 先看這些
- `README.md`（定位與快速開始）、`docs/DESIGN.md`（為什麼這樣設計）、`CONTRIBUTING.md`（測試與規則）、`docs/ENGINES.md`（AI 引擎）。
- 架構：`backend/`（FastAPI 與分析邏輯）、`frontend/`（沒有建置步驟）、`scripts/`（更新股價、產生分析、示範資料）、`tests/`、`profiles/`（可調整的門檻）、`examples/strategy/`（範例筆記）。

## 改完一定要跑（全部離線）
```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt pytest httpx   # 第一次
.venv/bin/python -m pytest -q tests
for f in frontend/*.js; do node --check "$f"; done
```
測試要全過才算完成。改了前端的行為，請補或更新 `tests/e2e/`（`SD_E2E=1 pytest tests/e2e -q`，需要 Playwright）。

## 常見的客製化，該改哪裡
- **加一條自己的規則／事實**：事實表在 `backend/fact_catalog.py`（新增事實要同時更新目錄，測試會檢查）、規則運算式在 `backend/pin_expr.py`、常見句型範本在 `backend/pin_templates.py`；可以不用 AI，直接在「策略筆記」頁輸入固定檢查項目。
- **調整門檻**：`profiles/default/profile.json`（停損、風報比、護欄上限）；不要直接改程式碼裡的數字。
- **新增資料來源**：`backend/data_provider.py`、`backend/news_feed.py`；資料路徑一律用 `backend/config.py` 的 `DATA_DIR`。
- **接其他 AI**：`backend/llm_engines.py`（支援 `claude`、`codex`、OpenAI 相容 API、自訂命令）；API 金鑰只能從環境變數讀，不要寫進檔案。
- **改介面文字**：`frontend/*.html`、`frontend/*.js`；所有動態文字用 `textContent` 或 `escapeHtml()`（`frontend/util.js`），不要把 API 資料直接塞進 `innerHTML`。

## 不要做的事（這些是刻意的設計，改了會讓安全或誠實性失效）
1. **不要放寬 Host／Origin 檢查**（`backend/app.py` 的中介層）。這個服務沒有登入，靠只綁 `127.0.0.1`＋防 DNS rebinding 保護持倉。要用其他主機名稱，請用 `STOCK_DASHBOARD_ALLOWED_HOSTS`。
2. **不要讓 AI 直接決定分數或通過／未通過**。分數上限、規則判斷、引文與事實引用的驗證都在程式裡（`score_guards.py`、`pin_expr.py`、`strategy.py` 的 `verify_rows`）。
3. **不要拿今天的資料評價過去的買入**：「買入當時」的事實只能用買入日之前的資料（`strategy.py` 的 `entry_facts`）。
4. **不要把個人資料寫進程式或文件**：`data/` 已被 `.gitignore` 排除，不要 `git add -f`；`tests/test_no_personal_refs.py` 會掃描個人路徑、信箱與私人網路位址。測試與示範一律使用合成資料。
5. **不要讓 `backend/seed.py` 覆蓋、刪除或穿越符號連結**：它只在資料夾是空的時「新增」檔案。
6. **不要把 API 金鑰、持倉或筆記記錄到日誌或錯誤訊息裡**（參考 `llm_engines.redact()` 與 `app.py` 的 `_redact_paths`）。
7. **不要加入自動下單、收費諮詢或個股推薦**。這是整理資訊與提醒紀律的工具，不構成投資建議；文件與介面保持這個說法。

## 相容性與風格
- Python 3.9 以上（CI 跑 3.9／3.11／3.12）：不要用 `match`、執行階段的 `X | Y` 型別、f-string 內再用相同種類的引號。
- 前端不要引入建置工具或 CDN；圖表函式庫已放在 `frontend/vendor/`。
- 繁體中文、台灣用語，中文與英數字之間加空格；不要使用簡體字或廣東話寫法。
- commit 訊息簡短說明「為什麼」。
