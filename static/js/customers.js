'use strict';
(()=>{let csrf='',page=1,seq=0,timer;const $=s=>document.querySelector(s),status=$('#customer-status');const node=(tag,text)=>{const e=document.createElement(tag);e.textContent=text;return e;};
function crmPanel(customer) {
  const panel=node('details','');
  panel.append(node('summary','Historial e interacciones'));
  const feed=node('div',''),more=node('button','Ver más'),channel=node('select',''),kind=node('select','');
  for(const name of ['email','telefono','whatsapp','tienda','web']) channel.add(new Option(name,name));
  channel.value=customer.canal_preferido;
  for(const name of ['consulta','seguimiento','soporte','contacto']) kind.add(new Option(name,name));
  const channelLabel=node('label','Canal de la interacción');channelLabel.append(channel);
  const kindLabel=node('label','Tipo de interacción');kindLabel.append(kind);
  const add=node('button','Registrar interacción');add.className=more.className='button secondary';
  let page=1;
  async function fetchPage(reset) {
    more.disabled=true;
    try {
      const data=await Valerion.request('/api/admin/clientes/'+customer.id+'/historial?page='+page);
      if(reset) feed.replaceChildren();
      for(const o of data.compras.items) feed.append(node('p','#'+o.id+' · '+(o.producto||'Producto histórico')+' · '+Valerion.money(o.monto_centavos/100)+' · '+o.estado));
      for(const e of data.eventos) feed.append(node('p',new Date(e.fecha).toLocaleString('es-MX')+' · '+e.tipo+' · '+e.canal+' · '+e.actor+(e.detalle.nuevo?' · '+e.detalle.anterior+' → '+e.detalle.nuevo:'')));
      more.hidden=!data.mas_eventos&&!data.compras.siguiente;
    } catch(error) {status.textContent=error.message;} finally {more.disabled=false;}
  }
  more.onclick=()=>{page++;fetchPage(false);};
  add.onclick=async()=>{add.disabled=true;try{
    await Valerion.request('/api/admin/clientes/'+customer.id+'/interacciones',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({tipo:kind.value,canal:channel.value})});
    page=1;await fetchPage(true);
  }catch(error){status.textContent=error.message;}finally{add.disabled=false;}};
  panel.append(feed,more,channelLabel,kindLabel,add);
  panel.ontoggle=()=>{if(panel.open){page=1;fetchPage(true);}};
  return panel;
}
async function load(){const current=++seq;try{const data=await Valerion.request('/api/admin/clientes?'+new URLSearchParams({q:$('#customer-search').value,page}));if(seq!==current)return;$('#customer-list').replaceChildren(...data.items.map(c=>{const card=node('article','');card.className='account-card';card.append(node('h2',c.nombre),node('p',c.email),node('p',c.activo?'Cuenta activa':'Cuenta suspendida'),node('p',c.preferencia_contacto?'Acepta contacto informativo':'Sin preferencia de contacto'));const b=node('button',c.activo?'Suspender acceso':'Reactivar acceso');b.className='button secondary';b.onclick=async()=>{if(!confirm('¿'+(c.activo?'Suspender':'Reactivar')+' el acceso de esta cuenta? Sus sesiones se cerrarán.'))return;b.disabled=true;try{await Valerion.request('/api/admin/clientes/'+c.id,{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({activo:!c.activo})});await load();}catch(e){status.textContent=e.message;b.disabled=false;}};card.append(b,crmPanel(c));return card;}));status.textContent=`${data.total} cuentas · Página ${data.page} de ${data.pages}`;$('#customer-prev').disabled=page<=1;$('#customer-next').disabled=page>=data.pages;}catch(e){status.textContent=e.message;if(e.status===401)location.assign('/admin/login');}}
$('#customer-search').oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>{page=1;load();},250);};$('#customer-prev').onclick=()=>{page--;load();};$('#customer-next').onclick=()=>{page++;load();};(async()=>{try{csrf=(await Valerion.request('/api/auth/me')).csrf_token;await load();}catch(e){status.textContent=e.message;if(e.status===401)location.assign('/admin/login');}})();})();
