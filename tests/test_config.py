# -*- coding: utf-8 -*-
"""Config 加载测试."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from javert.config import JavertConfig, load_config, reset_config_cache


@pytest.fixture(autouse=True)
def _clear_env_and_cache(monkeypatch):
    """清掉 JAVERT_* 环境变量与 lru_cache."""
    for key in list(os.environ):
        if key.startswith("JAVERT_"):
            monkeypatch.delenv(key, raising=False)
    reset_config_cache()
    yield
    reset_config_cache()


def test_load_from_file(tmp_path: Path):
    config_file = tmp_path / "llm.yaml"
    config_file.write_text(
        yaml.safe_dump({
            "llm_endpoint": "http://test:9000/v1",
            "llm_model": "test-model",
            "max_tool_calls": 7,
        }),
        encoding="utf-8",
    )
    cfg = load_config(config_file)
    assert cfg.llm_endpoint == "http://test:9000/v1"
    assert cfg.llm_model == "test-model"
    assert cfg.max_tool_calls == 7


def test_env_overrides_file(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "llm.yaml"
    config_file.write_text(
        yaml.safe_dump({"max_tool_calls": 10, "llm_model": "from-file"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("JAVERT_MAX_TOOL_CALLS", "5")
    monkeypatch.setenv("JAVERT_LLM_MODEL", "from-env")
    cfg = load_config(config_file)
    assert cfg.max_tool_calls == 5
    assert cfg.llm_model == "from-env"


def test_missing_file_uses_defaults(tmp_path: Path):
    cfg = load_config(tmp_path / "does_not_exist.yaml")
    assert cfg.llm_endpoint == "http://192.168.31.62:30000/v1"
    assert cfg.max_tool_calls == 10


def test_invalid_yaml_top_level_raises(tmp_path: Path):
    config_file = tmp_path / "llm.yaml"
    config_file.write_text("- 1\n- 2\n", encoding="utf-8")  # list, not mapping
    with pytest.raises(ValueError, match="必须是 mapping"):
        load_config(config_file)


def test_resolve_paths():
    cfg = JavertConfig()
    # data_path 应是绝对路径
    assert cfg.data_path.is_absolute()
    assert cfg.notes_path.name == "case_notes.csv"
    assert cfg.fees_path.name == "shi_fee.csv"


def test_hub_raw_defaults():
    """add-workbench-sql-raw-source: 开关默认关 = 纯 CSV 行为不变."""
    cfg = JavertConfig()
    assert cfg.hub_raw_enabled is False
    assert cfg.hub_database == "TP_data_hub"


def test_hub_raw_env_override(monkeypatch):
    monkeypatch.setenv("JAVERT_HUB_RAW_ENABLED", "true")
    monkeypatch.setenv("JAVERT_HUB_DATABASE", "TP_other")
    cfg = JavertConfig()
    assert cfg.hub_raw_enabled is True
    assert cfg.hub_database == "TP_other"


def test_tool_result_max_chars_default():
    """fix-drug-audit-precision: 未配置时默认 2000 (与现状一致)."""
    cfg = JavertConfig()
    assert cfg.tool_result_max_chars == 2000


def test_tool_result_max_chars_from_file(tmp_path: Path):
    config_file = tmp_path / "llm.yaml"
    config_file.write_text(
        yaml.safe_dump({"tool_result_max_chars": 4000}), encoding="utf-8"
    )
    assert load_config(config_file).tool_result_max_chars == 4000


def test_oncology_eligibility_v2_defaults_off():
    """默认关闭必须逐字保留现网药品审核路径."""
    assert JavertConfig().oncology_eligibility_v2 == "off"


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_oncology_eligibility_v2_env_modes(monkeypatch, mode):
    monkeypatch.setenv("JAVERT_ONCOLOGY_ELIGIBILITY_V2", mode)
    assert JavertConfig().oncology_eligibility_v2 == mode


def test_oncology_eligibility_v2_rejects_unknown_mode(monkeypatch):
    monkeypatch.setenv("JAVERT_ONCOLOGY_ELIGIBILITY_V2", "maybe")
    with pytest.raises(ValueError):
        JavertConfig()
