// Exercise the installed Windows WebView2 UI with disposable per-user settings.
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const direct = process.argv.includes('--direct');
const exe = process.argv.slice(2).find(arg => arg !== '--direct') || path.join(root, 'work', 'tools', 'FagriShotLauncher.exe');
const run = await fs.mkdtemp(path.join(root, 'work', 'launcher', 'ui-'));
const portProbe = net.createServer();
await new Promise(resolve => portProbe.listen(0, '127.0.0.1', resolve));
const port = portProbe.address().port;
await new Promise(resolve => portProbe.close(resolve));
const local = path.join(run, 'LocalAppData');
const settingsFile = path.join(local,'FagriShot','settings','launcher.json');
await fs.mkdir(path.dirname(settingsFile),{recursive:true});
await fs.writeFile(settingsFile,JSON.stringify({schema:42}));
if (direct) {
  // A successful synthetic Play must return without opening Game setup or requiring a second click.
  const fixture = path.join(run,'direct-play-fixture');
  for (const folder of ['fagrishot','scripts','compatibility']) await fs.mkdir(path.join(fixture,folder),{recursive:true});
  await fs.writeFile(path.join(fixture,'fagrishot','__main__.py'),'');
  await fs.writeFile(path.join(fixture,'compatibility','supported.json'),'{}');
  await fs.writeFile(path.join(fixture,'scripts','fagrishot.py'), [
    'import sys', 'from pathlib import Path', 'assert sys.argv[1:] == ["play"]',
    'print("Starting FagriShot.", flush=True)', 'Path("received.txt").write_text("play")',
  ].join('\n'));
  const launcher = path.join(fixture,'FagriShotLauncher.exe');
  await fs.copyFile(exe,launcher);
  await promisify(execFile)(launcher,['--play-game'],{windowsHide:true, timeout:30000,
    env:{...process.env, LOCALAPPDATA:local, WEBVIEW2_USER_DATA_FOLDER:path.join(fixture,'webview')}});
  assert.equal(await fs.readFile(path.join(fixture,'received.txt'),'utf8'),'play');
  await assert.rejects(fs.access(path.join(fixture,'webview')), {code:'ENOENT'});
}
const app = spawn(exe, direct ? ['--play-game'] : [], {windowsHide:true, env:{...process.env, LOCALAPPDATA:local,
  WEBVIEW2_USER_DATA_FOLDER:path.join(run, 'webview'),
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port='+port}});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(f, label) {
  let last;
  for(let i=0;i<120;i++) {
    if(app.exitCode!==null) throw Error('Launcher exited with '+app.exitCode);
    try { const value = await f(); if(value) return value; } catch(e) {last=e;}
    await sleep(250);
  }
  throw Error('Timed out waiting for '+label+' '+(last || ''));
}
let ws;
try {
  const target = await until(async()=> (await (await fetch('http://127.0.0.1:'+port+'/json/list')).json())
    .find(p=>p.type==='page' && p.url.includes('tauri.localhost')), 'launcher page');
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
  let next=0;
  const calls=new Map();
  ws.onmessage=e=>{
    const m=JSON.parse(e.data);
    const pending=calls.get(m.id);
    if(pending){calls.delete(m.id); m.error?pending.reject(Error(m.error.message)):pending.resolve(m.result);}
  };
  const cdp=(method,params={})=>new Promise((resolve,reject)=>{
    const id=++next; calls.set(id,{resolve,reject}); ws.send(JSON.stringify({id,method,params}));
  });
  async function evalPage(expression) {
    const r=await cdp('Runtime.evaluate',{expression,returnByValue:true});
    if(r.exceptionDetails) throw Error(r.exceptionDetails.text);
    return r.result.value;
  }
  await until(()=>evalPage("document.querySelector('#retry')?.hidden === false"),'unsupported settings retry');
  if (direct) {
    await until(()=>evalPage("!document.querySelector('#direct-play-error').hidden && document.querySelector('#play-diagnostic').textContent.includes('Unsupported settings version')"),'failed direct Play opens setup with the CLI diagnostic');
  } else {
    assert.equal(await evalPage("document.querySelector('#direct-play-error').hidden"),true);
  }
  assert.ok(await evalPage("document.querySelector('[data-action=configure]').disabled && document.querySelector('[data-action=backup]').disabled"));
  await fs.writeFile(settingsFile,'{');
  await evalPage("document.querySelector('#retry').click()");
  await until(()=>evalPage("document.querySelector('#settings-warning')?.hidden === false && document.querySelector('#exefs').value"),'damaged settings recovery');
  assert.equal(await evalPage("document.querySelector('[data-action=inspect]').disabled"),true);
  await evalPage("document.querySelector('[data-page=data]').click(); document.querySelector('[data-action=backup]').click()");
  await until(()=>evalPage("document.querySelector('#feedback').textContent==='Finished.' && !document.querySelector('[data-action=backups]').disabled"),'backup with damaged game settings');
  await evalPage("document.querySelector('[data-action=backups]').click()");
  await until(()=>evalPage("document.querySelector('#feedback').textContent==='Finished.' && document.querySelector('#output').textContent.includes('No backups yet.') && !document.querySelector('[data-action=configure]').disabled"),'list with damaged game settings');
  await evalPage("document.querySelector('[data-page=setup]').click()");
  assert.equal(await evalPage("document.querySelector('#settings-warning').hidden"),false);
  await evalPage("document.querySelector('[data-action=configure]').click()");
  await until(()=>evalPage("document.querySelector('#settings-warning').hidden && !document.querySelector('[data-action=inspect]').disabled"),'settings repair');
  assert.equal(await evalPage("document.querySelector('#direct-play-error').hidden"),true);
  assert.equal(await evalPage('document.characterSet'),'UTF-8');
  assert.equal(await evalPage('document.title'),'FagriShot');
  const version = JSON.parse(await fs.readFile(path.join(root, 'launcher/src-tauri/tauri.conf.json'), 'utf8')).version;
  const gameTitle = 'Fire Emblem: Fortune\u2019s Weave';
  assert.equal(await evalPage("document.querySelector('#setup h2').innerText"), gameTitle);
  assert.equal(await evalPage("document.querySelector('aside small').innerText"), 'Windows x64 \u00b7 ' + version);
  assert.equal(await evalPage("document.querySelector('#play').disabled"),true);
  await fs.writeFile(path.join(run,'setup.png'),Buffer.from((await cdp('Page.captureScreenshot')).data,'base64'));
  const running = await evalPage("document.querySelector('[data-action=inspect]').click(); document.querySelector('#feedback').textContent");
  assert.equal(running, 'Running\u2026');
  await until(()=>evalPage("document.querySelector('#output').textContent.includes('ExeFS is missing') && !document.querySelector('[data-action=configure]').disabled"),'missing ExeFS diagnostic');
  const unicodePath = path.join(os.tmpdir(), path.basename(run), '\u65e5\u672c\u8a9e \u2013 \u0627\u062e\u062a\u0628\u0627\u0631 \u00e9', 'exefs');
  await evalPage("document.querySelector('#exefs').value = " + JSON.stringify(unicodePath));
  await evalPage("document.querySelector('[data-action=configure]').click()");
  await until(()=>evalPage("document.querySelector('#feedback').textContent==='Finished.' && !document.querySelector('[data-action=doctor]').disabled"),'settings write');
  const settings=JSON.parse(await fs.readFile(settingsFile,'utf8'));
  assert.equal(settings.schema,1);
  assert.equal(settings.exefs,unicodePath);
  await evalPage("document.querySelector('[data-page=data]').click(); document.querySelector('[data-action=doctor]').click()");
  await until(()=>evalPage("document.querySelector('#output').textContent.includes('Updates: manual')"),'diagnostics');
  assert.ok((await evalPage("document.querySelector('#output').textContent")).includes(unicodePath));
  // Editing the form and working with saves are independent, including when a second tool creates a backup.
  const draft = unicodePath + '-unsaved';
  await evalPage("document.querySelector('[data-page=setup]').click(); document.querySelector('#exefs').value=" + JSON.stringify(draft) +
    ";document.querySelector('#exefs').dispatchEvent(new Event('input'));document.querySelector('[data-page=data]').click()");
  const saves = path.join(local, 'FagriShot', 'saves');
  await fs.mkdir(saves, {recursive:true});
  await fs.writeFile(path.join(saves, 'fixture'), 'launcher save fixture');
  await evalPage("document.querySelector('[data-action=backup]').click()");
  await until(()=>evalPage("document.querySelector('#feedback').textContent==='Finished.' && !document.querySelector('#restore').disabled"),'backup creation');
  assert.equal(await evalPage("document.querySelector('#exefs').value"), draft, 'backup refresh discarded an unsaved location');
  const selectedBackup = await evalPage("document.querySelector('#snapshot').value");
  await fs.writeFile(path.join(saves, 'fixture'), 'second launcher save fixture');
  const external = await promisify(execFile)(exe, ['backup'], {env:{...process.env, LOCALAPPDATA:local}, windowsHide:true});
  const secondBackup = external.stdout.trim();
  assert.notEqual(secondBackup,selectedBackup);
  await evalPage("document.querySelector('[data-action=backups]').click()");
  await until(()=>evalPage("document.querySelector('#feedback').textContent==='Finished.' && !document.querySelector('#restore').disabled"),'backup list');
  assert.ok(await evalPage("Array.from(document.querySelector('#snapshot').options, o=>o.value).includes(" + JSON.stringify(secondBackup) + ")"), 'Restore list missed an external backup');
  assert.equal(await evalPage("document.querySelector('#snapshot').value"),selectedBackup,'refresh changed the chosen restore target');
  await evalPage("window.confirm=()=>false; document.querySelector('#restore').click()");
  assert.equal(await fs.readFile(path.join(saves,'fixture'),'utf8'),'second launcher save fixture');
  await evalPage("window.confirm=msg=>{window.testRestorePrompt=msg; return true;}; document.querySelector('#restore').click()");
  await until(()=>evalPage("document.querySelector('#feedback').textContent==='Finished.' && !document.querySelector('#restore').disabled"),'snapshot restore');
  assert.ok((await evalPage('window.testRestorePrompt')).includes(selectedBackup));
  assert.equal(await fs.readFile(path.join(saves,'fixture'),'utf8'),'launcher save fixture');
  await evalPage("document.querySelector('[data-page=setup]').click(); document.querySelector('#exefs').value=' '; document.querySelector('#exefs').dispatchEvent(new Event('input'))");
  assert.equal(await evalPage("document.querySelector('[data-action=configure]').disabled"),true,'blank ExeFS can silently reuse a saved path');
  assert.equal(await evalPage("document.querySelector('[data-action=inspect]').disabled"),true);
  await evalPage("document.querySelector('[data-page=about]').click()");
  const credits = await evalPage("document.querySelector('#about').innerText");
  assert.match(credits,/thunderq10/);
  assert.ok(credits.includes('Fortune\u2019s Weave port'));
  assert.ok(credits.includes('drakolordx7\u2019s nxrecomp work'));
  await fs.writeFile(path.join(run,'verification.json'),JSON.stringify({title:'FagriShot',passed:['window title',
    'default inputs','Play gate','missing ExeFS diagnostic','isolated settings write','diagnostics','credits',
    'Unicode title, footer, progress and credits','Unicode path in settings and diagnostics',
    'unsaved locations survive backup','external backups refresh without changing restoration target','blank locations rejected',
    'failed status blocks operations and retries','damaged settings warning and repair','backup and list during settings recovery',
    'declined restore leaves saves intact','confirmed restore recovers the selected synthetic save',
    ...(direct ? ['successful synthetic Play bypasses setup','failed direct Play opens setup with its diagnostic'] : [])]},null,2));
  console.log('Verified actual Windows launcher UI:',run);
} finally {
  ws?.close();
  app.kill();
}
