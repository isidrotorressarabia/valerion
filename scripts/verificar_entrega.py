"""Prevent accidental commits of secrets and machine-specific data (no secret output)."""
from pathlib import Path
import subprocess
import re
root=Path(__file__).resolve().parents[1]
try:
    result=subprocess.run(['git','ls-files','-z'],cwd=root,check=True,capture_output=True)
    paths=[Path(p) for p in result.stdout.decode().split('\0') if p]
except (OSError,subprocess.CalledProcessError):
    paths=[p.relative_to(root) for p in root.rglob('*') if p.is_file() and not set(p.parts)&{'.venv','__pycache__','.pytest_cache','.git'}]
errors=[]
for path in paths:
    if path.name=='.env' or any(p in {'.venv','node_modules','__pycache__'} for p in path.parts) or path.suffix in {'.db','.sqlite','.pyc','.zip'}:errors.append(str(path));continue
    if path.suffix not in {'.py','.txt','.md','.yaml','.yml','.json','.example'}:continue
    text=(root/path).read_text(errors='replace')
    if re.search(r'(?:postgres(?:ql)?|cloudinary)://[^\s:/]+:[^\s@]+@(?!localhost|127\.0\.0\.1|demo(?:[\s/\'\"]|$)|CLOUD_NAME|[^\s]*example)[^\s]+',text,re.I):errors.append(str(path))
if errors:raise SystemExit('Revisa archivos que pueden contener datos privados: '+', '.join(sorted(set(errors))))
print('Entrega sin archivos privados detectados. Esta revisión no sustituye un escáner de secretos ni revisa el historial de Git.')
