"""Live search: keyless DDGS discovery and optional Tavily API."""
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from app.services.ai_service import AIError


class WebSearchClient:
    def __init__(self, db):
        self.db = db

    @property
    def key(self):
        return self.db.get_setting("tavily_key") or os.environ.get("TAVILY_API_KEY", "")

    def search(self, query):
        if not self.key:
            raise AIError("Подключите Tavily для веб-поиска или выберите Gemini.")
        request = urllib.request.Request("https://api.tavily.com/search",
            data=json.dumps({"query": query[:1800], "search_depth": "basic", "max_results": 8,
                "include_answer": False, "include_raw_content": False}).encode(),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if code in (429, 432, 433):
                raise AIError("Лимит веб-поиска Tavily исчерпан. Повторите позже или отключите веб-поиск.") from None
            raise AIError("Не удалось выполнить веб-поиск. Проверьте ключ и лимиты Tavily.") from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise AIError("Поисковый сервис недоступен. Проверьте сеть и повторите запрос.") from None
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise AIError("Поисковый сервис вернул неверный формат ответа.")
        sources = []
        for item in data["results"][:8]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", ""))
            try:
                parsed = urlsplit(url)
            except ValueError:
                continue
            if parsed.scheme not in ("https", "http") or not parsed.netloc:
                continue
            sources.append({"url": url, "title": str(item.get("title", ""))[:300],
                            "content": str(item.get("content", ""))[:2500]})
        return sources

    @property
    def available(self):
        try:import ddgs;return True
        except ImportError:return bool(self.key)

    def discover(self,product,country='',city='',category='',trade_format='B2B',page=1,depth='wide'):
        from concurrent.futures import ThreadPoolExecutor,as_completed
        from ddgs import DDGS
        product=product[:250];region=country[:120];category=category[:100]
        base=' '.join(filter(None,[product,region,category]))
        suffixes=(['поставщики оптом','производитель фабрика wholesale manufacturer','дистрибьютор дилер','каталог поставщиков'] if trade_format=='B2B' else ['купить магазин','продавцы доставка','интернет магазин цена','объявления продажа'] if trade_format=='B2C' else ['частные объявления купить','продам с рук','объявления б у','доска объявлений'])
        queries=[base+' '+suffix for suffix in suffixes]
        if city and not country:queries[2]+=' '+city[:120]
        per_query=15 if depth=='wide' else 8
        collected={};failures=0
        def run(pair):
            i,query=pair
            results=DDGS(timeout=12).text(query,max_results=per_query,page=page,safesearch='moderate',backend='auto')
            return i,results
        # A stable round-robin order prevents the fastest engine/query from taking every place.
        with ThreadPoolExecutor(max_workers=4) as executor:
            jobs=[executor.submit(run,pair) for pair in enumerate(queries)]
            for future in as_completed(jobs):
                try:i,items=future.result();collected[i]=items
                except Exception:failures+=1
        sources=[];seen=set()
        for offset in range(per_query):
            for index in range(len(queries)):
                batch=collected.get(index,[])
                if offset>=len(batch):continue
                item=batch[offset];url=str(item.get('href') or item.get('url') or '')
                try:parsed=urlsplit(url)
                except ValueError:continue
                if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password:continue
                # Preserve individual listings while discarding exact duplicates and tracking parameters.
                canonical=parsed._replace(fragment='',query='&'.join(p for p in parsed.query.split('&') if p and not p.lower().startswith(('utm_','fbclid=','gclid=')))).geturl()
                if canonical in seen:continue
                seen.add(canonical);sources.append({'url':canonical,'title':str(item.get('title') or parsed.hostname)[:300],'content':str(item.get('body') or item.get('content') or '')[:1800],'query':queries[index]})
        if not sources and failures:raise AIError('Поисковые системы временно недоступны или ограничили запросы. Повторите позже; можно подключить Tavily в настройках.')
        return sources,failures
