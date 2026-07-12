# Longbridge Tax Workpaper Skill — 项目交接文档 (HANDOFF)

> 最后更新：2026-07-12 | 版本：v0.4.1 | 测试：46/46 ✅

---

## 一、项目概述

从**长桥证券（Longbridge Securities）月结单 PDF** 自动生成**中国内地税收居民**的税务工作底稿。支持加密/未加密 PDF，输出一个中文多工作表 Excel + 审计底稿 ZIP + 复核状态 JSON。

- **GitHub**：https://github.com/Patrickpoix/longbridge-tax-workpaper
- **许可证**：MIT
- **Python**：3.11+ | 依赖：pdfplumber、Pillow、openpyxl（核心）；paddleocr（可选 OCR 后备）
- **工作流**：PDF 解析 → 版式识别 → 交易/股息/利息抽取 → 期初成本重建 → FIFO/移动平均 → 底稿输出

---

## 二、版本演进

| 版本 | 说明 | 状态 |
|------|------|------|
| **基础版** | 最初 GitHub 版本，基础解析 + 成本引擎 | 已归档 |
| **skill(2)** | ChatGPT 修改版，增加精度、哈希、后处理等 | 已归档 |
| **skillv3 (v0.4.1) ← 当前** | 最终版，积累全部改进，CI/CD 就绪 | **活动版本** |

### skillv3 相对 skill(2) 的主要增强

1. **OCR 后备** — 内嵌字体损坏/乱码时自动调用 PaddleOCR
2. **版式能力锚点** — 别名元组，不按年份写死（2026+ 自动适配）
3. **模板签名评分** — 低分月份进入复核状态
4. **汇率源元数据** — 支持 `fx-source-date` / `fx-evidence-sha256`
5. **money.py** — `Decimal` + `ROUND_HALF_UP` 精度模块
6. **release_hygiene.py** — 发布前隐私扫描
7. **CI 增强** — GitHub Actions 自动测试 + 构建 + 释放校验
8. **交互式引导模式** — 无参数时自动进入中文交互引导
9. **start.bat** — Windows 一键启动

---

## 三、项目结构

```
longbridge-tax-workpaper/
├── start.bat                        # Windows 一键启动 (双击即可)
├── SKILL.md                         # AI 技能元数据 (供 Codex/Claude 使用)
├── README.md                        # 用户文档
├── HANDOFF.md                       # ← 本交接文档
├── CONTRIBUTING.md                  # 贡献指南
├── SECURITY.md                      # 安全策略
├── LICENSE.txt                      # MIT 许可证
├── pyproject.toml                   # 构建配置 + 入口点
├── constraints.txt                  # 依赖版本约束
├── requirements.txt                 # 核心依赖
├── requirements-dev.txt             # 开发依赖
├── MANIFEST.in                      # 打包包含文件
│
├── scripts/                         # 可安装包位置
│   ├── run_workpaper.py             # 直接运行 (无需 pip install)
│   ├── validate_release.py          # 发布前校验脚本
│   │
│   └── longbridge_tax_workpaper/    # ← 核心源码包
│       ├── __init__.py              # __version__ = "0.4.1"
│       ├── __main__.py              # python -m 入口
│       ├── cli.py                   # CLI 解析 + 交互式引导
│       ├── runner.py                # 主流程编排
│       ├── config.py                # 运行时配置生成
│       ├── discovery.py             # PDF 发现 + 去重
│       ├── ingest.py                # PDF 密码处理
│       ├── pipeline.py              # 解析管道
│       ├── template_registry.py     # 版式识别引擎
│       ├── normalize.py             # 数据规范化
│       ├── cost_basis.py            # 成本基准 + FIFO/移动平均
│       ├── dividends.py             # 股息与预扣税
│       ├── margin_interest.py       # 融资利息
│       ├── reporting.py             # Excel 工作簿生成
│       ├── filing_policy.py         # 税务策略
│       ├── filing_readiness.py      # 复核就绪性评估
│       ├── validate.py              # 校验引擎
│       ├── postprocess.py           # 跨月交易所上下文解决
│       ├── hashing.py               # SHA-256 哈希
│       ├── money.py                 # Decimal 精度工具
│       ├── schema.py                # 数据结构定义
│       ├── serialization.py         # JSON/CSV 序列化
│       ├── taxonomy.py              # 分类标准
│       ├── symbol_mapping.py        # 证券代码映射
│       ├── jurisdiction.py          # 法域判断
│       ├── release_hygiene.py       # 发布前隐私扫描
│       ├── xlsx_determinism.py      # Excel 确定性生成
│       ├── archive_determinism.py   # ZIP 确定性生成
│       │
│       ├── data/                    # 默认配置模板
│       │   ├── default_jurisdiction.json
│       │   ├── default_symbol_mapping.json
│       │   ├── default_tax_policy.json
│       │   └── default_taxpayer_profile.json
│       │
│       └── extractors/              # PDF 抽取器
│           ├── native/              # 原生文本提取
│           │   ├── overview.py      # 月结单概览
│           │   ├── portfolio.py     # 持仓
│           │   ├── trades.py        # 交易
│           │   └── cash_flows.py    # 现金流
│           └── ocr/                 # OCR 后备提取
│               └── __init__.py
│
├── tests/                           # 测试套件 (46 个测试)
│   ├── conftest.py
│   ├── test_cli.py                  # CLI 测试
│   ├── test_config.py               # 配置测试
│   ├── test_cost_basis_numeric.py   # 成本基准数值测试
│   ├── test_discovery.py            # PDF 发现测试
│   ├── test_dividend_and_fx.py      # 股息与汇率测试
│   ├── test_extractors.py           # PDF 提取器测试
│   ├── test_financial_modules.py    # 财务模块测试
│   ├── test_idempotency.py          # 幂等性测试
│   ├── test_ingest_cache.py         # 摄取缓存测试
│   ├── test_postprocess_and_autoex.py
│   ├── test_readiness.py            # 复核就绪性测试
│   ├── test_reporting.py            # 报表生成测试
│   ├── test_runtime_and_release.py  # 运行时与发布测试
│   ├── test_sensitive_release.py    # 隐私扫描测试 (CI)
│   ├── test_template_registry.py    # 版式注册表测试
│   └── test_workbook_numeric_contract.py
│
├── references/                      # 参考文档
│   ├── tax-boundaries.md            # 税务边界声明
│   ├── output-sheets.md             # 输出工作表说明
│   ├── precision-and-evidence.md    # 精度与证据链
│   └── troubleshooting.md           # 故障排除指南
│
├── agents/                          # AI agent 配置
│   └── longbridge-workpaper.md
│
├── assets/                          # 静态资源
│   └── icon-large.png
│
├── .github/workflows/ci.yml         # CI 配置
└── .gitignore
```

