const $ = (id) => document.getElementById(id);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

let state = null;
let wizardStep = 0;
let wizardMaxStep = 0;
let installTimer = null;
let qrTimer = null;
let qrRefreshTimer = null;
let logTimer = null;
let currentLog = "astrbot";
let contacts = { friends: [], groups: [], loaded: false };
let settingsDirty = false;
let lovehouseState = null;
let lovehouseSelected = "";
let pluginList = [];
let companionStatus = null;
let pluginTogglePending = false;
let pluginRestartPending = false;
let metricsLoading = false;
let lastMetricSample = 0;
let metricHistory = [];
let artStyle = "daily";
try { artStyle = localStorage.getItem("dafeiyu.art-style") || "daily"; } catch (_) {}
const ART_STYLES = { daily: "dafeiyu-daily.webp", sailor: "dafeiyu-sailor.webp", chibi: "dafeiyu-chibi-sailor.webp", classic: "dafeiyu-welcome.png" };
let qrModalWatch = null;
let qrFlowGeneration = 0;
let modalReturnFocus = null;
let modalGeneration = 0;
let localConnected = false;
let updateState = null;
let updateRequestPending = false;
let updateStatusLoading = false;
let updateReachable = false;
let updatePromptManual = false;
const updatePromptSeen = new Set();
let logRetentionFetchedAt = 0;

const STEP_NAMES = ["安装位置", "运行环境", "对话模型", "连接 QQ", "主人身份", "完成"];
const VIEW_META = {
  overview: ["我的肥鱼", "服务状态与资源占用"],
  whitelist: ["聊天权限", "管理 QQ 私聊、群聊与操作授权"],
  settings: ["设置", "模型、身份与启动选项"],
  logs: ["诊断日志", "查看服务状态与运行记录"],
  lovehouse: ["记忆小屋", "与爱之屋相连，让陪伴慢慢成为回忆"],
  artifacts: ["作品箱", "浏览工作目录中的文件"],
  plugins: ["插件百宝箱", "独立启停插件，按需配置权限"],
};

function applyArtStyle(style) {
  artStyle = ART_STYLES[style] ? style : "daily";
  try { localStorage.setItem("dafeiyu.art-style", artStyle); } catch (_) {}
  document.body.dataset.artStyle = artStyle;
  $$("[data-art-style]").filter(el => el.tagName === "BUTTON").forEach(btn => btn.setAttribute("aria-pressed", String(btn.dataset.artStyle === artStyle)));
  if (state) renderCompanionState();
}


function setConnectionStatus(connected) {
  localConnected = connected;
  $("connectionBanner")?.classList.toggle("hidden", connected);
  if ($("localServiceLabel")) $("localServiceLabel").textContent = connected ? "本机服务已连接" : "正在重新连接";
  if (updateState) renderUpdates();
  renderDashboardShortcuts();
}

function renderDashboardShortcuts() {
  const services = [["AstrBot", "astrbot", "Astrbot"], ["NapCat", "napcat", "Napcat"]];
  services.forEach(([label, key, suffix]) => {
    const button = $("pluginsOpen" + suffix);
    if (!button) return;
    const up = !!state?.status?.[key]?.up;
    button.disabled = !localConnected || !up || !!state?.preview || button.dataset.busy === "true";
    button.title = state?.preview ? "预览模式不打开本地后台" : !localConnected ? "请先重新连接本机服务" : !up ? `${label} 未启动，请先在首页一键开启` : `在默认浏览器打开本机 ${label} 后台`;
    $("plugins" + suffix + "Dot")?.classList.toggle("asleep", !localConnected || !up);
    const reveal = $("plugins" + suffix + "CredentialsToggle");
    if (reveal) {
      reveal.disabled = !localConnected || !!state?.preview || state?.installed === false;
      reveal.title = state?.preview ? "预览模式不读取登录凭据" : !localConnected ? "请先重新连接本机服务" : "按需查看本机保存的登录信息";
      if (reveal.disabled) clearDashboardCredentials(key);
    }
  });
  const a = !!state?.status?.astrbot?.up, n = !!state?.status?.napcat?.up;
  setText("pluginsDashboardHint", !localConnected ? "请先重新连接本机服务，再打开后台。" : state?.preview ? "后台入口预览；正式运行时在默认浏览器打开。" : a && n ? "在默认浏览器打开，仅连接这台电脑的服务。" : !a && !n ? "服务未启动，请先在首页一键开启。" : `${a ? "NapCat" : "AstrBot"} 未启动，请先在首页一键开启。`);
}

const dashboardCredentialViews = new Map();
const DASHBOARD_CREDENTIAL_SUFFIXES = { astrbot: "Astrbot", napcat: "Napcat" };

function dashboardCredentialElements(target) {
  const suffix = DASHBOARD_CREDENTIAL_SUFFIXES[target];
  return suffix ? { button: $("plugins" + suffix + "CredentialsToggle"), panel: $("plugins" + suffix + "Credentials") } : {};
}

function clearDashboardCredentials(target = null) {
  const targets = target ? [target] : Object.keys(DASHBOARD_CREDENTIAL_SUFFIXES);
  targets.forEach(key => {
    const view = dashboardCredentialViews.get(key);
    if (view) { view.generation += 1; view.controller?.abort(); view.controller = null; }
    const { button, panel } = dashboardCredentialElements(key);
    if (panel) {
      panel.querySelectorAll("input,textarea").forEach(control => { control.value = ""; control.defaultValue = ""; });
      panel.querySelectorAll("button").forEach(control => { control.onclick = null; });
      panel.replaceChildren();
      panel.classList.add("hidden");
    }
    if (button) { button.setAttribute("aria-expanded", "false"); button.removeAttribute("aria-busy"); button.textContent = "登录信息"; }
  });
}

function dashboardCredentialControl(identifier, value, hidden) {
  const control = document.createElement(hidden ? "input" : "textarea");
  if (hidden) control.type = "password";
  else control.rows = 2;
  control.id = identifier;
  control.className = "dashboard-credential-value";
  control.readOnly = true;
  control.autocomplete = "off";
  control.spellcheck = false;
  control.setAttribute("autocapitalize", "off");
  control.value = value;
  return control;
}

async function copyDashboardCredential(row) {
  const control = row.querySelector(".dashboard-credential-value");
  if (!control?.value || row.closest(".dashboard-credentials")?.classList.contains("hidden")) return;
  let copied = false;
  if (navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(control.value); copied = true; } catch (_) { /* Local WebView fallback below. */ }
  }
  if (!copied && control.value && row.isConnected && !document.hidden) {
    const buffer = document.createElement("textarea");
    buffer.className = "dashboard-copy-buffer";
    buffer.setAttribute("aria-hidden", "true");
    buffer.value = control.value;
    document.body.appendChild(buffer);
    try { buffer.select(); copied = document.execCommand("copy"); } catch (_) { copied = false; }
    finally { buffer.value = ""; buffer.remove(); }
  }
  toast(copied ? "已复制登录信息" : "复制未完成，请显示后手动选择复制。");
}

function renderDashboardCredentials(target, data) {
  const { panel } = dashboardCredentialElements(target);
  if (!panel || panel.classList.contains("hidden")) return;
  panel.replaceChildren();
  const heading = document.createElement("strong");
  heading.textContent = target === "astrbot" ? "AstrBot 登录信息" : "NapCat 登录信息";
  panel.appendChild(heading);
  const allowed = target === "astrbot" ? ["username", "password"] : ["token"];
  (Array.isArray(data.fields) ? data.fields : []).slice(0, 3).forEach(field => {
    if (!allowed.includes(field.id) || typeof field.value !== "string" || !field.value || field.value.length > 4096) return;
    const row = document.createElement("div");
    row.className = "dashboard-credential-field";
    const identifier = `dashboard-${target}-${field.id}`;
    const label = document.createElement("label");
    label.htmlFor = identifier;
    label.textContent = typeof field.label === "string" ? field.label.slice(0, 120) : field.id;
    row.appendChild(label);
    const sensitive = field.id !== "username";
    const control = dashboardCredentialControl(identifier, field.value, true);
    if (!sensitive) control.type = "text";
    row.appendChild(control);
    const actions = document.createElement("div");
    actions.className = "dashboard-credential-actions";
    if (sensitive) {
      const visibility = document.createElement("button");
      visibility.type = "button";
      visibility.className = "text-button";
      visibility.textContent = "显示";
      visibility.setAttribute("aria-pressed", "false");
      visibility.setAttribute("aria-controls", identifier);
      visibility.onclick = () => {
        const previous = row.querySelector(".dashboard-credential-value");
        const show = previous.tagName === "INPUT";
        const replacement = dashboardCredentialControl(identifier, previous.value, !show);
        previous.value = "";
        previous.replaceWith(replacement);
        visibility.textContent = show ? "隐藏" : "显示";
        visibility.setAttribute("aria-pressed", String(show));
      };
      actions.appendChild(visibility);
    }
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "text-button";
    copy.textContent = "复制";
    copy.onclick = () => copyDashboardCredential(row);
    actions.appendChild(copy);
    row.appendChild(actions);
    panel.appendChild(row);
  });
  (Array.isArray(data.warnings) ? data.warnings : []).slice(0, 5).forEach(message => {
    if (typeof message !== "string") return;
    const note = document.createElement("p");
    note.className = "dashboard-credential-note";
    note.textContent = message.slice(0, 600);
    panel.appendChild(note);
  });
}

async function toggleDashboardCredentials(target) {
  const { button, panel } = dashboardCredentialElements(target);
  if (!button || !panel) return;
  if (button.getAttribute("aria-expanded") === "true") { clearDashboardCredentials(target); return; }
  if (!localConnected || state?.preview || state?.installed === false || document.hidden) return;
  const view = dashboardCredentialViews.get(target) || { generation: 0, controller: null };
  dashboardCredentialViews.set(target, view);
  const generation = ++view.generation;
  const controller = new AbortController();
  view.controller = controller;
  button.setAttribute("aria-expanded", "true");
  button.setAttribute("aria-busy", "true");
  button.textContent = "收起信息";
  panel.classList.remove("hidden");
  const loading = document.createElement("p");
  loading.setAttribute("role", "status");
  loading.textContent = "正在读取本机登录信息…";
  panel.replaceChildren(loading);
  let data = null;
  try {
    data = await api("/api/dashboard/credentials", { method: "POST", cache: "no-store", credentials: "same-origin",
      body: JSON.stringify({ target }), signal: controller.signal });
    if (controller.signal.aborted || view.generation !== generation || document.hidden || $("view-plugins")?.classList.contains("hidden")) return;
    renderDashboardCredentials(target, data);
  } catch (_) {
    if (!controller.signal.aborted && view.generation === generation) {
      loading.textContent = "暂时没能读取登录信息，请收起后重试。";
      panel.replaceChildren(loading);
    }
  } finally {
    if (Array.isArray(data?.fields)) data.fields.forEach(field => { if (field && typeof field === "object") field.value = ""; });
    data = null;
    if (view.generation === generation) { view.controller = null; button.removeAttribute("aria-busy"); }
  }
}

function bindDashboardCredentialEvents() {
  Object.keys(DASHBOARD_CREDENTIAL_SUFFIXES).forEach(target => {
    dashboardCredentialElements(target).button?.addEventListener("click", () => toggleDashboardCredentials(target));
  });
  document.addEventListener("visibilitychange", () => { if (document.hidden) clearDashboardCredentials(); });
  window.addEventListener("pagehide", () => clearDashboardCredentials());
}

async function openLocalDashboard(name, button) {
  const dashboards = { astrbot: ["AstrBot", "http://127.0.0.1:6185"], napcat: ["NapCat", "http://127.0.0.1:6099"] };
  const dashboard = dashboards[name];
  if (!dashboard) return;
  if (state?.preview) { toast("预览模式不打开本地后台。"); return; }
  await withBusy(button, "正在打开…", async () => {
    await api("/api/open-url", { method: "POST", body: JSON.stringify({ url: dashboard[1] }) });
    toast(`已在默认浏览器打开 ${dashboard[0]} 后台`);
  });
  renderDashboardShortcuts();
}

async function withBusy(button, label, action) {
  if (!button || button.dataset.busy === "true") return;
  const previous = button.innerHTML;
  button.dataset.busy = "true";
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = label;
  if (state && ["btnStart", "btnStop", "btnRestart"].includes(button.id)) renderOverview();
  try { return await action(); }
  catch (err) { toast(err.message || String(err)); }
  finally {
    button.dataset.busy = "false";
    button.removeAttribute("aria-busy");
    button.disabled = false;
    button.innerHTML = previous;
    if (state) renderOverview();
  }
}

function emptyState(title, description, options = {}) {
  return `<div class="empty-state${options.error ? " error" : ""}">${options.art ? '<img src="/assets/ui/dafeiyu-rest.png" alt="休息中的大肥鱼" onerror="this.style.display=\'none\'">' : ""}<h3>${esc(title)}</h3><p>${esc(description)}</p></div>`;
}

function setText(id, value) {
  const element = $(id);
  if (element && element.textContent !== value) element.textContent = value;
}

function setMascotImage(id, filename, alt = "") {
  const image = $(id);
  if (!image) return;
  const source = `/assets/ui/${filename}`;
  if (image.getAttribute("src") !== source) {
    image.src = source;
    image.style.display = "";
  }
  image.dataset.pose = filename;
  image.alt = alt;
}

function versionLabel(version) {
  return version ? `v${String(version).replace(/^v/i, "")}` : "—";
}

function updateVersionKey(version) {
  return String(version || "").replace(/^v/i, "");
}

function pluginUpdateRows(u = updateState || {}) {
  return Array.isArray(u.plugin_updates?.plugins) ? u.plugin_updates.plugins : [];
}

function pendingUpdateChecks(u) {
  return !!u?.checking || u?.plugin_updates?.state === "checking";
}

function updateNoticeData(u, manual = false, now = Date.now() / 1000) {
  if (!u || pendingUpdateChecks(u) || (!manual && u.auto_check === false)
      || Number(u.snoozed_until || 0) > now) return null;
  const ignored = updateVersionKey(u.ignored_version);
  const appVersion = updateVersionKey(u.latest_version);
  const appAvailable = u.available === true && !!appVersion && !u.error && appVersion !== ignored;
  const p = u.plugin_updates || {};
  const pluginTag = updateVersionKey(p.tag || u.latest_version);
  const plugins = p.state === "checked" && !p.error && (!ignored || pluginTag !== ignored)
    ? pluginUpdateRows(u).filter(row => row && ["update_available", "available"].includes(row.status)
      && row.is_installed !== false && typeof row.latest === "string" && row.latest) : [];
  if (!appAvailable && !plugins.length) return null;
  const keys = [];
  if (appAvailable) keys.push(appVersion);
  if (plugins.length) keys.push(pluginTag || "plugins:" + plugins.map(row => `${row.id}:${row.latest}`).sort().join(","));
  return { appAvailable, plugins, keys: [...new Set(keys)],
    title: appAvailable && plugins.length ? "软件和插件都有新版本啦" : plugins.length ? "大肥鱼的插件有新版本啦" : "爱屋及鱼有新版本啦" };
}

function updateVersionTable(rows) {
  return `<div class="update-version-table-wrap"><table class="update-version-table"><thead><tr><th scope="col">项目</th><th scope="col">当前版本</th><th scope="col">最新版本</th></tr></thead><tbody>${rows.map(row => `<tr><th scope="row">${esc(row.name)}</th><td>${esc(row.current)}</td><td>${esc(row.latest)}</td></tr>`).join("")}</tbody></table></div>`;
}

