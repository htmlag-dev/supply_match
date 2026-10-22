"""Server console only. Never exposed as an HTTP endpoint."""
import argparse
import getpass
import json
import os
import secrets
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.services.auth_service import AuthService,password_hash
from app.config import DATA_DIR,DB_PATH

p=argparse.ArgumentParser();p.add_argument('action',choices=['create','reset-password','bootstrap-local']);p.add_argument('--email');p.add_argument('--import-legacy',action='store_true');args=p.parse_args()
auth=AuthService()
if args.action=='bootstrap-local':
    if os.environ.get('SUPPLY_MATCH_ENV')=='production':raise SystemExit('В production используйте create.')
    with auth.conn() as c:
        if c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():raise SystemExit('Администратор уже существует.')
    password=secrets.token_urlsafe(24);email='admin@supplymatch.local'
    user=auth.register({'email':email,'name':'Администратор','password':password},role='admin')
    target=DATA_DIR/'admin-bootstrap.txt'
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:f.write('Локальный администратор Supply Match\nEmail: '+email+'\nПароль: '+password+'\n\nХраните в менеджере паролей. После переноса удалите этот файл. Не размещайте data в публичной папке.\n')
    if args.import_legacy and DB_PATH.exists():
        import sqlite3
        # Copy the legacy workspace into encrypted storage without exposing it to new registrations.
        db=auth.workspace(user['id'])
        source=sqlite3.connect(DB_PATH)
        try:
            with db._conn() as dest:
                for line in source.iterdump():
                    if line.startswith(('BEGIN','COMMIT','CREATE TABLE','CREATE INDEX')):continue
                    dest.execute(line)
        finally:source.close()
    print('Создан локальный администратор. Данные входа: data/admin-bootstrap.txt')
elif args.action=='create':
    email=args.email or input('Email: ');password=getpass.getpass('Пароль (12–128 символов): ')
    if password!=getpass.getpass('Повторите пароль: '):raise SystemExit('Пароли не совпадают.')
    auth.register({'email':email,'name':'Администратор','password':password},role='admin');print('Администратор создан.')
else:
    email=args.email or input('Email: ');password=getpass.getpass('Новый пароль: ');hashed=password_hash(password)
    with auth.conn() as c:
        row=c.execute('SELECT id FROM users WHERE email=?',(email.lower().strip(),)).fetchone()
        if not row:raise SystemExit('Аккаунт не найден.')
        c.execute('UPDATE users SET password=? WHERE id=?',(hashed,row['id']))
        c.execute('DELETE FROM sessions WHERE user_id=?',(row['id'],))
    print('Пароль заменён, сессии отозваны.')
