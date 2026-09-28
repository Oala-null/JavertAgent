#!/usr/bin/env python3
"""动态生成肿瘤知识 authoring 来源 checksum 与计数基线。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from javert.oncology.authoring.baseline import write_baseline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/oncology/kb_authoring_baseline.json",
    )
    args = parser.parse_args()
    report = write_baseline(ROOT, args.output)
    print(f"baseline={args.output} checksum={report['snapshot_checksum']}")
    for name, value in report["counts"].items():
        print(f"{name}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
