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
# 1) 可选：配置 AI（不配也能用，自动降级为规则+模板）
export OPENAI_API_KEY=sk-...
# 也支持 DeepSeek / 通义等 OpenAI 兼容服务，改 config.yaml 的 ai.base_url / model

# 2) 首次扫码登录，登录态保存在 data/browser/boss
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
| `jobbot doctor [--live]` | 自检配置、简历、台账、浏览器、AI Key；`--live` 额外打开浏览器检查登录态 |
| `jobbot login` | 打开有头浏览器扫码登录，登录态持久化到本地 |
| `jobbot plan [--limit N]` | 搜索并生成投递计划（只读，不发送） |
| `jobbot apply --plan <文件或 run_id> [--yes]` | 执行计划；默认逐条确认，`--yes` 全部发送 |
| `jobbot run [--yes]` | `plan` + `apply` 一条命令 |
| `jobbot report [--date YYYY-MM-DD]` | 输出当日台账并写 Markdown 日报 |
| `jobbot demo` | 用 `examples/sample_jobs.json` 离线跑通全流程 |

全局参数：`--config`（默认 `config/config.yaml`）、`--db`（覆盖台账路径）、`-v`。

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
| `ai.enabled` | 是否启用 AI；没有 Key 时自动降级 |
| `ai.base_url / model / api_key_env` | OpenAI 兼容接口、模型名、Key 所在环境变量 |

切换地区/平台/数量只改 YAML，无需改代码。51job、智联的配置骨架已在文件中以注释给出，实现对应适配器后把 `enabled` 改为 `true` 即可。

## 5. AI 与匹配

**两段式匹配**：

1. 规则评分：技能命中（核心 8 分/项、熟悉 4 分/项，上限 40）、Java 后端标题、薪资区间、经验要求、城市、详情完整度；命中排除词直接 0 分。
2. AI 精排：启用且有 Key 时，模型返回 `fit/reasons/risks`，最终分 = 规则分 × 70% + AI 分 × 30%，避免模型单点失控。

**打招呼语**：AI 只能使用 `config/profile.yaml` 中的事实，输出 120 字以内正文；后置校验拒绝空文本、占位符、缺少姓名或技能的内容，不合格自动回退模板。

**降级**：无 Key、超时、非 200、JSON 解析失败都会记录 warning 并继续使用规则分与模板，不中断投递。

Prompt 中职位描述被包裹在 `<jd>...</jd>` 并明确标注为数据，防止 JD 中的文字被当成指令。

## 6. BOSS 首次使用与维护

BOSS 的「投递」在本工具中实现为「立即沟通 + 发送打招呼」；简历以你在 BOSS 站内维护的在线简历为准，工具不会上传本地图片或伪造附件。

1. `jobbot login` 打开浏览器，用手机扫码登录；登录态保存在 `data/browser/boss`，之后无需重复登录。
2. 若 BOSS 先弹出「安全验证」，请在该浏览器窗口里人工完成验证；工具会等待登录态，不会尝试绕过。
3. `jobbot plan` 搜索并生成计划。建议先看计划里的分数、理由、跳过统计与招呼语。
4. `jobbot apply --plan <文件>` 逐条确认后发送；发送成功以聊天页出现对应消息气泡为准。
5. 页面改版导致找不到元素时，工具会把截图与 HTML 存到 `data/debug/` 并以退出码 4 停止；修改 `config/selectors.boss.yaml` 即可适配，无需改代码。
6. 遇到验证码、滑块或风控提示时立即停机，状态记为 `requires_action`，不会尝试绕过。请人工处理后再运行。

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
src/jobbot/      源码（CLI、匹配、AI、平台适配器、台账、报告）
examples/        离线演示职位数据
tests/           77 个 pytest 用例（含 Playwright 浏览器级联调）
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
- 自动化测试：`python -m pytest` → **77 passed**。覆盖配置校验、薪资/经验解析、评分与排除词、台账去重与配额、招呼语校验、AI 重试与降级、BOSS HTML 解析与登录/风控检测、计划限额、CLI 端到端（demo / run / apply / report）。
- 适配器浏览器级联调：用本地合成站点驱动真实 Playwright + 系统 Chromium，验证「搜索 → 解析职位卡 → 拉详情 → 点击立即沟通 → 填招呼语 → 回车发送 → 消息气泡确认」整条链路。
- 真实 BOSS 投递：需要你的账号扫码登录后才能验证。代码按当前页面结构编写选择器，首次使用请先 `jobbot plan` 审阅；若页面改版，按 `data/debug/` 现场更新 `selectors.boss.yaml`。本仓库不包含账号凭证，也不会自动绕过平台验证。
- 真实浏览器冒烟测试（本机）：系统 Chromium 156 可由 Playwright 启动并访问 BOSS 首页；新开的无登录态会话被 BOSS 重定向到 `/web/passport/zp/verify.html` 安全验证页，`check_login` 正确返回未登录并提示人工登录，工具没有硬闯验证。真实搜索与发送必须在完成人工登录后验证。

## 10. 常见问题

| 现象 | 处理 |
| --- | --- |
| `浏览器启动失败` | 运行 `playwright install chromium`；或 `export JOBBOT_CHROMIUM=/usr/bin/chromium` 指向本机浏览器。本机 Ubuntu 20.04 会自动使用 `/snap/bin/chromium` |
| 登录时出现「安全验证」 | 在 `jobbot login` 打开的浏览器里人工完成验证后再扫码；首次登录不要用无头模式 |
| `登录态失效` | 重新 `jobbot login` |
| 搜索无结果 | 检查关键词、城市码、账号是否被限制；先用浏览器手动搜索确认 |
| 找不到「立即沟通」按钮 | 页面改版，按 `data/debug/` 更新 `config/selectors.boss.yaml` |
| 发送后提示“未确认” | 到 BOSS 聊天页人工核对；台账记为 `unknown`，7 天内不会重复投递 |
| 没有入选职位 | 降低 `defaults.min_score`、增加关键词，或查看计划中的跳过原因 |
| AI 未生效 | 检查环境变量名与 `ai.api_key_env` 是否一致、`ai.enabled` 是否为 true |

## 11. 设计文档

架构、数据流、配置语义、失败策略与扩展点见 [docs/设计方案.md](docs/设计方案.md)。
