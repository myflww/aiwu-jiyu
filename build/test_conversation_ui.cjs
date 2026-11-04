// Permission and late-response flows in isolation. No browser, QQ or network.
const runTest=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const test=(name,fn)=>runTest(name,{timeout:4000},fn);
const source=fs.readFileSync(path.join(__dirname,'../app/ui/conversation.js'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
const settle=async()=>{for(let i=0;i<4;i++)await new Promise(resolve=>setImmediate(resolve));};
const escape=value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const decode=value=>String(value).replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');

function harness(){
 const nodes=new Map(),calls=[],toasts=[],gates=[];let generation=0,saveModal=null;
 let personas={revision:'rev-one',can_apply:true,active_name:'原人格',cards:[{id:'calm',name:'安静小鱼',subtitle:'安静陪伴',author:'myflww',license:'非商业',tags:['轻松'],art:'/assets/plugins/astrbot_plugin_dafeiyu_stickers/pack/happy.png',source_urls:['https://github.com/myflww/aiwu-jiyu']}],backups:[{id:'backup-one',created_at:1700000000,card_before:'原人格'}]};
 let prompt='完整人格与示例',turns={reply_timing_mode:'adaptive',persona_profile:'natural',collection_seconds:10,collection_max_wait_seconds:30,collection_max_messages:12,collection_max_chars:6000,message_collection_enabled:true,natural_bubbles_enabled:true,group_reply_mode:'auto',group_wake_words:'小鱼',group_cooldown_seconds:90,group_hourly_limit:6,group_participation_probability:3,plugin_installed:true,plugin_enabled:true,rhythm:{mode:'adaptive',persona_profile:'natural',worker_running:false,recent:[]}};
 let life={settings:{search_enabled:false,search_provider:'bing_rss',searxng_url:'',profile_enabled:false,qzone_enabled:false},capabilities:{},owner_configured:true,bot_configured:true,avatars:[{id:'happy',label:'开心'}]};
 let searchResults=[{url:'https://example.com/source',title:'来源',snippet:'实际搜索摘要'}],review={action:'nickname',label:'确认修改',target_bot:'robot-test',before:'旧昵称',after:'新昵称'};
 function simple(node,selector){if(selector.startsWith('#'))return node.id===selector.slice(1);if(selector.startsWith('.'))return node.classList.contains(selector.slice(1));const attr=selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);if(attr)return Object.hasOwn(node.attrs,attr[1])&&(attr[2]===undefined||node.attrs[attr[1]]===attr[2]);return node.tagName===selector.toUpperCase();}
 const matches=(node,selector)=>selector.split(',').some(value=>{const parts=value.trim().split(/\s+/);if(!simple(node,parts.pop()))return false;for(let i=parts.length-1,ancestor=node.parentNode;i>=0;i--){while(ancestor&&!simple(ancestor,parts[i]))ancestor=ancestor.parentNode;if(!ancestor)return false;ancestor=ancestor.parentNode;}return true;});
 class Element{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.parentNode=null;this.attrs={};this.dataset={};this.listeners={};this.checked=false;this.disabled=false;this.value='';this._html='';this._text='';this._id='';const classes=new Set();this.classList={add:(...v)=>v.forEach(x=>classes.add(x)),remove:(...v)=>v.forEach(x=>classes.delete(x)),contains:v=>classes.has(v),toggle(v,on){on=on??!classes.has(v);on?classes.add(v):classes.delete(v);return on;}};}
  set id(v){if(nodes.get(this._id)===this)nodes.delete(this._id);this._id=String(v);if(v)nodes.set(this._id,this);}get id(){return this._id;}
  set className(v){String(v).split(/\s+/).filter(Boolean).forEach(x=>this.classList.add(x));}
  setAttribute(k,v){v=String(v);this.attrs[k]=v;if(k==='id')this.id=v;if(k==='class')this.className=v;if(k==='value')this.value=v;if(k==='checked')this.checked=true;if(k==='disabled')this.disabled=true;if(['type','src','href'].includes(k))this[k]=v;if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,x)=>x.toUpperCase())]=v;}
  appendChild(child){child.parentNode=this;this.children.push(child);return child;}
  after(child){const parent=this.parentNode;if(!parent)return;child.parentNode=parent;parent.children.splice(parent.children.indexOf(this)+1,0,child);}
  unregister(){this.children.forEach(x=>x.unregister());if(nodes.get(this.id)===this)nodes.delete(this.id);this.parentNode=null;}
  set innerHTML(v){this.children.forEach(x=>x.unregister());this.children=[];this._text='';this._html=String(v);parse(this._html,this);}get innerHTML(){return this._html;}
  set textContent(v){this.children.forEach(x=>x.unregister());this.children=[];this._text=String(v);this._html=escape(v);}get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}
  querySelectorAll(selector){const found=[];const walk=node=>{for(const child of node.children){if(matches(child,selector))found.push(child);walk(child);}};walk(this);return found;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  addEventListener(type,fn,options={}){(this.listeners[type]??=[]).push({fn,once:!!options.once,capture:!!options.capture});}
  get isConnected(){for(let node=this;node;node=node.parentNode)if(node===document.body)return true;return false;}
  async fire(type){if(type==='click'&&this.disabled)return;const event={type,target:this,currentTarget:this,preventDefault(){}};const entries=[...(this.listeners[type]||[])].sort((a,b)=>Number(b.capture)-Number(a.capture));for(const entry of entries.filter(x=>x.capture)){if(entry.once)this.listeners[type]=this.listeners[type].filter(x=>x!==entry);await entry.fn(event);}if(type==='click'&&this.onclick)await this.onclick(event);if(type==='change'&&this.onchange)await this.onchange(event);for(const entry of entries.filter(x=>!x.capture)){if(entry.once)this.listeners[type]=this.listeners[type].filter(x=>x!==entry);await entry.fn(event);}await settle();}
 }
 function parse(html,parent){const stack=[parent],voids=new Set(['input','img','br','hr']);for(const token of html.matchAll(/<\/?[a-zA-Z][^>]*>|[^<]+/g)){const value=token[0],node=stack.at(-1);if(value.startsWith('</')){if(stack.length>1)stack.pop();continue;}if(!value.startsWith('<')){if(node!==parent){node._text+=decode(value);if(node.tagName==='TEXTAREA')node.value+=decode(value);}continue;}const open=value.match(/^<([\w-]+)([\s\S]*?)\/?\s*>$/);if(!open)continue;const child=new Element(open[1]);for(const attr of open[2].matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g))child.setAttribute(attr[1],decode(attr[2]??attr[3]??attr[4]??''));node.appendChild(child);if(child.tagName==='OPTION'&&node.tagName==='SELECT'&&(!node.value||Object.hasOwn(child.attrs,'selected')))node.value=child.value;if(!voids.has(open[1])&&!value.endsWith('/>'))stack.push(child);}}
 const document={body:new Element('body'),getElementById:id=>nodes.get(id)||null,createElement:tag=>new Element(tag),querySelectorAll:selector=>document.body.querySelectorAll(selector)};
 const add=(id,parent=document.body,tag='div')=>{const node=new Element(tag);node.id=id;parent.appendChild(node);return node;};
 const settings=add('view-settings'),identity=add('settingsIdentity',settings),nav=add('nav',settings);nav.className='settings-section-nav';
 const whitelist=add('view-whitelist'),summary=add('summary',whitelist);summary.className='permission-summary';
 for(const id of ['modalTitle','modalBody','modalOk','modalCancel'])add(id,document.body,id.startsWith('modal')&&['modalOk','modalCancel'].includes(id)?'button':'div');
 function route(url,body){
  if(url==='/api/personas')return clone(personas);
  if(url==='/api/personas/preview')return {prompt};
  if(url==='/api/personas/apply'||url==='/api/personas/restore')return {ok:true};
  if(url==='/api/companion'){if(Object.keys(body).length)turns={...turns,...body};return clone(turns);}
  if(url==='/api/life')return clone(life);
  if(url==='/api/life/settings'){life.settings={...life.settings,...body};return {ok:true,persona_tools:{notice:'能力已保存'}};}
  if(url==='/api/life/search')return {results:clone(searchResults)};
  if(url==='/api/life/profile')return {nickname:'小鱼',signature:'日常陪伴'};
  if(url==='/api/life/prepare')return {draft_id:'draft-one',confirmation_token:'token-one',review:clone(review)};
  if(url==='/api/life/confirm')return {status:'verified'};
  if(url==='/api/life/cancel')return {ok:true};
  throw new Error('Unexpected fixture API '+url);
 }
 async function api(url,options={}){const body=JSON.parse(options.body||'{}');calls.push({url,body:clone(body),method:options.method||'GET'});const result=route(url,body),index=gates.findIndex(g=>g.url===url);if(index>=0){const gate=gates.splice(index,1)[0];gate.enter();const replacement=await gate.promise;return replacement===undefined?result:replacement;}return result;}
 const get=id=>{assert.ok(nodes.has(id),'missing '+id);return nodes.get(id);};
 const shell={api,esc:escape,toast:message=>toasts.push(String(message)),showView(){},getModalGeneration:()=>generation,refreshPlugins(){},
  openModal(title,html,save){++generation;get('modalTitle').textContent=title;get('modalBody').innerHTML=html;get('modalOk').disabled=false;get('modalOk').classList.remove('hidden');get('modalCancel').textContent='取消';saveModal=save;},
  closeModal(){++generation;saveModal=null;get('modalBody').innerHTML='';}};
 get('modalCancel').addEventListener('click',()=>shell.closeModal());
 const window={},context=vm.createContext({window,document,console,Date});vm.runInContext(source,context);window.AiwuConversation.init(shell);
 return {api:window.AiwuConversation,shell,calls,toasts,get,requests:url=>calls.filter(call=>call.url===url),save:()=>saveModal?.(),title:()=>get('modalTitle').textContent,
  click:id=>get(id).fire('click'),button:(selector,id='personaCardList')=>{const node=get(id).querySelector(selector);assert.ok(node,'missing '+selector);return node;},
  setPersonas:value=>{personas={...personas,...clone(value)};},setTurns:value=>{turns={...turns,...clone(value)};},setPrompt:value=>{prompt=value;},setSearch:value=>{searchResults=clone(value);},setReview:value=>{review={...review,...clone(value)};},
  defer(url){let enter,release,reject;const entered=new Promise(resolve=>{enter=resolve;}),promise=new Promise((yes,no)=>{release=yes;reject=no;});gates.push({url,enter,promise});return {entered,release,reject};}};
}

