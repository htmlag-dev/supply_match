"""Offline, transactional migration; never modifies encrypted source files."""
import argparse
import os
import re
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import DATA_DIR
from app.security import encrypted_connection, master_key
from app.postgres import connection, schema_for


def migrate_file(source,key,commit=False):
    from psycopg import sql
    with encrypted_connection(source,key) as old:
        tables=old.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        with connection(source) as new:
            existing=new.conn.execute('SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema=%s',(new.schema,)).fetchone()['n']
            if existing:raise ValueError('Целевая схема уже содержит таблицы: '+new.schema)
            counts={}
            for table in tables:
                name=table['name']
                if not re.fullmatch(r'[a-z_]+',name):raise ValueError('Неожиданное имя таблицы.')
                new.execute(table['sql'])
                rows=old.execute('SELECT * FROM "'+name+'"').fetchall();columns=[r['name'] for r in old.execute('PRAGMA table_info('+name+')')]
                if rows:
                    statement=sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(name),sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns))
                    with new.conn.cursor() as cursor:cursor.executemany(statement,[tuple(row[col] for col in columns) for row in rows])
                count=new.conn.execute(sql.SQL('SELECT COUNT(*) AS n FROM {}').format(sql.Identifier(name))).fetchone()['n']
                if count!=len(rows):raise ValueError('Количество строк не совпало: '+name)
                counts[name]=count
                if 'id' in columns:
                    sequence=new.conn.execute('SELECT pg_get_serial_sequence(%s,%s) AS seq',(name,'id')).fetchone()['seq']
                    if sequence:
                        maximum=new.conn.execute(sql.SQL('SELECT MAX(id) AS n FROM {}').format(sql.Identifier(name))).fetchone()['n']
                        if maximum is not None:new.conn.execute('SELECT setval(%s,%s,true)',(sequence,maximum))
            for index in old.execute("SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
                new.execute(index['sql'])
            if not commit:new.conn.rollback()
    return counts

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Остановите сервер и сначала сделайте резервную копию. Без --commit выполняется проверка с откатом.')
    parser.add_argument('--data-dir',type=Path,default=DATA_DIR);parser.add_argument('--commit',action='store_true');args=parser.parse_args()
    if not os.environ.get('SUPPLY_MATCH_DATABASE_URL'):parser.error('Задайте SUPPLY_MATCH_DATABASE_URL')
    key=master_key();files=[p for p in (args.data_dir/'accounts.enc',args.data_dir/'system.enc') if p.exists()]+list((args.data_dir/'workspaces').glob('*.enc'))
    if not files:parser.error('Базы не найдены')
    for file in files:
        counts=migrate_file(file,key,args.commit);print(schema_for(file),sum(counts.values()),'строк','сохранено' if args.commit else 'проверено с откатом')
