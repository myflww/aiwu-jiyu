/* Shared companionship surfaces. Runtime facts drive art; no background model calls. */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const ART = {
    connecting: "/assets/ui/states/dafeiyu-connecting-v140.png",
    working: "/assets/ui/states/dafeiyu-working-v140.png",
    remembering: "/assets/ui/states/dafeiyu-remembering-v140.png",
    quiet: "/assets/ui/states/dafeiyu-quiet-v140.png",
    repair: "/assets/ui/states/dafeiyu-repair-v140.png",
    awake: "/assets/ui/dafeiyu-chibi-sailor.webp",
    resting: "/assets/ui/dafeiyu-rest.png",
  };
  let shell, currentView = "overview", companion = null, tasks = null;
  let companionWriting = false;
  let currentPose = {key:"resting",label:"正在看看小家的状态",description:"状态会随着本机服务与实际任务更新。"};
  let memoryReading = false, health = null, disposed = false;
  let memoryLibrary = "", memoryRows = [], memoryRevision = "", memoryCursor = null, memoryTotal = 0;
  const generations = new Map();
  const generation = key => { const value=(generations.get(key)||0)+1;generations.set(key,value);return value; };
  const live = (key,value) => !disposed && generations.get(key)===value;
  const e = value => shell.esc(String(value ?? ""));
  const post = (url,body={}) => shell.api(url,{method:"POST",body:JSON.stringify(body)});
  const date = value => { if(!value)return "—";const d=new Date(typeof value==="number"?value*1000:value);return Number.isNaN(d.getTime())?"—":d.toLocaleString("zh-CN",{hour12:false}); };
  const hint = (text,error=false) => `<p class="exp-hint${error?" error":""}" role="status">${e(text)}</p>`;
  async function run(button,label,action) {
    if(button?.disabled)return;
    const old=button?.textContent;if(button){button.disabled=true;button.textContent=label;}
    try{return await action();}catch(error){shell.toast(error.message||"操作尚未完成，请重试。");}
    finally{if(button?.isConnected){button.disabled=false;button.textContent=old;}}
  }
  function insert(parent,id,html,prepend=false) {
    if(!parent || $(id))return;
    const node=document.createElement("section");node.id=id;node.className="section card experience-panel";node.innerHTML=html;
    prepend?parent.prepend(node):parent.append(node);
  }
  function artHead(image,kicker,title,description) {
    return `<div class="exp-heading"><img src="${image}" alt="" loading="lazy"><div><span class="exp-kicker">${kicker}</span><h2>${title}</h2><p>${description}</p></div></div>`;
  }
  function init(dependencies) {
    shell=dependencies;
    const overview=$("view-overview");
    insert(overview,"companionDailyPanel",artHead(ART.quiet,"陪伴，可以有自己的节奏","今天想怎么一起度过？","选择一种日常节奏，也可以只为今天留一点安静。")+`<div id="dailyProfileOptions" class="exp-profiles"></div><p id="dailyPreferenceStatus" class="exp-hint" role="status">正在读取陪伴偏好…</p><div class="row wrap"><button class="soft" data-exp-action="quiet">今天先安静陪我</button><button class="soft" data-exp-action="more">今天可以多聊一点</button><button class="text-button" data-exp-action="normal">恢复平时节奏</button><button class="text-button" data-exp-action="companion">详细陪伴设置 ↗</button></div>`);
    if(overview && $("companionDailyPanel"))overview.querySelector(".companion-hero")?.after($("companionDailyPanel"));
    insert(overview,"healthHomePanel",`<div class="row between wrap"><div><h2>小家体检</h2><p class="exp-hint">把 QQ、模型、插件和记忆的状态一起检查清楚。</p></div><button class="ghost" data-exp-action="health">检查一下</button></div><div id="healthHomeResult">${hint("只读检查不会发送 QQ 消息，也不会调用聊天模型。")}</div>`);
    insert($("view-logs"),"healthPanel",artHead(ART.repair,"把问题说清楚","小家健康检查","开关状态、实际运行和发送结果，会分开显示。")+`<div class="row wrap exp-filterbar"><label class="field"><span>想检查什么</span><select id="healthReason"><option value="all">整个小家</option><option value="proactive">为什么没有主动找我</option><option value="stickers">为什么没有配表情</option><option value="memory">为什么没有记住</option><option value="computer">为什么不能操作电脑</option></select></label><button class="primary" data-exp-action="health">开始只读体检</button></div><div id="healthResult">${hint("点击后读取本机实际状态。不会主动发测试消息或调用模型。")}</div>`,true);
    insert($("view-artifacts"),"taskPanel",artHead(ART.working,"她正在认真做事","任务进度与结果","只显示电脑工具实际发生的任务，不用动画猜测执行进度。")+`<div class="row between"><span class="exp-hint">执行记录不包含你输入的命令、正文或截图内容。</span><button class="text-button" data-exp-action="tasks">刷新任务</button></div><div id="taskList">${hint("正在读取任务记录…")}</div>`,true);
    insert($("view-settings"),"recoveryPanel",artHead(ART.repair,"给小家留一份退路","备份与恢复","更新前会创建备份。恢复范围和兼容版本会先明确展示。")+`<div class="row wrap"><button class="ghost" data-exp-action="backup">备份当前设置与随包插件</button><button class="text-button" data-exp-action="backups">刷新备份列表</button></div><p class="exp-hint">备份只包含允许恢复的设置与随包插件；QQ 登录、模型密钥、记忆和自行安装的插件独立保留。</p><div id="recoveryList">${hint("打开此页后读取本机备份。")}</div>`);
    $("settingsAppearance")?.after($("recoveryPanel"));
    const anchors=$("view-settings")?.querySelector("[data-settings-anchor]")?.parentElement;
    if(anchors){const button=document.createElement("button");button.className="ghost";button.type="button";button.textContent="备份恢复";button.onclick=()=>$("recoveryPanel")?.scrollIntoView({behavior:"smooth",block:"start"});anchors.appendChild(button);}
    insert($("view-lovehouse"),"memoryGardenPanel",artHead(ART.remembering,"每件小事，都有来处","你们的共同回忆","沿用这本爱之屋记忆库。可以查看、纠正、标记重要，也可以让一条回忆安静下来。")+`<div class="row wrap exp-filterbar"><label class="field"><span>要查看的记忆库</span><select id="memoryGardenLibrary"></select></label><label class="field"><span>搜索这本记忆库</span><input id="memoryGardenQuery" type="search" maxlength="200" placeholder="偏好、最近在意的事、一个约定…"></label><label class="field"><span>显示范围</span><select id="memoryGardenView"><option value="all">全部条目</option><option value="active">使用中的回忆</option><option value="disabled">暂不使用的回忆</option></select></label><button class="ghost" data-exp-action="memories">搜索／刷新</button><button class="soft" data-exp-action="memory-add">记下一件事</button></div><div id="memoryGardenList">${hint("先在本页选择一本记忆库，再查看真实回忆。")}</div>`);
    $("view-lovehouse")?.querySelector(".lovehouse-home")?.after($("memoryGardenPanel"));
    const notes={whitelist:[ART.connecting,"把聊天对象和操作权限安排妥当","主人、朋友和群聊的授权分别管理。"],plugins:[ART.awake,"把喜欢的本事，放进小家的百宝箱","先选择需要的能力，再打开各自设置。"],settings:[ART.quiet,"把小家调成你舒服的样子","模型与身份、日常陪伴、外观和备份各有位置。"]};
    Object.entries(notes).forEach(([view,[art,title,copy]])=>{
      const area=$("view-"+view);if(!area||$("pageNote-"+view))return;
      const node=document.createElement("div");node.id="pageNote-"+view;node.className="exp-page-note";node.innerHTML=`<img src="${art}" alt=""><div><b>${title}</b><p>${copy}</p></div>`;area.prepend(node);
    });
    document.addEventListener("click",event=>{
      const button=event.target.closest("[data-exp-action]");if(!button)return;
      const action=button.dataset.expAction;
      if(action==="companion")shell.openCompanion();
      else if(action==="health")run(button,"检查中…",loadHealth);
      else if(action==="tasks")run(button,"读取中…",loadTasks);
      else if(action==="backups")run(button,"读取中…",loadBackups);
      else if(action==="backup")run(button,"备份中…",async()=>{await post("/api/recovery/backup",{});await loadBackups();shell.toast("已保存一份可核对的本机备份。");});
      else if(["quiet","more","normal"].includes(action))run(button,"应用中…",()=>changeCompanion("/api/companion/temporary",action==="normal"?{mode:action}:{mode:action,until_end_of_day:true},action==="normal"?"已恢复平时节奏。":"今天的临时偏好已生效，到期会自动恢复。"));
      else if(action==="memories")run(button,"读取中…",loadMemories);
      else if(action==="memory-add")openMemoryEditor();
    });
    $("sidebarMascot")?.closest(".sidebar-art")?.addEventListener("click",openStateExplanation);
    $("memoryGardenQuery")?.addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();loadMemories().catch(error=>shell.toast(error.message));}});
    $("memoryGardenView")?.addEventListener("change",()=>loadMemories().catch(error=>shell.toast(error.message)));
    $("memoryGardenLibrary")?.addEventListener("change",async()=>{generation("memories");memoryReading=false;paintState();try{if(shell.selectLibrary)await shell.selectLibrary($("memoryGardenLibrary").value);else await loadMemories();}catch(error){shell.toast(error.message);}});
    window.addEventListener("pagehide",()=>{disposed=true;generations.clear();});
  }
  function profilesMarkup(data,attribute="data-exp-profile") {
    return Object.entries(data?.profiles||{}).map(([key,p])=>`<button class="exp-profile${data.profile===key?" selected":""}" type="button" ${attribute}="${e(key)}" aria-pressed="${data.profile===key}"><b>${e(p.label||key)}</b><span>${e(p.description||"")}</span></button>`).join("");
  }
  function renderDaily() {
    if(!$("dailyProfileOptions"))return;
    $("dailyProfileOptions").innerHTML=profilesMarkup(companion);
    $("dailyProfileOptions").querySelectorAll("[data-exp-profile]").forEach(button=>button.addEventListener("click",()=>run(button,"应用中…",async()=>{
      await changeCompanion("/api/companion/profile",{profile:button.dataset.expProfile},"已保存节奏并供后台热读取；状态最迟约1分钟刷新，能力开关保留你的选择。");
    })));
    const temp=companion?.temporary_preference;
    $("dailyPreferenceStatus").textContent=temp?.active?`${temp.label||temp.message||"临时节奏已生效"}${temp.expires_at?" · 至 "+date(temp.expires_at):""}`:companion?`当前：${companion.profiles?.[companion.profile]?.label||"自定义节奏"}。主动生成最多 ${companion.daily_limit} 次／天；实际发送由场景与运行状态决定。`:"暂时读不到陪伴偏好，可点击详细设置重试。";
    $("dailyProfileOptions").querySelectorAll("button").forEach(button=>{button.disabled=companionWriting||!!shell.getState()?.preview;});
    $("companionDailyPanel").querySelectorAll('[data-exp-action="quiet"],[data-exp-action="more"],[data-exp-action="normal"]').forEach(button=>{button.disabled=companionWriting||!companion?.proactive_enabled||!!shell.getState()?.preview;button.title=companion?.proactive_enabled?"改变临时偏好，不增加日预算":"先在陪伴设置中开启主动聊天";});
  }
  async function changeCompanion(url,body,message) {
    if(companionWriting)return;companionWriting=true;const g=generation("companion");renderDaily();
    try{const value=await post(url,body);if(!live("companion",g))return;companion=value;shell.toast(message);}
    finally{companionWriting=false;renderDaily();paintState();}
  }
  async function loadCompanion() {
    if(companionWriting)return;
    const g=generation("companion");try{const value=await shell.api("/api/companion");if(!live("companion",g))return;companion=value;renderDaily();paintState();}catch(error){if(live("companion",g)){$("dailyPreferenceStatus").textContent=error.message;}}
  }
  function navigateAction(action) {
    const modal={open_companion:shell.openCompanion,open_stickers:shell.openStickers,open_computer:shell.openComputer};
    if(modal[action.code]){shell.showView("plugins");modal[action.code]();return;}
    const pages={open_overview:"overview",open_logs:"logs",open_settings:"settings",open_plugins:"plugins",open_memory:"lovehouse"};
    if(pages[action.code])shell.showView(pages[action.code]);
  }
  async function loadHealth() {
    const g=generation("health"),reason=$("healthReason")?.value||"all";
    if($("healthResult"))$("healthResult").innerHTML=hint("正在读取本机状态…");
    const value=await shell.api("/api/health?reason="+encodeURIComponent(reason));if(!live("health",g))return;health=value;
    const labels={ok:"正常",attention:"需要留意",blocked:"尚不可用",unknown:"待确认"};
    $("healthResult").innerHTML=`<div class="row between wrap"><b>${e(value.summary||"体检完成")}</b><small>${date(value.checked_at)}</small></div><div class="exp-checks">${(value.checks||[]).map((check,index)=>`<article class="exp-check"><div class="row between"><h3>${e(check.label)}</h3><span class="badge ${check.status==="ok"?"green":check.status==="blocked"?"amber":"gray"}">${e(labels[check.status]||"待确认")}</span></div><p>${e(check.message)}</p>${check.evidence&&Object.keys(check.evidence).length?`<details><summary>查看实际依据</summary><dl>${Object.entries(check.evidence).map(([key,item])=>`<dt>${e(key)}</dt><dd>${e(typeof item==="object"?JSON.stringify(item):item)}</dd>`).join("")}</dl></details>`:""}<div class="row wrap">${(check.actions||[]).map((action,number)=>`<button type="button" class="text-button" data-health-action="${index}:${number}">${e(action.label)}</button>`).join("")}</div></article>`).join("")}</div>${hint("本次没有发送 QQ 测试消息或调用模型；连接接受也不代表对方已读。")}`;
    $("healthResult").querySelectorAll("[data-health-action]").forEach(button=>button.addEventListener("click",()=>{const [i,j]=button.dataset.healthAction.split(":").map(Number);const action=value.checks?.[i]?.actions?.[j];if(action)navigateAction(action);}));
    $("healthHomeResult").innerHTML=`<p>${e(value.summary||"体检完成")}</p><button class="text-button" data-exp-action="health-details">查看体检与修复指引 ↗</button>`;
    $("healthHomeResult").querySelector("button").onclick=()=>shell.showView("logs");paintState();
  }
  async function loadTasks() {
    const g=generation("tasks");try{
      const value=await shell.api("/api/tasks");if(!live("tasks",g))return;tasks=value;
      const rows=Array.from(new Map([...(value.active||[]),...(value.recent||[])].map(item=>[item.id,item])).values()).slice(0,30),labels={running:"正在进行",started:"开始处理",completed:"已完成",succeeded:"已完成",success:"已完成",failed:"未完成",interrupted:"已中断",unknown:"状态待确认",unconfirmed:"结果待确认"};
      $("taskList").innerHTML=rows.length?`<div class="exp-task-list">${rows.map((item,index)=>`<article class="exp-task"><span class="exp-task-dot ${item.status==="running"?"active":""}"></span><div><b>${e(item.label||item.action||"电脑操作")}</b><p>${e(item.summary||labels[item.status]||item.status)}</p><small>${date(item.started_at||item.at)}${item.ended_at?" — "+date(item.ended_at):""}</small>${item.artifact?`<div class="row wrap"><button class="text-button" data-task-artifact="${index}">打开作品 ↗</button><small>${e(item.artifact)}</small></div>`:""}</div><span class="badge ${["completed","success","succeeded"].includes(item.status)?"green":item.status==="failed"?"amber":"gray"}">${e(labels[item.status]||item.status)}</span></article>`).join("")}</div>`:hint(value.message||"暂时还没有电脑工具任务。你在 QQ 请她处理文件或操作桌面后，真实记录会出现在这里。");
      $("taskList").querySelectorAll("[data-task-artifact]").forEach(button=>button.onclick=()=>run(button,"打开中…",()=>post("/api/artifacts/open",{path:rows[Number(button.dataset.taskArtifact)].artifact})));paintState();
    }catch(error){if(live("tasks",g))$("taskList").innerHTML=hint(error.message,true);}
  }
  async function loadBackups() {
    const g=generation("backups");try{
      const value=await shell.api("/api/recovery");if(!live("backups",g))return;const rows=value.backups||value.items||[];
      $("recoveryList").innerHTML=rows.length?`<div class="exp-backups">${rows.map((item,index)=>`<article class="exp-backup"><div><b>${e(item.label||"小家备份")}</b><p>${date(item.created_at)} · ${e(item.source_version||item.version||"版本待确认")}</p><small>${e(item.summary||({manual:"手动备份",before_update:"安装更新前",before_restore:"恢复前的安全备份",before_rollback:"安装旧版前"}[item.reason])||item.reason||"")}</small></div><div class="row wrap"><button class="ghost" data-backup-restore="${index}" ${item.compatible===false?"disabled":""}>恢复这份备份</button>${item.source_version&&item.source_version!==value.current_version?`<button class="text-button" data-backup-download="${index}">下载匹配旧版</button>`:""}</div></article>`).join("")}</div>`:hint("还没有备份。可先手动保存一份；选择安装软件更新前也会自动备份。");
      $("recoveryList").querySelectorAll("[data-backup-restore]").forEach(button=>button.onclick=()=>confirmRestore(rows[Number(button.dataset.backupRestore)]));
      $("recoveryList").querySelectorAll("[data-backup-download]").forEach(button=>button.onclick=()=>confirmRollbackDownload(rows[Number(button.dataset.backupDownload)]));
    }catch(error){if(live("backups",g))$("recoveryList").innerHTML=hint(error.message,true);}
  }
  function confirmRestore(item) {
    shell.openModal("恢复前，再核对一下",`<p>将恢复 ${e(date(item.created_at))} 的允许恢复设置与随包插件。</p><p>QQ 登录、模型密钥、记忆和自行安装的插件继续保留。恢复前还会备份当前状态。</p><div class="notice">请先在首页一键关闭聊天服务。跨软件版本的插件备份需要先安装匹配版本。</div><label class="exp-confirm"><input type="checkbox" id="expRestoreConfirm">我已核对备份，并同意恢复所列内容</label>`,async()=>{if(!$("expRestoreConfirm")?.checked)throw new Error("请先核对并确认恢复范围。");await post("/api/recovery/restore",{backup_id:item.id||item.backup_id,confirmed:true});await loadBackups();shell.toast("已恢复允许恢复的设置与随包插件。");});$("modalOk").textContent="确认恢复";
  }
  function confirmRollbackDownload(item) {
    shell.openModal("下载匹配的旧版安装程序",`<p>下载与这份备份匹配的 ${e(item.source_version||item.version)} 官方发布安装程序，并检查校验值。</p><p>下载完成后仍由你选择安装；安装匹配版本后，再恢复对应备份。</p>`,async()=>{await post("/api/recovery/rollback",{backup_id:item.id||item.backup_id,confirmed:true});shell.toast("已开始准备匹配旧版，请在软件更新区查看下载状态。");shell.showView("settings");});$("modalOk").textContent="下载并校验";
  }
  function choosePose() {
    const s=shell?.getState();if(!s)return {key:"connecting",label:"正在连接小家",description:"正在读取本机控制台。"};
    const active=(tasks?.active||[]).filter(item=>["running","started"].includes(item.status));
    if(!shell.isConnected())return {key:"repair",label:"小家连接中断",description:"控制台暂时连不上本机服务，可到体检页查看。"};
    if(active.length)return {key:"working",label:"正在认真做事",description:active[0].label||"电脑工具正在执行实际任务。"};
    if(memoryReading)return {key:"remembering",label:"正在翻看共同回忆",description:"正在读取所选爱之屋记忆库。"};
    const a=s.status?.astrbot?.up,n=s.status?.napcat?.up;
    const starting=$("btnStart")?.dataset.busy==="true"||$("btnRestart")?.dataset.busy==="true";
    if(starting||((a||n)&&!(a&&n&&s.napcat?.logged_in)))return {key:"connecting",label:"正在和 QQ 连上线",description:"服务状态与 QQ 登录会分别核对；需要时可在首页扫码。"};
    if(!(a||n))return {key:"resting",label:"服务已停止 · 在休息",description:"首页一键开启后，才能在 QQ 聊天；浏览设置与本机回忆仍然可以。"};
    if(companion?.runtime?.code==="generating")return {key:"working",label:"正在想一句问候",description:"本机主动机会已通过检查，当前还没有确认发送。"};
    if(companion?.runtime?.code==="quiet_hours"||companion?.temporary_preference?.active&&companion.temporary_preference.mode==="quiet")return {key:"quiet",label:"安静地陪着你",description:companion.temporary_preference?.message||"当前免打扰安排生效，不主动催你回应。"};
    return {key:"awake",label:"QQ 在线 · 在这里陪你",description:"对话服务和 QQ 已连接。具体能力是否可用，可以查看小家体检。"};
  }
  function paintState() {
    if(!shell)return;currentPose=choosePose();document.body.dataset.mascotState=currentPose.key;
    const image=$("sidebarMascot");if(image){image.src=ART[currentPose.key]||ART.awake;image.alt=currentPose.label;image.style.display="";}
    if($("sidebarMascotCaption"))$("sidebarMascotCaption").textContent=currentPose.label;
    const button=$("sidebarMascot")?.closest(".sidebar-art");if(button){button.title=currentPose.description;button.setAttribute("aria-label",currentPose.label+"，查看状态说明");}
    if(["working","connecting","repair","remembering","quiet"].includes(currentPose.key)&&$("heroMascot"))$("heroMascot").src=ART[currentPose.key];
  }
  function openStateExplanation() {
    shell.openModal(currentPose.label,`<div class="exp-state-detail"><img src="${ART[currentPose.key]}" alt=""><p>${e(currentPose.description)}</p></div><p class="exp-hint">造型只表达已经观察到的状态；情绪陪伴属于本地模拟状态。</p>`,null);$("modalOk").classList.add("hidden");$("modalCancel").textContent="知道了";
  }
  async function loadView(view) {
    currentView=view;if(view==="overview"||view==="plugins")await loadCompanion();
    if(view==="artifacts")await loadTasks();if(view==="settings")await loadBackups();if(view==="lovehouse")await loadMemories();paintState();
  }
  function renderMemories() {
    const host=$("memoryGardenList"),rows=memoryRows;
    const layers={fact:"事实",preference:"偏好",commitment:"待跟进约定",episode:"共同经历",historical_dialogue:"历史对话",atom:"记忆片段"};
    host.innerHTML=`${hint(`已加载 ${memoryRows.length} / ${memoryTotal} 条匹配记录，由新到旧分页；“使用中”表示未手动停用，不表示本轮一定召回。`)}<div class="memory-garden-grid">${rows.map(row=>`<article class="memory-garden-entry${row.enabled===false?" disabled":""}"><span class="badge ${row.important?"amber":"gray"}">${e(row.enabled===false?"暂不使用":row.important?"重要回忆":layers[row.kind||row.layer]||row.kind||row.layer||"回忆")}</span><h3>${e(row.title||"未命名回忆")}</h3><small>${date(row.recorded_at)}</small><p>${e(row.confirmed||row.epistemic_status==="user_confirmed_statement"?"由你确认的内容":"来自这本爱之屋记忆库；可打开查看正文与来源。")}</p><div class="row wrap"><button class="text-button" data-memory-read="${e(row.id)}">查看与管理 ↗</button></div></article>`).join("")}</div>${!rows.length?hint("暂时没有符合筛选的记录。可以清除筛选、切换范围，或记下一件真实的小事。"):""}${memoryCursor?'<button type="button" class="ghost" id="memoryGardenMore">加载下一页</button>':""}`;
    host.querySelectorAll("[data-memory-read]").forEach(button=>button.onclick=()=>run(button,"读取中…",()=>openMemoryEditor(memoryRows.find(row=>row.id===button.dataset.memoryRead))));
    $("memoryGardenMore")?.addEventListener("click",event=>run(event.currentTarget,"读取中…",()=>loadMemories(true)));
  }
  async function loadMemories(append=false) {
    const host=$("memoryGardenList"),library=shell.getLibrary();if(!host)return;
    if(!library){host.innerHTML=hint("先在本页选择一本记忆库，再查看真实回忆。");return;}
    if($("memoryGardenLibrary") && shell.getLibraries){$("memoryGardenLibrary").innerHTML=shell.getLibraries().filter(row=>!row.is_demo).map(row=>`<option value="${e(row.id)}">${e(row.name)}</option>`).join("");$("memoryGardenLibrary").value=library;}
    const g=generation("memories");memoryReading=true;paintState();
    if(!append||memoryLibrary!==library){memoryRows=[];memoryCursor=null;memoryLibrary=library;host.innerHTML=hint("正在读取这本爱之屋的真实目录…");}
    try{
      const value=await post("/api/memories/catalog",{library_id:library,limit:6,view:$("memoryGardenView")?.value||"all",query:($("memoryGardenQuery")?.value||"").trim(),...(append&&memoryCursor?{cursor:memoryCursor}:{})});
      if(!live("memories",g)||shell.getLibrary()!==library)return;
      if(append&&memoryRevision&&value.revision!==memoryRevision)throw new Error("记忆目录发生变化，请刷新后重新查看。");
      memoryRows=Array.from(new Map([...memoryRows,...(value.entries||[])].map(row=>[row.id,row])).values());memoryRevision=value.revision;memoryCursor=value.next_cursor;memoryTotal=value.total??memoryRows.length;renderMemories();
    }catch(error){if(live("memories",g)&&shell.getLibrary()===library)host.innerHTML=hint(error.message,true);}
    finally{if(live("memories",g)){memoryReading=false;paintState();}}
  }
  async function openMemoryEditor(entry=null) {
    const library=shell.getLibrary();if(!library){shell.toast("先选择一本爱之屋记忆库。");return;}
    if(!entry){
      shell.openModal("记下一件真实的小事",`<div class="memory-editor"><label class="field"><span>想记住什么</span><textarea id="expMemoryText" maxlength="40000" placeholder="例如：我喜欢安静的早晨；周五面试后想和大肥鱼聊聊…"></textarea></label><label class="field"><span>这是一条</span><select id="expMemoryKind"><option value="fact">事实</option><option value="preference">偏好</option><option value="commitment">待跟进的约定</option></select></label><label class="small"><input id="expMemoryImportant" type="checkbox" checked> 标记为重要</label><p class="exp-hint">存入当前选中的爱之屋记忆库，来源标记为你亲自确认。约定会作为可召回内容，不保证在某个时刻发送。</p></div>`,async()=>{
        if(shell.getLibrary()!==library)throw new Error("选择的记忆库已变化，请重新打开。");
        await post("/api/memories/add",{library_id:library,text:$("expMemoryText").value.trim(),kind:$("expMemoryKind").value,important:$("expMemoryImportant").checked});await loadMemories();shell.toast("这件小事已经交给爱之屋保存。");
      });$("modalOk").textContent="保存到这本记忆库";return;
    }
    const ticket=shell.getModalGeneration?.(),g=generation("memory-detail"),value=await post("/api/memories/read",{library_id:library,id:entry.id,revision:memoryRevision});
    if(!live("memory-detail",g)||shell.getLibrary()!==library||shell.getModalGeneration?.()!==ticket)return;
    const record=value.record||{},title=record.display?.title||entry.title||"共同回忆",sources=record.sources||record.evidence||record.source||[];
    shell.openModal("回忆的正文与来处",`<div class="memory-editor"><label class="field"><span>标题</span><input id="expMemoryLabel" maxlength="100" value="${e(title)}"></label><label class="field"><span>回忆正文${value.complete===false?"（仅展示前一部分）":""}</span><textarea id="expMemoryText" maxlength="40000" ${value.complete===false?"readonly":""}>${e(value.text||"")}</textarea></label><p class="exp-hint">${value.complete===false?"正文较长；为避免覆盖未显示的内容，此处仅查看。请在爱之屋中完整更正。":"修改正文后点击保存更正，爱之屋会保留更正关系。"}</p><details><summary>原记录与来源</summary><p class="exp-hint">记录：${e(entry.id)} · ${e(record.epistemic_status||entry.epistemic_status||"来源待确认")}</p><pre class="exp-source-record">${e(JSON.stringify(sources,null,2))}</pre></details><div class="row wrap"><button class="soft" type="button" id="expMemoryToggle">${entry.enabled===false?"重新启用这条回忆":"暂不使用这条回忆"}</button><button class="soft" type="button" id="expMemoryMark">${entry.important?"取消重要标记":"标记重要"}</button><button class="text-button danger" type="button" id="expMemoryForget">预览永久忘记范围</button></div></div>`,async()=>{
      if(shell.getLibrary()!==library)throw new Error("选择的记忆库已变化，请重新打开。");
      if(value.complete===false)throw new Error("请在爱之屋中更正完整原文。");
      await post("/api/memories/mutate",{library_id:library,id:entry.id,action:"correct",expected_revision:value.revision,text:$("expMemoryText").value.trim(),label:$("expMemoryLabel").value.trim()});await loadMemories();shell.toast("更正已经保存，旧内容不会继续作为当前事实。");
    });$("modalOk").textContent="保存更正";$("modalOk").disabled=value.complete===false;
    $("expMemoryToggle").onclick=event=>run(event.currentTarget,"保存中…",async()=>{if(shell.getLibrary()!==library)throw new Error("选择的记忆库已变化，请重新打开。");const active=shell.getModalGeneration?.();await post("/api/memories/mutate",{library_id:library,id:entry.id,action:"enabled",enabled:entry.enabled===false,expected_revision:value.revision});if(shell.getModalGeneration?.()!==active)return;shell.closeModal();await loadMemories();shell.toast("已更新记忆使用状态。");});
    $("expMemoryMark").onclick=event=>run(event.currentTarget,"保存中…",async()=>{if(shell.getLibrary()!==library)throw new Error("选择的记忆库已变化，请重新打开。");const active=shell.getModalGeneration?.();await post("/api/memories/mutate",{library_id:library,id:entry.id,action:"important",important:!entry.important,expected_revision:value.revision});if(shell.getModalGeneration?.()!==active)return;shell.closeModal();await loadMemories();shell.toast("已更新重要标记。");});
    $("expMemoryForget").onclick=event=>run(event.currentTarget,"读取范围…",async()=>{
      if(shell.getLibrary()!==library)throw new Error("选择的记忆库已变化，请重新打开。");const active=shell.getModalGeneration?.();
      const preview=await post("/api/memories/mutate",{library_id:library,id:entry.id,action:"forget-preview"});if(shell.getLibrary()!==library||shell.getModalGeneration?.()!==active)return;
      const plan=preview.preview||{};
      shell.openModal("永久忘记前，请核对影响范围",`<p>这次会处理 <b>${e(plan.node_count??0)}</b> 条记忆、<b>${e(plan.archive_count??0)}</b> 份原始档案、<b>${e(plan.copied_archive_count??0)}</b> 份相关复制记录，以及本库 <b>${e(plan.backup_count??0)}</b> 份旧备份。</p><div class="notice">${e(plan.note||"此操作无法通过普通恢复功能撤销。")}</div><label class="exp-confirm"><input type="checkbox" id="expForgetConfirmed">我已核对范围，并确认永久忘记这些内容</label>`,async()=>{
        if(shell.getLibrary()!==library)throw new Error("选择的记忆库已变化，请重新预览。");
        if(!$("expForgetConfirmed")?.checked)throw new Error("请先核对影响范围并明确确认。");
        await post("/api/memories/mutate",{library_id:library,id:entry.id,action:"forget",preview_token:preview.preview_token,confirm:true});await loadMemories();shell.toast("爱之屋已完成确认范围内的忘记操作。");
      });$("modalOk").textContent="确认永久忘记";$("modalOk").classList.add("danger");
    });
  }
  window.AiwuExperience={init,loadView,paintState,loadHealth,loadTasks,loadBackups,loadMemories,profilesMarkup,renderDaily,loadCompanion,
    tick:()=>{loadCompanion();loadTasks();},
    setCompanion:value=>{companion=value;renderDaily();paintState();}};
})();
