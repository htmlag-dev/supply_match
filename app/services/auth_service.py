"""Accounts, revocable sessions, role checks, OAuth authorization-code flows."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import threading
from collections import OrderedDict
_PASSWORD_SLOTS=threading.BoundedSemaphore(2)
import urllib.request
from urllib.parse import urlencode
from pathlib import Path
from app.config import DATA_DIR
from app.database import Database
from app.services.mail_service import Mailer
from app.security import encrypted_connection,master_key

class AuthError(ValueError):pass
class Unauthorized(AuthError):pass
class Forbidden(AuthError):pass

def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def password_hash(password,salt=None,min_length=12):
    if not isinstance(password,str) or not min_length<=len(password)<=128:raise AuthError('Пароль должен содержать от 12 до 128 символов.')
    salt=salt or secrets.token_hex(16)
    if not _PASSWORD_SLOTS.acquire(blocking=False):raise AuthError('Сервис занят. Повторите вход через несколько секунд.')
    try:value=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=2**17,r=8,p=1,maxmem=256*1024*1024).hex()
    finally:_PASSWORD_SLOTS.release()
    return salt+'$'+value

def safe_user(row):return {k:row[k] for k in ('id','email','name','role','disabled','created_at','username','email_verified')}

class AuthService:
    def __init__(self,path=None,key=None):
        self.path=Path(path) if path else DATA_DIR/'accounts.enc';self.key=key or master_key()
        self.workdir=self.path.parent/'workspaces';self.mailer=Mailer();self._workspace_cache=OrderedDict();self._workspace_lock=threading.RLock()
        with self.conn() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,name TEXT NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL,disabled INTEGER NOT NULL DEFAULT 0,created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS password_resets(challenge TEXT PRIMARY KEY,user_id TEXT NOT NULL,code_mac TEXT NOT NULL,expires INTEGER NOT NULL,attempts INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS email_codes(challenge TEXT PRIMARY KEY,user_id TEXT NOT NULL,code_mac TEXT NOT NULL,expires INTEGER NOT NULL,attempts INTEGER NOT NULL,sent_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS payment_owners(payment_id TEXT PRIMARY KEY,user_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS identities(provider TEXT,subject TEXT,user_id TEXT,PRIMARY KEY(provider,subject));
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT,csrf TEXT,expires INTEGER,last_seen INTEGER);
            CREATE TABLE IF NOT EXISTS attempts(bucket TEXT PRIMARY KEY,count INTEGER,until INTEGER);
            CREATE TABLE IF NOT EXISTS oauth(state TEXT PRIMARY KEY,browser TEXT,provider TEXT,nonce TEXT,verifier TEXT,expires INTEGER);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor TEXT,action TEXT,target TEXT,created_at INTEGER);''')
        with self.conn() as c:
            if 'username' not in {r['name'] for r in c.execute('PRAGMA table_info(users)')}:
                c.execute("ALTER TABLE users ADD COLUMN username TEXT")
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username ON users(username)")
            if 'email_verified' not in {r['name'] for r in c.execute('PRAGMA table_info(users)')}:
                c.execute('ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0')
                c.execute("UPDATE users SET email_verified=1 WHERE role='admin'")
                c.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email_verified=0)")
            columns={r['name'] for r in c.execute('PRAGMA table_info(sessions)')}
            for field in ('ip_hash','device_hash'):
                if field not in columns:c.execute('ALTER TABLE sessions ADD COLUMN '+field+" TEXT NOT NULL DEFAULT ''")
    def conn(self):
        if os.environ.get('SUPPLY_MATCH_DATABASE_URL'):
            from app.postgres import connection
            return connection(self.path)
        return encrypted_connection(self.path,self.key)
    def workspace_exists(self,user_id):
        if not re.fullmatch('[a-f0-9]{32}',user_id):return False
        path=self.workdir/(user_id+'.enc')
        if not os.environ.get('SUPPLY_MATCH_DATABASE_URL'):return path.exists()
        import psycopg
        from app.postgres import schema_for
        with psycopg.connect(os.environ['SUPPLY_MATCH_DATABASE_URL'],connect_timeout=10) as c:
            return bool(c.execute('SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s',(schema_for(path),'suppliers')).fetchone())
    def workspace(self,user_id):
        if not re.fullmatch('[a-f0-9]{32}',user_id):raise AuthError('Некорректный пользователь.')
        with self._workspace_lock:
            if user_id not in self._workspace_cache:
                self._workspace_cache[user_id]=Database(self.workdir/(user_id+'.enc'),encryption_key=self.key)
                if len(self._workspace_cache)>128:self._workspace_cache.popitem(last=False)
            self._workspace_cache.move_to_end(user_id)
            return self._workspace_cache[user_id]
    def rate(self,bucket,limit=10,window=900):
        now=int(time.time());bucket=digest(bucket)
        with self.conn() as c:
            c.execute('DELETE FROM attempts WHERE until<?',(now,))
            row=c.execute('SELECT * FROM attempts WHERE bucket=?',(bucket,)).fetchone()
            if row and row['count']>=limit:raise AuthError('Слишком много попыток. Повторите позже.')
            c.execute('INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1',(bucket,now+window))
    def register(self,data,role='user',email_verified=True):
        email=str(data.get('email','')).strip().lower();name=str(data.get('name','')).strip();username=str(data.get('username','')).strip().lower()
        if username and not re.fullmatch(r'[a-z0-9_]{3,32}',username):raise AuthError('Логин: 3–32 латинские буквы, цифры или _.')
        if not re.fullmatch(r'[^\s@]{1,100}@[^\s@.]+(?:\.[^\s@.]+)+',email) or len(email)>254:raise AuthError('Введите корректный email.')
        if not 1<=len(name)<=80:raise AuthError('Имя должно содержать от 1 до 80 символов.')
        hashed=password_hash(data.get('password'));uid=secrets.token_hex(16)
        with self.conn() as c:
            if c.execute('SELECT 1 FROM users WHERE email=? OR username=?',(email,username or None)).fetchone():raise AuthError('Регистрация недоступна для этого email. Попробуйте войти.')
            c.execute('INSERT INTO users(id,email,name,password,role,disabled,created_at) VALUES(?,?,?,?,?,0,?)',(uid,email,name,hashed,role,int(time.time())))
            c.execute('UPDATE users SET email_verified=? WHERE id=?',(int(email_verified),uid))
            if username:c.execute('UPDATE users SET username=? WHERE id=?',(username,uid))
            row=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            return safe_user(row)
    def start_registration(self,data):
        if not self.mailer.configured():raise AuthError('Почтовый сервис не настроен. Владелец должен подключить SMTP; вход без проверки почты не разрешён.')
        user=self.register(data,email_verified=False)
        return self.begin_verification(user)
    def _code_mac(self,challenge,code):return hmac.new(self.key,(challenge+':'+code).encode(),hashlib.sha256).hexdigest()
    def begin_verification(self,user):
        if user['email_verified']:raise AuthError('Почта уже подтверждена.')
        if not self.mailer.configured():raise AuthError('Почтовый сервис ещё не настроен владельцем сервера.')
        self.rate('email-send:'+user['id'],5,3600)
        now=int(time.time());challenge=secrets.token_urlsafe(32);code=f'{secrets.randbelow(1000000):06d}'
        with self.conn() as c:
            previous=c.execute('SELECT sent_at FROM email_codes WHERE user_id=? ORDER BY sent_at DESC LIMIT 1',(user['id'],)).fetchone()
            if previous and now-previous['sent_at']<60:raise AuthError('Повторный код можно запросить через минуту.')
            c.execute('DELETE FROM email_codes WHERE user_id=? OR expires<?',(user['id'],now))
            c.execute('INSERT INTO email_codes VALUES(?,?,?,?,0,?)',(digest(challenge),user['id'],self._code_mac(challenge,code),now+600,now))
        try:self.mailer.send_code(user['email'],code)
        except ValueError:
            with self.conn() as c:c.execute('DELETE FROM email_codes WHERE challenge=?',(digest(challenge),))
            raise
        return {'verification_required':True,'challenge_id':challenge,'email':user['email'],'expires_in':600,'resend_after':60,'message':'Код отправлен на вашу почту. Проверьте входящие и спам.'}
    def resend_verification(self,challenge):
        if not isinstance(challenge,str) or len(challenge)>100:raise AuthError('Начните регистрацию или вход заново.')
        with self.conn() as c:row=c.execute('SELECT u.* FROM email_codes e JOIN users u ON u.id=e.user_id WHERE e.challenge=? AND u.email_verified=0 AND u.disabled=0',(digest(challenge),)).fetchone()
        if not row:raise AuthError('Начните регистрацию или вход заново.')
        return self.begin_verification(safe_user(row))
    def verify_email(self,challenge,code):
        if not isinstance(challenge,str) or len(challenge)>100 or not isinstance(code,str) or not re.fullmatch(r'\d{6}',code):raise AuthError('Введите шестизначный код из письма.')
        error='';user=None
        with self.conn() as c:
            row=c.execute('SELECT * FROM email_codes WHERE challenge=?',(digest(challenge),)).fetchone()
            if not row or row['expires']<=time.time():error='Код истёк. Запросите новый.'
            elif row['attempts']>=5:error='Лимит попыток исчерпан. Запросите новый код.'
            elif not hmac.compare_digest(row['code_mac'],self._code_mac(challenge,code)):
                c.execute('UPDATE email_codes SET attempts=attempts+1 WHERE challenge=?',(digest(challenge),));error='Неверный код.'
            else:
                account=c.execute('SELECT * FROM users WHERE id=? AND disabled=0',(row['user_id'],)).fetchone()
                if not account:error='Аккаунт недоступен.'
                else:
                    c.execute('UPDATE users SET email_verified=1 WHERE id=?',(row['user_id'],))
                    c.execute('DELETE FROM email_codes WHERE user_id=?',(row['user_id'],))
                    user=safe_user(c.execute('SELECT * FROM users WHERE id=?',(row['user_id'],)).fetchone())
        if error:raise AuthError(error)
        return user
    def request_password_reset(self,data):
        email=str(data.get('email','')).strip().lower()
        if len(email)>254:raise AuthError('Некорректный email.')
        if not self.mailer.configured():raise AuthError('Почтовый сервис не настроен владельцем сервера.')
        self.rate('password-reset:'+email,3,3600)
        challenge=secrets.token_urlsafe(32);code=f'{secrets.randbelow(1000000):06d}';now=int(time.time())
        with self.conn() as c:
            user=c.execute('SELECT * FROM users WHERE email=? AND disabled=0 AND email_verified=1',(email,)).fetchone()
            c.execute('DELETE FROM password_resets WHERE expires<?',(now,))
            if user:
                c.execute('DELETE FROM password_resets WHERE user_id=?',(user['id'],))
                c.execute('INSERT INTO password_resets VALUES(?,?,?,?,0)',(digest(challenge),user['id'],self._code_mac(challenge,code),now+600))
        if user:
            try:self.mailer.send_code(email,code,reset=True)
            except ValueError:
                with self.conn() as c:c.execute('DELETE FROM password_resets WHERE challenge=?',(digest(challenge),))
                # Same public response for existing and unknown accounts.
        return {'challenge_id':challenge,'message':'Если аккаунт доступен, код восстановления отправлен на почту. Код действует 10 минут.'}

    def reset_password(self,data):
        challenge=data.get('challenge_id');code=data.get('code');password=data.get('password')
        if not isinstance(challenge,str) or len(challenge)>100 or not isinstance(code,str) or not re.fullmatch(r'\d{6}',code):raise AuthError('Введите код из письма.')
        error='Код недействителен или истёк.'
        # Failed attempts must commit, rather than roll back on an exception.
        with self.conn() as c:
            row=c.execute('SELECT * FROM password_resets WHERE challenge=?',(digest(challenge),)).fetchone()
            if row and row['expires']>time.time() and row['attempts']<5:
                if hmac.compare_digest(row['code_mac'],self._code_mac(challenge,code)):
                    hashed=password_hash(password)
                    user=c.execute('SELECT id FROM users WHERE id=? AND disabled=0 AND email_verified=1',(row['user_id'],)).fetchone()
                    if user:
                        c.execute('UPDATE users SET password=? WHERE id=?',(hashed,user['id']))
                        c.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
                        c.execute('DELETE FROM password_resets WHERE user_id=?',(user['id'],))
                        c.execute("INSERT INTO audit(actor,action,target,created_at) VALUES(?,'password_reset',?,?)",(user['id'],user['id'],int(time.time())))
                        error=''
                else:c.execute('UPDATE password_resets SET attempts=attempts+1 WHERE challenge=?',(digest(challenge),))
        if error:raise AuthError(error)
        return {'ok':True,'message':'Пароль изменён. Войдите заново на всех устройствах.'}

    def login(self,data):
        email=str(data.get('email','')).lower().strip();password=data.get('password','')
        with self.conn() as c:row=c.execute('SELECT * FROM users WHERE email=? OR username=?',(email,email)).fetchone()
        expected=row['password'] if row and row['password'] else '0'*32+'$'+'0'*128
        try:valid=hmac.compare_digest(password_hash(password,expected.split('$')[0],min_length=1),expected)
        except AuthError:valid=False
        if not valid or not row or row['disabled']:raise Unauthorized('Неверный email или пароль.')
        return safe_user(row)
    def session(self,user,ip="",device=""):
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32);now=int(time.time())
        with self.conn() as c:
            valid=c.execute('SELECT 1 FROM users WHERE id=? AND email_verified=1 AND disabled=0',(user['id'],)).fetchone()
            if not valid:raise Unauthorized('Подтвердите почту перед входом.')
            c.execute('DELETE FROM sessions WHERE expires<?',(now,))
            c.execute('INSERT INTO sessions(token,user_id,csrf,expires,last_seen,ip_hash,device_hash) VALUES(?,?,?,?,?,?,?)',(digest(token),user['id'],csrf,now+30*86400,now,hmac.new(self.key,ip.encode(),hashlib.sha256).hexdigest(),hmac.new(self.key,device[:512].encode(),hashlib.sha256).hexdigest()))
            c.execute('INSERT INTO audit(actor,action,target,created_at) VALUES(?,?,?,?)',(user['id'],'login',user['id'],now))
        return token,csrf
    def authenticate(self,token):
        if not token:raise Unauthorized('Войдите в аккаунт.')
        now=int(time.time())
        with self.conn() as c:
            row=c.execute('SELECT u.*,s.csrf,s.last_seen FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.disabled=0 AND u.email_verified=1',(digest(token),now)).fetchone()
            if not row:raise Unauthorized('Сессия закончилась. Войдите снова.')
            if now-row['last_seen']>=300:c.execute('UPDATE sessions SET last_seen=? WHERE token=?',(now,digest(token)))
            return safe_user(row),row['csrf']
    def logout(self,token):
        with self.conn() as c:c.execute('DELETE FROM sessions WHERE token=?',(digest(token),))
    def logout_all(self,uid):
        with self.conn() as c:c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
    def admin_state(self):
        with self.conn() as c:
            users=[safe_user(r) for r in c.execute('SELECT * FROM users ORDER BY created_at DESC LIMIT 500')]
            audit=[dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 100')]
        orders=[];purchases=[];payments=[]
        for u in users:
            path=self.workdir/(u['id']+'.enc')
            if not path.exists():continue
            db=self.workspace(u['id'])
            orders.extend({**o,'user_id':u['id'],'user_email':u['email']} for o in db.list_orders())
            purchases.extend({**o,'user_id':u['id'],'user_email':u['email']} for o in db.list_purchases())
            with db._conn() as c:payments.extend({**dict(r),'user_id':u['id']} for r in c.execute('SELECT id,kind,order_id,amount,currency,status FROM payments'))
        return dict(users=users,orders=orders,purchases=purchases,payments=payments,audit=audit)
    def update_user(self,actor,uid,data):
        if actor['id']==uid:raise Forbidden('Собственный статус и роль меняются только через серверную консоль.')
        role=data.get('role');disabled=data.get('disabled')
        if role not in ('user','admin') or not isinstance(disabled,bool):raise AuthError('Некорректная роль или статус.')
        with self.conn() as c:
            if not c.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():raise AuthError('Пользователь не найден.')
            c.execute('UPDATE users SET role=?,disabled=? WHERE id=?',(role,int(disabled),uid))
            c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
            c.execute('INSERT INTO audit(actor,action,target,created_at) VALUES(?,?,?,?)',(actor['id'],'user:'+role+':'+str(disabled),uid,int(time.time())))
        return {'ok':True}
    def index_payments(self,user_id,db):
        with db._conn() as c:ids=[r['id'] for r in c.execute('SELECT id FROM payments')]
        if ids:
            with self.conn() as c:
                for pid in ids:c.execute('INSERT INTO payment_owners VALUES(?,?) ON CONFLICT(payment_id) DO NOTHING',(pid,user_id))
    def payment_owner(self,payment_id):
        with self.conn() as c:row=c.execute('SELECT user_id FROM payment_owners WHERE payment_id=?',(payment_id,)).fetchone()
        return row['user_id'] if row else None
    def oauth_config(self,provider):
        if provider not in ('google','apple'):raise AuthError('Неизвестный провайдер.')
        prefix='SUPPLY_MATCH_'+provider.upper()
        cfg={'id':os.environ.get(prefix+'_CLIENT_ID',''),'secret':os.environ.get(prefix+'_CLIENT_SECRET',''),'redirect':os.environ.get('SUPPLY_MATCH_PUBLIC_URL','').rstrip('/')+'/auth/'+provider+'/callback'}
        if not cfg['id'] or not cfg['secret'] or not cfg['redirect'].startswith('https://'):raise AuthError('Вход через '+provider.title()+' ещё не настроен владельцем сервера.')
        return cfg
    def providers(self):
        result={}
        for p in ('google','apple'):
            try:self.oauth_config(p);result[p]=True
            except AuthError:result[p]=False
        return result
    def oauth_start(self,provider):
        cfg=self.oauth_config(provider);state=secrets.token_urlsafe(32);browser=secrets.token_urlsafe(32);nonce=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
        with self.conn() as c:
            c.execute('DELETE FROM oauth WHERE expires<?',(int(time.time()),))
            c.execute('INSERT INTO oauth VALUES(?,?,?,?,?,?)',(digest(state),digest(browser),provider,nonce,verifier,int(time.time())+600))
        params=dict(client_id=cfg['id'],redirect_uri=cfg['redirect'],response_type='code',scope='openid email profile' if provider=='google' else 'name email',state=state,nonce=nonce)
        if provider=='google':params.update(code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),code_challenge_method='S256')
        else:params['response_mode']='form_post'
        return ('https://accounts.google.com/o/oauth2/v2/auth' if provider=='google' else 'https://appleid.apple.com/auth/authorize')+'?'+urlencode(params),browser
    def oauth_finish(self,provider,data,browser):
        import jwt
        cfg=self.oauth_config(provider);state=str(data.get('state',''))
        with self.conn() as c:
            row=c.execute('SELECT * FROM oauth WHERE state=?',(digest(state),)).fetchone()
            if not row or row['provider']!=provider or row['expires']<time.time() or not hmac.compare_digest(row['browser'],digest(browser)):raise Unauthorized('Проверка входа не пройдена. Начните вход заново.')
            c.execute('DELETE FROM oauth WHERE state=?',(digest(state),))
        params=dict(client_id=cfg['id'],client_secret=cfg['secret'],code=str(data.get('code','')),grant_type='authorization_code',redirect_uri=cfg['redirect'])
        if provider=='google':params['code_verifier']=row['verifier']
        try:
            endpoint='https://oauth2.googleapis.com/token' if provider=='google' else 'https://appleid.apple.com/auth/token'
            with urllib.request.urlopen(urllib.request.Request(endpoint,data=urlencode(params).encode(),headers={'Content-Type':'application/x-www-form-urlencoded'}),timeout=15) as response:result=json.load(response)
            jwks='https://www.googleapis.com/oauth2/v3/certs' if provider=='google' else 'https://appleid.apple.com/auth/keys'
            idtoken=result['id_token'];key=jwt.PyJWKClient(jwks,timeout=10).get_signing_key_from_jwt(idtoken).key
            claims=jwt.decode(idtoken,key,algorithms=['RS256'],audience=cfg['id'],issuer=['https://accounts.google.com','accounts.google.com'] if provider=='google' else 'https://appleid.apple.com',options={'require':['exp','iat','sub','aud','iss','nonce']})
            if not hmac.compare_digest(claims['nonce'],row['nonce']):raise ValueError()
            if claims.get('email_verified') not in (True,'true'):raise ValueError()
        except Exception:raise Unauthorized('Провайдер не подтвердил вход. Повторите попытку.') from None
        with self.conn() as c:
            existing=c.execute('SELECT u.* FROM identities i JOIN users u ON u.id=i.user_id WHERE i.provider=? AND i.subject=?',(provider,claims['sub'])).fetchone()
            if existing:
                if existing['disabled']:raise Unauthorized('Доступ к аккаунту ограничен.')
                c.execute('UPDATE users SET email_verified=1 WHERE id=?',(existing['id'],))
                return safe_user(c.execute('SELECT * FROM users WHERE id=?',(existing['id'],)).fetchone())
            email=str(claims.get('email','')).lower()
            if not email or c.execute('SELECT 1 FROM users WHERE email=?',(email,)).fetchone():raise AuthError('Этот email уже зарегистрирован. Войдите прежним способом. Автоматическое объединение аккаунтов отключено.')
            uid=secrets.token_hex(16)
            c.execute('INSERT INTO users(id,email,name,password,role,disabled,created_at) VALUES(?,?,?,?,?,0,?)',(uid,email,str(claims.get('name') or email.split('@')[0])[:80],'','user',int(time.time())))
            c.execute('UPDATE users SET email_verified=1 WHERE id=?',(uid,))
            c.execute('INSERT INTO identities VALUES(?,?,?)',(provider,claims['sub'],uid))
            return safe_user(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
