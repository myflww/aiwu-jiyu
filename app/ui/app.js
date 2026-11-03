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
let localConnected = false;
let updateState = null;
let updateRequestPending = false;
let updateStatusLoading = false;
let updateReachable = false;
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
  });
  const a = !!state?.status?.astrbot?.up, n = !!state?.status?.napcat?.up;
  setText("pluginsDashboardHint", !localConnected ? "请先重新连接本机服务，再打开后台。" : state?.preview ? "后台入口预览；正式运行时在默认浏览器打开。" : a && n ? "在默认浏览器打开，仅连接这台电脑的服务。" : !a && !n ? "服务未启动，请先在首页一键开启。" : `${a ? "NapCat" : "AstrBot"} 未启动，请先在首页一键开启。`);
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

function renderUpdates() {
  const u = updateState || {};
  const available = !!u.available;
  const verified = !!(u.downloaded && u.download_verified);
  const downloading = !!u.downloading;
  const checking = !!u.checking;
  const busy = updateRequestPending || checking || downloading;
  const progress = Math.max(0, Math.min(100, Number(u.progress) || 0));
  const latest = versionLabel(u.latest_version);
  if (u.current_version) {
    setText("sideVersion", versionLabel(u.current_version));
    setText("updateCurrentVersion", versionLabel(u.current_version));
  }
  if (!updateRequestPending && typeof u.auto_check === "boolean") $("updateAutoCheck").checked = u.auto_check;
  $("updateAutoCheck").disabled = updateRequestPending || !localConnected;
  const fresh = available && u.ignored_version !== u.latest_version;
  $("sideUpdateDot").classList.toggle("hidden", !fresh);
  $("sideUpdate").classList.toggle("has-update", fresh);
  setText("sideUpdateLabel", downloading ? `更新下载中 ${progress}%` : verified ? "更新已备好 · 查看" : fresh ? "发现新版本 · 查看" : checking ? "正在检查更新" : "检查更新");
  let message = u.message || "自动检查只会提醒，更新与否由你决定。";
  if (u.error) message = `更新暂未完成：${u.error}`;
  else if (downloading) message = `正在下载 ${latest}（${progress}%），你可以继续使用软件。`;
  else if (verified) message = "更新包已通过校验。安装时本窗口会关闭，聊天服务继续运行。";
  else if (checking) message = "正在检查更新…";
  else if (available) message = `${latest} 已发布。你可以现在更新，也可以继续使用当前版本。`;
  else if (u.checked_at && u.latest_version) message = "没有发现比当前版本更新的正式版本。";
  setText("updateMessage", message);
  $("updateMessage").classList.toggle("has-error", !!u.error);
  setText("updateVersionBadge", checking ? "检查中" : downloading ? "正在下载" : u.error ? "请稍后重试" : verified ? "更新已备好" : available ? `可更新至 ${latest}` : u.checked_at ? "暂无新版本" : "等待检查");
  $("updateVersionBadge").className = "badge " + (u.error ? "red" : verified ? "green" : available || checking ? "amber" : "gray");
  $("updateCheck").disabled = busy || !localConnected;
  setText("updateCheck", checking ? "正在检查…" : "检查更新");
  $("updateDownload").classList.toggle("hidden", !available || verified);
  $("updateDownload").disabled = busy || !updateReachable || !localConnected;
  setText("updateDownload", downloading ? `正在下载 ${progress}%` : u.error ? "重试下载" : "下载更新");
  $("updateInstall").classList.toggle("hidden", !verified);
  $("updateInstall").disabled = !verified || busy || !updateReachable || !localConnected;
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
  if (action === "install" && !(updateState?.downloaded && updateState?.download_verified)) {
    toast("先下载并校验更新包，再来安装。");
    return;
  }
  updateRequestPending = true;
  renderUpdates();
  try {
    updateState = await api(`/api/updates/${action}`, { method: "POST", body: JSON.stringify({}) });
    updateReachable = true;
    if (action === "install") toast("正在启动更新安装，软件窗口即将关闭。聊天服务继续运行。");
  } catch (err) {
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
  clearInterval(logTimer);
  logTimer = null;
  if (name === "overview") { renderOverview(); refreshMetrics(); }
  if (name === "whitelist") { renderWhitelist(); loadContacts(); }
  if (name === "settings") { renderSettings(); loadQuickLoginInfo(); }
  if (name === "logs") { loadLog(); logTimer = setInterval(loadLog, 3000); }
  if (name === "lovehouse") loadLoveHouse();
  if (name === "artifacts") loadArtifacts();
  if (name === "plugins") loadPlugins();
}

function showApp() {
  $("wizard").classList.add("hidden");
  $("app").classList.remove("hidden");
  showView("overview");
}

function showWizard(step) {
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
    const data = await api("/api/plugins");
    pluginList = data.plugins || [];
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
    astrbot_plugin_dafeiyu_companion: { title: "主动陪伴与情绪", desc: "鲸鱼娘自然口吻、主动问候、聊天情绪与电脑活动概况，各项可单独选择。", icon: "chat", theme: "pink", action: "companion", actionLabel: "陪伴设置" },
    astrbot_plugin_dafeiyu_stickers: { title: "大肥鱼表情包", desc: "按聊天语气自动搭配表情，原创贴纸与社区动图离线附带，与 QQ 小表情分开管理。", icon: "spark", theme: "pink", action: "stickers", actionLabel: "配图与素材" },
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
    const status = !installed ? "尚未部署，请先完成首次设置" : item.pending_change ? "已保存，重启对话服务后生效" : item.enabled ? card.action === "pc" ? (state?.pc_access?.enabled ? state.pc_access.desktop_enabled ? "已启用 · 完整桌面操控已授权" : "已启用 · 工作目录工具已授权" : "已启用 · 操作权限未授权") : card.action === "memory" ? "已启用 · 仅处理登记会话" : "已启用" : "已停用";
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
    else showView("lovehouse");
  }));
  $$("[data-plugin-toggle]").forEach(btn => btn.addEventListener("click", () => togglePlugin(btn.dataset.pluginToggle)));
  ["pluginsFolder", "pluginsInstallLh", "pluginsRestart"].forEach(id => { $(id).disabled = state?.installed === false || pluginTogglePending; });
}

