/* ImagineAI — frontend del producto. Sin dependencias. */
"use strict";

const $ = (sel) => document.querySelector(sel);

/* Normaliza el cuerpo de error de FastAPI a un texto legible.
   `detail` puede ser un string (HTTPException nuestra) o un array de errores de
   validación de Pydantic (422). Pasar ese array a Error() daba "[object Object]". */
function fastapiError(body, status) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && d.msg ? d.msg : null))
      .filter(Boolean);
    if (msgs.length) return msgs.join("; ");
  }
  if (detail && typeof detail === "object" && detail.msg) return detail.msg;
  return `Error ${status || ""}`.trim();
}

const els = {
  health: $("#health"),
  healthText: $("#health-text"),
  prompt: $("#prompt"),
  stack: $("#stack"),
  model: $("#model"),
  device: $("#device"),
  generate: $("#generate"),
  historyList: $("#history-list"),
  stages: $("#stages"),
  streamEmpty: $("#stream-empty"),
  streamWrap: $("#stream-wrap"),
  briefBox: $("#brief-box"),
  briefOut: $("#brief-out"),
  codeOut: $("#code-out"),
  toasts: $("#toasts"),
  previewFrame: $("#preview-frame"),
  openPreview: $("#open-preview"),
  download: $("#download"),
  // Auth
  loginOpen: $("#login-open"),
  userChip: $("#user-chip"),
  userAvatar: $("#user-avatar"),
  userName: $("#user-name"),
  logout: $("#logout"),
  authModal: $("#auth-modal"),
  authForm: $("#auth-form"),
  authUser: $("#auth-user"),
  authPass: $("#auth-pass"),
  authError: $("#auth-error"),
  authSubmit: $("#auth-submit"),
  authCancel: $("#auth-cancel"),
  // Editor de preview
  editMode: $("#edit-mode"),
  editHint: $("#edit-hint"),
  editBar: $("#edit-bar"),
  editTargetTag: $("#edit-target-tag"),
  editTargetText: $("#edit-target-text"),
  editInput: $("#edit-input"),
  editApply: $("#edit-apply"),
  editCancel: $("#edit-cancel"),
};

let currentSource = null; // EventSource activo
let currentJobId = null;
let currentUser = null;
let authMode = "login";

/* ── Salud + modelos ─────────────────────────── */
async function refreshHealth() {
  try {
    const h = await (await fetch("/api/health")).json();
    els.health.classList.toggle("ok", h.ollama);
    els.health.classList.toggle("bad", !h.ollama);
    els.healthText.textContent = h.ollama ? "Modelo local conectado" : "Ollama no responde";
  } catch {
    els.health.classList.add("bad");
    els.healthText.textContent = "Backend sin conexión";
  }
}

async function loadModels() {
  try {
    const data = await (await fetch("/api/models")).json();
    els.model.innerHTML = "";
    for (const m of data.models) {
      const opt = document.createElement("option");
      opt.value = m.name;
      opt.textContent = m.parameter_size ? `${m.name} (${m.parameter_size})` : m.name;
      if (m.name === data.default) opt.selected = true;
      els.model.appendChild(opt);
    }
  } catch {
    els.model.innerHTML = "<option value=''>— Ollama no disponible —</option>";
  }
}

async function loadStacks() {
  try {
    const data = await (await fetch("/api/stacks")).json();
    els.stack.innerHTML = "";
    for (const s of data.stacks) {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = s.label;
      if (s.id === data.default) opt.selected = true;
      els.stack.appendChild(opt);
    }
  } catch {
    els.stack.innerHTML = "<option value='react-tailwind'>React + Tailwind</option>";
  }
}

async function loadDevices() {
  try {
    const data = await (await fetch("/api/devices")).json();
    els.device.innerHTML = "";
    for (const d of data.devices) {
      const opt = document.createElement("option");
      opt.value = d.id;
      opt.textContent = d.label;
      if (d.id === data.default) opt.selected = true;
      els.device.appendChild(opt);
    }
  } catch {
    els.device.innerHTML = "<option value='gpu'>GPU</option><option value='cpu'>CPU</option>";
  }
}

/* ── Autenticación ───────────────────────────── */
function renderAuth() {
  const logged = !!currentUser;
  els.loginOpen.hidden = logged;
  els.userChip.hidden = !logged;
  if (logged) {
    els.userName.textContent = currentUser.username;
    els.userAvatar.textContent = currentUser.username[0].toUpperCase();
  }
  els.generate.disabled = false;
  els.generate.textContent = logged ? "✦ Generar prototipo" : "Inicia sesión para generar";
  if (!logged) {
    els.historyList.innerHTML =
      "<li class='history-empty'>Inicia sesión para ver tu historial.</li>";
  }
}

