# 故障排查

- 安装失败：使用 Python 3.11—3.13，在项目根目录执行 `python -m pip install .`。开发模式使用 `python -m pip install -e ".[dev]" -c constraints.txt`。
- 测试导入到旧仓库：执行 `python -c "import longbridge_tax_workpaper; print(longbridge_tax_workpaper.__file__)"` 核对 import path；不要把另一个 editable install 的 PASS 当成本仓库结果。
- 加密 PDF 无法打开：交互模式直接在隐藏密码提示中输入；非交互模式设置当前进程的 `LONGBRIDGE_PDF_PASSWORD`。不要通过命令行参数传密码。
- 历史 PDF 分散在多个目录：重复使用 `--extra-input-dir <dir>`；不要用分号把多个目录拼成一个 positional 路径。程序会跨根按 SHA-256 去重。
- FIFO 期初历史不足：只要标的在纳税年初有正持仓，就补充解释该期初库存所需的历史成交/持仓证据，即使本年度后来又买入。默认 moving-average 主方法可在有可解释正数展示成本时继续计算，但 FIFO 仍保留历史缺口。
- `unknown_template`：先安装 `python -m pip install ".[ocr]"` 后重试。若 OCR 后仍失败，保留 PDF，不继续生成结构化税务结果；为新布局增加能力锚点、表头别名和匿名回归 fixture，不要按年份放宽整个模板。
- PDF 字体或文字层异常：程序会检测空文本、连续方框/替换字符并自动尝试 OCR。OCR 页面图像只写入临时目录并自动删除；文本层与 OCR 冲突时进入复核状态。
- 未指定年度时报错：只有完整 1—12 月年度才会被自动选择；可补齐月结单或显式指定 `--tax-year` 生成不完整年度底稿。
- 人民币金额为空：检查是否同时提供 USD/CNY 与 HKD/CNY 年末汇率；未知汇率不会写成 0。
- 多账户：使用 `--account-id` 分开运行。
- 缓存未命中：只有 `pdf_extracts/<YYYY-MM>/manifest.json` 的文件名和 SHA-256 与当前 PDF 完全一致才会使用缓存。
- 重复运行：程序排除输出目录并按 PDF SHA-256 去重；若仍发现重复，检查是否把旧结果复制到其他未排除输入根。
- sanitized 包仍不适合公开发布：它去除直接/高粒度识别信息，但年度汇总金额仍是敏感财务信息。

## 版式漂移与未来年份

- 识别基于归一化后的能力锚点与表头别名，而非硬编码年份或固定坐标。
- 新增别名时在 `template_registry.py` 对应别名元组中补充同义词，并增加 synthetic/irreversibly anonymized regression fixture。
- 低置信度 OCR 或版式特征得分过低会升级为 `REVIEW_REQUIRED`，不会静默接受。
