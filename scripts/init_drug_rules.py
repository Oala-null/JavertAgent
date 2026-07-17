# -*- coding: utf-8 -*-
"""init_drug_rules — 建/装 药品类规则 (M8): 类型级 4 (R007/RD01-03) + 精选 ~29 (RD10+).

每条规则: write_rule 骨架 (含 drug_rule_type) → update_from_template_render(M8 渲染,
prompt_addon `|` 块 + derived_from_template=M8 + 保留 drug_rule_type).
类型级 R007/RD01-03 保持 ready；精选 RD10-RD37 保持 abandoned，避免与
类型级 bulk 规则重复审计、重复计违规。

精选药数据驱动: 每条都校验 (a) 在 drug_audit_kb.json (b) 在 output/drug_kb_hits.csv (无休眠药).

跑法: uv run python scripts/init_drug_rules.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from javert.audit.rule import Rule, Status  # noqa: E402
from javert.audit.rule_loader import load_rule  # noqa: E402
from javert.audit.rule_writer import update_from_template_render, write_rule  # noqa: E402
from javert.templating import load_template, render_template  # noqa: E402
from javert.tools.drug_audit_lookup import kb_entries, kb_stem  # noqa: E402

RULES_DIR = ROOT / "configs" / "rules"
M8_PATH = ROOT / "configs" / "templates" / "M8.yaml"
KB_PATH = ROOT / "configs" / "drug_audit_kb.json"
HITS_PATH = ROOT / "output" / "drug_kb_hits.csv"

VIOLATION_TYPE = {
    "限适应症": "超医保限定支付适应症用药",
    "超说明书": "超药品说明书适应症用药",
    "限二线": "超医保限定二线用药",
    "禁忌症": "用药安全/禁忌",  # 单列, 与 M1-M7 骗保类型可区分 (contraindication spec)
}
QUESTION_SUFFIX = {
    "限适应症": "超出医保限定支付适应症范围",
    "超说明书": "超出药品说明书适应症范围",
    "限二线": "无一线药失败/不耐受证据即作二线使用",
    "禁忌症": "用于存在该药说明书疾病禁忌的患者 (用药安全)",
}

# ───────────── 类型级 4 条 (工具 bulk 驱动, 全覆盖 928 药; trigger_kw=[] → router always-on) ─────────────
TYPE_LEVEL = [
    {
        "rule_id": "R007", "drug_rule_type": "限适应症", "domain": "各科室通用类",
        "target_desc": "限医保支付适应症的药品 (全覆盖该患者命中药)",
        "notes": "0325 R007 聚焦医保限定支付臂; 超说明书姊妹规则见 RD01. M8 限适应症类型级 (bulk 全覆盖).",
        # question/example/violation_type 沿用 R007 既有 0325 原文
    },
    {
        "rule_id": "RD01", "drug_rule_type": "超说明书", "domain": "药品",
        "target_desc": "超说明书适应症的药品 (全覆盖该患者命中药)",
        "notes": "M8 超说明书类型级 (bulk 全覆盖); 医保限定支付臂见 R007.",
    },
    {
        "rule_id": "RD02", "drug_rule_type": "限二线", "domain": "药品",
        "target_desc": "限二线支付的药品 (全覆盖该患者命中药)",
        "notes": "M8 限二线类型级. 文书常无结构化一线用药史 → INCONCLUSIVE 预期偏多 (设计风险, 单列 I 率).",
    },
    {
        "rule_id": "RD03", "drug_rule_type": "禁忌症", "domain": "药品",
        "target_desc": "存在说明书疾病禁忌的药品 (全覆盖该患者命中药)",
        "notes": "M8 禁忌症类型级. violation_type 单列「用药安全/禁忌」, 汇报口径与医保骗保违规分开.",
    },
]

# ───────────── 精选 (RD10+): (id, 通用名, rule_type, special_notes, pilot_caveat) ─────────────
# 数据驱动自 output/drug_kb_hits.csv (命中患者数 × 危险度); 含 on-label 闸用例 (甲状腺片/钙).
CURATED = [
    # 限适应症 — 辅助用药 / 限定支付重灾区
    ("RD10", "人血白蛋白", "限适应症", [], ""),
    ("RD11", "聚桂醇注射液", "限适应症", [], ""),
    ("RD12", "果糖注射液", "限适应症",
     ["果糖 stem 会命中甘油果糖/乳果糖等同名异药, 务必按原始 fee 名复核剂型再判."], ""),
    ("RD13", "ω-3鱼油脂肪乳注射液", "限适应症", [], ""),
    ("RD14", "注射用盐酸万古霉素", "限适应症", [], ""),
    ("RD15", "复方氨基酸注射液(20AA)", "限适应症", [], ""),
    ("RD16", "恩替卡韦口服溶液", "限适应症", [], ""),
    ("RD17", "注射用福沙匹坦双葡甲胺", "限适应症", [], ""),
    ("RD18", "右酮洛芬氨丁三醇注射液", "限适应症", [], ""),
    ("RD19", "重组人血小板生成素注射液", "限适应症", [], ""),
    ("RD20", "注射用艾普拉唑钠", "限适应症", [], ""),
    ("RD21", "泽布替尼胶囊", "限适应症", [], ""),
    ("RD22", "拓培非格司亭注射液", "限适应症", [], ""),
    ("RD23", "盐酸莫西沙星注射液", "限适应症", [], ""),
    # 超说明书
    ("RD24", "甲状腺片", "超说明书",
     ["甲状腺癌术后 / 甲状腺全切 / 任何甲减诊断使用甲状腺素片是替代治疗, 属对症 → CLEAN.",
      "甲状腺片 stem 命中左甲状腺素钠(优甲乐), 二者均为甲状腺激素替代, 同理判定."], ""),
    ("RD25", "盐酸尼卡地平注射液", "超说明书", [], ""),
    ("RD26", "盐酸二甲双胍片", "超说明书", [], ""),
    ("RD27", "苯磺酸氨氯地平片", "超说明书", [], ""),
    ("RD28", "缬沙坦片", "超说明书", [], ""),
    # 禁忌症 (用药安全)
    ("RD29", "奥美拉唑肠溶片", "禁忌症", [], ""),
    ("RD30", "葡萄糖酸钙片", "禁忌症",
     ["低钙血症 / 甲状腺术后低钙 / 钙缺乏患者补钙是对症, 仅当诊断命中高钙血症/含钙肾结石等禁忌才判 V."], ""),
    ("RD31", "碳酸钙片", "禁忌症",
     ["低钙 / 骨质疏松 / 妊娠期补钙是对症; 仅诊断命中高钙血症 / 含钙肾结石等禁忌才判 V."], ""),
    ("RD32", "对乙酰氨基酚片", "禁忌症", [], ""),
    ("RD33", "乙酰半胱氨酸颗粒", "禁忌症",
     ["说明书禁忌为哮喘患者; 诊断含支气管哮喘且用本药 → V (用药安全)."], ""),
    ("RD34", "甲氧氯普胺片", "禁忌症", [], ""),
    ("RD35", "阿司匹林片", "禁忌症", [], ""),
    ("RD36", "瑞舒伐他汀钙片", "禁忌症", [], ""),
    # 限二线
    ("RD37", "艾普拉唑肠溶片", "限二线", [], ""),
]

CURATED_STATUS: Status = "abandoned"


def _load_kb() -> dict:
    return json.loads(KB_PATH.read_text(encoding="utf-8"))["drugs"]


def _build_one(
    m8, kb, hit_set,
    *, rule_id, drug_rule_type, domain, target_desc,
    drug_focus="", special_notes=None, pilot_caveat="", trigger_kw=None,
    notes="", question=None, example="", violation_type=None, priority="P1",
    status: Status = "ready",
) -> str:
    special_notes = special_notes or []
    trigger_kw = trigger_kw or []
    vt = violation_type or VIOLATION_TYPE[drug_rule_type]
    q = question or (
        f"申请医保支付的「{drug_focus or drug_rule_type+'类药品'}」，"
        f"{QUESTION_SUFFIX[drug_rule_type]}。"
    )

    # 骨架含 drug_rule_type/status；prompt_addon 由下一步渲染填。
    skeleton = Rule(
        rule_id=rule_id, domain=domain, violation_type=vt, question=q, example=example,
        status=status, priority=priority, prompt_addon="", trigger_keywords=[],
        suggested_tools=[], expected_signal="", notes=notes,
        derived_from_template=None, drug_rule_type=drug_rule_type,
    )
    path = RULES_DIR / f"{rule_id}.yaml"
    write_rule(skeleton, path)

    rendered = render_template(m8, {
        "drug_rule_type": drug_rule_type,
        "target_desc": target_desc,
        "drug_focus": drug_focus,
        "special_notes": special_notes,
        "pilot_caveat": pilot_caveat,
        "trigger_kw": trigger_kw,
    })
    update_from_template_render(
        path,
        prompt_addon=rendered["prompt_addon"],
        template_id="M8",
        trigger_keywords=rendered.get("trigger_keywords"),
        suggested_tools=rendered.get("suggested_tools"),
        expected_signal=rendered.get("expected_signal"),
    )
    return f"{rule_id} [{drug_rule_type}] {status}"


def main() -> None:
    m8 = load_template(M8_PATH)
    kb = _load_kb()
    hits = pd.read_csv(HITS_PATH)
    hit_set = set(hits["通用名"])

    print("=== 类型级 4 条 (bulk 全覆盖, trigger_kw=[] always-on) ===")
    for spec in TYPE_LEVEL:
        rid = spec["rule_id"]
        q = spec.get("question")
        ex = spec.get("example", "")
        vt = spec.get("violation_type")
        # R007: 沿用既有 0325 question/example/violation_type
        if rid == "R007":
            existing = load_rule(RULES_DIR / "R007.yaml")
            q = q or existing.question
            ex = ex or existing.example
            vt = vt or existing.violation_type
        print("  " + _build_one(
            m8, kb, hit_set,
            rule_id=rid, drug_rule_type=spec["drug_rule_type"], domain=spec["domain"],
            target_desc=spec["target_desc"], notes=spec["notes"],
            question=q, example=ex, violation_type=vt, trigger_kw=[],
        ))

    print(
        f"\n=== 精选 {len(CURATED)} 条 "
        "(保留 abandoned；可用 --rules 显式单跑，不进入默认 router) ==="
    )
    for rid, drug, rt, notes_extra, caveat in CURATED:
        if drug not in kb:
            raise SystemExit(f"✗ {rid}: 「{drug}」不在 drug_audit_kb.json")
        if drug not in hit_set:
            raise SystemExit(f"✗ {rid}: 「{drug}」不在命中频次表 (休眠药, 不建精选规则)")
        kb_types = {e["rule_type"] for e in kb_entries(kb[drug])}
        if rt not in kb_types:
            raise SystemExit(f"✗ {rid}: 「{drug}」KB 类型 {kb_types} 不含 {rt}")
        stem = kb_stem(drug)
        trigger_kw = [drug] + ([stem] if stem != drug else [])
        notes = (
            f"精选药品规则 (M8 {rt}); 通用名「{drug}」, router 触发词 {trigger_kw}. "
            "由 bulk R007/RD01-03 独占, 消重复计违规 (fix-drug-audit-precision)."
        )
        print("  " + _build_one(
            m8, kb, hit_set,
            rule_id=rid, drug_rule_type=rt, domain="药品",
            target_desc=f"{drug} ({rt})", drug_focus=drug,
            special_notes=notes_extra, pilot_caveat=caveat,
            trigger_kw=trigger_kw, notes=notes, status=CURATED_STATUS,
        ))

    print(f"\n✓ 完成: 类型级 4 + 精选 {len(CURATED)} = {4 + len(CURATED)} 条 M8 规则")


if __name__ == "__main__":
    main()
