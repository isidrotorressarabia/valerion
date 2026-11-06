'use strict';
window.EditorKit = {
  pending:0, csrf:'', changed:()=>{},
  el(tag,cls='',text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;},
  button(text,fn,cls='table-button'){const b=this.el('button',cls,text);b.type='button';b.addEventListener('click',fn);return b;},
  field(obj,key,label,type='text',options=null){
    const box=this.el('label','editor-field');box.append(this.el('span','field-title',label));
    const input=this.el(type==='textarea'?'textarea':options?'select':'input');
    if(options){for(const [value,title] of Object.entries(options)){const opt=this.el('option','',title);opt.value=value;input.append(opt);}}
    else if(type!=='textarea')input.type=type;
    if(type==='checkbox'){input.checked=!!obj[key];box.classList.add('checkbox-field');}else input.value=obj[key]??'';
    input.addEventListener('input',()=>{obj[key]=type==='checkbox'?input.checked:type==='number'?Number(input.value):input.value;this.changed();});
    box.append(input);return box;
  },
  async upload(file){this.pending++;this.changed();try{return await Valerion.upload(file,this.csrf);}finally{this.pending--;this.changed();}},
  image(obj,key,label='Imagen'){
    const wrap=this.el('div','editor-image');wrap.append(this.field(obj,key,label+' · URL o archivo'));
    const preview=this.el('img');preview.src=obj[key]||'/static/img/placeholder.svg';preview.alt='Vista previa';preview.setAttribute('data-fallback','');preview.width=200;preview.height=140;
    const fileLabel=this.el('label','file-label','Subir desde mi equipo');const input=this.el('input');input.type='file';input.accept='image/jpeg,image/png,image/webp';fileLabel.append(input);
    const state=this.el('p','small muted','JPG, PNG o WebP · máximo 8 MB por imagen.');state.setAttribute('role','status');
    wrap.querySelector('input').addEventListener('change',()=>{preview.src=obj[key]||'/static/img/placeholder.svg';});
    input.addEventListener('change',async()=>{if(!input.files[0])return;input.disabled=true;state.textContent='Subiendo…';try{obj[key]=(await this.upload(input.files[0])).url;wrap.querySelector('input').value=obj[key];preview.src=obj[key];state.textContent='Imagen cargada. Guarda los cambios.';this.changed();}catch(e){state.textContent=e.message;}finally{input.disabled=false;input.value='';}});
    wrap.append(preview,fileLabel,state);return wrap;
  },
  list(host,items,renderItem,createItem,label='Agregar',limit=24){
    const paint=()=>{host.replaceChildren();items.forEach((item,index)=>{
      const card=this.el('div','editor-item');const bar=this.el('div','item-bar');bar.append(this.el('strong','',String(index+1).padStart(2,'0')));
      const move=(delta)=>{if(this.pending)return;const next=index+delta;if(next<0||next>=items.length)return;[items[index],items[next]]=[items[next],items[index]];this.changed();paint();};
      const up=this.button('↑',()=>move(-1));up.disabled=index===0;up.setAttribute('aria-label','Subir elemento');
      const down=this.button('↓',()=>move(1));down.disabled=index===items.length-1;down.setAttribute('aria-label','Bajar elemento');
      bar.append(up,down,this.button('Eliminar',()=>{if(this.pending)return;if(!confirm('¿Quitar este elemento?'))return;items.splice(index,1);this.changed();paint();}));
      card.append(bar);renderItem(card,item,index);host.append(card);
    });
    if(items.length<limit)host.append(this.button('+ '+label,()=>{items.push(createItem());this.changed();paint();},'button secondary'));
    };
    paint();return paint;
  }
};
