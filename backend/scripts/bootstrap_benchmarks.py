"""
生成合成演示案卷与桩件 fixture
----------------------------
**这些案卷是合成数据。** 它们用来验证回测框架本身能跑通（加载、防泄漏校验、
编排、评分、聚合），**永远不构成有效性证据**——种子材料与"真实结果"都是本脚本
编造的，不是历史事实。真实案卷应当由使用者用真实历史材料手写录入。

之所以把生成器留下来而不是只提交 JSON：让 fixture 的来源透明、可复现、可审计。

用法：
    cd backend && uv run python scripts/bootstrap_benchmarks.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CASE_ROOT = os.path.join(BACKEND, 'benchmarks')
FIXTURE_ROOT = os.path.join(BACKEND, 'tests', 'fixtures', 'benchmark')


# ══════════════════════════════════════════════════════════════
# 案卷一：电动车价格战（合成）
# ══════════════════════════════════════════════════════════════

CASE_1 = {
    "schema_version": 1,
    "case_id": "synthetic_ev_price_war_001",
    "title": "合成演示：某细分市场是否会继续降价（30 天）",
    "synthetic": True,
    "synthetic_rationale": "种子材料与真实结果均为本仓库编造，仅用于验证回测框架本身，不是历史事实。",
    "domain": "consumer_durables",
    "locale": "zh",
    "simulation_requirement": "推演未来 30 天该细分市场的终端成交价走向，以及主要参与者的公开表态如何演化。",
    "seed": {
        "documents": [
            {"path": "corpus/industry_note.md", "doc_date": "2026-01-05"},
            {"path": "corpus/channel_feedback.md", "doc_date": "2026-01-20"},
        ],
        "corpus_as_of": "2026-01-20",
        "cutoff": "2026-02-01",
        "horizon_days": 30,
        "outcome_window": {"start": "2026-02-01", "end": "2026-03-03"},
    },
    "expectations": {
        "key_actors": [
            {"name": "启明汽车", "aliases": ["启明", "Qiming"]},
            {"name": "远驰集团", "aliases": ["远驰", "Yuanchi"]},
            {"name": "行业协会", "aliases": []},
        ],
        "questions": [
            {
                "id": "q1",
                "text": "主流车型终端成交价的方向？",
                "type": "direction",
                "options": ["up", "down", "flat"],
                "answer": "down",
                "signal_terms": {
                    "down": ["降价", "下调", "让利", "走低", "促销"],
                    "up": ["涨价", "上调", "提价"],
                    "flat": ["持平", "企稳", "稳定"],
                },
            },
            {
                "id": "q2",
                "text": "行业竞争格局的判断？",
                "type": "direction",
                "options": ["intensify", "ease"],
                "answer": "intensify",
                "signal_terms": {
                    "intensify": ["竞争加剧", "份额争夺", "以价换量"],
                    "ease": ["竞争缓和", "默契", "协同"],
                },
            },
        ],
        "report_min_sections": 3,
        "min_nodes": 4,
        "min_entities": 2,
        "min_actions": 5,
        "min_report_chars": 200,
    },
    "assumptions": {"assumed_entities": 12, "assumed_hours": 72, "minutes_per_round": 60, "assumed_sections": 4},
    "provenance": {"author": "bootstrap_benchmarks.py", "method": "synthetic"},
}

CORPUS_1_A = """# 细分市场月度观察

## 渠道库存
华东与华南两大区的经销商库存周转天数已连续两个月上升，部分门店的展车周转明显放缓。
渠道端反馈，2025 年末冲量带来的库存尚未消化完毕。

## 产能
主要厂商的新产线已于上一季度投产，名义产能较去年同期有明显提升。

## 参与者态度
启明汽车在近期的沟通会上强调"以用户价值为中心"，未对价格策略作出明确表态。
远驰集团则表示将"保持战略定力"。

## 需求侧
终端进店量环比走弱，消费者的观望情绪有所抬头。
"""

CORPUS_1_B = """# 渠道反馈纪要

