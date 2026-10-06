"""清理历史遗留的重复实体与重复边（对应 docs/OPTIMIZATION.md 1.8）。

背景：旧版 `LocalGraphStore.upsert_node` / `upsert_edge` 以每次抽取新生成的 uuid
为主键，并且用的是 INSERT OR REPLACE。结果是**每点一次「构建图谱」，同一实体就会
再插一行**——实测一个图里 8167 行节点其实只对应 794 个不同名字，`SAP` 有 440 个副本。
旧边则各自指向自己那一轮的新 node uuid，所以看着不重复，其实同样在累积。

代码层已经改成按身份归并（见 `local_graph_store.upsert_node` / `upsert_edge`），
新写入不会再累积；**但已经写进库里的重复行不会自动消失**，这个脚本负责清理。

做法：先算出完整方案（包括把边重新指向规范节点后，哪些边变成了完全相同的重复），
再一次性执行。默认只演练。

安全设计：
- 默认 dry-run，只报告不写入；
- `--apply` 才写入，且写入前自动备份整份数据库到 `<db>.bak-<时间戳>`；
- 合并时优先保留**被边引用最多**的 uuid，并把边重新指向它，不产生孤儿边；
- 边只在 (graph_id, source, target, name, **fact**) 完全一致时才合并，
  带时间窗的不同事实不会被误并。

用法：
    cd backend
    uv run python scripts/dedupe_graph.py                  # 演练：只报告
    uv run python scripts/dedupe_graph.py --apply          # 执行（自动备份）
    uv run python scripts/dedupe_graph.py --graph <id> --apply
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

DEFAULT_DB = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads", "graphs.db")
)


def name_key(name: str) -> str:
    return (name or "").strip().lower()


def ensure_schema_migrated(db_path: str):
    """复用应用自己的迁移，补上 name_key 列（并尝试建唯一索引）。

    脚本用的是自己的 sqlite3 连接，不会顺带触发 `LocalGraphStore.__init__` 里的迁移。
    直接 import 类也不够——只有**实例化**才会跑 `_init_db` / `_migrate_schema`。
    这里调用同一个类，保证脚本与后端的 schema 演进永远一致。
    """
    backend = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from app.services.local_graph_store import LocalGraphStore

    LocalGraphStore(db_path)


def _merge_json_list(a, b):
    out = []
    for raw in (a, b):
        for item in (json.loads(raw) if raw else []):
            if item not in out:
                out.append(item)
    return out


def _merge_json_dict(base, extra):
    merged = dict(json.loads(base) if base else {})
    for k, v in (json.loads(extra) if extra else {}).items():
        merged.setdefault(k, v)
    return merged


def plan_graph(conn, graph_id):
    """算出一张图的完整清理方案，不做任何写入。"""
    groups = conn.execute(
        "SELECT lower(trim(name)) AS k FROM nodes WHERE graph_id=? "
        "GROUP BY k HAVING COUNT(*) > 1",
        (graph_id,),
    ).fetchall()

    ref_counts = {
        r["uuid_"]: r["c"]
        for r in conn.execute(
            "SELECT n.uuid_ AS uuid_, (SELECT COUNT(*) FROM edges e "
            "WHERE e.source_node_uuid=n.uuid_ OR e.target_node_uuid=n.uuid_) AS c "
            "FROM nodes n WHERE n.graph_id=?",
            (graph_id,),
        )
    }

    node_updates = []   # (canonical_uuid, merged_name, labels, attrs, summary)
    victim_nodes = []   # 待删除节点 uuid
    mapping = {}        # victim_uuid -> canonical_uuid

    for g in groups:
        rows = conn.execute(
            "SELECT * FROM nodes WHERE graph_id=? AND lower(trim(name))=? "
            "ORDER BY created_at, uuid_",
            (graph_id, g["k"]),
        ).fetchall()
        # 被边引用最多的当规范节点；并列时用 created_at/uuid 保证确定性
        canonical = sorted(
            rows, key=lambda r: (-ref_counts.get(r["uuid_"], 0), r["created_at"] or "", r["uuid_"])
        )[0]
        victims = [r for r in rows if r["uuid_"] != canonical["uuid_"]]

        labels = canonical["labels"] or "[]"
        attrs = canonical["attributes"] or "{}"
        summary = canonical["summary"] or ""
        for v in victims:
            labels = json.dumps(_merge_json_list(labels, v["labels"] or "[]"), ensure_ascii=False)
            attrs = json.dumps(_merge_json_dict(attrs, v["attributes"] or "{}"), ensure_ascii=False)
            summary = summary or (v["summary"] or "")

        node_updates.append(
            (canonical["uuid_"], canonical["name"], labels, attrs, summary)
        )
        for v in victims:
            victim_nodes.append(v["uuid_"])
            mapping[v["uuid_"]] = canonical["uuid_"]

    # 边：先按映射算重指向后的身份，再找完全重复的
    edges = conn.execute("SELECT * FROM edges WHERE graph_id=?", (graph_id,)).fetchall()
    seen = {}
    victim_edges = []
    repointed = 0
    for e in edges:
        src = mapping.get(e["source_node_uuid"], e["source_node_uuid"])
        tgt = mapping.get(e["target_node_uuid"], e["target_node_uuid"])
        if src != e["source_node_uuid"] or tgt != e["target_node_uuid"]:
            repointed += 1
        key = (src, tgt, e["name"], e["fact"])
        if key in seen:
            victim_edges.append(e["uuid_"])   # 已被更早的一条代表，删除
        else:
            seen[key] = e["uuid_"]

    return {
        "graph_id": graph_id,
        "groups": len(groups),
        "nodes_before": conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE graph_id=?", (graph_id,)).fetchone()[0],
        "edges_before": conn.execute(
            "SELECT COUNT(*) FROM edges WHERE graph_id=?", (graph_id,)).fetchone()[0],
        "node_updates": node_updates,
        "victim_nodes": victim_nodes,
        "mapping": mapping,
        "repointed": repointed,
        "victim_edges": victim_edges,
    }


def apply_plan(conn, plan):
    for uuid_, name, labels, attrs, summary in plan["node_updates"]:
        conn.execute(
            "UPDATE nodes SET name=?, name_key=?, labels=?, attributes=?, summary=? "
            "WHERE uuid_=?",
            (name, name_key(name), labels, attrs, summary, uuid_),
        )

    # 先删重复边，再重指向。顺序很重要：若库里已经存在 edges 唯一索引
    # （老库恰好没有重复边时迁移就会建成功），重指向过程中会撞唯一约束。
    # 先删掉多余的那一条，剩下的每条边在目标身份上就都是唯一的了。
    if plan["victim_edges"]:
        ph = ",".join("?" * len(plan["victim_edges"]))
        conn.execute(f"DELETE FROM edges WHERE uuid_ IN ({ph})", plan["victim_edges"])

    # 把边指向规范节点，按 canonical 分组以减少 SQL 次数
    by_canonical = {}
    for victim, canonical in plan["mapping"].items():
        by_canonical.setdefault(canonical, []).append(victim)
    for canonical, victims in by_canonical.items():
        ph = ",".join("?" * len(victims))
        conn.execute(
            f"UPDATE edges SET source_node_uuid=? WHERE source_node_uuid IN ({ph})",
            (canonical, *victims),
        )
        conn.execute(
            f"UPDATE edges SET target_node_uuid=? WHERE target_node_uuid IN ({ph})",
            (canonical, *victims),
        )

    if plan["victim_nodes"]:
        ph = ",".join("?" * len(plan["victim_nodes"]))
        conn.execute(f"DELETE FROM nodes WHERE uuid_ IN ({ph})", plan["victim_nodes"])

    conn.commit()


def main():
    ap = argparse.ArgumentParser(description="合并重复实体与重复边（OPTIMIZATION.md 1.8）")
    ap.add_argument("--db", default=DEFAULT_DB, help="数据库路径")
    ap.add_argument("--graph", default="", help="只处理指定 graph_id")
    ap.add_argument("--apply", action="store_true", help="真正写入（会自动备份）")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"数据库不存在: {args.db}")
        return 1

    if args.apply:
        backup = f"{args.db}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(args.db, backup)
        print(f"已备份数据库 -> {backup}\n")
        # 只用 --apply 时迁移 schema：演练应当保持纯只读
        ensure_schema_migrated(args.db)
    else:
        print("演练模式（不写入）。加 --apply 才会真正合并。\n")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    if args.graph:
        graph_ids = [args.graph]
    else:
        graph_ids = [r["graph_id"] for r in conn.execute(
            "SELECT DISTINCT graph_id FROM nodes ORDER BY graph_id")]

    plans = [p for p in (plan_graph(conn, gid) for gid in graph_ids) if p["groups"]]
    if not plans:
        print("没有发现重复数据，无需处理。")
        return 0

    tot_nodes = tot_repoint = tot_edges = 0
    for p in plans:
        if args.apply:
            apply_plan(conn, p)
            after = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE graph_id=?", (p["graph_id"],)).fetchone()[0]
            edges_after = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE graph_id=?", (p["graph_id"],)).fetchone()[0]
        else:
            after = p["nodes_before"] - len(p["victim_nodes"])
            edges_after = p["edges_before"] - len(p["victim_edges"])

        tot_nodes += len(p["victim_nodes"])
        tot_repoint += p["repointed"]
        tot_edges += len(p["victim_edges"])

        print(f"图 {p['graph_id']}")
        print(f"  重复组     : {p['groups']}")
        print(f"  节点       : {p['nodes_before']} -> {after}  (合并掉 {len(p['victim_nodes'])})")
        print(f"  边         : {p['edges_before']} -> {edges_after}  "
              f"(重指向 {p['repointed']}，去重删除 {len(p['victim_edges'])})")

    print(f"\n合计: 节点 -{tot_nodes}, 边重指向 {tot_repoint}, 边删除 {tot_edges}")

    if args.apply:
        for index_name, table, columns in (
            ("idx_nodes_graph_name_key", "nodes", "graph_id, name_key"),
            ("idx_edges_identity", "edges",
             "graph_id, source_node_uuid, target_node_uuid, name, fact"),
        ):
            try:
                conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table}({columns})")
                conn.commit()
                print(f"唯一索引 {index_name} 已建立")
            except sqlite3.IntegrityError as exc:
                print(f"仍无法建立 {index_name}: {exc}")
                return 1
        print("此后重建图谱不会再产生重复实体/边。")
    print("\n完成。" if args.apply else "\n演练结束。确认无误后加 --apply 执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
