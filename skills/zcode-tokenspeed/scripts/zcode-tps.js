/**
 * ZCode 状态栏：TPS 统计胶囊 —— 事件流实现（唯一方案）
 * ==================================================
 * 输入框工具栏常驻胶囊：● 首 token · tok/s · 思考 │ 第 N 轮 · 总用 · 输入 · 命中 · 累出
 *
 * 数据源（ZCode 主进程经 window.postMessage 转交的 ServicePort 事件流）：
 *   - version:1 事件流：usage.delta（真实 tokens）、model.request.status、stream.chunk
 *   - conversation 行事件：turnHeader / userInput / reasoning / assistantText
 * tok/s 与 token 数只取自真实 usage，不做文本估算：每个模型请求完成时更新一次；
 * 首 token 逐请求更新；一轮结束后，首 token 取本轮各请求的算术平均，tok/s 取按 outputTokens 加权的平均。
 *
 * ⚠️ 关键约束：**绝不调用 port.start()**
 *   MessagePort 队列一旦启用，消息只派发给"启用瞬间已注册的监听器"。本脚本是普通
 *   script（早于 type=module 的应用主包执行），若先 start() 就会消费掉服务端的
 *   Initialize 启动握手 → 应用的协议客户端永远收不到握手 → ZCode 3.12.2 卡在启动界面。
 *   因此只 addEventListener，把 start() 留给应用；届时双方都能收到消息。
 *
 *   真实 Chrome 实测（MessageChannel，双方持有同一 port）：
 *     我们先 start()          → 我们 ["Initialize"]，应用 []            ✗ 卡启动
 *     我们不 start、应用 start → 我们 ["Initialize"]，应用 ["Initialize"] ✓ 正常
 *   注意：不能用 Node 验证此语义——Node 的 MessagePort 在 addEventListener 时隐式
 *   start，会让"不 start"也失败，从而得出错误结论。
 *
 * 已知特性：tok/s 在本轮第一个模型请求完成后才出现（usage.delta 到达才有真实 tokens）；
 *   请求进行期间不显示估算速度，保留最近一次真实请求的值。
 *
 * 安全：只读事件流、只写自己的 host 元素；异常静默；找不到工具栏行时自清理。
 * 位置切换：胶囊右键菜单可在「输入框工具栏(默认) / 会话顶部(sticky)」间切换，
 *   localStorage 记忆（键 ztps-pos）；数据层零改动，仅挂载点不同。
 * 回滚：脚本/补丁异常时用 scripts/restore_clean.py --latest 秒级还原，无需重装。
 */
