"""预测市场（LMSR）与模拟分支 / A-B 对比的离线测试。"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.config import Config  # noqa: E402
from app.services.market_simulator import (  # noqa: E402
    MarketSimulator, lmsr_price, stance_score,
)
from app.services.simulation_fork import SimulationForkService  # noqa: E402
from app.services.simulation_manager import SimulationManager, SimulationStatus  # noqa: E402


@pytest.fixture()
def sim_root(tmp_path, monkeypatch):
    root = tmp_path / 'simulations'
    root.mkdir()
    monkeypatch.setattr(Config, 'OASIS_SIMULATION_DATA_DIR', str(root))
    monkeypatch.setattr(SimulationManager, 'SIMULATION_DATA_DIR', str(root))
    return root


def _make_sim(root, sim_id='sim_source', requirement='价格战会继续吗？',
              enable_market=True):
    sim_dir = root / sim_id
    (sim_dir / 'twitter').mkdir(parents=True, exist_ok=True)
    (sim_dir / 'state.json').write_text(json.dumps({
        'simulation_id': sim_id, 'project_id': 'proj', 'graph_id': 'graph',
        'status': 'ready', 'enable_twitter': True, 'enable_reddit': False,
        'enable_market': enable_market, 'entities_count': 2, 'profiles_count': 2,
    }, ensure_ascii=False), encoding='utf-8')
    (sim_dir / 'simulation_config.json').write_text(json.dumps({
        'simulation_id': sim_id, 'project_id': 'proj', 'graph_id': 'graph',
        'simulation_requirement': requirement,
        'time_config': {'total_simulation_hours': 24, 'minutes_per_round': 60},
        'agent_configs': [{'agent_id': 0, 'entity_name': 'A'}, {'agent_id': 1, 'entity_name': 'B'}],
    }, ensure_ascii=False), encoding='utf-8')
    (sim_dir / 'reddit_profiles.json').write_text('[]', encoding='utf-8')
    (sim_dir / 'twitter_profiles.csv').write_text('user_id,name\n0,A\n', encoding='utf-8')
    return sim_dir


def _append_actions(sim_dir, platform='twitter', rows=None):
    path = sim_dir / platform / 'actions.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as handle:
        for row in rows or []:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')


def test_lmsr_and_stance_are_deterministic():
    assert lmsr_price(0, 0, 20) == pytest.approx(0.5)
    assert lmsr_price(20, 0, 20) > 0.7
    assert lmsr_price(0, 20, 20) < 0.3
    assert stance_score('需求增长，项目成功落地') > 0
    assert stance_score('项目失败，需求下降') < 0
    assert stance_score('今天开会') == 0


def test_market_processes_actions_incrementally(sim_root):
    sim_dir = _make_sim(sim_root)
    _append_actions(sim_dir, 'twitter', [
        {'round': 0, 'agent_name': 'A', 'action_type': 'CREATE_POST',
         'action_args': {'content': '需求增长，项目成功落地，利好明显'}},
        {'round': 0, 'agent_name': 'B', 'action_type': 'CREATE_POST',
         'action_args': {'content': '项目失败，需求下降，风险很大'}},
        {'round': 0, 'agent_name': 'A', 'action_type': 'CREATE_POST',
         'action_args': {'content': '今天开会'}},
    ])
    first = MarketSimulator.process('sim_source')
    assert first['enabled'] is True
    assert first['trades_count'] == 2
    assert first['volume'] == 6
    assert 0 < first['price_yes'] < 1
    # 正负各一笔，价格仍在 0.5 附近
    assert first['price_yes'] == pytest.approx(0.5, abs=0.05)

    again = MarketSimulator.process('sim_source')
    assert again['trades_count'] == 2  # 游标没前进，不重复计

    _append_actions(sim_dir, 'twitter', [
        {'round': 1, 'agent_name': 'A', 'action_type': 'CREATE_POST',
         'action_args': {'content': '突破成功，增长加速，达成目标'}},
    ])
    third = MarketSimulator.process('sim_source')
    assert third['trades_count'] == 3
    assert third['price_yes'] == pytest.approx(third['price_yes'])
    assert len(third['history']) >= 2


def test_market_respects_enable_flag(sim_root):
    _make_sim(sim_root, enable_market=False)
    result = MarketSimulator.overview('sim_source')
    assert result['enabled'] is False


def test_fork_copies_config_and_records_metadata(sim_root):
    _make_sim(sim_root)
    result = SimulationForkService.fork(
        'sim_source', label='监管分支', event='监管介入', event_round=3,
        auto_start=False,
    )
    assert result['simulation_id'] != 'sim_source'
    fork = result['fork']
    assert fork['parent_simulation_id'] == 'sim_source'
    assert fork['event_round'] == 3
    assert fork['injection_status'] == 'pending'

    new_dir = sim_root / result['simulation_id']
    assert (new_dir / 'simulation_config.json').is_file()
    assert (new_dir / 'fork.json').is_file()
    loaded = SimulationManager().get_simulation(result['simulation_id'])
    assert loaded.status == SimulationStatus.READY
    assert loaded.enable_market is True
    assert loaded.profiles_count == 2


def test_compare_reports_action_delta(sim_root):
    source = _make_sim(sim_root)
    fork = SimulationForkService.fork('sim_source', label='b', auto_start=False)
    fork_dir = sim_root / fork['simulation_id']
    _append_actions(source, 'twitter', [
        {'round': 0, 'agent_name': 'A', 'action_type': 'CREATE_POST', 'action_args': {'content': '增长'}},
    ])
    _append_actions(fork_dir, 'twitter', [
        {'round': 0, 'agent_name': 'A', 'action_type': 'CREATE_POST', 'action_args': {'content': '增长'}},
        {'round': 1, 'agent_name': 'B', 'action_type': 'CREATE_POST', 'action_args': {'content': '下跌'}},
    ])
    result = SimulationForkService.compare('sim_source', fork['simulation_id'])
    assert result['a']['actions']['total_actions'] == 1
    assert result['b']['actions']['total_actions'] == 2
    assert result['delta']['actions'] == 1
