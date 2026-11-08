# ai.robot.cv

简历机器人

AI 求职自动化工具：按配置搜索职位、匹配评分、生成个性化打招呼语、投递并记账。默认只读，只有显式确认才会真正发送消息。

## 1. 目标对照

| `AGENTS.md` | 实现 |
| --- | --- |
| 自动化投递简历 | Playwright 驱动真实浏览器，登录后搜索岗位并发送打招呼 |
| AI 找工作 | 规则初筛 + 可选 LLM 精排与打招呼语生成；无 Key 自动降级 |
| 地区可配置 | `config/config.yaml` → `platforms.boss.city / city_code` |
| 平台可配置 | `platforms.*.enabled`；当前实现 BOSS，51job/智联预留 |
| 每个平台数量可配置 | `limit_per_run`（每次）与 `limit_per_day`（每日） |
| BOSS / 大连+北京 / 3 | 已写入默认配置 |

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

一键启动（整合桥＋看门狗＋扩展浏览器＋自动计划，推荐）：

```bash
./start_jobbot.py            # 搜索+评分+生成招呼语，只计划不发送（默认安全）
./start_jobbot.py --send     # 计划完成后自动发送招呼（外发！慎用）
./start_jobbot.py --limit 5 --send
```

脚本自动起本地 AI 桥与存储看门狗（已在跑则复用）、按 `config/config.yaml` 生成入口 URL 开浏览器跑 `auto_plan`、等收官后跑 `audit_run` 对账；snap 浏览器在受限 shell 下自动经 `systemd-run --user` 起。需先 `jobbot login` 登录。默认不发送，显式 `--send` 才走外发腿。下面是分步手动版。

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

可选：每个工作日 10:00 自动跑一次（限额已在配置里，默认每天最多 3 份）：

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
| `jobbot bridge --port 8765` | 浏览器扩展的本地 AI 桥 |
| `jobbot plan-url [--limit N] [--run ID] [--explain]` | 按 `config.yaml` 现值拼出扩展 `auto_plan` 的入口 URL（打印一行，不联网、不发送） |
| `jobbot import-extension --file <结果 JSON>` | 把扩展发送结果导入主台账 |
| `jobbot simulate [--fixture 文件] [--window-days N]` | 模拟投递：应用去重/评分/限额，不发送、不写台账 |

全局参数：`--config`（默认 `config/config.yaml`）、`--db`（覆盖台账路径）、`--ai local|online|off`（覆盖 AI provider）、`-v`。

退出码：`0` 成功、`2` 配置/参数错误、`3` 需要登录、`4` 页面结构/适配器错误、`5` 有条目失败、`6` 触发验证码或风控、`130` 中断。

## 4. 配置

`config/config.yaml` 是唯一入口：

```yaml
platforms:
  boss:
    enabled: true
    cities:
      - name: 大连
        code: "101070200"
      - name: 北京
        code: "101010100"
    limit_per_run: 3     # 每次最多投 3 份
    limit_per_day: 3     # 当日最多投 3 份
```

常用项：

