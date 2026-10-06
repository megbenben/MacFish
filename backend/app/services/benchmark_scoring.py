"""
回测评分与多次运行的稳定性度量
----------------------------
两层：

- **离线指标**（始终计算，零 LLM 调用）：关键词匹配、轨迹溯源、结构完整性。
  它们**不是有效性证据**，而是"管线有没有把该抓的东西抓住"的健全性检查。
  任何一个被当作准确率来宣传都是误用，所以字段名与注释里都标明了这一点。
- **LLM 裁判**（可选，`judge=True`）：让模型对照 ground truth 给报告打分。
  所有算术由本模块做——绝不相信裁判自己算出的总分。

稳定性（repeat N）：把同一案卷跑 N 次的结果做确定性聚合。
**刻意不用 embedding、也不用 LLM 做聚类**：项目里没有向量能力，而且用模型来判定
"两句话是不是同一个意思"会让指标本身变得不可复现。代价是改写措辞的同一断言会被
判为摇摆——指标因此**偏保守**，这个偏差方向是明示的、可接受的。
"""

import json
import logging
import math
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

#: 判定"稳定"的支持度阈值：N 次里至少 ceil(2N/3) 次出现
def stability_threshold(n: int) -> float:
    if n <= 0:
        return 1.0
    return math.ceil(2 * n / 3) / n


#: 断言聚类的 token 重叠阈值
CLAIM_SIMILARITY = 0.6
#: 一个断言至少要有这么多字符/词才值得纳入统计（过滤标题、空行）
MIN_CLAIM_CHARS = 12
MIN_CLAIM_TOKENS = 4

#: KPI 型数字（百分比/万/亿/元），用于"数字是否有出处"的统计
_KPI_NUMBER_RE = re.compile(r'\d+(?:\.\d+)?\s*(?:%|％|万|亿|元)')
_HEADING_RE = re.compile(r'^#{1,6}\s+(.*)$')
_SENTENCE_SPLIT_RE = re.compile(r'[。！？!?\n]+')


