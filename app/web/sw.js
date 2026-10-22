'use strict';
const CACHE='supplymatch-shell-v5';
const SHELL=['/','/app.js','/procurement.js','/cart.js','/account.js','/theme-utils.js','/ambient.js','/styles.css','/icon.svg','/icon-192.png','/icon-512.png','/manifest.webmanifest'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL))));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key))))));
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  if(event.request.method!=='GET' || url.origin!==self.location.origin || url.pathname.startsWith('/api/')) return;
  // Private data and API credentials never enter the offline cache.
  event.respondWith(fetch(event.request).then(response=>{
    if(response.ok && SHELL.includes(url.pathname)) { const copy=response.clone(); caches.open(CACHE).then(cache=>cache.put(event.request,copy)); }
    return response;
  }).catch(()=>caches.match(event.request).then(cached=>cached || (event.request.mode==='navigate'?caches.match('/'):Response.error()))));
});
