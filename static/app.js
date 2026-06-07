const els = {};
const ZOOM_LEVELS = [75, 90, 100, 110, 125, 150];
const BROWSER_PREF_KEY = "gridplayer-iptv-ui";
const SIDEBAR_MIN_WIDTH = 180;
const SIDEBAR_MAX_WIDTH = 420;
const PLAYER_PATH_FIELDS = {
  gridplayer: "gridplayer_path",
  mpv: "mpv_path",
  vlc: "vlc_path",
};
const GAME_SORT_RANK = {
  live: 0,
  scheduled: 1,
  inactive: 2,
  final: 3,
  unknown: 4,
  stream: 5,
};
let appState = null;
let selectedChannelId = null;
let filters = {
  sourceId: "all",
  category: "all",
  kind: "all",
  search: "",
};
let sortState = {
  key: null,
  direction: "default",
};
let uiPrefs = {
  zoom: 100,
  sidebarWidth: 250,
};
let sidebarResize = null;
let sportsRefreshTimer = null;

function $(id) {
  return document.getElementById(id);
}

function initials(name) {
  return String(name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function sourceName(sourceId) {
  const source = appState?.sources.find((item) => item.id === sourceId);
  return source?.name || "Unknown source";
}

function playerById(playerId) {
  return appState?.players?.items?.find((player) => player.id === playerId) || null;
}

function selectedPlayerId() {
  const selected = appState?.players?.selected || appState?.settings?.selected_player || "gridplayer";
  return PLAYER_PATH_FIELDS[selected] ? selected : "gridplayer";
}

function selectedPlayerLabel() {
  return playerById(selectedPlayerId())?.label || "GridPlayer";
}

function playerPathValue(playerId) {
  const pathField = PLAYER_PATH_FIELDS[playerId];
  if (!pathField) return "";
  return appState?.settings?.[pathField] || playerById(playerId)?.path || "";
}

function channelById(channelId) {
  return appState?.channels.find((channel) => channel.id === channelId) || null;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!payload.success) {
    throw new Error(payload.error || "Request failed");
  }
  return payload.data;
}

function showToast(message, kind = "info") {
  els.toast.textContent = message;
  els.toast.className = `toast ${kind === "error" ? "error" : ""}`;
  els.toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    els.toast.hidden = true;
  }, 3200);
}

async function loadState() {
  const data = await api("/api/state");
  appState = data;
  filters.sourceId = filters.sourceId || data.selected_source_id || "all";
  hydrateUiPrefs();
  render();
  scheduleSportsRefresh();
}

function scheduleSportsRefresh() {
  window.clearInterval(sportsRefreshTimer);
  const seconds = Number(appState?.sports?.refresh_seconds) || 1800;
  sportsRefreshTimer = window.setInterval(async () => {
    try {
      appState = await api("/api/state");
      render();
    } catch (error) {
      showToast(error.message, "error");
    }
  }, seconds * 1000);
}

function desktopRuntime() {
  return Boolean(appState?.runtime?.desktop);
}

function readBrowserPrefs() {
  try {
    return JSON.parse(window.localStorage.getItem(BROWSER_PREF_KEY) || "{}");
  } catch {
    return {};
  }
}

function writeBrowserPrefs(nextPrefs) {
  try {
    window.localStorage.setItem(BROWSER_PREF_KEY, JSON.stringify(nextPrefs));
  } catch {
    // localStorage can be unavailable in restricted browser contexts.
  }
}

function nearestZoom(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 100;
  return ZOOM_LEVELS.reduce((closest, level) => (
    Math.abs(level - numeric) < Math.abs(closest - numeric) ? level : closest
  ), ZOOM_LEVELS[0]);
}

function clampSidebarWidth(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 250;
  return Math.min(Math.max(Math.round(numeric), SIDEBAR_MIN_WIDTH), SIDEBAR_MAX_WIDTH);
}

function hydrateUiPrefs() {
  const browserPrefs = readBrowserPrefs();
  uiPrefs.zoom = nearestZoom(desktopRuntime() ? appState?.settings?.ui_zoom : browserPrefs.ui_zoom);
  uiPrefs.sidebarWidth = clampSidebarWidth(desktopRuntime() ? appState?.settings?.ui_sidebar_width : browserPrefs.ui_sidebar_width);
  applyZoom();
  applySidebarWidth();
}

