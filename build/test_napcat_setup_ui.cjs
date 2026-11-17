// Offline QQ package UI: native paths, verified imports, and installation races.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/ui/app.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const settle = async () => { for (let n=0; n<5; n++) await new Promise(resolve => setImmediate(resolve)); };

function harness() {
  class Element {
    constructor() {
      this.textContent = ''; this.innerHTML = ''; this.disabled = false; this.title = ''; this.attrs = {}; this.dataset = {}; this.style = {};
      const classes = new Set();
      this.classList = { add: (...items) => items.forEach(item => classes.add(item)), remove: (...items) => items.forEach(item => classes.delete(item)), contains: item => classes.has(item), toggle(item, force) { const enabled = force ?? !classes.has(item); enabled ? classes.add(item) : classes.delete(item); return enabled; } };
    }
    setAttribute(name, value) { this.attrs[name] = String(value); }
  }
  const nodes = new Map(['wizard','wizardNext','installError','napcatInstallRecovery','installBar','installPercent','installStep','installLog','toast'].map(id => [id,new Element()]));
  nodes.get('wizard').classList.add('hidden'); nodes.get('installError').classList.add('hidden'); nodes.get('napcatInstallRecovery').classList.add('hidden');
  const fields = new Map();
  const panel = { querySelector(selector) { if (!fields.has(selector)) fields.set(selector,new Element()); return fields.get(selector); } };
  const calls = [], timers = [];
  let inspect = { ok:true, version:'4.18.42', installed:false, cache_valid:false, status:'missing', message:'尚未找到可用连接包。', source_name:'原版社区归档', source_url:'https://github.com/example/archive', archive_url:'https://github.com/example/archive/releases/download/v4.18.42/NapCat.Shell.Node.zip', sha256:'a'.repeat(64), archive_name:'NapCat.Shell.Node.zip', missing_files:['napcat.mjs'] };
  let install = { ok:true, running:false, done:false, log:[] }, importError = '', importResult = null, statusError = false;
  const context = vm.createContext({
    console, URL, URLSearchParams, localStorage:{getItem:()=>null}, window:{},
    document:{getElementById:id=>nodes.get(id)||null,querySelectorAll:selector=>selector==='[data-napcat-setup]'?[panel]:[],addEventListener(){}},
    setInterval: fn => { timers.push(fn); return timers.length; },clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async (url, options={}) => {
      calls.push({url, method:options.method||'GET', body:options.body ? JSON.parse(options.body) : null});
      let data;
      if(url==='/api/setup/status') { if(statusError) throw new Error('服务不可用'); data=install; }
      else if(url==='/api/setup/napcat') data=inspect;
      else if(url==='/api/setup/napcat/import') {
        if(importError) return {ok:false,status:400,json:async()=>({ok:false,message:importError})};
        data=importResult || {ok:true,message:'原版离线包已校验并导入缓存。',napcat:{...inspect,cache_valid:true,status:'cached',message:'下载缓存已通过校验。'}};
        if(data.napcat?.cache_valid) inspect={...data.napcat};
      } else throw new Error('Unexpected request '+url);
      return {ok:true,status:200,json:async()=>clone(data)};
    },
  });
  vm.runInContext(source + `\n globalThis.ui = {loadNapcatSetup,importNapcatArchive,renderNapcatSetup,setupIsInstalling,startInstallPolling,
    setContext(value){state={installed:false,preview:false,...value};localConnected=true;},
    installFailure(){wizardStep=1;setupInstallError='下载失败';setupInstallRunning=false;},
    setRequestPending(value){setupInstallRequestPending=value;},
    feedback(){return napcatSetupFeedback;},running(){return setupInstallRunning;}};`, context);
  context.ui.setContext({});
  return {ui:context.ui,context,nodes,fields,calls,timers,field:selector=>panel.querySelector(selector),
    native: fn => { context.window.pywebview={api:{choose_napcat_archive:fn}}; },
    setInspect:value=>{inspect={...inspect,...value};},setInstall:value=>{install={...install,...value};},setImportError:value=>{importError=value;},setImportResult:value=>{importResult=value;},failStatus:()=>{statusError=true;},
  };
}

test('shows the pinned source, hash, and actual cache state with safe links',async()=>{
  const h=harness();
  h.setInspect({source_url:'javascript:alert(1)',source_name:'<img src=x>',cache_valid:true,status:'cached',message:'缓存已校验。'});
  await h.ui.loadNapcatSetup();
  assert.equal(h.field('[data-napcat-badge]').textContent,'缓存已校验');
  assert.match(h.field('[data-napcat-source-note]').textContent,/4\.18\.42 原版归档，SHA256 校验/);
  assert.equal(h.field('[data-napcat-meta="sha256"]').textContent,'a'.repeat(64));
  assert.equal(h.field('[data-napcat-meta="source"]').innerHTML,'&lt;img src=x&gt;');
  assert.equal(h.field('[data-napcat-import]').disabled,false);
});

