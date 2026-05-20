/**
 * Mobile-friendly report navigation: toggle panel, close on link follow, escape key.
 */
(function () {
  "use strict";

  function init() {
    const nav = document.getElementById("site-nav");
    const toggle = document.getElementById("nav-toggle");
    const panel = document.getElementById("site-nav-panel");
    if (!nav || !toggle || !panel) return;

    const setOpen = (open) => {
      panel.classList.toggle("is-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    };

    toggle.addEventListener("click", () => {
      setOpen(!panel.classList.contains("is-open"));
    });

    panel.querySelectorAll('a[href^="#"]').forEach((link) => {
      link.addEventListener("click", () => {
        if (window.matchMedia("(max-width: 768px)").matches) {
          setOpen(false);
        }
      });
    });

    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && panel.classList.contains("is-open")) {
        setOpen(false);
        toggle.focus();
      }
    });

    window.addEventListener("resize", () => {
      if (window.matchMedia("(min-width: 769px)").matches) {
        panel.classList.add("is-open");
        toggle.setAttribute("aria-expanded", "true");
      }
    });

    if (window.matchMedia("(min-width: 769px)").matches) {
      panel.classList.add("is-open");
      toggle.setAttribute("aria-expanded", "true");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
