'use strict';
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = value => new Intl.NumberFormat('ru-RU').format(value) + ' ₽';
const titles = {projects:'Проекты',monitoring:'Поиски и уведомления',cart:'Корзина',admin:'Администрирование','product-checkout':'Оформление покупки',purchases:'Мои заказы',calculator:'Стоимость покупки',appearance:'Внешний вид',history:'История ИИ',overview:'Обзор',find:'Найти продавцов',base:'Моя база',compare:'Сравнить предложения',pricing:'Тарифы и оплата',checkout:'Оформление тарифа',settings:'Настройки ИИ'};
const statusClass = {'новый':'','приглашён':'invited','ответил':'replied','отказ':'declined'};
let state = null, found = [], period = 'monthly', selectedPlan = 'pro', currentPage = '', letterId = null, deleteId = null;
let aiPollTimer=null, searchPage=0,lastSearch=null,searchHasMore=false;
let searchSources=[], searchMode='model', historyItems=[], purchaseToken='', purchaseFingerprint='';
let apiBase=localStorage.getItem('supplymatch-server') || document.querySelector('meta[name=api-base]')?.content || '';
let accessToken=sessionStorage.getItem('supplymatch-token') || '';
const defaultAppearance={theme:'system',accent:'green',density:'comfortable',start:'overview',workspace:'Моё пространство',effects:'aurora',motion:'subtle',glass:true,customBase:'dark',customAccent:'#a4e4b8',customBg:'#10151d',customSurface:'#19212d',customText:'#edf3fa'};
let appearance={...defaultAppearance};
try { appearance={...defaultAppearance,...JSON.parse(localStorage.getItem('supplymatch-appearance') || '{}')}; } catch {}
applyAppearance();
let checkoutToken = '', checkoutFingerprint = '', toastTimer;
function token() { return Array.from(crypto.getRandomValues(new Uint8Array(24)), n=>n.toString(16).padStart(2,'0')).join(''); }
async function api(path, method='GET', data) {
  const response = await fetch(apiBase+path, {method, credentials:'include', headers:{...(method==='GET'?{}:{'Content-Type':'application/json'}),...(window.SupplyAccount?.headers()||{})}, body:method==='GET'?undefined:JSON.stringify(data ?? {})});
  let result;
  try { result = await response.json(); } catch { throw new Error('Сервер вернул некорректный ответ. Проверьте, что приложение запущено.'); }
  if(response.status===401) window.SupplyAccount?.requireLogin();
  if (!response.ok) throw new Error(result.error || 'Не удалось выполнить запрос.');
  return result;
}
function status(selector, message='', error=false) { const el=$(selector); el.textContent=message; el.classList.toggle('error',error); }
function toast(message, error=false) { clearTimeout(toastTimer); $('#toast').textContent=message; $('#toast').classList.toggle('error',error); $('#toast').hidden=false; toastTimer=setTimeout(()=>$('#toast').hidden=true,4500); }
function empty(title, text, action='') { return `<div class="empty"><span class="empty-icon" aria-hidden="true">◇</span><h3>${esc(title)}</h3><p>${esc(text)}</p>${action}</div>`; }
function badge(s) { return `<span class="badge ${statusClass[s.status] || ''}">${esc(s.status || 'новый')}</span>`; }
function safeLink(url) { try { const u=new URL(url); return ['http:','https:'].includes(u.protocol)?u.href:''; } catch { return ''; } }
function siteMarkup(s) { const url=safeLink(s.site); return url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Сайт поставщика ↗</a>`:esc(s.site); }
function renderCard(s, index, isFound=false) {
  const saved = isFound && state.suppliers.some(x=>x.name===s.name);
  return `<article class="supplier-card"><div class="supplier-head"><span class="supplier-logo" aria-hidden="true">${esc(s.name?.[0] || 'П')}</span><div><h3>${esc(s.name)}</h3><small>${esc(s.type || 'Продавец')} · ${esc(s.country || (isFound&&s.city?'Доставка: '+s.city:s.city) || 'Регион не указан')}</small></div>${isFound?'<span class="badge invited">Проверить</span>':badge(s)}</div>
  <p>${esc(s.offer || 'Добавьте ассортимент и предложение поставщика.')}</p>${s.conditions?`<p class="conditions">${esc(s.conditions)}</p>`:''}${isFound&&s.why?`<p>${esc(s.why)}</p>`:''}${!isFound&&s.note?`<p class="note">${esc(s.note)}</p>`:''}
  <div class="supplier-facts">${[s.trade_format,s.category,s.unit_price,s.currency,s.moq?'Заказ от '+s.moq:'',s.lead_time,s.incoterms].filter(Boolean).map(f=>`<span class="badge">${esc(f)}</span>`).join('')}</div><div class="card-meta">${esc(s.contact)}${s.contact&&s.site?' · ':''}${siteMarkup(s)}</div>
  <div class="card-actions">${isFound?`<button class="button ${saved?'secondary':'primary'}" data-action="save-found" data-id="${index}" ${saved?'disabled':''}>${saved?'✓ В вашей базе':'＋ В мою базу'}</button>`:`<button class="button primary" data-action="purchase" data-id="${s.id}">Оформить покупку</button><button class="button secondary" data-action="catalog" data-id="${s.id}">Каталог моделей</button><button class="button secondary" data-action="cost" data-id="${s.id}">Рассчитать</button><button class="button secondary" data-action="edit" data-id="${s.id}">Редактировать</button><button class="button secondary" data-action="invite" data-id="${s.id}">✧ Написать письмо</button><button class="button secondary delete-button" data-action="delete" data-id="${s.id}" aria-label="Удалить ${esc(s.name)}">×</button>`}</div></article>`;
}
function renderOverview() {
  const suppliers=state.suppliers;
  $('#base-count').textContent=suppliers.length;
  $('#stat-total').textContent=suppliers.length;
  $('#stat-active').textContent=suppliers.filter(s=>s.status==='приглашён').length;
  $('#stat-replied').textContent=suppliers.filter(s=>s.status==='ответил').length;
  $('#stat-cities').textContent=new Set(suppliers.map(s=>s.city?.trim().toLowerCase()).filter(Boolean)).size;
  const steps=[state.ai_ready, suppliers.length>0, suppliers.some(s=>s.status==='ответил')];
  ['#step-key','#step-base','#step-reply'].forEach((selector,i)=>{ $(selector).classList.toggle('done',steps[i]); $(selector).textContent=steps[i]?'✓':i+1; });
  $('#progress').textContent=steps.filter(Boolean).length+' / 3';
  $('#recent').innerHTML=suppliers.length?`<div class="recent-row">${suppliers.slice(0,3).map(s=>`<a href="#base" class="recent-item"><span class="supplier-logo">${esc(s.name[0])}</span><div><strong>${esc(s.name)}</strong><small>${esc(s.type || 'Продавец')} · ${esc(s.country || s.city || 'Регион не указан')}</small></div>${badge(s)}<span aria-hidden="true">↗</span></a>`).join('')}</div>`:empty('Здесь будут ваши будущие партнёры','Начните с поиска или добавьте поставщика, с которым уже работаете.','<a class="button secondary" href="#find">Найти первого поставщика →</a>');
}
function renderBase() {
  const query=$('#base-query').value.trim().toLowerCase(), filter=$('#status-filter').value;
  const list=state.suppliers.filter(s=>(!filter || s.status===filter)&&(!query || [s.name,s.city,s.country,s.category,s.trade_format,s.type,s.offer,s.contact,s.note].join(' ').toLowerCase().includes(query)));
  $('#base-list').innerHTML=list.length?list.map((s,i)=>renderCard(s,i)).join(''):state.suppliers.length?empty('Ничего не найдено','Попробуйте другой запрос или сбросьте фильтр статусов.'):empty('Ваша база пока пуста','Добавьте знакомого поставщика вручную или найдите кандидатов с помощью ИИ.','<a class="button primary" href="#find">Начать поиск →</a>');
}
function renderFound() {
  if(searchMode==='model') found=found.map(s=>({...s,contact:'',conditions:'',currency:'',unit_price:'',moq:'',lead_time:'',incoterms:'',why:'Кандидат по знаниям модели. Актуальность и коммерческие условия нужно проверить.'}));
  function domain(s){try{return new URL(s.site).hostname;}catch{return '';}}
  for(const [id,values,label] of [['result-type',found.map(s=>s.type).filter(Boolean),'Все типы'],['result-domain',found.map(domain).filter(Boolean),'Все сайты']]){
    const select=$('#'+id),previous=select.value;
    select.innerHTML='<option value="">'+label+'</option>'+[...new Set(values)].sort((a,b)=>a.localeCompare(b,'ru')).map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');
    if([...select.options].some(o=>o.value===previous))select.value=previous;
  }
  const query=$('#result-query').value.trim().toLowerCase(),type=$('#result-type').value,site=$('#result-domain').value,savedFilter=$('#result-saved').value,sort=$('#result-sort').value;
  let list=found.map((s,i)=>({s,i})).filter(({s})=>{
    const saved=state?.suppliers.some(x=>x.name===s.name);
    return (!query||[s.name,s.offer,s.site,s.type,s.country].join(' ').toLowerCase().includes(query))&&(!type||s.type===type)&&(!site||domain(s)===site)&&(!savedFilter||(savedFilter==='saved'?saved:!saved));
  });
  if(sort==='name')list.sort((a,b)=>a.s.name.localeCompare(b.s.name,'ru'));
  if(sort==='site')list.sort((a,b)=>domain(a.s).localeCompare(domain(b.s)));
  $('#results-controls').hidden=!found.length;$('#results-count').textContent=list.length+' / '+found.length;
  $('#search-results').innerHTML=list.length?list.map(({s,i})=>renderCard(s,i,true)).join(''):found.length?empty('По фильтрам ничего не найдено','Измените строку поиска или выберите все типы и сайты.'):'';
  $('#search-more').hidden=!searchHasMore||searchMode!=='discovery'||searchPage>=5;
  $('#search-sources').hidden=!searchSources.length;
  $('#search-sources').innerHTML='<details><summary>Источники веб-поиска · '+searchSources.length+'</summary><p class="muted small-text">Результаты поиска не подтверждают наличие и надёжность продавца.</p>'+searchSources.map(source=>`<p><a class="text-link" href="${esc(safeLink(source.url))}" target="_blank" rel="noopener noreferrer">${esc(source.title || source.url)} ↗</a></p>`).join('')+'</details>';
}
function renderPlans() {
  $$('#page-pricing [data-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.period===period)));
  $('#plans').innerHTML=state.billing.plans.map(p=>{
    const current=state.access.plan===p.id;
    const button=p.id==='start'?`<a href="#overview" class="button secondary">${current?'Ваш текущий тариф ✓':'Бесплатный план'}</a>`:current?'<span class="button secondary">Ваш текущий тариф ✓</span>':`<a href="#checkout?plan=${p.id}&period=${period}" class="button ${p.id==='pro'?'primary':'secondary'}">${p.id==='team'?'Заявка на командную версию':'Оформить Профи'} <span>→</span></a>`;
    return `<article class="plan ${p.id==='pro'?'featured':''}">${p.id==='pro'?'<span class="recommended">ДЛЯ РЕГУЛЯРНЫХ ПОКУПОК</span>':''}<h2>${esc(p.name)}</h2><p>${esc(p.description)}</p><div class="price">${money(p[period])}<small> / ${period==='yearly'?'год':'мес.'}</small></div><div class="price-sub">${p.monthly?(period==='yearly'?money(p.yearly/12)+' в месяц при оплате за год':'Стоимость подписки'):'Встроенный ИИ или ваш API'}</div><ul class="plain-list">${p.features.map(f=>`<li>${esc(f)}</li>`).join('')}</ul>${button}</article>`;
  }).join('');
  renderOrders();
}
function renderOrders() {
  $('#orders').innerHTML=state.billing.orders.length?state.billing.orders.map(order=>{
    const plan=state.billing.plans.find(p=>p.id===order.plan);
    return `<div class="order-row"><div><strong>${esc(plan?.name || order.plan)}</strong> · ${order.period==='yearly'?'Год':'Месяц'}<small>${new Date(order.created_at).toLocaleDateString('ru-RU')} · № ${esc(order.id.slice(0,8))}</small></div><strong>${money(order.amount/100)}</strong><span class="badge ${order.status==='paid'?'replied':'invited'}">${order.status==='paid'?'Оплачен':'Ожидает оплаты'}</span></div>`;
  }).join(''):empty('Заявок пока нет','Здесь появится история оформления тарифов.');
}
function renderCheckout() {
  const plan=state.billing.plans.find(p=>p.id===selectedPlan&&p.monthly) || state.billing.plans.find(p=>p.id==='pro');
  selectedPlan=plan.id;
  const canPay=state.billing.payment_enabled && plan.id==='pro' && !window.SupplyMatchNative;
  $('#checkout-payment-notice').textContent=canPay?'Оплата будет выполнена на защищённой странице ЮKassa. Карточные данные не сохраняются в приложении.':'Сейчас сохраняется заявка без списания денег. Платёжный сервис ещё не подключён для этого тарифа.';
  $('#checkout-submit').textContent=canPay?'Создать платёж →':'Сохранить заявку →';
  $('#order-summary').innerHTML=`<span class="summary-label">ВАШ ВЫБОР</span><h2>${esc(plan.name)}</h2><p>${esc(plan.description)}</p><div class="summary-line"><span>Период</span><strong>${period==='yearly'?'12 месяцев':'1 месяц'}</strong></div><div class="summary-line"><span>Стоимость тарифа</span><strong>${money(plan[period])}</strong></div>${period==='yearly'?`<div class="summary-line"><span>Экономия за год</span><strong>${money(plan.monthly*12-plan.yearly)}</strong></div>`:''}<div class="summary-line summary-total"><span>Итого</span><strong>${money(plan[period])}</strong></div><p class="small-text">${period==='yearly'?'Разовая оплата за год':'Стоимость за месяц'}. Автосписания не подключены. Расходы по собственным API-ключам оплачиваются провайдеру отдельно.</p><ul class="plain-list">${plan.features.map(f=>`<li>${esc(f)}</li>`).join('')}</ul><div class="notice">${canPay?'К оплате: '+money(plan[period]):'Сейчас к оплате: 0 ₽. Сохраняется только заявка.'}<br>Тариф активируется после серверного подтверждения платежа.</div>`;
}
const providerDescriptions={
  auto:`<span class="feature-icon">✧</span><h2>ИИ уже в приложении</h2><p>Первым используется установленная локальная <strong>Qwen3-4B</strong>. При её недоступности — бесплатный OpenRouter, если владелец сервера настроил служебный ключ.</p><p>Автовыбор не переходит на платные модели. Для свежих предложений подключите веб-поиск Tavily.</p><p class="notice">Локальная модель работает на компьютере или сервере приложения. На телефоне используется подключение к этому серверу.</p>`,
  local:`<span class="feature-icon">▦</span><h2>Qwen3 без API-ключа</h2><p>Модель Qwen3-4B Q4_K_M работает через llama.cpp локально. Плата за токены отсутствует; скорость зависит от компьютера.</p><p>Один раз установите модель командой <code>python scripts/setup_local_ai.py</code>. При запуске приложения модель запускается автоматически.</p><a class="text-link" href="https://huggingface.co/Qwen/Qwen3-4B-GGUF" target="_blank" rel="noopener noreferrer">Официальная модель ↗</a>`,
  custom:`<span class="feature-icon">⚙</span><h2>Подключите свой API</h2><p>Поддерживаются форматы Chat Completions и Anthropic Messages. Укажите полный HTTPS-адрес конечной точки, модель и ключ.</p><p>Gemini использует отдельный готовый адаптер. Для других протоколов нужен дополнительный адаптер: один ключ не описывает устройство API.</p>`,
  anthropic:`<span class="feature-icon">✳</span><h2>Claude через Messages API</h2><p>Введите свой ключ и точный идентификатор доступной вам модели. Расходы оплачиваются вашему AI-провайдеру.</p><a class="text-link" href="https://platform.claude.com/docs/en/api/messages/create" target="_blank" rel="noopener noreferrer">Документация формата ↗</a>`,
  openrouter:`<span class="feature-icon">✧</span><h2>Начните бесплатно</h2><p>Модель <strong>openrouter/free</strong> выбирает доступную бесплатную модель. Плата за токены этой модели — 0 $. Доступность и лимиты зависят от провайдера и аккаунта.</p><p>Подходит для писем, первичного подбора и сравнения. Встроенный веб-поиск в этой интеграции не используется.</p><div class="provider-links"><a href="https://openrouter.ai/settings/keys" target="_blank" rel="noopener noreferrer">Получить API-ключ ↗</a><a href="https://openrouter.ai/openrouter/free" target="_blank" rel="noopener noreferrer">Бесплатная модель и условия ↗</a><a href="https://openrouter.ai/docs/api/reference/limits" target="_blank" rel="noopener noreferrer">Текущие ограничения ↗</a></div>`,
  groq:`<span class="feature-icon">↯</span><h2>Предсказуемая модель</h2><p><strong>openai/gpt-oss-20b</strong> — экономичный вариант для писем и сравнений. На 18.10.2026: 0,075 $ за миллион входных токенов и 0,30 $ за миллион выходных.</p><p>Бесплатные квоты и доступ к моделям проверяйте в своём аккаунте. Веб-поиск в этой интеграции отключён.</p><div class="provider-links"><a href="https://console.groq.com/keys" target="_blank" rel="noopener noreferrer">Создать API-ключ ↗</a><a href="https://console.groq.com/docs/models" target="_blank" rel="noopener noreferrer">Модели и актуальные цены ↗</a></div>`,
  gemini:`<span class="feature-icon">⌕</span><h2>Поиск в интернете</h2><p>Gemini поддерживает обращение к Google Search. Включайте веб-поиск на странице подбора. Даже в этом режиме проверяйте контакты и коммерческие условия самостоятельно.</p><p>Стоимость модели, веб-поиска и бесплатные квоты зависят от настроек Google-аккаунта.</p><div class="provider-links"><a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener noreferrer">Получить ключ Gemini ↗</a><a href="https://ai.google.dev/gemini-api/docs/pricing" target="_blank" rel="noopener noreferrer">Текущие тарифы ↗</a></div>`
};
function renderProvider(resetModel=true) {
  const provider=$('#provider').value;
  $('#provider-info').innerHTML=providerDescriptions[provider] || '<h2>Ваш AI-провайдер</h2><p>Укажите ключ и точное имя модели из документации провайдера.</p>';
  $('#custom-settings').hidden=provider!=='custom';
  $('#custom-endpoint').required=provider==='custom';
  $('#model').required=!['auto','local'].includes(provider);
  $('#model').disabled=provider==='auto';
  $('#api-key').disabled=['auto','local'].includes(provider);
  $('#clear-key').disabled=['auto','local'].includes(provider);
  if(provider==='custom') { $('#custom-endpoint').value=state.custom_endpoint || ''; $('#custom-format').value=state.custom_format || 'openai'; }
  $('#search-key-state').textContent=state.has_search_key?'Ключ поиска подключён. Пустое поле сохраняет его.':'Бесплатный веб-поиск работает без ключа. Tavily — дополнительный поисковый API.';
  if(resetModel) $('#model').value=state.providers[provider].configured_model || state.providers[provider].model;
  $('#key-state').textContent=['auto','local'].includes(provider)?(state.local_ready?'Локальная Qwen3 готова. Пользовательский ключ не нужен.':state.local_setup?.phase==='downloading'?'Встроенная модель загружается: '+state.local_setup.progress+'%. Оставьте сервер приложения запущенным.':'Встроенная модель загружается на сервере или ожидает установки. Свой ключ необязателен.') :provider===state.provider&&state.has_key?'Ключ подключён. Пустое поле сохраняет его. Удаление касается ключа в базе; ключ из переменной окружения останется активным.':'Вставьте ключ этого провайдера или задайте его переменной окружения на сервере.';
}
async function loadState(initial=false) {
  state=await api('/api/state');
  $('#ai-indicator').classList.toggle('ready',state.ai_ready);
  $('#ai-indicator span').textContent=state.ai_ready?state.providers[state.provider].name+' готов':'Настроить ИИ';
  $('#web').disabled=!(state.web_available||state.supports_web||state.has_search_key);
  if(initial)$('#web').checked=!$('#web').disabled;
  $('#web-hint').textContent=state.has_search_key?'Подключён Tavily. Найденные страницы анализирует выбранная модель. Поиск расходует квоту Tavily.':'Веб-поиск без ключа: несколько запросов к поисковым системам, реальные ссылки и расширение выдачи. Возможны временные ограничения поисковых систем.';
  renderOverview(); renderBase(); renderFound(); renderPlans(); renderCheckout(); renderPurchases(); renderAccess();
  if(initial) { $('#provider').value=state.provider; renderProvider(); }
  if(!state.ai_ready && ['loading','downloading'].includes(state.local_setup?.phase)) {
    $('#ai-indicator span').textContent=state.local_setup.phase==='downloading'?'Загрузка ИИ: '+state.local_setup.progress+'%':'ИИ запускается…';
    if(!aiPollTimer) aiPollTimer=setInterval(()=>loadState().catch(()=>{}),10000);
  } else if(aiPollTimer) { clearInterval(aiPollTimer); aiPollTimer=null; }
}
function route() {
  if(!state) return;
  const [raw,query='']=(location.hash.slice(1)||location.pathname.slice(1)||appearance.start||'overview').split('?');
  const page=titles[raw]?raw:'overview';
  const params=new URLSearchParams(query || location.search);
  if(page==='checkout') {
    const previous=selectedPlan+period;
    if(['pro','team'].includes(params.get('plan'))) selectedPlan=params.get('plan');
    if(['monthly','yearly'].includes(params.get('period'))) period=params.get('period');
    if(previous!==selectedPlan+period) { $('#checkout-result').hidden=true; status('#checkout-status'); }
    renderCheckout();
  }
  $$('.page').forEach(el=>el.hidden=el.id!=='page-'+page);
  $$('[data-page]').forEach(el=>{ const active=el.dataset.page===page||(page==='checkout'&&el.dataset.page==='pricing'); el.classList.toggle('active',active); if(active) el.setAttribute('aria-current','page'); else el.removeAttribute('aria-current'); });
  $('#breadcrumb').textContent=titles[page];
  document.title=titles[page]+' · Supply Match';
  if(page==='projects'||page==='monitoring') window.SupplyProcurement?.refresh();
  if(page==='pricing') renderPlans();
  if(page==='history') loadHistory();
  if(page==='appearance') renderAppearance();
  if(page==='admin') window.SupplyAccount.renderAdmin();
  if(page==='cart') window.SupplyCart?.open(params.get('seller'));
  if(page==='product-checkout') { renderSellerOptions(); if(params.has('seller')) { $('#purchase-seller').value=params.get('seller'); prefillPurchaseSeller(); } updatePurchaseSummary(); }
  if(page==='settings') { $('#provider').value=state.provider; renderProvider(); }
  if(currentPage!==page) { window.scrollTo(0,0); if(currentPage) $('#main').focus({preventScroll:true}); }
  currentPage=page;
}
window.addEventListener('hashchange',route);
$('#base-query').addEventListener('input',()=>state&&renderBase());
$('#status-filter').addEventListener('change',()=>state&&renderBase());
$$('[data-product]').forEach(button=>button.addEventListener('click',()=>$('#product').value=button.dataset.product));
$$('[data-period]').forEach(button=>button.addEventListener('click',()=>{ period=button.dataset.period; renderPlans(); }));
async function performSearch(append=false){
  if(!append){
    lastSearch={city:$('#city').value,product:$('#product').value,need:$('#need').value+'\nБюджет: '+$('#budget').value+'\nСостояние: '+$('#condition').value,web:$('#web').checked,country:$('#country').value,category:$('#category').value,purpose:$('#purpose').value,quantity:$('#quantity').value,trade_format:$('#trade-format').value,depth:$('#search-depth').value};
    searchPage=0;$('#result-query').value='';$('#result-type').value='';$('#result-domain').value='';$('#result-saved').value='';
  }
  if(!lastSearch)return;
  $('#search-submit').disabled=true;$('#search-more').disabled=true;
  status('#search-status',lastSearch.web?'Ищем реальные страницы продавцов по нескольким запросам…':'Подбираем кандидатов по знаниям модели. Может потребоваться до 4 минут…');
  try{
    const result=await api('/api/search','POST',{...lastSearch,page:searchPage+1});
    searchPage++;searchHasMore=!!result.has_more;
    if(append){const known=new Set(found.map(s=>s.site||s.name));found.push(...result.suppliers.filter(s=>!known.has(s.site||s.name)));const urls=new Set(searchSources.map(s=>s.url));searchSources.push(...(result.sources||[]).filter(s=>!urls.has(s.url)));}
    else{found=result.suppliers;searchSources=result.sources||[];}
    searchMode=result.mode;renderFound();await loadState();
    const description=searchMode==='discovery'?'Реальные веб-страницы; продавцы и условия ещё не проверены. Можно отфильтровать выдачу или расширить поиск.':searchMode==='model'?'Кандидаты по знаниям модели.':'Подбор по веб-источникам.';
    status('#search-status',found.length?'Найдено: '+found.length+'. '+description+' '+(result.message||''):(result.message||'Страницы не найдены. Попробуйте другое название товара или регион.'));
  }catch(error){status('#search-status',error.message,true);}
  finally{$('#search-submit').disabled=false;$('#search-more').disabled=false;}
}
$('#search-form').addEventListener('submit',event=>{event.preventDefault();performSearch(false);});
$('#search-more').addEventListener('click',()=>performSearch(true));
for(const id of ['result-query','result-type','result-domain','result-saved','result-sort'])$('#'+id).addEventListener(id==='result-query'?'input':'change',renderFound);
function openSupplier(s=null) {
  const form=$('#supplier-form'); form.reset();
  $('#supplier-id').value=s?.id || '';
  $('#supplier-dialog-title').textContent=s?'Редактировать поставщика':'Новый поставщик';
  if(s) for(const field of ['name','city','type','offer','conditions','contact','site','note','status','country','category','currency','unit_price','moq','lead_time','incoterms','trade_format']) {
    const el=form.elements.namedItem(field);
    if(['type','currency'].includes(field)&&s[field]&&!Array.from(el.options).some(o=>o.value===s[field])) el.add(new Option(s[field],s[field]));
    el.value=s[field] || (field==='status'?'новый':field==='trade_format'?'B2B':'');
  }
  status('#supplier-status'); $('#supplier-dialog').showModal();
}
$('#add-supplier').addEventListener('click',()=>openSupplier());
$('#close-supplier').addEventListener('click',()=>$('#supplier-dialog').close());
$('#supplier-form').addEventListener('submit',async event=>{
  event.preventDefault(); $('#supplier-submit').disabled=true;
  const id=$('#supplier-id').value, data=Object.fromEntries(new FormData(event.target));
  try {
    const result=await api('/api/suppliers'+(id?'/'+id:''),id?'PATCH':'POST',data);
    if(result.duplicate) { status('#supplier-status','Поставщик с таким названием уже есть в базе.',true); return; }
    $('#supplier-dialog').close(); await loadState(); toast('Поставщик сохранён');
  } catch(error) { status('#supplier-status',error.message,true); }
  finally { $('#supplier-submit').disabled=false; }
});
async function cardAction(event) {
  const button=event.target.closest('[data-action]'); if(!button || button.disabled) return;
  const {action,id}=button.dataset;
  if(action==='purchase') { location.hash='cart?seller='+id; return; }
  if(action==='catalog') return window.SupplyCart.openCatalog(Number(id));
  if(action==='cost') { const seller=state.suppliers.find(s=>s.id===Number(id)); if(seller && /^[0-9]+([.,][0-9]+)?$/.test(seller.unit_price)) $('#calc-price').value=seller.unit_price.replace(',','.'); if(seller?.currency && [...$('#calc-currency').options].some(o=>o.value===seller.currency)) $('#calc-currency').value=seller.currency; location.hash='calculator'; updateRateHint(); return; }
  if(action==='edit') return openSupplier(state.suppliers.find(s=>s.id===Number(id)));
  if(action==='delete') { deleteId=Number(id); $('#delete-status').textContent=''; return $('#delete-dialog').showModal(); }
  button.disabled=true;
  try {
    if(action==='save-found') {
      const result=await api('/api/suppliers','POST',found[Number(id)]);
      await loadState(); toast(result.duplicate?'Этот поставщик уже в вашей базе':'Поставщик добавлен в базу');
    }
    if(action==='invite') {
      toast('Готовим письмо поставщику…');
      const result=await api('/api/suppliers/'+id+'/invite','POST');
      letterId=Number(id); $('#letter-text').value=result.text; status('#letter-status'); $('#letter-dialog').showModal();
    }
  } catch(error) { toast(error.message,true); }
  finally { if(button.isConnected) button.disabled=false; }
}
$('#base-list').addEventListener('click',cardAction);
$('#search-results').addEventListener('click',cardAction);
$('#cancel-delete').addEventListener('click',()=>$('#delete-dialog').close());
$('#confirm-delete').addEventListener('click',async()=>{
  $('#confirm-delete').disabled=true;
  try { await api('/api/suppliers/'+deleteId,'DELETE'); $('#delete-dialog').close(); await loadState(); toast('Поставщик удалён'); }
  catch(error) { $('#delete-status').textContent=error.message; }
  finally { $('#confirm-delete').disabled=false; }
});
$('#close-letter').addEventListener('click',()=>$('#letter-dialog').close());
$('#copy-letter').addEventListener('click',async()=>{
  try { await navigator.clipboard.writeText($('#letter-text').value); status('#letter-status','Текст скопирован. Можно отправить письмо поставщику.'); }
  catch { $('#letter-text').select(); status('#letter-status','Скопируйте выделенный текст вручную: Ctrl+C или через меню устройства.'); }
});
$('#mark-invited').addEventListener('click',async()=>{
  $('#mark-invited').disabled=true;
  try { await api('/api/suppliers/'+letterId,'PATCH',{status:'приглашён'}); await loadState(); status('#letter-status','Статус изменён на «Приглашён». Отправьте письмо поставщику самостоятельно.'); }
  catch(error) { status('#letter-status',error.message,true); }
  finally { $('#mark-invited').disabled=false; }
});
$('#rank-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(!state.suppliers.length) return status('#rank-status','Сначала добавьте поставщиков в базу.',true);
  $('#rank-submit').disabled=true; status('#rank-status','Сравниваем предложения. Локальной модели может потребоваться до 4 минут…');
  try {
    const result=await api('/api/rank','POST',{requirements:$('#requirements').value});
    $('#ranking').innerHTML=result.ranking.map((r,i)=>`<article class="rank-card"><span class="rank-position">${String(i+1).padStart(2,'0')}</span><div class="rank-body"><h3>${esc(r.name)}</h3><p>${esc(r.reason)}</p></div><div class="rank-score">${esc(r.score)}<small> / 100</small></div></article>`).join('')+(result.advice?`<article class="panel"><h2>Что уточнить перед выбором</h2><p>${esc(result.advice)}</p></article>`:'');
    await loadState();
    status('#rank-status',result.ranking.length?'Сравнение готово. Оценка ИИ ориентировочная и зависит от полноты данных.':'Модель не вернула оценки. Дополните условия поставщиков и повторите.');
  } catch(error) { status('#rank-status',error.message,true); }
  finally { $('#rank-submit').disabled=false; }
});
$('#provider').addEventListener('change',()=>{ $('#api-key').value=''; $('#clear-key').checked=false; renderProvider(); status('#settings-status'); });
$('#settings-form').addEventListener('submit',async event=>{
  event.preventDefault(); const button=event.submitter; button.disabled=true;
  try {
    await api('/api/settings','POST',{provider:$('#provider').value,key:$('#api-key').value,model:$('#model').value,clear_key:$('#clear-key').checked,search_key:$('#search-key').value,clear_search_key:$('#clear-search-key').checked,endpoint:$('#custom-endpoint').value,api_format:$('#custom-format').value});
    $('#api-key').value=''; $('#search-key').value=''; $('#clear-key').checked=false; $('#clear-search-key').checked=false; await loadState(); renderProvider();
    status('#settings-status','Настройки сохранены. Проверьте подключение перед первым запросом.');
  } catch(error) { status('#settings-status',error.message,true); }
  finally { button.disabled=false; }
});
$('#test-ai').addEventListener('click',async()=>{
  if($('#api-key').value || $('#search-key').value || $('#clear-search-key').checked || $('#clear-key').checked || $('#provider').value!==state.provider || $('#model').value!==state.model) return status('#settings-status','Сначала сохраните изменённые настройки.',true);
  $('#test-ai').disabled=true; status('#settings-status','Проверяем подключение к '+state.providers[state.provider].name+'…');
  try { await api('/api/ai/test','POST'); status('#settings-status','Подключение работает. Можно начинать поиск и сравнение.'); }
  catch(error) { status('#settings-status',error.message,true); }
  finally { $('#test-ai').disabled=false; }
});
$('#checkout-form').addEventListener('submit',async event=>{
  event.preventDefault(); $('#checkout-submit').disabled=true; status('#checkout-status','Сохраняем заявку…');
  const data={plan:selectedPlan,period,email:$('#checkout-email').value.trim(),company:$('#checkout-company').value.trim(),consent:$('#checkout-consent').checked};
  const fingerprint=JSON.stringify(data);
  if(fingerprint!==checkoutFingerprint) { checkoutFingerprint=fingerprint; checkoutToken=token(); }
  try {
    const result=await api('/api/billing/checkout','POST',{...data,idempotency_key:checkoutToken,native_request:!!window.SupplyMatchNative});
    const order=result.order;
    $('#checkout-result').innerHTML=`<span class="pill">✓ Заявка сохранена</span><h2>${result.payment_enabled?'Завершите оплату':'Вы в списке на подключение'}</h2><p>${esc(result.message)}</p><p>Номер заявки: <strong>${esc(order.id.slice(0,8))}</strong> · Сумма будущей оплаты: ${money(order.amount/100)}<br>Контакт: ${esc(order.email)}</p><a class="button secondary" href="#pricing">Посмотреть мои заявки →</a>${result.checkout_url?`<a class="button primary" href="${esc(safeLink(result.checkout_url))}" target="_blank" rel="noopener noreferrer">Перейти к оплате →</a>`:''}`;
    $('#checkout-result').hidden=false; status('#checkout-status',result.payment_enabled?'Платёж создан. Перейдите к оплате по ссылке.':'Заявка сохранена. Деньги не списаны.');
    await loadState(); $('#checkout-result').scrollIntoView({behavior:'smooth',block:'center'});
  } catch(error) { status('#checkout-status',error.message,true); }
  finally { $('#checkout-submit').disabled=false; }
});

