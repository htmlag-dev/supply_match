"""Build the autonomous Hixo presentation (no server or secrets)."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
root = Path(__file__).resolve().parents[1]
target = root / 'build/Supply-Match-Unified-Hixo.zip'
target.parent.mkdir(exist_ok=True)
with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
    for name in ('index.html', 'style.css', 'catalog.js', 'model.js', 'app.js'):
        archive.write(root / 'hixo' / name, name)
print(target)