| 配置 | 说明 |
| --- | --- |
| `search.keywords` | 搜索关键词（默认 Java / Java 后端 / Spring Boot） |
| `search.exclude_keywords` | 标题/描述命中即跳过（默认销售、保险、房产中介、培训讲师、实习） |
| `search.salary_range_k` | 期望薪资（K），与简历一致：13-26 |
| `search.salary_floor_k` | 命中下限（当前 12）：岗位薪资**上限** ≥ 这个数就算命中（+20），8-12K、10-12K 这类贴边岗位不再被判「低于期望」；**上限低于它的直接拦下、不进计划也不发送**（2026-11-04：8-10K 这类只挂风险项会被分数闸放行，故改成硬拦）。这个键只能放宽、不能收紧（设到期望下限之上按期望下限处理）；删掉它则回到旧口径「上限必须 ≥ 期望下限 13K」 |
| `search.exclude_keywords_in_jd` | 排除词的**第二档范围**（2026-11-04 裁定：正文里的工作地/身份/岗位要拦）：只有列在这里的词才**额外**匹配职位正文，其余 `exclude_keywords` 仍只看标题/公司名/标签。当前装 长期驻外、驻日本、赴日、实习、在校、应届 —— 这些写在 JD 里没有歧义；而「销售/保险/教育/培训讲师」这类不列，因为当天实跑证明 JD 里的福利条款（「保险齐全」「健康保险（日本法定社保）」）会误杀纯 Java 岗（两条 12-24K / 18-22K 被 0 分挡掉）。命中时理由写成「岗位正文命中排除词「X」，主动跳过」，与第一档分开 |
| `search.max_pages` | 每个关键词最多翻页数 |
| `search.exclude_title` | 标题命中即跳过（当前 大模型 / 算法 / AI）。以前只写在扩展里，现在由 `jobbot plan-url` 拼进 `jobbot_exclude_title` 传给扩展 |
| `search.title_keywords` | 标题至少要命中其中一个（当前 Java / 后端 / 服务端）。同上，经 `jobbot plan-url` 的 `jobbot_title_keywords` 生效 |
| `defaults.min_score` | 进入计划的最低分（0-100，默认 60） |
| `defaults.dedupe_window_days` | 同一公司同一职位去重天数，默认 7，可配置 |
| `defaults.min/max_interval_seconds` | 两次发送之间的随机间隔（默认 20-45 秒） |
| `defaults.headless` | 是否无头运行；建议首次保持 `false` 便于观察 |
| `ai.provider` | 默认 `local`（Ollama）；可改为 `online` |
| `ai.fallback` | 默认 `null`（不回退）；回退目标必须 `enabled: true` |
| `ai.providers.local.enabled` | 默认 `true`（本地开） |
| `ai.providers.online.enabled` | 默认 `false`（在线关） |
| `ai.providers.local.*` | Ollama 地址/模型/超时/上下文/思考预算；默认 `deepseek-r1:8b` |
| `ai.providers.online.*` | OpenAI 兼容地址/模型/Key 环境变量 |
| `ai.providers.<名称>.score_prompt` / `.greeting_prompt` | 非空则整段替换默认的精排/招呼系统提示词，用来给不同模型各配一套；当前生效配置只有 `local`（qwen2.5:7b），留空用代码默认；`local_fast`（9b）那两段专用提示词随整段注释一起撤下，原文仍在 `config/config.yaml` 的注释里可贴回 |
| `ai.max_greeting_chars` | 招呼语正文长度上限（当前 120）；超长会截到窗口内最后一个句界 |
| `ai.min_greeting_chars` | 招呼语绝对下限（当前 30）。低于它、或截断后不足上限一半（= 模型写了一大段、窗口里放不下、被砍掉一半以上）都判**残稿**，直接回退模板，不用残稿去发消息 |
| `ai.use_for_scoring` | AI 精排；当前 `true`，评分与招呼语都走生效 provider `local`（qwen2.5:7b，300s），扩展 `auto_plan` 只对规则过线岗位调用 |
| `storage.verdicts` | 桥每次判定追加一行的流水文件（默认 `data/verdicts.jsonl`），平台跑完按 `job_id` 对账用 |

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

上面那句自 2026-11-06 起补充两点（旧句一个字不改，新句只作追加）：

