import pytest
import subprocess
import sys
from pathlib import Path
from sqlalchemy import create_engine, inspect, text

from app.database import Base
import app.models  # noqa: F401
from app.migration import (
    _fix_foreign_key_cascade,
    _rebuild_table,
    execute_migration,
)


def test_rebuild_table_preserves_model_constraints_and_indexes():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    insp = inspect(engine)
    existing_cols = {
        col["name"]: col for col in insp.get_columns("projects")
    }

    assert _rebuild_table(
        engine,
        "projects",
        Base.metadata.tables["projects"],
        existing_cols,
    )

    rebuilt = inspect(engine)
    unique_columns = {
        tuple(constraint["column_names"])
        for constraint in rebuilt.get_unique_constraints("projects")
    }
    index_names = {
        index["name"] for index in rebuilt.get_indexes("projects")
    }

    assert ("name",) in unique_columns
    assert ("project_id",) in unique_columns
    assert "ix_projects_id" in index_names


def test_fix_foreign_key_cascade_rebuilds_legacy_non_cascade_table():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            'CREATE TABLE "projects" ('
            '"id" INTEGER PRIMARY KEY, '
            '"name" VARCHAR(100) NOT NULL UNIQUE, '
            '"project_id" INTEGER NOT NULL UNIQUE, '
            '"created_at" DATETIME NOT NULL, '
            '"updated_at" DATETIME NOT NULL'
            ')'
        ))
        conn.execute(text(
            'CREATE TABLE "task_logs" ('
            '"id" INTEGER PRIMARY KEY, '
            '"created_at" DATETIME NOT NULL, '
            '"updated_at" DATETIME NOT NULL, '
            '"project_id" INTEGER NOT NULL, '
            '"task_type" VARCHAR(20) NOT NULL, '
            '"trigger_type" VARCHAR(20) NOT NULL, '
            '"status" VARCHAR(20) NOT NULL, '
            '"start_time" DATETIME NOT NULL, '
            'FOREIGN KEY ("project_id") REFERENCES "projects"("id")'
            ')'
        ))

    _fix_foreign_key_cascade(engine, Base.metadata)

    with engine.connect() as conn:
        fk_rows = conn.execute(text(
            "PRAGMA foreign_key_list('task_logs')"
        )).fetchall()

    assert any(
        row[2] == "projects" and row[6] == "CASCADE"
        for row in fk_rows
    )


def test_execute_migration_raises_when_schema_change_fails():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            'CREATE TABLE "projects" ('
            '"id" INTEGER PRIMARY KEY, '
            '"created_at" DATETIME NOT NULL, '
            '"updated_at" DATETIME NOT NULL'
            ')'
        ))
        conn.execute(text(
            "INSERT INTO projects (id, created_at, updated_at) "
            "VALUES (1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        ))

    with pytest.raises(RuntimeError, match="数据库迁移失败"):
        execute_migration(engine, Base.metadata)


def test_migrate_check_returns_distinct_exit_code_on_errors():
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "scripts/migrate.py",
            "--check",
            "--db-path",
            str(repo_root / "scripts"),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2


def test_deploy_script_distinguishes_check_errors_from_no_changes():
    script = Path("scripts/deploy.sh").read_text(encoding="utf-8")

    assert "check_status=$?" in script
    assert 'elif [ "$check_status" -eq 1 ]' in script
    assert "检查数据库迁移失败" in script
