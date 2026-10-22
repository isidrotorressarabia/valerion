"""Validated page builder: no executable HTML, CSS or JavaScript from editors."""
import re
from typing import Literal
from urllib.parse import quote
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, field_validator, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

def https_url(value):
    if not value:
        return ''
    url = TypeAdapter(HttpUrl).validate_python(value)
    if url.scheme != 'https' or url.username or url.password:
        raise ValueError('Utiliza un enlace HTTPS sin credenciales.')
    return str(url)

def action_url(value):
    if not value:
        return ''
    if re.fullmatch(r'#[a-zA-Z][a-zA-Z0-9_-]{0,80}', value):
        return value
    return https_url(value)

def image_url(value):
    if not value:
        return ''
    if re.fullmatch(r'/media/[a-f0-9]{32}\.webp', value):
        return value
    if value in ['/static/img/' + x for x in ['hero.svg','emblem.svg','placeholder.svg','bottle.svg','agave-reference.jpg']]:
        return value
    return https_url(value)

class ImageItem(StrictModel):
    url: str = Field(max_length=2083)
    alt: str = Field(default='', max_length=200)
    caption: str = Field(default='', max_length=500)
    _url = field_validator('url')(image_url)
    @model_validator(mode='after')
    def nonempty(self):
        if not self.url:
            raise ValueError('Selecciona una imagen.')
        return self

class StoryBlock(StrictModel):
    kind: Literal['text','image'] = 'text'
    title: str = Field(default='', max_length=150)
    text: str = Field(default='', max_length=6000)
    image: str = Field(default='', max_length=2083)
    alt: str = Field(default='', max_length=200)
    caption: str = Field(default='', max_length=500)
    _image = field_validator('image')(image_url)
    @model_validator(mode='after')
    def complete(self):
        if self.kind == 'image' and not self.image:
            raise ValueError('El bloque de imagen necesita una fotografía.')
        return self

class Card(StrictModel):
    title: str = Field(default='', max_length=150)
    text: str = Field(default='', max_length=4000)
    image: str = Field(default='', max_length=2083)
    alt: str = Field(default='', max_length=200)
    _image = field_validator('image')(image_url)

class Section(StrictModel):
    id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,60}$')
    kind: Literal['hero','values','catalog','essence','ritual','closing','contact','text','image_text','gallery','cards','faq','cta']
    enabled: bool = True
    nav: str = Field(default='', max_length=35)
    eyebrow: str = Field(default='', max_length=100)
    title: str = Field(default='', max_length=180)
    text: str = Field(default='', max_length=10000)
    image: str = Field(default='', max_length=2083)
    alt: str = Field(default='', max_length=200)
    button_text: str = Field(default='', max_length=60)
    button_url: str = Field(default='', max_length=2083)
    tone: Literal['light','dark','sand'] = 'light'
    align: Literal['left','center'] = 'left'
    image_side: Literal['left','right'] = 'right'
    spacing: Literal['compact','normal','large'] = 'normal'
    items: list[Card] = Field(default_factory=list, max_length=24)
    _image = field_validator('image')(image_url)
    _button = field_validator('button_url')(action_url)

class Theme(StrictModel):
    primary: str = '#173f33'
    accent: str = '#9b6c43'
    background: str = '#f7f5ee'
    text: str = '#193c31'
    font: Literal['editorial','modern','classic'] = 'editorial'
    corners: Literal['square','soft','round'] = 'soft'
    width: Literal['standard','wide'] = 'standard'
    columns: Literal[2,3,4] = 3
    @field_validator('primary','accent','background','text')
    @classmethod
    def hex_color(cls, v):
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', v):
            raise ValueError('Color hexadecimal inválido.')
        return v

class ContactItem(StrictModel):
    label: str = Field(default='', max_length=60)
    value: str = Field(min_length=3, max_length=254)

class Social(StrictModel):
    platform: str = Field(min_length=2, max_length=40)
    url: str = Field(min_length=8, max_length=2083)
    _url = field_validator('url')(https_url)

