# ai.robot.cv

简历机器人

AI 求职自动化工具：按配置搜索职位、匹配评分、生成个性化打招呼语、投递并记账。默认只读，只有显式确认才会真正发送消息。

## 1. 目标对照

| `目标.md` | 实现 |
| --- | --- |
| 自动化投递简历 | Playwright 驱动真实浏览器，登录后搜索岗位并发送打招呼 |
| AI 找工作 | 规则初筛 + 可选 LLM 精排与打招呼语生成；无 Key 自动降级 |
| 地区可配置 | `config/config.yaml` → `platforms.boss.city / city_code` |
| 平台可配置 | `platforms.*.enabled`；当前实现 BOSS，51job/智联预留 |
| 每个平台数量可配置 | `limit_per_run`（每次）与 `limit_per_day`（每日） |
| BOSS / 大连 / 5 | 已写入默认配置 |

## 2. 快速开始

```bash
cd ai.robot.cv.git
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 安装 Chromium（首次一次即可）
playwright install chromium

# 离线验证整条流水线（不联网、不登录、不发消息）
jobbot demo
```

> 本机是 Ubuntu 20.04，新版 Playwright 已不支持其自带 Chromium。工具会自动回退到系统 Chromium（本机为 `/snap/bin/chromium`），`jobbot doctor` 会显示实际使用的浏览器。若都没有，可 `export JOBBOT_CHROMIUM=/path/to/chrome`。

真实使用：

```bash
# 1) AI 默认使用本机 Ollama 的 deepseek-r1:8b（本地开、在线关）
# 先自检并实测一次本地推理（测试完会自动卸载模型）
jobbot doctor --ai-test

# 可选：临时改用在线模型（需先设置 Key；本行会临时启用 online）
export OPENAI_API_KEY=sk-...
jobbot --ai online plan

# 2) 首次扫码登录（系统 Chromium），登录态保存在 data/browser/boss
jobbot login

# 3) 生成计划（只读）：搜索 → 评分 → 生成招呼语，不发送
jobbot plan

# 4) 审阅计划后执行：默认逐条确认，加 --yes 跳过确认
jobbot apply --plan <计划文件> --yes

# 5) 查看日报
jobbot report
```

一条命令完成 3-4 步：

```bash
jobbot run --yes
```

> `run`/`apply` 不加 `--yes` 时不会发送任何消息；非交互环境（脚本/CI）必须显式 `--yes`。

可选：每个工作日 10:00 自动跑一次（限额已在配置里，默认每天最多 5 份）：

```cron
0 10 * * 1-5 cd /path/to/ai.robot.cv.git && .venv/bin/jobbot run --yes >> data/cron.log 2>&1
```

## 3. 命令

| 命令 | 作用 |
| --- | --- |
| `jobbot doctor [--live] [--ai-test]` | 自检配置、简历、台账、浏览器、AI provider；`--live` 检查登录态，`--ai-test` 真实调用一次 AI 并报告延迟 |
| `jobbot login` | 用系统 Chromium 手工扫码登录（避开 BOSS 对 Playwright 登录页的 `about:blank` 拦截） |
| `jobbot plan [--limit N]` | 搜索并生成投递计划（只读，不发送） |
| `jobbot apply --plan <文件或 run_id> [--yes]` | 执行计划；默认逐条确认，`--yes` 全部发送 |
| `jobbot run [--yes]` | `plan` + `apply` 一条命令 |
| `jobbot report [--date YYYY-MM-DD]` | 输出当日台账并写 Markdown 日报 |
| `jobbot demo` | 用 `examples/sample_jobs.json` 离线跑通全流程 |

全局参数：`--config`（默认 `config/config.yaml`）、`--db`（覆盖台账路径）、`--ai local|online|off`（覆盖 AI provider）、`-v`。

退出码：`0` 成功、`2` 配置/参数错误、`3` 需要登录、`4` 页面结构/适配器错误、`5` 有条目失败、`6` 触发验证码或风控、`130` 中断。

## 4. 配置

`config/config.yaml` 是唯一入口：

```yaml
platforms:
  boss:
    enabled: true
    city: 大连
    city_code: "101070200"
    limit_per_run: 5     # 每次最多投 5 份
    limit_per_day: 5     # 当日最多投 5 份
```

常用项：

