# 千川分析输入模块化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不新增经营指标、不拆分 Prompt 或模型调用、不改变 v5 AI 输出和报告渲染的前提下，从现有兼容事实快照生成五个章节事实包与一个可校验 manifest。

**Architecture:** 保留 `diagnostic-analysis-data.json` 作为兼容快照；新增一个无 CLI 的纯函数模块，将同一份确定性分析事实投影为五个章节包，并在最后写入含字节数和 SHA-256 的 manifest。`prepare_diagnostic_analysis` 仍返回原三元组，标准工作流只增加 manifest 产物路径；当前对话模型按照唯一 Prompt 一次读取五包并输出一份 Schema v5 JSON。

**Tech Stack:** Python 3.13 标准库、JSON、SHA-256、`unittest`、现有 Prompt/SKILL Markdown 合同。

---

## 文件职责

- Create: `scripts/analysis_units_lib.py`：构造、序列化和发布五个章节事实包及 manifest。
- Create: `tests/test_analysis_units.py`：覆盖章节隔离、压缩、引用、哈希和发布失败行为。
- Modify: `scripts/diagnostic_analysis_lib.py`：在兼容快照生成后调用章节包发布函数。
- Modify: `scripts/standard_workflow.py`：把 manifest 路径加入运行摘要，不改变 prepare 返回接口。
- Modify: `prompts/diagnostic-analysis-prompt-v1.md`：唯一 Prompt 改为读取 manifest 和五包。
- Modify: `SKILL.md`：AI 交接改为五包、单 Prompt、单次当前对话模型调用。
- Modify: `tests/test_diagnostic_analysis.py`、`tests/test_standard_workflow.py`、`tests/test_skill_contract.py`：锁定集成与合同。

### Task 1：章节投影与 manifest 纯函数

- [ ] **Step 1: 写失败测试**

在 `tests/test_analysis_units.py` 构造一个同时含 audience、scripts、interaction、delivery、commercial、highlights 的最小兼容事实包，断言：

```python
units = build_analysis_units(analysis)
self.assertEqual(list(units), [
    "chapter01-portfolio",
    "chapter02-audience",
    "chapter03-script",
    "chapter04-highlight",
    "chapter05-material",
])
self.assertNotIn("delivery", units["chapter01-portfolio"])
self.assertNotIn("audience", units["chapter04-highlight"])
self.assertNotIn("sourceMaterialIds", json.dumps(
    units["chapter02-audience"], ensure_ascii=False
))
self.assertEqual(
    units["chapter05-material"]["materials"][0]["highlightRefs"],
    ["highlight:m-1:zero_second"],
)
```

- [ ] **Step 2: 验证 RED**

Run: `/opt/homebrew/opt/python@3.13/libexec/bin/python3 -m unittest tests.test_analysis_units -v`

Expected: FAIL，原因是 `analysis_units_lib` 尚不存在。

- [ ] **Step 3: 最小实现五个投影**

在 `scripts/analysis_units_lib.py` 定义：

```python
UNIT_DEFINITIONS = (
    ("chapter01-portfolio", "chapter01-portfolio-input.json"),
    ("chapter02-audience", "chapter02-audience-input.json"),
    ("chapter03-script", "chapter03-script-input.json"),
    ("chapter04-highlight", "chapter04-highlight-input.json"),
    ("chapter05-material", "chapter05-material-input.json"),
)

def build_analysis_units(analysis):
    """Project one compatible fact snapshot into five isolated model inputs."""

def publish_analysis_units(output_dir, analysis):
    """Write compact units first and publish the hash manifest last."""
```

投影规则固定为：

- Chapter 01：周期、去 URL 素材身份、现有 `commercial`、现有聚合 `interaction` 和合法证据域；不添加新指标。
- Chapter 02：三种人群口径、全部维度和 `preferencesByMetric`；删除所有重复 `sourceMaterialIds` 和与 `preferencesByMetric.SHOW_COUNT` 重复的 `preferences`。
- Chapter 03：完整脚本文本、标签、证据句和确定性 `script:*` 引用。
- Chapter 04：成功帧、缺失帧、覆盖状态和 JPEG 路径；不复制其他章节事实。
- Chapter 05：每条素材的身份、三口径偏好、脚本引用、高光引用、缺失帧和现有 RPA `delivery` 明细；本轮不新增 API 指标。

- [ ] **Step 4: 验证 GREEN**

Run: `/opt/homebrew/opt/python@3.13/libexec/bin/python3 -m unittest tests.test_analysis_units -v`

Expected: PASS。

### Task 2：原子发布、哈希和体积合同

- [ ] **Step 1: 写失败测试**

增加测试断言 `publish_analysis_units` 生成：

```text
analysis-units/
├── analysis-unit-manifest.json
├── chapter01-portfolio-input.json
├── chapter02-audience-input.json
├── chapter03-script-input.json
├── chapter04-highlight-input.json
└── chapter05-material-input.json
```