async function refreshUser() {
  try {
    const data = await (await fetch("/api/auth/me")).json();
    currentUser = data.user;
  } catch {
    currentUser = null;
  }
  renderAuth();
  if (currentUser) loadHistory();
}

function openAuthModal(mode = "login") {
  setAuthMode(mode);
  els.authError.hidden = true;
  els.authModal.hidden = false;
  els.authUser.focus();
}
function closeAuthModal() {
  els.authModal.hidden = true;
  els.authForm.reset();
  els.authError.hidden = true;
}
function setAuthMode(mode) {
  authMode = mode;
  document.querySelectorAll(".auth-tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.mode === mode));
  els.authSubmit.textContent = mode === "login" ? "Entrar" : "Crear cuenta";
  els.authPass.autocomplete = mode === "login" ? "current-password" : "new-password";
}

document.querySelectorAll(".auth-tab").forEach((tab) => {
  tab.addEventListener("click", () => setAuthMode(tab.dataset.mode));
});
els.loginOpen.addEventListener("click", () => openAuthModal("login"));
els.authCancel.addEventListener("click", closeAuthModal);
els.authModal.addEventListener("click", (e) => {
  if (e.target === els.authModal) closeAuthModal();
});
els.logout.addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
  currentUser = null;
  renderAuth();
});

