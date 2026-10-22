'use strict';
let contentCsrf = '', pendingUploads = 0, dirty = false, saving = false;
const form = document.querySelector('#content-form');
const save = document.querySelector('#content-save');
const status = document.querySelector('#content-save-status');
const error = document.querySelector('#content-error');
function values() { return Object.fromEntries(Array.from(form.querySelectorAll('[data-content-key]')).map(el => [el.dataset.contentKey, el.value])); }
let savedSnapshot = JSON.stringify(values());
function updateState() {
  dirty = JSON.stringify(values()) !== savedSnapshot;
  save.disabled = !contentCsrf || pendingUploads > 0 || saving;
  status.textContent = saving ? 'Guardando cambios…' : pendingUploads ? 'Subiendo imagen…' : dirty ? 'Hay cambios sin guardar.' : 'Sin cambios pendientes.';
}
form.addEventListener('input', updateState);
window.addEventListener('beforeunload', event => { if (dirty || pendingUploads) { event.preventDefault(); event.returnValue = ''; } });
form.querySelectorAll('[data-image-field]').forEach(field => {
  field.addEventListener('change', () => {
    const reference = field.value.trim();
    if (reference.startsWith('https://') || reference.startsWith('/media/') || reference.startsWith('/static/img/')) document.getElementById(`image-${field.dataset.contentKey}`).src = reference;
  });
});
form.querySelectorAll('[data-upload-target]').forEach(input => {
  input.addEventListener('change', async () => {
    const file = input.files[0]; if (!file) return;
    const key = input.dataset.uploadTarget;
    const field = document.getElementById(`content-${key}`);
    const preview = document.getElementById(`image-${key}`);
    const message = document.getElementById(`status-${key}`);
    const oldValue = field.value;
    input.disabled = true; field.disabled = true; pendingUploads++; updateState();
    const blob = URL.createObjectURL(file); preview.src = blob; message.textContent = 'Subiendo imagen…';
    try {
      const result = await Valerion.upload(file, contentCsrf);
      field.value = result.url; preview.src = result.url;
      message.textContent = 'Imagen cargada. Guarda los cambios para publicarla.';
    } catch (e) {
      preview.src = oldValue; message.textContent = e.message;
      if (e.status === 401) { dirty = false; pendingUploads = 0; window.location.assign('/admin/login'); }
    } finally { URL.revokeObjectURL(blob); input.disabled = false; field.disabled = false; input.value = ''; pendingUploads = Math.max(0, pendingUploads - 1); updateState(); }
  });
});
form.addEventListener('submit', async event => {
  event.preventDefault(); if (!contentCsrf || pendingUploads || saving) return;
  const body = {valores: values()}; saving = true; updateState(); error.hidden = true;
  const controls = Array.from(form.querySelectorAll('input,textarea,button'));
  controls.forEach(el => { el.disabled = true; });
  try {
    await Valerion.request('/api/contenido', {method: 'PUT', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': contentCsrf}, body: JSON.stringify(body)});
    savedSnapshot = JSON.stringify(values());
    Valerion.toast('Contenido actualizado. Recarga la vista del cliente para ver los cambios.');
  } catch (e) {
    error.textContent = e.message; error.hidden = false; error.scrollIntoView({block: 'center'});
    if (e.status === 401) { dirty = false; window.location.assign('/admin/login'); }
  } finally { saving = false; controls.forEach(el => { el.disabled = false; }); updateState(); }
});
document.querySelector('#content-logout').addEventListener('click', async () => {
  if (pendingUploads || saving) return;
  try { await Valerion.request('/api/auth/logout', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': contentCsrf}, body: '{}'}); dirty = false; window.location.assign('/admin/login'); }
  catch (e) { Valerion.toast(e.message); }
});
(async () => {
  try { contentCsrf = (await Valerion.request('/api/auth/me')).csrf_token; form.querySelectorAll('[data-upload-target]').forEach(input => { input.disabled = false; }); updateState(); }
  catch (e) { if (e.status === 401) window.location.assign('/admin/login'); else { error.textContent = e.message; error.hidden = false; } }
})();
