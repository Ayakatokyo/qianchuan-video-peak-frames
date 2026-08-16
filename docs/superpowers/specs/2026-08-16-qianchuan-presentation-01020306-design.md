# 千川 PresentationReport v1（01/02/03/06）设计

## 目标

将第 01、02、03、06 章从旧 `final-report-data.json` 读取路径迁移到 PresentationReport v1，形成仅面向稳定展示的投影合同。迁移不改变 AI schema v4/v5、质量门禁、原始事实数据或 `evidenceRefs` 约束；缺失展示数据时 HTML 保持旧字段兼容回退。

## 章节合同

`report.sections` 固定为 `chapter01` 至 `chapter06`。每个新迁移章节均包含 `id`、`title`、`source`、`evidenceRefs` 和 `availability`，并在不可用时保留可预测的空数组或空对象。

### Chapter 01

投影 TOP 素材组合层数据：经营事实锚点、`overallConclusion`、组合洞察，以及固定顺序的五维 Tab（ROI、互动、人群、脚本、高光）。每个维度保留面向素材或事实的证据引用，并映射到原始 `topMaterialRoi`、`batchInsights` 与 `portfolioDimensionConclusions`。

### Chapter 02

投影人群概览及柱状图下钻所需的统一 `buckets`。bucket 保留分组名称、指标、数值、关联素材 ID 和证据引用，使现有点击下钻无需重新解析旧聚合对象。缺少指标或关联素材时章节标记 `partial`，但仍可展示可用 bucket。

### Chapter 03

投影脚本一级/二级分类树、各分类的聚合事实、关联素材和证据引用。一级、二级分类固定以源数据的确定顺序输出；素材定位只使用有效的 `materialId`。

### Chapter 06

将 AI `nextActions` 投影为稳定行动卡：`title`、`body`、`action`、`validation`、`priority`、`evidenceRefs`、关联 `materialIds` 和 `availability`。优先级沿用既有合法值，缺失或无可定位证据的行动卡降级为 `partial`，而非修改 AI 结论。

## 渲染与兼容

模板的 01/02/03/06 先读取 `presentation.chapters` 中对应章节；若章节不存在或不可用则回退当前 `topMaterialRoi`、`batchInsights`、`audience`、`scripts` 和 `conclusion.nextActions`。既有 Tab、柱状图点选和素材定位交互复用现有组件，仅替换数据入口。

## 校验与测试

`validate_presentation_report()` 扩展验证固定章节顺序、五维 Tab、bucket/脚本分类引用、行动优先级和所有关联素材 ID。单测覆盖完整投影、缺失事实与证据时的降级、旧 HTML 回退、生成 JSON 和 zip 文件包含 Presentation JSON。全量测试、字节码编译、`git diff --check` 以及真实 Top18 报告浏览器验收共同构成完成条件。
