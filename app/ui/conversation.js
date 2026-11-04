/* Conversation controls. No test message, account write or search on page load. */
(() => {
  "use strict";
  const $=id=>document.getElementById(id);
  let shell,personaState=null,turnState=null,personasGeneration=0,turnGeneration=0,lifeGeneration=0,personaWriting=false,turnWriting=false;
  const turnEdits={turn:0,group:0},turnDirty={turn:false,group:false};
  const timingModes={adaptive:{name:"自然动态",tag:"推荐",description:"根据话有没有说完、最近的发送间隔与人格，自然调整等待和回应方式。"},fixed:{name:"固定等待",tag:"旧版节奏",description:"每条新消息重新计时，等到固定秒数再一起回应；保留原来的 10 秒模式。"},instant:{name:"即时回应",tag:"少等一会儿",description:"不额外等待收集；消息到达后进入处理，模型生成与发送仍需要时间。"}};
  const groupModes={auto:{name:"自然加入",tag:"推荐",description:"叫到她时回应，也跟进刚刚正在进行的对话；群里热闹时少插话。"},addressed:{name:"只在点名时",tag:"有叫有回应",description:"实际 @、引用她的消息，或明确叫她的名字时才回应。"},quiet:{name:"安静旁听",tag:"只认 @ / 引用",description:"只回应实际 @ 或引用她消息的内容；单独提到名字不会唤醒。"},light:{name:"旧版轻度参与",tag:"高级兼容",description:"保留原来的相关消息概率与频率上限；推荐日常使用“自然加入”。"}};
  const personaRhythms={natural:{name:"自然朋友",hint:"松弛地接话，完整问题认真答，随口聊天轻一点。"},classic:{name:"经典肥鱼",hint:"保留一点小脾气和俏皮，接话有停顿，重要内容说清楚。"},gentle:{name:"温柔陪伴",hint:"更愿意听你补充，温柔接住，少抢话、少追问。"},playful:{name:"元气搭子",hint:"日常接梗更轻快，认真话题与急事仍优先说重点。"},study:{name:"冷静学伴",hint:"完整问题更利落，解释与任务结果完整表达。"}};
  const e=value=>shell.esc(String(value??""));
  const post=(path,body={})=>shell.api(path,{method:"POST",body:JSON.stringify(body)});
  const cardArt=value=>/^\/assets\/plugins\/astrbot_plugin_dafeiyu_stickers\/pack\/(?:happy|sad|angry|shy|sleepy|love)\.png$/.test(value||"")?value:"";
  const notice=(id,message)=>{if($(id))$(id).textContent=message;};
  async function busy(button,label,action){
    if(!button||button.disabled)return;const before=button.textContent;button.disabled=true;button.textContent=label;
    try{await action();}catch(error){shell.toast(error.message||"操作未完成，请重试。");}
    finally{if(button.isConnected){button.disabled=false;button.textContent=before;}}
  }
  function section(id,html){const node=document.createElement("section");node.id=id;node.className="experience-panel conversation-panel";node.innerHTML=html;return node;}
  function modeButtons(modes,attribute){return Object.entries(modes).map(([key,value])=>`<button type="button" class="conversation-mode" ${attribute}="${key}" aria-pressed="false"><span class="conversation-mode-tag">${e(value.tag)}</span><b>${e(value.name)}</b><span>${e(value.description)}</span></button>`).join("");}
  function markTurnDirty(scope){++turnGeneration;++turnEdits[scope];turnDirty[scope]=true;notice(scope==="turn"?"turnNotice":"groupNotice","有未保存的修改，保存后才会应用。");paintModes();}
  function paintModes(){
    const timing=timingModes[$("turnTimingMode")?.value],group=groupModes[$("groupReplyMode")?.value];if(!timing||!group)return;
    document.querySelectorAll("[data-timing-mode]").forEach(button=>{const selected=button.dataset.timingMode===$("turnTimingMode").value;button.setAttribute("aria-pressed",String(selected));button.classList.toggle("selected",selected);});
    document.querySelectorAll("[data-group-mode]").forEach(button=>{const selected=button.dataset.groupMode===$("groupReplyMode").value;button.setAttribute("aria-pressed",String(selected));button.classList.toggle("selected",selected);});
    notice("turnModeHelp",timing.description);notice("groupModeHelp",group.description);
    $("turnFixedFields").classList.toggle("hidden",$("turnTimingMode").value!=="fixed");
    $("turnAdaptiveExamples").classList.toggle("hidden",$("turnTimingMode").value!=="adaptive");
    $("groupLegacyFields").classList.toggle("hidden",$("groupReplyMode").value!=="light");
    $("turnPausedNotice").classList.toggle("hidden",$("turnCollectEnabled").checked);
    const key=turnState?.persona_profile||turnState?.rhythm?.persona_profile,profile=Object.hasOwn(personaRhythms,key)?personaRhythms[key]:null;
    notice("turnPersonaRhythm",profile?`当前节奏人格：${profile.name} · ${profile.hint}`:"人格节奏会随已应用的人格卡调整；自定义人格使用自然朋友节奏。");
  }
  function paintRhythm(value){
    const rhythm=value.rhythm||{},rows=Array.isArray(rhythm.recent)?rhythm.recent.filter(row=>row&&typeof row==="object"&&!Array.isArray(row)).slice(-8).reverse():[];
    notice("turnRuntimeNotice",rhythm.worker_running===true?"已读取到插件在线心跳。是否回应仍由真实会话权限与当前规则决定。":"尚未读取到有效的节奏心跳；这里展示已保存设置，不能据此确认当前会话已生效。");
    $("turnRecentDecisions").innerHTML=rows.length?`<ol class="conversation-decisions">${rows.map(row=>{
      const when=typeof row.at==="number"&&Number.isFinite(row.at)&&row.at>0&&row.at<4102444800?new Date(row.at*1000).toLocaleTimeString("zh-CN",{timeZone:"Asia/Shanghai",hour:"2-digit",minute:"2-digit",second:"2-digit"}):"时间未记录";
      const wait=typeof row.wait_seconds==="number"&&Number.isFinite(row.wait_seconds)&&row.wait_seconds>=0&&row.wait_seconds<=120?`等待 ${Number(row.wait_seconds.toFixed(1))} 秒`:"等待未记录";
      const profile=Object.hasOwn(personaRhythms,row.profile)?personaRhythms[row.profile]:null,scope=row.group===true?"群聊":row.group===false?"私聊":"对话";
      return `<li><div><time>${e(when)}</time><span>${e(scope)}${profile?` · ${e(profile.name)}`:""}</span><b>${e(wait)}</b></div><p>${e(String(row.reason_label||row.reason||"尚无可读的决策说明").slice(0,240))}</p></li>`;
    }).join("")}</ol>`:'<p class="exp-hint">还没有近期决策记录。收到获准处理的新消息后，这里会显示原因和等待，不显示聊天正文。</p>';
  }
  function init(value){
    shell=value;
    if($("personaCardsPanel"))return;
    const panel=section("personaCardsPanel",`<div class="exp-heading"><img src="/assets/ui/states/dafeiyu-remembering-v140.png" alt=""><div><small>同一条小鱼，也有不同的相处方式</small><h2>人格预设</h2><p>五张完整人格卡，先预览再应用。切换前保留原人格备份。</p></div></div><div class="row between wrap"><p id="personaCurrent" class="exp-hint" role="status">打开此页后读取运行人格。</p><button type="button" class="text-button" id="personaRefresh">刷新人格</button></div><div id="personaCardList" class="persona-card-grid"></div><details class="conversation-advanced"><summary>恢复原人格与来源说明</summary><p class="exp-hint">切换保留聊天、记忆、工具选择和独立权限。旧聊天中的措辞不会被删除；完整预设也不能代替模型实际能力。</p><div id="personaBackupList"></div></details>`);
    $("settingsIdentity")?.after(panel);
    const turn=section("conversationTurnPanel",`
      <div class="exp-heading"><img src="/assets/ui/states/dafeiyu-connecting-v140.png" alt=""><div><small>等你把话说完，再好好接住</small><h2>对话节奏</h2><p>选一种相处节奏就好，细节交给小鱼。</p></div></div>
      <input type="hidden" id="turnTimingMode" value="adaptive">
      <div class="conversation-mode-grid" role="group" aria-label="选择对话节奏">${modeButtons(timingModes,"data-timing-mode")}</div>
      <p id="turnModeHelp" class="conversation-mode-help"></p><p id="turnPersonaRhythm" class="conversation-persona-note"></p>
      <div id="turnPausedNotice" class="notice">当前已暂停收集连续消息。可在高级设置中重新开启；选择节奏不会替你解除暂停。</div>
      <div id="turnAdaptiveExamples" class="conversation-examples" aria-label="自然动态如何接话">
        <div><b>轻松闲聊</b><span>自然停顿，轻松接话，不抢着长篇解释。</span></div>
        <div><b>问题已经完整</b><span>少等一点，认真接住你的问题。</span></div>
        <div><b>你还在继续补充</b><span>参考最近发消息的间隔，多留一点说话空间。</span></div>
        <div><b>有急事要说</b><span>缩短等待，先回应眼前重点。</span></div>
      </div>
      <details class="conversation-advanced" id="turnAdvanced"><summary>高级：固定秒数、连续消息与发送方式</summary>
        <label class="perm-row"><input type="checkbox" id="turnCollectEnabled" checked><div><b>收集连续短消息</b><span class="small">关闭后暂停等待收集；即时回应模式也不额外等待。最长等待与容量上限始终有边界。</span></div></label>
        <div id="turnFixedFields" class="companion-form-grid hidden"><label class="field"><span>最后一条后固定等待（秒）</span><input id="turnWaitSeconds" type="number" min="1" max="30" value="10"></label></div>
        <div class="companion-form-grid"><label class="field"><span>连续输入最长等待（秒）</span><input id="turnMaxSeconds" type="number" min="5" max="120" value="30"></label><label class="field"><span>单批最多消息数</span><input id="turnMaxMessages" type="number" min="2" max="30" value="12"></label><label class="field"><span>单批最多文字数</span><input id="turnMaxChars" type="number" min="500" max="20000" value="6000"></label></div>
        <label class="perm-row"><input type="checkbox" id="turnBubblesEnabled"><div><b>合适时发成几条完整短句</b><span class="small">关闭后发成一条，仍可按场景调整语气。正式说明、代码与任务结果保持完整。</span></div></label>
        <p class="exp-hint">群里按发言人分别收集，不把两个人的话合成一个人的输入。命令及需要立即处理的输入按规则绕过等待。</p>
        <button class="text-button" type="button" id="turnGoGroups">去设置群聊回应 ↗</button>
      </details>
      <div class="row wrap"><button class="soft" type="button" id="turnSave" disabled>保存对话节奏</button><button class="text-button" type="button" id="turnRefresh">刷新</button></div><p id="turnNotice" class="exp-hint" role="status">正在等待读取已保存设置。</p>
      <details class="conversation-advanced" id="turnDecisionDetails"><summary>最近为什么这样接话</summary><p id="turnRuntimeNotice" class="exp-hint"></p><div id="turnRecentDecisions"></div></details>`);
    panel.after(turn);
    const groups=section("groupRulesPanel",`
      <div class="exp-heading"><img src="/assets/ui/states/dafeiyu-quiet-v140.png" alt=""><div><small>给群里的每个人留一点空间</small><h2>群聊怎么接话</h2><p>三种实用模式，选好就能用，无需先调概率和冷却。</p></div></div>
      <input type="hidden" id="groupReplyMode" value="auto">
      <div class="conversation-mode-grid" role="group" aria-label="选择群聊回应方式">${modeButtons(Object.fromEntries(Object.entries(groupModes).filter(([key])=>key!=="light")),"data-group-mode")}</div>
      <p id="groupModeHelp" class="conversation-mode-help"></p><p class="exp-hint">仅在聊天名单允许的群里生效。不同成员分别接话；群聊模式不会扩大电脑操作或账号修改权限。</p>
      <details class="conversation-advanced" id="groupAdvanced"><summary>高级：唤醒名字与旧版规则</summary>
        <label class="field"><span>明确叫她的名字（逗号分隔）</span><input id="groupWakeWords" maxlength="200" placeholder="大肥鱼,小鱼"></label><p class="exp-hint">自然加入、只在点名时会识别这些名字；安静旁听只认实际 @ 或引用她。</p>
        <button type="button" class="text-button conversation-legacy-mode" data-group-mode="light" aria-pressed="false">使用旧版轻度参与规则</button>
        <div id="groupLegacyFields" class="hidden"><div class="companion-form-grid"><label class="field"><span>两次轻度参与间隔（秒）</span><input id="groupCooldown" type="number" min="0" max="3600" value="90"></label><label class="field"><span>每小时轻度参与上限</span><input id="groupHourlyLimit" type="number" min="0" max="60" value="6"></label><label class="field"><span>相关消息参与机会（%）</span><input id="groupProbability" type="number" min="0" max="20" value="3"></label></div><p class="exp-hint">只用于兼容旧版。0 次表示不轻度参与；明确叫到仍可回应。这些数值不会影响上方三种日常模式。</p></div>
      </details>
      <div class="row wrap"><button class="soft" type="button" id="groupSave" disabled>保存群聊方式</button><button class="text-button" type="button" id="groupRefresh">刷新</button></div><p id="groupNotice" class="exp-hint" role="status">正在等待读取已保存设置。</p>`);
    $("view-whitelist")?.querySelector(".permission-summary")?.after(groups);
    const nav=$("view-settings")?.querySelector(".settings-section-nav");
    for(const [label,target] of [["人格预设","personaCardsPanel"],["对话节奏","conversationTurnPanel"]]){if(!nav)continue;const b=document.createElement("button");b.type="button";b.textContent=label;b.onclick=()=>$(target)?.scrollIntoView({behavior:"smooth",block:"start"});nav.appendChild(b);}
    $("personaRefresh").onclick=event=>busy(event.currentTarget,"读取中…",loadPersonas);
    $("turnGoGroups").onclick=()=>shell.showView("whitelist");
    ["turnRefresh","groupRefresh"].forEach(id=>$(id).onclick=event=>busy(event.currentTarget,"读取中…",loadTurns));
    $("turnSave").onclick=event=>busy(event.currentTarget,"保存中…",async()=>{
      const mode=$("turnTimingMode").value;if(!Object.hasOwn(timingModes,mode))throw new Error("请选择一种对话节奏。");
      const body={reply_timing_mode:mode,message_collection_enabled:$("turnCollectEnabled").checked,collection_max_wait_seconds:readInteger("turnMaxSeconds",5,120,"最长等待"),collection_max_messages:readInteger("turnMaxMessages",2,30,"单批消息数"),collection_max_chars:readInteger("turnMaxChars",500,20000,"单批文字数"),natural_bubbles_enabled:$("turnBubblesEnabled").checked};
      if(mode==="fixed")body.collection_seconds=readInteger("turnWaitSeconds",1,30,"固定等待");
      await saveTurns(body,"turn");
    });
    $("groupSave").onclick=event=>busy(event.currentTarget,"保存中…",async()=>{
      const mode=$("groupReplyMode").value;if(!Object.hasOwn(groupModes,mode))throw new Error("请选择一种群聊方式。");
      const body={group_reply_mode:mode,group_wake_words:$("groupWakeWords").value};
      if(mode==="light")Object.assign(body,{group_cooldown_seconds:readInteger("groupCooldown",0,3600,"轻度参与间隔"),group_hourly_limit:readInteger("groupHourlyLimit",0,60,"每小时参与上限"),group_participation_probability:readInteger("groupProbability",0,20,"参与机会")});
      await saveTurns(body,"group");
    });
    for(const [scope,selector] of [["turn","#conversationTurnPanel input"],["group","#groupRulesPanel input"]])document.querySelectorAll(selector).forEach(node=>{if(node.type!=="hidden")for(const event of ["input","change"])node.addEventListener(event,()=>markTurnDirty(scope));});
    document.querySelectorAll("[data-timing-mode]").forEach(button=>button.onclick=()=>{$("turnTimingMode").value=button.dataset.timingMode;markTurnDirty("turn");});
    document.querySelectorAll("[data-group-mode]").forEach(button=>button.onclick=()=>{$("groupReplyMode").value=button.dataset.groupMode;markTurnDirty("group");});
    paintModes();paintRhythm({});
  }
  function readInteger(id,min,max,label){const raw=$(id).value,value=Number(raw);if(String(raw).trim()===""||!Number.isInteger(value)||value<min||value>max)throw new Error(`${label}请填写 ${min}–${max} 范围内的整数。`);return value;}
  async function loadTurns(){
    const ticket=++turnGeneration,value=await shell.api("/api/companion");if(ticket!==turnGeneration)return;turnState=value;
    const fields={turn:{turnWaitSeconds:"collection_seconds",turnMaxSeconds:"collection_max_wait_seconds",turnMaxMessages:"collection_max_messages",turnMaxChars:"collection_max_chars"},group:{groupWakeWords:"group_wake_words",groupCooldown:"group_cooldown_seconds",groupHourlyLimit:"group_hourly_limit",groupProbability:"group_participation_probability"}};
    for(const scope of ["turn","group"]){if(turnDirty[scope])continue;for(const [id,key] of Object.entries(fields[scope]))if(value[key]!==undefined)$(id).value=value[key];}
    if(!turnDirty.turn){$("turnTimingMode").value=Object.hasOwn(timingModes,value.reply_timing_mode)?value.reply_timing_mode:"adaptive";$("turnCollectEnabled").checked=value.message_collection_enabled!==false;$("turnBubblesEnabled").checked=value.natural_bubbles_enabled!==false;}
    if(!turnDirty.group)$("groupReplyMode").value=Object.hasOwn(groupModes,value.group_reply_mode)?value.group_reply_mode:"auto";
    paintModes();paintRhythm(value);
    if(!turnDirty.turn)notice("turnNotice",!value.plugin_installed?"完成运行环境安装后部署对话节奏能力。":!value.plugin_enabled?"设置已保存；需要在插件页启用“对话节奏与陪伴”。":value.message_collection_enabled===false?"当前已暂停收集；已保留所选模式。":`已保存“${timingModes[$("turnTimingMode").value].name}”。运行插件会热读取；实际在线情况见下方近期决策。`);
    if(!turnDirty.group)notice("groupNotice",!value.plugin_enabled?"规则已保存；请先启用“对话节奏与陪伴”插件。":`已保存“${groupModes[$("groupReplyMode").value].name}”；仍遵守聊天名单与会话权限。`);
    if(!turnWriting)for(const id of ["turnSave","groupSave"])$(id).disabled=false;
  }
  async function saveTurns(body,scope){
    if(!turnState)throw new Error("请先读取已保存设置，再保存修改。");if(turnWriting)throw new Error("正在保存对话设置，请稍后再试。");
    const revision=turnEdits[scope],target=scope==="turn"?"turnNotice":"groupNotice";turnWriting=true;++turnGeneration;for(const id of ["turnSave","groupSave"])$(id).disabled=true;
    try{await post("/api/companion",body);if(turnEdits[scope]===revision)turnDirty[scope]=false;try{await loadTurns();}catch{notice(target,turnDirty[scope]?"本次提交已保存；你随后编辑的内容尚未保存，暂时无法刷新运行状态。":"设置已保存；暂时无法刷新运行状态，可以稍后再刷新。");shell.toast("设置已保存；暂时无法刷新运行状态。");return;}notice(target,turnDirty[scope]?"本次提交已保存；你随后编辑的内容尚未保存。":"已保存，运行插件会热读取新规则。");shell.toast("对话规则已保存。");}
    finally{turnWriting=false;for(const id of ["turnSave","groupSave"])$(id).disabled=!turnState;}
  }
  async function loadPersonas(){
    if(personaWriting)return;const ticket=++personasGeneration,value=await shell.api("/api/personas");if(ticket!==personasGeneration||personaWriting)return;personaState=value;
    notice("personaCurrent",`当前：${value.active_name||"尚未确认"}。${value.notice||""}`);
    $("personaCardList").innerHTML=(value.cards||[]).map((card,index)=>`<article class="persona-card${value.active_card_id===card.id?" selected":""}"><img loading="lazy" src="${e(cardArt(card.art))}" alt="${e(card.name)}造型"><div><h3>${e(card.name)}</h3><p>${e(card.subtitle)}</p><div class="persona-tags">${(card.tags||[]).map(t=>`<span>${e(t)}</span>`).join("")}</div><small>${e(card.source_kind||"完整预设")}</small></div><button type="button" class="${value.active_card_id===card.id?"soft":"ghost"}" data-persona-preview="${index}">${value.active_card_id===card.id?"查看当前卡片":"预览这张人格"}</button></article>`).join("");
    $("personaCardList").querySelectorAll("[data-persona-preview]").forEach(b=>b.onclick=()=>busy(b,"读取中…",()=>previewPersona(value.cards[Number(b.dataset.personaPreview)])));
    $("personaBackupList").innerHTML=(value.backups||[]).length?value.backups.map((r,i)=>`<div class="row between wrap"><span>${e(new Date(Number(r.created_at)*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai"}))} · ${e(r.card_before||"原人格")}</span><button type="button" class="text-button" data-persona-restore="${i}">恢复这份原人格</button></div>`).join(""):'<p class="exp-hint">尚未切换过运行人格。</p>';
    $("personaBackupList").querySelectorAll("[data-persona-restore]").forEach(b=>b.onclick=()=>restorePersona(value.backups[Number(b.dataset.personaRestore)]));
  }
  async function previewPersona(card){
    if(!card)return;const before=shell.getModalGeneration(),value=await post("/api/personas/preview",{card_id:card.id});if(before!==shell.getModalGeneration())return;
    const state=personaState,revision=state?.revision??null;
    shell.openModal(card.name+" · 完整人格卡",`<div class="persona-preview"><div class="exp-heading"><img src="${e(cardArt(card.art))}" alt=""><div><b>${e(card.subtitle)}</b><p>${e(card.author)}</p><small>${e(card.license)}</small></div></div><p>应用前备份当前人格；保留原有工具选择、聊天和记忆。旧会话的用词可能仍会影响后续表达。</p><label class="field"><span>完整设定与示例</span><textarea rows="15" readonly>${e(value.prompt)}</textarea></label><div class="row wrap">${(card.source_urls||[]).filter(x=>/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(x)).map(url=>`<a href="${e(url)}" target="_blank" rel="noopener noreferrer">查看参考项目 ↗</a>`).join("")}</div>${!state?.can_apply?'<p class="notice">请先启动对话服务，再应用人格卡。</p>':""}</div>`,async()=>{
      if(!state?.can_apply)throw new Error("请先启动对话服务。");personaWriting=true;++personasGeneration;
      try{await post("/api/personas/apply",{card_id:card.id,expected_revision:revision});shell.toast("已应用人格，原人格备份已保留。");}finally{personaWriting=false;await Promise.all([loadPersonas(),loadTurns()]);}
    });$("modalOk").textContent="应用这张人格";$("modalOk").disabled=!state?.can_apply;
  }
  function restorePersona(item){
    const revision=personaState?.revision??null;
    shell.openModal("恢复原人格",`<p>将恢复这份备份里的原提示词，当前提示词也会先备份。聊天、记忆和独立工具选择保留。</p><label class="exp-confirm"><input type="checkbox" id="personaRestoreConfirm">确认恢复这份原人格</label>`,async()=>{if(!$("personaRestoreConfirm")?.checked)throw new Error("请确认要恢复的原人格。");personaWriting=true;++personasGeneration;try{await post("/api/personas/restore",{backup_id:item.id,expected_revision:revision,confirmed:true});shell.toast("原人格已恢复。");}finally{personaWriting=false;await Promise.all([loadPersonas(),loadTurns()]);}});$("modalOk").textContent="恢复人格";$("modalOk").disabled=!personaState?.can_apply;
  }
  async function openLife(){
    const ticket=++lifeGeneration,before=shell.getModalGeneration(),value=await shell.api("/api/life");if(ticket!==lifeGeneration||before!==shell.getModalGeneration())return;
    const options=value.settings||{},caps=value.capabilities||{};
    shell.openModal("QQ 生活与联网搜索",`<div id="lifeSettingsSheet"><div class="notice blue">作用对象是已登录的机器人 QQ 小号。搜索只发送选定关键词；资料修改和空间发布先预览，再由主人确认。</div><label class="perm-row"><input type="checkbox" id="lifeSearchEnabled" ${options.search_enabled?"checked":""}><div><b>让她联网查资料和新梗</b><span class="small">使用真实搜索结果和链接。不会把全部对话自动上传给搜索服务。</span></div></label><div class="companion-form-grid"><label class="field"><span>搜索服务</span><select id="lifeSearchProvider"><option value="bing_rss">Bing 公开搜索 · 无需额外 Key</option><option value="searxng">我的 SearXNG</option></select></label><label class="field" id="lifeEndpointField"><span>SearXNG HTTPS 地址</span><input id="lifeEndpoint" type="url" maxlength="1000" placeholder="https://search.example.com/search" value="${e(options.searxng_url)}"></label></div><label class="perm-row"><input type="checkbox" id="lifeProfileEnabled" ${options.profile_enabled?"checked":""}><div><b>允许准备修改小鱼的 QQ 资料</b><span class="small">昵称、签名、自定义在线状态和六张随附头像，执行前展示具体变化。</span></div></label><label class="perm-row"><input type="checkbox" id="lifeQzoneEnabled" ${options.qzone_enabled?"checked":""}><div><b>允许准备 QQ 空间文字动态</b><span class="small">默认仅自己可见，可在预览前选择范围。不会代发微信朋友圈。</span></div></label><button class="soft" type="button" id="lifeSave">保存能力开关</button><p id="lifeNotice" class="exp-hint" role="status">${e(!value.owner_configured||!value.bot_configured?"请先完成主人和机器人账号设置。":"能力开关与插件开关分别复核。")}</p><details class="conversation-advanced"><summary>试一次网页搜索</summary><label class="field"><span>只填写想查的关键词</span><input id="lifeQuery" maxlength="200" placeholder="例如：某个新梗的来源"></label><button class="ghost" id="lifeSearchRun" type="button">搜索并查看来源</button><div id="lifeSearchResults" class="life-search-results" role="status"></div></details><details class="conversation-advanced"><summary>查看资料、准备修改或动态</summary><button class="ghost" type="button" id="lifeReadProfile">读取小鱼当前资料</button><p id="lifeProfile" class="exp-hint" role="status"></p><label class="field"><span>想做什么</span><select id="lifeAction"><option value="nickname">修改昵称</option><option value="signature">修改个性签名</option><option value="avatar">换一张大肥鱼头像</option><option value="status">自定义在线状态</option><option value="qzone">发布 QQ 空间文字动态</option></select></label><label class="field" id="lifeTextField"><span id="lifeTextLabel">新昵称</span><textarea id="lifeText" rows="3" maxlength="800"></textarea></label><label class="field hidden" id="lifeAvatarField"><span>选择随附头像</span><select id="lifeAvatar">${(value.avatars||[]).map(a=>`<option value="${e(a.id)}">${e(a.label)}</option>`).join("")}</select></label><label class="field hidden" id="lifeStatusIconField"><span>在线状态图标</span><select id="lifeStatusIcon">${(value.icons||[{id:"smile",label:"微笑"},{id:"focus",label:"奋斗"},{id:"rest",label:"休息"},{id:"coffee",label:"咖啡"}]).map(i=>`<option value="${e(i.id)}">${e(i.label)}</option>`).join("")}</select><small>确认后会切换成在线的自定义状态。</small></label><label class="field hidden" id="lifeVisibilityField"><span>这条动态谁能看</span><select id="lifeVisibility"><option value="private">仅自己</option><option value="friends">QQ 好友</option><option value="public">公开</option></select></label><button class="soft" type="button" id="lifePrepare">先预览具体变化</button><p class="exp-hint">QQ 里也可以让她准备，再说“确认这次修改”或“确认发布这条动态”。只处理主人当前唯一的待确认内容。</p></details><details class="conversation-advanced"><summary>实际支持范围</summary><p class="exp-hint">兴趣标签：${e(caps.tags?.message||"尚无已核验的修改接口，请在 QQ 官方客户端管理。")}</p><p class="exp-hint">${e(caps.qzone?.message||"空间能力须检查当前连接服务的真实支持；失败或未知不会自动重发。")}</p><p class="exp-hint">${e(caps.search?.message||"公开搜索服务可能限流；失败会明确提示，可以切换自己的 SearXNG。")}</p></details></div>`,null);
    $("modalTitle").textContent="QQ 生活与联网搜索";$("modalOk").classList.add("hidden");$("modalCancel").textContent="关闭";
    const generation=shell.getModalGeneration(),alive=()=>generation===shell.getModalGeneration()&&!!$("lifeSettingsSheet")?.isConnected;
    $("lifeSearchProvider").value=options.search_provider||"bing_rss";
    const provider=()=>$("lifeEndpointField").classList.toggle("hidden",$("lifeSearchProvider").value!=="searxng");provider();$("lifeSearchProvider").onchange=provider;
    $("lifeSave").onclick=event=>busy(event.currentTarget,"保存中…",async()=>{const result=await post("/api/life/settings",{search_enabled:$("lifeSearchEnabled").checked,search_provider:$("lifeSearchProvider").value,searxng_url:$("lifeEndpoint").value.trim(),profile_enabled:$("lifeProfileEnabled").checked,qzone_enabled:$("lifeQzoneEnabled").checked});if(!alive())return;notice("lifeNotice",result.persona_tools?.notice||"能力已保存，请求和账号操作仍会复核当前权限。");shell.toast("生活能力开关已保存。");shell.refreshPlugins?.();});
    $("lifeSearchRun").onclick=event=>busy(event.currentTarget,"搜索中…",async()=>{const query=$("lifeQuery").value.trim();const result=await post("/api/life/search",{query});if(!alive())return;if($("lifeQuery").value.trim()!==query){notice("lifeSearchResults","关键词已改变，请重新搜索。");return;}$("lifeSearchResults").innerHTML=(result.results||[]).filter(r=>/^https:\/\/[^\s<>"\\]+$/.test(r.url||"")).map(r=>`<article><a href="${e(r.url)}" target="_blank" rel="noopener noreferrer">${e(r.title||r.url)} ↗</a><p>${e(r.snippet)}</p></article>`).join("")||"没有找到可引用的结果。";});
    $("lifeReadProfile").onclick=event=>busy(event.currentTarget,"读取中…",async()=>{const result=await post("/api/life/profile");if(alive())notice("lifeProfile",`机器人 QQ：${result.bot_id||result.user_id||"已核对"}；昵称：${result.nickname||"未返回"}；签名：${result.signature||"空／暂未返回"}`);});
    const changeAction=()=>{const action=$("lifeAction").value;$("lifeTextField").classList.toggle("hidden",action==="avatar");$("lifeAvatarField").classList.toggle("hidden",action!=="avatar");$("lifeVisibilityField").classList.toggle("hidden",action!=="qzone");$("lifeStatusIconField")?.classList.toggle("hidden",action!=="status");$("lifeTextLabel").textContent={nickname:"新昵称",signature:"新签名（可留空清除）",qzone:"动态正文",status:"自定义在线状态文案"}[action]||"内容";$("lifeText").maxLength={nickname:40,signature:200,qzone:800,status:40}[action]||800;};changeAction();$("lifeAction").onchange=changeAction;
    let draftGeneration=0;["lifeAction","lifeText","lifeAvatar","lifeVisibility","lifeStatusIcon"].forEach(id=>$(id)?.addEventListener("input",()=>{++draftGeneration;}));
    $("lifePrepare").onclick=event=>busy(event.currentTarget,"准备预览…",async()=>{const ticket=++draftGeneration,action=$("lifeAction").value,text=$("lifeText").value.trim(),payload=action==="avatar"?{avatar_id:$("lifeAvatar").value}:action==="qzone"?{content:text,visibility:$("lifeVisibility").value}:action==="signature"?{signature:text}:action==="status"?{wording:text,icon_id:$("lifeStatusIcon").value}:{nickname:text};const result=await post("/api/life/prepare",{action,payload});if(result.ok===false||!result.draft_id)throw new Error(result.message||"当前服务不能准备这项操作。");if(!alive()||ticket!==draftGeneration){post("/api/life/cancel",{draft_id:result.draft_id}).catch(()=>{});if(alive())notice("lifeNotice","内容已改变，请重新预览这次修改。");return;}reviewLife(result);});
  }
  function reviewLife(value){
    const review=value.review||{};
    shell.openModal(review.label||"确认这次账号操作",`<div class="life-review"><p>目标：机器人 QQ <b>${e(review.target_bot)}</b></p>${review.avatar_url?`<img class="life-avatar-preview" src="${e(cardArt(review.avatar_url))}" alt="将使用的头像">`:""}${review.before?`<label class="field"><span>当前</span><pre>${e(review.before)}</pre></label>`:""}<label class="field"><span>将改为／发布内容</span><pre>${e(review.after)}</pre></label>${review.visibility_label?`<p>可见范围：<b>${e(review.visibility_label)}</b></p>`:""}<p class="exp-hint">确认只执行本次预览。结果未知时不会自动重发。</p><label class="exp-confirm"><input type="checkbox" id="lifeConfirmed">我已核对目标和这次具体内容</label></div>`,async()=>{
      if(!$("lifeConfirmed")?.checked)throw new Error("请先核对这次具体内容。");
      const result=await post("/api/life/confirm",{draft_id:value.draft_id,confirmation_token:value.confirmation_token,confirmed:true});
      shell.toast(result.message||({verified:"已核对修改结果。",accepted:"平台已接受。",uncertain:"执行结果未知，请先手动检查，不要重复发送。",blocked:"执行前检查未通过，未执行。",rejected:"QQ 明确拒绝了这次操作，请先检查原因。",unavailable:"当前连接服务不支持此操作，请使用 QQ 官方客户端。"}[result.status]||"请检查实际结果。"));
    });$("modalOk").textContent=review.action==="qzone"?"确认发布这条动态":"确认这次修改";
    const generation=shell.getModalGeneration();$("modalCancel").addEventListener("click",()=>{if(generation===shell.getModalGeneration())post("/api/life/cancel",{draft_id:value.draft_id}).catch(()=>{});},{once:true,capture:true});
  }
  async function loadView(view){
    if(view==="settings")await Promise.all([loadPersonas().catch(err=>notice("personaCurrent",err.message)),loadTurns().catch(err=>notice("turnNotice",err.message))]);
    if(view==="whitelist")await loadTurns().catch(err=>notice("groupNotice",err.message));
  }
  window.AiwuConversation={init,loadView,openLife,loadPersonas,loadTurns};
})();
