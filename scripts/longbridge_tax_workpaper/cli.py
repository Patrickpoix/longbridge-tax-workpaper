from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from typing import Any

from . import __version__
from .runner import run_workpaper


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


def _fx(values: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for currency, raw_rate in _key_value_pairs(values, label="汇率").items():
        try:
            rate = Decimal(raw_rate)
        except InvalidOperation as exc:
            raise argparse.ArgumentTypeError(f"无效汇率: {currency}={raw_rate}") from exc
        if rate <= 0:
            raise argparse.ArgumentTypeError(f"汇率必须大于0: {currency}={raw_rate}")
        result[currency] = float(rate)
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
    parser.add_argument("input_dir", nargs="?", help="包含月结单PDF的目录")
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
        help="年末人民币中间价，例如 --fx USD=7.0288 --fx HKD=0.90322",
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
        help="可选汇率来源日期，例如 --fx-source-date USD=2025-12-31",
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
    return parser


def _interactive_prompt() -> tuple[dict[str, Any], list[str]]:
    """交互式引导：当无命令行参数时，逐项询问用户输入。"""
    print("=" * 54)
    print("  长桥证券税务工作底稿 — 交互式模式")
    print("=" * 54)
    print()

    # 1. 输入目录
    raw = input("请输入月结单目录路径（可直接拖入文件夹）:\n> ").strip().strip('"').strip("'")
    if not raw:
        print("错误：必须指定月结单目录", file=sys.stderr)
        sys.exit(1)
    input_dir = raw

    # 2. 密码
    pwd = input("\nPDF密码（未加密则直接回车）:\n> ")
    if pwd:
        os.environ["LONGBRIDGE_PDF_PASSWORD"] = pwd

    # 3. 纳税年度
    year_raw = input("\n纳税年度（例如 2025，回车自动检测完整年度）:\n> ").strip()
    tax_year = int(year_raw) if year_raw else None

    # 4. 输出目录
    out_raw = input("\n输出目录（默认 outputs）:\n> ").strip().strip('"').strip("'")
    output_dir = out_raw or "outputs"

    # 5. 汇率
    fx_args: list[str] = []
    usd = input("\nUSD/CNY 年末汇率（例如 7.0288，回车跳过）:\n> ").strip()
    if usd:
        fx_args.append("--fx=USD=" + usd)
    hkd = input("\nHKD/CNY 年末汇率（例如 0.90322，回车跳过）:\n> ").strip()
    if hkd:
        fx_args.append("--fx=HKD=" + hkd)

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