test('persona preview never applies automatically; apply and restore keep the shown revision and require restore consent',async()=>{
 const h=harness();await h.api.loadPersonas();await h.button('[data-persona-preview]').fire('click');assert.equal(h.requests('/api/personas/apply').length,0);
 await h.save();assert.deepEqual(h.requests('/api/personas/apply')[0].body,{card_id:'calm',expected_revision:'rev-one'});
 await h.button('[data-persona-restore]','personaBackupList').fire('click');await assert.rejects(()=>h.save(),/确认/);assert.equal(h.requests('/api/personas/restore').length,0);
 h.get('personaRestoreConfirm').checked=true;await h.save();assert.deepEqual(h.requests('/api/personas/restore')[0].body,{backup_id:'backup-one',expected_revision:'rev-one',confirmed:true});
});

test('a late persona preview cannot replace a different modal or apply anything',async()=>{
 const h=harness();await h.api.loadPersonas();const gate=h.defer('/api/personas/preview'),click=h.button('[data-persona-preview]').fire('click');await gate.entered;
 h.shell.openModal('另一个窗口','<p>正在操作别的内容</p>',null);gate.release();await click;assert.equal(h.title(),'另一个窗口');assert.equal(h.requests('/api/personas/apply').length,0);
});

test('life opening is read-only and saving passes only explicit capability settings without searching or sending',async()=>{
 const h=harness();await h.api.openLife();assert.deepEqual(h.calls.map(x=>x.url),['/api/life']);
 h.get('lifeSearchEnabled').checked=true;h.get('lifeProfileEnabled').checked=true;h.get('lifeSearchProvider').value='searxng';h.get('lifeEndpoint').value='https://search.example.com/search';
 const gate=h.defer('/api/life/settings'),saving=h.click('lifeSave');await gate.entered;h.get('lifeEndpoint').value='https://new.example.com/search';gate.release();await saving;
 assert.deepEqual(h.requests('/api/life/settings')[0].body,{search_enabled:true,search_provider:'searxng',searxng_url:'https://search.example.com/search',profile_enabled:true,qzone_enabled:false});
 assert.equal(h.get('lifeEndpoint').value,'https://new.example.com/search');assert.equal(h.requests('/api/life/search').length,0);assert.equal(h.requests('/api/life/prepare').length,0);assert.equal(h.requests('/api/life/confirm').length,0);
});

