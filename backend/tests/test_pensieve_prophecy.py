"""冥想盆 / 水晶预言球的离线回归测试。

不需要网络：LLM 客户端被替换成一构造就抛错的假对象，所有路径都必须走
确定性降级。测试数据全部建在 tmp_path 下，不碰用户的 uploads/。
"""
import json
import os
import sqlite3
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.config import Config  # noqa: E402
from app.models.project import ProjectManager  # noqa: E402
from app.services.local_graph_store import (  # noqa: E402
    LocalEdge, LocalGraphStore, LocalNode,
)
from app.services.pensieve_service import PensieveService  # noqa: E402
from app.services.pensieve_store import PensieveStore  # noqa: E402
from app.services.prophecy_service import ProphecyService  # noqa: E402
from app.services.prophecy_store import ProphecyStore  # noqa: E402
from app.services.report_agent import ReportManager  # noqa: E402
from app.services.simulation_manager import SimulationManager  # noqa: E402


class _OfflineLLM:
    """任何构造尝试都失败，证明服务没有偷偷联网。"""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("offline test: LLM disabled")


@pytest.fixture()
def artifacts(tmp_path, monkeypatch):
    uploads = tmp_path / 'uploads'
    projects = uploads / 'projects'
    simulations = uploads / 'simulations'
    reports = uploads / 'reports'
    for path in (projects, simulations, reports):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(Config, 'UPLOAD_FOLDER', str(uploads))
    # 这三个目录是 import 时算好的类属性，只改 Config 不够
    monkeypatch.setattr(ProjectManager, 'PROJECTS_DIR', str(projects))
    monkeypatch.setattr(ReportManager, 'REPORTS_DIR', str(reports))
    monkeypatch.setattr(SimulationManager, 'SIMULATION_DATA_DIR', str(simulations))
    PensieveStore._instances.clear()
    ProphecyStore._instances.clear()

    # ---- 项目 ----
    project_dir = projects / 'proj_test'
    project_dir.mkdir()
    (project_dir / 'project.json').write_text(json.dumps({
        'project_id': 'proj_test',
        'name': 'ROTD 测试项目',
        'status': 'graph_completed',
        'created_at': '2026-01-01T00:00:00',
        'updated_at': '2026-01-01T00:00:00',
        'files': [{
            'original_filename': 'seed.md',
            'saved_filename': 'saved_seed.md',
            'path': str(project_dir / 'files' / 'saved_seed.md'),
            'size': 128,
        }],
        'total_text_length': 600,
        'ontology': {
            'entity_types': [{'name': 'Organization', 'description': '组织'}],
            'edge_types': [{'name': 'HAS_METRIC', 'description': '拥有指标'}],
        },
        'analysis_summary': '分析摘要：ROTD 的提升依赖可靠性与主数据治理。',
        'graph_id': 'graph_test',
        'simulation_requirement': '如何把 ROTD 从 85% 提升到 95%？',
        'chunk_size': 500,
        'chunk_overlap': 50,
        'error': None,
    }, ensure_ascii=False), encoding='utf-8')
    (project_dir / 'extracted_text.txt').write_text(
        'ELDS 希望把端到端 ROTD 从 85% 提升到 95%。可靠性必须嵌入承诺与供给决策。\n'
        '主数据治理是第 0 环节，主数据不干净会让 ROTD 的改善无法持续。',
        encoding='utf-8')

    # ---- 图谱 ----
    graph_store = LocalGraphStore(str(uploads / 'graphs.db'))
    graph_store.create_graph('graph_test', name='测试图谱')
    graph_store.upsert_node(LocalNode(
        uuid_='n1', name='ELDS', labels=['Organization'],
        summary='ELDS 是制造组织，关注端到端 ROTD。',
        attributes={'role': 'MU'}, graph_id='graph_test'))
    graph_store.upsert_node(LocalNode(
        uuid_='n2', name='ROTD', labels=['Concept'],
        summary='ROTD 是准时交付率指标。',
        attributes={}, graph_id='graph_test'))
    graph_store.upsert_edge(LocalEdge(
        uuid_='e1', name='HAS_METRIC', fact='ELDS 关注 ROTD',
        source_node_uuid='n1', target_node_uuid='n2',
        attributes={}, graph_id='graph_test'))

    # ---- 模拟 ----
    sim_dir = simulations / 'sim_test'
    (sim_dir / 'twitter').mkdir(parents=True)
    (sim_dir / 'state.json').write_text(json.dumps({
        'simulation_id': 'sim_test',
        'project_id': 'proj_test',
        'graph_id': 'graph_test',
        'status': 'completed',
        'entities_count': 2,
        'profiles_count': 1,
        'entity_types': ['Organization', 'Concept'],
        'config_reasoning': '围绕 ROTD 提升与主数据治理展开。',
        'created_at': '2026-01-01T02:00:00',
    }, ensure_ascii=False), encoding='utf-8')
    (sim_dir / 'simulation_config.json').write_text(json.dumps({
        'simulation_requirement': '如何把 ROTD 从 85% 提升到 95%？',
    }, ensure_ascii=False), encoding='utf-8')
    (sim_dir / 'reddit_profiles.json').write_text(json.dumps([{
        'user_id': 1,
        'name': 'Alice',
        'username': 'alice',
        'bio': '供应链专家，关注 ROTD 与主数据。',
        'persona': 'Alice 习惯用数据说话，强调可靠性与月度复盘。',
        'profession': 'Supply Chain Manager',
        'interested_topics': ['ROTD', '主数据'],
    }], ensure_ascii=False), encoding='utf-8')
    actions = [
        {'round': 0, 'timestamp': '2026-01-01T03:00:00', 'event_type': 'simulation_start'},
        {'round': 0, 'timestamp': '2026-01-01T03:00:01', 'agent_id': 1,
         'agent_name': 'Alice', 'action_type': 'CREATE_POST',
         'action_args': {'content': 'ROTD 要从 85% 提升到 95%，关键是可靠性。'}},
        {'round': 1, 'timestamp': '2026-01-01T04:00:00', 'agent_id': 1,
         'agent_name': 'Alice', 'action_type': 'CREATE_COMMENT',
         'action_args': {'content': '我同意，ELDS 需要先治理主数据。'}},
        {'round': 2, 'timestamp': '2026-01-01T05:00:00', 'agent_id': 1,
         'agent_name': 'Alice', 'action_type': 'DO_NOTHING', 'action_args': {}},
    ]
    (sim_dir / 'twitter' / 'actions.jsonl').write_text(
        '\n'.join(json.dumps(line, ensure_ascii=False) for line in actions),
        encoding='utf-8')

    db_path = sim_dir / 'twitter_simulation.db'
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE user (user_id INTEGER PRIMARY KEY, name TEXT, bio TEXT);
        CREATE TABLE trace (
            user_id INTEGER, created_at DATETIME, action TEXT, info TEXT,
            PRIMARY KEY(user_id, created_at, action, info)
        );
        CREATE TABLE post (
            post_id INTEGER PRIMARY KEY, user_id INTEGER, content TEXT,
            created_at DATETIME, num_likes INTEGER DEFAULT 0,
            num_shares INTEGER DEFAULT 0
        );
    """)
    conn.execute("INSERT INTO user VALUES (1, 'Alice', '供应链专家')")
    conn.execute(
        "INSERT INTO trace VALUES (1, '2026-01-01T06:00:00', 'interview', ?)",
        (json.dumps({'prompt': '你怎么看 ROTD？', 'response': '先做可靠性，再谈速度。',
                     'interview_id': 'iv1'}, ensure_ascii=False),)
    )
    conn.execute(
        "INSERT INTO post VALUES (1, 1, 'ROTD 需要可靠性支撑', '2026-01-01', 5, 2)")
    conn.commit()
    conn.close()

    # ---- 报告 ----
    report_dir = reports / 'report_test'
    report_dir.mkdir()
    (report_dir / 'meta.json').write_text(json.dumps({
        'report_id': 'report_test',
        'simulation_id': 'sim_test',
        'graph_id': 'graph_test',
        'simulation_requirement': '如何把 ROTD 从 85% 提升到 95%？',
        'status': 'completed',
        'outline': {
            'title': 'ROTD 未来预演',
            'summary': 'ROTD 的提升取决于可靠性与主数据治理。',
            'sections': [
                {'title': '未来状态', 'content': 'ROTD 将从 85% 提升到 95%，可靠性是核心。'},
                {'title': '风险', 'content': '主数据不干净会拖累 ROTD 的持续改善。'},
            ],
            'scenarios': [
                {'name': '稳步提升', 'description': '可靠性嵌入承诺，ROTD 达标。',
                 'trigger_conditions': ['月度评审连续两次达标'],
                 'relative_likelihood': '高', 'key_actors': ['ELDS', 'Alice']},
                {'name': '治理滞后', 'description': '主数据问题导致提升停滞。',
                 'trigger_conditions': ['主数据缺陷率上升'],
                 'relative_likelihood': '中', 'key_actors': ['ELDS']},
            ],
        },
        'markdown_content': '# ROTD 未来预演',
        'created_at': '2026-01-02T00:00:00',
        'completed_at': '2026-01-02T01:00:00',
        'error': None,
    }, ensure_ascii=False), encoding='utf-8')

    # 坏报告：JSON 解析失败，收割必须跳过它而不是整体失败
    bad_dir = reports / 'report_bad'
    bad_dir.mkdir()
    (bad_dir / 'meta.json').write_text('{not valid json', encoding='utf-8')

    return {
        'uploads': uploads,
        'project_id': 'proj_test',
        'simulation_id': 'sim_test',
        'report_id': 'report_test',
    }


# --------------------------------------------------------------------- store
def test_store_roundtrip_and_dedupe(artifacts):
    store = PensieveStore.get_instance()
    payload = {
        'external_key': 'test:key',
        'kind': 'seed',
        'title': '测试记忆',
        'content': 'ROTD 与可靠性',
        'summary': '摘要',
        'tags': ['seed'],
        'entities': ['ROTD'],
        'project_id': 'proj_test',
    }
    first, created = store.upsert_memory(payload)
    assert created is True
    second, created_again = store.upsert_memory({**payload, 'content': '更新后的内容'})
    assert created_again is False
    assert second['memory_id'] == first['memory_id']
    assert second['content'] == '更新后的内容'

    assert store.count_memories(q='更新后的内容') == 1
    assert store.count_memories(kind='seed') == 1
    assert store.list_memories(q='不存在') == []


# ------------------------------------------------------------------- harvest
def test_harvest_is_idempotent_and_covers_all_kinds(artifacts):
    result = PensieveService.harvest('all')
    assert result['created'] > 0
    kinds = set(result['by_kind'])
    assert {'seed', 'world', 'entity', 'agent', 'episode', 'voice', 'prophecy'} <= kinds

    total = result['total']
    again = PensieveService.harvest('all')
    assert again['created'] == 0
    assert again['updated'] > 0
    assert again['total'] == total

    store = PensieveStore.get_instance()
    assert store.stats()['strands'] > 0
    assert store.count_memories(report_id='report_bad') == 0

    # 检索与详情
    memories = store.list_memories(q='ROTD', limit=50)
    assert memories
    detail = PensieveService.get_memory_detail(memories[0]['memory_id'])
    assert detail is not None
    assert 'strands' in detail

    network = store.get_network(limit=300)
    assert network['nodes']
    assert 'edges' in network


def test_replay_and_distill_fallback(artifacts, monkeypatch):
    PensieveService.harvest('all')
    memory = PensieveStore.get_instance().list_memories(q='ROTD', limit=1)[0]

    scene = PensieveService.replay(memory['memory_id'])
    assert scene is not None
    assert scene['narration']
    assert scene['timeline']

    monkeypatch.setattr('app.services.pensieve_service.LLMClient', _OfflineLLM)
    second = PensieveStore.get_instance().list_memories(kind='prophecy', limit=1)[0]
    vial = PensieveService.distill([memory['memory_id'], second['memory_id']],
                                    '这些记忆共同说明了什么？')
    assert vial['used_llm'] is False
    assert vial['content']
    assert PensieveStore.get_instance().get_vial(vial['vial_id']) is not None

    insight = PensieveStore.get_instance().get_memory(vial['memory_id'])
    assert insight is not None
    assert insight['kind'] == 'insight'


# ------------------------------------------------------------------ prophecy
def test_prophecy_lifecycle(artifacts, monkeypatch):
    monkeypatch.setattr('app.services.prophecy_service.LLMClient', _OfflineLLM)
    PensieveService.harvest('all')

    result = ProphecyService.forge(artifacts['report_id'])
    assert result['created'] is True
    assert result['used_llm'] is False
    orb = result['orb']
    assert orb['status'] == 'sealed'
    assert orb['source_report_id'] == artifacts['report_id']
    assert len(orb['branches']) == 2
    assert len(orb['indicators']) >= 2
    assert 55 <= orb['confidence'] <= 85
    assert orb['deadline']

    again = ProphecyService.forge(artifacts['report_id'])
    assert again['created'] is False
    assert again['orb']['orb_id'] == orb['orb_id']

    opened = ProphecyService.open_orb(orb['orb_id'])
    assert opened['status'] == 'opened'
    assert opened['opened_at']

    scry = ProphecyService.scry(orb['orb_id'], 'ROTD 能达标吗？')
    assert scry['answer']
    assert scry['used_llm'] is False
    assert len(scry['orb']['scryings']) == 1

    verified = ProphecyService.verify(orb['orb_id'], 'ROTD 提升到 95%，月度评审连续达标。',
                                      verdict='fulfilled', accuracy=82)
    assert verified['status'] == 'fulfilled'
    assert verified['accuracy'] == 82
    assert verified['verified_at']

    stats = ProphecyStore.get_instance().stats()
    assert stats['total'] == 1
    assert stats['fulfilled'] == 1
    assert stats['average_accuracy'] == 82.0

    # 预言球与应验结果都回流到了冥想盆
    store = PensieveStore.get_instance()
    assert store.get_memory_by_external_key(f"orb:{orb['orb_id']}") is not None
    assert store.get_memory_by_external_key(f"orb-verdict:{orb['orb_id']}") is not None
    assert any(m['kind'] == 'voice' for m in store.list_memories(kind='voice'))


def test_verify_derives_verdict_and_blocks_sealed(artifacts, monkeypatch):
    monkeypatch.setattr('app.services.prophecy_service.LLMClient', _OfflineLLM)
    orb = ProphecyService.forge(artifacts['report_id'])['orb']

    with pytest.raises(PermissionError):
        ProphecyService.scry(orb['orb_id'], '现在能问吗？')
    with pytest.raises(PermissionError):
        ProphecyService.verify(orb['orb_id'], '结果')

    ProphecyService.open_orb(orb['orb_id'])
    partial = ProphecyService.verify(orb['orb_id'], '部分达成', accuracy=40)
    assert partial['status'] == 'partial'
    shattered = ProphecyService.verify(orb['orb_id'], '完全偏离', accuracy=10)
    assert shattered['status'] == 'shattered'


def test_forge_rejects_path_traversal(artifacts):
    with pytest.raises(ValueError):
        ProphecyService.forge('../etc/passwd')
    with pytest.raises(ValueError):
        ProphecyService.forge('..')


def test_replay_returns_source_citations(artifacts):
    PensieveService.harvest('all')
    store = PensieveStore.get_instance()

    episode = store.list_memories(kind='episode', limit=1)[0]
    scene = PensieveService.replay(episode['memory_id'])
    assert scene is not None
    assert scene['citations']
    assert scene['citation_count'] == len(scene['citations'])
    first = scene['timeline'][0]['citation']
    assert first['memory_id']
    assert first['route'].startswith('/')
    assert first['label']

    # 报告章节的引用要带上章节号，而不是只给一个 report_id
    section = store.list_memories(q='未来状态', limit=1)[0]
    section_scene = PensieveService.replay(section['memory_id'])
    labels = [citation['label'] for citation in section_scene['citations']]
    assert any('第 1 章' in label for label in labels)
    assert all(citation['route'] for citation in section_scene['citations'])


def test_auto_harvest_is_idempotent_and_skips_unfinished_simulations(artifacts):
    store = PensieveStore.get_instance()

    # 模拟已结束（state.json completed）时自动收存一次
    assert PensieveService.auto_harvest_simulation(
        artifacts['simulation_id'], background=False) is True
    assert store.count_memories(simulation_id=artifacts['simulation_id']) > 0
    marker = artifacts['uploads'] / 'simulations' / 'sim_test' / '.pensieve_harvested'
    assert marker.exists()
    assert PensieveService.auto_harvest_simulation(
        artifacts['simulation_id'], background=False) is False

    # 单目标自动收存只处理该报告，不要求全量扫描
    report_result = PensieveService.auto_harvest(
        'report', artifacts['report_id'], background=False)
    assert report_result is not None
    assert report_result['created'] > 0

    # 未结束的模拟不能写标记
    unfinished = artifacts['uploads'] / 'simulations' / 'sim_running'
    unfinished.mkdir()
    (unfinished / 'state.json').write_text(json.dumps({
        'simulation_id': 'sim_running', 'status': 'running'}), encoding='utf-8')
    assert PensieveService.auto_harvest_simulation(
        'sim_running', background=False) is False
    assert not (unfinished / '.pensieve_harvested').exists()


def test_prophecy_reminders_cover_due_and_overdue(artifacts, monkeypatch):
    from datetime import date, timedelta

    monkeypatch.setattr('app.services.prophecy_service.LLMClient', _OfflineLLM)
    orb = ProphecyService.forge(artifacts['report_id'])['orb']
    orb_id = orb['orb_id']

    # 默认 90 天期限，不在 7 天提醒窗口内
    assert ProphecyService.reminders(7)['count'] == 0

    store = ProphecyStore.get_instance()
    raw = store.get_orb(orb_id)
    raw['deadline'] = (date.today() - timedelta(days=2)).isoformat()
    store.upsert_orb(raw)

    reminders = ProphecyService.reminders(7)
    assert reminders['count'] == 1
    assert reminders['overdue'] == 1
    assert reminders['items'][0]['days_left'] == -2
    assert reminders['items'][0]['deadline_state'] == 'overdue'

    decorated = ProphecyService.get_orb(orb_id)
    assert decorated['days_left'] == -2
    assert decorated['deadline_state'] == 'overdue'
    assert ProphecyService.list_orbs()[0]['deadline_state'] == 'overdue'

    # 已应验/破碎的球不再提醒
    ProphecyService.open_orb(orb_id)
    ProphecyService.verify(orb_id, '实际结果', verdict='fulfilled', accuracy=100)
    assert ProphecyService.reminders(7)['count'] == 0

    overview = ProphecyService.overview()
    assert 'reminders' in overview
    assert overview['reminders']['count'] == 0


def test_blueprints_are_registered():
    from app import create_app
    app = create_app()
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert '/api/pensieve/overview' in rules
    assert '/api/pensieve/harvest' in rules
    assert '/api/prophecy/overview' in rules
    assert '/api/prophecy/reminders' in rules
    assert '/api/prophecy/orbs/forge' in rules


def test_prophecy_calibration_uses_confidence_and_excludes_partial(artifacts, monkeypatch):
    monkeypatch.setattr('app.services.prophecy_service.LLMClient', _OfflineLLM)
    orb = ProphecyService.forge(artifacts['report_id'])['orb']
    ProphecyService.open_orb(orb['orb_id'])
    ProphecyService.verify(orb['orb_id'], '结果达成', verdict='fulfilled')

    calibration = ProphecyService.calibration()
    assert calibration['n'] == 1
    expected = (orb['confidence'] / 100.0 - 1.0) ** 2
    assert calibration['brier_score'] == pytest.approx(expected, abs=1e-4)
    assert calibration['reliability']

    # partial 不是二值结果，必须排除
    ProphecyService.verify(orb['orb_id'], '部分达成', verdict='partial')
    assert ProphecyService.calibration()['excluded_partial'] == 1
