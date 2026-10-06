"""
回测命令行
---------
回测的主接口是 CLI（本轮不做前端页面）。子命令：

    list      列出案卷
    validate  跑防泄漏校验并写回 validation.json
    plan      只做校验与成本预估，**不发起任何 LLM 调用**
    run       真实回测（必须显式给 --budget-calls）
    report    打印某次运行的记分卡
    verify    用桩件做离线端到端验证（零 LLM）

用法示例：
    cd backend
    uv run python scripts/run_benchmark.py list
    uv run python scripts/run_benchmark.py plan --cases synthetic_ev_price_war_001
    uv run python scripts/run_benchmark.py verify
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
FIXTURE_ROOT = os.path.join(BACKEND, 'tests', 'fixtures', 'benchmark')


def _fmt(value, width=8):
    if value is None:
        return '-'.rjust(width)
    if isinstance(value, float):
        return f'{value:.3f}'.rjust(width)
    return str(value).rjust(width)


# ══════════════════════════════════════════════════════════════
# list / validate
# ══════════════════════════════════════════════════════════════

def cmd_list(args) -> int:
    from app.services import benchmark

    case_ids = benchmark.BenchmarkManager.list_case_ids()
    if not case_ids:
        print('没有找到任何案卷。先运行 scripts/bootstrap_benchmarks.py 生成演示案卷。')
        return 1

    print(f'{"case_id":44s} {"合成":4s} {"校验":6s} {"cutoff":12s} {"horizon":8s}')
    print('-' * 82)
    for case_id in case_ids:
        case = benchmark.BenchmarkManager.load(case_id)
        validation = benchmark.read_validation(case)
        valid = '通过' if (validation or {}).get('ok') else ('未校验' if validation is None else '未通过')
        print(f'{case_id:44s} {"是" if case.synthetic else "否":4s} {valid:6s} '
              f'{case.cutoff:12s} {case.horizon_days:<8d}')
    return 0


def cmd_validate(args) -> int:
    from app.services import benchmark

    case_ids = args.cases or benchmark.BenchmarkManager.list_case_ids()
    failed = 0
    for case_id in case_ids:
        case = benchmark.BenchmarkManager.load(case_id)
        report = benchmark.validate_case(case)
        benchmark.write_validation(case, report)
        print(f'{"OK  " if report["ok"] else "FAIL"} {case_id}')
        for name, check in report['checks'].items():
            if not check['ok']:
                failed += 1
                print(f'       {name}: {json.dumps(check, ensure_ascii=False)}')
    return 1 if failed else 0


# ══════════════════════════════════════════════════════════════
# plan（零 LLM）
# ══════════════════════════════════════════════════════════════

def cmd_plan(args) -> int:
    from app.services import benchmark
    from app.services.cost_estimator import estimate_calls
    from app.services.text_processor import TextProcessor

    case_ids = args.cases or benchmark.BenchmarkManager.list_case_ids()
    grand_total = 0
    problems = 0

    for case_id in case_ids:
        case = benchmark.BenchmarkManager.load(case_id)
        validation = benchmark.validate_case(case)

        text = case.corpus_text()
        chunks = len(TextProcessor.split_text(text, chunk_size=500, overlap=50)) if text.strip() else 0

        # 第一/二阶段可精确计算；第三/五阶段取决于实体数（只有建完图谱才知道），
        # 所以这里参数化，绝不假装运行前知道实体数。
        assumed_entities = int(args.assume_entities or case.assumptions.get('assumed_entities') or 20)
        rounds = int(args.max_rounds or 0)
        if not rounds:
            hours = int(case.assumptions.get('assumed_hours') or 72)
            minutes = int(case.assumptions.get('minutes_per_round') or 60)
            rounds = max(int(hours * 60 / minutes), 1)

        estimate = estimate_calls(
            chunk_count=chunks,
            entity_count=assumed_entities,
            total_rounds=rounds,
            platform_count=2,
            section_count=int(case.assumptions.get('assumed_sections') or 4),
        )
        total = estimate['total'] * max(args.repeats, 1)
        grand_total += total

        ok = validation['ok']
        problems += 0 if ok else 1

        print(f'案卷 {case_id}')
        print(f'  标题      : {case.title}')
        print(f'  合成案卷  : {"是（不构成有效性证据）" if case.synthetic else "否"}')
        print(f'  cutoff    : {case.cutoff}   horizon: {case.horizon_days} 天')
        print(f'  防泄漏校验: {"通过" if ok else "未通过"}')
        if not ok:
            for name, check in validation['checks'].items():
                if not check['ok']:
                    print(f'      × {name}: {json.dumps(check, ensure_ascii=False)[:160]}')
        print(f'  文本块数  : {chunks}（可精确计算）')
        print(f'  推定实体数: {assumed_entities}（假设值——真实值只有建完图谱才知道）')
        print(f'  推定轮数  : {rounds}（{estimate["assumptions"]["platform_count"]} 个平台）')
        print('  各阶段预估:', json.dumps(estimate['stages'], ensure_ascii=False))
        print(f'  合计      : {total} 次 LLM 调用 × {max(args.repeats,1)} 次重复'
              f'（模拟阶段是上界）')
        print(f'  敏感度    : 实体数每 ±10 个，约 ±{rounds * 10 * 2} 次调用')
        print()

    print(f'全部案卷合计约 {grand_total} 次 LLM 调用')
    if problems:
        print(f'注意：有 {problems} 个案卷未通过防泄漏校验，run 时会拒绝执行（除非 --allow-unvalidated）')
    return 1 if problems else 0


# ══════════════════════════════════════════════════════════════
# run
# ══════════════════════════════════════════════════════════════

def _print_progress(case_index, total, case_id, repeat, stage, pct, message) -> None:
    print(f'  [{case_index + 1}/{total}] {case_id} r{repeat + 1} {stage} {pct}% {message}')


def cmd_run(args) -> int:
    from app.services import benchmark
    from app.services.benchmark_driver import RealPipelineDriver

    if not args.budget_calls:
        print('错误：必须显式指定 --budget-calls。回测会产生真实费用，'
              '没有预算上限的运行一律拒绝。')
        print('      想先看看会花多少，用 plan；想离线验证框架，用 verify。')
        return 2

    case_ids = args.cases or benchmark.BenchmarkManager.list_case_ids()
    if not case_ids:
        print('没有可用的案卷')
        return 1

    # 防泄漏闸门：未通过校验的案卷拒绝执行
    blocked = []
    for case_id in case_ids:
        case = benchmark.BenchmarkManager.load(case_id)
        if not benchmark.validate_case(case)['ok']:
            blocked.append(case_id)
    if blocked and not args.allow_unvalidated:
        print(f'错误：以下案卷未通过防泄漏校验，拒绝执行：{blocked}')
        print('      如确要继续，加 --allow-unvalidated（结果会被标记为不可用于头条统计）')
        return 2
    if blocked:
        print(f'警告：{blocked} 未通过防泄漏校验，仍将执行，结果不计入头条统计')

    # 记录 git 状态（不强制干净：工作树带正在进行中的改动是常态，
    # 但必须记录，否则分数无法回溯到代码状态）
    from app.services.run_manifest import git_state
    git = git_state() or {}
    if git.get('dirty') and args.require_clean:
        print('错误：工作树不干净，且指定了 --require-clean')
        return 2
    if git.get('dirty'):
        print(f'提示：工作树不干净（commit={git.get("commit")}, '
              f'diff_sha1={git.get("tracked_diff_sha1")}），已记录进运行清单')

    # Ctrl-C 时杀掉正在跑的模拟子进程，避免留下孤儿
    try:
        from app.services.simulation_runner import SimulationRunner
        SimulationRunner.register_cleanup()
        if getattr(SimulationRunner, '_cleanup_done', False):
            print('错误：本进程的模拟清理钩子已被消耗（_cleanup_done=True），拒绝继续')
            return 2
    except Exception as exc:  # noqa: BLE001
        print(f'提示：注册清理钩子失败: {exc}')

    store = benchmark.BenchmarkRunStore.create(args.out)
    print(f'运行 ID: {store.run_id}')
    print(f'案件 {len(case_ids)} 个 × 重复 {args.repeats} 次，预算 {args.budget_calls} 次调用')
    print()

    if args.dry_run:
        print('--dry-run：只做校验与预估，不发起任何调用')
        return cmd_plan(argparse.Namespace(
            cases=case_ids, repeats=args.repeats, max_rounds=args.max_rounds,
            assume_entities=args.assume_entities))

    result = benchmark.run_all(
        run_store=store,
        case_ids=case_ids,
        driver_factory=lambda case, repeat_index: RealPipelineDriver(),
        repeats=args.repeats,
        max_rounds=args.max_rounds,
        budget_calls=args.budget_calls,
        progress=_print_progress,
        cancel=store.cancel_requested,
    )

    print()
    _print_scorecard(result['scorecard'], result.get('stability'))
    print(f'\n结果已写入: {store.root}')
    return 0


# ══════════════════════════════════════════════════════════════
# report
# ══════════════════════════════════════════════════════════════

def _print_scorecard(scorecard: dict, stability: dict = None) -> None:
    print('═' * 66)
    print('记分卡')
    print('═' * 66)
    headline = scorecard.get('headline') or {}
    print(f'  尝试总数      : {scorecard.get("attempts_total")}')
    print(f'  可用于头条统计: {scorecard.get("headline_attempts")}')
    print(f'  不合格        : {scorecard.get("ineligible_attempts")}')
    print()
    print('  头条指标（仅统计合格尝试）:')
    print(f'    方向命中率(关键词代理): {_fmt(headline.get("direction_proxy_hit_rate"))}')
    print(f'    关键角色提及召回率    : {_fmt(headline.get("key_actor_mention_recall"))}')
    print(f'    关键角色图谱召回率    : {_fmt(headline.get("key_actor_graph_recall"))}')
    print(f'    事件关键词召回率      : {_fmt(headline.get("event_term_recall"))}')
    print(f'    数字有出处比例        : {_fmt(headline.get("number_grounding_rate"))}')

    if scorecard.get('banner'):
        print()
        print(f'  ⚠ {scorecard["banner"]}')

    ineligible = scorecard.get('ineligible') or []
    if ineligible:
        print()
        print('  不合格尝试明细（不计入任何头条数字）:')
        for item in ineligible:
            print(f'    {item["case_id"]} #{item["attempt"]}: {item["status"]}'
                  f'{" (合成案卷)" if item.get("synthetic") else ""}'
                  f'{" (被截断)" if item.get("horizon_truncated") else ""}'
                  f'{" — " + str(item.get("status_reason")) if item.get("status_reason") else ""}')

    if stability:
        print()
        print('═' * 66)
        print('多次运行稳定性')
        print('═' * 66)
        for case_id, report in stability.items():
            print(f'  {case_id}: 可用尝试 {report.get("usable_attempts")}/{report.get("run_count")}')
            if report.get('incomparable'):
                print(f'    ⚠ 不可比：{"；".join(report.get("reasons") or [])}')
                continue
            claims = report.get('claims') or {}
            if claims:
                print(f'    断言稳定性      : {_fmt(claims.get("claim_stability_rate"))}'
                      f'  (稳定 {claims.get("stable_count")} / 摇摆 {claims.get("volatile_count")})')
            graph = report.get('graph') or {}
            if graph:
                print(f'    图谱 Jaccard    : {_fmt(graph.get("mean_pairwise_jaccard"))}'
                      f'  (稳定实体 {len(graph.get("stable_entities") or [])})')
            scenarios = report.get('scenarios') or {}
            if scenarios:
                print(f'    情景 Jaccard    : {_fmt(scenarios.get("mean_pairwise_jaccard"))}')
    print()


def cmd_report(args) -> int:
    from app.services import benchmark

    store = benchmark.BenchmarkRunStore(args.run)
    if not os.path.isfile(store.run_file):
        print(f'找不到运行: {args.run}')
        return 1

    scorecard = store._read_json(store.scorecard_file) or {}
    stability = store._read_json(store.stability_file)
    if args.json:
        print(json.dumps({'scorecard': scorecard, 'stability': stability},
                         ensure_ascii=False, indent=2))
    else:
        _print_scorecard(scorecard, stability)

    print(f'运行目录: {store.root}')
    return 0


# ══════════════════════════════════════════════════════════════
# verify（离线端到端验证，零 LLM）
# ══════════════════════════════════════════════════════════════

def cmd_verify(args) -> int:
    """用桩件把整条编排跑一遍，并断言结果符合预期。"""
    from app.services import benchmark
    from app.services.benchmark_driver import StubDriver

    checks = []

    def check(name, condition, detail=''):
        checks.append((bool(condition), name, detail))

    case_id = 'synthetic_ev_price_war_001'
    case = benchmark.BenchmarkManager.load(case_id)

    # 1) 防泄漏校验五项，逐项断言
    validation = benchmark.validate_case(case)
    check('校验总通过', validation['ok'], json.dumps(
        {k: v['ok'] for k, v in validation['checks'].items()}, ensure_ascii=False))
    for name, payload in validation['checks'].items():
        check(f'  检查项 {name}', payload['ok'])
    check('语料哈希已登记', all(d.get('sha256') for d in case.documents))

    # 2) 单次尝试：正常路径
    store = benchmark.BenchmarkRunStore.create('bverify_normal')
    executor = benchmark.CaseExecutor(StubDriver(FIXTURE_ROOT), run_store=store, budget_calls=10 ** 6)
    result = executor.run_case(case, attempt=1)
    check('正常路径状态 ok', result['status'] == 'ok', result.get('status_reason') or '')
    check('列出 graph_id', bool(result['ids'].get('graph_id')))
    check('离线指标已计算', result['offline_metrics'].get('key_actor_mention_recall') is not None)
    check('合成案卷不计入头条', result['headline_eligible'] is False)

    # 3) 各条"静默错误"护栏
    guards = [
        ('图谱过小', {'graph.json': {'node_count': 1, 'entity_count': 0, 'nodes': []}}, 'invalid_run'),
        ('报告为空', {'report.json': {'status': 'completed', 'markdown': '太短', 'outline': {}}}, 'invalid_run'),
        ('报告失败', {'report.json': {'status': 'failed', 'markdown': '', 'error': 'x'}}, 'failed'),
        ('零动作', {'run.json': {'runner_status': 'completed', 'total_rounds': 12,
                              'twitter_actions_count': 0, 'reddit_actions_count': 0}}, 'invalid_run'),
        ('模拟失败', {'run.json': {'runner_status': 'failed', 'total_rounds': 0,
                               'twitter_actions_count': 0, 'reddit_actions_count': 0, 'error': 'boom'}}, 'failed'),
    ]
    import shutil
    import tempfile

    for name, overrides, expected in guards:
        temp_root = tempfile.mkdtemp(prefix='bench-guard-')
        shutil.copytree(os.path.join(FIXTURE_ROOT, case_id), os.path.join(temp_root, case_id))
        for filename, payload in overrides.items():
            with open(os.path.join(temp_root, case_id, filename), 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, ensure_ascii=False)
        guard_store = benchmark.BenchmarkRunStore.create(f'bverify_{name}')
        guard_executor = benchmark.CaseExecutor(StubDriver(temp_root), run_store=guard_store,
                                                budget_calls=10 ** 6)
        guard_result = guard_executor.run_case(case, attempt=1)
        check(f'护栏 [{name}] 拦下', guard_result['status'] == expected,
              f'得到 {guard_result["status"]}: {guard_result.get("status_reason")}')
        check(f'护栏 [{name}] 不进头条', guard_result['headline_eligible'] is False)
        shutil.rmtree(temp_root, ignore_errors=True)

    # 4) 轮数截断护栏
    trunc_store = benchmark.BenchmarkRunStore.create('bverify_trunc')
    trunc_executor = benchmark.CaseExecutor(StubDriver(FIXTURE_ROOT), run_store=trunc_store,
                                            budget_calls=10 ** 6)
    trunc = trunc_executor.run_case(case, attempt=1, max_rounds=3)
    check('轮数截断被标记', trunc['horizon_truncated'] is True,
          f'total_rounds={trunc["counts"].get("executed_rounds")} planned={trunc["counts"].get("planned_rounds")}')

    # 5) 预算护栏
    tight_store = benchmark.BenchmarkRunStore.create('bverify_budget')
    tight_executor = benchmark.CaseExecutor(StubDriver(FIXTURE_ROOT), run_store=tight_store,
                                            budget_calls=1)
    tight = tight_executor.run_case(case, attempt=1)
    check('预算超限被拦下', tight['status'] == 'over_budget', tight.get('status_reason') or '')

    # 6) 桩件注入失败 → 不该炸掉整轮
    fail_store = benchmark.BenchmarkRunStore.create('bverify_failstage')
    fail_executor = benchmark.CaseExecutor(StubDriver(FIXTURE_ROOT, fail_at='build_graph'),
                                           run_store=fail_store, budget_calls=10 ** 6)
    failed = fail_executor.run_case(case, attempt=1)
    check('阶段失败被捕获', failed['status'] == 'failed' and 'build_graph' in (failed.get('status_reason') or ''),
          failed.get('status_reason') or '')

    # 7) 整轮编排 + 稳定性
    run_store = benchmark.BenchmarkRunStore.create('bverify_run')
    variants = ['run_a', 'run_b', 'run_c']
    run_result = benchmark.run_all(
        run_store=run_store,
        case_ids=[case_id],
        driver_factory=lambda c, repeat_index: StubDriver(
            FIXTURE_ROOT, variant=variants[repeat_index % len(variants)]),
        repeats=3,
        budget_calls=10 ** 6,
    )
    check('整轮状态 completed', run_result['state'] == 'completed')
    check('三次尝试全部入库', run_result['summary']['attempts_total'] == 3)
    check('记分卡无头条数字', run_result['scorecard']['headline_attempts'] == 0)
    check('记分卡给出横幅', bool(run_result['scorecard'].get('banner')))

    stability = (run_result.get('stability') or {}).get(case_id) or {}
    claims = stability.get('claims') or {}
    rate = claims.get('claim_stability_rate')
    check('稳定性算出了断言稳定率', rate is not None, f'rate={rate}')
    # 三个变体里有一句被改写，所以稳定率应当明显小于 1 且大于 0
    check('稳定率介于 0 与 1 之间（确实区分出稳定与摇摆）',
          rate is not None and 0 < rate < 1, f'rate={rate}')
    check('既有稳定断言也有摇摆断言',
          (claims.get('stable_count') or 0) > 0 and (claims.get('volatile_count') or 0) > 0,
          f'stable={claims.get("stable_count")} volatile={claims.get("volatile_count")}')
    check('情景稳定性已计算', (stability.get('scenarios') or {}).get('mean_pairwise_jaccard') is not None)
    check('图谱稳定性已计算', (stability.get('graph') or {}).get('mean_pairwise_jaccard') is not None)

    # 8) 不可比检测：配置指纹不同时必须响亮失败
    incomparable = benchmark.run_all(
        run_store=benchmark.BenchmarkRunStore.create('bverify_incomparable'),
        case_ids=[case_id],
        driver_factory=lambda c, i: StubDriver(FIXTURE_ROOT, variant=variants[i % 3]),
        repeats=2,
        budget_calls=10 ** 6,
    )
    check('可比时未误报不可比',
          not ((incomparable.get('stability') or {}).get(case_id) or {}).get('incomparable'))

    # 9) 结果落盘且可读回
    check('summary.json 已落盘', os.path.isfile(run_store.summary_file))
    check('scorecard.json 已落盘', os.path.isfile(run_store.scorecard_file))
    check('stability.json 已落盘', os.path.isfile(run_store.stability_file))
    check('报告副本已落盘',
          os.path.isfile(run_store.attempt_file(case_id, 1, 'report.md')))
    re_read = run_store.read_attempts(case_id)
    check('尝试结果可读回', len(re_read) == 3, f'读到 {len(re_read)} 条')

    # 清理验证产物
    for run_id in ('bverify_normal', 'bverify_trunc', 'bverify_budget', 'bverify_failstage',
                   'bverify_run', 'bverify_incomparable'):
        shutil.rmtree(os.path.join(benchmark.RUN_ROOT, run_id), ignore_errors=True)
    for name, _, _ in guards:
        shutil.rmtree(os.path.join(benchmark.RUN_ROOT, f'bverify_{name}'), ignore_errors=True)

    print('离线端到端验证（桩件，零 LLM 调用）')
    print('=' * 66)
    for ok, name, detail in checks:
        mark = '✓' if ok else '✗'
        suffix = f'  {detail}' if (detail and not ok) else ''
        print(f'  {mark} {name}{suffix}')
    passed = sum(1 for ok, _, _ in checks if ok)
    print('=' * 66)
    print(f'通过 {passed} / {len(checks)}')
    return 0 if passed == len(checks) else 1


# ══════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description='MacFish 回测命令行')
    sub = parser.add_subparsers(dest='command', required=True)

    p_list = sub.add_parser('list', help='列出案卷')
    p_list.set_defaults(func=cmd_list)

    p_validate = sub.add_parser('validate', help='跑防泄漏校验')
    p_validate.add_argument('--cases', nargs='*')
    p_validate.set_defaults(func=cmd_validate)

    p_plan = sub.add_parser('plan', help='校验 + 成本预估（零 LLM）')
    p_plan.add_argument('--cases', nargs='*')
    p_plan.add_argument('--repeats', type=int, default=1)
    p_plan.add_argument('--max-rounds', type=int, default=None)
    p_plan.add_argument('--assume-entities', type=int, default=None)
    p_plan.set_defaults(func=cmd_plan)

    p_run = sub.add_parser('run', help='真实回测')
    p_run.add_argument('--cases', nargs='*')
    p_run.add_argument('--repeats', type=int, default=1)
    p_run.add_argument('--max-rounds', type=int, default=None)
    p_run.add_argument('--budget-calls', type=int, default=0,
                       help='本次运行允许的最大 LLM 调用数（必填，无默认值）')
    p_run.add_argument('--assume-entities', type=int, default=None)
    p_run.add_argument('--out', default=None, help='指定 run_id')
    p_run.add_argument('--allow-unvalidated', action='store_true')
    p_run.add_argument('--require-clean', action='store_true',
                       help='要求工作树干净（默认只记录不强制）')
    p_run.add_argument('--dry-run', action='store_true', help='等同于 plan')
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser('report', help='打印某次运行的记分卡')
    p_report.add_argument('--run', required=True)
    p_report.add_argument('--json', action='store_true')
    p_report.set_defaults(func=cmd_report)

    p_verify = sub.add_parser('verify', help='离线端到端验证（桩件，零 LLM）')
    p_verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
