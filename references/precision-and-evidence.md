# 精度、汇率和证据

## 精度

- PDF 解析层与部分数量字段仍可能使用 `float`；本项目不宣称全链路都是 Decimal。
- 汇率、关键货币金额、税额与成本分配进入计算边界时统一转换到 `Decimal`。
- 关键内部货币计算保留 8 位精度；人民币输出保留 2 位小数。
- 取整使用 `ROUND_HALF_UP`。
- 年度汇总优先汇总未做展示级取整的金额，再按输出精度规范化。
- 审计字段必须保持量纲正确，例如 `pool_quantity_before` 是数量，`pool_total_cost_before` 是金额。

## 年末汇率

为每个币种记录：

- 汇率数值；
- 汇率日期；
- 发布机构或来源状态；
- 来源 URL；
- 归档证据 SHA-256（如有）。

脚本本身不伪造或猜测官方汇率。ChatGPT 或用户负责提供汇率及来源元数据；缺失时人民币字段为空并进入复核阻断。

## PDF、缓存与交付证据

- 完整工作簿/底稿的文件追溯表记录原 PDF 文件名和 SHA-256。
- 文本 sidecar 缓存必须同时匹配文件名和 SHA-256。
- 多输入目录中的 PDF 跨根按 SHA-256 去重。
- 原始 PDF 默认不复制进底稿 ZIP；`--include-source-pdfs` 仅用于明确的本地归档。
- `processed_delivery.zip` 仍保留高粒度追溯信息；`sanitized_delivery.zip` 删除来源文件名/SHA 和交易/持仓级信息。
- manifest 的 `package_version` 取真实包版本，`schema_version` 独立为 `v4`。
