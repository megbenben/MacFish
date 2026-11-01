"""模拟结束后采访应回退到落盘证据，而不是直接失败。"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.config import Config  # noqa: E402
from app.services.simulation_runner import SimulationRunner  # noqa: E402
from app.services.zep_tools import ZepToolsService  # noqa: E402


class _FakeLLM:
    def chat(self, messages, **kwargs):
        return "【回放模式】我一直主张先稳住价格，再谈份额。"

    def chat_json(self, messages, **kwargs):
        return {}


@pytest.fixture()
def sim_dir(tmp_path, monkeypatch):
    root = tmp_path / 'simulations'
    target = root / 'sim_test'
    (target / 'twitter').mkdir(parents=True)
    monkeypatch.setattr(Config, 'OASIS_SIMULATION_DATA_DIR', str(root))

    (target / 'state.json').write_text(json.dumps({
        'simulation_id': 'sim_test', 'status': 'completed',
        'enable_twitter': True, 'enable_reddit': False,
        'entities_count': 1, 'profiles_count': 1,
    }), encoding='utf-8')
    (target / 'reddit_profiles.json').write_text(json.dumps([
        {'user_id': 0, 'username': 'alice', 'name': 'Alice',
         'bio': '品牌负责人', 'persona': '重视长期品牌价值。', 'profession': '品牌总监'}
    ], ensure_ascii=False), encoding='utf-8')
    with open(target / 'twitter' / 'actions.jsonl', 'w', encoding='utf-8') as handle:
        handle.write(json.dumps({
            'round': 0, 'agent_id': 0, 'agent_name': 'Alice',
            'action_type': 'CREATE_POST',
            'action_args': {'content': '降价会伤害品牌，我们应该先稳住价格。'},
        }, ensure_ascii=False) + '\n')
    return target


def test_interview_falls_back_to_persisted_evidence(sim_dir, monkeypatch):
    def _offline(*args, **kwargs):
        raise ValueError('模拟环境未运行')

    monkeypatch.setattr(SimulationRunner, 'interview_agents_batch', staticmethod(_offline))

    service = ZepToolsService(llm_client=_FakeLLM())
    monkeypatch.setattr(
        service, '_select_agents_for_interview',
        lambda **kwargs: ([{'realname': 'Alice', 'username': 'alice', 'bio': '品牌负责人'}],
                          [0], 'test selection'),
    )

    result = service.interview_agents(
        simulation_id='sim_test',
        interview_requirement='怎么看降价？',
        custom_questions=['你会降价吗？'],
        max_agents=1,
    )

    assert result.interviewed_count == 1
    assert result.interviews
    assert result.interviews[0].agent_name == 'Alice'
    assert '【回放模式】' in result.interviews[0].response
    assert '模拟环境未运行' in result.summary
