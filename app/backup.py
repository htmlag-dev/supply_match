"""Authenticated backup archives. Keys are never included. Restore into a new directory."""
import io
import json
import os
import sqlite3
import zipfile
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.security import MAGIC


def validate_database(payload, key):
    if not payload.startswith(MAGIC):raise ValueError('Неверный формат базы.')
    raw=AESGCM(key).decrypt(payload[len(MAGIC):len(MAGIC)+12],payload[len(MAGIC)+12:],MAGIC)
    c=sqlite3.connect(':memory:')
    try:
        c.deserialize(raw)
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Нарушена целостность базы.')
    finally:c.close()


def create_backup(source, destination, key):
    """Run while the application is stopped, to keep accounts/workspaces consistent."""
    source=Path(source);destination=Path(destination)
    if destination.exists():raise ValueError('Файл назначения уже существует.')
    files=[p for p in source.glob('*.enc') if p.is_file()]+list((source/'workspaces').glob('*.enc'))
    if not files:raise ValueError('Зашифрованные базы не найдены.')
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            payload=path.read_bytes();validate_database(payload,key)
            archive.writestr(path.relative_to(source).as_posix(),payload)
    nonce=os.urandom(12)
    payload=b'SMBACKUP1'+nonce+AESGCM(key).encrypt(nonce,buffer.getvalue(),b'SMBACKUP1')
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
    return len(files)


def verify_backup(backup, key):
    payload=Path(backup).read_bytes();header=b'SMBACKUP1'
    if not payload.startswith(header):raise ValueError('Неверный формат копии.')
    raw=AESGCM(key).decrypt(payload[9:21],payload[21:],header)
    result={}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for info in archive.infolist():
            name=info.filename;parts=Path(name).parts
            if name in result or name.startswith('/') or '..' in parts or not (len(parts)==1 and name.endswith('.enc') or len(parts)==2 and parts[0]=='workspaces' and parts[1].endswith('.enc')):raise ValueError('Некорректный путь в копии.')
            data=archive.read(info);validate_database(data,key);result[name]=data
    if not result:raise ValueError('Копия пуста.')
    return result


def restore_backup(backup, destination, key):
    files=verify_backup(backup,key);destination=Path(destination)
    if destination.exists():raise ValueError('Восстановление разрешено только в новую папку.')
    destination.mkdir(parents=True,mode=0o700)
    for name,data in files.items():
        path=destination/name;path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(data)
    # Invalidate sessions and one-time credentials restored from the past.
    from app.security import encrypted_connection
    account=destination/'accounts.enc'
    if account.exists():
        with encrypted_connection(account,key) as c:
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('sessions','password_resets','email_codes','oauth'):
                if table in tables:c.execute('DELETE FROM '+table)
    return len(files)
