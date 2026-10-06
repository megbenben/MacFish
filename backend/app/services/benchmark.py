"""
回测框架：案卷加载、防泄漏校验、运行存储、编排执行
--------------------------------------------------
存在的理由：MacFish 此前完全没有校准机制——没有任何手段判断一次预测对不对，
所以也无法判断任何改动是进步还是退步。这个模块提供那个基线。

**案卷与结果刻意分开存放：**
- 案卷（手工编写、应当进版本控制）：`backend/benchmarks/cases/<case_id>/`
- 运行结果（运行时产物、应被忽略）：`backend/uploads/benchmarks/<run_id>/`
`backend/uploads/` 整个被 gitignore，所以手工 fixture 不能放那里。

**关于合成案卷**：仓库自带的两个演示案卷带 `synthetic: true` 标记。它们是结构合法的
fixture，用来验证框架本身能跑通，**永远不构成有效性证据**。所有评分都带
`headline_eligible` 字段（须为 ok + validated + 非合成 + 未被截断），聚合只统计它。
"""

import hashlib
import json
import logging
import os
import re
import shutil
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..config import Config

logger = logging.getLogger(__name__)

CASE_SCHEMA_VERSION = 1
RUN_SCHEMA_VERSION = 1

#: 案卷根目录（进版本控制）
CASE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../benchmarks'))
#: 运行结果根目录（运行时产物）
RUN_ROOT = os.path.join(Config.UPLOAD_FOLDER, 'benchmarks')

#: 防泄漏：语料里允许出现的最晚日期必须早于 cutoff
_ISO_DATE_RE = re.compile(r'\b(19|20)\d{2}-\d{2}-\d{2}\b')
#: 数字泄漏扫描时抽取的数量型 token
_NUMBER_RE = re.compile(r'\d+(?:\.\d+)?')


# ══════════════════════════════════════════════════════════════
# 案卷
# ══════════════════════════════════════════════════════════════

@dataclass
class BenchmarkCase:
    """一个回测案卷。"""
    case_id: str
    title: str
    simulation_requirement: str
    synthetic: bool = False
    synthetic_rationale: str = ""
    locale: str = "zh"
    domain: str = ""
    seed: Dict[str, Any] = field(default_factory=dict)
    expectations: Dict[str, Any] = field(default_factory=dict)
    assumptions: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    ground_truth: Dict[str, Any] = field(default_factory=dict)
    case_dir: str = ""

    # -- 便利取值 --
    @property
    def documents(self) -> List[Dict[str, Any]]:
        return list(self.seed.get('documents') or [])

    @property
    def cutoff(self) -> str:
        return str(self.seed.get('cutoff') or '')

    @property
    def horizon_days(self) -> int:
        try:
            return int(self.seed.get('horizon_days') or 0)
        except (TypeError, ValueError):
            return 0

    def corpus_paths(self) -> List[str]:
        return [os.path.join(self.case_dir, d.get('path', '')) for d in self.documents]

    def corpus_text(self) -> str:
        parts = []
        for path in self.corpus_paths():
            try:
                with open(path, 'r', encoding='utf-8') as handle:
                    parts.append(handle.read())
            except OSError:
                continue
        return '\n\n'.join(parts)

    def min_nodes(self) -> int:
        return int(self.expectations.get('min_nodes') or 1)

    def min_entities(self) -> int:
        return int(self.expectations.get('min_entities') or 1)

    def min_actions(self) -> int:
        return int(self.expectations.get('min_actions') or 1)

    def min_report_chars(self) -> int:
        return int(self.expectations.get('min_report_chars') or 1)

    def min_sections(self) -> int:
        return int(self.expectations.get('report_min_sections') or 1)

    def key_actors(self) -> List[Dict[str, Any]]:
        return list(self.expectations.get('key_actors') or [])

    def questions(self) -> List[Dict[str, Any]]:
        return list(self.expectations.get('questions') or [])

    def to_dict(self) -> Dict[str, Any]:
        return {
            'case_id': self.case_id,
            'title': self.title,
            'simulation_requirement': self.simulation_requirement,
            'synthetic': self.synthetic,
            'synthetic_rationale': self.synthetic_rationale,
            'locale': self.locale,
            'domain': self.domain,
            'seed': self.seed,
            'expectations': self.expectations,
            'assumptions': self.assumptions,
            'provenance': self.provenance,
        }


