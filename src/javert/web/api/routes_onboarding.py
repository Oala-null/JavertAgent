# -*- coding: utf-8 -*-
"""onboarding 路由 — /onboarding 可视化数据接入工作室.

redesign-onboarding-demo-flow: 现场演示自动驾驶 — 上传即自动归类 + 服务端会话单一真相源
+ 流向图 + 一键载入 + 终端 jv-go 收尾. 在 add-visual-schema-onboarding 地基上加自动驾驶层.

端点:
  GET  /onboarding                       双栏页面 (流向图 + 结果卡)
  POST /api/onboarding/upload            上传文件 → 抽列 + 自动归类 + 写会话 → 全量状态
  POST /api/onboarding/delete-upload     删文件 (物理 + 会话级联清映射) → 全量状态
  GET  /api/onboarding/session           读会话态 (冷启动从 _uploads/ 重建)
  POST /api/onboarding/session           会话 mutation (映射/键模式/桥表/节点/日期/stored) → 全量状态
  POST /api/onboarding/classify          重新自动归类 (非手动文件) → 全量状态
  POST /api/onboarding/profile           点列剖析 (采样近似/全量精确)
  POST /api/onboarding/preflight         连接预检 (从会话构 mapping + 可执行诊断)
  POST /api/onboarding/date-ambiguities  扫歧义日期列 (D/M vs M/D 待确认清单)
  POST /api/onboarding/clear-output      清 data_import 产出 + .loaded.env (重导走 GUI)
  POST /api/onboarding/start             两道闸 → 落 mapping + 跑 ETL + 写诚实 .loaded.env

GUI 与 CLI 共用 onboarding/{classifier,profiler,join_preflight,etl_engine,manifest_loader}.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import yaml
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from javert.onboarding.classifier import (
    classify_columns,
    default_key_mode,
    match_fields,
)
from javert.onboarding.etl_engine import PROJECT_ROOT, run_etl
from javert.onboarding.join_preflight import preflight_keys, preflight_time_windows
from javert.onboarding.manifest_loader import Manifest, load_manifest
from javert.onboarding.profiler import detect_date_format, profile_file_column, sample_column
from javert.web.onboarding_session import get_state
from javert.web.templating import render

logger = logging.getLogger("javert.web.api.routes_onboarding")

router = APIRouter(tags=["onboarding"])

DATA_IMPORT_DIR = PROJECT_ROOT / "data_import"   # ETL 产出目录 (可 monkeypatch)
UPLOAD_DIR = DATA_IMPORT_DIR / "_uploads"
ALIAS_PATH = PROJECT_ROOT / "configs" / "field_alias.yaml"


def _out_dir() -> Path:
    """ETL 产出目录 (读模块全局, 供测试 monkeypatch)."""
    return DATA_IMPORT_DIR
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.一-鿿-]")
MAX_UPLOAD_BYTES = 2 << 30  # 2 GB 上限 (容纳 ~505MB 真实文书表, 挡 DoS)

# spoke → .loaded.env 环境变量名 (产出哪张表才写哪条, 设计 D5 诚实化).
# fees/notes 默认文件名已是产出名 (shi_fee.csv/case_notes.csv), 仍显式写防默认漂移;
# 诊断/手术/化验/检查默认是 .xls/旧名, 产出 csv 时 MUST 覆盖, 否则 loader 找不到文件.
_SPOKE_ENV = {
    "fees": "JAVERT_FEES_FILE",
    "notes": "JAVERT_NOTES_FILE",
    "diagnoses": "JAVERT_ZD_FILE",
    "surgeries": "JAVERT_SS_FILE",
    "labs": "JAVERT_LABS_FILE",
    "examinations": "JAVERT_EXAMINATIONS_FILE",
}

# clear-output 清掉的 data_import 产出 (不动 _uploads — 那是接入输入, 可重导)
_OUTPUT_FILES = [
    "shi_fee.csv", "case_notes.csv", "shi_zd.csv", "shi_ss.csv",
    "lab_results.csv", "examinations.csv",
    ".loaded.env", "column_mapping.generated.yaml", "_stored_spokes.json",
]


def _safe_filename(name: str) -> str:
    base = Path(name).name
    return _SAFE_NAME.sub("_", base) or "upload.csv"


def _upload_path(safe: str) -> Path:
    """同名上传替换 logical 文件 (设计 D4: 不再生 foo_1.csv 幽灵文件)."""
    return UPLOAD_DIR / safe


class UnsafePath(ValueError):
    """客户端给的文件路径逃逸出项目根 (防越权读任意文件)."""


# 客户端给的 file 只允许落在这些数据目录内 (上传 / 既有数据 / ETL 产出);
# 绝不允许读 .env (会泄露 JAVERT_SESSION_SECRET → 伪造会话绕过鉴权) / configs / src / sqlite.
def _allowed_roots() -> list[Path]:
    return [
        UPLOAD_DIR.resolve(),
        (PROJECT_ROOT / "data").resolve(),
        (PROJECT_ROOT / "data_import").resolve(),
    ]


def _safe_path(file: str) -> Path:
    """把客户端给的相对路径解析为允许目录内的绝对路径; 逃逸或越白名单 → 拒绝.

    GUI 端点收的 file 来自前端 (上传 relpath 或 mapping 源路径), 必须夹在数据白名单内.
    仅夹在 PROJECT_ROOT 不够 — 已登录用户能借此读 .env(session secret)/configs/审计库.
    CLI 不走此路 (本地操作者可信).
    """
    p = (PROJECT_ROOT / file).resolve()
    if not any(p == r or p.is_relative_to(r) for r in _allowed_roots()):
        raise UnsafePath(f"路径越权 (仅允许 data/ data_import/ _uploads/): {file}")
    return p


def _validate_mapping_paths(mapping: dict) -> None:
    """校验 mapping 里所有源文件 / 桥表路径都在项目根内 (防越权)."""
    for key, section in mapping.items():
        if not isinstance(section, dict):
            continue
        if section.get("file"):
            _safe_path(section["file"])
        bridge = section.get("bridge")
        if isinstance(bridge, dict) and bridge.get("file"):
            _safe_path(bridge["file"])


def _sniff_sep(path: Path) -> str | None:
    """.txt 自动嗅探分隔符 (tab/逗号/分号/竖线), 防制表符 TXT 塌成单列; .csv 用逗号."""
    if path.suffix.lower() != ".txt":
        return ","
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            head = f.readline()
        cand = {"\t": head.count("\t"), ",": head.count(","),
                ";": head.count(";"), "|": head.count("|")}
        best = max(cand, key=cand.get)
        return best if cand[best] > 0 else None  # None → pandas python 引擎自动嗅探
    except Exception:  # noqa: BLE001
        return None


_ROWCOUNT_WINDOW = 2 << 20  # 读前 2MB 用 csv 数逻辑行 → 按 filesize 缩放 (秒回, 不全扫)


def _estimate_csv_rows(path: Path) -> tuple[int, bool]:
    """估算 csv/txt 逻辑行数. 用 csv 解析前 2MB 数逻辑行 (正确处理引号内换行,
    避免文书内容多行字段把物理行数算爆), 再按文件大小缩放. 返回 (n, exact)."""
    import csv
    import io
    size = path.stat().st_size
    try:
        with open(path, "rb") as f:
            chunk = f.read(min(size, _ROWCOUNT_WINDOW))
    except Exception:  # noqa: BLE001
        return 0, False
    whole = len(chunk) >= size
    text = chunk.decode("utf-8", errors="ignore")
    if not whole:
        # 丢掉末尾不完整行, 减少跨窗口截断误差
        cut = text.rfind("\n")
        if cut > 0:
            text = text[:cut]
    try:
        rows = sum(1 for _ in csv.reader(io.StringIO(text)))
    except Exception:  # noqa: BLE001
        rows = text.count("\n") + 1
    n = max(0, rows - 1)  # 减表头 (窗口含文件起始)
    if whole:
        return n, True
    est = int(n * (size / max(1, len(text.encode("utf-8")))))
    return max(est, n), False


def _estimate_xlsx_rows(path: Path) -> tuple[int, bool]:
    """openpyxl read_only 取 max_row — 不载全表."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb.active
        n = (ws.max_row or 0)
        wb.close()
        return max(0, n - 1), True  # 减表头
    except Exception:  # noqa: BLE001
        return 0, False


