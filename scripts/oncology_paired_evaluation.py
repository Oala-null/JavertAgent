# -*- coding: utf-8 -*-
"""从去标识 same-run oncology shadow 输入生成 canonical paired A/B 包。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from javert.evidence.evaluation import (
    EvaluationPlan, ExpertAdjudication, persist_evaluation_package,
)
from javert.oncology.evaluation_adapter import build_oncology_paired_package


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 oncology same-input paired A/B evaluation")
    parser.add_argument("--input", type=Path, required=True, help="去标识 paired 输入 JSON")
    parser.add_argument("--output-dir", type=Path, required=True, help="全新输出目录，禁止覆盖")
    args = parser.parse_args()
    raw = json.loads(args.input.read_text(encoding="utf-8"))
    plan = EvaluationPlan.model_validate(raw["plan"])
    adjudications = tuple(
        ExpertAdjudication.model_validate(item) for item in raw.get("adjudications", [])
    )
    package = build_oncology_paired_package(
        evaluation_id=str(raw["evaluation_id"]), plan=plan,
        rows=list(raw.get("rows") or []), adjudications=adjudications,
    )
    manifest = persist_evaluation_package(package, args.output_dir, real_case=False)
    print(
        f"{args.output_dir} status={package.report.status.value} "
        f"cases={len(package.cases)} manifest={manifest.evaluation_id}"
    )
    if package.report.status.value in {"INVALID", "FAIL"}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
