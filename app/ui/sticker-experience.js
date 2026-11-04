/* Local library tools; selecting a file never contributes it to GitHub. */
(() => {
  "use strict";
  const $=id=>document.getElementById(id);
  function mount(shell) {
    const host=$("stickerExperienceMount");if(!host)return;
    const ticket=shell.getModalGeneration(),alive=()=>host.isConnected&&shell.getModalGeneration()===ticket;
    const e=value=>shell.esc(String(value??"")),post=(url,body={})=>shell.api(url,{method:"POST",body:JSON.stringify(body)});
    const serial={browse:0,preview:0,materials:0};let items=[],previewItems=[],previewScene="general",materialState=null;
    let importing=false,materialsBusy=false,fileDrafts=[];
    const safeImage=item=>{
      const value=String(item.url||item.path||"");
      return /^\/(?:assets\/plugins\/|api\/stickers\/(?:custom\/|materials\/image\/))[^\s"<>]*$/.test(value)?value:"";
    };
    host.innerHTML=`<section class="sticker-v140-tools" aria-label="查找与收藏表情"><h4>把喜欢的表情，放在顺手的位置</h4><div class="row wrap exp-filterbar"><label class="field"><span>搜索图片与场景</span><input type="search" id="stickerSearchQuery" maxlength="100" placeholder="开心、晚安、作者、画面说明…"></label><button type="button" class="ghost" id="stickerSearchRun">查找</button></div><div class="sticker-v140-tabs"><label><input type="checkbox" id="stickerFavoritesOnly">只看收藏</label><label>排序<select id="stickerLibrarySort" aria-label="图库排序"><option value="default">默认顺序</option><option value="recent">最近使用</option><option value="title">按名称</option></select></label><small id="stickerSearchCount"></small></div><div id="stickerSearchResults" class="sticker-v140-results" aria-label="图库搜索结果"></div></section>
    <details class="sticker-advanced"><summary>试一试 · 这个场景适合哪张表情？</summary><p class="exp-hint">本地试选只根据文字标签与已保存反馈排序，不调用模型，也不发送 QQ；实际聊天仍允许大肥鱼选择不发图。</p><label class="field"><span>写一句聊天场景</span><textarea id="stickerTryText" maxlength="800" rows="3" placeholder="例如：今天终于把一直头疼的工作完成啦！"></textarea></label><label class="field"><span>场景类型</span><select id="stickerTryScene"><option value="general">一般聊天</option><option value="casual">轻松闲聊</option><option value="greeting">主动问候</option><option value="support">认真倾诉／求助</option><option value="task">技术与任务处理</option></select></label><button class="soft" type="button" id="stickerTryRun">查看本地候选</button><p id="stickerTryNotice" class="exp-hint" role="status"></p><div id="stickerTryResults" class="sticker-v140-results"></div></details>
    <details class="sticker-advanced"><summary>批量导入 · 多张图片或 ZIP</summary><p class="exp-hint">导入保存在本机。重复图片会复用已有条目，保留原说明与启停状态；不自动公开投稿。</p><label class="field"><span>选择多张 PNG、JPEG、WebP、GIF</span><input type="file" multiple id="stickerBatchFiles" accept="image/png,image/jpeg,image/webp,image/gif"></label><label class="field"><span>或选择图片 ZIP</span><input type="file" id="stickerBatchZip" accept=".zip,application/zip"></label><button class="text-button" type="button" id="stickerBatchSuggest">按文件名准备标签草稿</button><p class="exp-hint">草稿只依据文件名关键词，不会识别图片内容，也不调用模型；请先核对、修改后再导入。ZIP 内图片可在导入后逐张编辑。</p><div id="stickerBatchDrafts" class="sticker-v140-results"></div><div class="companion-form-grid"><label class="field"><span>共同关键词（可选）</span><input id="stickerBatchTags" maxlength="200" placeholder="开心，庆祝"></label><label class="field"><span>作者署名（可选）</span><input id="stickerBatchAuthor" maxlength="100"></label></div><label class="field"><span>适用场景（可选）</span><input id="stickerBatchUse" maxlength="160" placeholder="例如 分享好消息"></label><label class="field"><span>避用场景（可选）</span><input id="stickerBatchAvoid" maxlength="160" placeholder="例如 对方难过或认真求助"></label><p class="exp-hint">一次最多20张，单张8 MiB，图片原文件合计24 MiB；ZIP最多20 MiB，服务端还会检查真实格式、解压范围及容量。</p><button class="soft" type="button" id="stickerBatchImport">导入到我的图库</button><div id="stickerBatchReport" class="sticker-batch-report" role="status"></div></details>
    <details class="sticker-advanced"><summary>社区素材独立更新</summary><p class="exp-hint">从爱屋及鱼正式发布读取图片、说明和许可。先查看变化，再选择应用；个人图库和插件代码继续保留。</p><div class="row wrap"><button type="button" class="ghost" id="stickerMaterialsCheck">检查素材更新</button><button type="button" class="soft" id="stickerMaterialsApply" disabled>下载并应用这次素材更新</button></div><p id="stickerMaterialsNotice" class="exp-hint" role="status"></p><div id="stickerMaterialsChanges" class="exp-hint"></div></details>`;
    async function run(button,label,action) {
      if(!alive()||button.disabled)return;const old=button.textContent;button.disabled=true;button.textContent=label;
      try{await action();}catch(error){if(alive())shell.toast(error.message||"操作未完成，请重试。");}
      finally{if(alive()&&button.isConnected){button.disabled=false;button.textContent=old;if(materialState)paintMaterials(materialState);}}
    }
    function rowHTML(item,index,preview=false) {
      return `<article class="sticker-v140-result"><img src="${e(safeImage(item))}" alt="${e(item.title||item.label||"表情")}" loading="lazy"><div><b>${e(item.title||item.label||item.id)}</b><p>${e(item.description||item.caption||item.emotion||"")}</p><small>${e(item.use_when||item.use||"")}</small><p>${e(typeof item.attribution==="string"?item.attribution:"")}</p></div><button type="button" class="text-button" ${preview?"data-try-reject":"data-library-favorite"}="${index}">${preview?"这张不合适":item.favorite?"取消收藏":"收藏"}</button>${preview?`<button type="button" class="text-button" data-try-clear="${index}">撤销避用反馈</button>`:""}</article>`;
    }
    async function browse() {
      const g=++serial.browse,query=$("stickerSearchQuery").value.trim(),favorite=$("stickerFavoritesOnly").checked,sort=$("stickerLibrarySort").value;
      const value=await shell.api(`/api/stickers/library?query=${encodeURIComponent(query)}&favorites_only=${favorite}&sort=${encodeURIComponent(sort)}`);
      if(!alive()||g!==serial.browse)return;items=value.stickers||[];
      $("stickerSearchCount").textContent=`${value.count??items.length} / ${value.total_count??items.length} 张`;
      $("stickerSearchResults").innerHTML=items.length?items.map((item,index)=>rowHTML(item,index)).join(""):'<p class="exp-hint">没有匹配的素材。可以换个关键词，或导入自己的表情包。</p>';
      $("stickerSearchResults").querySelectorAll("[data-library-favorite]").forEach(button=>button.onclick=()=>run(button,"保存中…",async()=>{
        const chosen=items[Number(button.dataset.libraryFavorite)];if(!chosen)return;
        ++serial.browse;await post("/api/stickers/library/favorite",{id:chosen.id,favorite:!chosen.favorite});if(alive())await browse();
      }));
    }
    $("stickerSearchRun").onclick=event=>run(event.currentTarget,"查找中…",browse);
    $("stickerSearchQuery").addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();browse().catch(error=>{if(alive())shell.toast(error.message);});}});
    ["stickerFavoritesOnly","stickerLibrarySort"].forEach(id=>$(id).addEventListener("change",()=>browse().catch(error=>{if(alive())shell.toast(error.message);})));
    $("stickerTryRun").onclick=event=>run(event.currentTarget,"试选中…",async()=>{
      const text=$("stickerTryText").value.trim(),scene=$("stickerTryScene").value;if(!text)throw new Error("先写一句要试选的聊天场景。");
      const g=++serial.preview,value=await post("/api/stickers/library/preview",{text,scene});if(!alive()||g!==serial.preview)return;
      previewItems=value.candidates||[];previewScene=scene;$("stickerTryNotice").textContent=value.message||"本地候选已准备。";
      $("stickerTryResults").innerHTML=previewItems.length?previewItems.map((item,index)=>rowHTML(item,index,true)).join(""):'<p class="exp-hint">这个场景建议先好好说话，不自动配图。</p>';
      $("stickerTryResults").querySelectorAll("[data-try-reject],[data-try-clear]").forEach(button=>button.onclick=()=>run(button,"保存中…",async()=>{
        const index=Number(button.dataset.tryReject??button.dataset.tryClear),chosen=previewItems[index],reason=button.dataset.tryClear!==undefined?"clear":"not_suitable";if(!chosen)return;
        const g=serial.preview,scene=previewScene;
        await post("/api/stickers/library/feedback",{id:chosen.id,scene,reason});if(!alive()||g!==serial.preview)return;
        $("stickerTryNotice").textContent=reason==="clear"?"已撤销这张表情在此场景的避用反馈。":"已记住：在这类场景里降低这张表情的优先级。";
      }));
    });
    const invalidatePreview=()=>{serial.preview+=1;previewItems=[];$("stickerTryResults").innerHTML="";$("stickerTryNotice").textContent="场景已改变，可重新试选。";};
    ["stickerTryText","stickerTryScene"].forEach(id=>$(id).addEventListener("input",invalidatePreview));
    $("stickerTryScene").addEventListener("change",invalidatePreview);
    ["stickerBatchFiles","stickerBatchZip"].forEach(id=>$(id).addEventListener("change",()=>{
      const other=$(id==="stickerBatchFiles"?"stickerBatchZip":"stickerBatchFiles");if($(id).files?.length)other.value="";
      fileDrafts=[];$("stickerBatchDrafts").innerHTML="";
    }));
    $("stickerBatchSuggest").onclick=()=>{
      const files=Array.from($("stickerBatchFiles").files||[]);if(!files.length){shell.toast("先选择多张本地图片，才可以按文件名准备草稿。");return;}if(files.length>20){shell.toast("请先把选择缩减到最多20张。");return;}
      const rules=[[/开心|高兴|happy|joy|笑|庆祝/i,"happy","开心"],[/难过|哭|sad|委屈/i,"sad","委屈"],[/生气|angry|气鼓/i,"angry","生气"],[/害羞|shy/i,"shy","害羞"],[/晚安|睡|sleep|night/i,"sleepy","晚安"],[/喜欢|爱|love|heart/i,"love","喜欢"],[/疑惑|confus|问号/i,"confused","疑惑"]];
      fileDrafts=files.map(file=>{const title=file.name.replace(/\.[^.]+$/,"").replace(/[_-]+/g," ").slice(0,60),match=rules.find(rule=>rule[0].test(title));return {name:file.name,title,emotion:match?.[1]||"daily",tags:match?[match[2]]:[]};});
      $("stickerBatchDrafts").innerHTML=fileDrafts.map((draft,index)=>`<div class="sticker-v140-result"><div><small>${e(draft.name)}</small><label class="field"><span>草稿名称</span><input data-batch-title="${index}" maxlength="60" value="${e(draft.title)}"></label><label class="field"><span>草稿关键词</span><input data-batch-tags="${index}" maxlength="200" value="${e(draft.tags.join("，"))}"></label></div></div>`).join("");
    };
    const encode=file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.onerror=()=>reject(new Error("图片或ZIP读取失败，请重新选择。"));reader.readAsDataURL(file);});
    $("stickerBatchImport").onclick=event=>run(event.currentTarget,"导入中…",async()=>{
      if(importing)return;const zip=$("stickerBatchZip").files?.[0],files=Array.from($("stickerBatchFiles").files||[]);
      if(!zip&&!files.length)throw new Error("先选择多张图片或一份ZIP。");
      if(zip&&zip.size>20*1024*1024)throw new Error("ZIP不能超过20 MiB。");
      if(!zip&&(files.length>20||files.some(file=>file.size>8*1024*1024)||files.reduce((sum,file)=>sum+file.size,0)>24*1024*1024))throw new Error("最多20张，单张8 MiB，原图总计24 MiB。");
      const defaults={tags:$("stickerBatchTags").value.split(/[,，、]/).map(text=>text.trim()).filter(Boolean),attribution:$("stickerBatchAuthor").value.trim(),use_when:$("stickerBatchUse").value.trim(),avoid_when:$("stickerBatchAvoid").value.trim(),emotion:"daily",enabled:true};
      if(defaults.tags.length>12)throw new Error("共同关键词最多12个。");
      importing=true;$("stickerBatchFiles").disabled=$("stickerBatchZip").disabled=true;
      try{
        let body,url;
        if(zip){body={archive_base64:await encode(zip),defaults};url="/api/stickers/custom/zip";}
        else{const encoded=[];for(const [index,file] of files.entries()){const image_base64=await encode(file);if(!alive())return;const draft=fileDrafts[index];let metadata;
          if(draft?.name===file.name){const title=host.querySelector(`[data-batch-title="${index}"]`)?.value.trim(),tags=(host.querySelector(`[data-batch-tags="${index}"]`)?.value||"").split(/[,，、]/).map(value=>value.trim()).filter(Boolean);metadata={title:title||draft.title,emotion:draft.emotion,tags:Array.from(new Set([...defaults.tags,...tags])).slice(0,12)};}
          encoded.push({name:file.name,image_base64,...(metadata?{metadata}:{})});}body={files:encoded,defaults};url="/api/stickers/custom/batch";}
        if(!alive())return;const value=await post(url,body);if(!alive())return;
        const labels={imported:"已导入",reused:"复用已有",invalid:"未导入"};
        $("stickerBatchReport").innerHTML=`<b>导入 ${e(value.imported??0)} · 复用 ${e(value.reused??0)} · 未导入 ${e(value.invalid??0)}</b>${(value.results||[]).map(row=>`<p>${e(row.name)}：${e(labels[row.status]||row.status)}${row.message?" · "+e(row.message):""}</p>`).join("")}`;
        $("stickerBatchFiles").value=$("stickerBatchZip").value="";fileDrafts=[];$("stickerBatchDrafts").innerHTML="";await shell.onCatalogChange?.();if(alive())await browse();
      }finally{importing=false;if(alive())$("stickerBatchFiles").disabled=$("stickerBatchZip").disabled=false;}
    });
    function paintMaterials(value) {
      materialState=value;const changes=value.changes||{};
      $("stickerMaterialsNotice").textContent=`当前：${value.version||"随附素材"}${value.latest_version?" · 发布素材 "+value.latest_version:""}。${value.message||""}`;
      $("stickerMaterialsChanges").textContent=value.fingerprint?`新增 ${changes.added||0}，变化 ${changes.changed||0}，移出社区包 ${changes.removed||0}；个人图片不会因此移除。`:"先检查更新，核对变化后再选择应用。";
      $("stickerMaterialsApply").disabled=materialsBusy||value.available!==true||!value.fingerprint;
      $("stickerMaterialsCheck").disabled=materialsBusy;
    }
    $("stickerMaterialsCheck").onclick=event=>run(event.currentTarget,"检查中…",async()=>{
      if(materialsBusy)return;materialsBusy=true;const g=++serial.materials;if(materialState)paintMaterials(materialState);
      try{const value=await post("/api/stickers/materials/check");if(alive()&&g===serial.materials)paintMaterials(value);}
      finally{materialsBusy=false;if(alive()&&materialState)paintMaterials(materialState);}
    });
    $("stickerMaterialsApply").onclick=event=>run(event.currentTarget,"下载与校验中…",async()=>{
      const chosen=materialState;if(materialsBusy||!chosen?.available||!chosen.fingerprint)throw new Error("先检查并核对本次素材变化。");
      materialsBusy=true;paintMaterials(chosen);
      try{const value=await post("/api/stickers/materials/apply",{fingerprint:chosen.fingerprint});if(!alive())return;paintMaterials(value);await shell.onCatalogChange?.();if(alive()){await browse();shell.toast("社区素材已应用，个人图库保留。");}}
      finally{materialsBusy=false;if(alive()&&materialState)paintMaterials(materialState);}
    });
    browse().catch(error=>{if(alive())$("stickerSearchResults").textContent=error.message;});
    const initial=++serial.materials;shell.api("/api/stickers/materials").then(value=>{if(alive()&&initial===serial.materials)paintMaterials(value);}).catch(error=>{if(alive())$("stickerMaterialsNotice").textContent=error.message;});
  }
  window.AiwuStickerExperience={mount};
})();
