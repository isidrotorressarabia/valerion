'use strict';
window.Valerion = {
  imageSize(url,width=640){
    try{const u=new URL(url,location.origin);if(u.hostname==='res.cloudinary.com'&&/^\/[^/]+\/image\/upload\/v\d+\//.test(u.pathname)&&[160,640,1280].includes(width))u.pathname=u.pathname.replace('/image/upload/','/image/upload/f_auto,q_auto,c_limit,w_'+width+'/');return u.href;}catch{return url;}
  },
  money: value => new Intl.NumberFormat('es-MX', {style: 'currency', currency: 'MXN'}).format(Number(value)),
  toast(message) {
    const el = document.querySelector('#toast');
    el.textContent = message; el.hidden = false;
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { el.hidden = true; }, 4500);
  },
  async request(url, options = {}) {
    const response = await fetch(url, {credentials: 'same-origin', ...options});
    if (response.status === 204) return null;
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.errores?.map(e => e.mensaje).join(' ') || (typeof data.detail === 'string' ? data.detail : 'No se pudo completar la operación.'));
      error.status = response.status;
      throw error;
    }
    return data;
  },
  async upload(file, csrf) {
    if (file.size > 8 * 1024 * 1024) throw new Error('La imagen supera 8 MB. Selecciona una más pequeña.');
    const data = new FormData(); data.append('archivo', file);
    return this.request('/api/imagenes', {method: 'POST', headers: {'X-CSRF-Token': csrf}, body: data});
  },
  fallback(image) {
    image.removeAttribute('srcset');
    const reference = document.body.dataset.fallbackImage || '/static/img/placeholder.svg';
    if (image.src.includes('photo-1732043846982-5522e1e2626c') && reference === '/static/img/placeholder.svg') image.src = '/static/img/agave-reference.jpg';
    else if (image.src !== new URL(reference, location.href).href && !image.src.endsWith('/static/img/placeholder.svg')) image.src = reference;
    else if (!image.src.endsWith('/static/img/placeholder.svg')) image.src = '/static/img/placeholder.svg';
  },
};
document.addEventListener('error', event => {
  if (event.target instanceof HTMLImageElement && event.target.hasAttribute('data-fallback')) Valerion.fallback(event.target);
}, true);
document.querySelectorAll('img[data-fallback]').forEach(img => { if (img.complete && img.naturalWidth === 0) Valerion.fallback(img); });
document.addEventListener('click', event => {
  const close = event.target.closest('[data-close]');
  if (close) document.getElementById(close.dataset.close).close();
});
