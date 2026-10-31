(function () {
  if (window.__jobbotLoaded) return;
  window.__jobbotLoaded = true;

  const P = window.JOBBOT_PROFILE;
  const CARD = ".job-card-wrap";
  const CHAT_BTN = ".btn-startchat, .op-btn-chat";
  const CHAT_INPUT =
    "div.chat-input[contenteditable='true'], .chat-input[contenteditable='true'], #chat-input[contenteditable='true']";
  const MESSAGE = ".message-item, .chat-message";

  const state = { job: null, greeting: "", auto: null };
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
    return {
      id: match ? match[1] : "",
      url: href,
      title: text(q(".job-name", card)),
      company: text(q(".boss-name", card)) || text(q(".company-name", card)),
      salary: text(q(".job-salary", card)),
      area: text(q(".company-location", card)),
      tags,
      description: detailText.slice(0, 2000),
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

  async function makeGreeting(job) {
    setStatus("AI 生成中（本地模型约 2-3 分钟）…");
    log(`AI: ${job.title} / ${job.company}`);
    const result = await chrome.runtime.sendMessage({ type: "ai_greeting", job });
    if (!result || !result.ok) {
      const error = result ? result.error : "无响应";
      setStatus(`AI 失败：${error}`);
      log(`AI 失败：${error}`);
      return null;
    }
    const greeting = result.greeting.replace(/\s+/g, " ").trim().slice(0, 160);
    state.greeting = greeting;
    setGreeting(greeting);
    setStatus(`AI 完成（${Math.round((result.elapsed || 0) / 1000)}s，${result.source || "ai"}）`);
    log(`招呼语：${greeting}`);
    return greeting;
  }

  async function openChat(job, greeting, auto) {
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
      input.value = greeting;
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
  let autoRunning = false;

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

  function extractCardJob(card) {
    const link = card.querySelector('a.job-name, a[href*="/job_detail/"]');
    const href = link ? new URL(link.getAttribute("href"), location.href).href : "";
    const match = href.match(/\/job_detail\/([^/?#]+)\.html/);
    const tags = qa(".tag-list li", card).map(text).filter(Boolean);
    return {
      id: match ? match[1] : "",
      url: href,
      title: text(q(".job-name", card)),
      company: text(q(".boss-name", card)) || text(q(".company-name", card)),
      salary: text(q(".job-salary", card)),
      area: text(q(".company-location", card)),
      tags,
      description: "",
      alreadyChat: card.innerText.includes("已沟通") || card.innerText.includes("继续沟通"),
    };
  }

  async function saveAuto(state) {
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
          handled: state.handled,
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
          handled: [],
          exclude: parseList(
            params.get("jobbot_exclude") || "销售,保险,教育,在校,应届,实习"
          ),
          excludeTitle: parseList(
            params.get("jobbot_exclude_title") || "大模型,算法,AI"
          ),
          titleKeywords: parseList(
            params.get("jobbot_title_keywords") || "Java,后端,服务端"
          ),
        };
        await saveAuto(state);
      }
      if (!state.active) {
        setStatus("自动计划已完成");
        return;
      }
      ensurePanel();
      setStatus(`自动搜索 ${state.queueIndex + 1}/${state.queue.length}`);
      const card = await waitFor(CARD, 20000);
      if (!card) {
        await advanceAutoPlan(state);
        return;
      }
      const candidate = qa(CARD)
        .filter(visible)
        .map(extractCardJob)
        .find((job) => {
          if (!job.id || state.handled.includes(job.id) || job.alreadyChat) return false;
          const haystack = `${job.title} ${job.company} ${(job.tags || []).join(" ")}`;
          if (state.exclude.some((word) => haystack.includes(word))) return false;
          if (state.excludeTitle.some((word) => job.title.includes(word))) return false;
          if (job.tags.some((tag) => tag.includes("应届") || tag.includes("在校"))) return false;
          return state.titleKeywords.some((word) =>
            job.title.toLowerCase().includes(word.toLowerCase())
          );
        });
      if (!candidate) {
        await advanceAutoPlan(state);
        return;
      }
      state.job = candidate;
      setJob(candidate);
      log(`候选：${candidate.title} / ${candidate.company}`);
      const greeting = await makeGreeting(candidate);
      if (!greeting) {
        await finishAutoPlan(state, "AI 生成失败");
        return;
      }
      state.results.push({
        job: candidate,
        greeting,
        source: "ai",
        time: new Date().toISOString(),
      });
      state.handled.push(candidate.id);
      await saveAuto(state);
      await advanceAutoPlan(state);
    } finally {
      autoRunning = false;
    }
  }

  async function saveAutoSend(state) {
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

  async function autoSendAdvance(state, ok, error) {
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
      status: ok ? "sent" : "unconfirmed",
      error: error || "",
      time: new Date().toISOString(),
    });
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

  async function autoSendCurrent(state) {
    const current = state.jobs[state.index];
    if (!current) {
      await finishAutoSend(state);
      return;
    }
    state.currentJobId = current.job.id;
    await saveAutoSend(state);
    ensurePanel();
    setJob(current.job);
    state.greeting = current.greeting;
    setGreeting(current.greeting);
    setStatus(`自动发送 ${state.index + 1}/${state.jobs.length}`);
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
  });
})();
