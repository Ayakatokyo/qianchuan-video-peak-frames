# 千川批处理技能流程实施计划（中文阅读版）

> 本文仅供中文阅读。实施时必须以英文权威计划 [2026-07-23-qianchuan-batch-skill-flow.md](./2026-07-23-qianchuan-batch-skill-flow.md) 为准；命令、路径、测试名和提交信息均保持英文原样。

**目标：** 让 `SKILL.md` 要求先从资产表筛选 TOP 素材，再进行批量 RPA 诊断，并将选材 JSON 保留为运行产物。

**架构：** 保持现有两个 CLI 相互独立。`SKILL.md` 负责规定批处理编排：建立唯一的明确日期范围，写出本次运行目录中的 `top_materials.json`，校验该产物，随后将其传给既有的 RPA 峰值帧 CLI。新增一个小型静态契约测试，防止流程顺序和单素材边界回退。

**技术栈：** Markdown、Python 3 `unittest`、既有命令行脚本。

---

### 任务 1：新增批处理技能契约测试

**文件：**
- 新建：`tests/test_skill_contract.py`
- 测试：`tests/test_skill_contract.py`

- [ ] **步骤 1：编写验证“资产表优先”顺序的失败测试**

```python
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "SKILL.md"


class BatchSkillContractTests(unittest.TestCase):
    def test_batch_workflow_exports_and_reuses_run_local_top_materials(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        batch_start = skill.index("## Batch TOP Materials Run")
        export_start = skill.index(
            "python scripts/export_top_materials.py", batch_start
        )
        rpa_start = skill.index(
            "python scripts/extract_peak_frames.py", batch_start
        )

        self.assertLess(export_start, rpa_start)
        self.assertIn('--output "/path/to/run/top_materials.json"', skill)
        self.assertIn(
            '--top-materials-json "/path/to/run/top_materials.json"', skill
        )
        self.assertIn("--custom-start-date 2026-06-01", skill)
        self.assertIn("--custom-end-date 2026-06-07", skill)
```

- [ ] **步骤 2：运行新测试，确认它在当前技能下失败**

运行：`.venv/bin/python -m unittest tests.test_skill_contract -v`

预期：因当前标题仍为 `## TOP Materials RPA Run`，且没有规定本次运行专属的产物路径，报 `ValueError: substring not found`。

- [ ] **步骤 3：扩展测试，约束单素材入口的用途**

在 `BatchSkillContractTests` 中加入：

```python
    def test_single_material_rpa_is_documented_as_debugging_only(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        batch_start = skill.index("## Batch TOP Materials Run")
        single_start = skill.index("## Single-Material RPA Debug Run")

        self.assertLess(batch_start, single_start)
        self.assertIn(
            "not an alternative selection method for a TOP-material batch run",
            skill,
        )
```

- [ ] **步骤 4：提交失败测试**

```bash
git add tests/test_skill_contract.py
git commit -m "test: define asset-first batch skill contract"
```

### 任务 2：将技能批处理流程改为资产表优先

**文件：**
- 修改：`SKILL.md:12-63`
- 测试：`tests/test_skill_contract.py`

- [ ] **步骤 1：用有序批处理流程替换通用工作流和 TOP RPA 小节**

更新 `SKILL.md`：流程开头加入资产表优先规则，并在所有单素材 RPA 指引之前增加 `## Batch TOP Materials Run` 小节。待写入内容的英文原文见权威计划第 86 至 120 行。

该小节必须做到：

- 两条命令都使用同一组显式的起止日期；
- 先执行 `export_top_materials.py`，写入 `"/path/to/run/top_materials.json"`；
- 在进入 RPA 前校验 `materials`、`materialId` 和实际日期；
- 随后通过 `--top-materials-json` 向 `extract_peak_frames.py` 传入同一个文件；
- 保留 `top_materials.json`、归一化诊断记录、峰值帧摘要和 JPEG；
- 将 `result_type=files` CSV 信封未适配表述为阻塞性限制，不可报告成功。

- [ ] **步骤 2：将现有直接 RPA 示例移动至批处理小节之后**

将标题改为 `## Single-Material RPA Debug Run`，并在命令前加入这段英文：

```markdown
Use this only to diagnose or replay one already known material. `--material-id` is not an alternative selection method for a TOP-material batch run.
```

在这段说明之后，保留原有的 `--source rpa --material-id ...` 命令与连接器说明不变。

- [ ] **步骤 3：更新速查表和常见错误**

将在线来源速查项替换为以下英文两行：

```markdown
| TOP-material batch | Run asset-table selection first, then pass its run-local `top_materials.json` to RPA with the same CUSTOM dates |
| Single-material debug | Pass `--source rpa --shop-id ... --material-id ... --date-range-type ...` |
```

在 `## Common Mistakes` 下加入以下四条英文规则：

```markdown
- Do not call batch RPA before asset-table selection or use `--material-id` to bypass it.
- Do not use different ranking and diagnosis date ranges.
- Do not overwrite or discard the run-local `top_materials.json`.
- Treat an RPA `result_type=files` response as a blocking adapter limitation until CSV-envelope integration is available.
```

- [ ] **步骤 4：运行技能契约测试并确认通过**

运行：`.venv/bin/python -m unittest tests.test_skill_contract -v`

预期：`OK`，两个测试均通过。

- [ ] **步骤 5：提交技能指引修改**

```bash
git add SKILL.md tests/test_skill_contract.py
git commit -m "docs: make batch skill asset-table first"
```

### 任务 3：运行回归验证

**文件：**
- 验证：`SKILL.md`
- 验证：`tests/test_skill_contract.py`
- 验证：`tests/test_peak_frames.py`
- 验证：`tests/test_top_materials.py`
- 验证：`tests/test_rpa_csv_parser.py`

- [ ] **步骤 1：运行完整 Python 测试套件**

运行：`.venv/bin/python -m unittest discover -s tests -v`

预期：`OK`，没有失败或错误。

- [ ] **步骤 2：检查文档中的批处理命令顺序**

运行：`sed -n '1,180p' SKILL.md`

预期：`## Batch TOP Materials Run` 中，`export_top_materials.py` 出现在 `extract_peak_frames.py` 之前；两条命令的展示日期一致，并使用同一个本次运行目录内的 `top_materials.json` 路径。

- [ ] **步骤 3：确认对适配器边界的说明准确**

运行：`rg -n "result_type=files|CSV-envelope|Single-Material RPA Debug Run" SKILL.md`

预期：技能将文件信封处理标记为阻塞，并将直接 `--material-id` 使用标记为仅调试用途。

- [ ] **步骤 4：如检查发现仅需修正文档，则提交该修正**

运行：`git status --short`

预期：不存在与本计划相关的暂存或未暂存改动；不要创建空提交。

## 计划自检

- 覆盖性：任务 2 实现资产表优先、日期一致、运行专属选材 JSON 保留、停止条件、单素材仅调试边界，以及已知 CSV 信封限制。任务 1 使关键顺序与边界可回归验证。任务 3 验证完整测试集和最终文档。
- 占位项：没有不完整步骤或未明确命令。
- 一致性：任务 1 断言的标题和路径字符串由任务 2 创建。
