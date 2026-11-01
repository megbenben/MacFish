"""
水晶预言球服务

把一份已完成报告铸造成一颗可长期追踪的预言球：
- sealed → opened（触碰开启）
- scry（问卜）
- verify（应验 / 部分应验 / 破碎，并记录准确度）

预言与问卜会回流冥想盆；跨库写入失败只告警，不影响预言球主流程。
所有 LLM 调用均可降级为确定性的本地生成。
"""
import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.locale import get_locale, t
from ..utils.logger import get_logger
from .pensieve_service import PensieveService, extract_keywords
from .prophecy_store import ProphecyStore
from .benchmark_scoring import calibration_metrics

logger = get_logger('macfish.prophecy_service')

TERMINAL_STATUSES = {'fulfilled', 'partial', 'shattered'}
ALL_STATUSES = {'sealed', 'opened'} | TERMINAL_STATUSES
_ID_RE = re.compile(r'^[A-Za-z0-9_.-]{1,128}$')


def _valid_id(value: str) -> bool:
    """report_id / simulation_id 会被拼进文件路径，拒绝 .. 与路径分隔符。"""
    return bool(value) and '..' not in value and bool(_ID_RE.fullmatch(value))


def _now() -> str:
    return datetime.now().isoformat()


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _as_str_list(value: Any, limit: int = 20) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or '').strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _likelihood_of(text: str) -> int:
    text = str(text or '').lower()
    percent = re.search(r'(\d{1,3})\s*%', text)
    if percent:
        return max(0, min(100, int(percent.group(1))))
    if any(word in text for word in ('高', 'high', 'likely', 'probable')):
        return 65
    if any(word in text for word in ('中', 'medium', 'moderate', 'possible')):
        return 35
    if any(word in text for word in ('低', 'low', 'unlikely', 'rare')):
        return 12
    return 25


