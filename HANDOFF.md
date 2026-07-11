# Longbridge Tax Workpaper Skill — 项目交接文档 (HANDOFF)

## 项目概述

从长桥证券月结单 PDF 自动生成中国内地税收居民的工作底稿（多工作表 Excel + 审计底稿 ZIP + 精简交付包 + 复核状态 JSON）。

GitHub: https://github.com/Patrickpoix/longbridge-tax-workpaper

## 工作区结构

`
E:\github发表\longbridge tax workpaper\
├── longbridge-tax-workpaper\          # GitHub 仓库最初版本（基础版）
├── skill(2)\longbridge-tax-workpaper\ # ChatGPT 修改版（增加 precision-and-evidence 等）
├── skillv3\longbridge-tax-workpaper\  # ✅ 当前最终版（v0.4.1，已推送到 GitHub）
│   ├── SKILL.md                       # Codex 技能描述（AI 使用入口）
│   ├── README.md                      # 用户说明（含 badge、流程图、OCR 安装等）
│   ├── pyproject.toml                 # 版本 0.4.1
│   ├── HANDOFF.md                     ← 本文档
│   ├── scripts/
│   │   ├── run_workpaper.py           # 无 pip 安装时的直接运行入口
│   │   ├── validate_release.py        # GitHub 发布前校验工具
│   │   └── longbridge_tax_workpaper/  # 核心源码包
│   │       ├── cli.py                 # argparse CLI，支持 --fx-source-date 等
│   │       ├── pipeline.py            # PDF→StatementResult 流水线（含 OCR 降级）
│   │       ├── discovery.py           # PDF 发现、去重、年度/账户拆分
│   │       ├── template_registry.py   # 版式识别（别名 + 能力锚点，不写死年份）
│   │       ├── config.py              # 运行时配置生成（policy/profile/jurisdiction）
│   │       ├── cost_basis.py          # FIFO + 移动加权平均成本引擎
│   │       ├── reporting.py           # 多工作表 Excel 生成
│   │       ├── filing_readiness.py    # 复核就绪性 21 项检查
│   │       ├── money.py               # Decimal ROUND_HALF_UP 精度模块
│   │       ├── hashing.py             # SHA-256 流式哈希
│   │       ├── release_hygiene.py     # 发布前隐私扫描
│   │       ├── symbol_mapping.py      # 证券代码精确映射
│   │       ├── postprocess.py         # 自动行权/公司行动后处理
│   │       ├── ingest.py              # PDF 解密+文本抽取
│   │       ├── extractors/
│   │       │   ├── native/            # PDF 原生文本层解析
│   │       │   │   ├── overview.py    # 账户总览
│   │       │   │   ├── trades.py      # 股票/期权成交
│   │       │   │   ├── portfolio.py   # 持仓/现金余额
│   │       │   │   └── cash_flows.py  # 资金流水
│   │       │   └── ocr/               # PaddleOCR 后备
│   │       │       └── paddle.py
│   │       └── data/                  # 默认配置（税务政策、纳税人资料、法域映射等）
│   ├── tests/                         # 46 个测试
│   ├── references/                    # 参考文档（税务边界、输出规范、排查指南）
│   ├── assets/                        # 图标
│   ├── .github/workflows/ci.yml       # GitHub Actions CI（3.11-3.13 + Windows）
│   └── agents/openai.yaml             # Codex 技能元数据
├── skillv3\.review\                   # 审查记录（含真实 PDF 验证输出，不进仓库）
├── 个人版本\                           # 用户个人版本（含真实月结单数据，不进仓库）
└── Longbridge Tax Workpaper Skill 项目交接文档.docx  # ChatGPT 写的交接文档
`

## 版本演进

| 版本 | 路径 | 特性 |
|------|------|------|
| 基础版 | longbridge-tax-workpaper\ | 最初 GitHub 版本，基础解析+成本引擎 |
| skill(2) | skill(2)\ | ChatGPT 修改版，加 precision-and-evidence、hashing、postprocess 等 |
| **skillv3 (v0.4.1)** | skillv3\longbridge-tax-workpaper\ | ✅ 当前最终版，累积所有改进 |

