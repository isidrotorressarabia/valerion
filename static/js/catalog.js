'use strict';
(()=>{
const grid=document.querySelector('#product-grid');if(!grid)return;
const search=document.querySelector('#catalog-search'),sort=document.querySelector('#catalog-sort'),category=document.querySelector('#catalog-category');
let seq=0,timer,page=1;
const previous=document.querySelector('#catalog-prev'),next=document.querySelector('#catalog-next');
const el=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls||'';if(text!==undefined)n.textContent=text;return n;};
function render(data){
 const products=data.items;grid.replaceChildren();previous.disabled=page<=1;next.disabled=page>=data.pages;document.querySelector('#catalog-page').textContent=`Página ${page} de ${data.pages} · ${data.total} productos`;
 for(const p of products){const card=el('article','product-card'),picture=el('a','product-picture photo-only');picture.href='/producto/'+p.id;
 const img=el('img','ambient-image');img.src=Valerion.imageSize(p.imagen_url,640);img.srcset=Valerion.imageSize(p.imagen_url,640)+' 640w, '+Valerion.imageSize(p.imagen_url,1280)+' 1280w';img.sizes='(max-width:640px) 100vw, (max-width:1100px) 50vw, 33vw';img.alt=p.nombre;img.width=600;img.height=640;img.loading='lazy';img.setAttribute('data-fallback','');picture.append(img);if(p.featured)picture.append(el('span','product-badge','Selección Valerion'));
 const info=el('div','product-info'),row=el('div','product-title-row');const title=el('h3'),link=el('a','',p.nombre);link.href=picture.href;title.append(link);const price=el('p','price',Valerion.money(p.precio));price.append(el('small','',' MXN'));row.append(title,price);const detail=el('a','product-discover',grid.dataset.discover+' ↗');detail.href=picture.href;
 info.append(el('p','eyebrow',p.category||grid.dataset.label),row,el('p','product-description',p.descripcion),detail);card.append(picture,info);grid.append(card);}
 document.querySelector('#catalog-empty').hidden=products.length>0;document.querySelector('#catalog-status').textContent=data.total+' expresiones encontradas.';
}
async function load(){const id=++seq;grid.setAttribute('aria-busy','true');document.querySelector('#catalog-error').hidden=true;try{const products=await Valerion.request('/api/mezcales?'+new URLSearchParams({q:search.value,sort:sort.value,category:category.value,page,page_size:12}));if(id===seq)render(products);}catch(e){if(id===seq)document.querySelector('#catalog-error').hidden=false;}finally{if(id===seq)grid.setAttribute('aria-busy','false');}}
const reset=()=>{page=1;load();};previous.onclick=()=>{page--;load();};next.onclick=()=>{page++;load();};search.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(reset,220);});sort.onchange=reset;category.onchange=reset;document.querySelector('#retry-catalog').onclick=load;document.querySelector('#reset-search').onclick=()=>{search.value='';category.value='';reset();};
})();