def _read_columns(path: Path, sample_rows: int = 8) -> tuple[list[str], list[dict], int, bool]:
    """秒回: 只读表头 + 前 N 行采样, 行数走字节估算 (不全扫). 返回 (cols, sample, n, exact).

    数值分布/精确行数走按需的 /api/onboarding/profile (点列才算).
    """
    if path.suffix.lower() in (".xls", ".xlsx"):
        df = pd.read_excel(path, dtype=str, nrows=sample_rows)
        cols = list(df.columns)
        n, exact = _estimate_xlsx_rows(path)
    else:
        sep = _sniff_sep(path)
        kw = {"sep": sep, "engine": "python"} if sep is None else {"sep": sep, "low_memory": False}
        df = pd.read_csv(path, dtype=str, nrows=sample_rows, **kw)
        cols = list(df.columns)
        n, exact = _estimate_csv_rows(path)
    sample = df.fillna("").head(sample_rows).to_dict("records")
    return [str(c) for c in cols], sample, n, exact


def manifest_view(manifest: Manifest) -> list[dict]:
    """manifest → 前端渲染数据 (展示有处理路径的 spoke; tabular 进流向图可映射)."""
    out: list[dict] = []
    for key, spoke in manifest.spokes.items():
        out.append({
            "key": key,
            "name": spoke.name,
            "status": spoke.status,
            "tool": spoke.tool,
            "is_tabular": spoke.is_tabular,
            "id_form": spoke.id_form,
            "join_key": spoke.join_key,
            "default_key_mode": default_key_mode(spoke) if spoke.is_tabular else None,
            "via_bridge": spoke.via_bridge.model_dump() if spoke.via_bridge else None,
            "fields": [
                {"key": f.key, "name": f.name, "required": f.required, "is_date": f.is_date}
                for f in spoke.fields
            ],
        })
    return out