def norm(text: str) -> str:
    """NFKC + 小写 + 标点转空格 + 保留 CJK。确定性归一化的唯一入口。"""
    text = unicodedata.normalize('NFKC', text or '').lower()
    text = re.sub(r'[^\w一-鿿]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _hit(needle: str, haystack_norm: str) -> bool:
    """needle 是否作为完整片段出现在已归一化的 haystack 里。"""
    target = norm(needle)
    if not target:
        return False
    return target in haystack_norm


# ══════════════════════════════════════════════════════════════
# 离线指标
# ══════════════════════════════════════════════════════════════

def offline_metrics(
    *,
    markdown: str,
    graph_nodes: Sequence[str],
    case,
    corpus_text: str,
    outline: Optional[Dict[str, Any]] = None,
    min_report_chars: int = 1,
) -> Dict[str, Any]:
    """
    计算全部离线指标。

    **这些是关键词代理，不是准确率。** 它们回答"报告有没有提到该提的东西"，
    不回答"预测对不对"。字段名带 proxy / recall 字样就是为了提醒这一点。
    """
    report_norm = norm(markdown)
    corpus_norm = norm(corpus_text)
    graph_norm = [norm(name) for name in graph_nodes if name]

    # 1) 关键角色：报告里提到了多少 / 图谱里抓到了多少
    actors = case.key_actors()
    actor_names = [a.get('name') or a for a in actors] if actors else []
    aliases = {a.get('name'): list(a.get('aliases') or []) for a in actors if isinstance(a, dict)}

    mention_hits, graph_hits, missed_in_report, missed_in_graph = [], [], [], []
    for name in actor_names:
        candidates = [name] + aliases.get(name, [])
        in_report = any(_hit(c, report_norm) for c in candidates if c)
        in_graph = any(any(_hit(c, node) for c in candidates if c) for node in graph_norm)
        mention_hits.append(in_report)
        graph_hits.append(in_graph)
        if not in_report:
            missed_in_report.append(name)
        if not in_graph:
            missed_in_graph.append(name)

    total_actors = len(actor_names) or 1

    # 2) 方向代理：按 signal_terms 计数投票
    questions = case.questions()
    direction_results = []
    direction_correct = 0
    for question in questions:
        options = question.get('options') or []
        signal_terms = question.get('signal_terms') or {}
        answer = question.get('answer')

        counts = {}
        for option in options:
            terms = signal_terms.get(option) or []
            counts[option] = sum(report_norm.count(norm(t)) for t in terms if norm(t))

        picked = max(counts, key=lambda o: counts[o]) if counts else None
        if picked is not None and counts.get(picked, 0) == 0:
            picked = None  # 一个信号词都没出现，不硬猜
        correct = picked == answer
        direction_correct += 1 if correct else 0
        direction_results.append({
            'question_id': question.get('id'),
            'expected': answer,
            'picked_by_keywords': picked,
            'correct': correct,
            'signal_counts': counts,
        })

    # 3) 事件关键词覆盖率
    events = case.ground_truth.get('events') or []
    event_hits = []
    for event in events:
        terms = [t for t in (event.get('key_terms') or []) if t]
        hit = bool(terms) and all(_hit(t, report_norm) for t in terms)
        event_hits.append({'event_id': event.get('id'), 'hit': hit})

    # 4) 数字是否有出处（标记率，不是分数——预测本就会引入新数字）
    report_numbers = [m.group(0) for m in _KPI_NUMBER_RE.finditer(markdown)]
    grounded, ungrounded = [], []
    for token in report_numbers:
        digits = re.sub(r'[^\d.]', '', token)
        if digits and digits in corpus_norm:
            grounded.append(token)
        else:
            ungrounded.append(token)

    # 5) 结构完整性
    has_scenarios = bool((outline or {}).get('scenarios'))
    sections = (outline or {}).get('sections') or []
    structure = {
        'has_scenarios': has_scenarios,
        'section_count': len(sections),
        'report_chars': len(markdown),
        'min_sections_ok': len(sections) >= case.min_sections(),
        'min_report_chars_ok': len(markdown) >= min_report_chars,
    }

    return {
        'note': '关键词代理指标：衡量报告是否覆盖了该覆盖的内容，不等同于预测准确率',
        'key_actor_mention_recall': round(sum(mention_hits) / total_actors, 4),
        'key_actor_graph_recall': round(sum(graph_hits) / total_actors, 4),
        'key_actor_missed_in_report': missed_in_report,
        # 区分"管线没抽到"和"报告没提"——否则会静默冤枉错的阶段
        'key_actor_missed_in_graph': missed_in_graph,
        'direction_proxy_hit_rate': round(direction_correct / (len(questions) or 1), 4) if questions else None,
        'direction_details': direction_results,
        'event_term_recall': round(
            sum(1 for e in event_hits if e['hit']) / (len(event_hits) or 1), 4
        ) if event_hits else None,
        'number_grounding_rate': round(len(grounded) / (len(report_numbers) or 1), 4) if report_numbers else None,
        'ungrounded_numbers': ungrounded[:20],
        'structure': structure,
    }


# ══════════════════════════════════════════════════════════════
# LLM 裁判（可选）
# ══════════════════════════════════════════════════════════════

JUDGE_SYSTEM_PROMPT = """你是一个严格的预测报告评审专家。你将看到一份预测报告和该预测窗口结束后
实际发生的情况（ground truth）。请逐项核对报告在多大程度上命中了实际结果。

评分原则：
- 只依据报告文本与 ground truth，不要引入你自己的外部知识
- 每条判定都必须能引用报告中的原句作为依据；找不到原句就不要声称命中
- 报告给出的方向与实际相反时，该项明确判错，不要含糊
- 特别关注"校准诚实度"：只给一个笃定结论的报告，比给出多个情景并附触发条件的报告，应当得分更低"""

JUDGE_USER_PROMPT = """## 预测需求
{simulation_requirement}

## 需要判定的问题
{questions}

## 实际结果（ground truth）
{ground_truth}

## 报告中应当被检查的关键主体
{actors}

## 报告全文
{report}

请输出JSON（不要markdown）：
{{
  "questions": [
    {{"question_id": "...", "report_stance": "报告实际给出的选项值之一，或 null", "correct": true/false, "quote": "报告中的原句"}}
  ],
  "actors": [
    {{"name": "...", "mentioned": true/false, "consequential": true/false, "quote": "报告中的原句"}}
  ],
  "events": [
    {{"event_id": "...", "anticipated": true/false, "quote": "报告中的原句"}}
  ],
  "scores": {{
    "quantitative_grounding": 0-4,
    "calibration_honesty": 0-4
  }},
  "hallucination_flags": ["报告中出现的、实际并未发生的断言"],
  "notes": "简要说明"
}}"""


def judge_report(*, markdown: str, case, llm_client=None) -> Dict[str, Any]:
    """
    用 LLM 对照 ground truth 给报告打分。

    裁判的原始输出会被**严格校验**：每个 question_id / event_id 必须恰好出现一次，
    report_stance 必须属于该问题的 options，每条引用必须是报告里的字面子串。
    引用对不上的项会被丢弃并记 `judge_unsupported_quote`——宁可少给分，
    也不要采信一段报告里根本没有的"证据"。
    """
    from ..utils.llm_client import LLMClient

    client = llm_client or LLMClient()

    questions_desc = "\n".join(
        f"- {q.get('id')}: {q.get('text')}（可选值: {q.get('options')}）"
        for q in case.questions()
    ) or "（无结构化问题）"

    actors_desc = "\n".join(
        f"- {a.get('name')}（别名: {a.get('aliases') or []}）"
        for a in case.key_actors()
    ) or "（无）"

    prompt = JUDGE_USER_PROMPT.format(
        simulation_requirement=case.simulation_requirement,
        questions=questions_desc,
        ground_truth=json.dumps(case.ground_truth, ensure_ascii=False, indent=2),
        actors=actors_desc,
        report=markdown,
    )

    raw = client.chat_json(
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
    )

    return _validate_judge_output(raw, markdown=markdown, case=case)


def _validate_judge_output(raw: Dict[str, Any], *, markdown: str, case) -> Dict[str, Any]:
    """校验裁判输出：对不上的证据一律丢弃，缺失的项记为 incomplete 而不是补零。"""
    issues: List[str] = []
    unsupported: List[str] = []

    expected_q = {q.get('id'): q for q in case.questions()}
    expected_e = {e.get('id'): e for e in (case.ground_truth.get('events') or [])}

    questions_out = []
    seen_q = set()
    for item in (raw.get('questions') or []):
        if not isinstance(item, dict):
            continue
        qid = item.get('question_id')
        if qid not in expected_q:
            issues.append(f'未知 question_id: {qid}')
            continue
        if qid in seen_q:
            issues.append(f'重复 question_id: {qid}')
            continue
        seen_q.add(qid)

        quote = str(item.get('quote') or '')
        if quote and quote not in markdown:
            unsupported.append(f'question:{qid}')
            quote = ''

        stance = item.get('report_stance')
        options = expected_q[qid].get('options') or []
        if stance is not None and options and stance not in options:
            issues.append(f'{qid} 的 report_stance 不在 options 内: {stance}')
            stance = None

        questions_out.append({
            'question_id': qid,
            'report_stance': stance,
            'correct': bool(item.get('correct')) and bool(quote),
            'quote': quote,
            'expected': expected_q[qid].get('answer'),
        })

    actors_out, seen_a = [], set()
    for item in (raw.get('actors') or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '')
        if not name or name in seen_a:
            continue
        seen_a.add(name)
        quote = str(item.get('quote') or '')
        if quote and quote not in markdown:
            unsupported.append(f'actor:{name}')
            quote = ''
        actors_out.append({
            'name': name,
            'mentioned': bool(item.get('mentioned')),
            'consequential': bool(item.get('consequential')) and bool(quote),
            'quote': quote,
        })

    events_out, seen_e = [], set()
    for item in (raw.get('events') or []):
        if not isinstance(item, dict):
            continue
        eid = item.get('event_id')
        if eid not in expected_e or eid in seen_e:
            continue
        seen_e.add(eid)
        quote = str(item.get('quote') or '')
        if quote and quote not in markdown:
            unsupported.append(f'event:{eid}')
            quote = ''
        events_out.append({
            'event_id': eid,
            'anticipated': bool(item.get('anticipated')) and bool(quote),
            'quote': quote,
        })

    missing_q = sorted(set(expected_q) - seen_q)
    missing_e = sorted(set(expected_e) - seen_e)
    if missing_q:
        issues.append(f'judge_incomplete: 缺 questions {missing_q}')
    if missing_e:
        issues.append(f'judge_incomplete: 缺 events {missing_e}')

    scores = raw.get('scores') or {}

    def _clamp(value: Any) -> Optional[int]:
        try:
            return max(0, min(4, int(value)))
        except (TypeError, ValueError):
            return None

    # 只对裁判确实给出判定的项计分；缺失项省略而不是补零
    judgeable_q = [q for q in questions_out if q['quote']]
    judgeable_a = [a for a in actors_out if a['quote']]

    return {
        'judge_model': None,
        'questions': questions_out,
        'actors': actors_out,
        'events': events_out,
        'scores': {
            'quantitative_grounding': _clamp(scores.get('quantitative_grounding')),
            'calibration_honesty': _clamp(scores.get('calibration_honesty')),
        },
        # 方向命中率只统计"裁判给出了带引用判定"的问题
        'direction_accuracy': (
            round(sum(1 for q in judgeable_q if q['correct']) / len(judgeable_q), 4)
            if judgeable_q else None
        ),
        'actor_consequential_rate': (
            round(sum(1 for a in judgeable_a if a['consequential']) / len(judgeable_a), 4)
            if judgeable_a else None
        ),
        'hallucination_flags': list(raw.get('hallucination_flags') or []),
        'judge_unsupported_quote': unsupported,
        'judge_issues': issues,
        'notes': str(raw.get('notes') or ''),
    }


# ══════════════════════════════════════════════════════════════
# 多次运行的稳定性
# ══════════════════════════════════════════════════════════════

def extract_claims(markdown: str) -> List[Tuple[str, str]]:
    """
    把报告切成 (断言文本, 所属章节标题)。

    确定性抽取：按标题切段，再按句号/换行断句，过滤过短的片段。
    情景块会被跳过——它是要单独度量的另一层。
    """
    claims: List[Tuple[str, str]] = []
    section = ''
    in_scenario = False

    for raw_line in (markdown or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            title = heading.group(1).strip()
            # 情景块起始（按标题文字判断，避免依赖具体措辞之外的内部标记）
            in_scenario = bool(re.match(r'^(未来)?情景', title)) or '情景树' in title
            section = title
            continue

        if in_scenario:
            continue

        for piece in _SENTENCE_SPLIT_RE.split(line):
            piece = piece.strip(' \t-*#>')
            if len(piece) < MIN_CLAIM_CHARS:
                continue
            if len(piece.split()) < MIN_CLAIM_TOKENS and not re.search(r'[一-鿿]', piece):
                continue
            claims.append((piece, section))
    return claims


def cluster_claims(claims: Sequence[Dict[str, Any]],
                   threshold: float = CLAIM_SIMILARITY) -> List[Dict[str, Any]]:
    """
    贪心单链聚类，确定性（按归一化文本排序后处理 + 固定 tie-break）。

    claims 里每项需含 {'text', 'run_index'}。
    """
    clusters: List[Dict[str, Any]] = []

    for claim in sorted(claims, key=lambda c: (norm(c['text']), c['run_index'])):
        tokens = set(norm(claim['text']).split())
        for cluster in clusters:
            if jaccard(tokens, cluster['rep_tokens']) >= threshold:
                cluster['claims'].append(claim)
                break
        else:
            clusters.append({'rep_tokens': tokens, 'claims': [claim]})

    return clusters


def stability(attempts: Sequence[Dict[str, Any]],
              attempt_artifacts: Optional[Dict[int, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    对同一案卷的 N 次尝试做确定性聚合。

    Args:
        attempts: 每次尝试的结果字典（含 status / case_sha256 / offline_metrics）
        attempt_artifacts: {attempt 序号: {'report_markdown', 'graph_entities', 'scenarios', 'manifest'}}

    Returns:
        稳定性报告。若各次运行的配置不一致，返回 `incomparable` 并**响亮失败**——
        那测的是配置漂移而不是随机性，静默平均会得出毫无意义的数字。
    """
    n = len(attempts)
    report: Dict[str, Any] = {
        'run_count': n,
        'incomparable': False,
        'reasons': [],
    }

    ok_attempts = [a for a in attempts if a.get('status') == 'ok']
    report['usable_attempts'] = len(ok_attempts)
    if len(ok_attempts) < 2:
        report['reasons'].append(f'可用的成功尝试不足 2 次（{len(ok_attempts)}），无法度量稳定性')
        return report

    # 可比性检查：同一案卷 + 同一材料 + 同一模型
    fingerprints = set()
    for attempt in ok_attempts:
        manifest = (attempt.get('manifest') or {})
        llm = manifest.get('llm') or {}
        fingerprints.add((
            attempt.get('case_sha256'),
            manifest.get('extra', {}).get('config_sha256') if isinstance(manifest.get('extra'), dict) else None,
            llm.get('model'),
        ))
    if len(fingerprints) > 1:
        report['incomparable'] = True
        report['reasons'].append(
            '各次运行的案卷指纹 / 配置 / 模型不一致——测到的是配置漂移，不是随机性'
        )
        return report

    artifacts = attempt_artifacts or {}
    threshold = stability_threshold(len(ok_attempts))

    # -- A. 图谱结构 --
    entity_sets: List[Set[str]] = []
    for attempt in ok_attempts:
        artifact = artifacts.get(attempt.get('attempt'))
        names = (artifact or {}).get('graph_entities') or []
        entity_sets.append({norm(name) for name in names if norm(name)})

    if any(entity_sets):
        pair_scores = [
            jaccard(entity_sets[i], entity_sets[j])
            for i in range(len(entity_sets)) for j in range(i + 1, len(entity_sets))
        ]
        union = set().union(*entity_sets)
        support = {
            entity: sum(1 for s in entity_sets if entity in s) / len(entity_sets)
            for entity in union
        }
        report['graph'] = {
            'mean_pairwise_jaccard': round(sum(pair_scores) / len(pair_scores), 4) if pair_scores else None,
            'core_ratio': round(len(set.intersection(*entity_sets)) / len(union), 4) if union else None,
            'stable_entities': sorted(e for e, s in support.items() if s >= threshold),
            'volatile_entities': sorted(e for e, s in support.items() if s < threshold),
            'threshold': round(threshold, 4),
        }

    # -- B. 报告断言 --
    all_claims: List[Dict[str, Any]] = []
    for index, attempt in enumerate(ok_attempts):
        artifact = artifacts.get(attempt.get('attempt'))
        markdown = (artifact or {}).get('report_markdown') or ''
        for text, section in extract_claims(markdown):
            all_claims.append({'text': text, 'section': section, 'run_index': index})

    if all_claims:
        clusters = cluster_claims(all_claims)
        total = len(clusters)
        cluster_reports = []
        for cluster in clusters:
            runs = {c['run_index'] for c in cluster['claims']}
            support_ratio = len(runs) / len(ok_attempts)
            cluster_reports.append({
                'support': round(support_ratio, 4),
                'stable': support_ratio >= threshold,
                'variants': [c['text'] for c in cluster['claims']],
                'sections': sorted({c['section'] for c in cluster['claims']}),
                'runs': sorted(runs),
            })
        cluster_reports.sort(key=lambda c: (-c['support'], c['variants'][0] if c['variants'] else ''))

        stable = [c for c in cluster_reports if c['stable']]
        volatile = [c for c in cluster_reports if not c['stable']]

        report['claims'] = {
            'total_clusters': total,
            # 主指标：每个簇的平均支持度，1.0 = 每条结论每次运行都出现
            'claim_stability_rate': round(sum(c['support'] for c in cluster_reports) / total, 4),
            'stable_count': len(stable),
            'volatile_count': len(volatile),
            'stable_claims': [c['variants'][0] for c in stable][:30],
            'volatile_claims': volatile[:30],
            'threshold': round(threshold, 4),
        }

    # -- C. 情景 --
    scenario_sets: List[Set[str]] = []
    for attempt in ok_attempts:
        artifact = artifacts.get(attempt.get('attempt'))
        scenarios = (artifact or {}).get('scenarios') or []
        scenario_sets.append({
            norm(s.get('name') or '') for s in scenarios if isinstance(s, dict) and norm(s.get('name') or '')
        })

    if any(scenario_sets):
        pairs = [
            jaccard(scenario_sets[i], scenario_sets[j])
            for i in range(len(scenario_sets)) for j in range(i + 1, len(scenario_sets))
        ]
        report['scenarios'] = {
            'mean_pairwise_jaccard': round(sum(pairs) / len(pairs), 4) if pairs else None,
            'per_run_counts': [len(s) for s in scenario_sets],
        }

    return report


# ══════════════════════════════════════════════════════════════
# 记分卡
# ══════════════════════════════════════════════════════════════

def build_scorecard(attempts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    聚合成记分卡。

    **头条数字只统计 headline_eligible 的尝试**；其余（合成案卷、未通过校验、
    被截断、跑失败）单列在 ineligible 桶里，并在输出里带横幅说明。
    """
    eligible = [a for a in attempts if a.get('headline_eligible')]
    ineligible = [a for a in attempts if not a.get('headline_eligible')]

    def _mean(values: List[float]) -> Optional[float]:
        clean = [v for v in values if isinstance(v, (int, float))]
        return round(sum(clean) / len(clean), 4) if clean else None

    def _collect(bucket: List[Dict[str, Any]], key: str) -> List[float]:
        return [a.get('offline_metrics', {}).get(key) for a in bucket]

    card: Dict[str, Any] = {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'attempts_total': len(attempts),
        'headline_attempts': len(eligible),
        'ineligible_attempts': len(ineligible),
        'headline': {
            'direction_proxy_hit_rate': _mean(_collect(eligible, 'direction_proxy_hit_rate')),
            'key_actor_mention_recall': _mean(_collect(eligible, 'key_actor_mention_recall')),
            'key_actor_graph_recall': _mean(_collect(eligible, 'key_actor_graph_recall')),
            'event_term_recall': _mean(_collect(eligible, 'event_term_recall')),
            'number_grounding_rate': _mean(_collect(eligible, 'number_grounding_rate')),
        },
        'ineligible': [
            {
                'case_id': a.get('case_id'),
                'attempt': a.get('attempt'),
                'status': a.get('status'),
                'status_reason': a.get('status_reason'),
                'synthetic': a.get('synthetic'),
                'validated': a.get('validated'),
                'horizon_truncated': a.get('horizon_truncated'),
            }
            for a in ineligible
        ],
        'partial': any(not a.get('llm_scores') for a in attempts),
        # banner 恒定存在（无合格尝试时为文案，否则为 None）：
        # 让消费方不必先判断字段在不在
        'banner': None,
    }

    if not eligible:
        card['banner'] = (
            '没有合格的（headline_eligible）尝试，因此本记分卡没有任何准确率数字。'
            '合格条件：跑通 + 通过防泄漏校验 + 非合成案卷 + 未被轮数上限截断。'
            '合成演示案卷永远不会合格——它们是 fixture，不是有效性证据。'
        )
    return card