function applyZoom() {
  document.documentElement.style.setProperty("--ui-zoom", String(uiPrefs.zoom / 100));
  if (els.zoomValue) {
    els.zoomValue.textContent = `${uiPrefs.zoom}%`;
  }
  if (els.zoomOutButton) {
    els.zoomOutButton.disabled = uiPrefs.zoom <= ZOOM_LEVELS[0];
  }
  if (els.zoomInButton) {
    els.zoomInButton.disabled = uiPrefs.zoom >= ZOOM_LEVELS[ZOOM_LEVELS.length - 1];
  }
}

async function persistUiPreference(key, value) {
  if (desktopRuntime()) {
    const data = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ [key]: value }),
    });
    appState = data.state;
    return;
  }

  writeBrowserPrefs({ ...readBrowserPrefs(), [key]: value });
}

function applySidebarWidth() {
  document.documentElement.style.setProperty("--sidebar-width", `${uiPrefs.sidebarWidth}px`);
}

async function changeZoom(direction) {
  const index = ZOOM_LEVELS.indexOf(uiPrefs.zoom);
  const nextIndex = Math.min(Math.max(index + direction, 0), ZOOM_LEVELS.length - 1);
  const nextZoom = ZOOM_LEVELS[nextIndex];
  if (nextZoom === uiPrefs.zoom) return;

  uiPrefs.zoom = nextZoom;
  applyZoom();
  try {
    await persistUiPreference("ui_zoom", nextZoom);
  } catch (error) {
    showToast(error.message, "error");
  }
}

function startSidebarResize(event) {
  sidebarResize = {
    startX: event.clientX,
    startWidth: uiPrefs.sidebarWidth,
  };
  els.sidebarResizer.setPointerCapture(event.pointerId);
  document.body.classList.add("is-resizing-sidebar");
}

async function finishSidebarResize() {
  if (!sidebarResize) return;
  sidebarResize = null;
  document.body.classList.remove("is-resizing-sidebar");
  try {
    await persistUiPreference("ui_sidebar_width", uiPrefs.sidebarWidth);
  } catch (error) {
    showToast(error.message, "error");
  }
}

function updateSidebarResize(event) {
  if (!sidebarResize) return;
  const zoomFactor = uiPrefs.zoom / 100 || 1;
  const delta = (event.clientX - sidebarResize.startX) / zoomFactor;
  uiPrefs.sidebarWidth = clampSidebarWidth(sidebarResize.startWidth + delta);
  applySidebarWidth();
}

function filteredChannels() {
  if (!appState) return [];
  const search = filters.search.trim().toLowerCase();
  const favorites = new Set(appState.favorites);
  const channels = appState.channels
    .filter((channel) => filters.sourceId === "all" || channel.source_id === filters.sourceId)
    .filter((channel) => filters.category === "all" || channel.group === filters.category)
    .filter((channel) => filters.kind !== "favorites" || favorites.has(channel.id))
    .filter((channel) => {
      if (!search) return true;
      return [channel.name, channel.group, sourceName(channel.source_id), channel.game?.text, channel.game?.status_long]
        .join(" ")
        .toLowerCase()
        .includes(search);
    })
    .sort((a, b) => a.order - b.order || a.name.localeCompare(b.name));

  if (!sortState.key || sortState.direction === "default") {
    return channels;
  }

  const factor = sortState.direction === "asc" ? 1 : -1;
  return [...channels].sort((a, b) => {
    let result = 0;
    if (sortState.key === "game") {
      result = compareGameStatus(a, b);
    } else {
      const left = sortState.key === "group" ? a.group : a.name;
      const right = sortState.key === "group" ? b.group : b.name;
      result = String(left || "").localeCompare(String(right || ""), undefined, {
        numeric: true,
        sensitivity: "base",
      });
    }
    return result * factor || a.order - b.order || a.name.localeCompare(b.name);
  });
}

function logoHtml(channel, extraClass = "") {
  if (channel.logo) {
    return `<span class="logo ${extraClass}"><img src="${escapeHtml(channel.logo)}" alt="" onerror="const parent=this.parentElement; this.remove(); if(parent) parent.textContent='${initials(channel.name)}';"></span>`;
  }
  return `<span class="logo ${extraClass}">${initials(channel.name)}</span>`;
}

function gameInfo(channel) {
  return channel.game || {
    kind: "stream",
    text: "Stream",
    subtext: "No game lookup",
    start_time: "",
    status_long: "No game lookup",
    matched: false,
  };
}

