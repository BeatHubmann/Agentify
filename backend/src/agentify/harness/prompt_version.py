"""Prompt version (A§9.5, OP-9): one hash over everything that shapes agent output.

Deliberately over-broad: agent and harness source, registry declarations, prompts and skills.
Every revision written by an agent run records it."""

import hashlib
from pathlib import Path


def compute(*roots: Path) -> str:
    digest = hashlib.sha256()
    for root in roots:
        for path in sorted(
            p
            for p in root.rglob("*")
            if p.is_file()
            and p.suffix in {".py", ".md", ".yaml", ".json"}
            and "__pycache__" not in p.parts
        ):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def default() -> str:
    package = Path(__file__).resolve().parents[1]
    registry = Path(__file__).resolve().parents[4] / "registry"
    return compute(package, registry)
