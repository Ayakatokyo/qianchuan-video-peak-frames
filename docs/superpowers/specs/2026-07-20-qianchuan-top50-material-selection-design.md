# TOP 50 素材池前置能力设计

## 目标与边界

本功能从千川资产表
`ods_api_jlqc_uni_overall_roi_live_video_info_du` 读取指定日期范围的
`origin_data`，在本地解析、筛选、按素材汇总整体消耗，并输出稳定的
`top_materials.json`。该文件供后续单素材诊断和高光截帧流程消费。

本期不调用或修改未配置的单素材诊断 RPA，也不处理视频下载、诊断数据
导入或高光截帧。

## 命令接口

新增独立入口：

```bash
python scripts/export_top_materials.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-05 \
  --output "/path/to/top_materials.json" \
  --name-regex "..." \
  --material-type "..." \
  --material-type "..."
```

- `--start-date`、`--end-date` 和 `--output` 必填，日期使用 ISO
  `YYYY-MM-DD` 格式，且起始日期不得晚于结束日期。
- `--top-n` 可选，默认 `50`。
- `--name-regex` 可选。存在时使用 Python 正则在完整素材名称中搜索匹配。
- `--material-type` 可重复；存在时素材类型精确匹配任一值即可保留。
- 不提供名称或类型参数时，不对此维度过滤。

## 内置只读资产表连接器

连接器实现位于当前仓库，不依赖外部资产表查数技能目录，也不调用其脚本。
它从环境读取 `ENV_BACKEND_HOST` 和会话值；凭证不进入命令参数、日志、
异常、输出文件或 Git。

连接器遵循已验证的平台协议：

1. 检索资产表名称，取得资产表对象 ID。
2. 获取表详情，得到实际物理表名。
3. 获取字段列表，严格验证存在 `dt` 分区键和 `origin_data` 字段。
4. 针对日期范围中的每个 `dt` 构造只读分页查询。

程序不接受用户提供的 SQL，仅构造如下形式的单条 `SELECT`：

```sql
SELECT origin_data
FROM <validated-physical-table>
WHERE dt = '<date>'
ORDER BY origin_data
LIMIT 2000 OFFSET <offset>
```

请求动态 SQL 预览接口时固定页面大小为 2,000，并从响应行的 `origs` 值读取
原始 JSON 字符串。每个分区持续请求下一 SQL 页，直至获得不足 2,000 行的页。
所有元数据、HTTP、响应结构和分页检查都通过后才继续业务处理。连接器不执行
任何修改数据或结构的操作。

## 解析、筛选与排名

每个 `origin_data` 必须解析为 JSON 对象，并使用下列路径：

| 业务字段 | JSON 路径 |
| --- | --- |
| 素材 ID | `dimensions.material_id.Value` |
| 视频名称 | `dimensions.roi2_material_video_name.ValueStr` |
| 素材类型 | `dimensions.roi2_material_type_v3.ValueStr` |
| 视频类型 | `dimensions.roi2_material_video_type.ValueStr` |
| 整体消耗 | `metrics.stat_cost_for_overall_roi2.Value` |

解析后先应用名称和类型筛选，再按规范化后的 `materialId` 汇总整体消耗。成本在
内存中以 `Decimal` 累加。不得在行级取 TOP N；同一素材 ID 的所有符合条件行都
必须计入后再排序。

`materialId=-2` 代表 AIGC 动态创意集合，不能映射到单条诊断 RPA。它是唯一
正常业务排除：仍统计行数和整体消耗，但不会进入 `materials`，并在输出中写明
排除原因。

同一素材 ID 的名称、素材类型或视频类型若冲突，导出失败。无效 JSON、缺失或
无效素材 ID、无效整体消耗，以及筛选后没有合格素材，也会失败且不发布结果。

排序为整体消耗降序、规范化 `materialId` 升序。后一个条件使成本相同的排名稳定。

## 输出契约

最终文件为 UTF-8 JSON、固定键顺序并带一个末尾换行。先写入同目录临时文件，
全部成功后原子替换目标文件；已存在的目标文件默认视为冲突。

```json
{
  "actualStartDate": "2026-05-01",
  "actualEndDate": "2026-05-05",
  "rankingMetric": "stat_cost_for_overall_roi2",
  "filters": {
    "materialNameRegex": null,
    "materialTypes": []
  },
  "excluded": [
    {
      "materialId": "-2",
      "reason": "AIGC dynamic creative collection cannot map to a single-material diagnosis RPA",
      "sourceRowCount": 0,
      "totalCost": 0
    }
  ],
  "materials": [
    {
      "actualStartDate": "2026-05-01",
      "actualEndDate": "2026-05-05",
      "rankingMetric": "stat_cost_for_overall_roi2",
      "rank": 1,
      "materialId": "7614072931477585947",
      "materialName": "...",
      "materialType": "...",
      "videoType": "...",
      "totalCost": 38809.75
    }
  ]
}
```

成本在 JSON 中为数值，不是字符串。根对象的周期、排名口径与每条素材记录中的
字段重复写入，令下游可以独立消费任一记录。

## 失败策略和测试

元数据请求、每页查询和响应校验使用有限重试。网络错误、非成功业务响应、分页
异常、数据结构异常和输出冲突均终止本次命令，不生成最终文件。

新增单元测试不访问真实资产表，使用伪造 HTTP 会话覆盖：元数据校验、逐日多页
读取、单日超过 2,000 行、嵌套 JSON 路径、跨行汇总、名称/类型筛选、AIGC 排除、
成本并列排序、稳定 JSON 写入，以及失败时不发布输出。现有高光截帧回归测试保持
不变。