| 配置 | 说明 |
| --- | --- |
| `search.keywords` | 搜索关键词（默认 Java / Java 后端 / Spring Boot） |
| `search.exclude_keywords` | 标题/描述命中即跳过（默认销售、保险、房产中介、培训讲师、实习） |
| `search.salary_range_k` | 期望薪资（K），与简历一致：13-26 |
| `search.max_pages` | 每个关键词最多翻页数 |
| `defaults.min_score` | 进入计划的最低分（0-100，默认 60） |
| `defaults.min/max_interval_seconds` | 两次发送之间的随机间隔（默认 20-45 秒） |
| `defaults.headless` | 是否无头运行；建议首次保持 `false` 便于观察 |
| `ai.provider` | 默认 `local`（Ollama）；可改为 `online` |
| `ai.fallback` | 默认 `null`（不回退）；回退目标必须 `enabled: true` |
| `ai.providers.local.enabled` | 默认 `true`（本地开） |
| `ai.providers.online.enabled` | 默认 `false`（在线关） |
| `ai.providers.local.*` | Ollama 地址/模型/超时/上下文/思考预算；默认 `deepseek-r1:8b` |
| `ai.providers.online.*` | OpenAI 兼容地址/模型/Key 环境变量 |
| `ai.use_for_scoring` | AI 精排；本地 8B CPU 默认关闭（详见第 5 节） |

切换地区/平台/数量只改 YAML，无需改代码。51job、智联的配置骨架已在文件中以注释给出，实现对应适配器后把 `enabled` 改为 `true` 即可。

## 5. AI 与匹配

### 5.1 本地 / 在线两种 provider

默认策略：**本地开，在线关**。

```yaml
ai:
  provider: local
  fallback: null
  providers:
    local:
      enabled: true
    online:
      enabled: false
```

| provider | 默认 | 协议 | 默认模型 | 需要 Key | 适用 |
| --- | --- | --- | --- | --- | --- |
| `local` | 开 | Ollama 原生 `/api/chat` + `/api/tags` | `deepseek-r1:8b` | 否 | 本机 Ollama，数据不出本机 |
| `online` | 关 | OpenAI 兼容 `/chat/completions` | `gpt-4o-mini` | 是（`OPENAI_API_KEY`） | 更快，或本地不可用时回退 |

选择优先级：命令行 `--ai local|online|off` > `config.yaml` 的 `ai.provider`。`--ai online` 会在本次运行中临时把 online 置为启用；显式写 `--ai` 时只使用该 provider，不再回退。想让在线长期可用，把 `online.enabled` 改成 `true`，并设置 `fallback: online` 才会自动回退。

```bash
jobbot --ai local doctor          # 查看本地 provider（默认）
jobbot --ai online plan           # 本次临时启用在线模型
jobbot --ai off plan              # 本次完全关闭 AI
```

### 5.2 匹配与招呼语

**两段式匹配**：

1. 规则评分：技能命中（核心 8 分/项、熟悉 4 分/项，上限 40）、Java 后端标题、薪资区间、经验要求、城市、详情完整度；命中排除词直接 0 分。
2. AI 精排（`ai.use_for_scoring: true` 时）：模型返回 `fit/reasons/risks`，最终分 = 规则分 × 70% + AI 分 × 30%，避免模型单点失控。

**打招呼语**：AI 只能使用 `config/profile.yaml` 中的事实，输出 120 字以内正文；后置校验拒绝空文本、占位符、缺少姓名或技能的内容，超长时按句号截断，不合格自动回退模板。

**降级**：Ollama 不可达、模型未安装、在线无 Key、超时、返回为空或 JSON 解析失败，都会记录 warning 并继续使用规则分与模板，不中断投递。

### 5.3 本地 deepseek-r1:8b 的实测性能

本机 Ollama 已配置 `OLLAMA_NUM_GPUS=0`（纯 CPU），实测：

| 项目 | 结果 |
| --- | --- |
| `doctor --ai-test`（冷启动 + 16 token 回答） | 成功，约 **64–159 秒**（受磁盘缓存影响），返回 `OK`，测试后自动卸载 |
| `plan --fixture --limit 1` 端到端 | 成功，`greeting_source: ai`，招呼语 115 字，总耗时约 **152 秒** |
| 一次评分调用（budget=2048，ctx=6144） | **成功**，返回合法 JSON：`fit=96` + 3 条理由 + 3 条风险，耗时 **500.7 秒**（约 8.3 分钟） |

R1 会先输出一段“思考”内容，`think: false` 在本机版本上未能完全抑制。`thinking_budget` 会为最终答案额外预留 token；当前本地配置为 `budget=2048`、`ctx=6144`、`timeout=900s`，评分已可完成，但单条约 8 分钟。因此 `use_for_scoring` 仍默认 `false`；需要本地精排时改为 `true`，并注意一次计划会对多个候选逐一评分，耗时可能接近一小时。更实用的是改 true 后用 `--ai online` 做精排。

