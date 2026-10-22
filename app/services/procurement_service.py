"""Projects, order fulfilment, exact comparisons and persistent in-app alerts."""
import json
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from app.services.catalog_service import CatalogService
from app.services.cost_service import CURRENCIES, number, rounded

STAGES = {
    'agreement': 'Согласование', 'awaiting_payment': 'Ожидает оплаты',
    'paid': 'Оплата подтверждена провайдером', 'shipped': 'Отправлен',
    'received': 'Получен', 'completed': 'Завершён', 'cancelled': 'Отменён',
}
TRANSITIONS = {
    'agreement': {'awaiting_payment', 'shipped', 'cancelled'},
    'awaiting_payment': {'shipped', 'cancelled'}, 'paid': {'shipped'},
    'shipped': {'received'}, 'received': {'completed'},
    'completed': set(), 'cancelled': set(),
}

def text(data, key, maximum=500):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError('Некорректное поле: ' + key)
    return value.strip()

class ProcurementService:
    def __init__(self, db):
        self.db = db
        with db._conn() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,budget TEXT,currency TEXT NOT NULL,note TEXT NOT NULL,created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS project_links(project_id TEXT NOT NULL,kind TEXT NOT NULL,entity_id TEXT NOT NULL,PRIMARY KEY(project_id,kind,entity_id));
                CREATE TABLE IF NOT EXISTS fulfilment(order_id TEXT PRIMARY KEY,stage TEXT NOT NULL,tracking TEXT NOT NULL DEFAULT '',carrier TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS order_events(id INTEGER PRIMARY KEY,order_id TEXT NOT NULL,stage TEXT NOT NULL,note TEXT NOT NULL,created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS saved_searches(id TEXT PRIMARY KEY,name TEXT NOT NULL,params TEXT NOT NULL,enabled INTEGER NOT NULL,next_run INTEGER NOT NULL,last_run INTEGER NOT NULL DEFAULT 0,seen TEXT NOT NULL DEFAULT '[]',error TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,title TEXT NOT NULL,payload TEXT NOT NULL,created_at INTEGER NOT NULL,is_read INTEGER NOT NULL DEFAULT 0);
            ''')

    @staticmethod
    def notify(c, title, payload):
        c.execute('INSERT INTO notifications VALUES(?,?,?,?,0)',
                  (uuid.uuid4().hex, title, json.dumps(payload, ensure_ascii=False), int(time.time())))
        c.execute('DELETE FROM notifications WHERE id NOT IN (SELECT id FROM notifications ORDER BY created_at DESC,rowid DESC LIMIT 200)')

    def state(self):
        with self.db._conn() as c:
            projects = [dict(r) for r in c.execute('SELECT * FROM projects ORDER BY created_at DESC')]
            for p in projects:
                p['links'] = [dict(r) for r in c.execute('SELECT kind,entity_id FROM project_links WHERE project_id=?', (p['id'],))]
                orders = c.execute("SELECT p.amount,p.currency FROM purchases p JOIN project_links l ON l.entity_id=p.id AND l.kind='order' WHERE l.project_id=?", (p['id'],)).fetchall()
                p['committed'] = rounded(sum((Decimal(r['amount']) for r in orders if r['currency'] == p['currency']), Decimal(0)))
                p['other_currencies'] = sorted({r['currency'] for r in orders if r['currency'] != p['currency']})
                p['remaining'] = rounded(Decimal(p['budget']) - Decimal(p['committed'])) if p['budget'] is not None else None
            searches = [dict(r) for r in c.execute('SELECT id,name,params,enabled,next_run,last_run,error FROM saved_searches ORDER BY rowid DESC')]
            for s in searches:s['params'] = json.loads(s['params'])
            alerts = [{**dict(r), 'payload': json.loads(r['payload'])} for r in c.execute('SELECT * FROM notifications ORDER BY created_at DESC,rowid DESC')]
        return {'projects': projects, 'searches': searches, 'notifications': alerts, 'stages': STAGES}

    def project(self, data, project_id=None):
        name = text(data, 'name', 120)
        if not name:raise ValueError('Укажите название проекта.')
        currency = data.get('currency', 'RUB')
        if currency not in CURRENCIES:raise ValueError('Неизвестная валюта.')
        budget = None if data.get('budget') in ('', None) else rounded(number(data['budget'], 'Бюджет'))
        note = text(data, 'note', 2000)
        with self.db._conn() as c:
            if project_id:
                if not c.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():raise ValueError('Проект не найден.')
                c.execute('UPDATE projects SET name=?,budget=?,currency=?,note=? WHERE id=?', (name,budget,currency,note,project_id))
            else:
                if c.execute('SELECT COUNT(*) FROM projects').fetchone()[0] >= 100:raise ValueError('Лимит — 100 проектов.')
                project_id = uuid.uuid4().hex
                c.execute('INSERT INTO projects VALUES(?,?,?,?,?,?)', (project_id,name,budget,currency,note,int(time.time())))
        return {'id': project_id}

    def link(self, project_id, data):
        kind = data.get('kind'); entity = str(data.get('entity_id', ''))
        tables = {'supplier': ('suppliers', 'id'), 'item': ('catalog_items', 'id'), 'order': ('purchases', 'id')}
        if kind not in tables:raise ValueError('Неизвестный тип связи.')
        # Initialize optional catalog table before querying it.
        if kind == 'item':CatalogService(self.db)
        with self.db._conn() as c:
            if not c.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():raise ValueError('Проект не найден.')
            table, key = tables[kind]
            if not c.execute(f'SELECT 1 FROM {table} WHERE {key}=?', (entity,)).fetchone():raise ValueError('Объект не найден в вашем пространстве.')
            if data.get('remove') is True:
                c.execute('DELETE FROM project_links WHERE project_id=? AND kind=? AND entity_id=?', (project_id,kind,entity))
            else:c.execute('INSERT OR IGNORE INTO project_links VALUES(?,?,?)', (project_id,kind,entity))
        return {'ok': True}

    def delete_project(self, project_id):
        with self.db._conn() as c:
            c.execute('DELETE FROM project_links WHERE project_id=?', (project_id,))
            c.execute('DELETE FROM projects WHERE id=?', (project_id,))
        return {'ok': True}

    def order(self, order_id, data=None):
        with self.db._conn() as c:
            order = c.execute('SELECT * FROM purchases WHERE id=?', (order_id,)).fetchone()
            if not order:raise ValueError('Заказ не найден.')
            record = c.execute('SELECT * FROM fulfilment WHERE order_id=?', (order_id,)).fetchone()
            current = dict(record) if record else dict(order_id=order_id, stage='agreement',tracking='',carrier='',revision=0)
            # Payment truth is owned exclusively by the payment adapter.
            if order['status'] == 'paid' and current['stage'] in ('agreement','awaiting_payment'):
                current['stage'] = 'paid'
            if data is not None:
                if data.get('revision') != current['revision']:raise ValueError('Заказ изменился. Обновите карточку.')
                stage = data.get('stage', current['stage'])
                if stage != current['stage'] and stage not in TRANSITIONS[current['stage']]:raise ValueError('Недопустимый переход статуса. Оплату подтверждает только провайдер.')
                tracking = text(data, 'tracking', 200);carrier = text(data, 'carrier', 120);note = text(data, 'note', 2000)
                c.execute('INSERT INTO fulfilment VALUES(?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET stage=excluded.stage,tracking=excluded.tracking,carrier=excluded.carrier,revision=excluded.revision',
                          (order_id,stage,tracking,carrier,current['revision']+1))
                c.execute('INSERT INTO order_events(order_id,stage,note,created_at) VALUES(?,?,?,?)', (order_id,stage,note,int(time.time())))
                self.notify(c, 'Заказ: ' + STAGES[stage], {'order_id': order_id})
                current.update(stage=stage,tracking=tracking,carrier=carrier,revision=current['revision']+1)
            current['events'] = [dict(r) for r in c.execute('SELECT * FROM order_events WHERE order_id=? ORDER BY id DESC LIMIT 100', (order_id,))]
            current['allowed'] = sorted(TRANSITIONS[current['stage']])
        return current

    def compare(self, data):
        key = text(data, 'comparison_key', 200).casefold()
        if not key:raise ValueError('Укажите ключ идентичной модели и комплектации.')
        qty = number(data.get('quantity'), 'Количество', True)
        from app.services.cart_service import CartService
        cart = CartService(self.db);base = cart.state()
        # Reuse validated cart quotes, including known fees and currency rates.
        base = {**base, **{k: data[k] for k in ('fees','rates','base_currency','deadline') if k in data}}
        rows = []
        for item in cart.catalog.list():
            if item['comparison_key'].strip().casefold() != key:continue
            candidate = cart.validate({**base, 'lines': [{'item_id': item['id'], 'quantity': str(qty)}]})
            quote = cart.quote({**candidate, 'revision': 0})
            rows.append({'item': item, 'total': quote['total'], 'currency': quote['base_currency'], 'eligible': quote['ready'], 'errors': quote['errors'], 'arrival': quote['lines'][0]['arrival']})
        rows.sort(key=lambda r: (not r['eligible'], r['total'] is None, Decimal(r['total'] or 0)))
        return {'offers': rows, 'quantity': str(qty), 'note': 'Расходы и курсы берутся из корзины. Одинаковый ключ должен обозначать одинаковую комплектацию, состояние и единицу измерения.'}

    def save_search(self, data):
        name = text(data, 'name', 120);params = data.get('params')
        if not name or not isinstance(params, dict):raise ValueError('Укажите название и параметры поиска.')
        clean = {k: text(params,k,250) for k in ('product','country','city','category')}
        if not clean['product'] or not clean['city']:raise ValueError('Нужны товар и город доставки.')
        clean['trade_format'] = params.get('trade_format','B2B')
        if clean['trade_format'] not in ('B2B','B2C','C2C'):raise ValueError('Неизвестный формат сделки.')
        with self.db._conn() as c:
            if c.execute('SELECT COUNT(*) FROM saved_searches').fetchone()[0] >= 30:raise ValueError('Лимит — 30 сохранённых поисков.')
            sid = uuid.uuid4().hex
            c.execute('INSERT INTO saved_searches(id,name,params,enabled,next_run) VALUES(?,?,?,?,?)', (sid,name,json.dumps(clean,ensure_ascii=False),int(data.get('enabled') is True),int(time.time())+86400))
        return {'id': sid}

    def run_search(self, sid):
        from app.services.search_service import WebSearchClient
        from app.services.access_service import AccessService
        # Claim before network access: prevents simultaneous manual/background runs.
        now = int(time.time())
        with self.db._conn() as c:
            row = c.execute('SELECT * FROM saved_searches WHERE id=?', (sid,)).fetchone()
            if not row:raise ValueError('Поиск не найден.')
            if row['last_run'] and now-row['last_run'] < 3600:raise ValueError('Повторная проверка доступна через час.')
            c.execute('UPDATE saved_searches SET last_run=?,next_run=? WHERE id=?', (now,now+86400,sid))
        try:
            with AccessService(self.db).ai_request():
                sources, failures = WebSearchClient(self.db).discover(**json.loads(row['params']),depth='quick')
                if failures:raise ValueError('Часть поисковых систем недоступна. Проверка не завершена; повторите позже.')
            seen = set(json.loads(row['seen']));fresh = [s for s in sources if s['url'] not in seen]
            with self.db._conn() as c:
                if not c.execute('SELECT 1 FROM saved_searches WHERE id=?',(sid,)).fetchone():return {'new':0}
                if fresh:self.notify(c, 'Новые предложения: ' + row['name'], {'search_id': sid, 'sources': fresh})
                combined = list(dict.fromkeys([s['url'] for s in sources] + json.loads(row['seen'])))[:2000]
                c.execute("UPDATE saved_searches SET seen=?,error='' WHERE id=?", (json.dumps(combined),sid))
            return {'new': len(fresh)}
        except Exception as error:
            with self.db._conn() as c:c.execute('UPDATE saved_searches SET error=? WHERE id=?', ('Поиск не выполнен. Проверьте подключение, лимиты и повторите позже.',sid))
            raise ValueError('Поиск не выполнен. Проверьте подключение, лимиты и повторите позже.') from error

    def search_action(self, sid, data=None):
        with self.db._conn() as c:
            if data is None:c.execute('DELETE FROM saved_searches WHERE id=?',(sid,))
            else:
                if not isinstance(data.get('enabled'),bool):raise ValueError('Укажите состояние мониторинга.')
                c.execute('UPDATE saved_searches SET enabled=? WHERE id=?',(int(data['enabled']),sid))
        return {'ok':True}

    def read_alert(self, nid):
        with self.db._conn() as c:c.execute('UPDATE notifications SET is_read=1 WHERE id=?',(nid,))
        return {'ok':True}
