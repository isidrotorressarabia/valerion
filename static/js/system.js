'use strict';
(()=>{
let csrf='';const $=s=>document.querySelector(s),status=$('#system-status');
const node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
async function request(url,method='GET'){try{return await Valerion.request(url,{method,headers:{'X-CSRF-Token':csrf,'Content-Type':'application/json'},...(method==='POST'?{body:'{}'}:{})});}catch(e){if(e.status===401)location.assign('/admin/login');throw e;}}
async function refresh(){status.textContent='Actualizando…';try{
 const [info,assets]=await Promise.all([request('/api/admin/sistema'),request('/api/admin/imagenes')]);
 $('#system-summary').textContent=`Versión ${info.version} · Base de datos disponible · ${assets.items.length} imágenes listadas`;
 $('#system-details').textContent=JSON.stringify({conexiones:info.pool,cache_contenido:info.content_cache,cache_catalogo:info.catalog_cache},null,2);
 $('#system-events').replaceChildren(...info.events.map(e=>node('li',`${new Date(e.created_at*1000).toLocaleString('es-MX')} · ${e.action} ${e.entity} · ${e.actor}`)));
 $('#system-assets').replaceChildren(...assets.items.map(asset=>{const card=node('article'),img=node('img');img.src=Valerion.imageSize(asset.url,160);img.alt='Imagen almacenada '+asset.id;img.loading='lazy';card.append(img,node('p',`${asset.width} × ${asset.height} · ${Math.ceil(asset.bytes/1024)} KB`),node('p',asset.referenced?'En uso':asset.can_delete?'Sin referencias':'Carga reciente'));
 const button=node('button','Eliminar');button.className='button secondary';button.disabled=!asset.can_delete;button.onclick=async()=>{if(!confirm('¿Eliminar esta imagen de Cloudinary? Guarda primero un respaldo con imágenes. La eliminación no se puede deshacer desde el panel.'))return;button.disabled=true;try{await request('/api/admin/imagenes/'+asset.id,'DELETE');await refresh();}catch(e){status.textContent=e.message;button.disabled=false;}};card.append(button);return card;}));status.textContent='Información actualizada.';
 }catch(e){status.textContent=e.message;}}
 document.querySelectorAll('[data-backup]').forEach(button=>button.onclick=async()=>{
 const buttons=document.querySelectorAll('[data-backup]');buttons.forEach(b=>b.disabled=true);status.textContent='Preparando respaldo… Mantén esta página abierta.';
 try{const response=await fetch('/api/admin/respaldo?images='+button.dataset.backup,{credentials:'same-origin'});if(!response.ok){const data=await response.json();throw new Error(data.detail||'No se pudo descargar.');}const blob=await response.blob(),url=URL.createObjectURL(blob),a=node('a');a.href=url;a.download='Valerion_Respaldo_'+new Date().toISOString().slice(0,10)+'.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);status.textContent='Respaldo descargado.';}catch(e){status.textContent=e.message;}finally{buttons.forEach(b=>b.disabled=false);}
 });
 $('#revoke-admin').onclick=async()=>{if(!confirm('¿Cerrar todas las sesiones de administrador, incluida esta?'))return;try{await request('/api/admin/sesiones/revocar','POST');location.assign('/admin/login');}catch(e){status.textContent=e.message;}};
 $('#refresh-system').onclick=refresh;$('#logout').onclick=async()=>{try{await request('/api/auth/logout','POST');location.assign('/admin/login');}catch(e){status.textContent=e.message;}};
 (async()=>{try{csrf=(await request('/api/auth/me')).csrf_token;await refresh();}catch(e){status.textContent=e.message;}})();
})();
