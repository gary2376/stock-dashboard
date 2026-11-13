# 瀏覽器端到端測試

這個目錄是以 pytest 管理的 Playwright E2E 測試。測試會在暫存目錄建立完整合成示範資料，為每個測試模組複製一份資料，再用隨機可用埠啟動 uvicorn。過程不會讀寫正式 `data/`、連線抓行情或呼叫 AI CLI。

一般的 `pytest` 與 CI 預設會略過整個 E2E 套件。只有同時設定 `SD_E2E=1` 且可匯入 Playwright 時才會執行。

## 安裝

先安裝專案本身的後端相依套件，再安裝測試工具與 Chromium：

```bash
pip install playwright pytest
playwright install chromium
```

測試會先嘗試使用系統 Chrome；若找不到，會改用 Playwright 安裝的 Chromium。

## 執行

```bash
SD_E2E=1 pytest tests/e2e -q
```

若本機不能綁定 loopback 埠，uvicorn fixture 會在啟動階段失敗並顯示伺服器日誌。
