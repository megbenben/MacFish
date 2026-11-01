"""删除历史模拟记录及其全部落盘产物。

历史卡片对应的是「一次模拟运行」（``sim_xxx``），它的数据分散在三处：

- ``uploads/simulations/<simulation_id>/``：状态、人设、配置、两个平台的
  数据库与动作日志 —— 整个目录删掉；
- ``uploads/reports/<report_id>/``：``meta.json`` 里 ``simulation_id`` 指向
  这次运行的报告 —— 一并删掉；
- ``uploads/projects/<project_id>/``：上传的原始文档与抽取文本。同一个项目
  可以点多次「进入环境搭建」而生成多条模拟记录，所以只有在这个项目已经
  没有任何其他模拟记录时才删除，避免删掉别的历史记录还在用的文件。

项目被删除、且它的图谱不再被任何其他项目引用时，``graphs.db`` 里的节点/
边也一并清除。

运行中的模拟拒绝删除：子进程还在往目录里写文件，先停止再删。
"""

import json
import os
import re
import shutil
from typing import Any, Dict, Iterable, List, Optional, Set

from ..config import Config
from ..utils.logger import get_logger
from ..models.project import ProjectManager
from .simulation_runner import SimulationRunner, RunnerStatus

logger = get_logger('mirofish.simulation_cleanup')

_SIM_ID_RE = re.compile(r'^sim_[A-Za-z0-9_-]{1,64}$')
_PROJECT_ID_RE = re.compile(r'^proj_[A-Za-z0-9_-]{1,64}$')
_GRAPH_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,128}$')

# 这些状态下子进程仍可能在写模拟目录，不能边写边删
_ACTIVE_RUNNER_STATUSES = {
    RunnerStatus.STARTING,
    RunnerStatus.RUNNING,
    RunnerStatus.PAUSED,
    RunnerStatus.STOPPING,
    RunnerStatus.STALLED,
}


class SimulationDeletionError(Exception):
    """删除历史模拟失败的基类。"""


class InvalidSimulationId(SimulationDeletionError):
    def __init__(self, simulation_id: Any):
        self.simulation_id = simulation_id
        super().__init__(f"非法的模拟ID: {simulation_id!r}")


class SimulationRunningError(SimulationDeletionError):
    def __init__(self, active_ids: Iterable[str]):
        self.active_ids = list(active_ids)
        super().__init__(f"模拟正在运行，不能删除: {', '.join(self.active_ids)}")


def _simulations_dir() -> str:
    return os.path.join(Config.UPLOAD_FOLDER, 'simulations')


