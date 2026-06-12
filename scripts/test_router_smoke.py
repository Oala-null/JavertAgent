"""smoke test RuleRouter on J66252 / J18906 / J40485.

预期:
  - J66252 (甲状腺恶性肿瘤, 117 fees) — 应触发 ~10-20 条 yaml (M1 重复 + M2 过度检查 信号)
  - J18906 (脑干占位, 海绵状血管瘤) — 应触发 ~5-15 条
  - J40485 (甲状腺良性 1 日手术) — 应几乎全跳 (v0.4 全量 0 V), 期望 final <= 5

跑法:
  uv run python scripts/test_router_smoke.py
"""
from __future__ import annotations

from pathlib import Path

from javert.config import get_config
from javert.data.csv_loader import CsvLoader
from javert.routing import (
    PatientRecord,
    RuleRouter,
    build_patient_record_for_router,
    default_shi_zd_path,
)


def run_one(router: RuleRouter, record: PatientRecord) -> None:
    import time
    print(f"\n{'=' * 72}")
    print(f"patient: {record.patient_id}")
    print(f"  fees={len(record.fee_items)}  diag={len(record.diagnoses)}  "
          f"visit={record.visit_type}  hosp_lvl={record.hospital_level}")
    if record.diagnoses:
        print(f"  diagnoses: {record.diagnoses[:5]}{' ...' if len(record.diagnoses) > 5 else ''}")
    t0 = time.perf_counter()
    decision = router.route(record)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"  route() 耗时:   {elapsed_ms:.1f} ms")
    print(f"  ───── routing stats ─────")
    for k, v in decision.stats.items():
        print(f"  {k:35} {v}")
    print(f"  ───── java rules triggered ─────")
    for jr, evs in sorted(decision.java_triggered.items()):
        examples = [(e.matched_keyword, e.matched_fee_name) for e in evs[:3]]
        print(f"  {jr}  ({len(evs)} 命中)  e.g. {examples}")
    print(f"  ───── final javert rules ({decision.n_final}) ─────")
    for rid in decision.final_rules[:30]:
        print(f"    {rid}")
    if decision.n_final > 30:
        print(f"    ... +{decision.n_final - 30} more")


def main() -> None:
    cfg = get_config()
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    zd_path = default_shi_zd_path()

    router = RuleRouter.from_defaults()
    print(f"loaded router:  {len(router._yaml_meta)} javert yaml,  "
          f"{router.violation_dict['n_entries']} dict entries,  "
          f"{router.violation_dict['n_keywords']} unique keywords")

    for pid in ["J66252", "J18906", "J40485", "J13365", "J61556"]:
        try:
            rec = build_patient_record_for_router(
                pid, loader,
                shi_zd_path=zd_path if zd_path.exists() else None,
            )
            run_one(router, rec)
        except Exception as e:
            print(f"[err] {pid}: {e}")


if __name__ == "__main__":
    main()
