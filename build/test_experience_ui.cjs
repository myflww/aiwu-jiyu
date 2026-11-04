// Public experience handlers with an isolated DOM/API simulation. No browser/network.
const runTest=require('node:test');
const test=(name,fn)=>runTest(name,{timeout:4000},fn);
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/ui/experience.js'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
const settle=async()=>{for(let i=0;i<4;i++)await new Promise(resolve=>setImmediate(resolve));};
const decode=value=>String(value).replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
const escape=value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');

function harness(){
 const nodes=new Map(),calls=[],toasts=[],views=[],documentEvents={},windowEvents={},gates=[],modals=[];
 let modalGeneration=0,modalOpen=false,saveModal=null,library='library-a',connected=true;
 let appState={preview:false,status:{astrbot:{up:true},napcat:{up:true}},napcat:{logged_in:true}};
 let companion={profile:'custom',daily_limit:4,proactive_enabled:true,profiles:{quiet:{label:'安静陪伴',description:'留出安静'},natural:{label:'自然朋友',description:'自然节奏'}},runtime:{code:'waiting_interval'}};
 let taskData={active:[],recent:[],source:'tool_journal'},backupData={current_version:'1.4.0',backups:[]};
 let healthData={checked_at:1700000000,summary:'实际连接待检查',checks:[{id:'qq',label:'QQ 登录',status:'unknown',message:'尚未确认',evidence:{logged_in:null},actions:[{code:'open_overview',label:'前往首页'}]}]};
 const catalogs=new Map(),records=new Map();
 const match=(node,selector)=>selector.split(',').some(selector=>{
  selector=selector.trim();if(selector.startsWith('.'))return node.classList.contains(selector.slice(1));
  if(selector.startsWith('#'))return node.id===selector.slice(1);
  const attr=selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);if(attr)return Object.hasOwn(node.attrs,attr[1])&&(attr[2]===undefined||node.attrs[attr[1]]===attr[2]);
  return node.tagName===selector.toUpperCase();
 });
 class Element{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.parentNode=null;this.attrs={};this.dataset={};this.style={};this.listeners={};this.value='';this.checked=false;this.disabled=false;this._html='';this._text='';this._id='';
   const classes=new Set();this.classList={add:(...values)=>values.forEach(v=>classes.add(v)),remove:(...values)=>values.forEach(v=>classes.delete(v)),contains:v=>classes.has(v),toggle(v,on){on=on??!classes.has(v);on?classes.add(v):classes.delete(v);return on;}};
  }
  set id(value){if(this._id&&nodes.get(this._id)===this)nodes.delete(this._id);this._id=String(value);if(value)nodes.set(this._id,this);}get id(){return this._id;}
  set className(value){String(value).split(/\s+/).filter(Boolean).forEach(v=>this.classList.add(v));}
  setAttribute(name,value){value=String(value);this.attrs[name]=value;if(name==='id')this.id=value;if(name==='class')this.className=value;if(name==='value')this.value=value;
   if(name.startsWith('data-'))this.dataset[name.slice(5).replace(/-([a-z])/g,(_,v)=>v.toUpperCase())]=value;
   if(name==='checked')this.checked=true;if(name==='disabled')this.disabled=true;if(name==='readonly')this.readOnly=true;if(['src','href','title','type'].includes(name))this[name]=value;
  }
  getAttribute(name){return this.attrs[name]??null;}removeAttribute(name){delete this.attrs[name];}
  appendChild(child){child.detach();child.parentNode=this;this.children.push(child);return child;}append(...children){children.forEach(child=>this.appendChild(child));}
  prepend(child){child.detach();child.parentNode=this;this.children.unshift(child);}
  detach(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(item=>item!==this);this.parentNode=null;}
  after(child){if(!this.parentNode)return;const parent=this.parentNode,index=parent.children.indexOf(this);child.detach();child.parentNode=parent;parent.children.splice(index+1,0,child);}
  unregister(){for(const child of this.children)child.unregister();if(this.id&&nodes.get(this.id)===this)nodes.delete(this.id);}
  remove(){this.unregister();this.detach();}
  replaceChildren(){this.children.forEach(child=>child.unregister());this.children=[];this._text='';this._html='';}
  set innerHTML(value){this.replaceChildren();this._html=String(value);parse(this._html,this);}get innerHTML(){return this._html;}
  set textContent(value){this.replaceChildren();this._text=String(value);this._html=escape(value);}get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
  querySelectorAll(selector){const found=[];const walk=node=>{for(const child of node.children){if(match(child,selector))found.push(child);walk(child);}};walk(this);return found;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}closest(selector){for(let node=this;node;node=node.parentNode)if(match(node,selector))return node;return null;}
  addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}scrollIntoView(){}focus(){}
  get isConnected(){for(let node=this;node;node=node.parentNode)if(node===document.body)return true;return false;}
  async fire(name,extra={}){if(name==='click'&&this.disabled)return;const event={type:name,target:this,currentTarget:this,preventDefault(){},...extra};
   for(const fn of [...(this.listeners[name]||[]),...(name==='click'&&this.onclick?[this.onclick]:[])])await fn(event);
   for(const fn of documentEvents[name]||[])await fn(event);await settle();
  }
 }
 function parse(html,parent){const stack=[parent],voids=new Set(['input','img','br','hr','meta','link']);
  for(const token of html.matchAll(/<\/?[a-zA-Z][^>]*>|[^<]+/g)){const value=token[0],node=stack.at(-1);
   if(value.startsWith('</')){if(stack.length>1)stack.pop();continue;}
   if(!value.startsWith('<')){if(node!==parent){node._text+=decode(value);if(node.tagName==='TEXTAREA')node.value+=decode(value);}continue;}
   const open=value.match(/^<([\w-]+)([\s\S]*?)\/?\s*>$/);if(!open)continue;const child=new Element(open[1]);
   for(const attr of open[2].matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g))child.setAttribute(attr[1],decode(attr[2]??attr[3]??attr[4]??''));
   node.appendChild(child);if(child.tagName==='OPTION'&&node.tagName==='SELECT'&&(!node.value||Object.hasOwn(child.attrs,'selected')))node.value=child.value;
   if(!voids.has(open[1])&&!value.endsWith('/>'))stack.push(child);
  }
 }
 const document={body:new Element('body'),createElement:tag=>new Element(tag),getElementById:id=>nodes.get(id)||null,addEventListener:(key,fn)=>(documentEvents[key]??=[]).push(fn)};
 for(const id of ['view-overview','view-logs','view-artifacts','view-settings','view-lovehouse','view-whitelist','view-plugins','modalBody','modalTitle','modalOk','modalCancel','btnStart','btnRestart','heroMascot','sidebarMascotCaption']){const element=new Element(id.startsWith('modalO')||id==='modalCancel'?'button':'div');element.id=id;document.body.appendChild(element);}
 const sidebar=new Element('div');sidebar.className='sidebar-art';document.body.appendChild(sidebar);const mascot=new Element('img');mascot.id='sidebarMascot';sidebar.appendChild(mascot);
 const hero=new Element('div');hero.className='companion-hero';nodes.get('view-overview').appendChild(hero);
 const window={addEventListener:(key,fn)=>(windowEvents[key]??=[]).push(fn)};
 const defaultCatalog=()=>({entries:[{id:'memory-one',title:'早晨喝茶',layer:'preference',enabled:true,important:false,recorded_at:1700000000}],revision:'revision-a',next_cursor:null,total:1});
 function route(url,body){
  if(url==='/api/companion')return clone(companion);
  if(url==='/api/companion/profile'){companion.profile=body.profile;return clone(companion);}
  if(url==='/api/companion/temporary'){companion.temporary_preference={active:body.mode!=='normal',mode:body.mode,label:'今天的节奏',expires_at:1700003600};return clone(companion);}
  if(url==='/api/tasks')return clone(taskData);
  if(url.startsWith('/api/health?'))return clone(healthData);
  if(url==='/api/recovery')return clone(backupData);
  if(['/api/recovery/backup','/api/recovery/restore','/api/recovery/rollback','/api/artifacts/open'].includes(url))return {ok:true};
  if(url==='/api/memories/catalog')return clone(catalogs.get(body.library_id+'|'+(body.cursor||''))||defaultCatalog());
  if(url==='/api/memories/read')return clone(records.get(body.id)||{complete:true,text:'这是一条真实的回忆',revision:'detail-revision',record:{display:{title:'偏好'},sources:[{kind:'confirmed'}]}});
  if(url==='/api/memories/add')return {ok:true};
  if(url==='/api/memories/mutate')return body.action==='forget-preview'?{ok:true,preview:{node_count:1,archive_count:2,backup_count:1,note:'永久删除'},preview_token:'preview-token-one'}:{ok:true};
  throw new Error('Unexpected fixture API '+url);
 }
 async function api(url,options={}){const body=JSON.parse(options.body||'{}');calls.push({url,body:clone(body),method:options.method||'GET'});const value=route(url,body);const at=gates.findIndex(gate=>gate.url===url);if(at>=0){const gate=gates.splice(at,1)[0];gate.enter();const override=await gate.promise;return override===undefined?value:override;}return value;}
 const get=id=>{const node=nodes.get(id);assert.ok(node,'missing '+id);return node;};
 const shell={api,esc:escape,toast:value=>toasts.push(String(value)),getState:()=>appState,isConnected:()=>connected,getLibrary:()=>library,
  showView:view=>views.push(view),openCompanion:()=>shell.openModal('陪伴设置','<p>陪伴</p>',null),openStickers:()=>shell.openModal('配图设置','<p>素材</p>',null),openComputer:()=>shell.openModal('电脑权限','<p>权限</p>',null),
  openModal(title,html,save){modalGeneration++;modalOpen=true;modals.push(title);get('modalTitle').textContent=title;get('modalBody').innerHTML=html;get('modalOk').disabled=false;get('modalOk').classList.remove('danger','hidden');get('modalCancel').textContent='取消';saveModal=save;return modalGeneration;},
  closeModal(){modalGeneration++;modalOpen=false;saveModal=null;get('modalBody').innerHTML='';},
  getModalGeneration:()=>modalGeneration,modalGeneration:()=>modalGeneration,isModalCurrent:token=>token===modalGeneration&&modalOpen,isModalOpen:()=>modalOpen,
 };
 const context=vm.createContext({document,window,console,Date,Map,Set,URL,URLSearchParams,encodeURIComponent,setTimeout,clearTimeout});vm.runInContext(source,context);window.AiwuExperience.init(shell);
 return {api:window.AiwuExperience,shell,calls,toasts,views,modals,get,document,
  setState:value=>{appState=clone(value);},setConnected:value=>{connected=value;},setTasks:value=>{taskData=clone(value);},setBackups:value=>{backupData=clone(value);},setHealth:value=>{healthData=clone(value);},
  setCompanion:value=>{companion={...companion,...clone(value)};window.AiwuExperience.setCompanion(companion);},setLibrary:value=>{library=value;},catalog:(lib,cursor,data)=>catalogs.set(lib+'|'+cursor,clone(data)),record:(id,value)=>records.set(id,clone(value)),
  button:(selector,id)=>{const matches=(id?get(id):document.body).querySelectorAll(selector);assert.ok(matches.length,'missing '+selector);return matches[0];},
  async click(id){await get(id).fire('click');},async action(name){await this.button('[data-exp-action="'+name+'"]').fire('click');},
  save:()=>saveModal?.(),modalTitle:()=>get('modalTitle').textContent,modalGeneration:()=>modalGeneration,
  hide:()=>{for(const fn of windowEvents.pagehide||[])fn();},
  requests:url=>calls.filter(call=>call.url===url),
  defer(url){let release,reject,enter;const entered=new Promise(resolve=>{enter=resolve;}),promise=new Promise((yes,no)=>{release=yes;reject=no;});gates.push({url,promise,enter});return {entered,release,reject};},
 };
}

