"""Command-line interface (PRD §4.7).

    sam-ingest ingest <use_case> [--refresh] [--limit N] [--dry-run]
    sam-ingest ingest all
    sam-ingest validate        # schema-check existing output
    sam-ingest stats           # print manifest + richness summary
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from .. import PIPELINE_VERSION
from ..core.cache import DiskCache
from ..core.http import PoliteClient
from ..core.schema import KnowledgeBlock, SeedConfig, UseCase, utcnow_iso
from ..core.sink import JsonlMarkdownSink
from ..core.state import RunState

log = logging.getLogger("sam_ingest")


def _paths(root: Path) -> dict:
    out = root / "out"
    return {
        "root": root,
        "config": root / "config",
        "out": out,
        "cache": out / ".cache",
        "state": out / "state.json",
    }


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _load_seed(config_dir: Path, use_case: str) -> SeedConfig:
    path = config_dir / f"{use_case}.yaml"
    raw: dict = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        log.warning("no config file %s — using empty seed", path)
    return SeedConfig(use_case=use_case, raw=raw)


def _run_use_case(use_case: str, p: dict, args, sink: JsonlMarkdownSink, run_ts: str) -> int:
    # Imported here so registration side effects happen after logging is configured.
    from ..extractors.base import get_extractor

    extractor = get_extractor(use_case)
    if extractor is None:
        log.warning("use case %r has no registered extractor yet — skipping", use_case)
        return 0

    seed = _load_seed(p["config"], use_case)
    cache = DiskCache(p["cache"])
    state = RunState(p["state"])
    with PoliteClient(cache) as client:
        from ..extractors.base import RunContext

        ctx = RunContext(
            client=client,
            seed=seed,
            state=state,
            run_ts=run_ts,
            config_dir=p["config"],
            limit=args.limit,
            refresh=args.refresh,
            dry_run=args.dry_run,
        )
        blocks = extractor.run(ctx)

    log.info("use_case=%s produced %d blocks", use_case, len(blocks))
    if args.dry_run:
        log.info("dry-run: not writing output or state")
        return len(blocks)
    sink.write(use_case, blocks)
    state.save()
    return len(blocks)


def cmd_ingest(args) -> int:
    p = _paths(Path(args.root).resolve())
    run_ts = utcnow_iso()
    sink = JsonlMarkdownSink(p["out"])

    if args.use_case == "all":
        targets = [uc.value for uc in UseCase]
    else:
        targets = [args.use_case]

    total = 0
    for uc in targets:
        total += _run_use_case(uc, p, args, sink, run_ts)

    if not args.dry_run:
        manifest = sink.finalize(run_ts, PIPELINE_VERSION)
        log.info("wrote manifest: %d total blocks across %d use cases",
                 manifest["total_blocks"], len(manifest["use_cases"]))
    print(f"Done. {total} blocks.")
    return 0


def cmd_validate(args) -> int:
    p = _paths(Path(args.root).resolve())
    blocks_dir = p["out"] / "blocks"
    if not blocks_dir.exists():
        print("No output to validate (out/blocks missing).")
        return 1
    today = utcnow_iso()[:10]  # ISO date; volatile blocks past this valid_until are stale
    errors = stale = total = 0
    for jsonl in sorted(blocks_dir.glob("*.jsonl")):
        for i, line in enumerate(jsonl.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            total += 1
            try:
                block = KnowledgeBlock.model_validate_json(line)
            except ValidationError as e:
                errors += 1
                print(f"{jsonl.name}:{i} INVALID: {e.error_count()} error(s)")
                continue
            if args.stale and block.valid_until and block.valid_until < today:
                stale += 1
                print(f"{jsonl.name}:{i} STALE: {block.id} valid_until={block.valid_until}")
    msg = f"Validated {total} blocks, {errors} invalid"
    if args.stale:
        msg += f", {stale} stale"
    print(msg + ".")
    return 1 if errors else 0


def cmd_stats(args) -> int:
    p = _paths(Path(args.root).resolve())
    manifest_path = p["out"] / "manifest.json"
    if not manifest_path.exists():
        print("No manifest.json — run `ingest` first.")
        return 1
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"Pipeline {m['pipeline_version']} — run {m['run_timestamp']}")
    print(f"Total blocks: {m['total_blocks']}\n")
    print(f"{'use case':<22} {'blocks':>6}  {'avg chars':>9}  sources")
    print("-" * 70)
    for uc, r in sorted(m["use_cases"].items()):
        sources = ", ".join(f"{k}={v}" for k, v in r["by_source"].items())
        print(f"{uc:<22} {r['block_count']:>6}  {r['avg_body_chars']:>9}  {sources}")
        aud = r["by_audience"]
        print(f"{'':<22} audience: patient={aud['patient']} "
              f"caregiver={aud['caregiver']} general={aud['general']}")
        if "dailymed_nature" in r:
            n = r["dailymed_nature"]
            print(f"{'':<22} dailymed: patient_facing={n['patient_facing']} "
                  f"clinical={n['clinical']}")
        if "by_volatility" in r:
            vol = ", ".join(f"{k}={v}" for k, v in r["by_volatility"].items())
            print(f"{'':<22} volatility: {vol}; countries={r['geo_country_count']}")
            if r["by_trip_type"]:
                tt = ", ".join(f"{k}={v}" for k, v in r["by_trip_type"].items())
                print(f"{'':<22} trip_types: {tt}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sam-ingest", description=__doc__)
    parser.add_argument(
        "--root",
        default=os.environ.get("SAM_INGEST_ROOT", "."),
        help="project root containing config/ and out/ (default: cwd)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("ingest", help="run an extractor")
    pi.add_argument("use_case", help="a use case, or 'all'")
    pi.add_argument("--refresh", action="store_true", help="bypass cache read")
    pi.add_argument("--limit", type=int, default=None, help="cap items per source")
    pi.add_argument("--dry-run", action="store_true", help="don't write output/state")
    pi.set_defaults(func=cmd_ingest)

    pv = sub.add_parser("validate", help="schema-check existing output")
    pv.add_argument("--stale", action="store_true",
                    help="also flag volatile blocks past their valid_until")
    pv.set_defaults(func=cmd_validate)

    ps = sub.add_parser("stats", help="print manifest summary")
    ps.set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
