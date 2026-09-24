const app = document.querySelector("#app");
const headerActions = document.querySelector("#headerActions");
const authModal = document.querySelector("#authModal");
const authMessage = document.querySelector("#authMessage");
const loginForm = document.querySelector("#loginForm");
const registerForm = document.querySelector("#registerForm");

const COMPANY_TYPES = [
  { id: "client", name: "Заказчик" },
  { id: "designer", name: "Проектировщик" },
  { id: "manufacturer", name: "Производитель" },
  { id: "serial", name: "Серийное производство" },
  { id: "supplier", name: "Поставщик" },
];

const state = {
  user: null,
  view: "home",
  dashboardTab: "overview",
  orders: [],
  makers: [],
  threads: [],
  messages: [],
  activeThreadId: null,
  regions: [],
  companies: [],
  companyFilters: { type: "", region: "", search: "" },
  services: [],
  activeCompanyId: null,
  favorites: [],
  adminTab: "overview",
  adminReports: [],
  adminReportsTotal: 0,
  compareResponses: [],
  makerFunnel: null,
  adminStats: null,
  adminAnalytics: null,
  adminActivity: [],
  adminUsers: [],
  adminOrders: [],
  adminServices: [],
  adminUserFilters: { role: "", search: "" },
  adminOrderFilters: { status: "", search: "" },
  notifications: [],
  unreadCount: 0,
  searchResults: null,
  searchQuery: "",
  documents: [],
  reviews: [],
  avgRating: 0,
  reviewsCount: 0,
  notifPrefs: null,
  notifFilter: "",
  notifView: "list",
  materials: [],
  templates: [],
  invoices: [],
  orderHistory: [],
  deliveryStatuses: [],
  tfaEnabled: false,
  selectedOrderIds: [],
  selectedUserIds: [],
  invoiceForm: null,
  deliveryForm: null,
  suppliers: [],
  certificates: [],
  timeEntries: [],
  totalHours: 0,
  clientRatings: [],
  clientAvgRating: 0,
  verifyUrl: null,
  gallery: [],
  aiOpen: false,
  aiMessages: [],
  aiLoading: false,
};

let deadlineTimers = [];
let ws = null;
let wsReconnectTimer = null;
let wsHeartbeatTimer = null;

function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const baseHeaders = options.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  if (method !== "GET" && csrfToken) baseHeaders["X-CSRF-Token"] = csrfToken;
  return fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: { ...baseHeaders, ...(options.headers || {}) },
  }).then((response) => response.json().catch(() => ({})).then((data) => {
    if (!response.ok) throw new Error(data.error || "Ошибка запроса");
    return data;
  }));
}

async function ensureCsrfToken() {
  try {
    const response = await fetch("/api/csrf-token", { method: "POST", credentials: "same-origin" });
    const data = await response.json().catch(() => ({}));
    csrfToken = data.csrf_token || null;
  } catch {
    csrfToken = null;
  }
  return csrfToken;
}

function money(value) {
  return `${new Intl.NumberFormat("ru-RU").format(Number(value) || 0)} руб.`;
}

function debounce(fn, ms = 300) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

function showToast(message, type = "error") {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    container.style.cssText = "position:fixed;top:80px;right:20px;z-index:200;display:flex;flex-direction:column;gap:8px;";
    document.body.appendChild(container);
  }
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("toast-show"));
  setTimeout(() => { toast.classList.remove("toast-show"); setTimeout(() => toast.remove(), 300); }, 3000);
}

function escapeHtml(value = "") {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function roleLabel(role) {
  return role === "maker" ? "Производитель" : "Заказчик";
}

function companyTypeLabel(type) {
  return COMPANY_TYPES.find((t) => t.id === type)?.name || type;
}

function statusLabel(status) {
  return { open: "Открыт", progress: "В работе", closed: "Завершен", cancelled: "Отменён" }[status] || status;
}

function statusClass(status) { return `status status-${status}`; }

function skeletonCards(count = 3) {
  return Array(count).fill('').map(() => `
    <article class="order-card">
      <div class="order-card-header">
        <div style="flex:1">
          <div class="skeleton skeleton-text" style="width:80px;height:20px;margin-bottom:8px"></div>
          <div class="skeleton skeleton-title"></div>
          <div class="skeleton skeleton-text"></div>
          <div class="skeleton skeleton-text" style="width:80%"></div>
        </div>
        <div class="skeleton" style="width:100px;height:24px"></div>
      </div>
      <div class="skeleton skeleton-text" style="width:60%"></div>
    </article>
  `).join('');
}

function emptyState(message, actionLabel = '', actionData = '') {
  return `<div class="empty">
    <p>${message}</p>
    ${actionLabel ? `<button class="button button-primary button-small" type="button" ${actionData} style="margin-top:12px">${actionLabel}</button>` : ''}
  </div>`;
}

function deadlineCountdown(deadlineStr) {
  const days = parseInt(deadlineStr) || 0;
  if (!days) return "";
  const hours = days * 24;
  const totalSeconds = hours * 3600;
  const now = Math.floor(Date.now() / 1000);
  const target = now + totalSeconds;
  const diff = target - now;
  if (diff <= 0) return '<span class="timer-expired">Срок истёк</span>';
  const d = Math.floor(diff / 86400);
  const h = Math.floor((diff % 86400) / 3600);
  const m = Math.floor((diff % 3600) / 60);
  const cls = d <= 3 ? "timer-urgent" : d <= 7 ? "timer-warning" : "timer-ok";
  return `<span class="deadline-timer ${cls}" data-deadline-seconds="${totalSeconds}">${d}д ${h}ч ${m}м</span>`;
}

function startDeadlineTimers() {
  stopDeadlineTimers();
  deadlineTimers.push(setInterval(() => {
    document.querySelectorAll(".deadline-timer[data-deadline-seconds]").forEach((el) => {
      let secs = parseInt(el.dataset.deadlineSeconds);
      if (secs <= 0) { el.className = "deadline-timer timer-expired"; el.textContent = "Срок истёк"; return; }
      secs--;
      el.dataset.deadlineSeconds = secs;
      const d = Math.floor(secs / 86400);
      const h = Math.floor((secs % 86400) / 3600);
      const m = Math.floor((secs % 3600) / 60);
      el.textContent = `${d}д ${h}ч ${m}м`;
      if (d <= 3) el.className = "deadline-timer timer-urgent";
      else if (d <= 7) el.className = "deadline-timer timer-warning";
    });
  }, 60000));
}

function stopDeadlineTimers() {
  deadlineTimers.forEach(clearInterval);
  deadlineTimers = [];
}

function openAuth(tab = "login") {
  authModal.classList.add("is-open");
  authModal.setAttribute("aria-hidden", "false");
  setAuthTab(tab);
}

function closeAuth() {
  authModal.classList.remove("is-open");
  authModal.setAttribute("aria-hidden", "true");
  authMessage.textContent = "";
}

function setAuthTab(tab) {
  document.querySelectorAll("[data-auth-tab]").forEach((b) => b.classList.toggle("is-active", b.dataset.authTab === tab));
  loginForm.classList.toggle("hidden", tab !== "login");
  registerForm.classList.toggle("hidden", tab !== "register");
  if (tab === "register") populateRegisterRegions();
}

async function populateRegisterRegions() {
  const select = document.getElementById("registerRegion");
  if (!select || select.options.length > 1) return;
  try {
    const data = await api("/api/regions");
    data.regions.forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r.id;
      opt.textContent = r.name;
      select.appendChild(opt);
    });
  } catch {}
}

function setView(view) {
  if (view === "dashboard" && !state.user) return openAuth("login");
  if (view === "admin" && (!state.user || state.user.role !== "admin")) return openAuth("login");
  navigate(VIEW_PATHS[view] || "/");
}

const VIEW_PATHS = {
  home: "/", market: "/market", companies: "/companies", services: "/services",
  articles: "/articles", notifications: "/notifications",
  dashboard: "/dashboard", admin: "/admin", company: "/companies", service: "/services",
  privacy: "/privacy", offer: "/offer", tariffs: "/tariffs",
};

function parseRoute(path) {
  const routes = [
    [/^\/$/, () => ({ view: "home" })],
    [/^\/market\/?$/, () => ({ view: "market" })],
    [/^\/companies\/?$/, () => ({ view: "companies" })],
    [/^\/companies\/(\d+)\/?$/, (m) => ({ view: "company", companyId: Number(m[1]) })],
    [/^\/services\/?$/, () => ({ view: "services" })],
    [/^\/services\/(\d+)\/?$/, (m) => ({ view: "service", serviceId: Number(m[1]) })],
    [/^\/articles\/?$/, () => ({ view: "articles" })],
    [/^\/articles\/([\w-]+)\/?$/, (m) => ({ view: "article", articleSlug: m[1] })],
    [/^\/privacy\/?$/, () => ({ view: "privacy" })],
    [/^\/offer\/?$/, () => ({ view: "offer" })],
    [/^\/tariffs\/?$/, () => ({ view: "tariffs" })],
    [/^\/chat\/?$/, () => ({ view: "dashboard", tab: "chats" })],
    [/^\/dashboard(?:\/([a-z-]+))?\/?$/i, (m) => ({ view: "dashboard", tab: m[1] || "overview" })],
    [/^\/notifications\/?$/, () => ({ view: "notifications" })],
    [/^\/admin(?:\/([a-z-]+))?\/?$/i, (m) => ({ view: "admin", tab: m[1] || "overview" })],
    [/^\/orders\/(\d+)\/?$/, () => ({ view: "dashboard", tab: "my-orders" })],
    [/^\/orders\/(\d+)\/contract\/?$/, () => ({ view: "dashboard", tab: "my-orders" })],
  ];
  for (const [re, fn] of routes) {
    const m = path.match(re);
    if (m) return fn(m);
  }
  return { view: "home" };
}

function applyRoute(path) {
  const route = parseRoute(path || "/");
  if (route.view === "company") {
    state.activeCompanyId = route.companyId;
    state.view = "company";
  } else if (route.view === "service") {
    state.activeServiceId = route.serviceId;
    state.view = "service";
  } else if (route.view === "article") {
    state.articleSlug = route.articleSlug;
    state.view = "article";
  } else if (route.tab) {
    state.view = route.view;
    if (route.tab) state.dashboardTab = route.tab;
    if (route.view === "admin") state.adminTab = route.tab;
  } else {
    state.view = route.view;
  }
}

function navigate(path) {
  closeDrawer();
  history.pushState({}, "", path);
  applyRoute(path);
  render();
}

window.addEventListener("popstate", () => {
  applyRoute(location.pathname);
  render();
});

document.querySelector("#burgerBtn")?.addEventListener("click", () => {
  const open = document.body.classList.toggle("drawer-open");
  document.querySelector("#burgerBtn").setAttribute("aria-expanded", String(open));
  document.querySelector("#mobileDrawer")?.setAttribute("aria-hidden", String(!open));
});
document.querySelector("#drawerOverlay")?.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
document.querySelectorAll(".drawer-nav button").forEach((b) => b.addEventListener("click", closeDrawer));

let csrfToken = null;

async function loadSession() {
  const data = await api("/api/session");
  state.user = data.user;
  if (state.user) {
    await ensureCsrfToken();
    connectWebSocket();
    loadNotifications();
    loadNotifPrefs();
    requestPushPermission();
    showVerifyBanner();
  }
}

async function loadNotifications() {
  if (!state.user) return;
  try {
    const data = await api("/api/notifications");
    state.notifications = data.notifications;
    state.unreadCount = data.unread;
    renderHeader();
  } catch {}
}

async function loadDocuments() {
  if (!state.user) return;
  const data = await api("/api/documents");
  state.documents = data.documents;
}

async function loadReviews(companyId) {
  const data = await api(`/api/reviews?company_id=${companyId}`);
  state.reviews = data.reviews;
  state.avgRating = data.avg_rating;
  state.reviewsCount = data.reviews_count;
}

async function globalSearch(query) {
  if (query.length < 2) { state.searchResults = null; return; }
  const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
  state.searchResults = data;
}

async function loadNotifPrefs() {
  if (!state.user) return;
  try {
    const data = await api("/api/notifications/preferences");
    state.notifPrefs = data.preferences;
  } catch {}
}

async function loadMaterials(category = "") {
  const qs = category ? `?category=${category}` : "";
  const data = await api(`/api/materials${qs}`);
  state.materials = data.materials;
}

async function loadTemplates() {
  if (!state.user) return;
  const data = await api("/api/templates");
  state.templates = data.templates;
}

async function loadInvoices() {
  if (!state.user) return;
  const data = await api("/api/invoices");
  state.invoices = data.invoices;
}

async function loadOrderHistory(orderId) {
  const data = await api(`/api/order-history?order_id=${orderId}`);
  state.orderHistory = data.history;
}

async function loadDelivery(orderId) {
  const data = await api(`/api/delivery?order_id=${orderId}`);
  state.deliveryStatuses = data.deliveries;
}

async function loadTfaStatus() {
  if (!state.user) return;
  const data = await api("/api/tfa/status");
  state.tfaEnabled = data.enabled;
}

async function loadSuppliers(search = "") {
  const qs = search ? `?search=${encodeURIComponent(search)}` : "";
  const data = await api(`/api/suppliers${qs}`);
  state.suppliers = data.suppliers;
}

async function loadCertificates(userId = "") {
  const qs = userId ? `?user_id=${userId}` : "";
  const data = await api(`/api/certificates${qs}`);
  state.certificates = data.certificates;
}

async function loadTimeEntries(orderId = "") {
  const qs = orderId ? `?order_id=${orderId}` : "";
  const data = await api(`/api/time-entries${qs}`);
  state.timeEntries = data.entries;
  state.totalHours = data.total_hours;
}

async function loadClientRatings(clientId) {
  const data = await api(`/api/client-ratings?client_id=${clientId}`);
  state.clientRatings = data.ratings;
  state.clientAvgRating = data.avg_rating;
}

function requestPushPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }
}

function sendPushNotification(title, body) {
  if ("Notification" in window && Notification.permission === "granted") {
    new Notification(title, { body, icon: "/meblio.png" });
  }
}

async function loadOrders() {
  const params = new URLSearchParams();
  const type = document.querySelector("#typeFilter")?.value;
  const city = document.querySelector("#cityFilter")?.value;
  const status = document.querySelector("#statusFilter")?.value;
  const budgetMin = document.querySelector("#budgetMinFilter")?.value;
  const budgetMax = document.querySelector("#budgetMaxFilter")?.value;
  if (type) params.set("type", type);
  if (city) params.set("city", city);
  if (status) params.set("status", status);
  if (budgetMin) params.set("budget_min", budgetMin);
  if (budgetMax) params.set("budget_max", budgetMax);
  const data = await api(`/api/orders${params.toString() ? `?${params}` : ""}`);
  state.orders = data.orders;
}

async function loadMakers() {
  const data = await api("/api/makers");
  state.makers = data.makers;
}

async function loadRegions() {
  const data = await api("/api/regions");
  state.regions = data.regions;
}

async function loadThreads() {
  if (!state.user) return;
  const data = await api("/api/threads");
  state.threads = data.threads;
  if (!state.activeThreadId && state.threads.length) state.activeThreadId = state.threads[0].id;
}

async function loadMessages(threadId) {
  if (!threadId) { state.messages = []; return; }
  const data = await api(`/api/threads/${threadId}/messages`);
  state.messages = data.messages;
}

function getCookie(name) {
  const match = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
  return match ? match[2] : "";
}

function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }

  const cfg = window.MEBLIO_CONFIG || {};
  // Behind TLS (nginx) the chat goes through same-origin /ws; local dev uses the direct WS port.
  const wsUrl = location.protocol === "https:"
    ? `wss://${location.host}/ws`
    : `ws://${location.hostname}:${cfg.wsPort || 8001}`;
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "auth", token: getCookie("meblio_session") }));
  };

  ws.onmessage = (event) => {
    try { handleWSMessage(JSON.parse(event.data)); } catch {}
  };

  ws.onclose = () => {
    if (wsHeartbeatTimer) { clearInterval(wsHeartbeatTimer); wsHeartbeatTimer = null; }
    wsReconnectTimer = setTimeout(connectWebSocket, 3000);
  };

  ws.onerror = () => {};

  // Heartbeat every 30s
  if (wsHeartbeatTimer) clearInterval(wsHeartbeatTimer);
  wsHeartbeatTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
  }, 30000);
}

function handleWSMessage(data) {
  if (data.type === "auth_ok") {
    if (state.activeThreadId && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "subscribe", thread_id: state.activeThreadId }));
    }
  } else if (data.type === "message" && data.thread_id === state.activeThreadId) {
    state.messages.push(data.message);
    appendMessage(data.message);
  } else if (data.type === "message") {
    sendPushNotification("Новое сообщение", `${data.message?.author_name || 'Пользователь'}: ${data.message?.body || ''}`.substring(0, 100));
    loadNotifications();
  } else if (data.type === "notification") {
    const notif = data.notification;
    state.notifications.unshift(notif);
    state.unreadCount++;
    renderHeader();
    showToast(`${notif.title}: ${notif.body}`, "success");
    sendPushNotification(notif.title, notif.body);
  }
}

function appendMessage(message) {
  const messagesEl = document.querySelector(".messages");
  if (!messagesEl) return;
  const wasAtBottom = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 60;
  const div = document.createElement("div");
  div.className = `message ${message.author_id === state.user.id ? "mine" : ""}`;
  div.innerHTML = `${escapeHtml(message.body)}<small>${escapeHtml(message.author_name)} · ${escapeHtml(message.created_at)}</small>`;
  messagesEl.appendChild(div);
  if (wasAtBottom) messagesEl.scrollTop = messagesEl.scrollHeight;
}

function subscribeThread(threadId) {
  if (ws && ws.readyState === WebSocket.OPEN && threadId) {
    ws.send(JSON.stringify({ type: "subscribe", thread_id: threadId }));
  }
}

function unsubscribeThread(threadId) {
  if (ws && ws.readyState === WebSocket.OPEN && threadId) {
    ws.send(JSON.stringify({ type: "unsubscribe", thread_id: threadId }));
  }
}

function renderHeader() {
  const themeIcon = document.documentElement.getAttribute("data-theme") === "dark" ? "\u2600" : "\u263E";
  const isAdmin = state.user?.role === "admin";
  const notifBadge = state.unreadCount > 0 ? `<span class="notif-badge">${state.unreadCount}</span>` : "";
  headerActions.innerHTML = state.user
    ? `<div class="search-bar-header">
         <input type="text" id="globalSearch" placeholder="Поиск..." value="${escapeHtml(state.searchQuery)}" autocomplete="off">
         ${state.searchResults ? `<div class="search-dropdown" id="searchDropdown">${renderSearchResults()}</div>` : ""}
       </div>
       <button class="theme-toggle" type="button" data-action="toggle-theme" title="Сменить тему">${themeIcon}</button>
       <button class="notif-btn" type="button" data-action="toggle-notifications" title="Уведомления">\uD83D\uDD14${notifBadge}</button>
       <span class="badge">${isAdmin ? "Админ" : roleLabel(state.user.role)}</span>
       ${isAdmin ? `<button class="button button-secondary button-small" type="button" data-view="admin">Админ-панель</button>` : ""}
       <button class="button button-secondary button-small" type="button" data-view="dashboard">${escapeHtml(state.user.name)}</button>
       <button class="button button-secondary button-small" type="button" data-action="logout">Выйти</button>`
    : `<div class="search-bar-header">
         <input type="text" id="globalSearch" placeholder="Поиск..." value="${escapeHtml(state.searchQuery)}" autocomplete="off">
         ${state.searchResults ? `<div class="search-dropdown" id="searchDropdown">${renderSearchResults()}</div>` : ""}
       </div>
       <button class="theme-toggle" type="button" data-action="toggle-theme" title="Сменить тему">${themeIcon}</button>
       <button class="button button-secondary" type="button" data-auth="login">Войти</button>
       <button class="button button-primary" type="button" data-auth="register">Регистрация</button>`;
  document.querySelectorAll(".nav button").forEach((b) => b.classList.toggle("is-active", b.dataset.view === state.view));
  const drawerAuth = document.querySelector("#drawerAuth");
  if (drawerAuth) {
    drawerAuth.innerHTML = state.user
      ? `<button class="button button-primary" type="button" data-view="dashboard">${escapeHtml(state.user.name)}</button>
         <button class="button button-secondary" type="button" data-action="logout">Выйти</button>`
      : `<button class="button button-secondary" type="button" data-auth="login">Войти</button>
         <button class="button button-primary" type="button" data-auth="register">Регистрация</button>`;
    drawerAuth.querySelectorAll("button").forEach((b) => b.addEventListener("click", closeDrawer));
  }
}

function closeDrawer() {
  document.body.classList.remove("drawer-open");
  document.querySelector("#burgerBtn")?.setAttribute("aria-expanded", "false");
  document.querySelector("#mobileDrawer")?.setAttribute("aria-hidden", "true");
}

function renderSearchResults() {
  if (!state.searchResults) return "";
  const { orders, companies, services } = state.searchResults;
  if (!orders.length && !companies.length && !services.length) return '<div class="search-empty">Ничего не найдено</div>';
  let html = "";
  if (orders.length) {
    html += '<div class="search-group"><strong>Заказы</strong>';
    orders.forEach(o => {
      html += `<div class="search-item" data-view="market"><span class="status status-${o.status}">${statusLabel(o.status)}</span> ${escapeHtml(o.title)} <small>${escapeHtml(o.city)} · ${money(o.budget)}</small></div>`;
    });
    html += '</div>';
  }
  if (companies.length) {
    html += '<div class="search-group"><strong>Компании</strong>';
    companies.forEach(c => {
      html += `<div class="search-item" data-company-id="${c.id}">${escapeHtml(c.name)} <small>${escapeHtml(c.city)} · ${companyTypeLabel(c.company_type)}</small></div>`;
    });
    html += '</div>';
  }
  if (services.length) {
    html += '<div class="search-group"><strong>Услуги</strong>';
    services.forEach(s => {
      html += `<div class="search-item">${escapeHtml(s.title)} <small>${escapeHtml(s.price_type || "")}</small></div>`;
    });
    html += '</div>';
  }
  return html;
}

function renderNotificationsPanel() {
  if (!state.notifications.length) return '<div class="notif-empty">Нет уведомлений</div>';
  return state.notifications.map(n => `
    <div class="notif-item ${n.is_read ? '' : 'unread'}" data-notif-id="${n.id}" data-notif-link="${escapeHtml(n.link)}">
      <strong>${escapeHtml(n.title)}</strong>
      <p>${escapeHtml(n.body)}</p>
      <small>${escapeHtml(n.created_at)}</small>
    </div>
  `).join('');
}

function starRating(rating, interactive = false) {
  let html = '<span class="star-rating">';
  for (let i = 1; i <= 5; i++) {
    html += `<span class="star ${i <= rating ? 'filled' : ''}" ${interactive ? `data-star="${i}"` : ''}>★</span>`;
  }
  html += '</span>';
  return html;
}

async function loadOrderStagesInto(orderId, panel) {
  try {
    const data = await api(`/api/orders/${orderId}/stages`);
    const stages = data.stages || [];
    const order = state.orders.find(o => o.id === Number(orderId));
    const isParticipant = order && state.user && (state.user.id === order.client_id || state.user.id === order.selected_maker_id || state.user.role === "admin");
    panel.innerHTML = `
      <div class="stages-box">
        <div class="stages-head">
          <strong>Этапы работы</strong>
          ${isParticipant && order?.status === "progress" ? `<button class="button button-secondary button-small" type="button" data-add-stage="${orderId}">+ Этап</button>` : ""}
        </div>
        ${stages.length ? `
          <ul class="stages-list">
            ${stages.map(s => `
              <li class="stages-item ${s.done ? "is-done" : ""}">
                ${isParticipant && order?.status === "progress" ? `
                  <label class="stages-check">
                    <input type="checkbox" ${s.done ? "checked" : ""} data-stage-toggle="${orderId}:${s.id}:${s.done ? 1 : 0}">
                    <span>${escapeHtml(s.name)}</span>
                  </label>` : `<span>${escapeHtml(s.name)}</span>`}
                <span class="stages-meta">
                  ${s.done ? `✓ ${escapeHtml((s.done_by_name || "") + (s.done_at ? ` · ${s.done_at.slice(0, 10)}` : ""))}` : "в работе"}
                  ${isParticipant && order?.status === "progress" ? `<button class="stages-del" type="button" data-stage-delete="${orderId}:${s.id}" title="Удалить">×</button>` : ""}
                </span>
              </li>
            `).join("")}
          </ul>
          <div class="stages-progress"><div class="stages-progress-fill" style="width:${Math.round((stages.filter(s => s.done).length / stages.length) * 100)}%"></div></div>
          <p class="muted">${stages.filter(s => s.done).length} из ${stages.length} этапов завершено</p>
        ` : '<p class="muted">Этапы появятся, когда заказ переведут в работу.</p>'}
      </div>`;
  } catch (error) {
    panel.innerHTML = `<div class="stages-box"><p class="muted">${escapeHtml(error.message)}</p></div>`;
  }
}

function openContractPrint(c) {
  const stageRows = (c.stages || []).map(s =>
    `<tr><td>${escapeHtml(s.name)}</td><td>${s.done ? "выполнен" : "в работе"}</td><td>${escapeHtml(s.done_at || "—")}</td></tr>`
  ).join("") || '<tr><td colspan="3">Этапы не заданы</td></tr>';
  const inv = c.invoice;
  const esc = escapeHtml;
  const html = `<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><title>Договор ${esc(String(c.id))} — Meblio</title>
    <style>
      body{font-family:Georgia,serif;max-width:800px;margin:40px auto;padding:0 24px;color:#111;line-height:1.5}
      h1{font-size:22px;text-align:center}h2{font-size:16px;margin-top:28px}
      table{width:100%;border-collapse:collapse;margin:12px 0}td,th{border:1px solid #999;padding:6px 8px;text-align:left;font-size:13px}
      th{background:#f3f3f3}.sig{display:flex;justify-content:space-between;margin-top:48px;gap:24px}
      .sig div{flex:1;border-top:1px solid #111;padding-top:6px;font-size:12px}
      @media print{body{margin:20px auto}}
    </style></head><body>
    <h1>Договор подряда № ${esc(String(c.id))}<br>на изготовление мебели</h1>
    <p>г. ${esc(c.client_city || c.city || "—")}&emsp;&emsp;&emsp;«${esc((c.created_at || "").slice(0, 10))}»</p>
    <h2>1. Стороны</h2>
    <table>
      <tr><th style="width:30%">Заказчик</th><td>${esc(c.client_name)}${c.client_inn ? `, ИНН ${esc(c.client_inn)}` : ""}${c.client_ogrn ? `, ОГРН/ОГРНИП ${esc(c.client_ogrn)}` : ""}<br>${esc(c.client_email || "")}, ${esc(c.client_phone || "")}</td></tr>
      <tr><th>Исполнитель</th><td>${esc(c.maker_name || "не выбран")}${c.maker_inn ? `, ИНН ${esc(c.maker_inn)}` : ""}${c.maker_ogrn ? `, ОГРН/ОГРНИП ${esc(c.maker_ogrn)}` : ""}<br>${esc(c.maker_email || "")}, ${esc(c.maker_phone || "")}</td></tr>
    </table>
    <h2>2. Предмет договора</h2>
    <p>Изготовление и поставка мебели: <strong>${esc(c.title)}</strong> (${esc(c.type)}), ${esc(String(c.quantity))} шт., г. ${esc(c.city)}.<br>
    Описание: ${esc(c.details || "—")}<br>
    Срок исполнения: ${esc(c.deadline || "—")}. Бюджет: ${Number(c.budget || 0).toLocaleString("ru-RU")} руб.</p>
    ${c.selected_maker_id ? `<p>Статус заказа: <strong>${esc(c.status)}</strong>${c.warranty_until ? `, гарантия до ${esc(c.warranty_until)}` : ""}.</p>` : ""}
    <h2>3. Этапы работ</h2>
    <table><thead><tr><th>Этап</th><th>Статус</th><th>Дата</th></tr></thead><tbody>${stageRows}</tbody></table>
    ${inv ? `<h2>4. Расчёты</h2><p>Счёт № ${esc(String(inv.id))} на ${Number(inv.amount).toLocaleString("ru-RU")} руб., статус: <strong>${esc(inv.status)}</strong>${inv.due_date ? `, срок оплаты ${esc(inv.due_date)}` : ""}.</p>` : "<h2>4. Расчёты</h2><p>Счёт не выставлен.</p>"}
    <h2>5. Ответственность сторон</h2>
    <p>Стороны обязуются соблюдать сроки и условия настоящего договора. Приёмка работ оформляется актом приёмки в личном кабинете Meblio. Гарантийный срок — 14 дней с даты приёмки.</p>
    <div class="sig">
      <div>Заказчик: ${esc(c.client_name)} / подпись</div>
      <div>Исполнитель: ${esc(c.maker_name || "")} / подпись</div>
    </div>
    <p style="margin-top:32px;color:#666;font-size:12px">Документ сформирован на площадке Meblio. Для печати используйте меню печати браузера.</p>
    <script>window.onload = function(){ setTimeout(function(){ window.print(); }, 400); };</script>
    </body></html>`;
  const win = window.open("", "_blank");
  if (!win) { showToast("Разрешите всплывающие окна для печати договора"); return; }
  win.document.write(html);
  win.document.close();
}

