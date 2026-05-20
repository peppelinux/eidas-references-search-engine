/**
 * Local document links and popup viewer (HTML / PDF) for the report UI.
 */
(function (global) {
  "use strict";

  const VIEWABLE = new Set(["pdf", "html", "htm"]);
  const ALLOWED_PREFIXES = [
    "../referenced-standards/",
    "../regulation/",
    "../implementing-acts/",
    "../implementing-decisions/",
  ];

  function isSafeSrc(src) {
    if (!src || typeof src !== "string") return false;
    if (/^https?:\/\//i.test(src) || /^javascript:/i.test(src)) return false;
    const norm = src.replace(/\\/g, "/");
    if (/\.\.(\/|$)/.test(norm.slice(3))) return false;
    return ALLOWED_PREFIXES.some((p) => norm.startsWith(p));
  }

  function openViewer(src, title) {
    if (!isSafeSrc(src)) {
      console.warn("Blocked viewer URL:", src);
      return null;
    }
    const url =
      "viewer.html?src=" +
      encodeURIComponent(src) +
      "&title=" +
      encodeURIComponent(title || "Document");
    return window.open(
      url,
      "eidas_doc_viewer",
      "noopener,noreferrer,width=1024,height=820,resizable=yes,scrollbars=yes"
    );
  }

  function hrefFromStoredPath(path) {
    if (!path) return null;
    const p = path.replace(/\\/g, "/");
    if (p.startsWith("implementing-acts/") || p.startsWith("implementing-decisions/") || p.startsWith("regulation/")) {
      return `../${p}`;
    }
    if (p.startsWith("standards/")) {
      return `../referenced-standards/${p}`;
    }
    return `../referenced-standards/${p}`;
  }

  function corpusSourceLinks(source) {
    const docs = [];
    if (!source || typeof source !== "string") return docs;
    const norm = source.replace(/\\/g, "/");
    let base;
    if (
      norm.startsWith("implementing-acts/") ||
      norm.startsWith("implementing-decisions/") ||
      norm.startsWith("regulation/")
    ) {
      const stem = norm.replace(/\.(md|html|pdf|txt)$/i, "");
      base = `../${stem}`;
    } else {
      const stem = norm.replace(/\.(md|html|pdf|txt)$/i, "");
      base = `../referenced-standards/standards/${stem}`;
    }
    for (const ext of ["md", "html", "pdf"]) {
      docs.push({
        type: ext,
        href: `${base}.${ext}`,
        label: `${ext.toUpperCase()}`,
      });
    }
    return docs;
  }

  function renderCorpusSourceLinksHtml(source, escapeHtml) {
    if (!source) return "—";
    const esc = escapeHtml || ((s) => String(s));
    const docs = corpusSourceLinks(source);
    const bits = docs.map((d) => {
      const href = esc(d.href);
      const view =
        d.type === "html" || d.type === "pdf"
          ? ` <a href="viewer.html?src=${href}&title=${esc(d.label)}" target="eidas_doc_viewer" rel="noopener" class="src-view">view</a>`
          : "";
      return `<a href="${href}">${esc(d.label)}</a>${view}`;
    });
    return `<code class="src-path">${esc(source)}</code><br/>` + bits.join(" · ");
  }

  function localDocumentsForNode(raw) {
    const docs = [];
    if (!raw || typeof raw !== "object") return docs;

    if (raw.source) {
      docs.push(...corpusSourceLinks(raw.source));
    }

    if (raw.section && raw.act_id) {
      const base = `../${raw.section}/${raw.act_id}/${raw.act_id}`;
      for (const ext of ["md", "html", "pdf"]) {
        if (!docs.some((d) => d.href === `${base}.${ext}`)) {
          docs.push({
            type: ext,
            href: `${base}.${ext}`,
            label: `EU act (${ext.toUpperCase()})`,
          });
        }
      }
    }

    const files = raw.files || {};
    for (const [kind, meta] of Object.entries(files)) {
      const k = (kind || "").toLowerCase();
      if (!VIEWABLE.has(k) || !meta || typeof meta !== "object") continue;
      const href = hrefFromStoredPath(meta.path);
      if (href) {
        docs.push({
          type: k === "htm" ? "html" : k,
          href,
          label: `Specification (${k.toUpperCase()})`,
        });
      }
    }
    return docs;
  }

  function renderDocumentLinksHtml(docs, escapeHtml) {
    if (!docs.length) return "";
    const esc = escapeHtml || ((s) => String(s));
    const rows = docs
      .map((d) => {
        const src = esc(d.href);
        const label = esc(d.label);
        const title = esc(d.label);
        return (
          `<span class="doc-link-row">` +
          `<a href="${src}" target="_blank" rel="noopener">${label}</a> ` +
          `<button type="button" class="btn-view-doc" data-src="${src}" data-title="${title}">View in window</button>` +
          `</span>`
        );
      })
      .join("<br/>");
    return `<div class="local-docs"><strong>Local copy</strong><br/>${rows}</div>`;
  }

  function bindViewButtons(container) {
    if (!container) return;
    container.querySelectorAll(".btn-view-doc").forEach((btn) => {
      btn.replaceWith(btn.cloneNode(true));
    });
    container.querySelectorAll(".btn-view-doc").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        openViewer(btn.getAttribute("data-src"), btn.getAttribute("data-title"));
      });
    });
  }

  global.EidasDocs = {
    isSafeSrc,
    openViewer,
    corpusSourceLinks,
    renderCorpusSourceLinksHtml,
    localDocumentsForNode,
    renderDocumentLinksHtml,
    bindViewButtons,
    VIEWABLE,
  };
})(typeof window !== "undefined" ? window : globalThis);
