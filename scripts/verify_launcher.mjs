// Exercise the installed Windows WebView2 UI with disposable per-user settings.
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const exe = process.argv[2] || path.join(root, 'work', 'tools', 'FagriShotLauncher.exe');
const run = await fs.mkdtemp(path.join(root, 'work', 'launcher', 'ui-'));
const portProbe = net.createServer();
await new Promise(resolve => portProbe.listen(0, '127.0.0.1', resolve));
const port = portProbe.address().port;
await new Promise(resolve => portProbe.close(resolve));
const local = path.join(run, 'LocalAppData');
const app = spawn(exe, [], {windowsHide:true, env:{...process.env, LOCALAPPDATA:local,
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
  await until(()=>evalPage("document.querySelector('#exefs')?.value"), 'selected paths');
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
  const settings=JSON.parse(await fs.readFile(path.join(local,'FagriShot','settings','launcher.json'),'utf8'));
  assert.equal(settings.schema,1);
  assert.equal(settings.exefs,unicodePath);
  await evalPage("document.querySelector('[data-page=data]').click(); document.querySelector('[data-action=doctor]').click()");
  await until(()=>evalPage("document.querySelector('#output').textContent.includes('Updates: manual')"),'diagnostics');
  assert.ok((await evalPage("document.querySelector('#output').textContent")).includes(unicodePath));
  await evalPage("document.querySelector('[data-page=about]').click()");
  const credits = await evalPage("document.querySelector('#about').innerText");
  assert.match(credits,/thunderq10/);
  assert.ok(credits.includes('Fortune\u2019s Weave port'));
  assert.ok(credits.includes('drakolordx7\u2019s nxrecomp work'));
  await fs.writeFile(path.join(run,'verification.json'),JSON.stringify({title:'FagriShot',passed:['window title',
    'default inputs','Play gate','missing ExeFS diagnostic','isolated settings write','diagnostics','credits',
    'Unicode title, footer, progress and credits','Unicode path in settings and diagnostics']},null,2));
  console.log('Verified actual Windows launcher UI:',run);
} finally {
  ws?.close();
  app.kill();
}