test('account changes prepare a draft first, require the review checkbox, and cancel the exact unconfirmed draft',async()=>{
 const h=harness();await h.api.openLife();h.get('lifeText').value='新昵称';await h.click('lifePrepare');assert.equal(h.requests('/api/life/confirm').length,0);
 await assert.rejects(()=>h.save(),/核对/);assert.equal(h.requests('/api/life/confirm').length,0);h.get('lifeConfirmed').checked=true;await h.save();
 assert.deepEqual(h.requests('/api/life/confirm')[0].body,{draft_id:'draft-one',confirmation_token:'token-one',confirmed:true});
 await h.api.openLife();h.get('lifeText').value='另一份草稿';await h.click('lifePrepare');await h.click('modalCancel');assert.deepEqual(h.requests('/api/life/cancel')[0].body,{draft_id:'draft-one'});assert.equal(h.requests('/api/life/confirm').length,1);
});

test('custom online status prepares only wording and the selected icon, then requires explicit review confirmation',async()=>{
 const h=harness();await h.api.openLife();h.get('lifeAction').value='status';await h.get('lifeAction').fire('change');h.get('lifeText').value='  认真干活  ';h.get('lifeStatusIcon').value='focus';
 h.setReview({action:'status',label:'确认在线状态',before:'休息',after:'认真干活 · 奋斗'});await h.click('lifePrepare');
 assert.deepEqual(h.requests('/api/life/prepare')[0].body,{action:'status',payload:{wording:'认真干活',icon_id:'focus'}});assert.equal(h.requests('/api/life/confirm').length,0);
 await assert.rejects(()=>h.save(),/核对/);h.get('lifeConfirmed').checked=true;await h.save();assert.deepEqual(h.requests('/api/life/confirm')[0].body,{draft_id:'draft-one',confirmation_token:'token-one',confirmed:true});
});

