"""
DeepSeek 驱动的知识图谱提取器
使用 LLM 从文本中提取实体、关系，并支持语义搜索
"""
import json
import re
import uuid as uuid_mod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from ..config import Config
from .local_graph_store import (
    LocalGraphStore, LocalNode, LocalEdge, LocalSearchResult
)

logger = get_logger('mirofish.graph_extractor')


class DeepSeekGraphExtractor:
    """使用 DeepSeek API 提取知识图谱实体和关系"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()
        self.store = LocalGraphStore.get_instance()

    def extract_from_text(
        self, graph_id: str, text: str, ontology: dict = None
    ) -> dict:
        """
        从文本中提取实体和关系

        Args:
            graph_id: 图谱 ID
            text: 输入文本
            ontology: 本体定义（实体类型和关系类型）

        Returns:
            {"nodes": [...], "edges": [...]}
        """
        entity_types = []
        edge_types = []
        if ontology:
            entity_types = [e.get("name", "") for e in ontology.get("entity_types", [])]
            edge_types = [e.get("name", "") for e in ontology.get("edge_types", [])]

        prompt = self._build_extraction_prompt(text, entity_types, edge_types)

        try:
            result = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=4096
            )
            return self._process_extraction_result(graph_id, result)
        except Exception as e:
            logger.warning(f"实体提取失败: {e}，使用规则回退")
            return {"nodes": [], "edges": []}

    def extract_from_batch(
        self, graph_id: str, texts: List[str], ontology: dict = None
    ) -> dict:
        """批量提取，合并结果并去重"""
        all_nodes = {}
        all_edges = {}

        for text in texts:
            if not text or not text.strip():
                continue
            result = self.extract_from_text(graph_id, text, ontology)
            for node in result.get("nodes", []):
                key = node.name.lower().strip()
                if key and key not in all_nodes:
                    all_nodes[key] = node
            for edge in result.get("edges", []):
                key = f"{edge.source_node_uuid}|{edge.name}|{edge.target_node_uuid}"
                if key not in all_edges:
                    all_edges[key] = edge

        return {"nodes": list(all_nodes.values()), "edges": list(all_edges.values())}

    def semantic_search(
        self, graph_id: str, query: str, scope: str = "edges",
        limit: int = 10, reranker: str = "cross_encoder"
    ) -> LocalSearchResult:
        """
        语义搜索：使用 DeepSeek 对候选进行相关性评分

        Args:
            graph_id: 图谱 ID
            query: 搜索查询
            scope: "edges" / "nodes" / "both"
            limit: 返回数量
            reranker: 忽略（本地用 LLM 评分替代）
        """
        edges = []
        nodes = []

        # 1. 关键词粗筛
        if scope in ("edges", "both"):
            edges = self.store.search_edges(graph_id, query, limit * 3)
        if scope in ("nodes", "both"):
            nodes = self.store.search_nodes(graph_id, query, limit * 3)

        # 2. 如果结果不多，直接返回
        if len(edges) + len(nodes) <= limit:
            return LocalSearchResult(edges=edges[:limit], nodes=nodes[:limit])

        # 3. 使用 DeepSeek 精排
        if edges:
            edges = self._rerank_edges(query, edges, limit)
        if nodes:
            nodes = self._rerank_nodes(query, nodes, limit)

        return LocalSearchResult(edges=edges[:limit], nodes=nodes[:limit])

    def _rerank_edges(self, query: str, edges: List[LocalEdge], limit: int) -> List[LocalEdge]:
        """使用 DeepSeek 对边进行语义重排序"""
        if len(edges) <= limit:
            return edges

        items_text = "\n".join(
            f"[{i}] {e.fact} (关系: {e.name})"
            for i, e in enumerate(edges)
        )
        try:
            response = self.llm.chat_json(
                messages=[{
                    "role": "system",
                    "content": "你是信息检索专家。根据查询对结果进行相关性排序。输出格式: {\"ranked_ids\": [最相关的条目序号, ...]}"
                }, {
                    "role": "user",
                    "content": f"查询: {query}\n\n候选条目:\n{items_text}\n\n返回最相关的 {limit} 个条目的序号列表（JSON格式）。"
                }],
                temperature=0.1,
                max_tokens=1024
            )
            ranked_ids = response.get("ranked_ids", [])
            id_set = set(ranked_ids[:limit])
            ranked = [edges[i] for i in ranked_ids[:limit] if 0 <= i < len(edges)]
            # 追加未被 LLM 选中但在范围内的
            for i, e in enumerate(edges):
                if i not in id_set and len(ranked) < limit:
                    ranked.append(e)
            return ranked[:limit]
        except Exception as e:
            logger.warning(f"边重排序失败: {e}")
            return edges[:limit]

    def _rerank_nodes(self, query: str, nodes: List[LocalNode], limit: int) -> List[LocalNode]:
        """使用 DeepSeek 对节点进行语义重排序"""
        if len(nodes) <= limit:
            return nodes

        items_text = "\n".join(
            f"[{i}] {n.name} ({', '.join(n.labels)}): {n.summary[:200]}"
            for i, n in enumerate(nodes)
        )
        try:
            response = self.llm.chat_json(
                messages=[{
                    "role": "system",
                    "content": "你是信息检索专家。根据查询对结果进行相关性排序。输出格式: {\"ranked_ids\": [最相关的条目序号, ...]}"
                }, {
                    "role": "user",
                    "content": f"查询: {query}\n\n候选条目:\n{items_text}\n\n返回最相关的 {limit} 个条目的序号列表（JSON格式）。"
                }],
                temperature=0.1,
                max_tokens=1024
            )
            ranked_ids = response.get("ranked_ids", [])
            id_set = set(ranked_ids[:limit])
            ranked = [nodes[i] for i in ranked_ids[:limit] if 0 <= i < len(nodes)]
            for i, n in enumerate(nodes):
                if i not in id_set and len(ranked) < limit:
                    ranked.append(n)
            return ranked[:limit]
        except Exception as e:
            logger.warning(f"节点重排序失败: {e}")
            return nodes[:limit]

    def _build_extraction_prompt(
        self, text: str, entity_types: List[str], edge_types: List[str]
    ) -> str:
        type_hint = ""
        if entity_types:
            type_hint += f"\n实体类型: {', '.join(entity_types)}"
        if edge_types:
            type_hint += f"\n关系类型: {', '.join(edge_types)}"

        return f"""从以下文本中提取所有实体及其关系。{type_hint}

