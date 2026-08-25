# 千川报表元数据验证与旧资产表路径清理设计

## 目标

将报表网关连通性验证与完整素材报告生成拆分。前者只验证授权请求和脱敏网关元数据落盘，不依赖内网对象存储结果文件；后者在内网可达时必须下载、解析、排名并向 RPA 提供素材 ID。

同时移除已不再被标准工作流使用的资产表取数客户端及其专属测试。

## 命令与运行目录

新增 `python scripts/run_standard_analysis.py fetch-metadata` 子命令，必填参数为：

- `--shop-id`
- `--advertiser-id`
- `--start-date`
- `--end-date`
- `--output-dir`

调用方将 `--output-dir` 指向本技能根目录的 `runs/<run-id>`。技能文档的标准命令使用该目录，自动化执行也必须为每次运行创建唯一的 `<run-id>`，不得使用 `/tmp` 作为用户可追溯的运行输出。命令创建该目录，并在其中写入：

- `source-data/gateway-response.json`：成功报表网关响应的脱敏元数据。
- `run-summary.json`：状态为 `metadata_ready`，包含请求参数与 `sourceData` 的相对路径。

该子命令不下载结果 CSV、不生成 `top_materials.json`、不创建 ROI 事实，也不调用 RPA。

## 实现边界

报表客户端将网关调用与结果文件解析拆分为两个公开的窄接口：

- `fetch_material_report_metadata(...)`：执行授权网关调用，验证 HTTP 成功和 `success=true`，写入脱敏元数据，返回不含认证信息的元数据摘要。
- 既有 `fetch_material_report(...)`：复用上述网关调用后继续下载并解析文件，保持完整 `prepare` 的失败语义不变。

标准 `prepare` 仍以调用方提供的输出目录为运行根目录，并继续在该目录的 `source-data/` 落盘完整报表取数工件。技能入口为用户创建一个唯一的 `runs/<run-id>` 目录后调用相应命令，避免将生产运行工件写入临时目录。

完整取数沿用参考技能的 `result_type=files` 交付模式：网关成功后先写入 `source-data/gateway-response.json`，内网可达时将结果文件按连接器返回的文件名原样保存为 CSV，并生成同目录的 `<csv 文件名>.structured.json`。结构化文件是完整 JSON 数组，保留每个素材记录的 `dimensions`、`metrics` 与嵌套字段；导出器从该记录集生成 `top_materials.json` 和 ROI 事实，以此完全替代原资产表 `origin_data` 的输入。网关元数据继续脱敏，不保存会话、Cookie、签名查询参数或其他认证信息。

## 旧路径删除范围

删除以下已无运行时消费者的旧资产表取数代码：

- `scripts/asset_table_client.py`
- `tests/test_top_materials.py` 中导入该客户端及其专属的发现、SQL、分页、重试与错误处理测试。

保留 `scripts/asset_metrics_lib.py`、`asset-material-facts-v1.json`、字段映射工件及其测试。它们是新报表记录的指标聚合和下游报告兼容契约，不会发起资产表请求。

## 错误处理与验证

元数据验证仅在网关 HTTP 请求失败、响应非 JSON、响应不是对象或 `success=false` 时失败。结果文件链接为内网地址而无法下载，不影响 `fetch-metadata` 的 `metadata_ready` 结论，但完整 `prepare` 仍在该情况下失败，并保留已经写入的脱敏网关元数据以便排障。内网可达时，只有 CSV 与结构化 JSON 都成功落盘且包含非空记录，完整取数阶段才可继续。

测试覆盖：

- 成功网关响应写入脱敏元数据且不进行下载。
- 文件型成功响应在内网可达时写入网关元数据、原始 CSV 与结构化 JSON，并以结构化记录驱动现有候选排名和 ROI 聚合。
- `fetch-metadata` 在指定运行目录写入 `metadata_ready` 摘要。
- 网关失败不产生成功状态。
- 删除后仓库没有 `asset_table_client` 的运行时或测试引用。
