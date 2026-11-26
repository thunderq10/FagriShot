import {invoke} from '@tauri-apps/api/core';
import {getVersion} from '@tauri-apps/api/app';
import {listen} from '@tauri-apps/api/event';
import './fagrishot.css';
import {bindPreferences, blockPreferences, loadArabic, preferencesBusy} from './player-preferences.js';

const q = id => document.getElementById(id);
const headings = {setup:'Game setup', data:'Local files', about:'About FagriShot'};
let status;
let loadedLocations = false;
let active;
let picking = false;

function navigate(page) {
  q('title').textContent = headings[page];
  for (const section of document.querySelectorAll('main section')) section.hidden = section.id !== page;
  for (const button of document.querySelectorAll('[data-page]')) {
    button.setAttribute('aria-current', button.dataset.page === page ? 'page' : 'false');
  }
}

function controls() {
  const externalBusy = !status || Boolean(active) || picking;
  blockPreferences(externalBusy);
  const blocked = externalBusy || preferencesBusy();
  const exefs = q('exefs').value;
  const romfs = q('romfs').value;
  const bothEntered = Boolean(exefs.trim() && romfs.trim());
  const selected = (status?.locations_saved || status?.locations_detected) && exefs === status.exefs && romfs === status.romfs;
  const needsRepair = Boolean(status?.settings_warning);
  for (const button of document.querySelectorAll('[data-action],[data-pick]')) {
    button.disabled = blocked;
  }
  q('build-code').disabled = blocked || !bothEntered || !status?.build_available || Boolean(status?.setup_error || status?.cpu_error);
  q('play').disabled = blocked || needsRepair || Boolean(status?.cpu_error) || !selected || !bothEntered || status?.play_ready !== true;
  q('restore').disabled = q('snapshot').disabled = blocked || !q('snapshot').value;
  q('exefs').disabled = q('romfs').disabled = blocked;
  q('cancel').hidden = !active?.canCancel;
  q('cancel').disabled = !active?.canCancel || active.cancelPending;
  if (status) {
    q('input-state').textContent = !exefs.trim() && !romfs.trim() ? 'Choose both folders' :
      !exefs.trim() ? 'Choose ExeFS' : !romfs.trim() ? 'Choose RomFS' :
      !selected ? 'New folder selection' : status.play_ready ? 'Ready to play' : !status.exefs_present ? 'Awaiting ExeFS' :
      !status.romfs_present ? 'Awaiting RomFS' : status.locations_detected ? 'Detected beside launcher' : 'Folders found';
  }
}

async function refresh(reloadLocations = false) {
  const next = await invoke('overview');
  for (const key of ['exefs','romfs']) {
    const previous = status?.locations_saved || status?.locations_detected ? status[key] : '';
    if (!loadedLocations || reloadLocations || q(key).value === previous) {
      q(key).value = next.locations_saved || next.locations_detected ? next[key] : '';
    }
  }
  loadedLocations = true;
  status = next;
  for (const key of ['settings','saves','logs']) q('loc-'+key).textContent = status[key];
  q('settings-warning').textContent = [status.settings_warning, status.setup_error, status.cpu_error].filter(Boolean).join(' ');
  q('settings-warning').hidden = !q('settings-warning').textContent;
  const snapshots = q('snapshot');
  const previous = snapshots.value;
  snapshots.replaceChildren();
  for (const id of [...status.backup_ids].reverse()) snapshots.add(new Option(id,id));
  if (status.backup_ids.includes(previous)) snapshots.value = previous;
  if (!snapshots.options.length) snapshots.add(new Option('No backups yet',''));
  q('build-help').textContent = status.play_ready ? 'Your build is ready. Choose Play to start the game.' :
    status.build_available ? 'Choose Build game code once to verify your files, compile the game and prepare shaders. On your next visit, choose Play.' :
    'To build the game, extract the complete FagriShot Windows player ZIP and open its launcher.';
  q('retry').hidden = true;
  controls();
}

function statusFailed(error) {
  status = undefined;
  q('settings-warning').textContent = String(error);
  q('settings-warning').hidden = false;
  q('feedback').textContent = 'Could not load launcher status.';
  q('retry').hidden = false;
  controls();
}