- 多家华东经销商反映，同城比价现象明显增多，客户普遍在等待更优惠的时机。
- 部分门店开始自行组织店头活动以促成交，力度不一。
- 行业协会近期召集会议，讨论"维护良好市场秩序"，未形成约束性结论。
- 远驰集团的一家区域经销商表示，若竞争对手率先调整价格，他们会跟进。
- 启明汽车的一位区域负责人认为，短期内需求难以明显改善。
"""

GROUND_TRUTH_1 = {
    "case_id": "synthetic_ev_price_war_001",
    "synthetic": True,
    "outcome_summary": (
        "合成结果：窗口期内主流车型终端成交价继续走低，启明汽车率先调整官方指导价，"
        "远驰集团随后跟进，行业竞争进一步加剧。"
    ),
    "answers": {"q1": "down", "q2": "intensify"},
    "events": [
        {
            "id": "e1",
            "date": "2026-02-12",
            "text": "启明汽车调整官方指导价",
            "key_terms": ["启明", "指导价"],
            "source": "synthetic",
        },
        {
            "id": "e2",
            "date": "2026-02-20",
            "text": "远驰集团宣布跟进",
            "key_terms": ["远驰", "跟进"],
            "source": "synthetic",
        },
    ],
    "key_actors_observed": ["启明汽车", "远驰集团"],
    # 这两个词若出现在语料里，说明结果被泄漏进了输入
    "leakage_terms": ["指导价调整", "跟进降价"],
    # 定义结果的数量级；出现在语料里同样是泄漏
    "answer_numbers": ["15", "20", "2.6"],
    "sources": [{"url": "synthetic://not-a-real-source", "retrieved_at": "2026-10-06"}],
}


# ══════════════════════════════════════════════════════════════
# 案卷二：产品召回舆情（合成）
# ══════════════════════════════════════════════════════════════

CASE_2 = {
    "schema_version": 1,
    "case_id": "synthetic_recall_crisis_002",
    "title": "合成演示：某消费品召回事件的舆情走向（14 天）",
    "synthetic": True,
    "synthetic_rationale": "种子材料与真实结果均为本仓库编造，仅用于验证回测框架本身，不是历史事实。",
    "domain": "public_relations",
    "locale": "zh",
    "simulation_requirement": "推演未来 14 天该召回事件的舆情走向：官方回应能否平息质疑，舆情热度会上升还是回落。",
    "seed": {
        "documents": [
            {"path": "corpus/incident_brief.md", "doc_date": "2026-04-02"},
        ],
        "corpus_as_of": "2026-04-02",
        "cutoff": "2026-04-05",
        "horizon_days": 14,
        "outcome_window": {"start": "2026-04-05", "end": "2026-04-19"},
    },
    "expectations": {
        "key_actors": [
            {"name": "华臻电器", "aliases": ["华臻"]},
            {"name": "市场监管部门", "aliases": ["监管"]},
            {"name": "消费者权益组织", "aliases": []},
        ],
        "questions": [
            {
                "id": "q1",
                "text": "事件舆情热度的走向？",
                "type": "direction",
                "options": ["rise", "fall", "flat"],
                "answer": "rise",
                "signal_terms": {
                    "rise": ["热度上升", "持续发酵", "发酵", "扩散", "升温"],
                    "fall": ["热度回落", "平息", "降温", "熄火"],
                    "flat": ["维持", "持平"],
                },
            },
        ],
        "report_min_sections": 2,
        "min_nodes": 3,
        "min_entities": 2,
        "min_actions": 3,
        "min_report_chars": 150,
    },
    "assumptions": {"assumed_entities": 8, "assumed_hours": 48, "minutes_per_round": 60, "assumed_sections": 3},
    "provenance": {"author": "bootstrap_benchmarks.py", "method": "synthetic"},
}

CORPUS_2_A = """# 事件简报

## 概况
华臻电器于本周发布公告，称某批次产品在极端使用条件下存在安全隐患，将对相关批次提供免费检测。

## 已采取的措施
公司开通了客服专线，并在官网发布说明。公告未提及具体的补偿安排。