Prompt 中职位描述被包裹在 `<jd>...</jd>` 并明确标注为数据，防止 JD 中的文字被当成指令。

## 6. BOSS 首次使用与维护

BOSS 会检测 Playwright 并将登录页跳转到 `about:blank`。因此 `jobbot login` 不再用 Playwright 打开登录页，而是启动本机系统 Chromium：

1. 运行 `jobbot login`，会弹出一个普通 Chromium 窗口；
2. 手机扫码登录；若出现「安全验证」，在该窗口人工完成；
3. 终端检测到登录 Cookie 后会提示成功，请关闭登录浏览器窗口，然后使用 6.1 的浏览器扩展路径（Playwright 搜索页会被 BOSS 跳空）。

BOSS 的「投递」在本工具中实现为「立即沟通 + 发送打招呼」；简历以你在 BOSS 站内维护的在线简历为准，工具不会上传本地图片或伪造附件。

### 6.1 浏览器扩展（真实 BOSS 路径）

BOSS 同样会拦截 Playwright 的搜索页（跳 `about:blank`），真实投递由 `extension/` 在普通 Chromium 内完成：

1. 启动本地 AI 桥：`jobbot bridge --port 8765`；
2. 用普通 Chromium 加载扩展：

```bash
/snap/bin/chromium --user-data-dir="$PWD/data/browser/boss" \
  --disable-extensions-except="$PWD/extension" \
  --load-extension="$PWD/extension" \
  "https://www.zhipin.com/web/geek/job?query=Java&city=101070200"
```

3. 在 BOSS 页面用扩展面板扫描/生成 AI 招呼语；发送时扩展选中会话、填入 contenteditable 编辑器并回车，确认 BOSS 气泡后下载结果；
4. 注意：点击「立即沟通」本身会先发送 BOSS 默认招呼语，AI 招呼会作为后续消息发送。要避免两条消息，可先在 BOSS「设置招呼语」中把默认招呼改成 AI 版本。

真实实测（2026-10-31）：本地 AI 生成的招呼语已发送给「杭州学天教育 / 后端JAVA开发工程师（Base杭州）」，BOSS 聊天页 23:24 显示「送达」。

## 7. 安全边界

- **默认只读**：`plan` 不发消息；`apply/run` 必须 `--yes` 或逐条确认。
- **限速与限额**：随机 20-45 秒间隔，单次与每日双限额，默认 5 份。
- **不绕过验证**：检测到验证码/风控即停机并保存现场。
- **不伪造经历**：AI 只改写措辞，不新增简历中不存在的事实。
- **隐私本地化**：台账、计划、日报、浏览器数据都在 `data/`（已 gitignore），不上传第三方。
- **结果不确定时保守处理**：点击发送后未确认到气泡，记为 `unknown` 并 7 天内阻止重复投递，提示人工核对。
- **合规提示**：自动化操作可能违反招聘平台用户协议并带来账号风控风险。请低频、本人使用，并自行确认平台条款。

## 8. 数据与目录

```text
config/          config.yaml / profile.yaml / selectors.boss.yaml
src/jobbot/      源码（CLI、匹配、AI、平台适配器、台账、报告、bridge）
extension/       Chromium 扩展（BOSS 真实投递路径）
examples/        离线演示职位数据
tests/           110 个 pytest 用例（含 Playwright 浏览器级联调、AI HTTP 回环、登录 Cookie 检测）
docs/            设计方案.md
data/            运行时数据（已忽略）
  jobbot.sqlite3   投递台账（去重、状态、每日计数）
  plans/           投递计划与结果 JSON
  reports/         Markdown 日报
  browser/boss/    浏览器登录态（含 Cookie，注意隐私）
  debug/           选择器失效/风控现场截图与 HTML
  demo-*/          离线演示产物
```

## 9. 验证状态

