"""
回测框架的单元测试（不联网、不调用 LLM）

这里放的是**适合隔离测试**的部分：防泄漏的五项检查各自能单独失败、稳定性度量的
数学、记分卡的聚合与合格判定。端到端的编排路径由
`scripts/run_benchmark.py verify` 用桩件覆盖（共用同一套 fixture）。

跑法：
    cd backend && uv run pytest tests/benchmark -q
"""

import json
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from app.services import benchmark  # noqa: E402
from app.services import benchmark_scoring as scoring  # noqa: E402

CASE_ID = 'synthetic_ev_price_war_001'
FIXTURE_ROOT = os.path.join(os.path.dirname(__file__), '..', 'fixtures', 'benchmark')


@pytest.fixture()
def case():
    return benchmark.BenchmarkManager.load(CASE_ID)


@pytest.fixture()
def case_copy(case, monkeypatch):
    """
    可安全篡改的案卷副本。

    目录层级要跟真实布局一致（<root>/cases/<case_id>），否则
    BenchmarkManager.case_dir() 拼出来的路径会落空。
    """
    temp_root = tempfile.mkdtemp(prefix='bench-unit-')
    target = os.path.join(temp_root, 'cases', CASE_ID)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copytree(case.case_dir, target)

    monkeypatch.setattr(benchmark, 'CASE_ROOT', temp_root)
    copied = benchmark.BenchmarkManager.load(CASE_ID)
    yield copied
    shutil.rmtree(temp_root, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# 防泄漏校验：每一项都要能单独失败
# ══════════════════════════════════════════════════════════════

def test_bundled_cases_pass_validation(case):
    report = benchmark.validate_case(case)
    assert report['ok'], report['checks']


def test_hash_integrity_fails_when_corpus_changed(case_copy):
    corpus_file = case_copy.corpus_paths()[0]
    with open(corpus_file, 'a', encoding='utf-8') as handle:
        handle.write('\n新增的一行，会让登记哈希失效。\n')

    report = benchmark.validate_case(case_copy)
    assert report['checks']['hash_integrity']['ok'] is False
    assert report['ok'] is False


def test_timeline_leak_detects_future_date(case_copy):
    """语料里出现 cutoff 之后的日期，必须被判为泄漏。"""
    corpus_file = case_copy.corpus_paths()[0]
    with open(corpus_file, 'a', encoding='utf-8') as handle:
        handle.write('\n后续会议计划于 2026-03-15 召开。\n')

    report = benchmark.validate_case(case_copy)
    assert report['checks']['timeline_leak']['ok'] is False
    assert '2026-03-15' in report['checks']['timeline_leak']['future_dates']


def test_term_leak_detects_outcome_wording(case_copy):
    corpus_file = case_copy.corpus_paths()[0]
    with open(corpus_file, 'a', encoding='utf-8') as handle:
        handle.write('\n市场传闻称将发生指导价调整。\n')

    report = benchmark.validate_case(case_copy)
    assert report['checks']['term_leak']['ok'] is False
    assert '指导价调整' in report['checks']['term_leak']['hits']


def test_number_leak_detects_answer_magnitude(case_copy):
    corpus_file = case_copy.corpus_paths()[0]
    with open(corpus_file, 'a', encoding='utf-8') as handle:
        handle.write('\n有分析认为降幅约为 15 个百分点。\n')

    report = benchmark.validate_case(case_copy)
    assert report['checks']['number_leak']['ok'] is False
    assert '15' in report['checks']['number_leak']['hits']


def test_date_order_fails_when_cutoff_precedes_corpus(case_copy):
    case_copy.seed = dict(case_copy.seed)
    case_copy.seed['cutoff'] = '2025-12-01'  # 早于 corpus_as_of
    report = benchmark.validate_case(case_copy)
    assert report['checks']['date_order']['ok'] is False


def test_case_json_must_not_reference_a_graph(case_copy):
    """案卷里出现 graph_id 意味着绑定了某次运行的产物，会破坏"重跑"的语义。"""
    case_file = os.path.join(case_copy.case_dir, 'case.json')
    with open(case_file, 'r+', encoding='utf-8') as handle:
        data = json.load(handle)
        data['graph_id'] = 'mirofish_someone_elses_graph'
        handle.seek(0)
        handle.truncate()
        json.dump(data, handle, ensure_ascii=False)

    with pytest.raises(ValueError, match='graph_id'):
        benchmark.BenchmarkManager.load(CASE_ID)


# ══════════════════════════════════════════════════════════════
# 离线指标
# ══════════════════════════════════════════════════════════════

def test_offline_metrics_recall_and_misses(case):
    # 注意：文本里绝不能出现"远驰集团"四个字——即便是否定句，
    # 关键词匹配也会把它算作命中（这正是关键词代理的已知局限）
    markdown = '报告讨论了启明汽车的动作与行业整体格局。'
    metrics = scoring.offline_metrics(
        markdown=markdown,
        graph_nodes=['启明汽车'],
        case=case,
        corpus_text=case.corpus_text(),
    )
    # 三个关键角色：启明汽车命中，远驰集团与行业协会都未提及
    assert metrics['key_actor_mention_recall'] == pytest.approx(1 / 3, abs=1e-4)
    assert set(metrics['key_actor_missed_in_report']) == {'远驰集团', '行业协会'}
    # 图谱召回：只抓到了启明汽车
    assert metrics['key_actor_graph_recall'] == pytest.approx(1 / 3, abs=1e-4)
    assert set(metrics['key_actor_missed_in_graph']) == {'远驰集团', '行业协会'}

    # 别名也算命中
    metrics_alias = scoring.offline_metrics(
        markdown='远驰方面表示会保持战略定力。',
        graph_nodes=[], case=case, corpus_text=case.corpus_text(),
    )
    assert '远驰集团' not in metrics_alias['key_actor_missed_in_report']


def test_direction_proxy_abstains_when_no_signal_terms(case):
    """一个信号词都没出现时不应硬猜方向。"""
    metrics = scoring.offline_metrics(
        markdown='这份报告完全没有涉及价格走向的措辞。',
        graph_nodes=[], case=case, corpus_text=case.corpus_text(),
    )
    for detail in metrics['direction_details']:
        assert detail['picked_by_keywords'] is None


def test_direction_proxy_picks_by_signal_count(case):
    metrics = scoring.offline_metrics(
        markdown='预计继续降价，厂商会进一步下调价格并让利促销。',
        graph_nodes=[], case=case, corpus_text=case.corpus_text(),
    )
    q1 = next(d for d in metrics['direction_details'] if d['question_id'] == 'q1')
    assert q1['picked_by_keywords'] == 'down'
    assert q1['correct'] is True


def test_ungrounded_numbers_are_flagged_not_scored(case):
    metrics = scoring.offline_metrics(
        markdown='预计降幅达到 99%。',
        graph_nodes=[], case=case, corpus_text=case.corpus_text(),
    )
    assert metrics['number_grounding_rate'] == 0.0
    assert '99%' in metrics['ungrounded_numbers']


# ══════════════════════════════════════════════════════════════
# 稳定性
# ══════════════════════════════════════════════════════════════

def test_claim_extraction_skips_scenario_block():
    markdown = (
        "# 报告\n\n## 核心发现\n\n这是一条足够长的、应当被抽取的断言内容。\n\n"
        "## 未来情景树\n\n这是情景块里的句子，不应当被当成报告断言统计。\n"
    )
    claims = scoring.extract_claims(markdown)
    texts = [c[0] for c in claims]
    assert any('足够长' in t for t in texts)
    assert not any('情景块里的句子' in t for t in texts)


def test_claim_clustering_groups_similar_and_separates_different():
    claims = [
        {'text': '渠道库存高企叠加产能释放以价换量动机较强', 'run_index': 0},
        {'text': '渠道库存高企叠加产能释放以价换量动机较强', 'run_index': 1},
        {'text': '监管可能介入并打断当前的价格竞争节奏', 'run_index': 0},
    ]
    clusters = scoring.cluster_claims(claims)
    sizes = sorted(len(c['claims']) for c in clusters)
    assert sizes == [1, 2], clusters


def test_stability_flags_incomparable_when_configs_differ():
    attempts = [
        {'status': 'ok', 'attempt': 1, 'case_sha256': 'a',
         'manifest': {'llm': {'model': 'm1'}, 'extra': {'config_sha256': 'x'}}},
        {'status': 'ok', 'attempt': 2, 'case_sha256': 'a',
         'manifest': {'llm': {'model': 'm2'}, 'extra': {'config_sha256': 'x'}}},
    ]
    report = scoring.stability(attempts, {})
    assert report['incomparable'] is True
    assert 'claims' not in report


def test_stability_needs_two_usable_attempts():
    attempts = [{'status': 'ok', 'attempt': 1}, {'status': 'failed', 'attempt': 2}]
    report = scoring.stability(attempts, {})
    assert report['usable_attempts'] == 1
    assert 'claims' not in report


def test_claim_stability_rate_bounds():
    """三次运行里两次相同、一次不同 -> 稳定率应严格介于 0 与 1 之间。"""
    attempts = [
        {'status': 'ok', 'attempt': i, 'case_sha256': 'a',
         'manifest': {'llm': {'model': 'm'}, 'extra': {'config_sha256': 'x'}}}
        for i in (1, 2, 3)
    ]
    artifacts = {
        1: {'report_markdown': '## 核心发现\n\n渠道库存高企叠加产能释放以价换量动机较强。'},
        2: {'report_markdown': '## 核心发现\n\n渠道库存高企叠加产能释放以价换量动机较强。'},
        3: {'report_markdown': '## 核心发现\n\n监管介入后价格竞争节奏被打断而且范围扩大。'},
    }
    report = scoring.stability(attempts, artifacts)
    rate = report['claims']['claim_stability_rate']
    assert 0 < rate < 1
    assert report['claims']['stable_count'] >= 1
    assert report['claims']['volatile_count'] >= 1


# ══════════════════════════════════════════════════════════════
# 记分卡
# ══════════════════════════════════════════════════════════════

def test_scorecard_excludes_synthetic_from_headline():
    attempts = [
        {'status': 'ok', 'headline_eligible': False, 'synthetic': True, 'attempt': 1,
         'case_id': 'c', 'offline_metrics': {'direction_proxy_hit_rate': 1.0}},
        {'status': 'ok', 'headline_eligible': True, 'synthetic': False, 'attempt': 2,
         'case_id': 'c', 'offline_metrics': {'direction_proxy_hit_rate': 0.0}},
    ]
    card = scoring.build_scorecard(attempts)
    assert card['headline_attempts'] == 1
    assert card['ineligible_attempts'] == 1
    # 头条只算合格那条
    assert card['headline']['direction_proxy_hit_rate'] == 0.0
    assert card['banner'] is None


def test_scorecard_banner_when_nothing_is_eligible():
    attempts = [
        {'status': 'ok', 'headline_eligible': False, 'synthetic': True, 'attempt': 1,
         'case_id': 'c', 'offline_metrics': {}},
    ]
    card = scoring.build_scorecard(attempts)
    assert card['headline_attempts'] == 0
    assert 'banner' in card
    assert card['headline']['direction_proxy_hit_rate'] is None


def test_stability_threshold_is_two_thirds():
    assert scoring.stability_threshold(3) == pytest.approx(2 / 3)
    assert scoring.stability_threshold(1) == 1.0
