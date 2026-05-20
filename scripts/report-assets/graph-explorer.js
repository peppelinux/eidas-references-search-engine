/**
 * Interactive hierarchical reference graph (EU legal acts → specifications).
 * Requires: vis-network, EidasSearch, window.EIDAS_GRAPH_DATA
 */
(function () {
  "use strict";

  const SDO_ICONS = {
    ETSI: { icon: "📡", label: "ETSI", color: "#0b5cad" },
    IETF: { icon: "🌐", label: "IETF", color: "#2d7d46" },
    W3C: { icon: "🔗", label: "W3C", color: "#005a9c" },
    CEN: { icon: "🇪🇺", label: "CEN", color: "#003399" },
    ARF: { icon: "🇪🇺", label: "ARF (EC TS)", color: "#003399" },
    "ISO-IEC": { icon: "🔒", label: "ISO/IEC", color: "#6b4c9a" },
    "ITU-T": { icon: "📞", label: "ITU-T", color: "#006699" },
    IEEE: { icon: "⚡", label: "IEEE", color: "#00629b" },
    other: { icon: "📄", label: "Other", color: "#666" },
  };

  const DOWNLOADED = new Set(["downloaded", "unchanged"]);

  let graphData = null;
  /** @type {Map<string, object>} */
  let graphNodesById = new Map();
  let network = null;
  let nodesDataSet = null;
  let edgesDataSet = null;
  let nodeById = new Map();
  let adjacency = { out: new Map(), in: new Map() };
  let excludedBodies = new Set();
  let layoutFrozen = false;
  let layoutFreezePending = false;
  /** @type {Map<string, {x: number, y: number}>} */
  const userPositions = new Map();

  const $ = (sel, root) => (root || document).querySelector(sel);

  function pinFixed() {
    return { x: true, y: true };
  }

  function rememberNodePositions(ids) {
    if (!network) return;
    const list = ids || nodesDataSet.getIds();
    const pos = network.getPositions(list);
    for (const id of list) {
      if (pos[id]) userPositions.set(id, { x: pos[id].x, y: pos[id].y });
    }
  }

  function positionFields(id) {
    const p = userPositions.get(id);
    if (!p) return {};
    return { x: p.x, y: p.y, fixed: pinFixed() };
  }

  function sdoMeta(body) {
    return SDO_ICONS[body] || SDO_ICONS.other;
  }

  function specLabel(node) {
    if (!node || typeof node !== "object") return "";
    if (node.type === "legal_regulation") return node.act_id || node.id || "";
    const parts = [node.body, node.designation].filter(Boolean);
    if (node.version) parts.push("V" + node.version);
    if (parts.length) return parts.join(" ");
    return node.act_id || node.id || "";
  }

  function specNodeId(node) {
    const label = specLabel(node);
    return label ? String(label).replace(/"/g, "'") : "";
  }

  /**
   * Assign vis-network hierarchical levels: root → legal → specs → nested specs (deeper = lower).
   */
  function computeNodeLevels(rawNodes, rawEdges) {
    const levels = new Map([["__root__", 0]]);

    for (const n of rawNodes) {
      if (n.type === "legal_regulation") levels.set(n.id, 1);
      if (n.type === "specification" && n.body === "ARF" && n.folder === "ARF") {
        levels.set(n.id, 1);
      }
    }

    for (const e of rawEdges) {
      if (e.kind !== "cites") continue;
      const fromLv = levels.get(e.from) ?? 0;
      levels.set(e.to, Math.max(levels.get(e.to) ?? 0, fromLv + 1));
    }

    let changed = true;
    let guard = 0;
    while (changed && guard++ < 64) {
      changed = false;
    for (const e of rawEdges) {
      if (e.kind !== "references" && e.kind !== "related") continue;
      const parentLv = levels.get(e.from);
        const childLv = levels.get(e.to) ?? 0;
        if (parentLv !== undefined) {
          const want = parentLv + 1;
          if (want > childLv) {
            levels.set(e.to, want);
            changed = true;
          }
        } else if (childLv > 2) {
          const wantParent = childLv - 1;
          if ((levels.get(e.from) ?? 0) < wantParent) {
            levels.set(e.from, wantParent);
            changed = true;
          }
        }
      }
    }

    for (const n of rawNodes) {
      if (n.type === "specification" && !levels.has(n.id)) {
        levels.set(n.id, 2);
      }
    }

    return levels;
  }

  function nodeHaystack(node) {
    return EidasSearch.buildHaystack([
      node.label,
      node.title,
      node.act_id,
      node.celex,
      node.designation,
      node.body,
      node.summary,
      node.scope_keywords,
      node.tags,
      node.search_text,
    ]);
  }

  function buildVisNodes(rawNodes, rawEdges) {
    const levels = computeNodeLevels(rawNodes, rawEdges);
    const visNodes = [];
    visNodes.push({
      id: "__root__",
      label: "eIDAS / EUDI\nlegal & technical references",
      level: levels.get("__root__") ?? 0,
      shape: "box",
      color: { background: "#1a365d", border: "#0f2440", highlight: { background: "#2c5282", border: "#1a365d" } },
      font: { color: "#fff", size: 14 },
      type: "root",
      fixed: false,
    });

    for (const n of rawNodes) {
      const id = n.id;
      if (n.type === "legal_regulation") {
        const title = n.title || "";
        const short = title.length > 42 ? title.slice(0, 40) + "…" : title;
        visNodes.push({
          id,
          label: `${n.act_id || "?"}\n${short}`,
          level: levels.get(id) ?? 1,
          shape: "box",
          color: {
            background: "#e8f4fc",
            border: "#0366d6",
            highlight: { background: "#cce5ff", border: "#024ea4" },
          },
          font: { size: 12, multi: true },
          type: "legal_regulation",
          raw: n,
        });
      } else if (n.type === "specification") {
        const meta = sdoMeta(n.body);
        const status = n.status || "";
        const ok = DOWNLOADED.has(status);
        const des = n.designation || n.id;
        const isArfCatalog =
          n.body === "ARF" && n.folder === "ARF";
        const shortDes = isArfCatalog
          ? "EC TS01–TS11"
          : des.length > 36
            ? des.slice(0, 34) + "…"
            : des;
        visNodes.push({
          id,
          label: `${meta.icon} ${shortDes}${n.version ? "\nV" + n.version : ""}`,
          level: levels.get(id) ?? 2,
          shape: isArfCatalog ? "box" : "ellipse",
          color: {
            background: ok ? "#e8fce8" : "#f0f0f0",
            border: meta.color,
            highlight: { background: ok ? "#d4f5d4" : "#e8e8e8", border: meta.color },
          },
          font: { size: 11, multi: true },
          borderWidth: ok ? 2 : 1,
          type: "specification",
          body: n.body,
          raw: n,
        });
      }
    }
    return visNodes;
  }

  function buildVisEdges(rawEdges, rawNodes) {
    const visEdges = [];
    const legalIds = new Set(rawNodes.filter((n) => n.type === "legal_regulation").map((n) => n.id));

    for (const lid of legalIds) {
      visEdges.push({
        id: `root-${lid}`,
        from: "__root__",
        to: lid,
        arrows: "to",
        color: { color: "#aab", highlight: "#0366d6" },
        width: 1,
        dashes: true,
      });
    }

    const arfCatalog = rawNodes.find(
      (n) => n.type === "specification" && n.body === "ARF" && n.folder === "ARF"
    );
    if (arfCatalog) {
      visEdges.push({
        id: "root-arf-catalog",
        from: "__root__",
        to: arfCatalog.id,
        arrows: "to",
        color: { color: "#003399", highlight: "#0366d6" },
        width: 1.5,
        dashes: true,
      });
    }

    for (const e of rawEdges) {
      if (e.kind !== "cites") continue;
      visEdges.push({
        id: `${e.from}-${e.to}-${e.kind}`,
        from: e.from,
        to: e.to,
        arrows: "to",
        color: { color: "#888", highlight: "#0366d6" },
        width: 1.5,
        title: "cites",
      });
    }

    for (const e of rawEdges) {
      if (e.kind !== "references") continue;
      visEdges.push({
        id: `${e.from}-${e.to}-ref`,
        from: e.from,
        to: e.to,
        arrows: "to",
        color: { color: "#c9a", highlight: "#b80" },
        width: 1,
        dashes: [4, 4],
        title: "references",
      });
    }

    for (const e of rawEdges) {
      if (e.kind !== "related") continue;
      visEdges.push({
        id: `${e.from}-${e.to}-related`,
        from: e.from,
        to: e.to,
        arrows: "to",
        color: { color: "#9ab", highlight: "#0366d6" },
        width: 1,
        dashes: [2, 6],
        title: "related (EUDI Wallet)",
      });
    }
    return visEdges;
  }

  function buildAdjacency(edges) {
    adjacency = { out: new Map(), in: new Map() };
    for (const e of edges) {
      if (!adjacency.out.has(e.from)) adjacency.out.set(e.from, new Set());
      if (!adjacency.in.has(e.to)) adjacency.in.set(e.to, new Set());
      adjacency.out.get(e.from).add(e.to);
      adjacency.in.get(e.to).add(e.from);
    }
  }

  function computeVisibility(parsed) {
    const visible = new Set();
    const highlight = new Set();

    for (const [id, node] of nodeById) {
      if (id === "__root__") {
        visible.add(id);
        continue;
      }
      const raw = node.raw || node;
      let show = true;

      if (raw.type === "specification" && excludedBodies.size > 0) {
        if (excludedBodies.has(raw.body)) show = false;
      }

      const hay = nodeHaystack(raw);
      const textMatch = EidasSearch.matchesQuery(hay, parsed);
      const hasTextFilter = EidasSearch.hasQuery(parsed);

      if (hasTextFilter && raw.type === "specification") {
        show = show && textMatch;
      } else if (hasTextFilter && raw.type === "legal_regulation") {
        show = show && textMatch;
      }

      if (show) visible.add(id);
      if (textMatch && hasTextFilter) highlight.add(id);
    }

    if (EidasSearch.hasQuery(parsed) || excludedBodies.size > 0) {
      let frontier = [...visible];
      while (frontier.length) {
        const next = [];
        for (const id of frontier) {
          const parents = adjacency.in.get(id);
          if (!parents) continue;
          for (const p of parents) {
            if (p !== "__root__" && !visible.has(p)) {
              visible.add(p);
              next.push(p);
            }
          }
        }
        frontier = next;
      }
      for (const id of [...visible]) {
        if (nodeById.get(id)?.type === "legal_regulation") {
          const children = adjacency.out.get(id);
          if (children) {
            children.forEach((c) => {
              const child = nodeById.get(c);
              if (!child || child.type !== "specification") return;
              const raw = child.raw;
              let ok = true;
              if (excludedBodies.size > 0 && excludedBodies.has(raw.body)) ok = false;
              if (
                EidasSearch.hasQuery(parsed) &&
                !EidasSearch.matchesQuery(nodeHaystack(raw), parsed)
              ) {
                if (!highlight.has(c)) ok = false;
              }
              if (ok || highlight.has(c)) visible.add(c);
            });
          }
        }
      }
      visible.add("__root__");
    }

    return { visible, highlight };
  }

  function applyFilters() {
    if (!nodesDataSet || !network) return;
    if (!layoutFrozen) {
      layoutFreezePending = true;
      return;
    }

    const q = $("#graph-search")?.value || "";
    const parsed = EidasSearch.parseQuery(q);
    const { visible, highlight } = computeVisibility(parsed);
    const hasFilter = EidasSearch.hasQuery(parsed) || excludedBodies.size > 0;

    const camera = {
      position: network.getViewPosition(),
      scale: network.getScale(),
    };

    const updates = [];
    for (const id of nodeById.keys()) {
      const hidden = !visible.has(id);
      const n = nodeById.get(id);
      const isHi = highlight.has(id);
      const upd = { id, hidden, ...positionFields(id) };
      if (n?.type === "specification" || n?.type === "legal_regulation") {
        if (hidden) {
          upd.opacity = 0;
        } else if (EidasSearch.hasQuery(parsed) && !isHi) {
          upd.opacity = 0.4;
        } else {
          upd.opacity = 1;
        }
        upd.borderWidth = isHi ? 3 : n.type === "specification" ? 2 : 1;
      }
      updates.push(upd);
    }
    nodesDataSet.update(updates);

    const edgeUpdates = edgesDataSet.get().map((e) => {
      const hide = !visible.has(e.from) || !visible.has(e.to);
      return { id: e.id, hidden: hide };
    });
    edgesDataSet.update(edgeUpdates);

    requestAnimationFrame(() => {
      network.moveTo({
        position: camera.position,
        scale: camera.scale,
        animation: false,
      });
    });

    const shown = [...visible].filter((id) => id !== "__root__").length;
    const total = nodeById.size - 1;
    const status = $("#graph-status");
    if (status) {
      let msg = `Showing ${shown} of ${total} nodes · hierarchical layout`;
      if (EidasSearch.hasQuery(parsed)) {
        const bits = [];
        for (const p of parsed.phrases) bits.push(`"${p}"`);
        for (const t of parsed.required) bits.push(`+${t}`);
        for (const t of parsed.excluded) bits.push(`-${t}`);
        msg += ` · search: ${bits.join(" ")}`;
      }
      if (excludedBodies.size) {
        const bodies = Object.keys(SDO_ICONS)
          .filter((b) => b !== "other" && !excludedBodies.has(b))
          .join(", ");
        msg += ` · SDO shown: ${bodies || "—"}`;
      }
      msg += " · drag to reposition (nodes stay put)";
      status.textContent = msg;
    }
  }

  function formatContextSummaryBlock(raw) {
    const parts = [];
    const legal = raw.parent_legal_regulations || [];
    if (legal.length) {
      const acts = legal
        .map((lp) => lp.title || lp.id)
        .filter(Boolean)
        .slice(0, 4);
      if (acts.length) parts.push(`Cited by EU act(s): ${acts.join("; ")}.`);
    }
    if (raw.reason) parts.push(String(raw.reason).trim());
    if (parts.length) {
      return `<div class="summary-block"><strong>Context</strong><br/>${EidasSearch.escapeHtml(parts.join(" "))}</div>`;
    }
    return (
      '<p class="summary-block"><em>No summary yet. Run <code>make summaries</code> ' +
      "(refreshes <code>report/</code> automatically).</em></p>"
    );
  }

  function resolveGraphNode(id) {
    if (!id) return null;
    return graphNodesById.get(id) || null;
  }

  function resolveNodeForDetail(nodeOrId) {
    const id = typeof nodeOrId === "string" ? nodeOrId : nodeOrId?.id;
    const visNode = typeof nodeOrId === "object" && nodeOrId ? nodeOrId : nodeById.get(id);
    const graphNode = resolveGraphNode(id);
    if (!visNode && !graphNode) return null;
    const raw = graphNode || visNode?.raw || visNode;
    return { ...(visNode || {}), id, raw };
  }

  function corpusAnchorId() {
    return graphData?.corpus_anchor_id || "legal:eidas-consolidated";
  }

  function resolveDetailTarget(nodeOrId) {
    const resolved = resolveNodeForDetail(nodeOrId);
    if (!resolved) return null;
    if (resolved.id !== "__root__") return resolved;
    return resolveNodeForDetail(corpusAnchorId()) || resolved;
  }

  function renderDetail(node) {
    const panel = $("#graph-detail");
    if (!panel) return;
    const resolved = resolveDetailTarget(node);
    if (!resolved || resolved.id === "__root__") {
      panel.innerHTML =
        '<p class="placeholder">Click a legal act or specification to view summary, scope keywords, and links.</p>';
      return;
    }
    const raw = resolved.raw || resolved;
    const links = [];
    const localDocs =
      window.EidasDocs && EidasDocs.localDocumentsForNode
        ? EidasDocs.localDocumentsForNode(raw)
        : [];
    if (raw.type === "legal_regulation") {
      if (raw.eli) links.push({ label: "EUR-Lex (ELI)", href: raw.eli, ext: true });
      if (raw.celex) {
        links.push({
          label: "EUR-Lex (CELEX)",
          href: `https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:${encodeURIComponent(raw.celex)}`,
          ext: true,
        });
      }
      if (raw.section && raw.act_id) {
        links.push({
          label: "Legal markdown",
          href: `../${raw.section}/${raw.act_id}/${raw.act_id}.md`,
        });
      }
    } else {
      if (raw.folder) {
        links.push({
          label: "reference.json",
          href: `../referenced-standards/standards/${raw.folder}/reference.json`,
        });
      }
      if (raw.download_url) {
        links.push({ label: "Download / catalogue", href: raw.download_url, ext: true });
      }
    }
    const localDocsHtml =
      window.EidasDocs && localDocs.length
        ? EidasDocs.renderDocumentLinksHtml(localDocs, EidasSearch.escapeHtml)
        : "";

    const badges = [];
    if (raw.type === "legal_regulation") badges.push('<span class="badge legal">EU legal act</span>');
    else {
      const meta = sdoMeta(raw.body);
      badges.push(`<span class="badge body">${EidasSearch.escapeHtml(meta.icon)} ${EidasSearch.escapeHtml(meta.label)}</span>`);
      if (raw.status) badges.push(`<span class="badge">${EidasSearch.escapeHtml(raw.status)}</span>`);
    }

    const titleBlock =
      raw.title && raw.title !== specLabel(raw)
        ? `<p class="spec-title"><strong>Title:</strong> ${EidasSearch.escapeHtml(raw.title)}</p>`
        : "";
    const purposeBlock = raw.purpose
      ? `<p class="spec-purpose"><strong>Purpose:</strong> ${EidasSearch.escapeHtml(raw.purpose)}</p>`
      : "";
    const summary = raw.summary
      ? `<div class="summary-block"><strong>Summary</strong><br/>${EidasSearch.escapeHtml(raw.summary)}</div>`
      : formatContextSummaryBlock(raw);

    const kw = (raw.scope_keywords || []).length
      ? `<p class="keywords"><strong>Scope / purpose:</strong> ${EidasSearch.escapeHtml(
          raw.scope_keywords.join(", ")
        )}</p>`
      : "";

    const metaRows = [];
    if (raw.celex) metaRows.push(["CELEX", raw.celex]);
    if (raw.kind) metaRows.push(["Kind", raw.kind]);
    if (raw.version) metaRows.push(["Version", raw.version]);
    if (raw.released_at) metaRows.push(["Released", raw.released_at]);
    if (raw.parent_legal_regulations?.length) {
      metaRows.push([
        "Cited by (EU law)",
        raw.parent_legal_regulations
          .map((p) => {
            const lid = `legal:${p.id}`;
            return `<a href="#" class="graph-jump" data-graph-node="${EidasSearch.escapeHtml(lid)}">${EidasSearch.escapeHtml(p.id)}</a>`;
          })
          .join(", "),
      ]);
    }
    if (raw.parent_specifications?.length) {
      metaRows.push([
        "Referenced by (specs)",
        raw.parent_specifications
          .map((p) => {
            const pid = specNodeId(p);
            const label = specLabel(p) || [p.body, p.designation].filter(Boolean).join(" ");
            if (!pid) return EidasSearch.escapeHtml(label);
            return `<a href="#" class="graph-jump" data-graph-node="${EidasSearch.escapeHtml(pid)}">${EidasSearch.escapeHtml(label)}</a>`;
          })
          .join(", "),
      ]);
    }

    const metaTable =
      metaRows.length > 0
        ? `<table class="meta-table"><tbody>${metaRows
            .map(([k, v]) => {
              const cell =
                typeof v === "string" && v.includes("<a ")
                  ? v
                  : EidasSearch.escapeHtml(v);
              return `<tr><th>${EidasSearch.escapeHtml(k)}</th><td>${cell}</td></tr>`;
            })
            .join("")}</tbody></table>`
        : "";

    panel.innerHTML = `
      <h3>${EidasSearch.escapeHtml(specLabel(raw))}</h3>
      <p class="badges">${badges.join("")}</p>
      ${titleBlock}
      ${purposeBlock}
      ${summary}
      ${kw}
      ${localDocsHtml}
      ${metaTable}
      <p class="links"><strong>Links</strong>
        ${links.map((l) => {
          const rel = l.ext ? ' target="_blank" rel="noopener"' : "";
          return `<a href="${EidasSearch.escapeHtml(l.href)}"${rel}>${EidasSearch.escapeHtml(l.label)}</a>`;
        }).join("")}
      </p>`;
    if (window.EidasDocs) EidasDocs.bindViewButtons(panel);

    panel.querySelectorAll(".graph-jump").forEach((a) => {
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        const targetId = a.getAttribute("data-graph-node");
        if (!targetId || !network || !nodeById.has(targetId)) return;
        const target = resolveNodeForDetail(targetId);
        nodesDataSet.update({ id: targetId, hidden: false, opacity: 1 });
        network.selectNodes([targetId]);
        network.focus(targetId, { scale: 1.15, animation: { duration: 400, easingFunction: "easeInOutQuad" } });
        renderDetail(target);
      });
    });
  }

  function initSdoChips(bodies) {
    const wrap = $("#sdo-filters");
    if (!wrap) return;
    wrap.innerHTML = '<span class="label">SDO (click to hide):</span>';
    for (const body of bodies) {
      const meta = sdoMeta(body);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "sdo-chip active";
      btn.dataset.body = body;
      btn.innerHTML = `<span class="icon">${meta.icon}</span><span>${EidasSearch.escapeHtml(meta.label)}</span>`;
      btn.title = `Show/hide ${meta.label} specifications`;
      btn.addEventListener("click", () => {
        if (excludedBodies.has(body)) {
          excludedBodies.delete(body);
          btn.classList.add("active");
          btn.classList.remove("muted");
        } else {
          excludedBodies.add(body);
          btn.classList.remove("active");
          btn.classList.add("muted");
        }
        applyFilters();
      });
      wrap.appendChild(btn);
    }
    excludedBodies.clear();
  }

  /**
   * Run hierarchical layout once, then freeze: physics off, positions stored, nodes pinned.
   */
  function freezeHierarchicalLayout() {
    if (!network || layoutFrozen) return;
    layoutFrozen = true;
    layoutFreezePending = false;

    rememberNodePositions();

    network.setOptions({
      layout: { hierarchical: { enabled: false }, improvedLayout: false },
      physics: { enabled: false, stabilization: { enabled: false } },
      edges: {
        smooth: { type: "cubicBezier", forceDirection: "vertical", roundness: 0.5 },
      },
    });

    const updates = [];
    for (const [id, p] of userPositions) {
      updates.push({ id, x: p.x, y: p.y, fixed: pinFixed() });
    }
    if (updates.length) nodesDataSet.update(updates);

    if (typeof network.stopSimulation === "function") network.stopSimulation();

    network.fit({ animation: { duration: 300, easingFunction: "easeInOutQuad" } });

    if (layoutFreezePending) applyFilters();
  }

  function setupDragPinning() {
    if (!network) return;

    network.on("dragStart", (params) => {
      if (!params.nodes?.length) return;
      network.setOptions({ physics: { enabled: false } });
      nodesDataSet.update(
        params.nodes.map((id) => ({
          id,
          fixed: false,
        }))
      );
    });

    network.on("dragEnd", (params) => {
      if (!params.nodes?.length) return;
      rememberNodePositions(params.nodes);
      nodesDataSet.update(
        params.nodes.map((id) => ({
          id,
          ...positionFields(id),
        }))
      );
      network.setOptions({ physics: { enabled: false } });
      if (typeof network.stopSimulation === "function") network.stopSimulation();
    });
  }

  function initNetwork(container, data) {
    layoutFrozen = false;
    layoutFreezePending = false;
    userPositions.clear();
    const visNodes = buildVisNodes(data.nodes, data.edges);
    const visEdges = buildVisEdges(data.edges, data.nodes);

    nodesDataSet = new vis.DataSet(visNodes);
    edgesDataSet = new vis.DataSet(visEdges);
    nodeById = new Map(visNodes.map((n) => [n.id, n]));
    buildAdjacency(visEdges);

    const options = {
      layout: {
        hierarchical: {
          enabled: true,
          direction: "UD",
          sortMethod: "hubsize",
          levelSeparation: 140,
          nodeSpacing: 85,
          treeSpacing: 75,
          blockShifting: true,
          edgeMinimization: true,
          parentCentralization: true,
          shakeTowards: "roots",
        },
      },
      physics: {
        enabled: true,
        hierarchicalRepulsion: {
          centralGravity: 0,
          springLength: 130,
          springConstant: 0.01,
          nodeDistance: 140,
          damping: 0.12,
        },
        stabilization: { iterations: 120, fit: true, updateInterval: 25 },
      },
      interaction: {
        dragNodes: true,
        dragView: true,
        zoomView: true,
        hover: true,
        tooltipDelay: 200,
        navigationButtons: true,
        keyboard: { enabled: true, bindToWindow: false },
      },
      nodes: {
        margin: 10,
        widthConstraint: { maximum: 200 },
      },
      edges: {
        smooth: { type: "cubicBezier", forceDirection: "vertical", roundness: 0.5 },
      },
    };

    network = new vis.Network(container, { nodes: nodesDataSet, edges: edgesDataSet }, options);
    setupDragPinning();

    network.once("stabilizationIterationsDone", freezeHierarchicalLayout);
    network.once("stabilized", freezeHierarchicalLayout);
    setTimeout(() => {
      if (!layoutFrozen) freezeHierarchicalLayout();
    }, 2500);

    network.on("click", (params) => {
      if (!params.nodes.length) return;
      renderDetail(resolveNodeForDetail(params.nodes[0]));
      network.selectNodes([params.nodes[0]]);
    });

    network.on("doubleClick", (params) => {
      if (params.nodes.length) network.focus(params.nodes[0], { scale: 1.2, animation: true });
    });
  }

  /** Keys that vis-network uses for pan/zoom — keep them in the search field when it is focused. */
  const GRAPH_NAV_KEYS = new Set([
    "ArrowLeft",
    "ArrowRight",
    "ArrowUp",
    "ArrowDown",
    "Home",
    "End",
    "PageUp",
    "PageDown",
  ]);

  function setupSearchKeyboard(searchEl) {
    if (!searchEl) return;

    const setGraphKeyboard = (enabled) => {
      if (!network) return;
      network.setOptions({
        interaction: { keyboard: { enabled, bindToWindow: false } },
      });
    };

    searchEl.addEventListener("focus", () => setGraphKeyboard(false));
    searchEl.addEventListener("blur", () => setGraphKeyboard(true));
    searchEl.addEventListener("keydown", (e) => {
      if (GRAPH_NAV_KEYS.has(e.key)) e.stopPropagation();
    });
  }

  function loadGraphData() {
    if (window.EIDAS_GRAPH_DATA) return Promise.resolve(window.EIDAS_GRAPH_DATA);
    return fetch("graph-data.json").then((r) => {
      if (!r.ok) throw new Error(r.statusText);
      return r.json();
    });
  }

  function boot() {
    const container = $("#graph-network");
    if (!container) return;

    loadGraphData()
      .then((data) => {
        graphData = data;
        graphNodesById = new Map(data.nodes.map((n) => [n.id, n]));
        const bodies = [...new Set(data.nodes.filter((n) => n.type === "specification" && n.body).map((n) => n.body))].sort();
        initSdoChips(bodies);
        initNetwork(container, data);

        const search = $("#graph-search");
        const debounce = (fn, ms) => {
          let t;
          return (...args) => {
            clearTimeout(t);
            t = setTimeout(() => fn(...args), ms);
          };
        };
        setupSearchKeyboard(search);
        search?.addEventListener("input", debounce(applyFilters, 200));
        $("#graph-search-btn")?.addEventListener("click", (e) => {
          e.preventDefault();
          applyFilters();
        });
        $("#graph-clear")?.addEventListener("click", () => {
          if (search) search.value = "";
          excludedBodies.clear();
          document.querySelectorAll(".sdo-chip").forEach((c) => {
            c.classList.add("active");
            c.classList.remove("muted");
          });
          applyFilters();
          renderDetail(null);
          if (layoutFrozen) {
            network.fit({ animation: { duration: 400, easingFunction: "easeInOutQuad" } });
          }
        });

        const params = new URLSearchParams(window.location.search);
        const qParam = params.get("q");
        if (qParam && search) search.value = qParam;
        layoutFreezePending = true;
        if (window.location.hash === "#graph") {
          document.getElementById("graph")?.scrollIntoView({ behavior: "smooth" });
        }
      })
      .catch((err) => {
        const status = $("#graph-status");
        if (status) status.textContent = "Could not load graph data. Run: make report";
        console.error(err);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
