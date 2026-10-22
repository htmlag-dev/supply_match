"""Authenticated encrypted SQLite snapshots; one application process per data directory."""
import base64
import os
import sqlite3
import tempfile
import threading
from pathlib import Path
from contextlib import contextmanager
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.config import DATA_DIR
_LOCKS={}
_GUARD=threading.Lock()
MAGIC=b'SMENC1\0'

def master_key():
    value=os.environ.get('SUPPLY_MATCH_ENCRYPTION_KEY','')
    if value:
        try:
            key=base64.urlsafe_b64decode(value)
            if len(key)!=32: raise ValueError()
            return key
        except Exception: raise RuntimeError('SUPPLY_MATCH_ENCRYPTION_KEY: нужен base64 ключ из 32 случайных байтов.') from None
    if os.environ.get('SUPPLY_MATCH_ENV')=='production':
        raise RuntimeError('Для production задайте SUPPLY_MATCH_ENCRYPTION_KEY в хранилище секретов.')
    DATA_DIR.mkdir(exist_ok=True)
    path=DATA_DIR/'encryption.key'
    if not path.exists():
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:f.write(os.urandom(32))
        except FileExistsError:pass
    key=path.read_bytes()
    if len(key)!=32:raise RuntimeError('Повреждён ключ шифрования. Не создавайте новый вместо старого.')
    return key

@contextmanager
def encrypted_connection(path,key):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with _GUARD:lock=_LOCKS.setdefault(str(path.resolve()),threading.RLock())
    with lock:
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        try:
            if path.exists():
                data=path.read_bytes()
                if not data.startswith(MAGIC):raise RuntimeError('Ожидается зашифрованное хранилище.')
                c.deserialize(AESGCM(key).decrypt(data[len(MAGIC):len(MAGIC)+12],data[len(MAGIC)+12:],MAGIC))
            before=c.total_changes
            schema=c.execute("PRAGMA schema_version").fetchone()[0]
            with c:yield c
            # Include schema mutations; SQLite serializes only after initialization.
            if c.total_changes!=before or c.execute("PRAGMA schema_version").fetchone()[0]!=schema or not path.exists():
                nonce=os.urandom(12);payload=MAGIC+nonce+AESGCM(key).encrypt(nonce,c.serialize(),MAGIC)
                fd,temp=tempfile.mkstemp(prefix='.encrypted-',dir=path.parent)
                try:
                    with os.fdopen(fd,'wb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
                    os.replace(temp,path)
                finally:
                    if os.path.exists(temp):os.unlink(temp)
        finally:c.close()
