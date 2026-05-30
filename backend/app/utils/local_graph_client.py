"""
LocalGraphClient — zep_cloud.client.Zep 的完全替代
使用本地 SQLite + DeepSeek 提取，与 zep_cloud API 完全兼容
"""
from typing import Any, Dict, List, Optional


class _GraphAPI:
    """graph.* 方法集合 (对应 zep_cloud Zep.client.graph.*)"""

    def __init__(self, store, extractor):
        self._store = store
        self._extractor = extractor
        self.node = _NodeAPI(store)
        self.edge = _EdgeAPI(store)
        self.episode = _EpisodeAPI(store)

    def create(self, graph_id: str, name: str = "", description: str = ""):
        self._store.create_graph(graph_id, name, description)

    def delete(self, graph_id: str):
        self._store.delete_graph(graph_id)

    def set_ontology(self, graph_ids: List[str], entities: dict = None,
                     edges: dict = None):
        if graph_ids:
            ontology = {
                "entity_types": [
                    {"name": name} for name in (entities or {}).keys()
                ],
                "edge_types": [
                    {"name": name} for name in (edges or {}).keys()
                ],
            }
            for gid in graph_ids:
                self._store.set_ontology(gid, ontology)

    def add(self, graph_id: str, type: str = "text", data: str = ""):
        ep = self._store.add_episode(graph_id, data, type)
        ontology = self._store.get_ontology(graph_id)
        self._extractor.extract_from_text(graph_id, data, ontology)
        return ep

    def add_batch(self, graph_id: str, episodes: list) -> list:
        texts = []
        result_episodes = []
        for ep in episodes:
            data_text = getattr(ep, 'data', str(ep))
            type_text = getattr(ep, 'type', 'text')
            ep_obj = self._store.add_episode(graph_id, data_text, type_text)
            result_episodes.append(ep_obj)
            texts.append(data_text)
        ontology = self._store.get_ontology(graph_id)
        self._extractor.extract_from_batch(graph_id, texts, ontology)
        return result_episodes

    def search(self, graph_id: str, query: str = "", limit: int = 10,
               scope: str = "edges", reranker: str = "cross_encoder"):
        return self._extractor.semantic_search(
            graph_id, query, scope=scope, limit=limit, reranker=reranker
        )


class _NodeAPI:
    """graph.node.* 方法集合"""

    def __init__(self, store):
        self._store = store

    def get(self, uuid_: str):
        return self._store.get_node(uuid_)

    def get_by_graph_id(self, graph_id: str, limit: int = 100,
                        uuid_cursor: str = "", **kwargs):
        return self._store.get_nodes_by_graph(graph_id, limit, uuid_cursor)

    def get_entity_edges(self, node_uuid: str):
        return self._store.get_entity_edges(node_uuid)


class _EdgeAPI:
    """graph.edge.* 方法集合"""

    def __init__(self, store):
        self._store = store

    def get_by_graph_id(self, graph_id: str, limit: int = 100,
                        uuid_cursor: str = "", **kwargs):
        return self._store.get_edges_by_graph(graph_id, limit, uuid_cursor)


class _EpisodeAPI:
    """graph.episode.* 方法集合"""

    def __init__(self, store):
        self._store = store

    def get(self, uuid_: str):
        return self._store.get_episode(uuid_)


class LocalGraphClient:
    """
    zep_cloud.client.Zep 的直接替代

    Usage:
        client = LocalGraphClient(api_key="ignored")
        client.graph.create(graph_id="g1", name="Test")
        client.graph.add_batch(graph_id="g1", episodes=[...])
    """

    def __init__(self, api_key: str = "", base_url: str = ""):
        # 延迟导入以避免循环依赖
        from ..services.local_graph_store import LocalGraphStore
        from ..services.deepseek_graph_extractor import DeepSeekGraphExtractor
        self._store = LocalGraphStore.get_instance()
        self._extractor = DeepSeekGraphExtractor()
        self.graph = _GraphAPI(self._store, self._extractor)