- **每条招呼语会拿到两条按岗位轮换的档案亮点**。`ai.pick_highlights()` 按 `job_id` 确定性地从 `config/profile.yaml` 的「亮点」里挑 2 条（同一岗位重跑拿到同一组，换岗位就换一组），写进用户消息的【本条优先使用的亮点】。把"挑哪条"变成代码决定，是因为 18 条历史招呼语里模型 12 次挑了同一条。（此句为第 117 批所写；同批还加过"要求逐条写进正文"的硬点名段，第 118 批已按同腿实测撤回，见下一条。）
- **「体现不全」这一半走的是硬点名**（第 120 批定稿）。`ai.GREETING_HARD` 三句一起追加在默认提示词后面：① 本条挑中的两条档案亮点要**逐条写进正文**、数字与系统名原样保留；② 正文里必须原样出现技能表中的至少一个原词；③ 第一句仍以「我是<姓名>，」开头，不许用职位名顶替。三句各对应一次真实作废：只加第 ① 句时撞上「未包含任何简历技能」（累计 7/10 条回退成模板），加到第 ② 句时又撞上「未包含姓名」（1/6），补齐第 ③ 句后同腿 9 次采样 **0 回退**。
  裁决数（两臂同走生产入口 `build_greeting`、同 3 条岗、每岗各采 3 次）：基线 提到亮点 10／均值 1.11／达阈 1/9／相似度 0.561；硬点名 16／**1.78**／**6/9**／**0.415**（相似度低＝更多样），两臂回退同为 0。
  `ai.providers.<provider>.greeting_prompt` 仍是**整段替换**，替换后不追加这三句。
- **「合格」不等于「读得通」**。`greeting.tail_defect()` 会标出末句被腰斩的正文（模型自己收在半截短语上，例如以「错误率控制在。」结尾，而它在 120 字窗口内，截断分支不会进）。这一格**只报不拦**：`bridge` 把它连同末 40 字一起写进 `data/verdicts.jsonl` 的 `greeting_tail_defect`／`greeting_tail`，先拿平台真实残句率，再决定拦下之后走模板还是重问一次。

另外一处容易踩的口径：`ai.providers.<provider>.greeting_prompt` 是**整段替换**，替换后不再追加硬点名那一句——要试新提示词就把整段（含 `GREETING_HARD`）一起写进覆盖里。

**降级**：Ollama 不可达、模型未安装、在线无 Key、超时、返回为空或 JSON 解析失败，都会记录 warning 并继续使用规则分与模板，不中断投递。

**劣势闸门**：`assets/简历补充.md`「## 劣势」的三档（年龄 / 学历 / 外语）落在 `config/profile.yaml` 的 `weaknesses`。规则评分据此检查岗位硬要求——年龄上限低于 `age`（-20）、要求「全日制 / 统招 / 学位」（-20）、要求外语口语流利或作为工作语言（-15），命中即扣分并在风险里标名劣势；「…优先 / 可接受 / 非全日制也可」同一小句内不算硬要求。招呼语侧把劣势写进档案与提示词，正文一旦出现「全日制 / 学位 / 流利 / 英语（日语）+交流」即判不合格并回退模板（「非全日制」「不流利」这类如实否定除外）。`weaknesses` 里删掉哪一档，就不再检查那一档。实时发送路径也带着它：`jobbot bridge` 的 `/ai_greeting` 响应含 `risks` 字段，扩展面板在状态行显示分数、在日志区逐条打印劣势风险，人工点「发送」前就看得到是哪一档劣势被命中。只读的 `jobbot simulate` 也不会吞掉这些风险：跳过项会逐条打印命中的劣势，台账仍不写入。

### 5.3 本地 deepseek-r1:8b 的实测性能

本机 Ollama 已配置 `OLLAMA_NUM_GPUS=0`（纯 CPU），实测：

| 项目 | 结果 |
| --- | --- |
| `doctor --ai-test`（冷启动 + 16 token 回答） | 成功，约 **64–159 秒**（受磁盘缓存影响），返回 `OK`，测试后自动卸载 |
| `plan --fixture --limit 1` 端到端 | 成功，`greeting_source: ai`，招呼语 115 字，总耗时约 **152 秒** |
| 一次评分调用（budget=2048，ctx=6144） | **成功**，返回合法 JSON：`fit=96` + 3 条理由 + 3 条风险，耗时 **500.7 秒**（约 8.3 分钟） |

