import {invoke} from '@tauri-apps/api/core';

const id = key => document.getElementById(key);
const field = key => id('graphics-' + key);
const options = {
  output: {console: 'Game default', '720p': '1280 × 720', '1080p': '1920 × 1080', '1440p': '2560 × 1440',
    '2160p': '3840 × 2160 (4K)', window: 'Window size at startup', '2560x1080': '2560 × 1080',
    '3440x1440': '3440 × 1440', '5120x1440': '5120 × 1440 (drawn at 3840 × 1080)'},
  scale: {'0': 'Game default (half per side)', '0.33': '33%', '0.5': '50%', '0.59': '59%', '0.67': '67%',
    '0.75': '75%', '0.85': '85%', '1.0': '100% (native)'},
  fps: {'30': '30 FPS (original)', '60': '60 FPS', '120': '120 FPS'},
  upscaler: {taau: 'TAAU (built in)', auto: 'Automatic', dlss: 'NVIDIA DLSS', fsr3: 'AMD FSR 3', xess: 'Intel XeSS', bilinear: 'Bilinear'},
  reflections: {full: 'Full', reduced: 'Reduced', off: 'Off'},
  ray_reflections: {off: 'Off (default)', on: 'On (visible surfaces)'},
};
const keys = Object.keys(options);
let original, busy = false, blocked = true, arabic;

function scaleValue(data) { return data.fixed_render ? 'fixed' : data.scale; }
function changes() {
  if (!original) return {};
  return Object.fromEntries(keys.filter(key => field(key).value !== (key === 'scale' ? scaleValue(original) : original[key]))
    .map(key => [key, field(key).value]));
}
function controls() {
  id('graphics-open').disabled = blocked || busy;
  id('arabic-text').disabled = blocked || busy || !arabic || (!arabic.available && !arabic.enabled);
  for (const key of keys) field(key).disabled = blocked || busy || !original;
  id('graphics-save').disabled = blocked || busy || !Object.keys(changes()).length;
  id('graphics-close').disabled = busy;
}
export function blockPreferences(value) { blocked = value; controls(); }
export function preferencesBusy() { return busy; }

function describeSize() {
  const output = field('output').value, scale = field('scale').value;
  if (scale === 'fixed') {
    id('graphics-size').textContent = 'The game uses your fixed 3D render size. Choose a percentage to replace it.';
  } else if (output === '2160p' && (scale === '0' || scale === '0.5' || scale === '1.0')) {
    id('graphics-size').textContent = scale === '1.0' ? '3D rendering and output are both 3840 × 2160.'
      : 'The game renders 3D at 1920 × 1080 and upscales it to 3840 × 2160.';
  } else {
    id('graphics-size').textContent = 'Render scale sets the 3D size per side. Output resolution also sets the size of text and menus.';
  }
}
function showGraphics(data) {
  original = data;
  for (const key of keys) {
    const select = field(key), selected = key === 'scale' ? scaleValue(data) : data[key];
    select.replaceChildren();
    for (const [value, label] of Object.entries(options[key])) {
      const missing = key === 'upscaler' && data.installed[value] === false;
      const option = new Option(missing ? label + ' (files missing)' : label, value);
      option.disabled = missing;
      select.add(option);
    }
    if (![...select.options].some(option => option.value === selected)) {
      select.add(new Option(selected === 'fixed' ? 'Fixed: ' + data.fixed_render : 'Custom: ' + selected, selected));
    }
    select.value = selected;
  }
  id('graphics-vendor').textContent = data.installed.dlss
    ? 'DLSS files found. The game checks NVIDIA RTX and driver support at startup.'
    : 'DLSS requires NVIDIA RTX and the four Streamline DLLs in upscalers\\streamline. TAAU works out of the box.';
  describeSize();
  controls();
}
function showArabic(data) {
  arabic = data;
  id('arabic-text').checked = data.enabled;
  const coverage = data.coverage || 'Coverage is unavailable for this catalog.';
  id('arabic-coverage').textContent = data.available ? [data.message, coverage].filter(Boolean).join(' ') :
    (data.message || 'Arabic preview files are unavailable.');
  controls();
}
export async function loadArabic() {
  try { showArabic(await invoke('arabic_text', {enabled: null})); }
  catch (error) { id('arabic-coverage').textContent = String(error); }
}
export function bindPreferences(updateControls) {
  function setBusy(value) { busy = value; updateControls(); }
  function close() {
    id('graphics-panel').hidden = true;
    id('graphics-open').setAttribute('aria-expanded', 'false');
    id('graphics-open').focus();
  }
  id('graphics-open').onclick = async () => {
    if (!id('graphics-panel').hidden) { close(); return; }
    original = undefined;
    id('graphics-panel').hidden = false;
    id('graphics-open').setAttribute('aria-expanded', 'true');
    id('graphics-message').textContent = 'Loading graphics settings…';
    setBusy(true);
    try { showGraphics(await invoke('graphics', {changes: {}})); id('graphics-message').textContent = ''; }
    catch (error) { id('graphics-message').textContent = String(error); }
    finally {
      setBusy(false);
      id('graphics-panel').scrollIntoView({block: 'nearest'});
      field('output').focus({preventScroll: true});
    }
  };
  for (const key of keys) field(key).onchange = () => { describeSize(); id('graphics-message').textContent = ''; controls(); };
  id('graphics-close').onclick = close;
  id('graphics-save').onclick = async () => {
    const edits = changes();
    setBusy(true);
    try {
      showGraphics(await invoke('graphics', {changes: edits}));
      id('graphics-message').textContent = 'Graphics settings saved for the next game start.';
    } catch (error) { id('graphics-message').textContent = String(error); }
    finally { setBusy(false); }
  };
  id('arabic-text').onchange = async () => {
    const enabled = id('arabic-text').checked;
    setBusy(true);
    try { showArabic(await invoke('arabic_text', {enabled})); }
    catch (error) { id('arabic-text').checked = arabic.enabled; id('arabic-coverage').textContent = String(error); }
    finally { setBusy(false); }
  };
}
