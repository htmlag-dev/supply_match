"""Generate Apple client-secret JWT into a private local file; never print the secret."""
import os
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import jwt
from app.config import DATA_DIR
prefix='SUPPLY_MATCH_APPLE_'
values={k:os.environ.get(prefix+k,'') for k in ('CLIENT_ID','TEAM_ID','KEY_ID','PRIVATE_KEY_FILE')}
if not all(values.values()):raise SystemExit('Задайте APPLE_CLIENT_ID, TEAM_ID, KEY_ID и PRIVATE_KEY_FILE в окружении.')
now=int(time.time())
token=jwt.encode({'iss':values['TEAM_ID'],'iat':now,'exp':now+30*86400,'aud':'https://appleid.apple.com','sub':values['CLIENT_ID']},Path(values['PRIVATE_KEY_FILE']).read_bytes(),algorithm='ES256',headers={'kid':values['KEY_ID']})
DATA_DIR.mkdir(exist_ok=True);target=DATA_DIR/'apple-client-secret.txt';fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(token)
print('JWT записан в data/apple-client-secret.txt. Срок 30 дней. Не публикуйте файл.')
