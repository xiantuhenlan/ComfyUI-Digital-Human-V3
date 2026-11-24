"""Read-only SHA256 verification of this plugin's shipped files."""
from pathlib import Path
from hashlib import sha256
import json
ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / "integrity/files.json").read_text(encoding="utf-8"))
errors = []
for name, expected in manifest["files"].items():
    path = ROOT / name
    if not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected:
        errors.append(name)
if errors:
    raise SystemExit("Integrity mismatch: " + ", ".join(errors))
print(f"SHA256 verified: {len(manifest['files'])} files")
