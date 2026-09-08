# -*- coding: utf-8 -*-
"""CD 专用试跑 runtime：候选提取与确定性 proof，未签发资产只出 shadow。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from javert.clinical_criteria.contracts import CriterionAssessment, CriterionState, ProofNode
from javert.clinical_criteria.evaluator import aggregate_state

from .contracts import ClinicalCriteriaEvaluation, CriteriaBlocker, SourceManifest
from .facts import NORMALIZER_VERSION, collect_records, extract_candidates
from .knowledge import (
    fragment_payload_checksum, load_criteria_asset, source_manifest_payload_checksum,
)

EVALUATOR_VERSION = "chronic-shadow-1.0.0"
_DOMAIN_NAMES = {
    "notes": {"文书", "诊断", "病史", "生命体征", "体征", "功能", "时间", "高敏感文书"},
    "labs": {"检验", "检验日期"},
    "examinations": {"检查", "影像", "ECG", "Holter", "EEG", "心超", "X线", "CT", "肺功能", "病理", "电生理", "血管检查", "冠脉", "眼底检查"},
    "diagnoses": {"诊断", "病史"}, "surgeries": {"手术"},
}


def _blocker(code, nodes):
    return CriteriaBlocker(
        block_reason_code=code, unresolved_question="需补充或审核条件资产及原始证据后复核",
        affected_node_ids=nodes, required_approver_roles=["慢病认定专家"],
        prohibited_fallbacks=["2020_POLICY", "LLM", "LOCAL_DEFAULT"],
    )


def _empty_result(rule, mode, code):
    """资产不可用时保留明确阻断；零摘要表示未加载，不能当有效 release。"""
    return ClinicalCriteriaEvaluation(
        schema_version="1.0.0", rule_id=rule.rule_id, disease_id=rule.rule_id,
        disease_name="慢病认定条件评估", policy_version="2025", release_id="unavailable",
        disease_revision_id=f"{rule.rule_id}-unavailable", asset_checksum="sha256:" + "0" * 64,
        execution_status="BLOCKED", evaluation_mode="shadow" if mode == "off" else mode,
        qualification_disposition="REVIEW_REQUIRED", legacy_verdict="INCONCLUSIVE",
        blocking_reasons=[_blocker(code, [f"{rule.rule_id}.ROOT"])],
        missing_items=["需核对运行开关或条件资产"],
        data_quality_flags=[code, "ASSET_NOT_LOADED", "CHRONIC_NO_CANDIDATE"],
        normalizer_version=NORMALIZER_VERSION, evaluator_version=EVALUATOR_VERSION,
        evaluated_at=datetime.now(timezone.utc),
    )


def _leaf_assessment(node, facts, revision_id):
    """只执行显式机器策略；自由文本 operator_or_threshold 不提供运行语义。"""
    reason = "缺少可用于确定性比较的证据"
    state = CriterionState.UNKNOWN
    comparisons = []
    expected = node.expected_condition
    policy = expected.get("type")
    supported = policy in {"presence", "categorical", "numeric"}
    policy_ready = all(
        not p or p.get("mode") == "not_applicable_or_unspecified"
        for p in (node.repetition_policy, node.temporal_policy)
    )
    if node.compilation_status != "compiled":
        reason = "条件节点尚未完整编译，候选仅供人工复核"
    elif node.node_type != "LEAF":
        reason = "逻辑节点缺少完整子条件，不把摘要当作已编译叶子"
    elif not policy_ready or not supported:
        reason = "比较、重复或时间策略尚未受支持，不能从条文摘要推断"
    else:
        for fact in facts:
            # 文书只有明确受控子类型与 evidence domain 对齐才能作为检查/检验证据。
            domains = set(_DOMAIN_NAMES.get(fact.source_domain, ()))
            if fact.anchor and fact.anchor.anchor:
                domains.add(fact.anchor.anchor.get("section", ""))
            if (not fact.anchor or not fact.anchor.locator or not fact.anchor.text
                or fact.uncertainty_reason or fact.certainty != "confirmed"
                or not ("多域" in node.evidence_domains or domains.intersection(node.evidence_domains))):
                continue
            # 语义关联由资产里的 literal terms 定义，不能信任模型把任意数值指派给节点。
            terms = expected.get("terms", [])
            if not terms or not any(str(term) in fact.assertion for term in terms):
                continue
            raw = str(fact.raw_value)
            if policy in {"presence", "categorical"}:
                if fact.polarity == "positive" and (policy == "presence" or raw in expected.get("values", [])):
                    comparisons.append(True)
                elif fact.polarity == "negative" and any(term in fact.assertion for term in expected.get("negative_terms", [])):
                    comparisons.append(False)
            elif policy == "numeric" and fact.polarity == "positive":
                # 一期只做相同明确单位的十进制比较，不发明医学单位转换。
                unit = expected.get("unit")
                if not unit or fact.raw_unit != unit:
                    continue
                try:
                    value, threshold = Decimal(raw), Decimal(str(expected["value"]))
                    if not value.is_finite() or not threshold.is_finite():
                        continue
                    op = expected.get("operator")
                    outcome = {"lt": value < threshold, "lte": value <= threshold,
                               "gt": value > threshold, "gte": value >= threshold,
                               "eq": value == threshold}.get(op)
                    if outcome is not None:
                        comparisons.append(outcome)
                        fact.normalized_value = raw
                        fact.canonical_unit = unit
                        fact.conversion_id = "identity-1.0.0"
                except (InvalidOperation, KeyError):
                    continue
        if comparisons:
            state = (CriterionState.CONFLICT if len(set(comparisons)) > 1 else
                     CriterionState.SATISFIED if comparisons[0] else CriterionState.NOT_SATISFIED)
            reason = "由原文关联、来源策略和显式比较确定；互相矛盾的适用证据保留冲突"
    return CriterionAssessment(
        criterion_id=node.criterion_id or node.node_id,
        criterion_type=node.fact_type or "incomplete_condition", state=state,
        expected_condition={**expected, "source_node_type": node.node_type, "summary": node.summary},
        normalized_facts=facts, evidence_anchors=[f.anchor for f in facts if f.anchor],
        comparison_result={"outcomes": comparisons}, reason=reason,
        missing_items=[node.node_id] if state == CriterionState.UNKNOWN else [],
        conflict_items=[node.node_id] if state == CriterionState.CONFLICT else [],
        normalizer_version=NORMALIZER_VERSION, evaluator_version=EVALUATOR_VERSION,
        criteria_revision=revision_id,
    )


def build_shadow_proof(revision, facts):
    """保留全部节点；不完整/blocked 内部节点是 UNKNOWN 容器，不执行缺失逻辑。"""
    def visit(node):
        child_nodes = [n for n in revision.nodes if n.parent_node_id == node.node_id]
        common = dict(node_id=node.node_id, evaluator_version=EVALUATOR_VERSION,
                      criteria_revision=revision.revision_id, source_version=revision.policy_version)
        if not child_nodes:
            assessment = _leaf_assessment(node, facts.get(node.node_id, []), revision.revision_id)
            return ProofNode(**common, operator="leaf", state=assessment.state,
                             criterion_id=assessment.criterion_id, criterion_type=assessment.criterion_type,
                             assessment=assessment, reason=assessment.reason)
        children = [visit(n) for n in child_nodes]
        states = [n.state for n in children]
        operator = node.operator or "AND"
        threshold = node.threshold
        malformed = operator == "AT_LEAST_N" and not (threshold and 1 <= threshold <= len(children))
        if malformed:
            operator, threshold = "AND", None
        complete = node.compilation_status == "compiled" and not malformed
        state = aggregate_state(operator, states, threshold) if complete else CriterionState.UNKNOWN
        # BLOCKED_ROOT 没有临床算子；AND 只是现有 ProofNode 合同的展示容器，固定 UNKNOWN。
        reason = "" if complete else "条件未完整编译或根被阻断；仅展示子节点，未执行该节点聚合"
        lower = sum(s == CriterionState.SATISFIED for s in states) if threshold else None
        upper = sum(s != CriterionState.NOT_SATISFIED for s in states) if threshold else None
        decisive = [n.node_id for n in children if n.state == state] if complete else []
        return ProofNode(**common, operator=operator, state=state, children=children,
                         threshold=threshold, lower_bound=lower, upper_bound=upper,
                         decisive_child_ids=decisive, reason=reason)
    return visit(next(n for n in revision.nodes if n.node_id == revision.root_node_id))


def evaluate_chronic_rule(rule, patient_id, *, loader, provider, config):
    """Runner 公共接线。on 暂不解锁：运行时校验 JSON，不伪造资产签发。"""
    mode = config.chronic_disease_criteria
    if mode == "off":
        return _empty_result(rule, mode, "CHRONIC_DISABLED")
    try:
        # 源政策 PDF 不随运行时制品发布，运行时只校验 JSON。
        raw = json.loads(config.resolve("configs/chronic_disease_sources.json").read_text(encoding="utf-8"))
        manifest = SourceManifest.model_validate(raw)
        if manifest.manifest_checksum != source_manifest_payload_checksum(raw):
            raise ValueError("manifest checksum")
        if any(f["fragment_checksum"] != fragment_payload_checksum(f) for f in raw["fragments"]):
            raise ValueError("fragment checksum")
        asset = load_criteria_asset(config.resolve("configs/chronic_disease_criteria.json"), source_manifest=manifest)
        policy = next(p for p in asset.policy_sets if p.policy_role == "current_recognition")
        revision = next(r for r in policy.disease_revisions if r.rule_id == rule.rule_id)
        if rule.clinical_criteria_ref != f"{policy.policy_id}/{rule.rule_id}":
            return _empty_result(rule, mode, "CRITERIA_REF_MISMATCH")
    except Exception:
        return _empty_result(rule, mode, "CRITERIA_ASSET_INVALID")
    records, flags = collect_records(loader, patient_id)
    parent_ids = {n.parent_node_id for n in revision.nodes}
    terminal_nodes = [n for n in revision.nodes if n.node_id not in parent_ids]
    facts, extraction_flags = extract_candidates(records, terminal_nodes, provider,
                                                 max_tokens=min(4096, config.llm_max_tokens))
    flags += extraction_flags
    flags.append("CHRONIC_CANDIDATE_MATCHED" if any(facts.values()) else "CHRONIC_NO_CANDIDATE")
    flags += ["SHADOW_ONLY", "SOURCE_DOCUMENTS_NOT_VERIFIED"]
    if mode == "on":
        flags.append("AUTOMATIC_RELEASE_NOT_ENABLED")
    if policy.review_status != "approved" or revision.review_status != "approved" or not policy.execution_enabled:
        flags.append("ASSET_NOT_APPROVED")
    if any(n.compilation_status != "compiled" for n in revision.nodes):
        flags.append("PARTIAL_CRITERIA")
    if not records:
        flags.append("NO_CLINICAL_DATA")
    proof = build_shadow_proof(revision, facts)
    missing, conflicts = [], []
    def collect(node):
        if node.state == CriterionState.UNKNOWN:
            missing.append(node.node_id)
        elif node.state == CriterionState.CONFLICT:
            conflicts.append(node.node_id)
        for child in node.children:
            collect(child)
    collect(proof)
    blockers = [revision.blocker] if revision.blocker else []
    blockers.append(_blocker("SHADOW_ONLY", [revision.root_node_id]))
    return ClinicalCriteriaEvaluation(
        schema_version="1.0.0", rule_id=rule.rule_id, disease_id=revision.canonical_disease_id,
        disease_name=revision.disease_name, policy_version=revision.policy_version,
        release_id=asset.release_id, disease_revision_id=revision.revision_id,
        asset_checksum=asset.asset_checksum, execution_status="BLOCKED", evaluation_mode=mode,
        qualification_disposition="REVIEW_REQUIRED", legacy_verdict="INCONCLUSIVE",
        shadow_proof_tree=proof, missing_items=missing, conflict_items=conflicts,
        blocking_reasons=blockers, data_quality_flags=sorted(set(flags)),
        normalizer_version=NORMALIZER_VERSION, evaluator_version=EVALUATOR_VERSION,
        evaluated_at=datetime.now(timezone.utc),
    )
