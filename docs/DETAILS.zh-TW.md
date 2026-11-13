# 詳細說明（繁體中文）

> 這是完整的功能與資料檔說明。快速開始與總覽請看根目錄的 [README](../README.md)。

> **免責聲明**：本工具為個人研究輔助軟體，由 AI 依公開資料自動產生分析，**不構成任何投資建議或買賣邀約**，也**不保證獲利**。分析可能出錯、過時或自相矛盾；資料來自第三方，可能延遲或不完整；AI 可能產生不實內容。所有投資決策與風險由使用者自行承擔，作者非證券投資顧問，不對任何損失負責。使用第三方資料請遵守各來源的服務條款。
>
> *Research aid only. AI-generated analysis from public data — not investment advice, no guarantee of results, may be wrong or outdated. You are solely responsible for your decisions and risk.*

股票看板：卡片總覽（股價位階 + 獲利兩關檢查 + 可切換時間尺度的區間位置bar + 新增/移除股票 + 美股/台股篩選 + 搜尋 + 排序(含持倉損益) + 關注星號置頂 + 持倉損益 + 手動更新按鈕 + 備份匯出/匯入）＋ 點卡片進詳細頁（TradingView式走勢圖、手動畫趨勢線、bar可切換1個月～三年時間尺度、基本面、Codex或Claude產生的完整投資分析、買入日期/均價標記）＋ **持倉Dashboard**（持倉比重圓餅圖、各檔損益長條圖、總市值/總成本/總損益統計）。

## 架構

- `backend/` — FastAPI + yfinance。股價/K線/基本面寫到`data/price_cache/{TICKER}.json`磁碟快取，一般請求都直接讀這份檔案，不會每次都打yfinance；只有全新加入、還沒快取過的股票才會即時打一次並順便寫入快取。`POST/DELETE /api/tickers`管理觀察清單，新增時自動嘗試`.TW`/`.TWO`解析台股代號，台股名稱另外查TWSE/TPEx開放資料拿中文簡稱。`POST /api/refresh/prices`、`POST /api/refresh/analysis`背景執行對應script更新快取，`GET /api/refresh/status`查進度。StaticFiles把`frontend/`掛在根目錄，一個指令啟動前後端。
- `frontend/` — 純HTML/CSS/JS，無框架。`index.html`卡片總覽，上方獨立一區「更新股價／更新基本面分析」按鈕（跟篩選/新增分開放，不會擠在一起）。`detail.html`+TradingView `lightweight-charts`畫K線圖，進頁面抓一次完整K線資料，之後切換時間尺度只是調整x軸可視窗格、不重抓K線不重建圖表，畫好的趨勢線（射線）也不會消失；位階/區間bar的數字仍會依新時間尺度打一次輕量API重算，但那不是K線資料，也不會動到圖表本身。
- `data/tickers.json` — 觀察清單，可在網頁上加/刪，或直接編輯這份檔案。**不進版控**（見下方「資料與隱私」），全新clone下來是空的。
- `data/favorites.json` — 特別關注清單，卡片右上角星號控制，`POST/DELETE /api/favorites/{ticker}`，關注的股票不管排序選什麼都固定置頂。**不進版控**。
- `data/price_cache/{TICKER}.json` — 股價K線+基本面磁碟快取，`refresh_prices.py`負責刷新。**不進版控**（天天變動、資料量也不小）。
- `data/analysis/{TICKER}.json` — Codex或Claude產生的質化分析，卡片與詳細頁都讀這份快取。**不進版控**（是個人觀察清單的衍生內容）。
- `data/holdings.json` — 持倉，每檔股票可記多筆買入交易（日期+均價+股數，股數選填），`POST /api/holdings/{ticker}`新增一筆、`DELETE /api/holdings/{ticker}/{lot_id}`刪單筆、`DELETE /api/holdings/{ticker}`清全部，卡片跟detail頁顯示的是加權平均成本算出的損益%，有填股數的話還會算損益金額跟目前市值。**不進版控**。
- `data/backups/` — 按網頁「匯出備份」把`tickers`/`favorites`/`holdings`/`analysis`打包成一個JSON，存一份在這裡＋觸發瀏覽器下載一份。`GET /api/backup/export`、`POST /api/backup/import`。**資料夾本身進版控**（clone下來就有這個資料夾，裡面附一份說明），裡面的`*.json`備份檔**不進版控**。
- `scripts/refresh_prices.py` — 重新打一輪yfinance，刷新`data/price_cache/`。快、便宜，設計給「常跑」。
- `scripts/generate_analysis.py` — 呼叫`codex exec`或`claude -p`（自動偵測，見下方「基本面分析引擎」），餵真實數字給LLM當事實依據，產生`data/analysis/`的質化分析。慢（每檔約1~2分鐘，預設3檔並行，可用`ANALYSIS_WORKERS`調整）、也比較貴，設計給「不用常跑」。**需要本機裝好且登入`codex`或`claude` CLI其中一個**，沒有的話CLI直接跑這支script或按網頁「更新基本面分析」按鈕都會印出清楚的安裝指示並跳過，其他功能不受影響；但新增股票時自動背景觸發的那次呼叫輸出有刻意丟棄（避免洗版），裝CLI前新增的股票不會自動生出分析，之後補裝好CLI再手動跑一次script或按按鈕即可。

