// Exercise the installed Windows launcher in isolated WebView2 profiles.
import {execFile, spawn} from 'node:child_process';
import {promisify} from 'node:util';
import fs from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const exe = path.resolve(process.argv.slice(2).find(arg => arg !== '--direct') ||
  path.join(root, 'work/tools/FagriShotLauncher.exe'));
const direct = process.argv.includes('--direct');
const work = path.join(root, 'work/launcher');
await fs.mkdir(work,{recursive:true});
const run = await fs.mkdtemp(path.join(work,'ui-'));
const local = path.join(run,'LocalAppData');
const settingsFile = path.join(local,'FagriShot/settings/launcher.json');
const version = JSON.parse(await fs.readFile(path.join(root,'launcher/src-tauri/tauri.conf.json'),'utf8')).version;
const passed = [];
const sleep = ms => new Promise(resolve => setTimeout(resolve,ms));
const exists = file => fs.access(file).then(()=>true,()=>false);

async function session(binary,args,profile,data,check) {
  const portProbe = net.createServer();
  await new Promise(resolve => portProbe.listen(0,'127.0.0.1',resolve));
  const port = portProbe.address().port;
  await new Promise(resolve => portProbe.close(resolve));
  const app = spawn(binary,args,{windowsHide:true,env:{...process.env,LOCALAPPDATA:data,
    WEBVIEW2_USER_DATA_FOLDER:profile, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port='+port}});
  async function until(f,label) {
    let last;
    const timeout = Date.now()+65000;
    while (Date.now() < timeout) {
      if (app.exitCode !== null) throw Error('Launcher exited with '+app.exitCode);
      try { const value=await f(); if (value) return value; } catch (error) { last=error; }
      await sleep(150);
    }
    throw Error('Timed out waiting for '+label+' '+(last||''));
  }
  let ws;
  try {
    const target = await until(async()=> (await (await fetch('http://127.0.0.1:'+port+'/json/list')).json())
      .find(p=>p.type==='page' && p.url.includes('tauri.localhost')),'launcher page');
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
    let next=0;
    const calls=new Map();
    ws.onmessage=e=>{
      const m=JSON.parse(e.data);
      const pending=calls.get(m.id);
      if (pending) { calls.delete(m.id); m.error?pending.reject(Error(m.error.message)):pending.resolve(m.result); }
    };
    const cdp=(method,params={})=>new Promise((resolve,reject)=>{
      const id=++next; calls.set(id,{resolve,reject}); ws.send(JSON.stringify({id,method,params}));
    });
    const evaluate=async expression=>{
      const r=await cdp('Runtime.evaluate',{expression,returnByValue:true});
      if (r.exceptionDetails) throw Error(r.exceptionDetails.text);
      return r.result.value;
    };
    const wait=(expr,label)=>until(()=>evaluate(expr),label);
    const click=selector=>evaluate('document.querySelector('+JSON.stringify(selector)+').click()');
    const input=(id,value)=>evaluate('var field=document.getElementById('+JSON.stringify(id)+');field.value='+
      JSON.stringify(value)+';field.dispatchEvent(new Event("input"))');
    await check({evaluate,wait,click,input,cdp,pid:app.pid});
  } finally {
    ws?.close();
    app.kill();
  }
}

// Read first-open state directly from the supplied installation with an empty player profile.
await session(exe,[],path.join(run,'first-webview'),local,async({evaluate,wait,cdp})=>{
  await wait("!document.querySelector('[data-pick=ExeFS]').disabled && document.querySelector('aside [data-version]').textContent==="+JSON.stringify(version),'first launch');
  assert.equal(await evaluate("document.querySelector('#title').textContent"),'Game setup');
  assert.equal(await evaluate("document.querySelector('#romfs').value+document.querySelector('#exefs').value"),'');
  assert.equal(await evaluate("!document.querySelector('[data-pick=RomFS]').disabled && document.querySelector('#build-code').disabled && document.querySelector('#play').disabled"),true);
  assert.equal(await evaluate("document.querySelector('#input-state').textContent"),'Choose both folders');
  await fs.writeFile(path.join(run,'first-open.png'),Buffer.from((await cdp('Page.captureScreenshot')).data,'base64'));
  passed.push('fresh player profile opens Game setup with empty inputs, two available folder pickers, Build and Play disabled');
});
// A failed build deliberately invalidates play-ready.json. Give that test a private copy of the CLI.
const packaged=await exists(path.join(path.dirname(exe),'player-package.json'));
const installed=packaged ? path.dirname(exe) : root;
const isolated=path.join(run,'real-cli'), isolatedExe=path.join(isolated,'FagriShotLauncher.exe');
for (const sub of ['scripts','compatibility',...(packaged ? ['tools'] : ['recomp/games/fefw'])])
  await fs.mkdir(path.join(isolated,sub),{recursive:true});
await fs.copyFile(exe,isolatedExe);
await fs.cp(path.join(installed,'fagrishot'),path.join(isolated,'fagrishot'),{recursive:true});
for (const file of ['compatibility/supported.json','scripts/fagrishot.py',packaged?'player-package.json':'recomp/games/fefw/config.py'])
  await fs.copyFile(path.join(installed,file),path.join(isolated,file));
if (packaged) await fs.symlink(path.join(installed,'tools/python'),path.join(isolated,'tools/python'),'junction');
else if (await exists(path.join(installed,'tools/.venv/Scripts/python.exe'))) {
  await fs.mkdir(path.join(isolated,'tools'),{recursive:true});
  await fs.symlink(path.join(installed,'tools/.venv'),path.join(isolated,'tools/.venv'),'junction');
}
await fs.mkdir(path.join(isolated,'fonts'),{recursive:true});
await fs.copyFile(path.join(installed,packaged?'fonts/DejaVuSans.ttf':'runtime/assets/fonts/DejaVuSans.ttf'),path.join(isolated,'fonts/DejaVuSans.ttf'));

await fs.mkdir(path.dirname(settingsFile),{recursive:true});
await fs.writeFile(settingsFile,JSON.stringify({schema:42}));
await session(isolatedExe,direct?['--play-game']:[],path.join(run,'webview'),local,async({evaluate,wait,click,input,cdp})=>{
  await wait("document.querySelector('#retry')?.hidden === false",'unreadable settings recovery');
  assert.equal(await evaluate("document.querySelector('#build-code').disabled && document.querySelector('#play').disabled"),true);
  if (direct) {
    await wait("!document.querySelector('#direct-play-error').hidden && document.querySelector('#play-diagnostic').textContent.includes('Unsupported settings version')",'direct Play diagnostic');
    passed.push('failed direct Play opens setup with CLI diagnostic');
  }
  await fs.writeFile(settingsFile,'{');
  await click('#retry');
  await wait("!document.querySelector('#settings-warning').hidden && !document.querySelector('[data-pick=ExeFS]').disabled",'repairable settings');
  await wait("!document.querySelector('#arabic-text').disabled && document.querySelector('#arabic-coverage').textContent === 'Play with Arabic text throughout the game (Experimental feature)'",'Arabic preview description');
  const arabicPreference=path.join(path.dirname(settingsFile),'arabic.json');
  await click('#arabic-text');
  await wait("document.querySelector('#arabic-text').checked && !document.querySelector('#arabic-text').disabled",'Arabic preview enabled');
  assert.equal(JSON.parse(await fs.readFile(arabicPreference,'utf8')).enabled,true);
  await click('#arabic-text');
  await wait("!document.querySelector('#arabic-text').checked && !document.querySelector('#arabic-text').disabled",'Arabic preview disabled');
  assert.equal(JSON.parse(await fs.readFile(arabicPreference,'utf8')).enabled,false);
  passed.push('Arabic preview checkbox shows the experimental feature description and persists enabling and disabling');
  await wait("document.querySelector('aside [data-version]').textContent==="+JSON.stringify(version),'release version');
  assert.equal(await evaluate('document.title'),'FagriShot');
  assert.equal(await evaluate("document.querySelector('#setup h2').innerText"),'Fire Emblem: Fortune\u2019s Weave');
  assert.equal(await evaluate("document.querySelector('aside small').innerText"),'Windows x64 \u00b7 '+version+' preview');
  assert.equal(await evaluate("Array.from(document.querySelectorAll('#setup [data-action]'),b=>b.textContent).join('|')"),'Build game code|Play');
  assert.equal(await evaluate("Array.from(document.querySelectorAll('#setup [data-pick]'),b=>b.dataset.pick).join('|')"),'ExeFS|RomFS');
  await fs.writeFile(path.join(run,'setup.png'),Buffer.from((await cdp('Page.captureScreenshot')).data,'base64'));
  passed.push('setup and retry', 'UTF-8 title and version', 'two folder pickers, one build button and Play');
  const existingIni=path.join(path.dirname(settingsFile),'fefw.ini');
  await fs.writeFile(existingIni,'; keep this comment\n[fefw]\nrender = 800x600\n[other]\nvalue = untouched\n');
  const receipt=path.join(isolated,'work/private/play-ready.json');
  await fs.mkdir(path.dirname(receipt),{recursive:true});
  await fs.writeFile(receipt,'{"test":"unchanged"}');
  await click('#graphics-open');
  await wait("!document.querySelector('#graphics-output').disabled && document.querySelector('#graphics-scale').value==='fixed'",'graphics settings');
  assert.equal(await evaluate("document.querySelector('#graphics-upscaler option[value=dlss]').disabled"),true);
  assert.equal(await evaluate("document.querySelector('#graphics-ray_reflections').value"),'off');
  for (const [key,value] of Object.entries({output:'2160p',scale:'1.0',fps:'60',reflections:'reduced',upscaler:'auto',ray_reflections:'on'})) {
    await evaluate(`var option=document.querySelector('#graphics-${key}'); option.value=${JSON.stringify(value)}; option.dispatchEvent(new Event('change'))`);
  }
  await click('#graphics-save');
  await wait("document.querySelector('#graphics-message').textContent.includes('saved for the next game') && !document.querySelector('#graphics-close').disabled",'graphics saved');
  const savedIni=await fs.readFile(existingIni,'utf8');
  for (const setting of ['; keep this comment','value = untouched','output = 2160p','render_ratio = 1.0','fps60 = 1','upscaler = auto','reflections = reduced','[experimental]','rt_visible_reflections = true']) assert.ok(savedIni.includes(setting),setting);
  assert.equal(await fs.readFile(receipt,'utf8'),'{"test":"unchanged"}');
  await click('#graphics-close');
  await click('#graphics-open');
  await wait("!document.querySelector('#graphics-output').disabled && document.querySelector('#graphics-fps').value==='60'",'stored graphics on reopen');
  assert.equal(await evaluate("document.querySelector('#graphics-scale').value"),'1.0');
  assert.equal(await evaluate("document.querySelector('#graphics-ray_reflections').value"),'on');
  assert.equal(await evaluate("document.querySelector('#play').disabled"),true);
  await evaluate("document.querySelector('#graphics-panel').scrollIntoView({block:'center'})");
  await fs.writeFile(path.join(run,'graphics.png'),Buffer.from((await cdp('Page.captureScreenshot')).data,'base64'));
  await click('#graphics-close');
  passed.push('native graphics editor saves and reloads 4K native, 60 FPS, upscaler and screen-space reflections', 'experimental hardware-ray setting defaults off and persists separately from screen-space reflections', 'graphics changes preserve other INI values and private build readiness', 'missing Streamline files disable the DLSS choice');
  const exampleInputs=path.join(os.tmpdir(),path.basename(run),'日本語 – اختبار é');
  const invalid=path.join(exampleInputs,'missing-exefs');
  await input('exefs',' ');
  assert.equal(await evaluate("document.querySelector('#build-code').disabled && document.querySelector('#play').disabled"),true);
  await input('exefs',invalid);
  await input('romfs',path.join(exampleInputs,'missing-romfs'));
  await click('#build-code');
  await wait("!document.querySelector('#build-code').disabled && document.querySelector('#output').textContent.includes('ExeFS')",'actual missing-input diagnostic');
  assert.equal(await evaluate("document.querySelector('#play').disabled"),true);
  passed.push('missing input prevents Play', 'invalid folders return CLI diagnostic');
  await click('[data-page=data]');
  await click('[data-action=doctor]');
  await wait("document.querySelector('#output').textContent.includes('Updates: manual') && !document.querySelector('[data-action=backup]').disabled",'diagnostics');
  const saves=path.join(local,'FagriShot','saves');
  await fs.mkdir(saves,{recursive:true});
  await fs.writeFile(path.join(saves,'fixture'),'original synthetic save');
  await click('[data-action=backup]');
  await wait("document.querySelector('#feedback').textContent==='Finished.' && !document.querySelector('#restore').disabled",'backup');
  const target=await evaluate("document.querySelector('#snapshot').value");
  await fs.writeFile(path.join(saves,'fixture'),'changed synthetic save');
  const extra=await promisify(execFile)(isolatedExe,['backup'],{env:{...process.env,LOCALAPPDATA:local},windowsHide:true});
  assert.notEqual(target,extra.stdout.trim());
  await click('[data-action=backups]');
  await wait("Array.from(document.querySelector('#snapshot').options,o=>o.value).length===2 && !document.querySelector('#restore').disabled",'snapshot list refresh');
  assert.equal(await evaluate("document.querySelector('#snapshot').value"),target);
  await evaluate("window.confirm=()=>false;document.querySelector('#restore').click()");
  assert.equal(await fs.readFile(path.join(saves,'fixture'),'utf8'),'changed synthetic save');
  await evaluate("window.confirm=()=>true;document.querySelector('#restore').click()");
  await wait("document.querySelector('#feedback').textContent==='Finished.' && !document.querySelector('#restore').disabled",'restore');
  assert.equal(await fs.readFile(path.join(saves,'fixture'),'utf8'),'original synthetic save');
  assert.equal(await evaluate("document.querySelector('#exefs').value"),invalid);
  await click('[data-page=about]');
  assert.ok((await evaluate("document.querySelector('#about').innerText")).includes('thunderq10'));
  passed.push('diagnostics and credits','snapshots are listed without changing the chosen restore target',
    'declined and confirmed restore','unsaved Unicode paths survive backup');
});

// The fixture replaces the expensive private game build with the same phase and status protocol.
const fixture=path.join(run,'fixture'), fakeExe=path.join(fixture,'FagriShotLauncher.exe');
for (const sub of ['fagrishot','scripts','compatibility']) await fs.mkdir(path.join(fixture,sub),{recursive:true});
await fs.writeFile(path.join(fixture,'fagrishot/__main__.py'),'');
await fs.writeFile(path.join(fixture,'compatibility/supported.json'),'{}');
await fs.copyFile(path.join(root,'tests/launcher/player_fixture.py'),path.join(fixture,'scripts/fagrishot.py'));
await fs.copyFile(exe,fakeExe);
const interpreter=path.join(path.dirname(exe),'tools/python');
const marker = path.join(fixture,'player-package.json');
await fs.writeFile(marker,JSON.stringify({schema:1,kind:'fagrishot-player'}));
await assert.rejects(promisify(execFile)(fakeExe,['status','--json'],{env:{...process.env,LOCALAPPDATA:local},windowsHide:true}),
  error => error.code===2 && error.stderr.includes('player installation is incomplete'));
passed.push('incomplete player bundle reports extraction error without a system-Python fallback');
if (await exists(path.join(interpreter,'python.exe'))) {
  await fs.mkdir(path.join(fixture,'tools'),{recursive:true});
  await fs.symlink(interpreter,path.join(fixture,'tools/python'),'junction');
} else {
  await fs.unlink(marker);
}
const fakeData=path.join(run,'fixture-data'), state=path.join(fakeData,'FagriShot');
const exefs=path.join(run,'exefs あ'),romfs=path.join(run,'romfs é');
for (const folder of [state,exefs,romfs]) await fs.mkdir(folder,{recursive:true});
await session(fakeExe,[],path.join(run,'fixture-webview'),fakeData,async({evaluate,wait,click,input,cdp,pid})=>{
  await wait("!document.querySelector('[data-pick=ExeFS]').disabled",'fixture overview');
  assert.equal(await evaluate("document.querySelector('#play').disabled"),true);
  const browse=async (kind,folder)=>{
    await click('[data-pick='+kind+']');
    await promisify(execFile)('powershell.exe',['-NoProfile','-File',path.join(root,'tests/launcher/select_folder.ps1'),
      '-LauncherPid',String(pid),'-Kind',kind,'-Folder',folder],{windowsHide:true,timeout:35000});
    await wait('document.getElementById('+JSON.stringify(kind.toLowerCase())+').value==='+JSON.stringify(folder),'native folder picker result');
  };
  await browse('ExeFS',exefs);
  assert.equal(await evaluate("document.querySelector('#build-code').disabled"),true);
  await browse('RomFS',romfs);
  await fs.writeFile(path.join(state,'pause'),'');
  await click('#build-code');
  await wait("document.querySelector('#build-phase').textContent.includes('Step 2 of 3') && !document.querySelector('#cancel').hidden",'visible build phase and Cancel');
  assert.equal(await evaluate("document.querySelector('#exefs').disabled && document.querySelector('#play').disabled"),true);
  await fs.writeFile(path.join(run,'build-progress.png'),Buffer.from((await cdp('Page.captureScreenshot')).data,'base64'));
  await click('#cancel');
  await wait("document.querySelector('#feedback').textContent.startsWith('Cancelled.') && !document.querySelector('#build-code').disabled",'cancelled build');
  assert.equal(await evaluate("document.querySelector('#play').disabled"),true);
  assert.equal(await exists(path.join(state,'fixture-receipt.json')),false);
  await fs.unlink(path.join(state,'pause'));
  await click('#build-code');
  await wait("!document.querySelector('#play').disabled && document.querySelector('#feedback').textContent.includes('ready to play')",'completed build');
  assert.deepEqual(JSON.parse(await fs.readFile(path.join(state,'fixture-receipt.json'),'utf8')),{exefs,romfs});
  await input('romfs',romfs+' changed');
  assert.equal(await evaluate("document.querySelector('#play').disabled"),true);
  await input('romfs',romfs);
  await click('#play');
  await wait("document.querySelector('#feedback').textContent==='Finished.' && !document.querySelector('#play').disabled",'Play returned');
  assert.equal((await fs.readFile(path.join(state,'plays.txt'),'utf8')).trim(),'fixture Play');
  passed.push('both native folder pickers return the selected Unicode directories',
    'selected Unicode locations reach one backend command', 'build phases stream visibly',
    'Cancel stops the running build and leaves Play disabled','retry builds and enables ready saved inputs',
    'changing a selected path disables Play', 'GUI Play uses the ready paths');
});
if (direct) {
  const profile=path.join(run,'direct-success-webview');
  await promisify(execFile)(fakeExe,['--play-game'],{windowsHide:true,timeout:30000,
    env:{...process.env,LOCALAPPDATA:fakeData,WEBVIEW2_USER_DATA_FOLDER:profile}});
  assert.equal((await fs.readFile(path.join(state,'plays.txt'),'utf8')).trim().split(/\r?\n/).length,2);
  assert.equal(await exists(profile),false);
  passed.push('successful direct Play bypasses setup');
}
await fs.writeFile(path.join(run,'verification.json'),JSON.stringify({exe,version,passed},null,2));
console.log('Verified Windows player launcher:',run);
