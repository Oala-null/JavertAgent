/* onboarding.js — 数据接入工作室 (redesign-onboarding-demo-flow).
   服务端会话态为单一真相源 (设计 D4): 每次 mutation 走服务端 → 整体重渲染.
   自动驾驶: 上传即自动认表 (结果卡 绿/琥珀) + 我的文件→Javert 表流向图 + 终端 jv-go 收尾. */
(function () {
  "use strict";

  const SPOKES = window.ONBOARDING_SPOKES || [];
  const SPOKE_BY_KEY = {};
  SPOKES.forEach((s) => (SPOKE_BY_KEY[s.key] = s));
  const TABULAR = SPOKES.filter((s) => s.is_tabular);
  const VIEW = SPOKES.filter((s) => s.status === "view");

  let STATE = { files: {}, map: {}, classify: {}, stored: [], date_decisions: {},
    node_positions: {}, hospital_code: "ext", normalize_dates: true,
    preflight: null, preflight_stale: false };

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ────────────── 网络 ──────────────
  async function getSession() {
    const r = await fetch("/api/onboarding/session");
    const j = await r.json();
    if (j.ok) applyState(j.state);
  }
  async function postSession(body) {
    const r = await fetch("/api/onboarding/session", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    const j = await r.json();
    if (j.ok) applyState(j.state);
    else alert("操作失败: " + (j.error || ""));
    return j;
  }
  function applyState(state) {
    if (!state) return;
    STATE = state;
    renderAll();
  }

  // ────────────── 上传 / 删除 ──────────────
  const DATA_EXT = /\.(csv|txt|xls|xlsx)$/i;
  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []).filter((f) => DATA_EXT.test(f.name));
    if (!files.length) {
      alert("没检测到可用数据文件 (csv / txt / xlsx)。\n把【文件本身】拖进来即可 (拖整个文件夹也行, 里面有 csv 就能识别)。");
      return;
    }
    for (const f of files) {
      const fd = new FormData();
      fd.append("file", f);
      try {
        const r = await fetch("/api/onboarding/upload", { method: "POST", body: fd });
        const j = await r.json();
        if (j.ok) applyState(j.state);
        else alert("上传失败: " + (j.error || ""));
      } catch (e) { alert("上传异常: " + e); }
    }
  }

  // 支持拖文件夹 (递归取里面的数据文件) + 拖文件; 非数据文件由 uploadFiles 过滤
  async function filesFromDrop(dt) {
    const items = dt && dt.items ? Array.from(dt.items) : [];
    const entries = items.map((it) => it.webkitGetAsEntry && it.webkitGetAsEntry()).filter(Boolean);
    if (!entries.length) return Array.from((dt && dt.files) || []);
    const out = [];
    const readDir = (reader) => new Promise((res) => reader.readEntries(res, () => res([])));
    const getFile = (entry) => new Promise((res) => entry.file(res, () => res(null)));
    for (const entry of entries) {
      if (entry.isFile) { const f = await getFile(entry); if (f) out.push(f); }
      else if (entry.isDirectory) {
        let ents = []; const reader = entry.createReader();
        for (let b = await readDir(reader); b.length; b = await readDir(reader)) ents = ents.concat(b);
        for (const e of ents) if (e.isFile) { const f = await getFile(e); if (f) out.push(f); }
      }
    }
    return out.length ? out : Array.from((dt && dt.files) || []);
  }
  async function deleteFile(rel, filename) {
    if (!confirm(`删除已导入文件「${filename}」？\n(用它映射的字段会一并清空)`)) return;
    try {
      const r = await fetch("/api/onboarding/delete-upload", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file: rel }) });
      const j = await r.json();
      if (j.ok) applyState(j.state);
      else alert("删除失败: " + (j.error || ""));
    } catch (e) { alert("删除异常: " + e); }
  }

  // ────────────── 渲染主入口 ──────────────
  function renderAll() {
    const hc = $("#hospital-code"); if (hc && hc.value !== STATE.hospital_code) hc.value = STATE.hospital_code || "ext";
    const bt = $("#batch-tag"); if (bt && bt.value !== (STATE.batch_tag || "")) bt.value = STATE.batch_tag || "";
    const nd = $("#normalize-dates"); if (nd) nd.checked = !!STATE.normalize_dates;
    const sy = $("#sync-142"); if (sy) sy.checked = STATE.sync_142 !== false;
    // 桥表文件可选列表 = 已上传文件 (option 值用相对路径, 标签显文件名)
    const updl = $("#upfiles-dl");
    if (updl) updl.innerHTML = Object.entries(STATE.files || {}).map(([rel, info]) =>
      `<option value="${esc(rel)}">${esc((info || {}).filename || rel)}</option>`).join("");
    // 回到空白 (无文件无预检) 时收掉结果面板 (防清空后残留旧"已载入"消息)
    if (!STATE.preflight && !Object.keys(STATE.files || {}).length) {
      const panel = $("#preflight-panel");
      if (panel) { panel.hidden = true; panel.innerHTML = ""; }
    }
    renderCards();
    renderViewNote();
    renderFlow();
    computeGates();
  }

  // 文件角色: stored / recognized / ambiguous
  function fileRole(rel) {
    if ((STATE.stored || []).some((s) => s.file === rel)) return { role: "stored" };
    const c = STATE.classify[rel];
    if (c && c.spoke) return { role: "recognized", spoke: c.spoke };
    return { role: "ambiguous", classify: c || {} };
  }
  // 某 spoke 在该文件上已映射的字段
  function spokeFieldsForFile(spoke, rel) {
    const fields = (STATE.map[spoke] || {}).fields || {};
    const out = {};
    Object.entries(fields).forEach(([k, v]) => { if (v.file === rel) out[k] = v; });
    return out;
  }

  // ────────────── 结果卡 ──────────────
  function renderCards() {
    const box = $("#result-cards");
    box.innerHTML = "";
    const rels = Object.keys(STATE.files);
    if (!rels.length) {
      box.innerHTML = `<div class="cards-empty">还没有文件 — 拖入费用 / 文书 / 诊断 / 手术等 CSV, 系统会自动认表。</div>`;
      return;
    }
    rels.forEach((rel) => box.appendChild(buildCard(rel)));
  }

  function buildCard(rel) {
    const info = STATE.files[rel] || {};
    const r = fileRole(rel);
    const card = document.createElement("div");
    card.className = "rc";
    card.dataset.file = rel;
    const rows = info.n_rows_exact ? `${(info.n_rows || 0).toLocaleString()} 行`
      : `~${(info.n_rows || 0).toLocaleString()} 行(估算)`;

    if (r.role === "stored") {
      const sd = (STATE.stored || []).find((s) => s.file === rel) || {};
      card.classList.add("rc-stored");
      card.innerHTML = `<div class="rc-head"><span class="rc-badge b-stored">已声明新表</span>
        <span class="rc-name">${esc(sd.name)}</span>${delBtn(rel, info.filename)}</div>
        <div class="rc-sub">${esc(info.filename)} · ${rows} · 患者键 <code>${esc(sd.patient_col)}</code></div>
        <div class="rc-note">只存不审 · 暂不参与判定 (病案概览可见原文)</div>`;
      return card;
    }

    if (r.role === "ambiguous") {
      card.classList.add("rc-ambiguous");
      const reason = (r.classify.reason) || "请确认这是哪张表";
      card.innerHTML = `<div class="rc-head"><span class="rc-badge b-amb">请确认这是哪张表</span>
        <span class="rc-name">${esc(info.filename)}</span>${delBtn(rel, info.filename)}</div>
        <div class="rc-sub">${rows} · ${(info.columns || []).length} 列 · ${esc(reason)}</div>
        <div class="rc-pick">这是: ${spokePicker(rel, null)}
          <button class="btn-ghost rc-asnew" type="button" data-file="${esc(rel)}">或声明为新表</button></div>
        <div class="rc-chips">${colChips(rel)}</div>`;
      return card;
    }

    // recognized
    const spoke = r.spoke;
    const meta = SPOKE_BY_KEY[spoke];
    const mapped = spokeFieldsForFile(spoke, rel);
    const required = meta.fields.filter((f) => f.required);
    const missing = required.filter((f) => !mapped[f.key]);
    const ok = missing.length === 0;
    card.classList.add(ok ? "rc-green" : "rc-amber");
    const pc = info.patient_count != null
      ? `${info.patient_count_exact ? "" : "~"}${info.patient_count} 患者` : "";
    let head = `<div class="rc-head">
      <span class="rc-badge ${ok ? "b-green" : "b-amber"}">${ok ? "已就绪" : "需补"}</span>
      <span class="rc-name">已认出: ${esc(meta.name)}</span>${delBtn(rel, info.filename)}</div>`;
    let sub = `<div class="rc-sub">${esc(info.filename)} · ${required.length - missing.length}/${required.length} 必填${pc ? " · " + pc : ""} · ${rows}</div>`;
    let body = "";
    if (!ok) {
      body = `<div class="rc-missing">缺必填: ${missing.map((f) => esc(f.name)).join("、")} — 展开「调整 ▾」补映射</div>`;
    }
    const cockpit = `<details class="rc-cockpit"${ok ? "" : " open"}>
      <summary>调整 ▾</summary>${cockpitBody(rel, spoke)}</details>`;
    card.innerHTML = head + sub + body + cockpit;
    return card;
  }

  function delBtn(rel, filename) {
    return `<button class="rc-del" type="button" title="删除此文件"
      data-del="${esc(rel)}" data-fn="${esc(filename)}">✕</button>`;
  }

  // 表归属下拉 (recognized/ambiguous 一次定表, 非逐字段)
  function spokePicker(rel, current) {
    let opts = `<option value="">— 选择 —</option>`;
    TABULAR.forEach((s) => {
      opts += `<option value="${s.key}"${s.key === current ? " selected" : ""}>${esc(s.name)}</option>`;
    });
    return `<select class="rc-spoke-pick" data-file="${esc(rel)}">${opts}</select>`;
  }

  function colChips(rel) {
    const cols = (STATE.files[rel] || {}).columns || [];
    return cols.map((c) => `<span class="col-chip" data-file="${esc(rel)}" data-col="${esc(c)}">${esc(c)}</span>`).join("");
  }

  // 驾驶舱: 逐字段输入 + 键模式 + 桥表 + 列剖析 (默认收起, 设计 D1)
  function cockpitBody(rel, spoke) {
    const meta = SPOKE_BY_KEY[spoke];
    const m = STATE.map[spoke] || { key_mode: "synth", bridge: {}, fields: {} };
    const dlId = `dl-${rel.replace(/[^a-z0-9]/gi, "")}`;
    let fieldsHtml = meta.fields.map((f) => {
      const cur = (m.fields[f.key] || {});
      const val = cur.file === rel ? cur.col : (cur.col || "");
      const auto = cur.auto ? " auto-filled" : "";
      return `<div class="cf-row"><span class="cf-label">${esc(f.name)}${f.required ? '<i class="req">*</i>' : ""}</span>
        <input class="cf-input${auto}" list="${dlId}" data-spoke="${spoke}" data-field="${f.key}"
          value="${esc(val)}" placeholder="列名" autocomplete="off"></div>`;
    }).join("");
    const km = m.key_mode || "synth";
    const br = m.bridge || {};
    let keyrow = `<div class="cf-keyrow"><label>连接键
      <select class="cf-keymode" data-spoke="${spoke}">
        <option value="synth"${km === "synth" ? " selected" : ""}>裸号合成 (synth)</option>
        <option value="asis"${km === "asis" ? " selected" : ""}>已是落盘形态 (asis)</option>
        <option value="bridge"${km === "bridge" ? " selected" : ""}>经桥表归一 (bridge)</option>
      </select></label>
      <button class="btn-ghost cf-clear" type="button" data-spoke="${spoke}">清空本表</button>
      <div class="cf-bridge" data-spoke="${spoke}"${km === "bridge" ? "" : " hidden"}>
        <input class="br-file" list="upfiles-dl" placeholder="桥表文件 (从已上传的文件里选)" value="${esc(br.file || "")}">
        <input class="br-src" placeholder="桥表源键列" value="${esc(br.src || "")}">
        <input class="br-canon" placeholder="桥表 canonical 列" value="${esc(br.canon || "")}">
      </div></div>`;
    return `<div class="cf-spoke">这张表归属: ${spokePicker(rel, spoke)}</div>
      <datalist id="${dlId}">${((STATE.files[rel] || {}).columns || []).map((c) => `<option value="${esc(c)}">`).join("")}</datalist>
      <div class="cf-fields">${fieldsHtml}</div>${keyrow}
      <div class="rc-chips">${colChips(rel)}</div>`;
  }

  // ────────────── 视图表说明 ──────────────
  function renderViewNote() {
    const bar = $("#view-note-bar");
    if (!VIEW.length) { bar.hidden = true; return; }
    bar.hidden = false;
    bar.innerHTML = "派生视图 (无需映射): " + VIEW.map((s) =>
      `<span class="vn-chip">${esc(s.name)} <small>由${(s.tool || "")}派生</small></span>`).join(" ");
  }

  // ────────────── 流向图: 我的文件 → Javert 表 ──────────────
  function flowNodes() {
    // 左: 文件; 右: tabular spoke (有映射的实线) + stored
    const files = Object.keys(STATE.files).map((rel) => ({
      id: "file:" + rel, side: "left", label: (STATE.files[rel] || {}).filename || rel, rel }));
    const spokes = TABULAR.map((s) => ({ id: "spoke:" + s.key, side: "right", label: s.name, spoke: s.key }));
    const stored = (STATE.stored || []).map((s) => ({
      id: "stored:" + s.name, side: "right", label: s.name + " (新表)", stored: s.name }));
    return { files, spokes: spokes.concat(stored) };
  }
  function flowEdges() {
    // 每个 (文件→spoke) 映射一条边; 标签 = 该 spoke 实际映射的患者键列
    const edges = [];
    Object.entries(STATE.map).forEach(([spoke, m]) => {
      const fields = m.fields || {};
      const filesUsed = new Set();
      Object.values(fields).forEach((v) => v.file && filesUsed.add(v.file));
      const keyCol = (fields.patient_id || {}).col || "?";
      filesUsed.forEach((rel) => edges.push({ from: "file:" + rel, to: "spoke:" + spoke, label: keyCol, spoke }));
    });
    (STATE.stored || []).forEach((s) => {
      edges.push({ from: "file:" + s.file, to: "stored:" + s.name, label: s.patient_col || "?", stored: s.name });
    });
    return edges;
  }

  function renderFlow() {
    const wrap = $("#flow-wrap"), nodesBox = $("#flow-nodes");
    if (!Object.keys(STATE.files).length) { wrap.hidden = true; return; }
    wrap.hidden = false;
    const { files, spokes } = flowNodes();
    nodesBox.innerHTML = "";
    const canvas = $("#flow-canvas");
    const cw = canvas.clientWidth || 760;
    const place = (list, x) => list.forEach((nd, i) => {
      const el = document.createElement("div");
      el.className = "flow-node " + (nd.side === "left" ? "fn-file" : "fn-spoke");
      if (nd.spoke) el.classList.add("fn-tab");
      if (nd.stored) el.classList.add("fn-stored");
      el.dataset.node = nd.id;
      el.textContent = nd.label;
      const saved = STATE.node_positions[nd.id];
      el.style.left = (saved ? saved.x : x) + "px";
      el.style.top = (saved ? saved.y : 16 + i * 56) + "px";
      nodesBox.appendChild(el);
      wireDrag(el);
    });
    place(files, 12);
    place(spokes, Math.max(220, cw - 190));
    drawEdges();
  }

  function nodeCenter(id, side) {
    const el = $(`.flow-node[data-node="${cssEsc(id)}"]`);
    if (!el) return null;
    const c = $("#flow-canvas").getBoundingClientRect();
    const b = el.getBoundingClientRect();
    return { x: (side === "right" ? b.left : b.right) - c.left, y: b.top - c.top + b.height / 2 };
  }
  function cssEsc(s) { return String(s).replace(/["\\]/g, "\\$&"); }

  function drawEdges() {
    const svg = $("#flow-edges"), canvas = $("#flow-canvas");
    if (!svg || !canvas) return;
    const cw = canvas.clientWidth, ch = canvas.clientHeight;
    svg.setAttribute("width", cw); svg.setAttribute("height", ch);
    svg.setAttribute("viewBox", `0 0 ${cw} ${ch}`);
    const parts = [];
    flowEdges().forEach((e, idx) => {
      const a = nodeCenter(e.from, "left"), b = nodeCenter(e.to, "right");
      if (!a || !b) return;
      const mx = (a.x + b.x) / 2;
      const cls = e.stored ? "fe-stored" : "fe-live";
      const dash = e.stored ? ' stroke-dasharray="5 4"' : "";
      const d = `M${a.x},${a.y} C${mx},${a.y} ${mx},${b.y} ${b.x},${b.y}`;
      parts.push(`<path d="${d}" class="${cls}" fill="none"${dash}/>`);
      // 透明加粗命中线 (点连线看明细)
      parts.push(`<path d="${d}" class="fe-hit" fill="none" data-spoke="${e.spoke || ""}" data-stored="${e.stored || ""}"/>`);
      parts.push(`<circle cx="${b.x}" cy="${b.y}" r="3" class="${cls}"/>`);
      parts.push(`<text x="${mx}" y="${(a.y + b.y) / 2 - 5}" class="fe-label" text-anchor="middle">${esc(e.label)}</text>`);
    });
    svg.innerHTML = parts.join("");
    $$(".fe-hit", svg).forEach((p) => p.addEventListener("click", (ev) => {
      const sp = p.getAttribute("data-spoke");
      if (sp) showEdgeDetail(sp, ev);
    }));
  }

  function showEdgeDetail(spoke, ev) {
    const meta = SPOKE_BY_KEY[spoke];
    const m = STATE.map[spoke] || { fields: {} };
    const pop = $("#edge-pop");
    let rows = meta.fields.map((f) => {
      const v = m.fields[f.key];
      return v ? `<div><code>${esc(v.col)}</code> → <b>${esc(f.name)}</b></div>` : "";
    }).filter(Boolean).join("");
    pop.innerHTML = `<div class="pop-head">${esc(meta.name)} · 源列→目标<button class="pop-x">×</button></div>
      <div class="pop-body">${rows || "(未映射)"}</div>`;
    pop.hidden = false;
    pop.style.left = (window.scrollX + ev.clientX + 8) + "px";
    pop.style.top = (window.scrollY + ev.clientY + 8) + "px";
    $(".pop-x", pop).onclick = () => (pop.hidden = true);
  }

  // 节点拖拽 (绝对定位 + 重连 SVG; 松手存会话位置, 设计 Polish 7.1)
  function wireDrag(el) {
    el.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const canvas = $("#flow-canvas").getBoundingClientRect();
      const start = el.getBoundingClientRect();
      const offx = e.clientX - start.left, offy = e.clientY - start.top;
      el.classList.add("dragging");
      const move = (ev) => {
        let x = ev.clientX - canvas.left - offx;
        let y = ev.clientY - canvas.top - offy;
        x = Math.max(0, Math.min(x, canvas.width - el.offsetWidth));
        y = Math.max(0, Math.min(y, canvas.height - el.offsetHeight));
        el.style.left = x + "px"; el.style.top = y + "px";
        drawEdges();
      };
      const up = () => {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        el.classList.remove("dragging");
        postSession({ action: "set_node_position", node_id: el.dataset.node,
          x: parseFloat(el.style.left), y: parseFloat(el.style.top) });
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
  }

  // ────────────── 两道闸 ──────────────
  function requiredGaps() {
    const gaps = [];
    TABULAR.forEach((s) => {
      const fields = (STATE.map[s.key] || {}).fields || {};
      if (!Object.keys(fields).length) return;
      s.fields.filter((f) => f.required && !fields[f.key]).forEach((f) => gaps.push(`${s.name}·${f.name}`));
    });
    return gaps;
  }
  function computeGates() {
    const gaps = requiredGaps();
    const anyMapped = TABULAR.some((s) => Object.keys((STATE.map[s.key] || {}).fields || {}).length);
    const hint = $("#gate-hint"), btn = $("#btn-start");
    if (!anyMapped) {
      hint.textContent = "上传文件 — 系统自动认表; 没认全的会标「需补」, 展开调整即可";
      hint.className = "gate-hint"; btn.disabled = true; return;
    }
    if (gaps.length) {
      hint.textContent = "必填闸: 缺 " + gaps.join("、"); hint.className = "gate-hint gate-block";
      btn.disabled = true; return;
    }
    const pf = STATE.preflight;
    if (STATE.preflight_stale) {
      hint.innerHTML = `此前预检已过期 — <button class="link-btn" id="re-preflight" type="button">重新预检</button> (点「载入数据」会自动重检)`;
      hint.className = "gate-hint gate-warn"; btn.disabled = false;
      const rb = $("#re-preflight"); if (rb) rb.onclick = preflight;
    } else if (pf && pf.verdict === "red") {
      hint.textContent = "连接预检 🔴 键几乎不交 — 见下方诊断, 改映射后重检";
      hint.className = "gate-hint gate-block"; btn.disabled = true;
    } else if (pf && pf.verdict === "green") {
      hint.textContent = "✓ 必填齐 + 预检通过 — 可「载入数据」"; hint.className = "gate-hint gate-ok";
      btn.disabled = false;
    } else {
      hint.textContent = "必填齐 ✓ — 建议先「连接预检」再「载入数据」"; hint.className = "gate-hint gate-ok";
      btn.disabled = false;
    }
  }

  // ────────────── 连接预检 ──────────────
  async function preflight() {
    const panel = $("#preflight-panel");
    panel.hidden = false; panel.innerHTML = "连接预检中…";
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });  // 面板在页底, 滚到可见
    try {
      const r = await fetch("/api/onboarding/preflight", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const j = await r.json();
      if (j.state) STATE = j.state;
      renderPreflight(j);
      renderAll();
      return j;
    } catch (e) { panel.textContent = "预检异常: " + e; }
  }
  function renderPreflight(j) {
    const panel = $("#preflight-panel");
    if (!j.ok) {
      panel.innerHTML = `<div class="pf-red">校验失败: ${esc((j.errors || [j.error]).join("; "))}</div>`;
      return;
    }
    const pf = j.preflight;
    let html = "<div class='pf-grid'>";
    (j.spokes || []).forEach((s) => {
      html += `<div class="pf-spoke"><b>${esc(s.name)}</b>: ${s.rows} 行 / ${s.patients} 患者`;
      if (s.bridge_miss) html += ` <span class="warn">桥表失配 ${s.bridge_miss}</span>`;
      if (s.date_notes && s.date_notes.length) html += `<div class="pf-note">${esc(s.date_notes.join(" · "))}</div>`;
      html += "</div>";
    });
    html += "</div>";
    if (pf) {
      const cls = pf.verdict === "green" ? "pf-green" : (pf.verdict === "red" ? "pf-red" : "pf-yellow");
      html += `<div class="pf-verdict ${cls}">${pf.emoji} 键交集覆盖率 ${(pf.coverage * 100).toFixed(0)}% (${pf.hit}/${pf.total}, 参照 ${esc(pf.primary)}) — ${esc(pf.label)}</div>`;
      const per = Object.entries(pf.per_spoke || {}).map(([k, v]) =>
        `${esc((SPOKE_BY_KEY[k] || {}).name || k)} ${(v * 100).toFixed(0)}%`).join(" · ");
      if (per) html += `<div class="pf-per">各表命中: ${per}</div>`;
      // 红灯可执行诊断 (设计 D7)
      if (pf.verdict === "red" && pf.diagnostics) {
        const d = pf.diagnostics;
        html += `<div class="pf-block">🔴 已挡住「载入数据」</div>
          <div class="pf-diag">⚠ 命中最低: <b>${esc(d.lowest_name)}</b> (${(d.lowest_rate * 100).toFixed(0)}%) — ${esc(d.message)}</div>`;
      }
    }
    (j.time_windows || []).forEach((tw) => {
      const cls = tw.verdict === "green" ? "pf-green" : "pf-yellow";
      html += `<div class="pf-verdict ${cls}">${tw.emoji} 时间窗口 ${esc(tw.name)} vs 住院期: ${(tw.out_rate * 100).toFixed(0)}% 落窗口外${tw.note ? " — " + esc(tw.note) : ""}</div>`;
    });
    panel.innerHTML = html;
  }

  // ────────────── 日期歧义确认 (设计 D8, 一次性) ──────────────
  async function ensureDatesResolved(thenFn) {
    let j;
    try {
      const r = await fetch("/api/onboarding/date-ambiguities", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      j = await r.json();
    } catch (e) { return thenFn(); }
    const pending = (j.ambiguities || []).filter((a) => !a.decided);
    if (!pending.length) return thenFn();
    showDateModal(pending, thenFn);
  }
  function showDateModal(items, thenFn) {
    const body = $("#date-modal-body");
    body.innerHTML = `<p class="dm-intro">以下日期列整列都是 1–12, 无法自动判断是「日/月」还是「月/日」。请选择, 避免日期被搞反:</p>` +
      items.map((a) => {
        const cmp = (a.samples || []).map((s) =>
          `<div class="dm-sample"><code>${esc(s.raw)}</code> → D/M: <b>${esc(s.dm)}</b> / M/D: <b>${esc(s.md)}</b></div>`).join("");
        return `<div class="dm-row" data-key="${esc(a.decision_key)}">
          <div class="dm-name">${esc(a.name)} <small>(${esc(a.file)})</small></div>${cmp}
          <label class="dm-opt"><input type="radio" name="d-${esc(a.decision_key)}" value="dm" checked> 日/月 (D/M)</label>
          <label class="dm-opt"><input type="radio" name="d-${esc(a.decision_key)}" value="md"> 月/日 (M/D)</label></div>`;
      }).join("");
    openModal("date-modal");
    $("#date-confirm").onclick = async () => {
      const decisions = {};
      items.forEach((a) => {
        const sel = body.querySelector(`input[name="d-${cssEsc(a.decision_key)}"]:checked`);
        decisions[a.decision_key] = sel ? sel.value === "dm" : true;
      });
      await postSession({ action: "set_date_decisions", decisions });
      closeModal("date-modal");
      thenFn();
    };
  }

  // ────────────── 载入数据 ──────────────
  async function start() {
    ensureDatesResolved(doStart);
  }
  async function doStart() {
    const panel = $("#preflight-panel");
    panel.hidden = false; panel.innerHTML = "落映射 + 跑 ETL 载入数据…";
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    try {
      const r = await fetch("/api/onboarding/start", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const j = await r.json();
      if (!j.ok) {
        let msg = j.error || (j.errors || []).join("; ");
        if (j.gate === "preflight" && j.preflight && j.preflight.diagnostics) {
          const d = j.preflight.diagnostics;
          msg += `<div class="pf-diag">⚠ 命中最低: <b>${esc(d.lowest_name)}</b> — ${esc(d.message)}</div>`;
        }
        panel.innerHTML = `<div class="pf-red">${j.gate === "preflight" ? "🔴 连接预检挡住: " : "必填闸: "}${msg}</div>`;
        await getSession();
        return;
      }
      renderLoaded(j);
      await getSession();
    } catch (e) { panel.textContent = "异常: " + e; }
  }
  function renderLoaded(j) {
    const panel = $("#preflight-panel");
    let html = `<div class="loaded-banner">✅ 文件已载入 — ${j.tables.length} 表 / <b>${j.patients_total}</b> 个可审核患者 → <code>${esc(j.output_dir)}/</code></div><div class='pf-grid'>`;
    j.tables.forEach((t) => {
      html += `<div class="pf-spoke"><b>${esc(t.file)}</b>: ${t.rows} 行 / ${t.patients} 患者${t.bridge_miss ? ` <span class='warn'>失配 ${t.bridge_miss}</span>` : ""}</div>`;
    });
    html += "</div>";
    html += `<div class="run-hint"><b>👉 去终端敲一个词开跑审核</b> (本工作台只载入数据, LLM 审核走终端):
      <div class="cmd-row"><pre id="run-cmd">jv-go</pre><button class="btn-primary" id="copy-cmd" type="button">复制 jv-go</button><span id="copy-ok" class="copy-ok" hidden>已复制 ✓</span></div>
      <div class="pf-per">已载入患者: ${esc((j.patient_ids || []).slice(0, 12).join(", "))}${j.patients_total > 12 ? " …" : ""}</div>
      <details><summary>没装 jv-* 快捷命令? 展开原始命令</summary><pre class="next-steps">${esc((j.next_steps || []).join("\n"))}</pre></details></div>`;
    panel.innerHTML = html;
    // 自动复制到剪贴板 + 按钮兜底
    try { navigator.clipboard?.writeText("jv-go").then(() => { const o = $("#copy-ok"); if (o) o.hidden = false; }); } catch (e) {}
    const cb = $("#copy-cmd");
    if (cb) cb.onclick = () => { navigator.clipboard?.writeText("jv-go"); const o = $("#copy-ok"); if (o) o.hidden = false; };
  }

  // ────────────── 清空已载入 ──────────────
  async function clearOutput() {
    if (!confirm("清空全部 — 删除已载入产出 + 上传文件 + 当前映射, 回到空白页面?\n(不可撤销; 之后需重新拖文件)")) return;
    const btn = $("#btn-clear");
    btn.disabled = true; btn.textContent = "清空中…";
    try {
      const r = await fetch("/api/onboarding/clear-output", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const j = await r.json();
      if (j.ok) {
        applyState(j.state);  // 页面整体重渲染 → 卡片/流向图清空
        const panel = $("#preflight-panel");
        panel.hidden = false;
        panel.innerHTML = `<div class="loaded-banner">🧹 已清空全部 — 删除 ${(j.removed || []).length} 个文件/产出, 页面已回到空白。可重新拖文件开始。</div>`;
        panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } else alert("清空失败: " + (j.error || ""));
    } catch (e) { alert("清空异常: " + e); }
    finally { btn.disabled = false; btn.textContent = "清空全部"; }
  }

  // ────────────── 声明新表 modal ──────────────
  function showDeclareModal() {
    const rels = Object.keys(STATE.files);
    if (!rels.length) { alert("请先上传文件"); return; }
    const body = $("#declare-modal-body");
    const fileOpts = rels.map((rel) => `<option value="${esc(rel)}">${esc((STATE.files[rel] || {}).filename || rel)}</option>`).join("");
    body.innerHTML = `<label class="dl-field">中文名 <input id="dl-name" placeholder="如: 输血记录"></label>
      <label class="dl-field">数据文件 <select id="dl-file">${fileOpts}</select></label>
      <label class="dl-field">患者键列 <input id="dl-pcol" list="dl-cols" placeholder="如: 住院号"></label>
      <datalist id="dl-cols"></datalist>
      <div class="dl-preview" id="dl-preview"></div>`;
    const refresh = () => {
      const rel = $("#dl-file").value;
      const info = STATE.files[rel] || {};
      $("#dl-cols").innerHTML = (info.columns || []).map((c) => `<option value="${esc(c)}">`).join("");
      const cols = info.columns || [];
      const sample = (info.sample || []).slice(0, 5);
      let t = `<table class="dl-tbl"><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>`;
      sample.forEach((row) => { t += "<tr>" + cols.map((c) => `<td>${esc(row[c])}</td>`).join("") + "</tr>"; });
      t += "</tbody></table>";
      $("#dl-preview").innerHTML = `<div class="dl-pv-title">前 ${sample.length} 行预览:</div>` + t;
    };
    $("#dl-file").onchange = refresh; refresh();
    openModal("declare-modal");
    $("#declare-confirm").onclick = async () => {
      const name = $("#dl-name").value.trim(), file = $("#dl-file").value, pcol = $("#dl-pcol").value.trim();
      if (!name || !file || !pcol) { alert("请填中文名 / 文件 / 患者键列"); return; }
      await postSession({ action: "add_stored", name, file, patient_col: pcol });
      closeModal("declare-modal");
    };
  }

  // ────────────── modal 工具 ──────────────
  function openModal(id) { $("#" + id).hidden = false; }
  function closeModal(id) { $("#" + id).hidden = true; }

  // ────────────── 列剖析 popover ──────────────
  async function showProfile(file, col, anchor) {
    const pop = $("#profile-pop");
    pop.hidden = false;
    pop.innerHTML = `<div class="pop-head">剖析「${esc(col)}」<button class="pop-x">×</button></div><div class="pop-body">计算中…</div>`;
    const rect = anchor.getBoundingClientRect();
    pop.style.top = (window.scrollY + rect.bottom + 6) + "px";
    pop.style.left = (window.scrollX + rect.left) + "px";
    $(".pop-x", pop).onclick = () => (pop.hidden = true);
    try {
      const r = await fetch("/api/onboarding/profile", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file, column: col, mode: "sample", as_key: false }) });
      const j = await r.json();
      $(".pop-body", pop).innerHTML = j.ok ? renderProfile(j.profile) : ("剖析失败: " + esc(j.error));
    } catch (e) { $(".pop-body", pop).textContent = "异常: " + e; }
  }
  function renderProfile(p) {
    const rows = [];
    rows.push(`<b>类型</b>: ${esc(p.kind)}` + (p.exact ? "" : " (采样近似)"));
    rows.push(`<b>非空</b>: ${p.count} · <b>空值率</b>: ${(p.null_rate * 100).toFixed(0)}%`);
    if (p.kind === "numeric") rows.push(`<b>范围</b>: ${p.min} ~ ${p.max} · 均值 ${Number(p.mean).toFixed(2)}`);
    if (p.unique != null) rows.push(`<b>唯一值</b>: ${p.unique} · <b>重复度</b>: ${((p.dup_rate || 0) * 100).toFixed(0)}%`);
    if (p.date) {
      const d = p.date;
      rows.push(`<b>日期</b>: dayfirst=${d.dayfirst}${d.dayfirst_confident ? "" : "(歧义)"} · 解析率 ${(d.parse_rate * 100).toFixed(0)}%`);
      if (d.is_time_only) rows.push(`<span class="warn">⚠ 纯时间无日期, 不可做时间窗口</span>`);
      if (d.anomalies && d.anomalies.length) rows.push(`<span class="warn">⚠ 异常值: ${esc(d.anomalies.slice(0, 3).join(", "))}</span>`);
    }
    return rows.join("<br>");
  }

  // ────────────── 事件委托 ──────────────
  function wireDelegation() {
    document.body.addEventListener("click", (e) => {
      const t = e.target;
      if (t.classList.contains("rc-del")) deleteFile(t.dataset.del, t.dataset.fn);
      else if (t.classList.contains("col-chip")) showProfile(t.dataset.file, t.dataset.col, t);
      else if (t.classList.contains("rc-asnew")) showDeclareModal();
      else if (t.dataset && t.dataset.close) closeModal(t.dataset.close);
      else if (t.classList.contains("cf-clear")) postSession({ action: "clear_spoke", spoke: t.dataset.spoke });
    });
    document.body.addEventListener("change", (e) => {
      const t = e.target;
      if (t.classList.contains("rc-spoke-pick")) {
        const sp = t.value;
        if (sp) postSession({ action: "reclassify_file", file: t.dataset.file, spoke: sp });
      } else if (t.classList.contains("cf-input")) {
        const col = t.value.trim();
        const rel = fileOfCockpit(t);
        postSession({ action: "set_mapping", spoke: t.dataset.spoke, field: t.dataset.field,
          file: col ? rel : null, col: col || null });
      } else if (t.classList.contains("cf-keymode")) {
        postSession({ action: "set_key_mode", spoke: t.dataset.spoke, mode: t.value });
      } else if (t.classList.contains("br-file") || t.classList.contains("br-src") || t.classList.contains("br-canon")) {
        const box = t.closest(".cf-bridge"); const spoke = box.dataset.spoke;
        postSession({ action: "set_bridge", spoke, bridge: {
          file: $(".br-file", box).value.trim(), src: $(".br-src", box).value.trim(),
          canon: $(".br-canon", box).value.trim() } });
      }
    });
  }
  // 驾驶舱 input 所属文件 (该卡的 data-file)
  function fileOfCockpit(input) {
    const card = input.closest(".rc");
    return card ? card.dataset.file : null;
  }

  // ────────────── 初始化 ──────────────
  function init() {
    wireDelegation();
    const fi = $("#file-input"), dz = $("#dropzone");
    $("#btn-pick").onclick = () => fi.click();
    fi.onchange = () => uploadFiles(fi.files);
    // 全页面接住拖放 — 防止拖偏了浏览器直接打开文件; dropzone 仅给视觉反馈
    document.addEventListener("dragover", (e) => e.preventDefault());
    document.addEventListener("drop", async (e) => {
      e.preventDefault();
      dz.classList.remove("drag-over");
      uploadFiles(await filesFromDrop(e.dataTransfer));
    });
    dz.addEventListener("dragenter", () => dz.classList.add("drag-over"));
    dz.addEventListener("dragleave", () => dz.classList.remove("drag-over"));
    $("#btn-preflight").onclick = preflight;
    $("#btn-start").onclick = start;
    $("#btn-clear").onclick = clearOutput;
    $("#btn-declare").onclick = showDeclareModal;
    $("#btn-reset").onclick = () => { if (confirm("重置所有字段映射?")) postSession({ action: "reset_mappings" }); };
    const onEl = (sel, ev, fn) => { const el = $(sel); if (el) el.addEventListener(ev, fn); };
    onEl("#hospital-code", "change", (e) =>
      postSession({ action: "set_meta", hospital_code: e.target.value.trim() || "ext" }));
    onEl("#batch-tag", "change", (e) =>
      postSession({ action: "set_meta", batch_tag: e.target.value.trim() }));
    onEl("#normalize-dates", "change", (e) =>
      postSession({ action: "set_meta", normalize_dates: e.target.checked }));
    onEl("#sync-142", "change", (e) =>
      postSession({ action: "set_meta", sync_142: e.target.checked }));
    let rt;
    window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(renderFlow, 120); });
    getSession();  // 从服务端会话态恢复 (刷新/冷启动)
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
