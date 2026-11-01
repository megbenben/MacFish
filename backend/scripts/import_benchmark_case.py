#!/usr/bin/env python3
"""
导入一个真实回测案卷
====================

仓库自带的两个案卷是 synthetic fixture，只能验证框架。要得到真实准确率，
必须导入"种子材料 + 已发生结果"的真实案卷。本脚本负责把一份外部案卷
规范化落盘、重算哈希、登记到 registry，并跑一次防泄漏校验。

用法::

    uv run python scripts/import_benchmark_case.py \
        --spec /path/to/spec.json \
        --corpus /path/to/corpus \
        --case-id ev_price_war_2025

spec.json 结构（字段与 benchmarks/cases/<id>/case.json 一致）::

    {
      "title": "...",
      "simulation_requirement": "...",
      "synthetic": false,
      "locale": "zh",
      "domain": "consumer_durables",
      "seed": {
        "corpus_as_of": "2025-01-31",
        "cutoff": "2025-02-01",
        "horizon_days": 30,
        "outcome_window": {"start": "2025-02-01", "end": "2025-03-03"},
        "documents": [
          {"path": "industry_note.md", "doc_date": "2025-01-05"},
          {"path": "channel_feedback.md", "doc_date": "2025-01-20"}
        ]
      },
      "expectations": {
        "questions": [
          {"id": "q1", "text": "...", "options": ["up","down","flat"],
           "answer": "down", "signal_terms": {"down": ["降价"], ...}}
        ],
        "key_actors": [...],
        "min_nodes": 4, "min_entities": 2, "min_actions": 5,
        "min_report_chars": 200, "report_min_sections": 3
      }
    }

``ground_truth.json`` 与 spec 同目录，或通过 ``--ground-truth`` 指定。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, BACKEND_ROOT)

from app.services.benchmark import CASE_ROOT, BenchmarkManager, validate_case  # noqa: E402


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _fail(message: str) -> None:
    print(f'[import] 失败: {message}', file=sys.stderr)
    raise SystemExit(2)


def _load_json(path: str) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f'读取 {path} 失败: {exc}')
    if not isinstance(data, dict):
        _fail(f'{path} 必须是 JSON 对象')
    return data


def _register(case_id: str, title: str) -> None:
    registry_path = os.path.join(CASE_ROOT, 'registry.json')
    registry = _load_json(registry_path) if os.path.isfile(registry_path) else {'cases': []}
    cases = [c for c in (registry.get('cases') or []) if c.get('case_id') != case_id]
    cases.append({'case_id': case_id, 'enabled': True, 'synthetic': False, 'title': title})
    registry['cases'] = cases
    registry['schema_version'] = registry.get('schema_version') or 1
    with open(registry_path, 'w', encoding='utf-8') as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def main() -> int:
    parser = argparse.ArgumentParser(description='导入真实回测案卷')
    parser.add_argument('--spec', required=True, help='案卷 spec.json')
    parser.add_argument('--corpus', required=True, help='种子材料目录')
    parser.add_argument('--case-id', required=True, help='案卷 ID（目录名）')
    parser.add_argument('--ground-truth', default='', help='ground_truth.json（默认与 spec 同目录）')
    parser.add_argument('--force', action='store_true', help='覆盖已存在的案卷')
    parser.add_argument('--skip-validate', action='store_true', help='导入后不跑防泄漏校验')
    args = parser.parse_args()

    case_id = args.case_id.strip()
    if not case_id or '/' in case_id or case_id.startswith('.'):
        _fail(f'非法 case-id: {case_id!r}')

    spec = _load_json(args.spec)
    ground_truth_path = args.ground_truth or os.path.join(os.path.dirname(args.spec), 'ground_truth.json')
    ground_truth = _load_json(ground_truth_path)

    if not spec.get('simulation_requirement'):
        _fail('spec 缺少 simulation_requirement')
    if spec.get('synthetic') is not False:
        _fail('真实案卷必须显式写 "synthetic": false（合成案卷请用 bootstrap 脚本）')
    if 'graph_id' in spec:
        _fail('case.json 不应包含 graph_id（图谱是运行时产物）')

    target = os.path.join(CASE_ROOT, 'cases', case_id)
    if os.path.isdir(target):
        if not args.force:
            _fail(f'案卷已存在: {target}（加 --force 覆盖）')
        shutil.rmtree(target)
    os.makedirs(target, exist_ok=True)

    documents = list((spec.get('seed') or {}).get('documents') or [])
    if not documents:
        _fail('seed.documents 为空')
    copied = []
    for document in documents:
        rel = str(document.get('path') or '').strip()
        if not rel:
            _fail('documents[].path 为空')
        source = os.path.join(args.corpus, rel)
        if not os.path.isfile(source):
            _fail(f'找不到语料文件: {source}')
        destination = os.path.join(target, 'corpus', rel)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(source, destination)
        copied.append({
            'path': f'corpus/{rel}',
            'doc_date': document.get('doc_date', ''),
            'sha256': _sha256(destination),
        })

    case_payload = dict(spec)
    case_payload['case_id'] = case_id
    case_payload.setdefault('title', case_id)
    case_payload.setdefault('schema_version', 1)
    seed = dict(case_payload.get('seed') or {})
    seed['documents'] = copied
    case_payload['seed'] = seed
    ground_truth['case_id'] = case_id
    ground_truth.setdefault('synthetic', False)

    with open(os.path.join(target, 'case.json'), 'w', encoding='utf-8') as handle:
        json.dump(case_payload, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    with open(os.path.join(target, 'ground_truth.json'), 'w', encoding='utf-8') as handle:
        json.dump(ground_truth, handle, ensure_ascii=False, indent=2)
        handle.write('\n')

    _register(case_id, case_payload.get('title') or case_id)
    print(f'[import] 已写入 {target}')

    if not args.skip_validate:
        case = BenchmarkManager.load(case_id)
        report = validate_case(case)
        if not report.get('ok'):
            print('[import] 防泄漏校验未通过：', file=sys.stderr)
            for check in report.get('checks', []):
                if not check.get('ok'):
                    print(f"  - {check.get('name')}: {check.get('detail')}", file=sys.stderr)
            return 1
        print(f'[import] 校验通过（{len(report.get("checks", []))} 项）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
