# 瀏覽器端到端測試

這個目錄是以 pytest 管理的 Playwright E2E 測試。測試會在暫存目錄建立完整合成示範資料，為每個測試模組複製一份資料，再用隨機可用埠啟動 uvicorn。過程不會讀寫正式 `data/`、連線抓行情或呼叫 AI CLI。

一般的 `pytest` 與 CI 預設會略過整個 E2E 套件。只有同時設定 `SD_E2E=1` 且可匯入 Playwright 時才會執行。

## 安裝與執行

在 repo 根目錄依序執行：

```bash
pip install -r backend/requirements.txt pytest httpx playwright
playwright install chromium
SD_E2E=1 python -m pytest tests/e2e -q
```

測試會先嘗試啟動系統安裝的 Google Chrome；找不到時，會自動改用 `playwright install chromium` 裝的 Chromium。

## 沒有設定 SD_E2E=1 的時候

沒有設定 `SD_E2E=1`（或沒有安裝 Playwright）時，整個 E2E 套件會被標示為略過（skipped），不會失敗。所以一般的 `python -m pytest -q tests` 與 CI 都不會跑到它們。

## 疑難排解

若本機不能綁定 loopback 埠，uvicorn fixture 會在啟動階段失敗，並顯示伺服器日誌。