def _load_alias() -> dict:
    if ALIAS_PATH.exists():
        with open(ALIAS_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _reset_workbench_caches() -> None:
    """重置工作台渲染缓存 (病案概览 zd/ss/fees + loader 叠加层), 使 data_import 变更立即生效.

    onboarding 与工作台同进程, 载入/清空后调一次即可, 无需重启 web. 工作台未挂载时静默跳过.
    """
    try:
        from javert.web import patient_overview
        patient_overview.reset_caches()
    except Exception:  # noqa: BLE001
        pass
    try:
        from javert.web.api import routes_workbench
        routes_workbench.reset_loader()
    except Exception:  # noqa: BLE001
        pass


# ────────────── 自动归类 + 自动映射 (设计 D2) ──────────────

def _apply_file_to_spoke(st, rel: str, spoke_key: str, manifest: Manifest, alias: dict) -> None:
    """把文件归到某 spoke + 自动预填命中字段; 清掉该文件在其它 spoke 的旧自动映射."""
    info = st.files.get(rel)
    if not info:
        return
    sp = manifest.spoke(spoke_key)
    matched = match_fields(sp, info["columns"], alias)
    # 该文件之前自动绑到别的 spoke → 清掉 (改绑唯一目标表)
    for other, m in st.map.items():
        if other == spoke_key:
            continue
        fields = m.get("fields", {})
        for fk in [k for k, v in list(fields.items())
                   if v.get("file") == rel and v.get("auto")]:
            del fields[fk]
    st.apply_auto_map(spoke_key, rel, matched, key_mode=default_key_mode(sp))
    _update_patient_count(st, rel)
    cands = (st.classify.get(rel) or {}).get("candidates", [])
    st.set_classification(rel, {"spoke": spoke_key, "ambiguous": False,
                                "candidates": cands, "reason": "", "manual": True})


def _update_patient_count(st, rel: str) -> None:
    """估算该文件映射的患者数 (采样 patient_id 列唯一值) → 写回 files 供结果卡展示."""
    info = st.files.get(rel)
    if not info:
        return
    pcol = None
    for m in st.map.values():
        f = m.get("fields", {}).get("patient_id")
        if f and f.get("file") == rel and f.get("col"):
            pcol = f["col"]
            break
    if not pcol:
        info.pop("patient_count", None)
        return
    try:
        vals = sample_column(_safe_path(rel), pcol)
        uniq = {str(v).strip() for v in vals
                if str(v).strip() and str(v).strip().lower() != "nan"}
        info["patient_count"] = len(uniq)
        info["patient_count_exact"] = bool(info.get("n_rows_exact")) and info.get("n_rows", 0) <= 5000
    except Exception:  # noqa: BLE001
        info.pop("patient_count", None)


def _classify_and_apply(st, rel: str, manifest: Manifest, alias: dict) -> None:
    """对一个文件自动归类 + (非歧义时) 自动映射."""
    info = st.files.get(rel)
    if not info:
        return
    res = classify_columns(info["columns"], manifest, alias)
    st.set_classification(rel, res.to_dict())
    if not res.ambiguous and res.spoke:
        # apply 命中字段 (不带 manual 标记 — 自动归类)
        sp = manifest.spoke(res.spoke)
        for other, m in st.map.items():
            if other == res.spoke:
                continue
            fields = m.get("fields", {})
            for fk in [k for k, v in list(fields.items())
                       if v.get("file") == rel and v.get("auto")]:
                del fields[fk]
        st.apply_auto_map(res.spoke, rel, res.matched, key_mode=default_key_mode(sp))
        _update_patient_count(st, rel)


# ────────────── 会话 → mapping ──────────────

def _build_mapping_from_state(st) -> dict:
    """会话映射态 → run_etl/preflight 用的 mapping dict (与旧前端 buildMapping 同形)."""
    mapping: dict = {
        "hospital_code": st.hospital_code or "ext",
        "normalize_dates": bool(st.normalize_dates),
    }
    if st.date_decisions:
        mapping["date_decisions"] = dict(st.date_decisions)
    for spoke, m in st.map.items():
        fields = m.get("fields", {})
        if not fields:
            continue
        if "patient_id" in fields:
            file = fields["patient_id"]["file"]
        else:
            file = next(iter(fields.values()))["file"]
        columns = {k: v["col"] for k, v in fields.items()}
        section = {"file": file, "key_mode": m.get("key_mode", "synth"), "columns": columns}
        bridge = m.get("bridge") or {}
        if m.get("key_mode") == "bridge" and bridge.get("file"):
            section["bridge"] = {"file": bridge["file"],
                                 "source_col": bridge.get("src", ""),
                                 "canonical_col": bridge.get("canon", "")}
        mapping[spoke] = section
    return mapping


# ────────────── 页面 ──────────────

@router.get("/onboarding", response_class=HTMLResponse)
def onboarding_page(request: Request):
    from javert.web.auth import current_user
    manifest = load_manifest()
    return HTMLResponse(render(
        "onboarding.html",
        title="Javert · 数据接入工作室",
        current_user=current_user(request),
        spokes=manifest_view(manifest),
    ))


# ────────────── 会话读写 (设计 D4 单一真相源) ──────────────

@router.get("/api/onboarding/session")
def onboarding_session_get(request: Request):
    """读会话态. 冷启动 (进程重启) 从 _uploads/ 现存文件重建自动映射 (设计 D4)."""
    manifest = load_manifest()
    alias = _load_alias()
    st = get_state(request, manifest)
    if not st.files and UPLOAD_DIR.exists():
        for p in sorted(UPLOAD_DIR.glob("*")):
            if not p.is_file():
                continue
            try:
                cols, sample, n, exact = _read_columns(p)
            except Exception:  # noqa: BLE001
                continue
            rel = str(p.relative_to(PROJECT_ROOT))
            st.add_file(rel, {"filename": p.name, "columns": cols, "n_rows": n,
                              "n_rows_exact": exact, "sample": sample})
            _classify_and_apply(st, rel, manifest, alias)
    return {"ok": True, "state": st.to_dict()}


@router.post("/api/onboarding/session")
async def onboarding_session_post(request: Request):
    """会话 mutation: 每次更新服务端态并返回全量状态 (前端整体重渲染)."""
    body = await request.json()
    action = body.get("action")
    manifest = load_manifest()
    alias = _load_alias()
    st = get_state(request, manifest)

    if action == "set_mapping":
        st.set_mapping(body["spoke"], body["field"], body.get("file"),
                       body.get("col"), body.get("auto", False))
        # 同文件其余字段顺手补齐 (手动绑一列后)
        file = body.get("file")
        if file and body.get("col"):
            sp = manifest.spoke(body["spoke"])
            info = st.files.get(file)
            if info:
                st.apply_auto_map(body["spoke"], file,
                                  match_fields(sp, info["columns"], alias))
    elif action == "clear_spoke":
        st.clear_spoke(body["spoke"])
    elif action == "reset_mappings":
        st.reset_mappings()
    elif action == "set_key_mode":
        st.set_key_mode(body["spoke"], body["mode"])
    elif action == "set_bridge":
        st.set_bridge(body["spoke"], body.get("bridge", {}))
    elif action == "set_meta":
        st.set_meta(body.get("hospital_code"), body.get("normalize_dates"),
                    body.get("batch_tag"), body.get("sync_142"))
    elif action == "set_node_position":
        st.set_node_position(str(body["node_id"]),
                             float(body.get("x", 0)), float(body.get("y", 0)))
    elif action == "set_date_decisions":
        st.set_date_decisions(body.get("decisions", {}))
    elif action == "reclassify_file":
        rel, spoke = body.get("file"), body.get("spoke")
        if rel in st.files and spoke in manifest.tabular_spokes():
            _apply_file_to_spoke(st, rel, spoke, manifest, alias)
        else:
            return JSONResponse(status_code=400, content={"ok": False, "error": "文件或表无效"})
    elif action == "add_stored":
        rel = body.get("file")
        if rel not in st.files:
            return JSONResponse(status_code=400, content={"ok": False, "error": "文件未上传"})
        st.add_stored({"name": body.get("name", "").strip(), "file": rel,
                       "patient_col": body.get("patient_col", "").strip()})
    else:
        return JSONResponse(status_code=400, content={"ok": False, "error": f"未知 action: {action}"})
    return {"ok": True, "state": st.to_dict()}


# ────────────── 上传抽列 + 自动归类 ──────────────

@router.post("/api/onboarding/upload")
async def onboarding_upload(request: Request, file: UploadFile = File(...)):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(file.filename or "upload.csv")
    dst = _upload_path(safe)
    # 流式落盘 + 大小上限 (防 await file.read() 全量入内存 OOM / 磁盘耗尽 DoS)
    written = 0
    try:
        with open(dst, "wb") as fh:
            while True:
                chunk = await file.read(1 << 20)  # 1 MB
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    fh.close()
                    dst.unlink(missing_ok=True)
                    return JSONResponse(status_code=413, content={
                        "ok": False, "error": f"文件超过上限 {MAX_UPLOAD_BYTES // (1 << 30)} GB"})
                fh.write(chunk)
    except Exception as e:  # noqa: BLE001
        dst.unlink(missing_ok=True)
        return JSONResponse(status_code=400, content={"ok": False, "error": f"写入失败: {e}"})
    try:
        cols, sample, n, exact = _read_columns(dst)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "error": f"解析失败: {e}"})

    manifest = load_manifest()
    alias = _load_alias()
    st = get_state(request, manifest)
    rel = str(dst.relative_to(PROJECT_ROOT))
    if rel in st.files:
        st.remove_file(rel)  # 同名替换: 先清旧映射再重建 (设计 D4)
    st.add_file(rel, {"filename": dst.name, "columns": cols, "n_rows": n,
                      "n_rows_exact": exact, "sample": sample})
    _classify_and_apply(st, rel, manifest, alias)
    return {"ok": True, "file": rel, "filename": dst.name, "state": st.to_dict()}


