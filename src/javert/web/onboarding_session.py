# -*- coding: utf-8 -*-
"""onboarding 进程内会话状态 — 接入过程单一真相源 (设计 D4).

刻意不落盘 JSON: 落盘会引入原子写/并发同步/磁盘漂移等新复杂度与 bug 面;
演示进程生命周期内的内存态已足够, 进程重启 (演示期不会发生) 从 _uploads/ 重建兜底.

按 starlette session cookie 里的 onb_sid 索引 _SESSIONS 进程字典. 每次 mutation
更新服务端态并返回全量状态 (to_dict), 前端整体重渲染 — 消灭「增删出问题」整类 bug.

state 形状: {files, map, classify, stored, date_decisions, node_positions, meta, preflight}
  files            rel -> {filename, columns[], n_rows, n_rows_exact, sample[]}
  map              spoke -> {key_mode, bridge:{file,src,canon}, fields:{field:{file,col,auto}}}
  classify         rel -> {spoke, ambiguous, candidates[], reason}
  stored           [{name, file, patient_col}]
  date_decisions   "spoke.col" -> dayfirst(bool)   (用户对歧义日期列的确认)
  node_positions   node_id -> {x, y}               (流向图节点拖拽位置)
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request

from javert.onboarding.classifier import default_key_mode
from javert.onboarding.manifest_loader import Manifest

# 进程内会话表 (按 onb_sid 索引). 不落盘 — 进程重启即清, 冷启动从 _uploads/ 重建.
_SESSIONS: dict[str, "OnbState"] = {}
_LOCK = threading.RLock()


@dataclass
class OnbState:
    """一个浏览器会话的接入过程态. 所有字段均可 JSON 序列化回前端."""

    files: dict[str, Any] = field(default_factory=dict)
    map: dict[str, Any] = field(default_factory=dict)
    classify: dict[str, Any] = field(default_factory=dict)
    stored: list[dict] = field(default_factory=list)
    date_decisions: dict[str, bool] = field(default_factory=dict)
    node_positions: dict[str, Any] = field(default_factory=dict)
    hospital_code: str = "ext"
    batch_tag: str = ""        # v2.1 式批次标签 → 写进 .loaded.env (JAVERT_BATCH_TAG)
    sync_142: bool = True      # 默认同步到工作台/142 (.loaded.env 写 JAVERT_SQL_ENABLED=true)
    normalize_dates: bool = True
    preflight: dict | None = None
    preflight_stale: bool = False

    # ───── 序列化 ─────
    def to_dict(self) -> dict:
        return {
            "files": self.files,
            "map": self.map,
            "classify": self.classify,
            "stored": self.stored,
            "date_decisions": self.date_decisions,
            "node_positions": self.node_positions,
            "hospital_code": self.hospital_code,
            "batch_tag": self.batch_tag,
            "sync_142": self.sync_142,
            "normalize_dates": self.normalize_dates,
            "preflight": self.preflight,
            "preflight_stale": self.preflight_stale,
        }

    def reset_data(self, manifest: Manifest) -> None:
        """清空全部接入数据回到空白 (文件/映射/归类/声明表/节点/日期/预检); 保留 hospital_code/batch_tag 设置."""
        self.files = {}
        self.classify = {}
        self.stored = []
        self.node_positions = {}
        self.date_decisions = {}
        self.preflight = None
        self.preflight_stale = False
        self.map = {}
        for key, spoke in manifest.tabular_spokes().items():
            self.map[key] = {"key_mode": default_key_mode(spoke), "bridge": {}, "fields": {}}

    # ───── 文件 ─────
    def add_file(self, rel: str, info: dict) -> None:
        self.files[rel] = info
        self._invalidate_preflight()

    def remove_file(self, rel: str) -> None:
        """删文件 + 级联清掉引用它的全部映射 (设计 D4: 服务端单清, 不再 JS-vs-磁盘双清)."""
        self.files.pop(rel, None)
        self.classify.pop(rel, None)
        for spoke, m in self.map.items():
            fields = m.get("fields", {})
            for fk in [k for k, v in fields.items() if v.get("file") == rel]:
                del fields[fk]
            bridge = m.get("bridge") or {}
            if bridge.get("file") == rel:
                m["bridge"] = {}
        # 声明的 stored 表引用了该文件 → 一并移除
        self.stored = [s for s in self.stored if s.get("file") != rel]
        self._invalidate_preflight()

    # ───── 映射 ─────
    def ensure_spoke(self, spoke: str) -> dict:
        if spoke not in self.map:
            self.map[spoke] = {"key_mode": "synth", "bridge": {}, "fields": {}}
        return self.map[spoke]

    def spoke_file(self, spoke: str) -> str | None:
        fields = self.map.get(spoke, {}).get("fields", {})
        for v in fields.values():
            return v.get("file")
        return None

    def set_mapping(self, spoke: str, field_key: str, file: str | None,
                    col: str | None, auto: bool = False) -> None:
        m = self.ensure_spoke(spoke)
        if not col:
            m["fields"].pop(field_key, None)
        else:
            bound = self.spoke_file(spoke)
            if bound and file and file != bound:
                # 跨文件: 清空该表旧映射改绑新文件 (不弹窗拦截, 设计 D4)
                m["fields"] = {}
                auto = False
            m["fields"][field_key] = {"file": file, "col": col, "auto": bool(auto)}
        self._invalidate_preflight()

    def apply_auto_map(self, spoke: str, file: str, matched: dict[str, str],
                       key_mode: str | None = None) -> None:
        """自动归类命中字段 → 写入映射 (auto=True). 仅填未映射字段, 不覆盖手动值."""
        m = self.ensure_spoke(spoke)
        if key_mode:
            m["key_mode"] = key_mode
        for fk, col in matched.items():
            if fk in m["fields"] and not m["fields"][fk].get("auto"):
                continue  # 已有手动映射, 不覆盖
            m["fields"][fk] = {"file": file, "col": col, "auto": True}
        self._invalidate_preflight()

    def clear_spoke(self, spoke: str) -> None:
        m = self.ensure_spoke(spoke)
        m["fields"] = {}
        self._invalidate_preflight()

    def reset_mappings(self) -> None:
        for m in self.map.values():
            m["fields"] = {}
        self._invalidate_preflight()

    def set_key_mode(self, spoke: str, mode: str) -> None:
        self.ensure_spoke(spoke)["key_mode"] = mode
        self._invalidate_preflight()

    def set_bridge(self, spoke: str, bridge: dict) -> None:
        self.ensure_spoke(spoke)["bridge"] = bridge or {}
        self._invalidate_preflight()

    # ───── 归类 ─────
    def set_classification(self, rel: str, result: dict) -> None:
        self.classify[rel] = result

    # ───── stored 新表 ─────
    def add_stored(self, entry: dict) -> None:
        self.stored = [s for s in self.stored if s.get("name") != entry.get("name")]
        self.stored.append(entry)

    # ───── 元数据 / 日期 / 节点位置 ─────
    def set_meta(self, hospital_code: str | None = None,
                 normalize_dates: bool | None = None,
                 batch_tag: str | None = None,
                 sync_142: bool | None = None) -> None:
        if batch_tag is not None:
            self.batch_tag = batch_tag.strip()  # batch_tag 不影响 ETL 产出 → 不作废预检
            return
        if sync_142 is not None:
            self.sync_142 = bool(sync_142)       # 同步开关不影响 ETL → 不作废预检
            return
        if hospital_code is not None:
            self.hospital_code = hospital_code
        if normalize_dates is not None:
            self.normalize_dates = bool(normalize_dates)
        self._invalidate_preflight()

    def set_date_decisions(self, decisions: dict[str, bool]) -> None:
        self.date_decisions.update({k: bool(v) for k, v in (decisions or {}).items()})

    def set_node_position(self, node_id: str, x: float, y: float) -> None:
        self.node_positions[node_id] = {"x": x, "y": y}

    # ───── 预检 ─────
    def set_preflight(self, pf: dict | None) -> None:
        self.preflight = pf
        self.preflight_stale = False

    def _invalidate_preflight(self) -> None:
        """任一影响预检的输入变更 → 旧预检结论过期 (设计 D7, 防 stale 绿/红灯残留)."""
        if self.preflight is not None:
            self.preflight_stale = True


def new_state(manifest: Manifest) -> OnbState:
    """新会话: 为每个 tabular spoke 预置默认 key_mode (manifest 声明)."""
    st = OnbState()
    for key, spoke in manifest.tabular_spokes().items():
        st.map[key] = {"key_mode": default_key_mode(spoke), "bridge": {}, "fields": {}}
    return st


def _sid(request: Request) -> str:
    """会话 id (存 starlette 签名 cookie). 无则生成."""
    sid = request.session.get("onb_sid")
    if not sid:
        sid = uuid.uuid4().hex
        request.session["onb_sid"] = sid
    return sid


def get_state(request: Request, manifest: Manifest) -> OnbState:
    """取/建当前会话态 (进程内, 按 onb_sid)."""
    sid = _sid(request)
    with _LOCK:
        st = _SESSIONS.get(sid)
        if st is None:
            st = new_state(manifest)
            _SESSIONS[sid] = st
        return st


def reset_sessions() -> None:
    """测试用: 清空进程会话表."""
    with _LOCK:
        _SESSIONS.clear()
