# -*- coding: utf-8 -*-
"""慢病 draft 知识资产的合同、来源与确定性构建门禁。"""

from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from javert.chronic.contracts import (
    ChronicDiseaseCriteriaAsset,
    ClinicalCriteriaEvaluation,
    CriterionNode,
    CriterionState,
    ExecutionStatus,
)
from javert.chronic.knowledge import (
    asset_payload_checksum,
    automatic_evaluation_eligible,
    canonical_json_bytes,
    load_criteria_asset,
    load_source_manifest,
    source_manifest_payload_checksum,
    validate_source_isolation,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "configs" / "chronic_disease_sources.json"
CRITERIA = ROOT / "configs" / "chronic_disease_criteria.json"
SCHEMA = ROOT / "configs" / "schemas" / "chronic_disease_criteria.schema.json"
SCRIPT = ROOT / "scripts" / "build_chronic_disease_criteria.py"


def _builder_module():
    spec = importlib.util.spec_from_file_location("build_chronic_criteria", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load():
    manifest = load_source_manifest(SOURCES, root=ROOT)
    return manifest, load_criteria_asset(CRITERIA, source_manifest=manifest)


def test_2025保持20病种并保留首版全部主要节点() -> None:
    _, asset = _load()
    current = next(item for item in asset.policy_sets if item.policy_version == "2025")

    assert [item.rule_id for item in current.disease_revisions] == [
        f"CD{index:02d}" for index in range(1, 21)
    ]
    assert len({item.canonical_disease_id for item in current.disease_revisions}) == 20
    old = load_criteria_asset(ROOT / "configs/chronic_disease_criteria.draft-r1.json")
    for previous, revision in zip(old.policy_sets[0].disease_revisions, current.disease_revisions, strict=True):
        assert {node.node_id for node in previous.nodes} <= {node.node_id for node in revision.nodes}
    assert all(item.lifecycle.value == "draft" for item in current.disease_revisions)
    assert all(item.review_status.value == "needs_review" for item in current.disease_revisions)
    assert all(not automatic_evaluation_eligible(item) for item in current.disease_revisions)


def test_类风湿剩余功能分级阻断且禁止2020_LLM和默认值补缺() -> None:
    _, asset = _load()
    current = next(item for item in asset.policy_sets if item.policy_version == "2025")
    blocked = {
        item.rule_id: item
        for item in current.disease_revisions
        if item.execution_status.value == "BLOCKED"
    }

    assert set(blocked) == {"CD19"}
    assert blocked["CD19"].blocker.block_reason_code == "FUNCTIONAL_IMPAIRMENT_THRESHOLD_MISSING"
    assert blocked["CD19"].blocker.affected_node_ids == ["CD19.ROOT", "CD19.FUNCTION"]
    for revision in blocked.values():
        assert revision.blocker is not None
        assert set(revision.blocker.prohibited_fallbacks) == {
            "2020_POLICY",
            "LLM",
            "LOCAL_DEFAULT",
        }
        root = next(item for item in revision.nodes if item.node_id == revision.root_node_id)
        assert root.node_type.value == "BLOCKED_ROOT"
        assert root.operator is None


def test_2020与2025来源及政策物理隔离() -> None:
    manifest, asset = _load()
    documents = {item.source_document_id: item for item in manifest.documents}
    policies = {item.policy_version: item for item in asset.policy_sets}

    assert policies["2025"].source_document_ids == ["hlj-outpatient-chronic-2025"]
    assert documents[policies["2025"].source_document_ids[0]].policy_version == "2025"
    assert policies["2020"].source_document_ids == ["heihe-outpatient-chronic-2020"]
    assert documents[policies["2020"].source_document_ids[0]].policy_version == "2020"
    assert policies["2020"].disease_revisions == []
    assert policies["2020"].execution_enabled is False


def test_来源页码和四层checksum均可复验() -> None:
    manifest, asset = _load()

    assert {item.physical_page_count for item in manifest.documents} == {19, 32}
    assert all(item.physical_page >= 1 for item in manifest.fragments)
    assert all(item.fragment_checksum.startswith("sha256:") for item in manifest.fragments)
    assert asset.source_manifest_checksum == manifest.manifest_checksum
    assert asset.asset_checksum.startswith("sha256:")


def test_篡改fragment或asset会在加载时失败(tmp_path: Path) -> None:
    source_raw = json.loads(SOURCES.read_text(encoding="utf-8"))
    source_raw["fragments"][1]["reviewed_excerpt"] += "篡改"
    source_path = tmp_path / "sources.json"
    source_path.write_text(json.dumps(source_raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest checksum"):
        load_source_manifest(source_path, root=ROOT)

    criteria_raw = json.loads(CRITERIA.read_text(encoding="utf-8"))
    criteria_raw["release_id"] += "-tampered"
    criteria_path = tmp_path / "criteria.json"
    criteria_path.write_text(json.dumps(criteria_raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="asset checksum"):
        load_criteria_asset(criteria_path)


def test_schema拒绝缺病种和多根() -> None:
    raw = json.loads(CRITERIA.read_text(encoding="utf-8"))
    missing = deepcopy(raw)
    missing["policy_sets"][0]["disease_revisions"].pop()
    with pytest.raises(ValidationError, match="CD01-CD20"):
        ChronicDiseaseCriteriaAsset.model_validate(missing)

    multiple_roots = deepcopy(raw)
    revision = multiple_roots["policy_sets"][0]["disease_revisions"][1]
    revision["nodes"][1]["parent_node_id"] = None
    multiple_roots["asset_checksum"] = asset_payload_checksum(multiple_roots)
    with pytest.raises(ValidationError, match="只能有一个 root"):
        ChronicDiseaseCriteriaAsset.model_validate(multiple_roots)

    fake_approval = deepcopy(raw)
    fake_approval["policy_sets"][0]["disease_revisions"][1]["review_status"] = "approved"
    with pytest.raises(ValidationError, match="reviewer"):
        ChronicDiseaseCriteriaAsset.model_validate(fake_approval)


def test_schema文件与Pydantic模型一致() -> None:
    expected = _builder_module().pretty_json_bytes(
        ChronicDiseaseCriteriaAsset.model_json_schema()
    )
    assert SCHEMA.read_bytes() == expected


def test_相同输入重复构建字节一致() -> None:
    module = _builder_module()
    manifest_1, asset_1 = module.rebuild_existing()
    manifest_2, asset_2 = module.rebuild_existing()

    assert module.pretty_json_bytes(manifest_1) == module.pretty_json_bytes(manifest_2)
    assert module.pretty_json_bytes(asset_1) == module.pretty_json_bytes(asset_2)
    assert canonical_json_bytes(asset_1) == canonical_json_bytes(asset_2)
    assert module.pretty_json_bytes(manifest_1) == SOURCES.read_bytes()
    assert module.pretty_json_bytes(asset_1) == CRITERIA.read_bytes()


def test_BLOCKED结果不能形成第五真值或自动资格() -> None:
    raw = json.loads((ROOT / "configs/chronic_disease_criteria.draft-r1.json").read_text(encoding="utf-8"))
    blocker = raw["policy_sets"][0]["disease_revisions"][0]["blocker"]
    result = ClinicalCriteriaEvaluation(
        schema_version="1.0.0",
        rule_id="CD01",
        disease_id="aplastic-anemia",
        disease_name="再生障碍性贫血",
        policy_version="2025",
        release_id=raw["release_id"],
        disease_revision_id="CD01-2025-r1",
        asset_checksum=raw["asset_checksum"],
        execution_status=ExecutionStatus.BLOCKED,
        evaluation_mode="shadow",
        root_state=None,
        qualified=None,
        qualification_disposition="REVIEW_REQUIRED",
        legacy_verdict="INCONCLUSIVE",
        proof_tree=None,
        shadow_proof_tree=None,
        blocking_reasons=[blocker],
        normalizer_version="1.0.0",
        evaluator_version="1.0.0",
        evaluated_at=datetime.now(timezone.utc),
    )
    assert result.root_state is None
    assert result.qualified is None
    assert set(CriterionState) == {
        CriterionState.SATISFIED,
        CriterionState.NOT_SATISFIED,
        CriterionState.UNKNOWN,
        CriterionState.CONFLICT,
    }


def test_五项专家解释保留坐标checksum且不伪造审批() -> None:
    manifest, asset = _load()
    expected = {
        "H4": ("CD07", "高血压3级or（高血压1-2级+靶器官损害/临床综合征）"),
        "H5": ("CD19", "总分需≥6分andX线为III期+"),
        "H6": ("CD09", "病史是必备条件，病史and（HBV/HCV分支）"),
        "H7": ("CD01", "CBCand骨髓穿刺and骨髓活检and排除检查"),
        "H8": ("CD03", "糖尿病肾病4个子项满足任一即可"),
    }
    assert {item.cell: (item.rule_id, item.statement) for item in manifest.expert_interpretations} == expected
    assert all(item.sheet == "待线下确认" for item in manifest.expert_interpretations)
    assert all(item.workbook_checksum == "sha256:a0f8816f00bbc1088d9b9350eb0aeef26c5c0e1a609a0420d87fbe22be3a4e47" for item in manifest.expert_interpretations)
    assert all(item.original_confirmation_status == "pending" for item in manifest.expert_interpretations)
    assert all(item.review_status == "needs_review" for item in manifest.fragments)
    assert all(item.reviewed_by is None and item.reviewed_at is None for item in asset.policy_sets[0].disease_revisions)
    assert asset.policy_sets[0].execution_enabled is False


def test_旧资产字节及checksum链保留且未改其他病种() -> None:
    module = _builder_module()
    manifest, asset = _load()
    old_sources = load_source_manifest(module.DRAFT_SOURCES, root=ROOT)
    old = load_criteria_asset(module.DRAFT_CRITERIA, source_manifest=old_sources)
    assert asset.previous_asset_checksum == old.asset_checksum
    assert manifest.previous_manifest_checksum == old_sources.manifest_checksum
    changed = {"CD01", "CD03", "CD07", "CD09", "CD19"}
    for previous, revision in zip(old.policy_sets[0].disease_revisions, asset.policy_sets[0].disease_revisions, strict=True):
        if revision.rule_id in changed:
            assert revision.previous_revision_id == previous.revision_id
            assert revision.revision_id == revision.rule_id + "-2025-r2"
        else:
            assert revision == previous
    assert {r.rule_id for r in old.policy_sets[0].disease_revisions if r.blocker} == {"CD01", "CD07", "CD09", "CD19"}


def test_明确反馈组合按作用域编译() -> None:
    _, asset = _load()
    revisions = {r.rule_id: r for r in asset.policy_sets[0].disease_revisions}
    def children(rule: str, name: str) -> set[str]:
        return {n.node_id for n in revisions[rule].nodes if n.parent_node_id == f"{rule}.{name}"}
    nodes = {n.node_id: n for r in revisions.values() for n in r.nodes}
    assert nodes["CD07.ROOT"].operator == "OR"
    assert children("CD07", "ROOT") == {"CD07.BP_GRADE3", "CD07.GRADE12_DAMAGE"}
    assert nodes["CD07.GRADE12_DAMAGE"].compilation_status.value == "partial"
    assert nodes["CD09.ROOT"].operator == "AND"
    assert children("CD09", "ROOT") == {"CD09.HISTORY", "CD09.VIRAL_ANY"}
    assert nodes["CD09.VIRAL_ANY"].operator == "OR"
    assert children("CD09", "VIRAL_ANY") == {"CD09.HBV_ANY", "CD09.HCV_ANY"}
    assert nodes["CD01.DIAGNOSTIC_ALL"].operator == "AND"
    assert children("CD01", "DIAGNOSTIC_ALL") == {"CD01.CBC_2OF3", "CD01.MARROW_ASP", "CD01.MARROW_BIOPSY", "CD01.EXCLUSION"}
    assert nodes["CD19.SCORE_XRAY"].operator == "AND"
    assert nodes["CD19.SCORE"].expected_condition["value"] == 6
    assert nodes["CD19.SCORE"].expected_condition["operator"] == "gte"
    assert nodes["CD19.FUNCTION"].compilation_status.value == "partial"


def test_CD03肾病恰好四选一且尿蛋白定量独立分支() -> None:
    _, asset = _load()
    revision = asset.policy_sets[0].disease_revisions[2]
    nodes = {n.node_id: n for n in revision.nodes}
    assert nodes["CD03.DKD"].operator == "OR"
    assert {n.node_id for n in revision.nodes if n.parent_node_id == "CD03.DKD"} == {
        "CD03.DKD_EGFR", "CD03.DKD_PROTEIN_POSITIVE", "CD03.DKD_PROTEIN_QUANT", "CD03.DKD_UACR",
    }
    assert nodes["CD03.DKD_PROTEIN_QUANT"].operator == "OR"
    for node_id, operator, value, unit in [
        ("DKD_EGFR", "lt", 60, "mL/min/1.73m2"),
        ("DKD_PROTEIN_RATIO", "gte", 300, "mg/g"),
        ("DKD_PROTEIN_24H", "gte", 0.5, "g/24h"),
        ("DKD_UACR", "gte", 300, "mg/g"),
    ]:
        policy = nodes[f"CD03.{node_id}"].expected_condition
        assert (policy["operator"], policy["value"], policy["unit"]) == (operator, value, unit)
    assert nodes["CD03.CAD_BRANCH"].compilation_status.value == "partial"
    assert not automatic_evaluation_eligible(revision)


@pytest.mark.parametrize("field,value", [("operator", "approximately"), ("value", True), ("value", "sixty"), ("value", float("inf")), ("unit", ""), ("terms", [])])
def test_numeric非法阈值和单位拒绝(field, value) -> None:
    _, asset = _load()
    node = next(n for n in asset.policy_sets[0].disease_revisions[2].nodes if n.node_id == "CD03.DKD_EGFR")
    raw = node.model_dump(mode="json")
    raw["expected_condition"][field] = value
    with pytest.raises(ValidationError, match="numeric"):
        CriterionNode.model_validate(raw)


def test_没有书面解释不能解除C类阻断且空逻辑不能假装完整() -> None:
    _, asset = _load()
    raw = asset.model_dump(mode="json")
    revision = raw["policy_sets"][0]["disease_revisions"][0]
    revision["expert_interpretation_ids"] = []
    for node in revision["nodes"]:
        node["expert_interpretation_ids"] = []
    with pytest.raises(ValidationError, match="必须有书面解释"):
        ChronicDiseaseCriteriaAsset.model_validate(raw)
    raw = asset.model_dump(mode="json")
    node = next(n for n in raw["policy_sets"][0]["disease_revisions"][2]["nodes"] if n["node_id"] == "CD03.CAD_BRANCH")
    node["compilation_status"] = "compiled"
    with pytest.raises(ValidationError, match="显式子节点"):
        ChronicDiseaseCriteriaAsset.model_validate(raw)


def test_解释篡改即使重算manifest也拒绝(tmp_path: Path) -> None:
    raw = json.loads(SOURCES.read_text())
    raw["expert_interpretations"][0]["statement"] += "未授权修改"
    raw["manifest_checksum"] = source_manifest_payload_checksum(raw)
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="interpretation checksum"):
        load_source_manifest(path, root=ROOT)


def test_解释不能指向无关节点() -> None:
    manifest, asset = _load()
    raw = asset.model_dump(mode="json")
    revision = raw["policy_sets"][0]["disease_revisions"][2]
    retina = next(n for n in revision["nodes"] if n["node_id"] == "CD03.RETINA")
    retina["expert_interpretation_ids"] = revision["expert_interpretation_ids"]
    invalid = ChronicDiseaseCriteriaAsset.model_validate(raw)
    with pytest.raises(ValueError, match="超出书面解释范围"):
        validate_source_isolation(invalid, manifest)


def test_重建不依赖生成文件或原xlsx(monkeypatch, tmp_path: Path) -> None:
    module = _builder_module()
    monkeypatch.setattr(module, "CRITERIA_OUT", tmp_path / "absent.json")
    monkeypatch.setattr(module, "SOURCES_OUT", tmp_path / "absent-sources.json")
    manifest, asset = module.rebuild_existing()
    assert module.pretty_json_bytes(manifest) == SOURCES.read_bytes()
    assert module.pretty_json_bytes(asset) == CRITERIA.read_bytes()


def test_来源schema文件与模型一致() -> None:
    from javert.chronic.contracts import SourceManifest
    module = _builder_module()
    assert module.SOURCE_SCHEMA_OUT.read_bytes() == module.pretty_json_bytes(SourceManifest.model_json_schema())


@pytest.mark.parametrize("name,value,expected", [
    ("DKD_EGFR", "59.999", "SATISFIED"), ("DKD_EGFR", "60", "NOT_SATISFIED"),
    ("DKD_PROTEIN_RATIO", "300", "SATISFIED"), ("DKD_PROTEIN_RATIO", "299.999", "NOT_SATISFIED"),
    ("DKD_PROTEIN_24H", "0.5", "SATISFIED"), ("DKD_PROTEIN_24H", "0.4999", "NOT_SATISFIED"),
    ("DKD_UACR", "300", "SATISFIED"), ("DKD_UACR", "299.999", "NOT_SATISFIED"),
])
def test_CD03生成数值策略可被runtime消费且不舍入(name, value, expected) -> None:
    from javert.chronic.runtime import _leaf_assessment
    from javert.clinical_criteria.contracts import EvidenceAnchor, Observation
    _, asset = _load()
    revision = asset.policy_sets[0].disease_revisions[2]
    node = next(n for n in revision.nodes if n.node_id == f"CD03.{name}")
    policy = node.expected_condition
    fact = Observation(
        fact_type=node.fact_type, concept_id=policy["concept_id"],
        raw_value=value, raw_unit=policy["unit"], assertion=policy["terms"][0],
        polarity="positive", certainty="confirmed", source_domain="labs",
        anchor=EvidenceAnchor(source="synthetic-lab", locator="synthetic-row", text="合成检验阈值证据"),
    )
    assert _leaf_assessment(node, [fact], revision.revision_id).state == expected
    fact.raw_unit = "unknown-unit"
    assert _leaf_assessment(node, [fact], revision.revision_id).state == "UNKNOWN"


def test_修改解释不能沿用r2编译规则(monkeypatch, tmp_path: Path) -> None:
    from javert.chronic.knowledge import interpretation_payload_checksum
    module = _builder_module()
    raw = json.loads(module.FEEDBACK_OUT.read_text())
    raw[0]["statement"] = "合成：变更为AND"
    raw[0]["interpretation_checksum"] = interpretation_payload_checksum(raw[0])
    path = tmp_path / "feedback.json"
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(module, "FEEDBACK_OUT", path)
    with pytest.raises(ValueError, match="必须创建新revision"):
        module.rebuild_existing()


def test_作者摘要即使标注approved仍不能冒充原文审核() -> None:
    from datetime import date
    from javert.chronic.contracts import DiseaseRevision
    manifest, asset = _load()
    raw = asset.policy_sets[0].disease_revisions[2].model_dump(mode="json")
    history = next(n for n in raw["nodes"] if n["node_id"] == "CD03.DM_HISTORY")
    history["parent_node_id"] = None
    raw.update(nodes=[history], root_node_id=history["node_id"], expert_interpretation_ids=[],
               lifecycle="active", review_status="approved", reviewed_by="synthetic-reviewer",
               reviewed_at="2026-09-08T00:00:00Z", review_evidence_ref="synthetic-review-only")
    revision = DiseaseRevision.model_validate(raw)
    policy = asset.policy_sets[0].model_copy(update={
        "lifecycle": "active", "review_status": "approved", "execution_enabled": True,
        "disease_revisions": [revision],
    })
    for fragment in manifest.fragments:
        fragment.review_status = "approved"
    assert not automatic_evaluation_eligible(revision, policy=policy, source_manifest=manifest, service_date=date(2026, 9, 8))