R1 会先输出一段“思考”内容，`think: false` 在本机版本上未能完全抑制。当前分工是：AI 精排用 `local_fast`（qwen2.5:7b，`timeout=300s`，实测 35.7–214.7 秒）；招呼语仍用 R1（`timeout=1500s`，实测 357–434 秒）。R1 做精排不可用：1500/2000 秒都会在 thinking 耗尽输出预算处失败（`done_reason=length`，thinking 3805 / 7364 字），继续加 timeout 不能解决。

Prompt 中职位描述被包裹在 `<jd>...</jd>` 并明确标注为数据，防止 JD 中的文字被当成指令。

### 5.4 精排与招呼用哪个模型

本节作废下列旧说法（它们都在已提交版本里，我不改原句，只在此点名）：第 38 行「AI 默认使用本机 Ollama 的 deepseek-r1:8b」、第 125 行「默认 `deepseek-r1:8b`」、第 151 行 provider 表里 `local` 那行的模型列、第 175 行小节标题「本地 deepseek-r1:8b 的实测性能」、第 271 行故障表里的「未找到模型 deepseek-r1:8b → `ollama pull deepseek-r1:8b`」（行号按本次改动后的工作区现读，可能随后续编辑漂移，以引号里的原文为准）。

同一节（5.3）的末句要单独说明：它**不是**已提交原文，工作区里那句「当前分工是：AI 精排用 `local_fast`（qwen2.5:7b…）；招呼语仍用 R1…」是前面批次就地改写出来的，已提交版本那一位置的原句是「…当前本地配置为 `budget=2048`、`ctx=6144`、`timeout=900s`…因此 `use_for_scoring` 仍默认 `false`…」。改写发生在早于本节笔次的批次，原句随时可用 `git show HEAD:README.md` 取回；按现在的口径，那句改写出来的分工也已作废——精排与招呼统一走 `local`，`local_fast` 只是挂着未启用的第二候选。上面这句是本节自己写的（未提交），就地改掉了指代错误、无旧句可作废：原先它把这句未提交的改写也算进「都在已提交版本里」那一串。

同样被就地改写过的已提交行还有两处，一并列出以免只有 `git diff` 的删除列能看见：第 4 节配置表里 `ai.use_for_scoring` 那行（HEAD 原文「AI 精排；本地 8B CPU 默认关闭（详见第 5 节）」，现在写的是 `true` 且走 `local`）、6.1 第 4 条「自动发送的分数闸」（HEAD 原文只记 `score/passed/risks`，现在多了 `rule_score/ai_fit` 与精排合并那半句）。这两处的 HEAD 原文都用 `git show HEAD:README.md` 取回；本批只新增句子、没有再改写任何已提交行。

现在（2026-11-03）`config/config.yaml` 里**只有一个生效的本地 provider**：`local` = `qwen2.5:7b`，精排与招呼语都走它（`ai.scoring_provider` 不设，代码默认模型也已从 `deepseek-r1:8b` 换成 `qwen2.5:7b`）。原来的 `deepseek-r1:1.5b` 那一段按你的要求**整段注释保留**，没有删除；`local_fast` 这个键名还在，但不再是「精排专用」，它现在挂着第二候选 `qwen3.5:9b`（`enabled: false`）。