class ProphecyService:
    """预言球的铸造、开启、问卜与应验校验。"""

    @classmethod
    def store(cls) -> ProphecyStore:
        return ProphecyStore.get_instance()

    # ------------------------------------------------------------ 报告读取
    @staticmethod
    def _reports_dir() -> str:
        return os.path.join(Config.UPLOAD_FOLDER, 'reports')

    @classmethod
    def _read_report_meta(cls, report_id: str) -> Optional[Dict[str, Any]]:
        if not _valid_id(report_id):
            return None
        root = cls._reports_dir()
        path = os.path.join(root, report_id, 'meta.json')
        if not os.path.exists(path):
            legacy = os.path.join(root, f'{report_id}.json')
            if os.path.exists(legacy):
                path = legacy
            else:
                return None
        return _read_json(path)

    @classmethod
    def _project_for_simulation(cls, simulation_id: str) -> str:
        if not _valid_id(simulation_id):
            return ''
        state = _read_json(os.path.join(
            Config.UPLOAD_FOLDER, 'simulations', simulation_id, 'state.json'))
        return (state or {}).get('project_id') or ''

    @classmethod
    def _outline_of(cls, meta: Dict[str, Any]):
        from .report_agent import ReportOutline  # 延迟导入，避免循环
        raw = meta.get('outline')
        if not raw:
            return None
        try:
            return ReportOutline.from_dict(raw)
        except Exception:
            return None

    # ------------------------------------------------------------ 铸造
    @classmethod
    def forge(cls, report_id: str) -> Dict[str, Any]:
        report_id = (report_id or '').strip()
        if not report_id:
            raise ValueError(t('api.prophecyReportIdRequired'))
        if not _valid_id(report_id):
            raise ValueError(t('api.prophecyInvalidReportId'))

        store = cls.store()
        existing = store.get_orb_by_report(report_id)
        if existing:
            return {'orb': cls._decorate_orb(store.get_orb(existing['orb_id'])),
                    'created': False, 'used_llm': False}

        meta = cls._read_report_meta(report_id)
        if not meta:
            raise LookupError(t('api.prophecyReportNotFound', id=report_id))

        outline = cls._outline_of(meta)
        payload, used_llm = cls._forge_payload(meta, outline)

        simulation_id = meta.get('simulation_id') or ''
        project_id = cls._project_for_simulation(simulation_id)
        horizon_days = _clamp_int(payload.get('horizon_days'), 7, 730, 90)
        created_at = _now()
        deadline = (datetime.now() + timedelta(days=horizon_days)).date().isoformat()
        seer = payload.get('seer') or ('The MacFish Seer' if get_locale() == 'en'
                                        else 'MacFish 预言者')

        orb = store.upsert_orb({
            'orb_id': f'orb_{report_id.removeprefix("report_")[:8]}_{os.urandom(3).hex()}',
            'title': payload.get('title') or f'预言 · {report_id}',
            'prophecy': payload.get('prophecy') or '',
            'summary': payload.get('summary') or '',
            'status': 'sealed',
            'confidence': _clamp_int(payload.get('confidence'), 0, 100, 60),
            'horizon_days': horizon_days,
            'deadline': deadline,
            'seer': seer,
            'source_report_id': report_id,
            'source_simulation_id': simulation_id,
            'project_id': project_id,
            'tags': _as_str_list(payload.get('tags'), 12),
            'entities': _as_str_list(payload.get('entities'), 16),
            'branches': payload.get('branches') or [],
            'indicators': payload.get('indicators') or [],
            'payload': {
                'used_llm': used_llm,
                'report_title': (outline.title if outline else '') or '',
                'created_by': 'macfish',
            },
            'created_at': created_at,
        })

        cls._remember_orb(orb)
        return {'orb': cls._decorate_orb(store.get_orb(orb['orb_id'])),
                'created': True, 'used_llm': used_llm}

    @classmethod
    def _forge_payload(cls, meta: Dict[str, Any],
                       outline) -> Tuple[Dict[str, Any], bool]:
        # 先尝试让 LLM 写一条真正的「预言」，失败则用报告大纲确定性地拼一条
        try:
            client = LLMClient()
            context = cls._report_context(meta, outline)
            prompt = (
                "你是霍格沃茨预言厅里的一颗水晶预言球。请根据下面这份预测报告，"
                "铸造一条可以被未来验证的预言。\n"
                "要求：\n"
                "1) prophecy 用神秘、克制、带意象的预言口吻（2-4 句），不要照抄报告摘要；\n"
                "2) branches 给 2-5 条情景分支，likelihood 为 0-100 的整数；\n"
                "3) indicators 给 3-6 个可观察的先兆，每项包含 signal 与 threshold；\n"
                "4) entities 是预言点名的人/组织/概念；\n"
                "5) horizon_days 为 30-365 的整数；confidence 为 0-100，反映证据强度，不要给满；\n"
                "6) 用与报告相同的语言书写。\n"
                "只返回 JSON：{\"title\":\"...\",\"prophecy\":\"...\",\"summary\":\"...\","
                "\"confidence\":0,\"horizon_days\":90,\"branches\":"
                "[{\"name\":\"...\",\"likelihood\":0,\"trigger\":\"...\",\"outcome\":\"...\"}],"
                "\"indicators\":[{\"signal\":\"...\",\"threshold\":\"...\"}],"
                "\"entities\":[\"...\"],\"tags\":[\"...\"],\"seer\":\"...\"}\n\n"
                f"报告材料：\n{context}"
            )
            data = client.chat_json([
                {"role": "system", "content": "你只输出有效 JSON。"},
                {"role": "user", "content": prompt},
            ], temperature=0.7, max_tokens=2400)
            if isinstance(data, dict) and (data.get('prophecy') or data.get('title')):
                normalized = cls._normalize_payload(data, meta, outline)
                return normalized, True
        except Exception as exc:
            logger.warning("预言铸造的 LLM 调用失败，使用本地降级: %s", exc)

        return cls._fallback_payload(meta, outline), False

    @staticmethod
    def _report_context(meta: Dict[str, Any], outline) -> str:
        parts = [
            f"报告 ID：{meta.get('report_id')}",
            f"预测需求：{meta.get('simulation_requirement') or ''}",
        ]
        if outline:
            parts.append(f"标题：{outline.title}")
            parts.append(f"摘要：{outline.summary}")
            for section in (outline.sections or [])[:6]:
                parts.append(f"章节「{section.title}」：{(section.content or '')[:700]}")
            for scenario in (outline.scenarios or [])[:6]:
                parts.append(
                    f"情景「{scenario.name}」：{scenario.description}；"
                    f"可能性 {scenario.relative_likelihood}；"
                    f"触发 {', '.join(scenario.trigger_conditions)}；"
                    f"角色 {', '.join(scenario.key_actors)}"
                )
        else:
            parts.append(f"报告摘要：{meta.get('markdown_content', '')[:2000]}")
        return '\n'.join(parts)

    @classmethod
    def _normalize_payload(cls, data: Dict[str, Any], meta: Dict[str, Any],
                           outline) -> Dict[str, Any]:
        branches = []
        for item in data.get('branches') or []:
            if not isinstance(item, dict):
                continue
            branches.append({
                'name': str(item.get('name') or item.get('title') or '未命名分支'),
                'likelihood': _clamp_int(item.get('likelihood'), 0, 100, 25),
                'trigger': str(item.get('trigger') or ''),
                'outcome': str(item.get('outcome') or item.get('description') or ''),
            })
        if not branches:
            branches = cls._fallback_branches(outline)

        indicators = []
        for item in data.get('indicators') or []:
            if isinstance(item, dict):
                signal = str(item.get('signal') or item.get('text') or '').strip()
                threshold = str(item.get('threshold') or '').strip()
                if signal:
                    indicators.append({'signal': signal, 'threshold': threshold})
            elif isinstance(item, str) and item.strip():
                indicators.append({'signal': item.strip(), 'threshold': ''})
        if not indicators:
            indicators = cls._fallback_indicators(outline)

        title = str(data.get('title') or '').strip()
        if not title:
            title = (outline.title if outline else '') or f"预言 · {meta.get('report_id')}"
        summary = str(data.get('summary') or '').strip()
        if not summary:
            summary = (outline.summary if outline else '') or meta.get('simulation_requirement') or ''

        return {
            'title': title,
            'prophecy': str(data.get('prophecy') or '').strip(),
            'summary': summary,
            'confidence': _clamp_int(data.get('confidence'), 0, 100, 60),
            'horizon_days': _clamp_int(data.get('horizon_days'), 7, 730, 90),
            'branches': branches,
            'indicators': indicators,
            'entities': _as_str_list(data.get('entities'), 16),
            'tags': _as_str_list(data.get('tags'), 12),
            'seer': str(data.get('seer') or '').strip(),
        }

    @classmethod
    def _fallback_payload(cls, meta: Dict[str, Any], outline) -> Dict[str, Any]:
        title = (outline.title if outline else '') or f"预言 · {meta.get('report_id')}"
        summary = ((outline.summary if outline else '')
                   or meta.get('simulation_requirement') or '')
        branches = cls._fallback_branches(outline)
        indicators = cls._fallback_indicators(outline)
        entities = []
        if outline:
            for scenario in outline.scenarios or []:
                for actor in scenario.key_actors or []:
                    if actor not in entities:
                        entities.append(actor)
        entities = entities[:16]
        tags = extract_keywords(f"{title}\n{summary}", entities, limit=8)

        scenario_count = len(branches)
        confidence = 55 + (8 if scenario_count >= 2 else 0) + \
            (5 if meta.get('status') == 'completed' else 0)
        confidence = max(55, min(85, confidence))
        horizon_days = 90
        branch_names = '、'.join(b['name'] for b in branches[:3]) or '未知的分岔'
        first_signal = (indicators[0]['signal'] if indicators else '第一条与预期不符的消息')

        if get_locale() == 'en':
            prophecy = (
                f"When the ripples of “{title}” spread, {summary[:180]} "
                f"The glass shows {scenario_count or 'several'} branching futures: {branch_names}. "
                f"Before the next {horizon_days} days, watch for this first sign: {first_signal}. "
                "What the orb shows is a possibility, never a command."
            )
            seer = 'The MacFish Seer'
        else:
            prophecy = (
                f"当「{title}」的余波散开，{summary[:180]} "
                f"水晶球里浮出 {scenario_count or '数'} 条分岔：{branch_names}。"
                f"在未来 {horizon_days} 天里，先留意这样一个信号：{first_signal}。"
                "球中所映只是可能性，从来不是命令。"
            )
            seer = 'MacFish 预言者'

        return {
            'title': title,
            'prophecy': prophecy,
            'summary': summary,
            'confidence': confidence,
            'horizon_days': horizon_days,
            'branches': branches,
            'indicators': indicators,
            'entities': entities,
            'tags': tags,
            'seer': seer,
        }

    @staticmethod
    def _fallback_branches(outline) -> List[Dict[str, Any]]:
        scenarios = list(outline.scenarios) if outline and outline.scenarios else []
        if not scenarios:
            return [{
                'name': '未被命名的分岔',
                'likelihood': 50,
                'trigger': '报告中的关键假设开始出现变化',
                'outcome': '局势沿报告的主线继续演化',
            }]

        raw = []
        for scenario in scenarios[:5]:
            if isinstance(scenario, dict):
                name = scenario.get('name') or '未命名分支'
                description = scenario.get('description') or ''
                triggers = scenario.get('trigger_conditions') or []
                likelihood_text = scenario.get('relative_likelihood') or ''
            else:
                name = scenario.name or '未命名分支'
                description = scenario.description or ''
                triggers = scenario.trigger_conditions or []
                likelihood_text = scenario.relative_likelihood or ''
            raw.append({
                'name': name,
                'likelihood': _likelihood_of(likelihood_text),
                'trigger': triggers[0] if triggers else '',
                'outcome': description,
            })

        total = sum(item['likelihood'] for item in raw) or len(raw)
        for item in raw:
            item['likelihood'] = max(1, round(item['likelihood'] * 100 / total))
        return raw

    @staticmethod
    def _fallback_indicators(outline) -> List[Dict[str, Any]]:
        indicators = []
        seen = set()
        if outline:
            for scenario in outline.scenarios or []:
                conditions = (scenario.get('trigger_conditions') if isinstance(scenario, dict)
                              else scenario.trigger_conditions) or []
                for condition in conditions:
                    text = str(condition or '').strip()
                    if text and text not in seen:
                        seen.add(text)
                        indicators.append({'signal': text, 'threshold': '出现即视为该分支被触发'})
                    if len(indicators) >= 8:
                        return indicators
        if not indicators:
            indicators.append({
                'signal': '报告中最重要的假设出现反转',
                'threshold': '连续两次观察到与预测相反的证据',
            })
        return indicators

    # ------------------------------------------------------------ 开启
    @classmethod
    def open_orb(cls, orb_id: str) -> Optional[Dict[str, Any]]:
        store = cls.store()
        orb = store.get_orb(orb_id)
        if not orb:
            return None
        if orb['status'] == 'sealed':
            orb['status'] = 'opened'
            orb['opened_at'] = _now()
            store.upsert_orb(orb)
            cls._remember_orb(orb)
        return cls._decorate_orb(store.get_orb(orb_id))

    # ------------------------------------------------------------ 问卜
    @classmethod
    def scry(cls, orb_id: str, question: str) -> Dict[str, Any]:
        store = cls.store()
        orb = store.get_orb(orb_id)
        if not orb:
            raise LookupError(t('api.prophecyOrbNotFound', id=orb_id))
        if orb['status'] == 'sealed':
            raise PermissionError(t('api.prophecySealedForScry'))
        question = (question or '').strip()
        if not question:
            raise ValueError(t('api.prophecyQuestionRequired'))

        used_llm = False
        answer = ''
        try:
            client = LLMClient()
            branches = json.dumps(orb.get('branches') or [], ensure_ascii=False)
            indicators = json.dumps(orb.get('indicators') or [], ensure_ascii=False)
            prompt = (
                "你是水晶预言球里的回声。请只根据下面的预言内容回答问卜者，"
                "不要编造预言之外的事实。回答 2-5 句，既要给出倾向，也要保留不确定性。\n\n"
                f"预言：{orb.get('prophecy')}\n"
                f"摘要：{orb.get('summary')}\n"
                f"分支：{branches}\n"
                f"先兆：{indicators}\n"
                f"当前状态：{orb.get('status')}；已有应验记录：{orb.get('outcome') or '无'}\n\n"
                f"问卜：{question}"
            )
            answer = client.chat([
                {"role": "system", "content": "你说话像一颗古老而克制的预言球。"},
                {"role": "user", "content": prompt},
            ], temperature=0.7, max_tokens=900).strip()
            used_llm = bool(answer)
        except Exception as exc:
            logger.warning("问卜的 LLM 调用失败，使用本地降级: %s", exc)

        if not answer:
            answer = cls._fallback_scry(orb, question)

        scrying = store.add_scrying(orb_id, question, answer)
        cls._remember_scrying(orb, scrying)
        return {
            'answer': answer,
            'used_llm': used_llm,
            'scrying': scrying,
            'orb': cls._decorate_orb(store.get_orb(orb_id)),
        }

    @classmethod
    def _fallback_scry(cls, orb: Dict[str, Any], question: str) -> str:
        branches = orb.get('branches') or []
        question_keywords = set(extract_keywords(question, limit=10))

        def branch_score(branch):
            text = f"{branch.get('name', '')} {branch.get('trigger', '')} {branch.get('outcome', '')}"
            keywords = set(extract_keywords(text, limit=10))
            return (len(question_keywords & keywords), int(branch.get('likelihood') or 0))

        best = max(branches, key=branch_score) if branches else None
        indicators = [item.get('signal', '') for item in (orb.get('indicators') or [])
                      if item.get('signal')]
        english = get_locale() == 'en'

        if english:
            if best:
                text = (f"The glass does not answer plainly. It shows the branch "
                        f"“{best.get('name')}”: {best.get('outcome')} "
                        f"It is triggered when: {best.get('trigger') or 'the report’s key assumption shifts'}. "
                        f"First sign to watch: {indicators[0] if indicators else 'the first credible contradiction'}. "
                        "Possibility, not command.")
            else:
                text = ("The glass is quiet. No branch has been named yet; treat the prophecy "
                        "as a question, not a verdict.")
            return text

        if best:
            return (
                f"水晶球没有直接回答，只映出「{best.get('name')}」这条分支："
                f"{best.get('outcome') or '局势沿这条路径继续'}。"
                f"触发它需要：{best.get('trigger') or '报告中的关键假设开始变化'}。"
                f"当前最该留意的先兆是：{indicators[0] if indicators else '第一条可信的反证'}。"
                "这只是可能性，不是命令。"
            )
        return "水晶球暂时沉默。预言里还没有被命名的分岔，先把它当作一个问题，而不是结论。"

    # ------------------------------------------------------------ 应验校验
    @classmethod
    def verify(cls, orb_id: str, outcome: str, verdict: str = '',
               accuracy: Optional[Any] = None, notes: str = '') -> Optional[Dict[str, Any]]:
        store = cls.store()
        orb = store.get_orb(orb_id)
        if not orb:
            return None
        if orb['status'] == 'sealed':
            raise PermissionError(t('api.prophecySealedForVerify'))

        outcome = (outcome or '').strip()
        if not outcome:
            raise ValueError(t('api.prophecyOutcomeRequired'))

        verdict = (verdict or '').strip().lower()
        if verdict and verdict not in TERMINAL_STATUSES:
            raise ValueError(t('api.prophecyInvalidVerdict'))

        if accuracy is not None and str(accuracy).strip() != '':
            score = _clamp_int(accuracy, 0, 100, 0)
        elif verdict:
            score = {'fulfilled': 100, 'partial': 50, 'shattered': 0}[verdict]
        else:
            score = cls._score_outcome(outcome, orb)

        if not verdict:
            if score >= 66:
                verdict = 'fulfilled'
            elif score >= 33:
                verdict = 'partial'
            else:
                verdict = 'shattered'

        orb.update({
            'status': verdict,
            'outcome': outcome,
            'accuracy': score,
            'verified_at': _now(),
            'notes': (notes or '').strip(),
        })
        store.upsert_orb(orb)
        cls._remember_verdict(orb)
        return cls._decorate_orb(store.get_orb(orb_id))

    @staticmethod
    def _score_outcome(outcome: str, orb: Dict[str, Any]) -> int:
        """用「实际结果中有多少关键信息曾被预言覆盖」估算准确度。"""
        outcome_keywords = set(extract_keywords(outcome, limit=24))
        if not outcome_keywords:
            return 0
        prophecy_text = '\n'.join([
            orb.get('prophecy') or '',
            orb.get('summary') or '',
            json.dumps(orb.get('branches') or [], ensure_ascii=False),
        ])
        prophecy_keywords = set(extract_keywords(
            prophecy_text, orb.get('entities'), orb.get('tags'), limit=40))
        shared = outcome_keywords & prophecy_keywords
        coverage = len(shared) / max(1, len(outcome_keywords))
        score = int(round(coverage * 100))
        # 点名对象直接出现时给一点加权，避免长文本把命中率稀释
        for entity in orb.get('entities') or []:
            if entity and str(entity) in outcome:
                score += 5
        return max(0, min(100, score))

    # ------------------------------------------------------------ 统计 / 列表
    @classmethod
    def calibration(cls) -> Dict[str, Any]:
        """预言球的概率校准。

        只统计**已终结且置信度明确**的球：
        - fulfilled → 1
        - shattered → 0
        - partial 不是二值结果，排除（否则会把"半对"硬塞成 0/1）
        """
        pairs = []
        for orb in cls.store().list_orbs():
            status = orb.get('status')
            if status not in ('fulfilled', 'shattered'):
                continue
            confidence = orb.get('confidence')
            try:
                probability = float(confidence) / 100.0
            except (TypeError, ValueError):
                continue
            if not (0.0 <= probability <= 1.0):
                continue
            pairs.append((probability, 1 if status == 'fulfilled' else 0))
        result = calibration_metrics(pairs)
        result['excluded_partial'] = sum(
            1 for orb in cls.store().list_orbs() if orb.get('status') == 'partial'
        )
        return result

    @classmethod
    def overview(cls) -> Dict[str, Any]:
        store = cls.store()
        stats = store.stats()
        report_rows = []
        root = cls._reports_dir()
        if os.path.isdir(root):
            for name in sorted(os.listdir(root), reverse=True):
                meta = _read_json(os.path.join(root, name, 'meta.json'))
                if not meta:
                    continue
                outline = meta.get('outline') or {}
                orb = store.get_orb_by_report(name)
                report_rows.append({
                    'report_id': name,
                    'title': outline.get('title') or name,
                    'simulation_id': meta.get('simulation_id') or '',
                    'status': meta.get('status') or '',
                    'created_at': meta.get('created_at') or '',
                    'forged': bool(orb),
                    'orb_id': orb['orb_id'] if orb else '',
                })
                if len(report_rows) >= 50:
                    break
        return {'stats': stats, 'reports': report_rows,
                'reminders': cls.reminders(),
                'calibration': cls.calibration()}

    # ------------------------------------------------------------ 到期提醒
    @staticmethod
    def _parse_deadline(value: Any) -> Optional[date]:
        text = str(value or '').strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

    @classmethod
    def _decorate_orb(cls, orb: Optional[Dict[str, Any]],
                      soon_days: int = 7) -> Optional[Dict[str, Any]]:
        """给球补上 days_left / deadline_state，前端不必重复算日期。"""
        if not orb:
            return orb
        orb = dict(orb)
        deadline = cls._parse_deadline(orb.get('deadline'))
        status = orb.get('status') or ''
        if deadline is None or status in TERMINAL_STATUSES:
            orb['days_left'] = None
            orb['deadline_state'] = ''
            return orb
        days_left = (deadline - date.today()).days
        orb['days_left'] = days_left
        if days_left < 0:
            orb['deadline_state'] = 'overdue'
        elif days_left == 0:
            orb['deadline_state'] = 'today'
        elif days_left <= 3:
            orb['deadline_state'] = 'critical'
        elif days_left <= soon_days:
            orb['deadline_state'] = 'soon'
        else:
            orb['deadline_state'] = 'upcoming'
        return orb

    @classmethod
    def reminders(cls, days: int = 7) -> Dict[str, Any]:
        """即将到期或已过期、且仍可校验的预言球。"""
        days = _clamp_int(days, 1, 365, 7)
        items = []
        for orb in cls.store().list_orbs():
            decorated = cls._decorate_orb(orb, soon_days=days)
            state = decorated.get('deadline_state')
            if state not in ('overdue', 'today', 'critical', 'soon'):
                continue
            items.append({
                'orb_id': decorated['orb_id'],
                'title': decorated.get('title') or decorated['orb_id'],
                'status': decorated.get('status') or '',
                'confidence': decorated.get('confidence'),
                'deadline': decorated.get('deadline') or '',
                'days_left': decorated.get('days_left'),
                'deadline_state': state,
                'source_report_id': decorated.get('source_report_id') or '',
            })
        items.sort(key=lambda item: item.get('days_left', 0))
        return {
            'days': days,
            'count': len(items),
            'overdue': sum(1 for item in items if item['deadline_state'] == 'overdue'),
            'due_soon': sum(1 for item in items if item['deadline_state'] != 'overdue'),
            'items': items,
        }

    @classmethod
    def list_orbs(cls, status: str = '', source_report_id: str = '') -> List[Dict[str, Any]]:
        if status and status not in ALL_STATUSES:
            raise ValueError(t('api.prophecyInvalidStatus'))
        return [cls._decorate_orb(orb) for orb in
                cls.store().list_orbs(status=status, source_report_id=source_report_id)]

    @classmethod
    def get_orb(cls, orb_id: str) -> Optional[Dict[str, Any]]:
        return cls._decorate_orb(cls.store().get_orb(orb_id))

    @classmethod
    def delete_orb(cls, orb_id: str) -> bool:
        return cls.store().delete_orb(orb_id)

    # ------------------------------------------------------------ 回流冥想盆
    @classmethod
    def _remember_orb(cls, orb: Dict[str, Any]) -> None:
        try:
            branch_text = '\n'.join(
                f"- {b.get('name')}（{b.get('likelihood', 0)}%）：{b.get('outcome', '')}"
                for b in orb.get('branches') or []
            )
            memory = PensieveService.record_external_memory(
                external_key=f"orb:{orb['orb_id']}",
                kind='prophecy',
                title=f"预言球 · {orb.get('title')}",
                content='\n\n'.join(filter(None, [
                    orb.get('prophecy') or '',
                    orb.get('summary') or '',
                    branch_text,
                    f"状态：{orb.get('status')}；置信度：{orb.get('confidence')}%；"
                    f"期限：{orb.get('deadline')}",
                ])),
                summary=(orb.get('summary') or orb.get('prophecy') or '')[:240],
                source_type='prophecy',
                source_id=orb['orb_id'],
                project_id=orb.get('project_id') or '',
                simulation_id=orb.get('source_simulation_id') or '',
                report_id=orb.get('source_report_id') or '',
                tags=_as_str_list(['orb', 'prophecy', orb.get('status')] + list(orb.get('tags') or []), 12),
                entities=_as_str_list(orb.get('entities'), 16),
                importance=94,
                metadata={
                    'orb_id': orb['orb_id'],
                    'status': orb.get('status'),
                    'confidence': orb.get('confidence'),
                    'deadline': orb.get('deadline'),
                },
            )
            # 立刻把预言球接到来源报告的总纲上，不必等下一次全量收割
            if memory and orb.get('source_report_id'):
                store = PensieveService.store()
                summary = store.get_memory_by_external_key(
                    f"report:{orb['source_report_id']}:summary")
                if summary:
                    store.add_strand(summary['memory_id'], memory['memory_id'],
                                     'prophecy_of', '预言球源自这份报告', 0.9)
        except Exception as exc:
            logger.warning("预言球记忆回流失败: %s", exc)

    @classmethod
    def _remember_scrying(cls, orb: Dict[str, Any], scrying: Dict[str, Any]) -> None:
        try:
            memory = PensieveService.record_external_memory(
                external_key=f"orb-scry:{scrying['scrying_id']}",
                kind='voice',
                title=f"问卜 · {orb.get('title')}",
                content=f"问：{scrying.get('question')}\n\n答：{scrying.get('answer')}",
                summary=(scrying.get('answer') or '')[:240],
                source_type='prophecy',
                source_id=orb['orb_id'],
                project_id=orb.get('project_id') or '',
                simulation_id=orb.get('source_simulation_id') or '',
                report_id=orb.get('source_report_id') or '',
                tags=['orb', 'scrying', 'voice'],
                entities=_as_str_list(orb.get('entities'), 16),
                importance=72,
                metadata={'orb_id': orb['orb_id'], 'scrying_id': scrying['scrying_id']},
            )
            if memory:
                store = PensieveService.store()
                orb_memory = store.get_memory_by_external_key(f"orb:{orb['orb_id']}")
                if orb_memory:
                    store.add_strand(orb_memory['memory_id'], memory['memory_id'],
                                     'scrying_of', '向预言球的问卜', 0.8)
        except Exception as exc:
            logger.warning("问卜记忆回流失败: %s", exc)

    @classmethod
    def _remember_verdict(cls, orb: Dict[str, Any]) -> None:
        try:
            memory = PensieveService.record_external_memory(
                external_key=f"orb-verdict:{orb['orb_id']}",
                kind='insight',
                title=f"应验记录 · {orb.get('title')}",
                content='\n\n'.join(filter(None, [
                    f"判定：{orb.get('status')}",
                    f"准确度：{orb.get('accuracy')}%",
                    f"实际结果：{orb.get('outcome') or ''}",
                    f"备注：{orb.get('notes') or ''}",
                ])),
                summary=(orb.get('outcome') or '')[:240],
                source_type='prophecy',
                source_id=orb['orb_id'],
                project_id=orb.get('project_id') or '',
                simulation_id=orb.get('source_simulation_id') or '',
                report_id=orb.get('source_report_id') or '',
                tags=['orb', 'verdict', orb.get('status') or ''],
                entities=_as_str_list(orb.get('entities'), 16),
                importance=90,
                metadata={
                    'orb_id': orb['orb_id'],
                    'verdict': orb.get('status'),
                    'accuracy': orb.get('accuracy'),
                },
            )
            if memory:
                store = PensieveService.store()
                orb_memory = store.get_memory_by_external_key(f"orb:{orb['orb_id']}")
                if orb_memory:
                    store.add_strand(orb_memory['memory_id'], memory['memory_id'],
                                     'verdict_of', '预言球的应验记录', 0.9)
        except Exception as exc:
            logger.warning("应验记忆回流失败: %s", exc)
