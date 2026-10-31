from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from .adapters import (
    AdapterError,
    BossAdapter,
    FixtureAdapter,
    LoginRequired,
    RiskControlDetected,
    SelectorError,
)
from .ai import AIClient
from .browser import find_system_chromium
from .config import Config, ConfigError, Profile, load_config, load_profile
from .models import Plan
from .planner import Planner, load_plan_file
from .ratelimit import RateLimiter
from .reporter import format_counts, format_plan, write_daily_report
from .storage import Storage, iso, now_local

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_LOGIN = 3
EXIT_ADAPTER = 4
EXIT_PARTIAL = 5
EXIT_RISK = 6


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def _make_adapter(
    cfg: Config,
    profile: Profile,
    platform_name: str,
    fixture: str | None = None,
    headless: bool | None = None,
    logger: logging.Logger | None = None,
):
    pc = cfg.platform(platform_name)
    logger = logger or logging.getLogger("jobbot")
    common = (pc, cfg.search, cfg.defaults, profile, logger)
    if fixture:
        return FixtureAdapter(*common, jobs_path=fixture)
    if pc.name == "boss":
        return BossAdapter(*common, headless=headless, debug_dir=cfg.storage.debug_dir)
    raise ConfigError(f"平台 {pc.name} 尚未实现适配器")


