"""模拟证据服务与引用登记的离线测试。"""

import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.config import Config  # noqa: E402
from app.services.simulation_evidence import (  # noqa: E402
    CitationRegistry, SimulationEvidenceService,
)


@pytest.fixture()
def sim(tmp_path, monkeypatch):
    root = tmp_path / 'simulations'
    sim_dir = root / 'sim_test'
    (sim_dir / 'twitter').mkdir(parents=True)
    monkeypatch.setattr(Config, 'OASIS_SIMULATION_DATA_DIR', str(root))

    (sim_dir / 'state.json').write_text(json.dumps({
        'simulation_id': 'sim_test',
        'project_id': 'proj_test',
        'graph_id': 'graph_test',
        'status': 'completed',
        'entities_count': 12,
        'profiles_count': 12,
        'enable_twitter': True,
        'enable_reddit': False,
    }), encoding='utf-8')
    (sim_dir / 'simulation_config.json').write_text(json.dumps({
        'time_config': {'total_simulation_hours': 24, 'minutes_per_round': 60}
    }), encoding='utf-8')

    actions = [
        {'event_type': 'simulation_start', 'platform': 'twitter'},
        {'round': 0, 'event_type': 'round_start'},
        {'round': 0, 'agent_id': 1, 'agent_name': 'Alice',
         'action_type': 'CREATE_POST', 'action_args': {'content': '我们考虑降价 10%'}},
        {'round': 1, 'agent_id': 2, 'agent_name': 'Bob',
         'action_type': 'CREATE_COMMENT', 'action_args': {'content': '降价会伤害品牌'}},
        {'round': 2, 'agent_id': 1, 'agent_name': 'Alice',
         'action_type': 'LIKE_POST', 'action_args': {'post_id': 1}},
    ]
    with open(sim_dir / 'twitter' / 'actions.jsonl', 'w', encoding='utf-8') as handle:
        for item in actions:
            handle.write(json.dumps(item, ensure_ascii=False) + '\n')

    conn = sqlite3.connect(sim_dir / 'twitter_simulation.db')
    conn.executescript("""
        CREATE TABLE user (user_id INTEGER, agent_id INTEGER, user_name TEXT, name TEXT);
        CREATE TABLE post (post_id INTEGER, user_id INTEGER, content TEXT, quote_content TEXT,
                           created_at TEXT, num_likes INTEGER, num_dislikes INTEGER, num_shares INTEGER);
        CREATE TABLE comment (comment_id INTEGER, post_id INTEGER, user_id INTEGER, content TEXT,
                              created_at TEXT, num_likes INTEGER, num_dislikes INTEGER);
    """)
    conn.execute("INSERT INTO user VALUES (1, 1, 'alice', 'Alice')")
    conn.execute("INSERT INTO user VALUES (2, 2, 'bob', 'Bob')")
    conn.execute(
        "INSERT INTO post VALUES (1, 1, '我们考虑降价 10%', '', '2026-01-01', 5, 0, 2)"
    )
    conn.execute(
        "INSERT INTO comment VALUES (1, 1, 2, '降价会伤害品牌', '2026-01-01', 3, 0)"
    )
    conn.commit()
    conn.close()
    return sim_dir


def test_overview_reads_posts_actions_and_profiles(sim):
    data = SimulationEvidenceService.overview('sim_test')
    assert data['status'] == 'completed'
    assert data['platforms'] == ['twitter']
    assert data['time_config']['planned_rounds'] == 24
    assert data['per_platform']['twitter']['posts'] == 1
    assert data['per_platform']['twitter']['comments'] == 1
    # 5 行里 simulation_start / round_start 被过滤，剩 3 条动作
    assert data['per_platform']['twitter']['actions'] == 3
    text = SimulationEvidenceService.overview_text('sim_test')
    assert '24h' in text and 'Alice' in text


def test_search_ranks_matching_content_and_dedupes(sim):
    items = SimulationEvidenceService.search('sim_test', '降价', limit=10)
    assert items
    assert all('降价' in item['content'] for item in items)
    # DB post 和 CREATE_POST 动作内容相同，只保留一条
    assert len(items) == 2
    assert {item['kind'] for item in items} == {'post', 'comment'}


def test_citation_registry_reuses_ids():
    registry = CitationRegistry()
    first = registry.register({'source_id': 'twitter:post:1', 'content': 'a'})
    again = registry.register({'source_id': 'twitter:post:1', 'content': 'a'})
    second = registry.register({'source_id': 'twitter:post:2', 'content': 'b'})
    assert first == again == 'S1'
    assert second == 'S2'
    assert [item['citation_id'] for item in registry.items()] == ['S1', 'S2']


def test_report_agent_simulation_tool_registers_citations(sim):
    from app.services.report_agent import ReportAgent
    from app.services.zep_tools import ZepToolsService

    class _FakeLLM:
        def chat(self, *args, **kwargs):
            return ''

    agent = ReportAgent(
        graph_id='graph_test',
        simulation_id='sim_test',
        simulation_requirement='测试',
        llm_client=_FakeLLM(),
        zep_tools=ZepToolsService(llm_client=_FakeLLM()),
    )
    text = agent._execute_tool('simulation_search', {'query': '降价', 'limit': 5})
    assert '[S1]' in text
    assert '降价' in text
    assert len(agent._citation_registry) >= 1
    overview = agent._execute_tool('simulation_overview', {})
    assert '24h' in overview


def test_news_digest_orders_by_engagement(sim):
    news = SimulationEvidenceService.news_digest('sim_test', limit=5)
    assert news['count'] >= 2
    assert news['headlines'][0]['likes'] >= news['headlines'][-1]['likes']
    assert all(item['title'] for item in news['headlines'])
