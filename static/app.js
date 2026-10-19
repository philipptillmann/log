'use strict';
const $ = id => document.getElementById(id);
const utf8 = new TextEncoder();
const b64 = bytes => { let out = ''; for (const b of bytes) out += String.fromCharCode(b); return btoa(out); };
const unb64 = text => Uint8Array.from(atob(text), c => c.charCodeAt(0));
let key, token, username, vault = {notes: []}, version = 0, selected;
let timer, dirty = false, saving = null, revision = 0, blocked = false;
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; };
async function api(path, data) {
  const response = await fetch('/api/' + path, {method: data === undefined ? 'GET' : 'POST', headers: {'Content-Type':'application/json', ...(token ? {'Authorization':'Bearer ' + token} : {})}, ...(data === undefined ? {} : {body:JSON.stringify(data)})});
  const result = await response.json();
  if (!response.ok) { const error = new Error(result.error || 'Verbindung fehlgeschlagen.'); error.status = response.status; throw error; }
  return result;
}
async function derive(password, salt) {
  const material = await crypto.subtle.importKey('raw', utf8.encode(password), 'PBKDF2', false, ['deriveBits']);
  const bits = new Uint8Array(await crypto.subtle.deriveBits({name:'PBKDF2',hash:'SHA-256',salt:unb64(salt),iterations:600000}, material, 512));
  const result = {auth:b64(bits.slice(32)), key:await crypto.subtle.importKey('raw',bits.slice(0,32),'AES-GCM',false,['encrypt','decrypt'])};
  bits.fill(0);
  return result;
}
async function encryptedVault() {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:utf8.encode('log:v1:' + username)}, key, utf8.encode(JSON.stringify(vault)));
  return {iv:b64(iv),ciphertext:b64(new Uint8Array(ciphertext))};
}
async function authenticate(register) {
  $('auth-error').textContent = '';
  if (!$('auth-form').reportValidity()) return;
  if (!globalThis.crypto?.subtle) { $('auth-error').textContent = 'HTTPS ist erforderlich (oder localhost).'; return; }
  if (register && $('password').value.length < 12) { $('auth-error').textContent = 'Mindestens 12 Zeichen für ein neues Passwort.'; return; }
  if (register && !confirm('Passwort sicher aufbewahren. Ohne Passwort sind deine Einträge nicht wiederherstellbar. Account erstellen?')) return;
  $('login').disabled = $('register').disabled = true;
  try {
    username = $('username').value;
    const salt = register ? b64(crypto.getRandomValues(new Uint8Array(32))) : (await api('salt',{name:username})).salt;
    const derived = await derive($('password').value, salt);
    if (register) await api('register',{name:username,salt,auth:derived.auth});
    token = (await api('login',{name:username,auth:derived.auth})).token;
    key = derived.key;
    const stored = await api('vault');
    vault = stored.vault ? JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:unb64(stored.vault.iv),additionalData:utf8.encode('log:v1:' + username)},key,unb64(stored.vault.ciphertext)))) : {notes:[]};
    if (!Array.isArray(vault.notes)) throw new Error('Ungültiger Datenbestand.');
    version = stored.version; dirty = false; blocked = false; revision = 0;
    $('password').value = '';
    const hour = new Date().getHours();
    $('greeting').textContent = `${hour < 11 ? 'Guten Morgen' : hour < 18 ? 'Guten Tag' : 'Guten Abend'}, ${username}`;
    $('auth-view').hidden = true; $('app').hidden = false;
    if (!vault.notes.length) newNote(); else { selected = [...vault.notes].sort((a,b)=>b.date.localeCompare(a.date))[0].id; render(); loadNote(); }
  } catch (error) { token = null; key = null; $('auth-error').textContent = error.name === 'OperationError' ? 'Entschlüsselung fehlgeschlagen.' : error.message; }
  finally { $('login').disabled = $('register').disabled = false; }
}
$('auth-form').onsubmit = event => { event.preventDefault(); authenticate(false); };
$('register').onclick = () => authenticate(true);
function note() { return vault.notes.find(n=>n.id===selected); }
function changed() {
  dirty = true; revision++; $('status').textContent = 'Nicht gespeichert'; clearTimeout(timer);
  timer = setTimeout(() => save().catch(()=>{}), 700);
}
async function save() {
  clearTimeout(timer);
  if (saving) { await saving; if (dirty) return save(); return; }
  if (!dirty) return;
  if (blocked) throw new Error('Speicherkonflikt: Bitte exportieren und neu laden.');
  saving = (async () => {
    const currentRevision = revision;
    $('status').textContent = 'Speichert …';
    try {
      const encrypted = await encryptedVault();
      const result = await api('vault',{vault:encrypted,version});
      version = result.version;
      if (revision === currentRevision) dirty = false;
      $('status').textContent = dirty ? 'Nicht gespeichert' : 'Gespeichert';
    } catch (error) { if (error.status === 409 || error.status === 401) blocked = true; $('status').textContent = error.message; throw error; }
  })();
  try { await saving; } finally { saving = null; }
  if (dirty) return save();
}
function newNote() {
  const n = {id:crypto.randomUUID(),date:today(),title:'',body:'',folder:'',tags:[]};
  vault.notes.push(n); selected = n.id; changed(); render(); loadNote();
  if (matchMedia('(max-width:760px)').matches) { $('sidebar').hidden = true; $('toggle-sidebar').setAttribute('aria-expanded','false'); }
  $('body').focus();
}
function loadNote() {
  const n = note();
  for (const field of ['date','title','body','folder']) { $(field).value = n ? n[field] : ''; $(field).disabled = !n; }
  $('tags').value = n ? n.tags.join(', ') : ''; $('tags').disabled = !n; $('delete').disabled = !n;
}
function fillSelect(id, values, label) {
  const select = $(id), previous = select.value; select.replaceChildren(new Option(label,''));
  for (const value of values) select.add(new Option(value,value));
  if (values.includes(previous)) select.value = previous;
}
function render() {
  const folders = [...new Set(vault.notes.map(n=>n.folder).filter(Boolean))].sort();
  // Include parent paths so selecting a parent includes all nested folders.
  const paths = new Set(); for (const folder of folders) { const parts = folder.split('/'); for (let i=1;i<=parts.length;i++) paths.add(parts.slice(0,i).join('/')); }
  fillSelect('folder-filter',[...paths].sort(),'Alle Ordner');
  fillSelect('tag-filter',[...new Set(vault.notes.flatMap(n=>n.tags))].sort(),'Alle Tags');
  $('folders').replaceChildren(...[...paths].sort().map(value=>new Option(value,value)));
  const query = $('search').value.toLocaleLowerCase(), folder = $('folder-filter').value, tag = $('tag-filter').value;
  const list = vault.notes.filter(n=>(!folder || n.folder === folder || n.folder.startsWith(folder+'/')) && (!tag || n.tags.includes(tag)) && (!query || [n.title,n.body,n.folder,...n.tags].join(' ').toLocaleLowerCase().includes(query))).sort((a,b)=>b.date.localeCompare(a.date));
  $('notes').replaceChildren();
  for (const n of list) {
    const button = document.createElement('button'); button.type='button'; button.className = n.id === selected ? 'active' : '';
    button.textContent = n.title || n.body.split('\n')[0].slice(0,55) || n.date;
    const date = document.createElement('small'); date.textContent=n.date; button.append(date);
    button.onclick = () => { selected=n.id; render(); loadNote(); if(matchMedia('(max-width:760px)').matches){$('sidebar').hidden=true; $('toggle-sidebar').setAttribute('aria-expanded','false');} };
    $('notes').append(button);
  }
}
for (const field of ['date','title','body','folder','tags']) $(field).addEventListener('input', () => {
  const n = note(); if (!n) return;
  if (field === 'tags') n.tags = [...new Set($('tags').value.split(',').map(t=>t.trim()).filter(Boolean))];
  else n[field] = $(field).value;
  changed(); render();
});
for (const field of ['search','folder-filter','tag-filter']) $(field).addEventListener('input',render);
$('new').onclick = newNote;
$('toggle-sidebar').onclick = () => { $('sidebar').hidden = !$('sidebar').hidden; $('toggle-sidebar').setAttribute('aria-expanded',String(!$('sidebar').hidden)); };
$('details-toggle').onclick = () => { $('details').hidden = !$('details').hidden; $('details-toggle').setAttribute('aria-expanded',String(!$('details').hidden)); };
$('delete').onclick = () => {
  if (!note() || !confirm('Eintrag löschen?')) return;
  vault.notes = vault.notes.filter(n=>n.id!==selected); selected=vault.notes[0]?.id;
  changed(); render(); loadNote();
};
$('export').onclick = () => {
  if (!confirm('Der Export enthält alle Einträge, Ordner und Tags unverschlüsselt. Herunterladen?')) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(vault,null,2)],{type:'application/json'}));
  const a = document.createElement('a'); a.href=url; a.download=`log-${today()}.json`; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
};
$('logout').onclick = async () => {
  try { await save(); } catch { alert('Änderungen wurden nicht gespeichert. Bitte exportieren, bevor du die Seite schließt.'); return; }
  try { await api('logout',{}); } catch { /* Token remains only in memory until the page reloads. */ }
  token=null; key=null; vault={notes:[]}; location.reload();
};
window.addEventListener('beforeunload', event => { if (dirty || saving) {event.preventDefault(); event.returnValue='';} });
window.addEventListener('online', () => { if (dirty && !blocked) save().catch(()=>{}); });
document.addEventListener('visibilitychange', () => { if (document.hidden && dirty && !blocked) save().catch(()=>{}); });