@router.post("/api/onboarding/delete-upload")
async def onboarding_delete_upload(request: Request):
    """删一个上传文件: 物理删 (仅限 _uploads) + 会话级联清映射 → 全量状态 (设计 D4)."""
    body = await request.json()
    file = body.get("file")
    if not file:
        return JSONResponse(status_code=400, content={"ok": False, "error": "缺 file"})
    p = (PROJECT_ROOT / file).resolve()
    if not p.is_relative_to(UPLOAD_DIR.resolve()):
        return JSONResponse(status_code=400, content={
            "ok": False, "error": "只能删除 _uploads 内的上传文件"})
    try:
        p.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "error": f"删除失败: {e}"})
    manifest = load_manifest()
    st = get_state(request, manifest)
    st.remove_file(file)
    return {"ok": True, "file": file, "state": st.to_dict()}


# ────────────── 重新归类 ──────────────

@router.post("/api/onboarding/classify")
async def onboarding_classify(request: Request):
    """重新自动归类所有非手动文件 (alias 改后或手动触发) → 全量状态 (设计 D2)."""
    manifest = load_manifest()
    alias = _load_alias()
    st = get_state(request, manifest)
    for rel in list(st.files):
        if (st.classify.get(rel) or {}).get("manual"):
            continue  # 手动定表的不动
        _classify_and_apply(st, rel, manifest, alias)
    return {"ok": True, "state": st.to_dict()}


