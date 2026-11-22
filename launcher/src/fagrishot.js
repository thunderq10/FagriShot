import {invoke} from '@tauri-apps/api/core';
import {listen} from '@tauri-apps/api/event';
import './fagrishot.css';

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
  const blocked = !status || Boolean(active) || picking;
  const exefs = q('exefs').value;
  const romfs = q('romfs').value;
  const bothEntered = Boolean(exefs.trim() && romfs.trim());
  const saved = exefs === status?.exefs && romfs === status?.romfs;
  const needsRepair = Boolean(status?.settings_warning);
  for (const button of document.querySelectorAll('[data-action],[data-pick]')) {
    button.disabled = blocked;
  }
  document.querySelector('[data-action="configure"]').disabled = blocked || !bothEntered;
  document.querySelector('[data-action="inspect"]').disabled = blocked || needsRepair || !bothEntered;
  q('build-code').disabled = blocked || needsRepair || !status?.source_checkout || !exefs.trim() ||
    (exefs === status?.exefs && !status?.exefs_present);
  const canUseGame = saved && bothEntered && status?.exefs_present && status?.romfs_present && status?.has_pair && status?.exe_built;
  q('prepare').disabled = blocked || needsRepair || !canUseGame || !status?.source_checkout;
  q('play').disabled = blocked || needsRepair || !canUseGame;
  q('restore').disabled = q('snapshot').disabled = blocked || !q('snapshot').value;
  q('exefs').disabled = q('romfs').disabled = blocked;
  q('cancel').hidden = !active?.canCancel;
  q('cancel').disabled = !active?.canCancel || active.cancelPending;
  if (status) {
    q('input-state').textContent = !saved ? 'Unsaved locations' : !status.exefs_present ? 'Awaiting ExeFS' :
      !status.romfs_present ? 'Awaiting RomFS' : 'Folders found';
  }
}

async function refresh(reloadLocations = false) {
  const next = await invoke('overview');
  for (const key of ['exefs','romfs']) {
    if (!loadedLocations || reloadLocations || q(key).value === status?.[key]) q(key).value = next[key];
  }
  loadedLocations = true;
  status = next;
  for (const key of ['settings','saves','logs']) q('loc-'+key).textContent = status[key];
  q('settings-warning').textContent = status.settings_warning || '';
  q('settings-warning').hidden = !status.settings_warning;
  const snapshots = q('snapshot');
  const previous = snapshots.value;
  snapshots.replaceChildren();
  for (const id of [...status.backup_ids].reverse()) snapshots.add(new Option(id,id));
  if (status.backup_ids.includes(previous)) snapshots.value = previous;
  if (!snapshots.options.length) snapshots.add(new Option('No backups yet',''));
  if (!status.source_checkout) q('build-help').textContent = 'The local tools package supports diagnostics and save recovery. Open the launcher inside your FagriShot source checkout to build game code.';
  q('retry').hidden = true;
  controls();
}

function statusFailed(error) {
  status = undefined;
  q('feedback').textContent = `Could not load launcher status. ${error}`;
  q('retry').hidden = false;
  controls();
}

function log(line) {
  const out = q('output');
  out.append(document.createTextNode(line+'\n'));
  while (out.childNodes.length > 400) out.firstChild.remove();
  out.scrollTop = out.scrollHeight;
}

async function run(action) {
  const trigger = document.querySelector(`[data-action="${action}"]`);
  if (active || trigger.disabled) return;
  const backupId = q('snapshot').value;
  if (action === 'restore' && !window.confirm(`Restore snapshot ${backupId}? FagriShot will back up your current saves first.`)) return;
  active = {action, canCancel:false, cancelPending:false};
  controls();
  q('feedback').textContent = 'Running\u2026';
  q('output').replaceChildren();
  try {
    await invoke('action',{action, exefs:q('exefs').value, romfs:q('romfs').value, backupId});
    q('feedback').textContent = 'Finished.';
    if (['configure','build-code','restore','backup','backups','prepare'].includes(action)) {
      await refresh(action === 'configure').catch(statusFailed);
    }
  } catch (error) { q('feedback').textContent = String(error); }
  finally { active = undefined; controls(); }
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
navigate('setup');
controls();
loadStatus();