test('editing while prepare is pending cancels the old draft and preserves the new input without opening review',async()=>{
 const h=harness();await h.api.openLife();h.get('lifeText').value='旧昵称';const gate=h.defer('/api/life/prepare'),preparing=h.click('lifePrepare');await gate.entered;
 h.get('lifeText').value='刚修改的新昵称';await h.get('lifeText').fire('input');gate.release();await preparing;
 assert.equal(h.title(),'QQ 生活与联网搜索');assert.equal(h.get('lifeText').value,'刚修改的新昵称');assert.equal(h.get('modalBody').querySelector('#lifeConfirmed'),null);
 assert.deepEqual(h.requests('/api/life/cancel').map(call=>call.body),[{draft_id:'draft-one'}]);assert.equal(h.requests('/api/life/confirm').length,0);assert.match(h.get('lifeNotice').textContent,/内容已改变/);
});

test('unavailable prepare responses without a real draft never expose a confirmation action',async()=>{
 for(const response of [{ok:false,status:'unavailable',message:'连接服务不支持状态修改'},{status:'unavailable',message:'当前无法准备预览'}]){
  const h=harness();await h.api.openLife();h.get('lifeText').value='新昵称';const gate=h.defer('/api/life/prepare'),preparing=h.click('lifePrepare');await gate.entered;gate.release(response);await preparing;
  assert.equal(h.title(),'QQ 生活与联网搜索');assert.equal(h.get('modalBody').querySelector('#lifeConfirmed'),null);assert.equal(h.get('modalOk').classList.contains('hidden'),true);
  assert.equal(h.save(),undefined);assert.equal(h.requests('/api/life/confirm').length,0);assert.equal(h.requests('/api/life/cancel').length,0);assert.ok(h.toasts.includes(response.message));
 }
});

