// Интерфейс RSS News Filter: обычный JavaScript поверх API (/api/...), без сборки и зависимостей.
"use strict";

const POLL_MS = 5000;        // как часто опрашиваем ленту и каналы
const FEED_PAGE = 50;        // сколько статей просим за раз
const FRESH_MS = 60000;      // сколько держится пометка «новое»

const state = {
  users: [],
  userId: null,
  tab: "feed",
  filters: [],
  myChannels: [],
  allChannels: [],
  posts: [],
  feed: [],
  feedLimit: FEED_PAGE,
  feedSignature: "",
  feedChip: null,            // id фильтра, по которому сужена лента (null — все)
  seen: null,                // id статей, которые пользователь уже видел
  fresh: new Map(),          // id статьи -> когда она появилась
  lastFeedAt: null,
  feedError: null,
};

// ---------- утилиты ----------

const $ = (sel) => document.querySelector(sel);

/** Создаёт элемент. Текст всегда вставляется как текст, не как HTML. */
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

function storageGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* приватный режим — не страшно */ }
}

function toast(message, isError = false) {
  const el = h("div", { class: "toast" + (isError ? " err" : "") }, message);
  $("#toasts").append(el);
  setTimeout(() => el.remove(), isError ? 6000 : 3500);
}

const relFmt = new Intl.RelativeTimeFormat("ru", { numeric: "auto" });
const absFmt = new Intl.DateTimeFormat("ru", { dateStyle: "medium", timeStyle: "short" });
const timeFmt = new Intl.DateTimeFormat("ru", { timeStyle: "medium" });

function relTime(iso) {
  if (!iso) return "";
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return "только что";
  if (abs < 3600) return relFmt.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return relFmt.format(Math.round(diff / 3600), "hour");
  if (abs < 86400 * 7) return relFmt.format(Math.round(diff / 86400), "day");
  return absFmt.format(new Date(iso));
}

function timeEl(iso) {
  if (!iso) return null;
  return h("time", { datetime: iso, title: absFmt.format(new Date(iso)) }, relTime(iso));
}

const KIND_LABEL = { keyword: "слова", topic: "тема" };

// ---------- API ----------

function errorText(detail, status) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // ошибки валидации FastAPI: [{loc, msg, ...}]
    return detail.map((d) => String(d.msg || d).replace(/^Value error,\s*/, "")).join("; ");
  }
  return `ошибка сервера (${status})`;
}

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("сервер недоступен");
  }
  if (response.status === 204) return null;
  let data = null;
  try { data = await response.json(); } catch { /* пустой или не JSON */ }
  if (!response.ok) throw new Error(errorText(data && data.detail, response.status));
  return data;
}

/** Блокирует кнопку на время запроса и показывает ошибку тостом. */
async function withBusy(button, busyText, fn) {
  const original = button ? button.textContent : "";
  if (button) { button.disabled = true; if (busyText) button.textContent = busyText; }
  try {
    return await fn();
  } catch (err) {
    toast(err.message, true);
    return undefined;
  } finally {
    if (button) { button.disabled = false; button.textContent = original; }
  }
}

// ---------- пользователи ----------

async function loadUsers() {
  state.users = await api("GET", "/api/users");
  const saved = Number(storageGet("userId"));
  const ids = state.users.map((u) => u.id);
  if (!ids.includes(state.userId)) {
    state.userId = ids.includes(saved) ? saved : (ids[0] ?? null);
  }
  renderUsers();
}

function renderUsers() {
  const select = $("#user-select");
  select.replaceChildren(
    ...state.users.map((u) => h("option", { value: u.id, selected: u.id === state.userId }, u.name))
  );
  const has = state.userId !== null;
  const current = state.users.find((u) => u.id === state.userId);
  $("#user-script").textContent = current ? current.name.toLowerCase() : "добро пожаловать";
  select.disabled = !has;
  $("#user-delete").hidden = !has;
  $("#no-user").hidden = has;
  document.querySelector(".tabs").hidden = !has;
  if (!has) document.querySelectorAll("[role=tabpanel]").forEach((p) => (p.hidden = true));
}

async function selectUser(id) {
  state.userId = id;
  storageSet("userId", String(id));
  state.feed = [];
  state.feedSignature = "";
  state.feedLimit = FEED_PAGE;
  state.feedChip = null;
  state.seen = null;
  state.fresh.clear();
  renderUsers();
  showTab(state.tab);
  await refreshAll();
}

