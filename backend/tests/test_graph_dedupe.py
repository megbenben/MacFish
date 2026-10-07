"""节点/边去重的回归测试。

不需要网络：抽取器只喂一份构造好的 LLM 结果，不调用任何模型。
"""
import json
import os
import sqlite3
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCRIPTS = os.path.join(BACKEND, 'scripts')
for p in (BACKEND, SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.services.local_graph_store import LocalGraphStore, LocalNode, LocalEdge  # noqa: E402
from app.services.deepseek_graph_extractor import DeepSeekGraphExtractor  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    s = LocalGraphStore(str(tmp_path / "graphs.db"))
    # nodes/edges 对 graphs 有外键约束（PRAGMA foreign_keys=ON），先建图
    for gid in ("g", "g1", "g2"):
        s.create_graph(gid, name=gid)
    return s


@pytest.fixture()
def extractor(store, monkeypatch):
    # 抽取器默认会 get_instance() 指向真实的 uploads/graphs.db，测试必须把它
    # 重定向到临时库，否则会污染用户数据。llm_client 传哑对象——该路径不碰模型。
    monkeypatch.setattr(
        LocalGraphStore, "get_instance",
        classmethod(lambda cls, *a, **k: store),
    )
    return DeepSeekGraphExtractor(llm_client=object())


EXTRACTION = {
    "entities": [
        {"name": "SAP", "type": "Organization", "summary": "ERP 厂商"},
        {"name": "MES", "type": "Concept", "summary": "制造执行系统"},
    ],
    "relations": [
        {"source": "SAP", "target": "MES", "relation": "SUPPLIES", "fact": "SAP 提供 MES 集成"},
    ],
}


# --- 存储层 ---

def test_same_name_reuses_uuid_and_merges(store):
    first = store.upsert_node(LocalNode(
        uuid_="u1", name="SAP", labels=["Organization"],
        summary="第一版", attributes={"a": 1}, graph_id="g"))
    second = store.upsert_node(LocalNode(
        uuid_="u2", name="SAP", labels=["Concept"],
        summary="", attributes={"b": 2}, graph_id="g"))

    assert second.uuid_ == "u1", "再次写入必须复用已有 uuid"
    assert len(store.get_nodes_by_graph("g", limit=100)) == 1
    merged = store.get_node("u1")
    assert set(merged.labels) == {"Organization", "Concept"}
    assert merged.attributes == {"a": 1, "b": 2}
    assert merged.summary == "第一版", "已有的非空 summary 不应被空值覆盖"


def test_name_key_ignores_case_and_whitespace(store):
    store.upsert_node(LocalNode(uuid_="u1", name="SAP", labels=[], summary="",
                                attributes={}, graph_id="g"))
    again = store.upsert_node(LocalNode(uuid_="u2", name="  sap ", labels=[], summary="",
                                        attributes={}, graph_id="g"))
    assert again.uuid_ == "u1"
    assert len(store.get_nodes_by_graph("g", limit=100)) == 1


def test_same_name_in_different_graphs_stays_separate(store):
    store.upsert_node(LocalNode(uuid_="u1", name="SAP", labels=[], summary="",
                                attributes={}, graph_id="g1"))
    store.upsert_node(LocalNode(uuid_="u2", name="SAP", labels=[], summary="",
                                attributes={}, graph_id="g2"))
    assert len(store.get_nodes_by_graph("g1", limit=10)) == 1
    assert len(store.get_nodes_by_graph("g2", limit=10)) == 1


def test_repeated_edge_merges_by_identity(store):
    common = dict(name="SUPPLIES", fact="SAP 提供 MES 集成", source_node_uuid="n1",
                  target_node_uuid="n2", attributes={}, graph_id="g")
    store.upsert_edge(LocalEdge(uuid_="e1", **common))
    again = store.upsert_edge(LocalEdge(uuid_="e2", **common))
    assert again.uuid_ == "e1"
    assert len(store.get_edges_by_graph("g", limit=100)) == 1


def test_edges_with_different_facts_are_not_merged(store):
    common = dict(name="SUPPLIES", source_node_uuid="n1", target_node_uuid="n2",
                  attributes={}, graph_id="g")
    store.upsert_edge(LocalEdge(uuid_="e1", fact="事实甲", **common))
    store.upsert_edge(LocalEdge(uuid_="e2", fact="事实乙", **common))
    assert len(store.get_edges_by_graph("g", limit=100)) == 2


def test_unique_indexes_present_on_fresh_db(store):
    conn = sqlite3.connect(store.db_path)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND sql LIKE '%UNIQUE%'")}
    assert "idx_nodes_graph_name_key" in names
    assert "idx_edges_identity" in names


# --- 抽取器：重建图谱不应产生累积，且边不指向幽灵节点 ---

def test_repeated_build_does_not_grow(store, extractor):
    extractor._process_extraction_result("g", EXTRACTION)
    first_nodes = len(store.get_nodes_by_graph("g", limit=100))
    first_edges = len(store.get_edges_by_graph("g", limit=100))

    for _ in range(4):      # 模拟用户反复点「构建图谱」
        extractor._process_extraction_result("g", EXTRACTION)

    assert len(store.get_nodes_by_graph("g", limit=100)) == first_nodes == 2
    assert len(store.get_edges_by_graph("g", limit=100)) == first_edges == 1


def test_repeated_build_keeps_edges_attached_to_real_nodes(store, extractor):
    """最关键的一条：复用 uuid 后，边不能指向已被合并掉的旧 uuid。"""
    for _ in range(3):
        extractor._process_extraction_result("g", EXTRACTION)

    node_uuids = {n.uuid_ for n in store.get_nodes_by_graph("g", limit=100)}
    edges = store.get_edges_by_graph("g", limit=100)
    assert edges, "应当至少有一条边"
    for e in edges:
        assert e.source_node_uuid in node_uuids, "边的源端指向了不存在的节点"
        assert e.target_node_uuid in node_uuids, "边的目标端指向了不存在的节点"


# --- 兼容老库（无 name_key 列 + 已有重复）---

LEGACY_SCHEMA = """
CREATE TABLE graphs (graph_id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '', ontology TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE nodes (uuid_ TEXT PRIMARY KEY, graph_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '', labels TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '', attributes TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE edges (uuid_ TEXT PRIMARY KEY, graph_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '', fact TEXT NOT NULL DEFAULT '',
    source_node_uuid TEXT NOT NULL DEFAULT '', target_node_uuid TEXT NOT NULL DEFAULT '',
    attributes TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT (datetime('now')),
    valid_at TEXT NOT NULL DEFAULT '', invalid_at TEXT NOT NULL DEFAULT '',
    expired_at TEXT NOT NULL DEFAULT '', episodes TEXT NOT NULL DEFAULT '[]');
CREATE TABLE episodes (uuid_ TEXT PRIMARY KEY, graph_id TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '', type TEXT NOT NULL DEFAULT 'text',
    processed INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT (datetime('now')));
"""


def _legacy_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute("INSERT INTO graphs (graph_id, name) VALUES ('g', 'legacy')")
    # 同一实体 3 个副本（旧实现每轮换新 uuid），边各自指向自己那一轮的 uuid
    for i, uuid_ in enumerate(["n1", "n2", "n3"]):
        conn.execute(
            "INSERT INTO nodes (uuid_, graph_id, name, labels, summary, attributes, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uuid_, "g", "SAP", json.dumps(["Organization"]), f"第{i}版",
             json.dumps({"round": i}), f"2026-10-0{i+1}T00:00:00"))
    for i, uuid_ in enumerate(["e1", "e2", "e3"]):
        conn.execute(
            "INSERT INTO edges (uuid_, graph_id, name, fact, source_node_uuid, "
            "target_node_uuid, attributes, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (uuid_, "g", "SUPPLIES", "SAP 提供 MES 集成", f"n{i+1}", f"n{i+1}",
             "{}", f"2026-10-0{i+1}T00:00:00"))
    conn.commit()
    conn.close()


