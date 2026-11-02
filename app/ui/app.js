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

const STEP_NAMES = ["准备", "安装", "模型", "扫码", "主人", "完成"];
const VIEW_META = {
  overview: ["概览", "大肥鱼住在这台电脑里"],
  whitelist: ["白名单", "只让信任的人唤醒她"],
  settings: ["设置", "模型、主人与启动方式"],
  logs: ["日志", "AstrBot 与 NapCat 的实时输出"],
  lovehouse: ["爱之屋", "长期记忆库、同步与直连"],
  artifacts: ["产物", "大肥鱼生成的软件和文件"],
  plugins: ["插件", "AstrBot 插件清单"],
};

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
    renderPills();
    if (!silent) return state;
    if (!$("app").classList.contains("hidden")) {
      renderOverview();
      if (!$("view-whitelist").classList.contains("hidden")) renderWhitelist();
      if (!$("view-settings").classList.contains("hidden") && !settingsDirty) renderSettings();
    }
    return state;
  } catch (err) {
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
  $$(".nav-item").forEach((btn) => btn.classList.toggle("active", btn.dataset.view === name));
  ["overview", "whitelist", "settings", "logs", "lovehouse", "artifacts", "plugins"].forEach((key) => {
    const section = $("view-" + key);
    if (section) section.classList.toggle("hidden", key !== name);
  });
  const [title, sub] = VIEW_META[name] || ["", ""];
  $("pageTitle").textContent = title;
  $("pageSub").textContent = sub;
  clearInterval(logTimer);
  logTimer = null;
  if (name === "overview") renderOverview();
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
  $("wizardNext").textContent = wizardStep === 5 ? "进入控制台" : "继续";
  $("wizardHint").textContent = "";

  const body = $("wizardBody");
  if (wizardStep === 0) {
    body.innerHTML = `
      <h2>先确认安装位置</h2>
      <p class="lead">运行时会装在这个目录，AstrBot、NapCat、日志和配置都放在一起，不污染系统 Python。</p>
      <label class="field" style="margin-bottom:16px"><span>安装目录</span>
        <div class="input-row">
          <input type="text" id="wizardInstallDir" value="${esc(state?.install || "")}" placeholder="例如 D:\\Dafeiyu">
          <button class="ghost" id="wizardBrowse">浏览</button>
        </div>
      </label>
      <dl class="kv">
        <dt>安装内容</dt><dd>AstrBot 4.29.4 + Python 3.13 + NapCat 4.18.42</dd>
        <dt>需要网络</dt><dd>首次安装约 300MB，走国内镜像</dd>
        <dt>需要准备</dt><dd>一个 QQ 小号、一个 DeepSeek API Key</dd>
      </dl>
      <div class="notice" style="margin-top:16px">
        NapCat 走的是个人号协议，不要用主号登录机器人；主号只作为“主人”和白名单使用。
      </div>`;
    $("wizardBrowse").addEventListener("click", chooseFolder);
    $("wizardNext").textContent = state?.installed ? "使用已有运行环境" : "开始安装";
  } else if (wizardStep === 1) {
    body.innerHTML = `
      <h2>正在安装运行环境</h2>
      <p class="lead">这一步会下载 uv、Python 3.13、AstrBot 和 NapCat。可以切到别的窗口，安装不会停。</p>
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
      <h2>给她一颗大脑</h2>
      <p class="lead">填 DeepSeek API Key。Key 会用 Windows DPAPI 加密保存在本机，不写日志、不上传。</p>
      <div class="stack">
        <label class="field"><span>API Key</span>
          <input type="password" id="wizardKey" placeholder="sk-..." autocomplete="off">
        </label>
        <label class="field"><span>模型</span>
          <select id="wizardModel">
            <option value="deepseek-chat">deepseek-chat（日常对话）</option>
            <option value="deepseek-reasoner">deepseek-reasoner（更慢更贵）</option>
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
      <h2>让小号登录 QQ</h2>
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
      <h2>谁能跟她说话</h2>
      <p class="lead">填你的主号 QQ。她会把你设为主人，并自动加入私聊白名单；其他人默认不回应。</p>
      <div class="stack">
        <label class="field"><span>主人 QQ</span>
          <input type="text" id="wizardOwner" placeholder="例如 123456789" value="${esc(state?.owner_qq || "")}">
        </label>
        <div class="notice green">白名单默认只放你一个人。以后可以在“白名单”页从好友/群列表里快捷添加。</div>
      </div>`;
    $("wizardNext").textContent = "保存并继续";
  } else if (wizardStep === 5) {
    body.innerHTML = `
      <h2>准备完成</h2>
      <p class="lead">最后一步会启动 AstrBot，确认人格和插件，然后进入控制台。</p>
      <dl class="kv">
        <dt>机器人</dt><dd>${esc(state?.bot_nickname || "大肥鱼")} <span class="mono">${esc(state?.bot_qq || "")}</span></dd>
        <dt>主人</dt><dd class="mono">${esc(state?.owner_qq || "")}</dd>
        <dt>模型</dt><dd>${esc(state?.model?.model || "deepseek-chat")}</dd>
        <dt>白名单</dt><dd>${esc((state?.whitelist?.private || []).length)} 个私聊 / ${esc((state?.whitelist?.groups || []).length)} 个群</dd>
      </dl>
      <div class="notice" style="margin-top:16px">进入控制台后，关闭窗口不会让她下线；要停服务请点“全部停止”。</div>`;
    $("wizardNext").textContent = "进入控制台";
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
  clearInterval(qrTimer);
  clearInterval(qrRefreshTimer);
  qrTimer = null;
  qrRefreshTimer = null;
  ensureServices().then(() => {
    reloadQr(imgId);
    const tick = async () => {
      try {
        const res = await fetch(`/api/qr/status?t=${Date.now()}`);
        const payload = await res.json();
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

function renderOverview() {
  if (!state) return;
  const a = state.status?.astrbot || {};
  const n = state.status?.napcat || {};
  $("ovAstrbot").textContent = a.up ? "运行中" : "已停止";
  $("ovAstrbotDesc").textContent = a.up ? `PID ${a.pid}` : "端口 6185";
  $("ovNapcat").textContent = n.up ? "运行中" : "已停止";
  $("ovNapcatDesc").textContent = n.up ? `PID ${n.pid}` : "端口 6099";
  $("ovAccount").textContent = state.napcat?.logged_in ? (state.napcat.nickname || "已登录") : "未登录";
  $("ovAccountDesc").textContent = state.napcat?.qq || "扫码后才能接消息";
  $("ovWhitelist").textContent = `${(state.whitelist?.private || []).length} / ${(state.whitelist?.groups || []).length}`;
  $("ovWhitelistDesc").textContent = state.whitelist?.enable ? "私聊 / 群聊（已启用）" : "白名单已关闭";
  $("ovNickname").textContent = state.bot_nickname || "大肥鱼";
  $("ovBotQQ").textContent = state.bot_qq || "-";
  $("ovOwner").textContent = state.owner_qq || "-";
  $("ovModel").textContent = state.model?.model || "-";
  $("ovInstall").textContent = state.install || "-";
  $("ovPersonaBadge").textContent = state.setup_complete ? "已启用" : "待完成";
  $("ovPersonaBadge").className = "badge " + (state.setup_complete ? "green" : "amber");
  const quickBtn = $("btnQuickLogin");
  if (quickBtn) {
    const show = !state.napcat?.logged_in && !!state.bot_qq;
    quickBtn.classList.toggle("hidden", !show);
    quickBtn.textContent = state.bot_qq ? `自动登录 ${state.bot_qq}` : "尝试自动登录";
  }

  const m = state.metrics || {};
  const pct = Math.max(0, Math.min(100, Number(m.percent) || 0));
  if (m.total_mb) {
    $("memDesc").textContent = `已用 ${fmtSize(m.used_mb)} / ${fmtSize(m.total_mb)} · 可用 ${fmtSize(m.available_mb)}`;
    $("memBadge").textContent = `${pct}%`;
    $("memBadge").className = "badge " + (pct >= 85 ? "red" : pct >= 70 ? "amber" : "green");
    $("memBar").style.width = `${pct}%`;
    $("memMeter").className = "meter" + (pct >= 85 ? " bad" : pct >= 70 ? " warn" : "");
  } else {
    $("memDesc").textContent = "暂时读取不到系统内存";
    $("memBadge").textContent = "-";
    $("memBadge").className = "badge gray";
    $("memBar").style.width = "0";
  }
  const chips = [];
  if (m.astrbot_mb) chips.push(`AstrBot ${fmtSize(m.astrbot_mb)}`);
  if (m.napcat_mb) chips.push(`NapCat ${fmtSize(m.napcat_mb)}`);
  if (m.lovehouse_mb) chips.push(`爱之屋 ${fmtSize(m.lovehouse_mb)}`);
  if (m.app_mb) chips.push(`本应用 ${fmtSize(m.app_mb)}`);
  $("memProcesses").innerHTML = chips.length
    ? chips.map((text) => `<span class="mem-chip">${esc(text)}</span>`).join("")
    : `<span class="hint">服务启动后会显示各进程占用。</span>`;

  const pc = state.pc_access || {};
  const adminCount = Object.keys(state.members || {}).length;
  $("pcBadge").textContent = pc.enabled ? "已开启" : "未开启";
  $("pcBadge").className = "badge " + (pc.enabled ? "green" : "gray");
  $("pcDesc").textContent = pc.enabled
    ? `主人 + ${adminCount} 个管理员；工作目录：${pc.workspace}`
    : "未开启；开启后她可以在指定工作目录里执行任务、生成文件。";
}

function renderWhitelist() {
  if (!state) return;
  $("wlEnable").checked = !!state.whitelist?.enable;
  $("wlSummary").textContent = `私聊 ${ (state.whitelist?.private || []).length } 个 · 群聊 ${ (state.whitelist?.groups || []).length } 个`;
  const members = state.members || {};
  const level = (id) => {
    const perm = members[id];
    if (!perm) return "仅聊天";
    const parts = [];
    if (perm.read) parts.push("读取");
    if (perm.write) parts.push("写文件");
    if (perm.run) parts.push("执行命令");
    return parts.length ? "管理员 · " + parts.join(" + ") : "仅聊天";
  };
  const entries = [];
  (state.whitelist?.private || []).forEach((id) => entries.push({ type: "private", id, label: id === state.owner_qq ? "主人" : "私聊" }));
  (state.whitelist?.groups || []).forEach((id) => entries.push({ type: "group", id, label: "群聊" }));
  const list = $("wlList");
  if (!entries.length) {
    list.innerHTML = `<div class="hint">还没有白名单。先从上面加一个 QQ 号。</div>`;
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
        <div class="who"><b>私聊 <span class="mono">${esc(entry.id)}</span></b><span>主人 · 始终放行 · 全部权限</span></div>
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
    <div class="notice" style="margin-bottom:12px">
      只限「大肥鱼工作目录」内的操作；每次调用都会记进审计。关机、格式化、删盘、改注册表、结束进程这类高危命令对所有人都拦截，主人也一样。
    </div>
    <div class="row wrap" style="gap:8px;margin-bottom:12px">
      <button class="ghost" data-preset="none">仅聊天</button>
      <button class="ghost" data-preset="assist">助手（读写文件）</button>
      <button class="ghost" data-preset="operator">操作员（读写 + 命令）</button>
    </div>
    <div class="stack">
      ${row("read", "读取文件", "查看工作目录里已有的文件、列目录")}
      ${row("write", "生成 / 修改文件", "在工作目录里写网页、脚本、文档等产物")}
      ${row("run", "执行命令", "运行 PowerShell（受安全策略限制，可做构建、转换、批量处理）")}
    </div>
    <div class="hint" style="margin-top:12px">保存后会重启一次 AstrBot，约 10 秒。</div>
  `, async () => {
    const read = !!document.querySelector('[data-perm-key="read"]')?.checked;
    const write = !!document.querySelector('[data-perm-key="write"]')?.checked;
    const run = !!document.querySelector('[data-perm-key="run"]')?.checked;
    toast("正在保存权限并重启 AstrBot…");
    const data = await api("/api/members", {
      method: "POST",
      body: JSON.stringify({ qq: id, read, write, run }),
    });
    state.members = data.members || {};
    state.whitelist = data.whitelist || state.whitelist;
    await refreshState(false);
    renderWhitelist();
    toast(read || write || run ? `已更新 ${id} 的权限` : `${id} 已改为仅聊天`);
  });
  document.querySelectorAll("[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const preset = btn.dataset.preset;
      const values = {
        none: { read: false, write: false, run: false },
        assist: { read: true, write: true, run: false },
        operator: { read: true, write: true, run: true },
      }[preset];
      ["read", "write", "run"].forEach((key) => {
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
    toast("已加入白名单，已生效");
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
    : "NapCat 未登录，先扫码后再刷新";
  if (!filtered.length) {
    if (!contacts.logged_in) {
      list.innerHTML = `<div class="hint">登录 NapCat 后这里会出现好友和群</div>
        <button class="ghost" id="contactsScan" style="margin-top:8px">去扫码登录</button>`;
      const scan = $("contactsScan");
      if (scan) scan.addEventListener("click", openQrModal);
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
    ? `电脑权限已开启；主人 + ${Object.keys(state.members || {}).length} 个管理员可调用，命令超时 ${pc.timeout_s || 60}s。`
    : "电脑权限未开启；可以先保存工作目录，需要时再到概览页授权。";
  settingsDirty = false;
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
    if (data.running && lovehouseSelected) {
      loadLovehouseSources();
      loadOrganizer();
    }
  } catch (err) {
    $("lhDesc").textContent = err.message || String(err);
    $("lhBadge").className = "badge red";
    $("lhBadge").textContent = "检测失败";
  }
}

function renderLoveHouse(data) {
  const version = data.version || "-";
  const latest = data.latest || "";
  $("lhVersion").textContent = version;
  $("lhRunning").textContent = data.running ? "运行中" : "未启动";
  const selected = (data.libraries || []).find((item) => item.id === lovehouseSelected);
  $("lhLibrary").textContent = selected ? selected.name : "-";
  $("lhDesc").textContent = data.installed
    ? `已安装 ${version}${latest && version !== latest ? `，最新 ${latest}` : ""}；数据目录 ${data.data_dir}`
    : "没有在本机找到 LoveHouse.exe，请先安装 0.10.2rc1 或更新版本。";
  $("lhBadge").className = "badge " + (data.running ? "green" : data.installed ? "amber" : "red");
  $("lhBadge").textContent = data.running ? "服务在线" : data.installed ? "未启动" : "未安装";
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
    list.innerHTML = `<div class="hint">没有可选记忆库。启动爱之屋后刷新，或在下面新建一个。</div>`;
    return;
  }
  list.innerHTML = libraries.map((item) => `
    <div class="list-item">
      <div class="who">
        <b>${esc(item.name)}</b>
        <span>${esc(item.node_count ?? 0)} 条记忆 · <span class="mono">${esc(item.id)}</span></span>
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
      loadLovehouseSources();
      loadOrganizer();
    });
  });
}

async function loadLovehouseSources() {
  if (!lovehouseSelected) return;
  try {
    const data = await api(`/api/lovehouse/sources?library_id=${encodeURIComponent(lovehouseSelected)}`);
    const sources = data.sources?.sources || [];
    $("lhSourceDesc").textContent = sources.length
      ? `当前库有 ${sources.length} 个已登记来源。`
      : "还没有连接。选好记忆库后点右上“连接并安装插件”。";
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
    $("lhSourceDesc").textContent = err.message || String(err);
  }
}

async function loadOrganizer() {
  if (!lovehouseSelected) return;
  try {
    const data = await api(`/api/lovehouse/organizer?library_id=${encodeURIComponent(lovehouseSelected)}`);
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
    lovehouseSelected = data.suggested_id || lovehouseSelected;
    renderLoveHouse(data);
    renderLovehouseLibraries();
    toast("记忆库已创建");
  } catch (err) { toast(err.message || String(err)); }
}

async function loadPlugins() {
  try {
    const data = await api("/api/plugins");
    pluginList = data.plugins || [];
    renderPlugins();
  } catch (err) {
    $("pluginsList").innerHTML = `<div class="hint">${esc(err.message || String(err))}</div>`;
  }
}

function renderPlugins() {
  const list = $("pluginsList");
  if (!pluginList.length) {
    list.innerHTML = `<div class="hint">插件目录为空，或 AstrBot 运行时尚未安装。</div>`;
    return;
  }
  list.innerHTML = pluginList.map((item) => `
    <div class="list-item">
      <div class="who">
        <b>${esc(item.name)} ${item.version ? `<span class="badge gray">${esc(item.version)}</span>` : ""}</b>
        <span>${esc(item.desc || item.id)}</span>
        <span class="mono">${esc(item.path)}</span>
      </div>
      <span class="badge ${item.enabled ? "green" : "gray"}">${item.enabled ? "已安装" : "已停用"}</span>
    </div>`).join("");
}

function fmtFileSize(bytes) {
  const value = Number(bytes) || 0;
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  if (value >= 1024) return `${Math.round(value / 1024)} KB`;
  return `${value} B`;
}

async function loadArtifacts() {
  const list = $("artifactList");
  if (!list) return;
  try {
    const data = await api("/api/artifacts");
    $("artifactDesc").textContent = data.workspace ? `工作目录：${data.workspace}` : "还没有设置工作目录。";
    const items = data.items || [];
    if (!items.length) {
      list.innerHTML = `<div class="hint">还没有产物。先在概览页开启电脑权限，再让她在工作目录里生成文件。</div>`;
      return;
    }
    list.innerHTML = items.map((item) => `
      <div class="list-item">
        <div class="who">
          <b>${esc(item.name)} ${item.kind ? `<span class="badge gray">${esc(item.kind)}</span>` : ""}</b>
          <span class="mono">${esc(item.path)}</span>
          <span>${fmtFileSize(item.size)} · ${fmtDate(new Date(item.modified * 1000))}</span>
        </div>
        <div class="row">
          <button class="ghost" data-open="${esc(item.path)}">打开</button>
          <button class="ghost" data-reveal="${esc(item.path)}">定位</button>
        </div>
      </div>`).join("");
    list.querySelectorAll("[data-open]").forEach((btn) => {
      btn.addEventListener("click", () => api("/api/artifacts/open", {
        method: "POST", body: JSON.stringify({ path: btn.dataset.open }),
      }).catch((err) => toast(err.message || String(err))));
    });
    list.querySelectorAll("[data-reveal]").forEach((btn) => {
      btn.addEventListener("click", () => api("/api/artifacts/reveal", {
        method: "POST", body: JSON.stringify({ path: btn.dataset.reveal }),
      }).catch((err) => toast(err.message || String(err))));
    });
  } catch (err) {
    list.innerHTML = `<div class="hint">${esc(err.message || String(err))}</div>`;
  }
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
      开启后，主人和被授权的管理员可以让大肥鱼在下面这个工作目录里读写文件、执行 PowerShell 命令。
      管理员的权限在「白名单 → 权限」里逐项分配。
      这不是操作系统沙箱：命令仍以当前 Windows 用户权限运行，只建议在你自己的电脑上、只对主人开启。
    </div>
    <div class="stack">
      <label class="row" style="gap:8px">
        <input type="checkbox" id="pcEnabled" style="width:auto" ${pc.enabled ? "checked" : ""}>
        <span>允许大肥鱼使用这台电脑</span>
      </label>
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
        workspace: $("pcWorkspace").value.trim(),
        timeout_s: Number($("pcTimeout").value) || 60,
        private_only: $("pcPrivate").checked,
        confirm: true,
      }),
    });
    state.pc_access = data;
    renderOverview();
    renderSettings();
    toast(enabled ? "电脑权限已开启" : "电脑权限已关闭");
  });
  const browse = $("pcBrowse");
  if (browse) browse.addEventListener("click", () => chooseFolder("pcWorkspace"));
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