- 离线流水线：`jobbot demo` 实跑通过，选中 5 个职位，模拟投递 5 条，台账去重复核 `6/9`，日报生成。
- 自动化测试：`python -m pytest` → **110 passed**。覆盖配置校验、薪资/经验解析、评分与排除词、台账去重与配额、招呼语校验与截断、AI 双协议/真实 HTTP 回环/探测/回退/重试/provider 默认开关与启停、`--ai` 切换、登录 Cookie 检测、BOSS HTML 解析与登录/风控检测、计划限额、CLI 端到端（demo / run / apply / report）。
- 默认策略实测：`jobbot doctor` → `local` 可用、`online` 显示「已在配置中关闭」；`jobbot --ai online doctor` → 选择 online 并临时启用（本机无 Key，显示未设置 Key）。
- AI 本地实测：`jobbot doctor --ai-test` → 本地 `deepseek-r1:8b` 成功返回 `OK`（64.4–158.6 秒，随冷启动/缓存变化），测试后自动卸载；`plan --fixture --limit 1` → 生成计划且 `greeting_source: ai`（151.7 秒，招呼语 115 字）。
- 本地评分实测（`budget=2048`、`ctx=6144`）：成功返回合法 JSON，`fit=96`，耗时 **500.7 秒**；默认仍关闭 `use_for_scoring`，避免每日计划耗时过长。
- 全链路离线实测（fixture + 本地 AI 精排 + 招呼 + 投递 + 台账/日报）：1 条，规则分 96 / AI 分 96，`greeting_source: ai`，总耗时 **10 分 46 秒**，退出码 0；投递为 fixture 模拟，不是真实 BOSS。
- AI 在线：本机未设置真实 `OPENAI_API_KEY`，未向外部服务发请求；用本地 HTTP mock 验证了 `/chat/completions` 请求体与 `Bearer` 鉴权，失败回退由单测覆盖。
- 适配器浏览器级联调：用本地合成站点驱动真实 Playwright + 系统 Chromium，验证「搜索 → 解析职位卡 → 拉详情 → 点击立即沟通 → 填招呼语 → 回车发送 → 消息气泡确认」整条链路。
- 真实 BOSS 链路（2026-10-31）：普通 Chromium 登录 → BOSS 聊天列表选中「杭州学天教育」→ `jobbot bridge` 用本地 `deepseek-r1:8b` 生成 AI 招呼语（source=ai，118.7 秒）→ 扩展填入并回车 → BOSS 聊天页 23:24 显示 AI 气泡「送达」；主台账/日报各 1 条。
  - 证据：`data/e2e-live/real-send-verified.png`、`data/e2e-live/verification.json`、`data/reports/jobbot-report-2026-10-31-all.md`
  - 边界：登录与职位选择为人工操作；Playwright 搜索路径被 BOSS 拦截，真实发送由浏览器扩展完成。扩展首次结果 JSON 的 job/greeting 为空（状态未赋值），代码已修复，本次以聊天页气泡与列表为准补记台账。
  - 注意：同一会话此前 22:56 已发送过一条 BOSS 默认招呼语；23:24 发送的是 AI 招呼语。
- 真实浏览器冒烟测试（本机）：系统 Chromium 156 可由 Playwright 启动并访问 BOSS 首页；新开的无登录态会话被 BOSS 重定向到 `/web/passport/zp/verify.html` 安全验证页，`check_login` 正确返回未登录并提示人工登录，工具没有硬闯验证。真实搜索与发送必须在完成人工登录后验证。

## 10. 常见问题

| 现象 | 处理 |
| --- | --- |
| `浏览器启动失败` | 运行 `playwright install chromium`；或 `export JOBBOT_CHROMIUM=/usr/bin/chromium` 指向本机浏览器。本机 Ubuntu 20.04 会自动使用 `/snap/bin/chromium` |
| 登录页跳 `about:blank` | BOSS 拦截了 Playwright。当前 `jobbot login` 已改为系统 Chromium；若仍出现，关闭窗口后重跑 `jobbot login` |
| 登录时出现「安全验证」 | 在 `jobbot login` 打开的浏览器里人工完成验证后再扫码 |
| `登录态失效` | 重新 `jobbot login` |
| 搜索无结果 | 检查关键词、城市码、账号是否被限制；先用浏览器手动搜索确认 |
| 找不到「立即沟通」按钮 | 页面改版，按 `data/debug/` 更新 `config/selectors.boss.yaml` |
| 发送后提示“未确认” | 到 BOSS 聊天页人工核对；台账记为 `unknown`，7 天内不会重复投递 |
| 没有入选职位 | 降低 `defaults.min_score`、增加关键词，或查看计划中的跳过原因 |
| `Ollama 不可达` | `systemctl status ollama`；`curl http://127.0.0.1:11434/api/tags` |
| `未找到模型 deepseek-r1:8b` | `ollama pull deepseek-r1:8b`，再 `jobbot doctor --ai-test` |
| 本地 AI 很慢 | 本机为纯 CPU 推理，单条招呼语约 2-3 分钟属正常；可 `--ai online`，或保持 `ai.use_for_scoring: false` |
| AI 未生效 | 先 `jobbot doctor --ai-test`；本地看 provider 是否可用；在线需设置 Key，并把 `online.enabled` 改 true 或本次加 `--ai online` |

## 11. 设计文档

架构、数据流、配置语义、失败策略与扩展点见 [docs/设计方案.md](docs/设计方案.md)。
