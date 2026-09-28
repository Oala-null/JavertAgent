from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import get_args

from javert.oncology.eligibility import CriterionType as RuntimeCriterionType
from javert.oncology.authoring.conditions import (
    build_condition_candidates,
    build_shared_qualifier_candidates,
    coverage_partition,
    source_rule_coverage,
    split_source_rule,
    validate_condition_tree,
)
from javert.oncology.authoring.curated import build_manifest
from javert.oncology.authoring.reports import build_representative_condition_snapshot
from javert.oncology.authoring.models import (
    CandidateDisposition,
    CombinationRequirement,
    CriterionType,
    IndicationBranchCandidate,
    NodeKind,
    TargetKind,
)
from javert.oncology.authoring.sources import (
    build_authoring_snapshot,
    build_candidate_universe,
    build_drug_crosswalk,
    build_pathology_links,
    normalize_drug_concept_name,
)
from scripts.build_oncology_authoring_candidates import _resolve_combination_targets


ROOT = Path(__file__).resolve().parents[1]


def _all_candidates():
    snapshot = build_authoring_snapshot(ROOT)
    fragments = {item.source_fragment_id: item for item in snapshot.source_fragments}
    branches = []
    nodes = []
    for rule in snapshot.source_rules:
        for branch in split_source_rule(rule, fragments[rule.source_fragment_id]):
            branches.append(branch)
            branch_nodes = build_condition_candidates(branch)
            branch_nodes.extend(
                build_shared_qualifier_candidates(
                    branch,
                    fragments[rule.source_fragment_id],
                    branch_nodes,
                )
            )
            nodes.extend(branch_nodes)
    return snapshot, branches, nodes


def test_source_snapshot_covers_every_insurance_and_guideline_rule() -> None:
    snapshot = build_authoring_snapshot(ROOT)
    counts = Counter(rule.policy_scope.value for rule in snapshot.source_rules)
    assert counts == {"INSURANCE_PAYMENT": 155, "GUIDELINE_INDICATION": 66}
    assert len(snapshot.source_fragments) <= len(snapshot.source_rules)
    assert {rule.source_fragment_id for rule in snapshot.source_rules} == {
        fragment.source_fragment_id for fragment in snapshot.source_fragments
    }
    assert all(fragment.anchor and fragment.original_text for fragment in snapshot.source_fragments)
    assert not any(rule.policy_scope.value == "NMPA_LABEL" for rule in snapshot.source_rules)


def test_generated_candidate_release_readiness_keeps_expert_blockers_visible() -> None:
    payload = __import__("json").loads(
        (ROOT / "docs/oncology/authoring/oncology_authoring_candidates.json").read_text()
    )
    readiness = payload["qa"]["release_readiness"]
    assert readiness["status"] == "REVIEW_REQUIRED"
    assert readiness["draft_rule_revisions"] == 221
    assert readiness["in_review_branches"] == payload["qa"]["partition_total"]
    assert readiness["unsupported_leaves"] > 0
    assert readiness["unresolved_combination_concept_nodes"] == 0
    assert readiness["biomarker_nodes_requiring_expert_context_review"] > 0


def test_crosswalk_keeps_dosage_form_and_codes_in_product_identity() -> None:
    concepts, products = build_drug_crosswalk(ROOT)
    assert len(products) > len(concepts)
    assert len({item.normalized_name for item in concepts}) == len(concepts)
    identities = {item.drug_product_id for item in products}
    assert len(identities) == len(products)
    same_concept = Counter(item.drug_concept_id for item in products)
    assert max(same_concept.values()) > 1

    concept_by_name = {item.normalized_name: item for item in concepts}
    cyclophosphamide = concept_by_name[normalize_drug_concept_name("环磷酰胺")]
    assert cyclophosphamide.drug_concept_id == "cyclophosphamide"
    cyclophosphamide_products = [
        item
        for item in products
        if item.drug_concept_id == cyclophosphamide.drug_concept_id
    ]
    assert {item.product_name for item in cyclophosphamide_products} >= {
        "注射用环磷酰胺",
        "环磷酰胺片",
        "环磷酰胺胶囊",
    }
    assert len({item.drug_product_id for item in cyclophosphamide_products}) == len(
        cyclophosphamide_products
    )


