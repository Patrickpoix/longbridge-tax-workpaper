from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from typing import Any

from . import __version__
from .runner import run_workpaper
from .discovery import find_pdfs, parse_pdf_set, split_account_and_year
from .postprocess import resolve_cross_month_statement_context
from .cost_basis import _securities_needing_prior_data
from pathlib import Path


def _key_value_pairs(values: list[str], *, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise argparse.ArgumentTypeError(f"{label}格式应为 USD=值")
        key, raw = value.split("=", 1)
        key = key.strip().upper()
        raw = raw.strip()
        if not key or not raw:
            raise argparse.ArgumentTypeError(f"无效{label}: {value}")
        result[key] = raw
    return result


def _fx(values: list[str]) -> dict[str, str]:
    """Parse and validate FX rate strings, returning them as strings to preserve precision."""
    result: dict[str, str] = {}
    for currency, raw_rate in _key_value_pairs(values, label="汇率").items():
        try:
            rate = Decimal(raw_rate)
        except InvalidOperation as exc:
            raise argparse.ArgumentTypeError(f"无效汇率: {currency}={raw_rate}") from exc
        if rate <= 0:
            raise argparse.ArgumentTypeError(f"汇率必须大于0: {currency}={raw_rate}")
        result[currency] = str(rate)
    return result


def _fx_metadata(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    urls = _key_value_pairs(args.fx_source, label="汇率来源")
    dates = _key_value_pairs(args.fx_source_date, label="汇率来源日期")
    evidence = _key_value_pairs(args.fx_evidence_sha256, label="汇率证据SHA-256")
    currencies = set(urls) | set(dates) | set(evidence)
    return {
        currency: {
            "source_status": "documented" if currency in urls or currency in evidence else "provided",
            "source_url": urls.get(currency),
            "source_date": dates.get(currency),
            "evidence_sha256": evidence.get(currency),
        }
        for currency in currencies
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从长桥证券月结单PDF生成中国内地税务工作底稿"
    )
    parser.add_argument("input_dir", nargs="?", help="包含月结单PDF的目录（递归扫描子目录中所有 *.pdf）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--output-dir", default="outputs", help="输出目录")
    parser.add_argument(
        "--tax-year", type=int, help="纳税年度；不填时只自动选择完整的1月至12月年度"
    )
    parser.add_argument("--account-id", help="多账户时指定账户编号")
    parser.add_argument(
        "--fx",
        action="append",
        default=[],
        help="年末人民币汇率中间价：1 USD = ? CNY，精确到4位小数。例如 --fx USD=7.0288 --fx HKD=0.90322（也支持 --fx-source-date 记录来源日期）",
    )
    parser.add_argument(
        "--fx-source",
        action="append",
        default=[],
        help="可选汇率来源URL，例如 --fx-source USD=https://...",
    )
    parser.add_argument(
        "--fx-source-date",
        action="append",
        default=[],
        help="可选汇率来源日期，例如 --fx-source-date USD=2025-12-31（会自动记录到Excel汇率工作表中）",
    )
    parser.add_argument(
        "--fx-evidence-sha256",
        action="append",
        default=[],
        help="可选归档证据SHA-256",
    )
    parser.add_argument("--policy", help="可选税务情景JSON")
    parser.add_argument("--profile", help="可选纳税人资料JSON")
    parser.add_argument("--jurisdiction", help="可选发行人/合约法域映射JSON")
    parser.add_argument(
        "--symbol-map",
        help="可选证券名称到代码映射JSON；未知名称不会猜测",
    )
    ocr_group = parser.add_mutually_exclusive_group()
    ocr_group.add_argument(
        "--enable-ocr",
        dest="enable_ocr",
        action="store_true",
        help="启用OCR自动后备（默认；仅在文本层或版式识别异常时使用）",
    )
    ocr_group.add_argument(
        "--disable-ocr",
        dest="enable_ocr",
        action="store_false",
        help="禁用OCR后备，仅使用PDF内嵌文本层",
    )
    parser.set_defaults(enable_ocr=True)
    parser.add_argument(
        "--include-source-pdfs",
        action="store_true",
        help="在底稿ZIP中复制原始PDF（高度敏感；默认不复制）",
    )
    # 税务口径选择参数（保守默认值 = 最稳妥的税务处理）
    parser.add_argument(
        "--cost-basis-method",
        choices=["FIFO", "MOVING_AVERAGE", "BOTH"],
        default="MOVING_AVERAGE",
        help="成本计算方法，默认 MOVING_AVERAGE（券商展示成本，无需前期数据）| FIFO（先进先出，需提供纳税年前月结单）| BOTH（并列输出两种）",
    )
    parser.add_argument(
        "--withholding-credit",
        action="store_true",
        help="启用境外预扣税抵免（默认关闭；仅在持有合格境外纳税凭证时启用）",
    )
    parser.add_argument(
        "--deduct-margin-interest",
        action="store_true",
        help="允许融资利息税前扣除（默认不扣除；需个案判断是否符合税法条件）",
    )
    return parser


def _interactive_prompt() -> tuple[dict[str, Any], list[str]]:
    """交互式引导：当无命令行参数时，逐项询问用户输入。"""
    print("=" * 54)
    print("  长桥证券税务工作底稿 — 交互式模式")
    print("=" * 54)
    print()

    # 1. 输入目录
    while True:
        raw = input("请输入月结单目录路径（可直接拖入文件夹）:\n"
                     "提示：如果选 FIFO/BOTH，年初有持仓的标的需要提供开仓以来月份的月结单\n> ").strip().strip('"').strip("'")
        if not raw:
            print("错误：必须指定月结单目录", file=sys.stderr)
            sys.exit(1)
        if raw.lower() == "q":
            sys.exit(1)
        if not os.path.isdir(raw):
            print(f"错误：目录不存在或无法访问: {raw}", file=sys.stderr)
            print("请重新输入，或输入 q 退出")
            continue
        input_dir = raw
        break

    # 2. 密码
    pwd = input("\nPDF密码（未加密则直接回车）:\n> ")
    if pwd:
        os.environ["LONGBRIDGE_PDF_PASSWORD"] = pwd

    # 3. 纳税年度
    while True:
        year_raw = input("\n纳税年度（例如 2025，回车自动检测完整年度）:\n> ").strip()
        if not year_raw:
            tax_year = None
            break
        try:
            tax_year = int(year_raw)
            if tax_year < 2010 or tax_year > 2100:
                print("错误：年度应在 2010-2100 之间", file=sys.stderr)
                continue
            break
        except ValueError:
            print(f"错误：无效年度 '{year_raw}'，请输入4位数字（如 2025）", file=sys.stderr)

    # 4. 输出目录
    out_raw = input("\n输出目录（默认 outputs）:\n> ").strip().strip('"').strip("'")
    output_dir = out_raw or "outputs"

    # 5. 汇率（1外币=?人民币，精确到4位小数）
    fx_args: list[str] = []
    usd = input("\nUSD/CNY 年末汇率（1 USD = ? CNY，例如 7.0288，回车跳过）:\n> ").strip()
    if usd:
        try:
            Decimal(usd)
            fx_args.append("--fx=USD=" + usd)
        except InvalidOperation:
            print(f"警告：忽略无效USD汇率 '{usd}'")
    hkd = input("\nHKD/CNY 年末汇率（1 HKD = ? CNY，例如 0.90322，回车跳过）:\n> ").strip()
    if hkd:
        try:
            Decimal(hkd)
            fx_args.append("--fx=HKD=" + hkd)
        except InvalidOperation:
            print(f"警告：忽略无效HKD汇率 '{hkd}'")

    # 6. 来源URL
    usd_url = input("\nUSD汇率来源URL（可选，回车跳过）:\n> ").strip()
    if usd_url:
        fx_args.append("--fx-source=USD=" + usd_url)
    hkd_url = input("\nHKD汇率来源URL（可选，回车跳过）:\n> ").strip()
    if hkd_url:
        fx_args.append("--fx-source=HKD=" + hkd_url)

    # 7. OCR
    ocr_raw = input("\n启用OCR后备？(Y/n，默认 Y):\n> ").strip().lower()
    if ocr_raw in ("n", "no"):
        fx_args.append("--disable-ocr")

    # 8. 成本计算方法
    print()
    print("--- 成本计算方法选择 ---")
    print("  MOVING_AVERAGE (默认): 使用券商展示成本（移动平均），无需前期月结单")
    print("  FIFO              : 先进先出法 ⚠ 需提供纳税年度前买入月份的月结单")
    print("  BOTH              : 并列输出两种方法 ⚠ 需提供纳税年度前买入月份的月结单")
    print()
    while True:
        cbm_raw = input("请选择（回车默认 MOVING_AVERAGE，或输入 FIFO / BOTH / MA）:\n> ").strip().upper()
        if not cbm_raw or cbm_raw in ("MA", "MOVING_AVERAGE"):
            print("  已选择：MOVING_AVERAGE（券商展示成本）")
            fx_args.append("--cost-basis-method=MOVING_AVERAGE")
            break
        elif cbm_raw in ("FIFO", "BOTH"):
            label = "FIFO" if cbm_raw == "FIFO" else "BOTH（含 FIFO）"
            print()
            print(f"  ⚠ {label} 需提供纳税年度前的月结单来追溯 FIFO 成本。")
            print()

            # Pre-scan: find securities needing prior data
            print("  正在扫描 PDF，分析持仓情况...")
            pwd = os.e*******get("LONGBRIDGE_PDF_PASSWORD", "")
            try:
                pdfs = find_pdfs(input_dir)
                stmts_all = resolve_cross_month_statement_context(
                    parse_pdf_set(pdfs, password=[redacted], enable_ocr=("--disable-ocr" not in fx_args))
                )
                _, _, stmts, _ = split_account_and_year(stmts_all, tax_year=tax_year)
                sec_info = _securities_needing_prior_data(stmts)
            except Exception as exc:
                print(f"  ⚠ 扫描异常: {exc}")
                sec_info = {"needs_prior": [], "has_buys": []}

            if sec_info.get("needs_prior"):
                print()
                print(f"  ⚠ 以下标的年初有持仓但本年度无买入记录：")
                for sid in sec_info["needs_prior"]:
                    print(f"     - {sid}")

                print()
                print("  如需计算这些标的的 FIFO 成本，请补充买入记录。")
                print("  如只需 MOVING_AVERAGE，直接回车跳过即可。")

                # 二次目录输入
                extra_dirs = input("\n  请输入补充目录路径（可直接拖入，多个用 ; 分隔，回车跳过）:\n  > ").strip()
                extra_pdfs: list[Any] = []
                if extra_dirs:
                    for d in extra_dirs.split(";"):
                        d = d.strip().strip('"').strip("'")
                        if os.path.isdir(d):
                            extra_pdfs.extend(str(p) for p in find_pdfs(d))
                    if extra_pdfs:
                        print(f"  已找到 {len(extra_pdfs)} 份补充月结单，重新扫描...")
                        try:
                            all_pdfs = list(pdfs) + [Path(p) for p in extra_pdfs]
                            from pathlib import Path
                            stmts_all2 = resolve_cross_month_statement_context(
                                parse_pdf_set(sorted(all_pdfs, key=lambda p: p.name), password=[redacted], enable_ocr=("--disable-ocr" not in fx_args))
                            )
                            _, _, stmts2, _ = split_account_and_year(stmts_all2, tax_year=tax_year)
                            sec_info2 = _securities_needing_prior_data(stmts2)
                            if not sec_info2.get("needs_prior"):
                                print(f"  ✓ 所有标的已可追溯 FIFO 成本！")
                            else:
                                still_missing = set(sec_info2["needs_prior"])
                                print(f"  补充后仍有 {len(still_missing)} 个标的缺少买入记录")
                            input_dir = input_dir + ";" + extra_dirs
                        except Exception as e:
                            print(f"  重新扫描异常: {e}")

                # 询问首次买入月份或开户日期
                buy_months = input("\n  你知道首次买入月份吗？（格式 标的=年月，多个以逗号分隔，回车跳过）:\n  > ").strip()
                if buy_months:
                    print(f"  已记录。")
                open_month = input("  或者输入开户年月推算（如 202303，回车跳过）:\n  > ").strip()
                if open_month:
                    print(f"  需补充 {open_month} 起月结单。")
            else:
                print("  ✓ 所有标的均有买入记录，无需历史数据。")

            print()
            confirm = input(f"  确认选择 {label}？按 Enter 确认，输入 back 重新选择:\n  > ").strip().lower()
            if confirm == "back":
                print("  已取消，请重新选择。")
                continue
            fx_args.append(f"--cost-basis-method={cbm_raw}")
            break
        else:
            print(f"  无效输入 '{cbm_raw}'，请输入 MA / FIFO / BOTH 或直接回车")

    print()
    print("正在处理，请稍候...")
    print()

    collected = {
        "input_dir": input_dir,
        "output_dir": output_dir,
        "tax_year": tax_year,
        "password": pwd if pwd else None,
    }
    return collected, fx_args


def _run(args: argparse.Namespace) -> int:
    """统一的运行入口。"""
    try:
        result = run_workpaper(
            args.input_dir,
            args.output_dir,
            password=os.environ.get("LONGBRIDGE_PDF_PASSWORD"),
            tax_year=args.tax_year,
            account_id=args.account_id,
            fx_rates=_fx(args.fx),
            fx_metadata=_fx_metadata(args),
            policy_path=args.policy,
            profile_path=args.profile,
            jurisdiction_path=args.jurisdiction,
            symbol_mapping_path=args.symbol_map,
            enable_ocr=args.enable_ocr,
            include_source_pdfs=args.include_source_pdfs,
            cost_basis_method=args.cost_basis_method,
            withholding_credit=args.withholding_credit,
            deduct_margin_interest=args.deduct_margin_interest,
        )
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "tax_year": result.tax_year,
                "account_id": result.account_id,
                "workbook": str(result.workbook),
                "workpapers_zip": str(result.workpapers_zip),
                "processed_delivery_zip": str(result.processed_delivery_zip),
                "review_status": str(result.review_status),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()

    # 无参数 → 交互式引导
    if argv is None:
        argv = sys.argv[1:] if len(sys.argv) > 1 else []

    if not argv:
        collected, extra_args = _interactive_prompt()
        # 在空args基础上覆盖交互值，再解析额外参数
        args = parser.parse_args([])
        args.input_dir = collected["input_dir"]
        args.output_dir = collected["output_dir"]
        args.tax_year = collected["tax_year"]
        if extra_args:
            extra = parser.parse_args(extra_args)
            if extra.fx:
                args.fx = extra.fx
            if extra.fx_source:
                args.fx_source = extra.fx_source
            if extra.enable_ocr is not None:
                args.enable_ocr = extra.enable_ocr
        return _run(args)

    args = parser.parse_args(argv)
    if not args.input_dir:
        parser.error("缺少包含月结单PDF的目录")

    return _run(args)