function openUpdateSettings() {
  closeModal();
  showView("settings");
  $("updateSettings")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function maybeShowUpdateNotice() {
  if (!updateReachable || !localConnected || updateRequestPending || !updateState || pendingUpdateChecks(updateState)) return;
  const notice = updateNoticeData(updateState, updatePromptManual);
  if (!notice) { updatePromptManual = false; return; }
  if (state?.preview) { updatePromptManual = false; return; }
  // Existing settings and QR flows own their dialog. A later status poll will
  // retry this queued notice after that dialog and the install wizard close.
  if (document.hidden || $("modalBack")?.classList.contains("show") || $("app")?.classList.contains("hidden")) return;
  if (notice.keys.every(key => updatePromptSeen.has(key))) { updatePromptManual = false; return; }
  notice.keys.forEach(key => updatePromptSeen.add(key));
  updatePromptManual = false;
  const u = updateState;
  const appLatest = u.error || !u.latest_version ? "暂未确认" : versionLabel(u.latest_version);
  const rows = [{ name: "爱屋及鱼", current: versionLabel(u.current_version), latest: appLatest },
    ...notice.plugins.map(row => ({ name: row.name || row.id, current: row.installed ? versionLabel(row.installed) : "未识别", latest: versionLabel(row.latest) }))];
  openModal(notice.title, `<div class="version-update-prompt" id="updatePromptContent"><div class="version-update-intro"><span class="version-update-spark" aria-hidden="true">✦</span><p>发现了可以选择的更新。先看看版本，再决定什么时候更新。</p></div>${updateVersionTable(rows)}<p class="version-update-note">插件随爱屋及鱼安装包一起更新。“前往更新”只打开更新设置，不会自动下载或安装。</p><div class="row version-update-actions"><button class="ghost" id="updatePromptLater">稍后提醒</button><button class="primary" id="updatePromptOpen">前往更新</button></div></div>`, null);
  // Keep the shared dialog's event handlers untouched; these controls disappear
  // with its body and cannot affect a later settings/QR dialog.
  $("modalOk").classList.add("hidden");
  $("modalCancel").classList.add("hidden");
  $("updatePromptLater").onclick = () => {
    closeModal();
    saveUpdatePreferences({ snooze_hours: 24 }, "好，明天再提醒软件与插件更新。");
  };
  $("updatePromptOpen").onclick = openUpdateSettings;
  $("updatePromptOpen").focus();
}

function renderPluginUpdateStatus() {
  const element = $("pluginUpdateSummary");
  if (!element) return;
  const p = updateState?.plugin_updates || {};
  const rows = pluginUpdateRows();
  const updates = p.state === "checked" ? rows.filter(row => ["update_available", "available"].includes(row.status)) : [];
  const unknown = !rows.length || rows.some(row => !["current", "not_installed", "update_available", "available"].includes(row.status));
  const checking = pendingUpdateChecks(updateState);
  let label = "等待检查", message = "和软件一起检查随附插件版本；下载和更新都由你决定。";
  if (!updateReachable) { label = "暂未连接"; message = "暂时读不到插件版本，请稍后手动检查。"; }
  else if (checking) { label = "检查中"; message = "正在合并检查软件与随附插件版本…"; }
  else if (updates.length) { label = `${updates.length} 个插件可更新`; message = "随爱屋及鱼安装包更新，可先到设置查看版本与下载选项。"; }
  else if (p.state === "checked") { label = unknown ? "部分版本待确认" : "已检查"; message = unknown ? "有插件版本尚未确认，请展开查看；未确认不代表已是最新。" : "已安装的随附插件暂无可用更新。"; }
  else if (p.state === "unavailable") { label = "检查未完成"; message = p.message || p.error || "暂时无法确认最新插件版本，请稍后再试。"; }
  else if (p.state === "no_release") { label = "暂无版本清单"; message = p.message || "当前发行版尚无可验证的插件版本清单。"; }
  setText("pluginUpdateBadge", label);
  $("pluginUpdateBadge").className = "badge " + (updates.length || checking ? "amber" : p.state === "checked" && !unknown ? "green" : "gray");
  setText("pluginUpdateMessage", message);
  $("pluginUpdateVersions").innerHTML = rows.length ? updateVersionTable(rows.map(row => ({ name: row.name || row.id,
    current: row.is_installed === false ? "未安装" : row.installed ? versionLabel(row.installed) : "未识别",
    latest: row.latest ? versionLabel(row.latest) : "暂未确认" }))) : '<p class="hint">联网检查完成后显示各插件的当前版本与最新版本。</p>';
  $("pluginUpdateOpen").onclick = () => { showView("settings"); $("updateSettings")?.scrollIntoView({ behavior: "smooth", block: "start" }); };
}

function renderUpdates() {
  const u = updateState || {};
  const available = !!u.available;
  const pluginsAvailable = u.plugin_updates?.state === "checked" && !u.plugin_updates?.error
    && pluginUpdateRows(u).some(row => ["update_available", "available"].includes(row.status));
  const canRepairPlugins = pluginsAvailable && u.plugin_updates?.can_repair === true;
  const rollback = u.rollback === true;
  const packageAvailable = available || canRepairPlugins || rollback;
  const verified = !!(u.downloaded && u.download_verified);
  const downloading = !!u.downloading;
  const checking = pendingUpdateChecks(u);
  const busy = updateRequestPending || checking || downloading;
  const progress = Math.max(0, Math.min(100, Number(u.progress) || 0));
  const latest = versionLabel(u.latest_version);
  if (u.current_version) {
    setText("sideVersion", versionLabel(u.current_version));
    setText("updateCurrentVersion", versionLabel(u.current_version));
  }
  if (!updateRequestPending && typeof u.auto_check === "boolean") $("updateAutoCheck").checked = u.auto_check;
  $("updateAutoCheck").disabled = updateRequestPending || !localConnected;
  const appFresh = available && updateVersionKey(u.ignored_version) !== updateVersionKey(u.latest_version);
  const pluginFresh = !!updateNoticeData({ ...u, auto_check: true, snoozed_until: 0 })?.plugins.length;
  const fresh = appFresh || pluginFresh;
  $("sideUpdateDot").classList.toggle("hidden", !fresh);
  $("sideUpdate").classList.toggle("has-update", fresh);
  setText("sideUpdateLabel", downloading ? `更新下载中 ${progress}%` : verified ? "更新已备好 · 查看" : fresh ? pluginFresh && !appFresh ? "插件有更新 · 查看" : "发现新版本 · 查看" : checking ? "正在检查更新" : "检查更新");
  setText("updateLatestVersion", u.latest_version && !u.error ? (rollback ? `${latest}（匹配旧版）` : latest) : "暂未确认");
  let message = u.message || "自动检查只会提醒，更新与否由你决定。";
  if (u.error) message = `更新暂未完成：${u.error}`;
  else if (downloading) message = `正在下载 ${latest}（${progress}%），你可以继续使用软件。`;
  else if (verified) message = rollback ? "匹配旧版的安装包已通过校验。选择安装后本窗口会关闭；安装匹配版本后再恢复对应备份。" : "更新包已通过校验。安装前会备份允许恢复的设置与随包插件；本窗口随后关闭。";
  else if (checking) message = "正在检查更新…";
  else if (available) message = `${latest} 已发布。你可以现在更新，也可以继续使用当前版本。`;
  else if (canRepairPlugins) message = "软件已是当前发行版本，但本机插件有更新。下载安装同版更新包即可修复插件，下载和安装仍由你决定。";
  else if (pluginsAvailable) message = "本机插件有新版，但当前发行包尚未通过同版修复确认。请重新检查，或打开发布记录查看。";
  else if (u.checked_at && u.latest_version) message = "没有发现比当前版本更新的正式版本。";
  setText("updateMessage", message);
  $("updateMessage").classList.toggle("has-error", !!u.error);
  setText("updateVersionBadge", checking ? "检查中" : downloading ? "正在下载" : u.error ? "请稍后重试" : verified ? rollback ? "旧版安装包已备好" : "更新已备好" : rollback ? "准备匹配旧版" : available ? `可更新至 ${latest}` : pluginsAvailable ? "插件可更新" : u.checked_at && u.latest_version ? "暂无软件新版" : "等待确认");
  $("updateVersionBadge").className = "badge " + (u.error ? "red" : verified ? "green" : packageAvailable || checking ? "amber" : "gray");
  $("updateCheck").disabled = busy || !localConnected;
  setText("updateCheck", checking ? "正在检查…" : "检查更新");
  $("updateDownload").classList.toggle("hidden", !packageAvailable || verified);
  $("updateDownload").disabled = busy || !updateReachable || !localConnected;
  setText("updateDownload", downloading ? `正在下载 ${progress}%` : u.error ? "重试下载" : pluginsAvailable && !available ? "下载插件更新安装包" : "下载更新");
  $("updateInstall").classList.toggle("hidden", !verified || !packageAvailable);
  $("updateInstall").disabled = !verified || !packageAvailable || busy || !updateReachable || !localConnected;
  setText("updateInstall", rollback ? "安装匹配旧版" : canRepairPlugins && !available ? "安装并修复插件" : "安装更新");
  $("updateIgnore").classList.toggle("hidden", !available || verified || downloading);
  $("updateIgnore").disabled = busy;
  $("updateInstallNote").classList.toggle("hidden", !verified);
  $("updateProgressWrap").classList.toggle("hidden", !downloading);
  $("updateProgress").style.width = `${progress}%`;
  $("updateProgressTrack").setAttribute("aria-valuenow", String(progress));
  setText("updatePercent", `${progress}%`);
  setText("updateCheckedAt", u.checked_at ? `上次检查：${fmtDate(u.checked_at)}` : "首次联网检查后，会在这里显示检查时间。");
  const releaseURL = /^https:\/\/github\.com\/myflww\/aiwu-jiyu\/releases(?:\/|$)/.test(u.release_url || "") ? u.release_url : "https://github.com/myflww/aiwu-jiyu/releases";
  $("updateReleaseLink").href = releaseURL;
  $("updateBanner").classList.toggle("hidden", !(available && u.notify));
  setText("updateBannerTitle", verified ? `${latest} 已准备好，随时可以更新` : `发现新版本 · ${latest}`);
  setText("updateBannerMessage", message);
  $("updateBannerProgressWrap").classList.toggle("hidden", !downloading);
  $("updateBannerProgress").style.width = `${progress}%`;
  setText("updateBannerAction", downloading ? `正在下载 ${progress}%` : verified ? "安装更新" : u.error ? "重试下载" : "下载更新");
  $("updateBannerAction").disabled = busy || !updateReachable || !localConnected;
  $("updateLater").disabled = updateRequestPending;
  renderPluginUpdateStatus();
  maybeShowUpdateNotice();
}

async function loadUpdateStatus() {
  if (updateStatusLoading || updateRequestPending) return;
  updateStatusLoading = true;
  try {
    updateState = await api("/api/updates");
    updateReachable = true;
    renderUpdates();
  } catch (err) {
    updateReachable = false;
    renderUpdates();
    setText("updateMessage", "暂时读不到更新信息。稍后可以点击“检查更新”重试。");
  } finally { updateStatusLoading = false; }
}

async function runUpdateAction(action) {
  if (updateRequestPending) return;
  if (action === "check") updatePromptManual = true;
  if (["download", "install"].includes(action) && !updateState?.available && updateState?.plugin_updates?.can_repair !== true && updateState?.rollback !== true) {
    toast("当前没有通过确认的软件更新或同版插件修复包，请先重新检查。");
    return;
  }
  if (action === "install" && !(updateState?.downloaded && updateState?.download_verified)) {
    toast("先下载并校验更新包，再来安装。");
    return;
  }
  updateRequestPending = true;
  renderUpdates();
  try {
    updateState = action === "download" && updateState?.rollback
      ? await api("/api/recovery/rollback", {method:"POST",body:JSON.stringify({backup_id:updateState.rollback_backup_id,confirmed:true})})
      : await api(`/api/updates/${action}`, { method: "POST", body: JSON.stringify({}) });
    updateReachable = true;
    if (action === "install") toast("正在启动更新安装，软件窗口即将关闭。完成后加载新版软件与插件。");
  } catch (err) {
    if (action === "check") updatePromptManual = false;
    updateState = { ...(updateState || {}), error: err.message || String(err) };
    toast(err.message || String(err));
  } finally { updateRequestPending = false; renderUpdates(); }
}

async function saveUpdatePreferences(preferences, successMessage) {
  if (updateRequestPending) return;
  updateRequestPending = true;
  renderUpdates();
  try {
    updateState = await api("/api/updates/settings", { method: "POST", body: JSON.stringify(preferences) });
    updateReachable = true;
    if (successMessage) toast(successMessage);
  } catch (err) { toast(err.message || String(err)); }
  finally { updateRequestPending = false; renderUpdates(); }
}

function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 2800);
}

async function api(path, options = {}) {
  const opts = { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } };
  const response = await fetch(path, opts);
  let data = {};
  try { data = await response.json(); } catch (e) { data = {}; }
  if (!response.ok || data.ok === false || data.status === "error") {
    throw new Error(data.message || `请求失败（${response.status}）`);
  }
  return data;
}

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function fmtDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function fmtSize(mb) {
  const value = Number(mb) || 0;
  if (!value) return "0 MB";
  return value >= 1024 ? `${(value / 1024).toFixed(1)} GB` : `${Math.round(value)} MB`;
}

async function refreshState(silent = true) {
  try {
    const next = await api("/api/state");
    state = next;
    renderCompanionState();
    setConnectionStatus(true);
    renderPills();
    if (!silent) return state;
    if (!$("app").classList.contains("hidden")) {
      renderOverview();
      if (!$("view-whitelist").classList.contains("hidden")) renderWhitelist();
      if (!$("view-settings").classList.contains("hidden") && !settingsDirty) renderSettings();
    }
    return state;
  } catch (err) {
    setConnectionStatus(false);
    if (!silent) throw err;
    return null;
  }
}

function renderPills() {
  if (!state) return;
  const map = [
    ["pillAstrbot", state.status?.astrbot?.up],
    ["pillNapcat", state.status?.napcat?.up],
  ];
  map.forEach(([id, up]) => {
    const dot = $(id);
    if (dot) dot.className = "dot" + (up ? " on" : "");
  });
}

function showView(name) {
  clearDashboardCredentials();
  $$(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === name);
    if (btn.dataset.view === name) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  });
  ["overview", "whitelist", "settings", "logs", "lovehouse", "artifacts", "plugins"].forEach((key) => {
    const section = $("view-" + key);
    if (section) section.classList.toggle("hidden", key !== name);
  });
  const [title, sub] = VIEW_META[name] || ["", ""];
  $("pageTitle").textContent = title;
  $("pageSub").textContent = sub;
  document.title = `${title} · 爱屋及鱼`;
  globalThis.scrollTo?.(0, 0);
  clearInterval(logTimer);
  logTimer = null;
  if (name === "overview") { renderOverview(); refreshMetrics(); }
  if (name === "whitelist") { renderWhitelist(); loadContacts(); }
  if (name === "settings") { renderSettings(); loadQuickLoginInfo(); }
  if (name === "logs") { loadLog(); logTimer = setInterval(loadLog, 3000); }
  if (name === "lovehouse") loadLoveHouse();
  if (name === "artifacts") loadArtifacts();
  if (name === "plugins") loadPlugins();
  globalThis.AiwuExperience?.loadView(name).catch(() => {});
  globalThis.AiwuConversation?.loadView(name).catch(() => {});
}

function showApp() {
  $("wizard").classList.add("hidden");
  $("app").classList.remove("hidden");
  showView("overview");
}

function showWizard(step) {
  clearDashboardCredentials();
  $("app").classList.add("hidden");
  $("wizard").classList.remove("hidden");
  wizardMaxStep = Math.max(wizardMaxStep, step);
  wizardStep = step;
  renderWizard();
}

function renderWizard() {
  const steps = $("wizardSteps");
  steps.innerHTML = STEP_NAMES.map((name, index) => {
    let cls = "step-chip";
    if (index === wizardStep) cls += " active";
    else if (index < wizardStep) cls += " done";
    return `<span class="${cls}">${index + 1} ${esc(name)}</span>`;
  }).join("");

  $("wizardBack").classList.toggle("hidden", wizardStep === 0 || wizardStep === 1);
  $("wizardNext").classList.remove("hidden");
  $("wizardNext").textContent = wizardStep === 5 ? "进入首页" : "继续";
  $("wizardHint").textContent = "";

  const body = $("wizardBody");
  if (wizardStep === 0) {
    body.innerHTML = `
      <h2>选一个她住下来的地方</h2>
      <p class="lead">聊天服务、连接配置和运行记录，都会安放在这个文件夹里。建议选一个空间充足的位置。</p>
      <label class="field" style="margin-bottom:16px"><span>安装目录</span>
        <div class="input-row">
          <input type="text" id="wizardInstallDir" value="${esc(state?.install || "")}" placeholder="例如 D:\\Dafeiyu">
          <button class="ghost" id="wizardBrowse">浏览</button>
        </div>
      </label>
      <dl class="kv">
        <dt>我们会准备</dt><dd>对话服务、QQ 连接服务和独立运行环境</dd>
        <dt>需要网络</dt><dd>首次安装会联网下载，请保持连接</dd>
        <dt>需要准备</dt><dd>一个 QQ 小号、一个 DeepSeek API Key</dd>
      </dl>
      <div class="notice" style="margin-top:16px">
        NapCat 走的是个人号协议，不要用主号登录机器人；主号只作为“主人”和白名单使用。
      </div>`;
    $("wizardBrowse").addEventListener("click", () => chooseFolder());
    $("wizardNext").textContent = state?.installed ? "使用已有运行环境" : "开始安装";
  } else if (wizardStep === 1) {
    body.innerHTML = `
      <h2>正在安装运行环境</h2>
      <p class="lead">我们正在下载并准备聊天所需的服务。可以先去忙别的，保持这个窗口打开就好。</p>
      <div class="progress"><span id="installBar"></span></div>
      <div class="row between" style="margin-top:10px">
        <div class="small muted" id="installStep">准备中…</div>
        <div class="small mono" id="installPercent">0%</div>
      </div>
      <pre class="install-log" id="installLog"></pre>`;
    $("wizardNext").disabled = true;
    startInstallPolling();
  } else if (wizardStep === 2) {
    body.innerHTML = `
      <h2>让她听见，也能回应</h2>
      <p class="lead">填入你的 DeepSeek API Key，连接对话模型。Key 会加密保存在本机，用于请求你选择的模型服务。</p>
      <div class="provider-shortcut wizard-provider-shortcut"><a class="provider-link" href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener noreferrer" data-deepseek-link>获取 DeepSeek API Key <span aria-hidden="true">↗</span></a><p>在浏览器打开官方 API 平台。创建 Key 后，回到这里粘贴即可。</p></div>
      <div class="stack">
        <label class="field"><span>API Key</span>
          <input type="password" id="wizardKey" placeholder="sk-..." autocomplete="off">
        </label>
        <label class="field"><span>模型</span>
          <select id="wizardModel">
            <option value="deepseek-chat">deepseek-chat（日常对话）</option>
            <option value="deepseek-reasoner">deepseek-reasoner（深度推理）</option>
          </select>
        </label>
      </div>
      <div class="notice" style="margin-top:14px">如果你刚换过 Key，直接填新的；留空会保留上一次保存的值。</div>`;
    if (state?.model?.key_mask) {
      $("wizardKey").placeholder = `已保存 ${state.model.key_mask}，留空不修改`;
    }
    $("wizardNext").textContent = "保存并继续";
  } else if (wizardStep === 3) {
    body.innerHTML = `
      <h2>和她连上线</h2>
      <p class="lead">手机 QQ 先切换到小号，再扫下面的码。登录成功后她会自动接上 AstrBot。</p>
      <div class="qr-box">
        <div class="qr-frame"><img id="wizardQr" alt="QQ 登录二维码"></div>
        <div>
          <div class="row" style="margin-bottom:10px"><span class="badge amber" id="qrBadge">等待扫码</span></div>
          <div class="qr-tip" id="qrTip">二维码约 2 分钟有效，页面会自动换码。</div>
          <div class="notice" style="margin-top:16px">
            如果 NapCat 还没启动，这里会自动拉起；首次启动可能需要十几秒。
          </div>
          <div class="notice" style="margin-top:10px">
            首次启动 NapCat 时 Windows 可能弹出防火墙提示，请勾选“专用网络”并允许；否则 QQ 消息可能收不到。
          </div>
          <button class="ghost" style="margin-top:14px" id="qrRefresh">立即刷新二维码</button>
        </div>
      </div>`;
    $("wizardNext").textContent = "已登录，继续";
    startQrFlow("wizardQr", "qrBadge", "qrTip");
    $("qrRefresh").addEventListener("click", () => reloadQr("wizardQr"));
  } else if (wizardStep === 4) {
    body.innerHTML = `
      <h2>让她先认识你</h2>
      <p class="lead">填你的主号 QQ。她会把你设为主人，并自动加入私聊白名单；其他人默认不回应。</p>
      <div class="stack">
        <label class="field"><span>主人 QQ</span>
          <input type="text" id="wizardOwner" placeholder="例如 123456789" value="${esc(state?.owner_qq || "")}">
        </label>
        <div class="notice green">聊天权限默认只放你一个人。以后可以在“聊天权限”页，邀请信任的好友和群聊。</div>
      </div>`;
    $("wizardNext").textContent = "保存并继续";
  } else if (wizardStep === 5) {
    body.innerHTML = `
      <h2>设置完成</h2>
      <p class="lead">最后会启动对话服务、检查人格和插件。完成后，就可以去 QQ 和大肥鱼打个招呼。</p>
      <dl class="kv">
        <dt>机器人</dt><dd>${esc(state?.bot_nickname || "大肥鱼")} <span class="mono">${esc(state?.bot_qq || "")}</span></dd>
        <dt>主人</dt><dd class="mono">${esc(state?.owner_qq || "")}</dd>
        <dt>模型</dt><dd>${esc(state?.model?.model || "deepseek-chat")}</dd>
        <dt>白名单</dt><dd>${esc((state?.whitelist?.private || []).length)} 个私聊 / ${esc((state?.whitelist?.groups || []).length)} 个群</dd>
      </dl>
      <div class="notice blue" style="margin-top:16px">关闭窗口后，服务会继续运行。想让她下线休息，可以在首页点击“一键关闭”，让她下线休息。</div>`;
    $("wizardNext").textContent = "进入首页";
  }
}

async function chooseFolder(targetId = "wizardInstallDir") {
  if (!window.pywebview || !window.pywebview.api) {
    toast("目录选择器只在桌面应用窗口里可用，可以直接输入路径");
    return;
  }
  try {
    const path = await window.pywebview.api.choose_folder();
    const target = $(targetId);
    if (path && target) target.value = path;
  } catch (err) {
    toast(err.message || String(err));
  }
}

function goWizard(delta) {
  if (wizardStep === 1) return;
  const next = Math.max(0, Math.min(5, wizardStep + delta));
  if (next === wizardStep) return;
  wizardStep = next;
  renderWizard();
}