def test_legacy_db_migrates_without_raising(tmp_path):
    path = str(tmp_path / "legacy.db")
    _legacy_db(path)

    # 关键：老库有重复数据，迁移必须优雅降级而不是抛异常（否则整个应用起不来）
    store = LocalGraphStore(path)
    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(nodes)")}
    assert "name_key" in cols, "迁移应当补上 name_key 列"
    assert conn.execute(
        "SELECT name_key FROM nodes WHERE uuid_='n1'").fetchone()[0] == "sap"
    unique = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND sql LIKE '%UNIQUE%'")}
    assert "idx_nodes_graph_name_key" not in unique, "有重复时不应强行建唯一索引"
    assert store.get_node("n1") is not None


def test_repair_script_cleans_legacy_db(tmp_path):
    import dedupe_graph

    path = str(tmp_path / "legacy2.db")
    _legacy_db(path)
    LocalGraphStore(path)      # 先跑一次迁移，补上 name_key

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    plan = dedupe_graph.plan_graph(conn, "g")
    assert plan["groups"] == 1
    assert len(plan["victim_nodes"]) == 2          # 3 个副本 -> 留 1

    dedupe_graph.apply_plan(conn, plan)
    nodes = conn.execute("SELECT * FROM nodes WHERE graph_id='g'").fetchall()
    assert len(nodes) == 1

    # 留下的那条应当是合并结果，且所有边都指向它
    survivor = nodes[0]["uuid_"]
    assert json.loads(nodes[0]["attributes"]) == {"round": 0}
    edges = conn.execute("SELECT * FROM edges WHERE graph_id='g'").fetchall()
    assert len(edges) == 1, "3 条一模一样的边应当被合并成 1 条"
    assert edges[0]["source_node_uuid"] == survivor
    assert edges[0]["target_node_uuid"] == survivor

    # 清理完就能建起唯一索引
    conn.execute("CREATE UNIQUE INDEX idx_nodes_graph_name_key ON nodes(graph_id, name_key)")
