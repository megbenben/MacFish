"""重建图谱前的「引用计数」（A7）。

重建会删掉旧图谱，而某些模拟的 state.json 还指着它 —— 删之前必须把这个数字
告诉用户，否则就是悄悄弄坏了别人正在用的东西。

不需要网络。
"""
import json
import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.api.graph import _count_graph_references  # noqa: E402
from app.config import Config  # noqa: E402


@pytest.fixture()
def sim_root(tmp_path, monkeypatch):
    root = tmp_path / 'simulations'
    root.mkdir()
    monkeypatch.setattr(Config, 'OASIS_SIMULATION_DATA_DIR', str(root))
    return root


def _write_sim(root, sim_id, graph_id):
    d = root / sim_id
    d.mkdir(parents=True, exist_ok=True)
    (d / 'state.json').write_text(json.dumps({'graph_id': graph_id}), encoding='utf-8')


def test_counts_simulations_pointing_at_the_graph(sim_root):
    _write_sim(sim_root, 'sim_a', 'g1')
    _write_sim(sim_root, 'sim_b', 'g1')
    _write_sim(sim_root, 'sim_c', 'other')
    assert _count_graph_references('g1') == 2
    assert _count_graph_references('other') == 1
    assert _count_graph_references('nobody') == 0


def test_tolerates_corrupt_state_files(sim_root):
    """坏掉的 state.json 不该让整个重建流程报错，只当它不存在"""
    d = sim_root / 'sim_broken'
    d.mkdir(parents=True)
    (d / 'state.json').write_text('{ not json', encoding='utf-8')
    _write_sim(sim_root, 'sim_ok', 'g1')

    assert _count_graph_references('g1') == 1


def test_empty_or_missing_input(sim_root):
    assert _count_graph_references('') == 0

    # 目录整个不存在时也不能抛
    assert _count_graph_references('g1') == 0


def test_missing_directory_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, 'OASIS_SIMULATION_DATA_DIR', str(tmp_path / 'nope'))
    assert _count_graph_references('g1') == 0