def _apply_plan(
    cfg: Config,
    storage: Storage,
    adapter,
    plan: Plan,
    plan_path: Path,
    assume_yes: bool,
) -> int:
    pc = cfg.platform(plan.platform)
    if not assume_yes and not sys.stdin.isatty():
        print("非交互环境必须显式加 --yes 才会发送消息。", file=sys.stderr)
        return EXIT_CONFIG
    if not plan.items:
        print("计划中没有待投递条目。")
        return EXIT_OK

    day_remaining = max(0, pc.limit_per_day - storage.count_sent_today(pc.name))
    if day_remaining <= 0:
        print(f"今日配额已用完（上限 {pc.limit_per_day}），不再发送。")
        return EXIT_OK

    limiter = RateLimiter(
        cfg.defaults.min_interval_seconds,
        cfg.defaults.max_interval_seconds,
        rng=random.Random(),
    )
    all_yes = assume_yes
    sent = failed = skipped = 0
    requires_action = False
    stop_reason = ""
    records: list[dict] = []

    def record(item, status: str, error: str = "") -> None:
        records.append(
            {
                "job_id": item.job.job_id,
                "title": item.job.title,
                "status": status,
                "error": error,
            }
        )

    print(f"开始投递：{plan.run_id}（今日剩余额度 {day_remaining}）")
    for idx, item in enumerate(plan.items, 1):
        if day_remaining <= 0:
            stop_reason = "今日配额已用完"
            break
        row = storage.get(plan.platform, item.job.job_id)
        if row is not None and row["status"] in ("sent", "unknown", "requires_action"):
            print(f"[{idx}] 跳过（台账状态 {row['status']}）：{item.job.title}")
            skipped += 1
            record(item, row["status"], "台账已存在")
            continue
        if row is not None and row["status"] == "planned" and row["run_id"] != plan.run_id:
            print(f"[{idx}] 跳过（已被其他计划占用）：{item.job.title}")
            skipped += 1
            continue

        if not all_yes:
            print(
                f"\n[{idx}] {item.job.title} · {item.job.company} · {item.job.salary} · "
                f"{item.match.score} 分"
            )
            print(f"招呼语：{item.greeting}")
            answer = input("发送？[y]发送 [n]跳过 [a]全部发送 [q]退出：").strip().lower()
            if answer == "q":
                stop_reason = "用户退出"
                break
            if answer == "a":
                all_yes = True
            elif answer != "y":
                print("  已跳过")
                skipped += 1
                continue

        limiter.before_send()
        print(f"[{idx}] 发送 → {item.job.company} · {item.job.title}")
        result = adapter.send_greeting(item.job, item.greeting)
        if result.ok:
            storage.mark_sent(plan.platform, item.job.job_id, result.evidence)
            sent += 1
            day_remaining -= 1
            print(f"  已投递（{result.evidence}）")
            record(item, "sent")
        elif result.requires_action:
            storage.mark_status(plan.platform, item.job.job_id, "requires_action", result.error)
            failed += 1
            requires_action = True
            stop_reason = "触发验证码/风控"
            print(f"  需人工处理，已停机：{result.error}")
            record(item, "requires_action", result.error)
            break
        elif result.uncertain:
            storage.mark_status(plan.platform, item.job.job_id, "unknown", result.error)
            failed += 1
            print(f"  发送结果未确认：{result.error}")
            record(item, "unknown", result.error)
        else:
            storage.mark_status(plan.platform, item.job.job_id, "failed", result.error)
            failed += 1
            print(f"  失败：{result.error}")
            record(item, "failed", result.error)

    status = "aborted" if requires_action else ("partial" if stop_reason else "completed")
    storage.finish_run(plan.run_id, sent, failed, status, stop_reason)

    result_path = plan_path.with_suffix(".result.json")
    result_path.write_text(
        json.dumps(
            {
                "run_id": plan.run_id,
                "platform": plan.platform,
                "finished_at": iso(now_local()),
                "sent": sent,
                "failed": failed,
                "skipped": skipped,
                "stop_reason": stop_reason,
                "items": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("")
    print(f"完成：已投递 {sent}，失败/待确认 {failed}，跳过 {skipped}")
    if stop_reason:
        print(f"停止原因：{stop_reason}")
    print(f"结果文件：{result_path}")
    if requires_action:
        return EXIT_RISK
    return EXIT_PARTIAL if failed else EXIT_OK


def _resolve_plan_path(cfg: Config, value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    in_plans = cfg.storage.plans_dir / f"{value}.json"
    if in_plans.is_file():
        return in_plans
    raise ConfigError(f"找不到计划文件：{value}（可传绝对路径或 data/plans 下的 run_id）")


def _check_browser() -> tuple[bool, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "未安装 playwright"

    override = os.environ.get("JOBBOT_CHROMIUM", "").strip()
    if override:
        return Path(override).exists(), override
    pw = None
    try:
        pw = sync_playwright().start()
        executable = pw.chromium.executable_path
        if Path(executable).exists():
            return True, executable
        system = find_system_chromium()
        if system:
            return True, f"{system}（系统浏览器）"
        return False, executable
    except Exception as exc:
        return False, f"检查失败：{exc}"
    finally:
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass


def cmd_doctor(args, cfg: Config, profile: Profile) -> int:
    print("== 配置 ==")
    print(f"配置文件：{cfg.path}")
    print(f"项目根目录：{cfg.root}")
    for name, pc in cfg.platforms.items():
        if pc.enabled:
            print(f"平台：{name} · {pc.city} · 每次 {pc.limit_per_run} · 每日 {pc.limit_per_day}")
    print(f"关键词：{'、'.join(cfg.search.keywords)}")
    print(f"排除词：{'、'.join(cfg.search.exclude_keywords) or '无'}")
    print(
        f"最低分：{cfg.defaults.min_score}　"
        f"发送间隔：{cfg.defaults.min_interval_seconds}-{cfg.defaults.max_interval_seconds}s"
    )

    print("\n== AI ==")
    if not cfg.ai.enabled:
        print("AI：已关闭（使用规则 + 模板）")
    else:
        print(
            f"选择：{cfg.ai.provider}　回退：{cfg.ai.fallback or '无'}"
            f"　AI 精排：{'开' if cfg.ai.use_for_scoring else '关'}"
        )
        ai_client = AIClient(cfg.ai, profile)
        statuses = ai_client.statuses()
        for name, provider in cfg.ai.providers.items():
            status = statuses[name]
            state = (
                f"可用（{status.detail}）"
                if status.ok
                else f"不可用：{status.detail}"
            )
            params = ""
            if provider.type == "ollama":
                params = (
                    f" [budget={provider.thinking_budget}"
                    f" ctx={provider.num_ctx}"
                    f" timeout={provider.timeout_seconds:g}s]"
                )
            print(
                f"  {name}: {provider.type}/{provider.model} @ {provider.base_url} · {state}{params}"
            )
        if args.ai_test:
            print("推理测试中（本地模型冷启动可能需要约 2 分钟）……")
            status = ai_client.prepare()
            print(f"生效 provider：{ai_client.last_provider or '无'} · {status.detail}")
            ai_ok = False
            if status.ok:
                ok, text, elapsed = ai_client.ping()
                ai_ok = ok
                print(
                    f"推理测试：{'成功' if ok else '失败'} · {elapsed:.1f}s · "
                    f"返回：{text[:80]!r}"
                )
                if not ok:
                    print("提示：本地模型思考过长，可增大 thinking_budget 或改用 --ai online。")
            unloaded = ai_client.unload_all_local()
            if unloaded:
                print(f"已卸载本地模型：{'、'.join(unloaded)}")
            if not ai_ok:
                return EXIT_CONFIG

    print("\n== 简历 ==")
    print(
        f"{profile.name} · {profile.age} 岁 · {profile.years_experience} 年经验 · "
        f"{profile.target_title} · {profile.location} · 期望 "
        f"{profile.expected_salary_k[0]}-{profile.expected_salary_k[1]}K"
    )

    print("\n== 台账 ==")
    storage = Storage(cfg.storage.db)
    print(f"{cfg.storage.db}")
    print(f"累计状态：{format_counts(storage.status_counts())}")

    print("\n== 浏览器 ==")
    browser_ok, info = _check_browser()
    print(f"{'可用' if browser_ok else '不可用'}：{info}")
    if not browser_ok:
        print("提示：运行 `playwright install chromium`，或设置 JOBBOT_CHROMIUM 指向本机 Chrome/Chromium")

    print("\n== 登录 ==")
    if args.live:
        pc = cfg.platform("boss" if "boss" in cfg.platforms else None)
        adapter = _make_adapter(cfg, profile, pc.name, headless=False)
        try:
            logged_in = adapter.check_login()
            print("已登录" if logged_in else "未登录，请运行 jobbot login")
        finally:
            adapter.close()
        if not logged_in:
            return EXIT_LOGIN
    else:
        print("未检查（加 --live 会打开浏览器读取登录态）")
    return EXIT_OK if browser_ok else EXIT_CONFIG


def cmd_login(args, cfg: Config, profile: Profile) -> int:
    pc = cfg.platform(args.platform)
    if pc.name != "boss":
        print(f"平台 {pc.name} 暂不支持扫码登录。", file=sys.stderr)
        return EXIT_CONFIG
    adapter = _make_adapter(cfg, profile, pc.name, headless=False)
    try:
        if adapter.check_login():
            print("当前已是登录状态，无需重复登录。")
            return EXIT_OK
        adapter.login(args.timeout)
        return EXIT_OK
    finally:
        adapter.close()


def _prepare_ai(cfg: Config, profile: Profile, logger: logging.Logger) -> AIClient:
    client = AIClient(cfg.ai, profile, logger)
    if not cfg.ai.enabled:
        logger.info("AI 已关闭，使用规则评分 + 模板招呼语")
        return client
    status = client.prepare()
    if status.ok:
        logger.info("AI：%s · %s · %s", status.provider, status.model, status.detail)
    else:
        logger.warning("AI 不可用（%s），本次使用规则评分 + 模板招呼语", status.detail)
    return client


def cmd_plan(args, cfg: Config, profile: Profile) -> int:
    logger = logging.getLogger("jobbot")
    platform_name = cfg.platform(args.platform).name
    storage = Storage(cfg.storage.db)
    adapter = _make_adapter(cfg, profile, platform_name, fixture=args.fixture, logger=logger)
    try:
        if not adapter.check_login():
            print("未登录或登录态失效，请先运行：jobbot login", file=sys.stderr)
            return EXIT_LOGIN
        ai_client = _prepare_ai(cfg, profile, logger)
        planner = Planner(cfg, profile, storage, adapter, ai_client, logger)
        plan, path = planner.build_plan(platform_name, args.limit)
        print(format_plan(plan))
        print(f"\n计划文件：{path}")
        if not plan.items:
            print("没有入选职位：可检查关键词/城市/最低分，或查看上面的跳过原因。")
        return EXIT_OK
    finally:
        adapter.close()


def cmd_apply(args, cfg: Config, profile: Profile) -> int:
    logger = logging.getLogger("jobbot")
    if not args.yes and not sys.stdin.isatty():
        print("非交互环境必须显式加 --yes 才会发送消息。", file=sys.stderr)
        return EXIT_CONFIG
    plan_path = _resolve_plan_path(cfg, args.plan)
    plan = load_plan_file(plan_path)
    pc = cfg.platform(plan.platform)
    storage = Storage(cfg.storage.db)
    adapter = _make_adapter(cfg, profile, pc.name, logger=logger)
    try:
        if not adapter.check_login():
            print("未登录或登录态失效，请先运行：jobbot login", file=sys.stderr)
            return EXIT_LOGIN
        return _apply_plan(cfg, storage, adapter, plan, plan_path, args.yes)
    finally:
        adapter.close()


def cmd_run(args, cfg: Config, profile: Profile) -> int:
    logger = logging.getLogger("jobbot")
    platform_name = cfg.platform(args.platform).name
    storage = Storage(cfg.storage.db)
    adapter = _make_adapter(cfg, profile, platform_name, fixture=args.fixture, logger=logger)
    try:
        if not adapter.check_login():
            print("未登录或登录态失效，请先运行：jobbot login", file=sys.stderr)
            return EXIT_LOGIN
        ai_client = _prepare_ai(cfg, profile, logger)
        planner = Planner(cfg, profile, storage, adapter, ai_client, logger)
        plan, path = planner.build_plan(platform_name, args.limit)
        print(format_plan(plan))
        print(f"\n计划文件：{path}")
        if not args.yes:
            print("\n未发送（默认只读）。审阅后运行：")
            print(f"  jobbot apply --plan {path} --yes")
            return EXIT_OK
        if not plan.items:
            print("没有可投递条目，结束。")
            return EXIT_OK
        return _apply_plan(cfg, storage, adapter, plan, path, True)
    finally:
        adapter.close()


def cmd_report(args, cfg: Config, profile: Profile) -> int:
    try:
        day = date.fromisoformat(args.date) if args.date else date.today()
    except ValueError:
        print(f"日期格式应为 YYYY-MM-DD：{args.date}", file=sys.stderr)
        return EXIT_CONFIG
    platform = cfg.platform(args.platform).name if args.platform else None
    storage = Storage(cfg.storage.db)
    path, counts = write_daily_report(storage, cfg.storage.reports_dir, day, platform)
    print(f"{day.isoformat()} 台账：{format_counts(counts)}")
    print(f"日报：{path}")
    return EXIT_OK


def cmd_demo(args, cfg: Config, profile: Profile) -> int:
    logger = logging.getLogger("jobbot")
    example = cfg.root / "examples" / "sample_jobs.json"
    if not example.exists():
        print(f"缺少离线示例数据：{example}", file=sys.stderr)
        return EXIT_CONFIG

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    demo_root = cfg.root / "data" / f"demo-{stamp}"
    storage_cfg = replace(
        cfg.storage,
        db=demo_root / "jobbot.sqlite3",
        plans_dir=demo_root / "plans",
        reports_dir=demo_root / "reports",
        debug_dir=demo_root / "debug",
    )
    demo_cfg = replace(
        cfg,
        storage=storage_cfg,
        defaults=replace(cfg.defaults, min_interval_seconds=0.0, max_interval_seconds=0.0),
        ai=replace(cfg.ai, enabled=False),
    )
    pc = demo_cfg.platform("boss" if "boss" in demo_cfg.platforms else None)
    storage = Storage(storage_cfg.db)
    adapter = FixtureAdapter(
        pc, demo_cfg.search, demo_cfg.defaults, profile, logger, jobs_path=example
    )
    try:
        print("== 离线演示（不联网、不登录、不发送任何真实消息）==")
        planner = Planner(demo_cfg, profile, storage, adapter, None, logger)
        plan, path = planner.build_plan(pc.name)
        print(format_plan(plan))
        print(f"\n计划文件：{path}\n")
        if not plan.items:
            print("演示数据没有入选职位，请检查 examples/sample_jobs.json 与最低分设置。")
            return EXIT_CONFIG
        print("== 模拟投递（结果写入演示台账）==")
        code = _apply_plan(demo_cfg, storage, adapter, plan, path, True)
        duplicate_count = sum(
            1 for job in adapter.all_jobs() if storage.is_duplicate(pc.name, job.job_id)[0]
        )
        print(
            f"\n== 去重复核 ==\n"
            f"{duplicate_count}/{len(adapter.all_jobs())} 个职位会被台账拦截，不会重复投递"
        )
        report_path, counts = write_daily_report(
            storage, storage_cfg.reports_dir, date.today(), pc.name
        )
        print(f"今日台账：{format_counts(counts)}")
        print(f"日报：{report_path}")
        print(f"演示目录：{demo_root}")
        return code
    finally:
        adapter.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobbot",
        description="AI 简历投递机器人：搜索职位 → 匹配评分 → 生成招呼语 → 人工确认投递。",
        epilog=(
            "退出码：0 成功；2 配置/参数错误；3 需要登录；4 页面结构/适配器错误；"
            "5 有条目失败；6 触发验证码或风控；130 中断。"
        ),
    )
    parser.add_argument("--config", default="config/config.yaml", help="配置文件路径")
    parser.add_argument("--db", default=None, help="覆盖台账数据库路径")
    parser.add_argument(
        "--ai", default=None, metavar="PROVIDER", help="覆盖 AI provider：local / online / off"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="自检配置、浏览器、登录态")
    p.add_argument("--live", action="store_true", help="打开浏览器检查登录态")
    p.add_argument("--ai-test", action="store_true", help="实际调用一次 AI 并报告延迟")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("login", help="扫码登录并持久化登录态")
    p.add_argument("--platform", default=None)
    p.add_argument("--timeout", type=int, default=180, help="等待扫码秒数")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("plan", help="生成投递计划（不发送）")
    p.add_argument("--platform", default=None)
    p.add_argument("--limit", type=int, default=None, help="本次最多入选数量")
    p.add_argument("--fixture", default=None, help="离线职位 JSON（演示/测试）")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("apply", help="执行计划（默认逐条确认）")
    p.add_argument("--plan", required=True, help="计划文件路径或 run_id")
    p.add_argument("--yes", action="store_true", help="跳过逐条确认，全部发送")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("run", help="plan + apply 一条命令")
    p.add_argument("--platform", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--yes", action="store_true", help="生成计划后直接发送")
    p.add_argument("--fixture", default=None, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="生成当日台账与日报")
    p.add_argument("--date", default=None, help="YYYY-MM-DD，默认今天")
    p.add_argument("--platform", default=None)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("demo", help="离线跑通全流水线（不联网）")
    p.set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        cfg = load_config(args.config)
        if args.db:
            cfg = replace(cfg, storage=replace(cfg.storage, db=Path(args.db).expanduser().resolve()))
        if args.ai:
            target = args.ai.strip()
            if target.lower() == "off":
                cfg = replace(cfg, ai=replace(cfg.ai, enabled=False))
            elif target not in cfg.ai.providers:
                print(
                    f"配置错误：未知 AI provider {target}；"
                    f"可用：{', '.join(cfg.ai.providers)}",
                    file=sys.stderr,
                )
                return EXIT_CONFIG
            else:
                providers = dict(cfg.ai.providers)
                providers[target] = replace(providers[target], enabled=True)
                cfg = replace(
                    cfg,
                    ai=replace(
                        cfg.ai,
                        enabled=True,
                        provider=target,
                        fallback=None,
                        providers=providers,
                    ),
                )
        profile = load_profile(cfg.profile_path)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return EXIT_CONFIG

    try:
        return args.func(args, cfg, profile)
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return EXIT_CONFIG
    except LoginRequired as exc:
        print(f"需要登录：{exc}", file=sys.stderr)
        return EXIT_LOGIN
    except RiskControlDetected as exc:
        print(f"已停机（风控/验证码）：{exc}", file=sys.stderr)
        return EXIT_RISK
    except (SelectorError, AdapterError) as exc:
        print(f"适配器错误：{exc}", file=sys.stderr)
        return EXIT_ADAPTER