const appearanceKeys=Object.keys(defaultAppearance);
const colorKeys=['customAccent','customBg','customSurface','customText'];
const looks={forest:{theme:'light',accent:'green'},midnight:{theme:'dark',accent:'green'},ocean:{theme:'light',accent:'blue'},aurora:{theme:'dark',accent:'purple',effects:'aurora'},sand:{theme:'light',accent:'amber',effects:'soft'}};
let themeProfiles=[];
try { const data=JSON.parse(localStorage.getItem('supplymatch-theme-profiles')||'[]'); if(Array.isArray(data)) themeProfiles=data.filter(p=>p&&typeof p.id==='string'&&typeof p.name==='string'&&p.settings).slice(0,8); } catch {}
function applyAppearance() {
  const root=document.documentElement, custom=appearance.theme==='custom'&&!SupplyTheme.validate(appearance);
  root.dataset.theme=custom?(appearance.customBase==='light'?'light':'dark'):appearance.theme==='light'?'light':appearance.theme==='dark'?'dark':window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
  root.dataset.accent=['green','blue','purple','amber'].includes(appearance.accent)?appearance.accent:'green';
  root.dataset.density=appearance.density==='compact'?'compact':'comfortable';
  root.dataset.effects=['aurora','soft','off'].includes(appearance.effects)?appearance.effects:'aurora';
  root.dataset.motion=['subtle','expressive','off'].includes(appearance.motion)?appearance.motion:'subtle';
  root.dataset.glass=String(appearance.glass!==false);
  const props=['bg','white','raised','subtle','input','ink','muted','green','lime','soft','line','accent-ink'];
  props.forEach(p=>root.style.removeProperty('--'+p));
  if(custom){
    const a=appearance.customAccent,b=appearance.customBg,s=appearance.customSurface,t=appearance.customText;
    const values=[b,s,s,s,`color-mix(in srgb,${b},${s} 45%)`,t,t,a,a,`color-mix(in srgb,${a} 12%,${s})`,`color-mix(in srgb,${t} 16%,${s})`,SupplyTheme.onAccent(a)];
    props.forEach((p,i)=>root.style.setProperty('--'+p,values[i]));
  }
  if($('#workspace-name')) $('#workspace-name').textContent=String(appearance.workspace||defaultAppearance.workspace).slice(0,40);
}
function renderAppearance() {
  for(const key of appearanceKeys){const el=$('#appearance-'+key);if(el) key==='glass'?el.checked=appearance[key]:el.value=appearance[key];}
  colorKeys.forEach(k=>$('#appearance-'+k+'-hex').value=appearance[k]);
  $('#custom-theme-controls').hidden=appearance.theme!=='custom';
  $$('.theme-preset').forEach(b=>b.setAttribute('aria-pressed',String(looks[b.dataset.look].theme===appearance.theme&&looks[b.dataset.look].accent===appearance.accent)));
  renderThemeProfiles();
}
function readAppearance(){
  const next={...appearance};
  for(const key of appearanceKeys){const el=$('#appearance-'+key);if(el)next[key]=key==='glass'?el.checked:el.value;}
  colorKeys.forEach(k=>next[k]=$('#appearance-'+k+'-hex').value.trim());
  return next;
}
function previewAppearance(){
  const next=readAppearance(),error=next.theme==='custom'?SupplyTheme.validate(next):'';
  $('#custom-theme-controls').hidden=next.theme!=='custom';
  status('#theme-contrast',error||'Контраст основного текста соответствует WCAG AA.',!!error);
  if(error)return false;
  appearance=next;applyAppearance();return true;
}
function saveAppearance(){localStorage.setItem('supplymatch-appearance',JSON.stringify(appearance));}
$('#appearance-form').addEventListener('submit',event=>{
  event.preventDefault();if(!previewAppearance()){status('#appearance-status','Исправьте цвета перед сохранением.',true);return;}
  try{saveAppearance();status('#appearance-status','Оформление сохранено на этом устройстве.');}catch{status('#appearance-status','Браузер не разрешил сохранить настройки.',true);}
});
$('#appearance-form').addEventListener('change',()=>{if(previewAppearance())status('#appearance-status','Предпросмотр. Нажмите «Применить оформление», чтобы сохранить.');});
colorKeys.forEach(key=>{
  $('#appearance-'+key).addEventListener('input',event=>{$('#appearance-'+key+'-hex').value=event.target.value;previewAppearance();});
  $('#appearance-'+key+'-hex').addEventListener('input',event=>{if(SupplyTheme.hex(event.target.value))$('#appearance-'+key).value=event.target.value;previewAppearance();});
});
$$('[data-look]').forEach(button=>button.addEventListener('click',()=>{
  appearance={...appearance,...looks[button.dataset.look]};applyAppearance();renderAppearance();
  try{saveAppearance();status('#appearance-status','Тема «'+button.querySelector('strong').textContent+'» применена и сохранена.');}catch{status('#appearance-status','Тема применена. Сохранение недоступно.',true);}
}));
function renderThemeProfiles(){
  $('#saved-theme-profiles').innerHTML=themeProfiles.length?themeProfiles.map(p=>`<div class="saved-theme-row"><button type="button" data-profile-id="${esc(p.id)}">${esc(p.name)}<small>Применить сохранённое оформление</small></button><button type="button" class="theme-delete" data-profile-delete="${esc(p.id)}" aria-label="Удалить тему ${esc(p.name)}">×</button></div>`).join(''):'<p class="muted small-text">Сохраните свой первый режим. До 8 тем на этом устройстве.</p>';
}
$('#save-theme-profile').addEventListener('click',()=>{
  const name=$('#theme-profile-name').value.trim().slice(0,32);
  if(!name){status('#appearance-status','Введите название своего режима.',true);return;}
  if(!previewAppearance())return;
  const existing=themeProfiles.find(p=>p.name===name);
  if(!existing&&themeProfiles.length>=8){status('#appearance-status','Сохранено 8 тем. Удалите одну, чтобы добавить новую.',true);return;}
  const profile={id:existing?.id||token(),name,settings:{...appearance}};
  themeProfiles=existing?themeProfiles.map(p=>p.id===existing.id?profile:p):[...themeProfiles,profile];
  try{localStorage.setItem('supplymatch-theme-profiles',JSON.stringify(themeProfiles));saveAppearance();renderThemeProfiles();status('#appearance-status','Режим «'+name+'» сохранён.');}catch{status('#appearance-status','Не удалось сохранить режим.',true);}
});
$('#saved-theme-profiles').addEventListener('click',event=>{
  const apply=event.target.closest('[data-profile-id]'),remove=event.target.closest('[data-profile-delete]');
  try{
    if(apply){const profile=themeProfiles.find(p=>p.id===apply.dataset.profileId);if(!profile)return;const next={...defaultAppearance,...profile.settings};if(next.theme==='custom'&&SupplyTheme.validate(next)){status('#appearance-status','Цвета сохранённой темы некорректны.',true);return;}appearance=next;applyAppearance();renderAppearance();saveAppearance();status('#appearance-status','Режим «'+profile.name+'» применён.');}
    if(remove){themeProfiles=themeProfiles.filter(p=>p.id!==remove.dataset.profileDelete);localStorage.setItem('supplymatch-theme-profiles',JSON.stringify(themeProfiles));renderThemeProfiles();}
  }catch{status('#appearance-status','Не удалось сохранить настройки.',true);}
});
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{if(appearance.theme==='system')applyAppearance();});
$('#appearance-reset').addEventListener('click',()=>{appearance={...defaultAppearance};try{localStorage.removeItem('supplymatch-appearance');}catch{}applyAppearance();renderAppearance();status('#appearance-status','Вернули исходное оформление. Сохранённые темы доступны ниже.');});
function renderAccess() {
  const a=state.access, label={trial:'Пробная версия',start:'Старт',pro:'Профи'}[a.plan];
  $('#access-banner').hidden=false;
  $('#access-banner').innerHTML=`<span><strong>${esc(label)}</strong>${a.plan==='trial'?` · осталось ${a.trial_days_left} дн.`:''} · ИИ: ${a.ai_used} / ${a.limits.ai} · База: ${state.suppliers.length} / ${a.limits.suppliers}</span><a href="#pricing">Тарифы →</a>`;
  $('.profile small').textContent=label;
}
function updateRateHint() {
  const same=$('#calc-currency').value===$('#calc-base').value;
  $('#calc-rate').disabled=same;
  if(same) $('#calc-rate').value='1';
  $('#calc-rate-hint').textContent=same?'Валюты совпадают, используется курс 1.':`Укажите, сколько ${$('#calc-base').value} стоит 1 ${$('#calc-currency').value}. Курс вводится вручную.`;
}
$('#calc-currency').addEventListener('change',updateRateHint); $('#calc-base').addEventListener('change',updateRateHint);
updateRateHint();
const formatAmount=(amount,currency)=>new Intl.NumberFormat('ru-RU',{style:'currency',currency,maximumFractionDigits:2}).format(Number(amount));
$('#calculator-form').addEventListener('submit',async event=>{
  event.preventDefault(); $('#calc-submit').disabled=true;
  try {
    const result=await api('/api/calculate','POST',{quantity:$('#calc-quantity').value,unit_price:$('#calc-price').value,currency:$('#calc-currency').value,base_currency:$('#calc-base').value,rate:$('#calc-rate').value,shipping:$('#calc-shipping').value,taxes:$('#calc-taxes').value,other:$('#calc-other').value,resale_price:$('#calc-resale').value});
    const amount=value=>formatAmount(value,result.currency);
    $('#calc-result').innerHTML=`<span class="summary-label">ПОЛНАЯ СТОИМОСТЬ ПО ВАШИМ ДАННЫМ</span><h2>${amount(result.total)}</h2>${[['Товар',result.goods],['Доставка',result.shipping],['Пошлины и налоги',result.taxes],['Прочие расходы',result.other]].map(([label,value])=>`<div class="summary-line"><span>${label}</span><strong>${amount(value)}</strong></div>`).join('')}<div class="summary-line summary-total"><span>На одну единицу</span><strong>${amount(result.per_unit)}</strong></div>${result.profit!==undefined?`<div class="divider"></div><h3>Сценарий перепродажи</h3><div class="summary-line"><span>Выручка</span><strong>${amount(result.revenue)}</strong></div><div class="summary-line"><span>Разница доходов и расходов</span><strong>${amount(result.profit)}</strong></div><p class="small-text">${result.margin!==null?'Маржа по введённым данным: '+esc(result.margin)+'%. ':''}Без расходов и налогов, которые вы не включили в расчёт.</p>`:''}<div class="notice">Оценка учитывает только введённые суммы. Она не подтверждает цену продавца или итоговые расходы на таможне.</div>`;
    status('#calc-status','Расчёт готов.');
  } catch(error) { status('#calc-status',error.message,true); }
  finally { $('#calc-submit').disabled=false; }
});
const presets={
  wholesale:{product:'Обувь для перепродажи',country:'Китай',category:'Одежда и обувь',quantity:'100 пар',mode:'B2B',purpose:'Для перепродажи',condition:'Новое',need:'Размерный ряд 36–44, нужны образцы, документы и доставка. Уточнить производителя и минимальную партию.'},
  retail:{product:'Ноутбук для работы',country:'',category:'Электроника',quantity:'1 шт.',mode:'B2C',purpose:'Для личного использования',condition:'Новое',need:'Официальная гарантия, документы, доставка. Сравнить комплектации и условия возврата.'},
  private:{product:'Велосипед',country:'',category:'Другое',quantity:'1 шт.',mode:'C2C',purpose:'Для личного использования',condition:'Бывшее в употреблении',need:'Нужны реальные фотографии, описание состояния и возможность осмотра при встрече.'},
  cafe:{product:'Кофе в зёрнах для кофейни',country:'',category:'Продукты и общепит',quantity:'30 кг в месяц',mode:'B2B',purpose:'Для бизнеса',condition:'Новое',need:'Свежая обжарка, дегустационная партия, регулярная доставка, документы.'}
};
$$('[data-preset]').forEach(button=>button.addEventListener('click',()=>{
  const preset=presets[button.dataset.preset];
  for(const field of ['product','country','category','quantity','purpose','condition','need']) $('#'+field).value=preset[field];
  $('#trade-format').value=preset.mode; toast('Сценарий заполнен. Уточните бюджет и условия.');
}));
function purchaseItemsMarkup(order){
  let items=[];try{items=JSON.parse(order.items||'[]');}catch{}
  if(!Array.isArray(items)||!items.length)return '';
  let expenses={};try{expenses=JSON.parse(order.expense_breakdown||'{}');}catch{}
  const breakdown=Object.entries({goods:'Товары',shipping:'Доставка',taxes:'Налоги / пошлины',other:'Прочее'}).filter(([key])=>expenses[key]!==undefined).map(([key,label])=>'<div class="summary-line"><span>'+label+'</span><strong>'+formatAmount(expenses[key],order.currency)+'</strong></div>').join('');
  return '<div class="purchase-item-list">'+items.map(item=>`<div><strong>${esc(item.name)}</strong><span>${esc(item.quantity)} × ${formatAmount(item.unit_price,order.currency)} = ${formatAmount(item.line_total,order.currency)}</span><small>${item.arrival?'Получение по расчёту: '+esc(item.arrival.from+' — '+item.arrival.to):'Доставка: нет информации'}</small></div>`).join('')+'</div>'+breakdown;
}
function renderPurchases() {
  const selection=$('#purchase-seller').value; renderSellerOptions();
  if(state.suppliers.some(s=>String(s.id)===selection)) $('#purchase-seller').value=selection;
  $('#purchases-list').innerHTML=state.purchases.length?state.purchases.map(order=>`<article class="purchase-card panel"><div class="section-heading"><div><span class="eyebrow">${esc(order.trade_format)} · № ${esc(order.id.slice(0,8))}</span><h2>${esc(order.product)}</h2><p>${esc(order.seller_name)} · ${new Date(order.created_at).toLocaleDateString('ru-RU')}</p></div><strong>${formatAmount(order.amount,order.currency)}</strong></div><div class="supplier-facts"><span class="badge">Количество: ${esc(order.quantity)}</span><span class="badge invited">${order.status==='paid'?'Оплачен':order.status==='pending_payment'?'Ожидает оплаты':'Оплата не подтверждена'}</span></div><button type="button" class="button secondary" data-order-manage="${esc(order.id)}">Этапы и доставка →</button><details><summary>Условия заказа</summary>${purchaseItemsMarkup(order)}<p>${order.items&&order.items!=='[]'?'Товары и введённые дополнительные расходы':'Цена единицы'}: ${formatAmount(order.unit_price,order.currency)}<br>Доставка: ${formatAmount(order.shipping,order.currency)}<br>Получение: ${esc(order.address)}<br>Контакт: ${esc(order.buyer_email)}</p></details></article>`).join(''):empty('Покупок пока нет','Добавьте продавца в базу, согласуйте условия и оформите первый заказ.','<a class="button primary" href="#base">Открыть базу →</a>');
}
function renderSellerOptions() {
  const current=$('#purchase-seller').value;
  $('#purchase-seller').innerHTML='<option value="">Выберите продавца</option>'+state.suppliers.map(s=>`<option value="${s.id}">${esc(s.name)}${s.country?' · '+esc(s.country):''}</option>`).join('');
  if(current) $('#purchase-seller').value=current;
}
function updatePurchaseSummary() {
  const quantity=Number($('#purchase-quantity').value),price=Number($('#purchase-price').value),shipping=Number($('#purchase-shipping').value);
  const total=quantity*price+shipping;
  $('#purchase-total').textContent=Number.isFinite(total)&&total>0?formatAmount(total,$('#purchase-currency').value):'Проверьте условия';
}
$('#new-purchase').addEventListener('click',()=>{
  if(!state.suppliers.length) { toast('Сначала добавьте продавца в базу.'); location.hash='base'; return; }
  location.hash='cart';
});
function prefillPurchaseSeller() {
  const seller=state.suppliers.find(s=>String(s.id)===$('#purchase-seller').value);
  if(seller) {
    if(!$('#purchase-product').value) $('#purchase-product').value=seller.offer.slice(0,500);
    $('#purchase-format').value=seller.trade_format || 'B2B';
    if([...$('#purchase-currency').options].some(o=>o.value===seller.currency)) $('#purchase-currency').value=seller.currency;
    if(!$('#purchase-price').value && /^[0-9]+([.,][0-9]+)?$/.test(seller.unit_price)) $('#purchase-price').value=seller.unit_price.replace(',','.');
    updatePurchaseSummary();
  }
}
$('#purchase-seller').addEventListener('change',prefillPurchaseSeller);
for(const field of ['quantity','price','shipping','currency']) $('#purchase-'+field).addEventListener('input',updatePurchaseSummary);
$('#purchase-form').addEventListener('submit',async event=>{
  event.preventDefault(); $('#purchase-submit').disabled=true;
  const data={supplier_id:Number($('#purchase-seller').value),product:$('#purchase-product').value,quantity:$('#purchase-quantity').value,unit_price:$('#purchase-price').value,shipping:$('#purchase-shipping').value,currency:$('#purchase-currency').value,trade_format:$('#purchase-format').value,buyer_email:$('#purchase-email').value.trim(),address:$('#purchase-address').value,consent:$('#purchase-consent').checked};
  const fingerprint=JSON.stringify(data);
  if(fingerprint!==purchaseFingerprint) { purchaseFingerprint=fingerprint; purchaseToken=token(); }
  try {
    const result=await api('/api/purchases','POST',{...data,idempotency_key:purchaseToken});
    $('#purchase-result').hidden=false;
    $('#purchase-result').innerHTML=`<span class="pill">Заказ сохранён</span><h2>№ ${esc(result.order.id.slice(0,8))}</h2><p>${esc(result.message)}</p><p>${formatAmount(result.order.amount,result.order.currency)} · ${esc(result.order.seller_name)}</p><a class="button secondary" href="#purchases">Мои заказы →</a>${result.checkout_url?`<a class="button primary" href="${esc(safeLink(result.checkout_url))}" data-payment="true" target="_blank" rel="noopener noreferrer">Перейти к оплате →</a>`:''}`;
    status('#purchase-status',result.payment_enabled?'Заказ создан. Перейдите на защищённую страницу оплаты.':'Заказ сохранён без списания денег.');
    await loadState(); $('#purchase-result').scrollIntoView({behavior:'smooth',block:'center'});
  } catch(error) { status('#purchase-status',error.message,true); }
  finally { $('#purchase-submit').disabled=false; }
});
async function loadHistory() {
  if(!state.access.limits.history) { $('#history-list').innerHTML=empty('История входит в Профи','Она также доступна в течение 14 дней пробной версии. Сохранённые результаты остаются в базе.','<a href="#pricing" class="button primary">Посмотреть тарифы →</a>'); return; }
  try {
    historyItems=await api('/api/history');
    $('#history-list').innerHTML=historyItems.length?historyItems.map(item=>`<article class="panel history-card"><span class="eyebrow">${item.kind==='search'?'ПОИСК':'СРАВНЕНИЕ'} · ${new Date(item.created_at).toLocaleString('ru-RU')}</span><h3>${esc(item.title)}</h3><button class="button secondary" data-history="${item.id}">Открыть результат →</button></article>`).join(''):empty('История пока пуста','Здесь появятся результаты ваших поисков и сравнений.');
  } catch(error) { $('#history-list').innerHTML=empty('Не удалось загрузить историю',error.message); }
}
$('#refresh-history').addEventListener('click',loadHistory);
$('#history-list').addEventListener('click',event=>{
  const button=event.target.closest('[data-history]'); if(!button) return;
  const item=historyItems.find(i=>i.id===Number(button.dataset.history)); if(!item) return;
  if(item.kind==='search') {
    found=item.payload.suppliers; searchSources=item.payload.sources || []; searchMode=item.payload.mode; renderFound();
    status('#search-status','Открыт сохранённый результат от '+new Date(item.created_at).toLocaleString('ru-RU')+'. Информация могла измениться.'); location.hash='find';
  } else {
    const result=item.payload;
    $('#ranking').innerHTML=result.ranking.map((r,i)=>`<article class="rank-card"><span class="rank-position">${i+1}</span><div class="rank-body"><h3>${esc(r.name)}</h3><p>${esc(r.reason)}</p></div><span class="rank-score">${esc(r.score)}<small> /100</small></span></article>`).join('')+`<article class="panel"><p>${esc(result.advice)}</p></article>`;
    status('#rank-status','Сохранённое сравнение от '+new Date(item.created_at).toLocaleString('ru-RU')); location.hash='compare';
  }
});
function openConnection() {
  $('#connection-url').value=apiBase; $('#connection-token').value='';
  if(!$('#connection-dialog').open) $('#connection-dialog').showModal();
}
$('#open-connection').addEventListener('click',openConnection);
$('#connection-close').addEventListener('click',()=>$('#connection-dialog').close());
$('#connection-form').addEventListener('submit',async event=>{
  event.preventDefault();
  try {
    const url=new URL($('#connection-url').value);
    if(url.protocol!=='https:' && !['localhost','127.0.0.1'].includes(url.hostname)) throw new Error('Для удалённого сервера нужен HTTPS-адрес.');
    if(url.username || url.password || url.search || url.hash) throw new Error('Адрес сервера должен быть без пароля, параметров и фрагмента.');
    const previousBase=apiBase, previousToken=accessToken;
    apiBase=url.origin; accessToken=$('#connection-token').value.trim();
    try { localStorage.setItem('supplymatch-server',apiBase); if(!await window.SupplyAccount.init()){ $('#connection-dialog').close(); return; } await loadState(true); } catch(error) { apiBase=previousBase; accessToken=previousToken; throw error; }
    localStorage.setItem('supplymatch-server',apiBase); sessionStorage.setItem('supplymatch-token',accessToken);
    $('#connection-token').value=''; $('#connection-dialog').close(); $('#startup-error').hidden=true; route(); toast('Сервер подключён');
  } catch(error) { status('#connection-status',error.message,true); }
});

