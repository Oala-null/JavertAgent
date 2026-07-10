# -*- coding: utf-8 -*-
"""Javert CLI — 10 subcommand 入口骨架.

子命令:
    init           启动初始化 (snapshot + sample_pilot + rule_init + AuditStore.init + LLM 探活)
    list           列出全部规则及审计统计
    dry-run        单跑一条规则 (打印完整 trace + 写入 store)
    run            批跑一条规则 (--patient 或 --pilot)
    mark           变更规则 status (drafting/ready/validated/abandoned)
    report         按规则汇总 verdict 分布
    show           重放历史 audit 的 trace
    audit-patient  对单患者跑一组规则
    web            启动 Web UI
    prompt-fit     按模板渲染 prompt_addon 写回 rule yaml
    template       模板子命令组 (list/show/validate)
"""

from __future__ import annotations

import sys

import click

from . import __version__


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "--version", "-V", prog_name="javert")
def main() -> None:
    """Javert — 医保自查自纠「做不了」规则审计脚手架."""


@main.command("init")
@click.option("--refresh-data", is_flag=True, help="覆盖已存在的 data/*.csv 快照")
@click.option("--rebuild-store", is_flag=True, help="删除并重建 output/audit.sqlite")
@click.option("--upstream", default="../zadig_agent/data", show_default=True,
              help="上游 zadig_agent data 目录路径")
def init_cmd(refresh_data: bool, rebuild_store: bool, upstream: str) -> None:
    """初始化项目: snapshot + rules + sqlite + LLM 探活."""
    from .commands.init import run_init
    run_init(refresh_data=refresh_data, rebuild_store=rebuild_store, upstream=upstream)


@main.command("list")
def list_cmd() -> None:
    """列出全部规则及审计统计 (drafting/ready/validated/abandoned)."""
    from .commands.list_rules import run_list
    run_list()


@main.command("dry-run")
@click.argument("rule_id")
@click.option("--patient", required=True, help="患者住院号")
def dry_run_cmd(rule_id: str, patient: str) -> None:
    """单跑一条规则, stdout 打印完整 trace, 同时写入 store."""
    from .commands.dry_run import run_dry_run
    run_dry_run(rule_id, patient)


@main.command("run")
@click.argument("rule_id")
@click.option("--patient", default=None, help="单个患者住院号 (与 --pilot 二选一)")
@click.option("--pilot", "use_pilot", is_flag=True, help="对 data/pilot_patients.txt 全量批跑")
def run_cmd(rule_id: str, patient: str | None, use_pilot: bool) -> None:
    """批跑一条规则, 写入 store, 进度日志到 stderr."""
    if (patient is None) == (use_pilot is False):
        click.echo("必须指定 --patient <id> 或 --pilot 之一", err=True)
        sys.exit(2)
    from .commands.run import run_batch
    run_batch(rule_id, patient=patient, use_pilot=use_pilot)


@main.command("mark")
@click.argument("rule_id")
@click.option("--status", "new_status", required=True,
              type=click.Choice(["drafting", "ready", "validated", "abandoned"]),
              help="新状态")
@click.option("--force", is_flag=True, help="强制允许后退状态")
def mark_cmd(rule_id: str, new_status: str, force: bool) -> None:
    """变更规则 status (forward 自由, backward 需 --force)."""
    from .commands.mark import run_mark
    run_mark(rule_id, new_status, force=force)


@main.command("report")
@click.option("--since", default=None, help="ISO 日期 (YYYY-MM-DD), 仅统计该日期之后")
@click.option("--rule", "rule_id", default=None, help="只看单条规则")
def report_cmd(since: str | None, rule_id: str | None) -> None:
    """按规则汇总 verdict 分布 + 平均置信度 + 中位数耗时."""
    from .commands.report import run_report
    run_report(since=since, rule_id=rule_id)


@main.command("show")
@click.argument("identifier")
@click.option("--patient", default=None, help="若 identifier 为 rule_id, 必须配 --patient")
def show_cmd(identifier: str, patient: str | None) -> None:
    """重放历史 audit 的 trace (输入 run_id 或 rule_id + --patient)."""
    from .commands.show import run_show
    run_show(identifier, patient=patient)


