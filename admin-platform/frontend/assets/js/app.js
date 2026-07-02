/**
 * ====================================================================
 *  超级管理员系统 · 前端逻辑 (app.js)
 *  - 鉴权检查 / 路由切换 / API 请求封装
 *  - 驾驶舱 / 用户(列表+详情) / 企业(列表+成员) / 任务(富表格) / 财务(流水+订单) / 审计
 * ====================================================================
 */
(function () {
  "use strict";

  var state = {
    view: "dashboard",
    admin: null,
    users: { page: 1, page_size: 20, total: 0, keyword: "", status: "" },
    enterprises: { page: 1, page_size: 20, total: 0, keyword: "", status: "" },
    tasks: { page: 1, page_size: 20, total: 0, keyword: "", platform: "", status: "" },
    transactions: { page: 1, page_size: 50, total: 0, user_id: "", direction: "" },
    orders: { page: 1, page_size: 50, total: 0 },
    audit: { page: 1, page_size: 50, total: 0 },
    errors: { page: 1, page_size: 20, total: 0, keyword: "", platform: "" },
    rechargeTarget: null,
    billingTab: "transactions",
  };

  var PAGE_TITLES = {
    dashboard: { title: "运营驾驶舱", eyebrow: "总览" },
    users: { title: "用户管理", eyebrow: "3级用户" },
    enterprises: { title: "企业管理", eyebrow: "2级企业" },
    tasks: { title: "任务监控", eyebrow: "全平台任务" },
    billing: { title: "计费财务", eyebrow: "财务" },
    pricing: { title: "定价配置", eyebrow: "金豆单价" },
    ai: { title: "AI 资源", eyebrow: "AI" },
    monitoring: { title: "监控中心", eyebrow: "监控" },
    audit: { title: "安全审计", eyebrow: "日志" },
  };

  var PLATFORM_LABEL = { temu: "TEMU", "1688": "1688", ozon: "OZON" };

  /* ── API 封装 ── */
  async function api(path, method, body) {
    var opt = { method: method || "GET", headers: {} };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    var resp = await fetch(path, opt);
    var data = await resp.json().catch(function () { return {}; });
    if (resp.status === 401) { window.location.href = "/"; return null; }
    if (!resp.ok && !data.ok) throw new Error(data.error || ("HTTP " + resp.status));
    return data;
  }

  /* ── Toast ── */
  var toastTimer = null;
  function toast(msg) {
    var el = document.getElementById("toast");
    el.textContent = msg; el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove("show"); }, 2500);
  }

  /* ── 工具 ── */
  function esc(s) {
    if (s == null) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmtTime(s) {
    if (!s) return "";
    return String(s).replace("T", " ").split(".")[0];
  }

  /* ── 视图切换 ── */
  function switchView(name) {
    state.view = name;
    document.querySelectorAll(".view-panel").forEach(function (p) { p.classList.remove("active"); });
    var target = document.getElementById("view-" + name);
    if (target) target.classList.add("active");
    document.querySelectorAll(".side-nav .nav-item").forEach(function (b) {
      b.classList.toggle("active", b.dataset.view === name);
    });
    var meta = PAGE_TITLES[name] || { title: name, eyebrow: "" };
    document.getElementById("pageTitle").textContent = meta.title;
    document.getElementById("pageEyebrow").textContent = meta.eyebrow;
    document.getElementById("backBtn").style.display = "none";
    stopQueuePoll();
    resetSubPanels(name);
    if (name === "dashboard") loadDashboard();
    if (name === "users") loadUsers();
    if (name === "enterprises") loadEnterprises();
    if (name === "tasks") loadTasks();
    if (name === "billing") loadBilling();
    if (name === "pricing") loadPricing();
    if (name === "ai") loadAI();
    if (name === "monitoring") loadMonitoring();
    if (name === "audit") loadAudit();
  }

  function resetSubPanels(view) {
    if (view === "users") {
      show("usersListPanel"); hide("userDetailPanel");
    }
    if (view === "enterprises") {
      show("entListPanel"); hide("entDetailPanel");
    }
    if (view === "tasks") { hide("taskDetailDrawer"); }
    if (view === "monitoring") { monitorTab = "errors"; switchMonitorTab("errors"); }
    if (view === "ai") { switchKeyModelTab("chat"); }
  }
  function show(id) { var e = document.getElementById(id); if (e) e.style.display = ""; }
  function hide(id) { var e = document.getElementById(id); if (e) e.style.display = "none"; }

  /* ══════════════════════════════════════
     运营驾驶舱
     ══════════════════════════════════════ */
  async function loadDashboard() {
    try {
      var d = await api("/api/admin/dashboard/overview");
      if (!d) return;
      document.getElementById("statUsers").textContent = d.users;
      document.getElementById("statEnt").textContent = d.enterprises;
      document.getElementById("statToday").textContent = d.today_imports;
      document.getElementById("statTotal").textContent = d.total_imports;
      document.getElementById("statDone").textContent = d.total_done;
      document.getElementById("statRunning").textContent = d.in_progress;
      document.getElementById("statFailed").textContent = d.total_error;
      document.getElementById("statRecharge").textContent = d.recharge_beans;
      document.getElementById("statConsume").textContent = d.consume_beans;
      loadRanking();
    } catch (e) { toast("加载驾驶舱失败: " + e.message); }
  }

  async function loadRanking() {
    try {
      var d = await api("/api/admin/billing/ranking?limit=10");
      var el = document.getElementById("rankingList");
      if (!el) return;
      var list = (d && d.ranking) || [];
      if (!list.length) { el.innerHTML = '<div class="empty-row">暂无数据</div>'; return; }
      el.innerHTML = list.map(function (r, i) {
        return '<div class="ranking-row">'
          + '<span class="rank-num">' + (i + 1) + '</span>'
          + '<span class="rank-name">' + esc(r.name) + '</span>'
          + '<span class="rank-val">' + (r.consumed || 0) + ' 豆</span>'
          + '</div>';
      }).join("");
    } catch (e) { /* 静默 */ }
  }

  /* ══════════════════════════════════════
     用户管理
     ══════════════════════════════════════ */
  async function loadUsers() {
    var s = state.users;
    try {
      var q = "?page=" + s.page + "&page_size=" + s.page_size
        + "&keyword=" + encodeURIComponent(s.keyword)
        + "&status=" + encodeURIComponent(s.status);
      var d = await api("/api/admin/users" + q);
      if (!d) return;
      renderUsers(d.users, d.total);
    } catch (e) { toast("加载用户失败: " + e.message); }
  }

  function userStatusBadge(u) {
    if (u.is_frozen) return '<span class="tag tag-frozen">冻结</span>';
    if (!u.is_active) return '<span class="tag tag-disabled">禁用</span>';
    return '<span class="tag tag-ok">正常</span>';
  }

  function renderUsers(users, total) {
    var tb = document.getElementById("usersBody");
    state.users.total = total;
    if (!users.length) {
      tb.innerHTML = '<tr><td colspan="10" class="empty-row">暂无数据</td></tr>';
      renderPager("usersPager", state.users, loadUsers); return;
    }
    tb.innerHTML = users.map(function (u) {
      return "<tr>"
        + "<td>" + u.id + "</td>"
        + '<td><a class="link-name" data-action="user-detail" data-uid="' + u.id + '">' + esc(u.account) + "</a></td>"
        + "<td>" + esc(u.uid || "") + "</td>"
        + "<td>" + esc(u.display_name) + "</td>"
        + "<td>" + esc(u.enterprise_name || "-") + "</td>"
        + "<td>" + (u.beans != null ? u.beans : "-") + "</td>"
        + "<td>" + (u.import_count || 0) + "</td>"
        + "<td>" + userStatusBadge(u) + "</td>"
        + "<td>" + esc(u.created_at) + "</td>"
        + '<td class="action-cell">' + userActions(u) + "</td>"
        + "</tr>";
    }).join("");
    renderPager("usersPager", state.users, loadUsers);
  }

  function userActions(u) {
    var b = [];
    b.push('<button class="mini-btn" data-action="recharge" data-uid="' + u.id + '" data-name="' + esc(u.account) + '">充值</button>');
    if (u.is_frozen) b.push('<button class="mini-btn" data-action="unfreeze" data-uid="' + u.id + '">解冻</button>');
    else b.push('<button class="mini-btn warn" data-action="freeze" data-uid="' + u.id + '">冻结</button>');
    if (u.is_active) b.push('<button class="mini-btn warn" data-action="disable" data-uid="' + u.id + '">禁用</button>');
    else b.push('<button class="mini-btn" data-action="enable" data-uid="' + u.id + '">启用</button>');
    return b.join("");
  }

  /* ── 用户详情 ── */
  async function showUserDetail(uid) {
    try {
      var d = await api("/api/admin/users/" + uid);
      if (!d) return;
      var u = d.user;
      hide("usersListPanel"); show("userDetailPanel");
      document.getElementById("backBtn").style.display = "";
      var html = '<div class="detail-grid">'
        + detailField("用户ID", u.id)
        + detailField("手机号", u.account)
        + detailField("UID", u.uid || "-")
        + detailField("昵称", u.display_name || "-")
        + detailField("金豆余额", u.beans)
        + detailField("角色", u.role)
        + detailField("企业", u.enterprise_name || "-")
        + detailField("状态", u.is_frozen ? "已冻结" : (u.is_active ? "正常" : "已禁用"))
        + detailField("注册时间", u.created_at)
        + detailField("更新时间", u.updated_at)
        + "</div>";
      html += '<h3 class="section-title">最近任务</h3>';
      var tasks = u.recent_tasks || [];
      if (tasks.length) {
        html += '<div class="table-wrap"><table class="data-table"><thead><tr>'
          + '<th>ID</th><th>标题</th><th>平台</th><th>状态</th><th>状态信息</th><th>时间</th>'
          + '</tr></thead><tbody>';
        html += tasks.map(function (t) {
          return "<tr><td>" + t.id + "</td><td>" + esc(t.title || "-") + "</td>"
            + "<td>" + esc(t.platform) + "</td>"
            + '<td><span class="tag ' + taskTagCls(t.status) + '">' + esc(t.status) + "</span></td>"
            + "<td>" + esc((t.status_msg || "").slice(0, 50)) + "</td>"
            + "<td>" + esc(t.created_at) + "</td></tr>";
        }).join("");
        html += '</tbody></table></div>';
      } else {
        html += '<div class="empty-row">暂无任务</div>';
      }
      html += '<h3 class="section-title">金豆流水</h3>';
      var txs = u.recent_transactions || [];
      if (txs.length) {
        html += '<div class="table-wrap"><table class="data-table"><thead><tr>'
          + '<th>金额</th><th>余额</th><th>原因</th><th>时间</th>'
          + '</tr></thead><tbody>';
        html += txs.map(function (t) {
          var cls = t.amount >= 0 ? "tag-ok" : "tag-frozen";
          return "<tr><td><span class='tag " + cls + "'>" + (t.amount >= 0 ? "+" : "") + t.amount + "</span></td>"
            + "<td>" + t.balance_after + "</td><td>" + esc(t.reason) + "</td><td>" + esc(t.created_at) + "</td></tr>";
        }).join("");
        html += '</tbody></table></div>';
      } else {
        html += '<div class="empty-row">暂无流水</div>';
      }
      document.getElementById("userDetailBody").innerHTML = html;
    } catch (e) { toast("加载用户详情失败: " + e.message); }
  }

  function detailField(label, val) {
    return '<div class="detail-field"><span class="df-label">' + label + '</span><span class="df-value">' + esc(val) + '</span></div>';
  }

  /* ══════════════════════════════════════
     企业管理 + 成员
     ══════════════════════════════════════ */
  async function loadEnterprises() {
    var s = state.enterprises;
    try {
      var q = "?page=" + s.page + "&page_size=" + s.page_size
        + "&keyword=" + encodeURIComponent(s.keyword)
        + "&status=" + encodeURIComponent(s.status);
      var d = await api("/api/admin/enterprises" + q);
      if (!d) return;
      renderEnterprises(d.enterprises, d.total);
    } catch (e) { toast("加载企业失败: " + e.message); }
  }

  function renderEnterprises(items, total) {
    var tb = document.getElementById("entBody");
    state.enterprises.total = total;
    if (!items.length) {
      tb.innerHTML = '<tr><td colspan="9" class="empty-row">暂无数据</td></tr>';
      renderPager("entPager", state.enterprises, loadEnterprises); return;
    }
    tb.innerHTML = items.map(function (e) {
      var st = '<span class="tag ' + (e.is_frozen ? "tag-frozen" : "tag-ok") + '">' + (e.is_frozen ? "冻结" : esc(e.status)) + "</span>";
      return "<tr>"
        + "<td>" + e.id + "</td>"
        + '<td><a class="link-name" data-action="ent-detail" data-eid="' + e.id + '">' + esc(e.name) + "</a></td>"
        + "<td>" + esc(e.plan_type || "free") + "</td>"
        + "<td>" + (e.member_count || 0) + "</td>"
        + "<td>" + (e.import_count || 0) + "</td>"
        + "<td>" + st + "</td>"
        + "<td>" + esc(e.contact_name || "-") + "</td>"
        + "<td>" + esc(e.created_at) + "</td>"
        + '<td class="action-cell">' + entActions(e) + "</td>"
        + "</tr>";
    }).join("");
    renderPager("entPager", state.enterprises, loadEnterprises);
  }

  function entActions(e) {
    if (e.is_frozen) return '<button class="mini-btn" data-action="ent-unfreeze" data-eid="' + e.id + '">解冻</button>';
    return '<button class="mini-btn warn" data-action="ent-freeze" data-eid="' + e.id + '">冻结</button>';
  }

  async function showEnterpriseDetail(eid) {
    try {
      var dd = await api("/api/admin/enterprises/" + eid);
      var md = await api("/api/admin/enterprises/" + eid + "/members");
      if (!dd) return;
      var e = dd.enterprise;
      hide("entListPanel"); show("entDetailPanel");
      document.getElementById("backBtn").style.display = "";
      document.getElementById("entDetailBody").innerHTML = '<div class="detail-grid">'
        + detailField("企业ID", e.id)
        + detailField("名称", e.name)
        + detailField("显示名", e.display_name || "-")
        + detailField("套餐", e.plan_type || "free")
        + detailField("状态", e.is_frozen ? "已冻结" : e.status)
        + detailField("成员数", e.member_count)
        + detailField("联系人", e.contact_name || "-")
        + detailField("联系电话", e.contact_phone || "-")
        + detailField("邀请码", e.invite_code)
        + detailField("创建时间", e.created_at)
        + "</div>";
      var members = (md && md.members) || [];
      var tb = document.getElementById("entMembersBody");
      if (!members.length) {
        tb.innerHTML = '<tr><td colspan="11" class="empty-row">暂无成员</td></tr>';
      } else {
        tb.innerHTML = members.map(function (m) {
          return "<tr>"
            + "<td>" + m.id + "</td>"
            + "<td>" + esc(m.account) + "</td>"
            + "<td>" + esc(m.uid || "") + "</td>"
            + "<td>" + esc(m.display_name || "-") + "</td>"
            + '<td><span class="tag ' + entRoleCls(m.role) + '">' + esc(m.role) + "</span></td>"
            + "<td>" + (m.beans != null ? m.beans : "-") + "</td>"
            + "<td>" + (m.import_count || 0) + "</td>"
            + "<td>" + (m.success_count || 0) + "</td>"
            + "<td>" + (m.error_count || 0) + "</td>"
            + "<td>" + esc(m.last_active_at || "-") + "</td>"
            + "<td>" + (m.is_frozen ? '<span class="tag tag-frozen">冻结</span>' : '<span class="tag tag-ok">正常</span>') + "</td>"
            + "</tr>";
        }).join("");
      }
    } catch (e) { toast("加载企业详情失败: " + e.message); }
  }

  function entRoleCls(role) {
    if (role === "owner") return "tag-owner";
    if (role === "admin") return "tag-admin";
    return "tag-member";
  }

  /* ══════════════════════════════════════
     任务监控（富表格）
     ══════════════════════════════════════ */
  async function loadTasks() {
    var s = state.tasks;
    try {
      var q = "?page=" + s.page + "&page_size=" + s.page_size
        + "&keyword=" + encodeURIComponent(s.keyword)
        + "&platform=" + encodeURIComponent(s.platform)
        + "&status=" + encodeURIComponent(s.status);
      var d = await api("/api/admin/tasks" + q);
      if (!d) return;
      renderTasks(d.tasks, d.total);
    } catch (e) { toast("加载任务失败: " + e.message); }
  }

  function taskTagCls(status) {
    if (status === "done") return "tag-ok";
    if (status === "error") return "tag-frozen";
    return "tag-warn";
  }

  function renderTasks(items, total) {
    var tb = document.getElementById("tasksBody");
    state.tasks.total = total;
    if (!items.length) {
      tb.innerHTML = '<tr><td colspan="8" class="empty-row">暂无任务</td></tr>';
      renderPager("tasksPager", state.tasks, loadTasks); return;
    }
    tb.innerHTML = items.map(function (item) {
      return "<tr>"
        // 用户/企业
        + '<td class="cell-user">'
        + '<div class="tu-account">' + esc(item.account || item.display_name || "-") + "</div>"
        + '<div class="tu-ent muted-cell">' + esc(item.enterprise_name || "个人") + "</div>"
        + "</td>"
        // 商品标题
        + '<td class="cell-title">'
        + titleBlock(item)
        + "</td>"
        // 状态
        + '<td><span class="tag ' + taskTagCls(item.status) + '" title="' + esc(item.status_msg || "") + '">' + esc(item.status) + "</span></td>"
        // 图片
        + '<td class="cell-images">' + taskImageRows(item) + "</td>"
        // 规格
        + '<td class="cell-spec">' + specCell(item) + "</td>"
        // 尺寸
        + '<td class="cell-size">' + sizeCell(item) + "</td>"
        // 耗时
        + '<td class="cell-time">' + esc(timeRange(item)) + "</td>"
        // 操作
        + '<td class="action-cell"><button class="mini-btn" data-action="task-detail" data-tid="' + item.id + '">详情</button></td>'
        + "</tr>";
    }).join("");
    renderPager("tasksPager", state.tasks, loadTasks);
  }

  function titleBlock(item) {
    var ref = item.ref_code || item.id;
    return '<div class="task-title-block">'
      + '<div class="tt-platform">' + (PLATFORM_LABEL[item.platform] || esc(item.platform || "")) + '</div>'
      + '<div class="tt-orig" title="' + esc(item.title || "") + '">' + esc((item.title || "未命名").slice(0, 30)) + '</div>'
      + (item.cn_title ? '<div class="tt-cn" title="' + esc(item.cn_title) + '">' + esc(item.cn_title.slice(0, 30)) + '</div>' : '')
      + '<div class="tt-ref muted-cell">ID: ' + esc(ref) + '</div>'
      + '</div>';
  }

  function taskImageRows(item) {
    var originals = normalizeImages(item.gallery_images || []);
    var generated = (item.generated_json || []).filter(function (g) {
      return !g.deleted && (g.generated_image || g);
    }).map(function (g) { return g.generated_image || g; });
    var origRow = imageRowHtml(originals, "orig", "源");
    var aiRow = imageRowHtml(generated, "ai", "AI");
    return '<div class="img-stack">' + origRow + aiRow + '</div>';
  }

  function normalizeImages(list) {
    if (!list) return [];
    if (Array.isArray(list)) return list.filter(function (s) { return typeof s === "string" && s; });
    if (list.galleryImages) return list.galleryImages;
    return [];
  }

  function imageRowHtml(list, kind, label) {
    var MAX = 5;
    if (!list.length) return '<div class="img-row empty-img"><span class="empty-thumb">' + label + '图</span></div>';
    var shown = list.slice(0, MAX);
    var overflow = list.length > MAX ? list.length - MAX : 0;
    var tiles = shown.map(function (src) {
      return '<button class="img-tile" type="button" data-action="preview-img" data-src="' + esc(src) + '" title="点击查看大图">'
        + '<img src="' + esc(src) + '" loading="lazy" alt="">'
        + '<span class="tile-badge ' + kind + '">' + label + '</span>'
        + '</button>';
    }).join("");
    if (overflow) tiles += '<span class="img-tile tile-more">+' + overflow + '</span>';
    return '<div class="img-row">' + tiles + '</div>';
  }

  function specCell(item) {
    var spec = item.spec_json || {};
    var keys = Object.keys(spec);
    if (!keys.length) return '<span class="muted-cell">-</span>';
    return keys.slice(0, 3).map(function (k) {
      return '<div class="spec-line"><span class="spec-k">' + esc(k) + '</span>:<span class="spec-v">' + esc(spec[k]) + '</span></div>';
    }).join("");
  }

  function sizeCell(item) {
    var sz = item.size_json || {};
    if (!sz.length && !sz.width && !sz.height && !sz.weight) return '<span class="muted-cell">-</span>';
    var parts = [];
    if (sz.length) parts.push("长" + sz.length);
    if (sz.width) parts.push("宽" + sz.width);
    if (sz.height) parts.push("高" + sz.height);
    if (sz.weight) parts.push(sz.weight + "g");
    return '<div class="size-block">' + parts.join("<br>") + '</div>';
  }

  function timeRange(item) {
    if (!item.created_at) return "-";
    if (item.status === "done" && item.finished_at && item.started_at) {
      return dur(item.started_at, item.finished_at);
    }
    return item.created_at.split(" ")[0];
  }

  function dur(a, b) {
    try {
      var d1 = new Date(a.replace(" ", "T"));
      var d2 = new Date(b.replace(" ", "T"));
      var s = Math.round((d2 - d1) / 1000);
      if (s < 60) return s + "s";
      if (s < 3600) return Math.round(s / 60) + "m";
      return (s / 3600).toFixed(1) + "h";
    } catch (e) { return "-"; }
  }

  /* ── 任务详情抽屉 ── */
  async function showTaskDetail(tid) {
    try {
      var d = await api("/api/admin/tasks/" + tid);
      if (!d) return;
      var t = d.task;
      show("taskDetailDrawer");
      var html = '<div class="detail-grid">'
        + detailField("任务ID", t.id)
        + detailField("平台", PLATFORM_LABEL[t.platform] || t.platform)
        + detailField("用户", (t.account || "") + (t.display_name ? " (" + t.display_name + ")" : ""))
        + detailField("企业", t.enterprise_name || "个人")
        + detailField("状态", t.status)
        + detailField("状态信息", t.status_msg || "-")
        + detailField("编号", t.ref_code || t.id)
        + detailField("创建时间", t.created_at)
        + detailField("开始时间", t.started_at || "-")
        + detailField("完成时间", t.finished_at || "-")
        + "</div>";
      html += '<h4 class="section-title-sm">原始标题</h4><div class="raw-text">' + esc(t.title || "-") + '</div>';
      if (t.cn_title) html += '<h4 class="section-title-sm">中文标题</h4><div class="raw-text">' + esc(t.cn_title) + '</div>';
      if (t.en_title) html += '<h4 class="section-title-sm">英文标题</h4><div class="raw-text">' + esc(t.en_title) + '</div>';
      var logs = t.step_logs || {};
      if (Object.keys(logs).length) {
        html += '<h4 class="section-title-sm">步骤日志</h4><div class="step-logs">';
        for (var key in logs) {
          var lg = logs[key];
          html += '<div class="step-log-item"><span class="step-key">' + esc(key) + '</span>'
            + '<span class="step-status tag ' + (lg.status === "ok" ? "tag-ok" : (lg.status === "error" ? "tag-frozen" : "tag-warn")) + '">' + esc(lg.status || "-") + '</span>'
            + '<span class="step-msg">' + esc((lg.message || "").slice(0, 80)) + '</span></div>';
        }
        html += '</div>';
      }
      document.getElementById("taskDetailBody").innerHTML = html;
    } catch (e) { toast("加载任务详情失败: " + e.message); }
  }

  /* ══════════════════════════════════════
     计费财务
     ══════════════════════════════════════ */
  async function loadBilling() {
    try {
      var s = await api("/api/admin/billing/summary");
      if (!s) return;
      document.getElementById("bRecharge").textContent = s.recharge_beans;
      document.getElementById("bConsume").textContent = s.consume_beans;
      document.getElementById("bNet").textContent = s.net_beans;
      document.getElementById("bTodayRe").textContent = s.today_recharge;
      document.getElementById("bTodayCo").textContent = s.today_consume;
      document.getElementById("bTxCount").textContent = s.tx_count;
      document.getElementById("bOrderCount").textContent = s.order_count;
    } catch (e) { toast("加载财务失败: " + e.message); }
    loadTransactions();
    loadOrders();
  }

  function switchBillingTab(tab) {
    state.billingTab = tab;
    document.querySelectorAll(".billing-tab").forEach(function (b) {
      b.classList.toggle("active", b.dataset.billingTab === tab);
    });
    document.querySelectorAll(".billing-panel").forEach(function (p) { p.style.display = "none"; });
    show("billingTab-" + tab);
  }

  async function loadTransactions() {
    var s = state.transactions;
    try {
      var q = "?page=" + s.page + "&page_size=" + s.page_size
        + "&direction=" + encodeURIComponent(s.direction)
        + (s.user_id ? "&user_id=" + s.user_id : "");
      var d = await api("/api/admin/billing/transactions" + q);
      if (!d) return;
      var tb = document.getElementById("txBody");
      s.total = d.total;
      var list = d.transactions || [];
      if (!list.length) {
        tb.innerHTML = '<tr><td colspan="8" class="empty-row">暂无流水</td></tr>';
      } else {
        tb.innerHTML = list.map(function (t) {
          var cls = t.amount >= 0 ? "tag-ok" : "tag-frozen";
          var sign = t.amount >= 0 ? "+" : "";
          return "<tr>"
            + "<td>" + t.id + "</td>"
            + "<td>" + esc(t.account || t.uid || t.user_id) + "</td>"
            + "<td>" + esc(t.enterprise_name || "-") + "</td>"
            + '<td><span class="tag ' + cls + '">' + (t.amount >= 0 ? "充值" : "消费") + "</span></td>"
            + "<td>" + sign + t.amount + "</td>"
            + "<td>" + t.balance_after + "</td>"
            + "<td>" + esc((t.reason || "").slice(0, 30)) + "</td>"
            + "<td>" + esc(t.created_at) + "</td>"
            + "</tr>";
        }).join("");
      }
      renderPager("txPager", s, loadTransactions);
    } catch (e) { toast("加载流水失败: " + e.message); }
  }

  async function loadOrders() {
    var s = state.orders;
    try {
      var d = await api("/api/admin/billing/orders?page=" + s.page + "&page_size=" + s.page_size);
      if (!d) return;
      var tb = document.getElementById("ordersBody");
      s.total = d.total;
      var list = d.orders || [];
      if (!list.length) {
        tb.innerHTML = '<tr><td colspan="9" class="empty-row">暂无订单</td></tr>';
      } else {
        tb.innerHTML = list.map(function (o) {
          return "<tr>"
            + "<td>" + o.id + "</td>"
            + "<td>" + esc(o.account || o.uid || o.user_id) + "</td>"
            + "<td>" + o.amount_beans + "</td>"
            + "<td>" + (o.amount_cny || 0) + "</td>"
            + "<td>" + esc(o.pay_method) + "</td>"
            + '<td><span class="tag tag-ok">' + esc(o.status) + "</span></td>"
            + "<td>" + esc(o.operator_name || "-") + "</td>"
            + "<td>" + esc((o.note || "").slice(0, 20)) + "</td>"
            + "<td>" + esc(o.created_at) + "</td>"
            + "</tr>";
        }).join("");
      }
      renderPager("ordersPager", s, loadOrders);
    } catch (e) { /* 静默 */ }
  }

  /* ══════════════════════════════════════
     安全审计
     ══════════════════════════════════════ */
  async function loadAudit() {
    var s = state.audit;
    try {
      var d = await api("/api/admin/audit?page=" + s.page + "&page_size=" + s.page_size);
      if (!d) return;
      var tb = document.getElementById("auditBody");
      s.total = d.total;
      var list = d.logs || [];
      if (!list.length) {
        tb.innerHTML = '<tr><td colspan="8" class="empty-row">暂无日志</td></tr>';
      } else {
        tb.innerHTML = list.map(function (l) {
          return "<tr>"
            + "<td>" + l.id + "</td>"
            + "<td>" + esc(l.admin_name || "-") + "</td>"
            + "<td>" + esc(l.action) + "</td>"
            + "<td>" + esc(l.target_type) + "</td>"
            + "<td>" + esc(l.target_id) + "</td>"
            + "<td>" + esc(typeof l.detail === "object" ? JSON.stringify(l.detail) : (l.detail || "")) + "</td>"
            + "<td>" + esc(l.ip) + "</td>"
            + "<td>" + esc(fmtTime(l.created_at)) + "</td>"
            + "</tr>";
        }).join("");
      }
      renderPager("auditPager", s, loadAudit);
    } catch (e) { toast("加载审计失败: " + e.message); }
  }

  /* ── 分页 ── */
  function renderPager(elId, pageState, loader) {
    var el = document.getElementById(elId);
    if (!el) return;
    var pages = Math.ceil(pageState.total / pageState.page_size) || 1;
    var cur = pageState.page;
    el.innerHTML = "";
    if (pages <= 1) return;
    var prev = document.createElement("button");
    prev.className = "page-btn"; prev.textContent = "上一页"; prev.disabled = cur <= 1;
    prev.onclick = function () { pageState.page = Math.max(1, cur - 1); loader(); };
    el.appendChild(prev);
    var info = document.createElement("span");
    info.className = "page-info";
    info.textContent = cur + " / " + pages + " 页（共 " + pageState.total + " 条）";
    el.appendChild(info);
    var next = document.createElement("button");
    next.className = "page-btn"; next.textContent = "下一页"; next.disabled = cur >= pages;
    next.onclick = function () { pageState.page = Math.min(pages, cur + 1); loader(); };
    el.appendChild(next);
  }

  /* ── 充值弹窗 ── */
  function openRecharge(uid, name) {
    state.rechargeTarget = uid;
    document.getElementById("rechargeTarget").textContent = "目标用户: " + name + " (ID: " + uid + ")";
    document.getElementById("rechargeAmount").value = 100;
    document.getElementById("rechargeNote").value = "";
    show("rechargeModal");
  }
  function closeRecharge() { hide("rechargeModal"); state.rechargeTarget = null; }
  async function confirmRecharge() {
    var uid = state.rechargeTarget;
    var amount = parseInt(document.getElementById("rechargeAmount").value, 10);
    var note = document.getElementById("rechargeNote").value.trim();
    if (!uid || !amount || amount <= 0) { toast("请输入有效数量"); return; }
    try {
      await api("/api/admin/users/" + uid + "/recharge", "POST", { amount: amount, note: note });
      toast("充值成功"); closeRecharge(); loadUsers();
    } catch (e) { toast("充值失败: " + e.message); }
  }

  /* ── 图片预览 ── */
  function previewImage(src) {
    var overlay = document.createElement("div");
    overlay.className = "img-preview-overlay";
    overlay.innerHTML = '<img src="' + esc(src) + '" alt=""><button class="preview-close">关闭</button>';
    overlay.onclick = function () { overlay.remove(); };
    document.body.appendChild(overlay);
  }

  /* ── 事件委托 ── */
  document.addEventListener("click", async function (e) {
    var navEl = e.target.closest("[data-view]");
    if (navEl) { switchView(navEl.dataset.view); return; }

    var action = e.target.dataset.action;
    if (action === "refresh") { switchView(state.view); toast("已刷新"); return; }
    if (action === "logout") { await api("/api/admin/auth/logout", "POST"); window.location.href = "/"; return; }
    if (action === "close-modal") { closeRecharge(); return; }
    if (action === "confirm-recharge") { confirmRecharge(); return; }
    if (action === "close-drawer") { hide("taskDetailDrawer"); return; }
    if (action === "preview-img") { previewImage(e.target.dataset.src); return; }

    if (action === "back-list") { switchView(state.view); return; }
    if (action === "user-back") { show("usersListPanel"); hide("userDetailPanel"); document.getElementById("backBtn").style.display = "none"; return; }
    if (action === "ent-back") { show("entListPanel"); hide("entDetailPanel"); document.getElementById("backBtn").style.display = "none"; return; }

    /* billing tabs */
    if (e.target.dataset.billingTab) { switchBillingTab(e.target.dataset.billingTab); return; }

    /* ai tabs */
    if (e.target.dataset.keyTab) { switchKeyModelTab(e.target.dataset.keyTab); return; }

    /* pricing / ai actions */
    if (action === "add-pricing") { addPricing(); return; }
    if (action === "del-pricing") {
      var pid = e.target.dataset.pid;
      try {
        await api("/api/admin/pricing/" + pid, "DELETE");
        toast("已删除"); loadPricing();
      } catch (err) { toast("删除失败: " + err.message); }
      return;
    }
    if (action === "refresh-keys") { loadKeyModel(); toast("已刷新"); return; }
    if (action === "add-keys") { addKeys(); return; }
    if (action === "del-key") { delKey(e.target.dataset.provider, e.target.dataset.key); return; }
    if (action === "revive-key") { reviveKey(e.target.dataset.provider, e.target.dataset.key); return; }
    if (action === "clear-failed") { clearFailed(e.target.dataset.provider); return; }

    /* monitoring tabs */
    if (e.target.dataset.monitorTab) { switchMonitorTab(e.target.dataset.monitorTab); return; }

    /* 搜索 */
    if (action === "search-users") {
      state.users.page = 1;
      state.users.keyword = document.getElementById("userSearch").value.trim();
      state.users.status = document.getElementById("userStatus").value;
      loadUsers(); return;
    }
    if (action === "search-ent") {
      state.enterprises.page = 1;
      state.enterprises.keyword = document.getElementById("entSearch").value.trim();
      state.enterprises.status = document.getElementById("entStatus").value;
      loadEnterprises(); return;
    }
    if (action === "search-tasks") {
      state.tasks.page = 1;
      state.tasks.keyword = document.getElementById("taskSearch").value.trim();
      state.tasks.platform = document.getElementById("taskPlatform").value;
      state.tasks.status = document.getElementById("taskStatus").value;
      loadTasks(); return;
    }
    if (action === "search-tx") {
      state.transactions.page = 1;
      state.transactions.user_id = document.getElementById("txUserId").value.trim();
      state.transactions.direction = document.getElementById("txDirection").value;
      loadTransactions(); return;
    }


    /* error center 搜索/重试 */
    if (action === "search-errors") {
      state.errors.page = 1;
      state.errors.keyword = document.getElementById("errKeyword").value.trim();
      state.errors.platform = document.getElementById("errPlatform").value;
      loadErrorTasks(); return;
    }
    if (action === "retry-one") {
      var tid = e.target.dataset.tid;
      try {
        await api("/api/admin/monitoring/errors/" + tid + "/retry", "POST");
        toast("已加入重试队列");
        loadErrorSummary();
        loadErrorTasks();
      } catch (err) { toast("重试失败: " + err.message); }
      return;
    }
    if (action === "batch-retry-errors") {
      var ids = [];
      document.querySelectorAll(".err-check:checked").forEach(function (cb) { ids.push(cb.value); });
      if (!ids.length) { toast("请先勾选任务"); return; }
      try {
        var d = await api("/api/admin/monitoring/errors/batch-retry", "POST", { import_ids: ids });
        toast("已重试 " + (d.retried || 0) + " 个任务");
        loadErrorSummary();
        loadErrorTasks();
      } catch (err) { toast("批量重试失败: " + err.message); }
      return;
    }
    if (action === "refresh-queue") { loadQueueStatus(); toast("已刷新"); return; }

    /* 详情跳转 */
    if (action === "user-detail") { showUserDetail(e.target.dataset.uid); return; }
    if (action === "ent-detail") { showEnterpriseDetail(e.target.dataset.eid); return; }
    if (action === "task-detail") { showTaskDetail(e.target.dataset.tid); return; }

    /* 充值 */
    if (action === "recharge") { openRecharge(e.target.dataset.uid, e.target.dataset.name); return; }

    /* 用户冻结/禁用 */
    if (["freeze", "unfreeze", "disable", "enable"].indexOf(action) >= 0) {
      var uid = e.target.dataset.uid;
      try {
        await api("/api/admin/users/" + uid + "/" + action, "POST");
        toast("操作成功"); loadUsers();
      } catch (err) { toast("操作失败: " + err.message); }
      return;
    }

    /* 企业冻结 */
    if (action === "ent-freeze" || action === "ent-unfreeze") {
      var eid = e.target.dataset.eid;
      var real = action === "ent-freeze" ? "freeze" : "unfreeze";
      try {
        await api("/api/admin/enterprises/" + eid + "/" + real, "POST");
        toast("操作成功"); loadEnterprises();
      } catch (err) { toast("操作失败: " + err.message); }
      return;
    }
  });

  /* ══════════════════════════════════════
     定价配置
     ══════════════════════════════════════ */
  async function loadPricing() {
    try {
      var d = await api("/api/admin/pricing");
      if (!d) return;
      var tb = document.getElementById("pricingBody");
      var list = d.configs || [];
      if (!list.length) {
        tb.innerHTML = '<tr><td colspan="7" class="empty-row">暂无定价配置</td></tr>';
        return;
      }
      tb.innerHTML = list.map(function (c) {
        return "<tr>"
          + "<td>" + c.id + "</td>"
          + "<td>" + esc(c.platform) + "</td>"
          + "<td>" + esc(c.step) + "</td>"
          + "<td><strong>" + c.cost_beans + "</strong> 豆</td>"
          + '<td><span class="tag ' + (c.is_active ? "tag-ok" : "tag-disabled") + '">' + (c.is_active ? "启用" : "停用") + "</span></td>"
          + "<td>" + esc(fmtTime(c.updated_at)) + "</td>"
          + '<td><button class="mini-btn warn" data-action="del-pricing" data-pid="' + c.id + '">删除</button></td>'
          + "</tr>";
      }).join("");
    } catch (e) { toast("加载定价失败: " + e.message); }
  }

  async function addPricing() {
    var platform = document.getElementById("newPlatform").value.trim();
    var step = document.getElementById("newStep").value.trim();
    var cost = parseInt(document.getElementById("newCost").value, 10);
    if (!platform || !step || isNaN(cost)) { toast("请填写完整"); return; }
    try {
      await api("/api/admin/pricing", "POST", { platform: platform, step: step, cost_beans: cost, is_active: true });
      toast("定价已保存");
      document.getElementById("newPlatform").value = "";
      document.getElementById("newStep").value = "";
      document.getElementById("newCost").value = "";
      loadPricing();
    } catch (e) { toast("保存失败: " + e.message); }
  }

  /* ══════════════════════════════════════
     AI 资源管理
     ══════════════════════════════════════ */
  var aiTab = "keys";

  var keyModelTab = "chat";

  function switchKeyModelTab(tab) {
    keyModelTab = tab;
    document.querySelectorAll(".key-model-tab").forEach(function (b) {
      b.classList.toggle("active", b.dataset.keyTab === tab);
    });
    loadKeyModel();
  }

  async function loadKeyModel() {
    var el = document.getElementById("keyModelPanel");
    if (!el) return;
    try {
      var d = await api("/api/admin/ai/keys?provider=" + encodeURIComponent(keyModelTab));
      var cfg = await api("/api/admin/ai/config");
      if (!d.connected) {
        el.innerHTML = '<div class="hint-card"><p>Key 池未连接: ' + esc(d.error || "") + '</p></div>';
        return;
      }
      var pools = d.pools || [];
      var providers = d.providers || {};
      var label = providers[keyModelTab] || keyModelTab;
      var pool = pools.length ? pools[0] : { provider: keyModelTab, label: label, normal: [], failed: [], counts: {} };

      var avail = (pool.counts && pool.counts.available) || 0;
      var cool = (pool.counts && pool.counts.cooling) || 0;
      var fail = (pool.counts && pool.counts.failed) || 0;
      var total = avail + cool + fail;

      // 模型标识
      var badgeCls = keyModelTab === "chat" ? "badge-chat" : "badge-vibe";

      var html = '<div class="model-header-bar"><span class="model-big-badge ' + badgeCls + '">' + esc(label) + '</span>'
        + '<span class="model-total">共 ' + total + ' 个 Key</span></div>';

      // 统计卡（放最上面，单独统计当前模型）
      html += '<div class="metric-grid">'
        + '<article class="metric-card"><span>可用 Key</span><strong class="stat-ok">' + avail + '</strong></article>'
        + '<article class="metric-card"><span>冷却中</span><strong class="stat-warn">' + cool + '</strong></article>'
        + '<article class="metric-card"><span>失效</span><strong class="stat-fail">' + fail + '</strong></article>'
        + '<article class="metric-card"><span>总计</span><strong>' + total + '</strong></article>'
        + '</div>';

      // 模型配置（融合进来，不再单独 Tab）
      if (cfg.available) {
        var m = cfg.models || {};
        var keys = cfg.keys || {};
        var oss = cfg.oss || {};
        var pipe = cfg.pipeline || {};
        html += '<div class="card" style="margin-bottom:1rem"><div class="card-head"><h3>模型配置</h3></div><div class="detail-grid">';
        if (keyModelTab === "chat") {
          html += detailField("Chat 模型", m.chat_model || "-");
          html += detailField("Base URL", m.chat_base_url || "-");
          html += detailField("API Key(脱敏)", keys.chat_api_key || "-");
        } else {
          html += detailField("图片模型", m.image_model || "-");
          html += detailField("图片尺寸", m.image_size || "-");
          html += detailField("Base URL", m.vibe_base_url || "-");
          html += detailField("API Key(脱敏)", keys.vibe_api_key || "-");
        }
        html += detailField("OSS Bucket", oss.bucket || "-");
        html += detailField("每用户并发", pipe.max_per_user || "-");
        html += '</div></div>';
      }

      // 粘贴框
      html += '<div class="key-add-box">'
        + '<div class="key-add-header">'
        + '<span class="key-add-title">添加 Key 到「' + esc(label) + '」池</span>'
        + '<button class="btn-primary" data-action="add-keys">添加到池</button>'
        + '<button class="ghost-btn" data-action="refresh-keys">刷新</button>'
        + '</div>'
        + '<textarea id="keyPasteArea" class="key-paste-area" rows="4" placeholder="粘贴 ' + esc(label) + ' 的 Key，一行一个（支持批量粘贴）。粘贴后点上方添加到池即可直接入池。"></textarea>'
        + '</div>';

      var normal = pool.normal || [];
      var failed = pool.failed || [];

      // 正常 Key 表
      if (normal.length) {
        html += '<div class="card" style="margin-bottom:1rem"><div class="card-head"><h3>正常 Key（' + normal.length + '）</h3></div>';
        html += '<div class="table-wrap"><table class="data-table"><thead><tr><th>模型</th><th>Key(脱敏)</th><th>状态</th><th>添加时间</th><th>失败次数</th><th>失败原因</th><th>操作</th></tr></thead><tbody>';
        normal.forEach(function (k) {
          var cls = k.status === "available" ? "tag-ok" : "tag-warn";
          var enc = encodeURIComponent(k.full_key || k.key);
          html += "<tr>"
            + '<td><span class="model-badge ' + esc(keyModelTab) + '">' + esc(label) + "</span></td>"
            + "<td><code>" + esc(k.key) + "</code></td>"
            + '<td><span class="tag ' + cls + '">' + esc(k.status) + "</span></td>"
            + "<td>" + esc(k.added_at) + "</td>"
            + "<td>" + (k.fail_count || 0) + "</td>"
            + '<td class="fail-reason-cell">' + esc(k.fail_reason || "-") + "</td>"
            + '<td class="action-cell">'
            + '<button class="mini-btn warn" data-action="del-key" data-provider="' + esc(keyModelTab) + '" data-key="' + enc + '">删除</button>'
            + (k.status !== "available" ? '<button class="mini-btn" data-action="revive-key" data-provider="' + esc(keyModelTab) + '" data-key="' + enc + '">恢复</button>' : "")
            + "</td></tr>";
        });
        html += '</tbody></table></div></div>';
      }

      // 失效 Key 表
      if (failed.length) {
        html += '<div class="card"><div class="card-head" style="display:flex;justify-content:space-between;align-items:center"><h3 style="color:#f0a0a0">失效 Key（' + failed.length + '）</h3>'
          + '<button class="mini-btn warn" data-action="clear-failed" data-provider="' + esc(keyModelTab) + '">批量清除</button></div>';
        html += '<div class="table-wrap"><table class="data-table"><thead><tr><th>模型</th><th>Key(脱敏)</th><th>失败原因</th><th>失效时间</th><th>操作</th></tr></thead><tbody>';
        failed.forEach(function (k) {
          var enc = encodeURIComponent(k.full_key || k.key);
          html += "<tr>"
            + '<td><span class="model-badge ' + esc(keyModelTab) + '">' + esc(label) + "</span></td>"
            + "<td><code>" + esc(k.key) + "</code></td>"
            + '<td class="fail-reason-cell">' + esc(k.fail_reason || "-") + "</td>"
            + "<td>" + esc(k.fail_at) + "</td>"
            + '<td class="action-cell"><button class="mini-btn" data-action="revive-key" data-provider="' + esc(keyModelTab) + '" data-key="' + enc + '">恢复</button>'
            + '<button class="mini-btn warn" data-action="del-key" data-provider="' + esc(keyModelTab) + '" data-key="' + enc + '">删除</button></td></tr>';
        });
        html += '</tbody></table></div></div>';
      }

      if (!normal.length && !failed.length) {
        html += '<div class="hint-card"><p>该模型池暂无 Key，在上方粘贴框添加。</p></div>';
      }

      el.innerHTML = html;
    } catch (e) { toast("加载失败: " + e.message); }
  }

  async function addKeys() {
    var text = document.getElementById("keyPasteArea").value;
    var keys = text.split(/[\n,\s]+/).filter(function (k) { return k.trim(); });
    if (!keys.length) { toast("请先粘贴 Key"); return; }
    try {
      var d = await api("/api/admin/ai/keys/add", "POST", { provider: keyModelTab, keys: keys });
      toast("添加 " + (d.added || 0) + " 个，重复 " + (d.duplicate || 0) + " 个");
      document.getElementById("keyPasteArea").value = "";
      loadKeyModel();
    } catch (e) { toast("添加失败: " + e.message); }
  }

  async function delKey(provider, encKey) {
    var key = decodeURIComponent(encKey);
    try {
      await api("/api/admin/ai/keys/remove", "POST", { provider: provider, key: key });
      toast("已删除"); loadKeyModel();
    } catch (e) { toast("删除失败: " + e.message); }
  }

  async function reviveKey(provider, encKey) {
    var key = decodeURIComponent(encKey);
    try {
      await api("/api/admin/ai/keys/update", "POST", { provider: provider, key: key, status: "available" });
      toast("已恢复"); loadKeyModel();
    } catch (e) { toast("恢复失败: " + e.message); }
  }

  async function clearFailed(provider) {
    if (!confirm("确认清除 " + provider + " 池的全部失效 Key？")) return;
    try {
      var d = await api("/api/admin/ai/keys/bulk-remove", "POST", { provider: provider, state: "failed" });
      toast("已清除 " + (d.removed || 0) + " 个失效 Key"); loadKeyModel();
    } catch (e) { toast("清除失败: " + e.message); }
  }

  /* ── 全选复选框（错误中心）── */
  document.addEventListener("change", function (e) {
    if (e.target.id === "errSelectAll") {
      document.querySelectorAll(".err-check").forEach(function (cb) { cb.checked = e.target.checked; });
    }
  });

  /* ── 初始化 ── */
  /* ══════════════════════════════════════
     监控中心：错误中心 + 队列监控
     ══════════════════════════════════════ */
  var monitorTab = "errors";
  var queueTimer = null;

  function switchMonitorTab(tab) {
    monitorTab = tab;
    document.querySelectorAll(".monitor-tab").forEach(function (b) {
      b.classList.toggle("active", b.dataset.monitorTab === tab);
    });
    document.querySelectorAll(".monitor-panel").forEach(function (p) { p.style.display = "none"; });
    var target = document.getElementById("monitorTab-" + tab);
    if (target) target.style.display = "";
    if (tab === "queue") { startQueuePoll(); } else { stopQueuePoll(); }
  }

  async function loadMonitoring() {
    loadErrorSummary();
    loadErrorTasks();
  }

  async function loadErrorSummary() {
    try {
      var d = await api("/api/admin/monitoring/errors/summary");
      if (!d) return;
      document.getElementById("errTotal").textContent = d.total_errors;
      document.getElementById("errToday").textContent = d.today_errors;
      document.getElementById("errTemu").textContent = d.temu_errors;
      document.getElementById("errUsers").textContent = d.affected_users;
      var bd = d.breakdown || [];
      var el = document.getElementById("errorBreakdown");
      if (!bd.length) { el.innerHTML = '<div class="empty-row">暂无分类数据</div>'; return; }
      el.innerHTML = bd.map(function (b) {
        return '<div class="breakdown-row"><span class="bd-type">' + esc(b.error_type) + '</span>'
          + '<span class="bd-count">' + b.count + ' 次</span></div>';
      }).join("");
    } catch (e) { toast("加载错误统计失败: " + e.message); }
  }

  async function loadErrorTasks() {
    var s = state.errors;
    try {
      var q = "?page=" + s.page + "&page_size=" + s.page_size
        + "&platform=" + encodeURIComponent(s.platform)
        + "&keyword=" + encodeURIComponent(s.keyword);
      var d = await api("/api/admin/monitoring/errors" + q);
      if (!d) return;
      renderErrorTasks(d.tasks, d.total);
    } catch (e) { toast("加载错误列表失败: " + e.message); }
  }

  function renderErrorTasks(items, total) {
    var tb = document.getElementById("errBody");
    state.errors.total = total;
    if (!items.length) {
      tb.innerHTML = '<tr><td colspan="8" class="empty-row">暂无失败任务</td></tr>';
      renderPager("errPager", state.errors, loadErrorTasks); return;
    }
    tb.innerHTML = items.map(function (t) {
      return "<tr>"
        + '<td><input type="checkbox" class="err-check" value="' + t.id + '"></td>'
        + "<td>" + t.id + "</td>"
        + "<td>" + esc((t.title || "未命名").slice(0, 25)) + "</td>"
        + "<td>" + esc(t.platform) + "</td>"
        + "<td>" + esc(t.account || "-") + "</td>"
        + '<td class="err-msg" title="' + esc(t.status_msg || "") + '">' + esc((t.status_msg || "").slice(0, 60)) + "</td>"
        + "<td>" + esc(t.updated_at) + "</td>"
        + '<td><button class="mini-btn" data-action="retry-one" data-tid="' + t.id + '">重试</button></td>'
        + "</tr>";
    }).join("");
    renderPager("errPager", state.errors, loadErrorTasks);
  }

  /* ── 队列监控 ── */
  async function loadQueueStatus() {
    try {
      var d = await api("/api/admin/monitoring/queue");
      if (!d) return;
      document.getElementById("qDepth").textContent = d.queue_depth;
      document.getElementById("qActive").textContent = d.total_active || 0;
      document.getElementById("qActiveUsers").textContent = (d.active_users || []).length;
      var redisEl = document.getElementById("qRedis");
      if (redisEl) redisEl.innerHTML = d.connected
        ? '<span class="tag tag-ok">已连接</span>'
        : '<span class="tag tag-frozen">未连接</span>';

      var qb = document.getElementById("queueItemsBody");
      var items = d.queued_items || [];
      if (!items.length) {
        qb.innerHTML = '<tr><td colspan="2" class="empty-row">队列为空</td></tr>';
      } else {
        qb.innerHTML = items.slice(0, 50).map(function (it) {
          return "<tr><td>" + esc(it.user_id) + "</td><td>" + esc(it.import_id) + "</td></tr>";
        }).join("");
      }

      var ab = document.getElementById("activeUsersBody");
      var au = d.active_users || [];
      if (!au.length) {
        ab.innerHTML = '<tr><td colspan="2" class="empty-row">无运行中任务</td></tr>';
      } else {
        ab.innerHTML = au.map(function (u) {
          return "<tr><td>" + esc(u.user_id) + "</td><td>" + u.active + "</td></tr>";
        }).join("");
      }
    } catch (e) { /* 静默，轮询中 */ }
  }

  function startQueuePoll() {
    loadQueueStatus();
    stopQueuePoll();
    queueTimer = setInterval(loadQueueStatus, 5000);
  }
  function stopQueuePoll() {
    if (queueTimer) { clearInterval(queueTimer); queueTimer = null; }
  }

  async function init() {
    try {
      var d = await api("/api/admin/auth/me");
      if (!d || !d.ok) { window.location.href = "/"; return; }
      state.admin = d.admin;
      document.getElementById("adminName").textContent = d.admin.display_name || d.admin.username;
      loadDashboard();
    } catch (e) { window.location.href = "/"; }
  }

  init();
})();
