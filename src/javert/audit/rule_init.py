# -*- coding: utf-8 -*-
"""Rule init — 从 0325 xls 生成 34 条 pilot yaml.

0325 表实为 .xls 文件 (扩展名 .csv 是误命名), 用 xlrd 读.
Pilot 子集 (34): 肿瘤 7 + 各科室通用类 10 + 临床检验 17 (硬编码白名单).
"""

from __future__ import annotations

import logging
from pathlib import Path

import xlrd

from .rule import Rule
from .rule_writer import write_rule

logger = logging.getLogger("javert.audit.rule_init")

# 选取标准: 仅依赖 search_notes / search_fees / note_diagnosis / drug_indication
# 不需要项目内涵字典 / 计价规则字典. 临床检验取 13 条过度检查 + 4 条简单串换.
PILOT_RULE_IDS: list[str] = [
    # 肿瘤 7
    "R185", "R191", "R193", "R196", "R200", "R201", "R202",
    # 各科室通用类 10
    "R001", "R002", "R003", "R004", "R005", "R006", "R007", "R010", "R011", "R012",
    # 临床检验 17 (13 过度检查 + R134/R135 + 4 简单串换)
    "R134", "R135",
    "R141", "R143", "R146", "R151", "R153", "R154", "R155", "R156", "R160", "R161", "R162",
    "R170", "R173", "R174", "R178",
]


def load_xls_rules(xls_path: Path) -> dict[str, Rule]:
    """读取 0325 xls, 返回 {rule_id: Rule} 全 163 条「做不了」."""
    if not xls_path.exists():
        raise FileNotFoundError(f"0325 表不存在: {xls_path}")
    wb = xlrd.open_workbook(str(xls_path))
    sh = wb.sheet_by_index(0)
    # 表头在第二行, 数据从第三行开始
    out: dict[str, Rule] = {}
    for r in range(2, sh.nrows):
        row = [sh.cell_value(r, c) for c in range(sh.ncols)]
        if str(row[7]).strip() != "做不了":
            continue
        try:
            seq = int(row[0])
        except (TypeError, ValueError):
            continue
        rule_id = f"R{seq:03d}"
        rule = Rule(
            rule_id=rule_id,
            domain=str(row[1]).strip(),
            violation_type=str(row[4]).strip(),
            question=str(row[2]).strip(),
            example=str(row[5]).strip(),
            status="drafting",
            prompt_addon="",
            trigger_keywords=[],
            suggested_tools=[],
            expected_signal="",
            notes="",
        )
        out[rule_id] = rule
    return out


def init_pilot_rules(
    xls_path: Path,
    rules_dir: Path,
    pilot_ids: list[str] | None = None,
    force: bool = False,
) -> dict[str, str]:
    """生成 34 条 pilot yaml.

    Args:
        xls_path: 0325.xls 路径.
        rules_dir: configs/rules 目录.
        pilot_ids: 想生成的 rule_id 列表; None 时用默认 PILOT_RULE_IDS.
        force: True 时覆盖已存在文件; False 时 skip.

    Returns:
        {rule_id: action} 其中 action ∈ {created, skipped, missing_in_xls}
    """
    rules_dir.mkdir(parents=True, exist_ok=True)
    pilot_ids = pilot_ids or PILOT_RULE_IDS
    all_rules = load_xls_rules(xls_path)
    actions: dict[str, str] = {}
    for rid in pilot_ids:
        rule = all_rules.get(rid)
        if rule is None:
            actions[rid] = "missing_in_xls"
            logger.warning("xls 中无此 rule_id: %s", rid)
            continue
        path = rules_dir / f"{rid}.yaml"
        if path.exists() and not force:
            actions[rid] = "skipped"
            continue
        write_rule(rule, path)
        actions[rid] = "created"
    created = sum(1 for v in actions.values() if v == "created")
    skipped = sum(1 for v in actions.values() if v == "skipped")
    missing = sum(1 for v in actions.values() if v == "missing_in_xls")
    logger.info("rule init: created=%d skipped=%d missing=%d", created, skipped, missing)
    return actions
