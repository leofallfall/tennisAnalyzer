"""Small, explicit in-place schema migrations.

``Base.metadata.create_all`` creates missing tables but never changes an existing one, so
a database created by an earlier version needs these steps. Each migration runs once, in
its own transaction, and is recorded in ``schema_migrations``. Migrations only add
columns and copy data: they never delete or rewrite existing values.

Only the API runs migrations (on startup, after ``create_all``); the worker does not use
the tables they touch.

No Alembic: two additive steps do not justify a new dependency. Revisit when the schema
needs renames, drops or a PostgreSQL deployment.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Connection, Engine, inspect, text

from tennis_api.models import AiImportOrigin, new_id


def _add_column(conn: Connection, table: str, column: str, ddl: str) -> None:
    existing = {c["name"] for c in inspect(conn).get_columns(table)}
    if column not in existing:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def _ai_integrity_and_history(conn: Connection) -> None:
    """2026-11: C2 integrity anchor + import history (AI audit fixes).

    * ``ai_analyses.manifest_sha256`` (NULL for packs created before: they are *legacy*
      and cannot be imported; the user prepares a new pack) and ``last_rejected_at``.
    * Every result already imported is copied, unchanged, to ``ai_analysis_imports`` as
      version 1 with ``origin = legacy_migration``. The original columns are kept.
    """
    _add_column(conn, "ai_analyses", "manifest_sha256", "VARCHAR(64)")
    _add_column(conn, "ai_analyses", "last_rejected_at", "DATETIME")
    rows = conn.execute(
        text(
            "SELECT a.id, a.analysis_id, a.kind, a.pack_id, a.adapter, a.model, a.result, "
            "a.validation_warnings, a.imported_at, a.created_at FROM ai_analyses a "
            "WHERE a.result IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM ai_analysis_imports i WHERE i.ai_analysis_id = a.id)"
        )
    ).all()
    for r in rows:
        conn.execute(
            text(
                "INSERT INTO ai_analysis_imports (id, ai_analysis_id, analysis_id, version, "
                "kind, pack_id, manifest_sha256, result_sha256, adapter, model, result, "
                "validation_warnings, origin, imported_at) VALUES (:id, :ai, :an, 1, :kind, "
                ":pack, NULL, NULL, :adapter, :model, :result, :warnings, :origin, :at)"
            ),
            {
                "id": new_id(),
                "ai": r.id,
                "an": r.analysis_id,
                "kind": r.kind,
                "pack": r.pack_id,
                "adapter": r.adapter,
                "model": r.model,
                "result": r.result,  # raw JSON text copied as is
                "warnings": r.validation_warnings,
                "origin": AiImportOrigin.LEGACY_MIGRATION.value,
                "at": r.imported_at or r.created_at,
            },
        )


def _analysis_player_handedness(conn: Connection) -> None:
    """2026-11 (M1.10): declared dominant hand and backhand style on analyses.

    Nullable, no backfill: existing analyses stay NULL ("not provided"). Values are never
    inferred, including from the browser-only player profile.
    """
    _add_column(conn, "analyses", "dominant_hand", "VARCHAR(8)")
    _add_column(conn, "analyses", "backhand", "VARCHAR(16)")


MIGRATIONS: list[tuple[str, Callable[[Connection], None]]] = [
    ("2026_11_18_ai_integrity_and_history", _ai_integrity_and_history),
    ("2026_11_19_analysis_player_handedness", _analysis_player_handedness),
]


def run_migrations(engine: Engine) -> list[str]:
    """Apply pending migrations. Returns the ids applied now. Idempotent."""
    applied_now: list[str] = []
    for mig_id, fn in MIGRATIONS:
        with engine.begin() as conn:
            done = conn.execute(
                text("SELECT 1 FROM schema_migrations WHERE id = :id"), {"id": mig_id}
            ).first()
            if done:
                continue
            fn(conn)
            conn.execute(
                text("INSERT INTO schema_migrations (id, applied_at) VALUES (:id, :at)"),
                {"id": mig_id, "at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")},
            )
            applied_now.append(mig_id)
    return applied_now
