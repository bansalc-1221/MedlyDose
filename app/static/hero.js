/* MedlyDose welcome page: theme, and the blister pack that pops once. */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  /* theme */
  $$("[data-theme-toggle]").forEach(b => b.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("mt-theme", next); } catch (e) {}
  }));

  /* the pack: five cells pop one after another when the page loads, and any cell can be clicked */
  const cells = $$("#pack .cell");
  const still = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  [0, 1, 2, 4, 5].forEach((idx, n) => {
    if (still) cells[idx].classList.add("popped");
    else setTimeout(() => cells[idx].classList.add("popped"), 700 + n * 260);
  });
  cells.forEach(c => c.addEventListener("click", () => c.classList.toggle("popped")));
})();