async function wizardNext() {
  try {
    if (wizardStep === 0) {
      const dir = ($("wizardInstallDir")?.value || "").trim();
      if (!dir) throw new Error("请填写安装目录");
      if (dir !== state?.install) {
        const moved = await api("/api/setup/relocate", {
          method: "POST",
          body: JSON.stringify({ install_dir: dir }),
        });
        if (moved.relaunch) {
          toast("正在切换到新安装目录，窗口会重新打开…");
          return;
        }
        await refreshState(false);
      }
      if (state?.installed) { wizardStep = 2; renderWizard(); return; }
      wizardStep = 1;
      renderWizard();
      const res = await api("/api/setup/install", {
        method: "POST",
        body: JSON.stringify({ model: { model: "deepseek-chat", api_base: "https://api.deepseek.com/v1" } }),
      });
      if (!res.ok) throw new Error(res.message || "安装启动失败");
      return;
    }
    if (wizardStep === 1) {
      $("wizardNext").disabled = true;
      const res = await api("/api/setup/install", {
        method: "POST",
        body: JSON.stringify({ model: { model: "deepseek-chat", api_base: "https://api.deepseek.com/v1" } }),
      });
      if (!res.ok) throw new Error(res.message || "安装启动失败");
      startInstallPolling();
      return;
    }
    if (wizardStep === 2) {
      const key = $("wizardKey").value.trim();
      const model = $("wizardModel").value;
      if (key && !key.startsWith("sk-")) throw new Error("Key 看起来不对，通常以 sk- 开头");
      if (!key && !state?.has_key) throw new Error("请先填写 API Key");
      const body = { model: { model, api_base: "https://api.deepseek.com/v1" } };
      if (key) body.api_key = key;
      await api("/api/config", { method: "POST", body: JSON.stringify(body) });
      await refreshState(false);
      wizardStep = 3;
      renderWizard();
      return;
    }
    if (wizardStep === 3) {
      if (!state?.napcat?.logged_in) throw new Error("还没有扫码成功");
      await api("/api/qr/complete", { method: "POST", body: JSON.stringify({}) });
      await refreshState(false);
      wizardStep = 4;
      renderWizard();
      return;
    }
    if (wizardStep === 4) {
      const owner = $("wizardOwner").value.trim();
      if (!/^\d{5,12}$/.test(owner)) throw new Error("请填正确的 QQ 号");
      await api("/api/config", { method: "POST", body: JSON.stringify({ owner_qq: owner }) });
      await refreshState(false);
      wizardStep = 5;
      renderWizard();
      return;
    }
    if (wizardStep === 5) {
      toast("正在启动服务…");
      await api("/api/setup/finalize", { method: "POST", body: JSON.stringify({}) });
      await new Promise((resolve) => setTimeout(resolve, 2500));
      await refreshState(false);
      showApp();
    }
  } catch (err) {
    toast(err.message || String(err));
  }
}

function startInstallPolling() {
  clearInterval(installTimer);
  const tick = async () => {
    try {
      const data = await api("/api/setup/status");
      const bar = $("installBar");
      if (!bar) return;
      bar.style.width = `${data.progress || 0}%`;
      $("installPercent").textContent = `${data.progress || 0}%`;
      $("installStep").textContent = data.step || "安装中…";
      const log = $("installLog");
      if (log) {
        const text = (data.log || []).join("\n");
        const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
        log.textContent = text || "等待输出…";
        if (nearBottom) log.scrollTop = log.scrollHeight;
      }
      if (data.done) {
        clearInterval(installTimer);
        installTimer = null;
        $("wizardNext").disabled = false;
        $("wizardNext").textContent = "安装完成，继续";
        toast("运行环境安装完成");
        await refreshState(false);
        wizardStep = 2;
        setTimeout(renderWizard, 600);
      } else if (data.error) {
        clearInterval(installTimer);
        installTimer = null;
        $("wizardNext").disabled = false;
        $("wizardNext").textContent = "重试";
        toast("安装失败：" + data.error);
      }
    } catch (err) { /* transient */ }
  };
  tick();
  installTimer = setInterval(tick, 1000);
}

async function ensureServices() {
  if (!state?.status?.napcat?.up) {
    try { await api("/api/start", { method: "POST", body: JSON.stringify({}) }); } catch (e) { /* ignore */ }
    await new Promise((resolve) => setTimeout(resolve, 2500));
    await refreshState(false);
  }
}

function reloadQr(imgId) {
  const img = $(imgId);
  if (!img) return;
  img.src = `/api/qr?refresh=1&t=${Date.now()}`;
}

function startQrFlow(imgId, badgeId, tipId) {
  const generation = ++qrFlowGeneration;
  clearInterval(qrTimer);
  clearInterval(qrRefreshTimer);
  qrTimer = null;
  qrRefreshTimer = null;
  ensureServices().then(() => {
    if (generation !== qrFlowGeneration || !$(imgId)) return;
    reloadQr(imgId);
    const tick = async () => {
      if (generation !== qrFlowGeneration || !$(imgId)) return;
      try {
        const res = await fetch(`/api/qr/status?t=${Date.now()}`);
        const payload = await res.json();
        if (generation !== qrFlowGeneration || !$(imgId)) return;
        const data = payload.data || {};
        if (data.isLogin) {
          clearInterval(qrTimer);
          clearInterval(qrRefreshTimer);
          qrTimer = null;
          qrRefreshTimer = null;
          if ($(badgeId)) { $(badgeId).className = "badge green"; $(badgeId).textContent = "已登录"; }
          if ($(tipId)) $(tipId).textContent = `登录成功：${payload.account?.nickname || ""} ${payload.account?.qq || ""}`;
          await refreshState(false);
        } else if (data.qrLoginAccepted) {
          if ($(badgeId)) { $(badgeId).className = "badge blue"; $(badgeId).textContent = "已扫码，等待确认"; }
        } else if (data.loginPhase === "waiting_qrcode") {
          if ($(badgeId)) { $(badgeId).className = "badge amber"; $(badgeId).textContent = "等待扫码"; }
        }
      } catch (err) { /* transient */ }
    };
    tick();
    qrTimer = setInterval(tick, 2500);
    qrRefreshTimer = setInterval(() => { if (qrTimer) reloadQr(imgId); }, 100000);
  });
}

function renderCompanionState() {
  if (!state) return;
  const a = state.status?.astrbot || {}, n = state.status?.napcat || {};
  const loggedIn = !!state.napcat?.logged_in;
  const online = !!(a.up && n.up && loggedIn);
  const needsSetup = state.installed === false;
  const stopping = $("btnStop").dataset.busy === "true";
  const starting = $("btnStart").dataset.busy === "true" || $("btnRestart").dataset.busy === "true";
  const serviceAwake = !!(a.up || n.up);
  document.body.dataset.companion = stopping ? "stopping" : starting ? "starting" : needsSetup ? "new" : online ? "online" : serviceAwake ? "connecting" : "resting";
  const mascotPose = !needsSetup && !serviceAwake && !starting ? "dafeiyu-rest.png" : "dafeiyu-welcome.png";
  setMascotImage("sidebarMascot", mascotPose, !serviceAwake && !needsSetup ? "休息中的大肥鱼" : "醒着的大肥鱼");
  setMascotImage("heroMascot", artStyle === "classic" && !serviceAwake ? "dafeiyu-rest.png" : ART_STYLES[artStyle] || ART_STYLES.daily);
  setText("sidebarMascotCaption", stopping ? "正在停止服务" : starting ? "正在启动服务" : needsSetup ? "等待首次设置" : online ? "QQ 在线" : serviceAwake ? "等待连接" : "服务已停止");
  globalThis.AiwuExperience?.paintState();
}

function renderOverview() {
  if (!state) return;
  const a = state.status?.astrbot || {};
  const n = state.status?.napcat || {};
  const loggedIn = !!state.napcat?.logged_in;
  const online = !!(a.up && n.up && loggedIn);
  const needsSetup = state.installed === false;
  renderCompanionState();
  const primary = $("btnStart");
  let actionLabel = "一键开启";
  let heroTitle = "大肥鱼正在休息";
  let description = "启动服务后，使用 QQ 与她聊天。";
  let statusText = "大肥鱼正在休息";
  let footnote = "关闭窗口会保留托盘与后台服务。";
  if (needsSetup) {
    actionLabel = "开始领养";
    heroTitle = "一键领养大肥鱼";
    description = "选好安家的位置，再连接对话模型和 QQ 小号。";
    statusText = state.preview ? "界面预览 · 尚未安装" : "欢迎来到爱屋及鱼";
    footnote = "准备一个 QQ 小号和 DeepSeek API Key，就可以开始。";
  } else if (!state.has_key) {
    actionLabel = "设置对话模型";
    heroTitle = "配置对话模型";
    description = "保存对话模型的 API Key，让她能听懂你的话。";
    statusText = "还差一步，准备对话模型";
    footnote = "Key 会加密保存在这台电脑上。";
  } else if (a.up && n.up && !loggedIn) {
    actionLabel = "扫码连接 QQ";
    heroTitle = "等待 QQ 登录";
    description = "用手机 QQ 小号扫码，让大肥鱼和你连上线。";
    statusText = "服务已启动 · 等待 QQ 登录";
    footnote = "请先在手机 QQ 切换到小号，再扫码。";
  } else if (online) {
    actionLabel = "已开启 · 正在陪你";
    heroTitle = "大肥鱼在线";
    description = "对话服务与 QQ 连接正常。";
    statusText = "QQ 已连接";
    footnote = "打开 QQ，给她的小号发一条消息吧。";
  }
  $("heroTitle").innerHTML = heroTitle;
  $("heroDescription").textContent = description;
  setText("heroStatus", statusText);
  $("heroFootnote").textContent = footnote;
  $("heroStatusDot").classList.toggle("asleep", !online);
  if (primary.dataset.busy !== "true") {
    primary.innerHTML = `<svg class="icon" aria-hidden="true"><use href="#i-power"></use></svg>${esc(actionLabel)}`;
    primary.disabled = online || $("btnStop").dataset.busy === "true" || $("btnRestart").dataset.busy === "true";
    primary.classList.toggle("connected", online);
  }
  if ($("btnStop").dataset.busy !== "true") {
    $("btnStop").disabled = !(a.up || n.up) || primary.dataset.busy === "true" || $("btnRestart").dataset.busy === "true";
    $("btnStop").title = a.up || n.up ? "让她下线休息；只停止聊天服务，软件窗口会保留" : "聊天服务尚未开启";
  }
  $("ovAstrbot").textContent = a.up ? "运行中" : "已停止";
  $("ovAstrbotDesc").textContent = a.up ? `PID ${a.pid}` : "端口 6185";
  $("ovNapcat").textContent = n.up ? "运行中" : "已停止";
  $("ovNapcatDesc").textContent = n.up ? `PID ${n.pid}` : "端口 6099";
  $("ovAccount").textContent = loggedIn ? (state.napcat.nickname || "QQ 已登录") : "等待连接";
  $("ovAccountDesc").textContent = state.napcat?.qq ? `QQ ${state.napcat.qq}` : "扫码登录后，她就能收到消息";
  $("btnRelogin").textContent = needsSetup ? "先完成设置 ↗" : loggedIn ? "切换登录 ↗" : "扫码登录 ↗";
  $("ovWhitelist").innerHTML = `${(state.whitelist?.private || []).length} <small>位私聊 · ${(state.whitelist?.groups || []).length} 个群</small>`;
  $("ovWhitelistDesc").textContent = state.whitelist?.enable ? "只回应名单中的人，主人始终可以聊天" : "聊天限制已关闭，请留意群聊与陌生人消息";
  $("ovOwner").textContent = state.owner_qq || "-";
  $("ovModel").textContent = state.model?.model || "-";
  $("ovInstall").textContent = state.install || "-";
  $("ovPersonaBadge").textContent = state.installed === false ? "等待安家" : state.setup_complete ? "人格已准备" : "待完成";
  $("ovPersonaBadge").className = "badge " + (state.installed !== false && state.setup_complete ? "green" : "amber");
  const quickBtn = $("btnQuickLogin");
  if (quickBtn) {
    const show = !state.napcat?.logged_in && !!state.bot_qq;
    quickBtn.classList.toggle("hidden", !show);
    quickBtn.textContent = state.bot_qq ? `自动登录 ${state.bot_qq}` : "尝试自动登录";
  }

  if (state.metrics && !metricsLoading) renderMetrics(state.metrics);

  const pc = state.pc_access || {};
  const adminCount = Object.keys(state.members || {}).length;
  $("pcBadge").textContent = pc.enabled ? "已授权" : "未授权";
  $("pcBadge").className = "badge " + (pc.enabled ? "green" : "gray");
  $("pcDesc").textContent = pc.enabled
    ? `已授权主人${adminCount ? `与 ${adminCount} 位管理员` : ""}${pc.desktop_enabled ? "使用电脑工具与桌面操控。" : "操作工作目录；完整桌面操控未开启。"}`
    : "电脑操作权限已关闭。可在此设置工作目录与操作授权。";
  renderDiagnosticsStatus();
}

function renderMetrics(m) {
  const valid = Number(m.total_mb) > 0 && m.available !== false;
  const pct = valid ? Math.max(0, Math.min(100, Number(m.percent) || 0)) : 0;
  setText("memPercent", valid ? `${pct.toFixed(0)}%` : "—");
  setText("memBadge", valid ? pct >= 85 ? "占用较高" : pct >= 70 ? "较多" : "充足" : "不可用");
  $("memBadge").className = "badge " + (!valid ? "gray" : pct >= 85 ? "red" : pct >= 70 ? "amber" : "green");
  setText("memDesc", valid ? `已用 ${fmtSize(m.used_mb)} / ${fmtSize(m.total_mb)} · 可用 ${fmtSize(m.available_mb)}` : "暂时无法读取系统物理内存");
  $("memBar").style.width = `${pct}%`;
  $("memMeter").className = "meter" + (pct >= 85 ? " bad" : pct >= 70 ? " warn" : "");
  $("memMeter").setAttribute("aria-valuenow", String(pct));
  const sampledAt = Number(m.sampled_at) || Date.now();
  if (valid && sampledAt !== lastMetricSample) {
    lastMetricSample = sampledAt;
    metricHistory.push({ at: sampledAt, value: pct });
    metricHistory = metricHistory.filter(point => sampledAt - point.at <= 60000).slice(-31);
    $("memHistory").setAttribute("points", metricHistory.map(point => `${Math.max(0,240 - (sampledAt - point.at)/60000*240).toFixed(1)},${(56-point.value*0.54).toFixed(1)}`).join(" "));
  }
  setText("memUpdated", valid ? `每 2 秒更新 · ${new Date(sampledAt).toLocaleTimeString("zh-CN", {hour12:false})}` : "等待有效采样");
  setText("memAppTotal", m.processes_available === false ? "暂时无法读取进程占用" : `应用进程合计 ${fmtSize(m.app_total_mb ?? [m.app_mb, m.astrbot_mb, m.napcat_mb, m.lovehouse_mb].reduce((sum,n) => sum+(Number(n)||0),0))}`);
  const components = [["桌面界面",m.app_mb],["对话服务",m.astrbot_mb],["QQ 连接",m.napcat_mb],["记忆服务",m.lovehouse_mb]];
  $("memProcesses").innerHTML = components.map(([name, value]) => `<span class="mem-chip">${esc(name)} <b>${m.processes_available === false ? "—" : esc(fmtSize(value))}</b></span>`).join("");
}

async function refreshMetrics() {
  if (metricsLoading || document.hidden || $("view-overview").classList.contains("hidden")) return;
  metricsLoading = true;
  try {
    const data = await api("/api/metrics");
    if (state) state.metrics = data;
    renderMetrics(data);
  } catch (_) { setText("memUpdated", "采样中断 · 正在重试"); }
  finally { metricsLoading = false; }
}

function renderWhitelist() {
  if (!state) return;
  setText("wlOwnerSummary", state.owner_qq || "尚未设置主人 QQ");
  setText("wlPrivateCount", String((state.whitelist?.private || []).length));
  setText("wlGroupCount", String((state.whitelist?.groups || []).length));
  $("wlEnable").checked = !!state.whitelist?.enable;
  $("wlSummary").textContent = `私聊 ${ (state.whitelist?.private || []).length } 个 · 群聊 ${ (state.whitelist?.groups || []).length } 个`;
  const members = state.members || {};
  const pcEnabled = !!state.pc_access?.enabled;
  const level = (id) => {
    const perm = members[id];
    if (!perm) return "仅聊天";
    const parts = [];
    if (perm.read) parts.push("读取");
    if (perm.write) parts.push("写文件");
    if (perm.run) parts.push("执行命令");
    if (perm.desktop) parts.push(state.pc_access?.desktop_enabled ? "桌面操控" : "桌面操控预设");
    return parts.length
      ? pcEnabled ? "管理员 · " + parts.join(" + ") : "已预设 " + parts.join(" + ") + " · 电脑权限未开启"
      : "仅聊天";
  };
  const entries = [];
  (state.whitelist?.private || []).forEach((id) => entries.push({ type: "private", id, label: id === state.owner_qq ? "主人" : "私聊" }));
  (state.whitelist?.groups || []).forEach((id) => entries.push({ type: "group", id, label: "群聊" }));
  const list = $("wlList");
  if (!entries.length) {
    list.innerHTML = emptyState("给信任的人，留一个位置", "在上方填写 QQ 号或群号，就能邀请他们和大肥鱼聊天。主人始终可以聊天。");
    return;
  }
  list.innerHTML = entries.map((entry) => {
    if (entry.type === "group") {
      return `<div class="list-item">
        <div class="who"><b>群聊 <span class="mono">${esc(entry.id)}</span></b><span>群里所有人都能唤醒她</span></div>
        <div class="row"><button class="ghost" data-remove="${entry.type}|${esc(entry.id)}">移除</button></div>
      </div>`;
    }
    if (entry.id === state.owner_qq) {
      return `<div class="list-item">
        <div class="who"><b>私聊 <span class="mono">${esc(entry.id)}</span></b><span>主人 · 始终可聊天 · ${pcEnabled ? state.pc_access?.desktop_enabled ? "电脑操作已授权（含桌面操控）" : "已授权工作目录工具，桌面操控未开启" : "电脑权限未开启"}</span></div>
        <span class="badge green">主人</span>
      </div>`;
    }
    const isAdmin = !!members[entry.id];
    return `<div class="list-item">
      <div class="who"><b>私聊 <span class="mono">${esc(entry.id)}</span></b><span>${esc(level(entry.id))}</span></div>
      <div class="row">
        <button class="ghost" data-perm="${esc(entry.id)}">${isAdmin ? "调整权限" : "设为管理员"}</button>
        <button class="ghost" data-remove="${entry.type}|${esc(entry.id)}">移除</button>
      </div>
    </div>`;
  }).join("");
  list.querySelectorAll("[data-remove]").forEach((btn) => {
    btn.addEventListener("click", () => removeWhitelist(btn.dataset.remove));
  });
  list.querySelectorAll("[data-perm]").forEach((btn) => {
    btn.addEventListener("click", () => openMemberModal(btn.dataset.perm));
  });
}