class Contact(StrictModel):
    title: str = Field(default='Conversemos.', max_length=150)
    intro: str = Field(default='Estamos cerca de tu próximo encuentro. Escríbenos para conocer nuestra colección.', max_length=2000)
    emails: list[ContactItem] = Field(default_factory=list, max_length=8)
    phones: list[ContactItem] = Field(default_factory=list, max_length=8)
    whatsapp: str = Field(default='', max_length=25)
    whatsapp_message: str = Field(default='Hola, me gustaría conocer más sobre Valerion.', max_length=1000)
    floating_whatsapp: bool = True
    address: str = Field(default='', max_length=500)
    hours: str = Field(default='', max_length=300)
    socials: list[Social] = Field(default_factory=list, max_length=15)
    @field_validator('whatsapp')
    @classmethod
    def whatsapp_number(cls, v):
        v = re.sub(r'[+ ()-]', '', v)
        if v and not re.fullmatch(r'[1-9][0-9]{7,14}', v):
            raise ValueError('WhatsApp: incluye código de país y entre 8 y 15 dígitos.')
        return v
    @model_validator(mode='after')
    def destinations(self):
        for e in self.emails:
            if not re.fullmatch(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,63}", e.value):
                raise ValueError('Correo de contacto inválido.')
        for p in self.phones:
            if not re.fullmatch(r'\+?[0-9 ()-]{7,25}', p.value):
                raise ValueError('Teléfono de contacto inválido.')
        return self

BUILTINS = {'hero':'inicio','values':'valores','catalog':'coleccion','essence':'esencia','ritual':'ritual','closing':'encuentro','contact':'contacto'}
def initial_sections():
    labels={'hero':'','values':'','catalog':'Colección','essence':'Nuestra esencia','ritual':'El ritual','closing':'','contact':'Contacto'}
    return [Section(id=i,kind=k,nav=labels[k]) for k,i in BUILTINS.items()]

class Studio(StrictModel):
    theme: Theme = Field(default_factory=Theme)
    contact: Contact = Field(default_factory=Contact)
    sections: list[Section] = Field(default_factory=initial_sections, max_length=40)
    @field_validator('sections')
    @classmethod
    def unique(cls, sections):
        ids=[s.id for s in sections]
        if len(ids)!=len(set(ids)):
            raise ValueError('Los identificadores de sección deben ser únicos.')
        built=[s.kind for s in sections if s.kind in BUILTINS]
        if len(built)!=len(set(built)):
            raise ValueError('Las secciones principales no se pueden repetir.')
        for s in sections:
            if s.kind in BUILTINS and s.id != BUILTINS[s.kind]:
                raise ValueError('No se puede cambiar el identificador de una sección principal.')
            if s.kind not in BUILTINS and s.id in BUILTINS.values():
                raise ValueError('Identificador reservado.')
        return sections

class StudioInput(StrictModel):
    version: int = Field(ge=1)
    document: Studio

class PublishInput(StrictModel):
    version: int = Field(ge=1)

class Spec(StrictModel):
    label: str = Field(min_length=1,max_length=80)
    value: str = Field(min_length=1,max_length=250)

class ProductExtra(StrictModel):
    published: bool = True
    featured: bool = False
    category: str = Field(default='', max_length=60)
    availability: Literal['available','request','sold_out'] = 'available'
    gallery: list[ImageItem] = Field(default_factory=list,max_length=24)
    blocks: list[StoryBlock] = Field(default_factory=list,max_length=40)
    specs: list[Spec] = Field(default_factory=list,max_length=20)

def whatsapp_link(contact, message=''):
    if not contact['whatsapp']:
        return ''
    return 'https://wa.me/'+contact['whatsapp']+'?text='+quote(message or contact['whatsapp_message'], safe='')

def theme_css(theme):
    t=Theme.model_validate(theme)
    fonts={'editorial':"Georgia,'Times New Roman',serif",'modern':"Arial,Helvetica,sans-serif",'classic':"'Palatino Linotype',Palatino,serif"}
    radius={'square':'0px','soft':'8px','round':'22px'}[t.corners]
    width='1440px' if t.width=='wide' else '1320px'
    return f':root{{--green:{t.primary};--copper:{t.accent};--cream:{t.background};--ink:{t.text};--serif:{fonts[t.font]};--radius:{radius};--site-width:{width};--columns:{t.columns};}}'