async function togglePlugin(id) {
  const item = pluginList.find(plugin => plugin.id === id);
  if (!item || pluginTogglePending) return;
  const focusId = id;
  pluginTogglePending = true;
  renderPlugins();
  try {
    const result = await api("/api/plugins/toggle", {method:"POST",body:JSON.stringify({id,enabled:!item.enabled})});
    if (result.plugins) pluginList = result.plugins;
    else await loadPlugins();
    toast(result.message || (result.restart_required ? "已保存，重启对话服务后生效" : "插件状态已更新"));
  } catch (err) { toast(err.message || String(err)); }
  finally {
    pluginTogglePending = false;
    renderPlugins();
    $$("[data-plugin-toggle]").find(btn => btn.dataset.pluginToggle === focusId)?.focus();
  }
}

function fmtFileSize(bytes) {
  const value = Number(bytes) || 0;
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  if (value >= 1024) return `${Math.round(value / 1024)} KB`;
  return `${value} B`;
}

async function openCompanionModal() {
  try {
    const data = await api("/api/companion");
    const active = pluginList.find(item => item.id === "astrbot_plugin_dafeiyu_companion")?.enabled;
    const choice = (key, title, description) => `<label class="perm-row"><input type="checkbox" id="companion-${key}" ${data[key] ? "checked" : ""}><div><b>${title}</b><span class="small">${description}</span></div></label>`;
    openModal("大肥鱼的陪伴方式", `
      ${active ? "" : '<div class="notice blue">插件尚未启用。可以先保存偏好，再回到列表开启“主动陪伴与情绪”。</div>'}
      <div class="stack companion-options">
        ${choice("whale_style_enabled", "鲸鱼娘口吻增强", "傲娇嘴硬但温柔，用自然中文接话；保留当前人格和实际工具能力，不套模板结尾。")}
        ${choice("proactive_enabled", "让她主动来找你", "只联系已和她私聊过的主人，遵守频率上限和免打扰时间。")}
        ${choice("emotion_enabled", "情绪陪伴", "结合你们的聊天节奏与文字调整表达；这是本地模拟的情绪状态。")}
        ${choice("activity_enabled", "感知电脑活动概况", "读取前台应用名称与离开/回来状态，提供给当前聊天模型；不记录键盘内容、不持续截屏。")}
      </div>
      <div class="companion-form-grid">
        <label class="field"><span>主动聊天最短间隔（分钟）</span><input type="number" id="companion-interval" min="30" max="1440" value="${esc(data.interval_minutes || 120)}"></label>
        <label class="field"><span>每天最多主动联系</span><input type="number" id="companion-limit" min="1" max="10" value="${esc(data.daily_limit || 3)}"></label>
        <label class="field"><span>免打扰开始（0–23 时）</span><input type="number" id="companion-quiet-start" min="0" max="23" value="${esc(data.quiet_start ?? 23)}"></label>
        <label class="field"><span>免打扰结束（0–23 时）</span><input type="number" id="companion-quiet-end" min="0" max="23" value="${esc(data.quiet_end ?? 8)}"></label>
      </div>
      <p class="hint">${esc(data.proactive_summary || "主动聊天使用当前模型，可能产生 API 用量。先与她私聊一次，再等待主动问候。")}</p>
      <div class="hint">屏幕截图与鼠标键盘操作在“电脑操作 → 操作权限”单独授权。保存后，已启用的陪伴插件会重新加载。</div>
    `, async () => {
      const body = {
        whale_style_enabled: $("companion-whale_style_enabled").checked,
        proactive_enabled: $("companion-proactive_enabled").checked,
        emotion_enabled: $("companion-emotion_enabled").checked,
        activity_enabled: $("companion-activity_enabled").checked,
        interval_minutes: Number($("companion-interval").value),
        daily_limit: Number($("companion-limit").value),
        quiet_start: Number($("companion-quiet-start").value),
        quiet_end: Number($("companion-quiet-end").value),
      };
      await api("/api/companion", {method:"POST",body:JSON.stringify(body)});
      await loadPlugins();
      toast(active ? "陪伴偏好已保存" : "偏好已保存，启用插件后生效");
    });
  } catch (err) { toast(err.message || String(err)); }
}

