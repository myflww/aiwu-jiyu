// UI policy tests: fake DOM/API only, no download, installation or network.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/ui/app.js'), 'utf8');

function harness() {
  const elements = new Map(), dialogs = [], calls = [], views = [];
  function element(id) {
    if (!elements.has(id)) {
      const classes = new Set(id === 'wizard' ? ['hidden'] : []);
      elements.set(id, { id, textContent: '', innerHTML: '', style: {}, dataset: {}, disabled: false,
        classList: { add: v => classes.add(v), remove: v => classes.delete(v), contains: v => classes.has(v),
          toggle(v, force) { const next = force === undefined ? !classes.has(v) : force; next ? classes.add(v) : classes.delete(v); return next; } },
        setAttribute() {}, focus() {}, scrollIntoView() {}, querySelector() { return null; } });
    }
    return elements.get(id);
  }
  const document = { hidden: false, getElementById: element, querySelectorAll: () => [], addEventListener() {} };
  const context = vm.createContext({ document, localStorage: { getItem() { return null; } },
    console, setTimeout, clearTimeout, setInterval, clearInterval, URLSearchParams });
  vm.runInContext(source, context);
  context.recordDialog = (title, body) => { dialogs.push({ title, body }); element('modalBack').classList.add('show'); };
  context.recordClose = () => element('modalBack').classList.remove('show');
  context.recordView = name => views.push(name);
  context.recordApi = async (url, options) => { calls.push({ url, options }); return context.response || {}; };
  vm.runInContext('openModal=recordDialog; closeModal=recordClose; showView=recordView; api=recordApi; toast=()=>{};', context);
  return { context, element, dialogs, calls, views,
    install(u) { context.fixture = u; vm.runInContext('updateState=fixture; updateReachable=true; localConnected=true; state={preview:false};', context); },
    render() { vm.runInContext('renderUpdates()', context); },
    notice(u, manual = false) { return context.updateNoticeData(u, manual, 1000); } };
}

function status(overrides = {}) {
  return { current_version: '1.3.1', latest_version: '1.3.2', available: true, auto_check: true,
    ignored_version: '', snoozed_until: 0, checked_at: '2026-11-03T12:00:00Z', error: '',
    plugin_updates: { state: 'checked', tag: 'v1.3.2', can_repair: true, plugins: [
      { id: 'companion', name: '大肥鱼陪伴', installed: '1.0.0', bundled: '1.0.0', latest: '1.1.0',
        is_installed: true, status: 'update_available', update_via: 'app' } ] }, ...overrides };
}

test('software and plugin checks settle together, then queue behind another dialog and show once', () => {
  const h = harness(), u = status();
  h.install({ ...u, plugin_updates: { ...u.plugin_updates, state: 'checking' } }); h.render();
  assert.equal(h.dialogs.length, 0);
  h.install({ ...u, checking: true }); h.render(); assert.equal(h.dialogs.length, 0);
  h.element('modalBack').classList.add('show'); h.install(u); h.render();
  assert.equal(h.dialogs.length, 0, 'QR/settings dialog is not replaced');
  h.element('modalBack').classList.remove('show'); h.render();
  assert.equal(h.dialogs.length, 1);
  assert.match(h.dialogs[0].body, /v1\.3\.1/);
  assert.match(h.dialogs[0].body, /v1\.3\.2/);
  assert.match(h.dialogs[0].body, /大肥鱼陪伴/);
  h.context.recordClose(); h.render(); assert.equal(h.dialogs.length, 1);
  assert.equal(h.calls.length, 0, 'notifying never starts downloads or installation');
  const nextLaunch = harness(); nextLaunch.install(u); nextLaunch.render();
  assert.equal(nextLaunch.dialogs.length, 1, 'dismissal is per page load');
});