选型依据是本地实测，读数在 **CHANGELOG.md 第十三批**：`qwen2.5:7b` 精排 10/10（中位 32.6 秒）+ 招呼 10/10 过校验（中位 20.1 秒）；`qwen3.5:9b` 精排 10/10 但中位 114.45 秒（慢 3.5 倍），fit 档位更多（6 档 vs 4 档）——不过对照实验（把提示词里那行「规则初筛得分」中和成 0，只换传进 `score_job` 的参数、不改生产码）显示 9b 的细档里混着复读那个数字：同一岗位 fit 从 95 掉到 0，`|Δ|=[95,11,7,10]`；7b 两臂逐岗位 `|Δfit|=0`（恒 85，稳定但也不给独立判断）。9b 的招呼语 0/10 过校验，原因是 `GREETING_SYSTEM` 没要求写姓名而 `validate_greeting` 要查姓名。**这两处后来都进了生产码**（第十四批）：`GREETING_SYSTEM` 加了「正文第一句必须以『我是<档案里的姓名>，』开头」，`score_job` 的提示词删掉了「规则初筛得分」那一行。改后复测：9b 招呼语 0/10 → 10/10，但 fit 由 6 档塌成 3 档、ρ(fit,期望序) 从 -0.803 变差到 -0.764（所有规则过线岗位都给同一个 85）；7b 反而由 4 档升到 5 档、ρ 从 -0.74 提到 **-0.894**、招呼语 0 条超限。⇒ 9b 的「细档」来源被证实就是复读那个数字，统一模型定为 `qwen2.5:7b`；9b 的长度问题已找到修法但**没落生产**：改成「只写两句话 + 全文不超过 100 字」后 9b 是 0/5 超限（正文 72–101 字、5/5 过校验），但同一措辞会把 7b 压到 54–58 字（低于 60 字口径）；给 fit 加刻度口径则把 7b 的 3 档抹成 1 档（`[75,75,75,75,75]`），9b 也只从 1 档到 2 档。两条都不适合做成全局措辞；第十六批补了 provider 级提示词覆盖后，这两段已作为 `local_fast`（9b）的专用提示词落进生产，`local`（7b）留空用默认。终版对比（7b 默认 vs 9b 专用，同 10 岗位）：合并后**过闸结论 0 条差异**，但单岗位中位耗时 48.2 秒 vs 193.8 秒、`llama-server` 峰值 RSS 5.05Gi vs 6.89Gi ⇒ 平台实测选 **`qwen2.5:7b`**。详见 CHANGELOG 第十五、十六批。

第十七批（2026-11-03）在同一套 10 岗位、同一入口又加了 `qwen3.5:4b`：十条里 9 条给出有效精排（第二十一批把那一栏分成两层读，见下），中位 65.4 秒、5 档、ρ=-0.785，招呼语 10/10 有 1 条会被新的残稿守卫挡掉（59 字←原文 131 字），10 岗位两侧合计 1133.1 秒，`ollama` 峰值 RSS 3.92Gi。它唯一的优势是最省内存（比 7b 少 1.1Gi），代价是慢一倍；精排那一栏要分两层读：十条里 4b 有一条拿回空正文，但那条的规则分是 0、生产入口在评分闸就返回了（`src/jobbot/bridge.py:84`），**只看生产真会精排的那七条，4b 与 7b 都是 7/7**（第二十一批分层重算，读数在 CHANGELOG）；`qwen2.5:7b` 是十条全有值、中位 30.35 秒、ρ=-0.894、合计 564.6 秒 ⇒ **生效模型仍是 `qwen2.5:7b`，选型没改**（7b 胜在耗时与排序一致性，不胜在精排有效率）。同一批把「43 字残稿能过校验」这个洞补上了：判据在 `greeting.validate_greeting`——截断后不足上限一半，或低于上表 `ai.min_greeting_chars`，即判残稿并回退模板。5.2 里那句已提交的「超长时按句号截断，不合格自动回退模板」原句不动，本段是它的补充。

### 5.5 合并权重、薪资命中口径与两套解码对表（2026-11-03 第二十二批）