document.addEventListener('click',async event=>{ const link=event.target.closest('a[data-payment]'); if(link && window.SupplyMatchNative) { event.preventDefault(); try { await window.SupplyMatchNative.openPayment(link.href); } catch(error) { toast('Не удалось открыть оплату: '+error.message,true); } } });
window.addEventListener('payments-return',()=>loadState().catch(error=>toast(error.message,true)));
if('serviceWorker' in navigator && !apiBase && !window.Capacitor?.isNativePlatform?.()) navigator.serviceWorker.register('/sw.js').catch(()=>{});
$('#refresh-purchases').addEventListener('click',async()=>{ try { await loadState(); toast('Статусы обновлены'); } catch(error) { toast(error.message,true); } });
$('#export-csv').addEventListener('click',async event=>{ event.preventDefault(); try { const response=await fetch(apiBase+'/api/export',{credentials:'include',headers:{...window.SupplyAccount.headers(),}}); if(!response.ok) throw new Error('Не удалось выгрузить базу. Проверьте подключение.'); const blob=await response.blob(); const url=URL.createObjectURL(blob); const link=document.createElement('a'); link.href=url; link.download='supply-match.csv'; document.body.appendChild(link); link.click(); link.remove(); setTimeout(()=>URL.revokeObjectURL(url),1000); } catch(error) { toast(error.message,true); } });
(async()=>{
  try { if(!await window.SupplyAccount.init())return; await loadState(true); route(); }
  catch(error) { $('#startup-error').textContent=error.message+' Обновите страницу после запуска сервера.'; $('#startup-error').hidden=false; if(location.protocol==='capacitor:' || location.protocol==='file:' || (location.protocol==='https:' && location.hostname==='localhost') || apiBase) openConnection(); }
})();