# ────────────── 列剖析 ──────────────

@router.post("/api/onboarding/profile")
async def onboarding_profile(request: Request):
    body = await request.json()
    file = body.get("file")
    col = body.get("column")
    mode = body.get("mode", "sample")
    as_key = bool(body.get("as_key", False))
    if not file or not col:
        return JSONResponse(status_code=400, content={"ok": False, "error": "缺 file/column"})
    try:
        path = _safe_path(file)
    except UnsafePath as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
    if not path.exists():
        return JSONResponse(status_code=404, content={"ok": False, "error": "文件不存在"})
    try:
        prof = profile_file_column(path, col, mode=mode, as_key=as_key)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "error": f"剖析失败: {e}"})
    d = asdict(prof)
    if prof.date is not None:
        d["date"] = asdict(prof.date)
        d["date"]["can_time_window"] = prof.date.can_time_window
    return {"ok": True, "profile": d}


# ────────────── 连接预检 + 可执行诊断 (设计 D7) ──────────────

def _preflight_diagnostics(pf, names: dict) -> dict | None:
    """把 KeyPreflight.per_spoke 打包为可执行诊断 (路由层, 不改 join_preflight 签名)."""
    if pf is None or not pf.per_spoke:
        return None
    lowest = min(pf.per_spoke, key=lambda k: pf.per_spoke[k])
    rate = pf.per_spoke[lowest]
    nm = names.get(lowest, lowest)
    if rate <= 0:
        msg = f"{nm} 命中 0% — 大概率患者键列映射错或缺桥表"
    elif rate < 0.5:
        msg = f"{nm} 命中仅 {rate*100:.0f}% — 检查患者键列映射 / 是否需经桥表归一"
    else:
        msg = f"{nm} 命中最低 ({rate*100:.0f}%), 其余表更高"
    return {"lowest_spoke": lowest, "lowest_name": nm,
            "lowest_rate": rate, "message": msg, "per_spoke": pf.per_spoke}


