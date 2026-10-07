"""人设生成阶段的成本护栏（A5）。

人设阶段是「每个实体一次 LLM 调用」，2000 个实体就是 2000 次 —— 而这一步在
/prepare 里直接开跑，此前没有任何事前提示。这里守住判定逻辑。

不需要网络。
"""
import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.api.simulation import _prepare_budget_guard  # noqa: E402
from app.services.cost_estimator import AGENTS_PER_BATCH, CONFIG_FIXED_CALLS  # noqa: E402


def _expected_prepare_calls(entity_count):
    import math
    return entity_count + CONFIG_FIXED_CALLS + math.ceil(entity_count / AGENTS_PER_BATCH)


def test_no_budget_means_no_guard():
    allowed, detail = _prepare_budget_guard(entity_count=5000, budget_max=0, allow_over_budget=False)
    assert allowed is True
    assert detail is None


def test_within_budget_is_allowed():
    # 20 个实体 ≈ 20 + 2 + 2 = 24 次，预算给 100
    allowed, detail = _prepare_budget_guard(entity_count=20, budget_max=100, allow_over_budget=False)
    assert allowed is True
    assert detail is None


def test_over_budget_is_blocked_with_details():
    """这就是实测踩到的场景：2000 个实体、默认不限预算改成一个很小的值"""
    allowed, detail = _prepare_budget_guard(entity_count=2000, budget_max=100, allow_over_budget=False)

    assert allowed is False
    assert detail['need_confirm'] is True
    assert detail['stage'] == 'profiles'
    assert detail['entity_count'] == 2000
    assert detail['limit'] == 100
    assert detail['estimated_calls'] == _expected_prepare_calls(2000)
    assert detail['estimated_calls'] > 2000  # 至少是「每实体一次」


def test_explicit_opt_in_bypasses_the_guard():
    allowed, detail = _prepare_budget_guard(entity_count=2000, budget_max=100, allow_over_budget=True)
    assert allowed is True
    assert detail is None


def test_zero_entities_is_allowed_through():
    """还没读到实体时不该拦（数量未知，交给后台任务再算）"""
    allowed, detail = _prepare_budget_guard(entity_count=0, budget_max=10, allow_over_budget=False)
    assert allowed is True
    assert detail is None


def test_boundary_is_exclusive():
    """刚好等于上限时放行，只拦「超过」"""
    limit = _expected_prepare_calls(30)
    allowed, _ = _prepare_budget_guard(entity_count=30, budget_max=limit, allow_over_budget=False)
    assert allowed is True

    allowed, detail = _prepare_budget_guard(entity_count=30, budget_max=limit - 1, allow_over_budget=False)
    assert allowed is False


def test_estimate_failure_does_not_block(monkeypatch):
    """预估器出错时应放行而不是拦住整个流程"""
    from app.api import simulation as sim_module
    from app.services import cost_estimator

    def boom(**kwargs):
        raise RuntimeError('estimator exploded')

    monkeypatch.setattr(cost_estimator, 'estimate_calls', boom)
    allowed, detail = sim_module._prepare_budget_guard(
        entity_count=100, budget_max=1, allow_over_budget=False
    )
    assert allowed is True
    assert detail is None
