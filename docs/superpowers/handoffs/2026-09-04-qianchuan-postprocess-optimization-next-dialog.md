# 千川 RPA 后处理优化交接摘要

## 1. 交接目的

本摘要用于在新对话中继续优化千川项目的 RPA 数据落盘完成后的链路：事实包生成、章节提示词、AI 输出校验、Presentation 投影以及前端报告渲染。当前阶段只完成方案梳理，不应直接修改业务代码；新对话应先基于本摘要继续需求澄清和设计评审。

## 2. 项目与分支状态

- 项目：`/Users/shuzida/Desktop/FDE客户/小仙炖/qianchuan-video-peak-frames`
- 当前工作区分支：`codex/migrate-direct-material-data`
- 当前工作树干净，三条分支均已推送并与对应 origin 同步。
- `codex/direct-url-frame-extraction`：最新提交 `c2989c6 fix: persist RPA CSV from async fetch tasks`
- `codex/http-pipe-frame-extraction`：最新提交 `bbc623d fix: unpack nested RPA task result envelope`
- `codex/migrate-direct-material-data`：最新提交 `274d83f fix: preserve RPA polling context in direct material branch`

三条分支的 RPA 获取和落盘逻辑已经分别优化完成。后续优化应尽量只改 RPA 完成后的公共后处理链路；如果分支间确实存在差异，应保留差异并在每条分支独立验证，不要把 RPA 采集实现重新合并或重写。

## 3. 用户已确认的边界

- RPA 数据落盘后，继续优化每一章的事实包、提示词和前端报告渲染链路。
- 暂不直接执行优化；新对话需要先完善思路、设计方案和执行方案。
- 三条分支的后处理逻辑可能不同，不能假设可以无条件共享全部实现。
- 不覆盖既有 RPA 原始数据、历史报告、历史分析 JSON 或隔离树中的用户改动。
- 真实批次回归应使用已有落盘数据；生成新产物时使用独立日期/目录。

## 4. 云图参考对话与可复用结论

参考对话：

- `codex://threads/01a05cbb-cae3-7150-a934-0b59699f1936`：3.2 版本整体优化及交接。
- `codex://threads/01a05cc8-2412-7b93-a49c-8e951f8af70b`：素材榜单事实包、提示词和前端渲染链路设计。
- `codex://threads/01a05fd8-babe-7210-bc75-de0cedf57da1`：第一部分事实包/提示词/前端执行与输入框回归修复。
- `codex://threads/01a060a9-6425-7920-b67b-4e9f78ddde8c`：第二部分人群与营销卖点 Mermaid 数据链路。
- `codex://threads/01a060a9-665e-7bc2-bc9a-ab7a58d56e62`：第四部分高光画面与资产 Mermaid 数据链路。
- `codex://threads/01a060a9-68ba-7b72-937e-4b5cb663ad6f`：第五部分单素材深度分析链路。
- `codex://threads/01a060b8-16a9-7243-a2ab-a8bec2075f13`：第四部分标准分类、本批次分类、分布图和 Excel 交付优化。

可迁移的核心原则：

1. 确定性展示事实与 AI 分析事实分离。展示图表、TOP 下钻、分类数量和固定资产数据不能由 AI 生成或修正。
2. 章节职责隔离。每章只读取自己的章节事实包；第 01 章的组合结论不能替代第 02 章洞察，第 02 章 TOP3 展示数据不应重复注入第 05 章 AI 输入。
3. Presentation 只做投影和契约组合，不重新聚合业务事实；HTML 只消费 Presentation 的展示节点。
4. 所有 AI 结论采用“少量事实锚点 → 判断 → 验证动作”，每个证据引用必须来自事实包且可回溯。
5. 数据不足、部分缺失和历史字段必须有明确降级状态，不能补造数字或静默吞错。
6. 前端交互需要键盘可访问、Tooltip 不裁切、移动端不溢出，并对空数据稳定降级。
7. Mermaid 数据流图采用与云图第一部分相同的简洁主链：输入/事实包 → 确定性处理 → AI 分析 → 前端报告。

## 5. 千川当前链路定位

主要文件：