test('runtime facts choose resting, connecting, awake, quiet and working; disconnection outranks a stale active record',async()=>{
 const h=harness();h.setState({status:{astrbot:{up:false},napcat:{up:false}},napcat:{logged_in:false}});h.api.paintState();assert.equal(h.document.body.dataset.mascotState,'resting');
 h.setState({status:{astrbot:{up:true},napcat:{up:true}},napcat:{logged_in:false}});h.api.paintState();assert.equal(h.document.body.dataset.mascotState,'connecting');
 h.setState({status:{astrbot:{up:true},napcat:{up:true}},napcat:{logged_in:true}});h.api.paintState();assert.equal(h.document.body.dataset.mascotState,'awake');
 h.setCompanion({runtime:{code:'quiet_hours'}});assert.equal(h.document.body.dataset.mascotState,'quiet');
 h.setTasks({active:[{id:'task-a',label:'真实任务',status:'running',started_at:1700000000}],recent:[]});await h.api.loadTasks();assert.equal(h.document.body.dataset.mascotState,'working');
 h.setConnected(false);h.api.paintState();assert.equal(h.document.body.dataset.mascotState,'repair');assert.doesNotMatch(h.get('taskList').innerHTML,/\d+%/);
});

test('tool steps deduplicate active/recent and open only the returned relative artifact through the existing endpoint',async()=>{
 const h=harness(),row={id:'task-a',action:'write',label:'写入作品',status:'completed',started_at:1700000000,ended_at:1700000010,artifact:'folder/result.txt',summary:'实际保存'};
 h.setTasks({active:[],recent:[row,row]});await h.api.loadTasks();assert.equal(h.get('taskList').querySelectorAll('[data-task-artifact]').length,1);
 await h.button('[data-task-artifact]','taskList').fire('click');assert.deepEqual(h.requests('/api/artifacts/open')[0].body,{path:'folder/result.txt'});
 h.setTasks({active:[],recent:[]});await h.api.loadTasks();assert.equal(h.get('taskList').querySelectorAll('[data-task-artifact]').length,0);
});

