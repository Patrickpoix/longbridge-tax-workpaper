# Longbridge Tax Workpaper Skill — 项目交接文档 (HANDOFF)

## 项目概述

从长桥证券月结单 PDF 自动生成中国内地税收居民的工作底稿。

GitHub: https://github.com/Patrickpoix/longbridge-tax-workpaper

## 版本演进

| 版本 | 特性 |
|---|---|
| 基础版 | 最初 GitHub 版本，基础解析+成本引擎 |
| skill(2) | ChatGPT 修改版，增加精度、哈希、后处理等 |
| **skillv3 (v0.4.1)** | 当前最终版，积累所有改进 |

## skillv3 相对 skill(2) 的主要增强

1. **OCR 后备** — 内嵌字体损坏/乱码时自动调用 PaddleOCR
2. **版式能力锚点** — 别名元组，不按年份写死
3. **模板签名评分** — 低分月份进入复核状态
4. **汇率源元数据** — 支持 fx-source-date/fx-evidence-sha256
5. **money.py** — Decimal ROUND_HALF_UP 精度模块
6. **release_hygiene.py** —发布前隐私扫描
7. **CI 增强** — GitHub Actions 自动测试+构建+释放校验

## 测试状态

45/46 通过（唯一失败是 Windows 入口点 PATH 问题，不影响 python -m）

## 隐私保护措施

- 源码、配置和仓库中不含任何真实账户号、密码、姓名
- test_sensitive_release.py 在 CI 中自动扫描敏感 token
- validate_release.py 发布前校验脚本包含同样的扫描逻辑
- 本地审查目录（含真实测试数据）在 .gitignore 中，不进仓库
- 原始 PDF 默认不进输出 ZIP
- GitHub Actions CI 每次 push 自动运行隐私检查
