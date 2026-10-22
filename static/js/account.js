'use strict';
(()=>{
 let csrf='';const $=s=>document.querySelector(s),message=$('#account-message');
 async function send(url,method,body){return Valerion.request(url,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)});}
 function formAction(form,action){form.addEventListener('submit',async e=>{e.preventDefault();const b=form.querySelector('button');b.disabled=true;message.textContent='Guardando…';try{await action(Object.fromEntries(new FormData(form)));}catch(e){message.textContent=e.message;if(e.status===401)location.assign('/cuenta/login');}finally{b.disabled=false;}});}
 const access=$('#access-form');if(access){formAction(access,async data=>{await send('/api/clientes/'+(access.dataset.register==='true'?'registro':'login'),'POST',data);location.assign('/cuenta');});return;}
 const profile=$('#profile-form');if(!profile)return;
 formAction(profile,async data=>{await send('/api/clientes/perfil','PUT',{nombre:data.nombre,preferencia_contacto:profile.elements.preferencia_contacto.checked});message.textContent='Perfil actualizado.';});
 formAction($('#password-form'),async data=>{await send('/api/clientes/password','POST',data);location.assign('/cuenta/login');});
 $('#account-logout').onclick=async()=>{try{await send('/api/clientes/logout','POST',{});location.assign('/cuenta/login');}catch(e){message.textContent=e.message;}};
 (async()=>{try{const data=await Valerion.request('/api/clientes/me');csrf=data.csrf;profile.elements.nombre.value=data.nombre;profile.elements.email.value=data.email;profile.elements.preferencia_contacto.checked=data.preferencia_contacto;document.querySelectorAll('form button').forEach(b=>b.disabled=false);message.textContent='Tus datos están disponibles.';}catch(e){message.textContent=e.message;if(e.status===401)location.assign('/cuenta/login');}})();
})();
