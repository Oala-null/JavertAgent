# -*- coding: utf-8 -*-
"""tests for web/hit_resolver.py (evidence-anchoring G2).

确定性纯函数 resolve_hits: drug 违规出码+限定+锚点 / byte-identical / 仅 etl_warning
空 / 同名多规格按本患者行 / 缺码降级 / 锚点四级阶梯 / 非药品规则无限定.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from javert.web.hit_resolver import (
    HitItem,
    resolve_hits_from_json,
)


# =========================================================
# 合成 fixtures (不依赖真实库/CSV, 完全确定)
# =========================================================
KB = {
    "注射用福沙匹坦双葡甲胺": [
        {"rule_type": "限适应症", "basis": "限放化疗所致恶心呕吐的预防。", "detect_logic": "x"}
    ],
    "布地奈德肠溶胶囊": [
        {"rule_type": "限适应症", "basis": "限具有进展风险的原发性免疫球蛋白A肾病(IgAN)成人患者。"}
    ],
    "甘露醇注射液": [
        {"rule_type": "限适应症", "basis": "限脑水肿/青光眼等。"}
    ],
}


def _fee_df(rows):
    return pd.DataFrame(
        rows,
        columns=["medins_list_name", "med_list_codg", "medins_list_codg"],
    )


J26355_FEES = _fee_df([
    ["注射用福沙匹坦双葡甲胺", "XA04AD012", "210999111"],
    ["(集)(基)吸入用布地奈德(天晴速畅)混悬液", "XR03BAB165L023010201523", "07310191300"],
    ["(基)吸入用布地奈德(普米克令舒)混悬液", "XR03BAB165L023020378239", "07310191304"],
    ["脑功能成像", "S21020000300010", "210200003"],
    # 缺码降级: 名匹配但编码列空
    ["甘露醇注射液", "", ""],
])


def _ev(*items):
    return json.dumps(list(items), ensure_ascii=False)


def _tc(*calls):
    return json.dumps(list(calls), ensure_ascii=False)


# =========================================================
# 场景 1: drug 违规出码 + 限定 + 锚点
# =========================================================
def test_resolve_drug_violation_code_restriction_anchor():
    ev = _ev(
        {
            "source": "drug_audit_lookup",
            "locator": "注射用福沙匹坦双葡甲胺",
            "text": "通用名「注射用福沙匹坦双葡甲胺」，限定适应症不符。",
        }
    )
    tc = _tc({"tool_name": "drug_audit_lookup",
              "arguments": {"patient_id": "J26355", "rule_type": "限适应症"}})
    hits = resolve_hits_from_json(ev, tc, "限适应症", J26355_FEES, KB)
    assert len(hits) == 1
    h = hits[0]
    assert h.source == "drug"
    assert h.name == "注射用福沙匹坦双葡甲胺"
    assert h.code_nat == "XA04AD012"
    assert h.code_local == "210999111"
    assert "放化疗" in h.restriction
    assert h.anchor.tab == "fees"
    assert h.anchor.query  # 有可高亮的 fee 名
    assert h.anchor.unresolved is False


# =========================================================
# 场景 2: 纯函数二次调用 byte-identical
# =========================================================
def test_resolver_is_pure_and_repeatable():
    ev = _ev(
        {"source": "drug_audit_lookup", "locator": "布地奈德肠溶胶囊", "text": "x"},
        {"source": "note_diagnosis", "locator": "患者诊断列表",
         "text": "患者诊断「腹腔肿瘤」，未见「IgA肾病」。"},
    )
    tc = _tc()
    a = resolve_hits_from_json(ev, tc, "限适应症", J26355_FEES, KB)
    b = resolve_hits_from_json(ev, tc, "限适应症", J26355_FEES, KB)
    dump = lambda hs: json.dumps([h.model_dump() for h in hs], ensure_ascii=False)
    assert dump(a) == dump(b)


# =========================================================
# 场景 3: etl_warning 不锚 (缺失标记); lab/exam surface 为可见命中项目
# (add-verdict-gate-layer: lab/exam 不再静默丢弃)
# =========================================================
def test_etl_warning_dropped_lab_exam_surfaced():
    ev = _ev(
        {"source": "etl_warning", "locator": "ETL_GAP: 麻醉记录", "text": "..."},
        {"source": "search_lab_results", "locator": "血小板检验", "text": "PLT 210"},
        {"source": "search_examinations", "locator": "CT", "text": "未见异常"},
    )
    hits = resolve_hits_from_json(ev, _tc(), None, J26355_FEES, KB)
    sources = [h.source for h in hits]
    # etl_warning 被丢弃, lab/exam 各 surface 一条
    assert "lab" in sources
    assert "exam" in sources
    assert all(s != "etl_warning" for s in sources)
    lab = next(h for h in hits if h.source == "lab")
    assert lab.name == "血小板检验"


# =========================================================
# 场景 4: 同名多规格 → 按本患者实际 fee 行列出 + 剂型复核标注
# =========================================================
def test_same_generic_multiple_specs_lists_per_row():
    ev = _ev({"source": "drug_audit_lookup", "locator": "布地奈德肠溶胶囊",
              "text": "通用名「布地奈德肠溶胶囊」"})
    hits = resolve_hits_from_json(ev, _tc(), "限适应症", J26355_FEES, KB)
    # 天晴速畅 + 普米克令舒 两行两码
    assert len(hits) == 2
    codes = {h.code_nat for h in hits}
    assert codes == {"XR03BAB165L023010201523", "XR03BAB165L023020378239"}
    for h in hits:
        # 原始 fee 名 verbatim 带出
        assert "吸入用布地奈德" in h.matched_fee_name
        # 剂型不符 (肠溶胶囊 vs 混悬液) → 复核标注, 不冒充确证
        assert h.review_note == "按通用名匹配, 剂型/复方需复核"
        assert "IgAN" in h.restriction


# =========================================================
# 场景 5: 缺码降级 — 名匹配但编码列空, 仍渲染
# =========================================================
def test_missing_code_degrades_gracefully():
    ev = _ev({"source": "drug_audit_lookup", "locator": "甘露醇注射液",
              "text": "通用名「甘露醇注射液」"})
    hits = resolve_hits_from_json(ev, _tc(), "限适应症", J26355_FEES, KB)
    assert len(hits) == 1
    h = hits[0]
    assert h.code_nat == ""
    assert h.code_local == ""
    assert h.name == "甘露醇注射液"
    assert "脑水肿" in h.restriction  # 仍带限定


def test_fee_code_from_patient_row():
    """脑功能成像 → 取该 patient fee 行的 med_list_codg / medins_list_codg."""
    ev = _ev({"source": "search_fees", "locator": "脑功能成像", "text": "脑功能成像费用"})
    hits = resolve_hits_from_json(ev, _tc(), None, J26355_FEES, KB)
    assert len(hits) == 1
    assert hits[0].code_nat == "S21020000300010"
    assert hits[0].code_local == "210200003"


# =========================================================
# 场景 6: 锚点阶梯四级各一例
# =========================================================
def test_anchor_ladder_keyword():
    ev = _ev({"source": "search_notes", "locator": "检查记录",
              "text": "患者行脑功能成像检查无明确指征"})
    tc = _tc({"tool_name": "search_notes",
              "arguments": {"patient_id": "X", "keyword": "脑功能成像"}})
    h = resolve_hits_from_json(ev, tc, None, None, KB)[0]
    assert h.anchor.match_level == "keyword"
    assert h.anchor.query == "脑功能成像"
    # char offset 精确指向 evidence.text
    assert ev_text_slice(ev, h) == "脑功能成像"


def ev_text_slice(ev_json, hit):
    text = json.loads(ev_json)[0]["text"]
    return text[hit.anchor.char_start:hit.anchor.char_end]


def test_anchor_ladder_locator():
    ev = _ev({"source": "note_diagnosis", "locator": "出院诊断",
              "text": "诊断为「甲状腺乳头状癌」"})
    h = resolve_hits_from_json(ev, _tc(), None, None, KB)[0]
    assert h.anchor.match_level == "locator"
    assert h.anchor.subsection == "出院诊断"
    assert h.anchor.query == "甲状腺乳头状癌"  # 软 query 从引文取


def test_anchor_ladder_text():
    ev = _ev({"source": "search_notes", "locator": "",
              "text": "现病史见「反复发热三天」记录"})
    h = resolve_hits_from_json(ev, _tc(), None, None, KB)[0]
    assert h.anchor.match_level == "text"
    assert h.anchor.subsection == ""
    assert h.anchor.query == "反复发热三天"


def test_anchor_ladder_text_ngram_no_quote():
    """rung 3 真 n-gram 分支: 无 「」 引文 → 取最长 CJK run (非 quoted 分支)."""
    ev = _ev({"source": "search_notes", "locator": "",
              "text": "患者多次行脑功能成像检查 完全无指征"})
    h = resolve_hits_from_json(ev, _tc(), None, None, KB)[0]
    assert h.anchor.match_level == "text"
    assert h.anchor.subsection == ""
    # 空格断开 → 取最长 CJK run
    assert h.anchor.query == "患者多次行脑功能成像检查"


def test_anchor_ngram_truncated_to_24():
    """n-gram [:24] 截断分支."""
    ev = _ev({"source": "search_notes", "locator": "", "text": "甲" * 30})
    h = resolve_hits_from_json(ev, _tc(), None, None, KB)[0]
    assert h.anchor.match_level == "text"
    assert len(h.anchor.query) == 24


def test_evidence_anchor_non_int_offset_coerced_no_crash():
    """信任边界: evidence.anchor 非 int 偏移 → coerce None, 不抛 ValidationError."""
    ev = _ev({"source": "note_diagnosis", "locator": "出院诊断", "text": "诊断",
              "anchor": {"query": "甲状腺", "char_start": "oops", "char_end": 3.5}})
    h = resolve_hits_from_json(ev, "[]", None, None, KB)[0]
    assert h.anchor.match_level == "evidence"
    assert h.anchor.query == "甲状腺"
    assert h.anchor.char_start is None
    assert h.anchor.char_end is None


def test_malformed_tool_calls_no_crash():
    """损坏 tool_calls (非 dict 元素 / 非 dict arguments) → 不崩, 正常走阶梯."""
    ev = _ev({"source": "note_diagnosis", "locator": "出院诊断", "text": "诊断「甲状腺癌」"})
    tc = json.dumps(
        ["not a dict", {"tool_name": "search_notes", "arguments": "oops"}],
        ensure_ascii=False,
    )
    h = resolve_hits_from_json(ev, tc, None, None, KB)[0]
    assert h.anchor.match_level == "locator"


def test_fee_anchor_no_match_uses_hit_name_not_unresolved():
    """D1: drug 命中但该 patient 无 fee 行 (未计费/未匹码) → 锚点用命中名做模糊 query,
    仍切 fees tab, match_level=name, NOT unresolved (跳转可达性与编码富集解耦)."""
    ev = _ev({"source": "drug_audit_lookup", "locator": "甘露醇注射液",
              "text": "通用名「甘露醇注射液」"})
    # fee_df 不含甘露醇行 → 无 fee 匹配 (拿不到码)
    no_match_fees = _fee_df([["脑功能成像", "S21020000300010", "210200003"]])
    h = resolve_hits_from_json(ev, _tc(), "限适应症", no_match_fees, KB)[0]
    assert h.anchor.tab == "fees"
    assert h.anchor.match_level == "name"
    assert h.anchor.unresolved is False
    assert h.anchor.query == "甘露醇注射液"   # 命中名做 query, 费用 tab 子串高亮
    # 编码缺失 (严格不臆造) 但名 + 限定仍渲染
    assert h.code_nat == ""
    assert h.name == "甘露醇注射液"
    assert "脑水肿" in h.restriction


def test_fee_hit_recomb_human_blood_jumps_without_code():
    """spec 场景: drug 命中「重组人血」, 患者 fee 无 stem 匹配行 (无码) → 锚点带 query=重组人血,
    NOT unresolved; 打开后费用 tab 高亮任何含「重组人血」的行 (修复 J94935 R007 跳不动)."""
    ev = _ev({"source": "drug_audit_lookup", "locator": "重组人血",
              "text": "用药「重组人血」无指征"})
    # 患者实际 fee 表里没有 stem 匹配「重组人血」的行 → 编码富集失败 (无码)
    other_fees = _fee_df([["注射用头孢曲松", "XA01", "001"]])
    h = resolve_hits_from_json(ev, _tc(), "限适应症", other_fees, {})[0]
    assert h.anchor.tab == "fees"
    assert h.anchor.query == "重组人血"
    assert h.anchor.unresolved is False
    assert h.anchor.match_level == "name"
    assert h.code_nat == ""  # 编码显示保持严格 — 无码不显码


def test_underivable_evidence_skipped():
    # locator 空 + text 无可锚定 n-gram → 名取不到 → 该条跳过 (不臆造)
    ev = _ev({"source": "search_notes", "locator": "", "text": "。。。"})
    assert resolve_hits_from_json(ev, _tc(), None, None, KB) == []


# =========================================================
# 场景 7: 非药品规则 → restriction 空
# =========================================================
def test_non_drug_rule_no_restriction():
    ev = _ev({"source": "search_fees", "locator": "脑功能成像", "text": "脑功能成像"})
    hits = resolve_hits_from_json(ev, _tc(), None, J26355_FEES, KB)
    assert len(hits) == 1
    assert hits[0].restriction == ""
    assert hits[0].code_nat == "S21020000300010"


# =========================================================
# 场景 8: drug 码优先 (fix-drug-code-match) — 相似药名锚到正确编码
# =========================================================
# 新版 KB: drugs[通用名] = {entries, codes}; 丁苯那嗪片/氘丁苯那嗪片 名互为子串, 码不同
KB_CODED = {
    "丁苯那嗪片": {
        "entries": [{"rule_type": "限适应症", "basis": "限亨廷顿舞蹈病。"}],
        "codes": ["XN07DING001", "XN07DING002"],
    },
}

# 患者同时计费两个相似药: 丁苯那嗪片 (码在 KB) + 氘丁苯那嗪片 (码不在 KB)
DING_FEES = _fee_df([
    ["(基)丁苯那嗪片", "XN07DING001", "L001"],
    ["(基)氘丁苯那嗪片", "XN07DEU999", "L002"],  # 名子串相似, 码不同
])


def test_drug_code_first_picks_only_matching_code_row():
    """证据通用名「丁苯那嗪片」→ 仅锚到码 ∈ KB code set 的行, 不串味氘丁苯那嗪片."""
    ev = _ev({"source": "drug_audit_lookup", "locator": "丁苯那嗪片",
              "text": "通用名「丁苯那嗪片」限定适应症不符"})
    hits = resolve_hits_from_json(ev, _tc(), "限适应症", DING_FEES, KB_CODED)
    assert len(hits) == 1
    h = hits[0]
    assert h.code_nat == "XN07DING001"        # 码精确命中行
    assert h.matched_fee_name == "(基)丁苯那嗪片"
    assert "氘" not in h.matched_fee_name      # 相似药行被码排除, 不错配编码
    assert "亨廷顿" in h.restriction


def test_drug_code_mismatch_falls_back_blank_code_needs_review():
    """患者只有码不在 KB 的相似药行 → 名兜底但编码留空 + needs_review (不臆造相似药码, 4.2)."""
    only_similar = _fee_df([["(基)氘丁苯那嗪片", "XN07DEU999", "L002"]])
    ev = _ev({"source": "drug_audit_lookup", "locator": "丁苯那嗪片",
              "text": "通用名「丁苯那嗪片」"})
    hits = resolve_hits_from_json(ev, _tc(), "限适应症", only_similar, KB_CODED)
    assert len(hits) == 1
    h = hits[0]
    assert h.code_nat == ""        # 不猜配相似药 XN07DEU999
    assert h.code_local == ""
    assert "需复核" in h.review_note
    assert "亨廷顿" in h.restriction  # 限定仍带出


def test_drug_code_path_byte_identical():
    """码路下二次调用序列化 byte-identical (4.3)."""
    from javert.web.hit_resolver import hits_to_json
    ev = _ev({"source": "drug_audit_lookup", "locator": "丁苯那嗪片", "text": "x"})
    a = resolve_hits_from_json(ev, _tc(), "限适应症", DING_FEES, KB_CODED)
    b = resolve_hits_from_json(ev, _tc(), "限适应症", DING_FEES, KB_CODED)
    assert hits_to_json(a) == hits_to_json(b)


# =========================================================
# 场景 9: 通用占位 locator → 提取真实被查项目名
# (fix: LLM 搜索未命中时 locator 写 "费用明细检索" 等占位, 命中按钮应显示具体药品/项目)
# =========================================================
def test_generic_locator_name_from_quoted_text():
    """locator 是 '费用明细检索' 占位 → 从 text 引文捞首个关键词 (完整通用名)."""
    ev = _ev({"source": "search_fees", "locator": "费用明细检索",
              "text": "在患者全量费用明细中，使用关键词 '注射用盐酸万古霉素'、'万古霉素' 检索，均未找到。"})
    h = resolve_hits_from_json(ev, _tc(), None, None, KB)[0]
    assert h.name == "注射用盐酸万古霉素"  # 不再显示 "费用明细检索"


def test_generic_locator_name_from_fullwidth_quote():
    ev = _ev({"source": "search_fees", "locator": "费用明细搜索",
              "text": "在患者全量费用明细中搜索关键词「果糖」，未找到任何相关费用项目。"})
    h = resolve_hits_from_json(ev, _tc(), None, None, KB)[0]
    assert h.name == "果糖"


def test_generic_locator_name_from_keyword_arg():
    """locator 形如 search_lab_results(item_keyword='X') → 取 keyword 值."""
    ev = _ev({"source": "search_lab_results",
              "locator": "search_lab_results (item_keyword='肌钙蛋白')",
              "text": "该患者无检验/化验报告记录"})
    h = resolve_hits_from_json(ev, _tc(), None, None, KB)[0]
    assert h.name == "肌钙蛋白"


def test_generic_locator_paren_fallback():
    ev = _ev({"source": "search_fees", "locator": "费用明细搜索(肩锁)",
              "text": "未找到费用项目"})
    h = resolve_hits_from_json(ev, _tc(), None, None, KB)[0]
    assert h.name == "肩锁"


def test_specific_locator_not_rewritten():
    """真实项目名 (非占位) 不被改写, 出码不受影响."""
    ev = _ev({"source": "search_fees", "locator": "脑功能成像", "text": "费用明细出现脑功能成像"})
    h = resolve_hits_from_json(ev, _tc(), None, J26355_FEES, KB)[0]
    assert h.name == "脑功能成像"
    assert h.code_nat == "S21020000300010"


def test_generic_locator_falls_back_to_tool_call_keyword_in_text():
    """汇总型 fee 证据 (文书是多项列表, 无单一项名 + 无引文) → 退回文中出现的实搜关键词."""
    ev = _ev({"source": "search_fees", "locator": "费用明细摘要",
              "text": "全身麻醉气管插管 800.00; 麻醉后复苏监护 300.00"})
    tc = _tc({"tool_name": "search_fees", "arguments": {"patient_id": "X", "keyword": "麻醉"}})
    h = resolve_hits_from_json(ev, tc, None, None, KB)[0]
    assert h.name == "麻醉"  # 不再是"费用明细摘要"


def test_generic_locator_tool_call_keyword_first_when_none_in_text():
    """文中没出现任何实搜词 → 取第一个搜过的关键词 (仍比"费用明细"强)."""
    ev = _ev({"source": "search_fees", "locator": "费用明细检索",
              "text": "以上项目均未在费用明细中找到"})
    tc = _tc({"tool_name": "search_fees", "arguments": {"keyword": "开髓引流"}},
             {"tool_name": "search_fees", "arguments": {"keyword": "根管充填"}})
    h = resolve_hits_from_json(ev, tc, None, None, KB)[0]
    assert h.name == "开髓引流"  # 第一个实搜词


# =========================================================
# 场景 10: lab/exam 命中锚点统一到合并「检验记录」tab (labs)
# (新增检验记录 tab + trace 跳转)
# =========================================================
def test_lab_exam_anchor_unified_to_labs_tab():
    ev = _ev(
        {"source": "search_lab_results", "locator": "肌红蛋白检验", "text": "无相关记录"},
        {"source": "examination", "locator": "超声", "text": "甲状腺结节 TI-RADS 4a"},
    )
    hits = resolve_hits_from_json(ev, _tc(), None, None, KB)
    lab = next(h for h in hits if h.source == "lab")
    exam = next(h for h in hits if h.source == "exam")
    assert lab.anchor.tab == "labs"
    assert exam.anchor.tab == "labs"
    # 具体项名 → 可跳转高亮该项 (命中到了才支持)
    assert lab.anchor.query == "肌红蛋白检验"
    assert lab.anchor.unresolved is False
    assert exam.anchor.query == "超声"
    assert exam.anchor.unresolved is False


def test_lab_generic_locator_tab_only_unresolved():
    """无可提取项名的通用占位 → 仅切到检验记录 tab, 不强求高亮."""
    ev = _ev({"source": "lab", "locator": "检验报告检索",
              "text": "该患者无检验/化验报告记录"})
    h = resolve_hits_from_json(ev, _tc(), None, None, KB)[0]
    assert h.source == "lab"
    assert h.anchor.tab == "labs"
    assert h.anchor.unresolved is True
    assert h.anchor.query == ""
