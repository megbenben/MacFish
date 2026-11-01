"""历史模拟记录的彻底删除（首页「管理记录 → 删除选中」的后端语义）。

删除必须同时覆盖：模拟目录、该模拟的报告、以及不再被其他记录引用的
项目文件；共享项目不能被误删；运行中的模拟必须整批拒绝。全部离线。
"""

import json
import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.config import Config  # noqa: E402
from app.models.project import ProjectManager  # noqa: E402
from app.services.report_agent import ReportManager  # noqa: E402
from app.services.simulation_cleanup import (  # noqa: E402
    InvalidSimulationId,
    SimulationRunningError,
    delete_simulations,
)
from app.services.simulation_runner import (  # noqa: E402
    RunnerStatus,
    SimulationRunState,
    SimulationRunner,
)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def _make_simulation(root, simulation_id, project_id, graph_id='graph_demo'):
    sim_dir = os.path.join(str(root), 'simulations', simulation_id)
    _write_json(os.path.join(sim_dir, 'state.json'), {
        'simulation_id': simulation_id,
        'project_id': project_id,
        'graph_id': graph_id,
        'status': 'completed',
    })
    with open(os.path.join(sim_dir, 'simulation.log'), 'w', encoding='utf-8') as f:
        f.write('simulation output')
    return sim_dir


def _make_project(root, project_id, graph_id='graph_demo'):
    project_dir = os.path.join(str(root), 'projects', project_id)
    os.makedirs(os.path.join(project_dir, 'files'), exist_ok=True)
    _write_json(os.path.join(project_dir, 'project.json'), {
        'project_id': project_id,
        'name': 'demo',
        'status': 'graph_completed',
        'created_at': '2026-01-01T00:00:00',
        'updated_at': '2026-01-01T00:00:00',
        'files': [{'filename': 'source.pdf'}],
        'graph_id': graph_id,
    })
    with open(os.path.join(project_dir, 'files', 'source.pdf'), 'wb') as f:
        f.write(b'%PDF-1.4')
    return project_dir


def _make_report(root, report_id, simulation_id):
    report_dir = os.path.join(str(root), 'reports', report_id)
    os.makedirs(report_dir, exist_ok=True)
    _write_json(os.path.join(report_dir, 'meta.json'), {
        'report_id': report_id,
        'simulation_id': simulation_id,
        'graph_id': 'graph_demo',
        'simulation_requirement': 'demo requirement',
        'status': 'completed',
        'outline': None,
        'markdown_content': '# report',
        'created_at': '2026-01-01T00:00:00',
        'completed_at': '2026-01-01T00:00:00',
        'error': None,
    })
    with open(os.path.join(report_dir, 'full_report.md'), 'w', encoding='utf-8') as f:
        f.write('# report')
    return report_dir


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, 'UPLOAD_FOLDER', str(tmp_path))
    monkeypatch.setattr(ReportManager, 'REPORTS_DIR', str(tmp_path / 'reports'))
    monkeypatch.setattr(ProjectManager, 'PROJECTS_DIR', str(tmp_path / 'projects'))
    monkeypatch.setattr(SimulationRunner, 'RUN_STATE_DIR', str(tmp_path / 'simulations'))
    # 不让真实磁盘上遗留的 run_state 干扰：默认没有运行中的模拟
    monkeypatch.setattr(
        SimulationRunner, 'get_run_state',
        classmethod(lambda cls, simulation_id: None),
    )
    SimulationRunner._run_states.clear()
    SimulationRunner._processes.clear()
    SimulationRunner._action_queues.clear()
    SimulationRunner._graph_memory_enabled.clear()
    yield tmp_path
    SimulationRunner._run_states.clear()
    SimulationRunner._processes.clear()
    SimulationRunner._action_queues.clear()
    SimulationRunner._graph_memory_enabled.clear()


def test_deletes_simulation_report_and_orphan_project(env):
    sim_dir = _make_simulation(env, 'sim_aaaa', 'proj_aaaa')
    project_dir = _make_project(env, 'proj_aaaa')
    report_dir = _make_report(env, 'report_aaaa', 'sim_aaaa')

    result = delete_simulations(['sim_aaaa'], delete_graphs=False)

    assert result['deleted'] == ['sim_aaaa']
    assert result['failed'] == []
    assert result['deleted_reports'] == ['report_aaaa']
    assert result['deleted_projects'] == ['proj_aaaa']
    assert not os.path.exists(sim_dir)
    assert not os.path.exists(report_dir)
    assert not os.path.exists(project_dir)