function compareGameStatus(leftChannel, rightChannel) {
  const left = gameInfo(leftChannel);
  const right = gameInfo(rightChannel);
  const leftRank = GAME_SORT_RANK[left.kind] ?? GAME_SORT_RANK.unknown;
  const rightRank = GAME_SORT_RANK[right.kind] ?? GAME_SORT_RANK.unknown;
  if (leftRank !== rightRank) return leftRank - rightRank;

  const leftStart = Number(left.start_timestamp) || 0;
  const rightStart = Number(right.start_timestamp) || 0;
  if (leftStart && rightStart && leftStart !== rightStart) return leftStart - rightStart;
  if (leftStart !== rightStart) return leftStart ? -1 : 1;

  return String(left.text || "").localeCompare(String(right.text || ""), undefined, {
    numeric: true,
    sensitivity: "base",
  });
}

function gameStatusHtml(channel) {
  const game = gameInfo(channel);
  const subtext = game.score || game.subtext || game.start_time || "";
  return `
    <div class="game-status ${escapeHtml(game.kind)}">
      <strong>${escapeHtml(game.text || "Unknown")}</strong>
      ${subtext ? `<small>${escapeHtml(subtext)}</small>` : ""}
    </div>
  `;
}

function detailMetaItem(label, value, note = "") {
  return `
    <span>
      ${escapeHtml(label)}
      <strong>${escapeHtml(value || "Unknown")}</strong>
      ${note ? `<small>${escapeHtml(note)}</small>` : ""}
    </span>
  `;
}

