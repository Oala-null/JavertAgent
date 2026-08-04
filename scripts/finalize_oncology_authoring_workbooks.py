#!/usr/bin/env python3
"""补充机器列保护并执行本地、零数据库连接验证。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from javert.oncology.authoring.workbooks import (  # noqa: E402
    protect_workbook,
    validate_workbook,
    write_validation_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "outputs/add-oncology-kb-authoring",
    )
    args = parser.parse_args(argv)
    output = args.output_dir
    targets = (
        (output / "肿瘤药指南适应证与医保限定条件树KB.xlsx", "eligibility"),
        (output / "肿瘤治疗方案组成KB.xlsx", "regimen"),
    )
    failed = False
    for path, kind in targets:
        protect_workbook(path)
        issues = validate_workbook(path, kind=kind)
        report = path.with_suffix(".validation.json")
        write_validation_report(report, issues)
        print(f"{path.name}: errors={len(issues)} report={report.name}")
        failed = failed or bool(issues)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
