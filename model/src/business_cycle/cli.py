"""데이터 수집·현재 판정·백테스트·오프라인 데모 CLI."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from .backtest.engine import run_backtest
from .config import Settings, default_root, load_settings
from .data.fred import FredCollector
from .data.local import load_local
from .pipeline import run_pipeline
from .reporting.writers import write_reports
from .synthetic import generate_synthetic_observations
from .validation import run_phase2_validation, run_real_data_validation


def _core_ids(settings: Settings) -> list[str]:
    return list(settings.indicators["indicators"].keys())


def _load(args: argparse.Namespace, settings: Settings) -> pd.DataFrame:
    if args.data_source == "local":
        return load_local(Path(args.data_dir))
    if args.data_source == "fred":
        frame, warnings = FredCollector(Path(args.cache_dir)).fetch(_core_ids(settings), args.start)
        for warning in warnings:
            print(f"경고: {warning}", file=sys.stderr)
        return frame
    raise ValueError(f"지원하지 않는 데이터 소스: {args.data_source}")


def command_fetch(args: argparse.Namespace) -> int:
    settings = load_settings()
    collector = FredCollector(Path(args.cache_dir))
    frame, warnings = collector.fetch(_core_ids(settings), args.start)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    for warning in warnings:
        print(f"경고: {warning}", file=sys.stderr)
    print(f"{len(frame):,}개 관측 저장: {output}")
    return 0


def command_nowcast(args: argparse.Namespace) -> int:
    settings = load_settings()
    frame = _load(args, settings)
    run = run_pipeline(frame, settings, args.as_of)
    paths = write_reports(run, Path(args.output_dir), "nowcast")
    print(json.dumps(run.result.to_dict(), ensure_ascii=False, indent=2))
    print("출력: " + ", ".join(str(path) for path in paths.values()), file=sys.stderr)
    return 0


def command_backtest(args: argparse.Namespace) -> int:
    settings = load_settings()
    frame = _load(args, settings)
    result = run_backtest(frame, settings, args.start, args.end, args.walk_forward)
    output = Path(args.output_dir)
    write_reports(result.run, output, "backtest-latest")
    (output / "backtest-history.csv").write_text(
        result.history.reset_index(names="date").to_csv(index=False), encoding="utf-8", newline="\n"
    )
    (output / "backtest-metrics.json").write_text(
        json.dumps(
            {"metrics": result.metrics, "metadata": result.metadata}, ensure_ascii=False, indent=2
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(result.metrics, ensure_ascii=False, indent=2))
    return 0


def command_demo(args: argparse.Namespace) -> int:
    settings = load_settings()
    frame = generate_synthetic_observations(
        args.start, args.end, int(settings.model["random_seed"])
    )
    run = run_pipeline(frame, settings, args.end)
    paths = write_reports(run, Path(args.output_dir), "demo-nowcast")
    backtest = run_backtest(frame, settings, args.backtest_start, args.end, True)
    metrics_path = Path(args.output_dir) / "demo-backtest-metrics.json"
    metrics_path.write_text(
        json.dumps(
            {"metrics": backtest.metrics, "metadata": backtest.metadata},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(run.result.to_dict(), ensure_ascii=False, indent=2))
    print(
        "출력: " + ", ".join(str(path) for path in [*paths.values(), metrics_path]), file=sys.stderr
    )
    return 0


def command_validate_real(args: argparse.Namespace) -> int:
    settings = load_settings()
    result = run_real_data_validation(
        settings,
        args.start,
        args.end,
        Path(args.cache_dir),
        Path(args.output_dir),
    )
    print(f"실자료 검증 보고서: {result.report_path}")
    print(f"차트 {len(result.chart_paths)}개 생성")
    print(f"8주 모멘텀 조정 채택: {result.adopted_adjustment}")
    return 0


def command_validate_phase2(args: argparse.Namespace) -> int:
    settings = load_settings()
    result = run_phase2_validation(
        settings,
        args.start,
        args.end,
        Path(args.cache_dir),
        Path(args.output_dir),
    )
    print(f"2차 실자료 보정 보고서: {result.report_path}")
    print(f"차트 {len(result.chart_paths)}개 생성")
    print(f"최종 채택 모델: {result.final_model}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """CLI 파서를 생성한다."""

    root = default_root()
    parser = argparse.ArgumentParser(prog="business-cycle")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="FRED 최신 수정치 수집")
    fetch.add_argument("--start", default="1960-01-01")
    fetch.add_argument("--cache-dir", default=str(root / "data/cache"))
    fetch.add_argument("--output", default=str(root / "data/processed/observations.csv"))
    fetch.set_defaults(handler=command_fetch)

    nowcast = subparsers.add_parser("nowcast", help="현재 경기국면 판정")
    nowcast.add_argument("--as-of", default=date.today().isoformat())
    nowcast.add_argument("--data-source", choices=["local", "fred"], default="local")
    nowcast.add_argument("--data-dir", default=str(root / "data/processed"))
    nowcast.add_argument("--cache-dir", default=str(root / "data/cache"))
    nowcast.add_argument("--start", default="1960-01-01")
    nowcast.add_argument("--output-dir", default=str(root / "outputs"))
    nowcast.set_defaults(handler=command_nowcast)

    backtest = subparsers.add_parser("backtest", help="인과적 walk-forward 백테스트")
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.add_argument("--walk-forward", action="store_true", required=True)
    backtest.add_argument("--data-source", choices=["local", "fred"], default="local")
    backtest.add_argument("--data-dir", default=str(root / "data/processed"))
    backtest.add_argument("--cache-dir", default=str(root / "data/cache"))
    backtest.add_argument("--output-dir", default=str(root / "outputs"))
    backtest.set_defaults(handler=command_backtest)

    demo = subparsers.add_parser("demo", help="합성 데이터 오프라인 데모")
    demo.add_argument("--start", default="1985-01-01")
    demo.add_argument("--backtest-start", default="1995-01-01")
    demo.add_argument("--end", default="2026-08-14")
    demo.add_argument("--output-dir", default=str(root / "outputs/demo"))
    demo.set_defaults(handler=command_demo)

    validate = subparsers.add_parser("validate-real", help="공식 FRED 실자료 검증")
    validate.add_argument("--start", default="1995-01-01")
    validate.add_argument("--end", default="2026-08-14")
    validate.add_argument("--cache-dir", default=str(root / "data/cache"))
    validate.add_argument("--output-dir", default=str(root / "outputs/real_data_validation"))
    validate.set_defaults(handler=command_validate_real)

    phase2 = subparsers.add_parser("validate-phase2", help="FRED 실자료 2차 보정 검증")
    phase2.add_argument("--start", default="1995-01-01")
    phase2.add_argument("--end", default="2026-08-14")
    phase2.add_argument("--cache-dir", default=str(root / "data/cache"))
    phase2.add_argument("--output-dir", default=str(root / "outputs/real_data_validation/phase2"))
    phase2.set_defaults(handler=command_validate_phase2)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점. 실패를 조용히 삼키지 않고 오류 원인을 출력한다."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as exc:  # CLI 경계에서는 오류 종류와 메시지를 모두 보존한다.
        print(f"오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
