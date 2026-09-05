"""Create the pgvector extension the retrieval queries depend on.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-05

``db/repository.py`` casts the TEXT embedding column with ``::vector`` and orders by the
``<=>`` cosine operator; both exist only once the ``vector`` extension is created in the
database. No earlier migration created it (0001 creates ``citext`` and ``pgcrypto`` only),
so every fresh database ingested fine and failed retrieval with ``type "vector" does not
exist`` -- the factory's disposable validation database first among them (D-053).
Production had the extension created by hand.

pgvector is marked trusted, so the database owner can create it without superuser
rights; the app's own connection is that owner. The server image must ship pgvector
(production runs ``pgvector/pgvector:pg16``); on a plain ``postgres`` image this
statement fails loudly at startup instead of retrieval failing quietly per query.
"""

from __future__ import annotations

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Deliberately a no-op. DROP EXTENSION would refuse while any object of type
    # vector exists (or, with CASCADE, drop those objects), a shared database may
    # have other users of the extension, and an unused extension is harmless.
    pass