def _preflight_payload(mapping: dict) -> dict:
    _validate_mapping_paths(mapping)
    manifest = load_manifest()
    result = run_etl(mapping, manifest)
    if not result.ok:
        return {"ok": False, "errors": result.fatal_errors}
    spoke_meta = {s.key: manifest.spoke(s.key) for s in result.spokes}
    names = {s.key: s.name for s in result.spokes}
    pf = preflight_keys(result.spokes, spoke_meta)
    tw = preflight_time_windows(result.spokes, spoke_meta)
    spokes = [{
        "key": s.key, "name": s.name, "rows": len(s.df),
        "patients": s.patient_count, "bridge_miss": s.bridge_miss,
        "date_notes": s.date_notes, "warnings": s.warnings,
    } for s in result.spokes]
    payload = {"ok": True, "spokes": spokes, "time_windows": tw}
    if pf is not None:
        payload["preflight"] = {
            "coverage": pf.coverage, "verdict": pf.verdict, "emoji": pf.emoji,
            "label": pf.label, "per_spoke": pf.per_spoke, "total": pf.total,
            "hit": pf.hit, "misses": pf.misses, "primary": pf.primary,
            "diagnostics": _preflight_diagnostics(pf, names),
        }
    return payload


@router.post("/api/onboarding/preflight")
async def onboarding_preflight(request: Request):
    body = await request.json()
    manifest = load_manifest()
    st = get_state(request, manifest)
    # 优先用会话态构 mapping (单一真相源); 兼容显式 mapping (旧客户端/测试)
    mapping = body.get("mapping") or _build_mapping_from_state(st)
    try:
        payload = _preflight_payload(mapping)
    except Exception as e:  # noqa: BLE001
        logger.warning("preflight 失败: %s", e)
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
    st.set_preflight(payload.get("preflight"))
    payload["state"] = st.to_dict()
    return payload


# ────────────── 日期歧义扫描 (设计 D8) ──────────────

def _date_sample_compare(vals: list[str], k: int = 5) -> list[dict]:
    """取几个原始值, 给 D/M 与 M/D 两种解析对比 (供 modal 让用户选)."""
    import warnings
    samples = [v for v in vals if str(v).strip()][:k]
    out: list[dict] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for v in samples:
            dm = pd.to_datetime(v, dayfirst=True, errors="coerce")
            md = pd.to_datetime(v, dayfirst=False, errors="coerce")
            out.append({
                "raw": v,
                "dm": dm.strftime("%Y-%m-%d") if pd.notna(dm) else "?",
                "md": md.strftime("%Y-%m-%d") if pd.notna(md) else "?",
            })
    return out


@router.post("/api/onboarding/date-ambiguities")
async def onboarding_date_ambiguities(request: Request):
    """扫已映射的日期列, 列出 dayfirst 无法判定的歧义列供一次性确认 (设计 D8)."""
    manifest = load_manifest()
    st = get_state(request, manifest)
    out: list[dict] = []
    for spoke, m in st.map.items():
        if spoke not in manifest.tabular_spokes():
            continue
        sp = manifest.spoke(spoke)
        fields = m.get("fields", {})
        for f in sp.fields:
            if not f.is_date:
                continue
            fm = fields.get(f.key)
            if not fm or not fm.get("col"):
                continue
            try:
                path = _safe_path(fm["file"])
                vals = sample_column(path, fm["col"])
            except Exception:  # noqa: BLE001
                continue
            prof = detect_date_format(vals)
            if not prof.is_ambiguous_dayfirst:
                continue
            out_col = f.targets[0] if f.targets else f.key
            decision_key = f"{spoke}.{out_col}"
            out.append({
                "spoke": spoke, "field": f.key, "name": f"{sp.name}·{f.name}",
                "file": st.files.get(fm["file"], {}).get("filename", fm["file"]),
                "decision_key": decision_key,
                "decided": decision_key in st.date_decisions,
                "samples": _date_sample_compare(vals),
            })
    return {"ok": True, "ambiguities": out, "state": st.to_dict()}