function log(line) {
  const out = q('output');
  out.append(document.createTextNode(line+'\n'));
  while (out.childNodes.length > 400) out.firstChild.remove();
  out.scrollTop = out.scrollHeight;
  if (active?.action === 'build-and-prepare') {
    const phase = /^\[setup (\d+)\/(\d+)\]\s+(.+)$/.exec(line);
    if (phase) q('build-phase').textContent = `Step ${phase[1]} of ${phase[2]} · ${phase[3]}`;
  }
}

async function run(action) {
  const trigger = document.querySelector(`[data-action="${action}"]`);
  if (active || trigger.disabled) return;
  const backupId = q('snapshot').value;
  if (action === 'restore' && !window.confirm(`Restore snapshot ${backupId}? FagriShot will back up your current saves first.`)) return;
  active = {action, canCancel:false, cancelPending:false};
  const building = action === 'build-and-prepare';
  q('build-progress').hidden = !building;
  if (building) q('build-phase').textContent = 'Starting build…';
  controls();
  q('feedback').textContent = action === 'play' ? 'Opening your built game…' : 'Running\u2026';
  q('output').replaceChildren();
  try {
    await invoke('action',{action, exefs:q('exefs').value, romfs:q('romfs').value, backupId});
    if (building || action === 'play') q('direct-play-error').hidden = true;
    if (building || ['restore','backup','backups'].includes(action)) {
      try { await refresh(building); } catch (error) { statusFailed(error); return; }
    }
    q('feedback').textContent = building && status?.play_ready ? 'Build complete. Your game is ready to play.' : 'Finished.';
  } catch (error) {
    q('feedback').textContent = String(error);
    if (building || action === 'play') {
      try { await refresh(); } catch (refreshError) { statusFailed(refreshError); log(String(error)); }
    }
  } finally { active = undefined; q('build-progress').hidden = true; controls(); }
}

for (const button of document.querySelectorAll('[data-page]')) button.onclick = () => navigate(button.dataset.page);
for (const button of document.querySelectorAll('[data-action]')) button.onclick = () => run(button.dataset.action);
for (const key of ['exefs','romfs']) q(key).oninput = controls;
for (const button of document.querySelectorAll('[data-pick]')) button.onclick = async () => {
  if (button.disabled) return;
  picking = true;
  controls();
  try {
    const value = await invoke('pick',{kind:button.dataset.pick});
    if (value) q(button.dataset.pick.toLowerCase()).value = value;
  } catch (error) { q('feedback').textContent = String(error); }
  finally { picking = false; controls(); }
};
q('cancel').onclick = async () => {
  const operation = active;
  if (!operation?.canCancel || operation.cancelPending) return;
  operation.cancelPending = true;
  controls();
  try {
    if (await invoke('cancel') && active === operation) {
      operation.canCancel = false;
      q('feedback').textContent = 'Cancelling\u2026';
    }
  } catch (error) {
    if (active === operation) q('feedback').textContent = String(error);
  } finally {
    operation.cancelPending = false;
    controls();
  }
};
let logListener, cancelListener;
async function loadStatus() {
  q('retry').disabled = true;
  try {
    logListener ??= await listen('build-line', event => log(String(event.payload)));
    cancelListener ??= await listen('action-cancellable', event => {
      if (active?.action === event.payload) { active.canCancel = true; controls(); }
    });
    await refresh();
    q('feedback').textContent = '';
  } catch (error) { statusFailed(error); }
  finally { q('retry').disabled = false; }
}
q('retry').onclick = loadStatus;
bindPreferences(controls);
loadArabic();
navigate('setup');
controls();
getVersion().then(version => {
  for (const label of document.querySelectorAll('[data-version]')) label.textContent = version;
}).catch(error => log(String(error)));
invoke('startup_play_error').then(error => {
  if (error) {
    q('play-diagnostic').textContent = error;
    q('direct-play-error').hidden = false;
  }
}).catch(error => log(String(error)));
loadStatus();