test('native import sends only a local path and reports success only after verification',async()=>{
  const h=harness();h.native(async()=>String.raw`D:\offline\NapCat.Shell.Node.zip`);
  await h.ui.loadNapcatSetup();h.ui.installFailure();
  await h.ui.importNapcatArchive();
  const writes=h.calls.filter(call=>call.method==='POST');
  assert.deepEqual(writes,[{url:'/api/setup/napcat/import',method:'POST',body:{path:String.raw`D:\offline\NapCat.Shell.Node.zip`}}]);
  assert.equal(h.ui.feedback().error,false);
  assert.equal(h.field('[data-napcat-badge]').textContent,'缓存已校验');
  assert.equal(h.nodes.get('wizardNext').textContent,'继续安装');
});

test('a hash mismatch stays visible and never claims a successful import',async()=>{
  const h=harness();h.native(async()=>String.raw`D:\wrong.zip`);h.setImportError('SHA256 校验失败：连接包与固定原版不一致。');
  await h.ui.loadNapcatSetup();await h.ui.importNapcatArchive();
  assert.equal(h.ui.feedback().error,true);
  assert.match(h.field('[data-napcat-feedback]').textContent,/SHA256 校验失败/);
  assert.equal(h.field('[data-napcat-feedback]').attrs.role,'alert');
  assert.equal(h.field('[data-napcat-badge]').textContent,'待准备');
});

test('an unverified ok response is not accepted as import success',async()=>{
  const h=harness();h.native(async()=>String.raw`D:\wrong.zip`);h.setImportResult({ok:true,message:'已导入',napcat:{cache_valid:false}});
  await h.ui.loadNapcatSetup();await h.ui.importNapcatArchive();
  assert.equal(h.ui.feedback().error,true);assert.match(h.ui.feedback().message,/尚未通过校验/);
});

test('canceling the desktop picker leaves the cache untouched',async()=>{
  const h=harness();h.native(async()=>'');await h.ui.loadNapcatSetup();await h.ui.importNapcatArchive();
  assert.equal(h.calls.filter(call=>call.method==='POST').length,0);assert.equal(h.ui.feedback(),null);
  assert.equal(h.field('[data-napcat-import]').disabled,false);
});

test('browser mode explains desktop selection or downloads without uploading data',async()=>{
  const h=harness();await h.ui.loadNapcatSetup();await h.ui.importNapcatArchive();
  assert.match(h.ui.feedback().message,/桌面应用窗口/);assert.match(h.ui.feedback().message,/downloads/);
  assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
});

test('running installation disables import and does not open the picker',async()=>{
  const h=harness();let picked=0;h.native(async()=>{picked++;return 'D:\\archive.zip';});h.setInstall({running:true});
  await h.ui.loadNapcatSetup();await h.ui.importNapcatArchive();
  assert.equal(h.field('[data-napcat-import]').disabled,true);assert.equal(picked,0);
});

test('installation starting while the picker is open blocks import without a write',async()=>{
  const h=harness();let release;h.native(()=>new Promise(resolve=>{release=resolve;}));
  await h.ui.loadNapcatSetup();const importing=h.ui.importNapcatArchive();await settle();
  assert.equal(h.field('[data-napcat-import]').disabled,true);
  h.setInstall({running:true});release('D:\\archive.zip');await importing;
  assert.equal(h.calls.filter(call=>call.method==='POST').length,0);assert.match(h.ui.feedback().message,/正在安装/);
});

test('unknown installation state requires a fresh check before importing',async()=>{
  const h=harness();h.failStatus();await h.ui.loadNapcatSetup();
  assert.equal(h.field('[data-napcat-import]').disabled,true);assert.match(h.field('[data-napcat-summary]').textContent,/无法确认安装状态/);
});

test('preview is read-only and cannot invoke the native picker',async()=>{
  const h=harness();h.ui.setContext({preview:true});let picked=0;h.native(async()=>{picked++;return 'D:\\archive.zip';});
  await h.ui.loadNapcatSetup();await h.ui.importNapcatArchive();assert.equal(picked,0);assert.equal(h.field('[data-napcat-import]').disabled,true);
});

test('polling waits for the new install request and reveals recovery after failure',async()=>{
  const h=harness();h.ui.setRequestPending(true);h.ui.startInstallPolling();await settle();
  assert.equal(h.calls.length,0);
  h.ui.setRequestPending(false);h.setInstall({error:'连接包下载失败',running:false});await h.timers[0]();await settle();
  assert.match(h.nodes.get('installError').textContent,/连接包下载失败/);
  assert.equal(h.nodes.get('napcatInstallRecovery').classList.contains('hidden'),false);
  assert.equal(h.nodes.get('wizardNext').textContent,'重试安装');
});
