"""Локальный HTTP JSON API и интерфейс приложения."""
import csv
import io
import json
import re
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from http.cookies import SimpleCookie
from app.services.auth_service import AuthService, AuthError, Unauthorized, Forbidden
from app.config import PROVIDERS, STATUSES, WEB_DIR, DATA_DIR
from app.database import Database, FIELDS
from app.services.ai_service import AIClient, AIError, validate_endpoint
from app.services.local_ai import LocalAIProcess
from app.services.billing_service import BillingService
from app.services.supplier_service import SupplierService
from app.services.access_service import AccessService
from app.services.cost_service import calculate_cost
from app.services.purchase_service import PurchaseService
from app.services.catalog_service import CatalogService
from app.services.cart_service import CartService
from app.services.payment_service import YooKassaGateway
from app.services.procurement_service import ProcurementService


def make_handler(db, service, ai, local_ai=None, auth=None):
    original_db, original_ai, original_service = db, ai, service
    billing = BillingService(db)
    access = AccessService(db)
    purchases = PurchaseService(db)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, obj=None, raw=None, ctype="application/json; charset=utf-8"):
            body = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            origin = self.headers.get("Origin", "")
            if origin and origin in os.environ.get("SUPPLY_MATCH_ALLOWED_ORIGINS", "").split(","):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-CSRF-Token, X-SupplyMatch-Native")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self' https: http://localhost:* http://127.0.0.1:*; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for key,value in getattr(self,"response_headers",[]):
                self.send_header(key,value)
            if os.environ.get("SUPPLY_MATCH_ENV")=="production":
                self.send_header("Strict-Transport-Security","max-age=31536000")
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Нужен запрос в формате JSON.")
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ValueError("Некорректная длина запроса.") from None
            if size < 0 or size > 200000:
                raise ValueError("Слишком большой запрос.")
            try:
                data = json.loads(self.rfile.read(size) or b"{}")
            except (ValueError, UnicodeError):
                raise ValueError("Некорректный JSON.") from None
            if not isinstance(data, dict):
                raise ValueError("Ожидается объект JSON.")
            return data

        def _text(self, data, key, limit=4000):
            value = data.get(key, "")
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError("Неверное или слишком длинное поле: " + key)
            return value.strip()

        def _supplier(self, data, partial=False):
            clean = {k: self._text(data, k) for k in FIELDS if k in data}
            if not partial or "name" in clean:
                if not clean.get("name"):
                    raise ValueError("Укажите название поставщика.")
            if "status" in clean and clean["status"] not in STATUSES:
                raise ValueError("Неизвестный статус поставщика.")
            if clean.get("trade_format") and clean["trade_format"] not in ("B2B", "B2C", "C2C"):
                raise ValueError("Выберите формат B2B, B2C или C2C.")
            if clean.get("site"):
                url = urlsplit(clean["site"])
                if url.scheme not in ("http", "https") or not url.netloc:
                    raise ValueError("Адрес сайта должен начинаться с https:// или http://.")
            return clean

        def _cookies(self):
            cookies=SimpleCookie()
            try:cookies.load(self.headers.get('Cookie',''))
            except Exception:pass
            return cookies

        def _cookie(self,name,value,age=2592000,oauth=False):
            secure=os.environ.get('SUPPLY_MATCH_PUBLIC_URL','').startswith('https://')
            self.response_headers.append(('Set-Cookie',f'{name}={value}; Path=/; HttpOnly; Max-Age={age}; SameSite={"None" if oauth else "Lax"}'+('; Secure' if secure else '')))

        def _client_ip(self):
            ip=self.client_address[0]
            if os.environ.get('SUPPLY_MATCH_TRUST_PROXY')=='1' and ip in ('127.0.0.1','::1'):
                import ipaddress
                forwarded=self.headers.get('X-Real-IP','')
                try:ip=str(ipaddress.ip_address(forwarded))
                except ValueError:pass
            return ip

        def _login(self,user):
            token,csrf=auth.session(user,ip=self._client_ip(),device=self.headers.get('User-Agent',''));self._cookie('sm_session',token)
            result={'user':user,'csrf':csrf}
            if self.headers.get('X-SupplyMatch-Native')=='1':result['session_token']=token
            return result

        def _auth_route(self,method,path):
            if method=='GET' and path=='/api/auth/me':
                try:user,csrf=auth.authenticate(self.session_token);return {'user':user,'csrf':csrf,'providers':auth.providers()}
                except Unauthorized:return {'user':None,'providers':auth.providers()}
            if method=='POST' and path in ('/api/auth/register','/api/auth/login'):
                data=self._body();auth.rate('ip:'+self._client_ip(),30);auth.rate('account:'+str(data.get('email','')).lower(),10)
                if path.endswith('register'):return auth.start_registration(data)
                user=auth.login(data)
                return self._login(user) if user['email_verified'] else auth.begin_verification(user)
            if method=='POST' and path in ('/api/auth/forgot-password','/api/auth/reset-password'):
                auth.rate('reset-ip:'+self._client_ip(),10,3600)
                data=self._body()
                return auth.request_password_reset(data) if path.endswith('forgot-password') else auth.reset_password(data)
            if method=='POST' and path=='/api/auth/verify':
                auth.rate('verify-ip:'+self._client_ip(),60);data=self._body();return self._login(auth.verify_email(data.get('challenge_id'),data.get('code')))
            if method=='POST' and path=='/api/auth/resend':
                auth.rate('resend-ip:'+self._client_ip(),10,3600);return auth.resend_verification(self._body().get('challenge_id'))
            if method=='POST' and path=='/api/auth/logout-all':
                self._body();auth.logout_all(self.user['id']);self._cookie('sm_session','',0);return {'ok':True}
            if method=='POST' and path=='/api/auth/logout':
                self._body();auth.logout(self.session_token);self._cookie('sm_session','',0);return {'ok':True}
            raise KeyError(path)

        def _route(self, method, path):
            db=getattr(self,'workspace_db',None) or original_db
            ai=AIClient(db) if auth else original_ai
            service=SupplierService(db,ai) if auth else original_service
            billing=BillingService(db);access=AccessService(db);purchases=PurchaseService(db)
            if auth and path.startswith('/api/admin/'):
                if self.user['role']!='admin':raise Forbidden('Нужны права администратора.')
                if method=='GET' and path=='/api/admin/state':return auth.admin_state()
                admin_match=re.fullmatch(r'/api/admin/users/([a-f0-9]{32})',path)
                if method=='PATCH' and admin_match:return auth.update_user(self.user,admin_match.group(1),self._body())
                raise KeyError(path)

            if path.startswith('/api/procurement'):
                procurement=ProcurementService(db)
                if path=='/api/procurement' and method=='GET':return procurement.state()
                if path=='/api/procurement/compare' and method=='POST':return procurement.compare(self._body())
                if path=='/api/procurement/projects' and method=='POST':return procurement.project(self._body())
                if path=='/api/procurement/searches' and method=='POST':return procurement.save_search(self._body())
                route=re.fullmatch(r'/api/procurement/(projects|searches|orders|notifications)/([a-f0-9-]{32,36})(/links|/run)?',path)
                if route:
                    kind,identifier,action=route.groups()
                    if kind=='projects':
                        if action=='/links' and method=='POST':return procurement.link(identifier,self._body())
                        if action is None and method=='PATCH':return procurement.project(self._body(),identifier)
                        if action is None and method=='DELETE':return procurement.delete_project(identifier)
                    if kind=='orders' and action is None:
                        if method=='GET':return procurement.order(identifier)
                        if method=='PATCH':return procurement.order(identifier,self._body())
                    if kind=='searches':
                        if action=='/run' and method=='POST':self._body();return procurement.run_search(identifier)
                        if action is None and method=='PATCH':return procurement.search_action(identifier,self._body())
                        if action is None and method=='DELETE':return procurement.search_action(identifier)
                    if kind=='notifications' and action is None and method=='PATCH':self._body();return procurement.read_alert(identifier)
                raise KeyError(path)

            if path.startswith('/api/catalog'):
                catalog=CatalogService(db)
                if method=='GET' and path=='/api/catalog':
                    params=parse_qs(urlsplit(self.path).query,keep_blank_values=True);sid=params.get('supplier_id',[None])[0]
                    if sid is not None and (not sid.isdigit() or len(sid)>12):raise ValueError('Некорректный поставщик.')
                    return {'items':catalog.list(int(sid) if sid is not None else None)}
                if method=='POST' and path=='/api/catalog':return catalog.save(self._body())
                if method=='POST' and path=='/api/catalog/import':
                    data=self._body();return catalog.import_price_list(data,commit=True)
                if method=='POST' and path=='/api/catalog/preview':return catalog.import_price_list(self._body())
                if method=='POST' and path=='/api/catalog/discover':
                    data=self._body()
                    with access.ai_request():return catalog.discover(data.get('supplier_id'),self._text(data,'query',250))
                item_match=re.fullmatch(r'/api/catalog/([a-f0-9]{32})',path)
                if item_match and method=='PATCH':return catalog.save(self._body(),item_match.group(1))
                if item_match and method=='DELETE':catalog.delete(item_match.group(1));return {'ok':True}
                raise KeyError(path)
            if path.startswith('/api/cart'):
                cart=CartService(db)
                if method=='GET' and path=='/api/cart':return cart.view()
                if method=='POST' and path=='/api/cart':
                    saved=cart.save(self._body());return cart.view(saved)
                if method=='POST' and path=='/api/cart/optimize':self._body();return cart.optimize()
                if method=='POST' and path=='/api/cart/checkout':
                    try:return cart.checkout(self._body())
                    finally:
                        if auth:auth.index_payments(self.user['id'],db)
                raise KeyError(path)

            match = re.fullmatch(r"/api/suppliers/(\d+)(/invite)?", path)
            if method == "GET" and path == "/api/state":
                provider_settings = {}
                for provider, config in PROVIDERS.items():
                    provider_settings[provider] = {**config, "configured_model": db.get_setting("model_" + provider) or (ai.model if provider == ai.provider else config["model"])}
                return {"has_key": bool(ai.key), "ai_ready": ai.ready, "local_ready": ai.local_ready,
                        "provider": ai.provider, "model": ai.model,
                        "custom_endpoint": db.get_setting("custom_endpoint"), "custom_format": db.get_setting("custom_format", "openai"),
                        "supports_web": ai.supports_web, "providers": provider_settings,
                        "has_search_key": bool(service.web.key), "web_available": service.web.available,
                        "local_setup": local_ai.status() if local_ai else {"phase": "external", "progress": 0},
                        "suppliers": db.list_suppliers(), "billing": billing.state(),
                        "access": access.state(), "purchases": db.list_purchases()}
            if method == "POST" and path == "/api/settings":
                data = self._body()
                provider = self._text(data, "provider", 30) or ai.provider
                if provider not in PROVIDERS:
                    raise ValueError("Неизвестный AI-провайдер.")
                key, model = self._text(data, "key", 1000), self._text(data, "model", 160)
                search_key = self._text(data, "search_key", 1000)
                endpoint = self._text(data, "endpoint", 2000)
                api_format = self._text(data, "api_format", 20) or "openai"
                if api_format not in ("openai", "anthropic"):
                    raise ValueError("Выберите формат совместимого API.")
                if provider == "custom":
                    validate_endpoint(endpoint)
                if model and not re.fullmatch(r"[A-Za-z0-9_./:@-]+", model):
                    raise ValueError("Некорректное имя модели.")
                db.set_setting("provider", provider)
                if data.get("clear_key") is True:
                    db.set_setting("key_" + provider, "")
                    if provider == "gemini":
                        db.set_setting("api_key", "")
                elif key:
                    db.set_setting("key_" + provider, key)
                db.set_setting("model_" + provider, model or PROVIDERS[provider]["model"])
                if provider == "custom":
                    db.set_setting("custom_endpoint", endpoint)
                    db.set_setting("custom_format", api_format)
                if data.get("clear_search_key") is True:
                    db.set_setting("tavily_key", "")
                elif search_key:
                    db.set_setting("tavily_key", search_key)
                return {"ok": True}
            if method == "POST" and path == "/api/ai/test":
                self._body()
                ai.generate("Ответь одним словом: подключено", json_mode=False)
                return {"ok": True}
            if method == "POST" and path == "/api/search":
                data = self._body()
                web = data.get("web", False)
                if not isinstance(web, bool):
                    raise ValueError("Неверный режим веб-поиска.")
                trade_format = self._text(data, "trade_format", 3) or "B2B"
                if trade_format not in ("B2B", "B2C", "C2C"):
                    raise ValueError("Выберите формат B2B, B2C или C2C.")
                page=data.get('page',1)
                if not isinstance(page,int) or isinstance(page,bool) or not 1<=page<=5:raise ValueError('Страница поиска должна быть от 1 до 5.')
                depth=self._text(data,'depth',12) or 'wide'
                if depth not in ('wide','quick'):raise ValueError('Неизвестная глубина поиска.')
                params = dict(page=page,depth=depth,city=self._text(data, "city", 200), product=self._text(data, "product", 500), need=self._text(data, "need", 5000), web=web,
                    country=self._text(data, "country", 200), category=self._text(data, "category", 200),
                    purpose=self._text(data, "purpose", 200), quantity=self._text(data, "quantity", 200), trade_format=trade_format)
                if not params["city"] or not params["product"]:
                    raise ValueError("Укажите товар и город доставки.")
                with access.ai_request():
                    result = service.search_with_sources(**params)
                db.add_history("search", params["product"] + " · " + params["city"], result)
                return result
            if method == "POST" and path == "/api/suppliers":
                sid = db.add_supplier(self._supplier(self._body()), limit=access.state()["limits"]["suppliers"])
                return {"id": sid, "duplicate": sid is None}
            if method == "POST" and path == "/api/rank":
                requirements = self._text(self._body(), "requirements")
                if not db.list_suppliers():
                    return service.rank(requirements)
                with access.ai_request():
                    result = service.rank(requirements)
                db.add_history("rank", requirements or "Сравнение предложений", result)
                return result
            if method == "GET" and path == "/api/history":
                if not access.state()["limits"]["history"]:
                    raise ValueError("История доступна в пробной версии и на тарифе Профи.")
                return db.list_history()
            if method == "POST" and path == "/api/calculate":
                return calculate_cost(self._body())
            if method == "POST" and path == "/api/purchases":
                try:return purchases.create(self._body())
                finally:
                    if auth:auth.index_payments(self.user["id"],db)
            if method == "POST" and path == "/api/billing/checkout":
                try:return billing.checkout(self._body())
                finally:
                    if auth:auth.index_payments(self.user["id"],db)
            if method == "POST" and path == "/api/payments/webhook":
                return YooKassaGateway(db).webhook(self._body())
            if match:
                sid = int(match.group(1))
                if not db.get_supplier(sid):
                    raise KeyError(sid)
                if match.group(2) and method == "POST":
                    self._body()
                    with access.ai_request():
                        return {"text": service.invite_text(sid)}
                if not match.group(2) and method == "PATCH":
                    db.update_supplier(sid, self._supplier(self._body(), partial=True))
                    return {"ok": True}
                if not match.group(2) and method == "DELETE":
                    db.delete_supplier(sid)
                    return {"ok": True}
            raise KeyError(path)

        def _handle(self, method):
            self.response_headers=[]
            path = self.path.split("?")[0]
            # Block browser requests originating on another website, including DNS rebinding.
            host = self.headers.get("Host", "")
            hostname = urlsplit("//" + host).hostname
            local_host = self.server.server_address[0]
            allowed = {"localhost", "127.0.0.1", "::1", local_host}
            allowed.update(filter(None, os.environ.get("SUPPLY_MATCH_PUBLIC_HOST", "").split(",")))
            allowed.discard("0.0.0.0")
            if hostname not in allowed:
                return self._send(403, {"error": "Недопустимый адрес сервера."})
            origin = self.headers.get("Origin")
            trusted_origin = origin in os.environ.get("SUPPLY_MATCH_ALLOWED_ORIGINS", "").split(",") if origin else False
            callback=bool(re.fullmatch(r"/auth/(google|apple)/callback",path))
            if not callback and not trusted_origin and ((origin and origin not in ("http://" + host, "https://" + host)) or self.headers.get("Sec-Fetch-Site") == "cross-site"):
                return self._send(403, {"error": "Запрос с другого сайта запрещён."})
            if method == "OPTIONS":
                return self._send(200, {"ok": True}) if trusted_origin else self._send(403, {"error": "Источник не разрешён."})
            token = os.environ.get("SUPPLY_MATCH_TOKEN", "") if not auth else ""
            if path.startswith("/api/") and path != "/api/payments/webhook" and token and not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                return self._send(401, {"error": "Нужен токен доступа к рабочему пространству."})
            try:
                if auth:
                    cookies=self._cookies()
                    self.session_token=cookies['sm_session'].value if 'sm_session' in cookies else ''
                    bearer=self.headers.get('Authorization','')
                    if bearer.startswith('Session '):self.session_token=bearer[8:]
                    if path.startswith('/auth/'):
                        auth.rate('oauth:'+self.client_address[0],30)
                        match=re.fullmatch(r'/auth/(google|apple)/(start|callback)',path)
                        if not match:raise KeyError(path)
                        provider,action=match.groups()
                        if action=='start' and method=='GET':
                            url,browser=auth.oauth_start(provider);self._cookie('sm_oauth',browser,600,oauth=True)
                            self.response_headers.append(('Location',url));return self._send(302,raw=b'')
                        if action=='callback' and method in ('GET','POST'):
                            if method=='POST':
                                if self.headers.get_content_type()!='application/x-www-form-urlencoded':raise AuthError('Некорректный callback.')
                                size=int(self.headers.get('Content-Length','0'))
                                if not 0<size<20000:raise AuthError('Некорректный callback.')
                                params=parse_qs(self.rfile.read(size).decode())
                            else:params=parse_qs(urlsplit(self.path).query)
                            data={k:v[0] for k,v in params.items()}
                            browser=cookies['sm_oauth'].value if 'sm_oauth' in cookies else ''
                            user=auth.oauth_finish(provider,data,browser);self._login(user);self._cookie('sm_oauth','',0,oauth=True)
                            self.response_headers.append(('Location','/#overview'));return self._send(303,raw=b'')
                        raise KeyError(path)
                    if path=='/api/payments/webhook' and method=='POST':
                        auth.rate('webhook:'+self.client_address[0],120,60);data=self._body()
                        obj=data.get('object',{});pid=str(obj.get('id','')) if isinstance(obj,dict) else ''
                        owner=auth.payment_owner(pid)
                        if not owner:return self._send(200,{'ok':True})
                        return self._send(200,YooKassaGateway(auth.workspace(owner)).webhook(data))
                    public=path in ('/api/auth/forgot-password','/api/auth/reset-password','/api/auth/me','/api/auth/login','/api/auth/register','/api/auth/verify','/api/auth/resend')
                    hook=re.fullmatch(r'/api/payments/webhook/([a-f0-9]{32})',path)
                    if path.startswith('/api/') and not public:
                        if hook and method=='POST':
                            auth.rate('webhook:'+self.client_address[0],120,60)
                            workspace=auth.workdir/(hook.group(1)+'.enc')
                            if not auth.workspace_exists(hook.group(1)):raise KeyError(path)
                            return self._send(200,YooKassaGateway(auth.workspace(hook.group(1))).webhook(self._body()))
                        self.user,csrf=auth.authenticate(self.session_token)
                        if method!='GET' and not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),csrf):raise Forbidden('Проверка безопасности запроса не пройдена. Обновите страницу.')
                        self.workspace_db=auth.workspace(self.user['id'])
                    if path.startswith('/api/auth/'):return self._send(200,self._auth_route(method,path))
                static = {"/": "index.html", "/index.html": "index.html", "/pricing": "index.html",
                          "/checkout": "index.html", "/settings": "index.html",
                          "/calculator": "index.html", "/purchases": "index.html", "/appearance": "index.html",
                          "/manifest.webmanifest": "manifest.webmanifest", "/sw.js": "sw.js", "/icon.svg": "icon.svg",
                          "/icon-192.png": "icon-192.png", "/icon-512.png": "icon-512.png",
                          "/procurement.js": "procurement.js", "/cart.js": "cart.js", "/account.js": "account.js", "/theme-utils.js": "theme-utils.js", "/ambient.js": "ambient.js", "/app.js": "app.js", "/styles.css": "styles.css"}
                if method == "GET" and path in static:
                    filename = static[path]
                    types = {"html": "text/html; charset=utf-8", "css": "text/css; charset=utf-8", "js": "text/javascript; charset=utf-8", "svg": "image/svg+xml", "png": "image/png", "webmanifest": "application/manifest+json"}
                    ctype = types[filename.rsplit(".", 1)[-1]]
                    return self._send(200, raw=(WEB_DIR / filename).read_bytes(), ctype=ctype)
                if method == "GET" and path == "/api/export":
                    output = io.StringIO(newline="")
                    writer = csv.writer(output, delimiter=";")
                    writer.writerow(FIELDS)
                    export_db=getattr(self,"workspace_db",None) or original_db
                    for supplier in export_db.list_suppliers():
                        # Neutralize spreadsheet formula injection in exported external data.
                        writer.writerow([("'" + str(supplier[k])) if str(supplier[k]).lstrip().startswith(("=", "+", "-", "@")) else supplier[k] for k in FIELDS])
                    return self._send(200, raw=output.getvalue().encode("utf-8-sig"), ctype="text/csv; charset=utf-8")
                request_db=getattr(self,"workspace_db",None) or original_db
                with request_db.settings_snapshot():result=self._route(method,path)
                self._send(200,result)
            except Unauthorized as error:
                self._send(401,{"error":str(error)})
            except Forbidden as error:
                self._send(403,{"error":str(error)})
            except (AIError, ValueError) as error:
                self._send(400, {"error": str(error)})
            except KeyError:
                self._send(404, {"error": "Не найдено"})
            except Exception:
                self._send(500, {"error": "Внутренняя ошибка сервера. Повторите запрос."})

        def do_GET(self): self._handle("GET")
        def do_POST(self): self._handle("POST")
        def do_PATCH(self): self._handle("PATCH")
        def do_DELETE(self): self._handle("DELETE")
        def do_OPTIONS(self): self._handle("OPTIONS")

    return Handler


def run(host="127.0.0.1", port=8000, open_browser=True, auto_setup=True):
    if os.environ.get('SUPPLY_MATCH_ENV')=='production':
        if host!='127.0.0.1' or not os.environ.get('SUPPLY_MATCH_PUBLIC_URL','').startswith('https://'):
            raise RuntimeError('Production: нужен HTTPS reverse proxy, SUPPLY_MATCH_PUBLIC_URL и привязка к 127.0.0.1.')
    local_ai = LocalAIProcess()
    auth=AuthService()
    db = Database(DATA_DIR / "system.enc", encryption_key=auth.key)
    ai = AIClient(db)
    server = ThreadingHTTPServer((host, port), make_handler(db, SupplierService(db, ai), ai, local_ai, auth=auth))
    from app.services.monitor_service import SearchMonitor
    monitor=SearchMonitor(auth)
    monitor.start()
    url = f"http://localhost:{port}"
    print(f"Supply Match запущен: {url}  (остановить: Ctrl+C)")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        local_ai.start(auto_setup=auto_setup)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        monitor.stop()
        server.server_close()
        local_ai.stop()
