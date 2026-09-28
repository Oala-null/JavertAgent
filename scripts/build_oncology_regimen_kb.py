# -*- coding: utf-8 -*-
"""构建肿瘤方案 KB，并从可选文书语料生成去标识待审核别名候选."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Iterable

from javert.oncology.knowledge import (
    KnowledgeEntryMetadata,
    KnowledgeMetadata,
    ReviewStatus,
    SourceReference,
    asset_payload_checksum,
    canonical_json_bytes,
    sha256_digest,
)
from javert.oncology.regimen import (
    AliasType,
    DrugConcept,
    RegimenComponent,
    RegimenEntry,
    RegimenKnowledgeAsset,
    TypedDrugAlias,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_KB = ROOT / "configs" / "oncology_drug_kb.json"
OUT_KB = ROOT / "configs" / "oncology_regimen_kb.json"
OUT_REVIEW = ROOT / "docs" / "oncology" / "regimen_alias_candidates.json"
DEFAULT_NOTES = ROOT / "data" / "case_notes.csv"
BUILD_DATE = date(2026, 7, 17)

_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]*"
    r"(?:[\s\-‐‑‒–—―+/]+[A-Za-z][A-Za-z0-9]*){0,6})"
    r"(?=\s*(?:方案|化疗))",
    re.I,
)
_NOISE_TOKEN_RE = re.compile(
    r"^(?:MG|G|ML|UG|MCG|QD|BID|TID|QID|Q\d+[HDW]|D\d+|C\d+|"
    r"QW|QOD|BIW|TIW|IV|IVGTT|PO|IM|SC|O2|\d+)+$",
    re.I,
)
_POSITIONAL_NOISE_TOKEN_RE = re.compile(
    r"^(?:QD|BID|TID|QID|Q\d+[HDW]|D\d+|C\d+|QW|QOD|BIW|TIW)$",
    re.I,
)
_CANDIDATE_SEPARATOR_RE = re.compile(r"[\s\-‐‑‒–—―+/]+")
MIN_CANDIDATE_FREQUENCY = 2

_HUB_DOCUMENT_SQL = """
SELECT ZW
FROM TB_CIS_MEDICAL_DOCUMENT
WHERE ZW LIKE N'%方案%' OR ZW LIKE N'%化疗%'
ORDER BY YLJGYQDM, JZLSH, WSLSH
"""
_HUB_SUMMARY_SQL = """
SELECT RYZD, CYZD, RYZZTZ, JCHZ, ZLGC, HBZ, CYQKMS, CYYZ, ZLJGSM, YYZTB1, YYZTB2
FROM TB_CIS_LEAVEHOSPITAL_SUMMARY s
WHERE NOT EXISTS (
    SELECT 1
    FROM TB_CIS_MEDICAL_DOCUMENT d
    WHERE d.YLJGYQDM = s.YLJGYQDM
      AND d.JZLSH = s.JZLSH
      AND (d.WSLB = '05' OR d.WSMC LIKE N'%出院小结%' OR d.WSMC LIKE N'%出院记录%')
)
ORDER BY YLJGYQDM, JZLSH
"""


def _source_ref(source_path: Path) -> SourceReference:
    return SourceReference(
        source_id="derived:oncology_drug_kb",
        title="Javert 肿瘤药知识库与经评审方案映射",
        version="1.0",
        publication_date=date(2026, 6, 1),
        retrieval_date=BUILD_DATE,
        checksum=sha256_digest(source_path.read_bytes()),
    )


def _entry_meta(source: SourceReference) -> KnowledgeEntryMetadata:
    return KnowledgeEntryMetadata(
        content_version="2026.07.17",
        effective_from=date(2025, 1, 1),
        source_refs=[source],
        review_status=ReviewStatus.APPROVED,
    )


def _alias(value: str, alias_type: AliasType) -> TypedDrugAlias:
    return TypedDrugAlias(value=value, alias_type=alias_type)


def _drug_concepts() -> list[DrugConcept]:
    return [
        DrugConcept(
            concept_id="disitamab-vedotin",
            generic_name="注射用维迪西妥单抗",
            aliases=[
                _alias("disitamab vedotin", AliasType.ENGLISH_GENERIC),
                _alias("爱地希", AliasType.TRADE),
            ],
            insurance_codes=["XL01EHW124B001010180949"],
        ),
        DrugConcept(
            concept_id="polatuzumab-vedotin",
            generic_name="注射用维泊妥珠单抗",
            aliases=[
                _alias("polatuzumab vedotin", AliasType.ENGLISH_GENERIC),
                _alias("优罗华", AliasType.TRADE),
                _alias("Pola", AliasType.REGIMEN_TOKEN),
            ],
            insurance_codes=["XL01FXW129B001010181735"],
        ),
        DrugConcept(
            concept_id="rituximab",
            generic_name="利妥昔单抗",
            aliases=[
                _alias("rituximab", AliasType.ENGLISH_GENERIC),
                _alias("美罗华", AliasType.TRADE),
                _alias("R", AliasType.REGIMEN_TOKEN),
            ],
        ),
        DrugConcept(
            concept_id="gemcitabine",
            generic_name="吉西他滨",
            aliases=[
                _alias("gemcitabine", AliasType.ENGLISH_GENERIC),
                _alias("Gem", AliasType.REGIMEN_TOKEN),
            ],
        ),
        DrugConcept(
            concept_id="oxaliplatin",
            generic_name="奥沙利铂",
            aliases=[
                _alias("oxaliplatin", AliasType.ENGLISH_GENERIC),
                _alias("Ox", AliasType.REGIMEN_TOKEN),
            ],
        ),
        DrugConcept(
            concept_id="cyclophosphamide",
            generic_name="环磷酰胺",
            aliases=[
                _alias("cyclophosphamide", AliasType.ENGLISH_GENERIC),
                _alias("C", AliasType.REGIMEN_TOKEN),
            ],
        ),
        DrugConcept(
            concept_id="doxorubicin",
            generic_name="多柔比星",
            aliases=[
                _alias("doxorubicin", AliasType.ENGLISH_GENERIC),
                _alias("阿霉素", AliasType.TRADE),
                _alias("H", AliasType.REGIMEN_TOKEN),
            ],
        ),
        DrugConcept(
            concept_id="vincristine",
            generic_name="长春新碱",
            aliases=[
                _alias("vincristine", AliasType.ENGLISH_GENERIC),
                _alias("O", AliasType.REGIMEN_TOKEN),
            ],
        ),
        DrugConcept(
            concept_id="prednisone",
            generic_name="泼尼松",
            aliases=[
                _alias("prednisone", AliasType.ENGLISH_GENERIC),
                _alias("强的松", AliasType.TRADE),
                _alias("P", AliasType.REGIMEN_TOKEN),
            ],
        ),
    ]


def _regimens(source: SourceReference) -> list[RegimenEntry]:
    meta = _entry_meta(source)
    return [
        RegimenEntry(
            regimen_id="pola-r-gemox",
            canonical_name="Pola-R-GemOx",
            aliases=["Pola-R-GemOx", "Pola R GemOx", "POLA-R-GEMOX"],
            cancer_contexts=["弥漫大B细胞淋巴瘤", "DLBCL"],
            components=[
                RegimenComponent(drug_concept_id="polatuzumab-vedotin", token="Pola"),
                RegimenComponent(drug_concept_id="rituximab", token="R"),
                RegimenComponent(drug_concept_id="gemcitabine", token="Gem"),
                RegimenComponent(drug_concept_id="oxaliplatin", token="Ox"),
            ],
            metadata=meta,
        ),
        RegimenEntry(
            regimen_id="r-gemox",
            canonical_name="R-GemOx",
            aliases=["R-GemOx", "R GemOx", "R-GEMOX"],
            cancer_contexts=["淋巴瘤", "弥漫大B细胞淋巴瘤", "DLBCL"],
            components=[
                RegimenComponent(drug_concept_id="rituximab", token="R"),
                RegimenComponent(drug_concept_id="gemcitabine", token="Gem"),
                RegimenComponent(drug_concept_id="oxaliplatin", token="Ox"),
            ],
            metadata=meta,
        ),
        RegimenEntry(
            regimen_id="r-chop",
            canonical_name="R-CHOP",
            aliases=["R-CHOP", "R CHOP"],
            cancer_contexts=["淋巴瘤", "弥漫大B细胞淋巴瘤", "DLBCL"],
            components=[
                RegimenComponent(drug_concept_id="rituximab", token="R"),
                RegimenComponent(drug_concept_id="cyclophosphamide", token="C"),
                RegimenComponent(drug_concept_id="doxorubicin", token="H"),
                RegimenComponent(drug_concept_id="vincristine", token="O"),
                RegimenComponent(drug_concept_id="prednisone", token="P"),
            ],
            metadata=meta,
        ),
        RegimenEntry(
            regimen_id="chop",
            canonical_name="CHOP",
            aliases=["CHOP"],
            cancer_contexts=["淋巴瘤", "弥漫大B细胞淋巴瘤", "DLBCL"],
            components=[
                RegimenComponent(drug_concept_id="cyclophosphamide", token="C"),
                RegimenComponent(drug_concept_id="doxorubicin", token="H"),
                RegimenComponent(drug_concept_id="vincristine", token="O"),
                RegimenComponent(drug_concept_id="prednisone", token="P"),
            ],
            metadata=meta,
        ),
    ]


def build_regimen_asset(source_path: Path = SOURCE_KB) -> dict:
    source = _source_ref(source_path)
    metadata = KnowledgeMetadata(
        schema_version="1.0.0",
        content_version="2026.07.17",
        effective_from=date(2025, 1, 1),
        source_refs=[source],
        checksum="sha256:" + "0" * 64,
        review_status=ReviewStatus.APPROVED,
    )
    asset = RegimenKnowledgeAsset(
        metadata=metadata,
        drug_concepts=_drug_concepts(),
        entries=_regimens(source),
    )
    raw = asset.model_dump(mode="json")
    raw["metadata"]["checksum"] = asset_payload_checksum(raw)
    return raw


def _content_columns(fieldnames: Iterable[str]) -> list[str]:
    preferred = (
        "content",
        "record_content",
        "note_content",
        "detail",
        "text",
        "内容",
        "病历内容",
    )
    fields = list(fieldnames)
    return [field for field in preferred if field in fields]


def _candidate_is_noise(value: str) -> bool:
    compact = _CANDIDATE_SEPARATOR_RE.sub("", value)
    if len(compact) < 2 or compact.isdigit():
        return True
    tokens = _CANDIDATE_SEPARATOR_RE.split(value)
    return all(_NOISE_TOKEN_RE.fullmatch(token) for token in tokens if token)


def _normalize_candidate(value: str) -> str:
    tokens = [
        token
        for token in _CANDIDATE_SEPARATOR_RE.split(
            unicodedata.normalize("NFKC", value).upper()
        )
        if token and not _POSITIONAL_NOISE_TOKEN_RE.fullmatch(token)
    ]
    return "-".join(tokens)


def _mine_texts(texts: Iterable[str]) -> tuple[Counter[str], str, int]:
    counts: Counter[str] = Counter()
    digest = hashlib.sha256()
    text_count = 0
    for raw_text in texts:
        text = unicodedata.normalize("NFKC", str(raw_text or ""))
        encoded = text.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        text_count += 1
        for match in _CANDIDATE_RE.finditer(text):
            value = match.group(1).strip()
            if _candidate_is_noise(value):
                continue
            alias = _normalize_candidate(value)
            if len(alias) >= 2:
                counts[alias] += 1
    return counts, f"sha256:{digest.hexdigest()}", text_count


def _candidate_report(
    counts: Counter[str],
    *,
    source_status: str,
    source_checksum: str,
    source_text_count: int,
    source_scope: str,
) -> dict:
    return {
        "schema_version": "1.0.0",
        "corpus_available": True,
        "source_status": source_status,
        "source_scope": source_scope,
        "source_checksum": source_checksum,
        "source_text_count": source_text_count,
        "minimum_frequency": MIN_CANDIDATE_FREQUENCY,
        "activation_policy": "frequency_never_activates_without_expert_review",
        "candidates": [
            {
                "normalized_alias": alias,
                "frequency": frequency,
                "review_status": "needs_review",
                "active": False,
            }
            for alias, frequency in sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
            if frequency >= MIN_CANDIDATE_FREQUENCY
        ],
    }


def mine_regimen_candidates(notes_path: Path | None) -> dict:
    """只输出 normalized candidate + 频次；不输出患者号或原文."""
    if notes_path is None or not notes_path.exists():
        return {
            "schema_version": "1.0.0",
            "corpus_available": False,
            "source_status": "source_unavailable",
            "activation_policy": "frequency_never_activates_without_expert_review",
            "candidates": [],
        }
    with notes_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = _content_columns(reader.fieldnames or [])
        if not columns:
            return {
                "schema_version": "1.0.0",
                "corpus_available": True,
                "source_status": "no_supported_content_column",
                "activation_policy": "frequency_never_activates_without_expert_review",
                "candidates": [],
            }
        texts = (
            " ".join(str(row.get(column) or "") for column in columns)
            for row in reader
        )
        counts, _, text_count = _mine_texts(texts)
    return _candidate_report(
        counts,
        source_status="mined",
        source_checksum=sha256_digest(notes_path.read_bytes()),
        source_text_count=text_count,
        source_scope="local_case_notes_csv",
    )


def _iter_hub_texts(connection, fetch_size: int = 1000) -> Iterable[str]:
    for sql in (_HUB_DOCUMENT_SQL, _HUB_SUMMARY_SQL):
        cursor = connection.cursor()
        cursor.execute(sql)
        while rows := cursor.fetchmany(fetch_size):
            for row in rows:
                for value in row:
                    if value:
                        yield str(value)
        cursor.close()


def mine_hub_regimen_candidates(connection, database_name: str) -> dict:
    """流式扫描中台正文；SQL 不选择患者号，返回值不保留原文."""
    counts, checksum, text_count = _mine_texts(_iter_hub_texts(connection))
    return _candidate_report(
        counts,
        source_status="mined_from_data_hub",
        source_checksum=checksum,
        source_text_count=text_count,
        source_scope=f"{database_name}:current_notes_regimen_cued",
    )


def write_assets(
    *,
    source_path: Path = SOURCE_KB,
    notes_path: Path | None = DEFAULT_NOTES,
    kb_out: Path = OUT_KB,
    review_out: Path = OUT_REVIEW,
) -> list[Path]:
    payloads = [
        build_regimen_asset(source_path),
        mine_regimen_candidates(notes_path),
    ]
    paths = [kb_out, review_out]
    for path, payload in zip(paths, payloads, strict=True):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(payload))
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE_KB)
    corpus = parser.add_mutually_exclusive_group()
    corpus.add_argument("--notes", type=Path, default=DEFAULT_NOTES)
    corpus.add_argument("--hub", action="store_true")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.hub:
        from javert.config import JavertConfig, get_config
        from javert.data import hub_source

        cfg = (
            JavertConfig(_env_file=str(args.env_file))
            if args.env_file
            else get_config()
        )
        connection = hub_source.connect(cfg)
        try:
            report = mine_hub_regimen_candidates(connection, cfg.hub_database)
        finally:
            connection.close()
        OUT_REVIEW.write_bytes(canonical_json_bytes(report))
        print(OUT_REVIEW)
        return
    for path in write_assets(source_path=args.source, notes_path=args.notes):
        print(path)


if __name__ == "__main__":
    main()
