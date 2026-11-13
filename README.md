# Stock Dashboard — 把你的交易筆記，對照成當下的事實

> **免責聲明**：本工具為個人研究輔助軟體，由程式與 AI 依公開資料整理資訊，**不構成任何投資建議或買賣邀約**，也**不保證獲利**。分析可能出錯、過時或自相矛盾；資料來自第三方，可能延遲或不完整；AI 可能產生不實內容。所有投資決策與風險由使用者自行承擔，作者非證券投資顧問，不對任何損失負責。

![首頁：觀察清單卡片](docs/img/index.png)

## 這是什麼

**剛開始投資的人，常常學了很多、也做了很多筆記，真正下單時卻忘記自己訂過的規則。**

停損要設在哪？趨勢不成立時能不能買？這筆是不是又在攤平？大多數虧損不是不知道規則，而是**在當下忘了**。

這個系統就是你的**規則提醒器**：把你自己的交易規則（筆記）放進來，它會對照這檔股票**當下的事實**（價格、均線、RSI、財報、新聞、大盤、你的持倉），逐條告訴你「通過／未通過／未知」，並附上**你筆記裡的原文出處與證據**，讓你在下手前看一眼，而不是靠記憶。

它也會回頭檢查你的**每一筆買入**：買的那一天，當時的趨勢、停損幅度、風報比，符不符合你自己的規則。

它**不是**選股明牌，也**不預測**漲跌。五面向分數**尚無回測證據**顯示高分之後表現較好：技術面分數用 5 年歷史回測（[`scripts/backtest_technical.py`](scripts/backtest_technical.py)），與同儕相比沒有顯著差異；財報、估值、新聞三軸由 AI 判斷，無法回測。請把它當成「整理資訊、提醒自己照自己的規則做」的工具。

## 立刻試試（不需要 AI 帳號）

```bash
git clone <this-repo> && cd stock_dashboard
docker compose up            # 或：./scripts/run_demo.sh（需要 Python 3.9+）
# 開 http://127.0.0.1:8013
```

示範模式使用**合成資料**（虛構的「示範公司 A–E」），不連網、不呼叫任何 AI；AI 分析、聊天與策略檢查的按鈕會停用，其餘功能（圖表、持倉、固定檢查項目、批次總覽、筆記管理）都能操作。

## 特色

| | |
|---|---|
| **引文由程式驗證** | 模型引用你筆記的每一句，程式逐字比對（必須存在、在所有筆記中唯一）；引用的事實代碼必須真的在事實表裡，證據文字由程式重新產生，不採信模型自己寫的。 |
| **「買入當時」與「現在」分開** | 你的每一筆買入，只用**買入日之前**的價格重算當時的趨勢、均線、停損幅度與風報比來檢查；不會拿今天的資料事後評價過去的買入。當時的財報、新聞、大盤沒有歷史資料就標為不可得，不編造。 |
| **固定檢查項目（釘選規則）** | 你直接輸入「停損不超過 7%」「RSI 超過 70 不追」；系統編譯成受限的運算式（本地範本優先，AI 協助為輔）、你確認解讀後，由**程式**每次判斷，不再花 AI 額度。無法程式判斷的（要看新聞、財報日曆、主觀判斷）誠實顯示「未知」。 |
| **批次總覽** | 一次比較多檔股票對你的規則的符合情形，釘選規則是固定欄位。 |
| **誠實的護欄** | 分數上限由程式強制（虧損公司、重挫股、週期股等），不是靠 prompt 請求；每個護欄觸發都留下紀錄。 |
| **本機優先** | 資料全在你的電腦；AI 呼叫使用**你自己登入的** `claude` 或 `codex` CLI；預設只綁 `127.0.0.1`。 |
| **K 線圖** | TradingView Lightweight Charts，含 MA10/20/50/200、成交量、畫趨勢線、買入點標記。 |
| **新手導覽** | 每個頁面第一次進入會用彈窗介紹功能。 |

<p align="center">
  <img src="docs/img/detail-chart.png" width="48%" alt="價格圖與均線、成交量">
  <img src="docs/img/detail-strategy.png" width="48%" alt="策略檢查：買入當時與現在">
</p>
<p align="center">
  <img src="docs/img/overview.png" width="48%" alt="批次總覽矩陣">
  <img src="docs/img/strategy.png" width="48%" alt="筆記管理與固定檢查項目">
</p>

## 它怎麼運作

