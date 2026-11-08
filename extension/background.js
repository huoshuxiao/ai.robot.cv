importScripts("profile.js");

const BRIDGE_URL = "http://127.0.0.1:8765/ai_greeting";
const SCORE_URL = "http://127.0.0.1:8765/score";
const AI_SCORE_URL = "http://127.0.0.1:8765/ai_score";
const MARK_SENT_URL = "http://127.0.0.1:8765/mark_sent";

async function markSent(body) {
  // 补账用的：消息已经在 BOSS 发出去了才走这里，所以任何失败都不许冒回发送流程——
  // 返回 ok=false 让 content.js 只记一行日志，不改变发送结果、也不重发。
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(MARK_SENT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify(body),
    });
    return await response.json();
  } catch (error) {
    return { ok: false, error: String(error) };
  } finally {
    clearTimeout(timer);
  }
}

async function scoreJob(job) {
  const started = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(SCORE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({ job }),
    });
    const data = await response.json();
    return { ...data, elapsed: Date.now() - started };
  } catch (error) {
    return { ok: false, error: String(error), elapsed: Date.now() - started };
  } finally {
    clearTimeout(timer);
  }
}

async function aiScore(job) {
  const started = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 2060000);
  try {
    const response = await fetch(AI_SCORE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({ job }),
    });
    const data = await response.json();
    return { ...data, elapsed: data.elapsed ?? (Date.now() - started) / 1000 };
  } catch (error) {
    return { ok: false, error: String(error), ai_applied: false, elapsed: (Date.now() - started) / 1000 };
  } finally {
    clearTimeout(timer);
  }
}

async function aiGreeting(job) {
  const started = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 2060000);
  try {
    const response = await fetch(BRIDGE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({ job }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      return {
        ok: false,
        error: data.error || `bridge HTTP ${response.status}`,
        elapsed: Date.now() - started,
      };
    }
    return {
      ok: true,
      greeting: data.greeting,
      source: data.source,
      score: data.score,
      minScore: data.min_score,
      passed: data.passed,
      risks: data.risks || [],
      elapsed: Date.now() - started,
    };
  } catch (error) {
    return { ok: false, error: String(error), elapsed: Date.now() - started };
  } finally {
    clearTimeout(timer);
  }
}

async function download(filename, content, mime) {
  const url =
    `data:${mime || "application/json"};charset=utf-8,` + encodeURIComponent(content);
  await chrome.downloads.download({ url, filename, saveAs: false });
  return { ok: true, filename };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "ai_greeting") {
    aiGreeting(message.job).then(sendResponse);
    return true;
  }
  if (message.type === "score_job") {
    scoreJob(message.job).then(sendResponse);
    return true;
  }
  if (message.type === "ai_score") {
    aiScore(message.job).then(sendResponse);
    return true;
  }
  if (message.type === "mark_sent") {
    markSent(message.body).then(sendResponse);
    return true;
  }
  if (message.type === "download") {
    download(message.filename, message.content, message.mime).then(sendResponse);
    return true;
  }
  return false;
});