async function loadLog() {
  try {
    const text = await (await fetch(`/api/logs?name=${currentLog}&t=${Date.now()}`)).text();
    const el = $("logView");
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    el.textContent = text || "暂无日志";
    if (nearBottom) el.scrollTop = el.scrollHeight;
  } catch (err) { /* ignore */ }
}

function openModal(title, body, onOk) {
  $("modalTitle").textContent = title;
  $("modalBody").innerHTML = body;
  $("modalBack").classList.add("show");
  $("modalOk").classList.remove("hidden");
  $("modalCancel").textContent = "取消";
  $("modalOk").onclick = async () => {
    try { if (onOk) await onOk(); closeModal(); } catch (err) { toast(err.message || String(err)); }
  };
}

function closeModal() {
  $("modalBack").classList.remove("show");
}

function openQrModal() {
  $("modalTitle").textContent = "重新扫码登录";
  $("modalBody").innerHTML = `
    <p class="hint">手机 QQ 先切到小号再扫。登录成功会自动关闭。</p>
    <div class="qr-frame" style="width:260px;height:260px;margin:12px auto"><img id="modalQr" alt="二维码"></div>
    <div class="row" style="justify-content:center"><span class="badge amber" id="modalQrBadge">等待扫码</span></div>
    <div class="hint" id="modalQrTip" style="text-align:center;margin-top:8px"></div>`;
  $("modalBack").classList.add("show");
  $("modalOk").classList.add("hidden");
  $("modalCancel").textContent = "关闭";
  startQrFlow("modalQr", "modalQrBadge", "modalQrTip");
  const watch = setInterval(async () => {
    if (!state?.napcat?.logged_in) {
      await refreshState(false);
      return;
    }
    clearInterval(watch);
    clearInterval(qrTimer);
    try {
      await api("/api/qr/complete", { method: "POST", body: JSON.stringify({}) });
      await refreshState(false);
      renderOverview();
      closeModal();
      toast("登录成功");
    } catch (err) { toast(err.message || String(err)); }
  }, 2500);
}