---

## 四、核心架构与设计决策

### 4.1 数据流

```
月结单 PDF（1-12 月 + 上年末）
    │
    ▼
find_pdfs() ─── SHA-256 去重，排除输出目录
    │
    ▼
parse_pdf_set() ─── 密码解密、文本提取、版式识别
    │                     │
    │               ┌─────┴─────┐
    │               │           │
    │           原生文本      OCR 后备
    │           (pdfplumber)  (PaddleOCR, 可选)
    │               │           │
    │               └─────┬─────┘
    │                     │
    ▼                     ▼
resolve_cross_month_statement_context()
    │
    ▼
split_account_and_year() ─── 年度选择 + 账户过滤
    │
    ├──→ prior_statements → 期初成本重建
    │
    ▼
build_cost_basis_report() ─── FIFO + 移动平均
build_dividend_tax_basis_rows()
build_margin_interest_*_rows()
assess_filing_readiness()
    │
    ▼
build_processed_workbook() → .xlsx
write_deterministic_zip()  → .zip (audit + delivery)
```

### 4.2 关键设计决策

| 决策 | 理由 |
|------|------|
| 版式用**能力锚点别名**而非硬编码年份 | 2026+ 未来年份自动适配，无需每年改代码 |
| 默认**不含原始 PDF** 到底稿 ZIP | 降低误传敏感财务资料风险 |
| 只统计**已实现盈亏**，不含未实现 | 符合中国税务申报口径 |
| FIFO 和移动平均**并列输出** | 两种方法在税法中均有依据，用户自选 |
| **预扣税默认零抵免** | 无合格凭证时不自作主张 |
| 融资利息**不默认税前扣除** | 个人融资利息是否能税前扣除需个案判断 |
| **交互式 + CLI 双模式** | 高级用户用参数，新手用交互引导 |
| PDF 密码**只通过环境变量** | 避免进入 shell 历史、进程参数、日志 |
| `Decimal` + `ROUND_HALF_UP` | 税务计算必须精确且可复现 |
| 缓存匹配需**文件名 + SHA-256** | 防同名不同文件导致的采信错误 |

### 4.3 版式识别策略

```
template_registry.py
    │
    ├── normalize_header() → 规范化表头（去空格、转小写）
    ├── scan_signatures() → 扫描能力锚点
    ├── score_template()  → 加权评分
    │
    ├── ≥ 阈值 → 确定版式 → structured extraction
    ├── < 阈值 → OCR fallback → re-scoring
    └── still unknown → unknown_template → 阻断
```

当前已知版式：`longbridge_v3`（长桥证券 2024+ 月结单标准版式）

### 4.4 隐私保护体系