test('untrusted persona, search and review text is escaped; active URLs must pass the allowed protocol',async()=>{
 const h=harness(),bad='<img src=x onerror=alert(1)>';h.setPrompt('</textarea>'+bad);await h.api.loadPersonas();await h.button('[data-persona-preview]').fire('click');
 assert.match(h.get('modalBody').innerHTML,/&lt;\/textarea&gt;&lt;img/);assert.doesNotMatch(h.get('modalBody').innerHTML,/<img src=x/);
 await h.api.openLife();h.setSearch([{url:'javascript:alert(1)',title:bad,snippet:bad},{url:'https://example.com/?x=1&y=2',title:bad,snippet:bad}]);h.get('lifeQuery').value='关键词';await h.click('lifeSearchRun');
 assert.doesNotMatch(h.get('lifeSearchResults').innerHTML,/javascript:|<img src=x/);assert.match(h.get('lifeSearchResults').innerHTML,/&lt;img/);
 h.setReview({before:bad,after:bad,target_bot:bad,visibility_label:bad});await h.click('lifePrepare');assert.doesNotMatch(h.get('modalBody').innerHTML,/<img src=x/);assert.match(h.get('modalBody').innerHTML,/&lt;img/);
});

test('edits during a rules GET or save survive the stale response instead of silently restoring old input',async()=>{
 const h=harness();await h.api.loadTurns();await h.button('[data-timing-mode="fixed"]','conversationTurnPanel').fire('click');const gate=h.defer('/api/companion'),loading=h.api.loadTurns();await gate.entered;
 h.get('turnWaitSeconds').value='23';await h.get('turnWaitSeconds').fire('input');gate.release();await loading;assert.equal(h.get('turnWaitSeconds').value,'23');
 const save=h.defer('/api/companion'),saving=h.click('turnSave');await save.entered;h.get('turnWaitSeconds').value='27';await h.get('turnWaitSeconds').fire('input');save.release();await saving;
 assert.equal(h.get('turnWaitSeconds').value,'27');assert.equal(h.requests('/api/companion').filter(call=>call.method==='POST').at(-1).body.collection_seconds,23);assert.match(h.get('turnNotice').textContent,/尚未保存/);
});

test('a returned search is not shown under a subsequently edited query, and a late life GET does not steal another modal',async()=>{
 const h=harness();await h.api.openLife();h.get('lifeQuery').value='旧关键词';const gate=h.defer('/api/life/search'),search=h.click('lifeSearchRun');await gate.entered;h.get('lifeQuery').value='新关键词';gate.release();await search;
 assert.match(h.get('lifeSearchResults').textContent,/关键词已改变/);assert.equal(h.get('lifeSearchResults').querySelectorAll('a').length,0);
 const life=h.defer('/api/life'),opening=h.api.openLife();await life.entered;h.shell.openModal('其他设置','<p>保留此窗口</p>',null);life.release();await opening;assert.equal(h.title(),'其他设置');
});