class BenchmarkManager:
    """案卷的发现与加载。"""

    @staticmethod
    def cases_root() -> str:
        return CASE_ROOT

    @staticmethod
    def case_dir(case_id: str) -> str:
        return os.path.join(CASE_ROOT, 'cases', case_id)

    @classmethod
    def list_case_ids(cls) -> List[str]:
        registry = os.path.join(CASE_ROOT, 'registry.json')
        if os.path.isfile(registry):
            try:
                with open(registry, 'r', encoding='utf-8') as handle:
                    data = json.load(handle)
                ids = [str(c.get('case_id')) for c in (data.get('cases') or []) if c.get('case_id')]
                if ids:
                    return ids
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning('读取案卷登记表失败，回退为扫描目录: %s', exc)

        cases_dir = os.path.join(CASE_ROOT, 'cases')
        if not os.path.isdir(cases_dir):
            return []
        return sorted(
            name for name in os.listdir(cases_dir)
            if os.path.isfile(os.path.join(cases_dir, name, 'case.json'))
        )

    @classmethod
    def load(cls, case_id: str) -> BenchmarkCase:
        """加载案卷。缺必需字段直接报错——回测宁可响亮失败也不要静默用错数据。"""
        directory = cls.case_dir(case_id)
        case_file = os.path.join(directory, 'case.json')
        if not os.path.isfile(case_file):
            raise FileNotFoundError(f'案卷不存在: {case_id}')

        with open(case_file, 'r', encoding='utf-8') as handle:
            data = json.load(handle)

        # case.json 里绝不允许出现 graph_id：那意味着案卷绑定了某次运行的产物，
        # 会让"同一个案卷重跑"变成复用旧图谱。
        if 'graph_id' in json.dumps(data):
            raise ValueError(f'案卷 {case_id} 的 case.json 里不应出现 graph_id')

        case = BenchmarkCase(
            case_id=data.get('case_id') or case_id,
            title=data.get('title') or case_id,
            simulation_requirement=str(data.get('simulation_requirement') or ''),
            synthetic=bool(data.get('synthetic')),
            synthetic_rationale=str(data.get('synthetic_rationale') or ''),
            locale=str(data.get('locale') or 'zh'),
            domain=str(data.get('domain') or ''),
            seed=data.get('seed') or {},
            expectations=data.get('expectations') or {},
            assumptions=data.get('assumptions') or {},
            provenance=data.get('provenance') or {},
            case_dir=directory,
        )

        if not case.simulation_requirement:
            raise ValueError(f'案卷 {case_id} 缺少 simulation_requirement')

        truth_file = os.path.join(directory, 'ground_truth.json')
        if os.path.isfile(truth_file):
            with open(truth_file, 'r', encoding='utf-8') as handle:
                case.ground_truth = json.load(handle)

        return case

    @classmethod
    def case_sha256(cls, case: BenchmarkCase) -> str:
        """案卷内容指纹：让每条结果能指向它所消费的确切 fixture 版本。"""
        digest = hashlib.sha256()
        digest.update(json.dumps(case.to_dict(), sort_keys=True, ensure_ascii=False).encode('utf-8'))
        for path in sorted(case.corpus_paths()):
            digest.update(open(path, 'rb').read() if os.path.isfile(path) else b'')
        digest.update(json.dumps(case.ground_truth, sort_keys=True, ensure_ascii=False).encode('utf-8'))
        return digest.hexdigest()


# ══════════════════════════════════════════════════════════════
# 防泄漏校验
# ══════════════════════════════════════════════════════════════