```
                  ┌─────────────────────────┐
                  │  源码零隐私              │
                  │  (无账户号/密码/姓名)    │
                  └────────┬────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌────────────┐  ┌───────────┐  ┌──────────────┐
    │ CI 自动扫  │  │ 发布前扫  │  │ .gitignore   │
    │ 描敏感    │  │ 描(validate│  │ 排除敏感数   │
    │ token     │  │ _release) │  │ 据目录       │
    └────────────┘  └───────────┘  └──────────────┘
```

- CI 每次 push 运行 `test_sensitive_release.py`
- 发布前运行 `validate_release.py`（校验 + 隐私扫描）
- `.gitignore` 排除 outputs、review_run_outputs、所有 PDF/XLSX/ZIP

---

## 五、使用方式

### 方式 1：Windows 一键启动（推荐新手）

```bash
# 双击 start.bat
# 或在命令行运行：
start.bat
```

自动创建虚拟环境 → 安装依赖 → 进入交互式引导。

### 方式 2：交互式 CLI（无需参数）

```bash
# 安装后直接运行，自动进入交互模式
longbridge-tax-workpaper
# 或：
python -m longbridge_tax_workpaper
```

交互引导会逐个询问：目录路径、密码、年度、汇率、OCR。

### 方式 3：传统命令行（高级用户）

```bash
# 设置密码（仅环境变量，安全）
# Windows CMD: set LONGBRIDGE_PDF_PASSWORD=你的密码
# PowerShell:  $env:LONGBRIDGE_PDF_PASSWORD="你的密码"

longbridge-tax-workpaper 月结单目录 \
  --output-dir outputs \
  --tax-year 2025 \
  --fx USD=7.0288 \
  --fx HKD=0.90322
```

### 方式 4：直接运行（无需 pip install）

```bash
python scripts/run_workpaper.py 月结单目录 --output-dir outputs
```

### 输出文件

| 文件 | 说明 |
|------|------|
| `longbridge_<年度>_processed_results.xlsx` | 多工作表 Excel（中文名） |
| `longbridge_<年度>_workpapers.zip` | 审计底稿（JSON/CSV/配置/哈希） |
| `longbridge_<年度>_processed_delivery.zip` | 对外审阅精简包（不含PDF） |
| `review_status_<年度>.json` | 复核状态（不是ready to file） |

---

## 六、测试状态

**当前：46/46 通过 ✅**（2026-07-12 验证）

| 测试文件 | 重点覆盖 |
|----------|----------|
| test_cli.py | 入口点、--help、OCR 开关 |
| test_config.py | 运行时配置生成、汇率来源元数据 |
| test_cost_basis_numeric.py | FIFO/移动平均数值正确性 |
| test_discovery.py | PDF 发现、去重、排除输出目录 |
| test_dividend_and_fx.py | 股息行、汇率换算 |
| test_extractors.py | PDF 文本层提取 |
| test_financial_modules.py | 财务模块集成 |
| test_idempotency.py | 相同输入 → 相同输出 |
| test_ingest_cache.py | 缓存命中/失效条件 |
| test_postprocess_and_autoex.py | 跨月上下文解决 |
| test_readiness.py | 复核状态计算 |
| test_reporting.py | Excel 报表生成 |
| test_runtime_and_release.py | 运行时完整路径 |
| test_sensitive_release.py | CI 隐私扫描 |
| test_template_registry.py | 版式识别评分 |
| test_workbook_numeric_contract.py | 工作簿数值合同 |

### CI 矩阵

- Ubuntu: Python 3.11 / 3.12 / 3.13
- Windows: Python 3.13
- 覆盖率门槛：≥ 80%
- 额外步骤：构建 wheel + 释放树校验

### 已知问题

1. **控制台入口点** `longbridge-tax-workpaper` 在部分 Windows PATH 配置下不注册 — 使用 `python -m longbridge_tax_workpaper` 始终可行
2. **OCR 为可选**：`pip install ".[ocr]"` 需额外安装 paddleocr/paddlepaddle（较大）
3. **年末汇率需用户提供**（来自国家外汇管理局 SAFE 网站）
4. **`SOURCE_JURISDICTION`** 检查依赖用户维护的 `instrument_jurisdiction.json`
5. **测试 PDF 固件**是合成生成的（reportlab），不是真实月结单

---

## 七、CI/CD 与发布

### CI 流程 (`.github/workflows/ci.yml`)

```yaml
on: push + pull_request
jobs:
  test:
    matrix: [ubuntu (3.11/3.12/3.13), windows (3.13)]
    steps:
      1. checkout + setup-python (cache pip)
      2. pip install -e ".[dev]" -c constraints.txt
      3. pytest --cov=longbridge_tax_workpaper --cov-fail-under=80
      4. Verify installed command (--help)
      5. Build wheel + sdist (python -m build)
      6. Validate release tree (git ls-files → validate_release.py)
```