# ────────────── 清空已载入 (设计 D5) ──────────────

@router.post("/api/onboarding/clear-output")
async def onboarding_clear_output(request: Request):
    """清空全部回到空白: data_import 产出 CSV + .loaded.env + 上传文件 + 会话态 (页面变空白)."""
    out_dir = _out_dir()
    removed: list[str] = []
    for fn in _OUTPUT_FILES:
        p = out_dir / fn
        if p.exists():
            p.unlink(missing_ok=True)
            removed.append(fn)
    for p in out_dir.glob("stored_*.csv"):
        p.unlink(missing_ok=True)
        removed.append(p.name)
    # 删上传文件 (仅限 _uploads), 让页面与磁盘都回空白
    up_dir = out_dir / "_uploads"
    if up_dir.exists():
        for p in up_dir.glob("*"):
            if p.is_file():
                p.unlink(missing_ok=True)
                removed.append(p.name)
    manifest = load_manifest()
    st = get_state(request, manifest)
    st.reset_data(manifest)  # 会话回空白 (文件/映射/归类/预检全清)
    _reset_workbench_caches()  # coexist: 清空 data_import 后工作台回到只显 shi
    return {"ok": True, "removed": removed, "state": st.to_dict()}


# ────────────── 一键落映射 + 跑 ETL ──────────────

