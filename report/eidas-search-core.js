/**
 * Shared search helpers for report search + interactive graph filtering.
 *
 * Query syntax (case-insensitive):
 *   word or +word     — must appear
 *   -word             — must not appear
 *   "phrase words"    — exact phrase (substring match)
 *
 * Examples: ETSI +"qualified certificate" -draft · "Wallet Unit" +TS03
 */
(function (global) {
  "use strict";

  const MIN_TERM_LEN = 2;

  /**
   * @typedef {{ raw: string, phrases: string[], required: string[], excluded: string[] }} ParsedQuery
   */

  /**
   * @param {string} query
   * @returns {ParsedQuery}
   */
  function parseQuery(query) {
    const raw = String(query || "").trim();
    /** @type {ParsedQuery} */
    const parsed = { raw, phrases: [], required: [], excluded: [] };
    if (!raw) return parsed;

    const phraseRe = /"([^"]*)"/g;
    let scratch = raw;
    let match;
    while ((match = phraseRe.exec(raw)) !== null) {
      const phrase = match[1].trim().toLowerCase();
      if (phrase) parsed.phrases.push(phrase);
    }
    scratch = raw.replace(phraseRe, " ");

    const tokens = scratch.match(/[+-]?[^\s]+/g) || [];
    for (let tok of tokens) {
      tok = tok.trim();
      if (!tok) continue;
      let mode = "required";
      let word = tok;
      if (tok[0] === "+" || tok[0] === "-") {
        mode = tok[0] === "-" ? "excluded" : "required";
        word = tok.slice(1);
      }
      word = word.toLowerCase();
      if (!word) continue;
      if (mode === "excluded") {
        if (word.length >= MIN_TERM_LEN) parsed.excluded.push(word);
      } else if (word.length >= MIN_TERM_LEN) {
        parsed.required.push(word);
      }
    }

    return parsed;
  }

  /** @param {ParsedQuery} parsed */
  function hasQuery(parsed) {
    return !!(
      parsed.phrases.length ||
      parsed.required.length ||
      parsed.excluded.length
    );
  }

  /** @param {ParsedQuery} parsed — strings to highlight in snippets (longest first) */
  function highlightPatterns(parsed) {
    const seen = new Set();
    const out = [];
    for (const p of parsed.phrases) {
      if (p && !seen.has(p)) {
        seen.add(p);
        out.push(p);
      }
    }
    for (const t of parsed.required) {
      if (t && !seen.has(t)) {
        seen.add(t);
        out.push(t);
      }
    }
    return out.sort((a, b) => b.length - a.length);
  }

  /** Back-compat: positive terms and phrases for callers expecting a term list. */
  function tokenize(query) {
    return highlightPatterns(parseQuery(query));
  }

  function parseTags(raw) {
    return String(raw || "")
      .split(/[,;\s]+/)
      .map((t) => t.trim().toLowerCase())
      .filter(Boolean);
  }

  function buildHaystack(parts) {
    return parts
      .flat()
      .filter(Boolean)
      .map((p) => (Array.isArray(p) ? p.join(" ") : String(p)))
      .join(" ")
      .toLowerCase();
  }

  /**
   * @param {string} haystack — lowercase text
   * @param {ParsedQuery} parsed
   */
  function matchesQuery(haystack, parsed) {
    const hay = haystack;
    if (!hasQuery(parsed)) return true;
    for (const phrase of parsed.phrases) {
      if (!hay.includes(phrase)) return false;
    }
    for (const term of parsed.required) {
      if (!hay.includes(term)) return false;
    }
    for (const term of parsed.excluded) {
      if (hay.includes(term)) return false;
    }
    return true;
  }

  /** @deprecated Use matchesQuery — all listed terms are required. */
  function matchesTerms(haystack, terms) {
    return matchesQuery(haystack, {
      raw: "",
      phrases: [],
      required: terms.map((t) => String(t).toLowerCase()),
      excluded: [],
    });
  }

  /**
   * @param {string} haystack
   * @param {ParsedQuery} parsed
   */
  function scoreQuery(haystack, parsed, requiredTags, tagHaystack) {
    if (requiredTags.length) {
      const tags = (tagHaystack || "").toLowerCase();
      for (const rt of requiredTags) {
        if (!tags.includes(rt)) return -1;
      }
    }
    if (!hasQuery(parsed)) return requiredTags.length ? 2 : 1;
    if (!matchesQuery(haystack, parsed)) return -1;
    let score = 0;
    for (const phrase of parsed.phrases) {
      if (haystack.includes(phrase)) score += 25;
    }
    for (const term of parsed.required) {
      if (haystack.includes(term)) score += 10;
    }
    return score;
  }

  /** @deprecated Use scoreQuery */
  function scoreHaystack(haystack, terms, requiredTags, tagHaystack) {
    return scoreQuery(
      haystack,
      { raw: "", phrases: [], required: terms, excluded: [] },
      requiredTags,
      tagHaystack
    );
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  global.EidasSearch = {
    parseQuery,
    hasQuery,
    highlightPatterns,
    tokenize,
    parseTags,
    buildHaystack,
    matchesQuery,
    matchesTerms,
    scoreQuery,
    scoreHaystack,
    escapeHtml,
  };
})(typeof window !== "undefined" ? window : globalThis);