test('rhythm and group choices work without advanced numbers and page loading performs only reads',async()=>{
 const h=harness();assert.equal(h.get('turnSave').disabled,true);assert.equal(h.get('groupSave').disabled,true);
 assert.equal(h.get('turnAdvanced').attrs.open,undefined);assert.equal(h.get('groupAdvanced').attrs.open,undefined);
 await h.api.loadView('settings');assert.ok(h.calls.every(call=>call.method==='GET'));assert.equal(h.get('turnTimingMode').value,'adaptive');assert.equal(h.get('groupReplyMode').value,'auto');
 const modes=h.get('conversationTurnPanel').querySelectorAll('[data-timing-mode]');assert.deepEqual(modes.map(b=>b.dataset.timingMode),['adaptive','fixed','instant']);assert.equal(modes[0].attrs['aria-pressed'],'true');
 const groups=h.get('groupRulesPanel').querySelectorAll('[data-group-mode]');assert.deepEqual(groups.map(b=>b.dataset.groupMode),['auto','addressed','quiet','light']);
 assert.equal(groups.at(-1).parentNode.id,'groupAdvanced');assert.equal(h.get('turnFixedFields').classList.contains('hidden'),true);assert.equal(h.get('groupLegacyFields').classList.contains('hidden'),true);
 assert.match(h.get('turnAdaptiveExamples').textContent,/轻松闲聊/);assert.match(h.get('turnAdaptiveExamples').textContent,/问题已经完整/);assert.match(h.get('turnAdaptiveExamples').textContent,/继续补充/);assert.match(h.get('turnAdaptiveExamples').textContent,/急事/);
});

test('mode changes preserve an explicit collection pause and bubble preference and never grant other capabilities',async()=>{
 const h=harness();h.setTurns({message_collection_enabled:false,natural_bubbles_enabled:false});await h.api.loadTurns();assert.equal(h.get('turnPausedNotice').classList.contains('hidden'),false);
 for(const mode of ['fixed','instant','adaptive']){
  await h.button(`[data-timing-mode="${mode}"]`,'conversationTurnPanel').fire('click');const before=h.calls.length;assert.equal(h.get('turnTimingMode').value,mode);assert.equal(h.calls.length,before);
  await h.click('turnSave');const body=h.requests('/api/companion').filter(call=>call.method==='POST').at(-1).body;
  assert.equal(body.reply_timing_mode,mode);assert.equal(body.message_collection_enabled,false);assert.equal(body.natural_bubbles_enabled,false);assert.equal('collection_seconds' in body,mode==='fixed');if(mode==='fixed')assert.equal(body.collection_seconds,10);
  assert.ok(!['enabled','proactive_enabled','activity_sensing_enabled','persona_profile','rhythm','group_reply_mode','owner_qq'].some(key=>key in body));assert.equal(h.get('turnPausedNotice').classList.contains('hidden'),false);
 }
 assert.ok(h.calls.every(call=>['/api/companion'].includes(call.url)));
});

test('all three group modes save just the selected rule and names, leaving legacy rate choices out',async()=>{
 const h=harness();await h.api.loadTurns();
 for(const mode of ['addressed','quiet','auto']){
  await h.button(`[data-group-mode="${mode}"]`,'groupRulesPanel').fire('click');assert.equal(h.get('groupReplyMode').value,mode);await h.click('groupSave');
  assert.deepEqual(h.requests('/api/companion').filter(call=>call.method==='POST').at(-1).body,{group_reply_mode:mode,group_wake_words:'小鱼'});
 }
 assert.match(h.get('groupModeHelp').textContent,/热闹/);assert.ok(h.calls.every(call=>call.url==='/api/companion'));
});

test('existing light mode retains its numbers while the recommended mode needs no legacy tuning',async()=>{
 const h=harness();h.setTurns({group_reply_mode:'light',group_cooldown_seconds:240,group_hourly_limit:4,group_participation_probability:2});await h.api.loadTurns();
 assert.equal(h.get('groupLegacyFields').classList.contains('hidden'),false);assert.equal(h.button('[data-group-mode="light"]','groupRulesPanel').attrs['aria-pressed'],'true');await h.click('groupSave');
 assert.deepEqual(h.requests('/api/companion').filter(call=>call.method==='POST').at(-1).body,{group_reply_mode:'light',group_wake_words:'小鱼',group_cooldown_seconds:240,group_hourly_limit:4,group_participation_probability:2});
 h.get('groupProbability').value='invalid';await h.get('groupProbability').fire('input');await h.button('[data-group-mode="auto"]','groupRulesPanel').fire('click');await h.click('groupSave');
 assert.deepEqual(h.requests('/api/companion').filter(call=>call.method==='POST').at(-1).body,{group_reply_mode:'auto',group_wake_words:'小鱼'});assert.equal(h.get('groupLegacyFields').classList.contains('hidden'),true);
});

