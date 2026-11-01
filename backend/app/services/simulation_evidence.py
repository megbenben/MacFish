"""
模拟证据服务
============

Report Agent 过去只查知识图谱。图谱是从**种子文档**抽出来的，而模拟过程中
Agent 真正发了什么帖子、评论、跟谁互动，默认并没有写回图谱
（``enable_graph_memory_update`` 默认 false）。结果是报告"看不到模拟"。

本模块把一次模拟的落盘产物整理成可检索、可引用的证据：

- ``<sim>/state.json`` / ``simulation_config.json``：规模与时间配置
- ``<sim>/<platform>/actions.jsonl``：逐回合动作（含 Agent 名与 action_args）
- ``<sim>/<platform>_simulation.db``：post / comment / user 表（点赞数等）

每个证据都带稳定的 ``source_id``（如 ``twitter:post:12``），报告生成时由
``CitationRegistry`` 映射成 ``S1``、``S2``…，最终随报告一起持久化。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..config import Config
from ..utils.logger import get_logger
from .market_simulator import MarketSimulator

logger = get_logger('mirofish.simulation_evidence')

#: 单条证据送入 LLM 的最大字符数
MAX_CONTENT_CHARS = 1200

#: actions.jsonl 里这些事件不是 Agent 行为，不参与检索
_NON_ACTION_EVENTS = {
    'simulation_start', 'simulation_end', 'round_start', 'round_end',
    'platform_start', 'platform_end',
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _content_hash(text: str) -> str:
    return hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()


def _query_terms(query: str) -> List[str]:
    """把查询拆成中英文检索词。

    SQLite 侧没有 FTS 时，这是最稳的关键词召回：英文按词，中文按 2/3-gram。
    只用于排序，不追求精确。
    """
    query = (query or '').lower().strip()
    if not query:
        return []
    terms = set(re.findall(r'[a-z0-9_]{2,}', query))
    for run in re.findall(r'[一-鿿]{2,}', query):
        for size in (2, 3):
            if len(run) < size:
                continue
            for i in range(len(run) - size + 1):
                terms.add(run[i:i + size])
    # 长词优先：避免 2-gram 把排序全占满
    return sorted(terms, key=len, reverse=True)[:24]


class CitationRegistry:
    """把证据 source_id 映射成报告里的 S1/S2…，同一来源只登记一次。"""

    def __init__(self) -> None:
        self._by_source: Dict[str, str] = {}
        self._items: List[Dict[str, Any]] = []

    def register(self, source: Dict[str, Any]) -> str:
        source_id = str(source.get('source_id') or '')
        if not source_id:
            source_id = f"anonymous:{_content_hash(source.get('content', ''))[:10]}"
        existing = self._by_source.get(source_id)
        if existing:
            return existing
        citation_id = f"S{len(self._items) + 1}"
        self._by_source[source_id] = citation_id
        item = dict(source)
        item['citation_id'] = citation_id
        self._items.append(item)
        return citation_id

    def register_many(self, sources: Iterable[Dict[str, Any]]) -> List[str]:
        return [self.register(source) for source in sources]

    def items(self) -> List[Dict[str, Any]]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)


class SimulationEvidenceService:
    """读取一次模拟的 posts / comments / actions，并提供检索与概览。"""

    # ------------------------------------------------------------ paths / io
    @classmethod
    def simulation_dir(cls, simulation_id: str) -> str:
        return os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)

    @classmethod
    def _read_json(cls, path: str) -> Dict[str, Any]:
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @classmethod
    def get_state(cls, simulation_id: str) -> Dict[str, Any]:
        root = cls.simulation_dir(simulation_id)
        return cls._read_json(os.path.join(root, 'state.json'))

    @classmethod
    def get_config(cls, simulation_id: str) -> Dict[str, Any]:
        root = cls.simulation_dir(simulation_id)
        return cls._read_json(os.path.join(root, 'simulation_config.json'))

    @classmethod
    def platforms(cls, simulation_id: str) -> List[str]:
        state = cls.get_state(simulation_id)
        result = []
        if state.get('enable_twitter', True):
            result.append('twitter')
        if state.get('enable_reddit', True):
            result.append('reddit')
        return result or ['twitter', 'reddit']

    # ------------------------------------------------------------ actions
    @classmethod
    def _iter_actions(cls, simulation_id: str, platform: str) -> Iterable[Dict[str, Any]]:
        path = os.path.join(cls.simulation_dir(simulation_id), platform, 'actions.jsonl')
        if not os.path.isfile(path):
            return
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as handle:
                for line_no, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    event_type = str(event.get('event_type') or '')
                    if event_type in _NON_ACTION_EVENTS:
                        continue
                    action_type = str(event.get('action_type') or event_type or 'ACTION')
                    args = event.get('action_args') or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {'content': args}
                    if not isinstance(args, dict):
                        args = {'content': str(args)}
                    content = (
                        args.get('content')
                        or args.get('text')
                        or args.get('quote_content')
                        or args.get('reason')
                        or ''
                    )
                    content = str(content).strip()
                    if not content:
                        # 点赞/关注之类没有文本，仍保留动作类型供统计
                        content = f"{action_type} {json.dumps(args, ensure_ascii=False)[:300]}"
                    yield {
                        'source_id': f"{platform}:action:{line_no}",
                        'kind': 'action',
                        'platform': platform,
                        'action_type': action_type,
                        'agent': str(event.get('agent_name') or event.get('agent_id') or ''),
                        'round': event.get('round'),
                        'timestamp': str(event.get('timestamp') or ''),
                        'content': content[:MAX_CONTENT_CHARS],
                        '_raw_len': len(content),
                    }
        except OSError as exc:
            logger.warning(f"读取 actions 失败 {path}: {exc}")

    # ------------------------------------------------------------ posts/comments
    @classmethod
    def _read_platform_db(
        cls, simulation_id: str, platform: str, limit: int = 800
    ) -> Dict[str, Any]:
        path = os.path.join(cls.simulation_dir(simulation_id), f'{platform}_simulation.db')
        result = {'posts': [], 'comments': [], 'users': 0}
        if not os.path.isfile(path):
            return result
        try:
            conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                result['users'] = _safe_int(
                    conn.execute('SELECT COUNT(*) AS c FROM user').fetchone()['c']
                )
                post_rows = conn.execute(
                    """
                    SELECT p.post_id, p.content, p.quote_content, p.created_at,
                           p.num_likes, p.num_dislikes, p.num_shares,
                           u.name AS user_name, u.user_id
                    FROM post p LEFT JOIN user u ON u.user_id = p.user_id
                    ORDER BY p.post_id DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                for row in post_rows:
                    content = (row['content'] or row['quote_content'] or '').strip()
                    if not content:
                        continue
                    result['posts'].append({
                        'source_id': f"{platform}:post:{row['post_id']}",
                        'kind': 'post',
                        'platform': platform,
                        'agent': row['user_name'] or f"user_{row['user_id']}",
                        'round': None,
                        'timestamp': str(row['created_at'] or ''),
                        'content': content[:MAX_CONTENT_CHARS],
                        'likes': _safe_int(row['num_likes']),
                        'dislikes': _safe_int(row['num_dislikes']),
                        'shares': _safe_int(row['num_shares']),
                    })

                comment_rows = conn.execute(
                    """
                    SELECT c.comment_id, c.content, c.created_at, c.num_likes,
                           c.num_dislikes, c.post_id,
                           u.name AS user_name, u.user_id
                    FROM comment c LEFT JOIN user u ON u.user_id = c.user_id
                    ORDER BY c.comment_id DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                for row in comment_rows:
                    content = (row['content'] or '').strip()
                    if not content:
                        continue
                    result['comments'].append({
                        'source_id': f"{platform}:comment:{row['comment_id']}",
                        'kind': 'comment',
                        'platform': platform,
                        'agent': row['user_name'] or f"user_{row['user_id']}",
                        'round': None,
                        'timestamp': str(row['created_at'] or ''),
                        'content': content[:MAX_CONTENT_CHARS],
                        'likes': _safe_int(row['num_likes']),
                        'dislikes': _safe_int(row['num_dislikes']),
                        'post_id': row['post_id'],
                    })
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning(f"读取模拟数据库失败 {path}: {exc}")
        return result

    # ------------------------------------------------------------ collect / search
    @classmethod
    def collect(
        cls, simulation_id: str, platforms: Optional[Sequence[str]] = None
    ) -> List[Dict[str, Any]]:
        """收集全部可检索证据，按内容去重（动作与 DB post 经常是同一句话）。"""
        selected = list(platforms) if platforms else cls.platforms(simulation_id)
        items: List[Dict[str, Any]] = []
        seen = set()
        for platform in selected:
            db_data = cls._read_platform_db(simulation_id, platform)
            for item in db_data['posts'] + db_data['comments']:
                # 与 actions 共用同一去重键：actions.jsonl 里的 CREATE_POST
                # 和 DB 里的 post 是同一条内容，保留带互动数的 DB 版本。
                key = ('content', _content_hash(item['content']))
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)
            for item in cls._iter_actions(simulation_id, platform):
                # post/comment 的 DB 版本带互动数，优先保留；动作只补 DB 里没有的
                key = ('content', _content_hash(item['content']))
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)

        # 预测市场成交也作为证据：报告可以引用"市场在某一轮转向"
        try:
            market = MarketSimulator.overview(simulation_id)
            for trade in (market.get('recent_trades') or []):
                price_before = float(trade.get('price_before') or 0.5)
                price_after = float(trade.get('price_after') or 0.5)
                content = (
                    f"市场交易 {trade.get('side')} {trade.get('shares')} 股，"
                    f"价格 {price_before:.2f} → {price_after:.2f}。"
                    f"触发原文：{trade.get('reason') or ''}"
                )
                items.append({
                    'source_id': f"market:trade:{trade.get('trade_id')}",
                    'kind': 'market_trade',
                    'platform': 'market',
                    'agent': trade.get('agent') or '',
                    'round': trade.get('round'),
                    'timestamp': trade.get('timestamp') or '',
                    'content': content[:MAX_CONTENT_CHARS],
                })
        except Exception as exc:
            logger.debug(f"读取市场证据失败: {exc}")

        return items

    @classmethod
    def search(
        cls,
        simulation_id: str,
        query: str = '',
        limit: int = 12,
        platforms: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """关键词检索。query 为空时返回互动最高的帖子/评论（用于概览）。"""
        items = cls.collect(simulation_id, platforms)
        terms = _query_terms(query)
        for item in items:
            content = item.get('content', '').lower()
            score = 0
            for term in terms:
                if term and term in content:
                    score += max(1, len(term) - 1)
            item['_score'] = score
            item['_engagement'] = (
                _safe_int(item.get('likes')) * 3
                + _safe_int(item.get('shares')) * 2
                + _safe_int(item.get('dislikes'))
            )
        if terms:
            matched = [item for item in items if item['_score'] > 0]
            pool = matched or items
            pool.sort(key=lambda x: (x['_score'], x['_engagement']), reverse=True)
        else:
            pool = sorted(items, key=lambda x: x['_engagement'], reverse=True)
        clean = []
        for item in pool[:max(1, min(int(limit or 12), 30))]:
            item = dict(item)
            item.pop('_score', None)
            item.pop('_engagement', None)
            item.pop('_raw_len', None)
            clean.append(item)
        return clean

    # ------------------------------------------------------------ overview
    @classmethod
    def overview(cls, simulation_id: str) -> Dict[str, Any]:
        state = cls.get_state(simulation_id)
        config = cls.get_config(simulation_id)
        platforms = cls.platforms(simulation_id)
        time_config = config.get('time_config') or {}
        total_hours = time_config.get('total_simulation_hours')
        minutes_per_round = time_config.get('minutes_per_round')
        rounds = None
        if total_hours and minutes_per_round:
            try:
                rounds = int(float(total_hours) * 60 / float(minutes_per_round))
            except (TypeError, ValueError, ZeroDivisionError):
                rounds = None

        per_platform: Dict[str, Any] = {}
        top_posts: List[Dict[str, Any]] = []
        action_types: Counter = Counter()
        agent_actions: Counter = Counter()
        last_actions: List[Dict[str, Any]] = []

        for platform in platforms:
            db_data = cls._read_platform_db(simulation_id, platform)
            actions = list(cls._iter_actions(simulation_id, platform))
            for action in actions:
                action_types[action.get('action_type', '')] += 1
                if action.get('agent'):
                    agent_actions[action['agent']] += 1
            per_platform[platform] = {
                'users': db_data['users'],
                'posts': len(db_data['posts']),
                'comments': len(db_data['comments']),
                'actions': len(actions),
            }
            top_posts.extend(db_data['posts'])
            last_actions.extend(actions[-10:])

        top_posts.sort(key=lambda x: (x.get('likes', 0), x.get('shares', 0)), reverse=True)
        last_actions.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

        try:
            market = MarketSimulator.overview(simulation_id)
        except Exception as exc:
            logger.debug(f"读取预测市场失败: {exc}")
            market = {'enabled': False}

        return {
            'simulation_id': simulation_id,
            'status': state.get('status', ''),
            'project_id': state.get('project_id', ''),
            'graph_id': state.get('graph_id', ''),
            'entities_count': _safe_int(state.get('entities_count')),
            'profiles_count': _safe_int(state.get('profiles_count')),
            'platforms': platforms,
            'time_config': {
                'total_simulation_hours': total_hours,
                'minutes_per_round': minutes_per_round,
                'planned_rounds': rounds,
            },
            'per_platform': per_platform,
            'action_types': dict(action_types.most_common(12)),
            'top_agents': [
                {'agent': name, 'actions': count}
                for name, count in agent_actions.most_common(10)
            ],
            'top_posts': top_posts[:8],
            'last_actions': last_actions[:8],
            'market': market,
        }

    @classmethod
    def news_digest(cls, simulation_id: str, limit: int = 12) -> Dict[str, Any]:
        """把高互动帖子/评论整理成一份"新闻头条"。

        这是派生视图，不是独立新闻平台：它只按互动量排序，保留原文与来源，
        让报告和 UI 有一个可快速扫读的入口。
        """
        # 只读帖子/评论 DB（上限 800 条/平台），不扫全量 actions——
        # 新闻台会被前端每 3 秒轮询，不能每次都做全量扫描。
        items = []
        for platform in cls.platforms(simulation_id):
            db_data = cls._read_platform_db(simulation_id, platform)
            items.extend(db_data['posts'])
            items.extend(db_data['comments'])
        items.sort(
            key=lambda item: (
                _safe_int(item.get('likes')) * 3 + _safe_int(item.get('shares')) * 2,
                str(item.get('timestamp') or ''),
            ),
            reverse=True,
        )
        headlines = []
        seen = set()
        for item in items:
            content = str(item.get('content') or '').strip()
            if not content:
                continue
            title = re.split(r'[。！？!?\n]', content)[0].strip()
            if not title or title in seen:
                continue
            seen.add(title)
            headlines.append({
                'source_id': item.get('source_id'),
                'title': title[:90],
                'platform': item.get('platform'),
                'agent': item.get('agent'),
                'likes': _safe_int(item.get('likes')),
                'shares': _safe_int(item.get('shares')),
                'content': content[:MAX_CONTENT_CHARS],
            })
            if len(headlines) >= max(1, min(int(limit or 12), 30)):
                break
        return {
            'simulation_id': simulation_id,
            'count': len(headlines),
            'headlines': headlines,
        }

    @classmethod
    def overview_text(cls, simulation_id: str) -> str:
        """把概览压成一段给 LLM 的文本，供大纲/章节 prompt 使用。"""
        data = cls.overview(simulation_id)
        lines = [
            f"模拟状态: {data['status'] or '未知'}",
            f"参与实体: {data['entities_count']}，生成人设: {data['profiles_count']}",
            f"平台: {', '.join(data['platforms'])}",
        ]
        tc = data['time_config']
        if tc.get('total_simulation_hours'):
            lines.append(
                f"时间配置: {tc['total_simulation_hours']}h / {tc['minutes_per_round']}min per round"
                + (f"（计划 {tc['planned_rounds']} 轮）" if tc.get('planned_rounds') else '')
            )
        for platform, counts in data['per_platform'].items():
            lines.append(
                f"{platform}: {counts['posts']} posts / {counts['comments']} comments / "
                f"{counts['actions']} actions / {counts['users']} users"
            )
        if data['action_types']:
            lines.append("动作分布: " + ', '.join(f"{k}={v}" for k, v in data['action_types'].items()))
        market = data.get('market') or {}
        if market.get('enabled'):
            lines.append(
                f"预测市场: YES {market.get('price_yes')} / NO {market.get('price_no')}"
                f"（成交量 {market.get('volume')}，{market.get('trades_count')} 笔）"
            )
        if data['top_agents']:
            lines.append("最活跃 Agent: " + ', '.join(
                f"{a['agent']}({a['actions']})" for a in data['top_agents'][:6]
            ))
        if data['top_posts']:
            lines.append("高互动帖子:")
            for post in data['top_posts'][:5]:
                lines.append(
                    f"- [{post['platform']}] {post.get('agent', '')} "
                    f"(likes={post.get('likes', 0)}): {post['content'][:160]}"
                )
        if data['top_posts']:
            lines.append("新闻头条（按互动量）:")
            for post in data['top_posts'][:3]:
                title = re.split(r'[。！？!?\n]', str(post.get('content') or ''))[0][:90]
                lines.append(f"- [{post['platform']}] {post.get('agent', '')}: {title}")
        return '\n'.join(lines)
