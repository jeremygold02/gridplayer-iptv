const els = {};
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
  render();
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
      return [channel.name, channel.group, sourceName(channel.source_id)]
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
    const left = sortState.key === "group" ? a.group : a.name;
    const right = sortState.key === "group" ? b.group : b.name;
    const result = String(left || "").localeCompare(String(right || ""), undefined, {
      numeric: true,
      sensitivity: "base",
    });
    return result * factor || a.order - b.order || a.name.localeCompare(b.name);
  });
}

function logoHtml(channel, extraClass = "") {
  if (channel.logo) {
    return `<span class="logo ${extraClass}"><img src="${escapeHtml(channel.logo)}" alt="" onerror="this.remove(); this.parentElement.textContent='${initials(channel.name)}';"></span>`;
  }
  return `<span class="logo ${extraClass}">${initials(channel.name)}</span>`;
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
  if (!selectedChannelId || !visibleIds.has(selectedChannelId)) {
    selectedChannelId = visible[0]?.id || null;
  }
}

function renderStatus() {
  const total = appState.channels.length;
  const sourceCount = appState.sources.length;
  els.channelSummary.textContent = `${total} channels across ${sourceCount} playlist${sourceCount === 1 ? "" : "s"}`;
  els.allCount.textContent = total;
  els.favoriteCount.textContent = appState.favorites.length;
  els.gridStatus.innerHTML = `
    <span class="status-dot ${appState.gridplayer.available ? "" : "offline"}"></span>
    <span>GridPlayer ${appState.gridplayer.available ? "Ready" : "Missing"}</span>
  `;
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
  const channels = filteredChannels();
  const favorites = new Set(appState.favorites);
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
      <div class="live-badge">LIVE</div>
      <div class="row-actions">
        <button class="icon-button ${favorites.has(channel.id) ? "active" : ""}" data-action="favorite" title="Favorite" type="button">☆</button>
        <button class="icon-button" data-action="open" title="Open in GridPlayer" type="button">
          <svg viewBox="0 0 24 24"><path d="M7 17 17 7"/><path d="M9 7h8v8"/><path d="M5 5h6M5 5v14h14v-6"/></svg>
        </button>
      </div>
    </div>
  `).join("");
}

function renderDetail() {
  const channel = selectedChannelId ? channelById(selectedChannelId) : null;
  if (!channel) {
    els.detailBar.innerHTML = `<div class="detail-empty">Select a channel</div>`;
    return;
  }

  els.detailBar.innerHTML = `
    <div class="detail-content">
      <div class="detail-main">
        ${logoHtml(channel, "detail-logo")}
        <div class="detail-text">
          <div class="detail-title">${escapeHtml(channel.name)}</div>
          <div class="detail-subtitle">${escapeHtml(channel.group)} · ${escapeHtml(sourceName(channel.source_id))}</div>
        </div>
      </div>
      <div class="detail-meta">
        <span>Stream<strong>${channel.url.split("?")[0].split(".").pop()?.toUpperCase() || "URL"}</strong></span>
        <span>Status<strong>Live</strong></span>
        <span>Source<strong>${escapeHtml(sourceName(channel.source_id))}</strong></span>
      </div>
      <div class="detail-actions" data-channel-id="${channel.id}">
        <button class="primary-button" data-detail-action="open" type="button">
          <svg viewBox="0 0 24 24"><path d="M7 17 17 7"/><path d="M9 7h8v8"/><path d="M5 5h6M5 5v14h14v-6"/></svg>
          <span>Open in GridPlayer</span>
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
  await api("/api/open", {
    method: "POST",
    body: JSON.stringify({ channel_id: channelId }),
  });
  showToast("Opened in GridPlayer");
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
    els.gridplayerPathInput.value = appState?.settings.gridplayer_path || appState?.gridplayer.path || "";
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

  els.detailBar.addEventListener("click", async (event) => {
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
    "channelSummary", "importFileButton", "importUrlButton", "refreshButton", "settingsButton",
    "gridStatus", "sourceList", "allCount", "favoriteCount", "categoryList", "resultCount",
    "searchInput", "channelList", "detailBar", "fileInput", "urlModal",
    "urlForm", "urlNameInput", "urlInput", "settingsModal", "settingsForm",
    "gridplayerPathInput", "toast",
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