els.authForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  els.authError.hidden = true;
  els.authSubmit.disabled = true;
  try {
    const resp = await fetch(`/api/auth/${authMode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: els.authUser.value.trim(),
        password: els.authPass.value,
      }),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(fastapiError(body, resp.status));
    currentUser = body;
    closeAuthModal();
    renderAuth();
    loadHistory();
  } catch (err) {
    els.authError.textContent = err.message;
    els.authError.hidden = false;
  } finally {
    els.authSubmit.disabled = false;
  }
});

/* ── Historial ───────────────────────────────── */
async function loadHistory() {
  if (!currentUser) return;
  let data;
  try {
    data = await (await fetch("/api/jobs")).json();
  } catch {
    return;
  }
  els.historyList.innerHTML = "";
  if (!data.jobs || !data.jobs.length) {
    els.historyList.innerHTML =
      "<li class='history-empty'>Aún no tienes generaciones. ¡Crea la primera!</li>";
    return;
  }
  for (const job of data.jobs) {
    const li = document.createElement("li");
    li.dataset.id = job.id;
    if (job.id === currentJobId) li.classList.add("active");
    const statusTxt = { done: "✓ listo", error: "✗ error" }[job.status] || "… generando";
    const stackTag = job.stack === "vanilla" ? "vanilla" : "react";
    const deviceTag = job.device === "cpu" ? " · cpu" : "";
    const kindTag = job.kind === "edit" ? " · ✎ edición" : "";
    li.innerHTML = `<span class="h-prompt"></span>
      <span class="h-meta ${job.status === "error" ? "error" : ""}">${statusTxt} · ${stackTag}${deviceTag}${kindTag}</span>`;
    li.querySelector(".h-prompt").textContent = job.prompt;
    li.addEventListener("click", () => openJob(job));
    els.historyList.appendChild(li);
  }
}

/* Abre un trabajo del historial: si sigue vivo se engancha al SSE; si ya terminó
   (p. ej. tras un reinicio del servidor, sin eventos en memoria) muestra la
   preview directamente. */
function openJob(job) {
  if (job.status === "done" && job.preview_url) {
    if (currentSource) { currentSource.close(); currentSource = null; }
    currentJobId = job.id;
    resetWorkArea();
    setStage("done");
    showDone({
      files: job.files || [],
      preview_url: job.preview_url,
      download_url: job.download_url,
    });
    markActiveHistory(job.id);
    return;
  }
  if (job.status === "error") {
    if (currentSource) { currentSource.close(); currentSource = null; }
    currentJobId = job.id;
    resetWorkArea();
    showStatus(`Error: ${job.error || "generación fallida"}`, true);
    markActiveHistory(job.id);
    return;
  }
  attachToJob(job.id);
}

function markActiveHistory(jobId) {
  document.querySelectorAll("#history-list li").forEach((li) =>
    li.classList.toggle("active", li.dataset.id === jobId));
}

/* ── Etapas ──────────────────────────────────── */
const STAGE_ORDER = ["briefing", "coding", "packaging", "done"];
function setStage(status) {
  if (status === "editing") status = "coding"; // las ediciones reutilizan la etapa visual de código
  const idx = STAGE_ORDER.indexOf(status);
  els.stages.querySelectorAll(".stage").forEach((el) => {
    const i = STAGE_ORDER.indexOf(el.dataset.stage);
    el.classList.toggle("completed", idx > i || status === "done");
    el.classList.toggle("active", idx === i && status !== "done");
  });
}
function resetStages() {
  els.stages.querySelectorAll(".stage").forEach((el) =>
    el.classList.remove("active", "completed"));
}

/* ── Tabs ────────────────────────────────────── */
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});
function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".pane").forEach((p) =>
    p.classList.toggle("active", p.id === `pane-${name}`));
}

/* ── Generación ──────────────────────────────── */
els.generate.addEventListener("click", async () => {
  if (!currentUser) {
    openAuthModal("login");
    return;
  }
  const prompt = els.prompt.value.trim();
  if (prompt.length < 8) {
    showStatus("Escribe un prompt un poco más largo (mínimo 8 caracteres).", true);
    els.prompt.focus();
    return;
  }
  if (prompt.length > 6000) {
    showStatus(`El prompt es demasiado largo (${prompt.length}/6000 caracteres).`, true);
    els.prompt.focus();
    return;
  }
  els.generate.disabled = true;
  els.generate.textContent = "Generando…";
  try {
    const resp = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt,
        model: els.model.value || null,
        stack: els.stack.value || null,
        device: els.device.value || null,
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(fastapiError(err, resp.status));
    }
    const { job_id } = await resp.json();
    attachToJob(job_id);
    loadHistory();
  } catch (e) {
    showStatus(`Error: ${e.message}`, true);
    els.generate.disabled = false;
    els.generate.textContent = "✦ Generar prototipo";
  }
});

function resetWorkArea() {
  els.streamEmpty.hidden = true;
  els.streamWrap.hidden = false;
  els.briefOut.textContent = "";
  els.codeOut.textContent = "";
  els.briefBox.open = true;
  clearToasts();
  els.openPreview.hidden = true;
  els.download.hidden = true;
  els.previewFrame.classList.remove("visible");
  els.previewFrame.removeAttribute("src");
  disableEditMode();
  els.editMode.hidden = true;
  resetStages();
  switchTab("stream");
}

function attachToJob(jobId) {
  if (currentSource) currentSource.close();
  currentJobId = jobId;
  resetWorkArea();
  markActiveHistory(jobId);

  const source = new EventSource(`/api/jobs/${jobId}/events`);
  currentSource = source;

  source.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    handleEvent(ev);
  };
  source.onerror = () => {
    // El servidor cierra el stream al terminar; solo marcamos error si el
    // trabajo seguía en curso.
    source.close();
    fetch(`/api/jobs/${jobId}`).then((r) => r.json()).then((job) => {
      if (job.status !== "done" && job.status !== "error") {
        showToast("Conexión perdida con el servidor. Reintentando…", "warning");
        setTimeout(() => attachToJob(jobId), 2000);
      }
    }).catch(() => {});
  };
}

function showDone(ev) {
  showToast(`Prototipo listo · archivos: ${(ev.files || []).join(", ")}`, "success");
  els.previewFrame.src = ev.preview_url;
  els.previewFrame.classList.add("visible");
  els.openPreview.href = ev.preview_url;
  els.openPreview.hidden = false;
  els.download.href = ev.download_url;
  els.download.hidden = false;
  els.editMode.hidden = false;
  switchTab("preview");
}

function handleEvent(ev) {
  switch (ev.type) {
    case "status":
      setStage(ev.status);
      if (ev.detail) showStatus(ev.detail, false);
      if (ev.status === "coding") els.briefBox.open = false;
      break;

    case "token": {
      const target = ev.stage === "brief" ? els.briefOut : els.codeOut;
      target.textContent += ev.text;
      target.parentElement === els.briefBox
        ? (els.briefBox.scrollTop = els.briefBox.scrollHeight)
        : (target.scrollTop = target.scrollHeight);
      break;
    }

    case "done": {
      setStage("done");
      showDone(ev);
      finishGeneration();
      break;
    }

    case "error":
      setStage("error");
      showStatus(`Error: ${ev.message}`, true);
      finishGeneration();
      break;
  }
}

function finishGeneration() {
  if (currentSource) { currentSource.close(); currentSource = null; }
  els.generate.disabled = false;
  els.generate.textContent = "✦ Generar prototipo";
  loadHistory();
}

/* ── Toasts ───────────────────────────────────
   Reemplazan la statusline única: se apilan, permiten ver varios estados/errores
   a la vez, se autodescartan (con pausa al pasar el mouse) o se cierran a mano, y
   de-duplican mensajes idénticos para no spamear en reintentos. */
const TOAST_TTL = { info: 4000, success: 4500, warning: 6000, error: 7000 };
const TOAST_MAX = 5;
const TOAST_ICONS = {
  success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
  error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M15 9l-6 6M9 9l6 6"/></svg>',
  warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17.5v.01"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.01"/></svg>',
};

function showToast(message, type = "info") {
  if (!message) return;
  const container = els.toasts;
  // De-dup: si ya hay un toast igual visible, solo reinicia su temporizador.
  const dup = [...container.children].find(
    (t) => t.dataset.msg === message && t.dataset.type === type);
  if (dup) { scheduleToastDismiss(dup); return; }

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.setAttribute("role", type === "error" ? "alert" : "status");
  toast.dataset.msg = message;
  toast.dataset.type = type;

  const icon = document.createElement("span");
  icon.className = "toast-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.innerHTML = TOAST_ICONS[type] || TOAST_ICONS.info;

  const text = document.createElement("span");
  text.className = "toast-msg";
  text.textContent = message; // textContent: a prueba de HTML en el mensaje

  const close = document.createElement("button");
  close.className = "toast-close";
  close.setAttribute("aria-label", "Cerrar notificación");
  close.textContent = "✕";
  close.addEventListener("click", () => dismissToast(toast));

  toast.append(icon, text, close);
  toast.addEventListener("mouseenter", () => clearTimeout(toast._timer));
  toast.addEventListener("mouseleave", () => scheduleToastDismiss(toast));
  container.appendChild(toast);

  // Limita el apilado quitando los más antiguos.
  while (container.children.length > TOAST_MAX) dismissToast(container.firstElementChild, true);

  requestAnimationFrame(() => toast.classList.add("show"));
  scheduleToastDismiss(toast);
}

function scheduleToastDismiss(toast) {
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => dismissToast(toast), TOAST_TTL[toast.dataset.type] || TOAST_TTL.info);
}

function dismissToast(toast, immediate) {
  if (!toast) return;
  clearTimeout(toast._timer);
  if (immediate) { toast.remove(); return; }
  toast.classList.remove("show");
  toast.classList.add("hide");
  toast.addEventListener("transitionend", () => toast.remove(), { once: true });
  setTimeout(() => toast.remove(), 300); // respaldo si no dispara transitionend
}

function clearToasts() {
  [...els.toasts.children].forEach((t) => dismissToast(t, true));
}

// Alias retrocompatible: el resto del código sigue llamando showStatus.
function showStatus(text, isError) {
  showToast(text, isError ? "error" : "info");
}

/* ── Editor de preview (click sobre elementos) ──────────────────
   La preview se sirve desde el mismo origen, así que podemos entrar al DOM del
   iframe: en modo edición, mover el mouse destaca el elemento bajo el cursor y
   el click lo selecciona y abre la barra de instrucciones. El cambio se envía a
   /api/jobs/{id}/edit, que produce una versión nueva del proyecto. */
let editModeOn = false;
let selectedEl = null;
let hoverEl = null;

const EDIT_STYLE_ID = "six-edit-style";
const EDIT_CSS = `
  .six-edit-hover { outline: 2px dashed #7c6cff !important; outline-offset: 2px; cursor: crosshair !important; }
  .six-edit-selected { outline: 3px solid #ff5c8a !important; outline-offset: 2px; }
`;

function iframeDoc() {
  try {
    return els.previewFrame.contentDocument;
  } catch {
    return null; // cross-origin: no debería pasar (mismo origen)
  }
}

function enableEditMode() {
  const doc = iframeDoc();
  if (!doc || !doc.body) {
    showToast("La preview aún no está lista para editar.", "warning");
    return;
  }
  editModeOn = true;
  els.editMode.classList.add("active");
  els.editMode.setAttribute("aria-pressed", "true");
  els.editHint.hidden = false;
  if (!doc.getElementById(EDIT_STYLE_ID)) {
    const style = doc.createElement("style");
    style.id = EDIT_STYLE_ID;
    style.textContent = EDIT_CSS;
    doc.head.appendChild(style);
  }
  doc.addEventListener("mousemove", onIframeHover, true);
  doc.addEventListener("click", onIframeClick, true);
}

function disableEditMode() {
  editModeOn = false;
  els.editMode.classList.remove("active");
  els.editMode.setAttribute("aria-pressed", "false");
  els.editHint.hidden = true;
  els.editBar.hidden = true;
  const doc = iframeDoc();
  if (doc) {
    doc.removeEventListener("mousemove", onIframeHover, true);
    doc.removeEventListener("click", onIframeClick, true);
    doc.querySelectorAll(".six-edit-hover, .six-edit-selected").forEach((el) =>
      el.classList.remove("six-edit-hover", "six-edit-selected"));
  }
  hoverEl = null;
  selectedEl = null;
}

function onIframeHover(e) {
  if (!editModeOn) return;
  const el = e.target;
  if (el === hoverEl || !el || el.tagName === "HTML" || el.tagName === "BODY") return;
  if (hoverEl) hoverEl.classList.remove("six-edit-hover");
  hoverEl = el;
  if (!el.classList.contains("six-edit-selected")) el.classList.add("six-edit-hover");
}

function onIframeClick(e) {
  if (!editModeOn) return;
  e.preventDefault();
  e.stopPropagation();
  const el = e.target;
  if (!el || el.tagName === "HTML" || el.tagName === "BODY") return;
  const doc = iframeDoc();
  if (doc) doc.querySelectorAll(".six-edit-selected").forEach((n) =>
    n.classList.remove("six-edit-selected"));
  el.classList.remove("six-edit-hover");
  el.classList.add("six-edit-selected");
  selectedEl = el;
  const selector = cssPath(el);
  els.editTargetTag.textContent = `<${el.tagName.toLowerCase()}>`;
  els.editTargetText.textContent =
    (el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 80) || selector;
  els.editBar.hidden = false;
  els.editInput.value = "";
  els.editInput.focus();
}

/* Selector CSS legible y razonablemente único para el elemento señalado. */
function cssPath(el) {
  const parts = [];
  let node = el;
  while (node && node.nodeType === 1 && node.tagName !== "HTML" && parts.length < 5) {
    let part = node.tagName.toLowerCase();
    if (node.id) {
      parts.unshift(`#${node.id}`);
      break;
    }
    const cls = [...node.classList]
      .filter((c) => !c.startsWith("six-edit-"))
      .slice(0, 3);
    if (cls.length) part += "." + cls.join(".");
    const parent = node.parentElement;
    if (parent) {
      const siblings = [...parent.children].filter((c) => c.tagName === node.tagName);
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
    }
    parts.unshift(part);
    node = node.parentElement;
  }
  return parts.join(" > ");
}

function cleanOuterHTML(el) {
  const clone = el.cloneNode(true);
  clone.classList.remove("six-edit-hover", "six-edit-selected");
  clone.querySelectorAll(".six-edit-hover, .six-edit-selected").forEach((n) =>
    n.classList.remove("six-edit-hover", "six-edit-selected"));
  return clone.outerHTML;
}

els.editMode.addEventListener("click", () => {
  editModeOn ? disableEditMode() : enableEditMode();
});
els.editCancel.addEventListener("click", () => {
  els.editBar.hidden = true;
  if (selectedEl) selectedEl.classList.remove("six-edit-selected");
  selectedEl = null;
});
els.editInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") els.editApply.click();
  if (e.key === "Escape") els.editCancel.click();
});

els.editApply.addEventListener("click", async () => {
  const instruction = els.editInput.value.trim();
  if (!selectedEl || instruction.length < 3) {
    els.editInput.focus();
    return;
  }
  els.editApply.disabled = true;
  els.editApply.textContent = "Aplicando…";
  try {
    const resp = await fetch(`/api/jobs/${currentJobId}/edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        instruction,
        selector: cssPath(selectedEl),
        element_html: cleanOuterHTML(selectedEl).slice(0, 6000),
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(fastapiError(err, resp.status));
    }
    const { job_id } = await resp.json();
    disableEditMode();
    attachToJob(job_id); // sigue la edición en vivo; al terminar carga la preview nueva
    loadHistory();
  } catch (e) {
    showStatus(`Error: ${e.message}`, true);
  } finally {
    els.editApply.disabled = false;
    els.editApply.textContent = "Aplicar";
  }
});

/* ── Init ────────────────────────────────────── */
refreshHealth();
setInterval(refreshHealth, 15000);
loadModels();
loadStacks();
loadDevices();
refreshUser();
