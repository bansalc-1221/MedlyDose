/* MedTracker: shared front-end code (theme, connection light, toasts, dialogs, helpers). */
(function () {
  "use strict";

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ---------- Theme ---------- */
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("mt-theme", t); } catch (e) {}
  }
  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }
  $$("[data-theme-toggle]").forEach(b =>
    b.addEventListener("click", () => applyTheme(currentTheme() === "dark" ? "light" : "dark")));

  /* ---------- Toasts ---------- */
  function toast(message, kind, ms) {
    const box = $("#toasts");
    const el = document.createElement("div");
    el.className = "toast " + (kind || "info");
    el.setAttribute("role", "status");
    el.textContent = message;
    box.appendChild(el);
    while (box.children.length > 4) box.firstChild.remove();
    setTimeout(() => el.remove(), ms || (kind === "bad" ? 6500 : 4000));
  }

  /* ---------- Connection light ---------- */
  let online = true;
  function setConn(ok) {
    $$(".conn").forEach(el => {
      el.classList.toggle("is-off", !ok);
      el.textContent = ok ? "Connected" : "Reconnecting…";
    });
    if (ok !== online) {
      online = ok;
      toast(ok ? "The app is back online." : "Lost contact with the app. Reconnecting automatically…",
            ok ? "ok" : "bad");
      window.dispatchEvent(new CustomEvent("mt-conn", { detail: ok }));
    }
  }

  /* ---------- API helper: JSON in, JSON out, 4 s timeout ---------- */
  async function api(path, opts) {
    opts = opts || {};
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), opts.timeout || 4000);
    try {
      const res = await fetch(path, {
        method: opts.method || "GET",
        headers: opts.body ? { "Content-Type": "application/json" } : undefined,
        body: opts.body ? JSON.stringify(opts.body) : undefined,
        cache: "no-store",
        signal: ctrl.signal
      });
      let data;
      try { data = await res.json(); }
      catch (e) { data = { ok: false, message: "The app sent an unexpected reply." }; }
      setConn(true);
      return data;
    } catch (e) {
      setConn(false);
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }

  /* Runs fn now and every ms milliseconds, never overlapping itself. */
  function poll(fn, ms) {
    let busy = false;
    async function tick() {
      if (busy) return;
      busy = true;
      try { await fn(); } catch (e) { /* the connection light already shows the problem */ }
      finally { busy = false; }
    }
    tick();
    setInterval(tick, ms);
    return tick;
  }

  setInterval(() => { api("/api/ping", { timeout: 2500 }).catch(() => {}); }, 3000);

  function fmtStamp(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d)) return esc(iso);
    const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
    if (d.toDateString() === new Date().toDateString()) return t;
    return d.toLocaleDateString([], { day: "numeric", month: "short" }) + " " + t;
  }

  const NAMES = { app: "App", sms_service: "SMS service", cloud_sync: "Cloud sync", database: "Database", traffic: "Traffic" };
  const WORDS = { green: "Healthy", amber: "Managing it", red: "Failed", unknown: "Unknown" };

  /* ---------- Dialogs ---------- */
  $$("dialog").forEach(dlg => dlg.addEventListener("click", e => { if (e.target === dlg) dlg.close("cancel"); }));
  $$("[data-close]").forEach(b => b.addEventListener("click", () => b.closest("dialog").close("cancel")));

  function confirmBox(o) {
    return new Promise(resolve => {
      const dlg = $("#confirm-dialog");
      $("#c-title").textContent = o.title;
      $("#c-text").textContent = o.text;
      $("#c-ok").textContent = o.confirm || "Confirm";
      dlg.returnValue = "cancel";
      const done = () => { dlg.removeEventListener("close", done); resolve(dlg.returnValue === "ok"); };
      dlg.addEventListener("close", done);
      dlg.showModal();
    });
  }

  /* ---------- Add-medicine dialog (shared by every page) ---------- */
  const addDlg = $("#add-dialog");
  let times = [];

  function renderChips() {
    $("#f-chips").innerHTML = times.map(t =>
      `<span class="chip">${esc(t)}<button type="button" data-rm="${esc(t)}" aria-label="Remove ${esc(t)}">` +
      `<svg class="ic"><use href="#i-x"/></svg></button></span>`).join("");
  }
  function addTime() {
    const v = $("#f-time").value;
    if (!v) return false;
    if (!times.includes(v)) { times.push(v); times.sort(); renderChips(); }
    $("#f-time").value = "";
    return true;
  }
  function openAdd() {
    times = [];
    renderChips();
    $("#f-name").value = "";
    $("#f-dose").value = "";
    $("#f-time").value = "";
    $("#add-error").textContent = "";
    addDlg.showModal();
    $("#f-name").focus();
  }

  $("#f-add-time").addEventListener("click", addTime);
  $("#f-time").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); addTime(); } });
  $("#f-chips").addEventListener("click", e => {
    const b = e.target.closest("[data-rm]");
    if (!b) return;
    times = times.filter(t => t !== b.dataset.rm);
    renderChips();
  });
  document.addEventListener("click", e => { if (e.target.closest("[data-open-add]")) openAdd(); });

  $("#add-form").addEventListener("submit", async e => {
    e.preventDefault();
    const err = $("#add-error");
    err.textContent = "";
    if (!times.length) addTime();  // they typed a time but forgot to press Add time

    const name = $("#f-name").value.trim();
    const dose = $("#f-dose").value.trim();
    if (!name) { err.textContent = "Enter the medicine's name."; $("#f-name").focus(); return; }
    if (!/\d/.test(dose) || dose.startsWith("-")) { err.textContent = "Enter a dose that includes a number, like 500 mg."; $("#f-dose").focus(); return; }
    if (!times.length) { err.textContent = "Add at least one time of day."; $("#f-time").focus(); return; }

    const btn = $("#add-submit");
    btn.disabled = true;
    try {
      const r = await api("/api/medicines", { method: "POST", body: { name: name, dose: dose, times: times } });
      if (!r.ok) { err.textContent = r.message; return; }
      addDlg.close();
      toast(r.message, "ok");
      window.dispatchEvent(new Event("meds-changed"));
    } catch (ex) {
      err.textContent = "Couldn't reach the app. Try again in a moment.";
    } finally {
      btn.disabled = false;
    }
  });

  window.MT = { $: $, $$: $$, esc: esc, api: api, poll: poll, toast: toast, confirmBox: confirmBox,
                openAdd: openAdd, fmtStamp: fmtStamp, NAMES: NAMES, WORDS: WORDS };
})();
