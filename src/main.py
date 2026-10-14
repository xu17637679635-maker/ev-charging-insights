"""One-command entry point for the whole pipeline.

    python -m src.main

Runs, in order:

1. **generate** -- simulate the charging sessions and write ``data/raw/*.csv``
2. **analyze**  -- run the six analyses and print the report
3. **plots**    -- render ``figures/*.png``

Each stage is also runnable on its own (``python -m src.generate`` etc.), which
is what you want while iterating on one of them. This module exists so that a
reader -- or an interviewer -- can reproduce everything from a clean checkout
with a single command.

The stages are deliberately *not* clever about caching. Regenerating is cheap at
the default size (a few seconds) and a stale intermediate that silently feeds
the next stage is a far worse failure than a redundant recomputation.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import analyze, generate, plots


def _header(step: int, total: int, title: str) -> None:
    print()
    print("=" * 74)
    print(f"  [{step}/{total}] {title}")
    print("=" * 74)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate, analyse and chart EV charging behaviour",
    )
    parser.add_argument("--days", type=int, default=generate.DAYS,
                        help="recorded days to simulate")
    parser.add_argument("--vehicles", type=int, default=generate.N_VEHICLES)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--data", type=Path, default=Path("data/raw"))
    parser.add_argument("--figures", type=Path, default=Path("figures"))
    parser.add_argument("--skip-generate", action="store_true",
                        help="reuse existing CSVs (faster while iterating on charts)")
    parser.add_argument("--dpi", type=int, default=110)
    args = parser.parse_args(argv)

    t0 = time.perf_counter()
    total = 2 if args.skip_generate else 3
    step = 0

    if not args.skip_generate:
        step += 1
        _header(step, total, "1. 生成充电数据")
        counts = generate.generate(args.data, seed=args.seed,
                                   vehicles=args.vehicles, days=args.days)
        print(f"  写入 {counts['sessions']:,} 条充电记录 / "
              f"{counts['stations']} 个充电桩 / {counts['daily']} 天")
        print(f"  输出目录: {args.data}")

    step += 1
    _header(step, total, f"{step}. 分析")
    findings, tables = analyze.run_all(args.data)
    # Route through the same UTF-8 setup the analyze CLI uses, so this works on
    # a Chinese Windows console too.
    analyze._use_utf8_stdout()
    print(analyze.format_report(findings, tables["sessions"]))

    step += 1
    _header(step, total, f"{step}. 生成图表")
    paths = plots.generate_all(args.data, args.figures, args.dpi)
    for p in paths:
        print(f"  {p}  ({p.stat().st_size / 1024:.0f} KB)")

    elapsed = time.perf_counter() - t0
    print()
    print("=" * 74)
    print(f"  完成，用时 {elapsed:.1f} 秒")
    print("=" * 74)
    print()
    print("提醒：数据由行为模型生成，不是真实世界测量值。")
    print("      生成器只编码了通勤规律、电池物理和用户习惯；")
    print("      「傍晚高峰」「快充瓶颈是电池」等结论是这些假设的推论，")
    print("      不是对真实城市的观测。详见 README 的「局限性」一节。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