function streamType(url) {
  const cleanUrl = String(url || "").split("?")[0].toLowerCase();
  const extension = cleanUrl.match(/\.([a-z0-9]{2,5})$/)?.[1];
  if (extension) return extension.toUpperCase();
  if (cleanUrl.startsWith("http://") || cleanUrl.startsWith("https://")) return "HTTP";
  return "URL";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function render() {
  if (!appState) return;
  syncSelectedChannel();
  renderStatus();
  renderPlayerSelect();
  renderSortHeaders();
  renderSources();
  renderCategories();
  renderChannels();
  renderDetail();
}

function renderSortHeaders() {
  document.querySelectorAll("[data-sort-key]").forEach((button) => {
    const isActive = sortState.key === button.dataset.sortKey && sortState.direction !== "default";
    button.classList.toggle("active", isActive);
    button.dataset.direction = isActive ? sortState.direction : "default";
    button.setAttribute("aria-sort", isActive ? (sortState.direction === "asc" ? "ascending" : "descending") : "none");
  });
}

function syncSelectedChannel() {
  const visible = filteredChannels();
  const visibleIds = new Set(visible.map((channel) => channel.id));
  if (selectedChannelId && !visibleIds.has(selectedChannelId)) {
    selectedChannelId = null;
  }
}

function renderStatus() {
  const total = appState.channels.length;
  els.allCount.textContent = total;
  els.favoriteCount.textContent = appState.favorites.length;
}

function renderPlayerSelect() {
  if (!els.playerSelect) return;
  els.playerSelect.value = selectedPlayerId();
}

function renderSources() {
  const rows = [
    `<button class="source-row ${filters.sourceId === "all" ? "active" : ""}" data-source-id="all" type="button">
      <span class="nav-icon">≡</span>
      <span><span class="source-name">All Playlists</span><span class="source-meta">${appState.channels.length} channels</span></span>
      <span></span>
    </button>`,
    ...appState.sources.map((source) => `
      <button class="source-row ${filters.sourceId === source.id ? "active" : ""}" data-source-id="${source.id}" type="button">
        <span class="nav-icon">${source.kind === "url" ? "↗" : "▣"}</span>
        <span><span class="source-name">${escapeHtml(source.name)}</span><span class="source-meta">${source.channel_count || 0} channels</span></span>
        <span class="source-meta">${source.kind}</span>
      </button>
    `),
  ];
  els.sourceList.innerHTML = rows.join("");
}

function renderCategories() {
  const categories = ["all", ...appState.categories];
  els.categoryList.innerHTML = categories.map((category) => {
    const label = category === "all" ? "All Categories" : category;
    const count = category === "all"
      ? appState.channels.length
      : appState.channels.filter((channel) => channel.group === category).length;
    return `
      <button class="nav-row ${filters.category === category ? "active" : ""}" data-category="${escapeHtml(category)}" type="button">
        <span class="nav-icon">${category === "all" ? "◇" : "•"}</span>
        <span>${escapeHtml(label)}</span>
        <span class="count">${count}</span>
      </button>
    `;
  }).join("");
}

function renderChannels() {
  if (!appState) return;
  const channels = filteredChannels();
  const favorites = new Set(appState.favorites);
  const playerLabel = selectedPlayerLabel();
  els.resultCount.textContent = `${channels.length} visible`;
  if (channels.length === 0) {
    els.channelList.innerHTML = `<div class="empty-state">No matching channels</div>`;
    return;
  }

  els.channelList.innerHTML = channels.map((channel) => `
    <div class="channel-row ${selectedChannelId === channel.id ? "selected" : ""}" data-channel-id="${channel.id}">
      <div class="channel-main">
        ${logoHtml(channel)}
        <div class="channel-text">
          <div class="channel-name">${escapeHtml(channel.name)}</div>
          <div class="channel-subtitle">${escapeHtml(sourceName(channel.source_id))}</div>
        </div>
      </div>
      <div class="group-pill">${escapeHtml(channel.group)}</div>
      ${gameStatusHtml(channel)}
      <div class="row-actions">
        <button class="icon-button ${favorites.has(channel.id) ? "active" : ""}" data-action="favorite" title="Favorite" type="button">☆</button>
        <button class="icon-button" data-action="open" title="Open in ${escapeHtml(playerLabel)}" type="button">
          <svg viewBox="0 0 24 24"><path d="M7 17 17 7"/><path d="M9 7h8v8"/><path d="M5 5h6M5 5v14h14v-6"/></svg>
        </button>
      </div>
    </div>
  `).join("");
}

function renderDetail() {
  const channel = selectedChannelId ? channelById(selectedChannelId) : null;
  if (!channel) {
    els.detailPanel.innerHTML = `<div class="detail-empty">Click on a channel to view more info</div>`;
    return;
  }
  const game = gameInfo(channel);
  const playerLabel = selectedPlayerLabel();

  els.detailPanel.innerHTML = `
    <div class="detail-content">
      <div class="detail-main">
        ${logoHtml(channel, "detail-logo")}
        <div class="detail-text">
          <div class="detail-title">${escapeHtml(channel.name)}</div>
          <div class="detail-subtitle">${escapeHtml(channel.group)} · ${escapeHtml(sourceName(channel.source_id))}</div>
        </div>
      </div>
      <div class="detail-meta">
        ${detailMetaItem("Game", game.text, game.score || game.status_long)}
        ${detailMetaItem("Start", game.start_time || (game.kind === "stream" ? "N/A" : "Unknown"))}
        ${detailMetaItem("Source", sourceName(channel.source_id))}
        ${detailMetaItem("Stream", streamType(channel.url))}
      </div>
      <div class="detail-actions" data-channel-id="${channel.id}">
        <button class="primary-button" data-detail-action="open" type="button">
          <svg viewBox="0 0 24 24"><path d="M7 17 17 7"/><path d="M9 7h8v8"/><path d="M5 5h6M5 5v14h14v-6"/></svg>
          <span>Open in ${escapeHtml(playerLabel)}</span>
        </button>
      </div>
    </div>
  `;
}

async function refreshWithState(request) {
  const data = await request;
  appState = data.state || data;
  render();
}

async function openChannel(channelId) {
  const data = await api("/api/open", {
    method: "POST",
    body: JSON.stringify({ channel_id: channelId, player: selectedPlayerId() }),
  });
  showToast(`Opened in ${data.label || selectedPlayerLabel()}`);
}

function fillSettingsForm() {
  els.gridplayerPathInput.value = playerPathValue("gridplayer");
  els.mpvPathInput.value = playerPathValue("mpv");
  els.vlcPathInput.value = playerPathValue("vlc");
}

function openModal(modal) {
  modal.hidden = false;
  const input = modal.querySelector("input");
  if (input) input.focus();
}

function closeModals() {
  els.urlModal.hidden = true;
  els.settingsModal.hidden = true;
}

function bindEvents() {
  els.importFileButton.addEventListener("click", () => els.fileInput.click());
  els.importUrlButton.addEventListener("click", () => openModal(els.urlModal));
  els.settingsButton.addEventListener("click", () => {
    fillSettingsForm();
    openModal(els.settingsModal);
  });

  document.querySelectorAll("[data-close-modal]").forEach((button) => {
    button.addEventListener("click", closeModals);
  });

  els.fileInput.addEventListener("change", async () => {
    const file = els.fileInput.files[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    try {
      await refreshWithState(api("/api/import-file", { method: "POST", body }));
      showToast(`Imported ${file.name}`);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      els.fileInput.value = "";
    }
  });

  els.urlForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await refreshWithState(api("/api/import-url", {
        method: "POST",
        body: JSON.stringify({
          name: els.urlNameInput.value,
          url: els.urlInput.value,
        }),
      }));
      closeModals();
      els.urlForm.reset();
      showToast("Playlist URL imported");
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.settingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await refreshWithState(api("/api/settings", {
        method: "POST",
        body: JSON.stringify({
          gridplayer_path: els.gridplayerPathInput.value,
          mpv_path: els.mpvPathInput.value,
          vlc_path: els.vlcPathInput.value,
        }),
      }));
      closeModals();
      showToast("Settings saved");
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.refreshButton.addEventListener("click", async () => {
    try {
      const data = await api("/api/refresh", { method: "POST" });
      appState = data.state;
      render();
      const failed = data.errors?.length || 0;
      showToast(failed ? `Refresh finished with ${failed} issue${failed === 1 ? "" : "s"}` : "Playlists refreshed");
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.playerSelect.addEventListener("change", async () => {
    try {
      await refreshWithState(api("/api/settings", {
        method: "POST",
        body: JSON.stringify({ selected_player: els.playerSelect.value }),
      }));
      showToast(`Player set to ${selectedPlayerLabel()}`);
    } catch (error) {
      renderPlayerSelect();
      showToast(error.message, "error");
    }
  });

  els.zoomOutButton.addEventListener("click", () => {
    changeZoom(-1);
  });

  els.zoomInButton.addEventListener("click", () => {
    changeZoom(1);
  });

  els.sidebarResizer.addEventListener("pointerdown", startSidebarResize);
  window.addEventListener("pointermove", updateSidebarResize);
  window.addEventListener("pointerup", finishSidebarResize);
  window.addEventListener("pointercancel", finishSidebarResize);

  els.searchInput.addEventListener("input", () => {
    filters.search = els.searchInput.value;
    renderChannels();
  });

  document.querySelector(".table-header").addEventListener("click", (event) => {
    const button = event.target.closest("[data-sort-key]");
    if (!button) return;
    const key = button.dataset.sortKey;
    if (sortState.key !== key) {
      sortState = { key, direction: "asc" };
    } else if (sortState.direction === "asc") {
      sortState.direction = "desc";
    } else {
      sortState = { key: null, direction: "default" };
    }
    renderSortHeaders();
    renderChannels();
  });

  els.sourceList.addEventListener("click", (event) => {
    const row = event.target.closest("[data-source-id]");
    if (!row) return;
    filters.sourceId = row.dataset.sourceId;
    render();
  });

  document.querySelector("[data-filter-kind='all']").addEventListener("click", () => {
    filters.kind = "all";
    document.querySelectorAll("[data-filter-kind]").forEach((button) => button.classList.toggle("active", button.dataset.filterKind === "all"));
    renderChannels();
  });

  document.querySelector("[data-filter-kind='favorites']").addEventListener("click", () => {
    filters.kind = "favorites";
    document.querySelectorAll("[data-filter-kind]").forEach((button) => button.classList.toggle("active", button.dataset.filterKind === "favorites"));
    renderChannels();
  });

  els.categoryList.addEventListener("click", (event) => {
    const row = event.target.closest("[data-category]");
    if (!row) return;
    filters.category = row.dataset.category;
    render();
  });

  els.channelList.addEventListener("click", async (event) => {
    const row = event.target.closest("[data-channel-id]");
    if (!row) return;
    const channelId = row.dataset.channelId;
    selectedChannelId = channelId;

    const action = event.target.closest("[data-action]")?.dataset.action;
    try {
      if (action === "favorite") {
        await refreshWithState(api(`/api/channels/${channelId}/favorite`, { method: "POST" }));
      } else if (action === "open") {
        await openChannel(channelId);
      } else {
        render();
      }
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.channelList.addEventListener("dblclick", async (event) => {
    const row = event.target.closest("[data-channel-id]");
    if (!row) return;
    try {
      await openChannel(row.dataset.channelId);
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.detailPanel.addEventListener("click", async (event) => {
    const actions = event.target.closest(".detail-actions");
    const action = event.target.closest("[data-detail-action]")?.dataset.detailAction;
    if (!actions || !action) return;
    try {
      if (action === "open") {
        await openChannel(actions.dataset.channelId);
      }
    } catch (error) {
      showToast(error.message, "error");
    }
  });
}

function initEls() {
  [
    "importFileButton", "importUrlButton", "refreshButton", "settingsButton",
    "playerSelect", "zoomOutButton", "zoomValue", "zoomInButton", "sidebarResizer",
    "sourceList", "allCount", "favoriteCount", "categoryList", "resultCount",
    "searchInput", "channelList", "detailPanel", "fileInput", "urlModal",
    "urlForm", "urlNameInput", "urlInput", "settingsModal", "settingsForm",
    "gridplayerPathInput", "mpvPathInput", "vlcPathInput", "toast",
  ].forEach((id) => {
    els[id] = $(id);
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  initEls();
  bindEvents();
  try {
    await loadState();
  } catch (error) {
    showToast(error.message, "error");
  }
});