## 外部反应
部分消费者在社交平台表达担忧，亦有用户质疑公告表述过于笼统。
消费者权益组织表示正在关注此事，尚未公开发表正式意见。
市场监管部门暂未就此发布信息。
"""

GROUND_TRUTH_2 = {
    "case_id": "synthetic_recall_crisis_002",
    "synthetic": True,
    "outcome_summary": (
        "合成结果：舆情在第二周进一步发酵，第三方检测机构公布结果后热度升至高点，"
        "监管随后介入并要求补充说明，公司最终扩大了处理范围。"
    ),
    "answers": {"q1": "rise"},
    "events": [
        {
            "id": "e1",
            "date": "2026-04-10",
            "text": "第三方检测机构公布结果",
            "key_terms": ["检测", "公布"],
            "source": "synthetic",
        },
    ],
    "key_actors_observed": ["华臻电器", "市场监管部门", "消费者权益组织"],
    "leakage_terms": ["扩大处理范围", "补充说明"],
    "answer_numbers": ["4.8", "37"],
    "sources": [{"url": "synthetic://not-a-real-source", "retrieved_at": "2026-10-06"}],
}


# ══════════════════════════════════════════════════════════════
# 桩件 fixture（离线验证用，零 LLM）
# ══════════════════════════════════════════════════════════════

#: 三个变体各自把某一句换成不同说法；其余句子保持一致，
#: 于是"稳定"与"摇摆"两类断言都能出现
STABILITY_VARIANTS = {
    "run_a": {"核心发现": "渠道库存高企叠加产能释放，以价换量的动机较强，竞争加剧的可能性较高。"},
    "run_b": {"核心发现": "产能集中释放且渠道消化缓慢，厂商倾向于用价格换取份额，竞争强度可能继续上升。"},
    "run_c": {"核心发现": "在库存与产能的双重压力下，价格手段仍是主要竞争方式，格局难言缓和。"},
}


def stub_report(case_id: str, overrides: dict = None) -> str:
    """一份结构完整、能被离线指标打到分的假报告。"""
    overrides = overrides or {}
    return f"""# 未来预测报告

> 合成桩件报告，用于离线验证回测框架本身。

## 核心发现

启明汽车与远驰集团在窗口期内均面临份额压力，终端成交价存在继续下调的压力。
{overrides.get("核心发现", "渠道库存高企叠加产能释放，以价换量的动机较强，竞争加剧的可能性较高。")}

## 参与者行为预测

远驰集团的一家区域经销商表示，若竞争对手率先调整价格，他们会跟进。
行业协会的协调作用有限，短期内难以形成约束性结论。
消费者权益组织在另一类事件中的表现说明第三方表态会放大关注度。

## 趋势与风险

