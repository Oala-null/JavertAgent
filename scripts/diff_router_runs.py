"""Diff audit_runs by created_at cutoffs: off batch vs on batch (router 漏检验证).

跑法:
  uv run python scripts/diff_router_runs.py J66252 \\
      --cutoff-before-off "2026-05-18 07:19:28" \\
      --cutoff-after-off  "2026-05-20 03:00:00" \\
      --cutoff-after-on   "2026-05-20 03:30:00"

逻辑:
  off_runs  = audit_runs WHERE patient=PID AND cutoff_before_off < created_at <= cutoff_after_off
  on_runs   = audit_runs WHERE patient=PID AND cutoff_after_off  < created_at <= cutoff_after_on
  按 rule_id 各取一条 (audit-patient 同一规则在 batch 内只跑一次)

输出:
  table: rule_id | off | on(skipped/V/C/I) | diff_flag
  汇总: V 命中漏检数, V 一致数, off 单 V 总数, on 跑数, router 真实节省 LLM 条数

漏检定义:
  - off=V 且 on 没跑 → ★★ HIGH (router 漏检真违规)
  - off=V 且 on=C 或 on=I → ★ MID (router 跑了但分歧)
  - off=I 且 on 没跑 → 半成色 (off 自己也没判出来, router 漏了也无所谓)
  - off=C 且 on 没跑 → ✓ router 正确节省
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

import click


@click.command()
@click.argument("patient_id")
@click.option("--db", "db_path", default="output/audit.sqlite", show_default=True)
@click.option("--cutoff-before-off", required=True, help='off 跑之前的 max(created_at), e.g. "2026-05-18 07:19:28"')
@click.option("--cutoff-after-off",  required=True, help='off 跑完之后 (== on 跑之前) 时戳')
@click.option("--cutoff-after-on",   required=True, help='on 跑完之后时戳; 设个未来值如 "2099-01-01" 也行')
def main(patient_id, db_path, cutoff_before_off, cutoff_after_off, cutoff_after_on):
    conn = sqlite3.connect(db_path)

    def _fetch(cutoff_lo, cutoff_hi):
        # 同一 rule_id 取最新 created_at 的 row
        sql = """
        SELECT a.rule_id, a.verdict, a.confidence, a.duration_ms, a.created_at
        FROM audit_runs a
        INNER JOIN (
            SELECT rule_id, MAX(created_at) AS mc
            FROM audit_runs
            WHERE patient_id = ? AND created_at > ? AND created_at <= ?
            GROUP BY rule_id
        ) latest ON a.rule_id = latest.rule_id AND a.created_at = latest.mc
        WHERE a.patient_id = ? AND a.created_at > ? AND a.created_at <= ?
        ORDER BY a.rule_id
        """
        cur = conn.execute(sql, (patient_id, cutoff_lo, cutoff_hi, patient_id, cutoff_lo, cutoff_hi))
        return {r[0]: r for r in cur.fetchall()}  # rule_id → row

    off = _fetch(cutoff_before_off, cutoff_after_off)
    on  = _fetch(cutoff_after_off, cutoff_after_on)

    click.echo(f"=== J66252 router off/on verdict diff ===")
    click.echo(f"  off batch: {len(off)} rules  (cutoff {cutoff_before_off} → {cutoff_after_off})")
    click.echo(f"  on  batch: {len(on)} rules   (cutoff {cutoff_after_off} → {cutoff_after_on})")
    click.echo("")

    # 全集 = off (因为 off 跑全部 P0, on 是子集)
    if not off:
        click.echo("✗ off 批为空, 检查 cutoff 时戳")
        return

    n_v_only_off = 0   # off=V, on=skipped → ★★ HIGH 漏检
    n_v_both = 0       # off=V, on=V → ok
    n_v_mismatch = 0   # off=V, on != V (jumped)
    n_c_only_off = 0   # off=C, on=skipped → ✓ router 节省
    n_i_only_off = 0   # off=I, on=skipped → 半成色
    saved_calls = 0    # off 跑了但 on 没跑的条数

    leak_high: list[str] = []
    mismatch: list[str] = []
    consistent_v: list[str] = []

    click.echo(f"| {'rule_id':7} | {'off':4} | {'conf':5} | {'on':6} | {'conf':5} | flag")
    click.echo("|" + "-" * 60)

    for rid in sorted(off.keys()):
        o = off[rid]
        n = on.get(rid)
        off_v, off_c = o[1], o[2] or 0.0
        if n is None:
            saved_calls += 1
            if off_v == "VIOLATION":
                n_v_only_off += 1
                leak_high.append(rid)
                flag = "★★ LEAK (off=V, on=skipped)"
            elif off_v == "CLEAN":
                n_c_only_off += 1
                flag = "✓ router saved"
            else:  # INCONCLUSIVE
                n_i_only_off += 1
                flag = "~  off=I, on=skipped"
            click.echo(f"| {rid:7} | {off_v[0]:4} | {off_c:5.2f} | {'skip':6} | {'-':5} | {flag}")
        else:
            on_v, on_c = n[1], n[2] or 0.0
            if off_v == "VIOLATION" and on_v == "VIOLATION":
                n_v_both += 1
                consistent_v.append(rid)
                flag = "✓ V↔V"
            elif off_v == "VIOLATION":
                n_v_mismatch += 1
                mismatch.append(f"{rid}({off_v[0]}→{on_v[0]})")
                flag = f"★ {off_v[0]} ↔ {on_v[0]}"
            elif on_v == "VIOLATION":
                mismatch.append(f"{rid}({off_v[0]}→{on_v[0]})")
                flag = f"~ {off_v[0]} ↔ {on_v[0]} (on 多发现)"
            else:
                flag = f"{off_v[0]} ↔ {on_v[0]}"
            click.echo(f"| {rid:7} | {off_v[0]:4} | {off_c:5.2f} | {on_v[0]:6} | {on_c:5.2f} | {flag}")

    click.echo("\n=== 摘要 ===")
    off_v_total = sum(1 for o in off.values() if o[1] == "VIOLATION")
    off_c_total = sum(1 for o in off.values() if o[1] == "CLEAN")
    off_i_total = sum(1 for o in off.values() if o[1] == "INCONCLUSIVE")
    on_v_total = sum(1 for n in on.values() if n[1] == "VIOLATION")
    on_c_total = sum(1 for n in on.values() if n[1] == "CLEAN")
    on_i_total = sum(1 for n in on.values() if n[1] == "INCONCLUSIVE")

    click.echo(f"off: V={off_v_total} C={off_c_total} I={off_i_total}  (total {len(off)})")
    click.echo(f"on : V={on_v_total} C={on_c_total} I={on_i_total}  (total {len(on)})")
    click.echo(f"router saved: {saved_calls} LLM calls ({saved_calls/len(off)*100:.1f}%)")
    click.echo("")
    click.echo(f"★★ HIGH 漏检 (off=V, on=skipped): {n_v_only_off}")
    if leak_high:
        click.echo(f"   rules: {', '.join(leak_high)}")
    click.echo(f"★ MID 分歧 (off=V, on={'V'.lower()}≠V): {n_v_mismatch}")
    if mismatch:
        click.echo(f"   rules: {', '.join(mismatch)}")
    click.echo(f"✓ V 一致 (off=V & on=V): {n_v_both}")
    click.echo(f"✓ C router 节省 (off=C, on=skipped): {n_c_only_off}")
    click.echo(f"~ I 半成色 (off=I, on=skipped): {n_i_only_off}")

    # Total off LLM 耗时
    off_total_ms = sum(o[3] or 0 for o in off.values())
    on_total_ms = sum(n[3] or 0 for n in on.values())
    click.echo("")
    click.echo(f"耗时累计: off {off_total_ms/1000:.1f}s ({off_total_ms/60000:.1f} min)")
    click.echo(f"          on  {on_total_ms/1000:.1f}s ({on_total_ms/60000:.1f} min)")
    click.echo(f"实际省: {(off_total_ms-on_total_ms)/60000:.1f} min ({(off_total_ms-on_total_ms)/off_total_ms*100:.1f}%)")


if __name__ == "__main__":
    main()