- `scripts/diagnostic_analysis_lib.py`：标准化 records，构造 audience、interaction、scripts、highlights 和章节分析单元。
- `scripts/qianchuan_presentation.py`：把事实与 AI 结论投影为 `presentation-report.json`。
- `scripts/final_report_lib.py`：报告数据组装、单素材事实、互动时序、脚本事实和 schema 校验。
- `scripts/report_renderer.py`：读取 Presentation/展示事实、裁剪 HTML 嵌入数据并生成报告/ZIP。
- `scripts/qianchuan_report_client.py`：素材汇总数据查询及字段完整性契约（本轮不应作为后处理优化入口，除非发现事实源缺字段）。
- `prompts/diagnostic-analysis-prompt-v1.md`：一次 AI 调用的五章输入、章节边界、证据规则和输出长度门禁。
- `templates/final-report-template.html`：章节结构、Tab、折叠区、图表和单素材工作区。
- `assets/report/unified-report.css`、`assets/report/unified-report.js`：样式与前端交互。
- `tests/test_diagnostic_analysis.py`、`tests/test_qianchuan_presentation.py`、`tests/test_final_report.py`、`tests/test_highlight_export.py`：事实、投影、报告和导出回归。

当前代码已经具备若干分离基础：`report-display-facts.json` 的读取/裁剪逻辑、Presentation 的章节投影、五章 `analysis-units` 输入、Chapter 01 爆款规则、Chapter 02 三口径人群洞察、Chapter 04 高光分类和 Chapter 05 五 Tab 结构。新对话应先审计真实生成链路，确认哪些设计已经落地，避免重复实现或误改已完成能力。

## 6. 建议的新对话工作顺序

### 阶段 A：现状审计（只读）

1. 分别检查三条分支最新提交、隔离树和工作区改动。
2. 选择一个已有完整 RPA 落盘批次，核对 records、CSV、peak-frames、analysis-units、AI JSON、Presentation、HTML 和 ZIP 的实际文件关系。
3. 绘制当前五章“事实源 → 分析输入 → AI 输出 → Presentation → HTML”的简图，标出重复字段和跨章节回退。
4. 对照云图参考对话，列出“已实现 / 部分实现 / 尚未实现 / 不适用于千川”的清单。

### 阶段 B：设计评审

优先确认以下决策，再写正式设计文档：

- 是否将 `report-display-facts.json` 作为三条分支新批次的强制产物。
- 展示事实和 AI 事实是否共享同一批次 manifest 与 SHA-256 校验。
- 五章是否继续一次 AI 调用，还是仅通过输入边界隔离职责（默认建议保持一次调用，避免扩大范围）。
- 是否先按章节逐项实施，建议顺序为 01 → 02 → 03 → 04 → 05，再做统一渲染收口。
- 是否需要把事实包裁剪/压缩纳入本轮，还是先只做职责分离和渲染一致性。
- 三条分支是否采用相同后处理提交序列，还是允许分支特定适配层。

### 阶段 C：实现与验证（获得用户批准后）

每个阶段均应先写失败测试，再做最小实现；每条分支独立运行定向测试、全量测试、`compileall`、`git diff --check`，并用新目录生成真实 HTML/ZIP。验证重点包括：

- 展示图表数值与确定性事实逐项一致；
- AI 证据引用合法且不跨章节/跨口径；
- 历史字段可读取但不会在新报告重复嵌入；
- 空数据和部分数据状态正确；
- Tab、折叠、下钻、高光帧预览和 Excel 导出不回归；
- ZIP 内容完整，不包含凭证，不意外携带旧 Excel 或调试文件。

## 7. 不可破坏约束

- 不修改三条分支已经验证成功的 RPA 提交、轮询、CSV 下载和落盘契约。
- 不把凭证、远程签名 URL 或执行过程写入事实包、AI 输出、HTML、ZIP 或交接文档。
- 不让 AI 生成排名、数量、占比、分类计数、帧集合或其他可确定性计算的数据。
- 不覆盖 `runs/` 下历史产物；新回归必须使用独立目录。
- 不删除或重命名历史字段，除非已有兼容读取和明确迁移测试。

## 8. 新对话启动提示词（可直接复制）

> 请先阅读交接摘要：`docs/superpowers/handoffs/2026-09-04-qianchuan-postprocess-optimization-next-dialog.md`。当前任务不是立即改代码，而是基于云图参考对话，审计千川三条分支在 RPA 数据落盘后的事实包、章节提示词、AI schema、Presentation 和前端报告渲染链路。请先输出“已实现/缺口/重复事实/跨章耦合”清单，再提出 2–3 个方案及推荐方案；获得确认后再编写设计文档和执行方案。保留三条分支各自的 RPA 获取实现，后处理优化需可独立验证、可追溯、可降级，并使用新目录生成真实报告回归产物。
