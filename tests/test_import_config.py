"""起單資料與重算用的設定（#493）：搬出 `product_terms` 之後的別名，與讀不到設定時的錯誤。"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from fcn_checker.check_config import ConfigPaths
from sp_workflow import import_config, product_terms
from sp_workflow.database import OperatorError
from sp_workflow.import_config import ImportConfigError, load_import_config

BUILTIN = ConfigPaths.with_fallback(Path(__file__).resolve().parents[1] / "config" / "review_standard.toml")


def test_product_terms_keeps_the_old_names_for_one_version() -> None:
    assert product_terms.ImportConfig is import_config.ImportConfig
    assert product_terms.load_import_config is import_config.load_import_config


def test_the_bundled_config_loads() -> None:
    config = load_import_config(BUILTIN)

    assert config.denominations and config.issuer_prefixes and config.calendar is not None


def test_a_missing_config_file_is_explained_to_the_operator(tmp_path: Path) -> None:
    paths = dataclasses.replace(BUILTIN, risk_level=tmp_path / "沒有這個檔.toml")

    with pytest.raises(ImportConfigError, match="沒有這個檔.toml") as info:
        load_import_config(paths)

    assert isinstance(info.value, OperatorError), "視窗照樣當作業人員看得懂的錯誤顯示"