function exportOrderHTML(order) {
  const rows = [
    ['Название', order.title],
    ['Тип', order.type],
    ['Количество', order.quantity + ' шт.'],
    ['Город', order.city],
    ['Бюджет', money(order.budget)],
    ['Срок', order.deadline],
    ['Статус', statusLabel(order.status)],
    ['Описание', order.details],
  ];
  if (order.responses?.length) {
    rows.push(['Отклики', order.responses.map(r => `${r.maker_name}: ${money(r.price)}, ${r.days} дн.`).join('\n')]);
  }
  const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${escapeHtml(order.title)}</title>
    <style>body{font-family:Arial,sans-serif;padding:40px}table{width:100%;border-collapse:collapse}td,th{border:1px solid #ddd;padding:8px;text-align:left}th{background:#f5f5f5}</style></head>
    <body><h1>Meblio — Заказ #${order.id}</h1><table>${rows.map(([k,v]) => `<tr><th>${k}</th><td>${escapeHtml(String(v))}</td></tr>`).join('')}</table>
    <p style="margin-top:30px;color:#999">Экспортировано из Meblio · ${new Date().toLocaleDateString('ru-RU')}</p></body></html>`;
  const win = window.open('', '_blank');
  win.document.write(html);
  win.document.close();
  setTimeout(() => win.print(), 500);
}

const HERO_IMG = "data:image/jpeg;base64,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";

function renderHome() {
  app.innerHTML = `
    <section class="hero">
      <div class="container hero-grid">
        <div>
          <p class="eyebrow">Платформа для заказа мебели</p>
          <h1>Мебель на заказ от проверенных производств</h1>
          <p class="lead">Разместите задачу, приложите чертежи или примеры, получите предложения от мебельных цехов и выберите исполнителя по цене, срокам и опыту.</p>
          <div class="hero-search">
            <input type="text" id="heroSearch" placeholder="Найти заказ, компанию или услугу..." class="hero-search-input">
            <button class="button button-primary" type="button" data-action="hero-search">Найти</button>
          </div>
          <div class="actions">
            <button class="button button-primary" type="button" data-create-order>Разместить заявку</button>
            <button class="button button-secondary" type="button" data-view="companies">Найти производителя</button>
          </div>
          <div class="stats">
            <div class="stat-card"><strong>${state.orders.length}</strong><p>активных проекта</p></div>
            <div class="stat-card"><strong>${state.makers.length}</strong><p>производителя на площадке</p></div>
            <div class="stat-card"><strong>1 день</strong><p>до первых откликов по заявке</p></div>
          </div>
        </div>
        <article class="hero-card">
          <img src="${HERO_IMG}" alt="Производство мебели">
          <div class="hero-card-body">
            <span class="status status-open">Для заказчиков и производств</span>
            <h2>От идеи до запуска в производство</h2>
            <p class="muted">Обсуждайте материалы, сроки, смету и монтаж с исполнителем в одном рабочем пространстве.</p>
          </div>
        </article>
      </div>
    </section>
    <section class="section">
      <div class="container split">
        <article class="role-card">
          <span class="role-label">Заказчикам</span>
          <h2>Получите предложения под ваш проект</h2>
          <p>Опишите мебель, сроки и бюджет, добавьте файлы и сравните отклики производств без десятков отдельных звонков.</p>
          <button class="button button-primary" type="button" data-auth="register" data-role="client">Создать кабинет заказчика</button>
        </article>
        <article class="role-card">
          <span class="role-label">Производителям</span>
          <h2>Получайте заказы под возможности цеха</h2>
          <p>Выбирайте подходящие проекты, отправляйте условия и ведите переговоры с заказчиками прямо на площадке.</p>
          <button class="button button-primary" type="button" data-auth="register" data-role="maker">Создать кабинет производителя</button>
        </article>
      </div>
    </section>`;
}

function warrantyActive(until) {
  if (!until) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const end = new Date(`${String(until).slice(0, 10)}T00:00:00`);
  return !Number.isNaN(end.getTime()) && end >= today;
}

function orderCard(order, showActions = true) {
  const user = state.user;
  const alreadyResponded = order.responses?.some((r) => r.maker_id === user?.id);
  const canRespond = user?.role === "maker" && order.status === "open" && !alreadyResponded;
  const canChoose = user?.role === "client" && user.id === order.client_id;
  const canChat = user && (user.id === order.client_id || user.id === order.selected_maker_id || order.responses?.some((r) => r.maker_id === user.id));
  return `
    <article class="order-card">
      <div class="order-card-header">
        <div>
          <span class="${statusClass(order.status)}">${statusLabel(order.status)}</span>
          <h3>${escapeHtml(order.title)}</h3>
          <p>${escapeHtml(order.details)}</p>
        </div>
        <div class="order-card-right">
          <strong>${money(order.budget)}</strong>
          ${deadlineCountdown(order.deadline)}
        </div>
      </div>
      <div class="meta-row">
        <span>${escapeHtml(order.type)}</span>
        <span>${order.quantity} шт.</span>
        <span>${escapeHtml(order.city)}</span>
        <span>${escapeHtml(order.deadline)}</span>
        <span>${escapeHtml(order.client_name || "Заказчик")}</span>
        ${order.warranty_until ? `<span class="badge ${warrantyActive(order.warranty_until) ? "badge-paid" : "badge-cancelled"}">Гарантия до ${escapeHtml(order.warranty_until)}${warrantyActive(order.warranty_until) ? " ✓" : " (истекла)"}</span>` : ""}
      </div>
      ${order.files?.length ? `<ul class="chips">${order.files.map((f) => `<li><a href="${f.url}" target="_blank" rel="noreferrer">${escapeHtml(f.name)}</a></li>`).join("")}</ul>` : ""}
      ${order.selected_maker_name ? `<p class="muted">Исполнитель: <strong>${escapeHtml(order.selected_maker_name)}</strong></p>` : ""}
      ${showActions ? `<div class="actions">
        ${canRespond ? `<button class="button button-primary button-small" type="button" data-respond="${order.id}">Откликнуться</button>` : ""}
        ${canChat ? `<button class="button button-secondary button-small" type="button" data-open-chat="${order.id}">Открыть чат</button>` : ""}
        ${canChoose ? `<button class="button button-secondary button-small" type="button" data-scroll-responses="${order.id}">Отклики: ${order.responses?.length || 0}</button>` : ""}
        ${(user?.id === order.client_id && (order.status === "open" || order.status === "progress")) ? `<button class="button button-secondary button-small" type="button" data-cancel-order="${order.id}">Отменить</button>` : ""}
        ${(order.status === "progress" && (user?.id === order.client_id || user?.id === order.selected_maker_id)) ? `<button class="button button-primary button-small" type="button" data-close-order="${order.id}">Завершить</button>` : ""}
        ${(order.status === "progress" && (user?.id === order.client_id || user?.id === order.selected_maker_id)) ? `<button class="button button-secondary button-small" type="button" data-toggle-stages="${order.id}">Этапы</button>` : ""}
        ${(order.status === "progress" && user?.id === order.client_id) ? `<button class="button button-primary button-small" type="button" data-accept-order="${order.id}">Принять работу</button>` : ""}
        ${(user?.id === order.client_id || user?.id === order.selected_maker_id) ? `<button class="button button-secondary button-small" type="button" data-order-contract="${order.id}">Договор</button>` : ""}
        ${(user?.role === "maker" && (order.status === "open" || order.status === "progress") && (order.responses?.some((r) => r.maker_id === user.id) || order.selected_maker_id === user.id)) ? `<button class="button button-secondary button-small" type="button" data-create-proposal="${order.id}">Отправить КП</button>` : ""}
        ${user && (user.id === order.client_id || user.id === order.selected_maker_id || order.responses?.some((r) => r.maker_id === user.id)) ? `<button class="button button-secondary button-small" type="button" data-list-proposals="${order.id}">КП</button>` : ""}
        ${user?.id === order.client_id ? `<button class="button button-secondary button-small" type="button" data-duplicate-order="${order.id}" title="Создать копию как черновик">Дублировать</button>` : ""}
        <button class="button button-secondary button-small" type="button" data-export-order="${order.id}" title="Экспорт в PDF">📥</button>
        <button class="button button-secondary button-small" type="button" data-delivery-history="${order.id}" title="Доставка">🚚</button>
        <button class="button button-secondary button-small" type="button" data-order-history="${order.id}" title="История">📋</button>
        <button class="button button-secondary button-small" type="button" data-report-order="${order.id}" title="Пожаловаться">⚠</button>
      </div>` : ""}
      <div class="stages-panel" id="stages-${order.id}" hidden></div>
    </article>`;
}

function renderCompanies() {
  const types = COMPANY_TYPES.filter((t) => t.id !== "client");
  app.innerHTML = `
    <section class="dashboard">
      <div class="container">
        <div class="dashboard-top">
          <div>
            <p class="eyebrow">Каталог компаний</p>
            <h1>Мебельные производства</h1>
            <p class="lead">Выбирайте исполнителей по типу, региону, специализации и описанию.</p>
          </div>
        </div>
        <div class="market-layout">
          <aside class="panel filters-panel">
            <label>Тип компании
              <select id="companyTypeFilter">
                <option value="">Все типы</option>
                ${types.map((t) => `<option value="${t.id}">${t.name}</option>`).join("")}
              </select>
            </label>
            <label>Регион
              <select id="companyRegionFilter">
                <option value="">Все регионы</option>
                ${state.regions.map((r) => `<option value="${r.id}">${escapeHtml(r.name)}</option>`).join("")}
              </select>
            </label>
            <label>Поиск
              <input id="companySearchFilter" placeholder="Название или описание">
            </label>
          </aside>
          <div class="order-list">
            ${state.companies.length ? state.companies.map((c) => companyCard(c)).join("") : emptyState("Компании не найдены. Попробуйте изменить фильтры.")}
          </div>
        </div>
      </div>
    </section>`;
}

function companyCard(c) {
  const skills = Array.isArray(c.skills) ? c.skills : [];
  const fav = isFavorite(c.id);
  const rating = c.avg_rating ? starRating(Math.round(c.avg_rating)) : '';
  const revCount = c.reviews_count ? `<small class="muted">(${c.reviews_count})</small>` : '';
  return `
    <article class="maker-card" data-company-id="${c.id}" style="cursor:pointer">
      <div class="maker-card-header">
        <div>
          <h3>${escapeHtml(c.name)}</h3>
          <p>${escapeHtml(c.city)} ${c.region_name ? "· " + escapeHtml(c.region_name) : ""} · ${companyTypeLabel(c.company_type)}</p>
          ${rating ? `<div class="rating-row">${rating} ${revCount}</div>` : ''}
        </div>
        <div class="actions" style="gap:6px">
          <span class="badge">${companyTypeLabel(c.company_type)}</span>
          ${state.user ? `<button class="favorite-btn ${fav ? "is-active" : ""}" type="button" data-toggle-favorite="${c.id}" title="${fav ? "Убрать из избранного" : "В избранное"}">${fav ? "\u2665" : "\u2661"}</button>` : ""}
        </div>
      </div>
      <p>${escapeHtml(c.about || "Описание пока не заполнено.")}</p>
      ${skills.length ? `<ul class="chips">${skills.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul>` : ""}
      ${c.capacity ? `<p class="muted">Мощность: ${escapeHtml(c.capacity)}</p>` : ""}
    </article>`;
}

function renderCompanyProfile(company) {
  const skills = Array.isArray(company.skills) ? company.skills : [];
  const rating = company.avg_rating ? starRating(Math.round(company.avg_rating)) : '';
  app.innerHTML = `
    <section class="dashboard">
      <div class="container">
        <p class="breadcrumbs"><a href="/" data-nav>Главная</a> / <a href="/companies" data-nav>Компании</a> / ${escapeHtml(company.name)}</p>
        <div class="dashboard-top">
          <div>
            <p class="eyebrow">Профиль компании</p>
            <h1>${escapeHtml(company.name)}</h1>
            <p class="lead">${companyTypeLabel(company.company_type)} · ${escapeHtml(company.city)} ${company.region_name ? "· " + escapeHtml(company.region_name) : ""}</p>
            ${company.verified_requisites_at ? '<p><span class="badge badge-paid">✓ Реквизиты проверены</span></p>' : ''}
            ${!company.is_public && state.user && (state.user.id === company.id || state.user.role === "admin") ? '<p><span class="badge" style="margin-top:6px">Профиль скрыт из каталога</span></p>' : ''}
            ${rating ? `<div class="rating-row">${rating} <span class="muted">(${company.reviews_count || 0} отзывов, среднее ${company.avg_rating})</span></div>` : ''}
          </div>
          <button class="button button-secondary" type="button" data-view="companies">Назад к каталогу</button>
        </div>
        <div class="company-profile-grid">
          <div class="company-main">
            <div class="panel">
              <h2>О компании</h2>
              <p>${escapeHtml(company.about || "Описание не заполнено.")}</p>
              ${company.capacity ? `<p class="muted">Мощность: ${escapeHtml(company.capacity)}</p>` : ""}
              ${skills.length ? `<ul class="chips">${skills.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul>` : ""}
            </div>
            ${company.inn || company.ogrn || company.website || company.region_name ? `
            <div class="panel">
              <h2>Реквизиты</h2>
              <dl class="requisites">
                ${company.inn ? `<div><dt>ИНН</dt><dd>${escapeHtml(company.inn)}</dd></div>` : ""}
                ${company.ogrn ? `<div><dt>ОГРН</dt><dd>${escapeHtml(company.ogrn)}</dd></div>` : ""}
                ${company.region_name ? `<div><dt>Регион</dt><dd>${escapeHtml(company.region_name)}</dd></div>` : ""}
                ${company.website ? `<div><dt>Веб-сайт</dt><dd><a href="${escapeHtml(company.website)}" target="_blank" rel="noreferrer">${escapeHtml(company.website)}</a></dd></div>` : ""}
              </dl>
              ${state.user?.role === "admin" && (company.inn || company.ogrn) ? `
                <div class="actions" style="margin-top:12px">
                  ${company.verified_requisites_at
                    ? `<button class="button button-secondary button-small" type="button" data-verify-requisites="${company.id}:0">Снять отметку</button>`
                    : `<button class="button button-primary button-small" type="button" data-verify-requisites="${company.id}:1">Подтвердить реквизиты</button>`}
                </div>` : ""}
            </div>` : ""}
            <div class="panel">
              <h2>Услуги (${company.services?.length || 0})</h2>
              ${company.services?.length ? company.services.map((s) => `
                <div class="service-item">
                  <h3>${escapeHtml(s.title)}</h3>
                  <p class="muted">${escapeHtml(s.description)}</p>
                  ${s.price_type ? `<span class="badge">${escapeHtml(s.price_type)}</span>` : ""}
                </div>
              `).join("") : emptyState("Услуг пока нет.")}
            </div>
            ${company.gallery?.length ? `
              <div class="panel">
                <h2>Галерея работ</h2>
                <div class="gallery-grid">
                  ${company.gallery.map((g) => `<img src="${g.url}" alt="${escapeHtml(g.name)}" class="gallery-img">`).join("")}
                </div>
              </div>` : ""}
            <div class="panel">
              <h2>Отзывы (${company.reviews_count || 0})</h2>
              ${company.reviews?.length ? company.reviews.map(r => `
                <div class="review-item">
                  <div class="review-header">
                    <strong>${escapeHtml(r.reviewer_name)}</strong>
                    ${starRating(r.rating)}
                    <small class="muted">${escapeHtml(r.created_at)}</small>
                  </div>
                  <p>${escapeHtml(r.text)}</p>
                </div>
              `).join("") : emptyState("Отзывов пока нет.")}
              ${state.user && state.user.id !== company.id ? `
                <div class="review-form-wrap">
                  <h3>Оставить отзыв</h3>
                  <form class="stack-form" id="reviewForm" data-company-id="${company.id}">
                    <label>Рейтинг
                      <div class="star-rating interactive" id="reviewStars">
                        ${[1,2,3,4,5].map(i => `<span class="star" data-star="${i}">★</span>`).join('')}
                      </div>
                      <input type="hidden" name="rating" value="5" id="reviewRating">
                    </label>
                    <label>Комментарий <textarea name="text" rows="3" placeholder="Ваш отзыв о работе с компанией..."></textarea></label>
                    <button class="button button-primary" type="submit">Отправить отзыв</button>
                  </form>
                </div>
              ` : ''}
            </div>
            ${state.user && state.user.id === company.id ? `
              <div class="panel">
                <h2>Документы (${company.documents?.length || 0})</h2>
                ${company.documents?.length ? company.documents.map(d => `
                  <div class="doc-item">
                    <span>📄 ${escapeHtml(d.original_name)} <small class="muted">(${d.doc_type}, ${(d.size/1024).toFixed(0)} КБ)</small></span>
                    <button class="button button-danger button-small" type="button" data-delete-document="${d.id}">Удалить</button>
                  </div>
                `).join("") : '<p class="muted">Документов пока нет.</p>'}
                <form class="stack-form" id="documentForm" enctype="multipart/form-data" style="margin-top:12px">
                  <label>Тип документа
                    <select name="doc_type">
                      <option value="certificate">Сертификат</option>
                      <option value="license">Лицензия</option>
                      <option value="portfolio">Портфолио</option>
                      <option value="other">Другое</option>
                    </select>
                  </label>
                  <label>Файл <input name="files" type="file" multiple required></label>
                  <button class="button button-primary button-small" type="submit">Загрузить</button>
                </form>
              </div>
            ` : ''}
          </div>
          <aside class="company-sidebar">
            <div class="panel">
              <div class="stat-card"><strong>${company.orders_count || 0}</strong><p>заказов</p></div>
              <div class="stat-card"><strong>${company.responses_count || 0}</strong><p>откликов</p></div>
              <div class="stat-card"><strong>${company.services?.length || 0}</strong><p>услуг</p></div>
              <div class="stat-card"><strong>${company.reviews_count || 0}</strong><p>отзывов</p></div>
              ${company.avg_rating ? `<div class="stat-card"><strong>${company.avg_rating}</strong><p>средний рейтинг</p></div>` : ''}
            </div>
            ${state.user?.role === "client" && state.orders.some((o) => o.client_id === state.user.id && o.status === "open") ? `
              <button class="button button-primary" type="button" data-invite-company="${company.id}" style="width:100%;margin-top:8px">Запросить расчёт</button>
            ` : ""}
          </aside>
        </div>
      </div>
    </section>`;
}

function renderMarket() {
  app.innerHTML = `
    <section class="dashboard">
      <div class="container">
        <div class="dashboard-top">
          <div>
            <p class="eyebrow">Биржа заказов</p>
            <h1>Открытые мебельные проекты</h1>
            <p class="lead">Посмотрите реальные заявки на производство мебели.</p>
          </div>
          <button class="button button-primary" type="button" data-create-order>Создать заказ</button>
        </div>
        <div class="market-layout">
          <aside class="panel filters-panel">
            <label>Тип проекта
              <select id="typeFilter">
                <option value="">Все типы</option>
                ${["Кухни и шкафы", "Офисная мебель", "HoReCa и retail", "Серийные детали"].map((i) => `<option>${i}</option>`).join("")}
              </select>
            </label>
            <label>Статус
              <select id="statusFilter">
                <option value="">Все статусы</option>
                <option value="open">Открыт</option>
                <option value="progress">В работе</option>
                <option value="closed">Завершен</option>
              </select>
            </label>
            <label>Город
              <input id="cityFilter" placeholder="Москва">
            </label>
            <label>Бюджет от, руб.
              <input id="budgetMinFilter" type="number" min="0" placeholder="0">
            </label>
            <label>Бюджет до, руб.
              <input id="budgetMaxFilter" type="number" min="0" placeholder="Без ограничений">
            </label>
          </aside>
          <div class="order-list">
            ${state.orders.length ? state.orders.map((o) => orderCard(o)).join("") : emptyState("Заказов пока нет. Будьте первым!", "Создать заказ", 'data-create-order')}
          </div>
        </div>
      </div>
    </section>`;
}

function renderDashboard() {
  if (!state.user) return openAuth("login");
  const isClient = state.user.role === "client";
  const tabs = isClient
    ? [["overview", "Обзор"], ["notifications", `Уведомления${state.unreadCount ? ' (' + state.unreadCount + ')' : ''}`], ["new-order", "Создать заказ"], ["my-orders", "Мои заказы"], ["estimate", "Калькулятор"], ["templates", "Шаблоны"], ["invoices", "Счета"], ["materials", "Материалы"], ["suppliers", "Поставщики"], ["certificates", "Сертификаты"], ["time", "Время"], ["favorites", "Избранные"], ["chats", "Сообщения"], ["security", "Безопасность"], ["profile", "Профиль"]]
    : [["overview", "Обзор"], ["notifications", `Уведомления${state.unreadCount ? ' (' + state.unreadCount + ')' : ''}`], ["available", "Доступные заказы"], ["responses", "Мои отклики"], ["funnel", "Воронка"], ["estimate", "Калькулятор"], ["my-services", "Мои услуги"], ["analytics", "Аналитика"], ["invoices", "Счета"], ["materials", "Материалы"], ["suppliers", "Поставщики"], ["time", "Время"], ["favorites", "Избранные"], ["chats", "Сообщения"], ["security", "Безопасность"], ["profile", "Профиль"]];
  app.innerHTML = `
    <section class="dashboard">
      <div class="container">
        <div class="dashboard-top">
          <div>
            <p class="eyebrow">Личный кабинет</p>
            <h1>${escapeHtml(state.user.name)}</h1>
            <p class="lead">${companyTypeLabel(state.user.company_type)} · ${escapeHtml(state.user.email)}</p>
          </div>
          <button class="button button-secondary" type="button" data-action="logout">Выйти</button>
        </div>
        <div class="dashboard-grid">
          <aside class="sidebar">
            ${tabs.map(([id, label]) => `<button type="button" class="${state.dashboardTab === id ? "is-active" : ""}" data-tab="${id}">${label}</button>`).join("")}
          </aside>
          <div class="content-stack">${dashboardContent()}</div>
        </div>
      </div>
    </section>`;
}

function dashboardContent() {
  if (state.dashboardTab === "new-order") return newOrderForm();
  if (state.dashboardTab === "my-orders") return myOrders();
  if (state.dashboardTab === "available") return availableOrders();
  if (state.dashboardTab === "responses") return myResponses();
  if (state.dashboardTab === "funnel") return makerFunnelView();
  if (state.dashboardTab === "estimate") return estimateView();
  if (state.dashboardTab === "my-services") return myServices();
  if (state.dashboardTab === "favorites") return myFavorites();
  if (state.dashboardTab === "chats") return chatView();
  if (state.dashboardTab === "profile") return profileForm();
  if (state.dashboardTab === "notifications") return state.notifView === "settings" ? notifPrefsView() : notificationsView();
  if (state.dashboardTab === "materials") return materialsView();
  if (state.dashboardTab === "templates") return templatesView();
  if (state.dashboardTab === "invoices") return invoicesView();
  if (state.dashboardTab === "suppliers") return suppliersView();
  if (state.dashboardTab === "certificates") return certificatesView();
  if (state.dashboardTab === "time") return timeTrackingView();
  if (state.dashboardTab === "security") return tfaSetupView();
  if (state.dashboardTab === "analytics") return makerStatsView();
  return overview();
}

function renderAsync(promise) {
  promise.then((html) => { app.innerHTML = html; startDeadlineTimers(); }).catch((e) => showToast(e.message));
}

async function makerStatsView() {
  let stats = null;
  try {
    stats = await api("/api/maker/stats");
  } catch (error) { return `<div class="panel">${escapeHtml(error.message)}</div>`; }
  return `
    <div class="stats">
      <div class="stat-card"><strong>${stats.responses_count}</strong><p>откликов</p></div>
      <div class="stat-card"><strong>${stats.conversion_rate}%</strong><p>конверсия в выбор</p></div>
      <div class="stat-card"><strong>${stats.active_orders}</strong><p>заказов в работе</p></div>
      <div class="stat-card"><strong>${money(stats.revenue)}</strong><p>выручка по закрытым</p></div>
      <div class="stat-card"><strong>${stats.avg_rating}</strong><p>средний рейтинг</p></div>
      <div class="stat-card"><strong>${stats.total_hours} ч</strong><p>учтено времени</p></div>
    </div>
    <div class="panel">
      <h2>Отклики по месяцам</h2>
      <div class="bar-row"><span class="bar-label"></span></div>
      ${stats.by_month.map((m) => `
        <div class="bar-row"><span class="bar-label">${escapeHtml(m.month)}</span><div class="bar-track"><div class="bar-fill bar-type" style="width:${Math.max(4, m.cnt / Math.max(1, ...stats.by_month.map(x => x.cnt)) * 100)}%"></div></div><span class="bar-value">${m.cnt}</span></div>
      `).join("") || '<p class="muted">Пока нет откликов</p>'}
    </div>`;
}

async function servicesCatalogView() {
  const data = await api("/api/services");
  state.servicesCatalog = data.services;
  return `
    <section class="section"><div class="container">
      <p class="eyebrow">Каталог</p>
      <h1>Услуги мебельных производств</h1>
      <div class="order-list" style="margin-top:16px">
        ${state.servicesCatalog.length ? state.servicesCatalog.map((s) => `
          <article class="maker-card">
            <div class="maker-card-header">
              <div>
                <h3><a href="/services/${s.id}" data-nav>${escapeHtml(s.title)}</a></h3>
                <p class="muted">${escapeHtml(s.company_name || "")} · ${escapeHtml(s.company_city || "")}</p>
              </div>
              ${s.price_type ? `<span class="badge">${escapeHtml(s.price_type)}</span>` : ""}
            </div>
            <p>${escapeHtml(s.description)}</p>
            ${(s.params || []).length ? `<dl class="service-params">${s.params.map((p) => `<div><dt>${escapeHtml(p.name)}</dt><dd>${escapeHtml(p.value)}</dd></div>`).join("")}</dl>` : ""}
            <div class="actions">
              <a class="button button-secondary button-small" href="/companies/${s.user_id}" data-nav>Открыть компанию</a>
            </div>
          </article>`).join("") : emptyState("Услуг пока нет.")}
      </div>
    </div></section>`;
}

async function servicePageView(serviceId) {
  let service;
  try {
    const data = await api(`/api/services/${serviceId}`);
    service = data.service;
  } catch (error) {
    return `<div class="panel"><div class="empty">${escapeHtml(error.message)}</div></div>`;
  }
  return `
    <section class="section"><div class="container">
      <p class="breadcrumbs"><a href="/" data-nav>Главная</a> / <a href="/services" data-nav>Услуги</a> / ${escapeHtml(service.title)}</p>
      <h1>${escapeHtml(service.title)}</h1>
      <p class="lead">${escapeHtml(service.company_name || "")} · ${escapeHtml(service.company_city || "")}${service.price_type ? " · " + escapeHtml(service.price_type) : ""}</p>
      ${(service.files || []).length ? `<ul class="chips">${service.files.map((f) => `<li><a href="${f.url}" target="_blank" rel="noreferrer">${escapeHtml(f.name)}</a></li>`).join("")}</ul>` : ""}
      <div class="panel"><p>${escapeHtml(service.description)}</p>
        ${(service.params || []).length ? `<dl class="service-params">${service.params.map((p) => `<div><dt>${escapeHtml(p.name)}</dt><dd>${escapeHtml(p.value)}</dd></div>`).join("")}</dl>` : ""}
      </div>
      <div class="actions">
        <a class="button button-primary" href="/companies/${service.user_id}" data-nav>Открыть компанию</a>
        <a class="button button-secondary" href="/market" data-nav>Найти заказы</a>
      </div>
    </div></section>`;
}

function renderMd(text) {
  const esc = escapeHtml(text || "");
  return esc
    .replace(/^### (.*)$/gm, "<h3>$1</h3>")
    .replace(/^## (.*)$/gm, "<h2>$1</h2>")
    .replace(/^# (.*)$/gm, "<h1>$1</h1>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/^\- (.*)$/gm, "<li>$1</li>")
    .replace(/\[(.+?)\]\(([^)]+)\)/g, '<a href="$2">$1</a>')
    .split(/\n{2,}/)
    .map((block) => block.match(/^\s*<(h\d|ul|li)/) ? block : `<p>${block.replace(/\n/g, "<br>")}</p>`)
    .join("\n");
}

async function articlesListView() {
  const data = await api("/api/articles");
  state.articlesList = data.articles;
  return `
    <section class="section"><div class="container">
      <p class="eyebrow">База знаний</p>
      <h1>Статьи о мебельном производстве</h1>
      <div class="order-list" style="margin-top:16px">
        ${state.articlesList.length ? state.articlesList.map((a) => `
          <article class="maker-card">
            <h3><a href="/articles/${a.slug}" data-nav>${escapeHtml(a.title)}</a></h3>
            <p>${escapeHtml(a.excerpt)}</p>
            <small class="muted">${escapeHtml(a.updated_at)}</small>
          </article>`).join("") : emptyState("Статей пока нет.")}
      </div>
    </div></section>`;
}

async function articlePageView(slug) {
  let article;
  try {
    const data = await api(`/api/articles/${slug}`);
    article = data.article;
  } catch (error) {
    return `<div class="panel"><div class="empty">${escapeHtml(error.message)}</div></div>`;
  }
  return `
    <section class="section"><div class="container" style="max-width:760px">
      <p class="breadcrumbs"><a href="/" data-nav>Главная</a> / <a href="/articles" data-nav>Статьи</a> / ${escapeHtml(article.title)}</p>
      <h1>${escapeHtml(article.title)}</h1>
      <small class="muted">${escapeHtml(article.updated_at)}</small>
      <div class="panel article-body">${renderMd(article.body_md)}</div>
    </div></section>`;
}

function privacyView() {
  return `
    <section class="section"><div class="container legal-page">
      <p class="breadcrumbs"><a href="/" data-nav>Главная</a> / Политика конфиденциальности</p>
      <p class="eyebrow">Правовая информация</p>
      <h1>Политика конфиденциальности</h1>
      <p class="muted legal-updated">Редакция от 23 сентября 2026 года</p>
      <div class="panel legal-body">
        <h2>1. Общие положения</h2>
        <p>Настояшая Политика конфиденциальности определяет порядок обработки и защиты персональных данных пользователей сайта и сервиса Meblio (далее — «Сайт», «Площадка») и размещена в целях обеспечения неограниченного доступа к информации об обработке персональных данных в соответствии с Федеральным законом от 27.07.2006 № 152-ФЗ «О персональных данных» (далее — 152-ФЗ).</p>
        <p>Использование Сайта, регистрация аккаунта и заполнение форм означают ознакомление с настоящей Политикой. Согласие на обработку персональных данных пользователь даёт отдельно при регистрации.</p>

        <h2>2. Оператор персональных данных</h2>
        <p>Оператором персональных данных является:</p>
        <ul>
          <li>наименование: <strong>[ООО «Меблио» / ИП — подставить реквизиты]</strong>;</li>
          <li>ИНН: <strong>[указать ИНН]</strong>;</li>
          <li>ОГРН/ОГРНИП: <strong>[указать]</strong>;</li>
          <li>адрес: <strong>[юридический и/или фактический адрес]</strong>;</li>
          <li>email для обращений по вопросам персональных данных: <strong>[pd@meblio.ru / указать email]</strong>.</li>
        </ul>
        <p>Заполните реквизиты оператора перед публикацией Политики в боевой среде.</p>

        <h2>3. Какие персональные данные обрабатываются</h2>
        <p>В зависимости от действий пользователя Площадка может обрабатывать:</p>
        <ul>
          <li><strong>Данные аккаунта:</strong> email, имя или название компании, пароль (хранится в виде соли и хеша, в открытом виде не сохраняется), роль (заказчик/производитель), город, регион, тип компании, компетенции и описание;</li>
          <li><strong>Контактные и реквизиты профиля:</strong> телефон, сайт, ИНН, ОГРН, признак публичности профиля;</li>
          <li><strong>Данные активности на Площадке:</strong> заказы и заявки, отклики, сообщения чата, файлы к заказам и сообщениям, услуги, галерея и документы компании, отзывы и рейтинги, избранные компании, счета, история уведомлений;</li>
          <li><strong>Технические данные:</strong> IP-адрес, данные браузера и устройства, cookie (см. раздел 8), даты и время действий — для безопасности, антифрод-проверок и работы сервиса;</li>
          <li><strong>Данные AI-ассистента:</strong> тексты обращений и ответов ассистента, история диалога;</li>
          <li><strong>Данные для писем:</strong> email, используемый для подтверждения адреса, восстановления пароля и служебных уведомлений.</li>
        </ul>
        <p>Специальные категории персональных данных (ст. 10 152-ФЗ) и биометрические персональные данные Площадкой не обрабатываются.</p>

        <h2>4. Цели обработки персональных данных</h2>
        <ul>
          <li>заключение и исполнение договора пользования Площадкой (публичной оферты);</li>
          <li>идентификация пользователя, создание и ведение аккаунта, аутентификация, включая двухфакторную;</li>
          <li>обеспечение работы маркетплейса: размещение заказов, отклики, переписка, рейтинги, счета;</li>
          <li>направление служебных писем и уведомлений (подтверждение email, восстановление пароля, статусы заказов);</li>
          <li>обеспечение безопасности, предотвращение злоупотреблений, rate limiting, техническое обслуживание и анализ ошибок;</li>
          <li>исполнение требований законодательства РФ и ответы на законные запросы уполномоченных органов;</li>
          <li>при включённом AI-ассистенте — формирование ответов на запросы пользователя.</li>
        </ul>

        <h2>5. Правовые основания обработки</h2>
        <ul>
          <li>согласие субъекта персональных данных (ст. 9, п. 1 ч. 1 ст. 6 152-ФЗ) — даётся при регистрации отдельным действием;</li>
          <li>заключение и исполнение договора (п. 5 ч. 1 ст. 6 152-ФЗ) — п. 2 ст. 432, ст. 437 ГК РФ;</li>
          <li>выполнение обязанностей, предусмотренных законом (п. 2 ч. 1 ст. 6, ст. 6.1, 11, 14 152-ФЗ, ФЗ-149 «Об информации»).</li>
        </ul>

        <h2>6. Действия с персональными данными и передача третьим лицам</h2>
        <p>Обработка включает сбор, запись, систематизацию, накопление, хранение, уточнение (обновление, изменение), извлечение, использование, обезличивание, блокирование, удаление, уничтожение — в объёме, необходимом для целей из раздела 4, автоматизированным и/или смешанным способом.</p>
        <p>Для работы сервиса данные могут передаваться лицам, обрабатывающим данные <strong>по поручению оператора</strong> (ст. 6, ст. 6.1 152-ФЗ), в частности:</p>
        <ul>
          <li>хостинг-провайдер, на серверах которого размещён Сайт (в т.ч. в Российской Федерации);</li>
          <li>провайдер SMTP-рассылок для отправки писем;</li>
          <li>при включённой интеграции AI — внешний провайдер LLM-API (передача текста диалога и служебного контекста профиля);</li>
          <li>при включённой Яндекс.Метрике — оператор веб-аналитики;</li>
          <li>государственные органы — в случаях, установленных законом.</li>
        </ul>
        <p>Трансграничная передача персональных данных (ст. 12 152-ФЗ) не осуществляется, если внешние AI/аналитика отключены. При их включении передача возможна получателям, указанным в настройках интеграций; отдельное согласие на трансграничную передачу запрашивается дополнительно, если это требуется законом.</p>

        <h2>7. Сроки хранения и удаление</h2>
        <ul>
          <li>данные аккаунта — до удаления аккаунта пользователя;</li>
          <li>при удалении аккаунта данные обезличиваются: имя заменяется на «Удалённый пользователь», история заказов и отзывы сохраняются в обезличенном виде для целей площадки (п. 5 ч. 1 ст. 6 152-ФЗ, договорные отношения с контрагентами);</li>
          <li>согласие на обработку — до его отзыва;</li>
          <li>технические cookie и логи — в сроки, необходимые для работы и безопасности сервиса (как правило, не более 30 дней для логов, до истечения cookie);</li>
          <li>по истечении целей обработки данные уничтожаются или обезличиваются, если иное не предусмотрено законом.</li>
        </ul>

        <h2>8. Cookie и аналогичные технологии</h2>
        <p>Сайт использует cookie и localStorage:</p>
        <ul>
          <li><code>meblio_session</code> — идентификатор сессии (HttpOnly, Secure, SameSite=Lax), срок до 7 дней; необходим для входа и работы кабинета;</li>
          <li><code>meblio_device</code> — отметка доверенного устройства для двухфакторной аутентификации, срок до 30 дней;</li>
          <li><code>meblio-theme</code> в localStorage — выбранная тема оформления;</li>
          <li><code>meblio-cookie-ok</code> в localStorage — факт закрытия информационного баннера о cookie.</li>
        </ul>
        <p>Технические cookie необходимы для работы сервиса. Необходимые cookie не отключаются пользователем через баннер; отказ от необязательных аналитических cookie возможен, если аналитика подключена, через настройки браузера или соответствующие средства.</p>

        <h2>9. Права пользователя персональных данных</h2>
        <p>Пользователь вправе (ст. 14, 21 152-ФЗ):</p>
        <ul>
          <li>получать информацию об обработке своих персональных данных;</li>
          <li>требовать уточнения, блокирования, уничтожения неточных или незаконно обрабатываемых данных;</li>
          <li>отозвать согласие на обработку персональных данных;</li>
          <li>требовать уведомления лиц, которым ранее были переданы неточные данные, об их уничтожении или исправлении;</li>
          <li>обжаловать действия оператора в Роскомнадзор или в суд.</li>
        </ul>
        <p>Запрос направляется на email оператора, указанный в разделе 2, с темой «Персональные данные». Срок ответа — не более 30 дней со дня получения запроса. Для отзыва согласия используйте удаление аккаунта в личном кабинете или напишите на тот же email.</p>

        <h2>10. Меры по обеспечению безопасности</h2>
        <p>Оператор принимает правовые, организационные и технические меры для защиты персональных данных от неправомерного доступа, уничтожения, изменения, копирования и распространения, в том числе: разграничение прав доступа, хранение паролей в виде соли и криптографического хеша, cookie HttpOnly/Secure/SameSite, проверки CSRF, ограничение частоты запросов (rate limiting), журналирование событий безопасности, резервное копирование, своевременное обновление программного обеспечения.</p>

        <h2>11. Обработка данных несовершеннолетних</h2>
        <p>Площадка предназначена для пользователей, достигших 14 лет. Согласие на обработку данных детей в возрасте от 14 до 18 лет даётся законным представителем в случаях, предусмотренных законодательством. Если вы считаете, что данные ребенка были переданы без необходимого согласия, сообщите оператору для их удаления.</p>

        <h2>12. Изменения Политики</h2>
        <p>Оператор вправе изменять Политику. Актуальная редакция всегда доступна на этой странице. При существенных изменениях уведомление размещается на Сайте и/или направляется по email.</p>
        <p>Дата последней редакции: <strong>23 сентября 2026 года</strong>.</p>

        <h2>13. Контакты</h2>
        <p>Вопросы по обработке персональных данных: <strong>[email оператора]</strong>. Актуальные реквизиты — в разделе 2 настоящей Политики.</p>
      </div>
    </div></section>`;
}

function offerView() {
  return `
    <section class="section"><div class="container legal-page">
      <p class="breadcrumbs"><a href="/" data-nav>Главная</a> / Публичная оферта</p>
      <p class="eyebrow">Правовая информация</p>
      <h1>Публичная оферта</h1>
      <p class="muted legal-updated">Договор оказания услуг по предоставлению доступа к информационной площадке Meblio · редакция от 23 сентября 2026 года</p>
      <div class="panel legal-body">
        <h2>1. Общие положения</h2>
        <p>1.1. Настоящий документ является публичной офертой (ст. 437 ГК РФ) <strong>[ООО «Меблио» / ИП — подставить реквизиты]</strong> (далее — «Исполнитель») и содержит все существенные условия договора оказания услуг по предоставлению доступа к информационной площадке Meblio.</p>
        <p>1.2. Сайт: mebl.io (далее — «Площадка») — информационная система для размещения заказов на изготовление мебели, поиска производителей и общения сторон.</p>
        <p>1.3. Услуги Исполнителя: предоставление технической возможности использовать функции Площадки (регистрация, кабинеты, размещение заказов, отклики, каталоги, чат, уведомления). Исполнитель <strong>не является стороной</strong> договора подряда (купли-продажи) между Заказчиком и Производителем и не гарантирует заключение, качество или исход сделки между ними.</p>
        <p>1.4. Пользователями могут быть физические и юридические лица, действующие в рамках своей предпринимательской деятельности (B2B), а также физические лица — заказчики мебели для личных нужд.</p>

        <h2>2. Акцепт оферты</h2>
        <p>2.1. Акцептом настоящей оферты является регистрация аккаунта на Площадке и/или фактическое использование функций Площадки после ознакомления с офертой.</p>
        <p>2.2. Акцепт означает полное и безоговорочное принятие всех условий настоящей Оферты и <a href="/privacy" data-nav>Политики конфиденциальности</a>.</p>
        <p>2.3. Исполнитель вправе изменять условия Оферты. Новая редакция вступает в силу с момента её размещения на Площадке и не применяется к отношениям, возникшим до даты размещения.</p>

        <h2>3. Предмет договора и функции Площадки</h2>
        <p>3.1. Исполнитель предоставляет Пользователю возмездно или на условиях, указанных на Сайте (тарифы, при наличии, публикуются отдельно), техническую возможность:</p>
        <ul>
          <li>проходить регистрацию и вести аккаунт (роль «Заказчик» или «Производитель»);</li>
          <li>размещать и просматривать заказы, отправлять отклики;</li>
          <li>вести переписку в чате, обмениваться файлами;</li>
          <li>размещать сведения о компании, услугах, галерее;</li>
          <li>пользоваться дополнительными функциями (уведомления, AI-ассистент, экспорт, счета) при их наличии.</li>
        </ul>
        <p>3.2. Отдельные функции могут временно быть недоступны в связи с обслуживанием, обновлениями или форс-мажором. Исполнитель стремится ограничивать недоступность разумным сроком.</p>

        <h2>4. Регистрация и аккаунт</h2>
        <p>4.1. Для регистрации пользователь указывает достоверные данные (email, имя или наименование, город, роль и иные запрашиваемые поля) и принимает Оферту и Политику.</p>
        <p>4.2. Один email — один аккаунт. Пользователь обязан обеспечить конфиденциальность пароля и незамедлительно уведомлять Исполнителя о компрометации аккаунта.</p>
        <p>4.3. Запрещается: указывать заведомо ложные сведения; использовать аккаунт для противоправных целей; распространять вредоносное ПО; совершать обман других пользователей; осуществлять нежелательную рекламу; обходить технические средства защиты и ограничения Площадки; иным образом нарушать законодательство РФ и права третьих лиц.</p>
        <p>4.4. Исполнитель вправе ограничить или заблокировать аккаунт при нарушении условий Оферты, требований закона или для обеспечения безопасности, с уведомлением пользователя, если это не противоречит закону.</p>

        <h2>5. Заказчики и Производители: отношения между собой</h2>
        <p>5.1. Размещение заказа, отклик, переписка и согласование условий на Площадке являются способом <strong>поиска контрагента</strong>. Договор подряда, поставки или оказания услуг на изготовление мебели заключается <strong>напрямую</strong> между Заказчиком и Производителем на согласованных ими условиях (предмет, цена, сроки, гарантия).</p>
        <p>5.2. Исполнитель не проверяет коммерческие условия сторон, не является гарантом оплаты, качества работ, сроков доставки и монтажа и не участвует в расчётах между Заказчиком и Производителем, если иное не предусмотрено отдельным функционалом Площадки.</p>
        <p>5.3. Стороны обязаны добросовестно вести переговоры, не совершать действий, вводящих контрагента в заблуждение, и соблюдать применимое законодательство (в т.ч. о рекламе, о защите прав потребителей — для соответствующих субъектов).</p>

        <h2>6. Права и обязанности сторон</h2>
        <p>6.1. Исполнитель обязуется: предоставлять доступ к функциям Площадки в соответствии с тарифом/условиями; обеспечивать работу сервиса с разумной надёжностью; обрабатывать персональные данные в соответствии с Политикой; отвечать на обращения в разумный срок.</p>
        <p>6.2. Пользователь обязуется: соблюдать Оферту и Политику; не нарушать права третьих лиц и закон; указывать достоверные данные; самостоятельно разрешать споры с контрагентами.</p>
        <p>6.3. Пользователь подтверждает, что обладает необходимыми правами на размещаемый контент (тексты, изображения, чертежи) либо имеет согласие правообладателя.</p>

        <h2>7. Ответственность и ограничение ответственности</h2>
        <p>7.1. Исполнитель отвечает за надлежащее предоставление услуг по настоящей Оферте в пределах, установленных законом.</p>
        <p>7.2. Исполнитель не несёт ответственности за: действия или бездействие Заказчиков и Производителей между собой; содержание размещённых пользователями объявлений; косвенные убытки, упущенную выгоду — в максимально допустимой законом мере; временную недоступность по причинам форс-мажора, действий третьих лиц или плановых работ.</p>
        <p>7.3. Совокупная ответственность Исполнителя по Оферте ограничена суммой вознаграждения, фактически полученного от Пользователя за период, в котором возникло нарушение, если иное не предусмотрено императивными нормами закона.</p>
        <p>7.4. Пользователь использует Площадку на свой риск в части выбора контрагента и условий сделки между сторонами.</p>

        <h2>8. Интеллектуальная собственность</h2>
        <p>8.1. Права на программный код, дизайн и товарные знаки Площадки принадлежат Исполнителю или его лицензиарам. Использование — только в рамках Оферты.</p>
        <p>8.2. Пользователь сохраняет права на свои материалы и предоставляет Исполнителю ограниченную лицензию на их размещение и техническую обработку в целях работы Площадки.</p>

        <h2>9. Персональные данные</h2>
        <p>Обработка персональных данных осуществляется в соответствии с <a href="/privacy" data-nav>Политикой конфиденциальности</a>, являющейся неотъемлемой частью отношений по Оферте.</p>

        <h2>10. Реквизиты Исполнителя</h2>
        <ul>
          <li>Наименование: <strong>[ООО «Меблио» / ИП — подставить]</strong></li>
          <li>ИНН: <strong>[указать]</strong></li>
          <li>ОГРН/ОГРНИП: <strong>[указать]</strong></li>
          <li>Адрес: <strong>[указать]</strong></li>
          <li>Email: <strong>[указать]</strong></li>
          <li>Сайт: mebl.io</li>
        </ul>
        <p>Реквизиты необходимо заполнить до публикации Оферты в боевой среде.</p>

        <h2>11. Применимое право и порядок разрешения споров</h2>
        <p>11.1. К отношениям применяется право Российской Федерации.</p>
        <p>11.2. Споры решаются в порядке претензии (письмо на email Исполнителя, срок ответа — 30 календарных дней) и, при недостижении согласия, в суде по месту нахождения Исполнителя или в ином порядке, предусмотренном законом.</p>
        <p>11.3. Если отдельные положения Оферты будут признаны недействительными, остальные положения сохраняют силу.</p>

        <h2>12. Порядок обращений</h2>
        <p>Вопросы по Оферте и функционированию Площадки: <strong>[email оператора]</strong>. Вопросы по персональным данным — см. Политику конфиденциальности.</p>
      </div>
    </div></section>`;
}

function tariffsView() {
  const plan = state.user?.plan || "free";
  const isPro = plan !== "free";
  return `
    <section class="section"><div class="container">
      <p class="breadcrumbs"><a href="/" data-nav>Главная</a> / Тарифы</p>
      <p class="eyebrow">Тарифы</p>
      <h1>Free и Pro</h1>
      <p class="lead">Оплата по тарифу — статусы счетов на площадке. Гарантия на работу — 14 дней после приёмки.</p>
      ${state.user ? `<p class="muted">Ваш текущий тариф: <strong class="badge ${isPro ? "badge-paid" : "badge-pending"}">${isPro ? "Pro" : "Free"}</strong></p>` : `<p class="muted">Войдите, чтобы переключить тариф.</p>`}
      <div class="tariff-grid">
        <article class="tariff-card ${!isPro ? "is-current" : ""}">
          <h2>Free</h2>
          <p class="tariff-price">0 ₽<span>/мес</span></p>
          <ul class="tariff-features">
            <li>5 откликов в месяц</li>
            <li>Заказы, чаты, счета</li>
            <li>Гарантия 14 дней</li>
            <li>Калькулятор сметы</li>
          </ul>
          ${state.user && isPro ? `<button class="button button-secondary" type="button" data-upgrade-plan="free">Перейти на Free</button>` : `<button class="button button-secondary" type="button" disabled>Текущий базовый</button>`}
        </article>
        <article class="tariff-card is-pro ${isPro ? "is-current" : ""}">
          <h2>Pro</h2>
          <p class="tariff-price">4 990 ₽<span>/мес</span></p>
          <ul class="tariff-features">
            <li>Безлимитные отклики</li>
            <li>Приоритет в каталоге</li>
            <li>КП и 비교ение откликов</li>
            <li>Гарантия 14 дней</li>
          </ul>
          ${state.user && !isPro ? `<button class="button button-primary" type="button" data-upgrade-plan="pro">Перейти на Pro</button>` : state.user ? `<button class="button button-secondary" type="button" disabled>Активен</button>` : `<button class="button button-primary" type="button" data-auth="login">Войти</button>`}
        </article>
      </div>
      <div class="panel" style="margin-top:24px">
        <h3>Что входит в сделку</h3>
        <p class="muted">Договор-HTML, счета со статусами, этапы и акт приёмки, верификация реквизитов, калькулятор сметы.</p>
        <a class="button button-secondary button-small" href="/offer" data-nav>Условия оферты</a>
      </div>
    </div></section>`;
}

function overview() {
  const myOrdersCount = state.orders.filter((o) => o.client_id === state.user.id).length;
  const myResponsesCount = state.orders.flatMap((o) => o.responses || []).filter((r) => r.maker_id === state.user.id).length;
  return `
    <div class="stats">
      <div class="stat-card"><strong>${state.user.role === "client" ? myOrdersCount : myResponsesCount}</strong><p>${state.user.role === "client" ? "ваших заказов" : "ваших откликов"}</p></div>
      <div class="stat-card"><strong>${state.threads.length}</strong><p>диалогов</p></div>
      <div class="stat-card"><strong>${state.orders.filter((o) => o.status === "open").length}</strong><p>открытых заказов</p></div>
    </div>
    <div class="panel">
      <h2>Быстрые действия</h2>
      <div class="actions">
        ${state.user.role === "client"
          ? '<button class="button button-primary" type="button" data-tab="new-order">Создать заказ</button><button class="button button-secondary" type="button" data-tab="my-orders">Мои заказы</button>'
          : '<button class="button button-primary" type="button" data-tab="available">Найти заказ</button><button class="button button-secondary" type="button" data-tab="responses">Мои отклики</button>'}
        <button class="button button-secondary" type="button" data-tab="chats">Сообщения</button>
        <a class="button button-secondary" href="/tariffs" data-nav>Тарифы</a>
      </div>
      ${state.user.role === "maker" ? `<p class="muted" style="margin-top:12px">Тариф: <strong>${escapeHtml(state.user.plan || "free")}</strong> · <a href="/tariffs" data-nav>изменить</a></p>` : ""}
    </div>`;
}

function newOrderForm() {
  return `
    <form class="stack-form grid-form" id="newOrderForm" enctype="multipart/form-data">
      <label>Тип проекта
        <select name="type" required>
          <option>Кухни и шкафы</option><option>Офисная мебель</option><option>HoReCa и retail</option><option>Серийные детали</option>
        </select>
      </label>
      <label>Название заказа <input name="title" placeholder="Например: мебель для шоурума" required></label>
      <label>Количество изделий <input name="quantity" type="number" min="1" value="10" required></label>
      <label>Город <input name="city" placeholder="Москва" required></label>
      <label>Бюджет, руб. <input name="budget" type="number" min="1" value="500000" required></label>
      <label>Срок <input name="deadline" placeholder="30 дней" required></label>
      <label class="full">Описание <textarea name="details" rows="5" placeholder="Материалы, размеры, монтаж, доставка" required></textarea></label>
      <label class="full">Файлы <input name="files" type="file" multiple></label>
      <label class="full"><input type="checkbox" name="is_draft" value="1"> Сохранить как черновик (виден только мне)</label>
      <button class="button button-primary full" type="submit">Опубликовать заказ</button>
    </form>`;
}

function myOrders() {
  let orders = state.orders.filter((o) => o.client_id === state.user.id && o.status !== "draft");
  const drafts = state.orders.filter((o) => o.client_id === state.user.id && o.status === "draft");
  const draftsBlock = drafts.length ? `
    <div class="panel" style="margin-bottom:16px">
      <h3>Черновики</h3>
      ${drafts.map((d) => `
        <div class="actions" style="justify-content:space-between">
          <span>${escapeHtml(d.title)} · ${money(d.budget)}</span>
          <button class="button button-primary button-small" type="button" data-publish-order="${d.id}">Опубликовать</button>
        </div>`).join("")}
    </div>` : "";
  return `<div class="order-list">${draftsBlock}${orders.length ? orders.map((o) => `${orderCard(o)}${responsesBlock(o)}`).join("") : (drafts.length ? "" : emptyState("Вы еще не создали заказ.", "Создать заказ", 'data-tab="new-order"'))}</div>`;
}

function responsesBlock(order) {
  if (!order.responses?.length) return "";
  const selected = state.compareResponses.filter((x) => x.orderId === order.id).map((x) => x.makerId);
  return `<div class="panel response-list" id="responses-${order.id}">
    <div class="admin-toolbar">
      <h3 style="margin:0">Отклики на "${escapeHtml(order.title)}"</h3>
      <div class="actions" style="margin:0">
        ${selected.length >= 2 ? `<button class="button button-primary button-small" type="button" data-compare-open="${order.id}">Сравнить (${selected.length})</button>` : ""}
        ${selected.length ? `<button class="button button-secondary button-small" type="button" data-compare-clear="${order.id}">Сбросить</button>` : ""}
      </div>
    </div>
    <p class="muted" style="margin:0 0 8px">Сортировка:
      <button class="hint link-button" type="button" data-sort-responses="${order.id}:price">по цене</button> ·
      <button class="hint link-button" type="button" data-sort-responses="${order.id}:days">по срокам</button>
    </p>
    ${order.responses.map((r) => `
      <article class="maker-card">
        <div class="maker-card-header">
          <div><h3>${escapeHtml(r.maker_name)}</h3><p>${escapeHtml(r.maker_city)} · ${r.days} дней · ${money(r.price)}</p></div>
          <div class="actions" style="margin:0">
            ${order.status === "open" ? `<label class="compare-check"><input type="checkbox" data-compare-toggle="${order.id}:${r.maker_id}" ${selected.includes(r.maker_id) ? "checked" : ""}> Сравнить</label>` : ""}
            ${order.selected_maker_id === r.maker_id ? '<span class="status status-progress">Выбран</span>' : ""}
          </div>
        </div>
        <p>${escapeHtml(r.message)}</p>
        <div class="actions">
          <button class="button button-secondary button-small" type="button" data-open-chat="${order.id}">Открыть чат</button>
          ${order.status === "open" ? `<button class="button button-primary button-small" type="button" data-choose-maker="${order.id}:${r.maker_id}">Выбрать</button>` : ""}
        </div>
      </article>`).join("")}
  </div>`;
}

function compareResponsesView(orderId) {
  const order = state.orders.find((o) => o.id === Number(orderId));
  const ids = state.compareResponses.filter((x) => x.orderId === Number(orderId)).map((x) => x.makerId);
  const rows = (order?.responses || []).filter((r) => ids.includes(r.maker_id));
  if (rows.length < 2) return emptyState("Выберите минимум два отклика для сравнения.");
  const metrics = [
    ["Компания", (r) => escapeHtml(r.maker_name)],
    ["Город", (r) => escapeHtml(r.maker_city || "—")],
    ["Цена", (r) => `<strong>${money(r.price)}</strong>`],
    ["Срок", (r) => `${r.days} дн.`],
    ["Цена/день", (r) => money(Math.round(r.price / Math.max(1, r.days)))],
    ["Сообщение", (r) => escapeHtml(r.message || "—")],
  ];
  return `
    <div class="panel">
      <div class="admin-toolbar">
        <h3 style="margin:0">Сравнение откликов</h3>
        <button class="button button-secondary button-small" type="button" data-close-compare>Закрыть</button>
      </div>
      <div class="compare-table-wrap">
        <table class="admin-table compare-table">
          <thead><tr><th>Параметр</th>${rows.map((r) => `<th>${escapeHtml(r.maker_name)}</th>`).join("")}</tr></thead>
          <tbody>
            ${metrics.map(([label, fmt]) => `
              <tr><td class="compare-label">${label}</td>${rows.map((r) => `<td>${fmt(r)}</td>`).join("")}</tr>
            `).join("")}
            <tr>
              <td class="compare-label">Действие</td>
              ${rows.map((r) => `
                <td>
                  <div class="actions">
                    <button class="button button-secondary button-small" type="button" data-open-chat="${order.id}">Чат</button>
                    ${order.status === "open" ? `<button class="button button-primary button-small" type="button" data-choose-maker="${order.id}:${r.maker_id}">Выбрать</button>` : ""}
                  </div>
                </td>`).join("")}
            </tr>
          </tbody>
        </table>
      </div>
    </div>`;
}

function availableOrders() {
  const orders = state.orders.filter((o) => o.status === "open" && !o.responses?.some((r) => r.maker_id === state.user.id));
  return `<div class="order-list">${orders.length ? orders.map((o) => orderCard(o)).join("") : emptyState("Новых заказов для отклика пока нет.")}</div>`;
}

function myResponses() {
  const orders = state.orders.filter((o) => o.responses?.some((r) => r.maker_id === state.user.id));
  return `<div class="order-list">${orders.length ? orders.map((o) => orderCard(o)).join("") : emptyState("Вы пока не оставляли отклики.")}</div>`;
}

async function loadMakerFunnel() {
  const data = await api("/api/maker/funnel");
  state.makerFunnel = data;
}

function makerFunnelView() {
  const f = state.makerFunnel;
  if (!f) return emptyState("Загрузка воронки…");
  const stageCards = f.stages.map((s, i) => `
    <div class="funnel-stage">
      <div class="funnel-stage-head">
        <span class="funnel-step">${i + 1}</span>
        <strong>${escapeHtml(s.label)}</strong>
        <span class="funnel-count">${s.orders.length}</span>
      </div>
      ${s.orders.length ? `
        <ul class="funnel-list">
          ${s.orders.map((o) => `
            <li>
              <span class="funnel-title">${escapeHtml(o.title)}</span>
              <span class="muted">${money(o.budget)} · ${escapeHtml(o.city || "—")}</span>
            </li>`).join("")}
        </ul>` : '<p class="muted funnel-empty">Пусто</p>'}
    </div>`).join("");
  return `
    <div class="panel">
      <h2 style="margin:0 0 8px">Воронка продаж</h2>
      <p class="muted" style="margin:0 0 16px">От доступных заказов до завершённых сделок.</p>
      <div class="funnel-grid">${stageCards}</div>
      <div class="actions" style="margin-top:16px">
        <button class="button button-secondary button-small" type="button" data-action="refresh-funnel">Обновить</button>
      </div>
    </div>`;
}

function myFavorites() {
  if (!state.favorites.length) return emptyState("Избранных компаний пока нет.", "Найти компании", 'data-view="companies"');
  return `
    <div class="order-list">
      ${state.favorites.map((f) => `
        <article class="maker-card" data-company-id="${f.company_id}" style="cursor:pointer">
          <div class="maker-card-header">
            <div>
              <h3>${escapeHtml(f.name)}</h3>
              <p>${escapeHtml(f.city)} ${f.region_name ? "· " + escapeHtml(f.region_name) : ""} · ${companyTypeLabel(f.company_type)}</p>
            </div>
            <button class="favorite-btn is-active" type="button" data-toggle-favorite="${f.company_id}" title="Убрать из избранного">\u2665</button>
          </div>
          <p>${escapeHtml(f.about || "")}</p>
        </article>
      `).join("")}
    </div>`;
}

function myServices() {
  return `
    <div class="panel">
      <div class="actions" style="margin-bottom:16px">
        <h2 style="margin:0">Мои услуги</h2>
        <button class="button button-primary button-small" type="button" data-action="add-service">Добавить услугу</button>
      </div>
      <div id="servicesList">${state.services.length ? state.services.map((s) => `
        <div class="service-item">
          <div class="service-item-header">
            <div><h3>${escapeHtml(s.title)}</h3><p class="muted">${escapeHtml(s.description)}</p></div>
            <div class="actions">
              <button class="button button-secondary button-small" type="button" data-edit-service="${s.id}">Ред.</button>
              <button class="button button-danger button-small" type="button" data-delete-service="${s.id}">Удал.</button>
            </div>
          </div>
        </div>`).join("") : emptyState("Услуг пока нет. Добавьте первую!", "Добавить услугу", 'data-action="add-service"')}</div>
    </div>
    <div id="serviceFormSlot"></div>`;
}

function serviceFormModal(service = null) {
  const isEdit = !!service;
  const params = (service && Array.isArray(service.params)) ? service.params : [];
  const paramRows = (params.length ? params : [{ name: "", value: "" }]).map((p) => `
    <div class="service-param-row">
      <input name="param_name" placeholder="Например: Материал" value="${escapeHtml(p.name || "")}" maxlength="80">
      <input name="param_value" placeholder="Значение" value="${escapeHtml(p.value || "")}" maxlength="200">
      <button class="button button-secondary button-small" type="button" data-action="remove-service-param" title="Удалить">✕</button>
    </div>`).join("");
  document.getElementById("serviceFormSlot").innerHTML = `
    <div class="modal is-open" id="serviceModal">
      <div class="modal-backdrop" data-close-service></div>
      <section class="modal-card">
        <button class="modal-close" type="button" data-close-service>x</button>
        <p class="eyebrow">${isEdit ? "Редактирование" : "Новая"} услуга</p>
        <h2>${isEdit ? escapeHtml(service.title) : "Добавить услугу"}</h2>
        <form class="stack-form" id="serviceForm">
          <label>Название <input name="title" value="${isEdit ? escapeHtml(service.title) : ""}" required></label>
          <label>Описание <textarea name="description" rows="3">${isEdit ? escapeHtml(service.description) : ""}</textarea></label>
          <label>Тип цены <input name="price_type" value="${isEdit ? escapeHtml(service.price_type || "") : ""}" placeholder="по проекту, от ... руб."></label>
          <div class="service-params-editor">
            <span class="muted">Параметры услуги</span>
            <div id="serviceParamRows">${paramRows}</div>
            <button class="button button-secondary button-small" type="button" data-action="add-service-param">+ Параметр</button>
          </div>
          <button class="button button-primary" type="submit">${isEdit ? "Сохранить" : "Добавить"}</button>
          ${isEdit && service.id ? `<button class="button button-secondary" type="button" data-edit-service="${service.id}" hidden>Сохранить</button>` : ""}
        </form>
      </section>
    </div>`;
}

function openProposalFormModal(orderId) {
  document.getElementById("serviceFormSlot")?.remove();
  document.body.insertAdjacentHTML("beforeend", `
    <div class="modal is-open" id="proposalModal">
      <div class="modal-backdrop" data-close-proposal></div>
      <section class="modal-card">
        <button class="modal-close" type="button" data-close-proposal>x</button>
        <p class="eyebrow">Коммерческое предложение</p>
        <h2>Новое КП</h2>
        <form class="stack-form" id="proposalForm" data-order-id="${orderId}">
          <label>Сумма, ₽ <input name="amount" type="number" min="1" required placeholder="150000"></label>
          <label>Срок, дней <input name="days" type="number" min="0" value="14"></label>
          <label>Комментарий <textarea name="message" rows="3" placeholder="Что входит, условия оплаты…"></textarea></label>
          <div class="proposal-items-editor">
            <span class="muted">Позиции (необязательно)</span>
            <div id="proposalItemRows">
              <div class="proposal-item-row">
                <input name="item_name" placeholder="Наименование">
                <input name="item_qty" type="number" min="1" value="1" placeholder="Кол-во">
                <input name="item_price" type="number" min="0" placeholder="Цена">
              </div>
            </div>
            <button class="button button-secondary button-small" type="button" data-action="add-proposal-item">+ Позиция</button>
          </div>
          <button class="button button-primary" type="submit">Отправить КП</button>
        </form>
      </section>
    </div>`);
}

async function openProposalsModal(orderId) {
  const data = await api(`/api/orders/${orderId}/proposals`);
  const proposals = data.proposals || [];
  document.getElementById("proposalListModal")?.remove();
  const canAct = state.user?.id && proposals.length && state.user.role === "client";
  document.body.insertAdjacentHTML("beforeend", `
    <div class="modal is-open" id="proposalListModal">
      <div class="modal-backdrop" data-close-proposal-list></div>
      <section class="modal-card">
        <button class="modal-close" type="button" data-close-proposal-list>x</button>
        <p class="eyebrow">Коммерческие предложения</p>
        <h2>КП по заказу #${orderId}</h2>
        ${proposals.length ? proposals.map((p) => {
          const items = Array.isArray(p.items) ? p.items : [];
          const isClient = state.user?.id && state.user.role === "client";
          return `
            <article class="proposal-card">
              <div class="proposal-card-head">
                <strong>${escapeHtml(p.maker_name || "")}</strong>
                <span class="badge badge-${p.status === "accepted" ? "paid" : p.status === "rejected" ? "cancelled" : "pending"}">${p.status === "accepted" ? "Принято" : p.status === "rejected" ? "Отклонено" : "Отправлено"}</span>
              </div>
              <p><strong>${money(p.amount)}</strong>${p.days ? ` · ${p.days} дн.` : ""}</p>
              ${p.message ? `<p class="muted">${escapeHtml(p.message)}</p>` : ""}
              ${items.length ? `<ul class="proposal-items">${items.map((it) => `<li>${escapeHtml(it.name)} × ${it.qty} · ${money(it.price * it.qty)}</li>`).join("")}</ul>` : ""}
              <small class="muted">${escapeHtml(p.created_at)}</small>
              ${isClient && p.status === "sent" ? `
                <div class="actions" style="margin-top:8px">
                  <button class="button button-primary button-small" type="button" data-proposal-status="${p.id}:accepted" data-proposal-order="${orderId}">Принять</button>
                  <button class="button button-secondary button-small" type="button" data-proposal-status="${p.id}:rejected" data-proposal-order="${orderId}">Отклонить</button>
                </div>` : ""}
              ${state.user?.id === p.maker_id && p.status === "rejected" ? `
                <div class="actions" style="margin-top:8px">
                  <button class="button button-secondary button-small" type="button" data-proposal-status="${p.id}:sent" data-proposal-order="${orderId}">Отправить снова</button>
                </div>` : ""}
            </article>`;
        }).join("") : `<p class="muted">КП пока нет.</p>`}
      </section>
    </div>`);
}

function profileForm() {
  const user = state.user;
  return `
    <form class="stack-form grid-form" id="profileForm">
      <label>Имя или компания <input name="name" value="${escapeHtml(user.name)}" required></label>
      <label>Город <input name="city" value="${escapeHtml(user.city)}" required></label>
      <label>Регион
        <select name="region_id">
          <option value="">Не указан</option>
          ${state.regions.map((r) => `<option value="${r.id}" ${user.region_id == r.id ? "selected" : ""}>${escapeHtml(r.name)}</option>`).join("")}
        </select>
      </label>
      <label>Телефон <input name="phone" value="${escapeHtml(user.phone || "")}" placeholder="+7"></label>
      <label>ИНН <input name="inn" value="${escapeHtml(user.inn || "")}" maxlength="12" placeholder="10 или 12 цифр"></label>
      <label>ОГРН <input name="ogrn" value="${escapeHtml(user.ogrn || "")}" maxlength="15" placeholder="13 или 15 цифр"></label>
      ${user.verified_requisites_at ? `<p class="full"><span class="badge badge-paid">✓ Реквизиты проверены ${escapeHtml(String(user.verified_requisites_at).slice(0, 10))}</span></p>` : ""}
      <label>Веб-сайт <input name="website" value="${escapeHtml(user.website || "")}" placeholder="https://example.ru"></label>
      <label class="full check-label">
        <input type="checkbox" name="is_public" ${user.is_public === 0 ? "" : "checked"}>
        Публичный профиль — показывать компанию в каталоге и поиске
      </label>
      ${user.role === "maker" ? `
        <label>Мощность <input name="capacity" value="${escapeHtml(user.capacity || "")}" placeholder="до 80 изделий в месяц"></label>
        <label class="full">Компетенции <input name="skills" value="${escapeHtml((user.skills || []).join(", "))}" placeholder="Кухни, шкафы, монтаж"></label>
      ` : '<input type="hidden" name="capacity" value=""><input type="hidden" name="skills" value="">'}
      <label class="full">Описание <textarea name="about" rows="4">${escapeHtml(user.about || "")}</textarea></label>
      <button class="button button-primary full" type="submit">Сохранить профиль</button>
    </form>
    ${user.role === "maker" ? `
      <h3 style="margin:24px 0 8px">Портфолио работ</h3>
      <form class="stack-form grid-form" id="galleryForm" enctype="multipart/form-data">
        <label class="full">Фото работ <input name="files" type="file" multiple accept="image/*"></label>
        <button class="button button-secondary full" type="submit">Загрузить в портфолио</button>
      </form>
      <div class="gallery-grid">${(state.gallery || []).map((g) => `
        <figure class="gallery-item">
          <img src="${g.url}" alt="${escapeHtml(g.name)}" class="gallery-img">
          <button class="button button-secondary button-small" type="button" data-del-gallery="${g.id}">Удалить</button>
        </figure>`).join("") || '<p class="muted">Портфолио пустое</p>'}
      </div>
    ` : ""}
    <h3 style="margin:24px 0 8px">Смена пароля</h3>
    <form class="stack-form grid-form" id="changePwForm">
      <label class="full">Текущий пароль <input name="old_password" type="password" required></label>
      <label class="full">Новый пароль <input name="new_password" type="password" minlength="6" placeholder="Не короче 6 символов" required></label>
      <button class="button button-secondary full" type="submit">Сменить пароль</button>
    </form>
    <h3 style="margin:24px 0 8px">Смена email</h3>
    <form class="stack-form grid-form" id="changeEmailForm">
      <label class="full">Новый email <input name="new_email" type="email" required></label>
      <label class="full">Текущий пароль <input name="password" type="password" required></label>
      <button class="button button-secondary full" type="submit">Сменить email</button>
    </form>
    <h3 style="margin:24px 0 8px; color: var(--danger, #dc2626)">Удаление аккаунта</h3>
    <form class="stack-form grid-form" id="deleteAccountForm">
      <p class="muted full">Аккаунт будет обезличен (заказы и отзывы сохранятся без ваших данных). Действие необратимо.</p>
      <label class="full">Пароль <input name="password" type="password" required></label>
      <button class="button button-secondary full" type="submit">Удалить аккаунт</button>
    </form>`;
}

function notificationsView() {
  const notifIcons = {
    new_order: '📦', response: '💬', message: '✉️', chosen: '✅',
    review: '⭐', order_status: '🔄', system: '🔔',
  };
  const notifLabels = {
    new_order: 'Новый заказ', response: 'Отклик', message: 'Сообщение',
    chosen: 'Выбран исполнителем', review: 'Отзыв', order_status: 'Статус заказа', system: 'Система',
  };
  return `
    <div class="panel">
      <div class="notif-header-actions">
        <h2 style="margin:0">Уведомления</h2>
        <div class="actions">
          <button class="button button-secondary button-small" type="button" data-action="notif-settings">⚙ Настройки</button>
          ${state.unreadCount ? `<button class="button button-secondary button-small" type="button" data-action="mark-all-read">✓ Все прочитано</button>` : ''}
          ${state.notifications.length ? `<button class="button button-danger button-small" type="button" data-action="clear-all-notifs">🗑 Очистить</button>` : ''}
        </div>
      </div>
      ${state.notifications.length ? `
        <div class="notif-filters">
          <button class="notif-filter-btn ${!state.notifFilter ? 'active' : ''}" data-notif-filter="">Все (${state.notifications.length})</button>
          <button class="notif-filter-btn ${state.notifFilter === 'unread' ? 'active' : ''}" data-notif-filter="unread">Непрочитанные (${state.unreadCount})</button>
          ${Object.entries(notifLabels).map(([k, v]) => {
            const count = state.notifications.filter(n => n.type === k).length;
            return count ? `<button class="notif-filter-btn ${state.notifFilter === k ? 'active' : ''}" data-notif-filter="${k}">${v} (${count})</button>` : '';
          }).join('')}
        </div>
        <div class="notifications-list">
          ${state.notifications.filter(n => {
            if (!state.notifFilter) return true;
            if (state.notifFilter === 'unread') return !n.is_read;
            return n.type === state.notifFilter;
          }).map(n => `
            <div class="notif-item ${n.is_read ? '' : 'unread'}" data-notif-id="${n.id}" ${n.link ? `data-notif-link="${escapeHtml(n.link)}"` : ''}>
              <div class="notif-icon">${notifIcons[n.type] || '🔔'}</div>
              <div class="notif-body">
                <div class="notif-title-row">
                  <strong>${escapeHtml(n.title)}</strong>
                  <span class="notif-type-badge">${notifLabels[n.type] || n.type}</span>
                </div>
                <p>${escapeHtml(n.body)}</p>
                <div class="notif-footer">
                  <small class="muted">${escapeHtml(n.created_at)}</small>
                  <button class="notif-delete-btn" type="button" data-delete-notif="${n.id}" title="Удалить">✕</button>
                </div>
              </div>
            </div>
          `).join('')}
        </div>
      ` : emptyState("Уведомлений пока нет.")}
    </div>`;
}

function notifPrefsView() {
  const p = state.notifPrefs;
  if (!p) return emptyState("Загрузка настроек...");
  return `
    <div class="panel">
      <div class="notif-header-actions">
        <h2 style="margin:0">Настройки уведомлений</h2>
        <button class="button button-secondary button-small" type="button" data-action="notif-back">← Назад</button>
      </div>
      <p class="muted" style="margin-bottom:16px">Выберите, какие уведомления вы хотите получать.</p>
      <form class="stack-form" id="notifPrefsForm">
        <div class="notif-pref-group">
          <h3>Типы уведомлений</h3>
          <label class="notif-pref-toggle">
            <input type="checkbox" name="new_order" ${p.new_order ? 'checked' : ''}>
            <span>📦 Новые заказы</span>
            <small>Уведомление о новых заказах на площадке</small>
          </label>
          <label class="notif-pref-toggle">
            <input type="checkbox" name="response" ${p.response ? 'checked' : ''}>
            <span>💬 Отклики</span>
            <small>Когда производитель откликнулся на ваш заказ</small>
          </label>
          <label class="notif-pref-toggle">
            <input type="checkbox" name="message" ${p.message ? 'checked' : ''}>
            <span>✉️ Сообщения</span>
            <small>Новые сообщения в чатах</small>
          </label>
          <label class="notif-pref-toggle">
            <input type="checkbox" name="chosen" ${p.chosen ? 'checked' : ''}>
            <span>✅ Выбор исполнителя</span>
            <small>Когда вас выбрали исполнителем</small>
          </label>
          <label class="notif-pref-toggle">
            <input type="checkbox" name="review" ${p.review ? 'checked' : ''}>
            <span>⭐ Отзывы</span>
            <small>Когда оставили отзыв на вашу компанию</small>
          </label>
          <label class="notif-pref-toggle">
            <input type="checkbox" name="order_status" ${p.order_status ? 'checked' : ''}>
            <span>🔄 Изменение статуса заказа</span>
            <small>Когда статус вашего заказа изменился</small>
          </label>
          <label class="notif-pref-toggle">
            <input type="checkbox" name="system" ${p.system ? 'checked' : ''}>
            <span>🔔 Системные</span>
            <small>Важные обновления платформы</small>
          </label>
        </div>
        <div class="notif-pref-group">
          <h3>Каналы доставки</h3>
          <label class="notif-pref-toggle">
            <input type="checkbox" name="push_enabled" ${p.push_enabled ? 'checked' : ''}>
            <span>🔔 Push-уведомления в браузере</span>
            <small>Всплывающие уведомления</small>
          </label>
          <label class="notif-pref-toggle">
            <input type="checkbox" name="email_enabled" ${p.email_enabled ? 'checked' : ''}>
            <span>📧 Email-уведомления</span>
            <small>Отправка на email (в разработке)</small>
          </label>
        </div>
        <div class="notif-pref-group">
          <h3>Мессенджеры</h3>
          <label>Telegram chat_id
            <input name="telegram_chat_id" value="${escapeHtml(state.user?.telegram_chat_id || "")}" placeholder="123456789" maxlength="64">
            <small class="muted">Нужен TELEGRAM_BOT_TOKEN на сервере. Узнайте chat_id у бота @userinfobot.</small>
          </label>
          <label>MAX chat_id
            <input name="max_chat_id" value="${escapeHtml(state.user?.max_chat_id || "")}" placeholder="max-chat-id" maxlength="64">
            <small class="muted">Нужен MAX_API_TOKEN на сервере.</small>
          </label>
        </div>
        <button class="button button-primary" type="submit">Сохранить настройки</button>
      </form>
    </div>`;
}

function chatView() {
  if (!state.threads.length) return emptyState("Диалогов пока нет. Они появятся после отклика или выбора исполнителя.");
  const active = state.threads.find((t) => t.id === state.activeThreadId) || state.threads[0];
  state.activeThreadId = active.id;
  return `
    <div class="chat-layout">
      <aside class="thread-list">
        ${state.threads.map((t) => `
          <button class="thread-button ${t.id === active.id ? "is-active" : ""}" type="button" data-thread="${t.id}">
            <strong>${escapeHtml(t.companion_name)}</strong><br>
            <span class="muted">${escapeHtml(t.order_title)}</span>
          </button>`).join("")}
      </aside>
      <section class="chat-box">
        <div class="chat-head"><h3>${escapeHtml(active.companion_name)}</h3><p class="muted">${escapeHtml(active.order_title)}</p></div>
        <div class="messages">
          ${state.messages.map((m) => `
            <div class="message ${m.author_id === state.user.id ? "mine" : ""}">
              ${escapeHtml(m.body)}
              ${(m.files || []).map((f) => `<div><a href="${f.url}" target="_blank" rel="noreferrer">📎 ${escapeHtml(f.name)}</a></div>`).join("")}
              <small>${escapeHtml(m.author_name)} · ${escapeHtml(m.created_at)}</small>
            </div>`).join("")}
        </div>
        <form class="chat-controls" id="chatForm">
          <input name="body" placeholder="Напишите сообщение">
          <input name="files" type="file" multiple title="Прикрепить файл" style="max-width:180px">
          <button class="button button-primary" type="submit">Отправить</button>
        </form>
      </section>
    </div>`;
}

function responseModal(orderId) {
  const order = state.orders.find((i) => i.id === Number(orderId));
  app.insertAdjacentHTML("beforeend", `
    <div class="modal is-open" id="responseModal">
      <div class="modal-backdrop" data-close-response></div>
      <section class="modal-card">
        <button class="modal-close" type="button" data-close-response>x</button>
        <p class="eyebrow">Отклик на заказ</p>
        <h2>${escapeHtml(order.title)}</h2>
        <form class="stack-form" id="responseForm" data-order-id="${order.id}">
          <label>Стоимость, руб. <input name="price" type="number" min="1" value="${Math.round(order.budget * 0.95)}" required></label>
          <label>Срок, дней <input name="days" type="number" min="1" value="30" required></label>
          <label>Комментарий <textarea name="message" rows="4" required>Готовы рассчитать проект и уточнить детали.</textarea></label>
          <button class="button button-primary" type="submit">Отправить отклик</button>
        </form>
      </section>
    </div>`);
}

async function refreshData() {
  await Promise.all([loadOrders(), loadMakers()]);
  if (state.user) {
    await Promise.all([loadThreads(), loadFavorites()]);
    if (state.dashboardTab === "chats") await loadMessages(state.activeThreadId);
    if (state.dashboardTab === "my-services" && state.user.role === "maker") {
      const data = await api(`/api/services?user_id=${state.user.id}`);
      state.services = data.services;
    }
    if (state.dashboardTab === "favorites") await loadFavorites();
    if (state.dashboardTab === "profile" && state.user.role === "maker") {
      try {
        const data = await api(`/api/companies/${state.user.id}`);
        state.gallery = data.company.gallery;
      } catch { state.gallery = []; }
    }
  }
}

async function render() {
  stopDeadlineTimers();
  renderHeader();
  const viewTitles = {
    home: "Meblio — площадка для заказчиков и производителей мебели",
    market: "Заказы — Meblio",
    companies: "Компании — Meblio",
    company: "Компания — Meblio",
    dashboard: "Личный кабинет — Meblio",
    notifications: "Уведомления — Meblio",
    privacy: "Политика конфиденциальности — Meblio",
    offer: "Публичная оферта — Meblio",
    tariffs: "Тарифы Free и Pro — Meblio",
  };
  document.title = viewTitles[state.view] || "Meblio";

  if (state.view === "home") {
    app.innerHTML = `<section class="hero"><div class="container">${skeletonCards(3)}</div></section>`;
    await refreshData();
    renderHeader();
    renderHome();
  } else if (state.view === "market") {
    app.innerHTML = `<section class="dashboard"><div class="container">${skeletonCards(3)}</div></section>`;
    await loadOrders();
    renderMarket();
  } else if (state.view === "companies") {
    await Promise.all([loadRegions(), loadCompanies()]);
    if (state.user) await loadFavorites();
    renderCompanies();
  } else if (state.view === "company") {
    app.innerHTML = `<section class="dashboard"><div class="container">${skeletonCards(2)}</div></section>`;
    const data = await api(`/api/companies/${state.activeCompanyId}`);
    renderCompanyProfile(data.company);
  } else if (state.view === "services") {
    renderAsync(servicesCatalogView());
  } else if (state.view === "service") {
    renderAsync(servicePageView(state.activeServiceId));
  } else if (state.view === "articles") {
    renderAsync(articlesListView());
  } else if (state.view === "article") {
    renderAsync(articlePageView(state.articleSlug));
  } else if (state.view === "privacy") {
    app.innerHTML = privacyView();
    startDeadlineTimers();
  } else if (state.view === "offer") {
    app.innerHTML = offerView();
    startDeadlineTimers();
  } else if (state.view === "tariffs") {
    renderAsync(tariffsView());
  } else if (state.view === "dashboard") {
    await Promise.all([loadOrders(), loadRegions()]);
    if (state.user?.role === "client") {
      try {
        const drafts = await api("/api/orders?status=draft");
        state.orders = state.orders.concat(drafts.orders);
      } catch {}
    }
    if (state.user?.role === "maker") {
      const data = await api(`/api/services?user_id=${state.user.id}`);
      state.services = data.services;
      if (state.dashboardTab === "funnel" && !state.makerFunnel) await loadMakerFunnel();
    }
    await loadThreads();
    renderDashboard();
    if (state.dashboardTab === "chats" && state.activeThreadId) subscribeThread(state.activeThreadId);
  } else if (state.view === "admin") {
    await Promise.all([loadAdminStats(), loadAdminUsers(), loadAdminOrders(), loadAdminServices()]);
    renderAdmin();
    if (state.adminTab === "analytics") {
      await loadAdminAnalytics();
      renderAdmin();
      setTimeout(renderAnalyticsCharts, 50);
    }
    if (state.adminTab === "reports") {
      await loadAdminReports();
      renderAdmin();
    }
  }
  startDeadlineTimers();
}

async function loadCompanies() {
  const f = state.companyFilters;
  const params = new URLSearchParams();
  if (f.type) params.set("type", f.type);
  if (f.region) params.set("region", f.region);
  if (f.search) params.set("search", f.search);
  const qs = params.toString();
  const data = await api(`/api/companies${qs ? `?${qs}` : ""}`);
  state.companies = data.companies;
}

async function loadFavorites() {
  if (!state.user) return;
  const data = await api("/api/favorites");
  state.favorites = data.favorites;
}

async function loadAdminStats() {
  const data = await api("/api/admin/stats");
  state.adminStats = data;
}

async function loadAdminAnalytics() {
  const data = await api("/api/admin/analytics");
  state.adminAnalytics = data;
}

async function loadAdminActivity() {
  const data = await api("/api/admin/activity");
  state.adminActivity = data.activity;
}

async function loadAdminUsers() {
  const f = state.adminUserFilters;
  const params = new URLSearchParams();
  if (f.role) params.set("role", f.role);
  if (f.search) params.set("search", f.search);
  const qs = params.toString();
  const data = await api(`/api/admin/users${qs ? `?${qs}` : ""}`);
  state.adminUsers = data.users;
}

async function loadAdminOrders() {
  const f = state.adminOrderFilters;
  const params = new URLSearchParams();
  if (f.status) params.set("status", f.status);
  if (f.search) params.set("search", f.search);
  const qs = params.toString();
  const data = await api(`/api/admin/orders${qs ? `?${qs}` : ""}`);
  state.adminOrders = data.orders;
}

async function loadAdminServices() {
  const data = await api("/api/admin/services");
  state.adminServices = data.services;
}

async function loadAdminReports() {
  const data = await api("/api/admin/reports?status=pending&page_size=100");
  state.adminReports = data.reports;
  state.adminReportsTotal = data.total;
}

function isFavorite(companyId) {
  return state.favorites.some((f) => f.company_id === companyId);
}

function renderAdmin() {
  const tabs = [
    ["overview", "Обзор"],
    ["analytics", "Аналитика"],
    ["users", "Пользователи"],
    ["orders", "Заказы"],
    ["services", "Услуги"],
    ["reports", "Жалобы"],
    ["activity", "Журнал"],
  ];
  app.innerHTML = `
    <section class="dashboard">
      <div class="container">
        <div class="dashboard-top">
          <div>
            <p class="eyebrow">Администрирование</p>
            <h1>Админ-панель Meblio</h1>
            <p class="lead">Управление пользователями, заказами, услугами и аналитика.</p>
          </div>
          <button class="button button-secondary" type="button" data-view="dashboard">В кабинет</button>
        </div>
        <div class="dashboard-grid">
          <aside class="sidebar">
            ${tabs.map(([id, label]) => `<button type="button" class="${state.adminTab === id ? "is-active" : ""}" data-admin-tab="${id}">${label}</button>`).join("")}
          </aside>
          <div class="content-stack">${adminContent()}</div>
        </div>
      </div>
    </section>`;
}

function adminContent() {
  if (state.adminTab === "users") return adminUsers();
  if (state.adminTab === "orders") return adminOrders();
  if (state.adminTab === "services") return adminServices();
  if (state.adminTab === "analytics") return adminAnalytics();
  if (state.adminTab === "reports") return adminReports();
  if (state.adminTab === "activity") return adminActivityLog();
  return adminOverview();
}

function adminReports() {
  const reports = state.adminReports || [];
  const statusLabels = { pending: "Ожидает", resolved: "Обработана", rejected: "Отклонена" };
  const typeLabels = { order: "Заказ", service: "Услуга", company: "Компания", review: "Отзыв", user: "Пользователь" };
  if (!reports.length) return emptyState("Жалоб нет.");
  return `
    <div class="panel">
      <div class="admin-toolbar">
        <h3 style="margin:0">Жалобы</h3>
        <span class="muted">Всего: ${state.adminReportsTotal ?? reports.length}</span>
      </div>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead><tr><th>#</th><th>Тип</th><th>ID</th><th>Причина</th><th>Отправил</th><th>Статус</th><th>Дата</th><th></th></tr></thead>
          <tbody>
            ${reports.map(r => `
              <tr>
                <td>${r.id}</td>
                <td>${typeLabels[r.target_type] || escapeHtml(r.target_type)}</td>
                <td>${r.target_id}</td>
                <td>${escapeHtml(r.reason)}</td>
                <td>${escapeHtml(r.reporter_name || "—")}</td>
                <td><span class="badge badge-${r.status === "pending" ? "pending" : r.status === "resolved" ? "paid" : "cancelled"}">${statusLabels[r.status] || escapeHtml(r.status)}</span></td>
                <td>${escapeHtml((r.created_at || "").slice(0, 16))}</td>
                <td>
                  ${r.status === "pending" ? `
                    <div class="actions">
                      <button class="button button-secondary button-small" type="button" data-report-resolve="${r.id}:rejected">Отклонить</button>
                      <button class="button button-primary button-small" type="button" data-report-resolve="${r.id}:resolved:hide">Принять + скрыть</button>
                    </div>` : "—"}
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
}

function adminOverview() {
  const s = state.adminStats;
  if (!s) return emptyState("Загрузка...");
  const budgetTotal = s.total_budget ? money(s.total_budget) : "0 руб.";
  const budgetAvg = s.avg_budget ? money(s.avg_budget) : "0 руб.";
  return `
    <div class="admin-kpi-grid">
      <div class="admin-kpi-card kpi-blue">
        <div class="kpi-icon">👥</div>
        <div class="kpi-info"><strong>${s.users}</strong><p>Пользователей</p></div>
        <div class="kpi-sub">+${s.new_users_week || 0} за неделю</div>
      </div>
      <div class="admin-kpi-card kpi-green">
        <div class="kpi-icon">📦</div>
        <div class="kpi-info"><strong>${s.orders}</strong><p>Заказов</p></div>
        <div class="kpi-sub">+${s.new_orders_week || 0} за неделю</div>
      </div>
      <div class="admin-kpi-card kpi-yellow">
        <div class="kpi-icon">💰</div>
        <div class="kpi-info"><strong>${budgetTotal}</strong><p>Общий бюджет</p></div>
        <div class="kpi-sub">Средний: ${budgetAvg}</div>
      </div>
      <div class="admin-kpi-card kpi-purple">
        <div class="kpi-icon">⭐</div>
        <div class="kpi-info"><strong>${s.avg_rating || "—"}</strong><p>Средний рейтинг</p></div>
        <div class="kpi-sub">${s.reviews_count || 0} отзывов</div>
      </div>
    </div>

    <div class="admin-charts-row">
      <div class="panel admin-chart-panel">
        <h3>Заказы по статусам</h3>
        <div class="admin-bar-chart">
          <div class="bar-row"><span class="bar-label">Открытые</span><div class="bar-track"><div class="bar-fill bar-open" style="width:${s.orders ? (s.open_orders/s.orders*100) : 0}%"></div></div><span class="bar-value">${s.open_orders}</span></div>
          <div class="bar-row"><span class="bar-label">В работе</span><div class="bar-track"><div class="bar-fill bar-progress" style="width:${s.orders ? (s.progress_orders/s.orders*100) : 0}%"></div></div><span class="bar-value">${s.progress_orders}</span></div>
          <div class="bar-row"><span class="bar-label">Завершены</span><div class="bar-track"><div class="bar-fill bar-closed" style="width:${s.orders ? (s.closed_orders/s.orders*100) : 0}%"></div></div><span class="bar-value">${s.closed_orders}</span></div>
        </div>
      </div>
      <div class="panel admin-chart-panel">
        <h3>Пользователи</h3>
        <div class="admin-bar-chart">
          <div class="bar-row"><span class="bar-label">Заказчики</span><div class="bar-track"><div class="bar-fill bar-client" style="width:${s.users ? (s.clients/s.users*100) : 0}%"></div></div><span class="bar-value">${s.clients}</span></div>
          <div class="bar-row"><span class="bar-label">Производители</span><div class="bar-track"><div class="bar-fill bar-maker" style="width:${s.users ? (s.makers/s.users*100) : 0}%"></div></div><span class="bar-value">${s.makers}</span></div>
        </div>
        <div class="admin-quick-stats">
          <span>📨 ${s.messages} сообщений</span>
          <span>💬 ${s.responses} откликов</span>
          <span>🛠 ${s.services} услуг</span>
        </div>
      </div>
    </div>

    <div class="panel">
      <h3>Быстрые действия</h3>
      <div class="actions">
        <button class="button button-primary button-small" type="button" data-admin-tab="users">Управление пользователями</button>
        <button class="button button-secondary button-small" type="button" data-admin-tab="orders">Управление заказами</button>
        <button class="button button-secondary button-small" type="button" data-admin-tab="analytics">Аналитика</button>
        <button class="button button-secondary button-small" type="button" data-admin-tab="activity">Журнал действий</button>
      </div>
    </div>`;
}

function adminAnalytics() {
  const a = state.adminAnalytics;
  if (!a) return emptyState("Загрузка аналитики...");
  const maxType = Math.max(1, ...Object.values(a.by_type || {}));
  const maxStatus = Math.max(1, ...Object.values(a.by_status || {}));
  const maxCity = Math.max(1, ...Object.values(a.by_city || {}).map(c => c.cnt));
  const maxRegion = Math.max(1, ...Object.values(a.by_region || {}).map(r => r.cnt));

  return `
    <!-- KPI Cards -->
    <div class="admin-kpi-grid">
      <div class="admin-kpi-card kpi-blue">
        <div class="kpi-icon">📊</div>
        <div class="kpi-info"><strong>${a.conversion_rate || 0}%</strong><p>Конверсия</p></div>
        <div class="kpi-sub">Заказы с откликами</div>
      </div>
      <div class="admin-kpi-card kpi-green">
        <div class="kpi-icon">✅</div>
        <div class="kpi-info"><strong>${a.completion_rate || 0}%</strong><p>Завершаемость</p></div>
        <div class="kpi-sub">Заказы выполнены</div>
      </div>
      <div class="admin-kpi-card kpi-yellow">
        <div class="kpi-icon">💬</div>
        <div class="kpi-info"><strong>${a.avg_responses || 0}</strong><p>Откликов/заказ</p></div>
        <div class="kpi-sub">Среднее количество</div>
      </div>
      <div class="admin-kpi-card kpi-purple">
        <div class="kpi-icon">✉️</div>
        <div class="kpi-info"><strong>${a.avg_messages_per_thread || 0}</strong><p>Сообщений/чат</p></div>
        <div class="kpi-sub">${a.total_messages || 0} всего</div>
      </div>
    </div>

    <!-- Charts Row 1 -->
    <div class="admin-charts-row">
      <div class="panel admin-chart-panel">
        <h3>Заказы по типам</h3>
        <div class="admin-bar-chart">
          ${Object.entries(a.by_type || {}).map(([k, v]) => `
            <div class="bar-row"><span class="bar-label">${escapeHtml(k)}</span><div class="bar-track"><div class="bar-fill bar-type" style="width:${(v/maxType*100)}%"></div></div><span class="bar-value">${v}</span></div>
          `).join("") || '<p class="muted">Нет данных</p>'}
        </div>
        ${a.avg_budget_by_type?.length ? `
        <div style="margin-top:16px">
          <h4 style="margin:0 0 8px;font-size:13px;color:var(--muted)">Средний бюджет по типам</h4>
          ${a.avg_budget_by_type.map(t => `
            <div class="bar-row"><span class="bar-label">${escapeHtml(t.type)}</span><div class="bar-track"><div class="bar-fill bar-budget" style="width:${(t.avg_budget/Math.max(...a.avg_budget_by_type.map(x=>x.avg_budget))*100)}%"></div></div><span class="bar-value">${money(t.avg_budget)}</span></div>
          `).join("")}
        </div>` : ''}
      </div>
      <div class="panel admin-chart-panel">
        <h3>Статусы заказов</h3>
        <div class="admin-bar-chart">
          ${Object.entries(a.by_status || {}).map(([k, v]) => `
            <div class="bar-row"><span class="bar-label">${statusLabel(k)}</span><div class="bar-track"><div class="bar-fill bar-status-${k}" style="width:${(v/maxStatus*100)}%"></div></div><span class="bar-value">${v}</span></div>
          `).join("") || '<p class="muted">Нет данных</p>'}
        </div>
        <div class="admin-donut-chart" id="statusDonut"></div>
      </div>
    </div>

    <!-- Charts Row 2 -->
    <div class="admin-charts-row">
      <div class="panel admin-chart-panel">
        <h3>Заказы по городам</h3>
        <div class="admin-bar-chart">
          ${(a.by_city || []).slice(0, 8).map(c => `
            <div class="bar-row"><span class="bar-label">${escapeHtml(c.city)}</span><div class="bar-track"><div class="bar-fill bar-city" style="width:${(c.cnt/maxCity*100)}%"></div></div><span class="bar-value">${c.cnt} (${money(c.total_budget)})</span></div>
          `).join("") || '<p class="muted">Нет данных</p>'}
        </div>
      </div>
      <div class="panel admin-chart-panel">
        <h3>Заказы по регионам</h3>
        <div class="admin-bar-chart">
          ${(a.by_region || []).slice(0, 8).map(r => `
            <div class="bar-row"><span class="bar-label">${escapeHtml(r.region)}</span><div class="bar-track"><div class="bar-fill bar-region" style="width:${(r.cnt/maxRegion*100)}%"></div></div><span class="bar-value">${r.cnt}</span></div>
          `).join("") || '<p class="muted">Нет данных</p>'}
        </div>
      </div>
    </div>

    <!-- Charts Row 3 -->
    <div class="admin-charts-row">
      <div class="panel admin-chart-panel">
        <h3>Активность по часам</h3>
        <div class="admin-hour-chart" id="hourChart"></div>
      </div>
      <div class="panel admin-chart-panel">
        <h3>Активность по дням недели</h3>
        <div class="admin-dow-chart" id="dowChart"></div>
      </div>
    </div>

    <!-- Charts Row 4 -->
    <div class="admin-charts-row">
      <div class="panel admin-chart-panel">
        <h3>Типы производителей</h3>
        <div class="admin-bar-chart">
          ${Object.entries(a.by_company || {}).map(([k, v]) => `
            <div class="bar-row"><span class="bar-label">${companyTypeLabel(k)}</span><div class="bar-track"><div class="bar-fill bar-company" style="width:${(v/Math.max(1,...Object.values(a.by_company||{}))*100)}%"></div></div><span class="bar-value">${v}</span></div>
          `).join("") || '<p class="muted">Нет данных</p>'}
        </div>
      </div>
      <div class="panel admin-chart-panel">
        <h3>Топ производителей по откликам</h3>
        ${a.top_makers?.length ? a.top_makers.map((m, i) => `
          <div class="top-maker-row">
            <span class="top-rank">#${i+1}</span>
            <span class="top-name">${escapeHtml(m.name)}</span>
            <span class="top-count">${m.cnt} откликов</span>
          </div>
        `).join("") : '<p class="muted">Нет данных</p>'}
        <h4 style="margin:16px 0 8px;font-size:13px;color:var(--muted)">Топ заказчиков</h4>
        ${(a.top_clients || []).map((c, i) => `
          <div class="top-maker-row">
            <span class="top-rank">#${i+1}</span>
            <span class="top-name">${escapeHtml(c.name)}</span>
            <span class="top-count">${c.cnt} заказов · ${money(c.total)}</span>
          </div>
        `).join("")}
      </div>
    </div>

    <!-- Revenue Table -->
    ${a.revenue?.length ? `
    <div class="panel">
      <div class="admin-toolbar">
        <h3 style="margin:0">Выручка по месяцам</h3>
        <button class="button button-secondary button-small" type="button" data-action="export-analytics">📥 Экспорт CSV</button>
      </div>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead><tr><th>Месяц</th><th>Заказов</th><th>Сумма бюджетов</th><th>Средний бюджет</th></tr></thead>
          <tbody>
            ${a.revenue.map(r => `<tr><td>${escapeHtml(r.month)}</td><td>${r.count}</td><td>${money(r.total)}</td><td>${money(Math.round(r.total / r.count))}</td></tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>` : ''}

    <!-- Growth Charts -->
    <div class="admin-charts-row">
      <div class="panel admin-chart-panel">
        <h3>Рост пользователей</h3>
        <div class="admin-growth-chart" id="userGrowthChart"></div>
      </div>
      <div class="panel admin-chart-panel">
        <h3>Рост заказов</h3>
        <div class="admin-growth-chart" id="orderGrowthChart"></div>
      </div>
    </div>

    <!-- Recent Activity -->
    <div class="admin-charts-row">
      <div class="panel admin-chart-panel">
        <h3>Последние заказы</h3>
        ${a.recent_orders?.length ? a.recent_orders.map(o => `
          <div class="recent-item">
            <span class="${statusClass(o.status)}">${statusLabel(o.status)}</span>
            <span class="recent-title">${escapeHtml(o.title)}</span>
            <span class="recent-meta">${money(o.budget)}</span>
          </div>
        `).join("") : '<p class="muted">Нет данных</p>'}
      </div>
      <div class="panel admin-chart-panel">
        <h3>Последние пользователи</h3>
        ${a.recent_users?.length ? a.recent_users.map(u => `
          <div class="recent-item">
            <span class="badge">${roleLabel(u.role)}</span>
            <span class="recent-title">${escapeHtml(u.name)}</span>
            <span class="recent-meta">${escapeHtml(u.email)}</span>
          </div>
        `).join("") : '<p class="muted">Нет данных</p>'}
      </div>
    </div>

    <!-- Platform Stats Summary -->
    <div class="panel">
      <h3>Сводка платформы</h3>
      <div class="stats">
        <div class="stat-card"><strong>${a.services_count || 0}</strong><p>услуг</p></div>
        <div class="stat-card"><strong>${a.services_with_files || 0}</strong><p>с файлами</p></div>
        <div class="stat-card"><strong>${a.active_threads || 0}</strong><p>активных чатов</p></div>
        <div class="stat-card"><strong>${a.total_messages || 0}</strong><p>сообщений</p></div>
      </div>
    </div>`;
}

function renderAnalyticsCharts() {
  const a = state.adminAnalytics;
  if (!a) return;

  // Hour chart
  const hourEl = document.getElementById("hourChart");
  if (hourEl) {
    const hours = a.by_hour || {};
    const maxH = Math.max(1, ...Object.values(hours));
    let html = '<div class="hour-bars">';
    for (let i = 0; i < 24; i++) {
      const v = hours[String(i)] || 0;
      const pct = (v / maxH * 100);
      html += `<div class="hour-bar-wrap"><div class="hour-bar" style="height:${pct}%"></div><span class="hour-label">${i}</span></div>`;
    }
    html += '</div>';
    hourEl.innerHTML = html;
  }

  // Day of week chart
  const dowEl = document.getElementById("dowChart");
  if (dowEl) {
    const dows = a.by_dow || {};
    const dayNames = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
    const maxD = Math.max(1, ...Object.values(dows));
    let html = '<div class="dow-bars">';
    for (let i = 0; i < 7; i++) {
      const v = dows[String(i)] || 0;
      const pct = (v / maxD * 100);
      html += `<div class="dow-bar-wrap"><div class="dow-bar" style="height:${pct}%"></div><span class="dow-label">${dayNames[i]}</span></div>`;
    }
    html += '</div>';
    dowEl.innerHTML = html;
  }

  // User growth chart
  const userGrowthEl = document.getElementById("userGrowthChart");
  if (userGrowthEl && a.user_growth?.length) {
    userGrowthEl.innerHTML = renderGrowthChart(a.user_growth.reverse(), "count");
  }

  // Order growth chart
  const orderGrowthEl = document.getElementById("orderGrowthChart");
  if (orderGrowthEl && a.order_growth?.length) {
    orderGrowthEl.innerHTML = renderGrowthChart(a.order_growth.reverse(), "count");
  }
}

function renderGrowthChart(data, valueKey) {
  if (!data.length) return '<p class="muted">Нет данных</p>';
  const max = Math.max(1, ...data.map(d => d[valueKey]));
  const width = 100;
  const height = 80;
  const points = data.map((d, i) => {
    const x = (i / (data.length - 1 || 1)) * width;
    const y = height - (d[valueKey] / max) * height;
    return `${x},${y}`;
  });
  const areaPoints = points.join(` ${width},${height} 0,${height}`);
  return `
    <div class="growth-chart-container">
      <svg viewBox="0 0 ${width} ${height + 10}" class="growth-svg">
        <defs>
          <linearGradient id="grad" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" style="stop-color:var(--primary);stop-opacity:0.3"/>
            <stop offset="100%" style="stop-color:var(--primary);stop-opacity:0.05"/>
          </linearGradient>
        </defs>
        <polygon points="${areaPoints}" fill="url(#grad)"/>
        <polyline points="${points.join(' ')}" fill="none" stroke="var(--primary)" stroke-width="1.5"/>
        ${data.map((d, i) => {
          const x = (i / (data.length - 1 || 1)) * width;
          const y = height - (d[valueKey] / max) * height;
          return `<circle cx="${x}" cy="${y}" r="2" fill="var(--primary)"/>`;
        }).join('')}
      </svg>
      <div class="growth-labels">
        <span>${data[0]?.month || ''}</span>
        <span>${data[data.length-1]?.month || ''}</span>
      </div>
      <div class="growth-values">
        <span>${data[0]?.count || 0}</span>
        <span>${data[data.length-1]?.count || 0}</span>
      </div>
    </div>`;
}

function exportAnalyticsCSV() {
  const a = state.adminAnalytics;
  if (!a) return;
  let csv = "Метрика,Значение\n";
  csv += `Всего заказов,${a.by_status ? Object.values(a.by_status).reduce((s,v)=>s+v,0) : 0}\n`;
  csv += `Конверсия,${a.conversion_rate || 0}%\n`;
  csv += `Завершаемость,${a.completion_rate || 0}%\n`;
  csv += `Средних откликов на заказ,${a.avg_responses || 0}\n`;
  csv += `Сообщений,${a.total_messages || 0}\n`;
  csv += `Активных чатов,${a.active_threads || 0}\n`;
  csv += "\nЗаказы по типам\n";
  Object.entries(a.by_type || {}).forEach(([k,v]) => csv += `${k},${v}\n`);
  csv += "\nЗаказы по городам\n";
  (a.by_city || []).forEach(c => csv += `${c.city},${c.cnt},${c.total_budget}\n`);
  csv += "\nВыручка по месяцам\n";
  (a.revenue || []).forEach(r => csv += `${r.month},${r.count},${r.total}\n`);
  csv += "\nТоп производителей\n";
  (a.top_makers || []).forEach((m,i) => csv += `#${i+1},${m.name},${m.cnt}\n`);
  const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `meblio-analytics-${new Date().toISOString().slice(0,10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
  showToast("CSV экспортирован", "success");
}

// === Estimate calculator ===
function estimateView() {
  const mats = state.materials || [];
  return `
    <div class="panel">
      <h2 style="margin:0 0 8px">Калькулятор сметы</h2>
      <p class="muted" style="margin-bottom:16px">Ориентировочная стоимость корпусной мебели: материал, раскрой, сборка и фурнитура. Итог уточняется с исполнителем.</p>
      <form class="stack-form grid-form" id="estimateForm">
        <label>Ширина, мм <input name="width" type="number" min="100" max="10000" value="600" required></label>
        <label>Высота, мм <input name="height" type="number" min="100" max="10000" value="2000" required></label>
        <label>Глубина, мм <input name="depth" type="number" min="100" max="10000" value="400" required></label>
        <label>Количество, шт <input name="qty" type="number" min="1" max="500" value="1" required></label>
        <label>Материал
          <select name="material_id">
            <option value="">Свой материал</option>
            ${mats.map(m => `<option value="${m.id}">${escapeHtml(m.name)} — ${money(m.price_per_m2)}/м²</option>`).join("")}
          </select>
        </label>
        <label>Цена материала, руб/м² <input name="material_price" type="number" min="0" value="1200" ${mats.length ? "" : ""}></label>
        <label>Сложность
          <select name="complexity">
            <option value="simple">Простая</option>
            <option value="medium" selected>Средняя</option>
            <option value="complex">Сложная</option>
          </select>
        </label>
        <label>Фурнитура
          <select name="hardware">
            <option value="basic">Базовая</option>
            <option value="standard" selected>Стандарт</option>
            <option value="premium">Премиум</option>
          </select>
        </label>
        <button class="button button-primary full" type="submit">Рассчитать</button>
      </form>
      <div id="estimateResult" class="estimate-result" hidden></div>
    </div>`;
}

async function runEstimate(form) {
  const data = Object.fromEntries(new FormData(form));
  const body = {
    width: Number(data.width),
    height: Number(data.height),
    depth: Number(data.depth),
    qty: Number(data.qty),
    complexity: data.complexity,
    hardware: data.hardware,
    material_price: Number(data.material_price) || 0,
    material_name: data.material_id ? "" : "Свой материал",
  };
  if (data.material_id) body.material_id = Number(data.material_id);
  const result = await api("/api/estimate", { method: "POST", body: JSON.stringify(body) });
  const box = document.getElementById("estimateResult");
  if (!box) return;
  box.hidden = false;
  box.innerHTML = `
    <h3>Предварительная смета</h3>
    <dl class="requisites">
      <div><dt>Площадь</dt><dd>${result.area_m2} м²</dd></div>
      <div><dt>Материал</dt><dd>${escapeHtml(result.material_name)} — ${money(result.material_price)}/м²</dd></div>
      <div><dt>Материалы</dt><dd>${money(result.material_cost)}</dd></div>
      <div><dt>Раскрой и сборка</dt><dd>${money(result.assembly_cost)}</dd></div>
      <div><dt>Фурнитура</dt><dd>${money(result.hardware_cost)}</dd></div>
      <div><dt>За единицу</dt><dd><strong>${money(result.unit_cost)}</strong></dd></div>
      <div><dt>Итого (${result.qty} шт.)</dt><dd><strong>${money(result.total)}</strong></dd></div>
      <div><dt>Гарантия</dt><dd>${result.warranty_days} дней после приёмки</dd></div>
    </dl>`;
}

// === Materials Catalog ===
function materialsView() {
  const cats = { ldsp: "ЛДСП", mdf: "МДФ", other: "Другое" };
  return `
    <div class="panel">
      <div class="admin-toolbar">
        <h2 style="margin:0">Каталог материалов</h2>
        ${state.user?.role === "admin" ? `<button class="button button-primary button-small" type="button" data-action="add-material">+ Добавить</button>` : ''}
      </div>
      <div class="notif-filters">
        <button class="notif-filter-btn ${!state.materialFilter ? 'active' : ''}" data-material-filter="">Все</button>
        ${Object.entries(cats).map(([k, v]) => `<button class="notif-filter-btn ${state.materialFilter === k ? 'active' : ''}" data-material-filter="${k}">${v}</button>`).join('')}
      </div>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead><tr><th>Название</th><th>Категория</th><th>Цена/м²</th><th>Толщина</th><th>Цвет</th><th>Бренд</th>${state.user?.role === "admin" ? '<th></th>' : ''}</tr></thead>
          <tbody>
            ${state.materials.map(m => `
              <tr>
                <td><strong>${escapeHtml(m.name)}</strong><br><small class="muted">${escapeHtml(m.description)}</small></td>
                <td><span class="badge">${cats[m.category] || m.category}</span></td>
                <td>${money(m.price_per_m2)}</td>
                <td>${m.thickness_mm} мм</td>
                <td>${escapeHtml(m.color)}</td>
                <td>${escapeHtml(m.brand)}</td>
                ${state.user?.role === "admin" ? `<td class="admin-actions"><button class="button button-danger button-small" type="button" data-delete-material="${m.id}">Удал.</button></td>` : ''}
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
}

// === Order Templates ===
function templatesView() {
  return `
    <div class="panel">
      <div class="admin-toolbar">
        <h2 style="margin:0">Шаблоны заказов</h2>
        <button class="button button-primary button-small" type="button" data-action="add-template">+ Создать шаблон</button>
      </div>
      ${state.templates.length ? `
        <div class="order-list">
          ${state.templates.map(t => `
            <article class="order-card">
              <div class="order-card-header">
                <div>
                  <h3>${escapeHtml(t.name)}</h3>
                  <p>${escapeHtml(t.type)} · ${t.quantity} шт. · ${escapeHtml(t.city || "Любой город")}</p>
                </div>
                <div class="order-card-right">
                  <strong>${money(t.budget)}</strong>
                  <span class="muted">${escapeHtml(t.deadline)}</span>
                </div>
              </div>
              ${t.details ? `<p class="muted">${escapeHtml(t.details)}</p>` : ''}
              <div class="actions">
                <button class="button button-primary button-small" type="button" data-use-template="${t.id}">Использовать</button>
                <button class="button button-secondary button-small" type="button" data-edit-template="${t.id}">Ред.</button>
                <button class="button button-danger button-small" type="button" data-delete-template="${t.id}">Удал.</button>
              </div>
            </article>
          `).join("")}
        </div>
      ` : emptyState("Шаблонов пока нет. Создайте первый!", "Создать шаблон", 'data-action="add-template"')}
    </div>`;
}

// === Invoices ===
function invoicesView() {
  const statusLabels = { pending: "Ожидает оплаты", paid: "Оплачен", cancelled: "Отменён" };
  return `
    <div class="panel">
      <h2 style="margin:0 0 16px">Счета</h2>
      ${state.invoices.length ? `
        <div class="admin-table-wrap">
          <table class="admin-table">
            <thead><tr><th>#</th><th>Заказ</th><th>От кого</th><th>Кому</th><th>Сумма</th><th>Статус</th><th>Срок</th><th></th></tr></thead>
            <tbody>
              ${state.invoices.map(inv => `
                <tr>
                  <td>${inv.id}</td>
                  <td>${escapeHtml(inv.order_title)}</td>
                  <td>${escapeHtml(inv.from_name)}</td>
                  <td>${escapeHtml(inv.to_name)}</td>
                  <td><strong>${money(inv.amount)}</strong></td>
                  <td><span class="badge badge-${inv.status}">${statusLabels[inv.status] || inv.status}</span></td>
                  <td>${escapeHtml(inv.due_date || "—")}</td>
                  <td><button class="button button-secondary button-small" type="button" data-view-invoice="${inv.id}">Просмотр</button></td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      ` : emptyState("Счетов пока нет.")}
    </div>`;
}

function invoiceDetailView(inv) {
  const items = JSON.parse(inv.items || "[]");
  return `
    <div class="panel">
      <div class="admin-toolbar">
        <h2 style="margin:0">Счёт #${inv.id}</h2>
        <button class="button button-secondary button-small" type="button" data-action="print-invoice">🖨 Печать</button>
      </div>
      <div class="invoice-doc" id="invoiceDoc">
        <div class="invoice-header">
          <div><strong>Meblio</strong><br><small class="muted">Площадка для заказа мебели</small></div>
          <div style="text-align:right"><strong>Счёт #${inv.id}</strong><br><small class="muted">${escapeHtml(inv.created_at)}</small></div>
        </div>
        <div class="invoice-parties">
          <div><strong>Поставщик:</strong><br>${escapeHtml(inv.from_name)}<br>${escapeHtml(inv.from_email || "")}<br>${escapeHtml(inv.from_phone || "")}</div>
          <div><strong>Заказчик:</strong><br>${escapeHtml(inv.to_name)}<br>${escapeHtml(inv.to_email || "")}<br>${escapeHtml(inv.to_phone || "")}</div>
        </div>
        <div class="invoice-ref">Заказ: <strong>${escapeHtml(inv.order_title)}</strong> (ID: ${inv.order_id})</div>
        <table class="admin-table" style="margin-top:16px">
          <thead><tr><th>Позиция</th><th>Количество</th><th>Цена</th><th>Сумма</th></tr></thead>
          <tbody>
            ${items.length ? items.map((it, i) => `
              <tr><td>${escapeHtml(it.name || `Позиция ${i+1}`)}</td><td>${it.qty || 1}</td><td>${money(it.price || 0)}</td><td>${money((it.qty || 1) * (it.price || 0))}</td></tr>
            `).join("") : `<tr><td colspan="3">Заказ</td><td><strong>${money(inv.amount)}</strong></td></tr>`}
          </tbody>
        </table>
        <div class="invoice-total">Итого: <strong>${money(inv.amount)}</strong></div>
        <div class="invoice-footer">
          <p>Статус: <span class="badge badge-${inv.status}">${{pending:"Ожидает оплаты",paid:"Оплачен",cancelled:"Отменён"}[inv.status] || inv.status}</span></p>
          ${inv.due_date ? `<p>Срок оплаты: ${escapeHtml(inv.due_date)}</p>` : ''}
          ${state.user && (state.user.id === inv.from_user_id || state.user.id === inv.to_user_id || state.user.role === "admin") && inv.status === "pending" ? `
            <div class="actions" style="margin-top:12px">
              <button class="button button-primary button-small" type="button" data-invoice-status="${inv.id}:paid">Отметить оплаченным</button>
              <button class="button button-secondary button-small" type="button" data-invoice-status="${inv.id}:cancelled">Отменить счёт</button>
            </div>` : ""}
          ${state.user && (state.user.id === inv.from_user_id || state.user.id === inv.to_user_id || state.user.role === "admin") && inv.status === "paid" ? `
            <div class="actions" style="margin-top:12px">
              <button class="button button-secondary button-small" type="button" data-invoice-status="${inv.id}:cancelled">Отменить счёт</button>
            </div>` : ""}
        </div>
      </div>
    </div>`;
}

// === Delivery Tracking ===
function deliveryTrackingView(orderId) {
  const statuses = { production: "В производстве", ready: "Готов к отгрузке", shipped: "Отгружен", delivering: "В доставке", delivered: "Доставлен" };
  const statusIcons = { production: "🏭", ready: "📦", shipped: "🚚", delivering: "🛣", delivered: "✅" };
  return `
    <div class="panel">
      <h3>Отслеживание доставки (Заказ #${orderId})</h3>
      ${state.deliveryStatuses.length ? `
        <div class="delivery-timeline">
          ${state.deliveryStatuses.map(d => `
            <div class="delivery-item">
              <div class="delivery-icon">${statusIcons[d.status] || "📍"}</div>
              <div class="delivery-info">
                <strong>${statuses[d.status] || d.status}</strong>
                ${d.location ? `<span class="muted"> · ${escapeHtml(d.location)}</span>` : ''}
                ${d.notes ? `<p class="muted">${escapeHtml(d.notes)}</p>` : ''}
                <small class="muted">${escapeHtml(d.created_at)}</small>
              </div>
            </div>
          `).join("")}
        </div>
      ` : '<p class="muted">Информация о доставке отсутствует.</p>'}
      ${state.user ? `
        <div style="margin-top:12px">
          <h4>Добавить статус</h4>
          <form class="stack-form" id="deliveryForm" data-order-id="${orderId}">
            <label>Статус
              <select name="status">
                ${Object.entries(statuses).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}
              </select>
            </label>
            <label>Местоположение <input name="location" placeholder="Москва, склад"></label>
            <label>Заметки <textarea name="notes" rows="2"></textarea></label>
            <button class="button button-primary button-small" type="submit">Добавить</button>
          </form>
        </div>
      ` : ''}
    </div>`;
}

// === Order History ===
function orderHistoryView(orderId) {
  const fieldLabels = { status: "Статус", selected_maker_id: "Исполнитель", title: "Название", budget: "Бюджет", details: "Описание" };
  return `
    <div class="panel">
      <h3>История изменений (Заказ #${orderId})</h3>
      ${state.orderHistory.length ? `
        <div class="admin-table-wrap">
          <table class="admin-table">
            <thead><tr><th>Дата</th><th>Пользователь</th><th>Поле</th><th>Было</th><th>Стало</th></tr></thead>
            <tbody>
              ${state.orderHistory.map(h => `
                <tr>
                  <td>${escapeHtml(h.created_at)}</td>
                  <td>${escapeHtml(h.user_name)}</td>
                  <td><span class="badge">${fieldLabels[h.field] || h.field}</span></td>
                  <td class="muted">${escapeHtml(h.old_value || "—")}</td>
                  <td><strong>${escapeHtml(h.new_value || "—")}</strong></td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      ` : '<p class="muted">История изменений пуста.</p>'}
    </div>`;
}

// === 2FA Setup ===
// === Suppliers Catalog ===
function suppliersView() {
  return `
    <div class="panel">
      <div class="admin-toolbar">
        <h2 style="margin:0">Каталог поставщиков</h2>
        <div class="actions">
          <input id="supplierSearch" placeholder="Поиск по названию, материалам, городу..." value="${escapeHtml(state.supplierSearch || "")}">
        </div>
      </div>
      ${state.suppliers.length ? `
        <div class="order-list">
          ${state.suppliers.map(s => `
            <article class="maker-card">
              <div class="maker-card-header">
                <div>
                  <h3>${escapeHtml(s.name)}</h3>
                  <p>${escapeHtml(s.city)} · ${escapeHtml(s.contact_name)}</p>
                </div>
                ${s.rating ? `<span class="badge">⭐ ${s.rating}</span>` : ''}
              </div>
              <p>${escapeHtml(s.description || "Описание отсутствует.")}</p>
              <ul class="chips">${s.materials.split(",").map(m => `<li>${escapeHtml(m.trim())}</li>`).join("")}</ul>
              <div class="meta-row">
                <span>📧 ${escapeHtml(s.email)}</span>
                <span>📞 ${escapeHtml(s.phone)}</span>
                ${s.website ? `<span>🌐 <a href="${escapeHtml(s.website)}" target="_blank">Сайт</a></span>` : ''}
              </div>
            </article>
          `).join("")}
        </div>
      ` : emptyState("Поставщиков пока нет.")}
    </div>`;
}

// === Certificates ===
function certificatesView() {
  const certTypes = { quality: "Качество", safety: "Безопасность", iso: "ISO", other: "Другое" };
  return `
    <div class="panel">
      <div class="admin-toolbar">
        <h2 style="margin:0">Сертификаты компании</h2>
        <button class="button button-primary button-small" type="button" data-action="add-certificate">+ Добавить</button>
      </div>
      ${state.certificates.length ? `
        <div class="admin-table-wrap">
          <table class="admin-table">
            <thead><tr><th>Название</th><th>Тип</th><th>Номер</th><th>Кем выдан</th><th>Дата выдачи</th><th>Срок действия</th><th></th></tr></thead>
            <tbody>
              ${state.certificates.map(c => `
                <tr>
                  <td><strong>${escapeHtml(c.name)}</strong></td>
                  <td><span class="badge">${certTypes[c.cert_type] || c.cert_type}</span></td>
                  <td>${escapeHtml(c.number || "—")}</td>
                  <td>${escapeHtml(c.issued_by || "—")}</td>
                  <td>${escapeHtml(c.issued_at || "—")}</td>
                  <td>${escapeHtml(c.expires_at || "Бессрочно")}</td>
                  <td class="admin-actions">
                    ${c.stored_name ? `<a href="/uploads/${c.stored_name}" target="_blank" class="button button-secondary button-small">📄</a>` : ''}
                    <button class="button button-danger button-small" type="button" data-delete-certificate="${c.id}">Удал.</button>
                  </td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      ` : emptyState("Сертификатов пока нет. Добавьте первый!", "Добавить", 'data-action="add-certificate"')}
    </div>`;
}

// === Time Tracking ===
function timeTrackingView() {
  return `
    <div class="panel">
      <div class="admin-toolbar">
        <h2 style="margin:0">Трекинг времени</h2>
        <div><strong>Всего:</strong> ${state.totalHours || 0} ч.</div>
      </div>
      <div style="margin-bottom:16px">
        <h4>Добавить запись</h4>
        <form class="stack-form grid-form" id="timeEntryForm">
          <label>Заказ (ID) <input name="order_id" type="number" min="1" required></label>
          <label>Задача <input name="task" placeholder="Проектирование, раскрой..." required></label>
          <label>Часы <input name="hours" type="number" min="0.5" step="0.5" value="1" required></label>
          <label>Дата <input name="date" type="date" value="${new Date().toISOString().slice(0,10)}"></label>
          <label class="full">Заметки <input name="notes" placeholder="Опционально"></label>
          <button class="button button-primary full" type="submit">Добавить</button>
        </form>
      </div>
      ${state.timeEntries.length ? `
        <div class="admin-table-wrap">
          <table class="admin-table">
            <thead><tr><th>Дата</th><th>Заказ</th><th>Задача</th><th>Часы</th><th>Заметки</th><th></th></tr></thead>
            <tbody>
              ${state.timeEntries.map(e => `
                <tr>
                  <td>${escapeHtml(e.date)}</td>
                  <td>${e.order_title ? escapeHtml(e.order_title) : `#${e.order_id}`}</td>
                  <td>${escapeHtml(e.task)}</td>
                  <td><strong>${e.hours} ч.</strong></td>
                  <td class="muted">${escapeHtml(e.notes || "")}</td>
                  <td><button class="button button-danger button-small" type="button" data-delete-time-entry="${e.id}">✕</button></td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      ` : '<p class="muted">Записей пока нет.</p>'}
    </div>`;
}

// === Client Rating Form ===
function clientRatingModal(orderId, clientId) {
  app.insertAdjacentHTML("beforeend", `
    <div class="modal is-open" id="clientRatingModal">
      <div class="modal-backdrop" data-close-admin-modal></div>
      <section class="modal-card">
        <button class="modal-close" type="button" data-close-admin-modal>x</button>
        <p class="eyebrow">Оценка заказчика</p>
        <h2>Оценить заказчика</h2>
        <form class="stack-form" id="clientRatingForm" data-order-id="${orderId}" data-client-id="${clientId}">
          <label>Рейтинг
            <div class="star-rating interactive" id="clientRatingStars">
              ${[1,2,3,4,5].map(i => `<span class="star" data-star="${i}">★</span>`).join('')}
            </div>
            <input type="hidden" name="rating" value="5" id="clientRatingValue">
          </label>
          <label>Комментарий <textarea name="text" rows="3" placeholder="Опишите опыт работы..."></textarea></label>
          <button class="button button-primary" type="submit">Отправить оценку</button>
        </form>
      </section>
    </div>`);
}

function tfaSetupView() {
  return `
    <div class="panel">
      <h2 style="margin:0 0 16px">Двухфакторная аутентификация (2FA)</h2>
      ${state.tfaEnabled ? `
        <div class="tfa-status tfa-enabled">
          <span class="tfa-icon">🔒</span>
          <div><strong>2FA включена</strong><p class="muted">Ваш аккаунт защищён двухфакторной аутентификацией.</p></div>
        </div>
        <button class="button button-danger button-small" type="button" data-action="disable-tfa">Отключить 2FA</button>
      ` : `
        <div class="tfa-status tfa-disabled">
          <span class="tfa-icon">🔓</span>
          <div><strong>2FA отключена</strong><p class="muted">Включите двухфакторную аутентификацию для дополнительной защиты.</p></div>
        </div>
        <div id="tfaSetupContent">
          <button class="button button-primary" type="button" data-action="setup-tfa">Настроить 2FA</button>
        </div>
      `}
    </div>`;
}

function adminActivityLog() {
  if (!state.adminActivity.length) return emptyState("Журнал действий пуст.");
  const actionLabels = {
    create_user: "Создал пользователя",
    update_user: "Обновил пользователя",
    delete_user: "Удалил пользователя",
    update_order_status: "Изменил статус заказа",
    delete_order: "Удалил заказ",
    delete_service: "Удалил услугу",
  };
  return `
    <div class="panel">
      <h2>Журнал действий администратора</h2>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead><tr><th>Дата</th><th>Админ</th><th>Действие</th><th>Объект</th><th>Детали</th></tr></thead>
          <tbody>
            ${state.adminActivity.map(a => `
              <tr>
                <td>${escapeHtml(a.created_at)}</td>
                <td>${escapeHtml(a.admin_name)}</td>
                <td><span class="badge">${actionLabels[a.action] || a.action}</span></td>
                <td>${a.target_type ? `${a.target_type} #${a.target_id || ""}` : "—"}</td>
                <td>${escapeHtml(a.details || "")}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
}

function adminUsers() {
  return `
    <div class="panel">
      <div class="admin-toolbar">
        <h2 style="margin:0">Пользователи (${state.adminUsers.length})</h2>
        <button class="button button-primary button-small" type="button" data-action="admin-create-user">+ Создать</button>
      </div>
      <div class="admin-filters">
        <label>Роль
          <select id="adminUserRoleFilter">
            <option value="">Все роли</option>
            <option value="client" ${state.adminUserFilters.role === "client" ? "selected" : ""}>Заказчик</option>
            <option value="maker" ${state.adminUserFilters.role === "maker" ? "selected" : ""}>Производитель</option>
            <option value="admin" ${state.adminUserFilters.role === "admin" ? "selected" : ""}>Админ</option>
          </select>
        </label>
        <label>Поиск
          <input id="adminUserSearch" placeholder="Имя или email" value="${escapeHtml(state.adminUserFilters.search)}">
        </label>
      </div>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead><tr><th>ID</th><th>Имя</th><th>Email</th><th>Роль</th><th>Тип</th><th>Город</th><th>Дата</th><th></th></tr></thead>
          <tbody>
            ${state.adminUsers.map((u) => `
              <tr>
                <td>${u.id}</td>
                <td><a href="#" data-admin-view-user="${u.id}" class="admin-link">${escapeHtml(u.name)}</a></td>
                <td>${escapeHtml(u.email)}</td>
                <td><span class="badge badge-${u.role}">${u.role === "admin" ? "Админ" : roleLabel(u.role)}</span></td>
                <td>${escapeHtml(u.company_type || "—")}</td>
                <td>${escapeHtml(u.city)}</td>
                <td>${escapeHtml(u.created_at)}</td>
                <td class="admin-actions">
                  <button class="button button-secondary button-small" type="button" data-admin-edit-user="${u.id}">Ред.</button>
                  <button class="button button-danger button-small" type="button" data-admin-delete-user="${u.id}">Удал.</button>
                </td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
}

function adminOrders() {
  return `
    <div class="panel">
      <h2 style="margin:0 0 16px">Заказы (${state.adminOrders.length})</h2>
      <div class="admin-filters">
        <label>Статус
          <select id="adminOrderStatusFilter">
            <option value="">Все статусы</option>
            <option value="open" ${state.adminOrderFilters.status === "open" ? "selected" : ""}>Открыт</option>
            <option value="progress" ${state.adminOrderFilters.status === "progress" ? "selected" : ""}>В работе</option>
            <option value="closed" ${state.adminOrderFilters.status === "closed" ? "selected" : ""}>Завершен</option>
          </select>
        </label>
        <label>Поиск
          <input id="adminOrderSearch" placeholder="Название заказа" value="${escapeHtml(state.adminOrderFilters.search)}">
        </label>
      </div>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead><tr><th>ID</th><th>Название</th><th>Тип</th><th>Статус</th><th>Бюджет</th><th>Город</th><th>Заказчик</th><th>Исполнитель</th><th>Дата</th><th></th></tr></thead>
          <tbody>
            ${state.adminOrders.map((o) => `
              <tr>
                <td>${o.id}</td>
                <td><a href="#" data-admin-view-order="${o.id}" class="admin-link">${escapeHtml(o.title)}</a></td>
                <td>${escapeHtml(o.type)}</td>
                <td><span class="${statusClass(o.status)}">${statusLabel(o.status)}</span></td>
                <td>${money(o.budget)}</td>
                <td>${escapeHtml(o.city)}</td>
                <td>${escapeHtml(o.client_name)}</td>
                <td>${o.selected_maker_name ? escapeHtml(o.selected_maker_name) : '<span class="muted">—</span>'}</td>
                <td>${escapeHtml(o.created_at)}</td>
                <td class="admin-actions">
                  <select class="admin-status-select" data-admin-order-status="${o.id}" ${o.status === "closed" ? "disabled" : ""}>
                    <option value="open" ${o.status === "open" ? "selected" : ""}>Открыт</option>
                    <option value="progress" ${o.status === "progress" ? "selected" : ""}>В работе</option>
                    <option value="closed" ${o.status === "closed" ? "selected" : ""}>Завершен</option>
                  </select>
                  <button class="button button-danger button-small" type="button" data-admin-delete-order="${o.id}">Удал.</button>
                </td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
}

function adminServices() {
  return `
    <div class="panel">
      <h2>Услуги (${state.adminServices.length})</h2>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead><tr><th>ID</th><th>Название</th><th>Компания</th><th>Цена</th><th></th></tr></thead>
          <tbody>
            ${state.adminServices.map((s) => `
              <tr>
                <td>${s.id}</td>
                <td>${escapeHtml(s.title)}</td>
                <td>${escapeHtml(s.company_name)}</td>
                <td>${escapeHtml(s.price_type || "—")}</td>
                <td class="admin-actions">
                  <button class="button button-danger button-small" type="button" data-admin-delete-service="${s.id}">Удал.</button>
                </td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
}

function adminEditUserModal(userId) {
  const user = state.adminUsers.find((u) => u.id === Number(userId));
  if (!user) return;
  app.insertAdjacentHTML("beforeend", `
    <div class="modal is-open" id="adminUserModal">
      <div class="modal-backdrop" data-close-admin-modal></div>
      <section class="modal-card">
        <button class="modal-close" type="button" data-close-admin-modal>x</button>
        <p class="eyebrow">Редактирование пользователя</p>
        <h2>${escapeHtml(user.name)}</h2>
        <form class="stack-form" id="adminUserForm" data-user-id="${user.id}">
          <label>Имя <input name="name" value="${escapeHtml(user.name)}" required></label>
          <label>Email <input value="${escapeHtml(user.email)}" disabled></label>
          <label>Роль
            <select name="role">
              <option value="client" ${user.role === "client" ? "selected" : ""}>Заказчик</option>
              <option value="maker" ${user.role === "maker" ? "selected" : ""}>Производитель</option>
              <option value="admin" ${user.role === "admin" ? "selected" : ""}>Админ</option>
            </select>
          </label>
          <label>Город <input name="city" value="${escapeHtml(user.city)}"></label>
          <label>Телефон <input name="phone" value="${escapeHtml(user.phone || "")}"></label>
          <button class="button button-primary" type="submit">Сохранить</button>
        </form>
      </section>
    </div>`);
}

function showCertificateForm(cert = null) {
  const isEdit = !!cert;
  app.insertAdjacentHTML("beforeend", `
    <div class="modal is-open" id="certificateModal">
      <div class="modal-backdrop" data-close-admin-modal></div>
      <section class="modal-card">
        <button class="modal-close" type="button" data-close-admin-modal>x</button>
        <p class="eyebrow">${isEdit ? "Редактирование" : "Новый"} сертификат</p>
        <h2>${isEdit ? escapeHtml(cert.name) : "Добавить сертификат"}</h2>
        <form class="stack-form" id="certificateForm" ${isEdit ? `data-edit-cert="${cert.id}"` : ''} enctype="multipart/form-data">
          <label>Название <input name="name" value="${isEdit ? escapeHtml(cert.name) : ""}" placeholder="Сертификат ISO 9001" required></label>
          <label>Тип
            <select name="cert_type">
              <option value="quality" ${isEdit && cert.cert_type === "quality" ? "selected" : ""}>Качество</option>
              <option value="safety" ${isEdit && cert.cert_type === "safety" ? "selected" : ""}>Безопасность</option>
              <option value="iso" ${isEdit && cert.cert_type === "iso" ? "selected" : ""}>ISO</option>
              <option value="other" ${isEdit && cert.cert_type === "other" ? "selected" : ""}>Другое</option>
            </select>
          </label>
          <label>Номер <input name="number" value="${isEdit ? escapeHtml(cert.number || "") : ""}" placeholder="№12345"></label>
          <label>Кем выдан <input name="issued_by" value="${isEdit ? escapeHtml(cert.issued_by || "") : ""}" placeholder="Орган по сертификации"></label>
          <label>Дата выдачи <input name="issued_at" type="date" value="${isEdit ? escapeHtml(cert.issued_at || "") : ""}"></label>
          <label>Срок действия <input name="expires_at" type="date" value="${isEdit ? escapeHtml(cert.expires_at || "") : ""}"></label>
          ${!isEdit ? '<label>Файл <input name="file" type="file" accept=".pdf,.jpg,.jpeg,.png"></label>' : ''}
          <button class="button button-primary" type="submit">${isEdit ? "Сохранить" : "Добавить"}</button>
        </form>
      </section>
    </div>`);
}

function showMaterialForm(material = null) {
  const isEdit = !!material;
  app.insertAdjacentHTML("beforeend", `
    <div class="modal is-open" id="materialModal">
      <div class="modal-backdrop" data-close-admin-modal></div>
      <section class="modal-card">
        <button class="modal-close" type="button" data-close-admin-modal>x</button>
        <p class="eyebrow">${isEdit ? "Редактирование" : "Новый"} материал</p>
        <h2>${isEdit ? escapeHtml(material.name) : "Добавить материал"}</h2>
        <form class="stack-form" id="materialForm">
          <label>Название <input name="name" value="${isEdit ? escapeHtml(material.name) : ""}" required></label>
          <label>Категория
            <select name="category">
              <option value="ldsp" ${isEdit && material.category === "ldsp" ? "selected" : ""}>ЛДСП</option>
              <option value="mdf" ${isEdit && material.category === "mdf" ? "selected" : ""}>МДФ</option>
              <option value="other" ${isEdit && material.category === "other" ? "selected" : ""}>Другое</option>
            </select>
          </label>
          <label>Цена за м², руб. <input name="price_per_m2" type="number" min="0" value="${isEdit ? material.price_per_m2 : ""}" required></label>
          <label>Толщина, мм <input name="thickness_mm" type="number" min="1" value="${isEdit ? material.thickness_mm : 18}" required></label>
          <label>Цвет <input name="color" value="${isEdit ? escapeHtml(material.color) : ""}"></label>
          <label>Бренд <input name="brand" value="${isEdit ? escapeHtml(material.brand) : ""}"></label>
          <label>Описание <textarea name="description" rows="2">${isEdit ? escapeHtml(material.description) : ""}</textarea></label>
          <button class="button button-primary" type="submit">${isEdit ? "Сохранить" : "Добавить"}</button>
        </form>
      </section>
    </div>`);
}

function showTemplateForm(template = null) {
  const isEdit = !!template;
  app.insertAdjacentHTML("beforeend", `
    <div class="modal is-open" id="templateModal">
      <div class="modal-backdrop" data-close-admin-modal></div>
      <section class="modal-card">
        <button class="modal-close" type="button" data-close-admin-modal>x</button>
        <p class="eyebrow">${isEdit ? "Редактирование" : "Новый"} шаблон</p>
        <h2>${isEdit ? escapeHtml(template.name) : "Создать шаблон заказа"}</h2>
        <form class="stack-form" id="templateForm" ${isEdit ? `data-edit-template-form="${template.id}"` : ''}>
          <label>Название шаблона <input name="name" value="${isEdit ? escapeHtml(template.name) : ""}" placeholder="Кухни для гостиниц" required></label>
          <label>Тип проекта
            <select name="type">
              ${["Кухни и шкафы", "Офисная мебель", "HoReCa и retail", "Серийные детали"].map(t => `<option ${isEdit && template.type === t ? "selected" : ""}>${t}</option>`).join("")}
            </select>
          </label>
          <label>Количество <input name="quantity" type="number" min="1" value="${isEdit ? template.quantity : 10}"></label>
          <label>Город <input name="city" value="${isEdit ? escapeHtml(template.city) : ""}" placeholder="Москва"></label>
          <label>Бюджет, руб. <input name="budget" type="number" min="0" value="${isEdit ? template.budget : 500000}"></label>
          <label>Срок <input name="deadline" value="${isEdit ? escapeHtml(template.deadline) : ""}" placeholder="30 дней"></label>
          <label>Описание <textarea name="details" rows="3">${isEdit ? escapeHtml(template.details) : ""}</textarea></label>
          <button class="button button-primary" type="submit">${isEdit ? "Сохранить" : "Создать"}</button>
        </form>
      </section>
    </div>`);
}

function showInvoiceDetail(inv) {
  app.insertAdjacentHTML("beforeend", `
    <div class="modal is-open" id="invoiceModal">
      <div class="modal-backdrop" data-close-admin-modal></div>
      <section class="modal-card modal-card-wide">
        <button class="modal-close" type="button" data-close-admin-modal>x</button>
        ${invoiceDetailView(inv)}
      </section>
    </div>`);
}

function showDeliveryTracking(orderId) {
  app.insertAdjacentHTML("beforeend", `
    <div class="modal is-open" id="deliveryModal">
      <div class="modal-backdrop" data-close-admin-modal></div>
      <section class="modal-card modal-card-wide">
        <button class="modal-close" type="button" data-close-admin-modal>x</button>
        ${deliveryTrackingView(orderId)}
      </section>
    </div>`);
}

function showOrderHistory(orderId) {
  app.insertAdjacentHTML("beforeend", `
    <div class="modal is-open" id="historyModal">
      <div class="modal-backdrop" data-close-admin-modal></div>
      <section class="modal-card modal-card-wide">
        <button class="modal-close" type="button" data-close-admin-modal>x</button>
        ${orderHistoryView(orderId)}
      </section>
    </div>`);
}

function adminCreateUserModal() {
  app.insertAdjacentHTML("beforeend", `
    <div class="modal is-open" id="adminCreateUserModal">
      <div class="modal-backdrop" data-close-admin-modal></div>
      <section class="modal-card">
        <button class="modal-close" type="button" data-close-admin-modal>x</button>
        <p class="eyebrow">Новый пользователь</p>
        <h2>Создать пользователя</h2>
        <form class="stack-form" id="adminCreateUserForm">
          <label>Роль
            <select name="role" required>
              <option value="client">Заказчик</option>
              <option value="maker">Производитель</option>
              <option value="admin">Админ</option>
            </select>
          </label>
          <label>Имя или компания <input name="name" required placeholder="Название"></label>
          <label>Email <input name="email" type="email" required placeholder="mail@example.ru"></label>
          <label>Пароль <input name="password" type="password" required minlength="6" placeholder="Минимум 6 символов"></label>
          <label>Город <input name="city" placeholder="Москва"></label>
          <label>Телефон <input name="phone" placeholder="+7"></label>
          <label>Тип компании
            <select name="company_type">
              <option value="client">Заказчик</option>
              <option value="manufacturer">Производитель</option>
              <option value="designer">Проектировщик</option>
              <option value="serial">Серийное производство</option>
              <option value="supplier">Поставщик</option>
            </select>
          </label>
          <button class="button button-primary" type="submit">Создать</button>
        </form>
      </section>
    </div>`);
}

async function adminViewUser(userId) {
  try {
    const data = await api(`/api/admin/users/${userId}`);
    const u = data.user;
    const skills = u.skills ? u.skills.split(",").map(s => s.trim()).filter(Boolean) : [];
    app.insertAdjacentHTML("beforeend", `
      <div class="modal is-open" id="adminUserDetailModal">
        <div class="modal-backdrop" data-close-admin-modal></div>
        <section class="modal-card modal-card-wide">
          <button class="modal-close" type="button" data-close-admin-modal>x</button>
          <p class="eyebrow">Профиль пользователя</p>
          <h2>${escapeHtml(u.name)}</h2>
          <div class="admin-user-detail-grid">
            <div>
              <div class="detail-field"><strong>Email:</strong> ${escapeHtml(u.email)}</div>
              <div class="detail-field"><strong>Роль:</strong> <span class="badge badge-${u.role}">${u.role === "admin" ? "Админ" : roleLabel(u.role)}</span></div>
              <div class="detail-field"><strong>Тип:</strong> ${companyTypeLabel(u.company_type)}</div>
              <div class="detail-field"><strong>Город:</strong> ${escapeHtml(u.city)} ${u.region_name ? "· " + escapeHtml(u.region_name) : ""}</div>
              <div class="detail-field"><strong>Телефон:</strong> ${escapeHtml(u.phone || "не указан")}</div>
              <div class="detail-field"><strong>Зарегистрирован:</strong> ${escapeHtml(u.created_at)}</div>
              ${skills.length ? `<div class="detail-field"><strong>Компетенции:</strong> <ul class="chips">${skills.map(s => `<li>${escapeHtml(s)}</li>`).join("")}</ul></div>` : ""}
              ${u.about ? `<div class="detail-field"><strong>О себе:</strong> <p>${escapeHtml(u.about)}</p></div>` : ""}
              ${u.capacity ? `<div class="detail-field"><strong>Мощность:</strong> ${escapeHtml(u.capacity)}</div>` : ""}
            </div>
            <div>
              <div class="stat-card"><strong>${u.orders_count || 0}</strong><p>заказов</p></div>
              <div class="stat-card"><strong>${u.responses_count || 0}</strong><p>откликов</p></div>
            </div>
          </div>
          <div class="actions" style="margin-top:16px">
            <button class="button button-secondary button-small" type="button" data-close-admin-modal>Закрыть</button>
            <button class="button button-primary button-small" type="button" data-admin-edit-user="${u.id}">Редактировать</button>
          </div>
        </section>
      </div>`);
  } catch (e) { showToast(e.message); }
}

async function adminViewOrder(orderId) {
  try {
    const data = await api(`/api/admin/orders/${orderId}`);
    const o = data.order;
    app.insertAdjacentHTML("beforeend", `
      <div class="modal is-open" id="adminOrderDetailModal">
        <div class="modal-backdrop" data-close-admin-modal></div>
        <section class="modal-card modal-card-wide">
          <button class="modal-close" type="button" data-close-admin-modal>x</button>
          <p class="eyebrow">Заказ #${o.id}</p>
          <h2>${escapeHtml(o.title)}</h2>
          <div class="admin-user-detail-grid">
            <div>
              <div class="detail-field"><strong>Тип:</strong> ${escapeHtml(o.type)}</div>
              <div class="detail-field"><strong>Статус:</strong> <span class="${statusClass(o.status)}">${statusLabel(o.status)}</span></div>
              <div class="detail-field"><strong>Бюджет:</strong> ${money(o.budget)}</div>
              <div class="detail-field"><strong>Количество:</strong> ${o.quantity} шт.</div>
              <div class="detail-field"><strong>Город:</strong> ${escapeHtml(o.city)}</div>
              <div class="detail-field"><strong>Срок:</strong> ${escapeHtml(o.deadline)}</div>
              <div class="detail-field"><strong>Заказчик:</strong> ${escapeHtml(o.client_name)}</div>
              <div class="detail-field"><strong>Дата создания:</strong> ${escapeHtml(o.created_at)}</div>
              ${o.details ? `<div class="detail-field"><strong>Описание:</strong> <p>${escapeHtml(o.details)}</p></div>` : ""}
            </div>
            <div>
              <div class="stat-card"><strong>${o.files?.length || 0}</strong><p>файлов</p></div>
              <div class="stat-card"><strong>${o.responses?.length || 0}</strong><p>откликов</p></div>
            </div>
          </div>
          ${o.files?.length ? `
            <div style="margin-top:12px"><strong>Файлы:</strong>
              <ul class="chips">${o.files.map(f => `<li><a href="${f.url || '#'}" target="_blank">${escapeHtml(f.original_name || f.name)}</a></li>`).join("")}</ul>
            </div>` : ""}
          ${o.responses?.length ? `
            <div style="margin-top:12px"><strong>Отклики:</strong>
              ${o.responses.map(r => `
                <div class="maker-card" style="margin-top:8px">
                  <div class="maker-card-header">
                    <div><h3>${escapeHtml(r.maker_name)}</h3><p>${money(r.price)} · ${r.days} дней</p></div>
                  </div>
                  <p>${escapeHtml(r.message)}</p>
                </div>
              `).join("")}
            </div>` : ""}
          <div class="actions" style="margin-top:16px">
            <button class="button button-secondary button-small" type="button" data-close-admin-modal>Закрыть</button>
          </div>
        </section>
      </div>`);
  } catch (e) { showToast(e.message); }
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button, [data-nav], [data-close-modal], [data-close-response], [data-close-service], [data-company-id]");
  if (!target) return;
  try {
    if (target.dataset.view) return setView(target.dataset.view);
    if (target.dataset.nav !== undefined) {
      event.preventDefault();
      return navigate(target.getAttribute("href"));
    }
    if (target.dataset.auth) {
      if (target.dataset.role) registerForm.elements.role.value = target.dataset.role;
      return openAuth(target.dataset.auth);
    }
    if (target.dataset.closeModal !== undefined) return closeAuth();
    if (target.dataset.closeResponse !== undefined) return document.querySelector("#responseModal")?.remove();
    if (target.dataset.closeService !== undefined) return document.querySelector("#serviceModal")?.remove();
    if (target.dataset.closeProposal !== undefined) return document.querySelector("#proposalModal")?.remove();
    if (target.dataset.closeProposalList !== undefined) return document.querySelector("#proposalListModal")?.remove();
    if (target.dataset.action === "add-proposal-item") {
      const rows = document.getElementById("proposalItemRows");
      if (rows) {
        rows.insertAdjacentHTML("beforeend", `
          <div class="proposal-item-row">
            <input name="item_name" placeholder="Наименование">
            <input name="item_qty" type="number" min="1" value="1" placeholder="Кол-во">
            <input name="item_price" type="number" min="0" placeholder="Цена">
          </div>`);
      }
      return;
    }
    if (target.dataset.verifyRequisites) {
      const [userId, flag] = target.dataset.verifyRequisites.split(":");
      await api("/api/admin/verify-requisites", {
        method: "POST",
        body: JSON.stringify({ user_id: Number(userId), verified: flag === "1" }),
      });
      showToast(flag === "1" ? "Реквизиты подтверждены" : "Отметка снята", "success");
      if (state.activeCompanyId) {
        const data = await api(`/api/companies/${state.activeCompanyId}`);
        return renderCompanyProfile(data.company);
      }
      return render();
    }
    if (target.dataset.action === "logout") {
      if (ws) { ws.close(); ws = null; }
      if (wsHeartbeatTimer) { clearInterval(wsHeartbeatTimer); wsHeartbeatTimer = null; }
      await api("/api/logout", { method: "POST", body: JSON.stringify({}) });
      csrfToken = null;
      state.user = null; state.view = "home"; state.dashboardTab = "overview";
      return render();
    }
    if (target.dataset.action === "toggle-theme") {
      const current = document.documentElement.getAttribute("data-theme");
      const next = current === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("meblio-theme", next);
      return renderHeader();
    }
    if (target.dataset.action === "toggle-notifications") {
      const panel = document.getElementById("notificationsPanel");
      if (panel) { panel.remove(); return; }
      document.body.insertAdjacentHTML("beforeend", `<div class="notifications-panel" id="notificationsPanel">
        <div class="notif-panel-header"><h3>Уведомления</h3><button class="button button-secondary button-small" type="button" data-action="close-notifications">✕</button></div>
        <div class="notif-panel-body">${renderNotificationsPanel()}</div>
      </div>`);
      return;
    }
    if (target.dataset.action === "close-notifications") {
      document.getElementById("notificationsPanel")?.remove();
      return;
    }
    if (target.dataset.action === "mark-all-read") {
      await api("/api/notifications/read-all", { method: "POST", body: JSON.stringify({}) });
      state.unreadCount = 0;
      state.notifications.forEach(n => n.is_read = 1);
      return render();
    }
    if (target.dataset.action === "notif-settings") {
      state.notifView = "settings";
      return render();
    }
    if (target.dataset.action === "notif-back") {
      state.notifView = "list";
      return render();
    }
    if (target.dataset.action === "clear-all-notifs") {
      if (!confirm("Удалить все уведомления?")) return;
      await api("/api/notifications/clear-all", { method: "DELETE" });
      state.notifications = [];
      state.unreadCount = 0;
      showToast("Уведомления очищены", "success");
      return render();
    }
    if (target.dataset.deleteNotif) {
      event.stopPropagation();
      const notifId = Number(target.dataset.deleteNotif);
      await api(`/api/notifications/${notifId}`, { method: "DELETE" });
      state.notifications = state.notifications.filter(n => n.id !== notifId);
      state.unreadCount = state.notifications.filter(n => !n.is_read).length;
      return render();
    }
    if (target.dataset.notifFilter !== undefined) {
      state.notifFilter = target.dataset.notifFilter;
      return render();
    }
    // Materials
    if (target.dataset.materialFilter !== undefined) {
      state.materialFilter = target.dataset.materialFilter;
      await loadMaterials(state.materialFilter);
      return render();
    }
    if (target.dataset.action === "add-material") {
      showMaterialForm();
      return;
    }
    if (target.dataset.deleteMaterial) {
      if (!confirm("Удалить материал?")) return;
      await api(`/api/materials/${target.dataset.deleteMaterial}`, { method: "DELETE" });
      showToast("Материал удалён", "success");
      await loadMaterials(state.materialFilter);
      return render();
    }
    // Templates
    if (target.dataset.action === "add-template") {
      showTemplateForm();
      return;
    }
    if (target.dataset.useTemplate) {
      const t = state.templates.find(x => x.id === Number(target.dataset.useTemplate));
      if (t) {
        state.view = "dashboard"; state.dashboardTab = "new-order";
        await render();
        setTimeout(() => {
          const form = document.getElementById("newOrderForm");
          if (form) {
            if (t.type) form.elements.type.value = t.type;
            if (t.title) form.elements.title.value = t.name;
            if (t.quantity) form.elements.quantity.value = t.quantity;
            if (t.city) form.elements.city.value = t.city;
            if (t.budget) form.elements.budget.value = t.budget;
            if (t.deadline) form.elements.deadline.value = t.deadline;
            if (t.details) form.elements.details.value = t.details;
          }
        }, 100);
      }
      return;
    }
    if (target.dataset.editTemplate) {
      const t = state.templates.find(x => x.id === Number(target.dataset.editTemplate));
      if (t) showTemplateForm(t);
      return;
    }
    if (target.dataset.deleteTemplate) {
      if (!confirm("Удалить шаблон?")) return;
      await api(`/api/templates/${target.dataset.deleteTemplate}`, { method: "DELETE" });
      showToast("Шаблон удалён", "success");
      await loadTemplates();
      return render();
    }
    // Invoices
    if (target.dataset.viewInvoice) {
      const data = await api(`/api/invoices/${target.dataset.viewInvoice}`);
      showInvoiceDetail(data.invoice);
      return;
    }
    if (target.dataset.action === "print-invoice") {
      const el = document.getElementById("invoiceDoc");
      if (el) { const w = window.open('', '_blank'); w.document.write(el.outerHTML); w.document.close(); setTimeout(() => w.print(), 300); }
      return;
    }
    // Delivery
    if (target.dataset.deliveryHistory) {
      await loadDelivery(target.dataset.deliveryHistory);
      showDeliveryTracking(target.dataset.deliveryHistory);
      return;
    }
    // Order History
    if (target.dataset.orderHistory) {
      await loadOrderHistory(target.dataset.orderHistory);
      showOrderHistory(target.dataset.orderHistory);
      return;
    }
    // 2FA
    if (target.dataset.action === "setup-tfa") {
      const data = await api("/api/tfa/setup", { method: "POST", body: JSON.stringify({}) });
      document.getElementById("tfaSetupContent").innerHTML = `
        <div class="tfa-qr-info">
          <p>Отсканируйте QR-код в приложении Google Authenticator или введите секрет вручную:</p>
          <div class="tfa-secret">${escapeHtml(data.secret)}</div>
          <form class="stack-form" id="tfaVerifyForm" style="margin-top:12px">
            <label>Код из приложения <input name="code" placeholder="000000" maxlength="6" required></label>
            <button class="button button-primary" type="submit" data-enable-tfa>Включить 2FA</button>
          </form>
        </div>`;
      return;
    }
    // Certificates
    if (target.dataset.action === "add-certificate") {
      showCertificateForm();
      return;
    }
    if (target.dataset.deleteCertificate) {
      if (!confirm("Удалить сертификат?")) return;
      await api(`/api/certificates/${target.dataset.deleteCertificate}`, { method: "DELETE" });
      showToast("Сертификат удалён", "success");
      await loadCertificates();
      return render();
    }
    // Time entries
    if (target.dataset.deleteTimeEntry) {
      if (!confirm("Удалить запись?")) return;
      await api(`/api/time-entries/${target.dataset.deleteTimeEntry}`, { method: "DELETE" });
      showToast("Запись удалена", "success");
      await loadTimeEntries();
      return render();
    }
    // Client rating
    if (target.dataset.rateClient) {
      const [orderId, clientId] = target.dataset.rateClient.split(":");
      clientRatingModal(orderId, clientId);
      return;
    }
    // Export Excel
    if (target.dataset.action === "export-excel") {
      window.location.href = "/api/export/excel";
      return;
    }
    if (target.dataset.action === "hero-search") {
      const input = document.getElementById("heroSearch");
      if (input?.value.trim()) {
        state.searchQuery = input.value.trim();
        await globalSearch(state.searchQuery);
        renderHeader();
      }
      return;
    }
    if (target.dataset.action === "add-service") { serviceFormModal(); return; }
    if (target.dataset.editService) {
      const s = state.services.find((sv) => sv.id === Number(target.dataset.editService));
      if (s) {
        try {
          const detail = await api(`/api/services/${s.id}`);
          serviceFormModal({ ...s, params: detail.service.params || [] });
        } catch {
          serviceFormModal(s);
        }
      }
      return;
    }
    if (target.dataset.action === "add-service-param") {
      const rows = document.getElementById("serviceParamRows");
      if (rows) {
        rows.insertAdjacentHTML("beforeend", `
          <div class="service-param-row">
            <input name="param_name" placeholder="Например: Материал" maxlength="80">
            <input name="param_value" placeholder="Значение" maxlength="200">
            <button class="button button-secondary button-small" type="button" data-action="remove-service-param" title="Удалить">✕</button>
          </div>`);
      }
      return;
    }
    if (target.dataset.action === "remove-service-param") {
      target.closest(".service-param-row")?.remove();
      return;
    }
    if (target.dataset.upgradePlan) {
      if (!state.user) return openAuth("login");
      const plan = target.dataset.upgradePlan;
      if (plan === "pro" && !confirm("Активировать тариф Pro? (демо: без платёжного провайдера)")) return;
      const r = await api("/api/tariff/upgrade", { method: "POST", body: JSON.stringify({ plan }) });
      if (r.user) state.user = r.user;
      showToast(`Тариф: ${plan}`, "success");
      return render();
    }
    if (target.dataset.createProposal) {
      const orderId = Number(target.dataset.createProposal);
      openProposalFormModal(orderId);
      return;
    }
    if (target.dataset.listProposals) {
      const orderId = Number(target.dataset.listProposals);
      await openProposalsModal(orderId);
      return;
    }
    if (target.dataset.proposalStatus) {
      const [pid, status] = target.dataset.proposalStatus.split(":");
      if (status === "accepted" && !confirm("Принять КП и выбрать этого исполнителя?")) return;
      if (status === "rejected" && !confirm("Отклонить КП?")) return;
      await api(`/api/proposals/${pid}/status`, { method: "POST", body: JSON.stringify({ status }) });
      showToast(status === "accepted" ? "КП принято" : status === "rejected" ? "КП отклонено" : "КП обновлено", "success");
      const orderId = Number(target.dataset.proposalOrder || 0);
      if (orderId) await openProposalsModal(orderId);
      if (status === "accepted") await refreshData();
      return;
    }
    if (target.dataset.deleteService) {
      if (!confirm("Удалить услугу?")) return;
      await api(`/api/services/${target.dataset.deleteService}`, { method: "DELETE" });
      showToast("Услуга удалена", "success");
      return render();
    }
    if (target.dataset.tab) {
      state.dashboardTab = target.dataset.tab;
      if (state.dashboardTab === "chats") { await loadThreads(); await loadMessages(state.activeThreadId); }
      if (state.dashboardTab === "my-services" && state.user?.role === "maker") {
        const data = await api(`/api/services?user_id=${state.user.id}`);
        state.services = data.services;
      }
      if (state.dashboardTab === "favorites") await loadFavorites();
      if (state.dashboardTab === "materials") await loadMaterials();
      if (state.dashboardTab === "templates") await loadTemplates();
      if (state.dashboardTab === "invoices") await loadInvoices();
      if (state.dashboardTab === "suppliers") await loadSuppliers();
      if (state.dashboardTab === "certificates") await loadCertificates();
      if (state.dashboardTab === "time") await loadTimeEntries();
      if (state.dashboardTab === "security") await loadTfaStatus();
      if (state.dashboardTab === "funnel") await loadMakerFunnel();
      return render();
    }
    if (target.dataset.duplicateOrder) {
      if (!confirm("Создать копию заказа как черновик?")) return;
      const data = await api(`/api/orders/${target.dataset.duplicateOrder}/duplicate`, { method: "POST", body: JSON.stringify({}) });
      showToast(`Копия создана: «${data.order.title}»`, "success");
      state.dashboardTab = "my-orders";
      state.view = "dashboard";
      await refreshData();
      return render();
    }
    if (target.dataset.compareClear) {
      const orderId = Number(target.dataset.compareClear);
      state.compareResponses = state.compareResponses.filter((x) => x.orderId !== orderId);
      return render();
    }
    if (target.dataset.compareOpen) {
      const orderId = Number(target.dataset.compareOpen);
      document.getElementById("compareModal")?.remove();
      app.insertAdjacentHTML("beforeend", `
        <div class="modal is-open" id="compareModal">
          <div class="modal-backdrop" data-close-compare></div>
          <section class="modal-card modal-card-wide">
            <button class="modal-close" type="button" data-close-compare>x</button>
            ${compareResponsesView(orderId)}
          </section>
        </div>`);
      return;
    }
    if (target.dataset.closeCompare !== undefined) {
      document.getElementById("compareModal")?.remove();
      return;
    }
    if (target.dataset.action === "refresh-funnel") {
      await loadMakerFunnel();
      return render();
    }
    if (target.dataset.createOrder !== undefined) {
      if (!state.user) return openAuth("register");
      if (state.user.role !== "client") return setView("market");
      state.view = "dashboard"; state.dashboardTab = "new-order";
      return render();
    }
    if (target.dataset.respond) {
      if (!state.user) return openAuth("login");
      return responseModal(target.dataset.respond);
    }
    if (target.dataset.chooseMaker) {
      const [orderId, makerId] = target.dataset.chooseMaker.split(":");
      await api(`/api/orders/${orderId}/choose`, { method: "POST", body: JSON.stringify({ maker_id: makerId }) });
      state.dashboardTab = "chats";
      return render();
    }
    if (target.dataset.openChat) {
      state.dashboardTab = "chats"; state.view = "dashboard";
      await render();
      return;
    }
    if (target.dataset.thread) {
      unsubscribeThread(state.activeThreadId);
      state.activeThreadId = Number(target.dataset.thread);
      subscribeThread(state.activeThreadId);
      await loadMessages(state.activeThreadId);
      return renderDashboard();
    }
    if (target.dataset.scrollResponses) {
      document.querySelector(`#responses-${target.dataset.scrollResponses}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    if (target.dataset.exportOrder) {
      const order = state.orders.find(o => o.id === Number(target.dataset.exportOrder));
      if (order) exportOrderHTML(order);
      return;
    }
    if (target.dataset.cancelOrder) {
      const orderId = Number(target.dataset.cancelOrder);
      if (confirm("Отменить заказ? Это действие нельзя отменить.")) {
        await api(`/api/orders/${orderId}/cancel`, { method: "POST", body: JSON.stringify({}) });
        showToast("Заказ отменён", "success");
        await refreshData();
        return render();
      }
    }
    if (target.dataset.toggleStages) {
      const panel = document.getElementById(`stages-${target.dataset.toggleStages}`);
      if (!panel) return;
      if (!panel.hidden) { panel.hidden = true; return; }
      await loadOrderStagesInto(target.dataset.toggleStages, panel);
      panel.hidden = false;
      return;
    }
    if (target.dataset.stageToggle) {
      const [orderId, stageId, done] = target.dataset.stageToggle.split(":");
      await api(`/api/orders/${orderId}/stages/${stageId}`, {
        method: "PUT",
        body: JSON.stringify({ done: done === "1" ? 0 : 1 }),
      });
      const panel = document.getElementById(`stages-${orderId}`);
      if (panel && !panel.hidden) await loadOrderStagesInto(orderId, panel);
      showToast("Этап обновлён", "success");
      return;
    }
    if (target.dataset.stageDelete) {
      const [orderId, stageId] = target.dataset.stageDelete.split(":");
      if (!confirm("Удалить этап?")) return;
      await api(`/api/orders/${orderId}/stages/${stageId}`, {
        method: "PUT",
        body: JSON.stringify({ delete: true }),
      });
      const panel = document.getElementById(`stages-${orderId}`);
      if (panel) await loadOrderStagesInto(orderId, panel);
      showToast("Этап удалён", "success");
      return;
    }
    if (target.dataset.addStage) {
      const orderId = target.dataset.addStage;
      const name = prompt("Название этапа:");
      if (!name?.trim()) return;
      await api(`/api/orders/${orderId}/stages`, { method: "POST", body: JSON.stringify({ name: name.trim() }) });
      const panel = document.getElementById(`stages-${orderId}`);
      if (panel) await loadOrderStagesInto(orderId, panel);
      return;
    }
    if (target.dataset.acceptOrder) {
      const orderId = Number(target.dataset.acceptOrder);
      if (!confirm("Принять работу и закрыть заказ? После этого можно оставить отзыв.")) return;
      try {
        await api(`/api/orders/${orderId}/accept`, { method: "POST", body: JSON.stringify({}) });
        showToast("Работа принята. Гарантия 14 дней.", "success");
        await refreshData();
        return render();
      } catch (error) {
        if (String(error.message).includes("Не завершены этапы") || String(error.message).includes("этап")) {
          if (confirm(`${error.message}\n\nПринять без завершённых этапов?`)) {
            await api(`/api/orders/${orderId}/accept`, { method: "POST", body: JSON.stringify({ force: true }) });
            showToast("Работа принята с подтверждением.", "success");
            await refreshData();
            return render();
          }
        } else throw error;
      }
      return;
    }
    if (target.dataset.orderContract) {
      const orderId = Number(target.dataset.orderContract);
      const data = await api(`/api/orders/${orderId}/contract`);
      openContractPrint(data.contract);
      return;
    }
    if (target.dataset.invoiceStatus) {
      const [invId, status] = target.dataset.invoiceStatus.split(":");
      if (status === "cancelled" && !confirm("Отменить счёт?")) return;
      if (status === "paid" && !confirm("Отметить счёт оплаченным?")) return;
      await api(`/api/invoices/${invId}`, { method: "PUT", body: JSON.stringify({ status }) });
      showToast(`Статус счёта: ${status === "paid" ? "оплачен" : "отменён"}`, "success");
      const refreshed = await api(`/api/invoices/${invId}`);
      showInvoiceDetail(refreshed.invoice);
      if (state.dashboardTab === "invoices") { await loadInvoices(); if (state.view === "dashboard") renderDashboard(); }
      return;
    }
    if (target.dataset.reportResolve) {
      const [reportId, status, hide] = target.dataset.reportResolve.split(":");
      if (!confirm(status === "rejected" ? "Отклонить жалобу?" : "Принять жалобу и скрыть объект?")) return;
      await api(`/api/admin/reports/${reportId}/resolve`, {
        method: "POST",
        body: JSON.stringify({ status, hide_target: hide === "hide" }),
      });
      showToast("Жалоба обработана", "success");
      await loadAdminReports();
      return render();
    }
    if (target.dataset.closeOrder) {
      const orderId = Number(target.dataset.closeOrder);
      if (!confirm("Завершить заказ? После завершения можно оставить отзыв.")) return;
      try {
        await api(`/api/orders/${orderId}/close`, { method: "POST", body: JSON.stringify({}) });
        showToast("Заказ завершён. Теперь можно оставить отзыв.", "success");
      } catch (error) {
        if (String(error.message).includes("этап")) {
          if (confirm(`${error.message}\n\nЗавершить без этапов?`)) {
            await api(`/api/orders/${orderId}/close`, { method: "POST", body: JSON.stringify({ force: true }) });
            showToast("Заказ завершён с подтверждением.", "success");
          } else return;
        } else throw error;
      }
      await refreshData();
      return render();
    }
    if (target.dataset.reportOrder) {
      const orderId = Number(target.dataset.reportOrder);
      const reason = prompt("Причина жалобы (обязательно):");
      if (!reason) return;
      try {
        await api("/api/reports", { method: "POST", body: JSON.stringify({ target_type: "order", target_id: orderId, reason }) });
        showToast("Жалоба отправлена модератору", "success");
      } catch (error) { showToast(error.message); }
      return;
    }
    if (target.dataset.delGallery) {
      await api(`/api/gallery/${target.dataset.delGallery}`, { method: "DELETE" });
      showToast("Работа удалена", "success");
      await refreshData();
      return render();
    }
    if (target.dataset.publishOrder) {
      await api(`/api/orders/${target.dataset.publishOrder}/publish`, { method: "POST", body: JSON.stringify({}) });
      showToast("Заказ опубликован", "success");
      await refreshData();
      return render();
    }
    if (target.dataset.inviteCompany) {
      const companyId = Number(target.dataset.inviteCompany);
      const open = state.orders.filter((o) => o.client_id === state.user.id && o.status === "open");
      const list = open.map((o) => `${o.id}. ${o.title}`).join("\n");
      const picked = prompt("К какому заказу запросить расчёт? Введите номер:\n" + list);
      const order = open.find((o) => String(o.id) === String(picked));
      if (!order) return;
      try {
        await api(`/api/companies/${companyId}/invite`, { method: "POST", body: JSON.stringify({ order_id: order.id }) });
        showToast("Запрос расчёта отправлен", "success");
      } catch (error) { showToast(error.message); }
      return;
    }
    if (target.dataset.sortResponses) {
      const [orderId, key] = target.dataset.sortResponses.split(":");
      const order = state.orders.find(o => o.id === Number(orderId));
      if (!order) return;
      const dir = state.sortDir?.[orderId]?.[key] === "asc" ? "desc" : "asc";
      state.sortDir = state.sortDir || {};
      state.sortDir[orderId] = state.sortDir[orderId] || {};
      state.sortDir[orderId][key] = dir;
      order.responses.sort((a, b) => {
        const va = key === "rating" ? (a.maker_rating || 0) : a[key];
        const vb = key === "rating" ? (b.maker_rating || 0) : b[key];
        return dir === "asc" ? va - vb : vb - va;
      });
      return renderDashboard();
    }
    if (target.dataset.notifLink) {
      const notifId = target.dataset.notifId;
      if (notifId) await api(`/api/notifications/${notifId}`, { method: "POST", body: JSON.stringify({}) });
      document.getElementById("notificationsPanel")?.remove();
      const link = target.dataset.notifLink.replace(/^\/order\//, "/orders/").replace(/^\/company\//, "/companies/");
      navigate(link);
      return;
    }
    if (target.dataset.deleteDocument) {
      if (!confirm("Удалить документ?")) return;
      await api(`/api/documents/${target.dataset.deleteDocument}`, { method: "DELETE" });
      showToast("Документ удалён", "success");
      return render();
    }
    if (target.dataset.companyId) {
      navigate(`/companies/${Number(target.dataset.companyId)}`);
      return;
    }
    if (target.dataset.toggleFavorite !== undefined) {
      if (!state.user) return openAuth("login");
      event.stopPropagation();
      const companyId = Number(target.dataset.toggleFavorite);
      if (isFavorite(companyId)) {
        await api(`/api/favorites/${companyId}`, { method: "DELETE" });
        state.favorites = state.favorites.filter((f) => f.company_id !== companyId);
      } else {
        await api("/api/favorites", { method: "POST", body: JSON.stringify({ company_id: companyId }) });
        await loadFavorites();
      }
      return render();
    }
    if (target.dataset.adminTab) {
      state.adminTab = target.dataset.adminTab;
      if (state.adminTab === "analytics") loadAdminAnalytics();
      if (state.adminTab === "activity") loadAdminActivity();
      if (state.adminTab === "reports") await loadAdminReports();
      return render();
    }
    if (target.dataset.action === "export-analytics") {
      exportAnalyticsCSV();
      return;
    }
    if (target.dataset.action === "admin-create-user") {
      adminCreateUserModal();
      return;
    }
    if (target.dataset.adminViewUser) {
      event.preventDefault();
      adminViewUser(target.dataset.adminViewUser);
      return;
    }
    if (target.dataset.adminViewOrder) {
      event.preventDefault();
      adminViewOrder(target.dataset.adminViewOrder);
      return;
    }
    if (target.dataset.adminEditUser !== undefined) {
      adminEditUserModal(target.dataset.adminEditUser);
      return;
    }
    if (target.dataset.adminDeleteUser !== undefined) {
      if (!confirm("Удалить пользователя?")) return;
      await api(`/api/admin/users/${target.dataset.adminDeleteUser}`, { method: "DELETE" });
      showToast("Пользователь удалён", "success");
      await loadAdminUsers();
      return render();
    }
    if (target.dataset.adminDeleteOrder !== undefined) {
      if (!confirm("Удалить заказ?")) return;
      await api(`/api/admin/orders/${target.dataset.adminDeleteOrder}`, { method: "DELETE" });
      showToast("Заказ удалён", "success");
      await loadAdminOrders();
      return render();
    }
    if (target.dataset.adminDeleteService !== undefined) {
      if (!confirm("Удалить услугу?")) return;
      await api(`/api/admin/services/${target.dataset.adminDeleteService}`, { method: "DELETE" });
      showToast("Услуга удалена", "success");
      await loadAdminServices();
      return render();
    }
    if (target.dataset.closeAdminModal !== undefined) {
      document.querySelector("#adminUserModal")?.remove();
      return;
    }
  } catch (error) { showToast(error.message); }
});

document.addEventListener("click", async (event) => {
  const star = event.target.closest("[data-star]");
  if (star && star.closest("#reviewStars")) {
    const rating = Number(star.dataset.star);
    document.getElementById("reviewRating").value = rating;
    document.querySelectorAll("#reviewStars .star").forEach((s, i) => {
      s.classList.toggle("filled", i < rating);
    });
    return;
  }
});

document.addEventListener("change", async (event) => {
  if (event.target.matches("[data-compare-toggle]")) {
    const [orderId, makerId] = event.target.dataset.compareToggle.split(":").map(Number);
    const idx = state.compareResponses.findIndex((x) => x.orderId === orderId && x.makerId === makerId);
    if (event.target.checked) {
      if (idx < 0) state.compareResponses.push({ orderId, makerId });
    } else if (idx >= 0) state.compareResponses.splice(idx, 1);
    return render();
  }
  if (event.target.matches("#typeFilter")) { await loadOrders(); renderMarket(); }
  if (event.target.matches("#statusFilter")) { await loadOrders(); renderMarket(); }
  if (event.target.matches("#companyTypeFilter")) { state.companyFilters.type = event.target.value; await loadCompanies(); renderCompanies(); }
  if (event.target.matches("#companyRegionFilter")) { state.companyFilters.region = event.target.value; await loadCompanies(); renderCompanies(); }
  if (event.target.matches("#adminUserRoleFilter")) { state.adminUserFilters.role = event.target.value; await loadAdminUsers(); renderAdmin(); }
  if (event.target.matches("#adminOrderStatusFilter")) { state.adminOrderFilters.status = event.target.value; await loadAdminOrders(); renderAdmin(); }
  if (event.target.matches("[data-admin-order-status]")) {
    const orderId = event.target.dataset.adminOrderStatus;
    const status = event.target.value;
    try {
      await api("/api/admin/orders/status", { method: "POST", body: JSON.stringify({ order_id: orderId, status }) });
      showToast("Статус обновлён", "success");
      await loadAdminOrders();
      render();
    } catch (error) { showToast(error.message); }
  }
});

document.addEventListener("input", debounce(async (event) => {
  if (event.target.matches("#cityFilter")) { await loadOrders(); renderMarket(); }
  if (event.target.matches("#companySearchFilter")) { state.companyFilters.search = event.target.value; await loadCompanies(); renderCompanies(); }
  if (event.target.matches("#budgetMinFilter") || event.target.matches("#budgetMaxFilter")) { await loadOrders(); renderMarket(); }
  if (event.target.matches("#globalSearch")) {
    state.searchQuery = event.target.value;
    await globalSearch(state.searchQuery);
    renderHeader();
    const dropdown = document.getElementById("searchDropdown");
    if (dropdown) dropdown.style.display = state.searchResults ? "block" : "none";
  }
  if (event.target.matches("#adminUserSearch")) {
    state.adminUserFilters.search = event.target.value;
    await loadAdminUsers();
    if (state.view === "admin") renderAdmin();
  }
  if (event.target.matches("#adminOrderSearch")) {
    state.adminOrderFilters.search = event.target.value;
    await loadAdminOrders();
    if (state.view === "admin") renderAdmin();
  }
}, 350));

document.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    if (event.target.id === "newOrderForm") {
      await api("/api/orders", { method: "POST", body: new FormData(event.target) });
      state.dashboardTab = "my-orders";
      return render();
    }
    if (event.target.id === "estimateForm") {
      event.preventDefault();
      await runEstimate(event.target);
      return;
    }
    if (event.target.id === "responseForm") {
      const orderId = event.target.dataset.orderId;
      const data = Object.fromEntries(new FormData(event.target));
      await api(`/api/orders/${orderId}/responses`, { method: "POST", body: JSON.stringify(data) });
      document.querySelector("#responseModal")?.remove();
      state.dashboardTab = "responses"; state.view = "dashboard";
      return render();
    }
    if (event.target.id === "proposalForm") {
      const form = event.target;
      const orderId = form.dataset.orderId;
      const fd = new FormData(form);
      const nameEls = form.querySelectorAll('[name="item_name"]');
      const qtyEls = form.querySelectorAll('[name="item_qty"]');
      const priceEls = form.querySelectorAll('[name="item_price"]');
      const items = [];
      nameEls.forEach((n, i) => {
        const name = (n.value || "").trim();
        if (!name) return;
        items.push({
          name,
          qty: Math.max(1, Number(qtyEls[i]?.value || 1)),
          price: Math.max(0, Number(priceEls[i]?.value || 0)),
        });
      });
      await api(`/api/orders/${orderId}/proposals`, {
        method: "POST",
        body: JSON.stringify({
          amount: Number(fd.get("amount") || 0),
          days: Number(fd.get("days") || 0),
          message: String(fd.get("message") || ""),
          items,
        }),
      });
      document.querySelector("#proposalModal")?.remove();
      showToast("КП отправлено заказчику", "success");
      await openProposalsModal(Number(orderId));
      return;
    }
    if (event.target.id === "serviceForm") {
      const form = event.target;
      const data = Object.fromEntries(new FormData(form).entries());
      const names = form.querySelectorAll('[name="param_name"]');
      const values = form.querySelectorAll('[name="param_value"]');
      const params = [];
      names.forEach((n, i) => {
        const name = (n.value || "").trim();
        const value = (values[i]?.value || "").trim();
        if (name) params.push({ name, value });
      });
      data.params = JSON.stringify(params);
      const modal = document.querySelector("#serviceModal");
      const isEdit = modal?.querySelector("[data-edit-service]");
      if (isEdit) {
        await api(`/api/services/${isEdit.dataset.editService}`, { method: "PUT", body: JSON.stringify(data) });
        showToast("Услуга обновлена", "success");
      } else {
        const fd = new FormData(form);
        fd.set("params", data.params);
        await api("/api/services", { method: "POST", body: fd });
        showToast("Услуга добавлена", "success");
      }
      modal?.remove();
      const sdata = await api(`/api/services?user_id=${state.user.id}`);
      state.services = sdata.services;
      state.dashboardTab = "my-services";
      return render();
    }
    if (event.target.id === "chatForm") {
      const formData = new FormData(event.target);
      const hasFiles = formData.get("files")?.size > 0;
      const body = String(formData.get("body") || "").trim();
      if (!body && !hasFiles) return;
      event.target.reset();
      const tempMsg = {
        id: Date.now(),
        thread_id: state.activeThreadId,
        author_id: state.user.id,
        author_name: state.user.name,
        body: hasFiles ? "📎 Файл" : body,
        created_at: new Date().toLocaleString("ru-RU"),
      };
      state.messages.push(tempMsg);
      appendMessage(tempMsg);
      if (hasFiles) {
        const uploadData = new FormData();
        if (body) uploadData.append("body", body);
        for (const file of formData.getAll("files")) uploadData.append("files", file);
        await api(`/api/threads/${state.activeThreadId}/files`, { method: "POST", body: uploadData });
      } else {
        await api(`/api/threads/${state.activeThreadId}/messages`, { method: "POST", body: JSON.stringify({ body }) });
      }
      return;
    }
    if (event.target.id === "profileForm") {
      const data = Object.fromEntries(new FormData(event.target));
      const result = await api("/api/profile", { method: "POST", body: JSON.stringify(data) });
      state.user = result.user;
      showToast("Профиль сохранён", "success");
      return render();
    }
    if (event.target.id === "galleryForm") {
      await api("/api/gallery", { method: "POST", body: new FormData(event.target) });
      showToast("Работа добавлена в портфолио", "success");
      await refreshData();
      return render();
    }
    if (event.target.id === "changePwForm") {
      const data = Object.fromEntries(new FormData(event.target));
      await api("/api/change-password", { method: "POST", body: JSON.stringify(data) });
      showToast("Пароль изменён. Войдите заново.", "success");
      state.user = null;
      return render();
    }
    if (event.target.id === "changeEmailForm") {
      const data = Object.fromEntries(new FormData(event.target));
      const result = await api("/api/change-email", { method: "POST", body: JSON.stringify(data) });
      if (result.verify_url) state.verifyUrl = result.verify_url;
      showToast("Email изменён. Подтвердите новый адрес.", "success");
      await loadSession();
      showVerifyBanner();
      return render();
    }
    if (event.target.id === "deleteAccountForm") {
      const data = Object.fromEntries(new FormData(event.target));
      if (!confirm("Удалить аккаунт? Это действие необратимо.")) return;
      await api("/api/delete-account", { method: "POST", body: JSON.stringify(data) });
      showToast("Аккаунт удалён", "success");
      state.user = null;
      return render();
    }
    if (event.target.id === "reviewForm") {
      const companyId = event.target.dataset.companyId;
      const data = Object.fromEntries(new FormData(event.target));
      await api("/api/reviews", { method: "POST", body: JSON.stringify({ company_id: companyId, rating: data.rating, text: data.text }) });
      showToast("Отзыв отправлен", "success");
      return render();
    }
    if (event.target.id === "documentForm") {
      await api("/api/documents", { method: "POST", body: new FormData(event.target) });
      showToast("Документ загружен", "success");
      return render();
    }
    if (event.target.id === "notifPrefsForm") {
      const form = event.target;
      const data = Object.fromEntries(new FormData(form));
      const tg = String(data.telegram_chat_id || "").trim();
      const mx = String(data.max_chat_id || "").trim();
      delete data.telegram_chat_id;
      delete data.max_chat_id;
      Object.keys(data).forEach(k => data[k] = data[k] === "on" ? 1 : 0);
      await api("/api/notifications/preferences", { method: "POST", body: JSON.stringify(data) });
      if (tg !== (state.user?.telegram_chat_id || "")) {
        const r = await api("/api/messenger/link", { method: "POST", body: JSON.stringify({ channel: "telegram", chat_id: tg }) });
        if (r.user) state.user = r.user;
      }
      if (mx !== (state.user?.max_chat_id || "")) {
        const r = await api("/api/messenger/link", { method: "POST", body: JSON.stringify({ channel: "max", chat_id: mx }) });
        if (r.user) state.user = r.user;
      }
      state.notifPrefs = data;
      state.notifView = "list";
      showToast("Настройки уведомлений сохранены", "success");
      return render();
    }
    if (event.target.id === "materialForm") {
      const data = Object.fromEntries(new FormData(event.target));
      const modal = document.querySelector("#materialModal");
      const isEdit = modal?.querySelector("[data-edit-material]");
      if (isEdit) {
        await api(`/api/materials/${isEdit.dataset.editMaterial}`, { method: "PUT", body: JSON.stringify(data) });
        showToast("Материал обновлён", "success");
      } else {
        await api("/api/materials", { method: "POST", body: JSON.stringify(data) });
        showToast("Материал добавлен", "success");
      }
      modal?.remove();
      await loadMaterials(state.materialFilter);
      return render();
    }
    if (event.target.id === "templateForm") {
      const data = Object.fromEntries(new FormData(event.target));
      const modal = document.querySelector("#templateModal");
      const isEdit = modal?.querySelector("[data-edit-template-form]");
      if (isEdit) {
        await api(`/api/templates/${isEdit.dataset.editTemplateForm}`, { method: "PUT", body: JSON.stringify(data) });
        showToast("Шаблон обновлён", "success");
      } else {
        await api("/api/templates", { method: "POST", body: JSON.stringify(data) });
        showToast("Шаблон создан", "success");
      }
      modal?.remove();
      await loadTemplates();
      return render();
    }
    if (event.target.id === "deliveryForm") {
      const data = Object.fromEntries(new FormData(event.target));
      data.order_id = event.target.dataset.orderId;
      await api("/api/delivery", { method: "POST", body: JSON.stringify(data) });
      showToast("Статус доставки обновлён", "success");
      await loadDelivery(data.order_id);
      document.querySelector("#deliveryModal")?.remove();
      showDeliveryTracking(data.order_id);
      return;
    }
    if (event.target.id === "tfaVerifyForm") {
      const code = event.target.elements.code.value;
      const enableTfa = event.target.querySelector("[data-enable-tfa]") !== null;
      await api("/api/tfa/verify", { method: "POST", body: JSON.stringify({ code, enable: enableTfa }) });
      state.tfaEnabled = true;
      showToast("2FA включена!", "success");
      return render();
    }
    if (event.target.id === "certificateForm") {
      const data = Object.fromEntries(new FormData(event.target));
      const modal = document.querySelector("#certificateModal");
      const isEdit = modal?.querySelector("[data-edit-cert]");
      if (isEdit) {
        await api(`/api/certificates/${isEdit.dataset.editCert}`, { method: "PUT", body: JSON.stringify(data) });
        showToast("Сертификат обновлён", "success");
      } else {
        await api("/api/certificates", { method: "POST", body: new FormData(event.target) });
        showToast("Сертификат добавлен", "success");
      }
      modal?.remove();
      await loadCertificates();
      return render();
    }
    if (event.target.id === "timeEntryForm") {
      const data = Object.fromEntries(new FormData(event.target));
      await api("/api/time-entries", { method: "POST", body: JSON.stringify(data) });
      showToast("Запись добавлена", "success");
      await loadTimeEntries();
      event.target.reset();
      event.target.elements.date.value = new Date().toISOString().slice(0,10);
      return render();
    }
    if (event.target.id === "clientRatingForm") {
      const data = Object.fromEntries(new FormData(event.target));
      data.order_id = event.target.dataset.orderId;
      data.client_id = event.target.dataset.clientId;
      await api("/api/client-ratings", { method: "POST", body: JSON.stringify(data) });
      document.querySelector("#clientRatingModal")?.remove();
      showToast("Оценка отправлена", "success");
      return;
    }
    if (event.target.id === "adminUserForm") {
      const userId = event.target.dataset.userId;
      const data = Object.fromEntries(new FormData(event.target));
      await api(`/api/admin/users/${userId}`, { method: "PUT", body: JSON.stringify(data) });
      document.querySelector("#adminUserModal")?.remove();
      showToast("Пользователь обновлён", "success");
      await loadAdminUsers();
      return render();
    }
    if (event.target.id === "adminCreateUserForm") {
      const data = Object.fromEntries(new FormData(event.target));
      await api("/api/admin/users", { method: "POST", body: JSON.stringify(data) });
      document.querySelector("#adminCreateUserModal")?.remove();
      showToast("Пользователь создан", "success");
      await loadAdminUsers();
      return render();
    }
  } catch (error) { showToast(error.message); }
});

let pendingLoginToken = null;

const forgotForm = document.querySelector("#forgotForm");

function showTfaStep(show) {
  document.querySelector("#tfaRow").classList.toggle("hidden", !show);
  document.querySelector("#loginSubmitBtn").textContent = show ? "Подтвердить код" : "Войти";
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  authMessage.textContent = "";
  try {
    const formData = Object.fromEntries(new FormData(loginForm));
    if (pendingLoginToken) {
      const result = await api("/api/tfa/login", { method: "POST", body: JSON.stringify({ login_token: pendingLoginToken, code: String(formData.tfa_code || "").trim() }) });
      pendingLoginToken = null;
      csrfToken = null;
      await ensureCsrfToken();
      state.user = result.user; state.view = "dashboard"; state.dashboardTab = "overview";
      closeAuth(); await render();
      return;
    }
    const result = await api("/api/login", { method: "POST", body: JSON.stringify({ email: formData.email, password: formData.password }) });
    if (result.tfa_required) {
      pendingLoginToken = result.login_token;
      showTfaStep(true);
      authMessage.textContent = "Введите код из приложения аутентификации";
      return;
    }
    csrfToken = null;
    await ensureCsrfToken();
    state.user = result.user; state.view = "dashboard"; state.dashboardTab = "overview";
    closeAuth(); await render();
  } catch (error) { authMessage.textContent = error.message; }
});

registerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  authMessage.textContent = "";
  try {
    const payload = Object.fromEntries(new FormData(registerForm));
    if (!payload.consent_pd) {
      authMessage.textContent = "Отметьте согласие на обработку персональных данных и прием оферты";
      return;
    }
    const result = await api("/api/register", { method: "POST", body: JSON.stringify(payload) });
    csrfToken = null;
    await ensureCsrfToken();
    state.verifyUrl = result.verify_url || state.verifyUrl;
    state.user = result.user; state.view = "dashboard"; state.dashboardTab = "overview";
    closeAuth(); await render();
    showVerifyBanner();
  } catch (error) { authMessage.textContent = error.message; }
});

document.querySelector("#forgotLink").addEventListener("click", () => {
  loginForm.classList.add("hidden");
  forgotForm.classList.remove("hidden");
});

document.querySelector("#forgotCancel").addEventListener("click", () => {
  forgotForm.classList.add("hidden");
  loginForm.classList.remove("hidden");
});

forgotForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  authMessage.textContent = "";
  try {
    const email = new FormData(forgotForm).get("email");
    await api("/api/forgot-password", { method: "POST", body: JSON.stringify({ email }) });
    forgotForm.classList.add("hidden");
    loginForm.classList.remove("hidden");
    authMessage.textContent = "Ссылка восстановления отправлена на почту";
  } catch (error) { authMessage.textContent = error.message; }
});

function showVerifyBanner() {
  const target = document.querySelector(".site-header");
  if (!target || !state.user || state.user.is_verified) return;
  if (document.querySelector("#verifyBanner")) return;
  const banner = document.createElement("div");
  banner.id = "verifyBanner";
  banner.style.cssText = "background:#fef3c7;color:#92400e;padding:8px 20px;text-align:center;font-size:14px;";
  const link = state.verifyUrl
    ? `<a href="${escapeHtml(state.verifyUrl)}" style="color:#1d4ed8;font-weight:600;">Подтвердить email</a>`
    : `<button type="button" id="resendVerify" style="color:#1d4ed8;font-weight:600;background:none;border:none;cursor:pointer;text-decoration:underline;">Выслать ссылку заново</button>`;
  banner.innerHTML = `Подтвердите адрес электронной почты. ${link} `;
  target.after(banner);
  document.querySelector("#resendVerify")?.addEventListener("click", async () => {
    try {
      const data = await api("/api/resend-verification", { method: "POST", body: JSON.stringify({ email: state.user.email }) });
      if (data.verify_url) state.verifyUrl = data.verify_url;
      showToast("Ссылка отправлена", "success");
    } catch (error) { showToast(error.message); }
  });
}

function renderResetPassword(token) {
  app.innerHTML = `
    <section class="section"><div class="container">
      <div class="card" style="max-width:420px;margin:0 auto;">
        <h1>Восстановление пароля</h1>
        <form id="resetPwForm" class="stack-form">
          <label>Новый пароль
            <input name="password" type="password" minlength="6" placeholder="Не короче 6 символов" required>
          </label>
          <button class="button button-primary" type="submit">Сохранить пароль</button>
        </form>
        <p class="form-result" id="resetPwMsg" aria-live="polite"></p>
      </div>
    </div></section>`;
  document.querySelector("#resetPwForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const msg = document.querySelector("#resetPwMsg");
    try {
      const password = new FormData(event.target).get("password");
      await api("/api/reset-password", { method: "POST", body: JSON.stringify({ token, password }) });
      msg.textContent = "Пароль изменён. Войдите с новым паролем.";
      msg.style.color = "#16a34a";
    } catch (error) { msg.textContent = error.message; }
  });
}


const AI_SUGGESTIONS = ["Как создать заказ?", "Подбери материал для фасадов", "Как работает оплата?", "Мои заказы"];

function aiBubbleHtml(msg) {
  const cls = msg.role === "user" ? "ai-msg-user" : "ai-msg-bot";
  const body = escapeHtml(msg.content).replace(/\n/g, "<br>");
  return `<div class="ai-msg ${cls}">${body}</div>`;
}

function renderAiMessages() {
  const box = document.querySelector("#aiMessages");
  if (!box) return;
  if (!state.user) {
    box.innerHTML = `
      <div class="empty" style="margin:0;">
        <p>Ассистент доступен после входа на площадку.</p>
        <button class="button button-primary" type="button" id="aiLoginBtn">Войти</button>
      </div>`;
    document.querySelector("#aiLoginBtn")?.addEventListener("click", () => openAuth("login"));
    return;
  }
  const chips = state.aiMessages.length
    ? ""
    : `<div class="ai-chips">${AI_SUGGESTIONS.map((s) => `<button type="button" class="chip ai-chip">${escapeHtml(s)}</button>`).join("")}</div>`;
  const typing = state.aiLoading ? `<div class="ai-msg ai-msg-bot ai-typing">Печатает…</div>` : "";
  box.innerHTML = chips + state.aiMessages.map(aiBubbleHtml).join("") + typing;
  box.scrollTop = box.scrollHeight;
}

async function loadAiHistory() {
  try {
    const data = await api("/api/ai/history");
    state.aiMessages = data.messages.map(({ role, content }) => ({ role, content }));
  } catch {
    state.aiMessages = [];
  }
  renderAiMessages();
}

async function sendAiMessage(text) {
  const message = (text || "").trim();
  if (!message || state.aiLoading) return;
  state.aiMessages.push({ role: "user", content: message });
  state.aiLoading = true;
  renderAiMessages();
  try {
    const data = await api("/api/ai/chat", { method: "POST", body: JSON.stringify({ message }) });
    state.aiMessages.push({ role: "assistant", content: data.reply });
  } catch (error) {
    state.aiMessages.push({ role: "assistant", content: `Не получилось получить ответ: ${error.message}` });
  } finally {
    state.aiLoading = false;
    renderAiMessages();
  }
}

function toggleAiPanel(open = !state.aiOpen) {
  state.aiOpen = open;
  document.querySelector("#aiPanel")?.classList.toggle("is-open", state.aiOpen);
  document.querySelector("#aiFab")?.classList.toggle("is-hidden", state.aiOpen);
  if (state.aiOpen) {
    renderAiMessages();
    if (state.user && !state.aiMessages.length) loadAiHistory();
    document.querySelector("#aiInput")?.focus();
  }
}

async function clearAiHistory() {
  if (!state.user) return;
  try {
    await api("/api/ai/history", { method: "DELETE" });
    state.aiMessages = [];
    renderAiMessages();
    showToast("Диалог очищен", "success");
  } catch (error) { showToast(error.message); }
}

function initAiWidget() {
  if (document.querySelector("#aiFab")) return;
  const fab = document.createElement("button");
  fab.id = "aiFab";
  fab.className = "ai-fab";
  fab.type = "button";
  fab.setAttribute("aria-label", "Открыть AI-ассистента");
  fab.innerHTML = `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>`;
  fab.addEventListener("click", () => toggleAiPanel(true));

  const panel = document.createElement("div");
  panel.id = "aiPanel";
  panel.className = "ai-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "AI-ассистент Meblio");
  panel.innerHTML = `
    <header class="ai-header">
      <div>
        <strong>Ассистент mebl.io</strong>
        <small id="aiProviderBadge"></small>
      </div>
      <div class="ai-header-actions">
        <button type="button" class="ai-icon-btn" id="aiClearBtn" title="Очистить диалог" aria-label="Очистить диалог">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
        </button>
        <button type="button" class="ai-icon-btn" id="aiCloseBtn" title="Закрыть" aria-label="Закрыть">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
        </button>
      </div>
    </header>
    <div class="ai-messages" id="aiMessages"></div>
    <form class="ai-input-row" id="aiForm">
      <input id="aiInput" type="text" maxlength="4000" placeholder="Спросите ассистента…" autocomplete="off">
      <button class="ai-send" type="submit" aria-label="Отправить">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
      </button>
    </form>`;
  document.body.append(fab, panel);

  panel.addEventListener("click", (event) => {
    const chip = event.target.closest(".ai-chip");
    if (chip) sendAiMessage(chip.textContent);
  });
  document.querySelector("#aiCloseBtn").addEventListener("click", () => toggleAiPanel(false));
  document.querySelector("#aiClearBtn").addEventListener("click", clearAiHistory);
  document.querySelector("#aiForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = document.querySelector("#aiInput");
    sendAiMessage(input.value);
    input.value = "";
  });

  if (getCookie("meblio_session")) {
    api("/api/ai/history").then((data) => {
      const badge = document.querySelector("#aiProviderBadge");
      if (badge && data.provider === "builtin") badge.textContent = "офлайн-режим";
    }).catch(() => {});
  }
}


document.querySelectorAll("[data-auth-tab]").forEach((b) => b.addEventListener("click", () => setAuthTab(b.dataset.authTab)));

const savedTheme = localStorage.getItem("meblio-theme");
if (savedTheme) document.documentElement.setAttribute("data-theme", savedTheme);

function initCookieBanner() {
  try {
    if (localStorage.getItem("meblio-cookie-ok")) return;
  } catch {}
  if (document.getElementById("cookieBanner")) return;
  const el = document.createElement("div");
  el.id = "cookieBanner";
  el.className = "cookie-banner";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-label", "Уведомление о cookie");
  el.innerHTML = `
    <p>Мы используем cookie для работы сайта, авторизации и сохранения настроек.
      Подробнее — в <a href="/privacy" data-nav>Политике конфиденциальности</a>.</p>
    <button class="button button-primary button-small" type="button" id="cookieAcceptBtn">Принять</button>`;
  document.body.appendChild(el);
  el.querySelector("#cookieAcceptBtn")?.addEventListener("click", () => {
    try { localStorage.setItem("meblio-cookie-ok", "1"); } catch {}
    el.remove();
  });
}

applyRoute(location.pathname);
initCookieBanner();
initAiWidget();
loadSession().then(render).catch((error) => {
  app.innerHTML = `<section class="section"><div class="container"><div class="empty">${escapeHtml(error.message)}</div></div></section>`;
});

const resetToken = new URLSearchParams(location.search).get("token");
if (location.pathname.startsWith("/reset-password") && resetToken) {
  document.title = "Восстановление пароля — Meblio";
  renderResetPassword(resetToken);
}

// Register service worker here: inline scripts are blocked by CSP script-src 'self'
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
