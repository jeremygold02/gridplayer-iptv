const els = {};
const ZOOM_LEVELS = [75, 90, 100, 110, 125, 150];
const BROWSER_PREF_KEY = "iptv-multi-player-ui";
const SIDEBAR_MIN_WIDTH = 180;
const SIDEBAR_MAX_WIDTH = 420;
const ROW_DOUBLE_CLICK_MS = 450;
const PLAYER_FLAG_PRESETS = {
  mpv: [
    { flag: "--fullscreen", label: "Fullscreen" },
    { flag: "--ontop", label: "Always on top" },
    { flag: "--force-window=yes", label: "Force player window" },
    { flag: "--keep-open=no", label: "Close when stream ends" },
    { flag: "--profile=low-latency", label: "Low latency profile" },
    { flag: "--cache=yes", label: "Use stream cache" },
    { flag: "--hls-bitrate=max", label: "Prefer best HLS quality" },
    { flag: "--hwdec=auto-safe", label: "Hardware decoding" },
  ],
  vlc: [
    { flag: "--autoscale", label: "Always fit window" },
    { flag: "--fullscreen", label: "Fullscreen" },
    { flag: "--no-video-title-show", label: "Hide video title" },
    { flag: "--one-instance", label: "Reuse VLC instance" },
    { flag: "--no-qt-video-autoresize", label: "Do not resize window to video" },
    { flag: "--network-caching=1000", label: "Network cache 1000ms" },
    { flag: "--live-caching=1000", label: "Live cache 1000ms" },
    { flag: "--avcodec-hw=any", label: "Hardware decoding" },
  ],
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
let customFilterPresets = {};
const BUILT_IN_FILTER_PRESET_IDS = {
  nba: "nba",
};
const API_REQUEST_POLICIES = {
  "GET /api/recording/status": "share",
  "GET /api/update/check": "share",
  "GET /api/update-check": "share",
  "POST /api/refresh": "share",
  "POST /api/sports/refresh": "share",
  "POST /api/update/install": "share",
  "GET /api/update/progress": "share",
  "POST /api/recording/clip/save": "queue",
  "POST /api/recording/stop": "share",
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
let recordingStatusTimer = null;
let apiKeyVisible = false;
let launchUpdateChecked = false;
let pendingUpdate = null;
let updateProgressTimer = null;
let recordingState = { active: false };
let recordingStopping = false;
let recordingFooterDismissed = false;
let pendingRecording = null;
let recordingProbeToken = 0;
let pendingSourceRemovalId = null;
let pendingSourceRenameId = null;
let pendingPlayerEditorId = null;
let pendingPlayerFlagsId = null;
const pendingChannelOpens = new Set();
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

function eventTargetElement(event) {
  if (event.target instanceof Element) return event.target;
  return event.target?.parentElement || null;
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
    player.available
    && player.path
  ));
}

function selectedPlayerId() {
  const selected = appState?.players?.selected || appState?.settings?.selected_player || "";
  const options = selectablePlayers();
  if (options.some((player) => player.id === selected)) return selected;
  if (options.length) return options[0].id;
  return selected;
}

function selectedPlayerLabel() {
  return playerById(selectedPlayerId())?.label || "Player";
}

function configuredPlayers() {
  return (appState?.players?.items || []).map((player) => ({
    id: player.id,
    name: player.name || player.label || "",
    path: player.configured_path || player.path || "",
    flags: player.configured_flags || "",
  }));
}

function executableStem(path) {
  return String(path || "")
    .split(/[\\/]/)
    .pop()
    .replace(/\.exe$/i, "")
    .trim();
}

function makeClientPlayerId(name, path) {
  const base = String(name || executableStem(path) || "player")
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 50) || "player";
  return `${base}-${Date.now().toString(36)}`;
}

function normalizedPlayerFromEditor(existingId = "") {
  const path = els.playerEditorPathInput.value.trim();
  const name = els.playerEditorNameInput.value.trim() || executableStem(path);
  return {
    id: existingId || makeClientPlayerId(name, path),
    name,
    path,
    flags: els.playerEditorFlagsInput.value.trim(),
  };
}