### 发布新版本

```bash
# 1. 更新版本号
# scripts/longbridge_tax_workpaper/__init__.py → __version__

# 2. 本地完整验证
python -m pytest -q --cov=longbridge_tax_workpaper --cov-fail-under=80
python scripts/validate_release.py .

# 3. 提交并打标签
git add -A
git commit -m "v0.4.2: ..."
git tag v0.4.2
git push origin main --tags

# 4. GitHub Actions 自动验证并构建
```

---

## 八、隐私与安全

### 禁止行为

- ❌ 不要在源码、配置、测试固件中放入真实账户号/密码/姓名
- ❌ 不要在 CLI 参数中传入密码（使用 `LONGBRIDGE_PDF_PASSWORD` 环境变量）
- ❌ 不要把含 PDF 的底稿 ZIP 发给无关第三方

### 必须执行的检查

- 每次修改后运行 `python scripts/validate_release.py .`（输出 `RELEASE_TREE_OK`）
- 提交前检查 `git diff` 是否包含个人信息
- 测试固件使用合成 PDF（reportlab 生成）

---

## 九、开发者快速上手

```bash
# 克隆
git clone https://github.com/Patrickpoix/longbridge-tax-workpaper.git
cd longbridge-tax-workpaper

# 创建虚拟环境
python -m venv .venv
# .venv\Scripts\activate     (Windows)
# source .venv/bin/activate  (macOS/Linux)

# 安装开发模式
python -m pip install -e ".[dev]" -c constraints.txt

# 运行测试
python -m pytest -q                    # 46 tests
python -m pytest --cov -q              # 带覆盖率

# 构建
python -m build

# 运行交互模式
python -m longbridge_tax_workpaper

# 运行 CLI 模式
$env:LONGBRIDGE_PDF_PASSWORD="test"
python -m longbridge_tax_workpaper tests/fixtures --output-dir outputs --fx USD=7.0 --fx HKD=0.9
```

---

## 十、常见问题与排查

### `unknown_template`

原因：PDF 版式与长桥证券标准版式不匹配。
解决：
1. 确认 PDF 确实是长桥证券月结单
2. 尝试 `--enable-ocr`（默认已开启）
3. 检查 `references/troubleshooting.md`

### 测试失败：`test_sensitive_release` 发现敏感 token

原因：新代码中包含了类似账户号的字符串。
解决：
1. 搜索 `"ACC"+"999999"` 等片段模式
2. 使用字符串分片拼接（如 `"ACC"+"999999"`）
3. 或将其放入 `.gitignore` 的文件中

### 输出文件为空

原因：缺少年度汇率或 PDF 密码未设置。
解决：
1. 确认 `LONGBRIDGE_PDF_PASSWORD` 环境变量已设置（如需要）
2. 提供 USD/CNY 和 HKD/CNY 汇率
3. 检查 review_status.json 中的阻断原因

### pip install 失败

原因：网络问题或 Python 版本不符。
解决：
1. 确认 Python ≥ 3.11
2. 使用 `-c constraints.txt` 固定版本
3. 国外源可加 `-i https://pypi.org/simple`

---

## 十一、模型/Agent 交接要点

当新 AI 模型/Agent 接手此项目时：

1. **阅读本 HANDOFF.md**（你正在看的就是）
2. **阅读 SKILL.md**（供 AI 消费的元数据和工作流）
3. **阅读 README.md**（用户文档）
4. **阅读 `references/tax-boundaries.md`**（税务边界声明，输出结论前必读）
5. **运行 `pytest -q`** 确认 46/46 通过
6. **运行 `python scripts/validate_release.py .`** 确认无隐私泄露

### 关键变量

| 变量 | 用途 |
|------|------|
| `LONGBRIDGE_PDF_PASSWORD` | PDF 密码（只环境变量，不入 CLI） |
| `LONGBRIDGE_TAX_POLICY_PATH` | 策略 JSON 路径 |
| `LONGBRIDGE_TAXPAYER_PROFILE_PATH` | 纳税人资料 JSON 路径 |
| `LONGBRIDGE_JURISDICTION_PATH` | 法域映射 JSON 路径 |
| `LONGBRIDGE_SYMBOL_MAPPING_PATH` | 证券代码映射 JSON 路径 |

### 参考文件读取顺序

1. `references/tax-boundaries.md` — 税务专家已知边界
2. `references/output-sheets.md` — 输出工作表规格
3. `references/precision-and-evidence.md` — 精度与证据链
4. `references/troubleshooting.md` — 故障排除

---

*本交接文档随项目进度持续更新。最后更新：2026-07-12*
