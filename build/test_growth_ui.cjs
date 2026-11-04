// Private evidence and reversible learning controls, without real services.
const runTest=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const test=(name,fn)=>runTest(name,{timeout:4000},fn);
const source=fs.readFileSync(path.join(__dirname,'../app/ui/growth.js'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value)),settle=async()=>{for(let i=0;i<4;i++)await new Promise(r=>setImmediate(r));};
const escape=value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const decode=value=>String(value).replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
function harness(){
 const nodes=new Map(),calls=[],toasts=[],gates=[],events={};let saveModal=null,modalCloses=0;
 let status={ok:true,enabled:true,counts:{feedback:1,pending:1,trial:1},notice:'只有原证据通过时才会使用'};
 let entries=[{id:'feedback-one',revision:'revision-one',kind:'feedback',status:'active',label:'少反问一点',source_quote:'私密但明确的反馈'},
  {id:'thought-one',revision:'revision-two',kind:'reflection',status:'pending',label:'她提出的想法',hypothesis:'她可能希望先把话说完',next_step:'下次先听完整句再答',behavior:'listen_first',evidence_ids:['e1'],trial_until:Date.now()/1000+600}];
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

 const page=add('view-lovehouse'),hero=add('hero',page);hero.className='lovehouse-home';
 for(const id of ['modalTitle','modalBody','modalOk','modalCancel'])add(id,document.body,['modalOk','modalCancel'].includes(id)?'button':'div');
 function route(url,body){
  if(url==='/api/growth')return clone(status);
  if(url==='/api/growth/inspect')return {ok:true,entries:clone(entries)};
  if(url==='/api/companion'){assert.deepEqual(Object.keys(body),['growth_enabled']);status.enabled=body.growth_enabled;return {ok:true};}
  if(url==='/api/growth/mutate'){const row=entries.find(x=>x.id===body.id);assert.ok(row);assert.equal(body.confirmed,true);assert.equal(row.revision,body.expected_revision);assert.ok(['approve','pause','resume'].includes(body.action));row.status={approve:'approved',pause:'paused',resume:row.kind==='feedback'?'active':'pending'}[body.action];row.revision+='-next';return {ok:true,message:'手记状态已保存'};}
  throw new Error('Forbidden/unexpected fixture API '+url);
 }
 async function api(url,options={}){const body=JSON.parse(options.body||'{}');calls.push({url,body:clone(body),method:options.method||'GET'});const result=route(url,body),index=gates.findIndex(g=>g.url===url);if(index>=0){const gate=gates.splice(index,1)[0];gate.enter();const replacement=await gate.promise;return replacement===undefined?result:replacement;}return result;}
 const get=id=>{assert.ok(nodes.has(id),'missing '+id);return nodes.get(id);};
 const shell={api,esc:escape,toast:message=>toasts.push(String(message)),openModal(title,html,save){get('modalTitle').textContent=title;get('modalBody').innerHTML=html;saveModal=save;},closeModal(){modalCloses++;get('modalBody').innerHTML='';saveModal=null;}};
 const window={addEventListener:(name,fn)=>(events[name]??=[]).push(fn)},context=vm.createContext({window,document,console,Date});vm.runInContext(source,context);window.AiwuGrowth.init(shell);
 return {api:window.AiwuGrowth,shell,calls,toasts,get,requests:url=>calls.filter(x=>x.url===url),save:()=>saveModal?.(),title:()=>get('modalTitle').textContent,closes:()=>modalCloses,
  setStatus:part=>{status={...status,...clone(part)};},setEntries:rows=>{entries=clone(rows);},click:id=>get(id).fire('click'),
  action(name,index=0){const buttons=get('growthEntries').querySelectorAll('[data-growth-action]');const button=buttons.find(b=>b.dataset.growthAction===name&&Number(b.dataset.growthIndex)===index);assert.ok(button,'missing '+name+' '+index);return button.fire('click');},
  hide(){for(const fn of events.pagehide||[])fn();},
  defer(url){let enter,release,reject;const entered=new Promise(r=>{enter=r;}),promise=new Promise((yes,no)=>{release=yes;reject=no;});gates.push({url,enter,promise});return {entered,release,reject};}};
}

test('view entry reads flags/counts only; private evidence needs the explicit inspect click each visit',async()=>{
 const h=harness();await h.api.loadView('lovehouse');assert.deepEqual(h.calls.map(x=>x.url),['/api/growth']);assert.equal(h.get('growthEntries').textContent,'');
 await h.click('growthInspect');assert.equal(h.requests('/api/growth/inspect').length,1);assert.match(h.get('growthEntries').innerHTML,/私密但明确/);
 await h.api.loadView('settings');assert.equal(h.get('growthEntries').textContent,'');await h.api.loadView('lovehouse');assert.equal(h.requests('/api/growth/inspect').length,1);
 assert.ok(h.calls.every(x=>['/api/growth','/api/growth/inspect'].includes(x.url)));
});

test('pause and resume are direct reversible actions with the shown revision, without an approval modal',async()=>{
 const h=harness();await h.api.loadView('lovehouse');await h.click('growthInspect');await h.action('pause');assert.equal(h.title(),'');
 assert.deepEqual(h.requests('/api/growth/mutate')[0].body,{id:'feedback-one',action:'pause',expected_revision:'revision-one',confirmed:true});
 await h.action('resume');assert.deepEqual(h.requests('/api/growth/mutate')[1].body,{id:'feedback-one',action:'resume',expected_revision:'revision-one-next',confirmed:true});
 assert.equal(h.requests('/api/growth/inspect').length,3);
});

