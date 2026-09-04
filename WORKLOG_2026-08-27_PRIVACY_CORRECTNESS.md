# Privacy / Correctness Remediation Worklog

> 方案约定文件名保留 `2026-08-27`；本轮实际施工与最终本地验收日期：2026-09-04。

## 1. 任务目的与实际变更范围

本轮按冻结整改方案在 `D:\longbridge-tax-workpaper` 当前 `main` 工作树执行 A→G 源码整改与本地发布验收。施工起点为：

- HEAD：`98b4631f8b53b8c3f3d83f56006a6bb5a1828ddc`
- branch：`main`
- origin：`https://github.com/Patrickpoix/longbridge-tax-workpaper.git`
- 施工开始时 repo-local `user.name` / `user.email`：均未设置；最终通过 GitHub commit API 对 HEAD 的账户归属做权威交叉验证后，已复用该 GitHub 认可的 HEAD author identity 写入 repo-local config；未修改 global Git identity。

实际范围包括：发布隐私门禁、CLI/Windows/多输入根、成本方法与期初证据、预扣税/融资利息语义、成本数值精度、三层交付隐私模型、CI、用户/Agent 文档和对应回归测试。

最终已完成：源码整改 commit/push、GitHub Actions 双轮验证、`main` 与 `v1.0.0` 的受控 history rewrite、显式 `force-with-lease` 更新、rewrite 后 fresh clone / 独立 venv 全链路验证。未猜测 Git identity；repo-local identity 只在 GitHub API 明确确认后设置，global Git identity 未修改。

## 2. 本轮主要正确性结论

### 2.1 发布隐私

- 移除源码中通过字符串分片等可逆方式保存的私有 denylist 值。
- 发布扫描改为通用账户样式、secret assignment 规则和运行时私有 denylist。
- 扩大 release tree 禁止范围至 statement/delivery 常见二进制、`.env*`、key/certificate、runtime config 等。
- `.gitignore` 继续只作为本地误提交缓解措施，不作为 release gate。

### 2.2 CLI / 多目录 / Windows

- 新增可重复 `--extra-input-dir`；不再把多个目录拼成伪路径。
- 跨全部输入根按 PDF SHA-256 去重；`.pdf/.PDF` 均可发现。
- 交互参数通过同一 argparse 合同进入 runner，修复 cost method / OCR 等参数丢失。
- 交互密码使用 `getpass` non-echo；非交互仍使用当前进程环境变量。
- stdout/stderr 在支持时显式配置 UTF-8，提升 Windows 中文 help/输出稳定性。

### 2.3 成本方法与期初证据

- 默认主复核方法改为 `MOVING_AVERAGE`，但 FIFO 与 moving-average 仍同时生成供审计对照。
- readiness 按选中方法的 `method_errors` 判断；FIFO-only 历史缺口不再错误阻断 moving-average。
- 只要目标年度年初有正持仓，FIFO 就要求历史成本证据，即使目标年度内后来又买入同一标的。
- 修复真实跨方法状态污染：FIFO 与 moving-average 原先可能共享同一可变 `Lot`，FIFO 原地扣减后会污染 moving-average 期初状态；现已使用独立副本。
- 正数券商展示期初成本可作为带明确 evidence status 的 moving-average 候选；不可解释/非正成本继续报错，不用 0 代替。

### 2.4 税务 option 语义

- `--withholding-credit` 现在表示“请求在测算中应用月结单预扣税候选”，不是自动法律资格确认。
- 应用的预扣税抵免按对应中国税额封顶；请求后仍保留税务证据 WARNING。
- `--deduct-margin-interest` 现在表示扣除请求/候选；默认仍不自动进入最终扣除。
- custom policy 明示融资利息可扣时程序尊重配置，但 readiness 仍要求人工复核。

### 2.5 数值与审计字段

- FIFO 分配成本与 moving-average pool 的关键货币运算进一步收敛到 Decimal 精度边界。
- 修复 `pool_quantity_before` 曾写入金额量纲的问题；新增独立 `pool_total_cost_before`。
- 文档明确：关键货币边界使用 Decimal，不宣称 PDF 解析与所有数量字段“全链路 Decimal”。

### 2.6 三层交付隐私模型

- `workpapers.zip`：最高敏感级别，可按明确请求含原 PDF。
- `processed_delivery.zip`：不含原 PDF，但仍含账户、交易、持仓、文件追溯等敏感专业复核信息。
- 新增 `sanitized_delivery.zip`：只保留 sanitized workbook、sanitized review status、README、manifest。
- sanitized workbook 的 sheet 集精确限制为：`年度纳税汇总`、`财产转让计税情景`、`年末汇率`、`复核就绪性`、`版本信息`。
- sanitized workbook 的账户显示改为“已脱敏”；复核说明改为“详见完整底稿”。
- sanitized review status 使用字段白名单，不输出详细原因/交易引用。
- manifest 分离 `package_version=1.0.0` 与 `schema_version=v4`。