def test_crosswalk_adds_prednisone_only_from_real_hospital_products() -> None:
    concepts, products = build_drug_crosswalk(ROOT)
    concept = next(
        item
        for item in concepts
        if item.normalized_name == normalize_drug_concept_name("泼尼松")
    )
    assert concept.drug_concept_id == "prednisone"
    matched = [
        item for item in products if item.drug_concept_id == concept.drug_concept_id
    ]
    assert len(matched) == 7
    assert {item.product_name for item in matched} == {"泼尼松片", "醋酸泼尼松片"}
    assert all(item.source_kind == "hospital_catalog" for item in matched)
    assert len({item.hospital_code for item in matched}) == len(matched)
    assert all(item.hospital_code for item in matched)


def test_exact_dexamethasone_target_uses_only_exact_real_catalog_products() -> None:
    concepts, products = build_drug_crosswalk(
        ROOT, supplemental_generic_names=["地塞米松"]
    )
    concept = next(
        item
        for item in concepts
        if item.normalized_name == normalize_drug_concept_name("地塞米松")
    )
    matched = [
        item for item in products if item.drug_concept_id == concept.drug_concept_id
    ]
    assert len(matched) == 8
    assert {item.product_name for item in matched} == {
        "醋酸地塞米松注射液",
        "醋酸地塞米松片",
    }
    assert not any("复方" in item.product_name for item in matched)
    assert all(item.source_kind == "hospital_catalog" for item in matched)


def test_all_source_rules_are_split_and_partitioned_exactly_once() -> None:
    snapshot, branches, _ = _all_candidates()
    per_rule = Counter(branch.rule_revision_id for branch in branches)
    assert set(per_rule) == {rule.revision_id for rule in snapshot.source_rules}
    assert min(per_rule.values()) >= 1
    partition = coverage_partition(branches)
    assert sum(partition.values()) == len(branches)
    assert set(partition) == {"approved", "in_review", "rejected", "unsupported"}
    source_coverage = source_rule_coverage(branches)
    assert source_coverage["total"] == len(snapshot.source_rules) == 221
    assert sum(source_coverage["counts"].values()) == 221
    assert len({row["rule_revision_id"] for row in source_coverage["rows"]}) == 221


def test_condition_candidates_cover_required_first_phase_types_and_tree_qa() -> None:
    snapshot, branches, nodes = _all_candidates()
    criterion_types = {node.criterion_type for node in nodes if node.criterion_type}
    assert {
        CriterionType.DIAGNOSIS,
        CriterionType.STAGE,
        CriterionType.BIOMARKER,
        CriterionType.PRIOR_THERAPY,
        CriterionType.COMBINATION_REQUIREMENT,
        CriterionType.TIME_WINDOW,
    } <= criterion_types
    fragment_ids = {item.source_fragment_id for item in snapshot.source_fragments}
    by_branch = {branch.branch_id: [] for branch in branches}
    for node in nodes:
        by_branch[node.branch_id].append(node)
    assert all(not validate_condition_tree(branch_nodes, fragment_ids) for branch_nodes in by_branch.values())
    assert CriterionType.UNSUPPORTED not in criterion_types
    assert coverage_partition(branches)["unsupported"] == 0
    assert all(
        isinstance(node.expected_value, dict) and node.expected_value
        for node in nodes
        if node.node_kind == NodeKind.LEAF
    )
    assert criterion_types - {CriterionType.UNSUPPORTED} <= set(
        get_args(RuntimeCriterionType)
    )


def test_numbered_source_rule_after_limit_prefix_splits_indication_branches() -> None:
    snapshot = build_authoring_snapshot(ROOT)
    fragments = {item.source_fragment_id: item for item in snapshot.source_fragments}
    rule = next(
        item
        for item in snapshot.source_rules
        if "至少接受过2个系统化疗" in fragments[item.source_fragment_id].original_text
    )
    branches = split_source_rule(rule, fragments[rule.source_fragment_id])
    assert len(branches) == 2
    assert "胃癌" in branches[0].source_text
    assert "尿路上皮癌" in branches[1].source_text


def test_parenthesized_numbering_splits_indication_branches() -> None:
    snapshot = build_authoring_snapshot(ROOT)
    fragments = {item.source_fragment_id: item for item in snapshot.source_fragments}
    rule = next(
        item
        for item in snapshot.source_rules
        if "单药维持治疗" in fragments[item.source_fragment_id].original_text
        and "标准 CHOP" in fragments[item.source_fragment_id].original_text
    )
    branches = split_source_rule(rule, fragments[rule.source_fragment_id])
    assert len(branches) == 5
    assert all(not item.source_text.endswith("适应证:") for item in branches)
    assert all("(2)" not in item.source_text for item in branches)


