importScripts("profile.js");

const BRIDGE_URL = "http://127.0.0.1:8765/ai_greeting";

async function aiGreeting(job) {
  const started = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 600000);
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
  if (message.type === "download") {
    download(message.filename, message.content, message.mime).then(sendResponse);
    return true;
  }
  return false;
});