function openMemberModal(id) {
  const perm = (state?.members || {})[id] || {};
  const row = (key, title, desc) => `
    <label class="perm-row">
      <input type="checkbox" class="perm-box" data-perm-key="${key}" ${perm[key] ? "checked" : ""}>
      <div>
        <b>${title}</b>
        <span class="small">${desc}</span>
      </div>
    </label>`;
  openModal(`权限 · ${id}`, `
    ${state?.pc_access?.enabled ? "" : '<div class="notice blue" style="margin-bottom:12px">电脑权限总开关还未开启。本次只保存该伙伴的权限预设；你主动开启电脑权限后，预设才可用于操作。</div>'}
    <div class="notice" style="margin-bottom:12px">
      文件和命令工具面向工作目录；桌面操控可查看屏幕、点击、输入并操作应用。桌面操控还需要主人开启总开关，仅在私聊生效。
    </div>
    <div class="row wrap" style="gap:8px;margin-bottom:12px">
      <button class="ghost" data-preset="none">仅聊天</button>
      <button class="ghost" data-preset="assist">助手（读写文件）</button>
      <button class="ghost" data-preset="operator">操作员（读写 + 命令）</button>
      <button class="ghost" data-preset="desktop">完整电脑操作</button>
    </div>
    <div class="stack">
      ${row("read", "读取文件", "查看工作目录里已有的文件、列目录")}
      ${row("write", "生成 / 修改文件", "在工作目录里写网页、脚本、文档等产物")}
      ${row("run", "执行命令", "运行 PowerShell（受安全策略限制，可做构建、转换、批量处理）")}
      ${row("desktop", "桌面操控", "查看屏幕与控件，点击、滚动、输入和打开应用；操作范围覆盖当前桌面")}
    </div>
    <div class="hint" style="margin-top:12px">保存后会重启一次 AstrBot，约 10 秒。</div>
  `, async () => {
    const read = !!document.querySelector('[data-perm-key="read"]')?.checked;
    const write = !!document.querySelector('[data-perm-key="write"]')?.checked;
    const run = !!document.querySelector('[data-perm-key="run"]')?.checked;
    const desktop = !!document.querySelector('[data-perm-key="desktop"]')?.checked;
    toast("正在保存权限并重启 AstrBot…");
    const data = await api("/api/members", {
      method: "POST",
      body: JSON.stringify({ qq: id, read, write, run, desktop }),
    });
    state.members = data.members || {};
    state.whitelist = data.whitelist || state.whitelist;
    await refreshState(false);
    renderWhitelist();
    toast(read || write || run || desktop ? `已更新 ${id} 的权限` : `${id} 已改为仅聊天`);
  });
  document.querySelectorAll("[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const preset = btn.dataset.preset;
      const values = {
        none: { read: false, write: false, run: false, desktop: false },
        assist: { read: true, write: true, run: false, desktop: false },
        operator: { read: true, write: true, run: true, desktop: false },
        desktop: { read: true, write: true, run: true, desktop: true },
      }[preset];
      ["read", "write", "run", "desktop"].forEach((key) => {
        const box = document.querySelector(`[data-perm-key="${key}"]`);
        if (box) box.checked = values[key];
      });
    });
  });
}

async function addWhitelist(type, id) {
  try {
    toast("正在写入并重启 AstrBot…");
    await api("/api/whitelist", { method: "POST", body: JSON.stringify({ action: "add", type, id }) });
    await refreshState(false);
    renderWhitelist();
    loadContacts();
    toast("已邀请到聊天小圈子，权限已生效");
  } catch (err) { toast(err.message || String(err)); }
}

async function removeWhitelist(pair) {
  const [type, id] = String(pair).split("|");
  try {
    await api("/api/whitelist", { method: "POST", body: JSON.stringify({ action: "remove", type, id }) });
    await refreshState(false);
    renderWhitelist();
    loadContacts();
    toast("已移除");
  } catch (err) { toast(err.message || String(err)); }
}

async function loadContacts() {
  try {
    const data = await api("/api/contacts");
    contacts = { ...data, loaded: true };
    renderContacts();
  } catch (err) {
    contacts = { friends: [], groups: [], logged_in: false, loaded: true };
    renderContacts();
  }
}

function renderContacts() {
  const list = $("contactsList");
  const query = ($("contactsSearch")?.value || "").trim().toLowerCase();
  const items = [];
  (contacts.friends || []).forEach((f) => items.push({ type: "private", id: f.id, name: f.remark || f.name, sub: f.remark && f.name ? f.name : "好友" }));
  (contacts.groups || []).forEach((g) => items.push({ type: "group", id: g.id, name: g.name, sub: "群聊" }));
  const filtered = items.filter((item) => {
    if (!query) return true;
    return item.id.includes(query) || String(item.name).toLowerCase().includes(query);
  }).slice(0, 80);
  $("contactsDesc").textContent = contacts.logged_in
    ? `已读取 ${(contacts.friends || []).length} 个好友、${(contacts.groups || []).length} 个群`
    : "QQ 小号还没登录，扫码后再刷新";
  if (!filtered.length) {
    if (!contacts.logged_in) {
      list.innerHTML = `<div class="hint">连接 QQ 小号后，这里会出现好友和群。</div>
        <button class="ghost" id="contactsScan" style="margin-top:8px">${state?.installed === false ? "先完成首次设置" : "去扫码登录"}</button>`;
      const scan = $("contactsScan");
      if (scan) scan.addEventListener("click", () => state?.installed === false ? showWizard(0) : openQrModal());
    } else {
      list.innerHTML = `<div class="hint">没有匹配的联系人</div>`;
    }
    return;
  }
  const existing = new Set([
    ...(state?.whitelist?.private || []).map((id) => `private|${id}`),
    ...(state?.whitelist?.groups || []).map((id) => `group|${id}`),
  ]);
  list.innerHTML = filtered.map((item) => {
    const key = `${item.type}|${item.id}`;
    const added = existing.has(key);
    return `<div class="list-item">
      <div class="who"><b>${esc(item.name || item.id)}</b><span class="mono">${esc(item.id)} · ${esc(item.sub)}</span></div>
      ${added ? '<span class="badge green">已在名单</span>' : `<button class="ghost" data-add="${key}">添加</button>`}
    </div>`;
  }).join("");
  list.querySelectorAll("[data-add]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const [type, id] = btn.dataset.add.split("|");
      addWhitelist(type, id);
    });
  });
}

function renderSettings() {
  if (!state) return;
  $("setKeyMask").textContent = state.model?.key_mask || "未保存";
  $("setModel").value = state.model?.model || "deepseek-chat";
  $("setApiBase").value = state.model?.api_base || "https://api.deepseek.com/v1";
  $("setOwner").value = state.owner_qq || "";
  $("setInstallDir").textContent = state.install || "-";
  const auto = state.autostart || {};
  $("setAutostart").checked = !!auto.enabled;
  $("setAutostart").disabled = auto.supported === false;
  $("setAutostartHint").textContent = auto.supported === false
    ? "当前系统不支持用户级注册表自启动。"
    : "当前用户级自启动，不需要管理员权限；启动后默认缩在托盘。";
  $("setAutoLogin").checked = !!state.auto_login;
  $("setAutoLogin").disabled = !state.bot_qq;
  const pc = state.pc_access || {};
  $("setWorkspace").value = pc.workspace || "";
  $("setWorkspaceHint").textContent = pc.enabled
    ? `电脑操作已授权；主人 + ${Object.keys(state.members || {}).length} 个管理员可调用，命令超时 ${pc.timeout_s || 60}s。`
    : "电脑权限未开启；可以先保存工作目录，需要时再到“我的肥鱼”页授权。";
  settingsDirty = false;
  setText("settingsSaveState", "对话设置已保存");
  renderUpdates();
}

async function loadQuickLoginInfo() {
  const hint = $("quickLoginHint");
  if (!hint) return;
  try {
    const data = await api("/api/napcat/quick-login");
    const list = (data.candidates || []).filter((item) => item.quick);
    if (!list.length) {
      hint.textContent = "还没有保存的快速登录授权；第一次扫码时勾选授权即可。";
      return;
    }
    const names = list.map((item) => `${item.nickname || "QQ"} ${item.qq}`).join("、");
    hint.textContent = `已授权：${names}${data.bot_qq ? `；启动时默认尝试 ${data.bot_qq}` : ""}`;
  } catch (err) {
    hint.textContent = err.message || String(err);
  }
}

async function loadLoveHouse() {
  if (!$("lhDesc")) return;
  $("lhDesc").textContent = "正在检测本机爱之屋…";
  try {
    const data = await api("/api/lovehouse/status");
    lovehouseState = data;
    if (!lovehouseSelected || !(data.libraries || []).some((item) => item.id === lovehouseSelected)) {
      lovehouseSelected = data.suggested_id || ((data.libraries || []).find((item) => !item.is_demo) || {}).id || "";
    }
    renderLoveHouse(data);
    renderLovehouseLibraries();
    if (!$("lhUmos").value.trim()) {
      $("lhUmos").value = (data.default_umos || []).join("\n");
    }
    await refreshLovehouseDetails();
  } catch (err) {
    $("lhDesc").textContent = err.message || String(err);
    $("lhBadge").className = "badge red";
    $("lhBadge").textContent = "检测失败";
  }
}

function renderLoveHouse(data) {
  const version = data.version || "-";
  const latest = data.latest || "";
  const selected = (data.libraries || []).find((item) => item.id === lovehouseSelected);
  setText("lhSelectedName", selected ? selected.name : "请先选择记忆库");
  setText("lhLibraryCount", `${(data.libraries || []).filter(item => !item.is_demo).length} 本记忆库`);
  $("lhDesc").textContent = data.installed
    ? `已安装 ${version}`
    : "先安装爱之屋，为回忆准备一个家。";
  $("lhDesc").title = data.installed ? `数据目录：${data.data_dir || "本机爱之屋目录"}` : "";
  $("lhBadge").className = "badge " + (data.running ? "green" : data.installed ? "amber" : "red");
  $("lhBadge").textContent = data.running ? "服务在线" : data.installed ? "未启动" : "未安装";
  $("lhInstallNote").classList.toggle("hidden", !!data.installed);
  const availability = [
    ["lhStart", !data.installed || data.running, !data.installed ? "请先安装爱之屋" : "爱之屋已经在运行了"],
    ["lhOpen", !data.installed, "请先安装爱之屋"],
    ["lhOpenFolder", !data.installed, "安装爱之屋后才能打开数据目录"],
    ["lhCreate", !data.installed || !data.running, !data.installed ? "请先安装爱之屋" : "请先启动爱之屋"],
    ["lhConnect", !data.installed || !data.running || !selected || state?.installed === false,
      !data.installed ? "请先安装爱之屋" : !data.running ? "请先启动爱之屋" : !selected ? "请先选择或新建记忆库" : "请先完成爱屋及鱼的首次设置"],
  ];
  availability.forEach(([id, disabled, reason]) => {
    $(id).disabled = !!disabled;
    $(id).title = disabled ? reason : "";
  });
  if (data.installed) $("lhOpenFolder").title = data.data_dir || "打开爱之屋数据目录";
  if (!data.running || !selected) {
    $("lhSources").innerHTML = "";
    setText("lhSourceDesc", !data.installed ? "安装爱之屋后，即可连接需要保存的 QQ 会话。" : !data.running ? "启动爱之屋后，查看这本记忆库的会话连接。" : "选择一本记忆库后，查看已经连接的会话。");
    $("lhOrganizerNotice").className = "notice";
    setText("lhOrganizerNotice", "在爱之屋的“记忆整理”里配置模型 API，聊天原文才会整理成可召回的记忆。");
  }
  const note = $("lhUpdateNote");
  if (data.update_available && latest) {
    note.style.display = "block";
    note.textContent = `当前 ${version}，最新 ${latest}。建议到 GitHub Releases 更新；升级前会保留数据快照。`;
  } else {
    note.style.display = "none";
  }
}

function renderLovehouseLibraries() {
  const list = $("lhLibraries");
  const libraries = (lovehouseState?.libraries || []).filter((item) => !item.is_demo);
  if (!libraries.length) {
    list.innerHTML = lovehouseState?.installed
      ? emptyState("暂无记忆库", "启动爱之屋后，可在下方新建记忆库。")
      : emptyState("爱之屋未安装", "从上方发布页安装爱之屋，然后刷新。");
    return;
  }
  list.innerHTML = libraries.map((item) => `
    <div class="list-item lovehouse-library-item${item.id === lovehouseSelected ? " selected" : ""}">
      <span class="lovehouse-library-icon" aria-hidden="true"><svg class="icon"><use href="#i-book"></use></svg></span>
      <div class="who">
        <b>${esc(item.name)}</b>
        <span>${esc(item.node_count ?? 0)} 条记忆</span>
        <details class="lovehouse-library-id"><summary>记忆库标识</summary><span class="mono">${esc(item.id)}</span></details>
      </div>
      ${item.id === lovehouseSelected
        ? '<span class="badge green">已选择</span>'
        : `<button class="ghost" data-lib="${esc(item.id)}">选择</button>`}
    </div>`).join("");
  list.querySelectorAll("[data-lib]").forEach((btn) => {
    btn.addEventListener("click", () => {
      lovehouseSelected = btn.dataset.lib;
      renderLoveHouse(lovehouseState);
      renderLovehouseLibraries();
      refreshLovehouseDetails();
    });
  });
}

async function refreshLovehouseDetails() {
  if (!lovehouseSelected || !lovehouseState?.running) return;
  $("lhSources").innerHTML = "";
  setText("lhSourceDesc", "正在读取所选记忆库的会话连接…");
  await Promise.all([loadLovehouseSources(), loadOrganizer()]);
  globalThis.AiwuExperience?.loadMemories().catch(() => {});
}

async function loadLovehouseSources() {
  if (!lovehouseSelected || !lovehouseState?.running) return;
  const libraryId = lovehouseSelected;
  try {
    const data = await api(`/api/lovehouse/sources?library_id=${encodeURIComponent(libraryId)}`);
    if (libraryId !== lovehouseSelected || !lovehouseState?.running) return;
    const sources = data.sources?.sources || [];
    const activeSources = sources.filter(item => item.enabled && !item.revoked);
    $("lhSourceDesc").textContent = sources.length
      ? `所选记忆库已登记 ${sources.length} 段会话，${activeSources.length} 段已启用。`
      : "暂无会话连接。选择记忆库后，在上方登记 QQ 会话。";
    $("lhSources").innerHTML = sources.length
      ? sources.map((item) => {
        const capture = item.capture && typeof item.capture === "object" ? item.capture : {};
        const saved = capture.complete_dialogues_saved ?? 0;
        const pending = capture.pending_records ?? 0;
        const activity = saved
          ? `已保存 ${saved} 轮对话`
          : (item.archive_status === "waiting" ? "等待首轮对话" : (item.archive_status || "等待首轮对话"));
        const pendingText = pending ? ` · 待交 ${pending}` : "";
        const last = item.last_received_at ? ` · 最近 ${fmtDate(item.last_received_at)}` : "";
        return `<div class="list-item">
          <div class="who">
            <b>${esc(item.name || "AstrBot 来源")}</b>
            <span class="mono">${esc(item.runtime_agent_id || "")} · ${esc(item.adapter || "")}</span>
            <span>${esc(activity)}${pendingText}${last}</span>
          </div>
          <span class="badge ${item.enabled && !item.revoked ? "green" : "gray"}">${item.enabled && !item.revoked ? "已启用" : "已停用"}</span>
        </div>`;
      }).join("")
      : "";
  } catch (err) {
    if (libraryId !== lovehouseSelected || !lovehouseState?.running) return;
    $("lhSourceDesc").textContent = err.message || String(err);
  }
}

async function loadOrganizer() {
  if (!lovehouseSelected || !lovehouseState?.running) return;
  const libraryId = lovehouseSelected;
  $("lhOrganizerNotice").className = "notice";
  setText("lhOrganizerNotice", "正在查看这本记忆库的整理模型…");
  try {
    const data = await api(`/api/lovehouse/organizer?library_id=${encodeURIComponent(libraryId)}`);
    if (libraryId !== lovehouseSelected || !lovehouseState?.running) return;
    const info = data.organizer || {};
    const notice = $("lhOrganizerNotice");
    if (info.model_ready) {
      notice.className = "notice green";
      notice.textContent = `记忆整理模型已配置：${info.model || "已就绪"}。原文会按爱之屋的节奏整理成记忆。`;
    } else {
      notice.className = "notice";
      notice.innerHTML = `还需要在爱之屋的“记忆整理”里配置模型 API（Base URL、模型名、Key）。未配置时原文会保存，但不会自动整理成可召回的经历。${info.message ? `<br><span class="small">${esc(info.message)}</span>` : ""}`;
    }
  } catch (err) {
    if (libraryId !== lovehouseSelected || !lovehouseState?.running) return;
    $("lhOrganizerNotice").textContent = err.message || String(err);
  }
}

async function connectLoveHouse() {
  if (!lovehouseSelected) { toast("先选择一个记忆库"); return; }
  const umos = $("lhUmos").value.split("\n").map((item) => item.trim()).filter(Boolean);
  if (!umos.length) { toast("请填至少一个 UMO"); return; }
  if (!confirm(`将把当前库绑定到 ${umos.length} 个会话，并安装/重载爱之屋插件。继续吗？`)) return;
  try {
    toast("正在创建来源、安装插件并重启 AstrBot…");
    const data = await api("/api/lovehouse/connect", {
      method: "POST",
      body: JSON.stringify({ library_id: lovehouseSelected, umos, name: "大肥鱼" }),
    });
    toast(`连接完成：${data.plugin?.bindings || 0} 个绑定`);
    await loadLoveHouse();
    await loadPlugins();
  } catch (err) { toast(err.message || String(err)); }
}

