#!/usr/bin/env python3
"""限量寻找通过取数预检的首页；stdout仅返回一个SYXH，便于院内Bash接收。"""
import argparse
from contextlib import closing
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from javert.config import get_config
from javert.data import hub_source as hs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-candidates", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.max_candidates <= 100:
        raise SystemExit("候选上限须在1-100")
    cfg = get_config()
    if cfg.hub_linkage_mode != "shanghai" or not cfg.hub_hospital_code:
        raise SystemExit("请设置shanghai模式和真实院区")
    with closing(hs.connect(cfg, timeout=15)) as cn:
        cn.timeout = 60
        candidates = hs.q(cn, """
            SELECT DISTINCT TOP (?) b.SYXH, b.CYRQ
            FROM dbo.TB_BA_SYJBK b
            JOIN dbo.TB_CIS_LEAVEHOSPITAL_SUMMARY s
              ON s.YLJGYQDM=b.YLJGYQDM AND s.BAH=b.BAH AND s.KH=b.KH AND s.KLX=b.KLX
            WHERE b.YLJGYQDM=?
              AND EXISTS (SELECT 1 FROM dbo.TB_HIS_ZY_FEE_DETAIL_FS f
                          WHERE f.YLJGYQDM=s.YLJGYQDM AND f.JZLSH=s.JZLSH)
            ORDER BY b.CYRQ DESC, b.SYXH
        """, (args.max_candidates, cfg.hub_hospital_code))
        for i, pid in enumerate(candidates["SYXH"], 1):
            try:
                bundle = hs.fetch_hospital_bundle(cn, str(pid), cfg.hub_hospital_code)
            except hs.HospitalLinkageError as exc:
                print(f"候选{i}未通过：{exc}", file=sys.stderr)
                continue
            print(f"候选{i}通过：费用{len(bundle['fees'])}行，文书{len(bundle['notes'])}行；尚未审计",
                  file=sys.stderr)
            print(pid)
            return
    raise SystemExit("本次候选范围无通过者；不代表全院无数据，请查看失败阶段。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"候选查询失败：{type(exc).__name__}；未启动审计")
