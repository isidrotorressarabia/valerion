'use strict';
const loginForm = document.querySelector('#login-form');
document.querySelector('#show-password').addEventListener('click', event => {
  const field = document.querySelector('#password');
  const visible = field.type === 'password';
  field.type = visible ? 'text' : 'password';
  event.currentTarget.textContent = visible ? 'Ocultar' : 'Ver';
  event.currentTarget.setAttribute('aria-pressed', String(visible));
  event.currentTarget.setAttribute('aria-label', visible ? 'Ocultar contraseña' : 'Mostrar contraseña');
});
loginForm.addEventListener('submit', async event => {
  event.preventDefault();
  const submit = document.querySelector('#login-submit');
  const error = document.querySelector('#login-error');
  error.hidden = true; submit.disabled = true; submit.textContent = 'Ingresando…';
  try {
    await Valerion.request('/api/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(Object.fromEntries(new FormData(loginForm)))});
    window.location.assign('/admin');
  } catch (e) {
    error.textContent = e.message; error.hidden = false;
    submit.disabled = false; submit.textContent = 'Entrar al panel →';
  }
});