async function createLovehouseLibrary() {
  const name = $("lhNewName").value.trim();
  if (!name) { toast("先填记忆库名称"); return; }
  try {
    const data = await api("/api/lovehouse/create-library", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    $("lhNewName").value = "";
    lovehouseState = data;
    const createdId = data.result?.library?.id || data.result?.selected_id;
    const selected = (data.libraries || []).find(item => item.id === createdId && !item.is_demo);
    lovehouseSelected = selected?.id || data.suggested_id || lovehouseSelected;
    renderLoveHouse(data);
    renderLovehouseLibraries();
    await refreshLovehouseDetails();
    toast("记忆库已创建");
  } catch (err) { toast(err.message || String(err)); }
}

async function loadPlugins() {
  try {
    const [data, companionData] = await Promise.all([
      api("/api/plugins"), api("/api/companion").catch(() => null),
    ]);
    pluginList = data.plugins || [];
    companionStatus = companionData;
    renderPlugins();
  } catch (err) {
    $("pluginsList").innerHTML = emptyState("暂时没能打开百宝箱", err.message || "请检查本机服务，再点击刷新试试。", { error: true });
  }
}

function renderPlugins() {
  const cards = {
    astrbot_plugin_face_convert: { title: "QQ 原生表情", desc: "将 [流泪]、[doge] 等文字标签转换成 QQ 自带的小表情。", icon: "spark", theme: "blue" },
    astrbot_plugin_pc_operator: { title: "电脑操作", desc: "读写工作目录、执行命令；最高权限可查看屏幕、点击输入与操作桌面应用。", icon: "folder", theme: "blue", action: "pc", actionLabel: "操作权限" },
    astrbot_plugin_love_house: { title: "长期记忆", desc: "将已登记 QQ 会话交给爱之屋保存、整理与召回。", icon: "book", theme: "pink", action: "memory", actionLabel: "会话与记忆库" },
    astrbot_plugin_dafeiyu_companion: { title: "对话节奏与陪伴", desc: "等连续消息说完、群聊按需回应；主动问候、情绪和电脑活动概况可单独选择。", icon: "chat", theme: "pink", action: "companion", actionLabel: "陪伴设置" },
    astrbot_plugin_dafeiyu_stickers: { title: "大肥鱼表情包", desc: "按聊天语气自动搭配表情，原创贴纸与社区动图离线附带，与 QQ 小表情分开管理。", icon: "spark", theme: "pink", action: "stickers", actionLabel: "配图与素材" },
    astrbot_plugin_dafeiyu_life: { title: "QQ 生活与联网搜索", desc: "查资料、接新梗；为机器人小号准备头像、昵称、签名与空间动态，各项单独开启。", icon: "spark", theme: "blue", action: "life", actionLabel: "生活与搜索设置" },
  };
  const included = pluginList.filter(item => item.bundled || cards[item.id]);
  const extras = pluginList.filter(item => !included.includes(item));
  const needsApply = pluginList.some(item => item.pending_change);
  $("pluginsApply").classList.toggle("hidden", !needsApply);
  $("pluginsApply").disabled = pluginTogglePending;
  setText("pluginsDesc", `${pluginList.length} 个插件 · ${pluginList.filter(item => item.enabled && item.installed !== false).length} 个启用`);
  function renderCard(item) {
    const card = cards[item.id] || { title:item.name || item.id, desc:item.desc || "本机安装的扩展插件。", icon:"puzzle",theme:"blue" };
    const installed = item.installed !== false;
    const status = !installed ? "尚未部署，请先完成首次设置" : item.pending_change ? "已保存，重启对话服务后生效" : item.enabled ? card.action === "pc" ? (state?.pc_access?.enabled ? state.pc_access.desktop_enabled ? "已启用 · 完整桌面操控已授权" : "已启用 · 工作目录工具已授权" : "已启用 · 操作权限未授权") : card.action === "memory" ? "已启用 · 仅处理登记会话" : card.action === "companion" ? `已启用 · ${companionStatus?.runtime?.message || (companionStatus?.enabled ? "陪伴设置已开启" : "请先选择陪伴方式")}` : "已启用" : "已停用";
    return `<article class="plugin-capability-card compact ${esc(card.theme)}"><div class="plugin-symbol"><svg class="icon" aria-hidden="true"><use href="#i-${esc(card.icon)}"></use></svg></div><div class="plugin-info"><h3>${esc(card.title)}</h3><p>${esc(card.desc)}</p><div class="plugin-module-status">${esc(status)}</div></div><div class="plugin-controls"><button type="button" class="plugin-switch" role="switch" aria-checked="${!!item.enabled}" aria-label="${esc(card.title)}" data-plugin-toggle="${esc(item.id)}" ${!installed || pluginTogglePending || !localConnected ? "disabled" : ""}><span class="switch-track" aria-hidden="true"><span></span></span><span class="switch-label">${item.enabled ? "已启用" : "已停用"}</span></button>${card.action ? `<button class="text-button" data-plugin-action="${card.action}">${esc(card.actionLabel)} ↗</button>` : ""}</div><details class="plugin-metadata"><summary>插件信息</summary><div><span>${esc(item.name || item.id)} · ${esc(item.version || "未知版本")}</span><span>作者：${esc(item.author || "未注明")}</span><code>${esc(item.id)}</code></div></details></article>`;
  }
  $("pluginsList").innerHTML = included.length ? included.map(renderCard).join("") : emptyState("没有随包插件", "完成首次设置后会自动部署。");
  $("pluginExtraSection").classList.toggle("hidden", !extras.length);
  $("pluginExtraList").innerHTML = extras.map(renderCard).join("");
  $$("[data-plugin-action]").forEach(btn => btn.addEventListener("click", () => {
    const action = btn.dataset.pluginAction;
    if (action === "pc") openPcModal();
    else if (action === "companion") openCompanionModal();
    else if (action === "stickers") openStickerLibrary();
    else if (action === "life") globalThis.AiwuConversation?.openLife().catch(err=>toast(err.message));
    else showView("lovehouse");
  }));
  $$("[data-plugin-toggle]").forEach(btn => btn.addEventListener("click", () => togglePlugin(btn.dataset.pluginToggle)));
  ["pluginsFolder", "pluginsInstallLh", "pluginsRestart"].forEach(id => { $(id).disabled = state?.installed === false || pluginTogglePending; });
}

async function togglePlugin(id) {
  const item = pluginList.find(plugin => plugin.id === id);
  if (!item || pluginTogglePending) return;
  const focusId = id;
  let companionSetupNeeded = false;
  pluginTogglePending = true;
  renderPlugins();
  try {
    const result = await api("/api/plugins/toggle", {method:"POST",body:JSON.stringify({id,enabled:!item.enabled})});
    if (result.plugins) pluginList = result.plugins;
    else await loadPlugins();
    if (id === "astrbot_plugin_dafeiyu_companion" && !item.enabled) {
      companionStatus = await api("/api/companion").catch(() => null);
      companionSetupNeeded = !!companionStatus && !companionStatus.plugin_enabled;
    }
    toast(result.message || (result.restart_required ? "已保存，重启对话服务后生效" : "插件状态已更新"));
  } catch (err) { toast(err.message || String(err)); }
  finally {
    pluginTogglePending = false;
    renderPlugins();
    $$("[data-plugin-toggle]").find(btn => btn.dataset.pluginToggle === focusId)?.focus();
  }
  if (companionSetupNeeded) openCompanionModal();
}

function fmtFileSize(bytes) {
  const value = Number(bytes) || 0;
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  if (value >= 1024) return `${Math.round(value / 1024)} KB`;
  return `${value} B`;
}

function companionRuntimeTime(value) {
  return Number.isFinite(value) && value > 0 ? new Date(value * 1000).toLocaleString("zh-CN", {month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "—";
}

function renderCompanionRuntime(data) {
  const runtime = data?.runtime || {};
  setText("companionRuntimeMessage", runtime.message || "尚未取得运行状态，请刷新查看。");
  const zone = runtime.timezone || data?.timezone || "local";
  const time = value => companionTimeInZone(value, zone);
  setText("companionNextTrigger", time(runtime.next_trigger_at));
  setText("companionChanceWindow", runtime.next_window_start && runtime.next_window_end
    ? runtime.next_window_start === runtime.next_window_end ? time(runtime.next_window_start)
      : `${time(runtime.next_window_start)} — ${time(runtime.next_window_end)}` : "—");
  setText("companionScheduleState", `${(runtime.schedule_mode || data?.schedule_mode) === "random" ? "随机机会" : "固定机会"} · ${zone === "Asia/Shanghai" ? "北京时间" : zone === "UTC" ? "UTC" : "本机时区"}`);
  setText("companionLastSuccess", time(runtime.last_success_at));
  setText("companionDailyCount", `${Number(runtime.daily_count) || 0} / ${Number(runtime.daily_limit ?? data?.daily_limit ?? 4)}`);
  const probability = Number(runtime.effective_probability ?? data?.trigger_probability ?? 100);
  setText("companionEffectiveProbability", `${Number.isFinite(probability) ? probability : 0}%`);
  setText("companionUnanswered", `${Number(runtime.unanswered_count) || 0} 次${runtime.paused_until_reply ? " · 等待主人回应后恢复" : ""}`);
  const outcomes = {eligible:"机会已命中，尚未确认发送",probability_skipped:"概率落空，未调用模型",sent:"已提交 QQ 适配器",declined:"这次选择暂不打扰",cancelled:"条件变化，已取消",generation_failed:"生成未成功，保留本次名额",delivery_uncertain:"送达未确认，暂不重复发送",storage_error:"本机状态保存失败，未执行"};
  setText("companionLastResult", outcomes[runtime.last_result] || "尚未尝试");
  setText("companionLastOpportunity", runtime.last_opportunity_at ? `${time(runtime.last_opportunity_at)} · ${outcomes[runtime.last_opportunity_result] || "已记录机会"}` : "尚无机会记录");
  setText("companionTriggerSource", runtime.last_trigger_source === "background_idle" ? "本机后台自主机会" : "尚无记录");
  setText("companionInitiatedCount", `${Number(runtime.initiated_count) || 0} 次（适配器接受）`);
  const receipts = {adapter_accepted:"适配器已接受，不代表主人已读",delivery_uncertain:"送达未确认，已保留名额避免重发",not_sent:"本次尚未发送"};
  setText("companionLastReceipt", receipts[runtime.last_receipt] || "尚无发送回执");
  const usage=runtime.usage;
  setText("companionModelUsage", usage && usage.available !== false
    ? `主动调用报告：输入 ${Number(usage.input_tokens)||0} / 输出 ${Number(usage.output_tokens)||0} Token；${Number(usage.reported_calls)||0} 次有用量报告，${Number(usage.unreported_calls)||0} 次未报告。仅此项主动调用，不代表全部聊天或账单金额。`
    : "尚未取得有效用量报告；不估算账单金额。这里只统计主动调用。" );
  setText("companionSessionState", runtime.session_known ? "已确认主人私聊" : "等待主人在 QQ 发一条消息");
}

function companionTimeInZone(value, zone) {
  if (!Number.isFinite(value) || value <= 0) return "—";
  try {
    return new Date(value * 1000).toLocaleString("zh-CN", {month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit",
      ...(zone === "Asia/Shanghai" || zone === "UTC" ? {timeZone:zone} : {})});
  } catch (_) { return companionRuntimeTime(value); }
}

function updateCompanionScheduleFields() {
  const random = $("companion-mode").value === "random";
  $("companion-fixed-fields").classList.toggle("hidden", random);
  $("companion-random-fields").classList.toggle("hidden", !random);
  $("companion-interval").disabled = random;
  $("companion-min-interval").disabled = !random;
  $("companion-max-interval").disabled = !random;
}

function companionFormInteger(id, low, high, label, fallback) {
  const input = $(id), raw = input.value.trim(), value = raw ? Number(raw) : NaN;
  if (!Number.isInteger(value) || value < low || value > high) {
    if (fallback !== undefined) return fallback;
    input.focus();
    throw new Error(`${label}请填写 ${low}–${high} 的整数。`);
  }
  return value;
}

function readCompanionPreferences(data) {
  const mode = $("companion-mode").value;
  if (!["fixed", "random"].includes(mode)) throw new Error("请选择固定或随机机会模式。");
  const zone = $("companion-timezone").value;
  if (!["local", "Asia/Shanghai", "UTC"].includes(zone)) throw new Error("请选择有效的时区。");
  let min = companionFormInteger("companion-min-interval", 5, 1440, "随机最短间隔", mode === "fixed" ? data.min_interval_minutes ?? 60 : undefined);
  let max = companionFormInteger("companion-max-interval", 5, 2880, "随机最长间隔", mode === "fixed" ? data.max_interval_minutes ?? 180 : undefined);
  if (max < min) {
    if (mode === "random") { $("companion-max-interval").focus(); throw new Error("随机最长间隔不能小于最短间隔。"); }
    min = data.min_interval_minutes ?? 60; max = data.max_interval_minutes ?? 180;
  }
  return {
    whale_style_enabled: $("companion-whale_style_enabled").checked,
    proactive_enabled: $("companion-proactive_enabled").checked,
    natural_bubbles_enabled: $("companion-natural_bubbles_enabled").checked,
    natural_bubbles_frequency: $("companion-bubbles-frequency").value,
    emotion_enabled: $("companion-emotion_enabled").checked,
    activity_enabled: $("companion-activity_enabled").checked,
    schedule_mode: mode, min_interval_minutes: min, max_interval_minutes: max, timezone: zone,
    interval_minutes: companionFormInteger("companion-interval", 5, 1440, "固定间隔", mode === "random" ? data.interval_minutes ?? 120 : undefined),
    daily_limit: companionFormInteger("companion-limit", 0, 20, "每日生成预算"),
    quiet_start: companionFormInteger("companion-quiet-start", 0, 23, "免打扰开始小时"),
    quiet_end: companionFormInteger("companion-quiet-end", 0, 23, "免打扰结束小时"),
    idle_minutes: companionFormInteger("companion-idle", 0, 1440, "私聊安静时间"),
    trigger_probability: companionFormInteger("companion-probability", 0, 100, "基础触发概率"),
    unanswered_decay_percent: companionFormInteger("companion-decay", 0, 100, "未回应概率降低比例"),
    max_unanswered: companionFormInteger("companion-unanswered", 0, 20, "未回应暂停次数"),
    session_expiry_days: companionFormInteger("companion-expiry", 0, 365, "私聊有效天数"),
  };
}

function fillCompanionReference(data) {
  const preset = data?.presets?.dsh_reference;
  if (!preset || typeof preset !== "object") { toast("尚未读到参考值，请关闭后重试。"); return; }
  const fields = {schedule_mode:"companion-mode",min_interval_minutes:"companion-min-interval",
    max_interval_minutes:"companion-max-interval",idle_minutes:"companion-idle",daily_limit:"companion-limit",
    quiet_start:"companion-quiet-start",quiet_end:"companion-quiet-end",timezone:"companion-timezone"};
  Object.entries(fields).forEach(([key, id]) => { if (preset[key] !== undefined) $(id).value = String(preset[key]); });
  updateCompanionScheduleFields();
  setText("companionPresetNote", "已填入 Aurabot 参考值，尚未保存。能力开关、基础概率和未回应减扰保持你的选择。");
}

async function openCompanionModal() {
  try {
    const data = await api("/api/companion");
    const active = pluginList.find(item => item.id === "astrbot_plugin_dafeiyu_companion")?.enabled;
    const choice = (key, title, description) => `<label class="perm-row"><input type="checkbox" id="companion-${key}" ${data[key] ? "checked" : ""}><div><b>${title}</b><span class="small">${description}</span></div></label>`;
    openModal("大肥鱼的陪伴方式", `<div class="companion-settings-sheet">
      ${active ? "" : '<div class="notice blue">插件尚未启用。可以先保存偏好，再回到列表开启“主动陪伴与情绪”。</div>'}
      <div class="companion-profile-choices"><h4>先选一种舒服的节奏</h4><div class="exp-profiles" id="companionProfileChoices">${globalThis.AiwuExperience?.profilesMarkup(data,"data-companion-profile")||""}</div><p class="exp-parameter-note" id="companionProfileNote">档位只填入节奏参数，保存后才生效；能力开关继续由你决定。具体数字是可调整的产品预设，不是经实验验证的最佳值。</p></div>
      <details class="companion-runtime-panel" aria-label="主动聊天运行状态"><summary>查看实际运行、预算与发送记录</summary>
        <div class="row between"><h4>主动聊天状态</h4><button class="text-button" type="button" id="companionRefreshRuntime">刷新状态</button></div>
        <p id="companionRuntimeMessage" role="status"></p>
        <dl><dt>聊天连接</dt><dd id="companionSessionState"></dd><dt>机会安排</dt><dd id="companionScheduleState"></dd><dt>下一次机会预估</dt><dd id="companionNextTrigger"></dd><dt>机会区间</dt><dd id="companionChanceWindow"></dd><dt>当前有效概率</dt><dd id="companionEffectiveProbability"></dd><dt>连续未回应</dt><dd id="companionUnanswered"></dd><dt>今日生成预算</dt><dd id="companionDailyCount"></dd></dl>
        <details class="companion-runtime-details"><summary>查看自主机会与发送记录</summary><dl><dt>最近机会</dt><dd id="companionLastOpportunity"></dd><dt>触发来源</dt><dd id="companionTriggerSource"></dd><dt>最近一次结果</dt><dd id="companionLastResult"></dd><dt>上次适配器接受</dt><dd id="companionLastSuccess"></dd><dt>累计主动发起</dt><dd id="companionInitiatedCount"></dd><dt>最近发送回执</dt><dd id="companionLastReceipt"></dd><dt>主动模型用量</dt><dd id="companionModelUsage"></dd></dl></details>
      </details>
      <div class="stack companion-options">
        ${choice("whale_style_enabled", "鲸鱼娘口吻增强", "傲娇嘴硬但温柔，用自然中文接话；保留当前人格和实际工具能力，不套模板结尾。")}
        ${choice("proactive_enabled", "让她主动来找你", "她会自己寻找话题，无需另设定时任务。保存后先在 QQ 聊一句；机会到来仍须通过概率、未回应和预算判断。")}
        ${choice("natural_bubbles_enabled", "像聊天一样分几条说", "合适的长回复会拆成 2–4 条，短句、代码、链接和工具结果保持完整；你发新消息时停止旧回复的后续分段。")}
        ${choice("emotion_enabled", "情绪陪伴", "结合你们的聊天节奏与文字调整表达；这是本地模拟的情绪状态。")}
        ${choice("activity_enabled", "感知电脑活动概况", "读取前台应用名称与离开/回来状态，提供给当前聊天模型；不记录键盘内容、不持续截屏。")}
      </div>
      <div class="companion-form-grid">
        <label class="field"><span>分段发送频率</span><select id="companion-bubbles-frequency"><option value="occasional">偶尔 · 自然聊天节奏</option><option value="often">经常 · 更偏短消息</option></select></label>
      </div>
      <details class="companion-manual-settings"><summary>手动调整陪伴节奏与高级参数</summary><div class="companion-schedule-head"><h4>主动机会安排</h4><button class="soft" type="button" id="companionReferencePreset">填入 Aurabot 参考值</button></div>
      <p class="companion-reference-note" id="companionPresetNote">参考 <a href="https://github.com/aureth-labs/dsh-aurabot-qq/blob/b3301f6b014016e141c07d266a716c0fea566aab/src/core/config.js" target="_blank" rel="noopener noreferrer">Aurabot 的公开配置 ↗</a>，不是实验验证的最佳频率。填入后需点击“保存设置”才生效。</p>
      <div class="companion-form-grid">
        <label class="field companion-wide"><span>安排方式</span><select id="companion-mode"><option value="fixed">固定机会间隔</option><option value="random">随机机会区间</option></select></label>
        <div class="companion-wide" id="companion-fixed-fields"><label class="field"><span>固定机会间隔（分钟）</span><input type="number" id="companion-interval" min="5" max="1440" step="1" value="${esc(data.interval_minutes ?? 120)}"></label></div>
        <div class="companion-wide companion-random-fields" id="companion-random-fields"><label class="field"><span>最短机会间隔（分钟）</span><input type="number" id="companion-min-interval" min="5" max="1440" step="1" value="${esc(data.min_interval_minutes ?? 60)}"></label><label class="field"><span>最长机会间隔（分钟）</span><input type="number" id="companion-max-interval" min="5" max="2880" step="1" value="${esc(data.max_interval_minutes ?? 180)}"></label></div>
        <label class="field companion-wide"><span>每天主动生成预算（0–20 次）</span><input type="number" id="companion-limit" min="0" max="20" step="1" value="${esc(data.daily_limit ?? 4)}"><small>0 不调用模型；生成失败或选择沉默也占名额，概率落空不占用。</small></label>
        <label class="field"><span>免打扰开始（0–23 时）</span><input type="number" id="companion-quiet-start" min="0" max="23" value="${esc(data.quiet_start ?? 23)}"></label>
        <label class="field"><span>免打扰结束（0–23 时）</span><input type="number" id="companion-quiet-end" min="0" max="23" value="${esc(data.quiet_end ?? 8)}"></label>
        <label class="field companion-wide"><span>免打扰与每日预算时区</span><select id="companion-timezone"><option value="local">跟随这台电脑</option><option value="Asia/Shanghai">北京时间 · UTC+8</option><option value="UTC">世界时 · UTC</option></select></label>
      </div>
      <details class="companion-advanced"><summary>高级调度 · 概率与未回应减扰</summary><div class="companion-form-grid">
        <label class="field"><span>私聊至少安静（分钟）</span><input type="number" id="companion-idle" min="0" max="1440" step="1" value="${esc(data.idle_minutes ?? 40)}"><small>与机会间隔同时满足，0不额外限制。</small></label>
        <label class="field"><span>机会基础概率（%）</span><input type="number" id="companion-probability" min="0" max="100" step="1" value="${esc(data.trigger_probability ?? 100)}"><small>0 不调用模型；未命中会等下一次机会。</small></label>
        <label class="field"><span>每次未回应后概率降低（%）</span><input type="number" id="companion-decay" min="0" max="100" step="1" value="${esc(data.unanswered_decay_percent ?? 50)}"><small>0 不衰减；50表示每次再乘一半。</small></label>
        <label class="field"><span>连续未回应后暂停（次）</span><input type="number" id="companion-unanswered" min="0" max="20" step="1" value="${esc(data.max_unanswered ?? 4)}"><small>0 不限制；主人聊一句即可恢复。</small></label>
        <label class="field companion-wide"><span>无主人私聊后暂停（天）</span><input type="number" id="companion-expiry" min="0" max="365" step="1" value="${esc(data.session_expiry_days ?? 7)}"><small>0 不限制。不会通过群聊或陌生人重建联系目标。</small></label>
      </div><p class="companion-reference-note">未回复暂停参考 <a href="https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat/blob/95f459ef33208aa2c123c733ba1b5d2188dd0977/core/features/gating_feature.py" target="_blank" rel="noopener noreferrer">AstrBot 主动消息 ↗</a>；概率随互动调整参考 <a href="https://github.com/Him666233/astrbot_plugin_group_chat_plus/blob/aa97e0e35a18733e73db753238dd2a3021446e42/utils/proactive_chat_manager.py" target="_blank" rel="noopener noreferrer">chat_plus ↗</a>。百分比衰减是本适配的独立选项，不是 Aurabot 的既有规则或科学结论。</p></details>
      </details><p class="hint">${esc(data.proactive_summary || "主动聊天使用当前模型，可能产生 API 用量。先与她私聊一次，再等待主动问候。")}</p>
      <div class="hint">屏幕截图与鼠标键盘操作在“电脑操作 → 操作权限”单独授权。已启用的陪伴插件会热读取新设置。</div>
    </div>`, async () => {
      const body = readCompanionPreferences(data);
      await api("/api/companion", {method:"POST",body:JSON.stringify(body)});
      await loadPlugins();
      toast(active ? "陪伴偏好已保存" : "偏好已保存，启用插件后生效");
    });
    $("companion-mode").value = data.schedule_mode === "random" ? "random" : "fixed";
    $("companion-timezone").value = ["local", "Asia/Shanghai", "UTC"].includes(data.timezone) ? data.timezone : "local";
    $("companion-mode").addEventListener("change", updateCompanionScheduleFields);
    $("companionReferencePreset").addEventListener("click", () => fillCompanionReference(data));
    if (data.profiles) $("companionProfileChoices").querySelectorAll("[data-companion-profile]").forEach(button => button.addEventListener("click", () => {
      const preset=data.profiles[button.dataset.companionProfile];if(!preset?.values)return;
      const fields={schedule_mode:"mode",interval_minutes:"interval",min_interval_minutes:"min-interval",max_interval_minutes:"max-interval",idle_minutes:"idle",daily_limit:"limit",quiet_start:"quiet-start",quiet_end:"quiet-end",timezone:"timezone",trigger_probability:"probability",unanswered_decay_percent:"decay",max_unanswered:"unanswered",session_expiry_days:"expiry"};
      Object.entries(fields).forEach(([key,suffix])=>{if(preset.values[key]!==undefined)$("companion-"+suffix).value=String(preset.values[key]);});
      $("companionProfileChoices").querySelectorAll("button").forEach(item=>item.setAttribute("aria-pressed",String(item===button)));
      updateCompanionScheduleFields();setText("companionProfileNote",`已填入“${preset.label}”，尚未保存。${preset.description||""}`);
    }));
    updateCompanionScheduleFields();
    $("companion-bubbles-frequency").value = data.natural_bubbles_frequency === "often" ? "often" : "occasional";
    const updateBubbleFrequency = () => { $("companion-bubbles-frequency").disabled = !$("companion-natural_bubbles_enabled").checked; };
    $("companion-natural_bubbles_enabled").addEventListener("change", updateBubbleFrequency);
    updateBubbleFrequency();
    renderCompanionRuntime(data);
    $("companionRefreshRuntime").addEventListener("click", event => withBusy(event.currentTarget, "读取中…", async () => {
      companionStatus = await api("/api/companion");
      renderCompanionRuntime(companionStatus);
    }));
  } catch (err) { toast(err.message || String(err)); }
}

let stickerTransientURL = null;
let stickerContributionTimer = null;
let stickerLibraryGeneration = 0;

function clearStickerTransient() {
  stickerLibraryGeneration += 1;
  if (stickerTransientURL) URL.revokeObjectURL(stickerTransientURL);
  stickerTransientURL = null;
  if (stickerContributionTimer) clearInterval(stickerContributionTimer);
  stickerContributionTimer = null;
  if ($("stickerGithubToken")) $("stickerGithubToken").value = "";
}

async function openStickerLibrary() {
  const openingGeneration = ++stickerLibraryGeneration;
  try {
    const data = await api("/api/stickers");
    if (openingGeneration !== stickerLibraryGeneration) return;
    const prefs = data.settings || {};
    const stickerReasons={probability_skipped:"这轮没有抽中自动配图机会",scene_suppressed:"当前更适合认真用文字回应",model_declined:"模型明确选择这轮不配图",marker_missing_no_match:"没有模型选图标记，也没有可靠的本地匹配",selected_model:"模型已选中候选图片",selected_fallback:"轻松场景已选中本地候选",cooldown:"图片发送间隔还没到",asset_invalid:"素材校验未通过",packet_not_sent:"图片没有进入实际发送",adapter_accepted:"图片发送调用已完成，不代表对方已读",delivery_unknown:"送达结果未知，程序不会自动重发"};
    const stickerDiagnostic=data.runtime_diagnostics||{};
    const active = pluginList.find(item => item.id === "astrbot_plugin_dafeiyu_stickers")?.enabled;
    const bundled = (data.stickers || []).filter(item => item.pack_id !== "custom");
    let personal = (data.stickers || []).filter(item => item.pack_id === "custom");
    let editing = null, selectedFile = null, selectedShare = null, sharePreview = null;
    let shareGeneration = 0, editorGeneration = 0;
    let github = {configured:false,login:""}, contributionRunning = false, contributionPollGeneration = 0;
    const emotions = {happy:"开心",sad:"委屈",angry:"生气",shy:"害羞",sleepy:"晚安",love:"喜欢",confused:"疑惑",daily:"日常"};
    const creditHTML = (data.packs || []).map(pack => {
      const author = pack.attribution;
      const lines = typeof author === "string" ? [author] : [author?.author,author?.website,author?.bilibili,...(author?.designers || []).map(item=>`${item.name} · ${item.url}`)];
      return `<p><b>${esc(pack.name)}</b><br>${lines.filter(Boolean).map(esc).join("<br>")}<br>${esc(pack.source_url || "")}<br>${esc(typeof pack.license === "string" ? pack.license : "详见随附许可与署名文件")}</p>`;
    }).join("");
    openModal("表情匹配与素材库", `
      ${active ? "" : '<div class="notice blue">素材已随应用附带，启用“大肥鱼表情包”后会在 QQ 生效。</div>'}
      <label class="perm-row"><input type="checkbox" id="sticker-auto" ${prefs.auto_match_enabled !== false ? "checked" : ""}><div><b>按聊天气氛自动配图</b><span class="small">模型可以选择不发图；严肃求助、低落倾诉和技术任务优先好好说话，明确点图不受自动概率影响。</span></div></label>
      <div class="companion-form-grid"><label class="field"><span>使用素材</span><select id="sticker-pack"><option value="all">全部可用素材</option><option value="original">爱屋及鱼原创</option><option value="community">社区授权与已收录投稿</option><option value="custom">只用我的表情包</option></select></label><label class="field"><span>自动配图间隔（秒）</span><input type="number" id="sticker-cooldown" min="10" max="300" value="${esc(prefs.cooldown_seconds ?? 30)}"></label></div>
      <details class="sticker-advanced"><summary>高级设置 · 什么时候发图</summary><div class="companion-form-grid"><label class="field"><span>每轮自动配图机会（%）</span><input type="number" id="sticker-probability" min="0" max="100" value="${esc(prefs.auto_probability ?? 35)}"></label><label class="field"><span>聊天场景策略</span><select id="sticker-scenario"><option value="balanced">适度陪伴 · 优先匹配语境</option><option value="playful">轻松活泼 · 仍避开严肃场景</option><option value="manual">只在明确点图时发送</option></select></label></div><label class="perm-row"><input type="checkbox" id="sticker-custom-enabled" ${prefs.custom_enabled !== false ? "checked" : ""}><div><b>允许选用我的表情包</b><span class="small">还会检查每张素材自己的启用状态、适用和避用场景。100% 也不强制每轮发图。</span></div></label></details>
      <details class="sticker-advanced"><summary>最近为什么没有发图片？</summary><p class="hint">${esc(stickerDiagnostic.last_message||stickerReasons[stickerDiagnostic.last_reason]||stickerDiagnostic.note||"还没有实际配图记录。这里不会为检查而发送 QQ 消息。")}</p>${Object.keys(stickerDiagnostic.counts||{}).length?`<ul>${Object.entries(stickerDiagnostic.counts).map(([key,count])=>`<li>${esc(stickerReasons[key]||"其他已记录状态")}：${esc(count)}</li>`).join("")}</ul>`:""}</details>
      <div id="stickerExperienceMount"></div><details class="sticker-advanced"><summary>随附素材原始预览</summary><p class="hint">随附 ${bundled.length} 张素材。你可以自然地说想看哪种表情，也能接着她刚提出的邀请说“可以呀”。</p>
      <div class="sticker-library-grid" tabindex="0" aria-label="随附表情素材列表，可滚动浏览">${bundled.map(item=>`<figure><img src="${esc(item.url || item.path)}" alt="${esc(item.label || item.id)}" loading="lazy"><figcaption>${esc(item.label || item.id)}<small>${item.pack_id === "original" ? "原创贴纸" : item.pack_id === "contributed" ? "已收录用户投稿" : "社区授权动图"}</small>${item.pack_id === "contributed" ? `<small>作者：${esc(typeof item.attribution === "string" ? item.attribution : "见素材许可")}</small>` : ""}</figcaption></figure>`).join("")}</div>
      </details><details class="sticker-advanced" id="stickerPersonalDetails"><summary>高级设置 · 我的表情包与社区投稿</summary>
        <p class="hint">导入、编辑和停用会立即保存到这台电脑；自动配图选项需点底部保存。支持 PNG、JPEG、WebP、GIF，每张不超过 8 MiB。图片的使用场景写得越清楚，她越容易选对。</p>
        <div class="row between wrap"><b id="stickerPersonalCount"></b><button class="soft" type="button" id="stickerAddCustom">导入自己的表情包</button></div>
        <p class="hint" id="stickerGlobalContributionStatus" role="status"></p><div class="custom-sticker-grid" id="stickerPersonalList"></div>
        <section class="sticker-edit-panel hidden" id="stickerEditor" aria-label="编辑个人表情包">
          <h4 id="stickerEditorTitle">导入表情包</h4><label class="field"><span>本地图片</span><input type="file" id="stickerCustomFile" accept="image/png,image/jpeg,image/webp,image/gif"></label>
          <img class="custom-sticker-preview hidden" id="stickerCustomPreview" alt="待导入的个人表情包">
          <div class="companion-form-grid"><label class="field"><span>表情名称</span><input id="stickerCustomTitle" type="text" maxlength="60" placeholder="例如 今天也很元气"></label><label class="field"><span>主要心情</span><select id="stickerCustomEmotion">${Object.entries(emotions).map(([value,label])=>`<option value="${value}">${label}</option>`).join("")}</select></label></div>
          <label class="field"><span>关键词（用逗号分隔，最多 12 个）</span><input id="stickerCustomTags" type="text" placeholder="例如 夸奖、开心、好耶"></label>
          <label class="field"><span>画面说明</span><input id="stickerCustomDescription" type="text" maxlength="240" placeholder="描述图片表达的意思"></label>
          <div class="companion-form-grid"><label class="field"><span>适合什么时候发</span><input id="stickerCustomUse" type="text" maxlength="160" placeholder="例如 被夸奖、分享好消息"></label><label class="field"><span>什么时候不要发</span><input id="stickerCustomAvoid" type="text" maxlength="160" placeholder="例如 对方难过、认真求助"></label></div>
          <label class="field"><span>作者署名</span><input id="stickerCustomAttribution" type="text" maxlength="100" placeholder="个人使用可留空，公开投稿时必须填写"></label>
          <div class="row wrap"><button class="soft" type="button" id="stickerSaveCustom">保存到我的图库</button><button class="text-button" type="button" id="stickerCancelCustom">收起编辑</button></div>
        </section>
        <section class="sticker-share-panel hidden" id="stickerSharePanel" aria-label="预览社区表情投稿">
          <h4>把这张表情分享给大家</h4><p id="stickerShareSelection"></p><label class="field"><span>公开作者署名</span><input type="text" id="stickerShareAttribution" maxlength="100"></label>
          <button class="soft" type="button" id="stickerPrepareShare">预览投稿内容</button><div id="stickerSharePreview" class="sticker-share-preview"></div>
          <label class="perm-row"><input type="checkbox" id="stickerShareRights"><div><b>确认公开投稿权与许可</b><span class="small">我拥有这张图的公开分发权，同意按 CC BY-NC 4.0 分享图片和这里展示的说明、署名。投稿进入公开 GitHub PR，审核收录后随新版分发。</span></div></label>
          <p class="hint"><a href="https://creativecommons.org/licenses/by-nc/4.0/" target="_blank" rel="noopener noreferrer">查看 CC BY-NC 4.0 许可 ↗</a></p>
          <details class="sticker-github-auth"><summary>GitHub 账号（可选，连接后可一键投稿）</summary><p id="stickerGithubStatus" class="hint">尚未连接自己的 GitHub 账号。</p><label class="field"><span>你自己的 GitHub 访问令牌</span><input type="password" id="stickerGithubToken" autocomplete="off" placeholder="不会随软件附带，也不会写入投稿文件"></label><label class="small"><input type="checkbox" id="stickerGithubRemember"> 在此电脑加密记住授权</label><div class="row wrap"><button class="soft" type="button" id="stickerGithubConnect">连接 GitHub</button><button class="text-button" type="button" id="stickerGithubDisconnect">断开授权</button></div><p class="hint">外部贡献者可使用仅公开仓库的 public_repo 授权。未授权也能导出投稿包，自行上传。<a href="https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens" target="_blank" rel="noopener noreferrer">GitHub 授权说明 ↗</a></p></details>
          <div class="row wrap"><button class="soft" type="button" id="stickerSubmitShare" disabled>一键投稿到 GitHub</button><button class="ghost" type="button" id="stickerExportShare" disabled>导出投稿包</button></div><p class="hint" id="stickerShareStatus" role="status"></p><a id="stickerShareResult" class="hidden" target="_blank" rel="noopener noreferrer">查看已提交的 PR ↗</a>
        </section>
      </details>
      <details class="hint sticker-credits"><summary>随附素材来源与许可</summary>${creditHTML}</details>`, async()=>{
        await api("/api/stickers/settings",{method:"POST",body:JSON.stringify({auto_match_enabled:$("sticker-auto").checked,
          cooldown_seconds:Number($("sticker-cooldown").value),pack_selection:$("sticker-pack").value,
          auto_probability:Number($("sticker-probability").value),scenario_policy:$("sticker-scenario").value,
          custom_enabled:$("sticker-custom-enabled").checked})});
        await loadPlugins();toast(active ? "表情偏好已保存" : "偏好已保存，启用插件后生效");
      });
    $("sticker-pack").value = prefs.pack_selection || "all";
    $("sticker-scenario").value = prefs.scenario_policy || "balanced";
    const instanceGeneration = stickerLibraryGeneration;
    const isCurrent = () => instanceGeneration === stickerLibraryGeneration && !!$("stickerPersonalList");
    function shareButtons() {
      if (!isCurrent()) return;
      const ready = !!sharePreview && $("stickerShareRights").checked && !contributionRunning;
      $("stickerSubmitShare").disabled = !ready || !github.configured;
      $("stickerExportShare").disabled = !ready;
      $("stickerGithubConnect").disabled = contributionRunning;
      $("stickerGithubDisconnect").disabled = contributionRunning || !github.configured;
      $("stickerShareAttribution").disabled = contributionRunning;
      $("stickerPrepareShare").disabled = contributionRunning;
      $("stickerShareRights").disabled = contributionRunning;
      $("stickerPersonalList").querySelectorAll("[data-sticker-share]").forEach(button=>{button.disabled=contributionRunning;});
      setText("stickerGithubStatus", github.configured ? `已连接 ${github.login}${github.remembered ? " · 本机加密保存" : " · 仅本次打开有效"}` : "尚未连接。可以先导出投稿包，或连接自己的账号后提交 PR。");
    }
    function invalidateShare() { shareGeneration+=1;sharePreview=null;if(!isCurrent())return;$("stickerShareRights").checked=false;$("stickerSharePreview").replaceChildren();$("stickerShareResult").classList.add("hidden");$("stickerShareResult").removeAttribute("href");shareButtons(); }
    function resetPreview() { if(stickerTransientURL) URL.revokeObjectURL(stickerTransientURL);stickerTransientURL=null;$("stickerCustomPreview").removeAttribute("src");$("stickerCustomPreview").classList.add("hidden"); }
    function editItem(item=null) {
      editorGeneration += 1;
      editing=item;selectedFile=null;resetPreview();$("stickerCustomFile").value="";
      $("stickerCustomFile").disabled=!!item;$("stickerEditor").classList.remove("hidden");
      setText("stickerEditorTitle",item ? "编辑这张表情" : "导入自己的表情包");
      const fields={Title:item?.title || "",Emotion:item?.emotion || "daily",Tags:(item?.tags || []).join("，"),Description:item?.description || "",Use:item?.use_when || "",Avoid:item?.avoid_when || "",Attribution:item?.attribution || ""};
      Object.entries(fields).forEach(([key,value])=>{$("stickerCustom"+key).value=value;});
      $("stickerCustomPreview").classList.toggle("hidden",!item);
      if(item) $("stickerCustomPreview").src=item.url;
      $("stickerEditor").scrollIntoView({block:"nearest",behavior:"smooth"});
    }
    async function refreshPersonal() {
      const latest=await api("/api/stickers");if(!isCurrent())return;personal=(latest.stickers || []).filter(item=>item.pack_id==="custom");renderPersonal();
    }
    function renderPersonal() {
      if(!isCurrent())return;
      setText("stickerPersonalCount",`我的图库 · ${personal.length} 张`);
      $("stickerPersonalList").innerHTML=personal.length ? personal.map(item=>`<article class="custom-sticker-card"><img src="${esc(item.url)}" alt="${esc(item.title)}" loading="lazy"><b>${esc(item.title)}</b><small>${esc(emotions[item.emotion] || item.emotion)} · ${item.enabled ? "已启用" : "已停用"}</small><div class="row wrap"><button type="button" class="text-button" data-sticker-edit="${esc(item.id)}">编辑</button><button type="button" class="text-button" data-sticker-toggle="${esc(item.id)}">${item.enabled ? "停用" : "启用"}</button><button type="button" class="text-button" data-sticker-share="${esc(item.id)}">投稿</button><button type="button" class="text-button" data-sticker-delete="${esc(item.id)}">移除</button></div></article>`).join("") : '<p class="hint">还没有自己的表情包，导入第一张试试吧。</p>';
      $("stickerPersonalList").querySelectorAll("[data-sticker-edit]").forEach(button=>button.addEventListener("click",()=>editItem(personal.find(item=>item.id===button.dataset.stickerEdit))));
      $("stickerPersonalList").querySelectorAll("[data-sticker-toggle]").forEach(button=>button.addEventListener("click",()=>withBusy(button,"保存中…",async()=>{
        const item=personal.find(item=>item.id===button.dataset.stickerToggle);await api("/api/stickers/custom/update",{method:"POST",body:JSON.stringify({id:item.id,changes:{enabled:!item.enabled}})});await refreshPersonal();
      })));
      $("stickerPersonalList").querySelectorAll("[data-sticker-delete]").forEach(button=>button.addEventListener("click",()=>withBusy(button,"移除中…",async()=>{
        const item=personal.find(item=>item.id===button.dataset.stickerDelete);
        if(!confirm(`从这台电脑的个人图库移除“${item.title}”？已提交的 GitHub 投稿不会因此撤回。`)) return;
        await api("/api/stickers/custom/delete",{method:"POST",body:JSON.stringify({id:item.id})});
        if(!isCurrent())return;
        if(selectedShare?.id===item.id){selectedShare=null;invalidateShare();$("stickerSharePanel").classList.add("hidden");}
        await refreshPersonal();
      })));
      $("stickerPersonalList").querySelectorAll("[data-sticker-share]").forEach(button=>button.addEventListener("click",async()=>{
        selectedShare=personal.find(item=>item.id===button.dataset.stickerShare);invalidateShare();
        $("stickerSharePanel").classList.remove("hidden");setText("stickerShareSelection",`选中素材：${selectedShare.title}`);
        $("stickerShareAttribution").value=selectedShare.attribution || github.login || "";
        $("stickerSharePanel").scrollIntoView({block:"nearest",behavior:"smooth"});
      }));
    }
    $("stickerAddCustom").addEventListener("click",()=>editItem());
    $("stickerCancelCustom").addEventListener("click",()=>{editorGeneration+=1;$("stickerEditor").classList.add("hidden");resetPreview();selectedFile=null;});
    ["Title","Emotion","Tags","Description","Use","Avoid","Attribution"].forEach(name=>$("stickerCustom"+name).addEventListener("input",()=>{editorGeneration+=1;}));
    $("stickerCustomFile").addEventListener("change",()=>{
      editorGeneration+=1;
      resetPreview();selectedFile=$("stickerCustomFile").files?.[0] || null;
      if(!selectedFile) return;
      if(selectedFile.size>8*1024*1024 || (selectedFile.type && !["image/png","image/jpeg","image/webp","image/gif"].includes(selectedFile.type))){selectedFile=null;$("stickerCustomFile").value="";toast("请选择不超过 8 MiB 的 PNG、JPEG、WebP 或 GIF。");return;}
      stickerTransientURL=URL.createObjectURL(selectedFile);$("stickerCustomPreview").src=stickerTransientURL;$("stickerCustomPreview").classList.remove("hidden");
    });
    $("stickerSaveCustom").addEventListener("click",event=>withBusy(event.currentTarget,"保存中…",async()=>{
      const savedGeneration=editorGeneration, savedItem=editing, savedFile=selectedFile;
      const changes={title:$("stickerCustomTitle").value.trim(),emotion:$("stickerCustomEmotion").value,
        tags:$("stickerCustomTags").value.split(/[,，、]/).map(s=>s.trim()).filter(Boolean),description:$("stickerCustomDescription").value.trim(),
        use_when:$("stickerCustomUse").value.trim(),avoid_when:$("stickerCustomAvoid").value.trim(),attribution:$("stickerCustomAttribution").value.trim()};
      if(!changes.title) throw new Error("先给这张表情起个名字。");
      if(savedItem) await api("/api/stickers/custom/update",{method:"POST",body:JSON.stringify({id:savedItem.id,changes})});
      else {
        if(!savedFile) throw new Error("请先选择本地图片。");
        const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.onerror=()=>reject(new Error("图片读取失败。"));reader.readAsDataURL(savedFile);});
        await api("/api/stickers/custom/import",{method:"POST",body:JSON.stringify({...changes,enabled:true,image_base64:encoded})});
      }
      await refreshPersonal();if(!isCurrent())return;if(editorGeneration===savedGeneration){$("stickerEditor").classList.add("hidden");resetPreview();selectedFile=null;}invalidateShare();toast("已保存到个人图库，素材选择会立即读取新内容。");
    }));
    $("stickerShareAttribution").addEventListener("input",invalidateShare);
    $("stickerShareRights").addEventListener("change",shareButtons);
    $("stickerPrepareShare").addEventListener("click",event=>withBusy(event.currentTarget,"准备预览…",async()=>{
      if(!selectedShare) throw new Error("请先选择一张个人表情包。");
      const chosen=selectedShare, attribution=$("stickerShareAttribution").value.trim();invalidateShare();const requestGeneration=shareGeneration;
      const prepared=await api("/api/stickers/contributions/preview",{method:"POST",body:JSON.stringify({item_id:chosen.id,attribution})});
      if(!isCurrent() || requestGeneration!==shareGeneration || selectedShare?.id!==chosen.id || $("stickerShareAttribution").value.trim()!==attribution)return;
      sharePreview=prepared;
      const meta=sharePreview.metadata;
      $("stickerSharePreview").innerHTML=`<img src="${esc(selectedShare.url)}" alt="将公开投稿的图片"><div><b>${esc(meta.title)}</b><p>目标：myflww/aiwu-jiyu 的公开 PR</p><p>作者：${esc(meta.attribution)} · ${esc(sharePreview.license)}</p><p>心情：${esc(emotions[meta.emotion] || meta.emotion)} · 标签：${esc((meta.tags || []).join("、") || "未填写")}</p><p>说明：${esc(meta.description || "未填写")}</p><p>适用：${esc(meta.use_when || "未填写")}<br>避用：${esc(meta.avoid_when || "未填写")}</p><p>${sharePreview.files.length} 个文件 · ${fmtFileSize(sharePreview.size)}；不会包含本机路径、账号配置或聊天记录。</p><details><summary>查看将公开的文件</summary><ul>${sharePreview.files.map(path=>`<li><code>${esc(path)}</code></li>`).join("")}</ul></details></div>`;
      $("stickerShareRights").checked=false;shareButtons();
    }));
    $("stickerGithubConnect").addEventListener("click",event=>withBusy(event.currentTarget,"连接中…",async()=>{
      const input=$("stickerGithubToken");const token=input.value.trim();input.value="";
      github=await api("/api/stickers/contributions/connect",{method:"POST",body:JSON.stringify({token,remember:$("stickerGithubRemember").checked})});shareButtons();toast("已连接你的 GitHub 账号。");
    }).finally(shareButtons));
    $("stickerGithubDisconnect").addEventListener("click",event=>withBusy(event.currentTarget,"断开中…",async()=>{github=await api("/api/stickers/contributions/disconnect",{method:"POST",body:"{}"});shareButtons();}).finally(shareButtons));
    const shareBody=()=>({item_id:selectedShare?.id,attribution:$("stickerShareAttribution").value.trim(),fingerprint:sharePreview?.fingerprint,rights_confirmed:$("stickerShareRights").checked,license:"CC-BY-NC-4.0"});
    async function pollContribution() {
      if(!isCurrent()) return;
      const requestGeneration=++contributionPollGeneration;
      const result=await api("/api/stickers/contributions/status");if(!isCurrent() || requestGeneration!==contributionPollGeneration)return;contributionRunning=result.running===true;setText("stickerShareStatus",result.message);setText("stickerGlobalContributionStatus",result.state==='idle'?'':result.message);shareButtons();
      if(sharePreview?.fingerprint===result.fingerprint && result.pr_url && /^https:\/\/github\.com\/myflww\/aiwu-jiyu\/pull\/\d+$/.test(result.pr_url)){$("stickerShareResult").href=result.pr_url;$("stickerShareResult").classList.remove("hidden");}
      if(!contributionRunning && stickerContributionTimer){clearInterval(stickerContributionTimer);stickerContributionTimer=null;}
    }
    $("stickerSubmitShare").addEventListener("click",event=>withBusy(event.currentTarget,"提交中…",async()=>{
      if(!sharePreview || !$("stickerShareRights").checked || !github.configured) throw new Error("先预览并确认公开许可，再连接 GitHub 投稿。");
      const result=await api("/api/stickers/contributions/submit",{method:"POST",body:JSON.stringify(shareBody())});
      if(!isCurrent())return;
      contributionPollGeneration+=1;
      contributionRunning=result.running===true;setText("stickerShareStatus",result.message);shareButtons();
      if(stickerContributionTimer) clearInterval(stickerContributionTimer);
      stickerContributionTimer=setInterval(()=>pollContribution().catch(err=>{if(isCurrent())setText("stickerShareStatus",err.message);}),2000);await pollContribution();
    }).finally(shareButtons));
    $("stickerExportShare").addEventListener("click",event=>withBusy(event.currentTarget,"打包中…",async()=>{
      if(!sharePreview || !$("stickerShareRights").checked) throw new Error("先预览并确认公开投稿许可。");
      const exporting=sharePreview,requestGeneration=shareGeneration;
      const response=await fetch("/api/stickers/contributions/export",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(shareBody())});
      if(!response.ok){const error=await response.json();throw new Error(error.message || "导出未完成。");}
      const content=await response.blob();if(!isCurrent() || requestGeneration!==shareGeneration || !$("stickerShareRights").checked)return;
      const url=URL.createObjectURL(content),link=document.createElement("a");link.href=url;link.download=`AiwuJiyu-sticker-${exporting.fingerprint.slice(0,12)}.zip`;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
      setText("stickerShareStatus","投稿包已生成。可在 GitHub 的新建 Issue 或 PR 中上传，由维护者审核。");
    }).finally(shareButtons));
    renderPersonal();shareButtons();
    globalThis.AiwuStickerExperience?.mount({api,esc,toast,getModalGeneration:()=>modalGeneration,onCatalogChange:async()=>{await refreshPersonal();if(isCurrent())invalidateShare();}});
    api("/api/stickers/contributions/auth").then(value=>{if(isCurrent()){github=value;shareButtons();}}).catch(()=>{});
    pollContribution().then(()=>{if(isCurrent() && contributionRunning && !stickerContributionTimer)stickerContributionTimer=setInterval(()=>pollContribution().catch(err=>{if(isCurrent())setText("stickerShareStatus",err.message);}),2000);}).catch(()=>{});
  } catch(err){toast(err.message || String(err));}
}

