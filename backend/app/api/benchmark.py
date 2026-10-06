"""
回测 API
-------
回测的主接口是 CLI（`scripts/run_benchmark.py`），这里提供等价的 HTTP 入口，
便于将来接前端、也便于在界面运行期间用 curl 触发。

**磁盘是权威来源**：`TaskManager` 是内存单例，重启即失，而一次回测动辄几十分钟。
所以进度写进 `uploads/benchmarks/<run_id>/run.json`，`/status` 先读磁盘，
TaskManager 只作为在途消息的缓存。
"""

import threading
import traceback

from flask import request, jsonify

from . import benchmark_bp
from ..services import benchmark
from ..services.benchmark_driver import RealPipelineDriver
from ..models.task import TaskManager, TaskStatus
from ..utils.logger import get_logger
from ..utils.locale import t, get_locale, set_locale

logger = get_logger('mirofish.api.benchmark')


def _run_public(run_meta: dict) -> dict:
    """裁剪运行元信息，去掉可能过大的字段。"""
    if not run_meta:
        return {}
    return {k: v for k, v in run_meta.items() if k != 'cases'}


@benchmark_bp.route('/cases', methods=['GET'])
def list_cases():
    """列出全部案卷及其校验状态。"""
    try:
        items = []
        for case_id in benchmark.BenchmarkManager.list_case_ids():
            case = benchmark.BenchmarkManager.load(case_id)
            validation = benchmark.read_validation(case)
            items.append({
                'case_id': case.case_id,
                'title': case.title,
                'synthetic': case.synthetic,
                'synthetic_rationale': case.synthetic_rationale,
                'cutoff': case.cutoff,
                'horizon_days': case.horizon_days,
                'validated': bool((validation or {}).get('ok')),
            })
        return jsonify({"success": True, "data": {"cases": items, "count": len(items)}})
    except Exception as e:
        logger.error(f'列出案卷失败: {e}')
        return jsonify({"success": False, "error": str(e)}), 500


@benchmark_bp.route('/cases/<case_id>', methods=['GET'])
def get_case(case_id):
    """案卷详情 + 逐项校验结果。"""
    try:
        case = benchmark.BenchmarkManager.load(case_id)
    except FileNotFoundError:
        return jsonify({"success": False, "error": f'案卷不存在: {case_id}'}), 404
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400

    try:
        validation = benchmark.validate_case(case)
        return jsonify({
            "success": True,
            "data": {
                "case": case.to_dict(),
                "validation": validation,
                "case_sha256": benchmark.BenchmarkManager.case_sha256(case),
            },
        })
    except Exception as e:
        logger.error(f'读取案卷失败: {e}')
        return jsonify({"success": False, "error": str(e)}), 500


@benchmark_bp.route('/plan', methods=['POST'])
def plan_run():
    """
    校验 + 成本预估。**不发起任何 LLM 调用。**

    请求体：{"cases": ["..."], "repeats": 1, "max_rounds": 10, "assume_entities": 20}

    注意：第三/五阶段的调用量取决于实体数，而实体数只有建完图谱才知道，
    所以这里用 `assume_entities` 参数化，并如实标注它是假设值。
    """
    try:
        from ..services.cost_estimator import estimate_calls
        from ..services.text_processor import TextProcessor

        data = request.get_json(silent=True) or {}
        case_ids = data.get('cases') or benchmark.BenchmarkManager.list_case_ids()
        repeats = max(int(data.get('repeats') or 1), 1)
        max_rounds = data.get('max_rounds')

        plan = []
        blocked = []
        grand_total = 0
        for case_id in case_ids:
            case = benchmark.BenchmarkManager.load(case_id)
            validation = benchmark.validate_case(case)
            if not validation['ok']:
                blocked.append(case_id)

            text = case.corpus_text()
            chunks = len(TextProcessor.split_text(text, chunk_size=500, overlap=50)) if text.strip() else 0
            entities = int(data.get('assume_entities')
                           or case.assumptions.get('assumed_entities') or 20)

            rounds = int(max_rounds or 0)
            if not rounds:
                hours = int(case.assumptions.get('assumed_hours') or 72)
                minutes = int(case.assumptions.get('minutes_per_round') or 60)
                rounds = max(int(hours * 60 / minutes), 1)

            estimate = estimate_calls(
                chunk_count=chunks, entity_count=entities, total_rounds=rounds,
                platform_count=2,
                section_count=int(case.assumptions.get('assumed_sections') or 4),
            )
            total = estimate['total'] * repeats
            grand_total += total

            plan.append({
                'case_id': case_id,
                'title': case.title,
                'synthetic': case.synthetic,
                'validated': validation['ok'],
                'validation_failures': [
                    name for name, check in validation['checks'].items() if not check['ok']
                ],
                'chunk_count': chunks,
                'assumed_entities': entities,
                'assumed_rounds': rounds,
                'stages': estimate['stages'],
                'estimated_calls': total,
                'upper_bound_stages': estimate['upper_bound_stages'],
            })

        return jsonify({
            "success": True,
            "data": {
                'cases': plan,
                'total_calls': grand_total,
                'repeats': repeats,
                'blocked_cases': blocked,
                'note': '模拟阶段是上界（假设每个 Agent 每轮都行动）；'
                        '实体数是假设值，真实值只有建完图谱才知道',
            },
        })
    except Exception as e:
        logger.error(f'回测预估失败: {e}')
        return jsonify({"success": False, "error": str(e),
                        "traceback": traceback.format_exc()}), 500


