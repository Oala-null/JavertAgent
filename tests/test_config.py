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
