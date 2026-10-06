# 回测案卷

这个目录存放**回测案卷**（benchmark cases）：一份"截至某个日期的种子材料" + "该日期之后
真实发生了什么"的组合，用来度量 MacFish 的预测质量。

> ## 合成案卷是 fixture，永远不构成有效性证据
>
> 仓库里自带的两个案卷（`synthetic_*`）用 `synthetic: true` 标记。它们的种子材料与
> "真实结果"都是 `scripts/bootstrap_benchmarks.py` **编造的**，用途是验证回测框架本身
> 能跑通（加载、防泄漏校验、编排、评分、聚合）。
>
> 它们的分数**不能**用来评价 MacFish 的预测能力。所有评分与记分卡都带
> `headline_eligible` 字段，合格条件是「跑通 + 通过防泄漏校验 + 非合成 + 未被截断」，
> 聚合只统计合格项；合成案卷永远落在不合格的桶里并单列显示。

## 目录结构

```
benchmarks/
  registry.json                 # 案卷登记表（顺序 + 启用状态）
  cases/<case_id>/
    case.json                   # 案卷定义
    ground_truth.json           # 结果与判定依据
    corpus/                     # 种子材料（可多个文件）
    validation.json             # 防泄漏校验结果缓存（生成后提交）
```

运行结果**不在这里**——它们写在 `uploads/benchmarks/<run_id>/`（该目录被 gitignore）。

## 怎么新增一个真实案卷

1. 建目录 `cases/<case_id>/`，放入 `corpus/` 里的种子材料。
   **材料的时间必须严格早于你要预测的结果**，否则是数据泄漏。
2. 写 `case.json`：
   - `seed.cutoff`：预测的起点日期。种子材料里不得出现这个日期及以后的内容
   - `seed.outcome_window`：结果对应的现实时间窗口
   - `expectations.questions`：结构化的可判定问题（方向类最适合回测）
   - `expectations.key_actors`：该出现的关键主体
   - `expectations.min_*`：规模下限，用来拦掉"图谱抽空却被打成预测错误"的假阴性
   - `synthetic: false`
3. 写 `ground_truth.json`：
   - `leakage_terms`：若这些词出现在种子材料里，说明结果被写进了输入
   - `answer_numbers`：定义结果的数量级（价格、百分比等），同样用于查泄漏
4. 计算语料 sha256 填进 `case.json` 的 `seed.documents[].sha256`
5. 校验并生成缓存：

```bash
cd backend
uv run python scripts/run_benchmark.py validate --cases <case_id>
```

校验通过后才允许执行。未通过的案卷会被 `run` 拒绝（除非显式 `--allow-unvalidated`，
那样分数不计入头条统计）。

## 防泄漏校验做了什么

五项，全部确定性、可离线复现：

| 检查 | 拦的是什么 |
|---|---|
| `hash_integrity` | 语料被改过但登记哈希没更新——缓存的校验结果会失真 |
| `date_order` | 材料日期晚于 `corpus_as_of`，或 `cutoff` 落在结果窗口之后 |
| `timeline_leak` | 语料里出现了 `cutoff` 及以后的日期 |
| `term_leak` | 结果定义词出现在语料里 |
| `number_leak` | 定义结果的数量级出现在语料里（如最终定价）|

回测最容易犯、也最致命的错误就是泄漏：把结果写进输入，模型"预测"得极准，
分数却毫无意义。所以这五项是**闸门**，不是提示。
