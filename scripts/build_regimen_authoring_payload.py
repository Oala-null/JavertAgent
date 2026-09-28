#!/usr/bin/env python3
"""生成去标识方案 authoring canonical payload。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from javert.oncology.authoring.ids import canonical_json_bytes  # noqa: E402
from javert.oncology.authoring.regimens import (  # noqa: E402
    build_regimen_authoring_records,
    canonical_regimen_payload,
)


def main() -> int:
    output = ROOT / "docs/oncology/authoring/regimen_authoring_payload.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_regimen_payload(build_regimen_authoring_records(ROOT))
    output.write_bytes(canonical_json_bytes(payload) + b"\n")
    print(
        " ".join(
            f"{key}={len(value)}"
            for key, value in payload.items()
            if isinstance(value, list)
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
