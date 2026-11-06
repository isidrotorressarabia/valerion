'use strict';
const K=EditorKit, host=document.querySelector('#studio-editor');
let doc,version,dirty=false,busy=false,currentTab='sections';
const kinds={text:'Texto editorial',image_text:'Imagen y texto',gallery:'Galería',cards:'Tarjetas',faq:'Preguntas frecuentes',cta:'Llamada a la acción'};
const builtin={hero:'Portada',values:'Valores',catalog:'Colección',essence:'Nuestra esencia',ritual:'El ritual',closing:'Cierre',contact:'Contacto'};
function state(){document.querySelector('#studio-save').disabled=!doc||busy||K.pending>0;document.querySelector('#studio-publish').disabled=!doc||busy||K.pending>0;document.querySelector('#studio-status').textContent=busy?'Guardando…':K.pending?'Subiendo imágenes…':dirty?'Cambios sin guardar':'Borrador guardado';}
K.changed=()=>{dirty=true;state();};
window.addEventListener('beforeunload',e=>{if(dirty||K.pending){e.preventDefault();e.returnValue='';}});
async function api(path='',method='GET',body){return Valerion.request('/api/admin/estudio'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':K.csrf},...(body?{body:JSON.stringify(body)}:{})});}
function error(e){const x=document.querySelector('#studio-error');x.textContent=e.message;x.hidden=false;x.scrollIntoView({block:'center'});if(e.status===401){dirty=false;location.assign('/admin/login');}}
function fields(parent,obj,specs){const grid=K.el('div','editor-grid');specs.forEach(([key,label,type,options])=>grid.append(K.field(obj,key,label,type,options)));parent.append(grid);}
function renderSections(){
 const tip=K.el('p','notice','Ordena con las flechas, oculta secciones o crea nuevas. Los textos e imágenes de las secciones principales se editan en «Textos e imágenes base». Puedes tener hasta 40 secciones.');host.append(tip);
 const add=K.el('div','add-section-bar');const select=K.el('select');select.setAttribute('aria-label','Tipo de nueva sección');for(const [id,title] of Object.entries(kinds)){const o=K.el('option','',title);o.value=id;select.append(o);}
 add.append(select,K.button('+ Agregar sección',()=>{if(doc.sections.length>=40)return Valerion.toast('Máximo 40 secciones.');doc.sections.push({id:'seccion-'+crypto.randomUUID().slice(0,8),kind:select.value,enabled:true,nav:'',eyebrow:'',title:'Nueva sección',text:'',image:'',alt:'',button_text:'',button_url:'',tone:'light',align:'left',image_side:'right',spacing:'normal',items:[]});K.changed();render();},'button'));host.append(add);
 doc.sections.forEach((s,i)=>{
  const panel=K.el('details','section-editor');const summary=K.el('summary');summary.append(K.el('span','section-index',String(i+1).padStart(2,'0')),K.el('strong','',s.title||builtin[s.kind]||kinds[s.kind]),K.el('span','status-badge '+(s.enabled?'active':''),s.enabled?'Visible':'Oculta'));panel.append(summary);
  const body=K.el('div','section-editor-body');const actions=K.el('div','editor-actions');
  for(const [label,delta] of [['↑ Subir',-1],['↓ Bajar',1]]){const b=K.button(label,()=>{const j=i+delta;[doc.sections[i],doc.sections[j]]=[doc.sections[j],doc.sections[i]];K.changed();render();});b.disabled=i+delta<0||i+delta>=doc.sections.length;actions.append(b);}
  if(!builtin[s.kind])actions.append(K.button('Duplicar',()=>{if(doc.sections.length>=40)return;doc.sections.splice(i+1,0,{...structuredClone(s),id:'seccion-'+crypto.randomUUID().slice(0,8),title:s.title+' · copia'});K.changed();render();}),K.button('Eliminar',()=>{if(!confirm('¿Eliminar esta sección del borrador?'))return;doc.sections.splice(i,1);K.changed();render();}));
  body.append(actions);fields(body,s,[['enabled','Mostrar sección','checkbox'],['nav','Etiqueta en el menú (vacía para ocultar enlace)']]);
  if(builtin[s.kind]){body.append(K.el('p','muted','Sección principal: '+builtin[s.kind]));const link=K.el('a','text-link',s.kind==='contact'?'Editar contacto →':'Editar textos e imágenes →');link.href=s.kind==='contact'?'#':'/admin/contenido';if(s.kind==='contact')link.onclick=e=>{e.preventDefault();setTab('contact');};body.append(link);}
  else {
   fields(body,s,[['eyebrow','Antetítulo'],['title','Título'],['text','Contenido','textarea'],['tone','Fondo','text',{light:'Claro',dark:'Verde de marca',sand:'Arena'}],['align','Alineación','text',{left:'Izquierda',center:'Centro'}],['spacing','Espaciado','text',{compact:'Compacto',normal:'Normal',large:'Amplio'}],['button_text','Texto del botón (opcional)'],['button_url','Enlace del botón (HTTPS o #seccion)']]);
   if(s.kind==='image_text'){body.append(K.image(s,'image'));fields(body,s,[['alt','Descripción accesible de imagen'],['image_side','Posición de imagen','text',{left:'Izquierda',right:'Derecha'}]]);}
   if(['gallery','cards','faq'].includes(s.kind)){
    const items=K.el('div');body.append(items);K.list(items,s.items,(card,item)=>{fields(card,item,[['title',s.kind==='faq'?'Pregunta':'Título'],['text',s.kind==='faq'?'Respuesta':'Texto o pie','textarea']]);if(s.kind!=='faq'){card.append(K.image(item,'image'));fields(card,item,[['alt','Descripción accesible']]);}},()=>({title:'',text:'',image:'',alt:''}),'Agregar elemento');
   }
  }
  panel.append(body);host.append(panel);
 });
}
function renderTheme(){
 const box=K.el('section','editor-panel');box.append(K.el('h2','','Apariencia de marca'),K.el('p','muted','Personaliza colores, tipografía y catálogo. Revisa el contraste en la vista previa antes de publicar.'));
 fields(box,doc.theme,[['primary','Color principal','color'],['accent','Color de acento','color'],['background','Fondo del sitio','color'],['text','Color del texto','color'],['font','Tipografía','text',{editorial:'Editorial · Georgia',modern:'Contemporánea · Arial',classic:'Clásica · Palatino'}],['corners','Bordes','text',{square:'Rectos',soft:'Suaves',round:'Redondeados'}],['width','Ancho de página','text',{standard:'Estándar',wide:'Amplio'}],['columns','Columnas del catálogo','number',{'2':'2 columnas','3':'3 columnas','4':'4 columnas'}]]);
 const palette=K.el('div','editor-actions');
 for(const [label,colors] of Object.entries({'Agave':{primary:'#173f33',accent:'#9b6c43',background:'#f7f5ee',text:'#193c31'},'Noche':{primary:'#23282c',accent:'#a77a4a',background:'#f2efe8',text:'#20252a'},'Tierra':{primary:'#663f31',accent:'#9a653b',background:'#faf3e9',text:'#3b302b'}})){palette.append(K.button(label,()=>{Object.assign(doc.theme,colors);K.changed();render();}));}
 box.append(palette);host.append(box);
}
function renderContact(){
 const c=doc.contact,box=K.el('section','editor-panel');box.append(K.el('h2','','Abre la conversación.'),K.el('p','muted','Solo se muestran los datos que completes. Los enlaces de WhatsApp abren un chat; el visitante decide enviar el mensaje.'));
 fields(box,c,[['title','Título de contacto'],['intro','Introducción','textarea'],['whatsapp','WhatsApp · código de país + número'],['whatsapp_message','Mensaje preparado de WhatsApp','textarea'],['floating_whatsapp','Botón flotante de WhatsApp','checkbox'],['address','Dirección (opcional)','textarea'],['hours','Horario (opcional)','textarea']]);
 box.append(K.el('p','field-help','Para México: 52 seguido del número de 10 dígitos. Ejemplo de formato: 526671234567. Usa tu número real.'));
 for(const [key,title] of [['emails','Correos electrónicos'],['phones','Teléfonos']]){box.append(K.el('h3','',title));const list=K.el('div');box.append(list);K.list(list,c[key],(card,item)=>fields(card,item,[['label','Etiqueta (Ventas, Atención…)'],['value',key==='emails'?'Correo':'Teléfono']]),()=>({label:'',value:''}),'Agregar contacto',8);}
 box.append(K.el('h3','','Redes sociales'));const socials=K.el('div');box.append(socials);K.list(socials,c.socials,(card,item)=>fields(card,item,[['platform','Red social (Instagram, Facebook, X, TikTok…)'],['url','Enlace completo HTTPS']]),()=>({platform:'Instagram',url:''}),'Agregar red social',15);
 host.append(box);
}
async function renderHistory(){
 host.append(K.el('p','notice','Recupera una de las últimas 10 versiones del diseño, contacto y redes. Se carga como borrador; revisa antes de publicar. Los productos y textos base se conservan por separado en el respaldo completo.'));
 try{const rows=await api('/historial');if(currentTab!=='history')return;rows.forEach(r=>{const item=K.el('div','history-row');item.append(K.el('span','',new Date(r.published_at).toLocaleString('es-MX')),K.button('Recuperar como borrador',async()=>{if(dirty&&!confirm('¿Descartar los cambios sin guardar del editor?'))return;busy=true;state();try{const result=await api('/restaurar/'+r.id,'POST',{version});doc=result.document;version=result.version;dirty=false;setTab('sections');Valerion.toast('Versión recuperada en el borrador.');}catch(e){error(e);}finally{busy=false;state();}}));host.append(item);});if(!rows.length)host.append(K.el('p','muted','El historial aparecerá después de la primera publicación.'));}catch(e){error(e);}
}
function render(){host.replaceChildren();if(!doc)return;({sections:renderSections,theme:renderTheme,contact:renderContact,history:renderHistory})[currentTab]();}
function setTab(tab){if(busy||K.pending)return;currentTab=tab;document.querySelectorAll('[data-tab]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.tab===tab)));render();}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>setTab(b.dataset.tab));
async function save(publish=false){if(busy||K.pending)return;busy=true;state();host.inert=true;document.querySelector('#studio-error').hidden=true;try{
 const result=await api('','PUT',{version,document:doc});doc=result.document;version=result.version;dirty=false;
 if(publish){const live=await api('/publicar','POST',{version});version=live.version;Valerion.toast('Diseño y contacto publicados.');}else Valerion.toast('Borrador guardado. Puedes abrir la vista previa.');
}catch(e){error(e);}finally{busy=false;host.inert=false;state();}}
document.querySelector('#studio-save').onclick=()=>save();document.querySelector('#studio-publish').onclick=()=>save(true);
document.querySelector('#studio-logout').onclick=async()=>{if(dirty&&!confirm('¿Salir sin guardar?'))return;try{await Valerion.request('/api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':K.csrf},body:'{}'});dirty=false;location.assign('/admin/login');}catch(e){error(e);}};
(async()=>{try{K.csrf=(await Valerion.request('/api/auth/me')).csrf_token;const data=await api();doc=data.document;version=data.version;render();state();}catch(e){error(e);}})();