预计窗口期内出现降价与跟进降价的连锁反应，舆情焦点集中在价格与产品安全两条线。
若市场监管部门介入，事件节奏可能被打断。
"""


STUB_OUTLINE = {
    "title": "未来预测报告",
    "summary": "价格战延续与舆情升温的双线推演",
    "sections": [
        {"title": "核心发现", "content": ""},
        {"title": "参与者行为预测", "content": ""},
        {"title": "趋势与风险", "content": ""},
    ],
    "scenarios": [
        {
            "name": "价格战延续",
            "description": "主流厂商相继调整价格，份额争夺加剧。",
            "trigger_conditions": ["头部厂商发布调价公告", "渠道库存继续攀升"],
            "relative_likelihood": "高",
            "key_actors": ["启明汽车", "远驰集团"],
        },
        {
            "name": "监管介入打断",
            "description": "监管表态后各方转向合规叙事，价格竞争暂缓。",
            "trigger_conditions": ["监管部门公开表态", "行业协会出台约束性意见"],
            "relative_likelihood": "中",
            "key_actors": ["市场监管部门"],
        },
        {
            "name": "需求回暖化解",
            "description": "需求侧改善使降价压力自然缓解。",
            "trigger_conditions": ["进店量连续两周回升"],
            "relative_likelihood": "低",
            "key_actors": ["行业协会"],
        },
    ],
}


def build_stub_fixtures(case: dict, corpus_text: str) -> dict:
    """为某个案卷生成桩件产物。数值刻意做小，便于人工核对。"""
    return {
        "corpus.json": {
            "project_id": f"proj_stub_{case['case_id']}",
            "ontology": {"entity_types": [], "edge_types": []},
            "chunk_count": 6,
            # 填真实语料：否则"数字是否有出处"这类指标没有意义
            "text": corpus_text,
        },
        "graph.json": {
            "graph_id": f"mirofish_stub_{case['case_id']}",
            "node_count": 9,
            "edge_count": 11,
            "entity_count": 6,
            "nodes": [
                {"name": "启明汽车"}, {"name": "远驰集团"}, {"name": "行业协会"},
                {"name": "市场监管部门"}, {"name": "消费者权益组织"}, {"name": "经销商"},
            ],
        },
        "simulation.json": {
            "simulation_id": f"sim_stub_{case['case_id']}",
            "graph_id": f"mirofish_stub_{case['case_id']}",
            "entities_count": 6,
            "profiles_count": 6,
            "planned_rounds": 12,
        },
        "run.json": {
            "runner_status": "completed",
            "total_rounds": 12,
            "twitter_actions_count": 40,
            "reddit_actions_count": 35,
        },
        "report.json": {
            "status": "completed",
            "markdown": stub_report(case["case_id"]),
            "outline": STUB_OUTLINE,
        },
    }


def write_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)


def main() -> int:
    import hashlib

    cases = [
        (CASE_1, {"corpus/industry_note.md": CORPUS_1_A, "corpus/channel_feedback.md": CORPUS_1_B}, GROUND_TRUTH_1),
        (CASE_2, {"corpus/incident_brief.md": CORPUS_2_A}, GROUND_TRUTH_2),
    ]

    registry = {"schema_version": 1, "cases": []}

    for case, corpus_files, truth in cases:
        case_dir = os.path.join(CASE_ROOT, 'cases', case['case_id'])
        os.makedirs(case_dir, exist_ok=True)

        # 写入语料并登记 sha256（校验器会比对）
        documents = []
        for rel_path, content in corpus_files.items():
            full = os.path.join(case_dir, rel_path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w', encoding='utf-8') as handle:
                handle.write(content)
            digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
            meta = next(d for d in case['seed']['documents'] if d['path'] == rel_path)
            documents.append({**meta, "sha256": digest})
        case['seed']['documents'] = documents

        write_json(os.path.join(case_dir, 'case.json'), case)
        write_json(os.path.join(case_dir, 'ground_truth.json'), truth)

        # 桩件 fixture
        combined = '\n\n'.join(corpus_files.values())
        for filename, payload in build_stub_fixtures(case, combined).items():
            write_json(os.path.join(FIXTURE_ROOT, case['case_id'], filename), payload)

        # 稳定性变体：三次运行的报告部分重合、部分不同，
        # 用来验证"稳定/摇摆"分类确实能区分出两者
        for variant, extra in STABILITY_VARIANTS.items():
            write_json(
                os.path.join(FIXTURE_ROOT, case['case_id'], 'variants', variant, 'report.json'),
                {"status": "completed", "markdown": stub_report(case['case_id'], extra), "outline": STUB_OUTLINE},
            )

        registry['cases'].append({
            "case_id": case['case_id'],
            "title": case['title'],
            "synthetic": True,
            "enabled": True,
        })
        print(f'已生成案卷 {case["case_id"]}')

    write_json(os.path.join(CASE_ROOT, 'registry.json'), registry)

    # 生成后立刻校验一遍：把防泄漏检查跑通，并把结果缓存进 validation.json
    from app.services import benchmark
    ok = True
    for case_meta in registry['cases']:
        case = benchmark.BenchmarkManager.load(case_meta['case_id'])
        report = benchmark.validate_case(case)
        benchmark.write_validation(case, report)
        flag = 'OK  ' if report['ok'] else 'FAIL'
        ok = ok and report['ok']
        print(f'{flag} 校验 {case.case_id}')
        if not report['ok']:
            for name, check in report['checks'].items():
                if not check['ok']:
                    print(f'       {name}: {check}')

    print()
    print('全部通过' if ok else '有案卷未通过校验——请检查上面的明细')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
