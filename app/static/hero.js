/* MedTracker landing page: theme, the blister pack that pops once, and the sign-in dialog. */
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

  /* sign-in dialog */
  const dlg = $("#auth-dialog"), form = $("#auth-form"), err = $("#auth-error");
  let mode = "signup";

  function setMode(m) {
    mode = m;
    $("#auth-title").textContent = m === "signup" ? "Create your account" : "Welcome back";
    $("#auth-submit").textContent = m === "signup" ? "Create account" : "Sign in";
    $("#a-pass").autocomplete = m === "signup" ? "new-password" : "current-password";
    $$("[data-tab]").forEach(t => t.setAttribute("aria-selected", String(t.dataset.tab === m)));
    err.textContent = "";
  }
  function openDialog(m) { setMode(m); form.reset(); dlg.showModal(); $("#a-name").focus(); }

  $$("[data-open]").forEach(b => b.addEventListener("click", () => openDialog(b.dataset.open)));
  $$("[data-tab]").forEach(t => t.addEventListener("click", () => setMode(t.dataset.tab)));
  $$("[data-close]").forEach(b => b.addEventListener("click", () => dlg.close()));
  dlg.addEventListener("click", e => { if (e.target === dlg) dlg.close(); });

  form.addEventListener("submit", async e => {
    e.preventDefault();
    const btn = $("#auth-submit");
    err.textContent = "";
    btn.disabled = true;
    try {
      const res = await fetch("/api/auth/" + mode, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: $("#a-name").value, password: $("#a-pass").value })
      });
      const data = await res.json();
      if (data.ok) { location.href = "/"; return; }
      err.textContent = data.message || "Something went wrong. Try again.";
    } catch (x) {
      err.textContent = "Couldn't reach the app. It may be restarting. Try again in a few seconds.";
    }
    btn.disabled = false;
  });

  /* If nobody has an account yet, the Sign in button opens Create account first. */
  fetch("/api/auth/me").then(r => r.json()).then(d => {
    if (!d.has_users) $$('[data-open="signin"]').forEach(b => b.dataset.open = "signup");
  }).catch(() => {});
})();
