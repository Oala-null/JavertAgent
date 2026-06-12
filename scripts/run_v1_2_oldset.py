# -*- coding: utf-8 -*-
"""跑 v1.2 batch: 老病人 list × ready 全集规则 + use_router + JAVERT_BATCH_TAG=v1.2.

部署位置: 192.168.31.62 (LLM sglang 在那), 不在本地 Mac 跑.

使用:
  ssh admin2@192.168.31.62
  cd ~/javert
  set -a && source .env && set +a
  export JAVERT_BATCH_TAG=v1.2     # 关键: 这次跑的 audit 会被标 v1.2

  # 先跑 7 核心 (专家批注的 7 个老病人)
  nohup uv run python scripts/run_v1_2_oldset.py --limit 7 > output/v1_2_oldset/_main_7.log 2>&1 &
  disown
  tail -f output/v1_2_oldset/_progress.jsonl

  # 7 核心跑完看效果, 满意则跑剩 23 个
  nohup uv run python scripts/run_v1_2_oldset.py --skip 7 > output/v1_2_oldset/_main_rest.log 2>&1 &

进度: output/v1_2_oldset/_progress.jsonl (一行一个 patient)
失败: 单 patient 异常不中断, 写 status=error 继续下一个.

依赖: data/v1_2_oldset.txt (由 select_v1_2_oldset.py 生成, 30 行)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

LIST_PATH = ROOT / "data" / "v1_2_oldset.txt"
OUT_DIR = ROOT / "output" / "v1_2_oldset"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit", type=int, default=None,
        help="只跑前 N 个 patient (用于先验证 7 核心)",
    )
    parser.add_argument(
        "--skip", type=int, default=0,
        help="跳过前 N 个 (用于跑剩 23 个: --skip 7)",
    )
    parser.add_argument(
        "--list", type=Path, default=LIST_PATH,
        help="patient_id list (一行一个), 默认 data/v1_2_oldset.txt",
    )
    parser.add_argument(
        "--concurrency", type=int, default=5,
        help="audit-patient 内部规则并发度 (默认 5)",
    )
    parser.add_argument(
        "--no-router", action="store_true",
        help="禁用 router prefilter (默认开启)",
    )
    parser.add_argument(
        "--priority", default="all",
        help="规则集合: P0 / P1 / P2 / all (默认 all = ready 全集)",
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(OUT_DIR / "_run.log", encoding="utf-8"),
        ],
    )
    logger = logging.getLogger("v1.2_oldset")

    if not args.list.exists():
        raise SystemExit(
            f"未找到 {args.list}; 先在本地跑 scripts/select_v1_2_oldset.py 生成"
        )

    pids_all = [
        line.strip() for line in args.list.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    pids = pids_all[args.skip:]
    if args.limit:
        pids = pids[: args.limit]

    batch_tag = os.environ.get("JAVERT_BATCH_TAG", "(未设)")
    logger.info(
        "v1.2 batch: %d/%d 病人 (skip=%d limit=%s), batch_tag=%s, priority=%s, router=%s, conc=%d",
        len(pids), len(pids_all), args.skip, args.limit or "all", batch_tag,
        args.priority, not args.no_router, args.concurrency,
    )

    if batch_tag != "v1.2":
        logger.warning(
            "JAVERT_BATCH_TAG != 'v1.2' (当前: %r). 这次跑的 audit 不会被标 v1.2,"
            " 工作台 sidebar 无法置顶. 继续请 Ctrl-C 中断并设 export JAVERT_BATCH_TAG=v1.2",
            batch_tag,
        )
        if not os.environ.get("JAVERT_BATCH_TAG_CONFIRM"):
            raise SystemExit(
                "中止. 请 export JAVERT_BATCH_TAG=v1.2 再跑,"
                " 或 JAVERT_BATCH_TAG_CONFIRM=1 强制继续"
            )

    # 延后 import 让 sys.path 先生效
    from javert.commands.audit_patient import run_audit_patient

    overall_start = time.perf_counter()
    progress_file = OUT_DIR / "_progress.jsonl"
    for i, pid in enumerate(pids, 1):
        t0 = time.perf_counter()
        logger.info("[%d/%d] %s 开始 (priority=%s)", i, len(pids), pid, args.priority)
        try:
            code = run_audit_patient(
                patient_id=pid,
                priority=args.priority,
                rules_arg=None,
                share_tool_cache=True,
                concurrency=args.concurrency,
                use_router=not args.no_router,
            )
            status = "ok" if code == 0 else f"exit_{code}"
            err = None
        except Exception as e:
            logger.exception("patient=%s 异常: %s", pid, e)
            status = "error"
            err = str(e)

        dt = time.perf_counter() - t0
        record = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "i": i,
            "total": len(pids),
            "patient_id": pid,
            "status": status,
            "duration_s": round(dt, 1),
            "batch_tag": batch_tag,
            "error": err,
        }
        with open(progress_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.info(
            "[%d/%d] %s 完成: status=%s %.1fs",
            i, len(pids), pid, status, dt,
        )

    logger.info(
        "全部跑完: %.1f min total", (time.perf_counter() - overall_start) / 60.0,
    )


if __name__ == "__main__":
    main()
