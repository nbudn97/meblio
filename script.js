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
  state.view = view;
  render();
}

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
  const data = await api(`/api/orders${params.toString() ? `?${params}` : ""}`);
  let orders = data.orders;
  if (budgetMin) orders = orders.filter(o => o.budget >= Number(budgetMin));
  if (budgetMax) orders = orders.filter(o => o.budget <= Number(budgetMax));
  state.orders = orders;
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

  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${protocol}//${location.hostname}:8001`);

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
          <img src="/meblio.png" alt="Производство мебели">
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
      </div>
      ${order.files?.length ? `<ul class="chips">${order.files.map((f) => `<li><a href="${f.url}" target="_blank" rel="noreferrer">${escapeHtml(f.name)}</a></li>`).join("")}</ul>` : ""}
      ${order.selected_maker_name ? `<p class="muted">Исполнитель: <strong>${escapeHtml(order.selected_maker_name)}</strong></p>` : ""}
      ${showActions ? `<div class="actions">
        ${canRespond ? `<button class="button button-primary button-small" type="button" data-respond="${order.id}">Откликнуться</button>` : ""}
        ${canChat ? `<button class="button button-secondary button-small" type="button" data-open-chat="${order.id}">Открыть чат</button>` : ""}
        ${canChoose ? `<button class="button button-secondary button-small" type="button" data-scroll-responses="${order.id}">Отклики: ${order.responses?.length || 0}</button>` : ""}
        ${(user?.id === order.client_id && (order.status === "open" || order.status === "progress")) ? `<button class="button button-secondary button-small" type="button" data-cancel-order="${order.id}">Отменить</button>` : ""}
        <button class="button button-secondary button-small" type="button" data-export-order="${order.id}" title="Экспорт в PDF">📥</button>
        <button class="button button-secondary button-small" type="button" data-delivery-history="${order.id}" title="Доставка">🚚</button>
        <button class="button button-secondary button-small" type="button" data-order-history="${order.id}" title="История">📋</button>
        <button class="button button-secondary button-small" type="button" data-report-order="${order.id}" title="Пожаловаться">⚠</button>
      </div>` : ""}
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
        <div class="dashboard-top">
          <div>
            <p class="eyebrow">Профиль компании</p>
            <h1>${escapeHtml(company.name)}</h1>
            <p class="lead">${companyTypeLabel(company.company_type)} · ${escapeHtml(company.city)} ${company.region_name ? "· " + escapeHtml(company.region_name) : ""}</p>
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
    ? [["overview", "Обзор"], ["notifications", `Уведомления${state.unreadCount ? ' (' + state.unreadCount + ')' : ''}`], ["new-order", "Создать заказ"], ["my-orders", "Мои заказы"], ["templates", "Шаблоны"], ["invoices", "Счета"], ["materials", "Материалы"], ["suppliers", "Поставщики"], ["certificates", "Сертификаты"], ["time", "Время"], ["favorites", "Избранные"], ["chats", "Сообщения"], ["security", "Безопасность"], ["profile", "Профиль"]]
    : [["overview", "Обзор"], ["notifications", `Уведомления${state.unreadCount ? ' (' + state.unreadCount + ')' : ''}`], ["available", "Доступные заказы"], ["responses", "Мои отклики"], ["my-services", "Мои услуги"], ["invoices", "Счета"], ["materials", "Материалы"], ["suppliers", "Поставщики"], ["time", "Время"], ["favorites", "Избранные"], ["chats", "Сообщения"], ["security", "Безопасность"], ["profile", "Профиль"]];
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
  return overview();
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
      </div>
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
      <button class="button button-primary full" type="submit">Опубликовать заказ</button>
    </form>`;
}

function myOrders() {
  const orders = state.orders.filter((o) => o.client_id === state.user.id);
  return `<div class="order-list">${orders.length ? orders.map((o) => `${orderCard(o)}${responsesBlock(o)}`).join("") : emptyState("Вы еще не создали заказ.", "Создать заказ", 'data-tab="new-order"')}</div>`;
}

function responsesBlock(order) {
  if (!order.responses?.length) return "";
  return `<div class="panel response-list" id="responses-${order.id}">
    <h3>Отклики на "${escapeHtml(order.title)}"</h3>
    ${order.responses.map((r) => `
      <article class="maker-card">
        <div class="maker-card-header">
          <div><h3>${escapeHtml(r.maker_name)}</h3><p>${escapeHtml(r.maker_city)} · ${r.days} дней · ${money(r.price)}</p></div>
          ${order.selected_maker_id === r.maker_id ? '<span class="status status-progress">Выбран</span>' : ""}
        </div>
        <p>${escapeHtml(r.message)}</p>
        <div class="actions">
          <button class="button button-secondary button-small" type="button" data-open-chat="${order.id}">Открыть чат</button>
          ${order.status === "open" ? `<button class="button button-primary button-small" type="button" data-choose-maker="${order.id}:${r.maker_id}">Выбрать</button>` : ""}
        </div>
      </article>`).join("")}
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
          <button class="button button-primary" type="submit">${isEdit ? "Сохранить" : "Добавить"}</button>
        </form>
      </section>
    </div>`;
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
      ${user.role === "maker" ? `
        <label>Мощность <input name="capacity" value="${escapeHtml(user.capacity || "")}" placeholder="до 80 изделий в месяц"></label>
        <label class="full">Компетенции <input name="skills" value="${escapeHtml((user.skills || []).join(", "))}" placeholder="Кухни, шкафы, монтаж"></label>
      ` : '<input type="hidden" name="capacity" value=""><input type="hidden" name="skills" value="">'}
      <label class="full">Описание <textarea name="about" rows="4">${escapeHtml(user.about || "")}</textarea></label>
      <button class="button button-primary full" type="submit">Сохранить профиль</button>
    </form>
    <h3 style="margin:24px 0 8px">Смена пароля</h3>
    <form class="stack-form grid-form" id="changePwForm">
      <label class="full">Текущий пароль <input name="old_password" type="password" required></label>
      <label class="full">Новый пароль <input name="new_password" type="password" minlength="6" placeholder="Не короче 6 символов" required></label>
      <button class="button button-secondary full" type="submit">Сменить пароль</button>
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
  }
}

async function render() {
  stopDeadlineTimers();
  renderHeader();

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
  } else if (state.view === "dashboard") {
    await Promise.all([loadOrders(), loadRegions()]);
    if (state.user?.role === "maker") {
      const data = await api(`/api/services?user_id=${state.user.id}`);
      state.services = data.services;
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
  if (state.adminTab === "activity") return adminActivityLog();
  return adminOverview();
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
  const target = event.target.closest("button, [data-close-modal], [data-close-response], [data-close-service], [data-company-id]");
  if (!target) return;
  try {
    if (target.dataset.view) return setView(target.dataset.view);
    if (target.dataset.auth) {
      if (target.dataset.role) registerForm.elements.role.value = target.dataset.role;
      return openAuth(target.dataset.auth);
    }
    if (target.dataset.closeModal !== undefined) return closeAuth();
    if (target.dataset.closeResponse !== undefined) return document.querySelector("#responseModal")?.remove();
    if (target.dataset.closeService !== undefined) return document.querySelector("#serviceModal")?.remove();
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
      if (s) serviceFormModal(s);
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
    if (target.dataset.notifLink) {
      const link = target.dataset.notifLink;
      const notifId = target.dataset.notifId;
      if (notifId) await api(`/api/notifications/${notifId}`, { method: "POST", body: JSON.stringify({}) });
      document.getElementById("notificationsPanel")?.remove();
      if (link.startsWith("/company/")) {
        state.activeCompanyId = Number(link.split("/")[2]);
        state.view = "company";
      } else if (link === "/market") {
        state.view = "market";
      } else if (link === "/chat") {
        state.view = "dashboard"; state.dashboardTab = "chats";
      }
      return render();
    }
    if (target.dataset.deleteDocument) {
      if (!confirm("Удалить документ?")) return;
      await api(`/api/documents/${target.dataset.deleteDocument}`, { method: "DELETE" });
      showToast("Документ удалён", "success");
      return render();
    }
    if (target.dataset.companyId) {
      state.activeCompanyId = Number(target.dataset.companyId);
      state.view = "company";
      return render();
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
    if (event.target.id === "responseForm") {
      const orderId = event.target.dataset.orderId;
      const data = Object.fromEntries(new FormData(event.target));
      await api(`/api/orders/${orderId}/responses`, { method: "POST", body: JSON.stringify(data) });
      document.querySelector("#responseModal")?.remove();
      state.dashboardTab = "responses"; state.view = "dashboard";
      return render();
    }
    if (event.target.id === "serviceForm") {
      const data = Object.fromEntries(new FormData(event.target));
      const modal = document.querySelector("#serviceModal");
      const isEdit = modal?.querySelector("[data-edit-service]");
      if (isEdit) {
        await api(`/api/services/${isEdit.dataset.editService}`, { method: "PUT", body: JSON.stringify(data) });
        showToast("Услуга обновлена", "success");
      } else {
        await api("/api/services", { method: "POST", body: new FormData(event.target) });
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
    if (event.target.id === "changePwForm") {
      const data = Object.fromEntries(new FormData(event.target));
      await api("/api/change-password", { method: "POST", body: JSON.stringify(data) });
      showToast("Пароль изменён. Войдите заново.", "success");
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
      const data = Object.fromEntries(new FormData(event.target));
      Object.keys(data).forEach(k => data[k] = data[k] === "on" ? 1 : 0);
      await api("/api/notifications/preferences", { method: "POST", body: JSON.stringify(data) });
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
    const result = await api("/api/register", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(registerForm))) });
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


document.querySelectorAll("[data-auth-tab]").forEach((b) => b.addEventListener("click", () => setAuthTab(b.dataset.authTab)));

const savedTheme = localStorage.getItem("meblio-theme");
if (savedTheme) document.documentElement.setAttribute("data-theme", savedTheme);

loadSession().then(render).catch((error) => {
  app.innerHTML = `<section class="section"><div class="container"><div class="empty">${escapeHtml(error.message)}</div></div></section>`;
});

const resetToken = new URLSearchParams(location.search).get("token");
if (location.pathname.startsWith("/reset-password") && resetToken) {
  document.title = "Восстановление пароля — Meblio";
  renderResetPassword(resetToken);
}