- **合并权重**：最终分 = 规则分 × **60%** + AI 精排分 × **40%**（`src/jobbot/matcher.py` 的 `RULE_WEIGHT` / `AI_WEIGHT`）。本节作废第 5.2 节里那句已提交的「最终分 = 规则分 × 70% + AI 分 × 30%，避免模型单点失控」——原句不动，以本节为准；`docs/设计方案.md` 第 5 节流程图那处的 0.7/0.3 同样按本节作废。
- **薪资命中口径**：新增 `search.salary_floor_k: 12`。岗位薪资上限 ≥12K 就算命中（+20），「8-12K / 10-12K」这类贴边岗位不再被判「低于期望」；上限低于 12K（如 8-10K）**直接硬拦**，不进计划也不会被 `auto_send` 发出去。影响面现算（`data/verify-20261103/impact22.py`）：10 岗位夹具**过闸结论 0 条翻转**，7 条过线岗位的合并分只在 ±1 内挪（如 74→75、93→92），规则 31 分那条从 41→45（仍不过闸）；台账 6 行里唯一改变判定的是「大连多麦德」——39 分（带薪资 risk）→ 59 分（无 risk），但仍差 1 分没到 60 闸。
- **两套解码对表（不等即报错）**：扩展现在除了送解好的 `salary`，还附一份页面原串 `salaryRaw`；桥两侧各解一次（扩展按页面真字形形状解，本机按写死的基码表解）。**只有扩展真的解成了 ASCII 数字**时才比对：两个 ASCII 值不等 ⇒ 判定为「平台字体版本变了」，`score_job` 直接返回 0 分、`passed=false`，risk 写「薪资字体两套解码不一致：页面原串本机表解出「X」，扩展按字形解出「Y」」，`auto_send` 因此不会发这条。扩展没解出来（送回来的还是私用区原串）或没送 `salaryRaw`（旧版扩展、CLI 解析路径）时**不算冲突**，走「本机未取到 / 表解出的区间」那一档如实报——2026-11-04 平台实跑第一版规则把「扩展没解出」当成不一致，40 条候选全被 0 分拦下，已修正（`src/jobbot/matcher.py:172`，CHANGELOG 第二十三批）。扩展侧同时改成**有界重试**（SPA 列表比 `document_idle` 晚到时也能解出），并把解码状态写进 `chrome.storage.local.jobbotCipherStatus`（`ok`/`reason`/`family`），事后不用盯屏幕也能查。扩展版本号 `0.5.9`，`chrome://extensions` 里看到它才算换上这一版（`0.5.8` 补的是 `auto_send` 入口页闸：入口 URL 只能手工拼，点的是「当前页」的立即沟通、记的却是计划里第 N 条岗位 ⇒ 当前页与这条岗位对不上时先跳回它自己的详情页，跳一次还对不上就记 `skipped` 不发送，避免把 A 的招呼语发给 B；`0.5.9` 补的是详情面板**归属**闸，见下句）；补取详情时以「面板内容换掉了」为准（点新卡片时面板可能仍显示上一岗位的正文，直接拿来会把上一岗位的 JD 算到本岗位头上），换不掉就按卡片信息评分并如实打日志；**这一条在 11-04 平台实跑被证明不够，`0.5.9` 又加了一层归属判定**：面板真的换掉了也可能换成**第一张卡片**的正文（SPA 每次换搜索都会把右侧面板重新选回列表第一条），那次实跑三条岗位的 `description` 逐字节相同（sha `83187275c21d`、383 字，正文末尾写着第 1 条岗位的公司与工作地址），所以取面板前先 `panelBelongsToJob()` 认一次归属——面板里必须出现这条岗位卡片上的公司名（或其剥掉「有限公司/技术/计算机」等后缀后的词干），认不出就按卡片信息评分并打一行「详情面板是别的岗位的正文」；归属只认公司名，因为那份实跑面板里连本岗位自己的标题都没出现（三条 `面板含标题=False`，第 1 条也 `False`）。代价现算：被污染的正文原本贡献「技能命中 +40」中的 24 分与「已获取职位详情 +3」，闸后两条岗位（大连多麦德、大连连英弘）规则分 96 → 69（−27，仍过 60 闸），中谷海运那条本来就因 12K 硬拦不进计划（带面板 55、按卡片 31，两档都 `skipped=True`）——`data/verify-20261104/impact27.py`；被排除词筛掉的卡片现在会逐条留痕（`state.filtered`，含「只在公司名 / 只在标题标签 / 都有」），计划导出 JSON 里能看到这一页到底丢了几张、为什么丢；自动计划的候选在评分前会先点中卡片、等列表页右侧的详情面板出正文（最多 4 秒），拿到才并进 `description` 与标签，拿不到就按卡片信息评并如实打一行日志——不补这一处时规则分普遍少 11-27（10 岗位夹具现算，`description` 抹空重算），AI 精排也是在对着空 JD 打分；找加密字体时不再只看 `.job-salary` 那一层，会把「最深的含私用区字符的子元素」「`.job-salary` 的字体栈」和页面注册过的每个 `@font-face` 家族名逐个试到能画满 10 个字形为止（BOSS 把字体只挂在子节点上时，旧版只能渲染出 1 个杂形、整场解不出）。多城投递时（`platforms.boss.cities` 有两城）评分按候选所属城市判：扩展带 `searchCity`，桥没收到时退回配置里那一个城市——此前写死单城会让第二座城市的每条岗位固定少 8 分（本地重放 40 条真候选：过闸 13/40 → 28/40）。

