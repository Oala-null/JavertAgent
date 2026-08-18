# -*- coding: utf-8 -*-
"""catalog_lookup — 按编码/名称和服务日期查询版本化诊疗目录。"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
import re
import unicodedata
from typing import Callable

import pandas as pd

DESCRIPTION = (
    "查询本地版本化诊疗项目目录。支持国家医保编码/医保编码/项目编码精确匹配，"
    "或项目名称保守匹配；service_date 会过滤尚未生效或已经失效的条目。"
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "item_code": {"type": "string", "description": "可选，国家医保编码/医保编码/项目编码"},
        "item_name": {"type": "string", "description": "可选，收费项目名称"},
        "service_date": {"type": "string", "description": "可选，服务日期 YYYY-MM-DD"},
    },
}

_COLUMNS = [
    "医保编码", "状态", "信息起效日期", "信息失效日期", "项目编码", "项目名称",
    "项目内涵", "计价单位", "收费标准", "备注", "限定内容", "费用类别", "国家医保编码",
]
_CODE_COLUMNS = ("国家医保编码", "医保编码", "项目编码")
_MAX_ROWS = 10


def _normalize_code(value: object) -> str:
    return str(value or "").strip().split("-", 1)[0]


def _normalize_name(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s+", "", text).split("/", 1)[0]
    return text[:-1] if text.endswith("费") else text


def _date_token(value: object, default: str) -> str:
    token = re.sub(r"\D", "", str(value or ""))[:8]
    return token if len(token) == 8 else default


@lru_cache(maxsize=4)
def _load_catalog(path_tokens: tuple[str, ...]) -> pd.DataFrame:
    frames = []
    for token in path_tokens:
        path = Path(token)
        if not path.exists():
            continue
        frame = pd.read_excel(path, dtype=str).fillna("")
        for column in _COLUMNS:
            if column not in frame.columns:
                frame[column] = ""
        frame = frame[_COLUMNS].copy()
        frame["_source"] = path.name
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=[*_COLUMNS, "_source", "_name", "_start", "_end"])
    catalog = pd.concat(frames, ignore_index=True).drop_duplicates(subset=_COLUMNS, keep="last")
    catalog["_name"] = catalog["项目名称"].map(_normalize_name)
    catalog["_start"] = catalog["信息起效日期"].map(lambda value: _date_token(value, "00000000"))
    catalog["_end"] = catalog["信息失效日期"].map(lambda value: _date_token(value, "99991231"))
    return catalog


def _active(rows: pd.DataFrame, service_date: str | None) -> pd.DataFrame:
    if not service_date:
        return rows
    try:
        token = date.fromisoformat(service_date).strftime("%Y%m%d")
    except ValueError as exc:
        raise ValueError("service_date 必须是 YYYY-MM-DD") from exc
    return rows[(rows["_start"] <= token) & (rows["_end"] >= token)]


def create_executor(paths: list[Path]) -> Callable[..., str]:
    path_tokens = tuple(str(path.resolve()) for path in paths)

    def execute(
        item_code: str | None = None,
        item_name: str | None = None,
        service_date: str | None = None,
        **_kwargs,
    ) -> str:
        if not (item_code or item_name):
            return "诊疗目录查询至少需要 item_code 或 item_name"
        catalog = _load_catalog(path_tokens)
        if catalog.empty:
            return "诊疗目录资产未就绪"

        matched = catalog.iloc[0:0]
        match_mode = ""
        if item_code:
            code = _normalize_code(item_code)
            code_mask = pd.Series(False, index=catalog.index)
            for column in _CODE_COLUMNS:
                code_mask |= catalog[column].map(_normalize_code) == code
            matched = _active(catalog[code_mask], service_date)
            match_mode = "编码精确"
        if matched.empty and item_name:
            name = _normalize_name(item_name)
            matched = _active(catalog[catalog["_name"] == name], service_date)
            match_mode = "名称规范化"
        if matched.empty:
            date_text = f"、服务日期 {service_date}" if service_date else ""
            return f"诊疗目录无有效匹配（编码={item_code or '-'}、名称={item_name or '-'}{date_text}）"

        lines = [f"诊疗目录匹配（{match_mode}，有效候选 {len(matched)} 条）:"]
        for index, (_, row) in enumerate(matched.head(_MAX_ROWS).iterrows(), 1):
            codes = "/".join(str(row[column]) for column in _CODE_COLUMNS if str(row[column]).strip()) or "无编码"
            lines.append(
                f"[{index}] {row['项目名称']} | 编码={codes} | 计价单位={row['计价单位'] or '未注明'} "
                f"| 收费标准={row['收费标准'] or '未注明'} | 有效期={row['_start']}~{row['_end']}"
            )
            if row["备注"]:
                lines.append(f"    备注: {row['备注']}")
            if row["限定内容"]:
                lines.append(f"    限定: {row['限定内容']}")
            if row["项目内涵"]:
                content = str(row["项目内涵"])
                lines.append(f"    内涵: {content[:360]}{'...' if len(content) > 360 else ''}")
        if len(matched) > _MAX_ROWS:
            lines.append(f"... 仅显示前 {_MAX_ROWS} 条，请增加编码缩小范围。")
        return "\n".join(lines)

    return execute