def _normalize(text: str) -> str:
    """NFKC + 小写 + 空白折叠 + 保留 CJK，用于泄漏扫描与文本比对。"""
    text = unicodedata.normalize('NFKC', text or '').lower()
    text = re.sub(r'[^\w一-鿿]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def sha256_file(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_case(case: BenchmarkCase) -> Dict[str, Any]:
    """
    五项检查，全部确定性、可离线复现。

    存在的意义：回测最容易犯的错是**数据泄漏**——把结果写进了种子材料，
    于是模型"预测"得极准，分数却毫无意义。所以这里把"材料必须早于结果"
    做成可执行的检查，而不是一句约定。
    """
    checks: Dict[str, Any] = {}

    # 1. 哈希完整性：语料是否与登记的一致
    hash_results = []
    for doc in case.documents:
        path = os.path.join(case.case_dir, doc.get('path', ''))
        actual = sha256_file(path)
        expected = doc.get('sha256')
        hash_results.append({
            'path': doc.get('path'),
            'expected': expected,
            'actual': actual,
            'ok': bool(actual) and (expected is None or actual == expected),
        })
    checks['hash_integrity'] = {
        'ok': all(item['ok'] for item in hash_results) and bool(hash_results),
        'documents': hash_results,
    }

    # 2. 日期顺序：材料日 <= corpus_as_of < cutoff <= 结果窗口起点
    doc_dates = [str(d.get('doc_date') or '') for d in case.documents if d.get('doc_date')]
    corpus_as_of = str(case.seed.get('corpus_as_of') or '')
    window = case.seed.get('outcome_window') or {}
    window_start = str(window.get('start') or '')
    window_end = str(window.get('end') or '')

    order_ok = bool(case.cutoff) and bool(corpus_as_of) and corpus_as_of < case.cutoff
    if window_start:
        order_ok = order_ok and case.cutoff <= window_start
    if doc_dates:
        order_ok = order_ok and max(doc_dates) <= corpus_as_of

    checks['date_order'] = {
        'ok': order_ok,
        'latest_doc_date': max(doc_dates) if doc_dates else None,
        'corpus_as_of': corpus_as_of,
        'cutoff': case.cutoff,
        'outcome_window': {'start': window_start, 'end': window_end},
    }

    corpus_raw = case.corpus_text()

    # 3. 时间线扫描：语料里不得出现 cutoff 及以后的日期
    #    （ISO 日期是零填充定长格式，字符串比较即时间比较）
    future_dates = sorted({
        match.group(0) for match in _ISO_DATE_RE.finditer(corpus_raw)
        if case.cutoff and match.group(0) >= case.cutoff
    })
    checks['timeline_leak'] = {'ok': not future_dates, 'future_dates': future_dates}

    corpus_norm = _normalize(corpus_raw)

    # 4. 词泄漏：结果定义词不得出现在语料里
    term_hits = [t for t in (case.ground_truth.get('leakage_terms') or [])
                 if _normalize(t) and _normalize(t) in corpus_norm]
    checks['term_leak'] = {'ok': not term_hits, 'hits': term_hits}

    # 5. 数字泄漏：定义结果的数量级不得出现在语料里
    answer_numbers = {str(n) for n in (case.ground_truth.get('answer_numbers') or [])}
    corpus_numbers = {m.group(0) for m in _NUMBER_RE.finditer(corpus_raw)}
    number_hits = sorted(answer_numbers & corpus_numbers)
    checks['number_leak'] = {'ok': not number_hits, 'hits': number_hits}

    report: Dict[str, Any] = {
        'case_id': case.case_id,
        'schema_version': CASE_SCHEMA_VERSION,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'ok': all(c['ok'] for c in checks.values()),
        'checks': checks,
        'corpus_sha256': hashlib.sha256(corpus_raw.encode('utf-8')).hexdigest(),
    }
    # 自身指纹不参与计算，避免自指
    report['validation_sha256'] = hashlib.sha256(
        json.dumps(report, sort_keys=True, ensure_ascii=False).encode('utf-8')
    ).hexdigest()
    return report


def write_validation(case: BenchmarkCase, report: Dict[str, Any]) -> str:
    path = os.path.join(case.case_dir, 'validation.json')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False, sort_keys=True)
    return path


def read_validation(case: BenchmarkCase) -> Optional[Dict[str, Any]]:
    path = os.path.join(case.case_dir, 'validation.json')
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return None


# ══════════════════════════════════════════════════════════════
# 运行结果存储（磁盘为准）
# ══════════════════════════════════════════════════════════════