test('backup compatibility is visible; restore needs consent, and rollback download explicitly confirms the selected id',async()=>{
 const h=harness();h.setBackups({current_version:'1.4.0',backups:[{id:'old',source_version:'1.3.2',compatible:false,created_at:1700000000},{id:'same',source_version:'1.4.0',compatible:true,created_at:1700000001}]});await h.api.loadBackups();
 const restore=h.get('recoveryList').querySelectorAll('[data-backup-restore]');assert.equal(restore[0].disabled,true);assert.equal(restore[1].disabled,false);
 await restore[1].fire('click');await assert.rejects(()=>h.save(),/确认/);assert.equal(h.requests('/api/recovery/restore').length,0);
 h.get('expRestoreConfirm').checked=true;await h.save();assert.deepEqual(h.requests('/api/recovery/restore')[0].body,{backup_id:'same',confirmed:true});
 await h.button('[data-backup-download]','recoveryList').fire('click');assert.equal(h.requests('/api/recovery/rollback').length,0);await h.save();assert.deepEqual(h.requests('/api/recovery/rollback')[0].body,{backup_id:'old',confirmed:true});
});

test('health is opt-in, uses a fixed navigation action and escapes all source data instead of executing it',async()=>{
 const h=harness();assert.equal(h.calls.length,0);const danger='<img src=x onerror=alert(1)>';
 h.setHealth({summary:danger,checks:[{label:danger,status:'unknown',message:danger,evidence:{note:danger},actions:[{code:'run_command',label:danger,target:'powershell bad'}]}]});await h.api.loadHealth();
 assert.match(h.get('healthResult').innerHTML,/&lt;img/);assert.doesNotMatch(h.get('healthResult').innerHTML,/<img src=x/);
 await h.button('[data-health-action]','healthResult').fire('click');assert.deepEqual(h.views,[]);assert.equal(h.calls.length,1);
});

