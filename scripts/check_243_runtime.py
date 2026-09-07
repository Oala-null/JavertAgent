#!/usr/bin/env python3
"""只读检查SQL源/既有结果表/LLM模型，绝不打印密码或执行DDL。"""
import argparse
from contextlib import closing
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from javert.config import get_config
from javert.data.hub_source import connect
from javert.store.sqlserver_store import SqlServerStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", action="store_true", help="Web启动只检查SQL，不请求LLM")
    args = parser.parse_args()
    cfg = get_config()
    if cfg.hub_linkage_mode != "shanghai" or not cfg.hub_hospital_code:
        raise SystemExit("CONFIG_LINKAGE_NOT_ENABLED")
    if args.web and (len(cfg.session_secret) < 32 or cfg.session_secret.startswith("javert-dev-change-me")):
        raise SystemExit("CONFIG_SESSION_SECRET_MISSING：沿用原Web有效环境，不要重新生成覆盖现有secret")
    if not cfg.sql_password:
        raise SystemExit("CONFIG_SQL_PASSWORD_MISSING：沿用原环境或项目.env配置")
    print(f"SQL源：{cfg.sql_host}:{cfg.sql_port}/{cfg.hub_database}")
    print(f"结果库：{cfg.sql_database}；SQL双写：{cfg.sql_enabled}；密码：已配置")
    with closing(connect(cfg, timeout=10)) as cn:
        cn.timeout = 30
        cn.cursor().execute("SELECT 1").fetchone()
    print("SQL源连接：通过")
    if cfg.sql_enabled:
        store = SqlServerStore(config=cfg)
        try:
            if not store.init_schema():
                raise SystemExit("RESULT_SCHEMA_CHECK_FAILED：未执行DDL，请检查已有结果库/表")
        finally:
            store.dispose()
        print("既有结果表：通过（只读，未执行DDL）")
    else:
        print("WARNING SQL_DISABLED：本次只写SQLite，不会自动出现在SQL工作台")
    if not args.web:
        import httpx
        with httpx.Client(timeout=15, trust_env=False) as client:
            response = client.get(cfg.llm_endpoint.rstrip("/") + "/models")
            response.raise_for_status()
            models = {row["id"] for row in response.json().get("data", [])}
        if cfg.llm_model not in models:
            raise SystemExit("LLM_MODEL_NOT_SERVED：现场llm_model必须与本机/models返回的模型别名一致")
        print("LLM连接和模型别名：通过（未启动推理）")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"运行环境检查失败：{type(exc).__name__}；未输出凭据或连接串")
