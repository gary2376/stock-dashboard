(function strategyNotesPage() {
  "use strict";

  const usageEl = document.getElementById("strategyUsage");
  const statusEl = document.getElementById("strategyStatus");
  const fileListEl = document.getElementById("strategyFileList");
  const fileInput = document.getElementById("strategyFileInput");
  const uploadButton = document.getElementById("uploadStrategyBtn");
  const pasteForm = document.getElementById("strategyPasteForm");
  const viewer = document.getElementById("strategyViewer");
  const viewerTitle = document.getElementById("strategyViewerTitle");
  const viewerContent = document.getElementById("strategyViewerContent");
  const fileListHeading = document.getElementById("strategyFilesTitle");
  let lastViewerButton = null;
  let fileListGeneration = 0;
  let strategySnapshot = null;

  function formatBytes(bytes) {
    const value = Number(bytes) || 0;
    if (value < 1024) return `${value}B`;
    if (value < 1024 * 1024) return `${Math.round(value / 1024)}KB`;
    return `${(value / (1024 * 1024)).toFixed(1)}MB`;
  }

  async function responseDetail(response) {
    try {
      const body = await response.json();
      if (typeof body.detail === "string") return body.detail;
      if (body.detail) return JSON.stringify(body.detail);
    } catch (_) {
      // A non-JSON server response falls back to its HTTP status below.
    }
    return `HTTP ${response.status}`;
  }

  function showStatus(message, isError) {
    statusEl.textContent = message;
    statusEl.classList.toggle("error", Boolean(isError));
  }

  function setBusy(button, busy) {
    button.disabled = busy;
    button.setAttribute("aria-busy", String(busy));
  }

  function strategyFileUrl(name) {
    return `/api/strategy/file/${encodeURIComponent(name)}`;
  }

  function renderUsage(data) {
    const limits = data.limits || {};
    const fileCount = Array.isArray(data.files) ? data.files.length : 0;
    usageEl.textContent = `${formatBytes(data.total_size)} / ${formatBytes(limits.max_total_bytes)}　·　${fileCount} / ${limits.max_files ?? "--"} 個檔案　·　單檔上限 ${formatBytes(limits.max_file_bytes)}`;
  }

  function rememberStrategySnapshot(data) {
    strategySnapshot = {
      files: Array.isArray(data.files) ? data.files : [],
      limits: data.limits || {},
      totalSize: Number(data.total_size) || 0,
    };
  }

  function contentSizeError(name, byteLength) {
    if (!strategySnapshot) {
      return "尚未取得儲存容量限制，請等待筆記清單載入後再試。";
    }
    const { files, limits, totalSize } = strategySnapshot;
    const maxFileBytes = Number(limits.max_file_bytes);
    const maxTotalBytes = Number(limits.max_total_bytes);
    if (!(maxFileBytes > 0) || !(maxTotalBytes > 0)) {
      return "無法取得完整的儲存容量限制，請重新整理筆記清單後再試。";
    }
    if (byteLength > maxFileBytes) {
      return `內容大小為 ${formatBytes(byteLength)}，超過單檔上限 ${formatBytes(maxFileBytes)}。`;
    }

    const existing = files.find(file => file.name === name);
    const nextTotal = totalSize - (Number(existing?.size) || 0) + byteLength;
    if (nextTotal > maxTotalBytes) {
      return `儲存後總用量將為 ${formatBytes(nextTotal)}，超過總容量 ${formatBytes(maxTotalBytes)}。`;
    }
    return "";
  }

  function makeFileRow(file) {
    const row = document.createElement("div");
    row.className = "strategy-file-row";

    const info = document.createElement("div");
    info.className = "strategy-file-info";
    const name = document.createElement("strong");
    name.textContent = file.name;
    const size = document.createElement("span");
    size.textContent = formatBytes(file.size);
    info.append(name, size);

    const actions = document.createElement("div");
    actions.className = "strategy-file-actions";
    const viewButton = document.createElement("button");
    viewButton.type = "button";
    viewButton.className = "range-btn";
    viewButton.textContent = "查看";
    viewButton.setAttribute("aria-label", `查看 ${file.name}`);
    viewButton.addEventListener("click", () => viewFile(file.name, viewButton));

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "range-btn strategy-delete-btn";
    deleteButton.textContent = "刪除";
    deleteButton.setAttribute("aria-label", `刪除 ${file.name}`);
    deleteButton.addEventListener("click", () => deleteFile(file.name, deleteButton));
    actions.append(viewButton, deleteButton);
    row.append(info, actions);
    return row;
  }

  function renderFileList(files) {
    fileListEl.replaceChildren();
    fileListEl.setAttribute("aria-busy", "false");
    if (!files.length) {
      const empty = document.createElement("p");
      empty.className = "empty strategy-empty";
      empty.textContent = "尚未儲存任何策略筆記。";
      fileListEl.appendChild(empty);
      return;
    }
    files.forEach(file => fileListEl.appendChild(makeFileRow(file)));
  }

  async function loadFiles(options = {}) {
    const generation = ++fileListGeneration;
    fileListEl.setAttribute("aria-busy", "true");
    try {
      const response = await fetch("/api/strategy");
      if (!response.ok) throw new Error(await responseDetail(response));
      const data = await response.json();
      if (generation !== fileListGeneration) return;
      rememberStrategySnapshot(data);
      renderUsage(data);
      renderFileList(Array.isArray(data.files) ? data.files : []);
      if (options.announce) showStatus("已重新整理策略筆記。", false);
    } catch (error) {
      if (generation !== fileListGeneration) return;
      fileListEl.setAttribute("aria-busy", "false");
      fileListEl.replaceChildren();
      const empty = document.createElement("p");
      empty.className = "empty strategy-empty";
      empty.textContent = "無法載入策略筆記。";
      fileListEl.appendChild(empty);
      showStatus(`載入失敗：${error.message}`, true);
    }
  }

  async function viewFile(name, button) {
    setBusy(button, true);
    try {
      const response = await fetch(strategyFileUrl(name));
      if (!response.ok) throw new Error(await responseDetail(response));
      const data = await response.json();
      viewerTitle.textContent = `筆記內容：${data.name}`;
      viewerContent.textContent = data.content || "";
      viewer.hidden = false;
      lastViewerButton = button;
      viewer.scrollIntoView({ behavior: "smooth", block: "start" });
      viewerContent.focus({ preventScroll: true });
    } catch (error) {
      showStatus(`查看失敗：${error.message}`, true);
    } finally {
      setBusy(button, false);
    }
  }

  async function deleteFile(name, button) {
    if (!window.confirm(`確定刪除「${name}」？`)) return;
    setBusy(button, true);
    try {
      const response = await fetch(strategyFileUrl(name), { method: "DELETE" });
      if (!response.ok) throw new Error(await responseDetail(response));
      if (!viewer.hidden && viewerTitle.textContent === `筆記內容：${name}`) {
        viewer.hidden = true;
        viewerContent.textContent = "";
      }
      showStatus(`已刪除 ${name}。`, false);
      await loadFiles();
      fileListHeading.setAttribute("tabindex", "-1");
      fileListHeading.focus();
    } catch (error) {
      showStatus(`刪除失敗：${error.message}`, true);
      setBusy(button, false);
    }
  }

  async function saveFile(name, content, button) {
    setBusy(button, true);
    try {
      const response = await fetch(strategyFileUrl(name), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (!response.ok) throw new Error(await responseDetail(response));
      showStatus(`已儲存 ${name}。`, false);
      await loadFiles();
      return true;
    } catch (error) {
      showStatus(`儲存失敗：${error.message}`, true);
      return false;
    } finally {
      setBusy(button, false);
    }
  }

  async function readSelectedFile() {
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      showStatus("請先選擇 .md 或 .txt 檔案。", true);
      fileInput.setAttribute("aria-invalid", "true");
      fileInput.focus();
      return;
    }
    fileInput.removeAttribute("aria-invalid");
    setBusy(uploadButton, true);
    try {
      const buffer = await file.arrayBuffer();
      let content;
      try {
        content = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
      } catch (_) {
        showStatus("檔案不是有效的 UTF-8 文字，未上傳。請先轉成 UTF-8 編碼。", true);
        fileInput.setAttribute("aria-invalid", "true");
        fileInput.focus();
        return;
      }
      const sizeError = contentSizeError(file.name, buffer.byteLength);
      if (sizeError) {
        showStatus(sizeError, true);
        fileInput.setAttribute("aria-invalid", "true");
        fileInput.focus();
        return;
      }
      const saved = await saveFile(file.name, content, uploadButton);
      if (saved) fileInput.value = "";
    } catch (_) {
      showStatus("無法讀取所選檔案。", true);
    } finally {
      setBusy(uploadButton, false);
    }
  }

  uploadButton.addEventListener("click", readSelectedFile);
  fileInput.addEventListener("change", () => fileInput.removeAttribute("aria-invalid"));
  document.getElementById("refreshStrategyBtn").addEventListener("click", () => loadFiles({ announce: true }));
  document.getElementById("closeStrategyViewerBtn").addEventListener("click", () => {
    viewer.hidden = true;
    viewerContent.textContent = "";
    if (lastViewerButton && lastViewerButton.isConnected) lastViewerButton.focus();
  });
  pasteForm.addEventListener("submit", async event => {
    event.preventDefault();
    const filenameInput = document.getElementById("strategyFilename");
    const contentInput = document.getElementById("strategyContent");
    const name = filenameInput.value.trim();
    const submitButton = pasteForm.querySelector('button[type="submit"]');
    if (!name) {
      showStatus("請輸入檔名。", true);
      filenameInput.setAttribute("aria-invalid", "true");
      filenameInput.focus();
      return;
    }
    filenameInput.removeAttribute("aria-invalid");
    const content = contentInput.value;
    const byteLength = new TextEncoder().encode(content).byteLength;
    const sizeError = contentSizeError(name, byteLength);
    if (sizeError) {
      showStatus(sizeError, true);
      contentInput.setAttribute("aria-invalid", "true");
      contentInput.focus();
      return;
    }
    contentInput.removeAttribute("aria-invalid");
    const saved = await saveFile(name, content, submitButton);
    if (saved) pasteForm.reset();
  });

  loadFiles();
})();