test('explicit daily preference sends a bounded temporary choice and profile selection preserves the capability endpoint',async()=>{
 const h=harness();await h.api.loadCompanion();await h.action('quiet');assert.deepEqual(h.requests('/api/companion/temporary')[0].body,{mode:'quiet',until_end_of_day:true});
 await h.button('[data-exp-profile="natural"]','dailyProfileOptions').fire('click');assert.deepEqual(h.requests('/api/companion/profile')[0].body,{profile:'natural'});
 assert.equal(h.requests('/api/companion/profile')[0].body.proactive_enabled,undefined);
});

test('memory pages deduplicate ids and never mix a late page from a previously selected library',async()=>{
 const h=harness();h.catalog('library-a','',{entries:[{id:'a',title:'旧库'}],revision:'r-a',next_cursor:'next-a',total:2});h.catalog('library-a','next-a',{entries:[{id:'a',title:'旧库'},{id:'b',title:'第二页'}],revision:'r-a',total:2});
 await h.api.loadMemories();await h.click('memoryGardenMore');assert.equal(h.get('memoryGardenList').querySelectorAll('[data-memory-read]').length,2);
 const gate=h.defer('/api/memories/catalog');const late=h.api.loadMemories();await gate.entered;h.setLibrary('library-b');h.catalog('library-b','',{entries:[{id:'c',title:'新库'}],revision:'r-b',total:1});await h.api.loadMemories();gate.release();await late;
 assert.match(h.get('memoryGardenList').innerHTML,/新库/);assert.doesNotMatch(h.get('memoryGardenList').innerHTML,/旧库/);
});

