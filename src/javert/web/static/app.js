// Javert 审核工作台 — vanilla JS (~250 行)
// 处理: review form 提交 / SSE 订阅 / banner dismiss / 原始病历 modal / sidebar 增量更新

(function () {
  "use strict";

  // =========================================================
  // Helpers
  // =========================================================
  function $(selector, root) { return (root || document).querySelector(selector); }
  function $$(selector, root) { return Array.from((root || document).querySelectorAll(selector)); }

  function showToast(text, kind) {
    var stack = $("#toast-stack");
    if (!stack) return;
    var t = document.createElement("div");
    t.className = "toast" + (kind ? " " + kind : "");
    t.textContent = text;
    stack.insertBefore(t, stack.firstChild);
    // 最多保留 5 条
    while (stack.childNodes.length > 5) {
      stack.removeChild(stack.lastChild);
    }
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 4000);
  }

  function verdictColor(v) {
    if (!v) return "unknown";
    v = ("" + v).toUpperCase();
    if (v === "V" || v === "VIOLATION") return "v";
    if (v === "I" || v === "INCONCLUSIVE") return "i";
    if (v === "C" || v === "CLEAN") return "c";
    return "unknown";
  }
  function verdictLabel(v) {
    return ({V: "认同", I: "改判不明", C: "驳回",
             VIOLATION: "违规", INCONCLUSIVE: "不明", CLEAN: "干净"})[("" + v).toUpperCase()] || v;
  }

  // 当前 sidebar 服务端 verdict filter (sidebar 下拉框反映服务端渲染口径)
  function _activeFilterMode() {
    var sel = document.querySelector('.filter-form select[name="filter"]');
    return (sel && sel.value) || "v_and_i";
  }
  // 某 run 的 AI 裁决是否落在当前 filter 的命中集 (决定 sidebar 进度分子是否 +1)
  function _isRelevantVerdict(auditVerdict, fmode) {
    var c = verdictColor(auditVerdict);   // 'v' / 'i' / 'c'
    if (fmode === "v_only") return c === "v";
    if (fmode === "i_only") return c === "i";
    if (fmode === "all") return c === "v" || c === "i" || c === "c";
    return c === "v" || c === "i";        // v_and_i (默认)
  }

  // =========================================================
  // Review form 提交
  // =========================================================
  window.submitReview = function (ev) {
    ev.preventDefault();
    var form = ev.target;
    var runId = form.getAttribute("data-run-id");
    var verdict = (form.querySelector('input[name="verdict"]:checked') || {}).value;
    if (!verdict) {
      alert("请先选择决策 (V / I / C)");
      return false;
    }
    var comment = (form.querySelector('textarea[name="comment"]') || {}).value || null;
    fetch("/review", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({run_id: runId, verdict: verdict, comment: comment}),
    }).then(function (resp) {
      if (!resp.ok) {
        return resp.json().then(function (j) { throw new Error(j.error || ("HTTP " + resp.status)); });
      }
      return resp.json();
    }).then(function (data) {
      // 刷新本卡片显示
      var card = form.closest(".violation-card");
      if (card) {
        card.classList.remove("verdict-v", "verdict-i", "verdict-c");
        // 注意: 本侧 verdict color 反映 Javert 判, 不变. 这里只更新 review 部分.
      }
      showToast("已提交: " + verdictLabel(verdict), "review");
      // 重载页面以拿到最新数据 (简单做法 — pilot 阶段够用)
      setTimeout(function () { location.reload(); }, 500);
    }).catch(function (e) {
      alert("提交失败: " + e.message);
    });
    return false;
  };

  window.showReviewForm = function (runId) {
    var f = document.getElementById("review-form-" + runId);
    if (f) f.classList.remove("hidden");
  };
  window.hideReviewForm = function (runId) {
    var f = document.getElementById("review-form-" + runId);
    if (f) f.classList.add("hidden");
  };

  // =========================================================
  // Welcome banner dismiss
  // =========================================================
  window.dismissBanner = function () {
    fetch("/api/banner/dismiss", {method: "POST", credentials: "same-origin"})
      .then(function () {
        var b = document.getElementById("welcome-banner");
        if (b && b.parentNode) b.parentNode.removeChild(b);
      });
  };

  // =========================================================
  // 原始病历 modal — 两 tab (文书 / 费用) + 搜索栏 (Ctrl+F)
  // =========================================================
  function _esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})[c];
    });
  }

  // ---------- 原始数据 fetch (modal + 对照面板共用缓存) + table builders ----------
  var _rawCache = {};
  function fetchRaw(pid) {
    if (_rawCache[pid]) return Promise.resolve(_rawCache[pid]);
    return fetch("/api/patient/" + encodeURIComponent(pid) + "/raw", {credentials: "same-origin"})
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (data) { _rawCache[pid] = data; return data; });
  }

  function _notesPanelHtml(data) {
    var notes = data.notes || [];
    var inner;
    if (!notes.length) {
      inner = '<table class="data-table"><tbody><tr><td class="muted">(无文书)</td></tr></tbody></table>';
    } else {
      // 后端已按 (bucket_order, 事件时间) 排好序 → 顺序分组渲染可折叠桶 (D3)
      var html = "", curBucket = null, groupRows = [];
      function flush() {
        if (curBucket === null) return;
        html += '<details class="note-bucket" open>' +
          '<summary class="note-bucket-head">' + _esc(curBucket) +
            ' <span class="nb-count">(' + groupRows.length + ')</span></summary>' +
          '<table class="data-table"><thead><tr>' +
            '<th>时间</th><th>阶段</th><th>子阶段</th><th>内容</th>' +
          '</tr></thead><tbody>' + groupRows.join("") + '</tbody></table>' +
        '</details>';
        groupRows = [];
      }
      notes.forEach(function (n) {
        var b = n.bucket || "其他";
        if (b !== curBucket) { flush(); curBucket = b; }
        groupRows.push('<tr data-subsection="' + _esc(n.subsection) + '">' +
          "<td>" + _esc(n.ts).slice(0, 10) + "</td>" +
          "<td>" + _esc(n.section) + "</td>" +
          "<td>" + _esc(n.subsection) + "</td>" +
          "<td style='max-width:480px;'>" + _esc(n.content) + "</td>" +
        "</tr>");
      });
      flush();
      inner = html;
    }
    return '<div class="tabpanel" data-panel="notes">' + inner + '</div>';
  }
  function _feesPanelHtml(data, hidden) {
    // 时间列前置 + 用后端预格式化的 fee_date (YYYY/MM/DD)
    var rows = (data.fees || []).map(function (f) {
      return '<tr data-name="' + _esc(f.medins_list_name) + '">' +
        "<td>" + _esc(f.fee_date || f.fee_ocur_time) + "</td>" +
        ["medins_list_name", "spec", "cnt", "pric", "det_item_fee_sumamt",
         "medins_chrgitm_type"]
          .map(function (c) { return "<td>" + _esc(f[c]) + "</td>"; }).join("") + "</tr>";
    }).join("");
    return '<div class="tabpanel" data-panel="fees"' + (hidden ? " hidden" : "") + '>' +
      '<table class="data-table"><thead><tr>' +
        '<th>时间</th><th>项目</th><th>规格</th><th>数量</th><th>单价</th><th>合计</th><th>类别</th>' +
      '</tr></thead><tbody>' + (rows || '<tr><td colspan="7" class="muted">(无费用)</td></tr>') + '</tbody></table>' +
      '</div>';
  }
  // 检验记录 panel — 检验(化验) + 检查(影像/超声) 合并一个 tab, 各一段; 项名作 data-name
  // 供命中跳转高亮 (search 引擎在 active tabpanel 内按文本匹配项名).
  function _labsPanelHtml(data, hidden) {
    var labs = data.labs || [], exams = data.exams || [];
    var sections = "";
    if (labs.length) {
      var abnormal = ["", "正常", "N"];
      var lrows = labs.map(function (l) {
        var abn = l.flag && abnormal.indexOf(l.flag) < 0;
        return '<tr data-name="' + _esc(l.item) + '"' + (abn ? ' class="lab-abnormal"' : '') + '>' +
          "<td>" + _esc(l.date) + "</td>" +
          "<td>" + _esc(l.item) +
            (l.inspection ? '<span class="lab-sub"> · ' + _esc(l.inspection) + '</span>' : '') + "</td>" +
          "<td>" + _esc(l.result) + (l.unit ? " " + _esc(l.unit) : "") + "</td>" +
          "<td>" + _esc(l.ref) + "</td>" +
          "<td>" + _esc(l.flag) + "</td>" +
          "<td>" + _esc(l.department) + "</td>" +
        "</tr>";
      }).join("");
      sections += '<div class="lab-section-head">检验 · 化验 (' + labs.length + ')</div>' +
        '<table class="data-table"><thead><tr>' +
          '<th>时间</th><th>项目</th><th>结果</th><th>参考</th><th>标志</th><th>科室</th>' +
        '</tr></thead><tbody>' + lrows + '</tbody></table>';
    }
    if (exams.length) {
      var erows = exams.map(function (e) {
        var concl = e.conclusion || e.describe || "";
        return '<tr data-name="' + _esc(e.item || e.check_type) + '">' +
          "<td>" + _esc(e.date) + "</td>" +
          "<td>" + _esc(e.check_type) +
            (e.item && e.item !== e.check_type ? " · " + _esc(e.item) : "") + "</td>" +
          "<td style='max-width:520px;'>" + _esc(concl) + "</td>" +
          "<td>" + _esc(e.department) + "</td>" +
        "</tr>";
      }).join("");
      sections += '<div class="lab-section-head">检查 · 影像 (' + exams.length + ')</div>' +
        '<table class="data-table"><thead><tr>' +
          '<th>时间</th><th>检查</th><th>结论 / 描述</th><th>科室</th>' +
        '</tr></thead><tbody>' + erows + '</tbody></table>';
    }
    if (!sections) {
      sections = '<table class="data-table"><tbody><tr>' +
        '<td class="muted">(无检验/检查记录)</td></tr></tbody></table>';
    }
    return '<div class="tabpanel" data-panel="labs"' + (hidden ? " hidden" : "") + '>' + sections + '</div>';
  }

  // ---------- 原始病历 modal (全量浏览入口, 保留) ----------
  window.showRawData = function (patientId) {
    var root = document.getElementById("modal-root") || document.body;
    fetchRaw(patientId).then(function (data) {
      root.innerHTML =
        '<div class="modal-overlay" onclick="if(event.target===this)closeModal()">' +
        '<div class="modal raw-modal" style="max-width:1040px;">' +
          '<button class="close-btn" onclick="closeModal()" title="关闭 (Esc)">×</button>' +
          '<h2>' + _esc(patientId) + ' · 原始病历</h2>' +
          '<p class="muted">主诊: ' + _esc(data.main_diagnosis || "—") + '</p>' +
          '<div class="modal-tabs" role="tablist">' +
            '<button type="button" class="tab active" data-tab="notes" onclick="switchTab(this,\'notes\')">' +
              '文书 (' + (data.n_notes || 0) + ' 段)</button>' +
            '<button type="button" class="tab" data-tab="fees" onclick="switchTab(this,\'fees\')">' +
              '费用 (' + (data.n_fees || 0) + ')</button>' +
            '<button type="button" class="tab" data-tab="labs" onclick="switchTab(this,\'labs\')">' +
              '检验记录 (' + ((data.n_labs || 0) + (data.n_exams || 0)) + ')</button>' +
          '</div>' +
          '<div class="modal-search src-search">' +
            '<input type="text" class="src-search-input" placeholder="搜索 (Ctrl+F / ⌘F) — 当前 tab 内高亮跳转" ' +
              'oninput="onSearchInput(this)" onkeydown="onSearchKey(event)">' +
            '<span class="match-count">0 / 0</span>' +
            '<button type="button" onclick="jumpMatch(-1)" title="上一个 (Shift+Enter)">↑</button>' +
            '<button type="button" onclick="jumpMatch(1)" title="下一个 (Enter)">↓</button>' +
            '<button type="button" onclick="clearSearch(this)" title="清空 (Esc)">×</button>' +
          '</div>' +
          '<div class="source-hint" hidden></div>' +
          '<div class="modal-body src-scope">' +
            _notesPanelHtml(data) + _feesPanelHtml(data, true) + _labsPanelHtml(data, true) + '</div>' +
        '</div></div>';
      setTimeout(function () {
        var inp = document.querySelector(".raw-modal .src-search-input");
        if (inp) inp.focus();
      }, 0);
    }).catch(function (e) { alert("加载原始数据失败: " + e.message); });
  };

  window.closeModal = function () {
    var root = document.getElementById("modal-root");
    if (root) root.innerHTML = "";
  };

  // =========================================================
  // 右侧原文同步面板 — 对照模式 (Cursor 式) (D7)
  // =========================================================
  function _ensureSourcePanel() {
    var panel = document.getElementById("source-panel");
    if (panel) return panel;
    panel = document.createElement("aside");
    panel.id = "source-panel";
    panel.className = "source-panel";
    document.body.appendChild(panel);
    return panel;
  }

  window.openSourcePanel = function (anchor) {
    if (!anchor) return;
    var pid = window.JAVERT_PATIENT;
    if (!pid) return;
    // 始终用右侧滑出对照面板 (parallel, 不 cover 原页面; 原违规卡片保留可左右对比).
    // 不再退回 modal — modal 会盖住整页且有渲染竞态.
    var panel = _ensureSourcePanel();
    fetchRaw(pid).then(function (data) {
      panel.innerHTML =
        '<div class="source-head">' +
          '<strong>' + _esc(pid) + ' · 原文对照</strong>' +
          '<button class="close-btn" onclick="closeSourcePanel()" title="关闭 (Esc)">×</button>' +
        '</div>' +
        '<div class="modal-tabs" role="tablist">' +
          '<button type="button" class="tab" data-tab="notes" onclick="switchTab(this,\'notes\')">文书 (' + (data.n_notes || 0) + ')</button>' +
          '<button type="button" class="tab" data-tab="fees" onclick="switchTab(this,\'fees\')">费用 (' + (data.n_fees || 0) + ')</button>' +
          '<button type="button" class="tab" data-tab="labs" onclick="switchTab(this,\'labs\')">检验记录 (' + ((data.n_labs || 0) + (data.n_exams || 0)) + ')</button>' +
        '</div>' +
        '<div class="modal-search src-search">' +
          '<input type="text" class="src-search-input" placeholder="搜索当前 tab" oninput="onSearchInput(this)" onkeydown="onSearchKey(event)">' +
          '<span class="match-count">0 / 0</span>' +
          '<button type="button" onclick="jumpMatch(-1)" title="上一个">↑</button>' +
          '<button type="button" onclick="jumpMatch(1)" title="下一个">↓</button>' +
        '</div>' +
        '<div class="source-hint" hidden></div>' +
        '<div class="source-body src-scope">' +
          _notesPanelHtml(data) + _feesPanelHtml(data, true) + _labsPanelHtml(data, true) + '</div>';
      document.body.classList.add("compare-open");
      _applyAnchorInScope(panel, anchor);
    }).catch(function (e) { showToast("加载原文失败: " + e.message); });
  };

  window.closeSourcePanel = function () {
    var panel = document.getElementById("source-panel");
    document.body.classList.remove("compare-open");
    _hlClearAll();
    if (panel) panel.innerHTML = "";
  };

  function _applyAnchorInScope(rootEl, anchor) {
    if (!rootEl) return;
    // labs/exams → 合并的「检验记录」tab (data-tab="labs"); 旧缓存 exam 锚点亦归并到此
    var tab = anchor.tab;
    if (tab === "labs" || tab === "exams") tab = "labs";
    else if (tab !== "fees") tab = "notes";
    // 切到对应 tab
    var btn = rootEl.querySelector('.tab[data-tab="' + tab + '"]');
    if (btn) switchTab(btn, tab);
    var scope = rootEl.querySelector(".src-scope");
    var hint = rootEl.querySelector(".source-hint");
    if (hint) { hint.hidden = true; hint.textContent = ""; }
    // 先滚到 subsection 那段 (若有) — 该段若在折叠桶内, 先展开桶
    if (anchor.subsection) {
      var row = scope && scope.querySelector('[data-subsection="' + _cssEscape(anchor.subsection) + '"]');
      if (row) {
        var det = row.closest && row.closest("details");
        if (det) det.open = true;
        row.scrollIntoView({block: "center", behavior: "smooth"});
      }
    }
    // 未能精确定位 (锚点解析失败) — 给提示, 让专家人工核对
    if (anchor.unresolved) {
      if (hint) { hint.hidden = false; hint.textContent = "未能精确定位到原文片段, 已切到对应页签, 请人工核对。"; }
      return;
    }
    // 无 query (「查阅文书原文」按钮入口) — 仅滚到该 tab 顶部, 不报错 (6.3)
    if (!anchor.query) {
      if (scope) scope.scrollTop = 0;
      return;
    }
    // 用 query 高亮 (居中) — 命中名常带品牌/规格/剂型噪音, 与费用行逐字不一致, 走分级兜底
    var inp = rootEl.querySelector(".src-search-input");
    if (inp) inp.value = anchor.query;
    var countEl = rootEl.querySelector(".match-count");
    var used = _highlightWithFallback(scope, anchor.query, countEl);
    if (used && used !== anchor.query && inp) inp.value = used;
    if (!used && hint) {
      hint.hidden = false;
      hint.textContent = "原文未找到「" + anchor.query + "」(可能 ETL 摘录与原文有差异), 已切到对应页签。";
    }
  }

  function _highlightCore(q) {
    // 项目/药名核心: 截到第一个括号 (半/全角 ( （ 【 「) 之前 + 去两端空格 (剥品牌/集采标记).
    var s = String(q == null ? "" : q);
    var i = s.search(/[(（【「]/);
    if (i > 0) s = s.slice(0, i);
    return s.trim();
  }

  // 分级高亮兜底 (不改后端锚点, 老缓存的整名 query 也能命中):
  //   1) 整名原样;  2) 核心 (第一个括号前, 剥品牌);
  //   3) 核心逐步去尾找最长可命中前缀 (剥剂型差异, 如肠溶胶囊↔混悬液 → 留活性成分 "布地奈德"), 下限 4 字防过泛.
  // 返回实际命中的 query 串, 都不中返回 null (runHighlight 最后一次已清空为 0/0).
  function _highlightWithFallback(scope, query, countEl) {
    if (runHighlight(scope, query, countEl)) return query;
    var core = _highlightCore(query);
    for (var len = core.length; len >= 4; len--) {
      var sub = core.slice(0, len);
      if (sub === query) continue;
      if (runHighlight(scope, sub, countEl)) return sub;
    }
    return null;
  }

  function _cssEscape(s) {
    return String(s == null ? "" : s).replace(/["\\]/g, "\\$&");
  }

  // hit-item 点击委托 → 解析 data-anchor → openSourcePanel
  function bindHitItems() {
    document.addEventListener("click", function (ev) {
      var el = ev.target.closest ? ev.target.closest(".hit-item") : null;
      if (!el) return;
      var raw = el.getAttribute("data-anchor");
      if (!raw) return;
      var anchor;
      try { anchor = JSON.parse(raw); } catch (_) { return; }
      openSourcePanel(anchor);
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      var el = document.activeElement;
      if (!el || !el.classList || !el.classList.contains("hit-item")) return;
      ev.preventDefault();
      var raw = el.getAttribute("data-anchor");
      if (!raw) return;
      try { openSourcePanel(JSON.parse(raw)); } catch (_) {}
    });
  }

  // =========================================================
  // 细类分组导航: chip 点击滚到组 + 只看不明 toggle (D5)
  // =========================================================
  window.scrollToGroup = function (anchorId) {
    var el = document.getElementById(anchorId);
    if (!el) return;
    if (el.tagName === "DETAILS") el.open = true;  // 折叠的组先展开再滚
    el.scrollIntoView({behavior: "smooth", block: "start"});
    $$(".vt-chip").forEach(function (c) {
      c.classList.toggle("active", c.getAttribute("data-target") === anchorId);
    });
  };

  window.toggleInconclusiveOnly = function (cb) {
    // 纯 CSS class: body.only-i 隐藏 verdict-v 卡片 + 无不明的组 (与 server filter 正交, 可逆)
    document.body.classList.toggle("only-i", !!(cb && cb.checked));
    refreshGroupVisibility();
  };

  // add-verdict-gate-layer: 缺文书 facet — body.hide-missing-doc 隐藏 gate_tag=缺文书 卡片.
  // 默认隐藏 (checkbox checked), 与 only-i / server verdict filter 正交叠加, 可逆翻看.
  window.toggleHideMissingDoc = function (cb) {
    document.body.classList.toggle("hide-missing-doc", !!(cb && cb.checked));
    refreshGroupVisibility();
  };

  // recover-deterministic-recall 3.3: 「只看被闸降级」facet — body.show-gated-only 隐藏
  // gate_tag 为空 (未被闸降级) 的卡片, 专家可抽查确定性 gate 的击杀 (降级标签 + LLM 原始推理).
  // 与 verdict filter / 缺文书 / only-i 正交叠加, 默认关.
  window.toggleGatedOnly = function (cb) {
    document.body.classList.toggle("show-gated-only", !!(cb && cb.checked));
    refreshGroupVisibility();
  };

  // 默认只看「违规」卡片: 不明 / 干净卡片默认隐藏 (body.hide-inconclusive / hide-clean),
  // 勾选 toggle 才显示. 偏好 localStorage 记住 (默认隐藏, 开过就保持开).
  window.toggleShowInconclusive = function (cb) {
    var show = !!(cb && cb.checked);
    document.body.classList.toggle("hide-inconclusive", !show);
    try { localStorage.setItem("javert_show_i", show ? "1" : "0"); } catch (_) {}
    refreshGroupVisibility();
  };
  window.toggleShowClean = function (cb) {
    var show = !!(cb && cb.checked);
    document.body.classList.toggle("hide-clean", !show);
    try { localStorage.setItem("javert_show_c", show ? "1" : "0"); } catch (_) {}
    refreshGroupVisibility();
  };

  // 整组无可见卡片时收起该组 (避免空的细类组标题) + 全隐藏时给提示.
  // getComputedStyle 已综合所有 body.* 隐藏规则 (verdict / only-i / 缺文书), 单一真相源.
  function refreshGroupVisibility() {
    var groups = $$(".vt-group");
    if (!groups.length) return;
    var anyVisible = false;
    groups.forEach(function (g) {
      var visible = $$(".violation-card", g).some(function (c) {
        return window.getComputedStyle(c).display !== "none";
      });
      g.style.display = visible ? "" : "none";
      if (visible) anyVisible = true;
    });
    var hint = document.getElementById("all-hidden-hint");
    if (hint) hint.hidden = anyVisible;
  }

  // patient detail 进页面初始化: 应用「隐藏缺文书」默认 + verdict 默认隐藏不明/干净 (含 localStorage 偏好).
  function initVerdictFilter() {
    if (!document.getElementById("vt-chip-row")) return;  // 仅 patient detail
    var missCb = document.getElementById("hide-missing-doc");
    if (missCb) document.body.classList.toggle("hide-missing-doc", !!missCb.checked);
    var showI = false, showC = false;
    try {
      showI = localStorage.getItem("javert_show_i") === "1";
      showC = localStorage.getItem("javert_show_c") === "1";
    } catch (_) {}
    document.body.classList.toggle("hide-inconclusive", !showI);
    document.body.classList.toggle("hide-clean", !showC);
    var cbI = document.getElementById("show-inconclusive"); if (cbI) cbI.checked = showI;
    var cbC = document.getElementById("show-clean"); if (cbC) cbC.checked = showC;
    refreshGroupVisibility();
  }

  // =========================================================
  // 共享高亮引擎 (modal + 对照面板共用; D7 抽成可复用模块)
  // =========================================================
  var _hl = {matches: [], idx: -1, scope: null, countEl: null};
  var _searchTimer = null;

  function _activeTabpanel(scope) {
    return scope ? scope.querySelector('.tabpanel:not([hidden])') : null;
  }

  function _hlClearIn(panel) {
    if (!panel) return;
    $$("mark.search-hit", panel).forEach(function (m) {
      var parent = m.parentNode;
      while (m.firstChild) parent.insertBefore(m.firstChild, m);
      parent.removeChild(m);
      parent.normalize();
    });
  }

  function _hlClearAll() {
    if (_hl.scope) _hlClearIn(_activeTabpanel(_hl.scope));
    _hl.matches = []; _hl.idx = -1; _hl.scope = null; _hl.countEl = null;
  }

  // 在 scope 内高亮 query; 返回是否有命中
  function runHighlight(scope, query, countEl) {
    if (_hl.scope) _hlClearIn(_activeTabpanel(_hl.scope));
    _hl.scope = scope; _hl.countEl = countEl; _hl.matches = []; _hl.idx = -1;
    var panel = _activeTabpanel(scope);
    if (!panel || !query) { _setCount(); return false; }
    var q = query.toLowerCase();
    var walker = document.createTreeWalker(panel, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        if (!n.nodeValue || !n.parentNode) return NodeFilter.FILTER_REJECT;
        var pn = n.parentNode.nodeName;
        if (pn === "SCRIPT" || pn === "STYLE" || pn === "MARK") return NodeFilter.FILTER_REJECT;
        return n.nodeValue.toLowerCase().indexOf(q) >= 0
          ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    var toReplace = [];
    while (walker.nextNode()) toReplace.push(walker.currentNode);
    toReplace.forEach(function (node) {
      var text = node.nodeValue, lower = text.toLowerCase();
      var frag = document.createDocumentFragment(), i = 0;
      while (i < text.length) {
        var hit = lower.indexOf(q, i);
        if (hit < 0) { frag.appendChild(document.createTextNode(text.slice(i))); break; }
        if (hit > i) frag.appendChild(document.createTextNode(text.slice(i, hit)));
        var mark = document.createElement("mark");
        mark.className = "search-hit";
        mark.textContent = text.slice(hit, hit + q.length);
        frag.appendChild(mark);
        i = hit + q.length;
      }
      node.parentNode.replaceChild(frag, node);
    });
    _hl.matches = $$("mark.search-hit", panel);
    _hl.idx = _hl.matches.length > 0 ? 0 : -1;
    _setCount();
    if (_hl.idx >= 0) _scrollToCurrent();
    return _hl.matches.length > 0;
  }

  function _setCount() {
    if (!_hl.countEl) return;
    if (_hl.matches.length === 0) { _hl.countEl.textContent = "0 / 0"; return; }
    _hl.countEl.textContent = (_hl.idx + 1) + " / " + _hl.matches.length;
    _hl.matches.forEach(function (m, i) { m.classList.toggle("current", i === _hl.idx); });
  }

  function _scrollToCurrent() {
    if (_hl.idx < 0 || _hl.idx >= _hl.matches.length) return;
    _hl.matches[_hl.idx].scrollIntoView({block: "center", behavior: "smooth"});
  }

  window.jumpMatch = function (dir) {
    if (_hl.matches.length === 0) return;
    _hl.idx = (_hl.idx + dir + _hl.matches.length) % _hl.matches.length;
    _setCount();
    _scrollToCurrent();
  };

  // tab 切换 (modal + 面板共用): btn 是被点的 tab 按钮
  window.switchTab = function (btn, name) {
    var container = btn.closest(".raw-modal") || btn.closest("#source-panel");
    if (!container) return;
    $$(".modal-tabs .tab", container).forEach(function (t) {
      t.classList.toggle("active", t.getAttribute("data-tab") === name);
    });
    var scope = container.querySelector(".src-scope");
    $$(".tabpanel", scope).forEach(function (p) {
      p.hidden = (p.getAttribute("data-panel") !== name);
    });
    var inp = container.querySelector(".src-search-input");
    var q = inp ? (inp.value || "").trim() : "";
    runHighlight(scope, q, container.querySelector(".match-count"));
  };

  window.onSearchInput = function (inp) {
    if (!inp) return;
    var container = inp.closest(".raw-modal") || inp.closest("#source-panel");
    var scope = container && container.querySelector(".src-scope");
    var countEl = container && container.querySelector(".match-count");
    var q = inp.value || "";
    if (_searchTimer) clearTimeout(_searchTimer);
    _searchTimer = setTimeout(function () { runHighlight(scope, q.trim(), countEl); }, 120);
  };

  window.onSearchKey = function (ev) {
    if (ev.key === "Enter") {
      ev.preventDefault();
      jumpMatch(ev.shiftKey ? -1 : 1);
    } else if (ev.key === "Escape") {
      ev.preventDefault();
      // 阻止冒泡到全局 keydown — 否则同一次 Esc 既清空又关闭 (两段式 Esc 失效)
      ev.stopPropagation();
      if (ev.target.value) { clearSearch(ev.target); }
      else if (ev.target.closest(".raw-modal")) { closeModal(); }
      else { closeSourcePanel(); }
    }
  };

  window.clearSearch = function (btnOrInp) {
    var container = btnOrInp && (btnOrInp.closest(".raw-modal") || btnOrInp.closest("#source-panel"));
    var inp = container && container.querySelector(".src-search-input");
    if (inp) inp.value = "";
    _hlClearAll();
    var cnt = container && container.querySelector(".match-count");
    if (cnt) cnt.textContent = "0 / 0";
  };

  // 全局 Ctrl+F / ⌘F + Esc: modal 或对照面板打开时拦截
  document.addEventListener("keydown", function (ev) {
    var ctx = document.querySelector(".modal-overlay .raw-modal") || document.getElementById("source-panel");
    if (!ctx || (ctx.id === "source-panel" && !document.body.classList.contains("compare-open"))) return;
    var key = ev.key.toLowerCase();
    if ((ev.ctrlKey || ev.metaKey) && key === "f") {
      ev.preventDefault();
      var inp = ctx.querySelector(".src-search-input");
      if (inp) { inp.focus(); inp.select(); }
    } else if (key === "escape") {
      if (document.activeElement && document.activeElement.classList
          && document.activeElement.classList.contains("src-search-input")
          && document.activeElement.value) return;
      ev.preventDefault();
      if (document.querySelector(".modal-overlay")) closeModal();
      else closeSourcePanel();
    }
  });

  // =========================================================
  // 病人列表 facet — 纯前端 show/hide, 与服务端 verdict filter 叠加 (D5)
  // =========================================================
  var FACET_KEY = "javert_facets";

  function _facetState() {
    return {
      pid: ((document.getElementById("facet-pid") || {}).value || "").trim(),
      dx: ((document.getElementById("facet-dx") || {}).value || "").trim(),
      fee: (document.getElementById("facet-fee") || {}).value || "all",
      time: (document.getElementById("facet-time") || {}).value || "all",
      sort: (document.getElementById("facet-sort") || {}).value || "default",
      tags: $$(".facet-chip.active").map(function (c) { return c.getAttribute("data-tag"); }),
    };
  }

  // 病人列表排序 — 纯前端重排 DOM 节点 (金额 / 生成时间, 顺逆序); 与 facet 筛选叠加.
  // data-fees / data-updated 已由 sidebar 渲染; "默认" 用首次记录的服务端原始序回退.
  function applySort() {
    var list = document.getElementById("patient-list");
    if (!list) return;
    var cards = $$(".patient-card", list);
    if (!cards.length) return;
    if (!cards[0].hasAttribute("data-orig-idx")) {
      cards.forEach(function (c, i) { c.setAttribute("data-orig-idx", i); });
    }
    var mode = (document.getElementById("facet-sort") || {}).value || "default";
    var cmp;
    if (mode === "fee_desc" || mode === "fee_asc") {
      cmp = function (a, b) {
        var d = (parseFloat(a.getAttribute("data-fees") || "0") || 0) -
                (parseFloat(b.getAttribute("data-fees") || "0") || 0);
        return mode === "fee_desc" ? -d : d;
      };
    } else if (mode === "time_desc" || mode === "time_asc") {
      cmp = function (a, b) {
        var d = (Date.parse(a.getAttribute("data-updated") || "") || 0) -
                (Date.parse(b.getAttribute("data-updated") || "") || 0);
        return mode === "time_desc" ? -d : d;
      };
    } else {
      cmp = function (a, b) {
        return (+a.getAttribute("data-orig-idx")) - (+b.getAttribute("data-orig-idx"));
      };
    }
    cards.sort(cmp);
    cards.forEach(function (c) { list.appendChild(c); });
  }

  function _saveFacets(st) {
    try { sessionStorage.setItem(FACET_KEY, JSON.stringify(st)); } catch (_) {}
  }

  function _inTimeWindow(d, preset, now) {
    if (!d || isNaN(d.getTime())) return false;
    if (preset === "today") {
      var start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      return d >= start;
    }
    if (preset === "week") return (now - d) <= 7 * 864e5;   // 近 7 天
    if (preset === "month") return (now - d) <= 30 * 864e5; // 近 30 天
    return true;
  }

  window.applyFacets = function () {
    var st = _facetState();
    _saveFacets(st);
    var active = (st.pid || st.dx || st.fee !== "all" || st.time !== "all" || st.tags.length > 0);
    var clearBtn = document.getElementById("facet-clear");
    if (clearBtn) clearBtn.hidden = !active;

    var pidq = st.pid.toLowerCase();
    var dxq = st.dx.toLowerCase();
    var now = new Date();
    var cards = $$(".patient-card");
    var shown = 0;
    cards.forEach(function (card) {
      var ok = true;
      if (pidq) {
        var pid = (card.getAttribute("data-patient-id") || "").toLowerCase();
        if (pid.indexOf(pidq) < 0) ok = false;
      }
      if (ok && dxq) {
        var dx = (card.getAttribute("data-dx") || "").toLowerCase();
        if (dx.indexOf(dxq) < 0) ok = false;
      }
      if (ok && st.fee !== "all") {
        var fee = parseFloat(card.getAttribute("data-fees") || "0") || 0;
        if (st.fee === "lt1") ok = fee < 10000;
        else if (st.fee === "1to5") ok = (fee >= 10000 && fee <= 50000);
        else if (st.fee === "gt5") ok = fee > 50000;
      }
      if (ok && st.tags.length) {
        ok = st.tags.indexOf(card.getAttribute("data-tag") || "") >= 0;
      }
      if (ok && st.time !== "all") {
        var u = card.getAttribute("data-updated") || "";
        ok = _inTimeWindow(u ? new Date(u) : null, st.time, now);
      }
      card.style.display = ok ? "" : "none";
      if (ok) shown++;
    });
    var pc = document.getElementById("patient-count");
    if (pc) pc.textContent = shown === cards.length ? cards.length : (shown + "/" + cards.length);
    var empty = document.getElementById("facet-empty");
    if (empty) empty.hidden = !(cards.length > 0 && shown === 0);
    applySort();  // 筛完重排 (排序与筛选正交叠加)
  };

  window.toggleTagChip = function (btn) {
    btn.classList.toggle("active");
    applyFacets();
  };

  window.clearFacets = function () {
    var pid = document.getElementById("facet-pid"); if (pid) pid.value = "";
    var dx = document.getElementById("facet-dx"); if (dx) dx.value = "";
    var fee = document.getElementById("facet-fee"); if (fee) fee.value = "all";
    var time = document.getElementById("facet-time"); if (time) time.value = "all";
    $$(".facet-chip.active").forEach(function (c) { c.classList.remove("active"); });
    try { sessionStorage.removeItem(FACET_KEY); } catch (_) {}
    applyFacets();
  };

  function restoreFacets() {
    if (!document.getElementById("facet-bar")) return;
    var st = null;
    try { st = JSON.parse(sessionStorage.getItem(FACET_KEY) || "null"); } catch (_) {}
    if (st) {
      var pid = document.getElementById("facet-pid"); if (pid && st.pid) pid.value = st.pid;
      var dx = document.getElementById("facet-dx"); if (dx && st.dx) dx.value = st.dx;
      var fee = document.getElementById("facet-fee"); if (fee && st.fee) fee.value = st.fee;
      var time = document.getElementById("facet-time"); if (time && st.time) time.value = st.time;
      var sort = document.getElementById("facet-sort"); if (sort && st.sort) sort.value = st.sort;
      (st.tags || []).forEach(function (t) {
        var chip = document.querySelector('.facet-chip[data-tag="' + t + '"]');
        if (chip) chip.classList.add("active");
      });
    }
    applyFacets();
  }

  // =========================================================
  // SSE 订阅
  // =========================================================
  function startSse() {
    if (typeof EventSource === "undefined") return;
    var es;
    try { es = new EventSource("/sse/reviews"); }
    catch (e) { return; }

    es.addEventListener("review_submitted", function (e) {
      try {
        var data = JSON.parse(e.data);
        // Sidebar 计数同步 (全局口径): 某 run 被「任意专家」首次审 (run_first_review) +
        // 该 run 的 AI 裁决落在当前 filter 命中集 → 进度 +1. 不分审核人 — 别的专家审完,
        // 我这边的卡片也实时 +1. run_first_review 保证改判 / 二次审不重复计数.
        if (data.run_first_review && data.patient_id
            && _isRelevantVerdict(data.run_verdict, _activeFilterMode())) {
          var sb = $('[data-patient-id="' + data.patient_id + '"]');
          if (sb) {
            var reviewed = parseInt(sb.getAttribute("data-reviewed") || "0", 10) + 1;
            var relevant = parseInt(sb.getAttribute("data-relevant") || "0", 10);
            sb.setAttribute("data-reviewed", reviewed);
            var pt = sb.querySelector(".progress-text");
            if (pt) pt.textContent = "已审 " + reviewed + "/" + relevant;
            if (relevant > 0 && reviewed >= relevant) {
              sb.classList.add("fully-reviewed");
              if (pt) pt.classList.add("progress-full");
              var nameSpan = sb.querySelector(".row1 span");
              if (nameSpan && !nameSpan.querySelector(".check-icon")) {
                var icon = document.createElement("span");
                icon.className = "check-icon";
                nameSpan.insertBefore(icon, nameSpan.firstChild);
              }
            }
          }
        }
        var card = $('[data-run-id="' + data.run_id + '"]');
        if (card) {
          var ul = card.querySelector(".others-reviews") || (function () {
            var d = document.createElement("div");
            d.className = "others-reviews";
            d.innerHTML = '<div style="font-weight:600; color:var(--text); margin-bottom:4px;">其他专家审核</div>';
            card.appendChild(d);
            return d;
          })();
          var existing = ul.querySelector('[data-reviewer="' + data.reviewer_username + '"]');
          var html = '<span class="badge badge-' + verdictColor(data.verdict) + '">' +
                     verdictLabel(data.verdict) + '</span> ' +
                     '<strong>' + (data.reviewer_display_name || data.reviewer_username) + '</strong>' +
                     ' <span class="muted">· 刚刚</span>';
          if (existing) {
            existing.innerHTML = html;
          } else {
            var row = document.createElement("div");
            row.className = "other-row";
            row.setAttribute("data-reviewer", data.reviewer_username);
            row.innerHTML = html;
            ul.appendChild(row);
          }
        }
        showToast(data.reviewer_username + " 已审 " + (data.run_id || ""), "review");
      } catch (_) {}
    });

    es.addEventListener("new_audit_run", function (e) {
      try {
        var data = JSON.parse(e.data);
        showToast("新增审计: " + data.patient_id + " " + data.rule_id + " → " + verdictLabel(data.verdict));
        var list = document.getElementById("patient-list");
        if (!list) return;
        var existing = list.querySelector('[data-patient-id="' + data.patient_id + '"]');
        if (existing) {
          // 更新 badge
          var vKey = verdictColor(data.verdict);
          var attr = vKey === "v" ? "data-v-count" : vKey === "i" ? "data-i-count" : "data-c-count";
          var cur = parseInt(existing.getAttribute(attr) || "0", 10);
          existing.setAttribute(attr, cur + 1);
          var rel = parseInt(existing.getAttribute("data-relevant") || "0", 10);
          existing.setAttribute("data-relevant", rel + 1);
          // 重渲染 badges + progress 略简: 刷新 row2 innerHTML
          var row2 = existing.querySelector(".row2");
          if (row2) {
            var vC = parseInt(existing.getAttribute("data-v-count"), 10);
            var iC = parseInt(existing.getAttribute("data-i-count"), 10);
            var cC = parseInt(existing.getAttribute("data-c-count"), 10);
            var rev = existing.getAttribute("data-reviewed");
            row2.innerHTML =
              (vC > 0 ? '<span class="badge badge-v">' + vC + 'V</span>' : '') +
              (iC > 0 ? '<span class="badge badge-i">' + iC + 'I</span>' : '') +
              '<span class="progress-text">已审 ' + rev + '/' + (vC + iC) + '</span>';
          }
        } else if (data.is_new_patient) {
          // prepend 新卡片
          var a = document.createElement("a");
          a.className = "patient-card";
          a.setAttribute("data-patient-id", data.patient_id);
          a.setAttribute("data-v-count", verdictColor(data.verdict) === "v" ? 1 : 0);
          a.setAttribute("data-i-count", verdictColor(data.verdict) === "i" ? 1 : 0);
          a.setAttribute("data-c-count", verdictColor(data.verdict) === "c" ? 1 : 0);
          a.setAttribute("data-reviewed", 0);
          a.setAttribute("data-relevant", 1);
          a.href = "/workbench/" + data.patient_id;
          a.innerHTML =
            '<div class="row1"><span>' + data.patient_id + '</span></div>' +
            '<div class="row2"><span class="badge badge-' + verdictColor(data.verdict) + '">1' +
            (verdictColor(data.verdict).toUpperCase()) +
            '</span><span class="progress-text">已审 0/1</span></div>';
          list.insertBefore(a, list.firstChild);
        }
      } catch (_) {}
    });

    es.addEventListener("heartbeat", function () { /* keepalive */ });
    es.onerror = function () { /* 浏览器会自动重连 */ };

    window.addEventListener("beforeunload", function () { es.close(); });
  }

  // =========================================================
  // 锚点跳转 — dashboard 下钻到 #run_aud_xxx 时滚到对应卡片 + 高亮一下
  // =========================================================
  function scrollToHashRun() {
    var hash = location.hash || "";
    if (hash.length < 5 || hash.slice(0, 5) !== "#run_") return;
    var el = document.getElementById(hash.slice(1));
    if (!el) return;
    el.scrollIntoView({behavior: "smooth", block: "center"});
    el.classList.add("hash-highlight");
    setTimeout(function () { el.classList.remove("hash-highlight"); }, 2500);
  }

  // ── 列表自动刷新兜底 (不靠 SSE/AuditWatcher) ──
  // 仅在病人列表页 (/workbench) 生效, 详情页不刷 (不打断看病人). 轮询 142 max run id;
  // 值变 = 有新裁决 → 防抖等结果稳定再整页刷一次 (facet 走 sessionStorage 会恢复).
  // 142 抖时 sig=None → 不动, 绝不误刷. 跑批时 sig 连变 → 计时器不断重置 → 跑完才刷一次.
  function initAutoRefresh() {
    if (location.pathname.replace(/\/$/, "") !== "/workbench") return;  // 只列表页
    if (!document.getElementById("patient-list")) return;
    var baseSig = null, settleTimer = null;
    function idle() {
      var ae = document.activeElement, t = ae && ae.tagName;
      if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT") return false;  // 用户在筛选
      return true;
    }
    function doReload() {
      if (idle()) location.reload();
      else settleTimer = setTimeout(doReload, 8000);  // 用户在操作 → 稍后再试
    }
    function poll() {
      fetch("/api/workbench/sig", { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          var s = j && j.sig;
          if (s === null || s === undefined) return;     // 142 抖 → 不动
          if (baseSig === null) { baseSig = s; return; }  // 首次 = 基线
          if (s !== baseSig) {                            // 有新裁决
            baseSig = s;
            if (settleTimer) clearTimeout(settleTimer);
            settleTimer = setTimeout(doReload, 18000);    // 防抖: 稳定 18s 再刷
          }
        })
        .catch(function () {});
    }
    setInterval(poll, 12000);
    poll();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      startSse();
      scrollToHashRun();
      restoreFacets();
      bindHitItems();
      initVerdictFilter();
      initAutoRefresh();
    });
  } else {
    startSse();
    scrollToHashRun();
    restoreFacets();
    bindHitItems();
    initVerdictFilter();
    initAutoRefresh();
  }

})();

/* small CSS shim — hide review form when needed via JS toggle */
document.head.insertAdjacentHTML(
  "beforeend",
  "<style>.hidden{display:none !important;}</style>"
);