class BenchmarkRunStore:
    """
    一次回测运行的落盘存储。

    磁盘是权威来源：TaskManager 是内存单例、重启即失，而回测动辄几十分钟，
    进度不能跟着丢。
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.root = os.path.join(RUN_ROOT, run_id)
        os.makedirs(self.root, exist_ok=True)

    @staticmethod
    def new_run_id() -> str:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        return f'brun_{stamp}_{uuid.uuid4().hex[:4]}'

    @classmethod
    def create(cls, run_id: Optional[str] = None) -> 'BenchmarkRunStore':
        return cls(run_id or cls.new_run_id())

    # -- 路径 --

    def _path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    @property
    def run_file(self) -> str:
        return self._path('run.json')

    @property
    def summary_file(self) -> str:
        return self._path('summary.json')

    @property
    def scorecard_file(self) -> str:
        return self._path('scorecard.json')

    @property
    def stability_file(self) -> str:
        return self._path('stability.json')

    @property
    def cancel_flag(self) -> str:
        return self._path('cancel.flag')

    def attempt_dir(self, case_id: str, attempt: int) -> str:
        directory = self._path('cases', case_id, f'attempt_{attempt:02d}')
        os.makedirs(directory, exist_ok=True)
        return directory

    def attempt_file(self, case_id: str, attempt: int, name: str) -> str:
        return os.path.join(self.attempt_dir(case_id, attempt), name)

    # -- 读写（原子写：临时文件 + os.replace）--

    @staticmethod
    def _write_json(path: str, payload: Any) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            # sort_keys 让相同输入产出字节一致的 JSON，便于 diff 与断言
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, path)

    @staticmethod
    def _read_json(path: str) -> Optional[Any]:
        if not os.path.isfile(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                return json.load(handle)
        except (json.JSONDecodeError, OSError):
            return None

    def write_run(self, payload: Dict[str, Any]) -> None:
        self._write_json(self.run_file, payload)

    def read_run(self) -> Optional[Dict[str, Any]]:
        return self._read_json(self.run_file)

    def write_attempt(self, case_id: str, attempt: int, result: Dict[str, Any]) -> str:
        path = self.attempt_file(case_id, attempt, 'result.json')
        self._write_json(path, result)
        return path

    def read_attempts(self, case_id: str) -> List[Dict[str, Any]]:
        case_dir = self._path('cases', case_id)
        if not os.path.isdir(case_dir):
            return []
        results = []
        for name in sorted(os.listdir(case_dir)):
            if not name.startswith('attempt_'):
                continue
            data = self._read_json(os.path.join(case_dir, name, 'result.json'))
            if data:
                results.append(data)
        return results

    def write_report_copy(self, case_id: str, attempt: int, markdown: str) -> str:
        path = self.attempt_file(case_id, attempt, 'report.md')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(markdown)
        return path

    def finalise(self, summary: Dict[str, Any], scorecard: Dict[str, Any],
                 stability: Optional[Dict[str, Any]] = None) -> None:
        self._write_json(self.summary_file, summary)
        self._write_json(self.scorecard_file, scorecard)
        if stability is not None:
            self._write_json(self.stability_file, stability)

    # -- 取消 --

    def request_cancel(self) -> None:
        with open(self.cancel_flag, 'w', encoding='utf-8') as handle:
            handle.write(datetime.now(timezone.utc).isoformat())

    def cancel_requested(self) -> bool:
        return os.path.isfile(self.cancel_flag)

    @classmethod
    def list_runs(cls) -> List[str]:
        if not os.path.isdir(RUN_ROOT):
            return []
        return sorted(
            name for name in os.listdir(RUN_ROOT)
            if os.path.isfile(os.path.join(RUN_ROOT, name, 'run.json'))
        )


# ══════════════════════════════════════════════════════════════
# 编排执行
# ══════════════════════════════════════════════════════════════

class AttemptTimeout(Exception):
    """单次尝试超时。"""


class CaseExecutor:
    """
    跑一个案卷的一次尝试，并施加全部"会导致结果静默失真"的护栏。

    为什么编排放在生产代码里而不是驱动里：id 串联、每案卷新 sim_id 的不变量、
    逐阶段护栏、预算扣减、清单采集——这些最容易静默出错的部分都在这一层，
    放在这里才能被离线桩件测到。
    """

    def __init__(self, driver, *, run_store: BenchmarkRunStore,
                 budget_calls: int = 0, section_estimate: int = 4):
        self.driver = driver
        self.store = run_store
        self.budget_calls = int(budget_calls or 0)
        self.section_estimate = section_estimate
        self.spent_calls = 0

    # -- 预算 --

    def _spend(self, calls: int) -> None:
        self.spent_calls += max(int(calls or 0), 0)

    @property
    def remaining_budget(self) -> int:
        if self.budget_calls <= 0:
            return 1 << 60
        return max(self.budget_calls - self.spent_calls, 0)

    # -- 主流程 --

    def run_case(self, case: BenchmarkCase, *, attempt: int = 1,
                 repeat_index: int = 0, max_rounds: Optional[int] = None,
                 progress: Optional[Callable[[str, int, str], None]] = None,
                 cancel: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
        """执行一次尝试，返回结果字典。**不抛异常**——失败也产出可入库的结果。"""
        def report(stage: str, pct: int, message: str = '') -> None:
            if progress:
                progress(stage, pct, message)

        validation = validate_case(case)
        validated = bool(validation.get('ok'))

        result: Dict[str, Any] = {
            'schema_version': RUN_SCHEMA_VERSION,
            'run_id': self.store.run_id,
            'case_id': case.case_id,
            'case_sha256': BenchmarkManager.case_sha256(case),
            'attempt': attempt,
            'repeat_index': repeat_index,
            'synthetic': case.synthetic,
            'validated': validated,
            'status': 'failed',
            'status_reason': None,
            'horizon_truncated': False,
            'headline_eligible': False,
            'ids': {},
            'counts': {},
            'cost': {},
            'offline_metrics': {},
            'llm_scores': None,
            'validation': validation,
        }

        artifacts: Dict[str, Any] = {}
        sim_run = None
        try:
            report('prepare_corpus', 0)
            corpus = self.driver.prepare_corpus(case, progress=report)
            self._spend(1 + getattr(corpus, 'chunk_count', 0))
            artifacts['corpus'] = corpus

            report('build_graph', 20)
            graph = self.driver.build_graph(case, corpus, name=f'bench_{case.case_id}', progress=report)
            self._spend(getattr(graph, 'node_count', 0))
            artifacts['graph'] = graph

            # 护栏：图谱抽空 → 报告必然近乎空，却会被打成"预测错了"，
            # 静默压低准确率。这类尝试必须排除在聚合之外。
            if graph.node_count < case.min_nodes() or graph.entity_count < case.min_entities():
                return self._finish(
                    result, artifacts, 'invalid_run',
                    reason=f'图谱过小: nodes={graph.node_count} (需>={case.min_nodes()}), '
                           f'entities={graph.entity_count} (需>={case.min_entities()})'
                )

            report('prepare_simulation', 40)
            sim = self.driver.prepare_simulation(case, corpus, graph, progress=report)
            self._spend(getattr(sim, 'entities_count', 0) + 2 + max(
                (getattr(sim, 'entities_count', 0) + 14) // 15, 0))
            artifacts['simulation'] = sim

            # 预算护栏：用 prepare 之后的真实规模重算，否则前面的估算可能严重偏低
            from .cost_estimator import estimate_calls
            real_estimate = estimate_calls(
                chunk_count=0,
                entity_count=sim.entities_count,
                total_rounds=sim.planned_rounds,
                platform_count=2,
                section_count=self.section_estimate,
            )
            if real_estimate['total'] > self.remaining_budget:
                return self._finish(
                    result, artifacts, 'over_budget',
                    reason=f'真实预估 {real_estimate["total"]} 超出剩余预算 {self.remaining_budget}'
                )

            if cancel and cancel():
                return self._finish(result, artifacts, 'cancelled', reason='运行已取消')

            report('run_simulation', 55)
            sim_run = self.driver.run_simulation(
                sim, max_rounds=max_rounds, poll_interval=5.0,
                timeout=1800.0, progress=report
            )
            artifacts['sim_run'] = sim_run
            self._spend(sim.planned_rounds * sim.entities_count * 2)

            # 护栏：轮数被 max_rounds 静默截短 → 报告描述的未来比案卷 horizon 短，
            # 拿它对照结果是错的
            planned = sim.planned_rounds
            truncated = bool(planned and sim_run.total_rounds < planned)
            result['horizon_truncated'] = truncated

            if sim_run.runner_status != 'completed':
                return self._finish(result, artifacts, 'failed',
                                    reason=f'模拟未完成: {sim_run.runner_status} {sim_run.error or ""}')

            # 护栏：监控线程可能在零动作的情况下照样标 COMPLETED
            actions_total = int(sim_run.twitter_actions_count or 0) + int(sim_run.reddit_actions_count or 0)
            if actions_total < case.min_actions():
                return self._finish(
                    result, artifacts, 'invalid_run',
                    reason=f'模拟动作过少: {actions_total} (需>={case.min_actions()})'
                )

            report('generate_report', 75)
            report_id = f'report_{uuid.uuid4().hex[:12]}'
            report_artifact = self.driver.generate_report(
                case, corpus, sim, report_id=report_id, progress=report
            )
            self._spend(1 + self.section_estimate * 6)
            artifacts['report'] = report_artifact

            if report_artifact.status != 'completed':
                return self._finish(result, artifacts, 'failed',
                                    reason=f'报告状态: {report_artifact.status} {report_artifact.error or ""}')

            markdown = report_artifact.markdown or ''
            if len(markdown) < case.min_report_chars():
                return self._finish(
                    result, artifacts, 'invalid_run',
                    reason=f'报告过短: {len(markdown)} 字符 (需>={case.min_report_chars()})'
                )

            report('scoring', 90)
            from .benchmark_scoring import offline_metrics
            result['offline_metrics'] = offline_metrics(
                markdown=markdown,
                graph_nodes=[n.get('name', '') for n in (graph.nodes or [])],
                case=case,
                corpus_text=corpus.text,
                outline=report_artifact.outline,
                min_report_chars=case.min_report_chars(),
            )
            return self._finish(result, artifacts, 'ok')

        except AttemptTimeout as exc:
            return self._finish(result, artifacts, 'timed_out', reason=str(exc))
        except Exception as exc:  # noqa: BLE001  回测不能因单次尝试崩掉整轮
            logger.exception('回测尝试失败 case=%s attempt=%s', case.case_id, attempt)
            return self._finish(result, artifacts, 'failed',
                                reason=f'{type(exc).__name__}: {exc}')
        finally:
            # 无论成败都要释放：清空 SimulationRunner 的类级缓存，
            # 否则运行越长内存越大，且进程退出时的清理钩子会误杀后续模拟
            try:
                self.driver.release(sim_run)
            except Exception as exc:  # noqa: BLE001
                logger.warning('释放资源失败（忽略）: %s', exc)

    def _finish(self, result: Dict[str, Any], artifacts: Dict[str, Any],
                status: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """收尾：补齐计数、判定 headline_eligible、落盘。"""
        corpus = artifacts.get('corpus')
        graph = artifacts.get('graph')
        sim = artifacts.get('simulation')
        sim_run = artifacts.get('sim_run')
        report_artifact = artifacts.get('report')

        result['status'] = status
        result['status_reason'] = reason
        result['ids'] = {
            'project_id': getattr(corpus, 'project_id', None),
            'graph_id': getattr(graph, 'graph_id', None),
            'simulation_id': getattr(sim, 'simulation_id', None),
            'report_id': getattr(report_artifact, 'report_id', None),
        }
        result['counts'] = {
            'chunk_count': getattr(corpus, 'chunk_count', None),
            'node_count': getattr(graph, 'node_count', None),
            'entity_count': getattr(graph, 'entity_count', None),
            'profiles_count': getattr(sim, 'profiles_count', None),
            'planned_rounds': getattr(sim, 'planned_rounds', None),
            'executed_rounds': getattr(sim_run, 'total_rounds', None),
            'actions_total': getattr(sim_run, 'actions_total', None),
            'report_chars': len(getattr(report_artifact, 'markdown', '') or ''),
        }
        result['cost'] = {'spent_calls_estimate': self.spent_calls, 'budget_calls': self.budget_calls}

        # 稳定性度量要用到的原始素材：图谱实体名与情景名。
        # 只存名字，不存整个图谱——够用且体积可控。
        result['graph_entities'] = [
            node.get('name', '') for node in (getattr(graph, 'nodes', None) or [])
            if node.get('name')
        ]
        outline = getattr(report_artifact, 'outline', None) or {}
        result['scenarios'] = [
            {'name': s.get('name'), 'trigger_conditions': s.get('trigger_conditions') or []}
            for s in (outline.get('scenarios') or []) if isinstance(s, dict)
        ]

        # 只有"跑通 + 通过防泄漏校验 + 非合成 + 未被截断"的结果才配进入头条统计。
        # 合成案卷永远不合格——它是 fixture，不是证据。
        result['headline_eligible'] = bool(
            status == 'ok'
            and result.get('validated')
            and not result.get('synthetic')
            and not result.get('horizon_truncated')
        )

        try:
            self.store.write_attempt(result['case_id'], result['attempt'], result)
            if report_artifact is not None and getattr(report_artifact, 'markdown', ''):
                self.store.write_report_copy(result['case_id'], result['attempt'], report_artifact.markdown)
        except Exception as exc:  # noqa: BLE001
            logger.warning('写入尝试结果失败: %s', exc)

        return result


# ══════════════════════════════════════════════════════════════
# 整轮编排（CLI 与 API 共用）
# ══════════════════════════════════════════════════════════════

def run_all(
    *,
    run_store: BenchmarkRunStore,
    case_ids: Sequence[str],
    driver_factory: Callable[[BenchmarkCase, int], Any],
    repeats: int = 1,
    max_rounds: Optional[int] = None,
    budget_calls: int = 0,
    progress: Optional[Callable[..., None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """
    顺序跑完一批案卷 × N 次重复，落盘 summary / scorecard / stability。

    **严格串行**：瓶颈是 LLM，并发在这里没有收益，而所有单例风险（SQLite WAL、
    SimulationRunner 的类级缓存）在并发下都更糟。

    Args:
        driver_factory: (case, repeat_index) -> 驱动。重复次数用于稳定性度量时，
            可以据此为每次返回带不同变体的桩件。
    """
    from .benchmark_scoring import build_scorecard, stability

    attempts: List[Dict[str, Any]] = []
    attempt_artifacts: Dict[int, Dict[str, Any]] = {}
    total_cases = len(case_ids)
    cancelled = False

    run_meta: Dict[str, Any] = {
        'schema_version': RUN_SCHEMA_VERSION,
        'run_id': run_store.run_id,
        'state': 'running',
        'started_at': datetime.now(timezone.utc).isoformat(),
        'config': {
            'cases': list(case_ids),
            'repeats': repeats,
            'max_rounds': max_rounds,
            'budget_calls': budget_calls,
        },
        'git': _git_state(),
        'progress': {},
        'cases': {},
    }
    run_store.write_run(run_meta)

    attempt_counter = 0
    try:
        for case_index, case_id in enumerate(case_ids):
            case = BenchmarkManager.load(case_id)
            run_meta['cases'].setdefault(case_id, {'attempts': []})

            for repeat_index in range(repeats):
                if cancel and cancel():
                    cancelled = True
                    break

                attempt_counter += 1
                attempt_no = attempt_counter
                driver = driver_factory(case, repeat_index)
                executor = CaseExecutor(driver, run_store=run_store, budget_calls=budget_calls)

                if progress:
                    progress(case_index, total_cases, case_id, repeat_index, 'start', 0,
                             f'开始 {case_id} 第 {repeat_index + 1}/{repeats} 次')

                result = executor.run_case(
                    case, attempt=attempt_no, repeat_index=repeat_index,
                    max_rounds=max_rounds,
                    progress=(lambda stage, pct, msg, _ci=case_index, _cid=case_id,
                                     _ri=repeat_index, _an=attempt_no:
                              progress(_ci, total_cases, _cid, _ri, stage, pct, msg) if progress else None),
                    cancel=cancel,
                )

                # 运行清单：让每条结果可复现，也是稳定性"可比性"检查的依据
                try:
                    from .run_manifest import build_manifest
                    result['manifest'] = build_manifest(
                        project_id=result['ids'].get('project_id'),
                        graph_id=result['ids'].get('graph_id'),
                        simulation_id=result['ids'].get('simulation_id'),
                        report_id=result['ids'].get('report_id'),
                        chunk_count=result['counts'].get('chunk_count'),
                        entity_count=result['counts'].get('entity_count'),
                        total_rounds=result['counts'].get('planned_rounds'),
                        max_rounds=max_rounds,
                        platform='parallel',
                        extra={'case_id': case_id, 'attempt': attempt_no,
                               'repeat_index': repeat_index, 'config_sha256': _config_sha256(case)},
                    )
                    run_store.write_attempt(case_id, attempt_no, result)
                except Exception as exc:  # noqa: BLE001
                    logger.warning('写入运行清单失败: %s', exc)

                attempts.append(result)
                attempt_artifacts[attempt_no] = _attempt_artifacts(run_store, case_id, attempt_no)

                run_meta['cases'][case_id]['attempts'].append({
                    'attempt': attempt_no,
                    'status': result['status'],
                    'status_reason': result.get('status_reason'),
                    'headline_eligible': result.get('headline_eligible'),
                })
                run_meta['progress'] = {
                    'case_index': case_index, 'total_cases': total_cases,
                    'case_id': case_id, 'repeat': repeat_index,
                    'spent_calls_est': sum(
                        a.get('cost', {}).get('spent_calls_estimate', 0) for a in attempts),
                }
                run_store.write_run(run_meta)

            if cancelled:
                break

        per_case_stability = {}
        for case_id in {a['case_id'] for a in attempts}:
            case_attempts = [a for a in attempts if a['case_id'] == case_id]
            if len(case_attempts) > 1:
                per_case_stability[case_id] = stability(case_attempts, attempt_artifacts)

        stability_payload = per_case_stability or None
        scorecard = build_scorecard(attempts)
        summary = _build_summary(attempts, case_ids, repeats, cancelled)

        run_meta['state'] = 'cancelled' if cancelled else 'completed'
        run_meta['completed_at'] = datetime.now(timezone.utc).isoformat()
        run_store.write_run(run_meta)
        run_store.finalise(summary, scorecard, stability_payload)

        return {
            'run_id': run_store.run_id,
            'state': run_meta['state'],
            'summary': summary,
            'scorecard': scorecard,
            'stability': stability_payload,
        }
    except Exception as exc:  # noqa: BLE001
        run_meta['state'] = 'failed'
        run_meta['error'] = f'{type(exc).__name__}: {exc}'
        run_meta['completed_at'] = datetime.now(timezone.utc).isoformat()
        run_store.write_run(run_meta)
        raise


def _build_summary(attempts: Sequence[Dict[str, Any]], case_ids: Sequence[str],
                   repeats: int, cancelled: bool) -> Dict[str, Any]:
    by_status: Dict[str, int] = {}
    for attempt in attempts:
        by_status[attempt['status']] = by_status.get(attempt['status'], 0) + 1

    return {
        'schema_version': RUN_SCHEMA_VERSION,
        'cancelled': cancelled,
        'cases_requested': list(case_ids),
        'repeats': repeats,
        'attempts_total': len(attempts),
        'by_status': by_status,
        'attempts': [
            {
                'case_id': a['case_id'],
                'attempt': a['attempt'],
                'status': a['status'],
                'status_reason': a.get('status_reason'),
                'headline_eligible': a.get('headline_eligible'),
                'spent_calls_estimate': a.get('cost', {}).get('spent_calls_estimate'),
            }
            for a in attempts
        ],
    }


def _attempt_artifacts(store: BenchmarkRunStore, case_id: str, attempt: int) -> Dict[str, Any]:
    """收集稳定性度量需要的产物（报告原文 / 图谱实体名 / 情景名）。"""
    artifacts: Dict[str, Any] = {}

    report_path = store.attempt_file(case_id, attempt, 'report.md')
    if os.path.isfile(report_path):
        with open(report_path, 'r', encoding='utf-8') as handle:
            artifacts['report_markdown'] = handle.read()

    result = store._read_json(store.attempt_file(case_id, attempt, 'result.json'))
    if result:
        artifacts['scenarios'] = result.get('scenarios') or []
        artifacts['graph_entities'] = result.get('graph_entities') or []
    return artifacts


def _config_sha256(case: BenchmarkCase) -> str:
    """配置指纹：用于判定多次运行是否真的可比。"""
    payload = json.dumps({
        'requirement': case.simulation_requirement,
        'assumptions': case.assumptions,
        'expectations': case.expectations,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def _git_state() -> Optional[Dict[str, Any]]:
    from .run_manifest import git_state
    return git_state()
