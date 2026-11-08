(function () {
  if (window.__jobbotLoaded) return;
  window.__jobbotLoaded = true;

  const P = window.JOBBOT_PROFILE;
  // 每一枚写进存储的状态都盖上「哪一版扩展写的」。没有这一枚，事后读到 `entryNavigatedFor` 缺失
  // 就分不开「这一场没触发那道闸」与「写它的扩展根本不认识这个字段」——E／F 两格一直卡在这上面。
  // `getManifest()` 在内容脚本里是同步可用的；取不到时留空串，不许让一次状态写盘整条抛出去。
  const EXT_VERSION = (() => {
    try {
      return (chrome.runtime.getManifest && chrome.runtime.getManifest().version) || "";
    } catch (error) {
      return "";
    }
  })();
  const CARD = ".job-card-wrap";
  const CHAT_BTN = ".btn-startchat, .op-btn-chat";
  const CHAT_INPUT =
    "div.chat-input[contenteditable='true'], .chat-input[contenteditable='true'], #chat-input[contenteditable='true'], .dialog-wrap [contenteditable='true'], .dialog-container [contenteditable='true'], .dialog-wrap textarea, .dialog-container textarea, textarea[placeholder*='问题']";
  const MESSAGE = ".message-item, .chat-message";

  const state = { job: null, greeting: "", auto: null, match: null };
  // autoPlan/autoSendCurrent 内部有同名局部 state（存储里的计划对象），
  // 桥的判定必须放这个模块级变量，否则被遮蔽后闸门拿到 undefined 就等于不拦。
  let lastMatch = null;
  let panelEl = null;
  let statusEl = null;
  let jobEl = null;
  let greetingEl = null;
  let logEl = null;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const text = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : "");
  const q = (sel, root = document) => root.querySelector(sel);
  const qa = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const visible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  };

  function ensurePanel() {
    if (panelEl && document.documentElement.contains(panelEl)) return;
    panelEl = document.createElement("div");
    panelEl.id = "jobbot-panel";
    panelEl.innerHTML = `
      <div class="jb-head"><span>jobbot</span><span class="jb-status" id="jb-status">待命</span></div>
      <div class="jb-body">
        <div class="jb-job" id="jb-job"></div>
        <textarea id="jb-greeting" placeholder="AI 招呼语"></textarea>
        <div class="jb-actions">
          <button id="jb-scan">扫描本页</button>
          <button id="jb-ai">生成招呼语</button>
          <button id="jb-send">发送</button>
          <button class="secondary" id="jb-export">导出结果</button>
          <button class="secondary" id="jb-html">导出HTML</button>
        </div>
        <div class="jb-log" id="jb-log"></div>
      </div>`;
    document.documentElement.appendChild(panelEl);
    statusEl = q("#jb-status", panelEl);
    jobEl = q("#jb-job", panelEl);
    greetingEl = q("#jb-greeting", panelEl);
    logEl = q("#jb-log", panelEl);

    q("#jb-scan", panelEl).addEventListener("click", () => {
      const cards = qa(CARD);
      setStatus(`找到 ${cards.length} 个职位卡`);
      if (cards.length) {
        state.job = extractJob(cards[0]);
        setJob(state.job);
      }
    });
    q("#jb-ai", panelEl).addEventListener("click", async () => {
      if (!state.job) {
        setStatus("请先扫描职位");
        return;
      }
      await makeGreeting(state.job);
    });
    q("#jb-send", panelEl).addEventListener("click", async () => {
      if (!state.job || !state.greeting) {
        setStatus("缺少职位或招呼语");
        return;
      }
      state.auto = "manual";
      await openChat(state.job, state.greeting, "manual");
    });
    q("#jb-export", panelEl).addEventListener("click", exportState);
    q("#jb-html", panelEl).addEventListener("click", downloadHtml);
  }

  function setStatus(message) {
    ensurePanel();
    statusEl.textContent = message;
    console.log("[jobbot]", message);
  }

  function log(message) {
    ensurePanel();
    logEl.textContent += `${new Date().toLocaleTimeString()} ${message}\n`;
    logEl.scrollTop = logEl.scrollHeight;
  }

  function setJob(job) {
    ensurePanel();
    jobEl.textContent = job ? `${job.title} · ${job.company} · ${job.salary}` : "";
  }

  function setGreeting(greeting) {
    ensurePanel();
    greetingEl.value = greeting || "";
  }

  function extractJob(card) {
    const link = card.querySelector('a.job-name, a[href*="/job_detail/"]');
    const href = link ? new URL(link.getAttribute("href"), location.href).href : "";
    const match = href.match(/\/job_detail\/([^/?#]+)\.html/);
    const cardTags = qa(".tag-list li", card).map(text).filter(Boolean);
    const detailTags = qa(".job-label-list li").map(text).filter(Boolean);
    const tags = Array.from(new Set([...cardTags, ...detailTags]));
    const detailBody = q(".job-detail-body");
    const detailText = detailBody ? detailBody.innerText.replace(/\s+/g, " ").trim() : "";
    const cardText = text(card);
    const salaryCipher = text(q(".job-salary", card));
    return {
      id: match ? match[1] : "",
      url: href,
      title: text(q(".job-name", card)),
      company: text(q(".boss-name", card)) || text(q(".company-name", card)),
      salary: decodeSalary(salaryCipher),
      salaryRaw: salaryCipher,
      searchCity: planCity,
      area: text(q(".company-location", card)),
      tags,
      description: stripUiChrome(detailText).slice(0, 2000),
      alreadyChat: cardText.includes("已沟通") || cardText.includes("继续沟通"),
    };
  }

  function pickCard() {
    const cards = qa(CARD).filter(visible);
    if (!cards.length) return null;
    const params = new URLSearchParams(location.hash.replace(/^#/, ""));
    const index = params.get("jobbot_card");
    if (index !== null && cards[Number(index)]) return cards[Number(index)];
    return cards.find((card) => !card.innerText.includes("在校/应届")) || cards[0];
  }

  function findChatButton() {
    const scope = q(".job-detail-op") || document;
    const candidates = qa(CHAT_BTN, scope).filter(visible);
    for (const el of candidates) {
      const label = text(el);
      if (label.includes("继续沟通")) return { el, label, already: true };
      if (label.includes("立即沟通")) return { el, label, already: false };
    }
    for (const el of qa("a,button", scope)) {
      if (!visible(el)) continue;
      const label = text(el);
      if (label.includes("继续沟通")) return { el, label, already: true };
      if (label.includes("立即沟通")) return { el, label, already: false };
    }
    return null;
  }

  async function requestGreeting(job) {
    // 只走 service worker：平台实测里页面直连 127.0.0.1 的请求会被拦（09:10 那轮
    // 拿到 greeting-null），而 SW 这一跳在 11-02 之前一直可用。
    return await chrome.runtime.sendMessage({ type: "ai_greeting", job });
  }

  async function requestScore(job) {
    return await chrome.runtime.sendMessage({ type: "score_job", job });
  }

  async function requestAiScore(job) {
    return await chrome.runtime.sendMessage({ type: "ai_score", job });
  }

  async function scoreCandidate(job) {
    const result = await requestScore(job);
    if (!result || result.ok === false) {
      const error = result ? result.error || "评分失败" : "无响应";
      log(`评分失败：${error}`);
      return { score: null, minScore: null, passed: null, risks: [], error, aiApplied: false };
    }
    const score = typeof result.score === "number" ? result.score : null;
    const minScore = typeof result.min_score === "number" ? result.min_score : null;
    const match = {
      score,
      minScore,
      passed: typeof result.passed === "boolean" ? result.passed : null,
      risks: result.risks || [],
      source: "rule",
      ruleScore: score,
      aiApplied: false,
      aiError: "",
      // 台账去重（桥查 `applications` 的 sent 全集）：比扩展自己那份发送历史更全
      duplicate: result.duplicate === true,
      duplicateReason: result.duplicate_reason || "",
      // 拦下的种类（硬拦／对表）由桥说，别让它到面板里统一变成「分数不够」
      skipped: result.skipped === true,
      reasons: result.reasons || [],
    };
    state.match = match;
    lastMatch = match;
    log(`规则分：${score ?? "-"}/${minScore ?? "-"} ${match.passed ? "通过" : "拦下"}`);
    if (match.risks.length) log(`风险 ${match.risks.length} 条：${match.risks.join("；")}`);
    return match;
  }

  async function aiScoreCandidate(job, ruleMatch) {
    setStatus(`AI 精排中：${job.title}（本地模型约 3–8 分钟）…`);
    log(`AI 精排：${job.title} / ${job.company}`);
    const result = await requestAiScore(job);
    if (!result || result.ok === false || result.ai_applied !== true) {
      const why = result
        ? result.ai_error || result.error || "AI 精排未返回有效结果"
        : "无响应";
      setStatus(`AI 精排失败：${why}`);
      log(`AI 精排失败：${why}（规则分 ${ruleMatch.score} 不兜底，停止本轮）`);
      throw new Error(`AI 精排失败：${why}`);
    }
    const score = typeof result.score === "number" ? result.score : ruleMatch.score;
    const minScore = typeof result.min_score === "number" ? result.min_score : ruleMatch.minScore;
    const match = {
      score,
      minScore,
      passed: typeof result.passed === "boolean" ? result.passed : null,
      risks: result.risks || [],
      reasons: result.reasons || [],
      source: "ai",
      ruleScore: typeof result.rule_score === "number" ? result.rule_score : ruleMatch.score,
      aiFit: typeof result.ai_fit === "number" ? result.ai_fit : null,
      aiApplied: true,
      aiError: "",
    };
    state.match = match;
    lastMatch = match;
    log(
      `AI 精排完成：fit=${match.aiFit ?? "-"}，规则 ${match.ruleScore} → 合并 ${score}/${minScore} ${
        match.passed ? "通过" : "拦下"
      }`
    );
    if (match.risks.length) log(`精排风险 ${match.risks.length} 条：${match.risks.join("；")}`);
    return match;
  }

  async function makeGreeting(job) {
    setStatus("AI 生成中（本地模型约 2-3 分钟）…");
    log(`AI: ${job.title} / ${job.company}`);
    lastMatch = null;
    const result = await requestGreeting(job);
    if (!result || !result.greeting) {
      const error = result ? result.error || "桥没给正文" : "无响应";
      setStatus(`AI 失败：${error}`);
      log(`AI 失败：${error}`);
      return null;
    }
    const greeting = result.greeting.replace(/\s+/g, " ").trim().slice(0, 160);
    state.greeting = greeting;
    setGreeting(greeting);
    const score = typeof result.score === "number" ? result.score : null;
    // 桥没给 passed 字段（旧版桥）时记 null：自动发送只认 passed === true，缺判定一律不发
    state.match = {
      score,
      minScore: typeof result.minScore === "number" ? result.minScore : null,
      passed: typeof result.passed === "boolean" ? result.passed : null,
      risks: result.risks || [],
      source: result.source || "template",
    };
    lastMatch = state.match;
    setStatus(`AI 完成（${Math.round((result.elapsed || 0) / 1000)}s，${result.source || "ai"}，${score ?? "-"} 分）`);
    log(`招呼语：${greeting}`);
    const risks = result.risks || [];
    if (risks.length) log(`风险 ${risks.length} 条：${risks.join("；")}`);
    if (state.match.passed === false) log(`规则分 ${score} 低于阈值 ${state.match.minScore}，自动发送会跳过`);
    return greeting;
  }

  async function openChat(job, greeting, auto) {
    if (auto !== "manual" && !(state.match && state.match.passed === true)) {
      const score = state.match ? state.match.score : "无";
      setStatus(`已拦下（不发送）：没有「通过」判定（规则分 ${score}）`);
      log(`拦下原因：${(state.match && state.match.risks ? state.match.risks : []).join("；") || "无风险条目"}`);
      await finish("skipped", `自动发送只放行 passed=true 的岗位（规则分 ${score}）`);
      return false;
    }
    if (!job.id) {
      setStatus("缺少 job id");
      return false;
    }
    const button = findChatButton();
    if (!button) {
      setStatus("未找到「立即沟通」按钮");
      log("未找到沟通按钮，可能页面未选中职位或已沟通");
      return false;
    }
    if (button.already) {
      setStatus("该职位已沟通，跳过");
      return false;
    }
    await chrome.storage.local.set({
      pendingChat: {
        job,
        greeting,
        auto,
        created: Date.now(),
        status: "opened",
      },
    });
    setStatus("打开聊天页…");
    button.el.click();
    await sleep(3000);
    if (await sendHere(greeting)) {
      await finish("sent", "");
      return true;
    }
    setStatus("已打开聊天页，等待发送");
    return false;
  }

  function fillInput(input, greeting) {
    input.focus();
    if (input.isContentEditable) {
      input.textContent = "";
      document.execCommand("selectAll", false, null);
      const inserted = document.execCommand("insertText", false, greeting);
      if (!inserted || !text(input)) input.innerText = greeting;
    } else {
      const proto = input instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      if (setter) setter.call(input, greeting);
      else input.value = greeting;
    }
    input.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: greeting }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  async function sendHere(greeting) {
    if (!greeting) {
      setStatus("招呼语为空，拒绝发送");
      return false;
    }
    const input = await waitFor(CHAT_INPUT, 15000);
    if (!input) {
      setStatus("未找到聊天输入框");
      return false;
    }
    setStatus("填写招呼语…");
    fillInput(input, greeting);
    await sleep(700);
    let sentByButton = false;
    const modal = qa(".dialog-wrap, .dialog-container").find(visible);
    if (modal) {
      for (let i = 0; i < 10; i += 1) {
        const button = Array.from(modal.querySelectorAll("button")).find(
          (el) => visible(el) && text(el).includes("发送") && !el.disabled
        );
        if (button) {
          button.click();
          sentByButton = true;
          break;
        }
        await sleep(300);
      }
    }
    if (!sentByButton) {
      for (const type of ["keydown", "keypress", "keyup"]) {
        input.dispatchEvent(
          new KeyboardEvent(type, {
            key: "Enter",
            code: "Enter",
            keyCode: 13,
            which: 13,
            bubbles: true,
            cancelable: true,
          })
        );
      }
    }
    const ok = await waitForMessage(greeting, 20000);
    setStatus(ok ? "已发送并确认气泡" : "发送后未确认到气泡");
    log(ok ? "消息气泡已出现" : "未确认到消息气泡");
    return ok;
  }

  async function waitFor(selector, timeout) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const el = qa(selector).find(visible);
      if (el) return el;
      await sleep(500);
    }
    return null;
  }

  async function waitForMessage(greeting, timeout) {
    const snippet = greeting.slice(0, 12);
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (qa(MESSAGE).some((el) => text(el).includes(snippet))) return true;
      await sleep(500);
    }
    return false;
  }

  async function finish(status, error) {
    const result = {
      job: state.job,
      greeting: state.greeting,
      status,
      error,
      mode: state.auto,
      time: new Date().toISOString(),
    };
    const stored = await chrome.storage.local.get({ jobbot_results: [] });
    stored.jobbot_results.push(result);
    await chrome.storage.local.set({ jobbot_results: stored.jobbot_results });
    if (status === "sent" && state.job) {
      await addSentHistory(state.job);
      await notifyLedger(state.job, "extension:chat", { greeting: state.greeting || "" });
    }
    await chrome.storage.local.remove("pendingChat");
    await chrome.storage.local.remove("pendingGreeting");
    await chrome.runtime.sendMessage({
      type: "download",
      filename: "jobbot-extension-result.json",
      content: JSON.stringify(result, null, 2),
    });
    setStatus(status === "sent" ? "已发送并已导出结果" : `结束：${status}`);
    log(JSON.stringify(result));
  }

  async function exportState() {
    const result = {
      job: state.job,
      greeting: state.greeting,
      status: "export",
      mode: state.auto,
      time: new Date().toISOString(),
    };
    await chrome.runtime.sendMessage({
      type: "download",
      filename: "jobbot-extension-result.json",
      content: JSON.stringify(result, null, 2),
    });
    setStatus("结果已导出");
  }

  async function downloadHtml() {
    await chrome.runtime.sendMessage({
      type: "download",
      filename: "boss-page.html",
      content: document.documentElement.outerHTML,
      mime: "text/html",
    });
    setStatus("HTML 已导出");
  }

  async function chatSend(job, providedGreeting) {
    ensurePanel();
    state.job = job;
    state.greeting = providedGreeting || "";
    setJob(job);
    setGreeting(state.greeting);
    setStatus("查找会话…");
    const deadline = Date.now() + 20000;
    let conversation = null;
    while (Date.now() < deadline) {
      conversation = qa(".friend-content-warp").find((el) => el.innerText.includes(job.company));
      if (conversation) break;
      await sleep(500);
    }
    if (!conversation) {
      setStatus(`未找到会话：${job.company}`);
      return;
    }
    const target = conversation.querySelector(".friend-content") || conversation;
    target.click();
    setStatus("打开会话…");
    await sleep(2000);

    const greeting = providedGreeting || (await makeGreeting(job));
    if (!greeting) return;
    state.greeting = greeting;
    setGreeting(greeting);
    await chrome.storage.local.set({
      pendingGreeting: { job, greeting, created: Date.now() },
    });

    const ok = await sendHere(greeting);
    await finish(ok ? "sent" : "unconfirmed", ok ? "" : "未确认到消息气泡");
  }

  function jobFromHash() {
    const params = new URLSearchParams(location.hash.replace(/^#/, ""));
    return {
      id: "",
      url: "",
      title: params.get("jobbot_title") || "Java后端开发工程师",
      company: params.get("jobbot_company") || "",
      salary: params.get("jobbot_salary") || "",
      area: params.get("jobbot_area") || "大连",
      tags: (params.get("jobbot_tags") || "Java,Spring").split(",").filter(Boolean),
      description: params.get("jobbot_description") || "",
      alreadyChat: true,
    };
  }

  async function chatSelectDump() {
    const params = new URLSearchParams(location.hash.replace(/^#/, ""));
    const company = params.get("jobbot_company") || "";
    setStatus("查找会话…");
    const deadline = Date.now() + 20000;
    let conversation = null;
    while (Date.now() < deadline) {
      conversation = qa(".friend-content-warp").find((el) => el.innerText.includes(company));
      if (conversation) break;
      await sleep(500);
    }
    if (!conversation) {
      setStatus(`未找到会话：${company}`);
      return;
    }
    const target = conversation.querySelector(".friend-content") || conversation;
    target.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
    setStatus("已选中会话，导出 HTML…");
    await sleep(3000);
    await downloadHtml();
  }

  async function chatResend() {
    const { pendingGreeting } = await chrome.storage.local.get("pendingGreeting");
    if (!pendingGreeting || !pendingGreeting.job || !pendingGreeting.greeting) {
      setStatus("没有待重发的招呼语");
      return;
    }
    await chatSend(pendingGreeting.job, pendingGreeting.greeting);
  }

  async function chatInputProbe() {
    const params = new URLSearchParams(location.hash.replace(/^#/, ""));
    const company = params.get("jobbot_company") || "";
    const deadline = Date.now() + 20000;
    let conversation = null;
    while (Date.now() < deadline) {
      conversation = qa(".friend-content-warp").find((el) => el.innerText.includes(company));
      if (conversation) break;
      await sleep(500);
    }
    if (!conversation) {
      setStatus(`未找到会话：${company}`);
      return;
    }
    const target = conversation.querySelector(".friend-content") || conversation;
    target.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
    await sleep(2500);
    const selectors = [
      "textarea",
      '[contenteditable="true"]',
      ".input",
      ".input-box",
      ".input-wrap",
      ".input-wrap-textarea",
    ];
    const rows = [];
    for (const selector of selectors) {
      for (const el of qa(selector)) {
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        rows.push({
          selector,
          tag: el.tagName,
          cls: el.className,
          rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
          display: style.display,
          visibility: style.visibility,
          opacity: style.opacity,
          text: (el.value || el.innerText || "").slice(0, 80),
          outer: el.outerHTML.slice(0, 300),
        });
      }
    }
    await chrome.runtime.sendMessage({
      type: "download",
      filename: "jobbot-input-probe.json",
      content: JSON.stringify(rows, null, 2),
    });
    setStatus(`输入探针：${rows.length} 个元素，已导出`);
  }

  const AUTO_KEY = "jobbotAutoState";
  const AUTO_SEND_KEY = "jobbotAutoSendState";
  const PREVIEW_KEY = "jobbotPreviewState";
  const HISTORY_KEY = "jobbotSentHistory";
  let autoRunning = false;

  function normText(value) {
    return String(value || "").replace(/\s+/g, "").toLowerCase();
  }

  function sameCompanyTitle(a, b) {
    return (
      normText(a && a.company) === normText(b && b.company) &&
      normText(a && a.title) === normText(b && b.title)
    );
  }



  async function notifyLedger(job, evidence, extra) {
    // 补账（乙）：消息已经在 BOSS 发出去了，才把这条写进桥的台账 `applications.sent`，
    // 让「去重以台账为准」当场看得见，不必等人工跑一次 extension-import。
    // 任何失败只记一行日志：绝不能因为补账失败去重发、也不改发送结果。
    try {
      const res = await chrome.runtime.sendMessage({
        type: "mark_sent",
        body: { job, evidence: evidence || "extension", ...(extra || {}) },
      });
      if (!res || res.ok !== true) {
        log(`台账补账未成：${(res && res.error) || "桥没有回应"}`);
      } else {
        log(`台账补账 ok：${res.company || ""}/${res.title || ""}｜台账现有 ${res.ledger_rows} 行`);
      }
      return res;
    } catch (error) {
      log(`台账补账异常：${String(error).slice(0, 70)}`);
      return { ok: false, error: String(error) };
    }
  }

  async function addSentHistory(job) {
    if (!job || !job.id) return;
    const stored = await chrome.storage.local.get({ [HISTORY_KEY]: [] });
    const history = stored[HISTORY_KEY];
    if (!history.some((item) => item.job && sameCompanyTitle(item.job, job))) {
      history.push({ job, time: new Date().toISOString(), status: "sent" });
    }
    await chrome.storage.local.set({ [HISTORY_KEY]: history.slice(-500) });
  }

  function parseList(value) {
    return (value || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function parseCities(value) {
    return parseList(value).map((item) => {
      const parts = item.split(":");
      if (parts.length === 1) return { code: parts[0], name: parts[0] };
      return { code: parts[0], name: parts.slice(1).join(":") };
    });
  }

  function buildQueue(cities, keywords) {
    const queue = [];
    for (const keyword of keywords) {
      for (const city of cities) queue.push({ keyword, city });
    }
    return queue;
  }

  function searchUrlFor(item, runId) {
    return (
      `https://www.zhipin.com/web/geek/job?query=${encodeURIComponent(item.keyword)}` +
      `&city=${encodeURIComponent(item.city.code)}` +
      `#jobbot_auto=auto_plan&jobbot_run=${encodeURIComponent(runId)}`
    );
  }


  // BOSS 用私有字体画薪资数字：DOM 里是 U+E000-F8FF 私用区码位，页面自己才认得那个字体。
  // 解码在 MAIN world 的 cipher.js 里做（隔离环境读不到页面 @font-face），这里只收结果。
  // BEGIN cipherStateBlock —— data/verify-20261104/cipher-wiring-teeth.mjs 把这段原文切回来 eval
  const CIPHER_RE = /[\uE000-\uF8FF]/;
  let cipherMap = null;
  let cipherPlain = false;
  // 自动计划当前跑到的搜索城市：评分要按候选所属城市判，不能只认配置里那一个
  let planCity = "";

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || data.type !== "jobbot-cipher-map") return;
    applyCipherMessage(data);
  });

  let cipherApplied = "";

  function applyCipherMessage(data) {
    if (!data || data.type !== "jobbot-cipher-map") return;
    const signature = JSON.stringify([
      data.map ? data.map.length : 0,
      data.reason || "",
      data.family || "",
      Boolean(data.plain),
    ]);
    if (signature === cipherApplied) return;   // data-* 会被反复读到，同一结果只落一次盘
    cipherApplied = signature;
    const status = {
      at: new Date().toISOString(),
      // plain＝这一页薪资本来就是明文，没有加密集合要解：算「已了结」，不算失败。
      // 之前它报 ok:false，监视面读到一条「12 次重试仍解不出」就会去查一条不存在的解码故障。
      ok: Boolean(data.map && data.map.length) || Boolean(data.plain),
      plain: Boolean(data.plain),
      extVersion: EXT_VERSION,
      reason: data.reason || "",
      family: data.family || "",
      map: data.map || null,
    };
    // 面板日志只有人在屏幕前才看得见；状态落一份到 storage，事后能按 run 对账。
    chrome.storage.local.set({ jobbotCipherStatus: status });
    if (data.plain) {
      cipherPlain = true;
      log(`薪资已是明文，无需解码：${status.reason}`);
      return;
    }
    if (!status.ok) {
      log(`薪资字体未解出：${status.reason || "无可行解"}，薪资按未取到处理`);
      return;
    }
    cipherMap = new Map(status.map.map(([code, digit]) => [String.fromCodePoint(code), String(digit)]));
    log(`薪资字体已解码：${status.map.map(([code, digit]) => `${code.toString(16)}→${digit}`).join(" ")}`);
  }

  function pullCipherFromDataset() {
    // MAIN world 把结果同时写在 <html data-jobbot-cipher>，避免「先 post 后挂监听」丢掉。
    const raw = document.documentElement.dataset.jobbotCipher;
    if (!raw) return;
    try {
      applyCipherMessage(JSON.parse(raw));
    } catch (error) {
      log(`薪资字体状态读取失败：${error}`);
    }
  }

  async function waitForCipherMap(ms) {
    const deadline = Date.now() + ms;
    while (!cipherMap && !cipherPlain && Date.now() < deadline) {
      pullCipherFromDataset();
      if (cipherMap || cipherPlain) return cipherMap;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return cipherMap;
  }

  function decodeSalary(value) {
    const raw = value || "";
    if (!cipherMap || !CIPHER_RE.test(raw)) return raw;
    return [...raw].map((ch) => cipherMap.get(ch) || ch).join("");
  }
  // END cipherStateBlock

  function extractCardJob(card) {
    const link = card.querySelector('a.job-name, a[href*="/job_detail/"]');
    const href = link ? new URL(link.getAttribute("href"), location.href).href : "";
    const match = href.match(/\/job_detail\/([^/?#]+)\.html/);
    const tags = qa(".tag-list li", card).map(text).filter(Boolean);
    const salaryCipher = text(q(".job-salary", card));
    return {
      id: match ? match[1] : "",
      url: href,
      title: text(q(".job-name", card)),
      company: text(q(".boss-name", card)) || text(q(".company-name", card)),
      salary: decodeSalary(salaryCipher),
      salaryRaw: salaryCipher,
      searchCity: planCity,
      area: text(q(".company-location", card)),
      tags,
      description: "",
      alreadyChat: card.innerText.includes("已沟通") || card.innerText.includes("继续沟通"),
    };
  }

  // BEGIN panelBelongsToJob —— 纯函数，data/verify-20261104/panel-ownership-teeth.mjs 按这对标记切回原文直接跑
  // 归属只认公司名：2026-11-04 平台实跑那份面板正文（`data/verify-20261104/live-plan-state.json`）里
  // 连本岗位自己的标题都没有出现（三条都是 `面板含标题=False`，第 1 条也 False），拿标题判定＝恒不成立；
  // 而面板底部固定写着「公司名 · HR」，公司名是唯一稳定存在的归属信号。
  function panelBelongsToJob(panelText, job) {
    const panel = String(panelText || "");
    if (panel.length < 40) return false;
    const company = String((job && job.company) || "").trim();
    if (!company) return false;
    if (panel.includes(company)) return true;
    // 卡片写全称、面板可能只写简称：剥掉常见后缀再拿词干比一次
    const stem = company.replace(
      /(股份有限公司|有限责任公司|有限公司|集团|科技|技术|信息|软件|计算机|研发基地)+/g,
      ""
    );
    return stem.length >= 3 && panel.includes(stem);
  }
  // END panelBelongsToJob

  // 自动计划扫的是卡片，卡片上没有岗位正文；BOSS 列表页点中卡片后右侧详情面板才会填出来。
  // 没有正文时技能命中只能靠标题与标签，AI 精排也是对着空 JD 打分，所以选中候选后补取一次详情面板。
  const PANEL_KEY = "jobbotPanelStatus";
  // 详情页抓取是「面板这条路走不通」时的补路，每加载一次页面最多补 3 次：
  // 多打 BOSS 请求有惊动反爬的风险，所以要限量，且结果一律过归属判定才敢用。
  let fetchAttempts = 0;
  const FETCH_MAX = 3;

  // 归属闸判了什么，写进存储好让事后能从磁盘查——今天这个缺陷就是这么抓到的：
  // 盯 `jobbotCipherStatus` 能看出字体解没解开，盯这个能看出正文是真取到了还是被闸掉了。
  async function notePanelStatus(job, verdict, diag) {
    const stored = await chrome.storage.local.get(PANEL_KEY);
    const prev = stored[PANEL_KEY] || { ok: 0, foreign: 0, stale: 0, empty: 0 };
    prev[verdict] = (prev[verdict] || 0) + 1;
    prev.last = `${verdict}｜${(job.company || "").slice(0, 20)}/${(job.title || "").slice(0, 20)}`;
    if (diag) prev.diag = diag;
    prev.time = new Date().toISOString();
    prev.extVersion = EXT_VERSION;
    await chrome.storage.local.set({ [PANEL_KEY]: prev });
  }

  // BEGIN usableFetchedBody —— 纯函数，data/verify-20261104/enrich-wiring-teeth.mjs 也把它切回来源 eval
  // 抓回来的详情页正文能不能用：够长 + 归属成立。BOSS 把请求导去验证页/登录页时，
  // 这里会拒——绝不能因为「是我自己发的请求」就免检，那等于把第廿七批堵掉的路又开回来。
  function usableFetchedBody(text, job) {
    const body = stripUiChrome(String(text || "").replace(/\s+/g, " ").trim());
    if (body.length < 40) return { ok: false, reason: `详情页正文只有 ${body.length} 字` };
    if (!panelBelongsToJob(body, job)) return { ok: false, reason: "详情页正文里没有这条的公司名" };
    return { ok: true, body: body.slice(0, 2000) };
  }
  // END usableFetchedBody

  // BEGIN stripUiChrome —— 纯函数，data/verify-20261104/enrich-wiring-teeth.mjs 按名字切回原文直接跑
  // 面板 innerText 顶上先挂着页面挂件：2026-11-05 平台实跑（run=boss1793852511）三条岗位的正文
  // 全部以「举报 微信扫码分享 职位描述 …」开头（4/4 系统性）。桥那边 `MIN_JD_CHARS=40` 是拿字符数当
  // 「JD 真到手」的前提，挂件那 13 个字会被算进这条前提——正文只有二几个字的小岗位就这么被当成到手了。
  // 只剥最开头的这两枚挂件词（连着剥，直到剥不动）：「驻外详情 · …」不剥，那是真信息，排除词闸靠它拦驻外岗。
  function stripUiChrome(text) {
    const CHROME = ["举报", "微信扫码分享"];
    let out = String(text || "").trim();
    let changed = true;
    while (changed) {
      changed = false;
      for (const token of CHROME) {
        if (out.startsWith(token)) {
          out = out.slice(token.length).replace(/^[\s·|,，]+/, "").trim();
          changed = true;
        }
      }
    }
    return out;
  }
  // END stripUiChrome

  async function enrichCandidate(job) {
    if (job.description && job.description.length >= 40) return job;
    const card = qa(CARD)
      .filter(visible)
      .find((el) => {
        const link = el.querySelector('a.job-name, a[href*="/job_detail/"]');
        return Boolean(link && job.id && link.href.indexOf(job.id) >= 0);
      });
    if (!card) return job;
    const readPanel = () => {
      const panel = q(".job-detail-body");
      return panel ? stripUiChrome(panel.innerText.replace(/\s+/g, " ").trim()) : "";
    };
    // 面板在点新卡片之前留的是上一岗位的正文；直接拿来用会把上一岗位的描述算到本岗位头上。
    // 「等它换掉」不够：2026-11-04 平台实跑（`data/verify-20261104/live-plan-state.json`）三条岗位的
    // description 逐字节相同（sha 83187275c21d，383 字），正文里写的还是第 1 条（中谷海运）的公司与工作地址
    // ⇒ SPA 每次换搜索都把面板重新选回第一张卡片，「换掉了」是真的，「换成本岗位的」是假的。
    // 所以再加一道归属判定：面板正文里必须能找到这条岗位的岗位名或公司名，找不到就不用（宁少不串岗）。
    const stale = readPanel();
    // 只点容器层：真页面存档（data/verify-20261102/dump-java.html）里 `a.job-name` 不带 target，
    // 合成 click 落在它身上会把当前标签页直接导航到详情页去。
    let dispatched = null;
    if (!card.classList.contains("active")) {
      dispatched = card.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
    }
    const deadline = Date.now() + 6000;
    let detail = "";
    let foreign = "";
    let polls = 0;
    let innerRetried = false;
    let maxSeen = stale.length;
    while (Date.now() < deadline) {
      const current = readPanel();
      polls += 1;
      if (current.length > maxSeen) maxSeen = current.length;
      if (current.length >= 40 && current !== stale) {
        if (panelBelongsToJob(current, job)) {
          detail = current;
          break;
        }
        foreign = current;
      }
      // 2.5 秒后仍旧「一点没换」⇒ 对卡片内层再补一次整序列（依旧只点 div，不点 a）
      if (!innerRetried && Date.now() > deadline - 3500) {
        const inner = card.querySelector(".job-card-box, .job-info, .job-title");
        if (inner) {
          innerRetried = true;
          for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
            inner.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
          }
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    if (!detail) {
      const verdict = foreign ? "foreign" : stale ? "stale" : "empty";
      // 光说「没换过来」下次还是猜。把当时页面上真有什么记下来：面板元素在不在、点之前多少字、
      // 点之后最长多少字、轮询了几次、卡片有没有变 active、桥/闸判成别人的次数——
      // 这几格能分清「面板根本没有」「点了没反应」「点了但内容没变」「换成别人家的」四种病。
      // `click未被拦`＝dispatchEvent 返回值：false＝Vue 接住了事件却没换卡（多半还要真手势）；
      // true＝一路没人拦（大概率我们点的那层压根没绑处理程序，得换一层点）。
      const diag = {
        面板元素: q(".job-detail-body") ? "有" : "无",
        点前字数: stale.length,
        点后最长: maxSeen,
        轮询次数: polls,
        卡片变active: card.classList.contains("active") ? "是" : "否",
        判成别人: foreign ? "是" : "否",
        补点内层: innerRetried ? "是" : "否",
        click未被拦: dispatched === null ? "没点(已active)" : dispatched ? "是" : "否",
      };
      // 面板这条路没走到本岗位的正文 ⇒ 补一次只读抓取（GET 这条岗位自己的详情页）。
      // 抓回来的文本先剥掉 style/script（真页面存档里面板文本就混着内联 CSS），再走**同一道**归属判定：
      // BOSS 把请求导到验证页/别的岗位时这里必须拒掉，不能因为「是我自己发的请求」就免检。
      let fetchNote = "";
      if (job.url && fetchAttempts < FETCH_MAX) {
        fetchAttempts += 1;
        try {
          const resp = await fetch(job.url, { credentials: "include" });
          const doc = new DOMParser().parseFromString(await resp.text(), "text/html");
          doc.querySelectorAll("style, script").forEach((n) => n.remove());
          const el = doc.querySelector(".job-detail-body") || doc.querySelector(".job-detail");
          const got = usableFetchedBody(el ? el.textContent : "", job);
          if (got.ok) {
            diag.抓取 = `成功 ${got.body.length} 字（第 ${fetchAttempts}/${FETCH_MAX} 次）`;
            await notePanelStatus(job, "fetched", diag);
            log(`详情页抓取到手：${got.body.length} 字（第 ${fetchAttempts}/${FETCH_MAX} 次，已过归属判定）`);
            return { ...job, description: got.body };
          }
          fetchNote = `抓了不能用：${got.reason}｜HTTP ${resp.status}`;
        } catch (error) {
          fetchNote = `抓取报错：${String(error).slice(0, 60)}`;
        }
      } else {
        fetchNote = job.url ? `本场已抓 ${fetchAttempts}/${FETCH_MAX} 次，不再抓` : "卡片没取到链接";
      }
      diag.抓取 = fetchNote;
      log(
        foreign
          ? `详情面板是别的岗位的正文（面板里找不到这条的岗位名/公司名），按卡片信息评分（宁少不串岗）`
          : stale
            ? `详情面板还没换成本岗位的正文，按卡片信息评分（宁少不串岗）｜${Object.entries(diag).map(([k, v]) => `${k}=${v}`).join(" ")}`
            : "详情面板没取到正文，按卡片信息评分（技能与精排会偏保守）"
      );
      await notePanelStatus(job, verdict, diag);
      return job;
    }
    await notePanelStatus(job, "ok");
    // 标签也只在确认归属之后并：`.job-label-list` 是面板里的，不归属时并进来就是别人的技能
    const extraTags = qa(".job-label-list li").map(text).filter(Boolean);
    return {
      ...job,
      description: detail.slice(0, 2000),
      tags: Array.from(new Set([...(job.tags || []), ...extraTags])),
    };
  }

  async function saveAuto(state) {
    state.extVersion = EXT_VERSION;
    // 心跳：`active` 只在收尾时才改成 false，所以「还在跑」与「半路死了」在存储里完全同形
    // （11-05 那场 `active=true phase=search results=0` 就是这么被干等的）。每次写状态都盖一枚 `updated_at`。
    state.updated_at = new Date().toISOString();
    await chrome.storage.local.set({ [AUTO_KEY]: state });
  }

  async function finishAutoPlan(state, note) {
    state.active = false;
    state.phase = "done";
    state.finished_at = new Date().toISOString();
    if (note) state.note = note;
    await saveAuto(state);
    await chrome.runtime.sendMessage({
      type: "download",
      filename: "jobbot-auto-plan.json",
      content: JSON.stringify(
        {
          run: state.run,
          mode: state.mode,
          limit: state.limit,
          queue: state.queue,
          results: state.results,
          skipped: state.skipped || [],
          duplicates: state.duplicates || [],
          filtered: state.filtered || [],
          handled: state.handled,
          trace: state.trace || [],
          finished_at: state.finished_at,
          note: state.note || "",
        },
        null,
        2
      ),
    });
    setStatus(`自动计划完成：${state.results.length}/${state.limit} 条，已导出`);
  }

  async function advanceAutoPlan(state) {
    state.queueIndex += 1;
    if (state.results.length >= state.limit || state.queueIndex >= state.queue.length) {
      await finishAutoPlan(state);
      return;
    }
    const item = state.queue[state.queueIndex];
    state.phase = "search";
    await saveAuto(state);
    setStatus(`下一搜索：${item.city.name} / ${item.keyword}`);
    await sleep(800);
    location.href = searchUrlFor(item, state.run);
  }

  function traceStep(state, step, extra) {
    state.trace = state.trace || [];
    const entry = { step: String(step), at: new Date().toISOString() };
    if (extra) entry.info = String(extra).slice(0, 120);
    state.trace.push(entry);
    if (state.trace.length > 24) state.trace.shift();
    return state.trace;
  }

  async function waitForCardsTraced(state, timeout) {
    // BOSS 上卡片出现得比本机慢，且页面可能被 SPA 刷新；每 15 秒往存储写一条心跳，
    // 这样即使这一页后来死了，也能从盘上看清它当时停在哪、页面上有没有卡片。
    const deadline = Date.now() + timeout;
    let lastTick = 0;
    while (Date.now() < deadline) {
      const el = qa(CARD).find(visible);
      if (el) return el;
      const elapsed = Math.round((timeout - (deadline - Date.now())) / 1000);
      const tick = Math.floor(elapsed / 15);
      if (tick > lastTick) {
        lastTick = tick;
        traceStep(state, `wait-${tick * 15}s`, `卡=${qa(CARD).length} 可见=${qa(CARD).filter(visible).length}`);
        await saveAuto(state);
      }
      await sleep(500);
    }
    return null;
  }

  async function autoPlan(params) {
    if (autoRunning) return;
    autoRunning = true;
    try {
      const runId = params.get("jobbot_run") || String(Date.now());
      const stored = await chrome.storage.local.get(AUTO_KEY);
      let state = stored[AUTO_KEY];
      if (!state || state.run !== runId) {
        const cities = parseCities(params.get("jobbot_cities"));
        const keywords = parseList(params.get("jobbot_keywords"));
        if (!cities.length || !keywords.length) {
          setStatus("自动计划缺少城市或关键词");
          return;
        }
        const limit = Number(params.get("jobbot_limit") || 3);
        state = {
          run: runId,
          mode: "auto_plan",
          active: true,
          phase: "search",
          limit: Math.max(1, limit),
          cities,
          keywords,
          queue: buildQueue(cities, keywords),
          queueIndex: 0,
          results: [],
          skipped: [],
          handled: [],
          exclude: parseList(
            params.get("jobbot_exclude") || "销售,保险,教育,在校,应届,实习"
          ),
          excludeTitle: parseList(
            params.get("jobbot_exclude_title") || "大模型,算法,AI"
          ),
          dedupeDays: Number(params.get("jobbot_dedupe_days") || 7),
          duplicates: [],
          filtered: [],
          titleKeywords: parseList(
            params.get("jobbot_title_keywords") || "Java,后端,服务端"
          ),
        };
        await saveAuto(state);
      }
      traceStep(
        state,
        "entry",
        `run=${state.run} 同id=${state.run === runId} active=${state.active} pending=${
          state.pending && state.pending.job ? state.pending.job.id : "无"
        } handled=${(state.handled || []).length} results=${(state.results || []).length} 页=${
          location.pathname
        } 标题=${(document.title || "").slice(0, 20)} 卡=${qa(CARD).length}`
      );
      await saveAuto(state);
      if (!state.active) {
        setStatus(
          `这一份计划（run=${state.run}）已经收工，这一趟没有重跑：存储里还是 ` +
            `${(state.results || []).length} 条旧结果，直接 auto_send 会照旧那几条发。` +
            `要按新口径重跑就换一个 run id（jobbot plan-url 每次现取一个新的）`
        );
        return;
      }
      state.dedupeDays = state.dedupeDays || 7;
      state.duplicates = state.duplicates || [];
      ensurePanel();
      setStatus(`自动搜索 ${state.queueIndex + 1}/${state.queue.length}`);
      const card = await waitForCardsTraced(state, 60000);
      if (card && !cipherMap) await waitForCipherMap(8000);
      const queueItem = state.queue && state.queue[state.queueIndex];
      planCity = (queueItem && queueItem.city && queueItem.city.name) || "";
      traceStep(state, card ? "cards-ok" : "cards-none");
      await saveAuto(state);
      if (!card) {
        await advanceAutoPlan(state);
        return;
      }
      // 先扫整页候选：被分数闸拦下的只记 skipped，不占 limit；只有 passed=true 才计数。
      while (state.results.length < state.limit) {
        const duplicates = [];
        const rejects = [];
        // 筛掉的卡片原先不留任何痕迹（在 pool 外就被丢掉，skipped 里查不到）；现在逐条写明为什么被丢。
        const whyRejected = (job) => {
          if (!job.id) return "卡片没取到职位编号";
          if (state.handled.includes(job.id)) return "本轮已处理过";
          if (job.alreadyChat) return "平台显示已沟通";
          // 去重以台账为准（2026-11-04 裁定）：扩展自己那份 `jobbotSentHistory` 不再当门禁——
          // 它只记这台浏览器发过的那几条，台账 `applications` 才是全集。
          // 拦不拦由桥在 /score 里查台账决定（响应 `duplicate`/`duplicate_reason`）。
          const inCompany = state.exclude.filter((word) => (job.company || "").includes(word));
          const inTitle = state.exclude.filter((word) =>
            `${job.title} ${(job.tags || []).join(" ")}`.includes(word)
          );
          if (inCompany.length || inTitle.length) {
            const where =
              inCompany.length && inTitle.length
                ? "标题与公司名都有"
                : inCompany.length
                ? "只在公司名"
                : "只在标题/标签";
            return `命中排除词「${Array.from(new Set([...inTitle, ...inCompany])).join("、")}」（${where}）`;
          }
          const hitTitle = state.excludeTitle.filter((word) => (job.title || "").includes(word));
          if (hitTitle.length) return `命中职位标题排除词「${hitTitle.join("、")}」`;
          if ((job.tags || []).some((tag) => tag.includes("应届") || tag.includes("在校"))) return "标签含应届/在校";
          if (
            !state.titleKeywords.some((word) => (job.title || "").toLowerCase().includes(word.toLowerCase()))
          )
            return "标题不含目标职位关键词";
          return "";
        };
        let candidate = null;
        if (state.pending && state.pending.job && state.pending.job.id) {
          candidate = state.pending.job;
        } else {
          for (const job of qa(CARD).filter(visible).map(extractCardJob)) {
            const reason = whyRejected(job);
            if (!reason) {
              candidate = job;
              break;
            }
            if (reason.startsWith("同公司同职位")) {
              duplicates.push({
                job_id: job.id,
                title: job.title,
                company: job.company,
                salary: job.salary || "",
                tags: (job.tags || []).slice(0, 8),
                reason,
              });
            } else if (!state.handled.includes(job.id)) {
              // 带上 tags/salary：不带这两样，事后没法判断这条是被**正确**筛掉还是被关键词误伤
              rejects.push({
                job_id: job.id,
                title: job.title,
                company: job.company,
                salary: job.salary || "",
                tags: (job.tags || []).slice(0, 8),
                reason,
                time: new Date().toISOString(),
              });
            }
          }
        }
        if (rejects.length) {
          state.filtered = state.filtered || [];
          // 同一张卡片每换一次搜索/关键词都会被重新扫到，原先不去重：今早 13 条里其实只有 2 个岗位（×7、×6）
          const known = new Set(state.filtered.map((f) => f.job_id));
          const fresh = rejects.filter((r) => !known.has(r.job_id));
          if (fresh.length) state.filtered.push(...fresh);
          traceStep(
            state,
            "filtered",
            `${fresh.length} 条被筛掉（本页第 ${state.queueIndex + 1} 趟，去重后累计 ${state.filtered.length} 条）：` +
              `${fresh.slice(0, 3).map((r) => `${(r.company || "").slice(0, 10)}→${r.reason}`).join("｜")}`
          );
          await saveAuto(state);
        }
        if (duplicates.length) {
          const knownDup = new Set(state.duplicates.map((d) => d.job_id));
          state.duplicates.push(...duplicates.filter((d) => !knownDup.has(d.job_id)));
          await saveAuto(state);
        }
        if (!candidate) {
          traceStep(state, "candidate-none");
          await saveAuto(state);
          break;
        }
        candidate = await enrichCandidate(candidate);
        state.job = candidate;
        setJob(candidate);
        log(`候选：${candidate.title} / ${candidate.company}${state.pending ? "（续跑）" : ""}`);
        // 等本地模型要 3-5 分钟，期间 BOSS 可能把这一页弄掉；先落盘再等，掉了也能从 pending 续上
        state.pending = { job: candidate, started_at: new Date().toISOString() };
        if (!state.handled.includes(candidate.id)) state.handled.push(candidate.id);
        traceStep(state, "to-score", candidate.id);
        await saveAuto(state);
        let scored = await scoreCandidate(candidate);
        // 去重以台账为准：`chrome.storage` 那份只记扩展自己发过的，桥查的 `applications` 才是全集
        if (scored && scored.duplicate === true) {
          state.duplicates = state.duplicates || [];
          state.duplicates.push({
            job_id: candidate.id,
            title: candidate.title,
            company: candidate.company,
            reason: scored.duplicateReason || "台账判定窗口内已投递",
            source: "ledger",
            time: new Date().toISOString(),
          });
          traceStep(state, "duplicate-ledger", `${candidate.company}→${scored.duplicateReason || ""}`.slice(0, 80));
          log(`台账去重：${candidate.company} / ${candidate.title} ⇒ 跳过不投`);
          state.pending = null;
          await saveAuto(state);
          continue;
        }
        if (!scored || scored.passed !== true) {
          state.skipped = state.skipped || [];
          state.skipped.push({
            job: candidate,
            score: scored ? scored.score : null,
            min_score: scored ? scored.minScore : null,
            passed: scored ? scored.passed : null,
            risks: scored ? scored.risks : [],
            rule_score: scored ? scored.score : null,
            ai_scored: false,
            reason:
              scored && scored.error
                ? `评分失败：${scored.error}`
                : scored && scored.skipped === true
                  ? // 桥说了哪一种就照抄那一句（硬拦／排除词／对表都算），别说「桥没说是哪一种」
                    (scored.reasons || []).map(String)[0] ||
                    `规则分 ${scored.score ?? "-"} 被规则闸拦下（桥没给原因）`
                  : `规则分 ${scored ? scored.score ?? "-" : "-"} 未过阈值 ${
                      scored ? scored.minScore ?? "-" : "-"
                    }`,
            time: new Date().toISOString(),
          });
          traceStep(state, "score-blocked", candidate.id);
          state.pending = null;
          await saveAuto(state);
          continue;
        }
        traceStep(state, "score-passed", `${scored.score}/${scored.minScore}`);
        await saveAuto(state);
        traceStep(state, "to-ai-score", candidate.id);
        const aiScored = await aiScoreCandidate(candidate, scored);
        if (aiScored) scored = aiScored;
        traceStep(
          state,
          scored.aiApplied ? "ai-score-done" : "ai-score-fallback",
          `rule=${scored.ruleScore ?? "-"} fit=${scored.aiFit ?? "-"} merged=${scored.score ?? "-"} ${
            scored.aiApplied ? `passed=${scored.passed}` : `error=${scored.aiError || "-"}`
          }`
        );
        await saveAuto(state);
        if (!scored || scored.passed !== true) {
          state.skipped = state.skipped || [];
          state.skipped.push({
            job: candidate,
            score: scored ? scored.score : null,
            min_score: scored ? scored.minScore : null,
            passed: scored ? scored.passed : null,
            risks: scored ? scored.risks : [],
            rule_score: scored ? scored.ruleScore ?? null : null,
            ai_fit: scored ? scored.aiFit ?? null : null,
            ai_scored: Boolean(scored && scored.aiApplied),
            reason: `AI 精排 ${scored ? scored.score ?? "-" : "-"} 未过阈值 ${
              scored ? scored.minScore ?? "-" : "-"
            }`,
            time: new Date().toISOString(),
          });
          traceStep(state, "ai-score-blocked", candidate.id);
          state.pending = null;
          await saveAuto(state);
          continue;
        }
        const aiMatch = scored;
        const greeting = await makeGreeting(candidate);
        if (!greeting) {
          state.skipped = state.skipped || [];
          state.skipped.push({
            job: candidate,
            score: aiMatch.score,
            min_score: aiMatch.minScore,
            passed: aiMatch.passed,
            risks: aiMatch.risks,
            rule_score: aiMatch.ruleScore ?? null,
            ai_fit: aiMatch.aiFit ?? null,
            ai_scored: Boolean(aiMatch.aiApplied),
            reason: "AI 生成失败",
            time: new Date().toISOString(),
          });
          traceStep(state, "greeting-null");
          state.pending = null;
          await saveAuto(state);
          continue;
        }
        const greetingMatch = lastMatch || state.match;
        traceStep(
          state,
          "greeting-ok",
          `${greeting.length} 字 ai=${Boolean(aiMatch.aiApplied)} fit=${aiMatch.aiFit ?? "-"} score=${
            aiMatch.score
          }/${aiMatch.minScore} 风险=${(aiMatch.risks || []).length}`
        );
        state.results.push({
          job: candidate,
          greeting,
          source: (greetingMatch && greetingMatch.source) || "template",
          score_source: aiMatch.aiApplied ? "ai" : "rule",
          score: aiMatch.score,
          passed: aiMatch.passed,
          risks: aiMatch.risks,
          rule_score: aiMatch.ruleScore ?? null,
          ai_fit: aiMatch.aiFit ?? null,
          ai_scored: Boolean(aiMatch.aiApplied),
          ai_error: aiMatch.aiError || "",
          time: new Date().toISOString(),
        });
        state.pending = null;
        await saveAuto(state);
      }
      await advanceAutoPlan(state);
    } finally {
      autoRunning = false;
    }
  }

  async function saveAutoSend(state) {
    state.extVersion = EXT_VERSION;
    state.updated_at = new Date().toISOString();  // 同 `saveAuto`：发送腿一条一条慢慢跑，读侧要能看出它还在动
    await chrome.storage.local.set({ [AUTO_SEND_KEY]: state });
  }

  async function finishAutoSend(state, note) {
    state.active = false;
    state.finished_at = new Date().toISOString();
    if (note) state.note = note;
    await saveAutoSend(state);
    await chrome.storage.local.remove("pendingChat");
    const sent = state.records.filter((r) => r.status === "sent").length;
    await chrome.runtime.sendMessage({
      type: "download",
      filename: "jobbot-auto-send-result.json",
      content: JSON.stringify(
        {
          run: state.run,
          total: state.jobs.length,
          sent,
          records: state.records,
          finished_at: state.finished_at,
          note: state.note || "",
        },
        null,
        2
      ),
    });
    setStatus(`自动发送完成：${sent}/${state.jobs.length}，已导出结果`);
  }

  async function autoSendAdvance(state, ok, error, recordStatus) {
    const current = state.jobs[state.index];
    if (!current) {
      await finishAutoSend(state);
      return;
    }
    if (state.currentJobId && state.currentJobId !== current.job.id) return;
    if (state.records.some((r) => r.job && r.job.id === current.job.id)) return;
    state.records.push({
      job: current.job,
      greeting: current.greeting,
      score: current.score,
      status: recordStatus || (ok ? "sent" : "unconfirmed"),
      error: error || "",
      time: new Date().toISOString(),
    });
    if (ok) {
      await addSentHistory(current.job);
      // 自动发送成功也要当场进台账（去重看的是台账，不是这枚存储状态）
      await notifyLedger(current.job, "extension:auto_send", {
        greeting: current.greeting || "",
        run: state.run || "",
      });
    }
    state.index += 1;
    state.currentJobId = null;
    await saveAutoSend(state);
    await chrome.storage.local.remove("pendingChat");
    if (state.index >= state.jobs.length) {
      await finishAutoSend(state);
      return;
    }
    const next = state.jobs[state.index];
    setStatus(`下一条 ${state.index + 1}/${state.jobs.length}`);
    await sleep(800);
    location.href =
      next.job.url + `#jobbot_auto=auto_send&jobbot_run=${encodeURIComponent(state.run)}`;
  }

  function pageJobId() {
    const match = location.pathname.match(/\/job_detail\/([^/?#]+)\.html/);
    return match ? match[1] : "";
  }

  // BEGIN autoSendEntryAction —— 纯函数，data/verify-20261104/entry-guard-teeth.mjs 按这对标记切回原文直接跑
  // auto_send 只能从 /job_detail/ 页手工起（init 里没有按钮替你算入口），点的是「当前页」的沟通按钮、
  // 记的却是「计划里第 index 条」的岗位 ⇒ 入口页与岗位对不上就会把 A 的招呼语发给 B。
  function autoSendEntryAction(pageId, jobId, jobUrl, run, navigatedFor) {
    if (!jobId || !jobUrl || pageId === jobId) return { action: "proceed" };
    if (navigatedFor === jobId) return { action: "skip", reason: "已跳转一次仍未落到该岗位详情页，不发送" };
    return {
      action: "navigate",
      url: `${jobUrl}#jobbot_auto=auto_send&jobbot_run=${encodeURIComponent(run || "")}`,
    };
  }
  // END autoSendEntryAction

  async function autoSendCurrent(state) {
    const current = state.jobs[state.index];
    if (!current) {
      await finishAutoSend(state);
      return;
    }
    const entry = autoSendEntryAction(
      pageJobId(),
      current.job.id,
      current.job.url,
      state.run,
      state.entryNavigatedFor || ""
    );
    if (entry.action === "navigate") {
      // 跳转前先落盘：换页会重载内容脚本，「已经为这条跳过一次」只能存在发送状态里
      state.entryNavigatedFor = current.job.id;
      // 留一条**不清空**的痕迹：`entryNavigatedFor` 在真正发送那一跳前会被抹成 ""（下面 `state.entryNavigatedFor = ""`），
      // 所以事后从存储里永远只能读到空串——「这一场不需要导航」与「导航过但被清了」两种情况长一模一样。
      // 这个数组专职回答「本条是不是靠额外导航才落到详情页的」，只追加、不重置。
      state.entryNavigations = (state.entryNavigations || []).concat([
        { job_id: current.job.id, at: new Date().toISOString() },
      ]);
      await saveAutoSend(state);
      setStatus(`跳回本条岗位详情页 ${state.index + 1}/${state.jobs.length}…`);
      location.href = entry.url;
      return;
    }
    if (entry.action === "skip") {
      state.entryNavigatedFor = "";
      setStatus(`已拦下（不发送）：${entry.reason}`);
      await autoSendAdvance(state, false, entry.reason, "skipped");
      return;
    }
    state.entryNavigatedFor = "";
    state.currentJobId = current.job.id;
    await saveAutoSend(state);
    ensurePanel();
    setJob(current.job);
    state.greeting = current.greeting;
    setGreeting(current.greeting);
    setStatus(`自动发送 ${state.index + 1}/${state.jobs.length}`);
    if (current.passed !== true) {
      setStatus(`已拦下（不发送）：规则分 ${current.score} 没有通过判定`);
      log(`拦下原因：${(current.risks || []).join("；") || "无风险条目"}${current.passed === undefined ? "（计划里没有 passed 字段，可能是旧版扩展）" : ""}`);
      await autoSendAdvance(
        state,
        false,
        `规则分 ${current.score} 未拿到通过判定`,
        "skipped"
      );
      return;
    }
    const button = findChatButton();
    if (!button) {
      await autoSendAdvance(state, false, "未找到沟通按钮");
      return;
    }
    await chrome.storage.local.set({
      pendingChat: {
        job: current.job,
        greeting: current.greeting,
        auto: "send",
        autoSend: true,
        created: Date.now(),
      },
    });
    setStatus(`点击${button.label}…`);
    button.el.click();
    await sleep(2500);

    const modal = qa(".dialog-wrap, .dialog-container").find(visible);
    let continueButton = null;
    if (modal) {
      continueButton = Array.from(
        modal.querySelectorAll("a,button,div,span")
      ).find((el) => visible(el) && text(el).includes("继续沟通"));
    }
    if (!continueButton) {
      continueButton = qa("a,button,div,span").find(
        (el) => visible(el) && text(el) === "继续沟通"
      );
    }
    if (continueButton) {
      setStatus("已发送默认招呼，进入聊天…");
      continueButton.click();
      await sleep(2500);
    }
    if (await sendHere(current.greeting)) {
      await autoSendAdvance(state, true, "");
      return;
    }
    setStatus("等待聊天页发送 AI 招呼…");
  }

  async function autoSendInit(params) {
    const run = params.get("jobbot_run") || "";
    const stored = await chrome.storage.local.get([AUTO_SEND_KEY, AUTO_KEY]);
    let state = stored[AUTO_SEND_KEY];
    if (!state || state.run !== run) {
      const results = (stored[AUTO_KEY] && stored[AUTO_KEY].results) || [];
      if (!results.length) {
        setStatus("没有自动计划结果，请先运行 auto_plan");
        return;
      }
      state = {
        run: run || stored[AUTO_KEY].run,
        active: true,
        index: 0,
        jobs: results,
        records: [],
        currentJobId: null,
      };
      await saveAutoSend(state);
    }
    if (!state.active) {
      setStatus("自动发送已完成");
      return;
    }
    await autoSendCurrent(state);
  }

  async function savePreview(state) {
    await chrome.storage.local.set({ [PREVIEW_KEY]: state });
  }

  async function finishPreview(state) {
    state.active = false;
    state.finished_at = new Date().toISOString();
    await savePreview(state);
    const missing = state.records.filter((r) => !r.button).length;
    await chrome.runtime.sendMessage({
      type: "download",
      filename: "jobbot-auto-send-preview.json",
      content: JSON.stringify(
        {
          run: state.run,
          total: state.jobs.length,
          missing,
          records: state.records,
          finished_at: state.finished_at,
        },
        null,
        2
      ),
    });
    setStatus(`预检完成：${state.jobs.length} 条，缺按钮 ${missing}`);
  }

  async function advancePreview(state) {
    state.index += 1;
    if (state.index >= state.jobs.length) {
      await finishPreview(state);
      return;
    }
    const next = state.jobs[state.index];
    await savePreview(state);
    setStatus(`预检下一条 ${state.index + 1}/${state.jobs.length}`);
    await sleep(500);
    location.href =
      next.job.url + `#jobbot_auto=auto_preview&jobbot_run=${encodeURIComponent(state.run)}`;
  }

  async function previewCurrent(state) {
    const current = state.jobs[state.index];
    ensurePanel();
    setJob(current.job);
    setGreeting(current.greeting);
    setStatus(`预检 ${state.index + 1}/${state.jobs.length}`);
    await sleep(1000);
    const candidates = qa(".btn-startchat, .op-btn-chat").filter(visible);
    const button = candidates.find((el) =>
      ["立即沟通", "继续沟通"].some((label) => text(el).includes(label))
    );
    state.records.push({
      job: current.job,
      greeting: current.greeting,
      button: button ? text(button) : "",
      button_class: button ? button.className : "",
      buttons_found: candidates.map((el) => text(el)),
      url: location.href,
      time: new Date().toISOString(),
    });
    await savePreview(state);
    await advancePreview(state);
  }

  async function autoPreviewInit(params) {
    const run = params.get("jobbot_run") || "";
    const stored = await chrome.storage.local.get([PREVIEW_KEY, AUTO_KEY]);
    let state = stored[PREVIEW_KEY];
    if (!state || state.run !== run) {
      const results = (stored[AUTO_KEY] && stored[AUTO_KEY].results) || [];
      if (!results.length) {
        setStatus("没有自动计划结果，请先运行 auto_plan");
        return;
      }
      state = { run: run || stored[AUTO_KEY].run, active: true, index: 0, jobs: results, records: [] };
      await savePreview(state);
    }
    if (!state.active) {
      setStatus("预检已完成");
      return;
    }
    await previewCurrent(state);
  }

  async function modalProbe() {
    ensurePanel();
    await waitFor(".btn-startchat, .op-btn-chat", 15000);
    const button = findChatButton();
    if (!button) {
      setStatus("未找到沟通按钮");
      await downloadHtml();
      return;
    }
    setStatus(`探测弹窗：点击${button.label}`);
    button.el.click();
    await sleep(2500);
    const modal = qa(".dialog-wrap, .dialog-container").find(visible);
    if (modal) {
      const continueButton = Array.from(
        modal.querySelectorAll("a,button,div,span")
      ).find((el) => visible(el) && text(el).includes("继续沟通"));
      if (continueButton) {
        setStatus("点击继续沟通");
        continueButton.click();
        await sleep(2000);
      }
    }
    setStatus("导出弹窗 HTML…");
    await downloadHtml();
  }

  async function handleChatPage() {
    const { pendingChat, chatDumpRequested } = await chrome.storage.local.get([
      "pendingChat",
      "chatDumpRequested",
    ]);
    if (chatDumpRequested) {
      await chrome.storage.local.remove("chatDumpRequested");
      await sleep(3000);
      await downloadHtml();
      return;
    }
    if (!pendingChat || !pendingChat.job || Date.now() - pendingChat.created > 600000) return;
    state.job = pendingChat.job;
    state.greeting = pendingChat.greeting;
    state.auto = pendingChat.auto;
    setJob(state.job);
    setGreeting(state.greeting);
    if (pendingChat.auto === "send") {
      setStatus("聊天页：自动发送…");
      const ok = await sendHere(state.greeting);
      if (pendingChat.autoSend) {
        const { [AUTO_SEND_KEY]: sendState } = await chrome.storage.local.get(
          AUTO_SEND_KEY
        );
        if (sendState && sendState.active) {
          await autoSendAdvance(
            sendState,
            ok,
            ok ? "" : "未确认到消息气泡"
          );
        } else {
          await finish(ok ? "sent" : "unconfirmed", ok ? "" : "未确认到消息气泡");
        }
      } else {
        await finish(ok ? "sent" : "unconfirmed", ok ? "" : "未确认到消息气泡");
      }
    } else {
      setStatus("聊天页：请确认后点击发送");
      q("#jb-send", panelEl).onclick = async () => {
        const ok = await sendHere(state.greeting);
        await finish(ok ? "sent" : "unconfirmed", ok ? "" : "未确认到消息气泡");
      };
    }
  }

  async function runAuto(mode) {
    if (!location.pathname.includes("/web/geek/job")) return;
    const resumeModes = ["resume_chat_dump", "resume_chat"];
    const guardKey = `jobbot_auto_${mode}`;
    const guard = await chrome.storage.local.get(guardKey);
    if (guard[guardKey] && Date.now() - guard[guardKey] < 600000) return;
    await chrome.storage.local.set({ [guardKey]: Date.now() });

    ensurePanel();
    setStatus(`自动模式：${mode}`);
    await waitFor(CARD, 20000);
    const card = pickCard();
    if (!card) {
      setStatus("未找到职位卡");
      return;
    }
    card.scrollIntoView({ block: "center" });
    if (!card.classList.contains("active")) {
      card.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
      await sleep(1500);
    } else {
      await sleep(300);
    }
    const job = extractJob(card);
    state.job = job;
    setJob(job);
    log(`职位：${job.title} / ${job.company} / ${job.salary}`);
    if (!job.title) {
      setStatus("职位信息不完整");
      return;
    }
    if (job.alreadyChat && !resumeModes.includes(mode)) {
      setStatus("职位已沟通");
      return;
    }
    if (mode === "chatdump") {
      const button = findChatButton();
      if (!button) {
        setStatus("未找到沟通按钮");
        return;
      }
      await chrome.storage.local.set({ chatDumpRequested: true });
      button.el.click();
      setStatus("已打开聊天页，等待导出 HTML…");
      await sleep(3000);
      if (location.pathname.includes("/chat")) await handleChatPage();
      return;
    }
    if (resumeModes.includes(mode)) {
      const button = findChatButton();
      if (!button || !button.already) {
        setStatus("未找到「继续沟通」，已停止（不会点击立即沟通）");
        return;
      }
      if (mode === "resume_chat_dump") {
        await chrome.storage.local.set({ chatDumpRequested: true });
        button.el.click();
        setStatus("已打开聊天页，等待导出 HTML…");
        await sleep(3000);
        if (location.pathname.includes("/chat")) await handleChatPage();
        return;
      }
      const greeting = await makeGreeting(job);
      if (!greeting) return;
      state.auto = "send";
      await chrome.storage.local.set({
        pendingChat: {
          job,
          greeting,
          auto: "send",
          created: Date.now(),
          status: "opened",
        },
      });
      button.el.click();
      setStatus("已打开聊天页，等待发送 AI 招呼…");
      await sleep(3000);
      if (await sendHere(greeting)) {
        await finish("sent", "");
      }
      return;
    }
    const greeting = await makeGreeting(job);
    if (!greeting) return;
    if (mode === "plan") {
      await chrome.runtime.sendMessage({
        type: "download",
        filename: "jobbot-extension-plan.json",
        content: JSON.stringify({ job, greeting, mode, time: new Date().toISOString() }, null, 2),
      });
      setStatus("计划已导出（未发送）");
      return;
    }
    if (mode === "send") {
      state.auto = "send";
      await openChat(job, greeting, "send");
    }
  }

  async function init() {
    ensurePanel();
    const params = new URLSearchParams(location.hash.replace(/^#/, ""));
    const auto = params.get("jobbot_auto");
    if (auto === "dump") {
      setStatus("导出页面 HTML…");
      await sleep(2500);
      await downloadHtml();
      return;
    }
    if (auto === "auto_plan") {
      if (location.pathname.includes("/web/geek/job")) {
        await autoPlan(new URLSearchParams(location.hash.replace(/^#/, "")));
        return;
      }
    }
    if (auto === "auto_send" && location.pathname.includes("/job_detail/")) {
      await autoSendInit(new URLSearchParams(location.hash.replace(/^#/, "")));
      return;
    }
    if (auto === "auto_preview" && location.pathname.includes("/job_detail/")) {
      await autoPreviewInit(new URLSearchParams(location.hash.replace(/^#/, "")));
      return;
    }
    if (auto === "modal_probe" && location.pathname.includes("/job_detail/")) {
      await modalProbe();
      return;
    }
    if (location.pathname.includes("/web/geek/chat")) {
      if (auto === "chat_dump") {
        setStatus("导出聊天页 HTML…");
        await sleep(3000);
        await downloadHtml();
        return;
      }
      if (auto === "chat_send") {
        await chrome.storage.local.remove("chatDumpRequested");
        await chatSend(jobFromHash());
        return;
      }
      if (auto === "chat_send_text") {
        const params = new URLSearchParams(location.hash.replace(/^#/, ""));
        const greeting = params.get("jobbot_greeting") || "";
        if (!greeting) {
          setStatus("缺少 jobbot_greeting");
          return;
        }
        await chrome.storage.local.remove("chatDumpRequested");
        await chatSend(jobFromHash(), greeting);
        return;
      }
      if (auto === "chat_select_dump") {
        await chatSelectDump();
        return;
      }
      if (auto === "chat_input_probe") {
        await chatInputProbe();
        return;
      }
      if (auto === "chat_resend") {
        await chatResend();
        return;
      }
      await handleChatPage();
      return;
    }
    if (
      auto === "plan" ||
      auto === "send" ||
      auto === "chatdump" ||
      auto === "resume_chat" ||
      auto === "resume_chat_dump"
    ) {
      if (location.pathname.includes("/web/geek/job")) {
        await runAuto(auto);
        return;
      }
    }
    setStatus("待命");
  }

  init().catch((error) => {
    console.error(error);
    try {
      setStatus(`错误：${error}`);
    } catch (_) {}
    // 只进 console 的失败等于没失败——我读不到磁盘以外的东西，你只能手抄报错
    //（18:18 那场就是这么停在 `cards-ok` 上、`active` 再没人置回 false 的）。
    try {
      chrome.storage.local.set({
        jobbotLastError: {
          at: new Date().toISOString(),
          extVersion: EXT_VERSION,
          path: location.pathname,
          error: String((error && error.stack) || error).split("\n").slice(0, 3).join(" | ").slice(0, 400),
        },
      });
    } catch (_) {}
  });
})();
