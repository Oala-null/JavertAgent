# -*- coding: utf-8 -*-
"""三例金标只保留最小、去标识事实，不复制真实患者导出."""

from __future__ import annotations

import json
from pathlib import Path


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "oncology"
FORBIDDEN_KEYS = {
    "patient_id",
    "patient_name",
    "姓名",
    "身份证号",
    "手机号",
    "住址",
    "hospital_code",
    "bah",
    "ba_id",
}


def test_golden_fixture_shells_are_minimized_and_deidentified():
    paths = sorted(FIXTURE_DIR.glob("*.json"))
    assert [path.stem for path in paths] == [
        "pola_cycle_conflict",
        "pola_transplant_gap",
        "urothelial_her2_low",
    ]
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["deidentified"] is True
        assert payload["fixture_id"].startswith("golden-")
        assert not (set(payload) & FORBIDDEN_KEYS)
        raw = path.read_text(encoding="utf-8")
        assert len(raw) < 2500
        assert "H310" not in raw


def test_golden_fixtures_contain_only_task_relevant_signals():
    urothelial = json.loads(
        (FIXTURE_DIR / "urothelial_her2_low.json").read_text(encoding="utf-8")
    )
    assert urothelial["pathology_observations"][0]["text"].endswith("CerbB2(1+)")
    assert urothelial["prior_treatment_observations"] == []
    assert urothelial["service_date"] == "2026-06-18"

    cycle_conflict = json.loads(
        (FIXTURE_DIR / "pola_cycle_conflict.json").read_text(encoding="utf-8")
    )
    assert "第四次 Pola-R-GemOx" in cycle_conflict["treatment_observations"][0][
        "text"
    ]
    assert cycle_conflict["encounter_date"][:4] == "2026"
    assert cycle_conflict["treatment_observations"][0]["document_date"][:4] == "2025"

    transplant_gap = json.loads(
        (FIXTURE_DIR / "pola_transplant_gap.json").read_text(encoding="utf-8")
    )
    assert transplant_gap["patient_context"]["age"] == 74
    assert transplant_gap["service_date"][:4] == "2026"
    assert "不适合造血干细胞移植" in transplant_gap["missing_documentation"][0]