## 3. 文件变更清单

### 新增

- `WORKLOG_2026-08-27_PRIVACY_CORRECTNESS.md`

### 修改

- `.github/workflows/ci.yml`
- `.gitignore`
- `HANDOFF.md`
- `README.md`
- `SECURITY.md`
- `SKILL.md`
- `agents/openai.yaml`
- `references/output-sheets.md`
- `references/precision-and-evidence.md`
- `references/tax-boundaries.md`
- `references/troubleshooting.md`
- `scripts/longbridge_tax_workpaper/cli.py`
- `scripts/longbridge_tax_workpaper/config.py`
- `scripts/longbridge_tax_workpaper/cost_basis.py`
- `scripts/longbridge_tax_workpaper/cost_basis_engine.py`
- `scripts/longbridge_tax_workpaper/discovery.py`
- `scripts/longbridge_tax_workpaper/dividends.py`
- `scripts/longbridge_tax_workpaper/filing_readiness.py`
- `scripts/longbridge_tax_workpaper/release_hygiene.py`
- `scripts/longbridge_tax_workpaper/reporting.py`
- `scripts/longbridge_tax_workpaper/runner.py`
- `scripts/validate_release.py`
- `start.bat`
- `tests/test_cli.py`
- `tests/test_config.py`
- `tests/test_cost_basis_numeric.py`
- `tests/test_discovery.py`
- `tests/test_dividend_and_fx.py`
- `tests/test_idempotency.py`
- `tests/test_readiness.py`
- `tests/test_reporting.py`
- `tests/test_sensitive_release.py`

### 删除 / 移动 / 重命名

- 无 tracked 文件删除、移动或重命名。
- 本地验证生成的 `.coverage`、pytest/cache、`build/`、`dist/`、egg-info 和失败的临时 `.venv-remediation` 已清理，不属于项目变更。

## 4. 实际运行的测试与验证

以下均为本轮真实执行结果，不包含推断结果：

### 定向回归

- `tests/test_sensitive_release.py`：6 passed。
- `tests/test_cli.py tests/test_discovery.py`：10 passed。
- `tests/test_config.py tests/test_dividend_and_fx.py tests/test_readiness.py`：18 passed。
- `tests/test_cost_basis_numeric.py tests/test_readiness.py`：最终 15 passed。
- `tests/test_reporting.py tests/test_idempotency.py`：3 passed。
- 最终 CLI + cost basis + sanitized 加强回归：13 passed。

### 全量与发布验收