async function loadArtifacts() {
  const list = $("artifactList");
  if (!list) return;
  list.setAttribute("aria-busy", "true");
  if (!list.innerHTML.trim()) list.innerHTML = emptyState("正在打开作品箱", "看看你们最近又完成了什么。");
  try {
    const data = await api("/api/artifacts");
    setText("artifactWorkspace", data.workspace || "还没有设置工作目录");
    setText("artifactDesc", data.workspace ? "文件保存在你选择的本机工作目录里。" : "先在设置里，给作品选一个存放的位置。");
    $("artifactFolder").disabled = !data.workspace;
    $("artifactFolder").title = data.workspace ? "打开本机工作目录" : "请先设置作品目录";
    const pcEnabled = !!state?.pc_access?.enabled;
    setText("artifactPermissionBadge", pcEnabled ? "电脑权限已开启" : "电脑权限未开启");
    $("artifactPermissionBadge").className = "badge " + (pcEnabled ? "green" : "gray");
    setText("artifactPermissionText", pcEnabled ? "可以去 QQ，请她在工作目录里处理文件。" : "先聊天也很好。需要一起动手时，再由你开启电脑权限。");
    setText("artifactSetup", state?.installed === false ? "先完成首次设置 ↗" : pcEnabled ? "管理电脑权限 ↗" : "设置电脑权限 ↗");
    const items = data.items || [];
    setText("artifactCount", String(items.length));
    if (!items.length) {
      list.classList.add("is-empty");
      list.innerHTML = emptyState("第一件小作品，等你们一起完成", "开启电脑权限后，去 QQ 请她在工作目录里生成文件。完成的作品会出现在这里。", { art: true });
      return;
    }
    list.classList.remove("is-empty");
    list.innerHTML = items.map(item => `<article class="artifact-file-card"><div class="file-card-heading"><div class="file-glyph"><svg class="icon" aria-hidden="true"><use href="#i-folder"></use></svg></div><span class="badge gray">${esc(item.kind || "文件")}</span></div><h3 title="${esc(item.name)}">${esc(item.name)}</h3><div class="file-meta"><span>${fmtFileSize(item.size)}</span><span>${item.modified ? esc(fmtDate(new Date(item.modified * 1000))) : "时间未提供"}</span></div><details class="file-path"><summary>查看文件位置</summary><code>${esc(item.path)}</code></details><div class="file-actions"><button class="soft" data-open="${esc(item.path)}">打开作品 <span aria-hidden="true">↗</span></button><button class="text-button" data-reveal="${esc(item.path)}">在文件夹中显示</button></div></article>`).join("");
    list.querySelectorAll("[data-open]").forEach(btn => btn.addEventListener("click", () => api("/api/artifacts/open", { method: "POST", body: JSON.stringify({ path: btn.dataset.open }) }).catch(err => toast(err.message || String(err)))));
    list.querySelectorAll("[data-reveal]").forEach(btn => btn.addEventListener("click", () => api("/api/artifacts/reveal", { method: "POST", body: JSON.stringify({ path: btn.dataset.reveal }) }).catch(err => toast(err.message || String(err)))));
  } catch (err) {
    list.classList.add("is-empty");
    list.innerHTML = emptyState("作品箱暂时没打开", err.message || "请检查工作目录，或点击刷新重试。", { error: true });
  } finally { list.removeAttribute("aria-busy"); }
}

