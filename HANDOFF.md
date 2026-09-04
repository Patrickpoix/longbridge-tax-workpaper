# Longbridge Tax Workpaper — 当前交接文档

> 当前活动版本：v1.0.0。测试结果以当前 GitHub Actions 和实际本地执行为准；本文件不维护固定“通过多少条测试”的静态数字。

## 1. 项目目标与边界

本项目把同一长桥证券账户的月结单 PDF 转换为中国内地税收居民可复核的税务工作底稿。它提供计算、证据链、并列税务情景和复核状态，不生成或声称生成法律上最终可直接申报的税表。

核心流水线：

`PDF discovery → parse/OCR fallback → account/year split → cost basis → dividends/withholding → margin interest → readiness → workbook → three delivery levels`

## 2. 当前权威入口与关键文件

- CLI：`scripts/longbridge_tax_workpaper/cli.py`
- 主编排：`scripts/longbridge_tax_workpaper/runner.py`
- 多目录发现：`scripts/longbridge_tax_workpaper/discovery.py`
- 成本编排：`scripts/longbridge_tax_workpaper/cost_basis.py`
- FIFO / moving-average 引擎：`scripts/longbridge_tax_workpaper/cost_basis_engine.py`
- 税务运行配置：`scripts/longbridge_tax_workpaper/config.py`
- 股息/预扣税：`scripts/longbridge_tax_workpaper/dividends.py`
- 复核状态：`scripts/longbridge_tax_workpaper/filing_readiness.py`
- Excel / sanitized workbook：`scripts/longbridge_tax_workpaper/reporting.py`
- 发布隐私检查：`scripts/longbridge_tax_workpaper/release_hygiene.py`、`scripts/validate_release.py`
- Agent 配置：`agents/openai.yaml`
- AI workflow：`SKILL.md`
- 税务/输出/精度边界：`references/`

## 3. 当前运行契约

### 输入与密码

- 主输入目录为 positional `input_dir`。
- 历史证据目录使用可重复的 `--extra-input-dir`，不得把多个目录拼成一个伪路径。
- PDF 发现大小写兼容 `.pdf/.PDF`，并跨全部输入根按 SHA-256 去重。
- 交互式密码通过 `getpass` 隐藏输入；非交互模式使用当前进程的 `LONGBRIDGE_PDF_PASSWORD`。密码不得进入 CLI 参数、源码、日志或产物。

### 成本方法

- 默认主方法：`MOVING_AVERAGE`。
- 工作簿仍同时生成 FIFO 与 moving-average 结果供审计比较。
- `--cost-basis-method FIFO|MOVING_AVERAGE|BOTH` 决定主复核错误合同；不是“只生成该方法”。
- 年初存在正持仓的标的对 FIFO 一律需要历史成本证据，即使本年度后来又买入。
- FIFO-only 历史缺口不得污染已选择的 moving-average readiness。
- 两个方法必须使用独立 opening-lot 状态，禁止共享可变 `Lot` 对象。

### 税务选项

- `--withholding-credit` 是“请求在测算中应用月结单预扣税候选”，应用额 capped at 中国税额；仍必须产生非阻断 WARNING，最终资格需凭证/专业复核。
- `--deduct-margin-interest` 是“融资利息扣除候选/请求”，不是自动法律认定。默认 policy 仍不直接扣除；custom policy 明示可扣除时也保留 WARNING。
- 缺汇率时 CNY 输出为空，不用 0 代替。

### 精度

- 汇率、关键货币金额、税额和成本分配使用 `Decimal` 精度边界。
- 内部关键货币计算规范化到 8 位，CNY 输出 2 位，`ROUND_HALF_UP`。
- PDF 解析值和部分数量字段仍可能是 float；不要写“全链路/全程 Decimal”。

## 4. 三层交付隐私模型

1. `longbridge_<year>_workpapers.zip`：最高敏感级别；详细 JSON/CSV/config/hash/evidence，可按明确请求包含原 PDF。
2. `longbridge_<year>_processed_delivery.zip`：不含原 PDF，但仍包含账户、交易、持仓与来源文件追溯；仅供明确授权的专业复核者。
3. `longbridge_<year>_sanitized_delivery.zip`：去标识化汇总复核包，仅含 sanitized workbook、sanitized `review_status.json`、README、manifest。

sanitized workbook 的 sheet 集必须精确为：

- `年度纳税汇总`
- `财产转让计税情景`
- `年末汇率`
- `复核就绪性`
- `版本信息`

账户显示为“已脱敏”；复核说明统一替换为“详见完整底稿”。sanitized review status 不带 detail、blocking/warning reason、pending review rows 或交易引用。

## 5. Manifest / version

Manifest 将发行包版本与 schema 版本分离：

- `package_version` = `longbridge_tax_workpaper.__version__`
- `schema_version` = `v4`

不要再把 schema 代号混进 package version。

## 6. 发布与隐私控制

- public tests 只能使用 synthetic / irreversible anonymized 数据。
- 禁止把真实敏感值通过 fragments、encoding、字符串拼接等可逆方式藏进仓库。
- `.gitignore` 不是 release gate；权威 release scan 为 `scripts/validate_release.py` + CI tracked-tree validation。
- 当前树修干净不等于 Git 历史干净。若历史存在泄漏，必须在独立备份、Actions 全绿、Git identity 已精确确认后再执行 history rewrite。

## 7. CI / 本地验证合同

GitHub Actions matrix：

- Ubuntu：Python 3.11 / 3.12 / 3.13，执行 coverage gate `--cov-fail-under=77`。
- Windows：Python 3.13，执行功能 `pytest -q`，不在 Windows job 强制 coverage。
- 所有 matrix：验证 module/console `--help`，执行 `python -m build`。
- 非 Windows：构造 clean tracked release tree 后运行 `scripts/validate_release.py`。

本地收尾至少执行：

```bash
git diff --check
python -m pytest -q
python scripts/validate_release.py .
python -m longbridge_tax_workpaper --help
longbridge-tax-workpaper --help
python -m build
```

若本机存在另一个 editable install，必须确认实际 import path 指向当前 checkout，避免“测试通过了错误仓库”。

## 8. 文档读取顺序

1. `README.md`
2. `SKILL.md`
3. `references/tax-boundaries.md`
4. `references/output-sheets.md`
5. `references/precision-and-evidence.md`
6. `references/troubleshooting.md`
7. 当前整改/工作日志（如存在）

## 9. Git 边界

修改、测试、diff 可以在未设置 repo-local identity 时进行；commit/push/history rewrite 必须先精确确认该 GitHub 账户的 commit email，不得猜测。历史改写是最后阶段，不与普通源代码修复混在一起。