def test_guideline_sections_and_numbering_do_not_silently_merge_indications() -> None:
    snapshot = build_authoring_snapshot(ROOT)
    fragments = {item.source_fragment_id: item for item in snapshot.source_fragments}
    rule = next(
        item
        for item in snapshot.source_rules
        if "恶性胸膜间皮瘤" in fragments[item.source_fragment_id].original_text
        and "MSI-H" in fragments[item.source_fragment_id].original_text
        and "纳武利尤单抗" in fragments[item.source_fragment_id].original_text
    )
    branches = split_source_rule(rule, fragments[rule.source_fragment_id])
    assert len(branches) >= 3
    assert any("恶性胸膜间皮瘤" in item.source_text for item in branches)
    assert any("结直肠癌" in item.source_text for item in branches)
    assert any("肝细胞癌" in item.source_text for item in branches)


def test_numbered_global_payment_window_is_shared_by_each_indication_branch() -> None:
    snapshot = build_authoring_snapshot(ROOT)
    fragments = {item.source_fragment_id: item for item in snapshot.source_fragments}
    rule = next(
        item
        for item in snapshot.source_rules
        if "支付不超过12个月" in fragments[item.source_fragment_id].original_text
        and "限以下情况方可支付" in fragments[item.source_fragment_id].original_text
    )
    fragment = fragments[rule.source_fragment_id]
    branches = split_source_rule(rule, fragment)
    assert all("支付不超过12个月" not in item.source_text for item in branches)
    for branch in branches:
        branch_nodes = build_condition_candidates(branch)
        shared = build_shared_qualifier_candidates(branch, fragment, branch_nodes)
        assert len(shared) == 1
        assert shared[0].criterion_type == CriterionType.TIME_WINDOW
        assert shared[0].expected_value == {
            "within_days": 360,
            "window_kind": "maximum_payment_duration",
        }


def test_combination_parser_keeps_only_target_phrase() -> None:
    branch = IndicationBranchCandidate(
        branch_id="branch_combination_target",
        rule_revision_id="rev_combination_target",
        source_fragment_id="fragment_combination_target",
        ordinal=1,
        source_text="联合纳武利尤单抗用于不可切除或转移性结直肠癌的一线治疗",
        source_span_start=0,
        source_span_end=29,
        disposition=CandidateDisposition.IN_REVIEW,
    )
    targets = [
        node.expected_value["display_name"]
        for node in build_condition_candidates(branch)
        if node.criterion_type == CriterionType.COMBINATION_REQUIREMENT
    ]
    assert targets == ["纳武利尤单抗"]


def test_combination_parser_normalizes_leading_connector_and_drug_class() -> None:
    branch = IndicationBranchCandidate(
        branch_id="branch_combination_class",
        rule_revision_id="rev_combination_class",
        source_fragment_id="fragment_combination_class",
        ordinal=1,
        source_text=(
            "联合以氟尿嘧啶用于胃癌，联合促黄体生成激素释放激素激动剂用于乳腺癌，"
            "联合顺铂用于肺癌"
        ),
        source_span_start=0,
        source_span_end=35,
        disposition=CandidateDisposition.IN_REVIEW,
    )
    targets = {
        node.expected_value["display_name"]: node.target_kind
        for node in build_condition_candidates(branch)
        if node.criterion_type == CriterionType.COMBINATION_REQUIREMENT
    }
    assert targets == {
        "氟尿嘧啶": TargetKind.CONCEPT,
        "促黄体生成激素释放激素激动剂": TargetKind.CLASS,
        "顺铂": TargetKind.CONCEPT,
    }


def test_combination_parser_ignores_historical_combination_and_duplicate_verb_match() -> None:
    branch = IndicationBranchCandidate(
        branch_id="branch_historical_combination",
        rule_revision_id="rev_historical_combination",
        source_fragment_id="fragment_historical_combination",
        ordinal=1,
        source_text="经本品联合化疗后达完全缓解；与氟维司群联合使用治疗内分泌治疗后进展患者",
        source_span_start=0,
        source_span_end=38,
        disposition=CandidateDisposition.IN_REVIEW,
    )
    targets = [
        node.expected_value["display_name"]
        for node in build_condition_candidates(branch)
        if node.criterion_type == CriterionType.COMBINATION_REQUIREMENT
    ]
    assert targets == ["氟维司群"]