- `python -m pytest -q`：85 passed，17.05s。
- `python -m pytest --cov=longbridge_tax_workpaper --cov-report=term --cov-fail-under=77 -q`：85 passed；总覆盖率 78.43%，达到 77% gate。
- `git diff --check`：PASS。
- `python scripts/validate_release.py .`：`RELEASE_TREE_OK`。
- `python -m longbridge_tax_workpaper --help`：PASS，并显示 `--extra-input-dir`、withholding、margin 新合同。
- `longbridge-tax-workpaper --help`：PASS，在当前仓库 `PYTHONPATH` 下验证当前源码。
- `python -m build`：PASS；成功生成 `longbridge_tax_workpaper-1.0.0.tar.gz` 与 `longbridge_tax_workpaper-1.0.0-py3-none-any.whl`。
- build 临时产物清理后再次执行 `validate_release.py`：`RELEASE_TREE_OK`。
- build 临时产物清理后再次执行 `git diff --check`：PASS。
- 新增 worklog 后再次执行 `validate_release.py`：`RELEASE_TREE_OK`；再次执行 `git diff --check`：PASS。
- 只读 Git 历史扫描：共 46 个历史 commit；非 synthetic 的完整账户样式直接命中为 0，但 46/46 commit 均存在旧 `BLOCKED_TEXT` validator 和可逆账户片段模式；历史禁止二进制/secret 路径计数为 0。结论：历史 rewrite 确有必要，污染来源是旧隐私门禁源码本身，而不是提交了 PDF/XLSX/CSV/ZIP/.env/key 等文件。
- Git identity 权威核验：GitHub REST commit API 成功返回当前 HEAD，并明确关联到仓库 owner `Patrickpoix`；API commit email 与本地 HEAD author email 完全一致。随后仅设置 repo-local `user.name` / `user.email`，未输出具体 email，未改 global config。
- 源码整改提交：`0298350d739debbcbac400afffd629c2792126fb`，提交后普通 push 到用户自己的 `Patrickpoix/longbridge-tax-workpaper`；该次 GitHub Actions Ubuntu 3.11/3.12/3.13 与 Windows 3.13 四个 job 全部 success。
- history rewrite 演练：先从完整 bundle 克隆独立 rehearsal repo，只重写 `scripts/validate_release.py` 与 `tests/test_sensitive_release.py` 中已确认的旧可逆隐私门禁片段；rewrite 后最新树与 rewrite 前树 `0` 文件差异，47 个可达 commit 中可逆片段命中 `0`，`pytest` 85/85 通过。
- 真实 history rewrite：在 fresh-fetch 确认远端未并发前进后，只重写 `main` 与 `v1.0.0`；rewrite 后本地 `main=a091d859be019658f6d794e19bf48f1ec2b56a11`、`v1.0.0=220e5aea814f256a8c7f47f808aec8e21c0e66a9`。最新树与 rewrite 前树仍为 `0` 文件差异，47 个可达 commit 中可逆片段命中 `0`。
- rewrite 后本地完整验证：`pytest` 85/85，coverage 78.43% ≥ 77%，release validator、module/console help、`python -m build` 全部通过。
- 远端更新使用显式旧 SHA 的 `force-with-lease`，分别更新 `main` 与 `v1.0.0`；远端回读 SHA 与本地一致。rewrite 后触发的 GitHub Actions 四个 matrix job 再次全部 success。
- GitHub fresh clone 验证：全新 clone 的 47 个可达 commit 中非 synthetic 完整账户样式命中 `0`、可逆片段命中 `0`、历史禁止文件路径命中 `0`；release tree `RELEASE_TREE_OK`。
- fresh clone 独立 venv 正常升级到现代 setuptools 后执行 `pip install -e ".[dev]"` 成功，import path 明确指向 fresh clone；随后 `pytest` 85/85、coverage 77.85% ≥ 77%、release validator、module/console help、`python -m build` 全部通过。
- 本地 `refs/original` 已删除并执行 GC；rehearsal clone、fresh-clone 验证目录、独立 venv、sanitizer 临时脚本和包含旧历史的临时 backup bundle 均已删除，避免在本机继续保留旧敏感历史副本。
- GitHub 服务器对象可达性复核：虽然旧 commit 已不再被当前 branch/tag 引用，但对 3 个代表性旧 SHA 的 GitHub commit API 查询仍返回 HTTP 200。因此当前结论只能是“远端 refs / fresh clone 历史已清洁”，不能声称 GitHub 服务器端旧对象已经物理清除；该剩余事项需要按 GitHub 官方敏感数据清除流程处理服务器端缓存/对象。

## 5. 发生过的失败、重试与不确定性

以下失败均未被记为 PASS：

1. 初次 focused pytest 实际导入了另一磁盘上的旧 editable install，因此该次测试结果判为无效。
2. 尝试在全局 Python 环境重新 `pip install -e ".[dev]" -c constraints.txt` 时，在卸载旧 editable install 阶段超时；read-back 证明 import path 未切换，因此未记为安装成功。
3. 临时 `.venv-remediation` 使用本机旧 setuptools 时不接受当前 `pyproject.toml` license metadata；该隔离环境安装失败并已删除。最终 `python -m build` 使用 PEP 517 隔离环境和 `setuptools>=77` 成功，说明项目正式 build 路径可用。
4. A 组两次 Local patch 因验证参数/Replace File 语法问题在写入前失败，均为零写入失败。
5. D 组一个 patch 因同文件多个相同上下文被判 ambiguous，在写入前失败，随后使用精确行上下文成功。
6. 成本回归中曾出现 4 个失败：先修复 synthetic statement 无实体 source PDF 时的 evidence hash 假设；剩余 1 个失败最终定位为 FIFO/MA 共享可变 `Lot` 的真实 bug，修复后 15/15 passed。
7. `python -m build` 有一条既有 MANIFEST warning：`tests` 下没有匹配的 `*.pdf`；构建仍成功。该 warning 与本轮 correctness/privacy 目标无功能影响，未为消除 warning 而添加无意义 PDF fixture。
8. 第一次只读历史扫描的 PowerShell/regex 命令因引号组合错误退出，未产生结果、未写入仓库；随后改用只读 Python 包装 Git 成功完成扫描。
9. 尝试使用 GitHub CLI 做 identity 查询时发现当前机器没有 `gh.exe`，因此该路径未执行；随后使用 GitHub REST commit API + 本地 HEAD author metadata 完成等价的权威核验。
10. history rewrite 第一次 rehearsal 使用 tree-filter，在 Local 进程时限内只跑到 13/47；第二次 index-filter 仍因 60 秒执行窗口在 31/47 终止。两次都只发生在独立 rehearsal clone，真实仓库未受影响。第三次提高单次 timeout 后完整完成 47/47。
11. fresh-clone 独立 venv 首轮组合命令中，`pytest` 85/85 和 coverage 77.85% 均已通过，但随后 release validator 按设计拒绝测试刚生成的 `.coverage`、`.pytest_cache`、egg-info、`__pycache__`，导致该串命令在 build 前退出；清理这些本地测试产物后，release validator、两种 help 和 build 独立重跑全部通过。