def test_keeps_project_shared_with_another_simulation(env):
    _make_simulation(env, 'sim_aaaa', 'proj_shared')
    second_dir = _make_simulation(env, 'sim_bbbb', 'proj_shared')
    project_dir = _make_project(env, 'proj_shared')

    result = delete_simulations(['sim_aaaa'], delete_graphs=False)

    assert result['deleted'] == ['sim_aaaa']
    assert result['deleted_projects'] == []
    assert os.path.isdir(second_dir)
    assert os.path.isdir(project_dir)


def test_batch_delete_removes_shared_project_after_last_record(env):
    _make_simulation(env, 'sim_aaaa', 'proj_shared')
    _make_simulation(env, 'sim_bbbb', 'proj_shared')
    project_dir = _make_project(env, 'proj_shared')

    result = delete_simulations(['sim_aaaa', 'sim_bbbb'], delete_graphs=False)

    assert sorted(result['deleted']) == ['sim_aaaa', 'sim_bbbb']
    assert result['deleted_projects'] == ['proj_shared']
    assert not os.path.exists(project_dir)


def test_running_simulation_rejects_whole_batch(env, monkeypatch):
    running_dir = _make_simulation(env, 'sim_running', 'proj_aaaa')
    idle_dir = _make_simulation(env, 'sim_idle', 'proj_bbbb')

    def fake_run_state(cls, simulation_id):
        if simulation_id == 'sim_running':
            return SimulationRunState(
                simulation_id=simulation_id,
                runner_status=RunnerStatus.RUNNING,
            )
        return None

    monkeypatch.setattr(SimulationRunner, 'get_run_state', classmethod(fake_run_state))

    with pytest.raises(SimulationRunningError) as exc_info:
        delete_simulations(['sim_running', 'sim_idle'], delete_graphs=False)

    assert exc_info.value.active_ids == ['sim_running']
    # 整批拒绝：没被删的记录目录必须原样保留
    assert os.path.isdir(running_dir)
    assert os.path.isdir(idle_dir)


@pytest.mark.parametrize('bad_id', ['../etc/passwd', 'sim_../x', 'proj_aaaa', '', None])
def test_rejects_invalid_ids(env, bad_id):
    with pytest.raises(InvalidSimulationId):
        delete_simulations([bad_id], delete_graphs=False)


def test_missing_directory_still_removes_reports(env):
    """目录被手工删过时，仍应清掉残留报告并让前端把卡片移除。"""
    report_dir = _make_report(env, 'report_orphan', 'sim_missing')

    result = delete_simulations(['sim_missing'], delete_graphs=False)

    assert result['deleted'] == ['sim_missing']
    assert result['failed'] == []
    assert not os.path.exists(report_dir)


def test_deletes_orphan_graph_with_project(env, monkeypatch):
    import app.services.simulation_cleanup as cleanup

    _make_simulation(env, 'sim_aaaa', 'proj_aaaa', graph_id='graph_orphan')
    _make_project(env, 'proj_aaaa', graph_id='graph_orphan')

    called = []
    monkeypatch.setattr(cleanup, '_delete_graph', lambda graph_id: called.append(graph_id) or True)

    result = delete_simulations(['sim_aaaa'])

    assert result['deleted_graphs'] == ['graph_orphan']
    assert called == ['graph_orphan']


def test_keeps_graph_still_referenced_by_another_project(env, monkeypatch):
    import app.services.simulation_cleanup as cleanup

    _make_simulation(env, 'sim_aaaa', 'proj_aaaa', graph_id='graph_shared')
    _make_project(env, 'proj_aaaa', graph_id='graph_shared')
    # 另一个项目（没有模拟记录）仍在使用同一张图谱
    _make_project(env, 'proj_other', graph_id='graph_shared')

    called = []
    monkeypatch.setattr(cleanup, '_delete_graph', lambda graph_id: called.append(graph_id) or True)

    result = delete_simulations(['sim_aaaa'])

    assert result['deleted_projects'] == ['proj_aaaa']
    assert result['deleted_graphs'] == []
    assert called == []