(() => {
  if (window.__ztps) return;
  window.__ztps = true;
  const dec = new TextDecoder();
  const MARK = "data-ztps";

  // ---------- 位置切换(胶囊右键菜单,localStorage 记忆) ----------
  const POS_KEY = "ztps-pos";
  const POSITIONS = [
    { id: "composer-below", label: "输入框下方" },
    { id: "toolbar", label: "输入框工具栏" },
    { id: "session-top", label: "会话顶部" },
  ];
  const getPos = () => {
    try {
      const v = localStorage.getItem(POS_KEY);
      return POSITIONS.some((p) => p.id === v) ? v : "composer-below";
    } catch (err) { return "composer-below"; }
  };
  const setPos = (id) => { try { localStorage.setItem(POS_KEY, id); } catch (err) { /* 静默 */ } };

  // ---------- 侧边栏/辅助对话与主会话边界判定 ----------
  // 核心诉求：无论何时只挂载在主界面对话框，绝不在侧边栏（辅助对话）来回跳动。
  function isInsideSidePane(el) {
    if (!el || typeof el.closest !== "function") return false;
    try {
      return !!el.closest(
        "[data-workspace-side-frame], " +
        "[data-side-pane-tabs-content], " +
        "[data-side-pane-tab-content], " +
        "[data-side-pane-tabs-viewport], " +
        "[data-workspace-sidebar-panel], " +
        "[data-mobile-side-pane-overlay], " +
        "[data-side-pane-terminal-stash], " +
        "aside, " +
        "[class*='side-pane'], [class*='sidePane'], [class*='sidebar-panel'], [class*='sidebarPanel']"
      );
    } catch (err) { return false; }
  }

  function isInsideMainConversation(el) {
    if (!el || typeof el.closest !== "function") return false;
    try {
      return !!el.closest("[data-workspace-conversation-frame]");
    } catch (err) { return false; }
  }

  // 会话顶部 sticky 的挂载点:当前会话消息区的滚动容器。
  // 从轮次 section 向上找第一个纵向可滚动的祖先(主内容区特征:overflow auto/scroll 且足够高)。
  function findSessionScroller() {
    const mainFrame = document.querySelector("[data-workspace-conversation-frame]");
    const scope = mainFrame || document;
    let secs = Array.from(scope.querySelectorAll("section[data-turn-id]")).filter((s) => !isInsideSidePane(s));
    const sec = secs[0];
    let cur = sec ? sec.parentElement : null;
    while (cur && cur !== document.body) {
      if (isInsideSidePane(cur)) break;
      let oy = "";
      try { oy = getComputedStyle(cur).overflowY; } catch (err) { /* ignore */ }
      if (/(auto|scroll)/.test(oy) && cur.clientHeight > 120) return cur;
      cur = cur.parentElement;
    }
    return null;
  }

  let posMenu = null;
  function closePosMenu() {
    if (posMenu) {
      posMenu.remove();
      posMenu = null;
      document.removeEventListener("pointerdown", onDocDownClose, true);
    }
  }
  // 点击菜单项的时序:pointerdown(此监听)→ pointerup → click。若 pointerdown 就关菜单,
  // click 会在已移除的元素上落空、永远选不中——故菜单内部点击不关,交给 click 处理
  function onDocDownClose(e) {
    if (posMenu && posMenu.contains(e.target)) return;
    closePosMenu();
  }
  function openPosMenu(x, y) {
    closePosMenu();
    const m = document.createElement("div");
    m.setAttribute("data-ztps-menu", "1");
    Object.assign(m.style, {
      position: "fixed", left: x + "px", top: y + "px", zIndex: "2147483000",
      background: "var(--color-background, var(--ztps-bg, #ffffff))",
      color: "var(--color-foreground, var(--ztps-fg, #1b1f24))",
      border: "1px solid rgba(127,127,127,0.3)", borderRadius: "10px",
      padding: "4px", display: "flex", flexDirection: "column", gap: "2px",
      fontSize: "12px", lineHeight: "1.4", boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
      userSelect: "none",
    });
    const cur = getPos();
    for (const p of POSITIONS) {
      const it = document.createElement("div");
      Object.assign(it.style, { padding: "5px 12px", borderRadius: "6px", cursor: "pointer", whiteSpace: "nowrap" });
      it.textContent = (p.id === cur ? "● " : "○ ") + p.label;
      it.addEventListener("pointerenter", () => { it.style.background = "rgba(127,127,127,0.18)"; });
      it.addEventListener("pointerleave", () => { it.style.background = "transparent"; });
      it.addEventListener("click", (e) => {
        e.stopPropagation();
        closePosMenu();
        if (p.id !== cur) { setPos(p.id); renderBar(); }
      });
      m.appendChild(it);
    }
    document.body.appendChild(m);
    // 视口内收敛,防止贴边溢出
    const r = m.getBoundingClientRect();
    if (r.right > window.innerWidth) m.style.left = Math.max(4, window.innerWidth - r.width - 4) + "px";
    if (r.bottom > window.innerHeight) m.style.top = Math.max(4, window.innerHeight - r.height - 4) + "px";
    posMenu = m;
    // 捕获阶段监听外部按下即关闭;setTimeout 避免右键自身的 pointerdown 立刻触发关闭
    setTimeout(() => document.addEventListener("pointerdown", onDocDownClose, true), 0);
  }
  // 右键入口挂 document 捕获阶段:任何中间层 stopPropagation 都拦不住,且不随 pill 生命周期失效
  document.addEventListener("contextmenu", (e) => {
    const t = e.target;
    if (t && typeof t.closest === "function" && t.closest("[data-ztps-bar]")) {
      e.preventDefault();
      openPosMenu(e.clientX, e.clientY);
    }
  }, true);

  // ---------- 主题兜底 ----------
  // 颜色优先用应用自己的 CSS 变量（--color-foreground 等），一旦某个版本改了变量名，
  // 就退到我们自己的变量上；而我们的变量按「应用主题类（.dark）→ 系统偏好」两级判定，
  // 避免出现「浅色主题 + 深色兜底字色」这种看不清的组合。
  const STYLE_ID = "ztps-style";
  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    try {
      const st = document.createElement("style");
      st.id = STYLE_ID;
      st.textContent = [
        "[data-ztps-bar]{--ztps-fg:#1b1f24;--ztps-dim:#5b6470;--ztps-accent:#b45309}",
        ".dark [data-ztps-bar],html.dark [data-ztps-bar],body.dark [data-ztps-bar]",
        "{--ztps-fg:#e8e8e8;--ztps-dim:#9a9a9a;--ztps-accent:#e0983a}",
        "@media (prefers-color-scheme: dark){",
        ":root:not(.light):not([data-theme='light']) [data-ztps-bar]",
        "{--ztps-fg:#e8e8e8;--ztps-dim:#9a9a9a;--ztps-accent:#e0983a}}",
        "[data-ztps-menu]{--ztps-bg:#ffffff;--ztps-fg:#1b1f24}",
        ".dark [data-ztps-menu],html.dark [data-ztps-menu],body.dark [data-ztps-menu]",
        "{--ztps-bg:#1e1e1e;--ztps-fg:#e8e8e8}",
        "@media (prefers-color-scheme: dark){",
        ":root:not(.light):not([data-theme='light']) [data-ztps-menu]",
        "{--ztps-bg:#1e1e1e;--ztps-fg:#e8e8e8}}",
        "/* 模型弹窗扁平列表宽度自适应保障 */",
        "[data-radix-popper-content-wrapper] [role='menu'].w-48,",
        "[data-radix-popper-content-wrapper] [role='listbox'].w-48,",
        "[data-radix-popper-content-wrapper] .w-48.max-h-72{width:20rem!important;min-width:20rem!important}",
      ].join("");
      document.head.appendChild(st);
    } catch (err) { /* 静默 */ }
  }

  const turns = new Map();          // turnId(msg_xxx) -> 轮统计
  const firstChunkByScid = {};
  const respTurn = new Map();       // assistantResponseId -> turnId（stream.chunk 的 assistantMessageId 归属）
  const scidTurn = new Map();       // sourceCommandId -> turnId（usage.delta 关联兜底）

  const get = (id) => {
    if (!turns.has(id)) turns.set(id, {
      msgId: id, sourceCommandId: null, sessionId: null,
      startedAt: null, endedAt: null, activeMs: null, modelDurationMs: null,
      firstChunkAt: null, rowFirstAt: null, lastUsageAt: null,
      outputTokens: 0, inputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0, reasoningTokens: 0, totalTokens: 0,
      requests: new Map(), requestSeq: 0, activeRequestId: null,
      turnEnded: false, streaming: false,
    });
    return turns.get(id);
  };

  function blankRequest(requestId) {
    return {
      requestId,
      startedAt: null, firstChunkAt: null,
      statusEndedAt: null, statusDurationMs: null,
      metricsDurationMs: null, metricsTtft: null,
      outputTokens: 0, metricsOutputTokens: 0,
      usageSeen: false, endedAt: null,
    };
  }

  function newAnonRequest(t) {
    const id = "anon:" + (++t.requestSeq);
    const r = blankRequest(id);
    t.requests.set(id, r);
    t.activeRequestId = id;
    return r;
  }

  // 每个模型请求的真实用量与耗时；请求结束（usage.delta）后才有有效值
  function requestOf(t, requestId) {
    if (!requestId) {
      const active = t.activeRequestId ? t.requests.get(t.activeRequestId) : null;
      if (active && !active.usageSeen) return active;
      return newAnonRequest(t);
    }
    let r = t.requests.get(requestId);
    if (r) return r;
    // 没有 started 事件时先按 chunk 建了匿名请求，拿到 requestId 后接管它
    const active = t.activeRequestId ? t.requests.get(t.activeRequestId) : null;
    if (active && active.requestId.startsWith("anon:") && !active.usageSeen) {
      t.requests.delete(active.requestId);
      active.requestId = requestId;
      t.requests.set(requestId, active);
      t.activeRequestId = requestId;
      return active;
    }
    r = blankRequest(requestId);
    t.requests.set(requestId, r);
    return r;
  }

  // 轮复用（编辑重发/重试复用同一 turnId）时清空上一轮统计，避免旧值残留到新轮
  function resetTurnStats(t) {
    t.endedAt = null; t.activeMs = null; t.modelDurationMs = null;
    t.firstChunkAt = null; t.rowFirstAt = null; t.lastUsageAt = null;
    t.outputTokens = 0; t.inputTokens = 0; t.cacheReadTokens = 0; t.cacheWriteTokens = 0; t.reasoningTokens = 0; t.totalTokens = 0;
    t.requests.clear(); t.activeRequestId = null; t.requestSeq = 0;
    t.turnEnded = false; t.streaming = true;
    if (t.sourceCommandId) delete firstChunkByScid[t.sourceCommandId];
  }

  let scanRaf = 0;
  function scheduleScan() {
    if (scanRaf) return;
    scanRaf = requestAnimationFrame(() => {
      scanRaf = 0;
      scan();
    });
  }

  function onRow(row) {
    if (!row) return;
    if (!row.turnId) return;
    const t = get(row.turnId);
    if (row.kind === "turnHeader") {
      if ((t.endedAt != null || t.lastUsageAt != null) && row.startedAt && row.startedAt !== t.startedAt) resetTurnStats(t);
      t.startedAt = row.startedAt ?? t.startedAt;
      t.endedAt = row.endedAt ?? t.endedAt;
      t.activeMs = row.activeMs ?? t.activeMs;
      t.modelDurationMs = row.modelDurationMs ?? t.modelDurationMs;
      const terminal = row.state != null && /^(completedSuccess|completed|failed|stopped|cancelled)/.test(row.state);
      t.streaming = row.state != null && !terminal;
      if (terminal || row.endedAt != null) t.turnEnded = true;
      else if (row.state === "running") t.turnEnded = false;
      if (row.sourceCommandId) t.sourceCommandId = row.sourceCommandId;
      scheduleScan();
    } else if (row.kind === "userInput") {
      if ((t.endedAt != null || t.lastUsageAt != null) && row.createdAt && row.createdAt !== t.startedAt) resetTurnStats(t);
      t.startedAt = row.createdAt ?? t.startedAt;
      t.streaming = true;
      t.turnEnded = false;
      if (row.sourceCommandId) t.sourceCommandId = row.sourceCommandId;
      scheduleScan();
    } else if (row.kind === "reasoning" || row.kind === "assistantText") {
      if (row.assistantResponseId) respTurn.set(row.assistantResponseId, row.turnId);
      if (t.firstChunkAt == null && t.rowFirstAt == null && row.createdAt) {
        t.rowFirstAt = row.createdAt;   // 兜底首块（优先 stream.chunk）
      }
    }
  }

  function onEvent(ev) {
    if (!ev || ev.version !== 1) return;
    const scid = ev.sourceCommandId;
    if (diag) diag.eventKinds[ev.kind] = (diag.eventKinds[ev.kind] || 0) + 1;
    if (ev.kind === "usage.delta") {
      if (!scid) return;
      const t = findByScid(scid) || turns.get(scidTurn.get(scid));
      if (!t) return;
      const r = requestOf(t, ev.requestId);
      r.outputTokens += ev.outputTokens || 0;
      r.usageSeen = true;
      r.endedAt = ev.occurredAt ?? r.endedAt;
      t.outputTokens += ev.outputTokens || 0;
      t.inputTokens += ev.inputTokens || 0;
      t.cacheReadTokens += ev.cacheReadTokens || 0;
      t.cacheWriteTokens += ev.cacheWriteTokens || 0;
      t.reasoningTokens += ev.reasoningTokens || 0;
      t.totalTokens += ev.totalTokens || 0;
      t.sessionId = ev.sessionId || t.sessionId;
      t.lastUsageAt = ev.occurredAt ?? t.lastUsageAt;
      if (t.firstChunkAt == null && firstChunkByScid[scid] != null) {
        t.firstChunkAt = firstChunkByScid[scid];
      }
      // 只有本轮的首次请求才能借用轮级首包；后续请求必须用自己的 chunk 时间
      const firstReq = t.requests.values().next().value;
      if (r.firstChunkAt == null && firstReq === r && firstChunkByScid[scid] != null) {
        r.firstChunkAt = firstChunkByScid[scid];
      }
      scheduleScan();
    } else if (ev.kind === "stream.chunk") {
      // 子代理/工具内嵌的 chunk 不计入本轮首 token 与速度
      if (ev.parentToolCallId) return;
      if (scid && firstChunkByScid[scid] == null) firstChunkByScid[scid] = ev.occurredAt;
      let t = findByScid(scid);
      if (!t && ev.assistantMessageId) {
        const tid = respTurn.get(ev.assistantMessageId);
        if (tid) { t = turns.get(tid); if (t && scid) scidTurn.set(scid, tid); }
      }
      if (!t && scid && scidTurn.has(scid)) t = turns.get(scidTurn.get(scid));
      if (!t) return;
      let r = t.activeRequestId ? t.requests.get(t.activeRequestId) : null;
      // 上一个请求已结束（拿到 usage 或终态）后，新 chunk 视为下一请求；
      // 这样即使客户端没发 model.request.status，也能按 chunk 边界分请求。
      if (!r || r.usageSeen || r.statusEndedAt != null) r = newAnonRequest(t);
      const at = ev.occurredAt || Date.now();
      if (r.firstChunkAt == null) r.firstChunkAt = at;
      if (t.firstChunkAt == null) t.firstChunkAt = at;
      t.sessionId = ev.sessionId || t.sessionId;
      t.streaming = true;   // turnHeader 未到时也标记生成中
      scheduleScan();
    } else if (ev.kind === "model.request.status") {
      if (ev.querySource && ev.querySource !== "main_turn") return;
      const t = findByScid(scid) || (ev.turnId && turns.get(ev.turnId));
      if (!t) return;
      const r = requestOf(t, ev.requestId);
      if (ev.status === "model_request_started") {
        if (r.startedAt == null) {
          r.startedAt = ev.occurredAt;
        } else if (!r.usageSeen && r.startedAt !== ev.occurredAt) {
          r.startedAt = ev.occurredAt;
          r.firstChunkAt = null;
          r.statusEndedAt = null;
          r.statusDurationMs = null;
          r.metricsDurationMs = null;
          r.metricsTtft = null;
        }
        t.activeRequestId = r.requestId;
        t.streaming = true;
      } else if (ev.status === "model_retry_scheduled") {
        if (!r.usageSeen) {
          r.startedAt = ev.occurredAt;
          r.firstChunkAt = null;
          r.statusEndedAt = null;
          r.statusDurationMs = null;
          r.metricsDurationMs = null;
          r.metricsTtft = null;
        }
        t.activeRequestId = r.requestId;
        t.streaming = true;
      } else if (ev.status === "model_request_completed" || ev.status === "model_request_failed") {
        r.statusEndedAt = ev.occurredAt ?? r.statusEndedAt;
        if (ev.durationMs != null) r.statusDurationMs = ev.durationMs;
      }
      scheduleScan();
    } else if (ev.kind === "model.request.metrics") {
      if (ev.querySource && ev.querySource !== "main_turn") return;
      const t = findByScid(scid) || (ev.turnId && turns.get(ev.turnId));
      if (!t) return;
      const r = requestOf(t, ev.requestId);
      if (ev.durationMs != null) r.metricsDurationMs = ev.durationMs;
      if (ev.timeToFirstContentMs != null) r.metricsTtft = ev.timeToFirstContentMs;
      if (ev.outputTokens != null) r.metricsOutputTokens = ev.outputTokens;
      scheduleScan();
    }
  }

  const findByScid = (scid) => {
    // 先走映射表（usage.delta 每条都要调，原来是无脑全表扫描）
    if (scidTurn.has(scid)) {
      const t = turns.get(scidTurn.get(scid));
      if (t && t.sourceCommandId === scid) return t;
    }
    for (const t of turns.values()) if (t.sourceCommandId === scid) return t;
    return null;
  };

  function handleFrame(data) {
    try {
      let d = data;
      if (d == null) return;
      if (d instanceof ArrayBuffer) d = new Uint8Array(d);
      if (!ArrayBuffer.isView(d)) return;
      const txt = dec.decode(d);
      const i = txt.indexOf("{");
      if (i < 0) return;
      const j = JSON.parse(txt.slice(i));
      if (j.version === 1) { onEvent(j); return; }
      const payload = j.frame && j.frame.payload;
      if (!payload) return;
      const evs = payload.events || payload.deltas || [];
      for (const e of evs) {
        if (e.row) onRow(e.row);
        else if (e.op === "row.appended" || e.op === "row.upserted") onRow(e.row || e);
      }
    } catch (err) { /* 静默 */ }
  }

  // ---------- 格式化 ----------
  const fmtLat = (ms) => {
    const s = Math.max(0, (ms || 0) / 1000);
    return (s < 10 ? String(+s.toFixed(1)) : String(Math.round(s))) + "s";
  };
  const fmtTps = (v) => (v >= 10 ? String(Math.round(v)) : String(+Number(v || 0).toFixed(1)));
  const fmtTok = (v) => {
    if (v < 1e3) return String(v);
    const trim = (n) => String(+n.toFixed(n < 10 ? 1 : 0));
    if (v < 1e6) return trim(v / 1e3) + "k";
    if (v < 1e9) return trim(v / 1e6) + "m";
    return trim(v / 1e9) + "b";
  };
  // 平均命中率：固定两位小数（不足补零），四舍五入。
  // 先放大取整再回缩，规避 toFixed 的二进制表示误差（如 (1.005).toFixed(2) === "1.00"）。
  const fmtPct = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "0.00";
    return (Math.round(n * 100 + 1e-9) / 100).toFixed(2);
  };
  const fmtStamp = (ms) => {
    if (ms == null) return null;
    const d = new Date(ms), now = new Date();
    const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    return d.toDateString() === now.toDateString() ? hm
      : d.getFullYear() === now.getFullYear() ? `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`
      : `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
  };

  // 每个模型请求的真实统计。优先用客户端下发的 duration/TTFT，缺失时用事件时间戳兜底。
  function requestMetricsOf(t) {
    const out = [];
    let i = 0;
    for (const r of t.requests.values()) {
      const outTok = r.outputTokens || r.metricsOutputTokens || 0;
      // 第一个请求缺 started 事件时，用轮开始时间兜底；后续请求必须有自己的 started
      const reqStart = r.startedAt ?? (i === 0 ? t.startedAt : null);
      const ttft = r.metricsTtft ?? (
        reqStart != null && r.firstChunkAt != null ? r.firstChunkAt - reqStart : null
      );
      let decodeMs = null;
      if (r.metricsDurationMs != null && r.metricsTtft != null && r.metricsDurationMs > r.metricsTtft) {
        decodeMs = r.metricsDurationMs - r.metricsTtft;   // 官方口径：请求耗时扣除首 token 等待
      } else if (r.statusDurationMs != null && ttft != null && r.statusDurationMs > ttft) {
        decodeMs = r.statusDurationMs - ttft;
      } else if (r.firstChunkAt != null && r.statusEndedAt != null && r.statusEndedAt > r.firstChunkAt) {
        decodeMs = r.statusEndedAt - r.firstChunkAt;      // 请求终态事件时间兜底
      } else if (r.firstChunkAt != null && r.endedAt != null && r.endedAt > r.firstChunkAt) {
        decodeMs = r.endedAt - r.firstChunkAt;            // usage 事件时间兜底
      } else if (reqStart != null && r.endedAt != null && r.endedAt > reqStart) {
        decodeMs = r.endedAt - reqStart;                  // 无 chunk 时只能用整个请求时长
      }
      const tps = outTok > 0 && decodeMs != null && decodeMs > 0
        ? outTok / (decodeMs / 1000) : null;
      out.push({ requestId: r.requestId, outputTokens: outTok, ttft, decodeMs, tps, usageSeen: r.usageSeen, endedAt: r.endedAt });
      i++;
    }
    return out;
  }

  function statsOf(t) {
    const firstAt = t.firstChunkAt ?? t.rowFirstAt ?? null;
    let ttft = firstAt != null && t.startedAt != null ? firstAt - t.startedAt : null;
    const reqs = requestMetricsOf(t);
    const done = reqs.filter((r) => r.usageSeen);
    const latestDone = done.length
      ? done.reduce((a, b) => ((a.endedAt ?? 0) >= (b.endedAt ?? 0) ? a : b))
      : null;
    let tps = null;
    let ttftAveraged = false;
    let tpsAveraged = false;

    if (t.turnEnded) {
      // 首 token：本轮各请求的算术平均（首包到达即可计入，不等 usage）
      const ttfts = reqs.map((r) => r.ttft).filter((v) => v != null && v >= 0);
      if (ttfts.length) {
        ttft = ttfts.reduce((a, b) => a + b, 0) / ttfts.length;
        ttftAveraged = true;
      }

      // tok/s：按 outputTokens 加权的平均速度：Σ(out_i × tps_i) / Σ out_i
      const rated = done.filter((r) => r.tps != null && r.outputTokens > 0);
      const totalOut = rated.reduce((a, r) => a + r.outputTokens, 0);
      if (totalOut > 0) {
        tps = rated.reduce((a, r) => a + r.outputTokens * r.tps, 0) / totalOut;
        tpsAveraged = true;
      } else if (t.modelDurationMs != null && t.modelDurationMs > 500 && t.outputTokens > 0) {
        // 没有逐请求耗时时的兜底：官方 turn 级模型耗时（仍是真实数据，但不是加权平均）
        tps = t.outputTokens / (t.modelDurationMs / 1000);
      }
      if (tps == null && latestDone && latestDone.tps != null) tps = latestDone.tps;
    } else {
      // 请求进行中：首 token 逐请求更新；当前请求还没首包时先隐藏，不拿上一请求冒充
      const active = t.activeRequestId
        ? reqs.find((r) => r.requestId === t.activeRequestId)
        : null;
      const shown = active || (reqs.length ? reqs[reqs.length - 1] : null);
      ttft = shown && shown.ttft != null && shown.ttft >= 0 ? shown.ttft : null;
      // tok/s 同上：只在请求完成后显示该请求的真实速度
      if (latestDone && latestDone.tps != null) tps = latestDone.tps;
    }

    return {
      stamp: fmtStamp(t.endedAt || t.startedAt), ttft, tps,
      ttftAveraged, tpsAveraged,
      out: t.outputTokens || 0, reasoning: t.reasoningTokens || 0,
      streaming: t.streaming,
    };
  }

  const hasActivity = (t) => t.streaming || t.requests.size > 0 || t.outputTokens > 0;

  // ---------- 渲染 ①: 输入框工具栏中央（当前会话最近一轮） ----------
  // 工具栏行的定位策略（按稳定性排序，逐级回退；不依赖任何随版本/语言变化的文案）：
  //   a) composer 容器内带 items-end 的 flex 行，且含交互控件（button/select/combobox）
  //   b) composer 容器内最后一个 items-end 的 flex 行
  // 历史：旧实现用 textContent.includes("完全访问") 判定，3.12.2 起该文案改为按 i18n key
  // 动态渲染，DOM 文本不再稳定命中 → 改为结构特征判定。
  // 诊断：window.__ztpsDiag 暴露每一环的探测结果（选择器命中数/候选行数/最终选择），
  // 版本升级后若工具栏消失，先在 console 看该对象定位失败环节。
  const COMPOSER_INPUT_SELECTORS = [
    "[data-testid='v4-composer-input']",
    "[data-testid*='composer-input']",
    "textarea[data-testid]",
    "form textarea",
    "textarea",
  ];

  function findComposerInput() {
    const tried = [];
    for (const sel of COMPOSER_INPUT_SELECTORS) {
      let hit = null;
      try {
        let els = Array.from(document.querySelectorAll(sel));
        if (els.length) {
          // ① 严格剔除侧边栏/辅助对话中的输入框，绝不将状态栏挂入侧边栏
          els = els.filter((e) => !isInsideSidePane(e));
          if (els.length) {
            // ② 优先取处于主会话容器 [data-workspace-conversation-frame] 内的输入框
            const mainEls = els.filter(isInsideMainConversation);
            const pool = mainEls.length ? mainEls : els;
            if (pool.length === 1) {
              hit = pool[0];
            } else {
              const vis = pool.filter((e) => e.offsetParent != null);
              const candidates = vis.length ? vis : pool;
              if (candidates.length === 1) {
                hit = candidates[0];
              } else {
                // ③ 排序准则：以最靠下为主要准则；但当纵向坐标极度接近（<20px，左右并排）时，
                //    绝对优先选择靠左（主工作区，left 较小）的主会话输入框，彻底杜绝跳动
                candidates.sort((a, b) => {
                  const ra = a.getBoundingClientRect();
                  const rb = b.getBoundingClientRect();
                  const dTop = rb.top - ra.top;
                  if (Math.abs(dTop) > 20) return dTop;
                  return ra.left - rb.left;
                });
                hit = candidates[0];
              }
            }
          }
        }
      } catch (err) { /* 选择器不合法，跳过 */ }
      tried.push({ selector: sel, count: hit ? 1 : 0 });
      if (hit) return { el: hit, tried };
    }
    return { el: null, tried };
  }

  // composer 卡片(v4-composer):工具栏行与「输入框下方」条共用的定位基准
  function findComposerCard() {
    const { el: ta, tried } = findComposerInput();
    if (diag) {
      diag.inputTried = tried;
      diag.inputFound = !!ta;
    }
    if (!ta) return null;
    let card = null;
    try { card = ta.closest("[data-testid='v4-composer']"); } catch (err) { /* ignore */ }
    if (!card) card = ta.closest("form") ? ta.closest("form").parentElement : null;
    if (!card) card = ta.parentElement;
    if (card && isInsideSidePane(card)) return null;   // 侧边栏卡片直接拒收
    return card || null;
  }

  function findToolbarRow() {
    const card = findComposerCard();
    if (!card || isInsideSidePane(card)) return null;

    // 从某元素向上找「工具栏行」：class 同时含 flex 与 items-end 的 div，且不得越过 card
    const rowFrom = (el, card) => {
      let cur = el;
      while (cur && cur !== card.parentElement) {
        if (cur.tagName === "DIV") {
          const c = typeof cur.className === "string" ? cur.className : "";
          if (/(^|\s)flex(\s|$)/.test(c) && /items-end/.test(c)) return cur;
        }
        cur = cur.parentElement;
      }
      return null;
    };

    // ① 首选：发送/停止按钮向上找工具栏行（生成中发送按钮会被官方切换为停止按钮 v4-stop）。
    let picked = null;
    try {
      const btn = card.querySelector("[data-testid='v4-composer-send'], [data-testid='v4-stop']");
      if (btn) picked = rowFrom(btn, card);
    } catch (err) { /* ignore */ }

    // ② 回退：card 内所有 items-end 行，取含交互控件的那一行（或最后一个）
    if (!picked) {
      const rows = Array.from(card.querySelectorAll("div")).filter((el) => {
        const c = typeof el.className === "string" ? el.className : "";
        return /(^|\s)flex(\s|$)/.test(c) && /items-end/.test(c);
      });
      if (diag) diag.rowCandidates = rows.length;
      if (!rows.length) return null;
      picked = rows.find((el) => el.querySelector("button,select,[role='combobox'],input")) ||
               rows[rows.length - 1];
    } else if (diag) {
      diag.rowCandidates = 1;
      diag.rowVia = "send-button";
    }

    if (diag) {
      diag.pickedRowText = (picked.textContent || "").slice(0, 60);
      diag.pickedRowClass = String(picked.className || "").slice(0, 120);
      diag.cardTag = card.tagName;
    }
    return picked;
  }

  // 诊断对象（console 里直接看 window.__ztpsDiag）
  let diag = null;
  try {
    diag = window.__ztpsDiag = window.__ztpsDiag || {};
    diag.scriptVersion = "3.18-real";
    diag.eventKinds = diag.eventKinds || {};
    diag.loadedAt = new Date().toISOString();
  } catch (err) { /* ignore */ }

  // ---------- 挂载点 ----------
  // 三种模式共用同一个 host(含内层 pill);切换模式时搬移节点并重置样式。
  //   composer-below: composer 卡片之后的独立一行(默认),与输入框同宽,承载会话累计统计
  //   toolbar:        工具栏行内水平居中
  //   session-top:    消息区滚动容器首子元素,sticky 吸顶;负 margin-bottom 抵消文档流占位,
  //                   host 上 pointer-events:none 放行下方消息,胶囊本体恢复 auto
  function attach(host, mode) {
    const changed = host.getAttribute("data-ztps-mode") !== mode;
    // 快路径：宿主已在文档树中、模式未变、且处于正确的父/兄弟节点下，无需重新查找 composer，0开销返回
    if (!changed && host.isConnected && host._target && host._target.isConnected) {
      if (mode === "composer-below" && host.previousElementSibling === host._target) return true;
      if (mode === "session-top" && host.parentElement === host._target) return true;
      if (mode === "toolbar" && host.parentElement === host._target) return true;
    }
    if (mode === "composer-below") {
      const card = findComposerCard();
      if (!card || !card.parentElement || isInsideSidePane(card)) return false;
      if (changed || host.parentElement !== card.parentElement || host.previousElementSibling !== card) {
        card.insertAdjacentElement("afterend", host);
        host.setAttribute("data-ztps-mode", mode);
        host.style.cssText = "";
        Object.assign(host.style, {
          display: "flex", justifyContent: "center",
          alignItems: "center",
          marginTop: "6px", padding: "0",
          alignSelf: "stretch",          // 与 composer 卡片同宽
          pointerEvents: "auto",
        });
      }
      host._target = card;
    } else if (mode === "session-top") {
      const sc = findSessionScroller();
      if (!sc || isInsideSidePane(sc)) return false;
      if (changed || host.parentElement !== sc) {
        sc.insertBefore(host, sc.firstChild);
        host.setAttribute("data-ztps-mode", mode);
        host.style.cssText = "";
        Object.assign(host.style, {
          position: "sticky", top: "8px", zIndex: "30",
          width: "100%", height: "22px",
          display: "flex", justifyContent: "center",
          margin: "0 0 -30px", padding: "0",
          pointerEvents: "none",
        });
      }
      host._target = sc;
    } else {
      const row = findToolbarRow();
      if (!row || isInsideSidePane(row)) return false;
      if (changed || host.parentElement !== row) {
        row.insertBefore(host, row.children[1] || null);
        host.setAttribute("data-ztps-mode", mode);
        host.style.cssText = "";
        Object.assign(host.style, {
          display: "inline-flex", flex: "0 1 auto",
          minWidth: "0", maxWidth: "50%",
          marginLeft: "auto", marginRight: "auto",   // 两侧 auto = 工具栏行内水平居中
          alignSelf: "center", height: "22px",
          margin: "0", padding: "0", position: "static",
          pointerEvents: "auto",
        });
        row.style.alignItems = "center";
      }
      host._target = row;
    }
    const pill = host.firstChild;
    if (pill) {
      pill.style.pointerEvents = "auto";
      pill.title = "ZCode TPS · 右键切换位置";
    }
    return true;
  }

  function renderBar() {
    try {
      const mode = getPos();
      // 只认当前主会话 DOM 里可见的轮次——排除侧边栏辅助对话的轮次干扰
      const mainFrame = document.querySelector("[data-workspace-conversation-frame]");
      let domSess = null;
      if (mainFrame) {
        domSess = mainFrame.getAttribute("data-session-id");
        if (!domSess) {
          const innerSess = mainFrame.querySelector("[data-session-id]");
          if (innerSess) domSess = innerSess.getAttribute("data-session-id");
        }
      }

      const visible = new Set();
      // 只有在内存中有轮次数据时才扫描可见 DOM；若 turns 为空（新启动/仅看历史），直接跳过数百个节点的 DOM 遍历
      if (turns.size > 0) {
        if (mainFrame) {
          mainFrame.querySelectorAll("section[data-turn-id]").forEach((el) => {
            visible.add(el.getAttribute("data-turn-id"));
          });
        } else {
          document.querySelectorAll("section[data-turn-id]").forEach((el) => {
            if (!isInsideSidePane(el)) visible.add(el.getAttribute("data-turn-id"));
          });
        }
      }
      lastVisible = visible;
      // 会话累计:轮数 / 累计输入 / 累计缓存命中 / 累计思考 / 累计输出(仅当前可见会话的轮次)
      const agg = { rounds: 0, input: 0, cache: 0, cacheWrite: 0, reasoning: 0, output: 0 };
      let latest = null;
      for (const t of turns.values()) {
        if (!visible.has(t.msgId)) continue;
        // 轮次归属会话与当前显示会话不符时排除(会话切换的 DOM 中间态残留兜底)
        if (t.sessionId && domSess && t.sessionId !== domSess) continue;
        if (t.startedAt != null || hasActivity(t)) agg.rounds++;
        agg.input += t.inputTokens || 0;
        agg.cache += t.cacheReadTokens || 0;
        agg.cacheWrite += t.cacheWriteTokens || 0;
        agg.reasoning += t.reasoningTokens || 0;
        // 只用真实 usage；请求未完成时暂时不计入，usage.delta 到达后一次性补上
        agg.output += t.outputTokens || 0;
        if (!latest || (t.startedAt ?? 0) > (latest.startedAt ?? 0)) latest = t;
      }
      let host = document.querySelector("[" + MARK + "-bar]");
      if (diag) {
        diag.domSessionId = domSess;
        diag.visibleTurns = visible.size;
        diag.turnStats = turns.size;
        diag.latestFound = !!latest;
        diag.posMode = mode;
        if (latest) {
          diag.latest = {
            msgId: latest.msgId, sessionId: latest.sessionId,
            startedAt: latest.startedAt, endedAt: latest.endedAt,
            streaming: latest.streaming, out: latest.outputTokens,
            reasoning: latest.reasoningTokens, requests: latest.requests.size,
            turnEnded: latest.turnEnded,
          };
        }
        diag.hostAttached = !!host;
      }
      // 数据态:轮次有时间戳或生成活动;空态:当前会话无任何轮次(新会话/未对话)。
      // 状态栏常驻:空态显示仅绿点的胶囊(空闲静态),不拿当前时间冒充轮次时间
      const hasData = !!latest && (hasActivity(latest) || latest.endedAt != null || latest.startedAt != null);
      if (diag) diag.hiddenReason = hasData ? null : "空态(无轮次数据)";
      if (!host) {
        host = document.createElement("div");
        host.setAttribute("data-ztps-bar", "1");
        host.setAttribute("data-ztps-mode", mode);
        const pill = document.createElement("div");
        Object.assign(pill.style, {
          display: "inline-flex", alignItems: "center", gap: "6px",
          minWidth: "0",
          fontSize: "11px", height: "20px", userSelect: "none",
          fontVariantNumeric: "tabular-nums",
          whiteSpace: "nowrap", overflow: "hidden",
          borderRadius: "999px",
          background: "rgba(127,127,127,0.08)",
          padding: "0 10px",
        });
        pill.addEventListener("contextmenu", (e) => e.preventDefault());
        host.appendChild(pill);
      }
      // 挂载点失效(找不到工具栏行/会话滚动容器,新建任务空态、设置页等)→ 移除,杜绝旧内容残留
      if (!attach(host, mode)) {
        if (diag) diag.hiddenReason = "挂载点未找到(" + mode + ")";
        closePosMenu();
        host.remove();
        return;
      }
      const pill = host.firstChild;
      if (!hasData) {
        // 空态:只显示空闲绿点;右键切位置等交互照常
        if (pill._zkey !== "empty") {
          pill._zkey = "empty";
          pill._zsegs = [];
          pill.style.background = "rgba(127,127,127,0.08)";
          pill.style.padding = "0 10px";
          pill.innerHTML = "";
          const dot = document.createElement("span");
          dot.textContent = "●";
          dot.style.color = "#4ade80";
          pill.appendChild(dot);
        }
        return;
      }
      const s = statsOf(latest);
      // 内容签名:未变化时完全跳过 DOM 写入与排版回流，达到零开销
      const key = [s.stamp, s.ttft, s.ttftAveraged, s.tps, s.tpsAveraged, s.out, s.reasoning, s.streaming,
                   agg.rounds, agg.input, agg.cache, agg.cacheWrite, agg.reasoning, agg.output].join("|");
      let segs = pill._zsegs;
      if (pill._zkey !== key) {
        pill._zkey = key;
        pill.style.background = "rgba(127,127,127,0.08)";
        pill.style.padding = "0 10px";
        pill.style.color = "var(--color-foreground-subtle, var(--ztps-dim, #5b6470))";
        pill.innerHTML = "";
        const ACCENT = "var(--color-warning, var(--ztps-accent, #b45309))";
        const VALUE = "var(--color-foreground, var(--ztps-fg, #1b1f24))";
        const span = (txt, cls) => {
          const sp = document.createElement("span");
          sp.textContent = txt;
          if (cls === "ACCENT") sp.style.color = ACCENT;
          else if (cls === "VALUE") sp.style.color = VALUE;
          return sp;
        };
        // 绿点常驻:生成中发亮,空闲静态
        const dot = span("●");
        dot.style.color = "#4ade80";
        if (s.streaming) dot.style.textShadow = "0 0 6px rgba(74,222,128,.8)";
        pill.appendChild(dot);
        // 段布局:本轮即时指标(g0:首 token/tok/s/思考)+ 会话累计(g1:轮/总用/输入/命中+命中率/累思/累出)。
        // tok/s 只在请求完成后用真实 tokens 更新；轮结束显示按 outputTokens 加权的平均。
        segs = [];
        const ttftLabel = s.ttftAveraged ? "平均首 token " : "首 token ";
        if (s.ttft != null && s.ttft >= 0) segs.push({ p: 1, g: 0, nodes: [span(ttftLabel), span(fmtLat(s.ttft), "VALUE")] });
        if (s.tps != null) segs.push({ p: 2, g: 0, nodes: [span((s.tpsAveraged ? "平均 " : "") + fmtTps(s.tps) + " tok/s", "ACCENT")] });
        if (s.reasoning > 0) segs.push({ p: 2.5, g: 0, nodes: [span("思考 "), span(fmtTok(s.reasoning), "ACCENT")] });
        if (agg.rounds > 0) segs.push({ p: 4, g: 1, nodes: [span("第 " + agg.rounds + " 轮")] });
        const totalTok = (agg.input || 0) + (agg.output || 0);
        if (totalTok > 0) segs.push({ p: 3, g: 1, nodes: [span("总用 "), span(fmtTok(totalTok), "VALUE")] });
        if (agg.input > 0) {
          const nodes = [span("输入 "), span(fmtTok(agg.input), "VALUE")];
          if (agg.cache > 0) {
            nodes.push(span("命中 " + fmtTok(agg.cache), "VALUE"));
            const pct = agg.input > 0 ? (agg.cache / agg.input) * 100 : 0;
            if (pct > 0) nodes.push(span(" 平均命中 " + fmtPct(pct) + "%", "ACCENT"));
          }
          segs.push({ p: 5, g: 1, nodes });
        }
        if (agg.reasoning > 0 && !(s.reasoning > 0)) {
          segs.push({ p: 5.5, g: 1, nodes: [span("累思 "), span(fmtTok(agg.reasoning), "VALUE")] });
        }
        if (agg.output > 0) segs.push({ p: 6, g: 1, nodes: [span("累出 "), span(fmtTok(agg.output), "VALUE")] });
        segs.forEach((g, i) => {
          if (i > 0) {
            const cross = segs[i - 1].g !== g.g;   // 跨组:竖线分隔,更醒目
            const sep = span(cross ? "│" : "·");
            sep.style.opacity = cross ? "0.45" : "0.55";
            g.nodes.unshift(sep);   // 分隔符跟段一起,降级时同生共死
          }
        });
        segs.forEach((g) => g.nodes.forEach((n) => pill.appendChild(n)));
        pill._zsegs = segs;

        // 仅在文本内容有变动时，才在 rAF 中执行溢出降级检查，杜绝无意义的 scrollWidth 强制回流
        try {
          requestAnimationFrame(() => {
            try {
              for (const drop of [3, 1, 2]) {
                if (pill.scrollWidth <= pill.clientWidth + 1) break;
                const g = segs.find((x) => x.p === drop);
                if (!g) continue;
                g.nodes.forEach((n) => n.remove());
              }
            } catch (err) { /* 静默 */ }
          });
        } catch (err) { /* 静默 */ }
      }
    } catch (err) { /* 静默 */ }
  }

  // ---------- 渲染 ②: 清理历史遗留的逐轮统计行（统计只在工具栏展示） ----------
  // 轮次表回收：turns / respTurn / scidTurn / firstChunkByScid 原来只增不删，
  // 长会话或长时间挂着会一直涨。规则：不在当前可见轮次里、且 30 分钟没有活动的记录删掉。
  const PRUNE_MS = 30 * 60 * 1000;
  let lastVisible = null;
  function prune() {
    const now = Date.now();
    for (const [id, t] of turns) {
      if (lastVisible && lastVisible.has(id)) continue;
      const last = t.lastUsageAt || t.endedAt || t.startedAt || 0;
      if (now - last <= PRUNE_MS) continue;
      turns.delete(id);
      if (t.sourceCommandId) scidTurn.delete(t.sourceCommandId);
      for (const [rid, tid] of respTurn) if (tid === id) respTurn.delete(rid);
    }
    for (const k of Object.keys(firstChunkByScid)) {
      if (!scidTurn.has(k) && now - (firstChunkByScid[k] || 0) > PRUNE_MS) delete firstChunkByScid[k];
    }
  }

  function removeLegacyFooters() {
    try {
      // 任何误落入侧边栏/辅助对话中的 bar 节点，强制摘除
      document.querySelectorAll(`[${MARK}-bar]`).forEach((el) => {
        if (isInsideSidePane(el)) el.remove();
      });
      document.querySelectorAll(`[${MARK}]:not([data-ztps-bar])`).forEach((el) => el.remove());
    } catch (err) { /* 静默 */ }
  }

  function scan() {
    renderBar();
  }

  function start() {
    ensureStyle();
    removeLegacyFooters();
    // 彻底移除 document.body 的 MutationObserver，杜绝长会话挂载时的底层 C++ 监听负荷。
    // 流式生成期间 100% 由 MessagePort 消息帧事件驱动实时刷新（0 延迟）；
    // 会话常驻状态栏通过 500ms 轻量巡检维护（挂载状态下瞬时短路，零开销）。
    setInterval(() => {
      if (document.hidden) return;
      scheduleScan();
    }, 500);
    // 低频维护（5s）：清理过期轮次数据与遗留废弃节点
    setInterval(() => {
      if (document.hidden) return;
      prune();
      removeLegacyFooters();
    }, 5000);
    scan();
  }
  if (document.body) start();
  else document.addEventListener("DOMContentLoaded", start);

  // ---------- 端口获取 ----------
  function hookPort(port) {
    if (!port || port.__ztps) return;
    port.__ztps = true;
    // 关键：只注册监听，**绝不调用 port.start()**
    // start() 会让消息只派发给"启用瞬间"的监听器，从而吃掉应用的 Initialize 握手。
    // 交给应用 start；届时所有已注册监听器（含我们）同时收到消息。
    port.addEventListener("message", (ev) => handleFrame(ev.data));
    window.__ztpsPort = port;   // 调试用
    if (diag) {
      diag.portHooked = true;
      diag.portStarted = false;   // 恒为 false —— 本版本刻意不 start
      diag.portHookedAt = new Date().toISOString();
    }
  }
  // 端口转交的消息格式跨版本不同，两种都要认：
  //   旧版（≤3.11.x）：window.postMessage("zcode:service-port", "*", [port])   → data 为字符串
  //   新版（3.12.2+）：window.postMessage({type:"zcode:service-port",...}, "*", [port]) → data 为对象
  // 只比较字符串会让新版端口永远不被接管（表现为 turnStats 恒为 0、胶囊不显示）。
  function takePortIfHandoff(d) {
    if (!d) return false;
    if (d === "zcode:service-port") return true;
    if (typeof d === "object" && d.type) {
      return d.type === "zcode:service-port" || d.type === "zcode:scoped-service-port";
    }
    return false;
  }
  window.addEventListener("message", (e) => {
    if (e.source !== window) return;
    if (diag) {
      diag.lastMessage = typeof e.data === "string" ? e.data
        : (e.data && e.data.type) || Object.prototype.toString.call(e.data);
      diag.lastMessagePorts = (e.ports || []).length;
    }
    if (takePortIfHandoff(e.data)) {
      if (e.ports && e.ports[0]) hookPort(e.ports[0]);
    }
  }, true);

  window.__ztpsTurns = turns;
  window.__ztpsHook = hookPort;   // 调试用：热注入时可对存量端口手动补挂
  // 自检：加载 5s 后若仍未接管端口，把诊断信息打到 console（便于版本升级后排障）
  setTimeout(() => {
    if (diag) {
      diag.portInjectedAt = diag.portInjectedAt || null;
      diag.diagReady = true;
      if (!diag.portHooked) {
        console.warn("[zcode-tps] 未接管到服务端口，统计将不可用。window.__ztpsDiag =", diag);
      } else {
        console.info("[zcode-tps] 已接管端口，window.__ztpsDiag =", diag);
      }
    }
  }, 5000);
})();
