'use strict';
(() => {
  const $ = selector => document.querySelector(selector);
  const call = (url, method, body, csrf) => Valerion.request(url, {
    method,
    headers: {'Content-Type': 'application/json', ...(csrf ? {'X-CSRF-Token': csrf} : {})},
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const tokenFromHash = () => {
    const match = /(?:^|[#&])t=([\w-]+)/.exec(location.hash);
    history.replaceState(null, '', location.pathname);
    return match ? match[1] : '';
  };

  // Aviso de verificación en Mi cuenta.
  const box = $('#verify-box');
  if (box) {
    (async () => {
      try {
        const me = await Valerion.request('/api/clientes/me');
        if (!me.verificacion_requerida || me.email_verificado) return;
        box.hidden = false;
        $('#verify-resend').onclick = async () => {
          const out = $('#verify-message');
          try { out.textContent = (await call('/api/clientes/verificacion/reenviar', 'POST', {}, me.csrf)).message; }
          catch (error) { out.textContent = error.message; }
        };
      } catch (error) { /* sin sesión: account.js redirige */ }
    })();
  }

  // Páginas de recuperación, restablecimiento, verificación y privacidad.
  const root = $('[data-extra]');
  if (!root) return;
  const mode = root.dataset.extra, message = $('#extra-message'), form = $('#extra-form');
  const say = text => { message.textContent = text; };
  const submit = handler => form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true;
    say('Procesando…');
    try { await handler(Object.fromEntries(new FormData(form))); }
    catch (error) {
      say(error.message);
      if (error.status === 401) location.assign('/cuenta/login');
      button.disabled = false;
    }
  });

  if (mode === 'recuperar') {
    submit(async data => { say((await call('/api/clientes/recuperar', 'POST', {email: data.email})).message); });
  } else if (mode === 'restablecer') {
    const token = tokenFromHash();
    if (!token) say('El enlace no es válido. Solicita uno nuevo desde «Recuperar contraseña».');
    submit(async data => {
      say((await call('/api/clientes/restablecer', 'POST', {token, nueva: data.nueva})).message);
      setTimeout(() => location.assign('/cuenta/login'), 1500);
    });
  } else if (mode === 'verificar') {
    const token = tokenFromHash();
    (async () => {
      if (!token) { say('El enlace no es válido.'); return; }
      try { say((await call('/api/clientes/verificar', 'POST', {token})).message); }
      catch (error) { say(error.message); }
    })();
  } else if (mode === 'privacidad') {
    let csrf = '';
    Valerion.request('/api/clientes/me').then(me => { csrf = me.csrf; form.querySelector('button').disabled = false; })
      .catch(() => location.assign('/cuenta/login'));
    submit(async data => {
      if (!confirm('Esta acción es permanente. ¿Eliminar tu cuenta?')) { form.querySelector('button').disabled = false; say(''); return; }
      say((await call('/api/clientes/me', 'DELETE', {password: data.password}, csrf)).message);
      setTimeout(() => location.assign('/'), 2000);
    });
  }
})();
