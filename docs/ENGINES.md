# AI 引擎設定

儀表板的 AI 功能（投資分析、策略檢查、釘選規則編譯、聊天）都透過同一個入口呼叫模型。支援四種引擎，
你只需要設定其中一種。

| 引擎 | 是什麼 | 需要什麼 |
| --- | --- | --- |
| `codex` | 你自己登入的 Codex CLI | 安裝並登入 `codex` |
| `claude` | 你自己登入的 Claude CLI | 安裝並登入 `claude` |
| `openai` | 任何 OpenAI 相容的 HTTP API（xAI Grok、Gemini、OpenRouter、Ollama、LM Studio…） | `LLM_BASE_URL`、`LLM_MODEL`（雲端另需 `LLM_API_KEY`） |
| `command` | 任意命令列程式：prompt 從 stdin 進、答案從 stdout 出 | `LLM_COMMAND` |

## 如何選擇引擎

1. 設了 `ANALYSIS_ENGINE`（或 `data/llm.json` 的 `engine`）就用它，值為 `claude`、`codex`、`openai`、`command` 其中一個。
2. 沒設的時候自動偵測，順序：`codex`（在 PATH 上）→ `claude`（在 PATH 上）→ `openai`（有 `LLM_BASE_URL`）→ `command`（有 `LLM_COMMAND`）。
3. 四種都不可用時，會印出設定說明。

右上角的 `/api/info` 會回報目前引擎的名稱、是 CLI 還是 API、以及 API 的主機名稱（不含網址路徑和金鑰），不會對外連線。

## 環境變數

| 變數 | 說明 |
| --- | --- |
| `ANALYSIS_ENGINE` | 強制使用某個引擎 |
| `LLM_BASE_URL` | OpenAI 相容 API 的網址，例如 `https://api.x.ai/v1`；程式會呼叫 `{LLM_BASE_URL}/chat/completions` |
| `LLM_MODEL` | 模型名稱 |
| `LLM_API_KEY` | API 金鑰，**只能**用環境變數提供 |
| `LLM_TEMPERATURE` | 選填，例如 `0.2` |
| `LLM_COMMAND` | `command` 引擎的命令列（用 shell 風格解析，不經過 shell） |
| `LLM_ALLOW_INSECURE` | 設為 `1` 才允許用明文 `http://` 連到非本機的主機（不建議） |

環境變數的優先順序高於設定檔。

## 設定檔（選用）

`data/llm.json`（位置跟著 `STOCK_DASHBOARD_DATA`）：

```json
{"engine": "openai", "base_url": "http://localhost:11434/v1", "model": "llama3.1"}
```

可用欄位：`engine`、`base_url`、`model`、`command`。**不要把金鑰寫進這個檔案**：裡面的 `api_key` 會被忽略並在日誌留下一次警告。
檔案壞掉（不是合法 JSON）時會被忽略。

## 範例

### xAI Grok

```bash
export LLM_BASE_URL=https://api.x.ai/v1
export LLM_API_KEY=你的金鑰
export LLM_MODEL=grok-4        # 用你帳號可用的模型名稱
scripts/run_local.sh
```

### Google Gemini（OpenAI 相容端點）

```bash
export LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
export LLM_API_KEY=你的金鑰
export LLM_MODEL=gemini-2.5-pro
```

### OpenRouter

```bash
export LLM_BASE_URL=https://openrouter.ai/api/v1
export LLM_API_KEY=你的金鑰
export LLM_MODEL=anthropic/claude-sonnet-4   # 任一 OpenRouter 模型代號
```

### Ollama（本機）

```bash
ollama pull llama3.1
export LLM_BASE_URL=http://localhost:11434/v1
export LLM_MODEL=llama3.1                    # 本機不需要金鑰
```

LM Studio 同理，預設網址是 `http://localhost:1234/v1`。

### 任意命令列

```bash
export LLM_COMMAND='my-llm-cli --model foo'   # prompt 從 stdin 進，答案從 stdout 出
```

`LLM_COMMAND` 以你自己的權限執行（跟你在終端機打的指令一樣），在空的暫存資料夾裡，逾時 180 秒；
請只放你信任的指令。

## 隱私

- **CLI 引擎（codex / claude）**：資料送給該 CLI 背後的服務，等同你自己用它。
- **API 引擎**：你的**持倉、策略筆記、分析內容**會被送到你設定的服務商。請確認你能接受該服務商的資料政策。
  程式不會把金鑰或完整 prompt 寫進日誌或錯誤訊息。
- **本機 Ollama / LM Studio**（`localhost`）：資料不離開你的電腦。連到本機時也不會走系統代理。
- 連到非本機的 `http://` 會被拒絕（明文傳送你的資料），請用 `https://`。
- 回應大小上限 2 MB，單次呼叫逾時 180 秒。

## 模型品質提醒

所有提示詞都要求模型回傳**嚴格的 JSON**。模型如果把 JSON 包在 markdown 程式碼區塊或前後加說明文字，程式可以處理；
但很小的本機模型常常輸出壞掉的 JSON、漏欄位或編造引用，這種情況下程式會把該次結果當成失敗（驗證不會因此放寬）。
策略檢查的引用句與事實代號永遠由程式驗證；釘選規則的最終判斷由程式計算，不靠模型。

## 費用

- CLI 引擎依你的訂閱或額度計算。
- API 引擎依服務商計價；`data/llm_usage.jsonl` 會記錄每次呼叫的秒數與 token 數（服務商有回報時），
  費用欄位（`cost_usd`）對 API 引擎為空，請以服務商帳單為準。
- 一次完整分析大約會呼叫一次模型；策略檢查每檔一次；釘選規則只有「模板對不上」時才會用到模型（編譯一次）。

## 疑難排解

| 症狀 | 處理 |
| --- | --- |
| 找不到可用的 AI 引擎 | 依訊息設定四種方式之一 |
| `API 回傳 HTTP 401` | 金鑰不對或沒設 `LLM_API_KEY` |
| `API 回傳 HTTP 404` | `LLM_BASE_URL` 少了 `/v1`（或對應的路徑），或模型名稱不存在 |
| `API 連線失敗` | 網址/埠不對，或本機的 Ollama / LM Studio 沒啟動 |
| `拒絕用明文 http…` | 改用 `https://`，或確認安全後設 `LLM_ALLOW_INSECURE=1` |
| `API 回應格式不是 OpenAI chat/completions` | 該服務不是 OpenAI 相容格式，或網址指到錯的路徑 |
| 結果一直驗證失敗 | 換較大的模型；本機小模型常常無法穩定輸出 JSON |
| 示範模式 | `STOCK_DASHBOARD_DEMO=1` 時所有 AI 功能都會停用，與引擎設定無關 |
