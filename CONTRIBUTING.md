# 參與貢獻

歡迎開 issue 回報問題或提出想法；Pull Request 不是必要的，有的話也很歡迎。這是個人研究用的工具，回覆可能不會很快。

## 專案結構

- `backend/`：FastAPI 後端與分析邏輯
- `frontend/`：純 HTML／CSS／JS，沒有建置步驟
- `scripts/`：更新股價、產生分析、示範資料、啟動腳本
- `tests/`：後端測試（pytest）；`tests/e2e/` 是瀏覽器端到端測試
- `docs/`：設計決策、AI 引擎設定與進階說明
- `examples/`、`profiles/`：範例策略筆記與內建分析規則

## 執行測試

```bash
pip install -r backend/requirements.txt pytest httpx
python -m pytest -q tests
for f in frontend/*.js; do node --check "$f"; done
```

瀏覽器端到端測試預設會略過，要跑的話請看 [tests/e2e/README.md](tests/e2e/README.md)：

```bash
pip install playwright && playwright install chromium
SD_E2E=1 python -m pytest tests/e2e -q
```

所有測試都離線執行，不會連網，也不會呼叫 AI。

## 幾條規則

- **不要放個人資訊**：`tests/test_no_personal_refs.py` 會掃描追蹤中的原始碼與文件，找出個人家目錄路徑、個人 e-mail 與私人網路位址（Tailscale）。請不要把這類內容寫進程式或文件。
- **評分護欄的測試資料集**：`tests/golden/guards.json` 凍結了目前護欄（`backend/score_guards.py`）的行為。只有在你刻意調整護欄門檻時，才用 `python tests/make_golden.py`（在 repo 根目錄執行）重新產生，並在 PR 或 commit 說明為什麼要改。
- **Python 3.9 相容**：CI 會用 Python 3.9、3.11、3.12 各跑一次，所以請避免 3.10 以上才有的語法，例如 `match` 陳述式、執行階段的 `X | Y` 型別寫法（請用 `Optional[X]`，或在檔案開頭加 `from __future__ import annotations` 並只用在型別註記），以及 f-string 裡再使用相同種類的引號（3.12 才允許）。
- **個人資料不進版控**：`data/` 底下的內容都被 `.gitignore` 排除，請不要用 `git add -f` 強制加入。
