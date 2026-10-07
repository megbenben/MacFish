"""模拟运行器与就绪判定的回归测试（B2 / B4 / B6 / A3）。

不需要网络。进程相关的用例自己起一个假脚本再回收，绝不碰真实模拟。
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.config import Config  # noqa: E402
from app.services.simulation_runner import (  # noqa: E402
    RunnerStatus,
    SimulationRunner,
    SimulationRunState,
)


@pytest.fixture()
def sim_root(tmp_path, monkeypatch):
    """把 RUN_STATE_DIR 指到临时目录，避免碰到 uploads/ 下的真实数据"""
    root = tmp_path / 'simulations'
    root.mkdir()
    monkeypatch.setattr(SimulationRunner, 'RUN_STATE_DIR', str(root))
    SimulationRunner._run_states.clear()
    SimulationRunner._processes.clear()
    yield root
    SimulationRunner._run_states.clear()
    SimulationRunner._processes.clear()


def _write_run_state(root, sim_id, **overrides):
    sim_dir = root / sim_id
    sim_dir.mkdir(parents=True, exist_ok=True)
    data = {
        'simulation_id': sim_id,
        'runner_status': 'running',
        'current_round': 1,
        'total_rounds': 10,
        'twitter_running': True,
        'reddit_running': True,
    }
    data.update(overrides)
    (sim_dir / 'run_state.json').write_text(json.dumps(data), encoding='utf-8')
    return sim_dir


def _read_run_state(root, sim_id):
    return json.loads((root / sim_id / 'run_state.json').read_text(encoding='utf-8'))


# ------------------------------------------------------- B6：动作日志游标

def test_action_log_does_not_skip_half_written_line(tmp_path):
    """写方是 open('a') + write(json + '\\n')，轮询可能撞上「半行」的瞬间。

    以前的实现 `except JSONDecodeError: pass` 之后仍然返回 f.tell()，
    游标越过这半行，这条动作就永久丢了（不计数、不进 recent_actions、无日志）。
    """
    log = tmp_path / 'actions.jsonl'
    full1 = json.dumps({'round': 1, 'agent_id': 1, 'agent_name': 'a', 'action_type': 'post'})
    full2 = json.dumps({'round': 1, 'agent_id': 2, 'agent_name': 'b', 'action_type': 'post'})
    half = '{"round":2,"agent_id":3,"agent_na'
    log.write_text(f'{full1}\n{full2}\n{half}', encoding='utf-8')

    state = SimulationRunState(simulation_id='t', total_rounds=3)
    pos = SimulationRunner._read_action_log(str(log), 0, state, 'twitter')

    assert state.twitter_actions_count == 2
    # 游标必须停在半行之前，不能越过
    assert pos < os.path.getsize(log)
    assert pos == len(f'{full1}\n{full2}\n'.encode('utf-8'))

    # 写方把剩下的补完，下一次轮询必须能读到
    with open(log, 'a', encoding='utf-8') as f:
        f.write('me":"c","action_type":"post"}\n')
    pos = SimulationRunner._read_action_log(str(log), pos, state, 'twitter')

    assert state.twitter_actions_count == 3
    assert pos == os.path.getsize(log)


def test_action_log_skips_corrupt_complete_line(tmp_path):
    """完整的一行却解析不了（真损坏）时跳过并告警，不能把整条日志卡死"""
    log = tmp_path / 'actions.jsonl'
    good = json.dumps({'round': 1, 'agent_id': 1, 'agent_name': 'a', 'action_type': 'post'})
    log.write_text(f'{good}\nNOT JSON AT ALL\n{good}\n', encoding='utf-8')

    state = SimulationRunState(simulation_id='t', total_rounds=3)
    pos = SimulationRunner._read_action_log(str(log), 0, state, 'twitter')

    assert state.twitter_actions_count == 2
    assert pos == os.path.getsize(log)


def test_action_log_handles_simulation_end_event(tmp_path):
    log = tmp_path / 'twitter' / 'actions.jsonl'
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps({'event_type': 'simulation_end', 'total_rounds': 5}) + '\n',
        encoding='utf-8',
    )
    state = SimulationRunState(simulation_id='t', total_rounds=5)
    SimulationRunner._read_action_log(str(log), 0, state, 'twitter')
    assert state.twitter_completed is True


# ------------------------------------------------------- A3：孤儿进程回收

def test_reap_orphans_kills_detached_simulation_process(sim_root, tmp_path):
    """父进程没了、子进程还活着（start_new_session=True）时必须被回收"""
    script = tmp_path / 'run_orphan_simulation.py'
    script.write_text('import time\ntime.sleep(60)\n', encoding='utf-8')

    child = subprocess.Popen(
        [sys.executable, str(script)],
        start_new_session=True,  # 脱离本进程的进程组，模拟 debug 重载后的孤儿
    )
    try:
        _write_run_state(sim_root, 'sim_orphan', process_pid=child.pid)

        handled = SimulationRunner.reap_orphans()

        assert 'sim_orphan' in handled
        # 进程应被终止
        for _ in range(50):
            if child.poll() is not None:
                break
            time.sleep(0.1)
        assert child.poll() is not None

        after = _read_run_state(sim_root, 'sim_orphan')
        assert after['runner_status'] == RunnerStatus.STALLED.value
        assert after['twitter_running'] is False
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_reap_orphans_leaves_foreign_process_alone(sim_root, tmp_path):
    """安全阀：命令行不像模拟脚本的进程，绝不误杀"""
    child = subprocess.Popen(['sleep', '60'], start_new_session=True)
    try:
        _write_run_state(sim_root, 'sim_foreign', process_pid=child.pid)

        handled = SimulationRunner.reap_orphans()

        assert handled == []
        assert child.poll() is None  # 还活着
        # 状态保持不变
        assert _read_run_state(sim_root, 'sim_foreign')['runner_status'] == 'running'
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_reap_orphans_converges_dead_pid(sim_root):
    """状态写着 running 但进程早就没了：收敛成 stalled，别让前端一直转圈"""
    # PID 用一个几乎不可能存在的值
    _write_run_state(sim_root, 'sim_dead', process_pid=999_999_999)

    handled = SimulationRunner.reap_orphans()

    assert 'sim_dead' in handled
    assert _read_run_state(sim_root, 'sim_dead')['runner_status'] == RunnerStatus.STALLED.value


def test_reap_orphans_ignores_finished_and_registered(sim_root):
    """已结束的记录、以及本次进程自己登记的，都不该被动"""
    _write_run_state(sim_root, 'sim_done', runner_status='completed', process_pid=1)
    _write_run_state(sim_root, 'sim_mine', process_pid=1)
    SimulationRunner._processes['sim_mine'] = object()

    handled = SimulationRunner.reap_orphans()

    assert handled == []
    assert _read_run_state(sim_root, 'sim_done')['runner_status'] == 'completed'
    assert _read_run_state(sim_root, 'sim_mine')['runner_status'] == 'running'


# ------------------------------------------- B2 / B4：就绪判定

@pytest.fixture()
def sim_data_dir(tmp_path, monkeypatch):
    root = tmp_path / 'simdata'
    root.mkdir()
    monkeypatch.setattr(Config, 'OASIS_SIMULATION_DATA_DIR', str(root))
    return root


def _make_sim(root, sim_id, *, status, config_generated=True,
              enable_twitter=True, enable_reddit=True):
    d = root / sim_id
    d.mkdir(parents=True, exist_ok=True)
    (d / 'state.json').write_text(json.dumps({
        'simulation_id': sim_id,
        'status': status,
        'config_generated': config_generated,
        'enable_twitter': enable_twitter,
        'enable_reddit': enable_reddit,
        'entities_count': 5,
    }), encoding='utf-8')
    (d / 'simulation_config.json').write_text('{}', encoding='utf-8')
    if enable_reddit:
        (d / 'reddit_profiles.json').write_text('[]', encoding='utf-8')
    if enable_twitter:
        (d / 'twitter_profiles.csv').write_text('id,name\n1,a\n', encoding='utf-8')
    return d


def _check(sim_id):
    from app.api.simulation import _check_simulation_prepared
    return _check_simulation_prepared(sim_id)


def test_stopped_simulation_can_be_restarted(sim_data_dir):
    """B2：/stop 现在写 stopped，必须能通过就绪检查（否则停止后再也起不来）"""
    _make_sim(sim_data_dir, 'sim_stopped', status='stopped')
    ok, _ = _check('sim_stopped')
    assert ok is True


def test_legacy_paused_state_still_recognised(sim_data_dir):
    """B2 兼容：历史上 /stop 写的是 paused，那些记录也要能救回来"""
    _make_sim(sim_data_dir, 'sim_paused', status='paused')
    ok, _ = _check('sim_paused')
    assert ok is True


def test_stalled_simulation_is_prepared(sim_data_dir):
    """A2 新状态 stalled 同样属于「准备工作已完成」"""
    _make_sim(sim_data_dir, 'sim_stalled', status='stalled')
    ok, _ = _check('sim_stalled')
    assert ok is True


def test_single_platform_simulation_is_prepared(sim_data_dir):
    """B4：只开 twitter 时不该因为缺 reddit_profiles.json 而永远判为未就绪"""
    _make_sim(sim_data_dir, 'sim_tw_only', status='ready', enable_reddit=False)
    ok, info = _check('sim_tw_only')
    assert ok is True
    assert 'reddit_profiles.json' not in info['existing_files']

    _make_sim(sim_data_dir, 'sim_rd_only', status='ready', enable_twitter=False)
    ok, _ = _check('sim_rd_only')
    assert ok is True


def test_missing_profiles_still_blocks(sim_data_dir):
    """反向用例：真的缺人设文件时仍然要判未就绪"""
    d = _make_sim(sim_data_dir, 'sim_missing', status='ready')
    os.remove(d / 'reddit_profiles.json')
    ok, info = _check('sim_missing')
    assert ok is False
    assert 'reddit_profiles.json' in info['missing_files']


def test_not_generated_config_blocks(sim_data_dir):
    _make_sim(sim_data_dir, 'sim_noconfig', status='ready', config_generated=False)
    ok, _ = _check('sim_noconfig')
    assert ok is False
