'use strict';
let csrf = '', products = [], deletingId = null, uploading = false, uploadSequence = 0;
let page=1, pageData={}, loadSequence=0, searchTimer;
const $ = selector => document.querySelector(selector);
const make = (tag, cls, text) => { const el = document.createElement(tag); if (cls) el.className = cls; if (text !== undefined) el.textContent = text; return el; };
async function adminRequest(url, method = 'GET', body) {
  try {
    return await Valerion.request(url, {method, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, ...(body !== undefined ? {body: JSON.stringify(body)} : {})});
  } catch (e) { if (e.status === 401) window.location.assign('/admin/login'); throw e; }
}
function paintTable() {
  const query = $('#admin-search').value.trim().toLocaleLowerCase('es');
  const matches = products;
  $('#admin-rows').replaceChildren();
  matches.forEach(p => {
    const row = make('tr'); const product = make('td'); const info = make('div', 'table-product');
    const img = make('img'); img.setAttribute('data-fallback', ''); img.src = Valerion.imageSize(p.imagen_url,160); img.alt = ''; img.width = 46; img.height = 56;
    const label = make('div'); label.append(make('strong', '', p.nombre), make('small', '', `#${String(p.id).padStart(3, '0')} · Valerion`)); info.append(img, label); product.append(info);
    const price = make('td', 'table-price', Valerion.money(p.precio));
    const state = make('td'); state.append(make('span', `status-badge ${p.link_venta ? 'active' : ''}`, !p.published ? 'Borrador' : p.availability==='sold_out' ? 'Agotado' : 'Publicado'));
    const updated = make('td', 'muted', new Intl.DateTimeFormat('es-MX', {day: '2-digit', month: 'short', year: 'numeric'}).format(new Date(p.actualizado_en)));
    const actions = make('td'); const edit = make('button', 'table-button', 'Editar'); edit.dataset.edit = p.id; edit.setAttribute('aria-label', `Editar ${p.nombre}`);
    const remove = make('button', 'table-button delete', 'Eliminar'); remove.dataset.delete = p.id; remove.setAttribute('aria-label', `Eliminar ${p.nombre}`);
    const duplicate = make('button','table-button','Duplicar'); duplicate.dataset.duplicate=p.id; const visit=make('a','table-button','Ver');visit.href='/producto/'+p.id;visit.target='_blank';visit.rel='noopener';actions.append(edit,duplicate,remove);if(p.published)actions.append(visit); row.append(product, price, state, updated, actions); $('#admin-rows').append(row);
  });
  $('#admin-empty').hidden = matches.length > 0;
  $('#table-count').textContent = pageData.total;
  $('#admin-status').textContent = `${pageData.total} productos · Página ${page} de ${pageData.pages}`;
  $('#metric-total').textContent = pageData.metrics.total;
  $('#metric-average').textContent = Valerion.money(pageData.metrics.average_cents/100);
  $('#metric-links').textContent = pageData.metrics.links;
  $('#admin-prev').disabled=page<=1;$('#admin-next').disabled=page>=pageData.pages;
}
async function load() {
  const seq=++loadSequence;$('#admin-error').hidden = true;
  try {
    const data=await adminRequest('/api/admin/mezcales?'+new URLSearchParams({page,q:$('#admin-search').value}));
    if(seq!==loadSequence)return;
    if(page>data.pages){page=data.pages;return load();}
    pageData=data;products=data.items;paintTable();
  } catch (e) { if(seq===loadSequence){$('#admin-error').textContent = e.message; $('#admin-error').hidden = false;} }
}
$('#admin-prev').onclick=()=>{if(page>1){page--;load();}};
$('#admin-next').onclick=()=>{if(page<pageData.pages){page++;load();}};

