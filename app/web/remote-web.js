/* Static web client: no cross-site cookies and no credentials in the ZIP. */
(function () {
  'use strict';
  const configured = document.querySelector('meta[name="static-api"]')?.content;
  if (!configured) return;
  const url = new URL(configured);
  if (url.protocol !== 'https:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Для веб-клиента нужен HTTPS-адрес сервера.');
  }
  const origin = url.origin;
  const key = 'supplymatch-web-session:' + origin;
  window.SupplyMatchWeb = Object.freeze({
    origin,
    getSession() { return {token: sessionStorage.getItem(key) || '', origin}; },
    setSession(value) { if(value.origin !== origin) throw new Error('Неверный адрес сессии.'); sessionStorage.setItem(key, value.token); },
    clearSession() { sessionStorage.removeItem(key); }
  });
})();