@benchmark_bp.route('/run', methods=['POST'])
def start_run():
    """
    启动一次回测（后台任务）。

    请求体：
        {
          "cases": ["..."], "repeats": 1, "max_rounds": 10,
          "budget_calls": 5000,          // 必填，且必须 > 0
          "allow_unvalidated": false
        }
    """
    try:
        data = request.get_json(silent=True) or {}

        budget_calls = int(data.get('budget_calls') or 0)
        if budget_calls <= 0:
            return jsonify({
                "success": False,
                "error": '必须指定 budget_calls（大于 0）。回测会产生真实费用，'
                         '不接受无预算上限的运行；想先估算请用 /api/benchmark/plan。',
            }), 400

        case_ids = data.get('cases') or benchmark.BenchmarkManager.list_case_ids()
        if not case_ids:
            return jsonify({"success": False, "error": '没有可用的案卷'}), 400

        # 防泄漏闸门
        blocked = []
        for case_id in case_ids:
            case = benchmark.BenchmarkManager.load(case_id)
            if not benchmark.validate_case(case)['ok']:
                blocked.append(case_id)
        if blocked and not data.get('allow_unvalidated'):
            return jsonify({
                "success": False,
                "error": f'以下案卷未通过防泄漏校验，拒绝执行: {blocked}',
            }), 400

        repeats = max(int(data.get('repeats') or 1), 1)
        max_rounds = data.get('max_rounds')

        store = benchmark.BenchmarkRunStore.create()
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type='benchmark_run',
            metadata={'run_id': store.run_id, 'cases': case_ids, 'repeats': repeats},
        )

        # 后台线程必须自己 set_locale（locale 是线程局域的）
        current_locale = get_locale()

        def run_worker():
            set_locale(current_locale)
            try:
                task_manager.update_task(task_id, status=TaskStatus.PROCESSING,
                                         progress=0, message=t('api.benchmarkStarted'))

                def progress(case_index, total, case_id, repeat, stage, pct, message):
                    overall = int(100 * (case_index + (pct or 0) / 100) / max(total, 1))
                    task_manager.update_task(
                        task_id, progress=min(overall, 99),
                        message=f'[{case_id} r{repeat + 1}] {stage}: {message}',
                        progress_detail={
                            'case_index': case_index, 'total_cases': total,
                            'case_id': case_id, 'repeat': repeat, 'stage': stage,
                        },
                    )

                result = benchmark.run_all(
                    run_store=store,
                    case_ids=case_ids,
                    driver_factory=lambda case, repeat_index: RealPipelineDriver(),
                    repeats=repeats,
                    max_rounds=max_rounds,
                    budget_calls=budget_calls,
                    progress=progress,
                    cancel=store.cancel_requested,
                )
                task_manager.complete_task(task_id, result={
                    'run_id': result['run_id'],
                    'state': result['state'],
                    'scorecard_path': store.scorecard_file,
                })
            except Exception as e:  # noqa: BLE001
                logger.error(f'回测运行失败: {e}')
                task_manager.fail_task(task_id, str(e))

        threading.Thread(target=run_worker, daemon=True).start()

        return jsonify({
            "success": True,
            "data": {
                'run_id': store.run_id,
                'task_id': task_id,
                'status': 'running',
                'blocked_cases': blocked,
            },
        })
    except Exception as e:
        logger.error(f'启动回测失败: {e}')
        return jsonify({"success": False, "error": str(e),
                        "traceback": traceback.format_exc()}), 500


@benchmark_bp.route('/runs', methods=['GET'])
def list_runs():
    """列出已落盘的回测运行。"""
    try:
        runs = []
        for run_id in benchmark.BenchmarkRunStore.list_runs():
            store = benchmark.BenchmarkRunStore(run_id)
            meta = store.read_run() or {}
            runs.append({
                'run_id': run_id,
                'state': meta.get('state'),
                'started_at': meta.get('started_at'),
                'completed_at': meta.get('completed_at'),
                'config': meta.get('config'),
            })
        return jsonify({"success": True, "data": {"runs": runs, "count": len(runs)}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@benchmark_bp.route('/runs/<run_id>', methods=['GET'])
def get_run(run_id):
    """读取一次运行的 summary / scorecard / stability。"""
    try:
        store = benchmark.BenchmarkRunStore(run_id)
        if not store.read_run():
            return jsonify({"success": False, "error": f'运行不存在: {run_id}'}), 404
        return jsonify({
            "success": True,
            "data": {
                'run': store.read_run(),
                'summary': store._read_json(store.summary_file),
                'scorecard': store._read_json(store.scorecard_file),
                'stability': store._read_json(store.stability_file),
            },
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@benchmark_bp.route('/runs/<run_id>/status', methods=['GET'])
def get_run_status(run_id):
    """进度查询：磁盘优先，TaskManager 只补充在途消息。"""
    try:
        store = benchmark.BenchmarkRunStore(run_id)
        meta = store.read_run()
        if not meta:
            return jsonify({"success": False, "error": f'运行不存在: {run_id}'}), 404
        return jsonify({"success": True, "data": {
            'run_id': run_id,
            'state': meta.get('state'),
            'progress': meta.get('progress'),
            'config': meta.get('config'),
            'error': meta.get('error'),
        }})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@benchmark_bp.route('/runs/<run_id>/cancel', methods=['POST'])
def cancel_run(run_id):
    """请求取消。协作式：编排在每个阶段之间检查这个标记。"""
    try:
        store = benchmark.BenchmarkRunStore(run_id)
        if not store.read_run():
            return jsonify({"success": False, "error": f'运行不存在: {run_id}'}), 404
        store.request_cancel()
        return jsonify({"success": True, "data": {'run_id': run_id, 'cancel_requested': True}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
