/**
 * Paginated catalogue tables — loads one page of rows at a time (no giant HTML dump).
 *
 * Expects a root element:
 *   <div id="catalogue" data-catalogue="downloaded"
 *        data-page-size="200" data-total="123"
 *        data-columns='["Body","Designation",...]'>
 */
(function () {
  "use strict";

  const PAGE_CACHE = new Map();

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector('script[data-catalogue-src="' + src + '"]');
      if (existing) {
        existing.addEventListener("load", () => resolve());
        existing.addEventListener("error", () => reject(new Error("Failed to load " + src)));
        if (existing.dataset.loaded === "1") resolve();
        return;
      }
      const s = document.createElement("script");
      s.src = src;
      s.async = true;
      s.dataset.catalogueSrc = src;
      s.onload = () => {
        s.dataset.loaded = "1";
        resolve();
      };
      s.onerror = () => reject(new Error("Failed to load " + src));
      document.head.appendChild(s);
    });
  }

  function pagePath(catalogueId, page) {
    const n = String(page).padStart(4, "0");
    return "data/" + catalogueId + "/page-" + n + ".js";
  }

  function loadPage(catalogueId, page) {
    const key = catalogueId + ":" + page;
    if (PAGE_CACHE.has(key)) return Promise.resolve(PAGE_CACHE.get(key));
    // Clear any stale payload before loading the next page script.
    window.EIDAS_CATALOGUE_PAGE = null;
    return loadScript(pagePath(catalogueId, page)).then(() => {
      const payload = window.EIDAS_CATALOGUE_PAGE;
      if (!payload || payload.id !== catalogueId || Number(payload.page) !== Number(page)) {
        throw new Error("Catalogue page payload missing for " + key);
      }
      PAGE_CACHE.set(key, payload);
      return payload;
    });
  }

  function renderHead(thead, columns) {
    thead.innerHTML =
      "<tr>" + columns.map((c) => "<th>" + escapeText(c) + "</th>").join("") + "</tr>";
  }

  function escapeText(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderBody(tbody, rows, colCount) {
    if (!rows.length) {
      tbody.innerHTML =
        '<tr><td colspan="' + colCount + '">No rows on this page.</td></tr>';
      return;
    }
    const html = rows
      .map((row) => "<tr>" + row.map((cell) => "<td>" + cell + "</td>").join("") + "</tr>")
      .join("");
    tbody.innerHTML = html;
  }

  function renderPager(nav, state) {
    const { page, pageCount, total, pageSize } = state;
    if (pageCount <= 1) {
      nav.innerHTML =
        '<p class="catalogue-page-info">' + total + " row" + (total === 1 ? "" : "s") + "</p>";
      return;
    }
    const from = page * pageSize + 1;
    const to = Math.min(total, (page + 1) * pageSize);
    nav.innerHTML =
      '<button type="button" class="btn" data-act="first" ' +
      (page === 0 ? "disabled" : "") +
      ">First</button>" +
      '<button type="button" class="btn" data-act="prev" ' +
      (page === 0 ? "disabled" : "") +
      ">Previous</button>" +
      '<span class="catalogue-page-info">' +
      from +
      "–" +
      to +
      " of " +
      total +
      " (page " +
      (page + 1) +
      " / " +
      pageCount +
      ")</span>" +
      '<button type="button" class="btn" data-act="next" ' +
      (page >= pageCount - 1 ? "disabled" : "") +
      ">Next</button>" +
      '<button type="button" class="btn" data-act="last" ' +
      (page >= pageCount - 1 ? "disabled" : "") +
      ">Last</button>";
  }

  function init(root) {
    const catalogueId = root.dataset.catalogue;
    if (!catalogueId) return;

    let columns = [];
    try {
      const raw = root.getAttribute("data-columns") || root.dataset.columns || "[]";
      columns = JSON.parse(raw);
    } catch (_err) {
      columns = [];
    }
    const pageSize = Math.max(1, parseInt(root.dataset.pageSize || "200", 10) || 200);
    const total = Math.max(0, parseInt(root.dataset.total || "0", 10) || 0);
    const pageCount = Math.max(1, Math.ceil(total / pageSize) || 1);

    const status = $("#catalogue-status", root) || root.querySelector("[data-role='status']");
    const thead = $("#catalogue-head", root);
    const tbody = $("#catalogue-body", root);
    const pager = $("#catalogue-pager", root);
    if (!thead || !tbody) return;

    renderHead(thead, columns);

    const state = { page: 0, pageCount, total, pageSize };

    const params = new URLSearchParams(window.location.search);
    const pageParam = parseInt(params.get("page") || "1", 10);
    if (pageParam >= 1 && pageParam <= pageCount) state.page = pageParam - 1;

    function setStatus(msg) {
      if (status) status.textContent = msg;
    }

    function showPage(page) {
      state.page = Math.max(0, Math.min(pageCount - 1, page));
      setStatus("Loading page " + (state.page + 1) + "…");
      tbody.innerHTML =
        '<tr><td colspan="' +
        Math.max(columns.length, 1) +
        '">Loading…</td></tr>';
      if (pager) renderPager(pager, state);

      const url = new URL(window.location.href);
      if (state.page === 0) url.searchParams.delete("page");
      else url.searchParams.set("page", String(state.page + 1));
      window.history.replaceState({}, "", url);

      return loadPage(catalogueId, state.page)
        .then((payload) => {
          renderBody(tbody, payload.rows || [], columns.length);
          if (pager) renderPager(pager, state);
          setStatus("");
        })
        .catch((err) => {
          const detail = err && err.message ? err.message : String(err);
          tbody.innerHTML =
            '<tr><td colspan="' +
            Math.max(columns.length, 1) +
            '">Could not load catalogue page (' +
            escapeText(detail) +
            ").</td></tr>";
          setStatus("Load failed.");
          console.error(err);
        });
    }

    if (pager) {
      pager.addEventListener("click", (ev) => {
        const btn = ev.target.closest("button[data-act]");
        if (!btn || btn.disabled) return;
        const act = btn.getAttribute("data-act");
        if (act === "first") showPage(0);
        else if (act === "prev") showPage(state.page - 1);
        else if (act === "next") showPage(state.page + 1);
        else if (act === "last") showPage(pageCount - 1);
      });
    }

    showPage(state.page);
  }

  function boot() {
    document.querySelectorAll("#catalogue[data-catalogue], [data-catalogue]").forEach(init);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
