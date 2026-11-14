// Shared helpers loaded first on every page that builds HTML strings.
// External text (company names, model output, URL params) must be escaped before it goes into innerHTML.
window.escapeHtml = function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
};
