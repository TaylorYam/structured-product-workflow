from pathlib import Path


def test_setup_migrates_the_install_database_before_recording_installation() -> None:
    script = (Path(__file__).resolve().parents[1] / "setup_panel.ps1").read_text(encoding="utf-8-sig")
    migration_call = (
        "& '.\\.venv\\Scripts\\spw-db-migrate.exe' --env-file (Join-Path $PSScriptRoot '.env') --admin-user postgres"
    )

    assert migration_call in script
    assert script.index(migration_call) < script.index("--record-installation")
    assert "if ($LASTEXITCODE -ne 0) { throw '資料庫遷移失敗" in script
