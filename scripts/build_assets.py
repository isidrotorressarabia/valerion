"""Build immutable browser assets and a template manifest from readable sources."""
from pathlib import Path
import hashlib
import json
from rjsmin import jsmin
from rcssmin import cssmin

root = Path(__file__).resolve().parents[1] / "static"
target = root / "build"
target.mkdir(exist_ok=True)
manifest = {}
for folder, compact in (("js", jsmin), ("css", cssmin)):
    for source in sorted((root / folder).glob("*."+folder)):
        data = compact(source.read_text(encoding="utf-8")).encode()
        name = f"{source.stem}.{hashlib.sha256(data).hexdigest()[:16]}{source.suffix}"
        (target/name).write_bytes(data)
        manifest[source.relative_to(root).as_posix()] = name
(target/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n",encoding="utf-8")
for obsolete in target.iterdir():
    if obsolete.name not in {*manifest.values(),"manifest.json"}:
        obsolete.unlink()
