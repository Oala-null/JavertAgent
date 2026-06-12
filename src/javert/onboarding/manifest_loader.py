# -*- coding: utf-8 -*-
"""manifest_loader — schema_manifest.yaml 校验加载 (数据模型唯一真相源).

设计 D1: UI 渲染 / ETL 转换 / tool registry 注册三处共读本模块, 杜绝字段表漂移.

公开 API:
    load_manifest(path=None) -> Manifest
    Manifest.spoke(key) / .live_spokes() / .view_spokes() / .tabular_spokes()
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_MANIFEST = PROJECT_ROOT / "configs" / "schema_manifest.yaml"

Status = Literal["live", "view", "stored"]


class ManifestError(ValueError):
    """manifest 结构非法 — status 取值非法 / 必填字段缺失 / join_key 不存在等."""


class FieldSpec(BaseModel):
    """spoke 的一个语义字段. key 与 column_mapping 的语义键一致."""

    model_config = ConfigDict(extra="forbid")

    key: str
    name: str
    required: bool = False
    targets: list[str] = Field(default_factory=list)
    is_date: bool = False


class Bridge(BaseModel):
    """桥表归一声明 (设计 D3): 经 table 把 source_col 归一到 canonical_col."""

    model_config = ConfigDict(extra="forbid")

    table: str
    source_col: str
    canonical_col: str


class Spoke(BaseModel):
    """一个数据分支 (费用/文书/诊断/手术/化验/检查/麻醉/病理/新声明表)."""

    model_config = ConfigDict(extra="forbid")

    key: str = ""  # 由 Manifest 注入 (yaml 里是 dict 键)
    name: str
    status: Status
    loader: str | None = None
    tool: str | None = None
    output_file: str | None = None
    id_form: Literal["compound", "bare"] | None = None
    id_column: str | None = None
    join_key: str | None = None
    via_bridge: Bridge | None = None
    output_schema: list[str] = Field(default_factory=list)
    split_sections: bool = False
    synth_seq: str | None = None
    const_columns: dict[str, str] = Field(default_factory=dict)
    derived_from: list[str] = Field(default_factory=list)
    fields: list[FieldSpec] = Field(default_factory=list)

    @property
    def is_tabular(self) -> bool:
        """tabular = 有独立物理输出表 (live/stored 且声明了 output_file)."""
        return self.output_file is not None

    @property
    def required_keys(self) -> list[str]:
        return [f.key for f in self.fields if f.required]

    def field(self, key: str) -> FieldSpec | None:
        for f in self.fields:
            if f.key == key:
                return f
        return None

    @model_validator(mode="after")
    def _check(self) -> "Spoke":
        label = self.key or self.name
        # tabular spoke 的结构校验
        if self.is_tabular:
            if not self.output_schema:
                raise ManifestError(f"spoke '{label}': tabular 但缺 output_schema")
            if self.id_form not in ("compound", "bare"):
                raise ManifestError(
                    f"spoke '{label}': tabular 必须声明合法 id_form (compound/bare)"
                )
            if not self.id_column:
                raise ManifestError(f"spoke '{label}': tabular 必须声明 id_column")
            if self.id_column not in self.output_schema:
                raise ManifestError(
                    f"spoke '{label}': id_column '{self.id_column}' 不在 output_schema"
                )
            # join_key (本表连 hub 用哪列的语义默认) 必须存在
            if not self.join_key:
                raise ManifestError(f"spoke '{label}': tabular 必须声明 join_key")
            if not self.required_keys:
                raise ManifestError(f"spoke '{label}': tabular 至少 1 个 required 字段")
            # 字段 targets 必须落在 output_schema 内
            for f in self.fields:
                for t in f.targets:
                    if t not in self.output_schema:
                        raise ManifestError(
                            f"spoke '{label}': 字段 '{f.key}' target '{t}' 不在 output_schema"
                        )
            # synth_seq / const_columns 列也要在 schema 内
            if self.synth_seq and self.synth_seq not in self.output_schema:
                raise ManifestError(
                    f"spoke '{label}': synth_seq '{self.synth_seq}' 不在 output_schema"
                )
            for c in self.const_columns:
                if c not in self.output_schema:
                    raise ManifestError(
                        f"spoke '{label}': const_columns 列 '{c}' 不在 output_schema"
                    )
            # 字段 key 不得重复
            seen: set[str] = set()
            for f in self.fields:
                if f.key in seen:
                    raise ManifestError(f"spoke '{label}': 字段 key '{f.key}' 重复")
                seen.add(f.key)
        else:
            # view/stored spoke: 不应有 tabular-only 字段
            if self.status == "view" and not self.tool:
                raise ManifestError(f"spoke '{label}': view spoke 必须声明 tool (视图工具)")
        return self


class Hub(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "患者"
    forms: list[str] = Field(default_factory=lambda: ["bare", "compound"])


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    hub: Hub = Field(default_factory=Hub)
    spokes: dict[str, Spoke]

    @model_validator(mode="after")
    def _check(self) -> "Manifest":
        for key, spoke in self.spokes.items():
            spoke.key = key
        # view spoke 的 derived_from 必须引用已存在 spoke
        for key, spoke in self.spokes.items():
            for src in spoke.derived_from:
                if src not in self.spokes:
                    raise ManifestError(
                        f"spoke '{key}': derived_from 引用了不存在的 spoke '{src}'"
                    )
        return self

    def spoke(self, key: str) -> Spoke:
        if key not in self.spokes:
            raise KeyError(f"manifest 无 spoke '{key}'")
        return self.spokes[key]

    def live_spokes(self) -> dict[str, Spoke]:
        return {k: s for k, s in self.spokes.items() if s.status == "live"}

    def view_spokes(self) -> dict[str, Spoke]:
        return {k: s for k, s in self.spokes.items() if s.status == "view"}

    def tabular_spokes(self) -> dict[str, Spoke]:
        """有独立物理输出表的 spoke (供 ETL 遍历)."""
        return {k: s for k, s in self.spokes.items() if s.is_tabular}


def load_manifest(path: Path | str | None = None) -> Manifest:
    """读取并校验 schema_manifest.yaml.

    Raises:
        ManifestError: 结构非法 (status 取值 / 必填字段 / join_key / output_schema 等).
    """
    p = Path(path) if path else DEFAULT_MANIFEST
    if not p.exists():
        raise FileNotFoundError(f"manifest 不存在: {p}")
    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ManifestError(f"manifest 顶层必须是 mapping, 实际 {type(raw).__name__}")
    try:
        return Manifest.model_validate(raw)
    except ValidationError as e:
        raise ManifestError(f"manifest 校验失败:\n{e}") from e


@lru_cache(maxsize=4)
def get_manifest(path: str | None = None) -> Manifest:
    """进程内缓存的 manifest 单例."""
    return load_manifest(path)


def reset_manifest_cache() -> None:
    get_manifest.cache_clear()
