# -*- coding: utf-8 -*-
"""确定性生成三类肿瘤知识资产的共享 JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path

from javert.oncology.knowledge import (
    EligibilityAssetEnvelope,
    PathologyAssetEnvelope,
    RegimenAssetEnvelope,
)


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "configs" / "schemas"
SCHEMAS = {
    "oncology_eligibility_rules.schema.json": EligibilityAssetEnvelope,
    "pathology_biomarker_kb.schema.json": PathologyAssetEnvelope,
    "oncology_regimen_kb.schema.json": RegimenAssetEnvelope,
}


def build_schemas(output_dir: Path = OUT_DIR) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, model in SCHEMAS.items():
        path = output_dir / filename
        content = json.dumps(
            model.model_json_schema(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        path.write_text(content + "\n", encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    for schema_path in build_schemas():
        print(schema_path)