function playerFlagValue(playerId) {
  return playerById(playerId)?.configured_flags || "";
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
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Request returned ${response.status} ${response.statusText || "non-JSON response"}.`);
  }
  if (!response.ok) {
    throw new Error(payload.error || `Request returned ${response.status} ${response.statusText || "error"}.`);
  }
  if (!payload.success) {
    throw new Error(payload.error || "Request failed");
  }
  return payload.data;
}

function sanitizeFilterPreset(item) {
  if (!item || typeof item !== "object") return null;
  const id = String(item.id || "").trim();
  const name = String(item.name || "").trim();
  const sport = String(item.sport || "").trim();
  const category = String(item.category || "all").trim() || "all";
  const terms = Array.isArray(item.terms)
    ? item.terms.map((term) => String(term).trim()).filter(Boolean)
    : [];
  if (!id || !name || !terms.length) return null;
  return { id, name, sport, category, terms };
}

async function loadCustomFilterPresets() {
  const response = await fetch("/static/filter_presets.json");
  if (!response.ok) {
    throw new Error(`Filter presets returned ${response.status}`);
  }
  const payload = await response.json();
  if (!Array.isArray(payload)) {
    throw new Error("Filter presets must be a list");
  }
  customFilterPresets = Object.fromEntries(
    payload
      .map(sanitizeFilterPreset)
      .filter(Boolean)
      .map((preset) => [preset.id, preset]),
  );
}

function presetById(presetId) {
  return customFilterPresets[presetId] || null;
}

function builtInFilterPreset(kind) {
  const presetId = BUILT_IN_FILTER_PRESET_IDS[kind];
  return presetId ? presetById(presetId) : null;
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
  scheduleRecordingStatusRefresh();
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
  if (nextState?.recording?.status) {
    setRecordingStatus(nextState.recording.status);
  }
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

function normalizedFilterText(value) {
  return ` ${String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim()} `;
}

function normalizedExactFilterText(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim();
}

function customFilterKind(filterId) {
  return `custom:${filterId}`;
}

function customFilters() {
  return Array.isArray(appState?.settings?.custom_filters) ? appState.settings.custom_filters : [];
}

function customFilterFromKind(kind) {
  if (!String(kind || "").startsWith("custom:")) return null;
  const filterId = String(kind).slice("custom:".length);
  return customFilters().find((filter) => filter.id === filterId) || null;
}

function filterDefinitionForKind(kind) {
  return builtInFilterPreset(kind) || customFilterFromKind(kind);
}

function customFilterSearchFields(channel) {
  const game = gameInfo(channel);
  return [channel.name, game.home, game.away].filter(Boolean);
}

function legacyCustomFilterOperator(value) {
  const operator = String(value || "").trim();
  return ["not_contains", "starts_with", "ends_with", "exact"].includes(operator) ? operator : "contains";
}

function customFilterRuleFromTerm(term, defaultOperator = "contains") {
  const raw = String(term || "").trim();
  if (!raw) return null;
  const prefix = raw[0];
  const prefixedOperators = {
    "-": "not_contains",
    "!": "not_contains",
    "^": "starts_with",
    "$": "ends_with",
    "=": "exact",
  };
  const operator = prefixedOperators[prefix] || legacyCustomFilterOperator(defaultOperator);
  const value = prefixedOperators[prefix] ? raw.slice(1).trim() : raw;
  return value ? { operator, value } : null;
}

function customFilterRules(definition) {
  return (definition.terms || [])
    .map((term) => customFilterRuleFromTerm(term, definition.operator))
    .filter(Boolean);
}

function fieldMatchesCustomFilterRule(field, rule) {
  if (rule.operator === "contains" || rule.operator === "not_contains") {
    return normalizedFilterText(field).includes(normalizedFilterText(rule.value));
  }

  const fieldText = normalizedExactFilterText(field);
  const termText = normalizedExactFilterText(rule.value);
  if (!fieldText || !termText) return false;
  if (rule.operator === "starts_with") return fieldText.startsWith(termText);
  if (rule.operator === "ends_with") return fieldText.endsWith(termText);
  if (rule.operator === "exact") return fieldText === termText;
  return false;
}

function channelMatchesFilterDefinition(channel, definition) {
  if (!definition) return true;
  const categories = definition.categories || (definition.category && definition.category !== "all" ? [definition.category] : []);
  if (categories.length && !categories.includes(channel.group)) {
    return false;
  }
  const rules = customFilterRules(definition);
  const fields = customFilterSearchFields(channel);
  if (!rules.length) return true;

  const negativeRules = rules.filter((rule) => rule.operator === "not_contains");
  if (negativeRules.some((rule) => fields.some((field) => fieldMatchesCustomFilterRule(field, rule)))) {
    return false;
  }

  const positiveRules = rules.filter((rule) => rule.operator !== "not_contains");
  if (!positiveRules.length) return true;
  return positiveRules.some((rule) => fields.some((field) => fieldMatchesCustomFilterRule(field, rule)));
}

function isNbaChannel(channel) {
  return channelMatchesFilterDefinition(channel, filterDefinitionForKind("nba"));
}

function isFilterKindChannel(channel, kind) {
  const definition = filterDefinitionForKind(kind);
  return definition ? channelMatchesFilterDefinition(channel, definition) : true;
}

function filteredChannels() {
  if (!appState) return [];
  const search = filters.search.trim().toLowerCase();
  const favorites = new Set(appState.favorites);
  const channels = appState.channels
    .filter((channel) => filters.sourceId === "all" || channel.source_id === filters.sourceId)
    .filter((channel) => filters.category === "all" || channel.group === filters.category)
    .filter((channel) => filters.kind !== "favorites" || favorites.has(channel.id))
    .filter((channel) => !filterDefinitionForKind(filters.kind) || isFilterKindChannel(channel, filters.kind))
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

function recordingConfig() {
  return appState?.recording || {};
}

function ffmpegStatus() {
  return recordingConfig().ffmpeg || { available: false, message: "FFmpeg status unavailable." };
}

function recordingQualityPresets() {
  return recordingConfig().quality_presets || [
    { id: "best", label: "Best" },
    { id: "2160", label: "4K or lower" },
    { id: "1440", label: "1440p or lower" },
    { id: "1080", label: "1080p or lower" },
    { id: "720", label: "720p or lower" },
    { id: "480", label: "480p or lower" },
    { id: "lowest", label: "Lowest" },
  ];
}

function clipDurationPresets() {
  return recordingConfig().clip_duration_presets || [
    { seconds: 30, label: "30 seconds" },
    { seconds: 60, label: "1 minute" },
    { seconds: 120, label: "2 minutes" },
    { seconds: 300, label: "5 minutes" },
    { seconds: 600, label: "10 minutes" },
  ];
}

function defaultClipSeconds() {
  return Number(appState?.settings?.recording_clip_seconds || recordingConfig().default_clip_seconds || 60);
}

function sanitizeClipSeconds(value) {
  const seconds = Number(value) || defaultClipSeconds();
  return clipDurationPresets().some((item) => Number(item.seconds) === seconds) ? seconds : defaultClipSeconds();
}

function recordingPathValue() {
  return appState?.settings?.recording_dir || recordingConfig().effective_dir || recordingConfig().default_dir || "";
}

function defaultRecordingOptions() {
  return {
    recording_dir: recordingPathValue(),
    recording_default_quality: appState?.settings?.recording_default_quality || "best",
    clip_enabled: false,
    clip_seconds: defaultClipSeconds(),
  };
}

function formatElapsed(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const secs = Math.floor(value % 60);
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value >= 1024 * 1024 * 1024) return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  if (value >= 1024) return `${Math.round(value / 1024)} KB`;
  return `${value} B`;
}

function isRecordingChannel(channelId) {
  return Boolean(recordingState?.active && recordingState.channel_id === channelId);
}

function isClipMode(status = recordingState) {
  return status?.mode === "clip";
}

function recordingBlocksChannel(channelId) {
  return Boolean(recordingState?.active && recordingState.channel_id !== channelId);
}

function recordingButtonHtml(channelId, compact = true) {
  const isActive = isRecordingChannel(channelId);
  const blocked = recordingBlocksChannel(channelId);
  const clipActive = isActive && isClipMode();
  const title = isActive ? (clipActive ? "Stop clip buffer" : "Stop recording") : "Record stream";
  const buttonClass = compact ? "icon-button record-button" : "secondary-button record-button detail-record-button";
  return `
    <button class="${buttonClass} ${isActive ? "active" : ""}" data-${compact ? "action" : "detail-action"}="record" title="${title}" type="button" ${blocked ? "disabled" : ""}>
      ${iconHtml(isActive ? "stop_circle" : "download")}
      ${compact ? "" : `<span>${isActive ? (clipActive ? "Stop Clip Buffer" : "Stop Recording") : "Record"}</span>`}
    </button>
  `;
}

function recordingStatusKind(status = recordingState) {
  return status?.state || (status?.active ? "recording" : "idle");
}

function recordingStatusKey(status = recordingState) {
  return [
    recordingStatusKind(status),
    status?.mode || "",
    status?.active ? "active" : "inactive",
    status?.channel_id || "",
    status?.output_path || "",
    status?.message || "",
  ].join("|");
}

function recordingFooterShouldShow(status = recordingState) {
  if (recordingFooterDismissed) return false;
  const state = recordingStatusKind(status);
  return Boolean(status?.active || state === "stopped" || (state === "error" && status?.output_path));
}

function recordingStatusCanShow(status = recordingState) {
  const state = recordingStatusKind(status);
  return Boolean(status?.active || ["preparing", "starting", "waiting", "retrying", "error", "stopped"].includes(state));
}

function setRecordingStatus(status, options = {}) {
  const previousKey = recordingStatusKey();
  const nextStatus = status || { active: false, state: "idle" };
  if (!options.force && recordingStatusKind(nextStatus) === "idle" && recordingStatusCanShow(recordingState) && !recordingState.active) {
    return;
  }
  recordingState = nextStatus;
  if (options.show || nextStatus.active || (recordingStatusKey(nextStatus) !== previousKey && recordingStatusCanShow(nextStatus))) {
    recordingFooterDismissed = false;
  }
}

function setLocalRecordingStatus(channelId, state, message, extra = {}) {
  const channel = channelById(channelId);
  setRecordingStatus({
    active: false,
    local: true,
    state,
    message,
    channel_id: channelId,
    channel_name: channel?.name || "Recording",
    quality_id: extra.quality_id || "",
    quality_label: extra.quality_label || "",
    output_path: extra.output_path || "",
    elapsed_seconds: 0,
    size_bytes: 0,
    ...extra,
  }, { show: true });
  render();
}

function shouldKeepLocalRecordingStatus(serverStatus) {
  if (!recordingState?.local || serverStatus?.active) return false;
  const localState = recordingStatusKind(recordingState);
  if (!["preparing", "starting", "waiting", "retrying", "error"].includes(localState)) return false;
  return true;
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
  renderCustomFilters();
  renderStatus();
  renderPlayerSelect();
  renderPlayerSettingsList();
  renderSortHeaders();
  renderSources();
  renderCategories();
  renderChannels();
  renderDetail();
  renderRecordingFooter();
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
  if (String(filters.kind || "").startsWith("custom:") && !customFilterFromKind(filters.kind)) {
    filters.kind = "all";
  }
}

function liveGameCount() {
  return (appState?.channels || []).filter(isLiveGameChannel).length;
}

function nbaChannelCount() {
  return (appState?.channels || []).filter(isNbaChannel).length;
}

function customFilterCount(filter) {
  return (appState?.channels || []).filter((channel) => channelMatchesFilterDefinition(channel, filter)).length;
}

function renderCustomFilters() {
  els.customFilterList.innerHTML = customFilters().map((filter) => {
    const kind = customFilterKind(filter.id);
    return `
      <button class="nav-row ${filters.kind === kind ? "active" : ""}" data-filter-kind="${escapeHtml(kind)}" type="button">
        ${iconHtml("filter_alt", "nav-icon")}
        <span>${escapeHtml(filter.name)}</span>
        <span class="count">${customFilterCount(filter)}</span>
      </button>
    `;
  }).join("");
}

function renderStatus() {
  const total = appState.channels.length;
  const liveCount = liveGameCount();
  els.allCount.textContent = total;
  els.favoriteCount.textContent = appState.favorites.length;
  els.nbaCount.textContent = nbaChannelCount();
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

function renderPlayerSettingsList() {
  if (!els.playerSettingsList) return;
  const players = configuredPlayers();
  if (!players.length) {
    els.playerSettingsList.innerHTML = `<div class="player-settings-empty">No players configured</div>`;
    return;
  }

  els.playerSettingsList.innerHTML = players.map((player) => {
    const hasFlags = Boolean(player.flags);
    return `
      <div class="player-settings-row" data-player-row="${escapeHtml(player.id)}">
        <div class="player-settings-main">
          <div class="player-settings-name">${escapeHtml(player.name)}</div>
          <div class="player-settings-path">${escapeHtml(player.path)}</div>
        </div>
        <div class="player-settings-actions">
          <button class="icon-button ${hasFlags ? "active" : ""}" data-player-flag-editor="${escapeHtml(player.id)}" type="button" title="${hasFlags ? "Edit flags" : "Add flags"}" aria-label="${hasFlags ? "Edit flags" : "Add flags"}">
            ${iconHtml("flag")}
          </button>
          <button class="icon-button" data-player-path-picker="${escapeHtml(player.id)}" type="button" title="Browse for executable" aria-label="Browse for executable">
            ${iconHtml("folder_open")}
          </button>
          <button class="icon-button" data-player-edit="${escapeHtml(player.id)}" type="button" title="Edit player" aria-label="Edit player">
            ${iconHtml("edit")}
          </button>
          <button class="icon-button danger-button" data-player-delete="${escapeHtml(player.id)}" type="button" title="Delete player" aria-label="Delete player">
            ${iconHtml("delete")}
          </button>
        </div>
      </div>
    `;
  }).join("");
  syncPlayerPathPickerButtons();
  syncPlayerFlagButtons();
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
          ${recordingButtonHtml(channel.id)}
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
      </div>
      <div class="detail-heading">
        <div class="detail-title">${escapeHtml(channel.name)}</div>
        <div class="detail-subtitle">${escapeHtml(channel.group)} · ${escapeHtml(sourceName(channel.source_id))}</div>
      </div>
      <div class="detail-meta">
        ${gameMetaHtml}
        ${detailMetaItem("Source", sourceName(channel.source_id))}
        ${detailMetaItem("Stream", streamType(channel.url))}
      </div>
      <div class="detail-actions" data-channel-id="${channel.id}">
        ${recordingButtonHtml(channel.id, false)}
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
  const playerId = selectedPlayerId();
  const playerLabel = selectedPlayerLabel();
  const openKey = `${channelId}:${playerId}`;
  if (pendingChannelOpens.has(openKey)) {
    showToast(`Opening in ${playerLabel}...`);
    return;
  }

  pendingChannelOpens.add(openKey);
  showToast(`Opening in ${playerLabel}...`);
  const data = await api("/api/open", {
    method: "POST",
    body: JSON.stringify({ channel_id: channelId, player: playerId }),
  }).finally(() => {
    pendingChannelOpens.delete(openKey);
  });
  showToast(`Opened in ${data.label || playerLabel}`);
}

function renderRecordingFooter() {
  const state = recordingStatusKind();
  const active = Boolean(recordingState?.active);
  const clipMode = isClipMode();
  const stopping = Boolean(recordingStopping && active);
  const visible = recordingFooterShouldShow();
  const hasFile = Boolean(recordingState?.output_path);
  const hasRevealPath = hasFile;
  document.body.classList.toggle("recording-active", active);
  document.body.classList.toggle("recording-footer-visible", visible);
  els.recordingFooter.hidden = !visible;
  if (!visible) return;

  els.recordingFooter.classList.toggle("preparing", ["preparing", "starting", "waiting", "retrying"].includes(state));
  els.recordingFooter.classList.toggle("error", state === "error");
  els.recordingFooter.classList.toggle("stopped", state === "stopped");
  els.recordingFooter.classList.toggle("active", active);
  els.recordingFooter.classList.toggle("clipping", clipMode);
  els.recordingFooter.classList.toggle("stopping", stopping);
  els.recordingFooterIcon.textContent = state === "error"
    ? "error"
    : (state === "stopped" ? "check_circle" : (state === "retrying" ? "sync" : (clipMode ? "content_cut" : "download")));

  const stateTitle = {
    preparing: clipMode ? "Preparing Clip Buffer" : "Preparing Recording",
    starting: clipMode ? "Starting Clip Buffer" : "Starting Recording",
    waiting: clipMode ? "Clip Buffer Ready" : "Recording Ready",
    retrying: clipMode ? "Retrying Clip Buffer" : "Retrying Recording",
    error: clipMode ? "Clip Buffer Error" : "Recording Error",
    stopped: clipMode ? "Clip Buffer Stopped" : "Recording Stopped",
    recording: clipMode ? "Clip Buffer" : "Recording",
  }[state] || (clipMode ? "Clip Buffer" : "Recording");
  els.recordingFooterTitle.textContent = `${stateTitle}: ${recordingState.channel_name || "Stream"}`;

  const metaParts = [];
  if (recordingState.message && state !== "recording") {
    metaParts.push(recordingState.message);
  }
  if (clipMode && active) {
    metaParts.push(`${formatElapsed(recordingState.clip_ready_seconds)} buffered`);
    metaParts.push(`${formatElapsed(recordingState.clip_seconds)} window`);
    metaParts.push(recordingState.quality_label || "Source");
    metaParts.push(formatBytes(recordingState.size_bytes));
  } else if (active || state === "stopped" || hasFile) {
    metaParts.push(formatElapsed(recordingState.elapsed_seconds));
    metaParts.push(recordingState.quality_label || "Source");
    metaParts.push(formatBytes(recordingState.size_bytes));
  }
  els.recordingFooterMeta.textContent = metaParts.filter(Boolean).join(" · ");
  els.recordingSaveClipButton.hidden = !(clipMode && active);
  els.recordingSaveClipButton.disabled = !(clipMode && active) || stopping;
  els.recordingOpenButton.disabled = !hasFile;
  els.recordingRevealButton.disabled = !hasRevealPath;
  els.recordingStopButton.hidden = !active;
  els.recordingStopButton.disabled = stopping;
  els.recordingStopButton.classList.toggle("is-loading", stopping);
  els.recordingStopButton.setAttribute("aria-busy", String(stopping));
  const stopIcon = els.recordingStopButton.querySelector(".recording-stop-icon");
  const stopSpinner = els.recordingStopButton.querySelector(".recording-stop-spinner");
  const stopLabel = els.recordingStopButton.querySelector(".recording-stop-label");
  if (stopIcon) stopIcon.hidden = stopping;
  if (stopSpinner) stopSpinner.hidden = !stopping;
  if (stopLabel) stopLabel.textContent = stopping ? "Stopping" : "Stop";
}

async function refreshRecordingStatus() {
  try {
    const status = await api("/api/recording/status");
    if (shouldKeepLocalRecordingStatus(status)) {
      renderChannels();
      renderDetail();
      renderRecordingFooter();
      return recordingState;
    }
    setRecordingStatus(status);
    renderChannels();
    renderDetail();
    renderRecordingFooter();
    return recordingState;
  } catch {
    return recordingState;
  }
}

function scheduleRecordingStatusRefresh() {
  window.clearInterval(recordingStatusTimer);
  recordingStatusTimer = window.setInterval(refreshRecordingStatus, 2000);
}

function qualityOptionLabel(quality) {
  const parts = [quality.label || "Source"];
  if (quality.width && quality.height && !String(quality.label || "").includes(`${quality.height}p`)) {
    parts.push(`${quality.width}x${quality.height}`);
  }
  if (quality.fps) {
    parts.push(`${Number(quality.fps).toFixed(Number(quality.fps) % 1 ? 1 : 0)} fps`);
  }
  return parts.join(" · ");
}

function showFfmpegModal(message = "") {
  els.ffmpegModalMessage.textContent = message || ffmpegStatus().message || "FFmpeg is not installed or was not found on PATH.";
  els.ffmpegInstallStatus.textContent = "";
  els.installFfmpegModalButton.disabled = false;
  closeModals();
  openModal(els.ffmpegModal);
}

function fillQualitySelect(selectElement, selected) {
  selectElement.innerHTML = recordingQualityPresets()
    .map((quality) => `<option value="${escapeHtml(quality.id)}">${escapeHtml(quality.label)}</option>`)
    .join("");
  selectElement.value = selected || "best";
}

function fillClipDurationSelect(selectElement, selected) {
  const current = sanitizeClipSeconds(selected);
  selectElement.innerHTML = clipDurationPresets()
    .map((item) => `<option value="${Number(item.seconds)}">${escapeHtml(item.label)}</option>`)
    .join("");
  selectElement.value = String(current);
}

function syncClipDurationField(toggleElement, fieldElement) {
  if (!toggleElement || !fieldElement) return;
  fieldElement.hidden = !toggleElement.checked;
}

function syncRecordingStartButton() {
  if (!els.startRecordingQualityButton || !els.recordingQualityClipToggle) return;
  const clipEnabled = els.recordingQualityClipToggle.checked;
  const icon = els.startRecordingQualityButton.querySelector(".material-icons");
  const label = els.startRecordingQualityButton.querySelector("span:not(.material-icons)");
  if (icon) icon.textContent = clipEnabled ? "content_cut" : "download";
  if (label) label.textContent = clipEnabled ? "Start Clip Buffer" : "Start Recording";
}

function recordingOptionsFromModal() {
  return {
    recording_dir: els.recordingOptionDirInput.value.trim(),
    recording_default_quality: els.recordingOptionQualitySelect.value || "best",
    clip_enabled: els.recordingOptionClipToggle.checked,
    clip_seconds: sanitizeClipSeconds(els.recordingOptionClipDurationSelect.value),
  };
}

function openRecordingOptionsModal(channelId) {
  const channel = channelById(channelId);
  if (!channel) return;
  pendingRecording = {
    channel_id: channelId,
    options: defaultRecordingOptions(),
  };
  els.recordingOptionsChannel.textContent = channel.name;
  els.recordingOptionDirInput.value = pendingRecording.options.recording_dir;
  fillQualitySelect(els.recordingOptionQualitySelect, pendingRecording.options.recording_default_quality);
  fillClipDurationSelect(els.recordingOptionClipDurationSelect, pendingRecording.options.clip_seconds);
  els.recordingOptionClipToggle.checked = Boolean(pendingRecording.options.clip_enabled);
  syncClipDurationField(els.recordingOptionClipToggle, els.recordingOptionClipDurationField);
  els.confirmRecordingOptionsButton.disabled = false;
  closeModals();
  openModal(els.recordingOptionsModal);
}

function ensureRecordingQualityModalOpen() {
  if (!els.recordingQualityModal.hidden) return;
  closeModals();
  openModal(els.recordingQualityModal);
}

function showRecordingQualityLoadingModal(channelId, options = {}) {
  const channel = channelById(channelId);
  pendingRecording = { channel_id: channelId, options };
  els.recordingQualityTitle.textContent = "Finding Stream Qualities";
  els.recordingQualityChannel.textContent = channel?.name || "Channel";
  els.recordingQualityLoading.hidden = false;
  els.recordingQualityFields.hidden = true;
  els.recordingQualityControls.hidden = true;
  els.recordingQualityActions.hidden = true;
  els.recordingQualityMessage.hidden = true;
  els.recordingQualityMessage.textContent = "";
  els.recordingQualitySelect.innerHTML = "";
  els.recordingQualityDirInput.value = options.recording_dir || recordingPathValue();
  fillClipDurationSelect(els.recordingQualityClipDurationSelect, options.clip_seconds);
  els.recordingQualityClipToggle.checked = Boolean(options.clip_enabled);
  syncClipDurationField(els.recordingQualityClipToggle, els.recordingQualityClipDurationField);
  syncRecordingStartButton();
  els.startRecordingQualityButton.hidden = false;
  els.startRecordingQualityButton.disabled = true;
  ensureRecordingQualityModalOpen();
}

function showRecordingQualityErrorModal(channelId, message, options = {}) {
  const channel = channelById(channelId);
  pendingRecording = { channel_id: channelId, options };
  els.recordingQualityTitle.textContent = "Could Not Check Stream Qualities";
  els.recordingQualityChannel.textContent = channel?.name || "Channel";
  els.recordingQualityLoading.hidden = true;
  els.recordingQualityFields.hidden = false;
  els.recordingQualityControls.hidden = true;
  els.recordingQualityActions.hidden = false;
  els.recordingQualityMessage.hidden = false;
  els.recordingQualityMessage.textContent = message || "The stream qualities could not be checked.";
  els.recordingQualitySelect.innerHTML = "";
  els.recordingQualityDirInput.value = options.recording_dir || recordingPathValue();
  fillClipDurationSelect(els.recordingQualityClipDurationSelect, options.clip_seconds);
  els.recordingQualityClipToggle.checked = Boolean(options.clip_enabled);
  syncClipDurationField(els.recordingQualityClipToggle, els.recordingQualityClipDurationField);
  syncRecordingStartButton();
  els.startRecordingQualityButton.hidden = true;
  els.startRecordingQualityButton.disabled = true;
  ensureRecordingQualityModalOpen();
}

function showRecordingQualityModal(prepared) {
  pendingRecording = {
    ...prepared,
    options: {
      ...defaultRecordingOptions(),
      ...(prepared.options || {}),
    },
  };
  const channel = channelById(prepared.channel_id);
  const unavailable = Boolean(prepared.quality_unavailable);
  els.recordingQualityTitle.textContent = unavailable ? "Quality Not Available" : "Choose Recording Quality";
  els.recordingQualityChannel.textContent = channel?.name || "Channel";
  els.recordingQualityLoading.hidden = true;
  els.recordingQualityFields.hidden = false;
  els.recordingQualityControls.hidden = false;
  els.recordingQualityActions.hidden = false;
  els.recordingQualityMessage.hidden = !unavailable;
  els.recordingQualityMessage.textContent = unavailable
    ? (prepared.message || `${prepared.requested_quality_label || "Selected quality"} is not available for this stream.`)
    : "";
  els.recordingQualitySelect.innerHTML = (prepared.qualities || [])
    .map((quality) => `<option value="${escapeHtml(quality.id)}">${escapeHtml(qualityOptionLabel(quality))}</option>`)
    .join("");
  els.recordingQualitySelect.value = prepared.selected_quality_id || prepared.qualities?.[0]?.id || "source";
  els.recordingQualityDirInput.value = pendingRecording.options.recording_dir || recordingPathValue();
  fillClipDurationSelect(els.recordingQualityClipDurationSelect, pendingRecording.options.clip_seconds);
  els.recordingQualityClipToggle.checked = Boolean(pendingRecording.options.clip_enabled);
  syncClipDurationField(els.recordingQualityClipToggle, els.recordingQualityClipDurationField);
  syncRecordingStartButton();
  els.startRecordingQualityButton.hidden = false;
  els.startRecordingQualityButton.disabled = !prepared.can_start;
  ensureRecordingQualityModalOpen();
}

function recordingQualityOptionsFromModal() {
  return {
    ...(pendingRecording?.options || {}),
    recording_dir: els.recordingQualityDirInput.value.trim(),
    clip_enabled: els.recordingQualityClipToggle.checked,
    clip_seconds: sanitizeClipSeconds(els.recordingQualityClipDurationSelect.value),
  };
}

async function prepareRecording(channelId, options = {}) {
  const probeToken = ++recordingProbeToken;
  const clipEnabled = Boolean(options.clip_enabled);
  const clipSeconds = sanitizeClipSeconds(options.clip_seconds);
  const localMode = {
    mode: clipEnabled ? "clip" : "recording",
    clip_seconds: clipEnabled ? clipSeconds : 0,
  };
  showRecordingQualityLoadingModal(channelId, options);
  setLocalRecordingStatus(channelId, "preparing", "Checking FFmpeg and stream qualities...", localMode);
  let data;
  try {
    data = await api("/api/recording/prepare", {
      method: "POST",
      body: JSON.stringify({ channel_id: channelId, recording_options: options }),
    });
  } catch (error) {
    if (probeToken !== recordingProbeToken) return;
    setLocalRecordingStatus(channelId, "error", error.message, localMode);
    showRecordingQualityErrorModal(channelId, error.message, options);
    showToast(error.message, "error");
    return;
  }
  if (probeToken !== recordingProbeToken) return;
  if (!data.ffmpeg?.available) {
    pendingRecording = { channel_id: channelId, options };
    setLocalRecordingStatus(channelId, "error", data.ffmpeg?.message || "FFmpeg is not installed or was not found.", localMode);
    showFfmpegModal(data.ffmpeg?.message);
    return;
  }

  const qualities = data.qualities || [];
  if (data.quality_unavailable) {
    setLocalRecordingStatus(channelId, "waiting", data.message || "Selected quality is not available for this stream.", {
      ...localMode,
      quality_label: data.requested_quality_label || "",
    });
    showRecordingQualityModal({ ...data, channel_id: channelId, options });
    return;
  }
  setLocalRecordingStatus(channelId, "waiting", "Choose a source quality to start recording.", {
    ...localMode,
    quality_label: data.selected_quality_id ? "Ready" : "",
  });
  showRecordingQualityModal({ ...data, channel_id: channelId, options });
}

async function startRecording(channelId, qualityId, options = {}) {
  const clipEnabled = Boolean(options.clip_enabled);
  const clipSeconds = sanitizeClipSeconds(options.clip_seconds);
  setLocalRecordingStatus(channelId, "starting", "Starting FFmpeg...", {
    quality_id: qualityId || "",
    mode: clipEnabled ? "clip" : "recording",
    clip_seconds: clipEnabled ? clipSeconds : 0,
  });
  try {
    const status = await api("/api/recording/start", {
      method: "POST",
      body: JSON.stringify({
        channel_id: channelId,
        quality_id: qualityId,
        recording_options: options,
        clip_enabled: clipEnabled,
        clip_seconds: clipEnabled ? clipSeconds : 0,
      }),
    });
    setRecordingStatus(status, { show: true });
    closeModals();
    render();
    showToast(clipEnabled ? "Clip buffer started" : "Recording started");
  } catch (error) {
    closeModals();
    setLocalRecordingStatus(channelId, "error", error.message, {
      quality_id: qualityId || "",
      mode: clipEnabled ? "clip" : "recording",
    });
    showToast(error.message, "error");
  }
}

async function confirmRecordingOptions() {
  if (!pendingRecording?.channel_id) return;
  const options = recordingOptionsFromModal();
  pendingRecording.options = options;
  els.confirmRecordingOptionsButton.disabled = true;
  closeModals();
  try {
    await prepareRecording(pendingRecording.channel_id, options);
  } finally {
    els.confirmRecordingOptionsButton.disabled = false;
  }
}

async function stopRecording(options = {}) {
  if (recordingStopping) return;
  const stoppingClip = isClipMode();
  recordingStopping = true;
  renderRecordingFooter();
  try {
    const status = await api("/api/recording/stop", { method: "POST" });
    recordingStopping = false;
    setRecordingStatus(status, { show: !options.dismiss });
    if (options.dismiss) {
      recordingFooterDismissed = true;
    }
    render();
    showToast(stoppingClip ? "Clip buffer stopped" : "Recording stopped");
  } catch (error) {
    recordingStopping = false;
    setRecordingStatus({ ...recordingState, state: "error", message: error.message }, { show: true });
    render();
    throw error;
  }
}

function dismissRecordingFooter() {
  if (recordingState?.active) {
    openModal(els.recordingDismissModal);
    return;
  }
  recordingFooterDismissed = true;
  renderRecordingFooter();
}

async function confirmDismissRecordingFooter() {
  els.confirmDismissRecordingButton.disabled = true;
  try {
    await stopRecording({ dismiss: true });
    closeModals();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    els.confirmDismissRecordingButton.disabled = false;
  }
}

async function handleRecordChannel(channelId) {
  if (isRecordingChannel(channelId)) {
    await stopRecording();
    return;
  }
  if (recordingState?.active) {
    showToast("Stop the current recording or clip buffer before starting another.", "error");
    return;
  }
  const options = defaultRecordingOptions();
  pendingRecording = { channel_id: channelId, options };
  await prepareRecording(channelId, options);
}

async function openRecordingFile() {
  const data = await api("/api/recording/open", {
    method: "POST",
    body: JSON.stringify({ player: selectedPlayerId() }),
  });
  showToast(`Opened recording in ${data.label || selectedPlayerLabel()}`);
}

async function revealRecordingFile() {
  await api("/api/recording/reveal", { method: "POST" });
}

async function saveClip() {
  els.recordingSaveClipButton.disabled = true;
  try {
    const status = await api("/api/recording/clip/save", { method: "POST" });
    setRecordingStatus(status, { show: true });
    render();
    showToast("Clip saved");
  } catch (error) {
    setRecordingStatus({ ...recordingState, state: "error", message: error.message }, { show: true });
    render();
    throw error;
  }
}

function fillSettingsForm() {
  renderPlayerSettingsList();
  els.apiSportsKeyInput.value = appState?.api_sports?.key || "";
  els.ffmpegPathInput.value = appState?.settings?.ffmpeg_path || ffmpegStatus().path || "";
  els.recordingDirInput.value = recordingPathValue();
  fillRecordingQualityPresets();
  renderFfmpegStatus();
  setApiKeyVisibility(false);
}

function fillRecordingQualityPresets() {
  const selected = appState?.settings?.recording_default_quality || "best";
  els.recordingDefaultQualitySelect.innerHTML = recordingQualityPresets()
    .map((quality) => `<option value="${escapeHtml(quality.id)}">${escapeHtml(quality.label)}</option>`)
    .join("");
  els.recordingDefaultQualitySelect.value = selected;
}

function renderFfmpegStatus() {
  const status = ffmpegStatus();
  els.ffmpegStatusTitle.textContent = status.available ? "FFmpeg ready" : "FFmpeg missing";
  els.ffmpegStatusText.textContent = status.available
    ? (status.path || "FFmpeg is available.")
    : (status.message || "FFmpeg is not installed or was not found.");
  els.installFfmpegSettingsButton.hidden = Boolean(status.available);
}

function setSettingsTab(tabName) {
  document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
    const active = tab.dataset.settingsTab === tabName;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll("[data-settings-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.settingsPanel !== tabName;
  });
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

function syncPlayerFlagButtons() {
  document.querySelectorAll("[data-player-flag-editor]").forEach((button) => {
    const playerId = button.dataset.playerFlagEditor;
    const label = playerById(playerId)?.label || playerId;
    const hasFlags = Boolean(playerFlagValue(playerId));
    button.classList.toggle("active", hasFlags);
    button.title = hasFlags ? `Edit ${label} flags` : `Add ${label} flags`;
    button.setAttribute("aria-label", button.title);
  });
}

function playerFlagPresetKey(playerId) {
  const player = playerById(playerId);
  const stem = executableStem(player?.configured_path || player?.path || "").toLowerCase();
  return PLAYER_FLAG_PRESETS[stem] ? stem : String(player?.label || "").trim().toLowerCase();
}

function renderPlayerFlagPresets(playerId) {
  const presets = PLAYER_FLAG_PRESETS[playerFlagPresetKey(playerId)] || [];
  if (!presets.length) {
    els.playerFlagPresetList.innerHTML = `<div class="flag-preset-empty">No built-in presets for this player. You can paste custom flags above.</div>`;
    return;
  }
  els.playerFlagPresetList.innerHTML = presets.map((preset) => `
    <button class="flag-preset-button" data-player-flag-preset="${escapeHtml(preset.flag)}" type="button">
      <span>${escapeHtml(preset.label)}</span>
      <code>${escapeHtml(preset.flag)}</code>
    </button>
  `).join("");
}

function appendPlayerFlag(flag) {
  const value = String(flag || "").trim();
  if (!value) return;
  const current = els.playerFlagsInput.value.trim();
  const existing = current ? current.split(/\s+/) : [];
  if (existing.includes(value)) return;
  els.playerFlagsInput.value = current ? `${current} ${value}` : value;
  els.playerFlagsInput.focus();
}

function openPlayerFlagsModal(playerId) {
  if (!playerById(playerId)) return;
  pendingPlayerFlagsId = playerId;
  const label = playerById(playerId)?.label || playerId;
  els.playerFlagsTitle.textContent = `${label} Flags`;
  els.playerFlagsInput.value = playerFlagValue(playerId);
  els.playerFlagsInput.placeholder = playerFlagPresetKey(playerId) === "vlc" ? "Example: --autoscale" : "Example: --fullscreen";
  els.playerFlagsHelp.textContent = `Flags are passed to ${label} before the stream URL when it launches.`;
  renderPlayerFlagPresets(playerId);
  openModal(els.playerFlagsModal);
}

function closePlayerFlagsModal() {
  els.playerFlagsModal.hidden = true;
  pendingPlayerFlagsId = null;
}

async function savePlayerFlags() {
  const playerId = pendingPlayerFlagsId;
  if (!playerId) return;
  const players = configuredPlayers().map((player) => (
    player.id === playerId ? { ...player, flags: els.playerFlagsInput.value.trim() } : player
  ));
  await savePlayers(players, { selectedPlayer: appState?.players?.selected || selectedPlayerId(), skipRender: true });
  renderPlayerSettingsList();
  closePlayerFlagsModal();
  showToast(`${playerById(playerId)?.label || "Player"} flags saved`);
}

async function pickPlayerExecutable(playerId, button) {
  button.disabled = true;
  try {
    const data = await api("/api/select-player-executable", {
      method: "POST",
      body: JSON.stringify({ player: playerId }),
    });
    if (data.path) {
      const players = configuredPlayers().map((player) => (
        player.id === playerId
          ? { ...player, path: data.path, name: player.name || executableStem(data.path) }
          : player
      ));
      await savePlayers(players, { selectedPlayer: appState?.players?.selected || selectedPlayerId() });
      showToast("Player path saved");
    }
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    syncPlayerPathPickerButtons();
  }
}

async function saveSettingsPatch(patch, options = {}) {
  const data = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify(patch),
  });
  setAppState(data.state, { preserveGames: true });
  if (!options.skipRender) {
    render();
  }
  return data.state;
}

async function savePlayers(players, options = {}) {
  const payload = { players };
  if (options.selectedPlayer !== undefined) {
    payload.selected_player = options.selectedPlayer;
  }
  return saveSettingsPatch(payload, options);
}

function openPlayerEditor(playerId = "") {
  const player = playerId ? configuredPlayers().find((item) => item.id === playerId) : null;
  pendingPlayerEditorId = player?.id || "";
  els.playerEditorTitle.textContent = player ? "Edit Player" : "Add Player";
  els.playerEditorNameInput.value = player?.name || "";
  els.playerEditorPathInput.value = player?.path || "";
  els.playerEditorFlagsInput.value = player?.flags || "";
  els.playerEditorBrowseButton.disabled = !desktopRuntime();
  els.playerEditorBrowseButton.title = desktopRuntime()
    ? "Browse for player executable"
    : "File picker is available in the desktop app";
  els.playerEditorBrowseButton.setAttribute("aria-label", els.playerEditorBrowseButton.title);
  openModal(els.playerEditorModal);
}

function closePlayerEditor() {
  els.playerEditorModal.hidden = true;
  pendingPlayerEditorId = null;
}

async function savePlayerEditor() {
  const player = normalizedPlayerFromEditor(pendingPlayerEditorId || "");
  if (!player.name) {
    showToast("Enter a player name", "error");
    return;
  }
  if (!/\.exe$/i.test(player.path)) {
    showToast("Choose an .exe file", "error");
    return;
  }

  const players = configuredPlayers();
  const existingIndex = players.findIndex((item) => item.id === pendingPlayerEditorId);
  if (existingIndex >= 0) {
    players[existingIndex] = player;
  } else {
    players.push(player);
  }
  await savePlayers(players, { selectedPlayer: appState?.players?.selected || player.id });
  closePlayerEditor();
  showToast(existingIndex >= 0 ? "Player saved" : "Player added");
}

async function deletePlayer(playerId) {
  const players = configuredPlayers().filter((player) => player.id !== playerId);
  const selected = (appState?.players?.selected === playerId || selectedPlayerId() === playerId)
    ? (players[0]?.id || "")
    : appState?.players?.selected || selectedPlayerId();
  await savePlayers(players, { selectedPlayer: selected });
  showToast("Player deleted");
}

async function browsePlayerEditorPath() {
  els.playerEditorBrowseButton.disabled = true;
  try {
    const data = await api("/api/select-player-executable", {
      method: "POST",
      body: JSON.stringify({ player: pendingPlayerEditorId || "" }),
    });
    if (data.path) {
      els.playerEditorPathInput.value = data.path;
      if (!els.playerEditorNameInput.value.trim()) {
        els.playerEditorNameInput.value = executableStem(data.path);
      }
      els.playerEditorPathInput.focus();
    }
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    els.playerEditorBrowseButton.disabled = false;
  }
}

async function pickFfmpegExecutable(button, options = {}) {
  const targetInput = options.targetInput || els.ffmpegPathInput;
  button.disabled = true;
  try {
    const data = await api("/api/select-ffmpeg-executable", { method: "POST" });
    if (data.path) {
      targetInput.value = data.path;
      if (options.save) {
        await saveSettingsPatch({ ffmpeg_path: data.path }, { skipRender: true });
        fillSettingsForm();
      }
      if (options.retryRecording && pendingRecording?.channel_id) {
        pendingRecording.options = {
          ...(pendingRecording.options || {}),
          ffmpeg_path: data.path,
        };
        closeModals();
        await prepareRecording(pendingRecording.channel_id, pendingRecording.options);
      }
    }
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function pickRecordingDirectory(button, options = {}) {
  const targetInput = options.targetInput || els.recordingDirInput;
  button.disabled = true;
  try {
    const data = await api("/api/select-recording-directory", { method: "POST" });
    if (data.path) {
      targetInput.value = data.path;
      targetInput.focus();
    }
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function installFfmpeg(statusElement = els.ffmpegInstallStatus) {
  els.installFfmpegModalButton.disabled = true;
  els.installFfmpegSettingsButton.disabled = true;
  statusElement.className = "update-status";
  statusElement.textContent = "Installing FFmpeg with winget...";
  try {
    const data = await api("/api/ffmpeg/install", { method: "POST" });
    setAppState(data.state, { preserveGames: true });
    fillSettingsForm();
    statusElement.textContent = "FFmpeg installed.";
    showToast("FFmpeg installed");
    if (pendingRecording?.channel_id) {
      pendingRecording.options = {
        ...(pendingRecording.options || {}),
        ffmpeg_path: data.state?.settings?.ffmpeg_path || pendingRecording.options?.ffmpeg_path || "",
      };
      closeModals();
      await prepareRecording(pendingRecording.channel_id, pendingRecording.options);
    }
  } catch (error) {
    statusElement.className = "update-status error";
    statusElement.textContent = error.message;
  } finally {
    els.installFfmpegModalButton.disabled = false;
    els.installFfmpegSettingsButton.disabled = false;
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

function formatUpdateProgress(progress) {
  const phase = progress?.phase || "idle";
  const message = progress?.message || "";
  if (phase === "downloading") {
    const downloaded = Number(progress.downloaded_bytes) || 0;
    const total = Number(progress.total_bytes) || 0;
    const speed = Number(progress.bytes_per_second) || 0;
    const speedText = speed > 0 ? ` (${formatBytes(Math.round(speed))}/s)` : "";
    if (total > 0) {
      return `Downloading ${formatBytes(downloaded)} / ${formatBytes(total)}${speedText}`;
    }
    if (downloaded > 0) {
      return `Downloading ${formatBytes(downloaded)}${speedText}`;
    }
    return message || "Downloading update...";
  }
  if (phase === "checking") return message || "Checking for update...";
  if (phase === "installing") return message || "Preparing update...";
  if (phase === "complete") return message || "Update downloaded. Restarting...";
  if (phase === "error") return message || "Update failed.";
  return message || "Preparing update...";
}

function stopUpdateProgressPolling() {
  window.clearInterval(updateProgressTimer);
  updateProgressTimer = null;
}

async function refreshUpdateProgress(statusElement) {
  const progress = await api("/api/update/progress");
  if (!progress.active) return;
  statusElement.className = `update-status ${progress.phase === "error" ? "error" : ""}`;
  statusElement.textContent = formatUpdateProgress(progress);
}

function startUpdateProgressPolling(statusElement) {
  stopUpdateProgressPolling();
  refreshUpdateProgress(statusElement).catch(() => undefined);
  updateProgressTimer = window.setInterval(() => {
    refreshUpdateProgress(statusElement).catch(() => undefined);
  }, 500);
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
  if (!(statusElement instanceof HTMLElement)) {
    statusElement = els.updateInstallStatus;
  }
  els.updateInstallButton.disabled = true;
  els.aboutInstallUpdateButton.disabled = true;
  statusElement.className = "update-status";
  statusElement.textContent = "Preparing update...";
  startUpdateProgressPolling(statusElement);
  try {
    const result = await api("/api/update/install", { method: "POST" });
    stopUpdateProgressPolling();
    statusElement.className = "update-status";
    statusElement.textContent = result.message || "Update downloaded. Restarting...";
  } catch (error) {
    stopUpdateProgressPolling();
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

function fillCustomFilterCategories(selected = "all") {
  const categories = ["all", ...(appState?.categories || [])];
  els.customFilterCategorySelect.innerHTML = categories.map((category) => {
    const label = category === "all" ? "All Categories" : category;
    return `<option value="${escapeHtml(category)}">${escapeHtml(label)}</option>`;
  }).join("");
  els.customFilterCategorySelect.value = categories.includes(selected) ? selected : "all";
}

function fillCustomFilterPresets(selected = "") {
  const options = Object.entries(customFilterPresets).map(([id, preset]) => {
    const label = preset.sport ? `${preset.sport} - ${preset.name}` : preset.name;
    return `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`;
  });
  els.customFilterPresetSelect.innerHTML = [
    '<option value="">Choose a preset</option>',
    ...options,
  ].join("");
  els.customFilterPresetSelect.value = selected;
}

function openCustomFilterDialog() {
  fillCustomFilterPresets();
  fillCustomFilterCategories("all");
  els.customFilterForm.reset();
  els.customFilterPresetSelect.value = "";
  els.customFilterCategorySelect.value = "all";
  closeModals();
  openModal(els.customFilterModal);
}

function applyCustomFilterPreset(presetId) {
  const preset = presetById(presetId);
  if (!preset) return;
  els.customFilterNameInput.value = preset.name;
  fillCustomFilterCategories(preset.category);
  els.customFilterTermsInput.value = preset.terms.join(", ");
  els.customFilterNameInput.focus();
  els.customFilterNameInput.select();
}

function parseCustomFilterTerms(value) {
  const seen = new Set();
  return String(value || "")
    .split(/[\n,]+/)
    .map((term) => term.trim())
    .filter((term) => {
      const key = term.toLowerCase();
      if (!term || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function makeCustomFilterId(name) {
  const base = String(name || "filter").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "filter";
  const existingIds = new Set(customFilters().map((filter) => filter.id));
  let id = base;
  let index = 2;
  while (existingIds.has(id) || customFilterPresets[id] || BUILT_IN_FILTER_PRESET_IDS[id]) {
    id = `${base}_${index}`;
    index += 1;
  }
  return id;
}

async function saveCustomFilter() {
  const name = els.customFilterNameInput.value.trim();
  const category = els.customFilterCategorySelect.value || "all";
  const terms = parseCustomFilterTerms(els.customFilterTermsInput.value);
  if (!name) {
    showToast("Filter name is required", "error");
    return;
  }
  if (!terms.length) {
    showToast("Add at least one channel term", "error");
    return;
  }

  const nextFilters = [
    ...customFilters(),
    {
      id: makeCustomFilterId(name),
      name,
      category,
      terms,
    },
  ];
  const data = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({ custom_filters: nextFilters }),
  });
  setAppState(data.state, { preserveGames: true });
  filters.kind = customFilterKind(nextFilters[nextFilters.length - 1].id);
  closeModals();
  render();
  showToast("Filter saved");
}

function openModal(modal) {
  modal.hidden = false;
  const input = modal.querySelector("input");
  if (input) input.focus();
}

function closeModals() {
  els.customFilterModal.hidden = true;
  els.urlModal.hidden = true;
  els.renameSourceModal.hidden = true;
  els.removeSourceModal.hidden = true;
  els.settingsModal.hidden = true;
  els.playerEditorModal.hidden = true;
  els.playerFlagsModal.hidden = true;
  els.ffmpegModal.hidden = true;
  els.recordingOptionsModal.hidden = true;
  els.recordingQualityModal.hidden = true;
  els.recordingDismissModal.hidden = true;
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
    setSettingsTab("general");
    openModal(els.settingsModal);
  });

  document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      setSettingsTab(tab.dataset.settingsTab);
    });
  });

  els.addFilterButton.addEventListener("click", openCustomFilterDialog);

  els.customFilterPresetSelect.addEventListener("change", () => {
    applyCustomFilterPreset(els.customFilterPresetSelect.value);
  });

  els.customFilterForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await saveCustomFilter();
    } catch (error) {
      showToast(error.message, "error");
    }
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

  document.querySelectorAll("[data-close-player-flags]").forEach((button) => {
    button.addEventListener("click", closePlayerFlagsModal);
  });

  document.querySelectorAll("[data-close-player-editor]").forEach((button) => {
    button.addEventListener("click", closePlayerEditor);
  });

  els.addPlayerButton.addEventListener("click", () => openPlayerEditor());

  els.playerSettingsList.addEventListener("click", async (event) => {
    const flagButton = event.target.closest("[data-player-flag-editor]");
    if (flagButton) {
      openPlayerFlagsModal(flagButton.dataset.playerFlagEditor);
      return;
    }

    const pathButton = event.target.closest("[data-player-path-picker]");
    if (pathButton) {
      await pickPlayerExecutable(pathButton.dataset.playerPathPicker, pathButton);
      return;
    }

    const editButton = event.target.closest("[data-player-edit]");
    if (editButton) {
      openPlayerEditor(editButton.dataset.playerEdit);
      return;
    }

    const deleteButton = event.target.closest("[data-player-delete]");
    if (deleteButton) {
      await deletePlayer(deleteButton.dataset.playerDelete);
    }
  });

  els.playerEditorBrowseButton.addEventListener("click", browsePlayerEditorPath);

  els.playerEditorForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await savePlayerEditor();
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.playerFlagPresetList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-player-flag-preset]");
    if (!button) return;
    appendPlayerFlag(button.dataset.playerFlagPreset);
  });

  els.playerFlagsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await savePlayerFlags();
    } catch (error) {
      showToast(error.message, "error");
    }
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

  els.updateInstallButton.addEventListener("click", () => installPendingUpdate());
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
          players: configuredPlayers(),
          ffmpeg_path: els.ffmpegPathInput.value,
          recording_dir: els.recordingDirInput.value,
          recording_default_quality: els.recordingDefaultQualitySelect.value,
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

  els.ffmpegPathPickerButton.addEventListener("click", () => {
    pickFfmpegExecutable(els.ffmpegPathPickerButton);
  });

  els.recordingDirPickerButton.addEventListener("click", () => {
    pickRecordingDirectory(els.recordingDirPickerButton);
  });

  els.recordingOptionDirPickerButton.addEventListener("click", () => {
    pickRecordingDirectory(els.recordingOptionDirPickerButton, {
      targetInput: els.recordingOptionDirInput,
    });
  });

  els.recordingQualityDirPickerButton.addEventListener("click", () => {
    pickRecordingDirectory(els.recordingQualityDirPickerButton, {
      targetInput: els.recordingQualityDirInput,
    });
  });

  els.recordingOptionClipToggle.addEventListener("change", () => {
    syncClipDurationField(els.recordingOptionClipToggle, els.recordingOptionClipDurationField);
  });

  els.recordingQualityClipToggle.addEventListener("change", () => {
    syncClipDurationField(els.recordingQualityClipToggle, els.recordingQualityClipDurationField);
    syncRecordingStartButton();
  });

  els.browseFfmpegModalButton.addEventListener("click", () => {
    pickFfmpegExecutable(els.browseFfmpegModalButton, { save: true, retryRecording: true });
  });

  els.installFfmpegModalButton.addEventListener("click", () => {
    installFfmpeg(els.ffmpegInstallStatus);
  });

  els.installFfmpegSettingsButton.addEventListener("click", () => {
    installFfmpeg(els.ffmpegStatusText);
  });

  els.startRecordingQualityButton.addEventListener("click", async () => {
    if (!pendingRecording?.channel_id) return;
    try {
      const options = recordingQualityOptionsFromModal();
      pendingRecording.options = options;
      await startRecording(pendingRecording.channel_id, els.recordingQualitySelect.value, options);
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.confirmRecordingOptionsButton.addEventListener("click", async () => {
    try {
      await confirmRecordingOptions();
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.recordingOpenButton.addEventListener("click", async () => {
    try {
      await openRecordingFile();
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.recordingRevealButton.addEventListener("click", async () => {
    try {
      await revealRecordingFile();
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.recordingSaveClipButton.addEventListener("click", async () => {
    try {
      await saveClip();
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.recordingStopButton.addEventListener("click", async () => {
    try {
      await stopRecording();
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  els.recordingDismissButton.addEventListener("click", dismissRecordingFooter);
  els.confirmDismissRecordingButton.addEventListener("click", confirmDismissRecordingFooter);

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

  els.filtersSection.addEventListener("click", (event) => {
    const button = event.target.closest("[data-filter-kind]");
    if (!button) return;
    filters.kind = button.dataset.filterKind;
    render();
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
    const target = eventTargetElement(event);
    if (!target) return;
    const row = target.closest("[data-channel-id]");
    if (!row) return;
    const channelId = row.dataset.channelId;
    const action = target.closest("[data-action]")?.dataset.action;
    const clickTime = Date.now();
    const isRepeatedRowClick = !action
      && lastChannelClick.id === channelId
      && clickTime - lastChannelClick.time <= ROW_DOUBLE_CLICK_MS;
    lastChannelClick = action ? { id: null, time: 0 } : { id: channelId, time: clickTime };
    selectedChannelId = channelId;

    try {
      if (action === "favorite") {
        await refreshWithState(api(`/api/channels/${channelId}/favorite`, { method: "POST" }));
      } else if (action === "record") {
        await handleRecordChannel(channelId);
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
    const target = eventTargetElement(event);
    if (!target) return;
    const actions = target.closest(".detail-actions");
    const action = target.closest("[data-detail-action]")?.dataset.detailAction;
    if (!actions || !action) return;
    try {
      if (action === "open") {
        await openChannel(actions.dataset.channelId);
      } else if (action === "record") {
        await handleRecordChannel(actions.dataset.channelId);
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
    "sourceList", "filtersSection", "addFilterButton", "allCount", "favoriteCount", "nbaCount", "liveGamesFilter",
    "liveGameCount", "customFilterList", "categoryList", "resultCount",
    "searchInput", "channelList", "detailPanel", "fileInput", "customFilterModal", "customFilterForm",
    "customFilterNameInput", "customFilterCategorySelect", "customFilterTermsInput", "customFilterPresetSelect", "urlModal",
    "urlForm", "urlNameInput", "urlInput", "renameSourceModal", "renameSourceForm", "renameSourceInput",
    "confirmRenameSourceButton", "removeSourceModal", "removeSourceName",
    "confirmRemoveSourceButton", "settingsModal", "settingsForm",
    "playerSettingsList", "addPlayerButton", "playerEditorModal", "playerEditorForm", "playerEditorTitle",
    "playerEditorNameInput", "playerEditorPathInput", "playerEditorFlagsInput", "playerEditorBrowseButton",
    "ffmpegPathInput", "ffmpegPathPickerButton",
    "playerFlagsModal", "playerFlagsForm", "playerFlagsTitle", "playerFlagsInput", "playerFlagPresetList", "playerFlagsHelp",
    "recordingDirInput", "recordingDirPickerButton", "recordingDefaultQualitySelect",
    "ffmpegStatusTitle", "ffmpegStatusText", "installFfmpegSettingsButton", "apiSportsKeyInput",
    "toggleApiKeyButton", "ffmpegModal", "ffmpegModalMessage", "ffmpegInstallStatus", "browseFfmpegModalButton",
    "installFfmpegModalButton", "recordingOptionsModal", "recordingOptionsChannel", "recordingOptionDirInput", "recordingOptionDirPickerButton",
    "recordingOptionQualitySelect", "recordingOptionClipToggle", "recordingOptionClipDurationField", "recordingOptionClipDurationSelect",
    "confirmRecordingOptionsButton", "recordingQualityModal", "recordingQualityChannel", "recordingQualitySelect",
    "recordingQualityTitle", "recordingQualityLoading", "recordingQualityLoadingText", "recordingQualityFields", "recordingQualityControls",
    "recordingQualityMessage", "recordingQualityDirInput", "recordingQualityDirPickerButton", "recordingQualityClipToggle",
    "recordingQualityClipDurationField", "recordingQualityClipDurationSelect", "recordingQualityActions", "startRecordingQualityButton",
    "recordingFooter", "recordingFooterIcon", "recordingFooterTitle", "recordingFooterMeta",
    "recordingSaveClipButton", "recordingOpenButton", "recordingRevealButton", "recordingStopButton", "recordingDismissButton",
    "recordingDismissModal", "confirmDismissRecordingButton", "aboutButton", "aboutModal", "aboutVersion", "aboutRepoLink",
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
    await loadCustomFilterPresets();
    await loadState();
  } catch (error) {
    showToast(error.message, "error");
  }
});
