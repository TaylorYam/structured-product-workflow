"""穩定的 Windows 啟動入口；一律從專案根目錄啟動，不連網。"""

from pathlib import Path


def arguments(script: str) -> list[str]:
    """啟動檔所在的專案根目錄：設定檔、審查標準與安裝目錄（上次選的資料夾、.env、核對紀錄）。"""
    root = Path(script).resolve().parent
    from fcn_checker.check_config import CONFIG_DIR, DEFAULTS

    config = root / CONFIG_DIR
    return [
        "--config-dir",
        str(config),
        "--builtin-config-dir",
        str(config),
        "--review-standard",
        str(root / DEFAULTS.review_standard),
        "--install-root",
        str(root),
    ]


def run(script: str) -> None:
    """核對的雙擊入口：開 FCN Workflow 視窗，停在核對頁。"""
    from fcn_checker.panel import main

    main(arguments(script))
