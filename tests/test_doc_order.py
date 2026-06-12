# -*- coding: utf-8 -*-
"""tests for web/doc_order.py — 文书临床序分桶 (redesign-review-card-and-source D3).

覆盖 spec 场景: 查房→病程 / 同意书→知情 / 未知→其他 / 子串冲突优先级 / 显示序.
"""

from __future__ import annotations

import pytest

from javert.web.doc_order import bucket_of, bucket_order_index


# spec 场景: 医师命名的查房记录 → 病程记录
def test_ward_round_named_by_doctor_is_progress():
    assert bucket_of("戴佳奇主治医师首次查房记录")[0] == "病程记录"
    assert bucket_of("殷志强主任医师首次查房记录")[0] == "病程记录"


# spec 场景: 各类知情书
@pytest.mark.parametrize("stage", [
    "手术知情同意书",
    "高值医用耗材使用告知书",
    "同意接受特殊检查（治疗）志愿书",
    "授权委托书",
    "麻醉知情同意书",
    "术中快速冷冻切片患方知情同意书",
])
def test_consent_forms_to_knowledge(stage):
    assert bucket_of(stage)[0] == "知情书"


# spec 场景: 未知 阶段 → 其他 (末位, 不丢)
def test_unknown_stage_trailing_bucket():
    name, order = bucket_of("某种没有任何关键词的奇怪阶段")
    assert name == "其他"
    assert order == 99


# 子串冲突: 「24小时入出院记录」含「出院记录」子串, 但应归入院 (入院桶早于出院桶)
def test_admission_wins_over_discharge_substring():
    assert bucket_of("24小时入出院记录")[0] == "入院记录"
    assert bucket_of("入院记录")[0] == "入院记录"


# 证书类: 疾病证明→出院; 休假证明→知情 (出院桶早于知情, 「疾病证明」优先匹中)
def test_certificate_disambiguation():
    assert bucket_of("疾病证明书")[0] == "出院小结"
    assert bucket_of("职工住院病伤娩休假证明书")[0] == "知情书"


# 含术语但归表单: 申请报告单 / 告知书 不被病程的「疑难」「术后」截走
def test_form_suffix_beats_progress_keyword():
    assert bucket_of("重大疑难手术申请报告单(新)")[0] == "知情书"
    assert bucket_of("术后情况告知书")[0] == "知情书"


# 手术 vs 知情: 手术记录归手术, 手术知情同意书归知情
def test_surgery_record_vs_consent():
    assert bucket_of("手术记录")[0] == "手术记录"
    assert bucket_of("操作记录")[0] == "手术记录"
    assert bucket_of("PICC操作记录")[0] == "手术记录"
    assert bucket_of("手术知情同意书")[0] == "知情书"


# 出院相关 (含手术科室后缀) → 出院 (出院桶早于手术)
def test_discharge_variants():
    assert bucket_of("出院小结")[0] == "出院小结"
    assert bucket_of("出院小结（手术科室）")[0] == "出院小结"
    assert bucket_of("死亡小结")[0] == "出院小结"
    assert bucket_of("死亡记录")[0] == "出院小结"


# 病案首页 / 康复病例首页
def test_homepage():
    assert bucket_of("病案首页")[0] == "病案首页"
    assert bucket_of("康复病例首页")[0] == "病案首页"


# 显示序: 病案首页1 < 入院2 < 病程3 < 手术4 < 知情5 < 出院6 < 其他99
def test_display_order_ranks():
    assert bucket_of("病案首页")[1] == 1
    assert bucket_of("入院记录")[1] == 2
    assert bucket_of("术前讨论")[1] == 3
    assert bucket_of("手术记录")[1] == 4
    assert bucket_of("手术知情同意书")[1] == 5
    assert bucket_of("出院小结")[1] == 6
    idx = bucket_order_index()
    assert idx["病案首页"] < idx["入院记录"] < idx["病程记录"] < idx["手术记录"] \
        < idx["知情书"] < idx["出院小结"] < idx["其他"]


# 空 / None → 其他, 永不抛
def test_empty_and_none():
    assert bucket_of("")[0] == "其他"
    assert bucket_of(None)[0] == "其他"
    assert bucket_of("   ")[0] == "其他"
