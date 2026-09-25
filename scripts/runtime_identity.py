"""Portable source identity, including unpacked installations without Git."""
from functools import lru_cache
import hashlib
from pathlib import Path
import platform


@lru_cache(maxsize=1)
def runtime_identity():
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    sources = sorted(root.glob("*.py"))
    for path in sources:
        digest.update(path.name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {"sourceSha256": digest.hexdigest(), "sourceFileCount": len(sources),
            "pythonVersion": platform.python_version(), "platform": platform.system()}