## 6. 相关验证边界

- GitHub Actions 已在源码整改普通 push 后和 history rewrite force-with-lease 后各运行一轮；两轮的 Ubuntu Python 3.11/3.12/3.13 与 Windows Python 3.13 全部 success。
- 未执行真实券商私有月结单端到端回归。本轮 public/release 边界禁止把真实私有 statement 数据复制到公开仓库或测试 fixture；现有 synthetic/匿名化测试用于验证合同。
- 未安装或运行可选 PaddleOCR 全量环境；本轮未修改 OCR 核心实现，相关既有测试已包含在 85 个全量测试中。
- GitHub fresh clone 已用独立 venv 做正常 editable install 和全量测试/coverage/help/build，因此原机器旧 editable install 的 false-green 风险已通过独立环境验证排除。

## 7. 已知未解决问题与仍不确定的结论

1. 当前 branch/tag refs 与 fresh clone 的可达 Git 历史已经完成清理：47 个可达 commit 的目标可逆片段命中为 0，最新树保持字节级等价。
2. **GitHub 服务器端旧对象仍可通过旧 SHA 直接访问**：代表性旧 commit API 当前仍返回 HTTP 200。下一步是 GitHub 平台侧敏感数据 purge；在该步骤完成并复核旧 SHA 不再可达前，不能宣称远端服务器已彻底物理删除旧对象。
3. 机器全局 Python 仍可能保留指向旧 checkout 的 editable install，但 fresh clone + 独立 venv 已证明正式安装链路可用。若后续长期本机开发，可单独整理全局 Python 环境，不应为此污染项目源码。
4. coverage headroom 仍较小：原工作区 78.43%，fresh-clone 独立 venv 77.85%，均通过冻结 77% gate。后续扩展代码时应关注门槛余量，但不为覆盖率数字本身堆低价值测试。
5. sanitized delivery 是去标识化汇总包，不等于公开可发布数据；年度金额本身仍是敏感财务信息。

## 8. 安全、数据、研究与执行边界

- 未向第三方上传真实券商 PDF、账户数据或密码。
- 未在 public fixture / source 中新增真实敏感值。
- 未用可逆编码、字符串拼接等方式隐藏真实敏感值。
- history rewrite 只修改已确认的两个隐私门禁源码/测试文件中的旧可逆片段，未顺手重写业务逻辑。
- 普通源码整改先 commit/push 并等待 Actions 全绿；history rewrite 之后使用显式旧 SHA 的 `force-with-lease` 更新 `main` 与 `v1.0.0`，没有对任何官方/上游仓库推送。
- 未猜测 GitHub commit email；只在 GitHub API 明确确认 HEAD commit 的账户归属和 email 匹配后，把同一 identity 写入 repo-local config；未改 global config。
- 未把税务 option 的用户请求表述成最终法律结论；程序和文档都保留复核边界。
- 本轮只在用户授权的本地项目中修改代码/文档，没有触碰无关仓库。

## 9. 下一位执行者优先阅读与下一步

优先阅读：

1. `HANDOFF.md`
2. `WORKLOG_2026-08-27_PRIVACY_CORRECTNESS.md`
3. `README.md`
4. `SECURITY.md`
5. `scripts/longbridge_tax_workpaper/runner.py`
6. `scripts/longbridge_tax_workpaper/cost_basis.py`
7. `scripts/longbridge_tax_workpaper/filing_readiness.py`
8. `scripts/longbridge_tax_workpaper/release_hygiene.py`
9. `.github/workflows/ci.yml`

下一步顺序：

1. 按 GitHub 官方“Removing sensitive data from a repository”流程处理服务器端旧对象/缓存 purge；向 GitHub Support 提供仓库、已完成 rewrite/force-push 的说明及需要 purge 的旧对象范围，但不要在公开 issue 中重新粘贴敏感值。
2. Support 完成后，重新查询代表性旧 SHA；只有旧对象不再可访问，才把服务器端清理状态改为完成。
3. 若任何泄漏内容属于仍有效 credential/token/password，必须独立 revoke/rotate；本轮已确认的主要污染是旧隐私门禁中的账户片段，不应把 history rewrite 当作 credential rotation 的替代品。
4. 后续开发保持当前 release validator、sanitized delivery、method-specific readiness 与 GitHub Actions gate；不要恢复可逆私有 denylist 或把真实敏感值写回 public tests/source。