- **薪资的第三种形态：明文（2026-11-04 第四十四批，扩展 `0.5.14`）**：BOSS 有的页面直接把薪资渲染成明文数字（现读一场里 `salary` 元素 42 个、私用区字符 0 个）。旧版把这种情况报成「12 次重试（约 12 秒）后仍未解出 `ok:false`」，盯日志的人会去查一条根本不存在的解码故障。现在 `jobbotCipherStatus` 记 `ok:true plain:true`，`waitForCipherMap` 也不再为它空烧那 8 秒；**而「元素全是空串＝还没渲染」仍等满窗口**，这条早退不是无条件的。**不改任何判定**：私用区原文即便扩展没解开，桥那份写死的基码表照样解得开（当日存储里 7 条真串逐个过 `parse_salary` 全部解出、`salary_is_obfuscated=true`、对表冲突 0），12K 硬拦不因此失效。

- **技能与标题也进硬闸（2026-11-05 裁定「要。职位匹配，技能匹配」）**：`score_job` 现在有三条「硬拦，不投递」——薪资上限低于 `salary_floor_k`（已有）、**JD 已到手（正文 ≥40 字）而简历技能与它一条都没重合**、**标题既不含目标方向词也不是工程师序列**。三条都「分数照算、只是拦下」（台账里看得出差多少，不是 0 分）。技能那条挂在「正文到手」这个前提上：当日可复原的 19 条岗位里 14 条压根没正文，那 0 命中是「没拿到 JD」不是「技能不匹配」，不看正文就拦会把详情面板没换过来的那一场整场清空（无正文时改挂 risk「判不了技能重合度」，按卡片信息放行——这一处**软化了字面指令**，要按字面「没正文也拦」说一声）。代价现读：那 19 条里新两把闸各拦 **0 条**（4 条 skipped 全被排除词/薪资闸先吃掉），所以它们的实际效果目前只有夹具四格与三层拔牙能证明，平台还没见过。

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