test('truncated memory is view-only and cannot replace unseen text with a short correction',async()=>{
 const h=harness();h.record('memory-one',{complete:false,text:'只展示前半段',revision:'r-view',record:{display:{title:'较长正文'},sources:['真实来源']}});await h.api.loadMemories();await h.button('[data-memory-read]','memoryGardenList').fire('click');
 assert.equal(h.get('expMemoryText').readOnly,true);assert.equal(h.get('modalOk').disabled,true);await assert.rejects(()=>h.save(),/完整原文/);assert.equal(h.requests('/api/memories/mutate').length,0);
});

test('forgetting requires a fetched scope preview and then separate explicit consent with that preview token',async()=>{
 const h=harness();await h.api.loadMemories();await h.button('[data-memory-read]','memoryGardenList').fire('click');await h.click('expMemoryForget');
 assert.match(h.modalTitle(),/永久忘记/);await assert.rejects(()=>h.save(),/确认/);assert.equal(h.requests('/api/memories/mutate').filter(c=>c.body.action==='forget').length,0);
 h.get('expForgetConfirmed').checked=true;await h.save();assert.deepEqual(h.requests('/api/memories/mutate').find(c=>c.body.action==='forget').body,{library_id:'library-a',id:'memory-one',action:'forget',preview_token:'preview-token-one',confirm:true});
});

test('memory contents, source records, titles and ids are escaped in cards and editor controls',async()=>{
 const h=harness(),evil='<img src=x onerror=bad()>';h.catalog('library-a','',{entries:[{id:'memory-one',title:evil,interpretation:evil}],revision:'r',total:1});h.record('memory-one',{complete:true,text:'</textarea>'+evil,revision:'r',record:{display:{title:'" autofocus '+evil},sources:[evil]}});
 await h.api.loadMemories();assert.doesNotMatch(h.get('memoryGardenList').innerHTML,/<img src=x/);await h.button('[data-memory-read]','memoryGardenList').fire('click');assert.doesNotMatch(h.get('modalBody').innerHTML,/<img src=x/);assert.match(h.get('expMemoryText').value,/<img src=x/);
});

test('a late memory detail may not replace an unrelated modal opened while it was loading',async()=>{
 const h=harness();await h.api.loadMemories();const gate=h.defer('/api/memories/read');const opening=h.button('[data-memory-read]','memoryGardenList').fire('click');await gate.entered;h.shell.openModal('另一个设置','<p>保留这里</p>',null);gate.release();await opening;
 assert.equal(h.modalTitle(),'另一个设置');
});

test('a changed library invalidates an existing memory correction before any mutation',async()=>{
 const h=harness();await h.api.loadMemories();await h.button('[data-memory-read]','memoryGardenList').fire('click');h.setLibrary('library-b');await assert.rejects(()=>h.save(),/记忆库/);assert.equal(h.requests('/api/memories/mutate').length,0);
});

test('a late forget preview cannot reopen a closed editor or replace another modal',async()=>{
 const h=harness();await h.api.loadMemories();await h.button('[data-memory-read]','memoryGardenList').fire('click');const gate=h.defer('/api/memories/mutate');const pending=h.click('expMemoryForget');await gate.entered;h.shell.closeModal();h.shell.openModal('其他页面','<p>继续设置</p>',null);gate.release();await pending;assert.equal(h.modalTitle(),'其他页面');
});

test('pagehide prevents late readings from painting another session',async()=>{
 const h=harness(),gate=h.defer('/api/tasks');const pending=h.api.loadTasks();await gate.entered;const before=h.get('taskList').innerHTML;h.hide();gate.release({active:[{id:'bad',status:'running',label:'过期任务'}],recent:[]});await pending;assert.equal(h.get('taskList').innerHTML,before);
});


test('a background companion read cannot undo a newer explicitly selected profile',async()=>{
 const h=harness();h.setCompanion({profile:'quiet'});const gate=h.defer('/api/companion');const pending=h.api.loadCompanion();await gate.entered;
 await h.button('[data-exp-profile="natural"]','dailyProfileOptions').fire('click');assert.match(h.get('dailyPreferenceStatus').textContent,/自然朋友/);
 gate.release();await pending;assert.match(h.get('dailyPreferenceStatus').textContent,/自然朋友/);
});