async function openStickerLibrary() {
  try {
    const data = await api("/api/stickers");
    const items = data.stickers || [];
    const prefs = data.settings || {auto_match_enabled:true,cooldown_seconds:30,pack_selection:"all"};
    const active = pluginList.find(item => item.id === "astrbot_plugin_dafeiyu_stickers")?.enabled;
    const credits = (data.packs || []).map(pack => {
      const author = pack.attribution;
      const lines = typeof author === "string" ? [author] : [author?.author, author?.website, author?.bilibili,
        ...(author?.designers || []).map(item => `${item.name} · ${item.url}`)];
      return `<p><b>${esc(pack.name)}</b><br>${lines.filter(Boolean).map(esc).join("<br>")}<br>${esc(pack.source_url || "")}<br>${esc(typeof pack.license === "string" ? pack.license : "详见随附 LICENSE-ASSETS 与署名文件")}</p>`;
    }).join("");
    openModal("表情匹配与素材库", `
      ${active ? "" : '<div class="notice blue">素材已经随应用附带，启用“大肥鱼表情包”后会在 QQ 生效。</div>'}
      <label class="perm-row" style="margin:14px 0"><input type="checkbox" id="sticker-auto" ${prefs.auto_match_enabled ? "checked" : ""}><div><b>按聊天情绪自动配图</b><span class="small">根据回复语气选择合适表情，每条回复最多一张；不强制每句话都配图。</span></div></label>
      <div class="companion-form-grid"><label class="field"><span>发送间隔（秒）</span><input type="number" id="sticker-cooldown" min="10" max="300" value="${esc(prefs.cooldown_seconds)}"></label><label class="field"><span>使用素材</span><select id="sticker-pack"><option value="all">全部随附素材</option><option value="original">爱屋及鱼原创</option><option value="community">社区授权素材</option></select></label></div>
      <p class="hint">共 ${items.length} 张随附素材。也可在 QQ 明确请求，或使用“/肥鱼表情 开心”。</p>
      <div class="sticker-library-grid" tabindex="0" aria-label="表情素材列表，可滚动浏览">${items.map(item => `<figure><img src="${esc(item.url || item.path)}" alt="${esc(item.label || item.id)}" loading="lazy"><figcaption>${esc(item.label || item.id)}<small>${item.pack_id === "community" ? "社区授权动图" : "原创贴纸"}</small></figcaption></figure>`).join("")}</div>
      <details class="hint sticker-credits"><summary>素材来源与许可</summary>${credits}</details>`, async () => {
        await api("/api/stickers/settings", {method:"POST",body:JSON.stringify({
          auto_match_enabled:$("sticker-auto").checked,
          cooldown_seconds:Number($("sticker-cooldown").value),
          pack_selection:$("sticker-pack").value,
        })});
        await loadPlugins();
        toast(active ? "表情匹配偏好已保存" : "偏好已保存，启用插件后生效");
      });
    $("sticker-pack").value = prefs.pack_selection;
  } catch (err) { toast(err.message || String(err)); }
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
  if (!$("modalBack").classList.contains("show")) modalReturnFocus = document.activeElement;
  $("modalTitle").textContent = title;
  $("modalBody").innerHTML = body;
  $("modalBack").classList.add("show");
  $("modalOk").classList.remove("hidden");
  $("modalCancel").classList.remove("hidden");
  $("modalCancel").textContent = "取消";
  $("modalOk").textContent = "保存设置";
  $("modalOk").onclick = () => withBusy($("modalOk"), "正在保存…", async () => { if (onOk) await onOk(); closeModal(); });
  const first = $("modalBody").querySelector("input,select,textarea,button");
  (first || $("modalCancel")).focus();
}

function closeModal() {
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