## skillv3 相对 skill(2) 的主要增强

1. **OCR 后备** — 内嵌字体损坏/乱码时自动调用 PaddleOCR；结果需通过月份、账户、金额校验
2. **版式能力锚点** — 别名元组（繁简、康熙部首、同义表头），不再按 2024/2025 写死版式；2026+ 措辞变化可自适应
3. **模板签名评分** — 识别成功后打分数 (≥8 可信)；低分月份进入 月结单版式识别置信度 复核状态
4. **--fx-source-date / --fx-evidence-sha256** — 汇率来源元数据支持
5. **money.py** — Decimal ROUND_HALF_UP 统一精度，内部 8 位/展示 2 位
6. **elease_hygiene.py** — 发布前扫描敏感信息（账户号、密码、真实姓名等）
7. **更好的 README** — badge + mermaid 流程图 + OCR 安装说明
8. **CI 增强** — GitHub Actions 自动跑测试 + build + release 校验

## 测试状态（本地 Windows Python 3.11）

`
45 passed, 1 failed

FAILED: test_console_entrypoint_help_runs_after_install
原因: Windows 上 pip install 后 console script 未注册到 PATH
不影响功能: 可用 python -m longbridge_tax_workpaper 替代
`

涵盖: 模板识别、OCR 降级、成本引擎、股息预扣税、融资利息、敏感信息扫描、工作簿输出、配置生成、CLI 参数、幂等性、缓存行为

### 2025 真实数据验证结果（.review/real-output-v041）

- 12 个月全部正确解析
- 成本引擎 FIFO + 移动平均正常生成
- 期初成本从 2024/8-12 月重建（80 笔事件）
- 唯一阻断: 缺少 --fx USD=... --fx HKD=...（预期行为，CNY 留空不写 0）

## 关键设计决策

1. **密码安全**: 只通过 LONGBRIDGE_PDF_PASSWORD 环境变量传入，不进 CLI 参数/日志/工作簿
2. **OCR 不作为默认**: 先走原生文本层，仅在文本异常时才 OCR
3. **不写死汇率**: 缺汇率时 CNY 为空并阻断复核，绝不用 0 代替
4. **情景并行**: 同时输出 FIFO/移动平均、分市场/跨市场/不抵亏三种测算
5. **已知版式**: 基于别名+能力锚点，不按年份写死
6. **期初成本**: 从真实历史成交重建，不用券商摊薄展示成本

## 使用方式

`ash
pip install .
="你的密码"
longbridge-tax-workpaper 月结单目录 --output-dir outputs --tax-year 2025 \
  --fx USD=7.0288 --fx HKD=0.90322 \
  --fx-source USD=https://www.safe.gov.cn/... \
  --fx-source HKD=https://www.safe.gov.cn/...
`

未安装 console entrypoint 时:
`ash
python -m longbridge_tax_workpaper 月结单目录 ...
`

## 已知限制 / 待改进

- console entrypoint 在部分 Windows 环境可能不可用（不影响 python -m）
- SOURCE_JURISDICTION 检查依赖于用户维护的 instrument_jurisdiction.json
- OCR 模块 (paddleocr) 是可选依赖，需要 pip install ".[ocr]"
- 2024 年只有 8-12 月数据，测试覆盖不完全
- combined-real-input 中含真实账户号 ACCOUNT_ID，已在 .gitignore 和 elease_hygiene.py 中屏蔽
- 年末汇率需要用户自行从国家外汇管理局网站获取并传入

## 隐私注意事项

- 源码和仓库中不含真实账户号、密码、姓名
- 	est_sensitive_release.py 和 alidate_release.py 发布前自动扫描敏感 token
- combined-real-input/、uture-input/、uture-output/、eal-output/ 等审查目录在 .gitignore 外，不进仓库
- 原始 PDF 默认不进输出 ZIP，需命令行 --include-source-pdfs

## 哪些文件已推送到 GitHub

所有 79 个文件已提交并推送到 main 分支。见 https://github.com/Patrickpoix/longbridge-tax-workpaper