test('adopting a thought shows its actual hypothesis first and never closes a subsequently opened modal',async()=>{
 const h=harness();await h.api.loadView('lovehouse');await h.click('growthInspect');await h.action('approve',1);
 assert.match(h.get('modalBody').innerHTML,/她可能希望先把话说完/);assert.equal(h.requests('/api/growth/mutate').length,0);
 const gate=h.defer('/api/growth/mutate'),saving=h.save();await gate.entered;h.shell.openModal('另外的设置','<p>保留这个窗口</p>',null);gate.release();await saving;
 assert.equal(h.title(),'另外的设置');assert.equal(h.closes(),0);assert.deepEqual(h.requests('/api/growth/mutate')[0].body,{id:'thought-one',action:'approve',expected_revision:'revision-two',confirmed:true});
});

test('late private reads are discarded after leaving or hiding and never repaint a returned page',async()=>{
 const h=harness();await h.api.loadView('lovehouse');const gate=h.defer('/api/growth/inspect'),reading=h.click('growthInspect');await gate.entered;
 await h.api.loadView('settings');await h.api.loadView('lovehouse');gate.release();await reading;assert.equal(h.get('growthEntries').textContent,'');
 const hidden=h.defer('/api/growth/inspect'),another=h.click('growthInspect');await hidden.entered;h.hide();hidden.release();await another;assert.equal(h.get('growthEntries').textContent,'');
});

test('an old flags GET cannot overwrite a subsequently saved toggle; refresh does not race a pending save',async()=>{
 const h=harness();h.setStatus({enabled:false});await h.api.loadView('lovehouse');const old=h.defer('/api/growth'),reading=h.api.loadView('lovehouse');await old.entered;
 h.get('growthEnabled').checked=true;const save=h.defer('/api/companion'),saving=h.get('growthEnabled').fire('change');await save.entered;
 const before=h.calls.length;await h.api.loadView('lovehouse');assert.equal(h.calls.length,before);assert.equal(h.get('growthEnabled').disabled,true);save.release();await saving;old.release();await reading;
 assert.equal(h.get('growthEnabled').checked,true);assert.equal(h.requests('/api/companion').length,1);assert.equal(h.get('growthEnabled').disabled,false);
});

test('unknown toggle result requires a fresh status read and never silently retries the write',async()=>{
 const h=harness();h.setStatus({enabled:false});await h.api.loadView('lovehouse');h.get('growthEnabled').checked=true;
 const gate=h.defer('/api/companion'),saving=h.get('growthEnabled').fire('change');await gate.entered;gate.reject(new Error('network interrupted'));await saving;
 assert.equal(h.get('growthEnabled').disabled,true);assert.match(h.get('growthSummary').textContent,/未确认/);assert.equal(h.requests('/api/companion').length,1);
 await h.click('growthRefresh');assert.equal(h.get('growthEnabled').checked,true);assert.equal(h.get('growthEnabled').disabled,false);assert.equal(h.requests('/api/companion').length,1);
});

test('a successful mutation followed by a failed refresh says saved and does not repeat the mutation',async()=>{
 const h=harness();await h.api.loadView('lovehouse');await h.click('growthInspect');const gate=h.defer('/api/growth/inspect'),pausing=h.action('pause');await gate.entered;gate.reject(new Error('refresh unavailable'));await pausing;
 assert.match(h.get('growthEntries').textContent,/已更新.*重新读取/);assert.equal(h.get('growthEntries').querySelectorAll('[data-growth-action]').length,0);assert.equal(h.requests('/api/growth/mutate').length,1);
 await h.click('growthInspect');assert.equal(h.requests('/api/growth/mutate').length,1);
});

test('mutation errors clear stale actions and require inspect before another revisioned operation',async()=>{
 const h=harness();await h.api.loadView('lovehouse');await h.click('growthInspect');const gate=h.defer('/api/growth/mutate'),pausing=h.action('pause');await gate.entered;gate.reject(new Error('unknown transport result'));await pausing;
 assert.match(h.get('growthEntries').textContent,/未确认/);assert.equal(h.get('growthEntries').querySelectorAll('[data-growth-action]').length,0);assert.equal(h.requests('/api/growth/inspect').length,1);assert.equal(h.requests('/api/growth/mutate').length,1);
});

test('a write completing after page navigation fetches only public flags and never restores old private bodies',async()=>{
 const h=harness();await h.api.loadView('lovehouse');await h.click('growthInspect');const gate=h.defer('/api/growth/mutate'),pausing=h.action('pause');await gate.entered;
 await h.api.loadView('settings');await h.api.loadView('lovehouse');gate.release();await pausing;
 assert.equal(h.requests('/api/growth/inspect').length,1);assert.equal(h.get('growthEntries').textContent,'');assert.equal(h.get('growthEnabled').disabled,false);
});

test('private quotes, hypotheses, plans, notices and error text cannot create markup or executable actions',async()=>{
 const h=harness(),bad='<img src=x onerror=alert(1)>';h.setStatus({notice:bad});h.setEntries([{id:'thought-one',revision:'r',kind:'reflection',status:'pending',label:bad,source_quote:bad,hypothesis:bad,next_step:'run '+bad,evidence_ids:['e']}]);
 await h.api.loadView('lovehouse');assert.doesNotMatch(h.get('growthSummary').innerHTML,/<img src=x/);await h.click('growthInspect');assert.match(h.get('growthEntries').innerHTML,/&lt;img/);assert.doesNotMatch(h.get('growthEntries').innerHTML,/<img src=x/);
 await h.action('approve');assert.match(h.get('modalBody').innerHTML,/&lt;img/);assert.equal(h.requests('/api/growth/mutate').length,0);assert.equal(h.calls.filter(x=>!/growth/.test(x.url)).length,0);
});
