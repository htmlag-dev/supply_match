"""Multi-model cart, exact Decimal quotes and explicit equivalent-offer optimization."""
import hashlib
import itertools
import json
import re
import uuid
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from app.services.catalog_service import CatalogService
from app.services.cost_service import CURRENCIES,number,rounded
from app.services.payment_service import YooKassaGateway

class CartService:
    def __init__(self,db):
        self.db=db;self.catalog=CatalogService(db)
        with db._conn() as c:
            columns={r['name'] for r in c.execute('PRAGMA table_info(purchases)')}
            for field in ('items','placed_on','delivery_estimate','expense_breakdown'):
                if field not in columns:c.execute('ALTER TABLE purchases ADD COLUMN '+field+" TEXT NOT NULL DEFAULT ''")
    def state(self):
        raw=self.db.get_setting('cart_v1')
        return json.loads(raw) if raw else dict(revision=0,lines=[],fees={},rates={},base_currency='RUB',order_date=date.today().isoformat(),deadline='')
    def validate(self,data):
        if not isinstance(data,dict):raise ValueError('Некорректная корзина.')
        lines=data.get('lines',[])
        if not isinstance(lines,list) or len(lines)>30:raise ValueError('В корзине может быть до 30 разных позиций.')
        if any(not isinstance(line,dict) or not isinstance(line.get('item_id'),str) or not re.fullmatch('[a-f0-9]{32}',line['item_id']) for line in lines):raise ValueError('Некорректная позиция каталога.')
        ids=[line['item_id'] for line in lines]
        catalogue={p['id']:p for p in self.catalog.for_ids(ids)};merged={}
        for line in lines:
            if not isinstance(line,dict) or line.get('item_id') not in catalogue:raise ValueError('Позиция каталога удалена или недоступна.')
            qty=number(line.get('quantity'),'Количество',True);key=line['item_id'];merged[key]=number(merged.get(key,Decimal(0))+qty,'Количество',True)
        base=data.get('base_currency','RUB')
        if base not in CURRENCIES:raise ValueError('Неизвестная валюта расчёта.')
        rates=data.get('rates',{});fees=data.get('fees',{})
        if not isinstance(rates,dict) or not isinstance(fees,dict) or len(fees)>200:raise ValueError('Некорректные расходы или курсы.')
        clean_rates={}
        for cur,value in rates.items():
            if cur not in CURRENCIES:raise ValueError('Неизвестная валюта курса.')
            if value not in ('',None):clean_rates[cur]=str(number(value,'Курс валюты',True))
        clean_fees={}
        for key,value in fees.items():
            if not re.fullmatch(r'\d+:(?:RUB|CNY|USD|EUR|KZT|TRY|AED)',key) or not isinstance(value,dict):raise ValueError('Некорректные условия расходов.')
            clean_fees[key]={k:None if value.get(k) in ('',None) else rounded(number(value[k],k)) for k in ('shipping','taxes','other')}
        try:
            placed=date.fromisoformat(data.get('order_date') or date.today().isoformat());deadline=date.fromisoformat(data['deadline']) if data.get('deadline') else None
        except (ValueError,TypeError):raise ValueError('Укажите дату в формате ГГГГ-ММ-ДД.') from None
        if placed<date.today() or placed>date.today()+timedelta(days=1825):raise ValueError('Дата оформления должна быть от сегодня до пяти лет вперёд.')
        if deadline and deadline<placed:raise ValueError('Желаемая дата получения не может быть раньше оформления.')
        return dict(lines=[dict(item_id=k,quantity=str(q)) for k,q in merged.items()],fees=clean_fees,rates=clean_rates,base_currency=base,order_date=placed.isoformat(),deadline=deadline.isoformat() if deadline else '')
    def save(self,data):
        clean=self.validate(data)
        with self.db._conn() as c:
            row=c.execute("SELECT v FROM settings WHERE k='cart_v1'").fetchone();current=json.loads(row['v']) if row else {'revision':0}
            if data.get('revision')!=current['revision']:raise ValueError('Корзина изменилась в другой вкладке. Обновите её перед сохранением.')
            if {k:v for k,v in current.items() if k!='revision'}==clean:return current
            clean['revision']=current['revision']+1
            c.execute("INSERT INTO settings VALUES('cart_v1',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",(json.dumps(clean,ensure_ascii=False),))
        return clean
    def view(self,cart=None):
        cart=cart or self.state();return {'cart':cart,'quote':self.quote(cart),'catalog_groups':self.catalog.groups()}
    def quote(self,cart=None,catalog=None):
        cart=cart or self.state();items={p['id']:p for p in (catalog if catalog is not None else self.catalog.for_ids([x['item_id'] for x in cart['lines']]))};sellers={s['id']:s for s in self.db.list_suppliers()};groups={};errors=[];lines=[]
        placed=date.fromisoformat(cart['order_date']);deadline=date.fromisoformat(cart['deadline']) if cart.get('deadline') else None
        for line in cart['lines']:
            item=items.get(line['item_id'])
            if not item:errors.append('Позиция удалена из каталога.');continue
            qty=Decimal(line['quantity']);seller=sellers[item['supplier_id']];key=str(item['supplier_id'])+':'+item['currency'];price=Decimal(item['unit_price']) if item['unit_price'] is not None else None
            if item.get('moq') is not None and qty<Decimal(item['moq']):errors.append(item['name']+': меньше минимальной партии.')
            if item['availability']!='available':errors.append(item['name']+': '+('нет в наличии.' if item['availability']=='out' else 'нет информации о наличии.'))
            if item.get('stock_qty') is not None and qty>Decimal(item['stock_qty']):errors.append(item['name']+': недостаточный остаток.')
            if price is None:errors.append(item['name']+': нет информации о цене.')
            arrival=None
            if item['lead_min'] is not None and item['lead_max'] is not None:arrival={'from':(placed+timedelta(days=item['lead_min'])).isoformat(),'to':(placed+timedelta(days=item['lead_max'])).isoformat()}
            if deadline and (arrival is None or date.fromisoformat(arrival['to'])>deadline):errors.append(item['name']+': срок получения не подтверждён или позже желаемой даты.')
            total=qty*price if price is not None else None
            if total is not None and total>Decimal('1000000000000'):raise ValueError('Сумма позиции превышает допустимый предел.')
            if total is not None:total=Decimal(rounded(total))
            detail={**item,'quantity':str(qty),'seller_name':seller['name'],'line_total':rounded(total) if total is not None else None,'arrival':arrival};lines.append(detail)
            group=groups.setdefault(key,dict(key=key,supplier_id=item['supplier_id'],seller_name=seller['name'],currency=item['currency'],items=[],known_goods=Decimal(0),unknown_prices=0))
            group['items'].append(detail)
            if total is None:group['unknown_prices']+=1
            else:group['known_goods']+=total
        converted=Decimal(0);complete=True
        for key,g in groups.items():
            fee=cart.get('fees',{}).get(key,{})
            g['goods']=None if g['unknown_prices'] else rounded(g['known_goods']);g['known_goods']=rounded(g['known_goods'])
            for k in ('shipping','taxes','other'):
                g[k]=fee.get(k)
                if g[k] is None:errors.append(g['seller_name']+': нет информации о расходах «'+{'shipping':'доставка','taxes':'налоги / пошлины','other':'прочее'}[k]+'». Введите согласованную сумму, в том числе 0, если расходов нет.')
            known=[Decimal(g[k]) for k in ('goods','shipping','taxes','other') if g[k] is not None]
            g['known_total']=rounded(sum(known,Decimal(0)))
            g['total']=g['known_total'] if all(g[k] is not None for k in ('goods','shipping','taxes','other')) else None
            rate=Decimal(1) if g['currency']==cart['base_currency'] else Decimal(cart['rates'][g['currency']]) if cart['rates'].get(g['currency']) else None
            if g['total'] is not None and rate is not None and Decimal(g['total'])*rate>Decimal('1000000000000000'):raise ValueError('Слишком большая сумма после конвертации.')
            g['base_total']=rounded(Decimal(g['total'])*rate) if g['total'] is not None and rate is not None else None
            if g['base_total'] is None:complete=False
            else:converted+=Decimal(g['base_total'])
            if rate is None:errors.append('Нет курса '+g['currency']+' к '+cart['base_currency']+'.')
        result=dict(lines=lines,groups=list(groups.values()),base_currency=cart['base_currency'],total=rounded(converted) if complete and lines else None,errors=list(dict.fromkeys(errors)),ready=bool(lines) and not errors,order_date=cart['order_date'],deadline=cart.get('deadline',''),revision=cart['revision'])
        result['quote_id']=hashlib.sha256(json.dumps(result,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        return result
    def optimize(self):
        cart=self.state();current=self.quote(cart);catalog=self.catalog.list();byid={x['id']:x for x in catalog};options=[];notes=[]
        for line in cart['lines']:
            original=byid.get(line['item_id']);qty=Decimal(line['quantity']);key=(original or {}).get('comparison_key','').strip().casefold()
            candidates=[x for x in catalog if key and x['comparison_key'].strip().casefold()==key] if key else [original] if original else []
            viable=[]
            for item in candidates:
                if item['unit_price'] is None or item['availability']!='available':continue
                if item['moq'] is not None and qty<Decimal(item['moq']):continue
                if item['stock_qty'] is not None and qty>Decimal(item['stock_qty']):continue
                if cart['deadline']:
                    if item['lead_max'] is None or date.fromisoformat(cart['order_date'])+timedelta(days=item['lead_max'])>date.fromisoformat(cart['deadline']):continue
                groupkey=str(item['supplier_id'])+':'+item['currency'];fee=cart['fees'].get(groupkey,{})
                if any(fee.get(k) is None for k in ('shipping','taxes','other')):continue
                rate=Decimal(1) if item['currency']==cart['base_currency'] else Decimal(cart['rates'][item['currency']]) if cart['rates'].get(item['currency']) else None
                if rate is None:continue
                viable.append((item,Decimal(rounded(qty*Decimal(item['unit_price'])))*rate,groupkey,sum((Decimal(fee[k]) for k in ('shipping','taxes','other')),Decimal(0))*rate))
            if not viable:raise ValueError('Для «'+(original or {}).get('name','удалённой модели')+'» нет предложения с известными ценой, наличием, расходами и подходящим сроком. Заполните каталог и расходы поставщиков.')
            if not key:notes.append('«'+original['name']+'»: нет ключа точной модели, замена не выполнялась.')
            options.append(sorted(viable,key=lambda v:v[1]+v[3])[:12])
        if not options:raise ValueError('Корзина пуста.')
        combinations=1
        for group in options:combinations*=len(group)
        def score(selection):
            counts={}
            for i,v in enumerate(selection):counts[v[0]['id']]=counts.get(v[0]['id'],Decimal(0))+Decimal(cart['lines'][i]['quantity'])
            if any(byid[item_id]['stock_qty'] is not None and qty>Decimal(byid[item_id]['stock_qty']) for item_id,qty in counts.items()):return Decimal('Infinity')
            grouped={}
            for v in selection:grouped[v[2]]=grouped.get(v[2],Decimal(0))+v[1]
            for group,fee in {v[2]:v[3] for v in selection}.items():grouped[group]+=fee
            return sum((Decimal(rounded(value)) for value in grouped.values()),Decimal(0))
        if combinations<=30000:best=min(itertools.product(*options),key=score);method='Точный перебор отобранных вариантов · до 12 предложений на модель'
        else:
            states=[[]]
            for group in options:states=sorted([s+[v] for s in states for v in group],key=score)[:150]
            best=states[0];method='Ограниченный поиск: глобальный минимум не гарантирован'
        if not score(best).is_finite():raise ValueError('Недостаточно остатков для всех моделей корзины одновременно.')
        proposed={**cart,'lines':[dict(item_id=v[0]['id'],quantity=cart['lines'][i]['quantity']) for i,v in enumerate(best)]};proposed={**self.validate(proposed),'revision':cart['revision']};quote=self.quote(proposed)
        if current['total'] is not None and quote['total'] is not None and Decimal(quote['total'])>Decimal(current['total']):proposed=cart;quote=current;notes.append('Текущий вариант дешевле найденного подбора и сохранён в предложении.')
        return dict(cart=proposed,quote=quote,current_total=current['total'],saving=rounded(Decimal(current['total'])-Decimal(quote['total'])) if current['total'] is not None and quote['total'] is not None else None,method=method,notes=notes,comparison='Сравниваются только одинаковые comparison_key, с известными расходами, до 12 предложений на модель. Сочетания без достоверных данных не участвуют.')
    def checkout(self,data):
        cart=self.state();quote=self.quote(cart)
        if not quote['ready']:raise ValueError('Оформление недоступно: уточните цены, наличие, расходы и сроки из списка корзины.')
        if data.get('quote_id')!=quote['quote_id']:raise ValueError('Условия корзины изменились. Пересчитайте её перед оформлением.')
        email=data.get('buyer_email','');address=data.get('address','');key=data.get('idempotency_key','')
        if not isinstance(email,str) or len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise ValueError('Укажите email покупателя.')
        if not isinstance(address,str) or not address.strip() or len(address)>2000:raise ValueError('Укажите адрес доставки или самовывоз.')
        if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{32,80}',key):raise ValueError('Некорректный идентификатор оформления.')
        if data.get('consent') is not True:raise ValueError('Подтвердите модели и согласованные с поставщиками условия.')
        pending=[]
        for g in quote['groups']:
            if Decimal(g['total'])<=0 or Decimal(g['total'])>Decimal('1000000000000'):raise ValueError('Недопустимая сумма заказа.')
            # Each seller/currency gets an independent order. No money is routed to another seller.
            token=hashlib.sha256((key+':'+g['key']).encode()).hexdigest();arrivals=[x['arrival'] for x in g['items']]
            delivery={'from':max(x['from'] for x in arrivals),'to':max(x['to'] for x in arrivals)} if all(arrivals) else None
            goods=sum((Decimal(x['line_total']) for x in g['items']),Decimal(0));extras=Decimal(g['taxes'])+Decimal(g['other'])
            order={'id':str(uuid.uuid4()),'supplier_id':g['supplier_id'],'seller_name':g['seller_name'],'product':('Корзина: '+', '.join(x['name'] for x in g['items']))[:500],'quantity':'1','unit_price':rounded(goods+extras),'shipping':g['shipping'],'amount':g['total'],'currency':g['currency'],'buyer_email':email.strip(),'address':address.strip(),'trade_format':'B2B','status':'awaiting_integration','created_at':datetime.now(timezone.utc).isoformat(),'idempotency_key':token,'items':json.dumps(g['items'],ensure_ascii=False),'placed_on':cart['order_date'],'delivery_estimate':json.dumps(delivery,ensure_ascii=False),'expense_breakdown':json.dumps({k:g[k] for k in ('goods','shipping','taxes','other')},ensure_ascii=False)}
            seller=self.db.get_supplier(g['supplier_id']);order['trade_format']=seller.get('trade_format') or 'B2B'
            pending.append(order)
        # Insert every order in one transaction before any external payment request.
        orders=[]
        with self.db._conn() as c:
            current=c.execute("SELECT v FROM settings WHERE k='cart_v1'").fetchone()
            if not current or json.loads(current['v'])!=cart:raise ValueError('Корзина изменилась. Обновите расчёт перед оформлением.')
            for item in quote['lines']:
                row=c.execute('SELECT payload FROM catalog_items WHERE id=?',(item['id'],)).fetchone()
                if not row or json.loads(row['payload']).get('updated_at')!=item['updated_at']:raise ValueError('Каталог изменился. Обновите цены и условия перед оформлением.')
            for order in pending:
                existing=c.execute('SELECT * FROM purchases WHERE idempotency_key=?',(order['idempotency_key'],)).fetchone()
                if existing:
                    if any(existing[k]!=order[k] for k in order if k not in ('id','created_at','status')):raise ValueError('Этот идентификатор уже использован с другими условиями корзины.')
                    orders.append(dict(existing));continue
                keys=list(order);c.execute('INSERT INTO purchases('+','.join(keys)+') VALUES('+','.join('?' for _ in keys)+')',list(order.values()));orders.append(order)
        results=[]
        for order in orders:
            try:payment=YooKassaGateway(self.db).checkout('purchase',order,'seller_'+str(order['supplier_id']))
            except ValueError as error:payment={'payment_enabled':False,'checkout_url':None,'message':str(error)+' Заказ сохранён.'}
            results.append({'order':order,**(payment or {'payment_enabled':False,'checkout_url':None,'message':'Оплата поставщику ещё не подключена. Заказ сохранён без списания и автоматической отправки продавцу.'})})
        return {'orders':results,'message':'Корзина разделена на заказы по поставщикам и валютам. Будущая дата — план оформления, а не автоматическое бронирование или гарантированная доставка.'}
