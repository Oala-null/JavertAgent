# -*- coding: utf-8 -*-
"""Javert 配置 — yaml 文件 + JAVERT_* 环境变量覆盖."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class JavertConfig(BaseSettings):
    """全局配置. 环境变量前缀 `JAVERT_` 直接覆盖任意字段."""

    model_config = SettingsConfigDict(
        env_prefix="JAVERT_",
        # 项目根 .env 自动兜底 (优先级最低: 显式 env > yaml > .env 文件 > 字段默认).
        # 凭证移出源码后, 忘 source .env 的 CLI/批跑进程也能拿到 JAVERT_SQL_PASSWORD.
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM
    llm_endpoint: str = "http://127.0.0.1:30000/v1"  # 环境指向由 .env 提供, 代码不带内网地址
    llm_model: str = "Qwen/Qwen3.6-35B-A3B-FP8"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 8192
    llm_timeout: int = 300
    llm_enable_thinking: bool = False

    # Runner
    max_tool_calls: int = 10
    retry_budget: int = 3
    # 单工具结果喂 LLM 前的截断上限 (fix-drug-audit-precision D2). 含必留标记的
    # 工具结果只截明细段, 头部整段保全.
    tool_result_max_chars: int = 2000

    # add-verdict-gate-layer: 裁决后确定性 gate 层开关. env JAVERT_VERDICT_GATE=off 直通 (回滚).
    verdict_gate: str = "on"

    # pilot-deterministic-precheck: M1 确定性预检开关. env JAVERT_PRECHECK=off 直通 (回滚).
    precheck: str = "on"

    # recover-deterministic-recall: persist 层重跑漂移防护开关 (老 V 新 C → 落 I + 标签).
    # env JAVERT_DRIFT_GUARD=off 直通 (回滚, 落库行为与本 change 之前逐字一致).
    drift_guard: str = "on"

    # Paths (字符串, 相对项目根)
    data_dir: str = "data"
    rules_dir: str = "configs/rules"
    templates_dir: str = "configs/templates"
    audit_db: str = "output/audit.sqlite"
    prompts_dir: str = "src/javert/audit/prompts"
    upstream_zadig_data: str = "../zadig_agent/data"
    notes_file: str = "case_notes.csv"
    fees_file: str = "shi_fee.csv"
    zd_file: str = "shi_zd.xls"
    ss_file: str = "shi_ss.xls"
    examinations_file: str = "sy_patient_examination.csv"
    labs_file: str = "sy_检验.csv"
    pilot_roster_file: str = "pilot_patients.txt"
    hospital_config: str = "configs/hospital_config.yaml"

    # v0.7: batch tag — 写入 audit_runs 时打上, 工作台 sidebar 显示 + 排序.
    # 默认 None = 不标 (baseline); CLI 跑 v1.2 重跑前 export JAVERT_BATCH_TAG=v1.2
    batch_tag: str | None = None

    # SQL Server 142 双写归档 (env: JAVERT_SQL_*)
    sql_enabled: bool = True
    sql_host: str = "127.0.0.1"
    sql_port: int = 1433
    sql_user: str = "sa"
    # 凭证不入源码 (进院前红区修复): 从 JAVERT_SQL_PASSWORD 环境变量注入 (source .env).
    # 未设置时 142 双写自动降级不可用 (sqlserver_store warn), 本地 SQLite 不受影响.
    sql_password: str = ""
    sql_database: str = "zadig"
    # 快照桥 (scripts/etl_from_sql.py) 的源数据库: 投资人/外院兜底数据填这里的 6 张 intake_* 表.
    # 与结果归档库 sql_database=zadig 区分 (同台 142 / 同账号, 桥读 aidb, 审计结果仍写 zadig).
    sql_source_database: str = "aidb"
    # add-workbench-sql-raw-source: 工作台原文 hub SQL 源 (env: JAVERT_HUB_RAW_ENABLED /
    # JAVERT_HUB_DATABASE). 默认关 = 纯 CSV 行为不变; 开启后 CSV 双 miss 时按患者号查 hub.
    hub_raw_enabled: bool = False
    hub_database: str = "sh_yb_platform"
    sql_driver: str = "ODBC Driver 18 for SQL Server"
    sql_pool_size: int = 5
    sql_max_overflow: int = 5
    sql_pool_timeout: int = 30

    # Web 服务 (env: JAVERT_WEB_*)
    web_host: str = "127.0.0.1"
    web_port: int = 8090
    web_reload: bool = False
    web_with_mssql: bool = True  # CLI --with-mssql / --no-mssql 覆盖

    # 工作台鉴权 (env: JAVERT_SESSION_*)
    session_secret: str = "javert-dev-change-me-in-production-32-chars-min"
    session_max_age: int = 60 * 60 * 24 * 30  # 30 天
    session_cookie_name: str = "javert_session"
    session_https_only: bool = False  # 内网部署暂为 false; 上 nginx + TLS 后 true
    session_same_site: str = "lax"

    # 注册开关 (env: JAVERT_ALLOW_REGISTER) — 默认关; 运维走 `javert mssql-user`
    # 或 SSMS 直接 INSERT
    allow_register: bool = False

    # harden-onsite-redlines: /api/patient/{pid}/raw 每会话限流档位 (slowapi 语法).
    # 30/min 不影响专家逐个点开病历; 现场误伤时 env JAVERT_RAW_RATE_LIMIT 一行可调.
    raw_rate_limit: str = "30/minute"

    # 解析为绝对路径
    def resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    @property
    def data_path(self) -> Path:
        return self.resolve(self.data_dir)

    @property
    def rules_path(self) -> Path:
        return self.resolve(self.rules_dir)

    @property
    def templates_path(self) -> Path:
        return self.resolve(self.templates_dir)

    @property
    def audit_db_path(self) -> Path:
        return self.resolve(self.audit_db)

    @property
    def prompts_path(self) -> Path:
        return self.resolve(self.prompts_dir)

    @property
    def notes_path(self) -> Path:
        return self.data_path / self.notes_file

    @property
    def fees_path(self) -> Path:
        return self.data_path / self.fees_file

    @property
    def zd_path(self) -> Path:
        return self.data_path / self.zd_file

    @property
    def ss_path(self) -> Path:
        return self.data_path / self.ss_file

    @property
    def examinations_path(self) -> Path:
        return self.data_path / self.examinations_file

    @property
    def labs_path(self) -> Path:
        return self.data_path / self.labs_file

    @property
    def pilot_roster_path(self) -> Path:
        return self.data_path / self.pilot_roster_file

    @property
    def upstream_data_path(self) -> Path:
        return self.resolve(self.upstream_zadig_data)

    @property
    def hospital_config_path(self) -> Path:
        return self.resolve(self.hospital_config)


def load_config(config_file: Path | str | None = None) -> JavertConfig:
    """读取 yaml 文件 + JAVERT_* 环境变量覆盖.

    优先级: 环境变量 (`JAVERT_*`) > yaml 文件 > 字段默认值.

    Args:
        config_file: yaml 文件路径; None 时使用 `configs/llm.yaml`.
    """
    path = Path(config_file) if config_file else PROJECT_ROOT / "configs" / "llm.yaml"
    file_data: dict[str, Any] = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"配置文件 {path} 顶层必须是 mapping, 实际是 {type(loaded).__name__}")
            file_data = loaded
    # 显式收集 JAVERT_* 环境变量, 让它们盖住 file_data
    # (pydantic-settings 在收到 kwargs 时会忽略 env, 所以手动 merge)
    env_overrides: dict[str, Any] = {}
    for field_name in JavertConfig.model_fields:
        env_key = f"JAVERT_{field_name.upper()}"
        if env_key in os.environ:
            env_overrides[field_name] = os.environ[env_key]
    merged = {**file_data, **env_overrides}
    return JavertConfig(**merged)


@lru_cache(maxsize=1)
def get_config() -> JavertConfig:
    """进程内单例缓存的配置."""
    return load_config()


def reset_config_cache() -> None:
    """供测试用途重置 `get_config()` 缓存."""
    get_config.cache_clear()