```mermaid
flowchart LR
  N[你的策略筆記<br/>data/strategy] --> R[切塊 + BM25 檢索]
  P[價格 / 財報 / 新聞<br/>yfinance, RSS] --> F[事實表 registry<br/>TECH · DAILY · FUND · MARKET · HOLD · BUY n]
  H[你的持倉] --> F
  R --> L[你的 AI CLI<br/>claude / codex]
  F --> L
  L --> V{程式驗證<br/>引文唯一 · 事實代碼存在<br/>範圍 now / entry 強制}
  V --> U[策略檢查結果]
  T[固定檢查項目<br/>編譯一次 + 你確認] --> E[安全運算式<br/>程式直接計算]
  F --> E --> U
```

- 技術面與大盤分數、護欄、釘選規則都是**程式計算**；只有「從筆記挑出適用規則並對照事實」這一步由 AI 做，而且結果要通過上面的驗證。
- 詳細設計取捨見 [docs/DESIGN.md](docs/DESIGN.md)。

## 沒有 Claude 或 Codex 也能用

AI 功能支援四種引擎，只要有一種就行：

| 引擎 | 需要什麼 | 適用 |
|---|---|---|
| `claude` | 登入的 [Claude Code](https://github.com/anthropics/claude-code) CLI | 預設之一 |
| `codex` | 登入的 [Codex CLI](https://github.com/openai/codex) | 預設之一 |
| `openai` | 任何 OpenAI 相容 API：`LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` | Grok（xAI）、Gemini、OpenRouter、本機 Ollama／LM Studio |
| `command` | 任何「從標準輸入讀提示詞、輸出答案」的命令列工具：`LLM_COMMAND` | 其他 CLI |

例如用 Grok：`LLM_BASE_URL=https://api.x.ai/v1 LLM_MODEL=<模型> LLM_API_KEY=<金鑰> ./scripts/run_local.sh`。完整設定、隱私與模型品質的注意事項見 [docs/ENGINES.md](docs/ENGINES.md)。

## 在自己的電腦上使用真實資料

```bash
cd backend && pip install -r requirements.txt && cd ..
./scripts/run_local.sh               # http://127.0.0.1:8013 ，只綁本機
```

- AI 功能需要上面四種引擎之一；沒有也能用圖表、持倉、釘選規則（由程式判斷、不需 AI）與示範模式。
- 第一次打開是空的：在首頁輸入代號按「新增」；把你的筆記（`.md`／`.txt`）放進網頁「策略筆記」；在個股頁輸入持倉；按「更新基本面分析」。想先看畫面，先跑示範模式。
- 想從手機連：用 `scripts/run_tailscale.sh`（只綁 Tailscale 網卡）。**不要**用 `--host 0.0.0.0`，這個服務沒有登入驗證。

## 資料與隱私

- 你的觀察清單、持倉、筆記、分析、聊天紀錄都在 `data/`（已加入 `.gitignore`），不會被 commit。
- 持倉與分析內容會在你執行 AI 功能時，送到**你自己設定的** AI（Claude、OpenAI 或你選的 API 供應商；使用本機 Ollama 則不離開電腦）。第一次使用會顯示同意視窗。
- `claude` 的呼叫已關閉工具、外掛與 MCP；`codex` 以唯讀沙盒執行，但**不是**安全邊界（它仍可讀取整個檔案系統）。詳見 [SECURITY.md](SECURITY.md)。
- 每次 AI 呼叫的 token 與等效費用記在 `data/llm_usage.jsonl`。實測：完整分析約 0.15 USD、策略檢查約 0.13 USD（API 等值，訂閱方案不一定實際計費）。

## 誠實的限制

- 分數與護欄是依常理訂的規則，**沒有回測證據顯示預測力**（見上）。
- 資料來自 yfinance 與 Google News／Yahoo RSS，受其服務條款約束（個人使用）；可能延遲、缺漏，台股資料品質尤其不穩。
- LLM 可能誤判。程式只保證「引文存在、事實代碼存在」，**不保證**通過／未通過的判斷正確。
- 單人、本機工具：沒有帳號系統、沒有權限管理。
- 目前由作者自己使用，部分輸出用語與規則偏向作者的交易風格；可用 [profile](docs/DETAILS.zh-TW.md#自訂分析規則profile) 調整。

## 開發

```bash
python -m pytest -q tests           # 400+ 個測試，不需要網路
```

- 後端：FastAPI + yfinance；前端：原生 HTML/JS（無框架）＋ Lightweight Charts v4。
- CI 設定在 `.gitlab-ci.yml`（Python 3.9／3.11／3.12）。
- 完整功能說明與資料檔格式：[docs/DETAILS.zh-TW.md](docs/DETAILS.zh-TW.md)。
- 授權：[MIT](LICENSE)；第三方聲明見 [NOTICE](NOTICE)。
