(function () {
  "use strict";

  const ALLOWED = [
    "../referenced-standards/",
    "../regulation/",
    "../implementing-acts/",
    "../implementing-decisions/",
  ];

  function isSafeSrc(src) {
    if (!src) return false;
    const norm = src.replace(/\\/g, "/");
    if (/^https?:\/\//i.test(norm)) return false;
    return ALLOWED.some((p) => norm.startsWith(p));
  }

  function extOf(src) {
    const m = src.match(/\.([a-z0-9]+)(?:\?|#|$)/i);
    return m ? m[1].toLowerCase() : "";
  }

  const params = new URLSearchParams(window.location.search);
  const src = params.get("src");
  const title = params.get("title") || "Document";

  document.getElementById("doc-title").textContent = title;
  const openTab = document.getElementById("open-tab");
  const errEl = document.getElementById("error");
  const frame = document.getElementById("doc-frame");
  const embed = document.getElementById("doc-embed");

  if (!src || !isSafeSrc(src)) {
    errEl.style.display = "block";
    errEl.textContent = "Invalid or missing document path.";
  } else {
    openTab.href = src;
    const ext = extOf(src);
    if (ext === "pdf") {
      embed.src = src;
      embed.classList.add("active");
    } else {
      frame.src = src;
      frame.classList.add("active");
    }
  }
})();
