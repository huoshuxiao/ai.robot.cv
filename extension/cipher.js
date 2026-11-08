// 在页面主世界运行：只有这里能拿到页面自己声明的 @font-face。
// 把私用区码位画到 canvas，按「孔洞数 + 细长比 + 字形相似度」一对一还原成数字，结果 postMessage 给内容脚本。
(() => {
  "use strict";
  // 每个数字允许的闭合孔洞数。4 的上三角在很多字形里不闭合，所以 0 和 1 都放过：
  // 实测这个版本的字体里 4 在 12×12 栅格下数到 0 个洞，硬卡 1 会让匹配无解。
  const DIGIT_HOLES = { 0: [1], 1: [0], 2: [0], 3: [0], 4: [0, 1], 5: [0], 6: [1], 7: [0], 8: [2], 9: [1] };
  const N = 12;

  function countHoles(grid) {
    const h = grid.length;
    const w = grid[0].length;
    const seen = Array.from({ length: h }, () => Array(w).fill(false));
    const stack = [];
    const push = (x, y) => {
      if (x < 0 || y < 0 || x >= w || y >= h || seen[y][x] || grid[y][x] !== ".") return;
      seen[y][x] = true;
      stack.push([x, y]);
    };
    for (let x = 0; x < w; x += 1) { push(x, 0); push(x, h - 1); }
    for (let y = 0; y < h; y += 1) { push(0, y); push(w - 1, y); }
    while (stack.length) {
      const [x, y] = stack.pop();
      push(x + 1, y); push(x - 1, y); push(x, y + 1); push(x, y - 1);
    }
    let holes = 0;
    for (let y = 0; y < h; y += 1) {
      for (let x = 0; x < w; x += 1) {
        if (seen[y][x] || grid[y][x] === "#") continue;
        holes += 1;
        const inner = [[x, y]];
        while (inner.length) {
          const [cx, cy] = inner.pop();
          if (cx < 0 || cy < 0 || cx >= w || cy >= h || seen[cy][cx] || grid[cy][cx] === "#") continue;
          seen[cy][cx] = true;
          inner.push([cx + 1, cy], [cx - 1, cy], [cx, cy + 1], [cx, cy - 1]);
        }
      }
    }
    return holes;
  }

  function glyphShape(font, ch) {
    const size = 96;
    const cs = document.createElement("canvas");
    cs.width = size;
    cs.height = size;
    const ctx = cs.getContext("2d", { willReadFrequently: true });
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, size, size);
    ctx.fillStyle = "#000";
    ctx.font = `${Math.round(size * 0.66)}px ${font}`;
    ctx.textBaseline = "middle";
    ctx.fillText(ch, 6, size / 2);
    const data = ctx.getImageData(0, 0, size, size).data;
    const ink = [];
    let minX = size;
    let maxX = -1;
    let minY = size;
    let maxY = -1;
    for (let y = 0; y < size; y += 1) {
      for (let x = 0; x < size; x += 1) {
        const on = data[(y * size + x) * 4] < 128;
        ink.push(on);
        if (!on) continue;
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
    if (maxX < 0) return null;
    const grid = [];
    for (let gy = 0; gy < N; gy += 1) {
      let row = "";
      for (let gx = 0; gx < N; gx += 1) {
        const x0 = minX + Math.floor(((maxX - minX + 1) * gx) / N);
        const x1 = Math.max(minX + Math.floor(((maxX - minX + 1) * (gx + 1)) / N), x0 + 1);
        const y0 = minY + Math.floor(((maxY - minY + 1) * gy) / N);
        const y1 = Math.max(minY + Math.floor(((maxY - minY + 1) * (gy + 1)) / N), y0 + 1);
        let hits = 0;
        for (let y = y0; y < y1; y += 1) for (let x = x0; x < x1; x += 1) if (ink[y * size + x]) hits += 1;
        row += hits > 0 ? "#" : ".";
      }
      grid.push(row);
    }
    return {
      grid,
      holes: countHoles(grid),
      mass: grid.join("").split("").filter((c) => c === "#").length,
      aspect: (maxX - minX + 1) / (maxY - minY + 1),
    };
  }

  function gridIoU(a, b) {
    let inter = 0;
    let uni = 0;
    for (let y = 0; y < a.length; y += 1) {
      for (let x = 0; x < a[y].length; x += 1) {
        const one = a[y][x] === "#";
        const two = b[y][x] === "#";
        if (one || two) uni += 1;
        if (one && two) inter += 1;
      }
    }
    return uni ? inter / uni : 0;
  }

  // 一对一最大权匹配：孔洞数必须与数字一致，且「细长字形只许是 1、1 只许是细长字形」
  function assignDigits(items, refs) {
    const score = (i, d) => {
      const r = refs[d];
      if (!r) return -1;
      if (!DIGIT_HOLES[d].includes(items[i].shape.holes)) return -1;
      if ((items[i].shape.aspect < 0.5) !== (d === 1)) return -1;
      return gridIoU(items[i].shape.grid, r.grid);
    };
    let best = { total: -1, map: null };
    const used = new Set();
    const picked = new Array(items.length).fill(null);
    const walk = (i, acc) => {
      if (best.map && acc + items.length - i <= best.total) return;
      if (i === items.length) {
        if (acc > best.total) best = { total: acc, map: [...picked] };
        return;
      }
      for (let d = 0; d <= 9; d += 1) {
        const s = score(i, d);
        if (s < 0 || used.has(d)) continue;
        used.add(d);
        picked[i] = d;
        walk(i + 1, acc + s);
        used.delete(d);
        picked[i] = null;
      }
    };
    walk(0, 0);
    return best.map;
  }

  function salaryFontFamily() {
    const el = document.querySelector(".job-salary");
    if (!el) return "";
    return getComputedStyle(el).fontFamily || "";
  }

  function post(payload) {
    const message = { type: "jobbot-cipher-map", ...payload };
    // 隔离环境的监听器可能还没挂上（MAIN world 与本脚本同时注入），结果额外写进 data-* 供拉取。
    try {
      document.documentElement.dataset.jobbotCipher = JSON.stringify(message);
    } catch (error) {
      // 序列化失败不影响主路径，postMessage 仍会尝试
    }
    window.postMessage(message, "*");
  }

  let lastFailure = "";

  function unquote(value) {
    return String(value || "").trim().replace(/^["']+|["']+$/g, "");
  }

  // BEGIN salaryTextState —— 纯函数：data/verify-20261104/cipher-plain-teeth.mjs 会把这段原文切回来 eval
  // 页面薪资此刻是哪种形态。分错一格的代价不对称：
  //   把「还没渲染」（全是空串）误判成明文 ⇒ 停止重试，真 glyph 迟到了也再也不去看＝漏解；
  //   把「混着私用区」误判成明文 ⇒ 同上，只是这条险由 cipherChars 先兜住。
  // 所以 pending 是默认，plain 只在「有字、且一个私用区字符都没有」时才给。
  function salaryTextState(texts) {
    const list = texts || [];
    let cipher = 0;
    let plain = 0;
    let empty = 0;
    for (const value of list) {
      const text = String(value == null ? "" : value).trim();
      if (!text) {
        empty += 1;
        continue;
      }
      if (/[\uE000-\uF8FF]/.test(text)) {
        cipher += 1;
        continue;
      }
      plain += 1;
    }
    if (cipher) return { state: "cipher", cipher, plain, empty };
    if (plain) return { state: "plain", cipher, plain, empty };
    return { state: "pending", cipher, plain, empty };
  }
  // END salaryTextState

  function salaryTexts() {
    const out = [];
    document.querySelectorAll("[class*='salary']").forEach((el) => out.push(el ? el.innerText || "" : ""));
    return out;
  }

  // 加密字体未必挂在 .job-salary 上，可能只在里面的子节点；候选要含「最深的含私用区字符元素」的计算字体、
  // salary 元素的字体栈（整串与拆开都试），以及页面注册过的每个 @font-face 家族名。
  function familyCandidates() {
    const out = [];
    const addStack = (value) => {
      const stack = String(value || "").trim();
      if (!stack) return;
      if (!out.includes(stack)) out.push(stack);
      stack.split(",").forEach((raw) => {
        const token = unquote(raw);
        if (!token || ["sans-serif", "serif", "monospace", "initial", "inherit"].includes(token)) return;
        if (!out.includes(token)) out.push(token);
      });
    };
    let deepest = null;
    document.querySelectorAll("[class*='salary']").forEach((host) => {
      [host, ...host.querySelectorAll("*")].forEach((el) => {
        const ownsCipher = [...el.childNodes].some(
          (node) => node.nodeType === 3 && /[\uE000-\uF8FF]/.test(node.textContent || "")
        );
        if (ownsCipher && (!deepest || deepest.contains(el))) deepest = el;
      });
    });
    if (deepest) addStack(getComputedStyle(deepest).fontFamily);
    addStack(salaryFontFamily());
    if (document.fonts && document.fonts.forEach) document.fonts.forEach((face) => addStack(face.family));
    return out;
  }

  function collectGlyphs(chars, font) {
    const items = [];
    const seenShapes = new Set();
    for (const code of [...chars].sort((a, b) => a - b)) {
      const shape = glyphShape(font, String.fromCodePoint(code));
      if (!shape || shape.mass < 30) continue;
      if (shape.holes === 1 && shape.mass > 50 && shape.mass < 58 && Math.abs(shape.aspect - 0.4) < 0.01) continue; // 缺字方框
      const key = `${shape.grid.join("")}|${shape.holes}|${shape.mass}`;
      if (seenShapes.has(key)) continue;
      seenShapes.add(key);
      items.push({ code, shape });
      if (items.length === 10) break;
    }
    return items;
  }

  async function solve() {
    const cipherChars = new Set();
    document.querySelectorAll("[class*='salary']").forEach((el) => {
      [...(el.innerText || "")].forEach((ch) => {
        const code = ch.codePointAt(0);
        if (code >= 0xe000 && code <= 0xf8ff) cipherChars.add(code);
      });
    });
    if (!cipherChars.size) {
      const st = salaryTextState(salaryTexts());
      // 明文数字／「面议」这类本来就没有加密集合：直接收工并说清是哪种，别再让监视面读到一条
      // 「解了 12 次仍失败」——那行会让人去查一条根本不存在的解码故障。
      if (st.state === "plain") {
        post({
          map: null,
          plain: true,
          reason: `薪资已是明文（有字 ${st.plain} 个、空 ${st.empty} 个、私用区 0 个），无需解码`,
          family: salaryFontFamily() || "",
        });
        return true;
      }
      lastFailure = `页面还没有私用区字符（salary 元素 ${st.cipher + st.plain + st.empty} 个｜明文 ${st.plain} 个／空 ${st.empty} 个${st.empty ? "，可能还没渲染" : ""}）`;
      return false;
    }
    const chars = new Set(cipherChars);
    for (let cp = 0xe000; cp <= 0xe0ff && chars.size < 64; cp += 1) chars.add(cp);
    const refs = {};
    for (const d of "0123456789") refs[d] = glyphShape("sans-serif", d);
    const candidates = familyCandidates();
    let font = "";
    let items = [];
    const tried = [];
    for (const candidate of candidates.length ? candidates : ["sans-serif"]) {
      const got = collectGlyphs(chars, candidate);
      tried.push(`${candidate}\u2192${got.length}`);
      if (got.length > items.length) {
        items = got;
        font = candidate;
      }
      if (got.length === 10) break;
    }
    if (items.length !== 10) {
      lastFailure = `候选字体都取不满 10 个字形（最多 ${items.length} 个，需 10 个）｜尝试 ${tried.join(" ") || "无候选字体"}`;
      return false;
    }
    const stats = items.map((it) => `${it.code.toString(16)}(h${it.shape.holes} a${it.shape.aspect.toFixed(2)} m${it.shape.mass})`).join(" ");
    const refStats = Object.entries(refs).map(([d, r]) => `${d}(h${DIGIT_HOLES[d]} a${r ? r.aspect.toFixed(2) : "?"})`).join(" ");
    const map = assignDigits(items, refs);
    if (!map) {
      post({ map: null, reason: `字形与数字一对一匹配无可行解｜字体=${font}｜字形 ${stats}｜参考 ${refStats}`, family: font });
      return true;
    }
    post({ map: items.map((it, i) => [it.code, map[i]]), stats, family: font });
    return true;
  }

  async function run(attempts, gapMs) {
    for (let i = 0; i < attempts; i += 1) {
      try {
        if (await solve()) return;
      } catch (error) {
        post({ map: null, reason: `解码异常 ${error}`, family: salaryFontFamily() || "" });
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, gapMs));
    }
    post({ map: null, reason: `${attempts} 次重试（约 ${Math.round((attempts * gapMs) / 1000)} 秒）后仍未解出：${lastFailure}`, family: salaryFontFamily() || "" });
  }

  const start = () => run(12, 1000);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(start).catch(start);
  else start();
})();