async function createUser(name) {
  const user = await api("POST", "/api/users", { name });
  toast(`Пользователь «${user.name}» создан и подписан на тестовый канал`);
  await loadUsers();
  await selectUser(user.id);
}

// ---------- вкладки ----------

const TABS = ["feed", "filters", "channels", "own"];

function showTab(tab) {
  if (!TABS.includes(tab)) tab = "feed";
  state.tab = tab;
  if (location.hash !== "#" + tab) history.replaceState(null, "", "#" + tab);
  document.querySelectorAll(".tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  if (state.userId === null) return;
  for (const t of TABS) $("#tab-" + t).hidden = t !== tab;
}

async function refreshAll() {
  if (state.userId === null) return;
  await Promise.all([loadFilters(), loadChannels(), loadFeed(), loadPosts()]);
}

// ---------- лента ----------

async function loadFeed() {
  const userId = state.userId;
  if (userId === null) return;
  let feed;
  try {
    feed = await api("GET", `/api/users/${userId}/feed?limit=${state.feedLimit}`);
    state.feedError = null;
  } catch (err) {
    state.feedError = err.message;
    renderFeedStatus();
    return;
  }
  if (userId !== state.userId) return; // пока ждали ответ, переключили пользователя

  const now = Date.now();
  if (state.seen === null) {
    state.seen = new Set(feed.map((e) => e.article_id));
  } else {
    for (const entry of feed) {
      if (!state.seen.has(entry.article_id)) {
        state.seen.add(entry.article_id);
        state.fresh.set(entry.article_id, now);
      }
    }
  }
  for (const [id, at] of state.fresh) if (now - at > FRESH_MS) state.fresh.delete(id);

  state.lastFeedAt = new Date();
  const signature = JSON.stringify(feed) + "|" + [...state.fresh.keys()].join(",");
  if (signature !== state.feedSignature) {
    state.feed = feed;
    state.feedSignature = signature;
    renderFeed();
  }
  renderFeedStatus();
}

function renderFeedStatus() {
  const el = $("#feed-status");
  if (state.feedError) {
    el.replaceChildren(h("span", { class: "error-text" }, "Не удалось обновить: " + state.feedError));
    return;
  }
  const n = state.feed.length;
  const parts = [`${n} ${plural(n, "статья", "статьи", "статей")}`];
  if (state.lastFeedAt) parts.push("обновлено в " + timeFmt.format(state.lastFeedAt));
  el.textContent = parts.join(" · ");
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

/** Из причин keyword-движка достаём найденные фрагменты, чтобы подсветить их в тексте. */
function highlightTerms(matches) {
  const terms = new Set();
  for (const m of matches) {
    if (m.kind !== "keyword") continue;
    for (const reason of m.reasons) {
      // «... в заголовке: «санкциями»» или «... в тексте: «санкциями, новыми»»
      const found = reason.match(/:\s*«([^»]+)»/);
      if (!found) continue;
      for (const word of found[1].split(/,\s*/)) if (word.trim().length >= 2) terms.add(word.trim());
    }
  }
  return [...terms];
}

function withMarks(text, terms) {
  if (!terms.length || !text) return [text];
  const escaped = terms.sort((a, b) => b.length - a.length).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`(${escaped.join("|")})`, "giu");
  return text.split(re).map((part, i) => (i % 2 === 1 ? h("mark", null, part) : part));
}

function renderMatch(m) {
  const head = [
    h("span", { class: "badge " + m.kind }, KIND_LABEL[m.kind] || m.kind),
    h("b", null, "«" + m.query + "»"),
  ];
  if (m.kind === "topic") {
    const pct = Math.max(0, Math.min(1, m.score)) * 100;
    head.push(h("span", { class: "score muted", title: "сходство с темой" },
      h("span", { class: "score-bar" }, h("i", { style: `width:${pct.toFixed(0)}%` })),
      m.score.toFixed(2)));
  }
  return h("div", { class: "why-row" }, head, h("ul", null, m.reasons.map((r) => h("li", null, r))));
}

function renderFeed() {
  renderChips();
  const list = $("#feed-list");
  const entries = state.feedChip === null
    ? state.feed
    : state.feed.filter((e) => e.matches.some((m) => m.filter_id === state.feedChip));

  $("#feed-count").textContent = state.feed.length ? String(state.feed.length) : "";
  $("#feed-more").hidden = state.feed.length < state.feedLimit || state.feedLimit >= 500;

  if (!entries.length) {
    list.replaceChildren(h("div", { class: "empty" }, emptyFeedText()));
    return;
  }

  list.replaceChildren(...entries.map((e) => {
    const terms = highlightTerms(e.matches);
    const isFresh = state.fresh.has(e.article_id);
    const summary = e.summary
      ? h("p", { class: "summary clamp", title: "нажмите, чтобы развернуть",
          onclick: (ev) => ev.currentTarget.classList.toggle("clamp") }, withMarks(e.summary, terms))
      : null;
    const title = e.link
      ? h("a", { href: e.link, target: "_blank", rel: "noopener noreferrer" }, withMarks(e.title, terms))
      : withMarks(e.title, terms);
    return h("article", { class: "article" + (isFresh ? " fresh" : "") },
      h("div", { class: "meta" },
        isFresh ? h("span", { class: "badge new" }, "новое") : null,
        h("span", null, e.channel_title),
        h("span", null, "·"),
        timeEl(e.published_at || e.fetched_at)),
      h("h4", null, title),
      summary,
      h("div", { class: "why" },
        h("div", { class: "why-title" }, "Почему в ленте:"),
        e.matches.map(renderMatch)));
  }));
}

function emptyFeedText() {
  if (state.feedChip !== null) return "По этому фильтру статей нет.";
  if (!state.filters.length) return "У вас нет фильтров — добавьте их на вкладке «Фильтры», и сюда попадут подходящие статьи.";
  if (!state.myChannels.length) return "Вы не подписаны ни на один канал — добавьте каналы на вкладке «Каналы».";
  return "Пока ни одна статья не прошла ваши фильтры. Лента обновляется сама.";
}

function renderChips() {
  const counts = new Map();
  for (const e of state.feed) for (const m of e.matches) counts.set(m.filter_id, (counts.get(m.filter_id) || 0) + 1);
  if (state.feedChip !== null && !state.filters.some((f) => f.id === state.feedChip)) state.feedChip = null;
  if (state.filters.length < 2) { $("#feed-chips").replaceChildren(); return; }

  const chip = (id, label, n) => h("button", {
    type: "button", class: "chip", "aria-pressed": String(state.feedChip === id),
    onclick: () => { state.feedChip = id; renderFeed(); },
  }, `${label} · ${n}`);

  $("#feed-chips").replaceChildren(
    chip(null, "Все", state.feed.length),
    ...state.filters.map((f) => chip(f.id, `${KIND_LABEL[f.kind]}: ${f.query}`, counts.get(f.id) || 0)),
  );
}

// ---------- фильтры ----------

async function loadFilters() {
  const userId = state.userId;
  const filters = await api("GET", `/api/users/${userId}/filters`).catch((e) => { toast(e.message, true); return null; });
  if (filters === null || userId !== state.userId) return;
  state.filters = filters;
  renderFilters();
  renderChips();
}

function renderFilters() {
  $("#filters-count").textContent = state.filters.length ? String(state.filters.length) : "";
  const list = $("#filter-list");
  if (!state.filters.length) {
    list.replaceChildren(h("div", { class: "empty" }, "Фильтров пока нет. Без фильтров лента пустая."));
    return;
  }
  list.replaceChildren(...state.filters.map((f) => {
    const n = f.matches_count;
    const passed = `прошло ${n} ${plural(n, "статья", "статьи", "статей")}`;
    const sub = f.kind === "topic"
      ? "поиск по смыслу"
      : "слова и фразы с учётом словоформ и опечаток";
    return h("div", { class: "item" },
      h("span", { class: "badge " + f.kind }, KIND_LABEL[f.kind]),
      h("div", { class: "main" },
        h("div", { class: "title" }, f.query),
        h("div", { class: "sub" }, sub, " · ", h("b", null, passed), " · добавлен ", timeEl(f.created_at)),
        f.kind === "topic" ? thresholdControl(f) : null),
      h("div", { class: "actions" },
        h("button", { type: "button", class: "btn btn-ghost btn-small btn-danger-text",
          onclick: (ev) => deleteFilter(f, ev.currentTarget) }, "Удалить")));
  }));
}

/** Ползунок порога у созданной темы: двигаешь — видно значение, отпускаешь — пересчёт. */
function thresholdControl(f) {
  const value = h("output", { class: "mono" }, f.effective_threshold.toFixed(2));
  const note = h("span", { class: "muted small" }, f.threshold === null ? "по умолчанию" : "свой");
  const range = h("input", {
    type: "range", min: "0", max: "1", step: "0.01", value: String(f.effective_threshold),
    "aria-label": `Порог сходства для темы «${f.query}»`,
    oninput: () => { value.textContent = Number(range.value).toFixed(2); note.textContent = "отпустите, чтобы применить"; },
    onchange: () => updateThreshold(f, Number(range.value), range, note),
  });
  const reset = f.threshold === null ? null : h("button", {
    type: "button", class: "btn btn-ghost btn-small", title: "вернуть порог по умолчанию",
    onclick: () => updateThreshold(f, null, range, note),
  }, "по умолчанию");
  return h("div", { class: "tune" }, h("span", { class: "small" }, "порог"), range, value, note, reset);
}

async function updateThreshold(f, threshold, range, note) {
  if (threshold !== null && threshold === f.threshold) return;
  range.disabled = true;
  note.textContent = "пересчитываю…";
  try {
    const updated = await api("PATCH", `/api/users/${state.userId}/filters/${f.id}`, { threshold });
    const n = updated.matches_count;
    toast(`Порог ${updated.effective_threshold.toFixed(2)} для «${f.query}»: ${n} ${plural(n, "статья", "статьи", "статей")} (было ${f.matches_count})`);
  } catch (err) {
    toast(err.message, true);
  }
  await Promise.all([loadFilters(), loadFeed()]);
}

async function deleteFilter(f, button) {
  await withBusy(button, "…", async () => {
    await api("DELETE", `/api/users/${state.userId}/filters/${f.id}`);
    toast(`Фильтр «${f.query}» удалён`);
    await Promise.all([loadFilters(), loadFeed()]);
  });
}

function updateKindUi() {
  const kind = document.querySelector("#filter-form input[name=kind]:checked").value;
  const query = $("#filter-query");
  if (kind === "topic") {
    query.placeholder = "например: освоение космоса";
    $("#kind-hint").textContent = "Тема своими словами — ищется по смыслу, на русском и английском. Чем выше порог, тем строже отбор.";
  } else {
    query.placeholder = "например: санкции или «новые санкции»";
    $("#kind-hint").textContent = "Слово или фраза. Учитываются словоформы и одна опечатка: «санкции» найдёт «санкциями».";
  }
  $("#threshold-box").hidden = kind !== "topic";
}

function updateThresholdUi() {
  const custom = $("#threshold-custom").checked;
  const range = $("#threshold-range");
  range.disabled = !custom;
  $("#threshold-value").textContent = custom ? Number(range.value).toFixed(2) : "по умолчанию";
}

async function submitFilter(ev) {
  ev.preventDefault();
  const form = ev.currentTarget;
  const kind = form.kind.value;
  const body = { kind, query: form.query.value.trim() };
  if (!body.query) return;
  if (kind === "topic" && $("#threshold-custom").checked) body.threshold = Number($("#threshold-range").value);

  // тема считается моделью по всем статьям пользователя — это может занять несколько секунд
  await withBusy($("#filter-submit"), kind === "topic" ? "Считаю…" : "Добавляю…", async () => {
    const flt = await api("POST", `/api/users/${state.userId}/filters`, body);
    form.query.value = "";
    await Promise.all([loadFilters(), loadFeed()]);
    const n = flt.matches_count;
    toast(`Фильтр добавлен: ${n} ${plural(n, "статья", "статьи", "статей")} в ленте`);
    // пустой результат на демо выглядит как поломка — подсказываем, что делать
    if (n === 0 && kind === "topic") {
      toast("Ни одна статья не набрала нужного сходства. Для коротких новостей попробуйте «свой порог» пониже, например 0.30.");
    } else if (n === 0) {
      toast("Пока статей с этими словами нет — новые попадут в ленту сами, как только появятся.");
    }
  });
}

// ---------- каналы ----------

async function loadChannels() {
  const userId = state.userId;
  let mine, all;
  try {
    [mine, all] = await Promise.all([api("GET", `/api/users/${userId}/channels`), api("GET", "/api/channels")]);
  } catch (err) {
    toast(err.message, true);
    return;
  }
  if (userId !== state.userId) return;
  state.myChannels = mine;
  state.allChannels = all;
  renderChannels();
}

function channelStatus(c) {
  if (c.last_error) return [h("span", { class: "status-dot err", title: "ошибка" }), h("div", { class: "error-text" }, "Ошибка: " + c.last_error)];
  if (c.last_fetched_at) return [h("span", { class: "status-dot ok", title: "работает" }), h("span", null, "скачан ", timeEl(c.last_fetched_at))];
  return [h("span", { class: "status-dot", title: "ещё не скачивался" }), h("span", null, "ещё не скачивался…")];
}

function channelItem(c, actions) {
  const [dot, statusText] = channelStatus(c);
  return h("div", { class: "item" },
    dot,
    h("div", { class: "main" },
      h("div", { class: "title" },
        c.title || c.url, " ",
        c.is_test ? h("span", { class: "badge test" }, "наш канал") : null),
      c.title ? h("div", { class: "sub mono" }, c.url) : null,
      h("div", { class: "sub" }, statusText)),
    h("div", { class: "actions" }, actions));
}

function renderChannels() {
  const mineIds = new Set(state.myChannels.map((c) => c.id));
  $("#channels-count").textContent = state.myChannels.length ? String(state.myChannels.length) : "";

  $("#my-channels").replaceChildren(...(state.myChannels.length
    ? state.myChannels.map((c) => channelItem(c, [
        h("button", { type: "button", class: "btn btn-ghost btn-small", onclick: (ev) => refreshChannel(c, ev.currentTarget) }, "Обновить"),
        h("button", { type: "button", class: "btn btn-ghost btn-small btn-danger-text", onclick: (ev) => unsubscribe(c, ev.currentTarget) }, "Отписаться"),
      ]))
    : [h("div", { class: "empty" }, "Нет подписок. Добавьте канал по ссылке выше.")]));

  const others = state.allChannels.filter((c) => !mineIds.has(c.id));
  $("#other-channels").replaceChildren(...(others.length
    ? others.map((c) => channelItem(c, [
        h("button", { type: "button", class: "btn btn-small", onclick: (ev) => subscribe(c, ev.currentTarget) }, "Подписаться"),
      ]))
    : [h("div", { class: "empty" }, "Других каналов нет.")]));
}

async function submitChannel(ev) {
  ev.preventDefault();
  const form = ev.currentTarget;
  const url = form.url.value.trim();
  await withBusy(form.querySelector("button"), "Добавляю…", async () => {
    const c = await api("POST", `/api/users/${state.userId}/channels`, { url });
    form.url.value = "";
    toast(c.last_fetched_at ? `Подписка на «${c.title || c.url}» оформлена` : "Канал добавлен, скачивается в фоне…");
    await Promise.all([loadChannels(), loadFeed()]);
  });
}

async function subscribe(c, button) {
  await withBusy(button, "…", async () => {
    await api("PUT", `/api/users/${state.userId}/channels/${c.id}`);
    toast(`Подписка на «${c.title || c.url}» оформлена`);
    await Promise.all([loadChannels(), loadFeed()]);
  });
}

async function unsubscribe(c, button) {
  await withBusy(button, "…", async () => {
    await api("DELETE", `/api/users/${state.userId}/channels/${c.id}`);
    toast(`Вы отписались от «${c.title || c.url}»`);
    await Promise.all([loadChannels(), loadFeed()]);
  });
}

async function refreshChannel(c, button) {
  await withBusy(button, "Скачиваю…", async () => {
    const updated = await api("POST", `/api/channels/${c.id}/refresh`);
    if (updated.last_error) toast("Канал не скачался: " + updated.last_error, true);
    else toast(`«${updated.title || updated.url}» обновлён`);
    await Promise.all([loadChannels(), loadFeed()]);
  });
}

// ---------- наш канал ----------

async function loadPosts() {
  try {
    state.posts = await api("GET", "/api/own-channel/posts");
  } catch (err) {
    toast(err.message, true);
    return;
  }
  renderPosts();
}

function renderPosts() {
  const list = $("#post-list");
  if (!state.posts.length) {
    list.replaceChildren(h("div", { class: "empty" }, "Постов пока нет."));
    return;
  }
  list.replaceChildren(...state.posts.map((p) => h("div", { class: "item" },
    h("div", { class: "main" },
      h("div", { class: "title" }, p.title),
      p.body ? h("div", { class: "sub" }, p.body.length > 240 ? p.body.slice(0, 240) + "…" : p.body) : null,
      h("div", { class: "sub" }, timeEl(p.created_at))))));
}

async function submitPost(ev) {
  ev.preventDefault();
  const form = ev.currentTarget;
  const body = { title: form.title.value.trim(), body: form.body.value.trim() };
  if (!body.title) return;
  await withBusy($("#post-submit"), "Публикую…", async () => {
    const post = await api("POST", "/api/own-channel/posts", body);
    form.reset();
    await Promise.all([loadPosts(), loadFeed()]);
    const testChannel = state.myChannels.find((c) => c.is_test);
    const inFeed = testChannel && state.feed.some((e) => e.channel_id === testChannel.id && e.title === post.title);
    if (inFeed) toast("Опубликовано — пост прошёл ваши фильтры и уже в ленте");
    else if (!testChannel) toast("Опубликовано. Вы не подписаны на наш канал, поэтому в вашей ленте поста не будет");
    else toast("Опубликовано. В вашу ленту пост не попал: он не прошёл ни один ваш фильтр");
  });
}

// ---------- запуск ----------

function bind() {
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));

  $("#user-select").addEventListener("change", (ev) => selectUser(Number(ev.target.value)));
  // новый пользователь: форма прямо в шапке вместо prompt()
  const newForm = $("#user-new-form");
  const toggleNewForm = (open) => {
    newForm.hidden = !open;
    for (const id of ["#user-select", "#user-add", "#user-delete"]) $(id).hidden = open || (id === "#user-delete" && state.userId === null);
    if (open) newForm.name.focus(); else newForm.reset();
  };
  $("#user-add").addEventListener("click", () => toggleNewForm(true));
  $("#user-new-cancel").addEventListener("click", () => toggleNewForm(false));
  newForm.addEventListener("keydown", (ev) => { if (ev.key === "Escape") toggleNewForm(false); });
  newForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = newForm.name.value.trim();
    if (!name) return;
    await withBusy(newForm.querySelector("button"), "Создаю…", async () => {
      await createUser(name);
      toggleNewForm(false);
    });
  });

  // удаление: вместо confirm() — повторное нажатие в течение нескольких секунд
  const delBtn = $("#user-delete");
  let delTimer = null;
  const resetDel = () => { clearTimeout(delTimer); delTimer = null; delBtn.textContent = "Удалить"; delBtn.classList.remove("btn-confirm"); };
  delBtn.addEventListener("click", async () => {
    const user = state.users.find((u) => u.id === state.userId);
    if (!user) return;
    if (delTimer === null) {
      delBtn.textContent = `Точно удалить «${user.name}»?`;
      delBtn.classList.add("btn-confirm");
      delTimer = setTimeout(resetDel, 4000);
      return;
    }
    resetDel();
    await withBusy(delBtn, "Удаляю…", async () => {
      await api("DELETE", `/api/users/${user.id}`);
      toast(`Пользователь «${user.name}» удалён вместе с фильтрами и подписками`);
      state.userId = null;
      await loadUsers();
      if (state.userId !== null) await selectUser(state.userId);
    });
  });
  $("#user-select").addEventListener("change", resetDel);
  $("#first-user-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = ev.currentTarget.name.value.trim();
    if (!name) return;
    try { await createUser(name); ev.target.reset(); } catch (err) { toast(err.message, true); }
  });

  $("#feed-reload").addEventListener("click", (ev) => withBusy(ev.currentTarget, null, loadFeed));
  $("#feed-autorefresh").addEventListener("change", (ev) => storageSet("autorefresh", ev.target.checked ? "1" : "0"));
  $("#feed-more").addEventListener("click", (ev) => {
    state.feedLimit = Math.min(500, state.feedLimit + FEED_PAGE);
    withBusy(ev.currentTarget, "Загружаю…", loadFeed);
  });

  document.querySelectorAll("#filter-form input[name=kind]").forEach((r) => r.addEventListener("change", updateKindUi));
  $("#threshold-custom").addEventListener("change", updateThresholdUi);
  $("#threshold-range").addEventListener("input", updateThresholdUi);
  $("#filter-form").addEventListener("submit", submitFilter);
  $("#channel-form").addEventListener("submit", submitChannel);
  $("#post-form").addEventListener("submit", submitPost);
}

function poll() {
  if (document.hidden || state.userId === null || !$("#feed-autorefresh").checked) return;
  loadFeed();
  // каналы скачиваются в фоне — на их вкладке показываем свежий статус
  if (state.tab === "channels") loadChannels();
}

async function start() {
  bind();
  if (storageGet("autorefresh") === "0") $("#feed-autorefresh").checked = false;
  updateKindUi();
  updateThresholdUi();
  showTab(location.hash.slice(1) || "feed");
  try {
    await loadUsers();
  } catch (err) {
    toast(err.message, true);
    return;
  }
  if (state.userId !== null) await selectUser(state.userId);
  setInterval(poll, POLL_MS);
  document.addEventListener("visibilitychange", poll);
}

start();