@main.command("audit-patient")
@click.argument("patient_id")
@click.option(
    "--priority", "priority",
    type=click.Choice(["P0", "P1", "P2", "P3", "all"]),
    default="P0",
    show_default=True,
    help="按 priority 过滤规则 (与 --rules 二选一); 'all' 取全部 ready (P0+P1+P2+P3)",
)
@click.option(
    "--rules", "rules_arg",
    default=None,
    help="逗号分隔的 rule_id 列表 (例如 R045,R191), 显式选定时绕过 priority 与 abandoned 过滤",
)
@click.option(
    "--share-tool-cache/--no-share-tool-cache", "share_tool_cache",
    default=True,
    show_default=True,
    help="跨规则共享 ToolExecutor 缓存 (同患者多规则复用检索; --no-share-tool-cache 跑冷启动 baseline)",
)
@click.option(
    "--concurrency", "concurrency",
    type=click.IntRange(1, 10),
    default=1,
    show_default=True,
    help="并发跑规则数 (1=串行, 2-10 走 ThreadPoolExecutor; sglang 端实际承受 3-5)",
)
@click.option(
    "--use-router", "use_router",
    is_flag=True,
    default=False,
    help="启用 Stage A RuleRouter prefilter (14372 字典 + yaml.trigger_keywords 双闸), 把 LLM 跑的规则从 N 收缩到命中子集. 与 --rules 互斥.",
)
def audit_patient_cmd(
    patient_id: str,
    priority: str,
    rules_arg: str | None,
    share_tool_cache: bool,
    concurrency: int,
    use_router: bool,
) -> None:
    """对单患者跑一组规则 (默认 P0, 跳 abandoned), 进度→stderr, summary→stdout."""
    from .commands.audit_patient import run_audit_patient
    code = run_audit_patient(
        patient_id=patient_id,
        priority=priority,
        rules_arg=rules_arg,
        share_tool_cache=share_tool_cache,
        concurrency=concurrency,
        use_router=use_router,
    )
    sys.exit(code)


@main.command("stats")
@click.option("--batch-tag", "batch_tag", default=None,
              help="只统计该 batch_tag 的裁决 (省略=全量)")
@click.option("--min-patients", "min_patients", type=int, default=None,
              help="覆盖系统性判定的最小样本数 (默认读 configs/systemic_thresholds.yaml)")
@click.option("--min-v-rate", "min_v_rate", type=float, default=None,
              help="覆盖系统性判定的 V 率阈值 (0-1, 默认读 configs)")
def stats_cmd(batch_tag: str | None, min_patients: int | None, min_v_rate: float | None) -> None:
    """规则维度跨患者聚合 (本地 sqlite): 患者数 / V / I / V 率 / 系统性违规."""
    from .commands.stats import run_stats
    sys.exit(run_stats(batch_tag=batch_tag, min_patients=min_patients, min_v_rate=min_v_rate))


@main.command("web")
@click.option("--host", default=None, help="监听地址 (默认 config.web_host = 127.0.0.1)")
@click.option("--port", default=None, type=int, help="监听端口 (默认 config.web_port = 8090)")
@click.option("--reload", is_flag=True, help="开发模式 hot-reload")
@click.option("--with-mssql/--no-mssql", "with_mssql", default=None,
              help="启用工作台 (登录/审核/SSE); --no-mssql 时仅规则浏览, 工作台路径 503")
def web_cmd(
    host: str | None,
    port: int | None,
    reload: bool,
    with_mssql: bool | None,
) -> None:
    """启动 FastAPI Web (规则浏览 + 审核工作台 /workbench + 142 双写)."""
    try:
        import uvicorn
    except ImportError:
        click.echo("✗ uvicorn 未安装. 请: uv sync 或 pip install 'fastapi uvicorn[standard]'", err=True)
        sys.exit(2)
    import os

    from .config import get_config, reset_config_cache
    cfg = get_config()
    # session secret 默认值 fail-fast 收敛进 create_app() (单一来源, uvicorn 直起也拦截)
    final_host = host or cfg.web_host
    final_port = port or cfg.web_port
    final_reload = reload or cfg.web_reload
    # 透传 with_mssql 到 create_app (默认走 config.web_with_mssql)
    if with_mssql is not None:
        os.environ["JAVERT_WEB_WITH_MSSQL"] = "true" if with_mssql else "false"
        # get_config 已被上面调用 lru_cache 住 — 重置让 uvicorn import 时读到本次覆盖
        reset_config_cache()
    click.echo(
        f"Javert Web → http://{final_host}:{final_port}"
        f"  (reload={final_reload}, with_mssql={with_mssql if with_mssql is not None else cfg.web_with_mssql})"
    )
    uvicorn.run(
        "javert.web.api.main:app",
        host=final_host,
        port=final_port,
        reload=final_reload,
        log_level="info",
    )