test('only outdated plugins can notify and offer the verified full-package repair flow', () => {
  const h = harness(); h.install(status({ latest_version: '1.3.1', available: false,
    plugin_updates: { ...status().plugin_updates, tag: 'v1.3.1' } })); h.render();
  assert.equal(h.dialogs.length, 1);
  assert.match(h.dialogs[0].title, /插件/);
  assert.equal(h.element('updateDownload').classList.contains('hidden'), false);
  assert.equal(h.element('updateDownload').textContent, '下载插件更新安装包');
  assert.equal(h.calls.length, 0);
  h.element('updatePromptOpen').onclick();
  assert.deepEqual(h.views, ['settings']);
  assert.equal(h.calls.length, 0, 'go to updates only navigates');
});

test('same-version plugin repair requires explicit server permission for download and installation', async () => {
  for (const canRepair of [false, undefined]) {
    const h = harness(); h.install(status({ available: false, latest_version: '1.3.1', downloaded: true, download_verified: true,
      plugin_updates: { ...status().plugin_updates, tag: 'v1.3.1', can_repair: canRepair } })); h.render();
    assert.equal(h.element('updateDownload').classList.contains('hidden'), true);
    assert.equal(h.element('updateInstall').classList.contains('hidden'), true);
    await h.context.runUpdateAction('download'); await h.context.runUpdateAction('install');
    assert.equal(h.calls.length, 0);
  }
});

test('automatic off suppresses startup notice; manual check may notify but ignores and snoozes remain honored', async () => {
  const h = harness(), u = status({ auto_check: false });
  assert.equal(h.notice(u), null);
  assert.ok(h.notice(u, true));
  assert.equal(h.notice({ ...u, snoozed_until: 2000 }, true), null);
  assert.equal(h.notice({ ...u, ignored_version: '1.3.2' }, true), null);
  h.install(u); h.render(); assert.equal(h.dialogs.length, 0);
  h.context.response = u; await h.context.runUpdateAction('check');
  assert.equal(h.dialogs.length, 1);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url, '/api/updates/check');
});

test('later saves only the existing 24-hour snooze preference', async () => {
  const h = harness(); h.install(status()); h.render();
  h.context.response = status({ snoozed_until: Date.now() / 1000 + 86400 });
  h.element('updatePromptLater').onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url, '/api/updates/settings');
  assert.deepEqual(JSON.parse(h.calls[0].options.body), { snooze_hours: 24 });
  assert.equal(h.element('modalBack').classList.contains('show'), false);
  h.render(); assert.equal(h.dialogs.length, 1);
});

test('unknown, unchecked, no-release and failed checks do not claim plugins are current', () => {
  for (const state of ['checked', 'unavailable', 'no_release']) {
    const h = harness(); h.install(status({ available: false,
      plugin_updates: { state, plugins: [{ id: 'unknown', name: '待确认插件', installed: null, latest: null, status: 'unknown' }] } })); h.render();
    assert.equal(h.dialogs.length, 0);
    assert.notEqual(h.element('pluginUpdateBadge').className, 'badge green');
    assert.doesNotMatch(h.element('pluginUpdateMessage').textContent, /暂无可用更新/);
  }
  const h = harness(); h.install(status({ available: false, latest_version: '', checked_at: '2026-11-03',
    error: '检查失败', plugin_updates: { state: 'unavailable', plugins: [] } })); h.render();
  assert.equal(h.element('updateLatestVersion').textContent, '暂未确认');
  assert.match(h.element('updateMessage').textContent, /失败/);
});

test('same release does not prompt twice when plugin status arrives later, and new release may notify', () => {
  const h = harness(); h.install(status({ plugin_updates: { state: 'unavailable', plugins: [] } })); h.render();
  h.context.recordClose(); h.install(status()); h.render(); assert.equal(h.dialogs.length, 1);
  h.install(status({ latest_version: '1.3.3', plugin_updates: { ...status().plugin_updates, tag: 'v1.3.3' } })); h.render();
  assert.equal(h.dialogs.length, 2);
});

test('server metadata is escaped in the notice and version table', () => {
  const h = harness(), u = status();
  u.plugin_updates.plugins[0].name = '<img src=x onerror=alert(1)>';
  h.install(u); h.render();
  assert.ok(h.dialogs[0].body.includes('&lt;img'));
  assert.ok(!h.dialogs[0].body.includes('<img'));
});
