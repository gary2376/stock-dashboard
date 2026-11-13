(function mountOnboardingTour() {
  "use strict";

  if (window.__sdTourMounted) return;
  window.__sdTourMounted = true;

  const PAGE_TOURS = {
    "/index.html": {
      readySelector: "#grid .card, #grid .empty",
      steps: [
        {
          selector: "#grid .first-run-empty",
          title: "從第一檔股票開始",
          text: "依照這三個步驟新增股票，或先開啟示範模式熟悉畫面。下方按鈕可以直接把游標移到新增欄位。",
        },
        {
          selector: "#grid .card",
          title: "觀察清單卡片",
          text: "每張卡片濃縮股價位階、獲利檢查與結論。先用卡片快速篩選，再點進去看完整依據。",
        },
        {
          selector: "#newTicker",
          title: "新增股票",
          text: "輸入美股或台股代號加入觀察清單。把想追蹤的公司集中起來，才容易持續比較。",
        },
        {
          selector: "#marketFilterBtns",
          title: "市場篩選",
          text: "切換全部、美股或台股，縮小目前要看的範圍，避免資訊一次太多。",
        },
        {
          selector: "#sortSelect",
          title: "排序清單",
          text: "依股價、位階或持倉損益排序，讓最需要處理的股票先浮上來。旁邊箭頭可切換順序。",
        },
        {
          selector: "#refreshPricesBtn",
          title: "更新股價",
          text: "抓取最新價格與區間資料。做判斷前先更新，才能避免拿過期價格比較。",
        },
        {
          selector: "#refreshAnalysisBtn",
          title: "更新基本面分析",
          text: "重新整理財報、估值與 AI 分析。公司事實會變，舊結論也要定期重看。",
        },
        {
          selector: "#backupControls",
          title: "匯出／匯入備份",
          text: "匯出可保存觀察清單、持倉與分析；匯入會覆蓋目前資料，操作前請先確認備份內容。",
        },
        {
          selector: "#strategyNavLink",
          title: "策略筆記",
          text: "把自己的買賣規則寫成筆記，之後可比較「你的筆記規則」與「目前事實」是否一致。",
        },
        {
          selector: "#overviewNavLink",
          title: "策略批次總覽",
          text: "一次選取多檔觀察清單股票，橫向比較它們對每條策略規則的通過狀態。",
        },
        {
          selector: "#dashboardNavLink",
          title: "持倉 Dashboard",
          text: "從整體角度看部位比重與損益，避免只盯單一股票而忽略組合風險。",
        },
        {
          selector: ".chat-widget-bubble:not([hidden])",
          title: "AI 投資助理",
          text: "可詢問清單比較、技術面、估值與新聞。它是整理資訊的助手，不是買賣保證。",
        },
      ],
    },
    "/dashboard.html": {
      readySelector: "#dashboardRoot .stat-row, #dashboardRoot .empty",
      steps: [
        {
          selector: "#holdingsPiePanel",
          title: "持倉比重",
          text: "圓餅圖用市值顯示資金集中在哪些股票。單一部位過大時，組合波動也可能被它主導。",
        },
        {
          selector: "#holdingsPlPanel",
          title: "各檔損益",
          text: "橫條圖比較每檔報酬率，方便找出拖累與領先部位；損益本身不等於未來好壞。",
        },
        {
          selector: "#holdingTotals",
          title: "持倉總計",
          text: "總市值、總成本、總損益與報酬率放在一起，讓你先掌握組合全貌再看個股。",
        },
      ],
    },
    "/detail.html": {
      readySelector: "#detailRoot .panel, #detailRoot .empty",
      steps: [
        {
          selector: "#priceChartPanel",
          title: "走勢圖與畫線",
          text: "K 線呈現價格變化；「畫趨勢線」可標記你觀察的支撐或方向，幫助把想法具體化。",
        },
        {
          selector: "#rangeBtns",
          title: "切換觀察區間",
          text: "一個月到三年的按鈕會改變圖表視窗與位階計算。短期波動和長期趨勢可能給出不同答案。",
        },
        {
          selector: "#indicatorToggles",
          title: "切換均線與成交量",
          text: "可顯示 MA10、MA20、MA50、MA200 與成交量；設定會保留在這台裝置，方便維持自己的看圖習慣。",
        },
        {
          selector: ".analysis-score-section",
          title: "五面向評分",
          text: "雷達圖整理技術面、財報、估值、新聞與市場方向。這是閱讀輔助，目前沒有證據證明高分會帶來較好報酬。",
        },
        {
          selector: ".score-reasons",
          title: "每一軸的理由",
          text: "分數下方列出各軸原因。先讀理由與資料限制，不要只看一個分數做決定。",
        },
        {
          selector: "#holdingPanel",
          title: "我的持倉與交易批次",
          text: "輸入買入日期、均價與選填股數；每次買入會保留成一批，方便計算平均成本與組合損益。",
        },
        {
          selector: "#investmentAnalysisPanel",
          title: "投資分析區塊",
          text: "這裡整理論點、反方觀點、財務、估值、催化劑與風險。把正反證據一起看，較不容易只找支持自己的資訊。",
        },
        {
          selector: "#pinnedChecksPanel",
          title: "固定檢查項目",
          text: "這些狀態由程式直接計算，不經過 AI；每條規則的白話解讀都必須先由你確認。持倉改變後會自動重算買入當時的結果。",
        },
        {
          selector: "#strategyCheckPanel",
          title: "策略檢查",
          text: "用你的筆記規則分兩種時間檢查：「現在」看這檔股票今天的狀態；「買入當時」則回到每筆買入那天，只用當天以前的價格與均線資料重算。引文與事實引用由程式驗證；通過與否、情境建議則是模型判斷。",
        },
        {
          selector: ".position-note",
          title: "持倉情境說明",
          text: "模型會依你的成本與部位補充情境，但這段不影響五面向分數，也不是加碼或賣出的指令。",
        },
      ],
    },
    "/strategy.html": {
      readySelector: ".strategy-wrap",
      steps: [
        {
          selector: ".strategy-header",
          title: "為什麼要寫策略筆記",
          text: "先把自己的買賣原則寫下來，檢查時才能比較「你的筆記規則」與「目前事實」，減少事後改口。",
        },
        {
          selector: "#pinnedRulesPanel",
          title: "固定檢查項目（釘選規則）",
          text: "把能用資料直接判斷的規則寫在這裡。先編譯、逐項閱讀系統的白話解讀，再由你確認；確認前不會啟用。",
        },
        {
          selector: "#strategyUploadPanel",
          title: "上傳筆記",
          text: "可上傳 Markdown 或純文字檔，適合匯入已整理好的規則與檢查清單。",
        },
        {
          selector: "#strategyPastePanel",
          title: "貼上文字",
          text: "也可以直接命名並貼上規則，適合快速記錄新想法或小幅修改。",
        },
        {
          selector: "#strategyUsage",
          title: "容量與檔案限制",
          text: "這裡顯示總用量、檔案數與單檔上限。超過限制時要先精簡或刪除舊筆記。",
        },
        {
          selector: "#strategyPrivacy",
          title: "隱私與傳送範圍",
          text: "筆記只儲存在本機 data/strategy；只有執行策略檢查時，才會傳給你本人已登入的 AI CLI。請勿放入敏感資料。",
        },
      ],
    },
    "/overview.html": {
      readySelector: ".overview-wrap",
      steps: [
        {
          selector: "#tickerPicker",
          title: "選擇批次股票",
          text: "從觀察清單勾選最多 10 檔股票；台股與美股快速按鈕可各補上前 3 檔。",
        },
        {
          selector: "#runBatchBtn",
          title: "執行批次檢查",
          text: "每檔約使用一次 AI 呼叫。確認檔數與額度成本後，再啟動批次檢查。",
        },
        {
          selector: "#batchMatrixPanel",
          title: "比較策略矩陣",
          text: "橫向比較每檔股票對各條規則的通過、未通過、未知與有條件狀態；點股票代號可回到個股詳情。",
        },
        {
          selector: "#usageBox",
          title: "查看 AI 額度",
          text: "展開後可查看各類呼叫次數、Token、API 等值成本與最近使用紀錄；訂閱方案不一定實際按此計費。",
        },
      ],
    },
  };

  const memoryCompleted = new Set();
  let activeTour = null;

  function pagePath() {
    const pathname = window.location.pathname;
    if (pathname === "/" || pathname.endsWith("/frontend/")) return "/index.html";
    return Object.keys(PAGE_TOURS).find(path => pathname.endsWith(path)) || "";
  }

  function storageKey(path) {
    return `sd_tour_v1_${path.slice(1, -5)}`;
  }

  function hasCompleted(path) {
    if (memoryCompleted.has(path)) return true;
    try {
      return localStorage.getItem(storageKey(path)) === "1";
    } catch (_) {
      return memoryCompleted.has(path);
    }
  }

  function rememberCompleted(path) {
    memoryCompleted.add(path);
    try {
      localStorage.setItem(storageKey(path), "1");
    } catch (_) {
      // The in-memory flag prevents another automatic start in this document.
    }
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function visibleTarget(selector) {
    const target = document.querySelector(selector);
    if (!target || target.hidden || target.getClientRects().length === 0) return null;
    const style = window.getComputedStyle(target);
    return style.display === "none" || style.visibility === "hidden" ? null : target;
  }

  function reducedMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function mountReplayButton(path) {
    const button = element("button", "tour-replay-btn", "？ 導覽");
    button.type = "button";
    button.setAttribute("aria-label", "重新播放本頁導覽");
    button.addEventListener("click", () => {
      if (document.querySelector("dialog.consent-dialog[open]")) return;
      startTour(path, button);
    });
    document.body.appendChild(button);
  }

  function startTour(path, trigger) {
    if (activeTour || document.querySelector("dialog.consent-dialog[open]")) return;
    const config = PAGE_TOURS[path];
    if (!config) return;

    const steps = config.steps
      .map(step => ({ ...step, target: visibleTarget(step.selector) }))
      .filter(step => step.target);
    if (!steps.length) {
      rememberCompleted(path);
      return;
    }

    const previouslyFocused = trigger || document.activeElement;
    const screen = element("div", "tour-screen");
    screen.setAttribute("aria-hidden", "true");
    const spotlight = element("div", "tour-spotlight");
    spotlight.setAttribute("aria-hidden", "true");
    const popover = element("section", "tour-popover");
    const title = element("h2", "tour-title");
    const description = element("p", "tour-description");
    const progress = element("span", "tour-progress");
    const actions = element("div", "tour-actions");
    const previousButton = element("button", "tour-btn tour-btn-secondary", "上一步");
    const nextButton = element("button", "tour-btn tour-btn-primary", "下一步");
    const skipButton = element("button", "tour-btn tour-btn-skip", "略過導覽");

    title.id = "sdTourTitle";
    description.id = "sdTourDescription";
    popover.setAttribute("role", "dialog");
    popover.setAttribute("aria-modal", "true");
    popover.setAttribute("aria-labelledby", title.id);
    popover.setAttribute("aria-describedby", description.id);
    previousButton.type = "button";
    nextButton.type = "button";
    skipButton.type = "button";
    actions.append(previousButton, nextButton, skipButton);
    popover.append(progress, title, description, actions);
    document.body.append(screen, spotlight, popover);

    activeTour = {
      path,
      steps,
      index: 0,
      previouslyFocused,
      screen,
      spotlight,
      popover,
      title,
      description,
      progress,
      previousButton,
      nextButton,
      skipButton,
    };

    function finish() {
      if (!activeTour) return;
      rememberCompleted(path);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
      document.removeEventListener("keydown", handleKeydown, true);
      document.removeEventListener("focusin", keepFocusInside);
      screen.remove();
      spotlight.remove();
      popover.remove();
      activeTour = null;
      if (previouslyFocused && previouslyFocused.isConnected && typeof previouslyFocused.focus === "function") {
        previouslyFocused.focus({ preventScroll: true });
      }
    }

    function focusableButtons() {
      return [previousButton, nextButton, skipButton].filter(button => !button.disabled);
    }

    function handleKeydown(event) {
      if (!activeTour) return;
      if (event.key === "Escape") {
        event.preventDefault();
        finish();
        return;
      }
      if (event.key !== "Tab") return;
      const buttons = focusableButtons();
      const first = buttons[0];
      const last = buttons[buttons.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    function keepFocusInside(event) {
      if (activeTour && !popover.contains(event.target)) nextButton.focus();
    }

    function updatePosition() {
      if (!activeTour) return;
      const step = steps[activeTour.index];
      if (!step || !step.target.isConnected) return;
      const rect = step.target.getBoundingClientRect();
      const gap = 8;
      const spotTop = Math.max(6, Math.min(window.innerHeight - 6, rect.top - 5));
      const spotLeft = Math.max(6, Math.min(window.innerWidth - 6, rect.left - 5));
      const spotRight = Math.min(window.innerWidth - 6, rect.right + 5);
      const spotBottom = Math.min(window.innerHeight - 6, rect.bottom + 5);
      spotlight.style.top = `${spotTop}px`;
      spotlight.style.left = `${spotLeft}px`;
      spotlight.style.width = `${Math.max(0, spotRight - spotLeft)}px`;
      spotlight.style.height = `${Math.max(0, spotBottom - spotTop)}px`;

      const popoverRect = popover.getBoundingClientRect();
      const viewportPadding = 12;
      let top;
      if (rect.bottom + gap + popoverRect.height <= window.innerHeight - viewportPadding) {
        top = rect.bottom + gap;
      } else if (rect.top - gap - popoverRect.height >= viewportPadding) {
        top = rect.top - gap - popoverRect.height;
      } else {
        top = Math.max(viewportPadding, (window.innerHeight - popoverRect.height) / 2);
      }
      const preferredLeft = rect.left + (rect.width - popoverRect.width) / 2;
      const maxLeft = Math.max(viewportPadding, window.innerWidth - popoverRect.width - viewportPadding);
      const maxTop = Math.max(viewportPadding, window.innerHeight - popoverRect.height - viewportPadding);
      popover.style.top = `${Math.max(viewportPadding, Math.min(top, maxTop))}px`;
      popover.style.left = `${Math.max(viewportPadding, Math.min(preferredLeft, maxLeft))}px`;
    }

    function showStep(index, focusPrimary) {
      activeTour.index = index;
      const step = steps[index];
      const currentTarget = visibleTarget(step.selector);
      if (!currentTarget) {
        steps.splice(index, 1);
        if (!steps.length) {
          finish();
          return;
        }
        showStep(Math.min(index, steps.length - 1), focusPrimary);
        return;
      }
      step.target = currentTarget;
      title.textContent = step.title;
      description.textContent = step.text;
      progress.textContent = `${index + 1} / ${steps.length}`;
      previousButton.disabled = index === 0;
      nextButton.textContent = index === steps.length - 1 ? "完成" : "下一步";
      step.target.scrollIntoView({
        behavior: reducedMotion() ? "auto" : "smooth",
        block: "center",
        inline: "nearest",
      });
      updatePosition();
      window.setTimeout(updatePosition, reducedMotion() ? 0 : 240);
      if (focusPrimary) nextButton.focus({ preventScroll: true });
    }

    previousButton.addEventListener("click", () => {
      if (activeTour.index > 0) showStep(activeTour.index - 1, true);
    });
    nextButton.addEventListener("click", () => {
      if (activeTour.index >= steps.length - 1) finish();
      else showStep(activeTour.index + 1, true);
    });
    skipButton.addEventListener("click", finish);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    document.addEventListener("keydown", handleKeydown, true);
    document.addEventListener("focusin", keepFocusInside);
    showStep(0, true);
  }

  function waitForPageContent(path, callback) {
    const config = PAGE_TOURS[path];
    if (!config || document.querySelector(config.readySelector)) {
      callback();
      return;
    }
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      observer.disconnect();
      window.clearTimeout(timeout);
      callback();
    };
    const observer = new MutationObserver(() => {
      if (document.querySelector(config.readySelector)) finish();
    });
    observer.observe(document.body, { childList: true, subtree: true });
    const timeout = window.setTimeout(finish, 8000);
  }

  function autoStartAfterConsent(path) {
    const attempt = () => {
      if (document.querySelector("dialog.consent-dialog[open]")) return false;
      if (window.__sdTourAutoAttempted?.[path]) return true;
      window.__sdTourAutoAttempted = window.__sdTourAutoAttempted || {};
      window.__sdTourAutoAttempted[path] = true;
      if (!hasCompleted(path)) {
        waitForPageContent(path, () => {
          if (!hasCompleted(path)) startTour(path);
        });
      }
      return true;
    };

    if (attempt()) return;
    let timeout = null;
    const cleanup = () => {
      observer.disconnect();
      if (timeout !== null) window.clearTimeout(timeout);
      window.removeEventListener("pagehide", cleanup);
    };
    const observer = new MutationObserver(() => {
      if (attempt()) cleanup();
    });
    observer.observe(document.body, { childList: true, subtree: true });
    timeout = window.setTimeout(cleanup, 10000);
    window.addEventListener("pagehide", cleanup, { once: true });
  }

  function init() {
    const path = pagePath();
    if (!path) return;
    mountReplayButton(path);
    autoStartAfterConsent(path);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
