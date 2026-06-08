const els = {};
const ZOOM_LEVELS = [75, 90, 100, 110, 125, 150];
const BROWSER_PREF_KEY = "iptv-multi-player-ui";
const SIDEBAR_MIN_WIDTH = 180;
const SIDEBAR_MAX_WIDTH = 420;
const ROW_DOUBLE_CLICK_MS = 450;
const PLAYER_PATH_FIELDS = {
  gridplayer: "gridplayer_path",
  mpv: "mpv_path",
  vlc: "vlc_path",
};
const PLAYER_PATH_INPUT_IDS = {
  gridplayer: "gridplayerPathInput",
  mpv: "mpvPathInput",
  vlc: "vlcPathInput",
};
const GAME_SORT_RANK = {
  loading: 0,
  live: 0,
  scheduled: 1,
  inactive: 2,
  final: 3,
  unknown: 4,
  stream: 5,
};
const API_REQUEST_POLICIES = {
  "GET /api/update/check": "share",
  "GET /api/update-check": "share",
  "POST /api/refresh": "share",
  "POST /api/sports/refresh": "share",
  "POST /api/update/install": "share",
  "POST /api/settings": "queue",
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
let apiKeyVisible = false;
let launchUpdateChecked = false;
let pendingUpdate = null;
let pendingSourceRemovalId = null;
let pendingSourceRenameId = null;
let categoryDrag = null;
let suppressCategoryClick = false;
let lastChannelClick = {
  id: null,
  time: 0,
};
const sharedApiRequests = new Map();
const queuedApiRequests = new Map();

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

function sourceById(sourceId) {
  return appState?.sources?.find((source) => source.id === sourceId) || null;
}

function playerById(playerId) {
  return appState?.players?.items?.find((player) => player.id === playerId) || null;
}

function selectablePlayers() {
  return (appState?.players?.items || []).filter((player) => (
    PLAYER_PATH_FIELDS[player.id]
    && player.available
    && player.path
  ));
}

function selectedPlayerId() {
  const selected = appState?.players?.selected || appState?.settings?.selected_player || "gridplayer";
  const options = selectablePlayers();
  if (options.some((player) => player.id === selected)) return selected;
  if (options.length) return options[0].id;
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

function playerPathInput(playerId) {
  const inputId = PLAYER_PATH_INPUT_IDS[playerId];
  return inputId ? els[inputId] : null;
}

function channelById(channelId) {
  return appState?.channels.find((channel) => channel.id === channelId) || null;
}

function appMeta() {
  return {
    name: appState?.app?.name || "IPTV Multi Player",
    version: appState?.app?.version || "0.1.0",
    repoUrl: appState?.app?.repo_url || "https://github.com/jeremygold02/iptv-multi-player",
  };
}

function iconHtml(name, extraClass = "") {
  return `<span class="material-icons ${extraClass}" aria-hidden="true">${escapeHtml(name)}</span>`;
}

function apiRequestKey(path, method) {
  return `${method} ${String(path).split("?")[0]}`;
}

function apiRequestPolicy(path, method, override) {
  if (override) return override;
  return API_REQUEST_POLICIES[apiRequestKey(path, method)] || "allow";
}

async function api(path, options = {}) {
  const { guardPolicy, headers, ...fetchOptions } = options;
  const method = String(fetchOptions.method || "GET").toUpperCase();
  const key = apiRequestKey(path, method);
  const policy = apiRequestPolicy(path, method, guardPolicy);

  if (policy === "share") {
    const existingRequest = sharedApiRequests.get(key);
    if (existingRequest) return existingRequest;

    const request = performApiRequest(path, fetchOptions, headers)
      .finally(() => {
        if (sharedApiRequests.get(key) === request) {
          sharedApiRequests.delete(key);
        }
      });
    sharedApiRequests.set(key, request);
    return request;
  }

  if (policy === "queue") {
    const previousRequest = queuedApiRequests.get(key) || Promise.resolve();
    const request = previousRequest
      .catch(() => undefined)
      .then(() => performApiRequest(path, fetchOptions, headers));
    const trackedRequest = request.finally(() => {
      if (queuedApiRequests.get(key) === trackedRequest) {
        queuedApiRequests.delete(key);
      }
    });
    queuedApiRequests.set(key, trackedRequest);
    return request;
  }

  return performApiRequest(path, fetchOptions, headers);
}

async function performApiRequest(path, options = {}, headers = undefined) {
  const defaultHeaders = options.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  const response = await fetch(path, {
    ...options,
    headers: {
      ...defaultHeaders,
      ...(headers || {}),
    },
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
  setAppState(data);
  filters.sourceId = filters.sourceId || data.selected_source_id || "all";
  hydrateUiPrefs();
  render();
  scheduleSportsRefresh();
  checkForLaunchUpdate();
  refreshPlaylistData({ refreshSports: true });
}

function scheduleSportsRefresh() {
  window.clearInterval(sportsRefreshTimer);
  if (!appState?.sports?.configured) return;
  const seconds = Number(appState?.sports?.refresh_seconds) || 1800;
  sportsRefreshTimer = window.setInterval(async () => {
    await refreshSportsData({ showErrors: true });
  }, seconds * 1000);
}

function mergeGameState(nextState) {
  if (!appState?.channels || !nextState?.channels) return nextState;
  const currentGames = new Map(appState.channels.map((channel) => [channel.id, channel.game]));
  return {
    ...nextState,
    channels: nextState.channels.map((channel) => {
      const currentGame = currentGames.get(channel.id);
      if (channel.game?.kind === "loading" && currentGame && currentGame.kind !== "loading") {
        return { ...channel, game: currentGame };
      }
      return channel;
    }),
  };
}

function setAppState(nextState, options = {}) {
  appState = options.preserveGames ? mergeGameState(nextState) : nextState;
  applyPinnedCategoryPrefs();
}

async function refreshSportsData(options = {}) {
  if (!appState?.sports?.configured) return null;
  try {
    const data = await api("/api/sports/refresh", { method: "POST" });
    setAppState(data);
    render();
    scheduleSportsRefresh();
    return data;
  } catch (error) {
    if (options.showErrors) {
      showToast(error.message, "error");
    }
    return null;
  }
}

async function refreshPlaylistData(options = {}) {
  const refreshSportsAfter = Boolean(options.refreshSports);
  try {
    const data = await api("/api/refresh", { method: "POST" });
    setAppState(data.state, { preserveGames: true });
    render();
    scheduleSportsRefresh();
    if (options.showResult) {
      const failed = data.errors?.length || 0;
      showToast(failed ? `Refresh finished with ${failed} issue${failed === 1 ? "" : "s"}` : "Playlists refreshed");
    }
    return data;
  } catch (error) {
    if (options.showErrors) {
      showToast(error.message, "error");
    }
    return null;
  } finally {
    if (refreshSportsAfter) {
      refreshSportsData();
    }
  }
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

function sanitizePinnedCategories(categories) {
  const seen = new Set();
  const configured = Array.isArray(categories) ? categories : [];
  return configured
    .map((category) => String(category || "").trim())
    .filter((category) => {
      if (!category || seen.has(category)) return false;
      seen.add(category);
      return true;
    });
}

function applyPinnedCategoryPrefs() {
  if (!appState) return;
  const categories = desktopRuntime()
    ? appState?.settings?.pinned_categories
    : readBrowserPrefs().pinned_categories;
  appState = {
    ...appState,
    settings: {
      ...(appState.settings || {}),
      pinned_categories: sanitizePinnedCategories(categories),
    },
  };
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
    setAppState(data.state, { preserveGames: true });
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

function isLiveGameChannel(channel) {
  const game = gameInfo(channel);
  return shouldShowGameInfo(game) && game.kind === "live";
}

function filteredChannels() {
  if (!appState) return [];
  const search = filters.search.trim().toLowerCase();
  const favorites = new Set(appState.favorites);
  const channels = appState.channels
    .filter((channel) => filters.sourceId === "all" || channel.source_id === filters.sourceId)
    .filter((channel) => filters.category === "all" || channel.group === filters.category)
    .filter((channel) => filters.kind !== "favorites" || favorites.has(channel.id))
    .filter((channel) => filters.kind !== "live-games" || isLiveGameChannel(channel))
    .filter((channel) => {
      if (!search) return true;
      return [channel.name, channel.group, sourceName(channel.source_id), searchableGameText(channel)]
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

function shouldShowGameInfo(game) {
  if (!game) return false;
  if (game.kind === "loading") return true;
  if (game.kind === "stream") return false;
  return game.matched !== false;
}

function searchableGameText(channel) {
  const game = gameInfo(channel);
  return shouldShowGameInfo(game) ? [game.text, game.status_long].join(" ") : "";
}

function orderedCategories() {
  const pinned = appState?.settings?.pinned_categories || [];
  const pinnedSet = new Set(pinned);
  return [
    "all",
    ...pinned,
    ...(appState?.categories || []).filter((category) => !pinnedSet.has(category)),
  ];
}

function categoryPinHtml(category, isPinned) {
  const label = isPinned ? "Unpin category" : "Pin category";
  const defaultIcon = isPinned ? "keep" : "keep_off";
  const hoverIcon = isPinned ? "keep_off" : "keep";
  return `
    <span class="category-pin ${isPinned ? "pinned" : "unpinned"}" data-pin-category="${escapeHtml(category)}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}">
      ${iconHtml(defaultIcon, "nav-icon pin-default")}
      ${iconHtml(hoverIcon, "nav-icon pin-hover")}
    </span>
  `;
}

async function toggleCategoryPin(category) {
  const current = appState?.settings?.pinned_categories || [];
  const next = current.includes(category)
    ? current.filter((item) => item !== category)
    : [...current, category];
  await savePinnedCategories(next);
  renderCategories();
}

async function savePinnedCategories(categories) {
  const next = sanitizePinnedCategories(categories);
  if (desktopRuntime()) {
    const data = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ pinned_categories: next }),
    });
    setAppState(data.state, { preserveGames: true });
  } else {
    writeBrowserPrefs({ ...readBrowserPrefs(), pinned_categories: next });
    appState = {
      ...appState,
      settings: {
        ...(appState.settings || {}),
        pinned_categories: sanitizePinnedCategories(next),
      },
    };
  }
}

async function reorderPinnedCategory(category, targetCategory, position = "after") {
  const current = appState?.settings?.pinned_categories || [];
  if (!current.includes(category)) return;

  const next = current.filter((item) => item !== category);
  const targetIndex = targetCategory ? next.indexOf(targetCategory) : -1;
  const insertIndex = targetIndex === -1
    ? next.length
    : targetIndex + (position === "after" ? 1 : 0);

  next.splice(insertIndex, 0, category);
  await savePinnedCategories(next);
  renderCategories();
}

function compareGameStatus(leftChannel, rightChannel) {
  const leftInfo = gameInfo(leftChannel);
  const rightInfo = gameInfo(rightChannel);
  const left = shouldShowGameInfo(leftInfo) ? leftInfo : { ...leftInfo, kind: "stream", text: "" };
  const right = shouldShowGameInfo(rightInfo) ? rightInfo : { ...rightInfo, kind: "stream", text: "" };
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
  if (!shouldShowGameInfo(game)) {
    return '<div class="game-status empty" aria-hidden="true"></div>';
  }
  const subtext = game.score || game.subtext || game.start_time || "";
  return `
    <div class="game-status ${escapeHtml(game.kind)}" ${game.kind === "loading" ? 'aria-busy="true"' : ""}>
      <strong><span class="game-status-label">${escapeHtml(game.text || "Unknown")}</span></strong>
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
  syncFilters();
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

function syncFilters() {
  const sourceIds = new Set(["all", ...(appState?.sources || []).map((source) => source.id)]);
  if (!sourceIds.has(filters.sourceId)) {
    filters.sourceId = "all";
  }

  const categories = new Set([
    "all",
    ...(appState?.categories || []),
    ...(appState?.settings?.pinned_categories || []),
  ]);
  if (!categories.has(filters.category)) {
    filters.category = "all";
  }

  if (filters.kind === "live-games" && !liveGameCount()) {
    filters.kind = "all";
  }
}

function liveGameCount() {
  return (appState?.channels || []).filter(isLiveGameChannel).length;
}

function renderStatus() {
  const total = appState.channels.length;
  const liveCount = liveGameCount();
  els.allCount.textContent = total;
  els.favoriteCount.textContent = appState.favorites.length;
  els.liveGameCount.textContent = liveCount;
  els.liveGamesFilter.hidden = liveCount === 0;
  document.querySelectorAll("[data-filter-kind]").forEach((button) => {
    button.classList.toggle("active", button.dataset.filterKind === filters.kind);
  });
}

function renderPlayerSelect() {
  if (!els.playerSelect) return;
  const options = selectablePlayers();
  const picker = els.playerSelect.closest(".player-picker");
  els.playerSelect.innerHTML = options
    .map((player) => `<option value="${escapeHtml(player.id)}">${escapeHtml(player.label)}</option>`)
    .join("");
  els.playerSelect.value = selectedPlayerId();
  els.playerSelect.disabled = options.length <= 1;
  if (picker) {
    picker.hidden = options.length <= 1;
  }
}

function renderSources() {
  const rows = [
    `<button class="source-row ${filters.sourceId === "all" ? "active" : ""}" data-source-id="all" type="button">
      ${iconHtml("playlist_play", "nav-icon")}
      <span><span class="source-name">All Playlists</span><span class="source-meta">${appState.channels.length} channels</span></span>
      <span></span>
    </button>`,
    ...appState.sources.map((source) => `
      <div class="source-row source-row-with-action ${filters.sourceId === source.id ? "active" : ""}">
        <button class="source-select" data-source-id="${escapeHtml(source.id)}" type="button">
          ${iconHtml(source.kind === "url" ? "link" : "folder", "nav-icon")}
          <span><span class="source-name">${escapeHtml(source.name)}</span><span class="source-meta">${source.channel_count || 0} channels</span></span>
          <span class="source-meta source-kind">${escapeHtml(source.kind)}</span>
        </button>
        <div class="source-actions">
          <button class="source-menu-trigger icon-button" data-source-menu-id="${escapeHtml(source.id)}" type="button" title="Playlist actions" aria-label="Actions for ${escapeHtml(source.name)}" aria-haspopup="menu" aria-expanded="false">
            ${iconHtml("more_vert")}
          </button>
          <div class="source-menu" data-source-menu="${escapeHtml(source.id)}" role="menu" hidden>
            <button data-source-rename-id="${escapeHtml(source.id)}" role="menuitem" type="button">
              ${iconHtml("edit")}
              <span>Rename</span>
            </button>
            <button class="danger-menu-item" data-source-remove-id="${escapeHtml(source.id)}" role="menuitem" type="button">
              ${iconHtml("delete")}
              <span>Delete</span>
            </button>
          </div>
        </div>
      </div>
    `),
  ];
  els.sourceList.innerHTML = rows.join("");
}

function renderCategories() {
  const categories = orderedCategories();
  const pinnedSet = new Set(appState?.settings?.pinned_categories || []);
  els.categoryList.innerHTML = categories.map((category) => {
    const label = category === "all" ? "All Categories" : category;
    const count = category === "all"
      ? appState.channels.length
      : appState.channels.filter((channel) => channel.group === category).length;
    const icon = category === "all"
      ? iconHtml("category", "nav-icon")
      : categoryPinHtml(category, pinnedSet.has(category));
    const isPinned = category !== "all" && pinnedSet.has(category);
    const pinnedAttrs = isPinned
      ? ` data-pinned-category="${escapeHtml(category)}"`
      : "";
    return `
      <button class="nav-row ${filters.category === category ? "active" : ""} ${isPinned ? "pinned-category" : ""}" data-category="${escapeHtml(category)}"${pinnedAttrs} type="button">
        ${icon}
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

  els.channelList.innerHTML = channels.map((channel) => {
    const isFavorite = favorites.has(channel.id);
    return `
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
          <button class="icon-button favorite-button ${isFavorite ? "active" : ""}" data-action="favorite" title="${isFavorite ? "Remove favorite" : "Favorite"}" type="button">
            ${iconHtml(isFavorite ? "favorite" : "favorite_border")}
          </button>
          <button class="icon-button" data-action="open" title="Open in ${escapeHtml(playerLabel)}" type="button">
            ${iconHtml("open_in_new")}
          </button>
        </div>
      </div>
    `;
  }).join("");
}

function renderDetail() {
  const channel = selectedChannelId ? channelById(selectedChannelId) : null;
  if (!channel) {
    els.detailPanel.innerHTML = `<div class="detail-empty">Click on a channel to view more info</div>`;
    return;
  }
  const game = gameInfo(channel);
  const playerLabel = selectedPlayerLabel();
  const gameMetaHtml = shouldShowGameInfo(game) ? `
        ${detailMetaItem("Game", game.text, game.score || game.status_long)}
        ${detailMetaItem("Start", game.start_time || (game.kind === "stream" ? "N/A" : "Unknown"))}
  ` : "";

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
        ${gameMetaHtml}
        ${detailMetaItem("Source", sourceName(channel.source_id))}
        ${detailMetaItem("Stream", streamType(channel.url))}
      </div>
      <div class="detail-actions" data-channel-id="${channel.id}">
        <button class="primary-button" data-detail-action="open" type="button">
          ${iconHtml("open_in_new")}
          <span>Open in ${escapeHtml(playerLabel)}</span>
        </button>
      </div>
    </div>
  `;
}

async function refreshWithState(request, options = {}) {
  const data = await request;
  setAppState(data.state || data, { preserveGames: true });
  render();
  scheduleSportsRefresh();
  if (options.refreshSports) {
    refreshSportsData();
  }
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
  els.apiSportsKeyInput.value = appState?.api_sports?.key || "";
  syncPlayerPathPickerButtons();
  setApiKeyVisibility(false);
}

function syncPlayerPathPickerButtons() {
  document.querySelectorAll("[data-player-path-picker]").forEach((button) => {
    const playerId = button.dataset.playerPathPicker;
    const label = playerById(playerId)?.label || playerId;
    const canBrowse = desktopRuntime();
    button.disabled = !canBrowse;
    button.title = canBrowse
      ? `Browse for ${label} executable`
      : "File picker is available in the desktop app";
    button.setAttribute("aria-label", button.title);
  });
}

async function pickPlayerExecutable(playerId, button) {
  const input = playerPathInput(playerId);
  if (!input) return;

  button.disabled = true;
  try {
    const data = await api("/api/select-player-executable", {
      method: "POST",
      body: JSON.stringify({ player: playerId }),
    });
    if (data.path) {
      input.value = data.path;
      input.focus();
    }
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    syncPlayerPathPickerButtons();
  }
}

function fillAboutDialog() {
  const meta = appMeta();
  pendingUpdate = null;
  els.aboutVersion.textContent = `${meta.name} ${meta.version}`;
  els.aboutRepoLink.href = meta.repoUrl;
  els.aboutRepoLink.textContent = "View source on GitHub";
  els.checkUpdatesButton.hidden = false;
  els.updateStatus.textContent = "";
  els.checkUpdatesButton.disabled = false;
  els.aboutInstallUpdateButton.hidden = true;
  els.aboutInstallUpdateButton.disabled = false;
}

function showRemoveSourceDialog(sourceId) {
  const source = sourceById(sourceId);
  if (!source) return;
  pendingSourceRemovalId = sourceId;
  els.removeSourceName.textContent = source.name;
  els.confirmRemoveSourceButton.disabled = false;
  closeModals();
  openModal(els.removeSourceModal);
}

function showRenameSourceDialog(sourceId) {
  const source = sourceById(sourceId);
  if (!source) return;
  pendingSourceRenameId = sourceId;
  els.renameSourceInput.value = source.name;
  els.confirmRenameSourceButton.disabled = false;
  closeModals();
  openModal(els.renameSourceModal);
  els.renameSourceInput.select();
}

async function renamePendingSource() {
  const sourceId = pendingSourceRenameId;
  if (!sourceId) return;

  const name = els.renameSourceInput.value.trim();
  els.confirmRenameSourceButton.disabled = true;
  try {
    const data = await api(`/api/sources/${encodeURIComponent(sourceId)}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
    setAppState(data.state, { preserveGames: true });
    pendingSourceRenameId = null;
    closeModals();
    render();
    showToast("Playlist renamed");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    els.confirmRenameSourceButton.disabled = false;
  }
}

async function removePendingSource() {
  const sourceId = pendingSourceRemovalId;
  if (!sourceId) return;

  const source = sourceById(sourceId);
  els.confirmRemoveSourceButton.disabled = true;
  try {
    const data = await api(`/api/sources/${encodeURIComponent(sourceId)}`, { method: "DELETE" });
    setAppState(data.state, { preserveGames: true });
    pendingSourceRemovalId = null;
    closeModals();
    render();
    scheduleSportsRefresh();
    showToast(`Removed ${source?.name || "playlist"}`);
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    els.confirmRemoveSourceButton.disabled = false;
  }
}

function clearCategoryDragState() {
  document.querySelectorAll(".category-drag-over-before, .category-drag-over-after").forEach((row) => {
    row.classList.remove("category-drag-over-before", "category-drag-over-after");
  });
}

function clearCategoryDraggingState() {
  document.querySelectorAll(".is-dragging-category").forEach((row) => {
    row.classList.remove("is-dragging-category");
  });
  clearCategoryDragState();
}

function categoryDropTarget(row, clientY) {
  if (!row || !categoryDrag || row.dataset.pinnedCategory === categoryDrag.category) {
    return null;
  }

  const rect = row.getBoundingClientRect();
  return {
    category: row.dataset.pinnedCategory,
    position: clientY > rect.top + rect.height / 2 ? "after" : "before",
    row,
  };
}

function categoryRowFromPoint(clientX, clientY) {
  return document.elementFromPoint(clientX, clientY)?.closest("#categoryList [data-pinned-category]") || null;
}

function updateUrl(update) {
  return update?.release_url || update?.repo_url || appMeta().repoUrl;
}

function renderAboutUpdateResult(update) {
  pendingUpdate = update;
  els.checkUpdatesButton.hidden = Boolean(update.update_available);
  els.updateStatus.className = `update-status ${update.update_available ? "available" : ""}`;
  els.updateStatus.innerHTML = update.update_available
    ? `${escapeHtml(update.message)} <a href="${escapeHtml(updateUrl(update))}" target="_blank" rel="noopener noreferrer">View release</a>`
    : escapeHtml(update.message);
  els.aboutInstallUpdateButton.hidden = !(update.update_available && update.can_install);
}

function fillUpdateDialog(update) {
  pendingUpdate = update;
  els.updateCurrentVersion.textContent = update.current_version || appMeta().version;
  els.updateLatestVersion.textContent = update.latest_version || "Unknown";
  els.updateReleaseLink.href = updateUrl(update);
  els.updateInstallStatus.className = "update-status";
  els.updateInstallStatus.textContent = update.can_install
    ? ""
    : "This update can be downloaded from GitHub, but automatic install only works in the packaged Windows app.";
  els.updateInstallButton.disabled = !update.can_install;
}

function showUpdateDialog(update) {
  fillUpdateDialog(update);
  closeModals();
  openModal(els.updateModal);
}

async function checkForLaunchUpdate() {
  if (launchUpdateChecked) return;
  launchUpdateChecked = true;
  try {
    const update = await api("/api/update/check");
    if (update.update_available && update.can_install) {
      showUpdateDialog(update);
    }
  } catch {
    // Launch checks should not interrupt normal app startup.
  }
}

async function installPendingUpdate(statusElement = els.updateInstallStatus) {
  if (!pendingUpdate) return;
  els.updateInstallButton.disabled = true;
  els.aboutInstallUpdateButton.disabled = true;
  statusElement.className = "update-status";
  statusElement.textContent = "Downloading update...";
  try {
    const result = await api("/api/update/install", { method: "POST" });
    statusElement.textContent = result.message || "Update downloaded. Restarting...";
  } catch (error) {
    statusElement.className = "update-status error";
    statusElement.textContent = error.message;
    els.updateInstallButton.disabled = false;
    els.aboutInstallUpdateButton.disabled = false;
  }
}

function setApiKeyVisibility(visible) {
  apiKeyVisible = Boolean(visible);
  els.apiSportsKeyInput.type = apiKeyVisible ? "text" : "password";
  els.toggleApiKeyButton.title = apiKeyVisible ? "Hide API key" : "Show API key";
  els.toggleApiKeyButton.setAttribute("aria-label", apiKeyVisible ? "Hide API key" : "Show API key");
  els.toggleApiKeyButton.setAttribute("aria-pressed", String(apiKeyVisible));
  const icon = els.toggleApiKeyButton.querySelector(".material-icons");
  if (icon) {
    icon.textContent = apiKeyVisible ? "visibility_off" : "visibility";
  }
}

function openModal(modal) {
  modal.hidden = false;
  const input = modal.querySelector("input");
  if (input) input.focus();
}

function closeModals() {
  els.urlModal.hidden = true;
  els.renameSourceModal.hidden = true;
  els.removeSourceModal.hidden = true;
  els.settingsModal.hidden = true;
  els.aboutModal.hidden = true;
  els.updateModal.hidden = true;
}

function setImportMenuOpen(open) {
  els.importMenu.hidden = !open;
  els.importPlaylistButton.setAttribute("aria-expanded", String(open));
}

function closeImportMenu() {
  setImportMenuOpen(false);
}

function closeSourceMenus(exceptSourceId = null) {
  document.querySelectorAll("[data-source-menu]").forEach((menu) => {
    const isExcepted = exceptSourceId && menu.dataset.sourceMenu === exceptSourceId;
    menu.hidden = !isExcepted;
  });
  document.querySelectorAll("[data-source-menu-id]").forEach((button) => {
    button.setAttribute("aria-expanded", String(Boolean(exceptSourceId && button.dataset.sourceMenuId === exceptSourceId)));
  });
}

function toggleSourceMenu(sourceId) {
  const menu = [...document.querySelectorAll("[data-source-menu]")]
    .find((item) => item.dataset.sourceMenu === sourceId);
  closeSourceMenus(menu?.hidden ? sourceId : null);
}

function bindEvents() {
  els.importPlaylistButton.addEventListener("click", (event) => {
    event.stopPropagation();
    setImportMenuOpen(els.importMenu.hidden);
  });

  els.importFileButton.addEventListener("click", () => {
    closeImportMenu();
    els.fileInput.click();
  });

  els.importUrlButton.addEventListener("click", () => {
    closeImportMenu();
    openModal(els.urlModal);
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".import-dropdown")) {
      closeImportMenu();
    }
    if (!event.target.closest(".source-actions")) {
      closeSourceMenus();
    }
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeImportMenu();
      closeSourceMenus();
    }
  });

  els.settingsButton.addEventListener("click", () => {
    fillSettingsForm();
    openModal(els.settingsModal);
  });

  els.aboutButton.addEventListener("click", () => {
    fillAboutDialog();
    els.settingsModal.hidden = true;
    openModal(els.aboutModal);
  });

  els.aboutInstallUpdateButton.addEventListener("click", () => {
    installPendingUpdate(els.updateStatus);
  });

  document.querySelectorAll("[data-close-modal]").forEach((button) => {
    button.addEventListener("click", closeModals);
  });

  els.checkUpdatesButton.addEventListener("click", async () => {
    els.checkUpdatesButton.disabled = true;
    els.updateStatus.className = "update-status";
    els.updateStatus.textContent = "Checking for updates...";
    try {
      renderAboutUpdateResult(await api("/api/update/check"));
    } catch (error) {
      els.updateStatus.className = "update-status error";
      els.updateStatus.textContent = error.message;
    } finally {
      els.checkUpdatesButton.disabled = false;
    }
  });

  els.updateInstallButton.addEventListener("click", installPendingUpdate);
  els.renameSourceForm.addEventListener("submit", (event) => {
    event.preventDefault();
    renamePendingSource();
  });
  els.confirmRemoveSourceButton.addEventListener("click", removePendingSource);

  els.fileInput.addEventListener("change", async () => {
    const file = els.fileInput.files[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    try {
      await refreshWithState(api("/api/import-file", { method: "POST", body }), { refreshSports: true });
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
      }), { refreshSports: true });
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
          api_sports_key: els.apiSportsKeyInput.value,
        }),
      }), { refreshSports: true });
      closeModals();
      showToast("Settings saved");
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.refreshButton.addEventListener("click", async () => {
    await refreshPlaylistData({ refreshSports: true, showErrors: true, showResult: true });
  });

  els.toggleApiKeyButton.addEventListener("click", () => {
    setApiKeyVisibility(!apiKeyVisible);
  });

  document.querySelectorAll("[data-player-path-picker]").forEach((button) => {
    button.addEventListener("click", () => {
      pickPlayerExecutable(button.dataset.playerPathPicker, button);
    });
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
    const menuButton = event.target.closest("[data-source-menu-id]");
    if (menuButton) {
      event.preventDefault();
      event.stopPropagation();
      toggleSourceMenu(menuButton.dataset.sourceMenuId);
      return;
    }

    const renameButton = event.target.closest("[data-source-rename-id]");
    if (renameButton) {
      event.preventDefault();
      event.stopPropagation();
      closeSourceMenus();
      showRenameSourceDialog(renameButton.dataset.sourceRenameId);
      return;
    }

    const removeButton = event.target.closest("[data-source-remove-id]");
    if (removeButton) {
      event.preventDefault();
      event.stopPropagation();
      closeSourceMenus();
      showRemoveSourceDialog(removeButton.dataset.sourceRemoveId);
      return;
    }

    const row = event.target.closest("[data-source-id]");
    if (!row) return;
    filters.sourceId = row.dataset.sourceId;
    render();
  });

  document.querySelectorAll("[data-filter-kind]").forEach((button) => {
    button.addEventListener("click", () => {
      filters.kind = button.dataset.filterKind;
      render();
    });
  });

  els.categoryList.addEventListener("click", (event) => {
    if (suppressCategoryClick) {
      suppressCategoryClick = false;
      event.preventDefault();
      event.stopPropagation();
      return;
    }

    const pin = event.target.closest("[data-pin-category]");
    if (pin) {
      event.preventDefault();
      event.stopPropagation();
      toggleCategoryPin(pin.dataset.pinCategory).catch((error) => {
        showToast(error.message, "error");
      });
      return;
    }

    const row = event.target.closest("[data-category]");
    if (!row) return;
    filters.category = row.dataset.category;
    render();
  });

  els.categoryList.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("[data-pin-category]")) return;
    const row = event.target.closest("[data-pinned-category]");
    if (!row) return;

    categoryDrag = {
      category: row.dataset.pinnedCategory,
      didMove: false,
      pointerId: event.pointerId,
      row,
      startY: event.clientY,
      targetCategory: null,
      targetPosition: null,
    };
    row.classList.add("is-dragging-category");
    row.setPointerCapture(event.pointerId);
  });

  els.categoryList.addEventListener("pointermove", (event) => {
    if (!categoryDrag || categoryDrag.pointerId !== event.pointerId) return;
    if (!categoryDrag.didMove && Math.abs(event.clientY - categoryDrag.startY) < 4) return;

    categoryDrag.didMove = true;
    event.preventDefault();
    clearCategoryDragState();

    const target = categoryDropTarget(categoryRowFromPoint(event.clientX, event.clientY), event.clientY);
    categoryDrag.targetCategory = target?.category || null;
    categoryDrag.targetPosition = target?.position || null;
    if (target) {
      target.row.classList.add(target.position === "before" ? "category-drag-over-before" : "category-drag-over-after");
    }
  });

  els.categoryList.addEventListener("pointerup", (event) => {
    if (!categoryDrag || categoryDrag.pointerId !== event.pointerId) return;
    const draggedCategory = categoryDrag.category;
    const didMove = categoryDrag.didMove;
    const pointerRow = categoryDrag.row;
    const targetCategory = categoryDrag.targetCategory;
    const targetPosition = categoryDrag.targetPosition;
    const shouldReorder = didMove && targetCategory;
    categoryDrag = null;
    clearCategoryDraggingState();
    if (pointerRow.hasPointerCapture(event.pointerId)) {
      pointerRow.releasePointerCapture(event.pointerId);
    }
    if (didMove) {
      suppressCategoryClick = true;
    }
    if (!shouldReorder) return;

    reorderPinnedCategory(draggedCategory, targetCategory, targetPosition).catch((error) => {
      showToast(error.message, "error");
    });
  });

  els.categoryList.addEventListener("pointercancel", (event) => {
    if (categoryDrag?.row?.hasPointerCapture(event.pointerId)) {
      categoryDrag.row.releasePointerCapture(event.pointerId);
    }
    categoryDrag = null;
    clearCategoryDraggingState();
  });

  els.channelList.addEventListener("click", async (event) => {
    const row = event.target.closest("[data-channel-id]");
    if (!row) return;
    const channelId = row.dataset.channelId;
    const action = event.target.closest("[data-action]")?.dataset.action;
    const clickTime = Date.now();
    const isRepeatedRowClick = !action
      && lastChannelClick.id === channelId
      && clickTime - lastChannelClick.time <= ROW_DOUBLE_CLICK_MS;
    lastChannelClick = action ? { id: null, time: 0 } : { id: channelId, time: clickTime };
    selectedChannelId = channelId;

    try {
      if (action === "favorite") {
        await refreshWithState(api(`/api/channels/${channelId}/favorite`, { method: "POST" }));
      } else if (action === "open") {
        await openChannel(channelId);
      } else if (isRepeatedRowClick) {
        lastChannelClick = { id: null, time: 0 };
        await openChannel(channelId);
      } else {
        render();
      }
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
    "importPlaylistButton", "importMenu", "importFileButton", "importUrlButton", "refreshButton", "settingsButton",
    "playerSelect", "zoomOutButton", "zoomValue", "zoomInButton", "sidebarResizer",
    "sourceList", "allCount", "favoriteCount", "liveGamesFilter", "liveGameCount", "categoryList", "resultCount",
    "searchInput", "channelList", "detailPanel", "fileInput", "urlModal",
    "urlForm", "urlNameInput", "urlInput", "renameSourceModal", "renameSourceForm", "renameSourceInput",
    "confirmRenameSourceButton", "removeSourceModal", "removeSourceName",
    "confirmRemoveSourceButton", "settingsModal", "settingsForm",
    "gridplayerPathInput", "mpvPathInput", "vlcPathInput", "apiSportsKeyInput",
    "toggleApiKeyButton", "aboutButton", "aboutModal", "aboutVersion", "aboutRepoLink",
    "checkUpdatesButton", "aboutInstallUpdateButton", "updateStatus", "updateModal",
    "updateCurrentVersion", "updateLatestVersion", "updateReleaseLink", "updateInstallStatus",
    "updateInstallButton", "toast",
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
