"""Build a public, static Hixo ZIP. Never include databases, Python or secrets."""
import argparse
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ('index.html', 'styles.css', 'remote-web.js', 'theme-utils.js', 'account.js',
          'app.js', 'cart.js', 'procurement.js', 'ambient.js', 'icon.svg',
          'icon-192.png', 'icon-512.png', 'manifest.webmanifest')


def build(api_origin, output):
    parsed = urlsplit(api_origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment
            or not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]+)?/?', api_origin)):
        raise ValueError('Укажите HTTPS origin сервера без пути, пароля и параметров.')
    api_origin = api_origin.rstrip('/')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ASSETS:
            data = (ROOT / 'app' / 'web' / name).read_bytes()
            if name == 'index.html':
                html = data.decode().replace('<meta name="api-base" content="">',
                    f'<meta name="api-base" content="{api_origin}"><meta name="static-api" content="{api_origin}">')
                html = re.sub(r'(src|href)="/([^"/]+)"', r'\1="./\2"', html)
                if any('data-hixo="off"' not in tag for tag in re.findall(r'<form\b[^>]*>', html)):
                    raise ValueError('Каждая форма должна отключать перехват Hixo.')
                data = html.encode()
            elif name == 'manifest.webmanifest':
                manifest = json.loads(data)
                for field in ('id', 'start_url', 'scope'): manifest[field] = './'
                for icon in manifest['icons']: icon['src'] = '.' + icon['src']
                for shortcut in manifest['shortcuts']: shortcut['url'] = '.' + shortcut['url']
                data = json.dumps(manifest, ensure_ascii=False).encode()
            archive.writestr(name, data)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-origin', default='https://supply-match.onrender.com')
    parser.add_argument('--output', type=Path, default=ROOT / 'build' / 'Supply-Match-Hixo.zip')
    args = parser.parse_args()
    print(build(args.api_origin, args.output))