test('a dirty group draft survives saving rhythm settings and a dirty rhythm draft survives saving group settings',async()=>{
 const h=harness();await h.api.loadTurns();await h.button('[data-group-mode="quiet"]','groupRulesPanel').fire('click');h.get('groupWakeWords').value='大肥鱼,鱼鱼';await h.get('groupWakeWords').fire('input');
 await h.button('[data-timing-mode="instant"]','conversationTurnPanel').fire('click');await h.click('turnSave');assert.equal(h.get('groupReplyMode').value,'quiet');assert.equal(h.get('groupWakeWords').value,'大肥鱼,鱼鱼');assert.match(h.get('groupNotice').textContent,/未保存/);
 await h.button('[data-timing-mode="fixed"]','conversationTurnPanel').fire('click');h.get('turnWaitSeconds').value='17';await h.get('turnWaitSeconds').fire('input');await h.click('groupSave');
 assert.equal(h.get('turnTimingMode').value,'fixed');assert.equal(h.get('turnWaitSeconds').value,'17');assert.match(h.get('turnNotice').textContent,/未保存/);
});

test('selection changed during a pending save stays unsaved and a second save cannot race it',async()=>{
 const h=harness();await h.api.loadTurns();await h.button('[data-timing-mode="fixed"]','conversationTurnPanel').fire('click');const gate=h.defer('/api/companion'),saving=h.click('turnSave');await gate.entered;
 assert.equal(h.get('groupSave').disabled,true);await h.click('groupSave');assert.equal(h.requests('/api/companion').filter(call=>call.method==='POST').length,1);
 await h.button('[data-timing-mode="instant"]','conversationTurnPanel').fire('click');await h.button('[data-group-mode="quiet"]','groupRulesPanel').fire('click');gate.release();await saving;
 assert.equal(h.get('turnTimingMode').value,'instant');assert.equal(h.get('groupReplyMode').value,'quiet');assert.match(h.get('turnNotice').textContent,/尚未保存/);assert.match(h.get('groupNotice').textContent,/未保存/);assert.equal(h.get('groupSave').disabled,false);
});

test('a failed save is not retried and releases both save controls without erasing the draft',async()=>{
 const h=harness();await h.api.loadTurns();await h.button('[data-timing-mode="instant"]','conversationTurnPanel').fire('click');const gate=h.defer('/api/companion'),saving=h.click('turnSave');await gate.entered;gate.reject(new Error('保存失败，请稍后重试'));await saving;
 assert.equal(h.requests('/api/companion').filter(call=>call.method==='POST').length,1);assert.equal(h.get('turnTimingMode').value,'instant');assert.equal(h.get('turnSave').disabled,false);assert.equal(h.get('groupSave').disabled,false);assert.match(h.toasts.at(-1),/保存失败/);
});

test('invalid modes and invalid active advanced values fail locally without any POST',async()=>{
 const h=harness();await h.api.loadTurns();h.get('turnTimingMode').value='admin';await h.click('turnSave');assert.match(h.toasts.at(-1),/请选择/);
 h.get('groupReplyMode').value='__proto__';await h.click('groupSave');assert.match(h.toasts.at(-1),/请选择/);
 await h.button('[data-timing-mode="fixed"]','conversationTurnPanel').fire('click');h.get('turnWaitSeconds').value='';await h.click('turnSave');assert.match(h.toasts.at(-1),/固定等待/);
 await h.button('[data-group-mode="light"]','groupRulesPanel').fire('click');h.get('groupProbability').value='21';await h.click('groupSave');assert.match(h.toasts.at(-1),/参与机会/);assert.equal(h.requests('/api/companion').filter(call=>call.method==='POST').length,0);
});