def test_alternative_values_are_grouped_under_any() -> None:
    branch = IndicationBranchCandidate(
        branch_id="branch_alternative_diagnoses",
        rule_revision_id="rev_alternative_diagnoses",
        source_fragment_id="fragment_alternative_diagnoses",
        ordinal=1,
        source_text="限局部晚期或转移性卵巢癌、输卵管癌或原发性腹膜癌患者",
        source_span_start=0,
        source_span_end=20,
        disposition=CandidateDisposition.IN_REVIEW,
    )
    nodes = build_condition_candidates(branch)
    any_nodes = [node for node in nodes if node.node_kind == NodeKind.ANY]
    diagnosis_nodes = [
        node for node in nodes if node.criterion_type == CriterionType.DIAGNOSIS
    ]
    stage_nodes = [node for node in nodes if node.criterion_type == CriterionType.STAGE]
    assert len(any_nodes) == 2
    assert len(diagnosis_nodes) >= 2
    assert len(stage_nodes) == 2
    assert len({node.parent_node_id for node in diagnosis_nodes}) == 1
    assert len({node.parent_node_id for node in stage_nodes}) == 1
    assert diagnosis_nodes[0].parent_node_id != stage_nodes[0].parent_node_id


def test_previously_unsupported_restrictions_are_typed() -> None:
    source_texts = (
        "系统性硬化病相关间质性肺疾病(SSc-ILD);",
        "具有进行性表型的慢性纤维化性间质性肺疾病。",
        "华氏巨球蛋白血症患者的治疗,按说明书用药。",
        "限癌性胸腹水患者。",
        "新诊断的原发性轻链型淀粉样变患者。本方案不适合也不推荐用于患有NYHA IIIB级或IV级心脏疾病或Mayo IIIB期的原发性轻链型淀粉样变患者。",
    )
    for ordinal, source_text in enumerate(source_texts, start=1):
        branch = IndicationBranchCandidate(
            branch_id=f"branch_typed_{ordinal}",
            rule_revision_id=f"rev_typed_{ordinal}",
            source_fragment_id=f"fragment_typed_{ordinal}",
            ordinal=1,
            source_text=source_text,
            source_span_start=0,
            source_span_end=len(source_text),
            disposition=CandidateDisposition.IN_REVIEW,
        )
        nodes = build_condition_candidates(branch)
        assert all(
            node.criterion_type != CriterionType.UNSUPPORTED
            for node in nodes
            if node.node_kind == NodeKind.LEAF
        )
        assert branch.disposition == CandidateDisposition.IN_REVIEW


def test_combination_targets_distinguish_concept_class_and_optionality() -> None:
    _, _, nodes = _all_candidates()
    combination = [node for node in nodes if node.criterion_type == CriterionType.COMBINATION_REQUIREMENT]
    assert combination
    assert {node.target_kind for node in combination} & {
        TargetKind.CONCEPT,
        TargetKind.CLASS,
        TargetKind.REGIMEN,
    }
    assert all(node.combination_requirement in set(CombinationRequirement) for node in combination)


def test_resolved_combination_targets_are_closed_over_shared_authority() -> None:
    _, _, nodes = _all_candidates()
    supplemental_names = {
        str(node.expected_value.get("display_name") or "")
        for node in nodes
        if node.criterion_type == CriterionType.COMBINATION_REQUIREMENT
        and node.target_kind == TargetKind.CONCEPT
        and isinstance(node.expected_value, dict)
    }
    concepts, products = build_drug_crosswalk(
        ROOT, supplemental_generic_names=supplemental_names
    )
    _resolve_combination_targets(nodes, concepts, products)

    concept_ids = {item.drug_concept_id for item in concepts}
    typed_combinations = [
        node
        for node in nodes
        if node.criterion_type == CriterionType.COMBINATION_REQUIREMENT
    ]
    assert typed_combinations
    assert all(
        node.target_id in concept_ids
        for node in typed_combinations
        if node.target_kind == TargetKind.CONCEPT
    )
    assert all(node.target_kind != TargetKind.REGIMEN for node in typed_combinations)

    unresolved = [
        node
        for node in nodes
        if node.criterion_type == CriterionType.UNSUPPORTED
        and isinstance(node.expected_value, dict)
        and node.expected_value.get("resolution_status") == "AUTHORITY_REQUIRED"
    ]
    assert unresolved
    assert all(
        node.target_kind == TargetKind.VALUE
        and not node.target_id
        and node.disposition == CandidateDisposition.UNSUPPORTED
        and node.expected_value.get("proposed_criterion_type")
        == CriterionType.COMBINATION_REQUIREMENT.value
        for node in unresolved
    )
    dexamethasone = next(
        node
        for node in typed_combinations
        if node.expected_value.get("display_name") == "地塞米松"
    )
    assert dexamethasone.target_kind == TargetKind.CONCEPT
    assert dexamethasone.target_id in concept_ids


