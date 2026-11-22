import {invoke} from '@tauri-apps/api/core';
import {listen} from '@tauri-apps/api/event';
import './fagrishot.css';

const q = id => document.getElementById(id);
let status;
let running = false;
let selected = 'setup';
const headings = {setup:'Game setup', data:'Local files', about:'About FagriShot'};

function navigate(page) {
  selected = page;
  q('title').textContent = headings[page];
  for (const section of document.querySelectorAll('main section')) section.hidden = section.id !== page;
  for (const button of document.querySelectorAll('[data-page]')) {
    button.setAttribute('aria-current', button.dataset.page === page ? 'page' : 'false');
  }
}
function controls() {
  for (const b of document.querySelectorAll('[data-action],[data-pick]')) b.disabled = running;
  q('build-code').disabled = running || !status?.source_checkout;
  q('restore').disabled = running || !q('snapshot').value;
  q('prepare').disabled = running || !status?.has_pair || !status?.exe_built;
  q('play').disabled = q('prepare').disabled;
  q('exefs').disabled = q('romfs').disabled = running;
}
async function refresh() {
  status = await invoke('overview');
  for (const k of ['exefs','romfs']) q(k).value = status[k];
  for (const k of ['settings','saves','logs']) q('loc-'+k).textContent = status[k];
  q('input-state').textContent = status.exefs_present ? 'ExeFS selected' : 'Awaiting ExeFS';
  const snapshots=q('snapshot');
  snapshots.replaceChildren();
  for(const id of [...status.backup_ids].reverse()) snapshots.add(new Option(id,id));
  if(!snapshots.options.length) snapshots.add(new Option('No backups yet',''));
  if(!status.source_checkout) q('build-help').textContent='The local tools package supports diagnostics and save recovery. Open the launcher inside your FagriShot source checkout to build game code.';
  controls();
}
function log(line) {
  const out = q('output');
  out.append(document.createTextNode(line+'\n'));
  while (out.childNodes.length > 400) out.firstChild.remove();
  out.scrollTop = out.scrollHeight;
}
async function run(action) {
  if(action==='restore' && !window.confirm('Restore this snapshot? FagriShot will back up your current saves first.')) return;
  running = true;
  controls();
  q('cancel').hidden = action === 'play';
  q('feedback').textContent = 'Running\u2026';
  q('output').replaceChildren();
  try {
    await invoke('action',{action,exefs:q('exefs').value,romfs:q('romfs').value, backupId:q('snapshot').value});
    q('feedback').textContent = 'Finished.';
    if (['configure','build-code','restore','backup','prepare'].includes(action)) await refresh();
  } catch(e) { q('feedback').textContent = String(e); }
  finally { running=false; q('cancel').hidden=true; controls(); }
}
for(const b of document.querySelectorAll('[data-page]')) b.onclick=()=>navigate(b.dataset.page);
for(const b of document.querySelectorAll('[data-action]')) b.onclick=()=>run(b.dataset.action);
for(const b of document.querySelectorAll('[data-pick]')) b.onclick=async()=>{
  try {
    const value=await invoke('pick',{kind:b.dataset.pick});
    if(value) q(b.dataset.pick.toLowerCase()).value=value;
  } catch(e) {q('feedback').textContent=String(e);}
};
q('cancel').onclick=()=>invoke('cancel').catch(e=>q('feedback').textContent=String(e));
listen('build-line', e=>log(String(e.payload))).then(refresh).catch(e=>{
  q('feedback').textContent=String(e);
  controls();
});
navigate(selected);