function preview() {
  $('#preview-name').textContent = $('#nombre').value || 'Tu nuevo mezcal';
  $('#preview-price').textContent = `${Valerion.money($('#precio').value || 0)} MXN`;
  $('#description-count').textContent = `${$('#descripcion').value.length} / 2000`;
}
function previewImage() {
  const value = $('#imagen_url').value.trim();
  $('#preview-image').src = (value.startsWith('https://') || value.startsWith('/media/') || value.startsWith('/static/img/')) ? value : '/static/img/placeholder.svg';
}
function openForm(product = null) {
  uploadSequence++; uploading = false; $('#product-image-file').disabled = false; $('#imagen_url').disabled = false; $('#product-upload-status').textContent = '';
  $('#product-form').reset(); $('#form-error').hidden = true;
  $('#product-id').value = product?.id || '';
  $('#form-title').textContent = product ? 'Editar producto' : 'Nuevo producto';
  for (const field of ['nombre', 'precio', 'descripcion', 'link_venta', 'imagen_url']) $(`#${field}`).value = product?.[field] || '';
  $('#save-product').disabled = false; $('#save-product').textContent = 'Guardar producto';
  productExtras=structuredClone(Object.fromEntries(Object.entries(product||{published:false,featured:false,category:'',availability:'available',gallery:[],blocks:[],specs:[]}).filter(([key])=>['published','featured','category','availability','gallery','blocks','specs'].includes(key)))); renderProductExtras(); preview(); previewImage(); $('#edit-dialog').showModal(); $('#nombre').focus();
}
$('#new-product').addEventListener('click', () => openForm());
$('#admin-search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{page=1;load();},250);});
$('#product-form').addEventListener('input', preview);
let previewTimer;
$('#imagen_url').addEventListener('input', () => { clearTimeout(previewTimer); previewTimer = setTimeout(previewImage, 400); });
$('#product-image-file').addEventListener('change', async event => {
  const input = event.currentTarget; const file = input.files[0]; if (!file) return;
  const sequence = ++uploadSequence; uploading = true; input.disabled = true;
  $('#imagen_url').disabled = true; $('#save-product').disabled = true;
  $('#product-upload-status').textContent = 'Subiendo imagen…';
  const blob = URL.createObjectURL(file); $('#preview-image').src = blob;
  try {
    const result = await Valerion.upload(file, csrf);
    if (sequence === uploadSequence) { $('#imagen_url').value = result.url; previewImage(); $('#product-upload-status').textContent = 'Imagen cargada. Guarda el producto para publicarla.'; }
  } catch (e) {
    if (sequence === uploadSequence) { $('#product-upload-status').textContent = e.message; previewImage(); }
    if (e.status === 401) window.location.assign('/admin/login');
  } finally {
    URL.revokeObjectURL(blob);
    if (sequence === uploadSequence) { uploading = false; input.disabled = false; input.value = ''; $('#imagen_url').disabled = false; $('#save-product').disabled = false; }
  }
});
$('#admin-rows').addEventListener('click', async event => {
  const duplicate=event.target.closest('[data-duplicate]'),edit=event.target.closest('[data-edit]'),remove=event.target.closest('[data-delete]');
  if(edit||duplicate){
    const button=edit||duplicate;button.disabled=true;
    try{const product=await adminRequest('/api/admin/mezcales/'+(edit?edit.dataset.edit:duplicate.dataset.duplicate));
      if(duplicate){product.id=null;product.nombre=product.nombre.slice(0,90)+' · copia';product.published=false;}openForm(product);
    }catch(e){Valerion.toast(e.message);}finally{button.disabled=false;}
  }
  if(remove){deletingId=Number(remove.dataset.delete);$('#delete-name').textContent=products.find(p=>p.id===deletingId).nombre;$('#delete-error').hidden=true;$('#confirm-delete').disabled=false;$('#delete-dialog').showModal();}
});
$('#product-form').addEventListener('submit', async event => {
  event.preventDefault(); if (uploading || EditorKit.pending) return; const button = $('#save-product'); button.disabled = true; button.textContent = 'Guardando…'; $('#form-error').hidden = true;
  const id = $('#product-id').value;
  const body = Object.fromEntries(new FormData(event.currentTarget)); body.link_venta = body.link_venta || null; Object.assign(body,productExtras);
  try { await adminRequest(`/api/mezcales${id ? '/' + id : ''}`, id ? 'PUT' : 'POST', body); $('#edit-dialog').close(); Valerion.toast(id ? 'Producto actualizado.' : 'Producto creado.'); await load(); }
  catch (e) { $('#form-error').textContent = e.message; $('#form-error').hidden = false; }
  finally { button.disabled = false; button.textContent = 'Guardar producto'; }
});
$('#confirm-delete').addEventListener('click', async () => {
  $('#confirm-delete').disabled = true;
  try { await adminRequest(`/api/mezcales/${deletingId}`, 'DELETE'); $('#delete-dialog').close(); Valerion.toast('Producto eliminado.'); await load(); }
  catch (e) { $('#delete-error').textContent = e.message; $('#delete-error').hidden = false; }
  finally { $('#confirm-delete').disabled = false; }
});
$('#logout').addEventListener('click', async () => { try { await adminRequest('/api/auth/logout', 'POST', {}); window.location.assign('/admin/login'); } catch (e) { Valerion.toast(e.message); } });
(async () => { try { csrf = (await adminRequest('/api/auth/me')).csrf_token; EditorKit.csrf=csrf; $('#new-product').disabled = false; await load(); } catch (e) { $('#admin-error').textContent = e.message; $('#admin-error').hidden = false; } })();

let productExtras={};
EditorKit.changed=()=>{$('#save-product').disabled=uploading||EditorKit.pending>0;};
function renderProductExtras(){
 const kit=EditorKit,host=$('#product-advanced');host.replaceChildren();
 const settings=make('details','content-section');settings.open=true;settings.append(make('summary','','Publicación y clasificación'));
 const fields=make('div','content-fields');fields.append(kit.field(productExtras,'published','Publicar en el catálogo','checkbox'),kit.field(productExtras,'featured','Destacar producto','checkbox'),kit.field(productExtras,'category','Categoría (Espadín, Ensamble…)'),kit.field(productExtras,'availability','Disponibilidad','text',{available:'Disponible',request:'Bajo consulta',sold_out:'Agotado'}));settings.append(fields);host.append(settings);
 const gallery=make('details','content-section');gallery.open=true;gallery.append(make('summary','','Galería · hasta 24 imágenes adicionales'));
 const inside=make('div','advanced-inside');const upload=make('input');upload.type='file';upload.multiple=true;upload.accept='image/jpeg,image/png,image/webp';upload.id='gallery-files';const label=make('label','file-label','Subir varias imágenes desde mi equipo');label.append(upload);inside.append(label);
 const status=make('p','small muted');status.setAttribute('role','status');inside.append(status);const list=make('div','gallery-edit-list');inside.append(list);
 const repaint=kit.list(list,productExtras.gallery,(card,item)=>{card.append(kit.image(item,'url'),kit.field(item,'alt','Descripción accesible'),kit.field(item,'caption','Pie de imagen'));},()=>({url:'',alt:'',caption:''}),'Agregar imagen por URL',24);
 upload.onchange=async()=>{const files=Array.from(upload.files);if(files.length+productExtras.gallery.length>24){status.textContent='Máximo 24 imágenes adicionales.';return;}upload.disabled=true;let done=0;for(const file of files){status.textContent=`Subiendo ${done+1} de ${files.length}…`;try{const result=await kit.upload(file);productExtras.gallery.push({url:result.url,alt:'',caption:''});done++;}catch(e){status.textContent=e.message;break;}}status.textContent=`${done} de ${files.length} imágenes cargadas. Guarda el producto.`;upload.disabled=false;upload.value='';repaint();kit.changed();};
 gallery.append(inside);host.append(gallery);
 const story=make('details','content-section');story.append(make('summary','','Descripción ampliada · texto e imágenes intercalados'));const blocks=make('div','advanced-inside');const blocklist=make('div');blocks.append(blocklist);story.append(blocks);host.append(story);
 const renderBlocks=kit.list(blocklist,productExtras.blocks,(card,block)=>{const kind=kit.field(block,'kind','Tipo de bloque','text',{text:'Texto',image:'Imagen'});kind.querySelector('select').addEventListener('change',()=>renderBlocks());card.append(kind);if(block.kind==='text')card.append(kit.field(block,'title','Título'),kit.field(block,'text','Texto','textarea'));else card.append(kit.image(block,'image'),kit.field(block,'alt','Descripción accesible'),kit.field(block,'caption','Pie de imagen'));},()=>({kind:'text',title:'',text:'',image:'',alt:'',caption:''}),'Agregar bloque',40);
 const specs=make('details','content-section');specs.append(make('summary','','Ficha técnica · campos personalizados'));const specList=make('div','advanced-inside');specs.append(specList);host.append(specs);kit.list(specList,productExtras.specs,(card,spec)=>{card.append(kit.field(spec,'label','Característica (Agave, Presentación…)'),kit.field(spec,'value','Valor'));},()=>({label:'',value:''}),'Agregar característica',20);
}