## 資料與隱私

`data/tickers.json`、`data/favorites.json`、`data/analysis/`、`data/holdings.json`、`data/backups/*.json`都是**個人資料**（你的觀察清單、關注、AI生成的投資筆記、持倉、備份檔），已加進`.gitignore`不會被commit。全新clone這個repo下來，`data/`底下這些檔案都不存在（`data/backups/`資料夾本身會在，裡面是空的），網頁會顯示空的watchlist，自己用UI的「新增」慢慢加，或用「匯入備份」一次還原。

**注意**：`.gitignore`只保護之後的commit。如果這個repo在加`.gitignore`之前就commit過真實的`data/`內容，那些檔案仍留在舊commit歷史裡，`git log`、`git show`都翻得到，單純設public不會自動清掉。要徹底清除得用`git filter-repo`之類工具重寫歷史（或乾脆開一個全新repo重新commit一次乾淨版本），再評估是否要設成public。

## 環境需求

- Python 3.9+
- `pip install -r backend/requirements.txt`
- （選用）[Codex CLI](https://github.com/openai/codex) 或 [Claude Code](https://github.com/anthropics/claude-code) 其中一個，裝好並登入，才能用「更新基本面分析」功能跟`scripts/generate_analysis.py`（兩個都裝也可以，`generate_analysis.py`會自動挑一個，見下方）
- （選用）[Tailscale](https://tailscale.com/)，只有想用`scripts/run_tailscale.sh`做遠端存取時才需要

## 基本面分析引擎

`scripts/generate_analysis.py`（跟網頁的「更新基本面分析」按鈕）會自動偵測本機裝了哪個LLM CLI：

- 兩個都裝：預設用`codex`，可以設定環境變數強制指定：`ANALYSIS_ENGINE=claude python3 scripts/generate_analysis.py`
- 只裝一個：自動用那一個
- 都沒裝：印出安裝指令並跳過，不影響網頁其他功能

```bash
# 裝 Codex CLI
npm install -g @openai/codex

# 或裝 Claude Code
npm install -g @anthropic-ai/claude-code
```

Prompt本身是自包含的，不需要額外設定就能跑出結構化的質化分析。如果想要更深的研究品質（护城河/估值的多模組交叉驗證框架），可以另外裝 [AI Berkshire](https://github.com/xbtlin/ai-berkshire)——一套同時相容Claude Code與Codex、MIT授權的價值投資研究skill合集，裝了之後LLM有機會在回答時借用那套方法論（非必要，純加分）：

```bash
git clone https://github.com/xbtlin/ai-berkshire.git
cd ai-berkshire

# Codex 用戶
./scripts/install-codex-skills.sh

# Claude Code 用戶
./scripts/install-claude-commands.sh
```

## 分析怎麼來的（五維度雷達圖）

`generate_analysis.py`每檔股票會先組好六塊「事實」再交給LLM，LLM只能引用這些區塊、不准自己編：

| 區塊 | 來源 | 誰算分數 |
| --- | --- | --- |
| 技術面（+日／週／季線、RSI、操作訊號） | `backend/meta_screen.py`：多重優勢（META）四關篩選：趨勢（Stage2）過濾＋四類優勢，趨勢不成立技術面封頂15分 | Python（規則） |
| 財報（含最近4季EPS預期vs實際） | yfinance基本面 + `get_earnings_dates` | LLM判斷，再過護欄 |
| 估值 | 本益比、分析師目標價（**目標價落後股價，不當主要依據**） | LLM判斷，再過護欄 |
| 新聞時事（公司新聞 + 財報反應 + 公司事件 + 總經／世界時事） | `backend/news_feed.py`：Google News / Yahoo RSS，免金鑰，濾掉報價頁/財報逐字稿等垃圾標題 | LLM判斷 |
| 市場方向（指數50/200MA + 近5日／距高點 + 那指/費半） | `backend/market_health.py` | Python（規則） |

- **每個分數都有「為什麼是N分」**：技術面、市場方向的理由由程式依同一套規則直接寫出（不可能跟分數矛盾）；其餘三項由LLM按rubric寫。
- **評分護欄**（`backend/score_guards.py`）：prompt只是請求、護欄是硬上限——公司虧損／自由現金流為負／負債高於現金1.5倍／最近一季EPS低於預期10%／價格近20日重挫40%以上／週期性產業／科技族群正在崩跌，這些條件成立時會直接下調分數並在理由中標註「【系統護欄】」。
- **懷疑式prompt**：強制寫`bear_case`（最強空方論點）、`reality_check`（哪裡可能高估）、`world_context`（世界局勢透過什麼管道影響這檔），要求標明「標題所稱／推論／資料未提供」，區分「標題發布日」與「事件發生日」。
- **實體驗證**：LLM文字裡出現資料區塊沒有的地名／市場名（例如把台灣加權指數寫成香港）會自動重試一次，仍有就記進`warnings`。
- 新聞來源是公開RSS，標題屬外部不可信文字：後端只當資料餵給LLM，前端一律escape、連結只接受http(s)。

## 怎麼用

```bash
# 1. 安裝相依套件
cd backend
pip install -r requirements.txt

# 2. 啟動（預設只監聽本機 127.0.0.1）
cd .. && ./scripts/run_local.sh

# 3. 瀏覽器開
http://127.0.0.1:8013/
```

> ⚠️ **這個服務沒有任何登入驗證**，而且會回傳你的持倉與分析。請不要用 `--host 0.0.0.0` 或把埠號對外開放；要從其他裝置連，請用下面的 Tailscale 方式。
> 另外：聊天與分析會把你的持倉／分析內容送給你自己登入的 `claude` 或 `codex` CLI（也就是送到對應的 AI 服務）。聊天預設優先用 `claude`（已停用所有工具），但這只是降低能力，**不是安全邊界**：`codex` 的唯讀沙盒仍可讀取整個檔案系統。

### 只想從區網/VPN其他裝置連，不想本機WiFi也能連

專案附了`scripts/run_tailscale.sh`，會自動查你自己機器當下的Tailscale IP，只bind那張網卡啟動（不是`0.0.0.0`），所以只有同一個tailnet上的裝置連得到，本機WiFi/乙太網路連不到：

```bash
./scripts/run_tailscale.sh
```

跑起來後終端機會印出你機器當下的Tailscale IP，用那個IP（或你自己tailnet的MagicDNS網址）從其他裝置連，例如`http://<你的tailscale IP>:8013/`。這個網頁本身**沒有帳密驗證**，只bind Tailscale網卡是唯一的存取限制，請自行評估是否足夠。

### 功能說明

- 總覽頁右上角輸入代號按「新增」即可加股票。**美股直接輸入代號（TSLA），台股輸入數字代號即可（2330），系統會自動試`.TW`/`.TWO`解析**，名稱會抓TWSE/TPEx的中文簡稱（如「華城」），不是代號或英文名。卡片右上角滑過去會浮現✕可移除。卡片大字是公司名、小字是代號。
- 搜尋框（header下方）：輸入代號或公司名稱即時篩選卡片。
- 右上角「全部／美股／台股」切換只看哪個市場；排序下拉可依股價/漲跌%/位階σ/市值等排序，搭配升降冪按鈕。
- 卡片右上角星號：特別關注的股票固定置頂（不管排序選什麼），detail頁也有一樣的星號，兩邊同步。
- **「更新股價」／「更新基本面分析」兩個按鈕**（獨立一列，在篩選列下方）：
  - 更新股價：重抓所有觀察中股票的K線+基本面，通常幾秒內完成，按鈕會顯示「更新中…」並停用，完成後自動刷新卡片並顯示上次更新時間。
  - 更新基本面分析：對所有股票重新呼叫codex或claude（自動偵測，見「基本面分析引擎」）寫投資分析，**每檔約1~2分鐘（3檔並行），21檔大概12~15分鐘**，背景跑不卡頁面，按鈕會停用直到跑完。兩個按鈕各自獨立狀態，同一種更新同時間只能跑一個（重複按會被擋409）。只要有任何一檔失敗（CLI沒裝、逾時、yfinance失敗…），按鈕下方文字會變紅顯示「上次更新失敗」而不是靜靜顯示完成，可以照原因修正後重按。
- 點卡片進個股詳細頁，圖表上方按鈕可切一個月/三個月/半年/一年/三年，走勢圖、區間位置bar、長期趨勢位階bar都跟著切換範圍重算，**但底層資料不會被丟掉，只是調整x軸要看哪一段**。
- 「畫趨勢線」按下後，在圖上點第一個點（橘色標記），再點第二個點——畫出的是**射線**，固定起點沿方向延伸到資料邊界（不是停在第二個點的線段）。完成後自動退出畫線模式。「清除畫線」整張清掉。
  - 連續點兩下要間隔一點（正常人手速沒問題），太快點（<100ms內）chart函式庫可能吃不到第二下。
- **持倉（多筆交易紀錄）**：detail頁「我的持倉」面板輸入買入日期、均價，股數選填，按「新增交易」就加一筆，同一檔股票可以分批買、每筆各自記錄、各自能單獨刪除（每筆旁邊的✕）；下方自動算出加權平均成本、總股數、損益%（有填股數的話還會多算損益金額跟目前市值）。勾選「在圖表上顯示買入點」會在走勢圖上每一筆都標一個買入箭頭+一條均價虛線（切換範圍不會消失，邏輯跟趨勢線一樣）；不想看就取消勾選，資料還在，只是不畫。「清除全部」整個刪掉這檔股票的所有交易紀錄。
- 排序下拉也可以選「持倉損益%」「持倉損益金額」，沒設持倉的股票排序時自動沉到最後。
- **持倉Dashboard**（總覽頁右上角「📊 持倉Dashboard」）：統計列顯示總市值/總成本/總損益/總報酬率；甜甜圈圖顯示持倉比重（依市值，只算有填股數的持倉，超過7檔自動合併成「其他」）；橫條圖顯示所有有持倉股票的損益%（不需要股數也會顯示）；下面是明細表格，點列可以直接跳到該股票的detail頁。沒有任何持倉紀錄時會顯示空狀態提示。
- **匯出/匯入備份**（更新按鈕下方獨立一列）：「匯出備份」把觀察清單、關注、持倉、投資分析打包成一個JSON，存一份到`data/backups/`同時觸發瀏覽器下載；「匯入備份」選一個備份檔（剛剛下載的、或`data/backups/`裡任一份）就會整個覆蓋回去，會先跳confirm再執行。這是目前唯一的備份機制——`data/`裡的個人資料都不進版控，電腦出事或資料夾被刪就真的救不回來，建議偶爾匯出一份存到別的地方。

## CLI方式更新（網頁按鈕已可做到一樣的事，這是給想自己排程或debug用的）

```bash
cd stock_dashboard   # repo根目錄
python3 scripts/refresh_prices.py              # 刷新所有股票的股價快取
python3 scripts/refresh_prices.py AVGO         # 只刷新指定代號
python3 scripts/generate_analysis.py           # 重新產生所有股票的投資分析
python3 scripts/generate_analysis.py 2330.TW   # 只跑指定代號
```

兩支script各自獨立、可以配自己想要的任何排程機制（cron/launchd/手動），專案本身不綁定特定排程方式。

## 已知限制

- 股價資料來源是`yfinance`（Yahoo Finance），免費但偶爾抓資料會延遲或失敗。
- 台股目前只試`.TW`（上市）跟`.TWO`（上櫃）兩種後綴，興櫃或其他市場代號不支援；中文名稱來源是TWSE/TPEx開放資料，查不到會退回yfinance的英文名。
- 分析文字是最後一次`generate_analysis.py`（或按「更新基本面分析」）當下產生的快照，不會自動更新。
- 兩關檢查（股價在低位階／獲利沒衰退）是規則硬算（σ≤-1、獲利年增≥0），不是LLM判斷。
- 趨勢線只存在當前分頁的記憶體裡，不會存檔，重新整理頁面會清空。
- 移除股票不會刪掉`data/price_cache/`或`data/analysis/`的快取檔，想清就手動刪。
- 兩個更新按鈕觸發的是背景thread，**如果重啟uvicorn，進行中的更新會被中斷**（之後再按一次就好，refresh_prices.py很快，generate_analysis.py可能要重跑比較久）。
- 這個網頁沒有任何帳密驗證，也沒有多使用者支援，設計上就是給一個人自己用的工具，不要公開部署在沒有存取控制的網路上。
- 「匯入備份」是整個覆蓋，不是合併——匯入前目前的觀察清單/關注/持倉/分析會被取代，不是跟備份檔內容合併。

## 這個系統是什麼、不是什麼

適合：剛開始投資、看了很多教學也做了筆記，但不知道「現在這檔股票的狀況符合筆記裡的哪幾條」的人。它把你的規則（筆記）和即時事實（價格、均線、財報、新聞、你的持倉）對照起來，附上出處與證據。

**不是**：選股明牌或預測工具。五面向分數**尚無回測證據**顯示高分之後表現較好：技術面分數用 5 年歷史回測（`scripts/backtest_technical.py`），與同儕相比沒有顯著差異；財報、估值、新聞三軸由 AI 判斷，無法回測。請當成「整理資訊、檢查自己有沒有照自己的規則」的輔助，不要當成買賣依據。

## 自訂分析規則（Profile）

`profiles/default/` 是內建設定：`profile.json`（技術面門檻、評分護欄門檻、訊號用語 `signal_style`：`state`＝偏多／偏空／觀望，`action`＝買進／賣出／續抱／觀望）加上選填的 `rules.md`（會附加到分析 prompt 的額外規則）。

要用自己的：複製 `profiles/default/` 到別處，改完以 `STOCK_DASHBOARD_PROFILE=/你的/資料夾` 啟動（`scripts/run_local.sh` 會自動使用 `data/profiles/personal/`，該資料夾不會進 git）。只有 default 裡已存在的欄位、且型別相同的值才會生效，錯誤的值會被忽略並記在分析結果的 `warnings`。

`rules.md` 只能影響模型的措辭與重點；**評分護欄的判斷邏輯寫在程式裡，profile 只能調門檻，不能關掉或繞過**。

## 策略筆記檢查（原型，CLI）

把你自己的交易策略／筆記（`.md`、`.txt`，單檔 ≤100KB、總量 ≤300KB、≤20 檔）放進 `data/strategy/`（不會進 git），然後：

```bash
python scripts/strategy_check.py BBBB            # 呼叫你登入的 claude CLI
python scripts/strategy_check.py BBBB --dry-run  # 只看會送出什麼 prompt、選到哪些段落
```

輸出是一張規則對照表。**程式只驗證兩件事**：①引文真的逐字存在於你的筆記、且在所有筆記中唯一；②引用的事實代碼（如 `TECH.stage2`）真的存在，證據文字由程式依代碼重新產生。`pass`／`fail` 是模型的判斷，**不代表程式證明規則成立**。筆記會被當成「可能含指令的使用者資料」，但這只能降低 prompt injection 風險，無法消除。

### 在網頁上使用

首頁右上「策略筆記」可上傳／貼上／刪除筆記（只存在本機 `data/strategy/`）；個股詳細頁的「策略檢查」按鈕會用你登入的 claude／codex CLI 產生規則對照表，結果顯示在頁面並存於 `data/strategy_checks/`。若該股票你有持倉紀錄，持倉事實也會納入；沒持倉則給「如果已持有／如果沒持有」兩種情境建議（模型建議，未經程式驗證）。主分析另有「你的持倉與情境」說明，只影響文字、不影響分數；要關閉請在 profile 設 `"include_position": 0`。

## 備份提醒

「匯出備份」涵蓋觀察清單、關注、持倉與最新分析。**不含**：`data/analysis_history/`（分析快照，無法重新產生，請自行複製整個資料夾）、`data/profiles/`（自訂 profile）、聊天紀錄。策略筆記預設不含；匯出時加 `?include_strategy=true` 才會包含（內含你的私人筆記，請妥善保管）。

## 不用等快照的檢驗：技術面回測

`python scripts/backtest_technical.py [--horizon 20]` 用本機 5 年價格快取，在每個歷史時點（只用當時可得資料）重算技術面分數，對照之後 N 日報酬，並扣掉同市場同日其他股票的平均報酬。只能測規則型的技術面；LLM 評分的軸（財報／估值／新聞）無法回測（歷史新聞不在快取，且模型自己的知識會洩漏未來）。結果有重疊窗口、事後挑選的清單、單一多頭行情等限制，腳本會印出來。

## AI 額度用量（實測，2026-11-11，claude 引擎）

`data/llm_usage.jsonl` 會記錄每次 LLM 呼叫的 token、等效費用與秒數，網頁「策略批次總覽」頁底也能看。實測（6 檔：3 檔台股、3 檔美股）：

| 項目 | 每次 | 6 檔合計 |
|---|---|---|
| 完整分析（含新聞、持倉說明） | 約 7.5k 輸入＋約 5k 輸出 token，約 0.15 USD（API 等值），約 55 秒 | 約 0.92 USD，並行 3 路約 3 分鐘 |
| 策略檢查（7 條規則） | 約 10k 輸入＋約 2.4k 輸出 token，約 0.13 USD，約 21 秒 | 約 0.77 USD，約 2.2 分鐘 |

費用是「API 等值」，訂閱方案不一定實際計費，但會消耗額度。系統呼叫 claude 時已關閉全域設定／外掛／MCP 與所有工具，每次呼叫的基本輸入從約 66k 降到約 2.7k token（約 16 倍）。codex 引擎只回報總 token 數，沒有費用。
