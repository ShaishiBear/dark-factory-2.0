"""Pin migration 0006: the chain, and the one statement it issues, without a database.

Retrieval casts the embedding column with ``::vector`` at query time, so a database
without the extension ingests fine and fails every search (D-053). The migration is the
only thing that creates it, so its statement and its place at the head of the chain are
pinned here through a fake ``op``; no Postgres is involved.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from unittest import mock

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = VERSIONS / "0006_pgvector_extension.py"


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _revision_chain() -> dict[str, str | None]:
    chain: dict[str, str | None] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision: str = "([^"]+)"$', text, re.M)
        down = re.search(r'^down_revision: str \| None = (None|"[^"]+")$', text, re.M)
        assert revision is not None and down is not None, path.name
        chain[revision.group(1)] = None if down.group(1) == "None" else down.group(1).strip('"')
    return chain


def test_migration_is_the_sole_head_and_follows_0005() -> None:
    module = _load(MIGRATION)
    assert module.revision == "0006"
    assert module.down_revision == "0005"
    chain = _revision_chain()
    assert chain["0006"] == "0005"
    heads = set(chain) - {down for down in chain.values() if down is not None}
    assert heads == {"0006"}, heads


def test_upgrade_creates_the_vector_extension_and_nothing_else() -> None:
    module = _load(MIGRATION)
    fake_op = mock.Mock()
    with mock.patch.object(module, "op", fake_op):
        module.upgrade()
    assert fake_op.method_calls == [mock.call.execute("CREATE EXTENSION IF NOT EXISTS vector")]


def test_downgrade_leaves_the_extension_in_place() -> None:
    module = _load(MIGRATION)
    fake_op = mock.Mock()
    with mock.patch.object(module, "op", fake_op):
        module.downgrade()
    assert fake_op.method_calls == []
