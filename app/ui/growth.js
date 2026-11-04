/* Private evidence is read only after an explicit inspect; writes never retry. */
(() => {
  "use strict";
  let shell, active=false, page=0, statusRead=0, privateRead=0, entriesVersion=0;
  let view=null, loaded=false, savedEnabled=false, writing=false, inspecting=false,bound=false;
  const $=id=>document.getElementById(id),e=x=>shell.esc(String(x??""));
  const post=(path,body={})=>shell.api(path,{method:"POST",body:JSON.stringify(body)});
  const message=(id,text)=>{if($(id))$(id).textContent=text;};
  const count=x=>Number.isFinite(Number(x))?Math.max(0,Math.trunc(Number(x))):0;
  const live=token=>active&&token===page&&!!$("growthPanel")?.isConnected;
  function controls(){
    if(!$("growthPanel"))return;
    $("growthEnabled").disabled=writing||!loaded;
    $("growthInspect").disabled=writing||inspecting;
    $("growthRefresh").disabled=writing;
    $("growthEntries").querySelectorAll("[data-growth-action]").forEach(button=>{button.disabled=writing;});
  }
  function forgetView(){view=null;++entriesVersion;message("growthEntries","");}
  function leave(){active=false;++page;++statusRead;++privateRead;inspecting=false;forgetView();}
  function init(value){shell=value;if(!bound&&window.addEventListener){bound=true;window.addEventListener("pagehide",leave);}}
  function mount(){
    if($("growthPanel"))return;
    const target=$("view-lovehouse");if(!target)return;
    const panel=document.createElement("section");panel.id="growthPanel";panel.className="experience-panel growth-panel";
    panel.innerHTML=`<div class="exp-heading"><img src="/assets/ui/states/dafeiyu-remembering-v140.png" alt="大肥鱼翻看相处手记"><div><small>相处过的事，要能改变下一次相处</small><h2>成长手记</h2><p>记住明确反馈，从真实经历提出想法；事实、猜测和尝试分别保留。</p></div></div>
      <label class="perm-row"><input id="growthEnabled" type="checkbox" disabled><div><b>让相处慢慢影响她</b><span class="small">自动记下明确偏好；有证据的语气建议可短期试行。反思沿用当前对话工具回合，可能增加本轮模型用量；不另开后台推理任务。</span></div></label>
      <p id="growthSummary" class="exp-hint" role="status">正在查看学习状态…</p>
      <div class="row wrap"><button type="button" class="soft" id="growthInspect">看看最近学到了什么</button><button type="button" class="text-button" id="growthRefresh">刷新状态</button></div>
      <div id="growthEntries" class="growth-entries"></div>
      <p class="exp-hint">明确反馈优先。反思不是事实，自由描述的计划不会执行为电脑、账号或权限操作；这些可检查的调整不代表真实意识。</p>`;
    const hero=target.querySelector(".lovehouse-home");if(hero)hero.after(panel);else target.appendChild(panel);
    $("growthRefresh").onclick=()=>loadView("lovehouse");
    $("growthInspect").onclick=()=>inspect();
    $("growthEnabled").onchange=saveEnabled;
  }
  async function saveEnabled(){
    const control=$("growthEnabled");if(writing||!loaded||!active)return;
    const enabled=control.checked,token=page,previous=savedEnabled;
    writing=true;++statusRead;++privateRead;inspecting=false;controls();
    try{
      const result=await post("/api/companion",{growth_enabled:enabled});
      if(result?.ok===false)throw new Error(result.message||"设置未保存。");
      savedEnabled=enabled;
      if(live(token)){control.checked=enabled;message("growthSummary",enabled?"学习已开启；明确反馈和有证据的低影响短试会按授权生效。":"学习已暂停，已有手记保留，可以继续查看。");}
    }catch(error){
      loaded=false;
      if(live(token)){control.checked=previous;message("growthSummary","设置结果未确认，请刷新状态核对后再修改。");}
      shell.toast(error.message||"设置结果未确认，请刷新核对。");
    }finally{
      writing=false;controls();
      if(active&&token!==page)await loadView("lovehouse");
    }
  }
  async function loadView(name){
    if(name!=="lovehouse"){leave();return;}
    mount();if(!$("growthPanel"))return;
    if(!active){active=true;++page;}
    if(writing){message("growthSummary","正在保存，完成后再刷新状态。");controls();return;}
    const token=page,request=++statusRead;
    try{
      const data=await shell.api("/api/growth");
      if(!live(token)||request!==statusRead||writing)return;
      if(data?.ok===false||typeof data?.enabled!=="boolean")throw new Error(data?.message||"暂时无法核对学习开关。");
      loaded=true;savedEnabled=data.enabled;$("growthEnabled").checked=savedEnabled;
      const totals=data.counts||{};
      message("growthSummary",`${data.enabled?"学习已开启":"学习已暂停"} · 明确反馈 ${count(totals.feedback)} 条 · 待核实想法 ${count(totals.pending)} 条 · 可短期尝试 ${count(totals.trial)} 条。${data.notice||""}`);
    }catch(error){if(live(token)&&request===statusRead)message("growthSummary",error.message||"暂时无法读取学习状态。");}
    finally{controls();}
  }
  async function inspect(fromMutation=false){
    if(!active||writing&&!fromMutation)return false;
    const token=page,request=++privateRead;inspecting=true;controls();
    try{
      const data=await post("/api/growth/inspect");
      if(!live(token)||request!==privateRead)return false;
      if(data?.ok===false||!Array.isArray(data?.entries))throw new Error(data?.message||"暂时无法查看手记。");
      view=data;++entriesVersion;paint();return true;
    }catch(error){
      if(live(token)&&request===privateRead){view=null;++entriesVersion;message("growthEntries",fromMutation?"手记已更新，但暂时无法重新读取。请点击查看核对；不要重复提交。":error.message||"暂时无法查看手记。");}
      return false;
    }finally{if(request===privateRead)inspecting=false;controls();}
  }
  function paint(){
    const entries=view.entries.slice(0,60),version=entriesVersion;
    if(!entries.length){$("growthEntries").innerHTML='<p class="growth-empty">还没有可查看的手记。明确说“少反问一点”或“让我说完”，她会在授权范围内记下；低影响短试不必逐条批准。</p>';return;}
    const status={active:"明确反馈 · 可用于相处",pending:"待核实想法",approved:"已采用的相处建议",paused:"已暂停使用",superseded:"已被新反馈替代"};
    $("growthEntries").innerHTML=entries.map((row,index)=>{
      const trial=typeof row.trial_until==="number"&&row.trial_until*1000>Date.now()&&row.status==="pending";
      const label=row.kind==="feedback"?"用户明确表达":"基于经历提出的想法";
      const source=Array.isArray(row.evidence_ids)?row.evidence_ids.length:0;
      return `<article class="growth-entry"><div class="row between wrap"><b>${e(row.label||label)}</b><span class="badge ${row.status==="active"||row.status==="approved"?"green":"gray"}">${e(trial?"待核实 · 短期试行候选":status[row.status]||"状态待确认")}</span></div>
        ${row.source_quote?`<blockquote>${e(row.source_quote)}</blockquote>`:""}
        ${row.hypothesis?`<p><b>她的想法：</b>${e(row.hypothesis)}</p>`:""}
        ${row.next_step?`<p><b>提出的下一步：</b>${e(row.next_step)}</p><p class="exp-hint">这段描述只供查看，不会自行执行。</p>`:""}
        <p class="exp-hint">${row.kind==="feedback"?"来源：这次明确反馈":"关联原记忆证据 "+source+" 项；猜测不自动成为事实"}。效果尚未经日常体验验证。</p>
        <div class="row wrap">${row.kind==="reflection"&&row.status==="pending"?`<button type="button" class="soft" data-growth-action="approve" data-growth-index="${index}">查看并采用建议</button>`:""}${["active","approved","pending"].includes(row.status)?`<button type="button" class="text-button" data-growth-action="pause" data-growth-index="${index}">暂停使用</button>`:row.status==="paused"?`<button type="button" class="text-button" data-growth-action="resume" data-growth-index="${index}">恢复使用</button>`:""}</div></article>`;
    }).join("");
    $("growthEntries").querySelectorAll("[data-growth-action]").forEach(button=>button.onclick=()=>{
      const row=entries[Number(button.dataset.growthIndex)],action=button.dataset.growthAction;
      return action==="approve"?review(row,version):mutate(row,action,version);
    });controls();
  }
  async function mutate(row,action,version,reviewNode=null){
    if(writing||!active||version!==entriesVersion||!row||!["approve","pause","resume"].includes(action))return;
    if(typeof row.id!=="string"||typeof row.revision!=="string"){shell.toast("记录版本缺失，请重新查看。");return;}
    const token=page;writing=true;++statusRead;++privateRead;inspecting=false;controls();
    try{
      const result=await post("/api/growth/mutate",{id:row.id,action,expected_revision:row.revision,confirmed:true});
      if(result?.ok===false)throw new Error(result.message||"这次操作没有完成。");
      if(reviewNode?.isConnected)shell.closeModal();
      shell.toast(result?.message||"手记状态已更新。");
      if(live(token)){forgetView();message("growthEntries","手记已更新，正在重新读取…");await inspect(true);}
    }catch(error){
      if(live(token)){forgetView();message("growthEntries","操作结果未确认。请重新查看后再操作，不会自动重复提交。");}
      shell.toast(error.message||"操作结果未确认，请重新查看。");
    }finally{
      writing=false;controls();
      if(active&&token!==page)await loadView("lovehouse");
    }
  }
  function review(row,version){
    if(writing||!active||!row||version!==entriesVersion)return;
    const token=page;
    shell.openModal("采用相处建议",`<div id="growthReview"><p><b>${e(row.label||"这条相处建议")}</b></p><p>${e(row.hypothesis||"尚无具体推测说明")}</p>${row.next_step?`<p>${e(row.next_step)}</p>`:""}<p class="exp-hint">只调整可撤回的相处建议；不修改记忆事实、权限或QQ账号。原证据会重新核对。</p></div>`,async()=>{
      if(!live(token)||!reviewNode?.isConnected||version!==entriesVersion)throw new Error("页面或手记已变化，请重新查看这条建议。");
      return mutate(row,"approve",version,reviewNode);
    },"采用这条建议");
    const reviewNode=$("growthReview");
  }
  window.AiwuGrowth={init,loadView};
})();