def _read_state(sim_dir: str) -> Dict[str, Any]:
    state_file = os.path.join(sim_dir, 'state.json')
    if not os.path.isfile(state_file):
        return {}
    try:
        with open(state_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # 状态文件坏了不应该阻止删除目录
        logger.warning("读取模拟状态失败（仍继续删除）: %s, error=%s", state_file, exc)
        return {}


def _normalize_ids(simulation_ids: Iterable[str]) -> List[str]:
    if isinstance(simulation_ids, str):
        simulation_ids = [simulation_ids]

    normalized: List[str] = []
    seen: Set[str] = set()
    for raw in simulation_ids or []:
        if not isinstance(raw, str) or not _SIM_ID_RE.match(raw):
            raise InvalidSimulationId(raw)
        if raw not in seen:
            seen.add(raw)
            normalized.append(raw)

    if not normalized:
        raise SimulationDeletionError("没有可删除的模拟ID")
    return normalized


def _active_ids(simulation_ids: Iterable[str]) -> List[str]:
    """找出仍然活着的模拟：状态文件与进程句柄任一显示在运行都算。"""
    active: List[str] = []
    for simulation_id in simulation_ids:
        state = SimulationRunner.get_run_state(simulation_id)
        if state is not None and state.runner_status in _ACTIVE_RUNNER_STATUSES:
            active.append(simulation_id)
            continue

        process = SimulationRunner._processes.get(simulation_id)  # noqa: SLF001
        if process is not None and process.poll() is None:
            active.append(simulation_id)
    return active


def _drop_runner_caches(simulation_id: str) -> None:
    """目录删掉后，清掉运行器里指向它的所有内存引用。"""
    SimulationRunner._run_states.pop(simulation_id, None)  # noqa: SLF001
    SimulationRunner._action_queues.pop(simulation_id, None)  # noqa: SLF001
    SimulationRunner._graph_memory_enabled.pop(simulation_id, None)  # noqa: SLF001
    SimulationRunner._processes.pop(simulation_id, None)  # noqa: SLF001
    SimulationRunner._monitor_threads.pop(simulation_id, None)  # noqa: SLF001

    for handles in (SimulationRunner._stdout_files, SimulationRunner._stderr_files):  # noqa: SLF001
        handle = handles.pop(simulation_id, None)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


def _delete_reports_for_simulation(simulation_id: str) -> List[str]:
    """删除 meta.json 中 simulation_id 匹配的全部报告目录。"""
    from .report_agent import ReportManager  # 延迟导入，避免拉重依赖

    deleted: List[str] = []
    for report in ReportManager.list_reports(simulation_id=simulation_id, limit=100000):
        if ReportManager.delete_report(report.report_id):
            deleted.append(report.report_id)
    return deleted


def _project_ids_still_referenced() -> Set[str]:
    """扫描现存模拟目录，返回仍被引用的项目ID。"""
    referenced: Set[str] = set()
    root = _simulations_dir()
    if not os.path.isdir(root):
        return referenced

    for entry in os.listdir(root):
        sim_dir = os.path.join(root, entry)
        if not os.path.isdir(sim_dir):
            continue
        project_id = _read_state(sim_dir).get('project_id') or ''
        if project_id:
            referenced.add(project_id)
    return referenced


def _graph_still_referenced(graph_id: str) -> bool:
    for project in ProjectManager.list_projects(limit=10_000_000):
        if project.graph_id == graph_id:
            return True
    return False


def _delete_graph(graph_id: str) -> bool:
    if not _GRAPH_ID_RE.match(graph_id or ''):
        logger.warning("跳过删除非法图谱ID: %r", graph_id)
        return False

    from .graph_builder import GraphBuilderService  # 延迟导入，避免循环依赖

    try:
        GraphBuilderService(api_key=Config.ZEP_API_KEY).delete_graph(graph_id)
        return True
    except Exception as exc:
        logger.warning("删除图谱失败: %s, error=%s", graph_id, exc)
        return False


def _cleanup_orphan_projects(
    project_ids: Iterable[str],
    *,
    delete_graphs: bool,
) -> tuple[List[str], List[str]]:
    """删除已经没有任何模拟记录的项目目录，以及不再被引用的图谱。"""
    still_referenced = _project_ids_still_referenced()
    deleted_projects: List[str] = []
    deleted_graphs: List[str] = []

    for project_id in project_ids:
        if project_id in still_referenced:
            continue
        if not _PROJECT_ID_RE.match(project_id or ''):
            logger.warning("跳过删除非法项目ID: %r", project_id)
            continue

        project = ProjectManager.get_project(project_id)
        if project is None:
            continue

        graph_id = (project.graph_id or '').strip()
        if ProjectManager.delete_project(project_id):
            deleted_projects.append(project_id)

            if delete_graphs and graph_id and not _graph_still_referenced(graph_id):
                if _delete_graph(graph_id):
                    deleted_graphs.append(graph_id)

    return deleted_projects, deleted_graphs


def delete_simulations(
    simulation_ids: Iterable[str],
    *,
    delete_graphs: bool = True,
) -> Dict[str, Any]:
    """批量删除历史模拟记录。

    Returns:
        {
            "deleted": [sim_id, ...],
            "failed": [{"simulation_id": ..., "error": ...}, ...],
            "deleted_reports": [report_id, ...],
            "deleted_projects": [project_id, ...],
            "deleted_graphs": [graph_id, ...],
        }

    Raises:
        InvalidSimulationId: 任一 ID 不合法。
        SimulationRunningError: 任一模拟仍在运行（整批拒绝，避免只删一半）。
    """
    ids = _normalize_ids(simulation_ids)

    active = _active_ids(ids)
    if active:
        raise SimulationRunningError(active)

    deleted: List[str] = []
    failed: List[Dict[str, str]] = []
    deleted_reports: List[str] = []
    affected_projects: Set[str] = set()

    root = _simulations_dir()
    for simulation_id in ids:
        sim_dir = os.path.join(root, simulation_id)
        state = _read_state(sim_dir) if os.path.isdir(sim_dir) else {}
        project_id = state.get('project_id') or ''

        try:
            if os.path.isdir(sim_dir):
                shutil.rmtree(sim_dir)

            _drop_runner_caches(simulation_id)

            try:
                deleted_reports.extend(_delete_reports_for_simulation(simulation_id))
            except Exception as exc:  # 报告清理失败不能让模拟目录“复活”
                logger.warning("删除模拟 %s 的报告失败: %s", simulation_id, exc)

            deleted.append(simulation_id)
            if project_id:
                affected_projects.add(project_id)
        except Exception as exc:
            logger.error("删除模拟失败: %s, error=%s", simulation_id, exc)
            failed.append({"simulation_id": simulation_id, "error": str(exc)})

    deleted_projects, deleted_graphs = _cleanup_orphan_projects(
        affected_projects, delete_graphs=delete_graphs
    )

    return {
        "deleted": deleted,
        "failed": failed,
        "deleted_reports": deleted_reports,
        "deleted_projects": deleted_projects,
        "deleted_graphs": deleted_graphs,
    }


def delete_simulation(simulation_id: str, *, delete_graphs: bool = True) -> Dict[str, Any]:
    """删除单条历史模拟记录，失败时抛错而不是返回 failed 列表。"""
    result = delete_simulations([simulation_id], delete_graphs=delete_graphs)
    if result["failed"]:
        raise SimulationDeletionError(result["failed"][0]["error"])
    return result
