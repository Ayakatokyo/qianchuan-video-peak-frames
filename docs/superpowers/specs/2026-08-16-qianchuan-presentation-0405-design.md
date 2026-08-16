# 千川 PresentationReport v1（04/05）设计

## 目标

在不改变千川 AI schema 与事实质量门禁的前提下，新增独立的 PresentationReport v1。第一阶段投影第 04 章高光画面洞察和第 05 章单素材深度分析；旧 `final-report-data.json` 保留，HTML 优先消费 presentation，缺失时回退旧数据。

## 数据流

`build_final_report_data()` 继续产出事实与 AI 汇总对象。新模块 `qianchuan_presentation.py` 接受该对象，生成稳定的展示模型并校验，再由报告生成器落盘 `presentation-report.json`。展示模型不修改或替代 AI 原始结论。

## PresentationReport v1

顶层包含 `presentationSchemaVersion: 1`、`report`、`materials`、`chapters`、`interactions`、`extensions`。每个素材保留稳定 `materialId`；高光帧通过 `frameRef` 定位，来源通过 `source` 和 `evidenceRefs` 表示，缺失数据用 `availability` 描述。

第 04 章固定 `portrait`、`product`、`other` 顺序，包含每组 `frameRefs`、帧卡数据、`commonConclusion`、`derivativeDirections` 和 AI/事实证据引用。第 05 章按素材生成工作区，包含受众、脚本、高光和互动时序四个 Tab，以及对应的事实和 AI 分析投影。

## 降级与校验

缺失素材、无高光帧、旧 AI schema 或缺少证据引用不得阻塞报告生成；对应章节标记 `availability: unavailable` 并提供空数组/缺失原因。Presentation 校验只验证展示合同（版本、ID 唯一性、固定分类顺序、引用格式、图表 buckets），AI 质量校验仍由 `final_report_lib.py` 执行。

## HTML 兼容

模板数据先读取 `presentation`，第 04/05 章的读取路径优先使用 `presentation.chapters.chapter04` 和 `chapter05`；当 presentation 不存在时继续使用当前 `conclusion`、`materials` 和 diagnostic 字段。第一阶段不删除旧适配字段。

## 测试

新增独立投影/校验单测，覆盖完整 v4/v5、旧 schema、缺失帧、无素材和证据引用缺失；扩展报告生成测试确认同时输出两个 JSON，且旧 HTML 输入仍可渲染。
