"""
回测驱动：真实管线的封装 + 供离线验证用的桩件
------------------------------------------
接缝**按阶段**切（而不是一个 run_case()），理由是：最容易静默出错的部分——
id 串联、"每个尝试必须用新的 simulation_id"、逐阶段护栏、预算扣减、清单采集——
全都在编排层（`benchmark.py` 的 CaseExecutor）。若把整条流程塞进驱动，这些就
无法在零 LLM 调用的情况下被测试。按阶段切还有个好处：可以注入"第 k 阶段失败"
的桩，逐条验证护栏。

`StubDriver` 从预置的 JSON fixture 读产物，纯文件 IO、零网络，是离线验证的载体。
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# 阶段产物（驱动与编排之间的数据契约）
# ══════════════════════════════════════════════════════════════

@dataclass
class PreparedCorpus:
    project_id: str
    ontology: Dict[str, Any] = field(default_factory=dict)
    analysis_summary: str = ''
    text: str = ''
    chunk_count: int = 0


@dataclass
class GraphArtifact:
    graph_id: str
    node_count: int = 0
    edge_count: int = 0
    entity_count: int = 0
    nodes: Sequence[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SimulationArtifact:
    simulation_id: str
    case_id: str = ''
    #: 报告生成需要图谱 id，而 SimulationRunner 的状态里不一定带——
    #: 所以在这里显式传递，别让下游去猜
    graph_id: str = ''
    entities_count: int = 0
    profiles_count: int = 0
    planned_rounds: int = 0


@dataclass
class SimulationRunArtifact:
    simulation_id: str
    runner_status: str = 'failed'
    total_rounds: int = 0
    twitter_actions_count: int = 0
    reddit_actions_count: int = 0
    error: Optional[str] = None


@dataclass
class ReportArtifact:
    report_id: str
    status: str = 'failed'
    markdown: str = ''
    outline: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# ══════════════════════════════════════════════════════════════
# 真实驱动
# ══════════════════════════════════════════════════════════════

class RealPipelineDriver:
    """
    把真实管线包成按阶段可调用的形态。

    严格串行使用：`LocalGraphStore` 是带 WAL 的单例、`SimulationRunner` 有类级缓存，
    并发跑多个案卷会撞 SQLite 锁与陈旧状态。
    """

    def prepare_corpus(self, case, *, progress=None):
        from .text_processor import TextProcessor
        from .ontology_generator import OntologyGenerator
        from ..models.project import ProjectManager, ProjectStatus

        project = ProjectManager.create_project(name=f'bench_{case.case_id}')
        project.simulation_requirement = case.simulation_requirement

        text = case.corpus_text()
        ProjectManager.save_extracted_text(project.project_id, text)

        if progress:
            progress('ontology', 10, '生成本体')

        ontology = OntologyGenerator().generate(
            document_texts=[text],
            simulation_requirement=case.simulation_requirement,
        )
        project.ontology = {
            'entity_types': ontology.get('entity_types', []),
            'edge_types': ontology.get('edge_types', []),
        }
        project.analysis_summary = ontology.get('analysis_summary', '')
        project.status = ProjectStatus.ONTOLOGY_GENERATED
        ProjectManager.save_project(project)

        chunk_count = len(TextProcessor.split_text(
            text, chunk_size=project.chunk_size, overlap=project.chunk_overlap))

        return PreparedCorpus(
            project_id=project.project_id,
            ontology=project.ontology,
            analysis_summary=project.analysis_summary,
            text=text,
            chunk_count=chunk_count,
        )

    def build_graph(self, case, corpus, *, name: str, progress=None):
        from .graph_builder import GraphBuilderService
        from .text_processor import TextProcessor
        from .zep_entity_reader import ZepEntityReader
        from ..models.project import ProjectManager, ProjectStatus

        builder = GraphBuilderService()
        graph_id = builder.create_graph(name=name)
        builder.set_ontology(graph_id, corpus.ontology)

        chunks = TextProcessor.split_text(corpus.text, chunk_size=500, overlap=50)
        episodes = builder.add_text_batches(graph_id, chunks, batch_size=3)
        builder._wait_for_episodes(episodes, timeout=1800)

        data = builder.get_graph_data(graph_id)
        nodes = data.get('nodes', [])

        entity_count = 0
        try:
            entity_count = ZepEntityReader().filter_defined_entities(
                graph_id, enrich_with_edges=False).filtered_count
        except Exception as exc:  # noqa: BLE001
            logger.warning('实体数探针失败（按 0 处理）: %s', exc)

        project = ProjectManager.get_project(corpus.project_id)
        if project:
            project.graph_id = graph_id
            project.status = ProjectStatus.GRAPH_COMPLETED
            ProjectManager.save_project(project)

        return GraphArtifact(
            graph_id=graph_id,
            node_count=len(nodes),
            edge_count=len(data.get('edges', [])),
            entity_count=entity_count,
            nodes=nodes,
        )

    def read_graph(self, graph_id: str) -> GraphArtifact:
        from .graph_builder import GraphBuilderService
        data = GraphBuilderService().get_graph_data(graph_id)
        nodes = data.get('nodes', [])
        return GraphArtifact(
            graph_id=graph_id,
            node_count=len(nodes),
            edge_count=len(data.get('edges', [])),
            entity_count=len(nodes),
            nodes=nodes,
        )

    def prepare_simulation(self, case, corpus, graph, *, progress=None):
        from .simulation_manager import SimulationManager

        manager = SimulationManager()
        # 两个平台都开：_check_simulation_prepared 硬编码要求两个平台的人设文件，
        # 单平台会让那条就绪检查永远失败
        state = manager.create_simulation(
            corpus.project_id, graph.graph_id, enable_twitter=True, enable_reddit=True)
        manager.prepare_simulation(
            simulation_id=state.simulation_id,
            simulation_requirement=case.simulation_requirement,
            document_text=corpus.text,
        )

        from .run_manifest import planned_rounds
        return SimulationArtifact(
            simulation_id=state.simulation_id,
            case_id=case.case_id,
            graph_id=graph.graph_id,
            entities_count=state.entities_count or 0,
            profiles_count=state.profiles_count or 0,
            planned_rounds=planned_rounds(state.simulation_id) or 0,
        )

    def run_simulation(self, sim, *, max_rounds=None, poll_interval=5.0,
                       timeout=1800.0, progress=None):
        from .simulation_runner import SimulationRunner

        SimulationRunner.start_simulation(
            simulation_id=sim.simulation_id,
            platform='parallel',
            max_rounds=max_rounds,
            # 关闭图谱记忆更新：这是唯一会让模拟子进程去写 graphs.db 的开关，
            # 关掉它就不存在跨进程 SQLite 争用
            enable_graph_memory_update=False,
        )

        deadline = time.time() + timeout
        terminal = {'completed', 'failed', 'stopped'}
        state = None
        while time.time() < deadline:
            state = SimulationRunner.get_run_state(sim.simulation_id)
            status = getattr(getattr(state, 'runner_status', None), 'value', None)
            if status in terminal:
                break
            if progress:
                progress('run_simulation', 60, f'模拟中: {status}')
            time.sleep(poll_interval)

        if state is None:
            return SimulationRunArtifact(simulation_id=sim.simulation_id, error='无法获取运行状态')

        status = getattr(state.runner_status, 'value', str(state.runner_status))
        return SimulationRunArtifact(
            simulation_id=sim.simulation_id,
            runner_status=status,
            total_rounds=int(getattr(state, 'total_rounds', 0) or 0),
            twitter_actions_count=int(getattr(state, 'twitter_actions_count', 0) or 0),
            reddit_actions_count=int(getattr(state, 'reddit_actions_count', 0) or 0),
            error=getattr(state, 'error', None),
        )

    def generate_report(self, case, corpus, sim, *, report_id: str, progress=None):
        from .report_agent import ReportAgent, ReportManager

        agent = ReportAgent(
            graph_id=sim.graph_id,
            simulation_id=sim.simulation_id,
            simulation_requirement=case.simulation_requirement,
        )
        report = agent.generate_report(report_id=report_id)

        markdown = report.markdown_content or ''
        if not markdown:
            path = ReportManager._get_report_markdown_path(report_id)
            if os.path.isfile(path):
                with open(path, 'r', encoding='utf-8') as handle:
                    markdown = handle.read()

        outline = report.outline.to_dict() if report.outline else None
        return ReportArtifact(
            report_id=report.report_id,
            status='completed' if report.status.value == 'completed' else 'failed',
            markdown=markdown,
            outline=outline,
            error=report.error,
        )

    def release(self, sim_run) -> None:
        """
        释放一次尝试的资源。

        必须清空 `SimulationRunner` 的类级缓存：`_run_states` 会永久累积，
        而 `_processes` 里留着已结束的进程会让进程退出时的 atexit 钩子去误杀
        尚未开始的后续模拟。
        """
        if sim_run is None:
            return
        from .simulation_runner import SimulationRunner

        simulation_id = sim_run.simulation_id
        for attr in ('_run_states', '_processes', '_monitor_threads',
                     '_action_queues', '_graph_memory_enabled'):
            cache = getattr(SimulationRunner, attr, None)
            if isinstance(cache, dict):
                cache.pop(simulation_id, None)
        for attr in ('_stdout_files', '_stderr_files'):
            cache = getattr(SimulationRunner, attr, None)
            if isinstance(cache, dict):
                handle = cache.pop(simulation_id, None)
                try:
                    if handle:
                        handle.close()
                except Exception:  # noqa: BLE001
                    pass


# ══════════════════════════════════════════════════════════════
# 桩件驱动（离线验证专用）
# ══════════════════════════════════════════════════════════════

class StubDriver:
    """
    从 fixture 目录读产物，零网络、零 LLM。

    fixture 布局（每案卷一个目录）：
        <fixtures>/<case_id>/corpus.json       {"chunk_count": N, "text": "..."}
        <fixtures>/<case_id>/graph.json        {"node_count":..,"entity_count":..,"nodes":[...]}
        <fixtures>/<case_id>/simulation.json   {"entities_count":..,"planned_rounds":..}
        <fixtures>/<case_id>/run.json          {"runner_status":"completed",...}
        <fixtures>/<case_id>/report.json       {"status":"completed","markdown":"...","outline":{...}}

    可以用 `fail_at='build_graph'` 让某个阶段抛错，用来逐条验证护栏。
    """

    #: 各次尝试依次返回的变体目录名（用于稳定性测试）
    def __init__(self, fixtures_root: str, *, variant: Optional[str] = None,
                 fail_at: Optional[str] = None, run_status: str = 'completed'):
        self.fixtures_root = fixtures_root
        self.variant = variant
        self.fail_at = fail_at
        self.run_status = run_status
        self._released: List[Any] = []

    def _load(self, case, filename: str) -> Dict[str, Any]:
        base = os.path.join(self.fixtures_root, case.case_id)
        if self.variant:
            variant_dir = os.path.join(base, 'variants', self.variant)
            if os.path.isdir(variant_dir):
                base = variant_dir
        path = os.path.join(base, filename)
        if not os.path.isfile(path):
            # variant 目录里缺文件时回落到主目录，便于只覆盖需要变化的部分
            path = os.path.join(self.fixtures_root, case.case_id, filename)
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)

    def _maybe_fail(self, stage: str) -> None:
        if self.fail_at == stage:
            raise RuntimeError(f'桩件在阶段 {stage} 注入失败')

    def prepare_corpus(self, case, *, progress=None):
        self._maybe_fail('prepare_corpus')
        data = self._load(case, 'corpus.json')
        return PreparedCorpus(
            project_id=data.get('project_id', f'proj_stub_{case.case_id}'),
            ontology=data.get('ontology', {}),
            text=data.get('text', case.corpus_text()),
            chunk_count=int(data.get('chunk_count', 0)),
        )

    def build_graph(self, case, corpus, *, name: str, progress=None):
        self._maybe_fail('build_graph')
        data = self._load(case, 'graph.json')
        return GraphArtifact(
            graph_id=data.get('graph_id', f'mirofish_stub_{case.case_id}'),
            node_count=int(data.get('node_count', 0)),
            edge_count=int(data.get('edge_count', 0)),
            entity_count=int(data.get('entity_count', 0)),
            nodes=data.get('nodes', []),
        )

    def read_graph(self, graph_id: str) -> GraphArtifact:
        raise NotImplementedError('桩件不支持按 id 读图')

    def prepare_simulation(self, case, corpus, graph, *, progress=None):
        self._maybe_fail('prepare_simulation')
        data = self._load(case, 'simulation.json')
        return SimulationArtifact(
            simulation_id=data.get('simulation_id', f'sim_stub_{case.case_id}'),
            case_id=case.case_id,
            graph_id=data.get('graph_id', f'mirofish_stub_{case.case_id}'),
            entities_count=int(data.get('entities_count', 0)),
            profiles_count=int(data.get('profiles_count', 0)),
            planned_rounds=int(data.get('planned_rounds', 0)),
        )

    def run_simulation(self, sim, *, max_rounds=None, poll_interval=0.0,
                       timeout=0.0, progress=None):
        self._maybe_fail('run_simulation')
        data = self._load_data_for_case(sim.case_id, 'run.json', 'simulation.json')
        total_rounds = int(data.get('total_rounds', sim.planned_rounds))
        if max_rounds:
            total_rounds = min(total_rounds, int(max_rounds))
        return SimulationRunArtifact(
            simulation_id=sim.simulation_id,
            # fixture 里的值优先；构造参数只作为未提供时的默认。
            # 否则改 fixture 里的 runner_status 会被静默忽略，
            # 让"模拟失败"这类护栏用例测了个寂寞。
            runner_status=data.get('runner_status') or self.run_status,
            total_rounds=total_rounds,
            twitter_actions_count=int(data.get('twitter_actions_count', 0)),
            reddit_actions_count=int(data.get('reddit_actions_count', 0)),
            error=data.get('error'),
        )

    def _load_data_for_case(self, case_id: str, *filenames: str) -> Dict[str, Any]:
        """依次尝试若干文件名，返回第一个存在的（桩件阶段拿不到 case 对象时的入口）。"""
        base = os.path.join(self.fixtures_root, case_id)
        for filename in filenames:
            candidate = os.path.join(base, filename)
            if self.variant:
                variant_candidate = os.path.join(base, 'variants', self.variant, filename)
                if os.path.isfile(variant_candidate):
                    candidate = variant_candidate
            if os.path.isfile(candidate):
                with open(candidate, 'r', encoding='utf-8') as handle:
                    return json.load(handle)
        raise FileNotFoundError(f'桩件缺少 fixture: {case_id}/{filenames}')

    def generate_report(self, case, corpus, sim, *, report_id: str, progress=None):
        self._maybe_fail('generate_report')
        data = self._load(case, 'report.json')
        return ReportArtifact(
            report_id=report_id,
            status=data.get('status', 'completed'),
            markdown=data.get('markdown', ''),
            outline=data.get('outline'),
            error=data.get('error'),
        )

    def release(self, sim_run) -> None:
        self._released.append(sim_run)