function openPcModal() {
  const pc = state?.pc_access || {};
  const members = Object.entries(state?.members || {});
  const memberText = members.length
    ? members.map(([qq, perm]) => {
        const parts = [];
        if (perm.read) parts.push("读取");
        if (perm.write) parts.push("写文件");
        if (perm.run) parts.push("命令");
        if (perm.desktop) parts.push("桌面操控");
        return `${qq}（${parts.join(" + ") || "仅聊天"}）`;
      }).join("、")
    : "还没有授权管理员";
  const audit = (pc.audit || []).map((item) => `
    <div class="list-item">
      <div class="who">
        <b>${esc(item.action || "run")}</b>
        <span class="mono">${esc(item.command || "")}</span>
        <span>${esc(item.result || "")}</span>
      </div>
    </div>`).join("");
  openModal("电脑权限", `
    <div class="notice" style="margin-bottom:12px">
      文件和命令工具用于工作目录。开启“完整桌面操控”后，她还能查看屏幕与控件、点击、输入、滚动和打开应用。
      主人拥有已开启的能力，其他人的权限在“聊天权限”里逐项分配。桌面操作以当前 Windows 用户权限执行，始终只允许私聊。
    </div>
    <div class="stack">
      <label class="row" style="gap:8px">
        <input type="checkbox" id="pcEnabled" style="width:auto" ${pc.enabled ? "checked" : ""}>
        <span>允许大肥鱼使用这台电脑</span>
      </label>
      <label class="perm-row"><input type="checkbox" id="pcDesktop" ${pc.desktop_enabled ? "checked" : ""}><div><b>完整桌面操控（最高权限）</b><span class="small">允许按需查看屏幕、操作鼠标键盘和应用；不会自动持续截屏</span></div></label>
      <label class="field"><span>工作目录</span>
        <div class="input-row">
          <input type="text" id="pcWorkspace" value="${esc(pc.workspace || "")}">
          <button class="ghost" id="pcBrowse">浏览</button>
        </div>
      </label>
      <label class="field"><span>命令超时（秒）</span>
        <input type="number" id="pcTimeout" min="5" max="300" value="${esc(pc.timeout_s || 60)}">
      </label>
      <label class="row" style="gap:8px">
        <input type="checkbox" id="pcPrivate" style="width:auto" ${pc.private_only !== false ? "checked" : ""}>
        <span class="small">只允许私聊，群聊拒绝（主人和管理员都一样）</span>
      </label>
      <label class="row" style="gap:8px">
        <input type="checkbox" id="pcConfirm" style="width:auto">
        <span class="small">我理解风险：这相当于让 QQ 上的大肥鱼操作这台电脑</span>
      </label>
    </div>
    <div class="hint" style="margin-top:12px">已授权：${esc(memberText)}</div>
    <div class="hint" style="margin-top:12px">最近操作：</div>
    <div class="list">${audit || '<div class="hint">还没有执行记录。</div>'}</div>
  `, async () => {
    const enabled = $("pcEnabled").checked;
    if (enabled && !$("pcConfirm").checked) {
      throw new Error("请勾选风险确认");
    }
    const data = await api("/api/pc-access", {
      method: "POST",
      body: JSON.stringify({
        enabled,
        desktop_enabled: enabled && $("pcDesktop").checked,
        workspace: $("pcWorkspace").value.trim(),
        timeout_s: Number($("pcTimeout").value) || 60,
        private_only: $("pcPrivate").checked,
        confirm: true,
      }),
    });
    state.pc_access = data;
    renderOverview();
    renderSettings();
    if (!$("view-plugins").classList.contains("hidden")) renderPlugins();
    toast(enabled ? "电脑权限已开启" : "电脑权限已关闭");
  });
  const browse = $("pcBrowse");
  if (browse) browse.addEventListener("click", () => chooseFolder("pcWorkspace"));
  const desktopToggle = $("pcDesktop"), privateToggle = $("pcPrivate");
  const syncDesktopChoice = () => {
    if (desktopToggle.checked) { $("pcEnabled").checked = true; privateToggle.checked = true; }
    privateToggle.disabled = desktopToggle.checked;
  };
  desktopToggle.addEventListener("change", syncDesktopChoice);
  $("pcEnabled").addEventListener("change", () => {
    if (!$("pcEnabled").checked) desktopToggle.checked = false;
    syncDesktopChoice();
  });
  syncDesktopChoice();
}

async function saveWorkspace() {
  try {
    const pc = state?.pc_access || {};
    const data = await api("/api/pc-access", {
      method: "POST",
      body: JSON.stringify({
        enabled: !!pc.enabled,
        workspace: $("setWorkspace").value.trim(),
        timeout_s: pc.timeout_s || 60,
        private_only: pc.private_only !== false,
        confirm: true,
      }),
    });
    state.pc_access = data;
    renderSettings();
    toast("工作目录已保存");
  } catch (err) {
    toast(err.message || String(err));
  }
}

async function saveSettings() {
  try {
    const body = {
      model: {
        model: $("setModel").value,
        api_base: $("setApiBase").value.trim() || "https://api.deepseek.com/v1",
      },
      owner_qq: $("setOwner").value.trim(),
    };
    const key = $("setKey").value.trim();
    if (key) body.api_key = key;
    toast("正在保存并重启 AstrBot…");
    await api("/api/config", { method: "POST", body: JSON.stringify(body) });
    $("setKey").value = "";
    await refreshState(false);
    renderSettings();
    toast("已保存");
  } catch (err) { toast(err.message || String(err)); }
}

function renderDiagnosticsStatus() {
  const a = !!state?.status?.astrbot?.up, n = !!state?.status?.napcat?.up;
  setText("logAstrbotState", a ? "正在运行" : "已停止");
  setText("logNapcatState", n ? "正在运行" : "已停止");
  $("logAstrbotDot").classList.toggle("asleep", !a);
  $("logNapcatDot").classList.toggle("asleep", !n);
  setText("logContext", `${currentLog === "astrbot" ? "对话服务" : "QQ 连接"} · ${(currentLog === "astrbot" ? a : n) ? "正在运行" : "服务已停止"}`);
}

async function loadLog() {
  renderDiagnosticsStatus();
  if (Date.now() - logRetentionFetchedAt > 60000) loadLogRetention();
  try {
    const response = await fetch(`/api/logs?name=${currentLog}&t=${Date.now()}`);
    if (!response.ok) throw new Error(`暂时读不到运行记录（${response.status}）`);
    const text = await response.text();
    const el = $("logView");
    const follow = $("logFollow").checked;
    const display = text || "这里暂时还没有运行记录。\n\n启动服务后，新的记录会自动出现在这里。";
    if (el.textContent !== display) el.textContent = display;
    if (follow) el.scrollTop = el.scrollHeight;
    setText("logLineCount", `${text ? text.split(/\r?\n/).filter(Boolean).length : 0} 行记录`);
    setText("logUpdatedAt", `更新于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`);
    $("logCopy").disabled = !text;
    $("logView").dataset.hasLogs = text ? "true" : "false";
  } catch (err) {
    setText("logUpdatedAt", err.message || "暂时无法读取，点击刷新重试");
  }
}