def test_synthetic_combination_parser_covers_all_required_statuses() -> None:
    branch = IndicationBranchCandidate(
        branch_id="branch_synthetic",
        rule_revision_id="rev_synthetic",
        source_fragment_id="fragment_synthetic",
        ordinal=1,
        source_text="联合合成药甲，可联合合成药乙，联合或不联合合成铂类",
        source_span_start=0,
        source_span_end=29,
        disposition=CandidateDisposition.IN_REVIEW,
    )
    requirements = {
        node.combination_requirement
        for node in build_condition_candidates(branch)
        if node.criterion_type == CriterionType.COMBINATION_REQUIREMENT
    }
    assert requirements == set(CombinationRequirement)


def test_pathology_links_keep_unknown_threshold_non_approved() -> None:
    _, _, nodes = _all_candidates()
    links = build_pathology_links(ROOT, nodes)
    assert links
    assert all(link["source_restriction_id"] for link in links)
    assert all(link["marker"] and link["methods"] and link["cancer_contexts"] for link in links)
    assert all(not link["approved_assertion"] for link in links if not link["threshold_known"])


def test_biomarker_expected_payload_uses_pathology_marker_authority_id() -> None:
    _, _, nodes = _all_candidates()
    biomarker_nodes = [
        node for node in nodes if node.criterion_type == CriterionType.BIOMARKER
    ]
    assert biomarker_nodes
    assert all(node.expected_value.get("marker_id") for node in biomarker_nodes)
    assert all(
        not str(node.expected_value["marker_id"]).startswith("marker_")
        for node in biomarker_nodes
    )
    assert {node.expected_value.get("status") for node in biomarker_nodes} <= {
        "positive",
        "negative",
    }


def test_representative_complex_drugs_are_present_without_silent_loss() -> None:
    names = {item.canonical_name for item in build_candidate_universe(ROOT)}
    for expected in ("帕博利珠单抗注射液", "替雷利珠单抗注射液", "维迪西妥单抗", "注射用维泊妥珠单抗"):
        assert any(expected in name or name in expected for name in names), expected
    assert any("奥拉帕利" in name or "尼拉帕利" in name for name in names)


def test_representative_complex_condition_snapshot_matches_tracked_artifact() -> None:
    expected = build_representative_condition_snapshot(ROOT)
    tracked = __import__("json").loads(
        (ROOT / "docs/oncology/authoring/representative_condition_snapshot.json").read_text()
    )
    assert tracked == expected
    assert all(expected["representatives"].values())


def test_candidate_universe_keeps_single_source_memberships() -> None:
    universe = build_candidate_universe(ROOT)
    assert universe
    source_counts = Counter()
    for item in universe:
        for source, present in item.source_membership.items():
            if present:
                source_counts[source] += 1
    assert source_counts["hospital_catalog"] > 0
    assert source_counts["national_catalog"] > 0
    assert source_counts["guideline"] > 0
    assert source_counts["structured_asset"] > 0
    assert source_counts["rule_yaml"] > 0
    assert all(item.disposition_reason for item in universe)
    primary = ("hospital_catalog", "national_catalog", "guideline")
    for source in primary:
        assert any(
            item.source_membership[source]
            and sum(item.source_membership[name] for name in primary) == 1
            for item in universe
        )
    assert any(item.source_membership["structured_asset"] for item in universe)
    assert any(item.source_membership["rule_yaml"] for item in universe)


def test_curated_manifest_is_deterministic_and_bulk_match_is_not_verified() -> None:
    first = build_manifest(ROOT)
    second = build_manifest(ROOT)
    assert first == second
    assert first["counts"]["rules"] == 28
    assert first["counts"]["atoms"] >= 28 * 5
    assert set(first["counts"]["by_atom_type"]) == {
        "SOURCE_RULE",
        "CLINICAL_EXTENSION",
        "EVIDENCE_POLICY",
        "NORMALIZATION",
        "DOCUMENTATION_GUIDANCE",
        "REGRESSION_GOLD",
    }
    assert all(atom["migration_status"] != "VERIFIED" for atom in first["atoms"])
    assert set(first["allowed_rule_status"].values()) == {"drafting"}
