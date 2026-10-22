"""Offline backup CLI; stop the server before create or restore."""
import argparse
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.backup import create_backup, verify_backup, restore_backup
from app.security import master_key
from app.config import DATA_DIR

parser=argparse.ArgumentParser(description='Зашифрованные резервные копии. Перед созданием остановите сервер; ключ храните отдельно.')
parser.add_argument('action',choices=['create','verify','restore'])
parser.add_argument('archive',type=Path)
parser.add_argument('--data-dir',type=Path,default=DATA_DIR)
parser.add_argument('--destination',type=Path)
args=parser.parse_args()
if os.environ.get('SUPPLY_MATCH_DATABASE_URL'):parser.error('Для PostgreSQL используйте pg_dump и pg_restore; этот инструмент копирует только зашифрованные SQLite-файлы.')
try:
    key=master_key()
    if args.action=='create':count=create_backup(args.data_dir,args.archive,key)
    elif args.action=='verify':count=len(verify_backup(args.archive,key))
    else:
        if not args.destination:parser.error('restore требует --destination с новой папкой')
        count=restore_backup(args.archive,args.destination,key)
    print('Проверено баз:',count)
except Exception:
    sys.exit('Операция не выполнена. Проверьте ключ, архив и путь назначения. Исходные базы не изменены.')