async function loadLogRetention() {
  logRetentionFetchedAt = Date.now();
  try {
    const data = await api("/api/logs/retention");
    const mb = value => Math.round(Number(value) / 1024 / 1024);
    setText("logRetentionRule", `启动时及每 ${Math.round(data.check_interval_seconds / 60)} 分钟检查；日志空闲后，单文件超过 ${mb(data.max_file_bytes)} MB 保留最新 ${mb(data.keep_bytes)} MB，连续 ${data.retention_days} 天未写入则清空。`);
    const errors = data.errors || [], deferred = data.deferred || [];
    const legacy = deferred.some(item => item.reason === "legacy_writer");
    $("logRetentionBadge").className = "badge " + (errors.length || legacy ? "amber" : "green");
    setText("logRetentionBadge", data.preview ? "规则预览" : errors.length ? "稍后重试" : legacy ? "等待旧服务重启" : "自动维护中");
    const checked = data.checked_at ? `最近检查：${fmtDate(new Date(data.checked_at * 1000))}。` : "启动后自动检查。";
    const result = data.reclaimed_bytes ? `本次释放 ${fmtFileSize(data.reclaimed_bytes)}。` : "";
    const note = errors.length ? "部分日志暂时无法整理，下次会自动重试。" : legacy ? "旧版服务仍在写入的日志会保留，服务下次启动后纳入整理。" : deferred.length ? "正在写入的日志会在空闲后整理。" : "仅整理控制台诊断日志，聊天记录和记忆库独立保存。";
    setText("logRetentionStatus", data.preview ? "预览模式只展示规则，不清理本机记录。" : checked + result + note);
  } catch (_) {
    setText("logRetentionBadge", "暂未读取");
    $("logRetentionBadge").className = "badge amber";
    setText("logRetentionStatus", "暂时无法读取维护状态，稍后会自动重试。");
  }
}

async function copyLogs() {
  const text = $("logView").textContent;
  if (!text || $("logView").dataset.hasLogs !== "true") { toast("还没有可以复制的运行记录。"); return; }
  try { await navigator.clipboard.writeText(text); toast("运行记录已复制。分享前，记得检查私人信息。"); }
  catch (err) {
    openModal("复制运行记录", `<p>可以选中下面的记录，再按 Ctrl+C 复制。</p><textarea id="logCopyText" rows="10" readonly>${esc(text)}</textarea>`);
    $("modalOk").textContent = "完成";
    $("logCopyText").focus(); $("logCopyText").select();
  }
}

function openModal(title, body, onOk) {
  const generation = ++modalGeneration;
  clearStickerTransient();
  if (!$("modalBack").classList.contains("show")) modalReturnFocus = document.activeElement;
  $("modalTitle").textContent = title;
  $("modalBody").innerHTML = body;
  $("modalBack").classList.add("show");
  $("modalOk").classList.remove("hidden");
  $("modalCancel").classList.remove("hidden");
  $("modalCancel").textContent = "取消";
  $("modalOk").textContent = "保存设置";
  $("modalOk").disabled = false;
  $("modalOk").classList.remove("danger");
  delete $("modalOk").dataset.busy;
  $("modalOk").onclick = async () => {
    const button=$("modalOk");
    if(generation!==modalGeneration || button.dataset.busy==="true")return;
    const label=button.textContent;button.dataset.busy="true";button.disabled=true;button.textContent="正在处理…";
    try { if(onOk)await onOk();if(generation===modalGeneration)closeModal(); }
    catch(error) { if(generation===modalGeneration)toast(error.message||String(error)); }
    finally { if(generation===modalGeneration){delete button.dataset.busy;button.disabled=false;button.textContent=label;} }
  };
  const first = $("modalBody").querySelector("input,select,textarea,button");
  (first || $("modalCancel")).focus();
}

function closeModal() {
  modalGeneration += 1;
  clearStickerTransient();
  if ($("modalQr")) {
    clearInterval(qrModalWatch);
    clearInterval(qrTimer);
    clearInterval(qrRefreshTimer);
    qrModalWatch = qrTimer = qrRefreshTimer = null;
    qrFlowGeneration++;
  }
  $("modalBack").classList.remove("show");
  $("modalBody").innerHTML = "";
  if (modalReturnFocus?.isConnected) modalReturnFocus.focus();
  modalReturnFocus = null;
}

function openQrModal() {
  if (!$("modalBack").classList.contains("show")) modalReturnFocus = document.activeElement;
  clearInterval(qrModalWatch);
  $("modalTitle").textContent = "重新扫码登录";
  $("modalBody").innerHTML = `
    <div class="qr-companion-head"><img src="/assets/ui/dafeiyu-welcome.png" alt="等待和你连接的大肥鱼"><p>先在手机 QQ 切换到小号，再扫下面的码。<br>连接成功后，我们就去 QQ 聊天。</p></div>
    <div class="qr-frame" style="width:260px;height:260px;margin:12px auto"><img id="modalQr" alt="二维码"></div>
    <div class="row" style="justify-content:center"><span class="badge amber" id="modalQrBadge">等待扫码</span></div>
    <div class="hint" id="modalQrTip" style="text-align:center;margin-top:8px"></div>`;
  $("modalBack").classList.add("show");
  $("modalOk").classList.add("hidden");
  $("modalCancel").classList.remove("hidden");
  $("modalCancel").textContent = "关闭";
  $("modalCancel").focus();
  startQrFlow("modalQr", "modalQrBadge", "modalQrTip");
  qrModalWatch = setInterval(async () => {
    if (!$("modalQr")) { clearInterval(qrModalWatch); qrModalWatch = null; return; }
    if (!state?.napcat?.logged_in) {
      await refreshState(false);
      return;
    }
    clearInterval(qrModalWatch);
    qrModalWatch = null;
    clearInterval(qrTimer);
    try {
      await api("/api/qr/complete", { method: "POST", body: JSON.stringify({}) });
      await refreshState(false);
      renderOverview();
      if ($("modalQr")) closeModal();
      toast("登录成功");
    } catch (err) { toast(err.message || String(err)); }
  }, 2500);
}

async function openDeepseekPortal(event) {
  const link = event.target.closest?.("a[data-deepseek-link]");
  if (!link || state?.preview) return;
  event.preventDefault();
  if (link.dataset.busy === "true") return;
  link.dataset.busy = "true";
  link.setAttribute("aria-disabled", "true");
  try {
    await api("/api/open-url", {
      method: "POST",
      body: JSON.stringify({ url: "https://platform.deepseek.com/api_keys" }),
    });
    toast("已在默认浏览器打开 DeepSeek API 平台");
  } catch (err) { toast(err.message || String(err)); }
  finally {
    link.dataset.busy = "false";
    link.removeAttribute("aria-disabled");
  }
}

function bindEvents() {
  bindDashboardCredentialEvents();
  document.addEventListener("click", openDeepseekPortal);
  $$(".nav-item").forEach((btn) => btn.addEventListener("click", () => showView(btn.dataset.view)));
  $$("[data-go]").forEach((btn) => btn.addEventListener("click", () => showView(btn.dataset.go)));
  $$("[data-settings-anchor]").forEach(btn => btn.addEventListener("click", () => $(btn.dataset.settingsAnchor)?.scrollIntoView({ behavior: "smooth", block: "start" })));
  $("logRefresh").addEventListener("click", loadLog);
  $("logCopy").addEventListener("click", copyLogs);
  $("logFollow").addEventListener("change", () => { if ($("logFollow").checked) $("logView").scrollTop = $("logView").scrollHeight; });
  $("artifactSetup").addEventListener("click", () => state?.installed === false ? showWizard(0) : openPcModal());
  $("sideUpdate").addEventListener("click", () => {
    showView("settings");
    $("updateSettings").scrollIntoView({ behavior: "smooth", block: "start" });
    if (!updateState?.available && !updateState?.checking && !updateState?.downloading) runUpdateAction("check");
    else loadUpdateStatus();
  });
  $("updateCheck").addEventListener("click", () => runUpdateAction("check"));
  $("updateDownload").addEventListener("click", () => runUpdateAction("download"));
  $("updateInstall").addEventListener("click", () => runUpdateAction("install"));
  $("updateBannerAction").addEventListener("click", () => runUpdateAction(updateState?.downloaded && updateState?.download_verified ? "install" : "download"));
  $("updateLater").addEventListener("click", () => saveUpdatePreferences({ snooze_hours: 24 }, "好，明天再提醒你。当前版本可以继续使用。"));
  $("updateIgnore").addEventListener("click", () => {
    if (updateState?.latest_version) saveUpdatePreferences({ ignored_version: updateState.latest_version }, "已忽略这个版本，之后的新版本仍会提醒你。");
  });
  $("updateAutoCheck").addEventListener("change", () => saveUpdatePreferences({ auto_check: $("updateAutoCheck").checked }, $("updateAutoCheck").checked ? "已开启新版本自动检查，只提醒、不自动安装。" : "已关闭自动检查，你仍可以手动检查更新。"));
  $("connectionRetry").addEventListener("click", () => withBusy($("connectionRetry"), "正在重连…", async () => {
    await refreshState(false);
    renderOverview();
    toast("已连接本机服务");
  }));
  $("wizardBack").addEventListener("click", () => goWizard(-1));
  $("wizardNext").addEventListener("click", wizardNext);

  $("btnStart").addEventListener("click", () => {
    if (!state) { toast("先重新连接本机服务，再来唤醒她。"); return; }
    if (state.installed === false) { showWizard(0); return; }
    if (!state.has_key) { showWizard(2); return; }
    if (state.status?.astrbot?.up && state.status?.napcat?.up && !state.napcat?.logged_in) { openQrModal(); return; }
    withBusy($("btnStart"), "正在唤醒她…", async () => {
      await api("/api/start", { method: "POST", body: JSON.stringify({}) });
      await refreshState(false);
      toast(state?.napcat?.logged_in ? "服务已经启动，可以去 QQ 找她了。" : "服务已经启动，接下来连接 QQ 小号。 ");
    });
  });
  $("btnStop").addEventListener("click", async () => {
    await withBusy($("btnStop"), "正在关闭…", async () => {
      await api("/api/stop", { method: "POST", body: JSON.stringify({}) }); await refreshState(false); toast("聊天服务已关闭，她已下线休息。软件窗口会保留。");
    });
  });
  $("btnRestart").addEventListener("click", async () => {
    await withBusy($("btnRestart"), "正在重新连接…", async () => {
      await api("/api/restart", { method: "POST", body: JSON.stringify({}) }); await refreshState(false); toast("服务已重新启动");
    });
  });
  $("btnRelogin").addEventListener("click", () => state?.installed === false ? showWizard(0) : openQrModal());
  $("btnQuickLogin").addEventListener("click", async () => {
    try {
      toast("正在尝试自动登录…");
      const data = await api("/api/napcat/quick-login", { method: "POST", body: JSON.stringify({}) });
      await refreshState(false);
      renderOverview();
      if (data.logged_in) {
        toast(`已自动登录：${data.account?.nickname || ""} ${data.account?.qq || ""}`);
      } else {
        toast("自动登录未成功，请扫码");
        openQrModal();
      }
    } catch (err) { toast(err.message || String(err)); }
  });
  $("btnOpenAstrbot").addEventListener("click", () => openLocalDashboard("astrbot", $("btnOpenAstrbot")));
  $("btnOpenNapcat").addEventListener("click", () => openLocalDashboard("napcat", $("btnOpenNapcat")));
  $("pluginsOpenAstrbot").addEventListener("click", () => openLocalDashboard("astrbot", $("pluginsOpenAstrbot")));
  $("pluginsOpenNapcat").addEventListener("click", () => openLocalDashboard("napcat", $("pluginsOpenNapcat")));
  $("btnOpenFolder").addEventListener("click", () => api("/api/open-folder", { method: "POST", body: JSON.stringify({}) }).catch(() => {}));
  $("pcManage").addEventListener("click", openPcModal);
  $("artifactRefresh").addEventListener("click", loadArtifacts);
  $("artifactFolder").addEventListener("click", () => api("/api/artifacts/open-folder", { method: "POST", body: JSON.stringify({}) }).catch(() => {}));

  $("wlEnable").addEventListener("change", async () => {
    try {
      await api("/api/whitelist", { method: "POST", body: JSON.stringify({ action: "toggle", enable: $("wlEnable").checked }) });
      await refreshState(false);
      renderWhitelist();
    } catch (err) { toast(err.message || String(err)); }
  });
  $("wlAddBtn").addEventListener("click", () => {
    const id = $("wlId").value.trim();
    if (!id) { toast("先填号码"); return; }
    addWhitelist($("wlType").value, id);
    $("wlId").value = "";
  });
  $("wlApplyBtn").addEventListener("click", async () => {
    try { toast("正在应用并重启 AstrBot…"); await api("/api/config", { method: "POST", body: JSON.stringify({}) }); await refreshState(false); toast("已应用"); }
    catch (err) { toast(err.message || String(err)); }
  });
  $("contactsRefresh").addEventListener("click", loadContacts);
  $("contactsSearch").addEventListener("input", renderContacts);
  $("settingsSave").addEventListener("click", () => withBusy($("settingsSave"), "正在保存…", saveSettings));
  $("setWorkspaceBrowse").addEventListener("click", () => chooseFolder("setWorkspace"));
  $("setWorkspaceSave").addEventListener("click", saveWorkspace);
  $("setAutostart").addEventListener("change", async () => {
    try {
      const data = await api("/api/autostart", {
        method: "POST",
        body: JSON.stringify({ enabled: $("setAutostart").checked }),
      });
      state.autostart = data;
      $("setAutostart").checked = !!data.enabled;
      $("setAutostartHint").textContent = data.enabled ? "已开启：下次登录 Windows 时，会在系统托盘里启动。" : "开机自动启动已关闭，你可以随时手动打开她的小家。";
      toast(data.enabled ? "已开启开机自启" : "已关闭开机自启");
    } catch (err) { toast(err.message || String(err)); }
  });
  $("setAutoLogin").addEventListener("change", async () => {
    try {
      const data = await api("/api/napcat/auto-login", {
        method: "POST",
        body: JSON.stringify({ enabled: $("setAutoLogin").checked }),
      });
      state.auto_login = data.auto_login;
      toast(data.auto_login ? "已开启自动登录" : "已关闭自动登录");
    } catch (err) { toast(err.message || String(err)); }
  });

  $("lhRefresh").addEventListener("click", loadLoveHouse);
  $("lhLibRefresh")?.addEventListener("click", loadLoveHouse);
  $("lhSourceRefresh")?.addEventListener("click", () => { loadLovehouseSources(); loadOrganizer(); });
  $("lhStart").addEventListener("click", async () => {
    try { toast("正在启动爱之屋…"); await api("/api/lovehouse/start", { method: "POST", body: JSON.stringify({}) }); await loadLoveHouse(); toast("爱之屋已启动"); }
    catch (err) { toast(err.message || String(err)); }
  });
  $("lhOpen").addEventListener("click", async () => {
    try {
      const data = await api("/api/lovehouse/open", { method: "POST", body: JSON.stringify({}) });
      await loadLoveHouse();
      toast(data.focused ? "已切换到爱之屋窗口" : "爱之屋已启动，请从任务栏打开");
    }
    catch (err) { toast(err.message || String(err)); }
  });
  $("lhOpenFolder").addEventListener("click", () => api("/api/lovehouse/open-folder", { method: "POST", body: JSON.stringify({}) }).catch(() => {}));
  $("lhCreate").addEventListener("click", createLovehouseLibrary);
  $("lhConnect").addEventListener("click", connectLoveHouse);

  $$("button[data-art-style]").forEach(btn => btn.addEventListener("click", () => applyArtStyle(btn.dataset.artStyle)));
  $("pluginsRefresh").addEventListener("click", loadPlugins);
  $("pluginsFolder").addEventListener("click", () => api("/api/plugins/open-folder", { method: "POST", body: JSON.stringify({}) }).catch(() => {}));
  $("pluginsInstallLh").addEventListener("click", async () => {
    try { toast("正在安装插件文件…"); await api("/api/plugins/install-lovehouse", { method: "POST", body: JSON.stringify({}) }); await loadPlugins(); toast("插件文件已安装；请在爱之屋页完成连接和绑定"); }
    catch (err) { toast(err.message || String(err)); }
  });
  async function applyPluginChanges() {
    if (pluginRestartPending) return;
    pluginRestartPending = true;
    $("pluginsRestart").disabled = true;
    try { toast("正在重启 AstrBot…"); await api("/api/astrbot/restart", { method: "POST", body: JSON.stringify({}) }); await loadPlugins(); toast("对话服务已重启"); }
    catch (err) { toast(err.message || String(err)); }
    finally { pluginRestartPending = false; $("pluginsRestart").disabled = false; }
  }
  $("pluginsRestart").addEventListener("click", applyPluginChanges);
  $("pluginsApply").addEventListener("click", () => withBusy($("pluginsApply"), "正在应用…", applyPluginChanges));
  $$(".log-tab").forEach((tab) => tab.addEventListener("click", () => {
    $$(".log-tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    currentLog = tab.dataset.log;
    loadLog();
  }));
  $("modalCancel").addEventListener("click", closeModal);
  $("modalBack").addEventListener("click", (event) => { if (event.target === $("modalBack")) closeModal(); });
  document.addEventListener("keydown", (event) => {
    if (!$("modalBack").classList.contains("show")) return;
    if (event.key === "Escape" && $("modalOk").dataset.busy !== "true") { event.preventDefault(); closeModal(); }
    if (event.key === "Tab") {
      const focusable = Array.from($("modalBack").querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href]')).filter((el) => el.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  document.addEventListener("input", (event) => {
    if (["setKey", "setModel", "setApiBase", "setOwner"].includes(event.target.id)) {
      settingsDirty = true;
      setText("settingsSaveState", "有未保存的对话设置");
    }
  });
}

async function init() {
  bindEvents();
  globalThis.AiwuConversation?.init({api,esc,toast,openModal,closeModal,showView,getModalGeneration:()=>modalGeneration,getState:()=>state,refreshPlugins:loadPlugins});
  globalThis.AiwuExperience?.init({api,esc,toast,openModal,closeModal,showView,openCompanion:openCompanionModal,openStickers:openStickerLibrary,openComputer:openPcModal,getState:()=>state,isConnected:()=>localConnected,getLibrary:()=>lovehouseSelected,getModalGeneration:()=>modalGeneration,getLibraries:()=>lovehouseState?.libraries||[],selectLibrary:async id=>{if(!(lovehouseState?.libraries||[]).some(row=>row.id===id&&!row.is_demo))throw new Error("请选择当前可用的记忆库。");lovehouseSelected=id;renderLoveHouse(lovehouseState);renderLovehouseLibraries();await refreshLovehouseDetails();}});
  applyArtStyle(artStyle);
  let connected = false;
  try {
    await refreshState(false);
    connected = true;
  } catch (err) {
    showApp();
    setConnectionStatus(false);
    $("heroStatus").textContent = "暂时连不上本机服务";
    $("heroTitle").textContent = "本机服务未连接";
    $("heroDescription").textContent = "先保持窗口打开，我们会自动重试。也可以点击上方“立即重试”。";
    $("heroFootnote").textContent = err.message || "请检查应用是否已正常启动。";
    $("btnStart").disabled = true;
  }
  loadUpdateStatus();
  setInterval(() => { if (!document.hidden) { refreshState(true); loadUpdateStatus(); } }, 4000);
  setInterval(refreshMetrics, 2000);
  setInterval(() => { if (!document.hidden) globalThis.AiwuExperience?.tick(); }, 15000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshMetrics(); });
  if (!connected) return;
  const params = new URLSearchParams(location.search);
  const forceWizard = params.get("wizard");
  if (forceWizard !== null) {
    showWizard(Math.max(0, Math.min(5, Number(forceWizard) || 0)));
    return;
  }
  if (state.setup_complete || state.preview) {
    showApp();
    const view = params.get("view");
    if (view && VIEW_META[view]) showView(view);
    return;
  }
  if (!state.installed) showWizard(0);
  else if (!state.has_key) showWizard(2);
  else if (!state.napcat?.logged_in) showWizard(3);
  else if (!state.owner_qq) showWizard(4);
  else showWizard(5);
}

document.addEventListener("DOMContentLoaded", init);