文本:
```
{text[:3000]}
```

返回严格的 JSON 格式（不要包含 markdown 代码块）:
{{
  "entities": [
    {{
      "name": "实体名称",
      "type": "实体类型（如：人物/组织/事件/概念/地点）",
      "summary": "一句话描述该实体在文本中的角色和关键信息"
    }}
  ],
  "relations": [
    {{
      "source": "源实体名称",
      "target": "目标实体名称",
      "relation": "关系名称（如：发表/点赞/反对/属于/参与/影响/提及）",
      "fact": "描述该关系的一句话事实"
    }}
  ]
}}

规则:
1. 实体名称必须精确，同名实体只提取一次
2. 每条关系必须对应文本中的具体事实
3. 如果文本中没有明确的关系，relations 可以为空数组
4. 只输出 JSON，不要输出任何解释"""

    def _get_system_prompt(self) -> str:
        return """你是知识图谱构建专家。你的任务是从文本中精确提取实体和关系。
- 实体是文本中提到的人、组织、事件、概念、地点等
- 关系是两个实体之间的具体交互或联系
- 只提取文本中明确提到的信息，不要推断或编造
- 实体名称使用文本中的原始表述
- 始终输出有效的 JSON"""

    def _process_extraction_result(self, graph_id: str, result: dict) -> dict:
        """将 LLM 提取结果转换为 LocalNode/LocalEdge 并存储"""
        now = datetime.now(timezone.utc).isoformat()
        nodes = []
        edges = []
        name_to_uuid = {}

        # 先创建所有节点
        for ent in result.get("entities", []):
            name = (ent.get("name") or "").strip()
            if not name:
                continue
            node_uuid = str(uuid_mod.uuid4())
            name_to_uuid[name.lower()] = node_uuid
            node = LocalNode(
                uuid_=node_uuid,
                name=name,
                labels=[ent.get("type", "Unknown")],
                summary=ent.get("summary", ""),
                attributes=ent.get("attributes", {}),
                graph_id=graph_id,
                created_at=now,
            )
            self.store.upsert_node(node)
            nodes.append(node)

        # 创建边
        for rel in result.get("relations", []):
            source_name = (rel.get("source") or "").strip()
            target_name = (rel.get("target") or "").strip()
            if not source_name or not target_name:
                continue

            source_uuid = name_to_uuid.get(source_name.lower())
            target_uuid = name_to_uuid.get(target_name.lower())

            # 如果源或目标实体不存在，创建占位节点
            if not source_uuid:
                source_uuid = str(uuid_mod.uuid4())
                name_to_uuid[source_name.lower()] = source_uuid
                node = LocalNode(
                    uuid_=source_uuid, name=source_name,
                    labels=["Unknown"], summary="",
                    attributes={}, graph_id=graph_id, created_at=now,
                )
                self.store.upsert_node(node)
                nodes.append(node)

            if not target_uuid:
                target_uuid = str(uuid_mod.uuid4())
                name_to_uuid[target_name.lower()] = target_uuid
                node = LocalNode(
                    uuid_=target_uuid, name=target_name,
                    labels=["Unknown"], summary="",
                    attributes={}, graph_id=graph_id, created_at=now,
                )
                self.store.upsert_node(node)
                nodes.append(node)

            edge = LocalEdge(
                uuid_=str(uuid_mod.uuid4()),
                name=rel.get("relation", "RELATED_TO"),
                fact=rel.get("fact", f"{source_name} -> {target_name}"),
                source_node_uuid=source_uuid,
                target_node_uuid=target_uuid,
                attributes=rel.get("attributes", {}),
                graph_id=graph_id,
                created_at=now,
            )
            self.store.upsert_edge(edge)
            edges.append(edge)

        return {"nodes": nodes, "edges": edges}
