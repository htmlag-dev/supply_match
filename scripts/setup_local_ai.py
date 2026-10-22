"""Загрузка официальной Qwen3-4B Q4_K_M и CPU runtime llama.cpp в data/local-ai."""
import hashlib
import json
import os
import platform
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'data' / 'local-ai'
MODEL_NAME = 'Qwen3-4B-Q4_K_M.gguf'
MODEL_REPO = 'Qwen/Qwen3-4B-GGUF'
HEADERS = {'User-Agent':'SupplyMatch-LocalAI/1.0'}

def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=45) as response:
        return json.load(response)

def download(url, target, expected_hash=None, expected_size=None):
    if target.exists() and (not expected_size or target.stat().st_size == expected_size):
        if not expected_hash or file_hash(target) == expected_hash:
            print('Already ready: ' + target.name, flush=True)
            return
    part = target.with_suffix(target.suffix+'.part')
    digest = hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(url,headers=HEADERS),timeout=90) as response, part.open('wb') as output:
        total = int(response.headers.get('Content-Length',0))
        done, next_report = 0, 0
        while True:
            chunk=response.read(4*1024*1024)
            if not chunk: break
            output.write(chunk); digest.update(chunk); done+=len(chunk)
            if done>=next_report:
                print(f'{target.name}: {done//1048576} MB / {total//1048576 if total else "?"} MB',flush=True)
                next_report=done+128*1048576
    if expected_size and done != expected_size:
        raise RuntimeError('Download size mismatch')
    if expected_hash and digest.hexdigest() != expected_hash:
        raise RuntimeError('SHA256 mismatch')
    part.replace(target)

def file_hash(path):
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(4*1024*1024),b''): digest.update(chunk)
    return digest.hexdigest()

def setup():
    if platform.system() != 'Windows' or platform.machine().lower() not in ('amd64','x86_64'):
        raise RuntimeError('Automatic setup currently supports Windows x64. See docs/MOBILE_AND_AI.md for other platforms.')
    ROOT.mkdir(parents=True,exist_ok=True)
    releases=get_json('https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=8')
    release=next(r for r in releases if any(a['name'].endswith('bin-win-cpu-x64.zip') for a in r['assets']))
    asset=next(a for a in release['assets'] if a['name'].endswith('bin-win-cpu-x64.zip'))
    archive=ROOT/asset['name']
    digest=asset.get('digest') or ''
    download(asset['browser_download_url'],archive,digest.removeprefix('sha256:') or None,asset['size'])
    runtime=ROOT/'runtime'; runtime.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            path=(runtime/entry.filename).resolve()
            if not path.is_relative_to(runtime.resolve()): raise RuntimeError('Unsafe archive entry')
        bundle.extractall(runtime)
    model_info=get_json('https://huggingface.co/api/models/'+MODEL_REPO+'?blobs=true')
    model=next(item for item in model_info['siblings'] if item['rfilename']==MODEL_NAME)
    sha=model.get('lfs',{}).get('sha256')
    download('https://huggingface.co/'+MODEL_REPO+'/resolve/'+model_info['sha']+'/'+MODEL_NAME,ROOT/MODEL_NAME,sha,model.get('size'))
    manifest={'model':MODEL_REPO,'revision':model_info['sha'],'file':MODEL_NAME,'sha256':file_hash(ROOT/MODEL_NAME),
              'runtime_version':release['tag_name'],'runtime_source':asset['browser_download_url'],
              'license':'Apache-2.0 (Qwen), MIT (llama.cpp)'}
    (ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print('Local AI is ready. Run python main.py. No API key is needed.',flush=True)

if __name__=='__main__':
    try: setup()
    except Exception as error:
        print('Setup failed: '+str(error),file=sys.stderr)
        sys.exit(1)