test('read failures leave saves disabled and a late read cannot replace newly selected modes',async()=>{
 const h=harness(),failed=h.defer('/api/companion'),reading=h.api.loadTurns();await failed.entered;failed.reject(new Error('暂时无法读取'));await assert.rejects(reading,/无法读取/);
 await h.click('turnSave');assert.equal(h.requests('/api/companion').filter(call=>call.method==='POST').length,0);assert.equal(h.get('turnSave').disabled,true);
 await h.api.loadTurns();const gate=h.defer('/api/companion'),late=h.api.loadTurns();await gate.entered;await h.button('[data-group-mode="quiet"]','groupRulesPanel').fire('click');await h.button('[data-timing-mode="instant"]','conversationTurnPanel').fire('click');gate.release();await late;
 assert.equal(h.get('groupReplyMode').value,'quiet');assert.equal(h.get('turnTimingMode').value,'instant');
});

test('recent decisions are bounded, escaped, content-free, and heartbeat does not imply account authorization',async()=>{
 const h=harness(),bad='<img src=x onerror=alert(1)>',secret='PRIVATE_CHAT_BODY_AND_QQ';h.setTurns({persona_profile:'gentle',rhythm:{worker_running:false,recent:[null,...Array.from({length:12},()=>({at:1762220000,reason_label:bad,reason:'urgent',wait_seconds:1.25,profile:'gentle',group:true,content:secret,qq:secret,umo:secret}))]}});await h.api.loadTurns();
 assert.equal(h.get('turnRecentDecisions').querySelectorAll('li').length,8);assert.doesNotMatch(h.get('turnRecentDecisions').innerHTML,/<img src=x|PRIVATE_CHAT_BODY_AND_QQ/);assert.match(h.get('turnRecentDecisions').innerHTML,/&lt;img/);assert.match(h.get('turnRecentDecisions').textContent,/等待 1.3 秒/);assert.match(h.get('turnRecentDecisions').textContent,/温柔陪伴/);
 assert.match(h.get('turnRuntimeNotice').textContent,/不能据此确认/);assert.match(h.get('turnPersonaRhythm').textContent,/温柔陪伴/);
 h.setTurns({rhythm:{worker_running:true,recent:[]}});await h.api.loadTurns();assert.match(h.get('turnRuntimeNotice').textContent,/真实会话权限/);assert.match(h.get('turnRecentDecisions').textContent,/没有近期决策/);
});

test('all five saved persona profiles explain their different rhythms without writing a persona or permission',async()=>{
 const h=harness();for(const [profile,name] of [['natural','自然朋友'],['classic','经典肥鱼'],['gentle','温柔陪伴'],['playful','元气搭子'],['study','冷静学伴']]){h.setTurns({persona_profile:profile});await h.api.loadTurns();assert.match(h.get('turnPersonaRhythm').textContent,new RegExp(name));}
 assert.ok(h.calls.every(call=>call.url==='/api/companion'&&call.method==='GET'));
});

test('a successful settings write followed by a failed refresh reports the saved result and never resubmits',async()=>{
 const h=harness();await h.api.loadTurns();await h.button('[data-timing-mode="instant"]','conversationTurnPanel').fire('click');const write=h.defer('/api/companion'),saving=h.click('turnSave');await write.entered;
 const refresh=h.defer('/api/companion');write.release();await refresh.entered;refresh.reject(new Error('读取失败'));await saving;
 assert.equal(h.requests('/api/companion').filter(call=>call.method==='POST').length,1);assert.equal(h.get('turnTimingMode').value,'instant');assert.match(h.get('turnNotice').textContent,/设置已保存/);assert.match(h.get('turnNotice').textContent,/无法刷新/);assert.equal(h.get('turnSave').disabled,false);
});

test('applying a persona refreshes its matching rhythm without replacing an unsaved mode',async()=>{
 const h=harness();await h.api.loadView('settings');await h.button('[data-timing-mode="instant"]','conversationTurnPanel').fire('click');await h.button('[data-persona-preview]').fire('click');h.setTurns({persona_profile:'gentle'});await h.save();
 assert.match(h.get('turnPersonaRhythm').textContent,/温柔陪伴/);assert.equal(h.get('turnTimingMode').value,'instant');assert.match(h.get('turnNotice').textContent,/未保存/);assert.equal(h.requests('/api/personas/apply').length,1);
});