manifest 必须包含 `executionMode: single_model_call`、`analysisSchemaVersion: 5`、素材顺序、五个单元路径、UTF-8 字节数和 SHA-256；逐项重新读取文件后计算值必须一致。测试同时断言 manifest 最后发布，任一单元序列化失败时不得留下 manifest。

- [ ] **Step 2: 验证 RED**

Run: `/opt/homebrew/opt/python@3.13/libexec/bin/python3 -m unittest tests.test_analysis_units -v`

Expected: FAIL，缺少发布或 manifest 字段。

- [ ] **Step 3: 实现紧凑序列化和 manifest**

统一使用：

```python
payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
byte_size = len(payload.encode("utf-8"))
digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

先将全部单元写入同目录临时文件并 `os.replace`，最后写入 manifest。manifest 不嵌入业务数据。

- [ ] **Step 4: 验证 GREEN**

Run: `/opt/homebrew/opt/python@3.13/libexec/bin/python3 -m unittest tests.test_analysis_units -v`

Expected: PASS。

### Task 3：接入 prepare 与标准工作流

- [ ] **Step 1: 写失败集成测试**

在 `tests/test_diagnostic_analysis.py` 断言 `prepare_diagnostic_analysis` 保持原返回三元组，同时生成 manifest 和五个包。在 `tests/test_standard_workflow.py` 断言运行摘要新增：

```python
self.assertEqual(
    summary["artifacts"]["analysisUnitManifest"],
    "diagnostic-analysis/analysis-units/analysis-unit-manifest.json",
)
```

- [ ] **Step 2: 验证 RED**

Run: `/opt/homebrew/opt/python@3.13/libexec/bin/python3 -m unittest tests.test_diagnostic_analysis tests.test_standard_workflow -v`

Expected: FAIL，缺少章节产物和摘要字段。

- [ ] **Step 3: 最小集成实现**

`prepare_diagnostic_analysis` 在兼容快照构造完成后调用 `publish_analysis_units(output_dir, analysis)`；`_summary` 根据固定路径填充 `analysisUnitManifest`。不修改 RPA、API 获取、报告 finalize 参数或 AI v5 Schema。

- [ ] **Step 4: 验证 GREEN**

Run: `/opt/homebrew/opt/python@3.13/libexec/bin/python3 -m unittest tests.test_analysis_units tests.test_diagnostic_analysis tests.test_standard_workflow -v`

Expected: PASS。

### Task 4：唯一 Prompt 与技能交接合同

- [ ] **Step 1: 写失败合同测试**

在 `tests/test_skill_contract.py` 断言 Prompt 和 SKILL：

```python
self.assertIn("analysis-unit-manifest.json", prompt)
self.assertIn("chapter01-portfolio-input.json", prompt)
self.assertIn("chapter05-material-input.json", prompt)
self.assertIn("一次模型调用", skill)
self.assertNotIn("作为唯一的结构化事实来源", prompt)
self.assertNotIn("prompts/analysis-units", skill)
```

- [ ] **Step 2: 验证 RED**

Run: `/opt/homebrew/opt/python@3.13/libexec/bin/python3 -m unittest tests.test_skill_contract -v`

Expected: FAIL，Prompt 仍声明兼容快照为唯一输入。

- [ ] **Step 3: 更新单份 Prompt 和 SKILL**

Prompt 保持一个权威文件，按通用规则和五章顺序说明输入路径、输出字段、证据前缀、全量 JPEG 审阅与统一自检。SKILL 要求当前对话模型一次读取 manifest、五包和 JPEG，一次输出完整 v5 JSON；`diagnostic-analysis-data.json` 仅作为兼容快照，不交给新分析流程。

- [ ] **Step 4: 验证 GREEN**

Run: `/opt/homebrew/opt/python@3.13/libexec/bin/python3 -m unittest tests.test_skill_contract tests.test_diagnostic_analysis -v`

Expected: PASS。

### Task 5：最新 Top6 真实回归与全量验证

- [ ] **Step 1: 在临时目录重建分析输入**

从 `runs/qianchuan-20260810-20260820-top6` 只读复制或引用 ready materials、diagnosis records、peak frames 和 ROI facts，在 `mktemp -d` 目录调用 `prepare_diagnostic_analysis`。不得重跑 API/RPA，不得覆盖原运行目录。

- [ ] **Step 2: 校验真实产物**

检查五个包的字节数、总字节数、manifest 哈希、6 个素材顺序、34 张 JPEG、缺失帧和跨章禁止字段。记录相对 544,024 bytes 兼容事实包的变化。

- [ ] **Step 3: 兼容报告回归**

使用原批次现有 `diagnostic-ai-analysis.json` 和临时输出完成报告生成，证明 AI v5 与报告渲染行为未改变；不得启动实际浏览器。

- [ ] **Step 4: 完整验证**

Run:

```bash
/opt/homebrew/opt/python@3.13/libexec/bin/python3 -m compileall -q scripts
/opt/homebrew/opt/python@3.13/libexec/bin/python3 -m unittest discover -s tests -q
git diff --check
```

Expected: Python 编译成功，全部测试通过，`git diff --check` 无输出。