def _persist_stored(stored: list, out_dir: Path) -> list[dict]:
    """声明的新表 (stored spoke) — 原样存下 + 记录, 绝不静默丢 (设计 D2)."""
    import json
    saved: list[dict] = []
    for entry in stored or []:
        name = entry.get("name")
        file = entry.get("file")
        pcol = entry.get("patient_col")
        if not (name and file):
            continue
        try:
            src = _safe_path(file)
        except UnsafePath:
            continue
        if not src.exists():
            continue
        df = pd.read_csv(src, dtype=str, low_memory=False) if src.suffix.lower() == ".csv" \
            else pd.read_excel(src, dtype=str)
        safe = _safe_filename(name)
        dst = out_dir / f"stored_{safe}.csv"
        df.to_csv(dst, index=False, encoding="utf-8")
        saved.append({"name": name, "file": dst.name, "rows": len(df), "patient_col": pcol,
                      "status": "stored", "note": "已接收·暂不参与判定"})
    if saved:
        (out_dir / "_stored_spokes.json").write_text(
            json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    return saved


def _build_loaded_env(rel_dir: str, produced_spokes: list[str], spoke_files: dict,
                      batch_tag: str = "", sync_142: bool = True) -> str:
    """诚实 .loaded.env: 只导出本次实际产出表对应的 JAVERT_*_FILE (设计 D5).

    缺表客户不再拿到指向不存在文件的 env (旧 bug: 硬写 ZD/SS).
    batch_tag 非空 → 写 JAVERT_BATCH_TAG, 使 jv-go/jv-run-all 跑出的裁决都带该批次标签.
    sync_142=True (默认) → JAVERT_SQL_ENABLED=true: 裁决双写 142 + 上工作台前端;
    False → 本地 sqlite-only (排练用, 不污染工作台).
    """
    lines = [
        "# 由 /onboarding 一键载入生成 — jv-* 命令 source 本文件",
        f"export JAVERT_DATA_DIR={rel_dir}",
    ]
    for key in produced_spokes:
        env = _SPOKE_ENV.get(key)
        fn = spoke_files.get(key)
        if env and fn:
            lines.append(f"export {env}={fn}")
    if batch_tag:
        lines.append(f"export JAVERT_BATCH_TAG={batch_tag}")
    lines.append(f"export JAVERT_SQL_ENABLED={'true' if sync_142 else 'false'}")
    return "\n".join(lines) + "\n"


@router.post("/api/onboarding/start")
async def onboarding_start(request: Request):
    manifest = load_manifest()
    st = get_state(request, manifest)
    body = await request.json()
    mapping = body.get("mapping") or _build_mapping_from_state(st)
    stored = body.get("stored") or st.stored
    try:
        _validate_mapping_paths(mapping)
    except UnsafePath as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})

    # 闸 1: 必填 + 闸 2: 连接预检 (run_etl 内部校验必填; preflight red 挡住).
    # 点"载入数据"始终在此重算预检 → 天然处理 stale (设计 D7).
    result = run_etl(mapping, manifest)
    if not result.ok:
        return JSONResponse(status_code=400, content={
            "ok": False, "gate": "required", "errors": result.fatal_errors})
    spoke_meta = {s.key: manifest.spoke(s.key) for s in result.spokes}
    names = {s.key: s.name for s in result.spokes}
    pf = preflight_keys(result.spokes, spoke_meta)
    st.set_preflight(_preflight_payload_pf(pf, names))
    if pf is not None and pf.verdict == "red":
        return JSONResponse(status_code=400, content={
            "ok": False, "gate": "preflight",
            "error": f"连接预检 🔴 覆盖率 {pf.coverage*100:.0f}% — 大概率列映射错, 已挡住",
            "preflight": {"coverage": pf.coverage, "verdict": "red", "per_spoke": pf.per_spoke,
                          "diagnostics": _preflight_diagnostics(pf, names)}})

    # 落 mapping + 写盘
    out_dir = _out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    gen_mapping = out_dir / "column_mapping.generated.yaml"
    gen_mapping.write_text(
        yaml.safe_dump(mapping, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tables = []
    produced: list[str] = []
    spoke_files: dict[str, str] = {}
    for s in result.spokes:
        if s.output_file:
            s.df.to_csv(out_dir / s.output_file, index=False, encoding="utf-8")
            tables.append({"file": s.output_file, "rows": len(s.df),
                           "patients": s.patient_count, "bridge_miss": s.bridge_miss})
            produced.append(s.key)
            spoke_files[s.key] = s.output_file

    stored_saved = _persist_stored(stored, out_dir)

    # 已载入患者 (跨表都解析得出的 canonical 裸号 → 真正可审核的患者集)
    from javert.onboarding.join_preflight import extract_bare_ids, id_hits
    patient_ids: list[str] = []
    fees_sr = next((s for s in result.spokes if s.key == "fees"), None)
    if fees_sr is not None:
        cand = extract_bare_ids(fees_sr.df, manifest.spoke("fees"))
        others = [s for s in result.spokes if s.key != "fees"]
        for pid in cand:
            if all(id_hits(s.df, manifest.spoke(s.key), pid) for s in others):
                patient_ids.append(pid)
    elif result.spokes:
        patient_ids = extract_bare_ids(result.spokes[0].df, manifest.spoke(result.spokes[0].key))

    # 写诚实 .loaded.env — 只含实际产出表 (设计 D5)
    rel_dir = str(out_dir.relative_to(PROJECT_ROOT)) if out_dir.is_relative_to(PROJECT_ROOT) else str(out_dir)
    (out_dir / ".loaded.env").write_text(
        _build_loaded_env(rel_dir, produced, spoke_files, st.batch_tag, st.sync_142),
        encoding="utf-8")
    sample = patient_ids[0] if patient_ids else "<患者号>"

    # coexist: 重置工作台渲染缓存, 让新接入病人立刻能在工作台详情页渲染 (不必重启 web)
    _reset_workbench_caches()

    return {
        "ok": True,
        "output_dir": rel_dir,
        "mapping_path": str(gen_mapping.relative_to(PROJECT_ROOT)) if gen_mapping.is_relative_to(PROJECT_ROOT) else str(gen_mapping),
        "tables": tables,
        "stored": stored_saved,
        "patients_total": len(patient_ids),
        "patient_ids": patient_ids[:50],
        "sample_patient": sample,
        "run_cmd": "jv-go",
        "preflight": ({"coverage": pf.coverage, "verdict": pf.verdict,
                       "emoji": pf.emoji, "label": pf.label} if pf else None),
        "next_steps": [
            "cd ~/26er/Javert && source data_import/.loaded.env",
            "uv run python scripts/jv_run_all.sh   # 或 jv-run <患者号> 跑单个",
        ],
    }


def _preflight_payload_pf(pf, names: dict) -> dict | None:
    """KeyPreflight → 会话存档用 dict (含 diagnostics)."""
    if pf is None:
        return None
    return {
        "coverage": pf.coverage, "verdict": pf.verdict, "emoji": pf.emoji,
        "label": pf.label, "per_spoke": pf.per_spoke, "total": pf.total,
        "hit": pf.hit, "misses": pf.misses, "primary": pf.primary,
        "diagnostics": _preflight_diagnostics(pf, names),
    }