3. 在 BOSS 页面用扩展面板扫描/生成 AI 招呼语；面板状态行显示规则分，日志区逐条打印命中的劣势风险；发送时扩展选中会话、填入 contenteditable 编辑器并回车，确认 BOSS 气泡后下载结果；
4. **自动发送的分数闸（只放行，不默认放行）**：桥随响应返回 `min_score` 与 `passed`（规则分是否过 `config.yaml` 的 `defaults.min_score`）。`auto_plan` 先规则评分，只对规则过线岗位再调 `/ai_score` 做 AI 精排并按 60/40 合并，把 `score/passed/risks/rule_score/ai_fit` 记进计划；`auto_send` **只放行 `passed: true` 的岗位**，`passed: false` 或拿不到判定（例如浏览器里还跑着旧版扩展、桥没回 `passed` 字段）一律拦下，不点「立即沟通」，结果记为 `skipped` 并导出；单条手工点「发送」不被这道闸拦（以你的判断为准，面板已把风险打在日志里）。这道闸读的是 `chrome.storage` 里**那份旧计划**的 `passed`，发送时不重新评分 ⇒ 改过评分口径（如 12K 硬拦）后必须用**新的 run id 重跑 `auto_plan`** 再 `auto_send`，否则旧计划里那条照样会发出去。
5. 自动模式：`auto_plan` 按 hash 里的城市/关键词/数量自动搜索并生成计划（不发送），并按 `jobbot_dedupe_days`（默认 7）跳过同公司同职位；`auto_send` 读取计划逐条发送；发送结果用 `jobbot import-extension --file ~/Downloads/jobbot-auto-send-result.json` 导入主台账；
6. 注意：点击「立即沟通」本身会先发送 BOSS 默认招呼语，AI 招呼会作为后续消息发送。要避免两条消息，可先在 BOSS「设置招呼语」中把默认招呼改成 AI 版本。
7. **薪资数字的私有字体**：BOSS 列表页把薪资数字用私有字体画成私用区码位（U+E000–F8FF），本机拿到的不是数字。扩展里的 `extension/cipher.js` 以 `"world": "MAIN"` 注入页面环境，在浏览器内把字体逐字栅格化→数孔洞→量细长比→按形状做一对一匹配，解出「码位→数字」映射后由 `content.js` 的 `decodeSalary()` 改写候选岗位的 `salary`；解码成功时面板日志区会出现一行 `薪资字体已解码：e031→0 …`。**解不出绝不猜**：等映射最多 2.5 秒，拿不到就按原样上报，桥那边继续如实判「薪资数字被平台字体加密，本机未取到」，评分口径不变。扩展同时把页面原串作为 `salaryRaw` 一起送出，桥会用本机那张写死基码的表再解一次做**对表**，两边不等就按 0 分拦下（见 5.5）。这一步不需要联网、不装依赖，也不动平台页面（只读字体）。

8. **自动计划的入口 URL 不要手拼**：上面第 2 步那条 URL 只打开「大连 + Java」这一页，城市/关键词/数量/排除词都得靠 hash 参数传给扩展；不传就回落 `extension/content.js` 里那套内置缺省（排除词只有 6 条，比 `config.yaml` 的 11 条少 5 条）。用命令现取：

```bash
.venv/bin/jobbot plan-url --explain
```

stdout 那一整行就是入口 URL（含 `jobbot_cities` / `jobbot_keywords` / `jobbot_limit` / `jobbot_exclude` / `jobbot_exclude_title` / `jobbot_title_keywords` / `jobbot_dedupe_days`，全部取自 YAML）；`--explain` 在 stderr 逐条标出每个参数来自哪一行配置。改完 YAML 重跑一次即可，`--limit` 可临时覆盖数量，`--run` 可指定 run id（默认 `boss<时间戳>`；**同一个 id 会续跑上次没跑完的计划，换一批要换 id**）。

## 7. 安全边界

- **默认只读**：`plan` 不发消息；`apply/run` 必须 `--yes` 或逐条确认。
- **限速与限额**：随机 20-45 秒间隔，单次与每日双限额，默认 3 份。
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
tests/           126 个 pytest 用例（含 Playwright 浏览器级联调、AI HTTP 回环、登录 Cookie 检测、扩展结果导入、模拟投递）
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

实测记录与修正履历按 `AGENTS.md`「## 规则」写在 **[CHANGELOG.md](CHANGELOG.md)**，本文件不再记录验证状态。

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