@main.command("prompt-fit")
@click.argument("rule_id")
@click.option("--template", "template_id", required=True, help="模板 id, 例如 M1")
@click.option("--vars", "vars_path", default=None, help="vars 文件 (json 或 yaml)")
@click.option("--interactive", is_flag=True, help="交互式逐字段问")
@click.option("--auto", is_flag=True, help="LLM 起草 vars (Qwen) 后人审确认")
@click.option("--dry-run", "dry_run", is_flag=True, help="仅渲染不写回 yaml")
@click.option("--output", "output_path", default=None,
              help="--dry-run 时输出路径; '-' 表示 stdout (默认)")
@click.option("--save-vars", "save_vars", default=None,
              help="把本次 vars dict 落盘到该 json 文件 (interactive / auto 模式留底)")
@click.option("--force", is_flag=True,
              help="prompt_addon 被人工手改 (render_hash 不符) 时仍强制覆盖")
def prompt_fit_cmd(
    rule_id: str,
    template_id: str,
    vars_path: str | None,
    interactive: bool,
    auto: bool,
    dry_run: bool,
    output_path: str | None,
    save_vars: str | None,
    force: bool,
) -> None:
    """按模板渲染 prompt_addon 写回 rule yaml."""
    from .commands.prompt_fit import run_prompt_fit_cli
    code = run_prompt_fit_cli(
        rule_id=rule_id,
        template_id=template_id,
        vars_path=vars_path,
        interactive=interactive,
        auto=auto,
        dry_run=dry_run,
        output_path=output_path,
        save_vars=save_vars,
        force=force,
    )
    sys.exit(code)


@main.group("template")
def template_group() -> None:
    """模板管理子命令组 (list/show/validate)."""


@template_group.command("list")
def template_list_cmd() -> None:
    """列出 configs/templates/ 下所有模板."""
    from .commands.template import run_template_list
    sys.exit(run_template_list())


@template_group.command("show")
@click.argument("template_id")
def template_show_cmd(template_id: str) -> None:
    """打印单模板完整内容 (master_prompt / fields / aux templates)."""
    from .commands.template import run_template_show
    sys.exit(run_template_show(template_id))


@template_group.command("validate")
@click.argument("template_id")
def template_validate_cmd(template_id: str) -> None:
    """校验模板 schema; empty / partial / ready 三态报告."""
    from .commands.template import run_template_validate
    sys.exit(run_template_validate(template_id))


@main.command("ensure-mssql-schema")
@click.option("--drop-first", is_flag=True,
              help="先 DROP 再 CREATE (需 JAVERT_ALLOW_DROP=1, 不可逆)")
def ensure_mssql_schema_cmd(drop_first: bool) -> None:
    """142 zadig 库幂等建 4 张 javert_* 表 + 索引."""
    from .commands.ensure_schema import run_ensure_schema
    sys.exit(run_ensure_schema(drop_first=drop_first))


from .commands.mssql_user import mssql_user_group as _mssql_user_group
main.add_command(_mssql_user_group)


@main.command("sync-to-mssql")
@click.option("--dry-run", "dry_run", is_flag=True, help="仅打印 plan, 不写")
@click.option("--pending-only", "pending_only", is_flag=True,
              help="仅同步 synced_at IS NULL 的行")
@click.option("--batch-size", "batch_size", type=click.IntRange(1, 1000), default=200,
              show_default=True, help="单批大小")
def sync_to_mssql_cmd(dry_run: bool, pending_only: bool, batch_size: int) -> None:
    """sqlite audit_runs → 142 javert_audit_runs 一次性 / 增量同步."""
    from .commands.sync_to_mssql import run_sync_to_mssql
    sys.exit(run_sync_to_mssql(
        dry_run=dry_run,
        pending_only=pending_only,
        batch_size=batch_size,
    ))


if __name__ == "__main__":
    main()