function bindEvents() {
  $$(".nav-item").forEach((btn) => btn.addEventListener("click", () => showView(btn.dataset.view)));
  $("wizardBack").addEventListener("click", () => goWizard(-1));
  $("wizardNext").addEventListener("click", wizardNext);

  $("btnStart").addEventListener("click", async () => {
    try { toast("正在启动…"); await api("/api/start", { method: "POST", body: JSON.stringify({}) }); await refreshState(false); renderOverview(); toast("已启动"); }
    catch (err) { toast(err.message || String(err)); }
  });
  $("btnStop").addEventListener("click", async () => {
    if (!confirm("停止后她会下线，确定吗？")) return;
    try { toast("正在停止…"); await api("/api/stop", { method: "POST", body: JSON.stringify({}) }); await refreshState(false); renderOverview(); toast("已停止"); }
    catch (err) { toast(err.message || String(err)); }
  });
  $("btnRestart").addEventListener("click", async () => {
    try { toast("正在重启…"); await api("/api/restart", { method: "POST", body: JSON.stringify({}) }); await refreshState(false); renderOverview(); toast("已重启"); }
    catch (err) { toast(err.message || String(err)); }
  });
  $("btnRelogin").addEventListener("click", openQrModal);
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
  $("btnOpenAstrbot").addEventListener("click", () => api("/api/open-url", {
    method: "POST", body: JSON.stringify({ url: "http://127.0.0.1:6185" }),
  }).catch((err) => toast(err.message || String(err))));
  $("btnOpenNapcat").addEventListener("click", () => api("/api/open-url", {
    method: "POST", body: JSON.stringify({ url: "http://127.0.0.1:6099" }),
  }).catch((err) => toast(err.message || String(err))));
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
  $("settingsSave").addEventListener("click", saveSettings);
  $("setWorkspaceBrowse").addEventListener("click", () => chooseFolder("setWorkspace"));
  $("setWorkspaceSave").addEventListener("click", saveWorkspace);
  $("setAutostart").addEventListener("change", async () => {
    try {
      const data = await api("/api/autostart", {
        method: "POST",
        body: JSON.stringify({ enabled: $("setAutostart").checked }),
      });
      state.autostart = data;
      renderSettings();
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
  $("lhLibRefresh").addEventListener("click", loadLoveHouse);
  $("lhSourceRefresh").addEventListener("click", () => { loadLovehouseSources(); loadOrganizer(); });
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

  $("pluginsRefresh").addEventListener("click", loadPlugins);
  $("pluginsFolder").addEventListener("click", () => api("/api/plugins/open-folder", { method: "POST", body: JSON.stringify({}) }).catch(() => {}));
  $("pluginsInstallLh").addEventListener("click", async () => {
    try { toast("正在安装插件文件…"); await api("/api/plugins/install-lovehouse", { method: "POST", body: JSON.stringify({}) }); await loadPlugins(); toast("插件文件已安装；请在爱之屋页完成连接和绑定"); }
    catch (err) { toast(err.message || String(err)); }
  });
  $("pluginsRestart").addEventListener("click", async () => {
    try { toast("正在重启 AstrBot…"); await api("/api/astrbot/restart", { method: "POST", body: JSON.stringify({}) }); toast("AstrBot 已重启"); }
    catch (err) { toast(err.message || String(err)); }
  });
  $$(".log-tab").forEach((tab) => tab.addEventListener("click", () => {
    $$(".log-tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    currentLog = tab.dataset.log;
    loadLog();
  }));
  $("modalCancel").addEventListener("click", closeModal);
  $("modalBack").addEventListener("click", (event) => { if (event.target === $("modalBack")) closeModal(); });
  document.addEventListener("input", (event) => {
    if (event.target.closest("#view-settings")) settingsDirty = true;
  });
}

async function init() {
  bindEvents();
  try {
    await refreshState(false);
  } catch (err) {
    $("app").innerHTML = `<div class="card" style="margin:40px">无法连接本地服务：${esc(err.message)}</div>`;
    return;
  }
  setInterval(() => refreshState(true), 4000);
  const params = new URLSearchParams(location.search);
  const forceWizard = params.get("wizard");
  if (forceWizard !== null) {
    showWizard(Math.max(0, Math.min(5, Number(forceWizard) || 0)));
    return;
  }
  if (state.setup_complete) {
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
