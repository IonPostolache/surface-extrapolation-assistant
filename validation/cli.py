"""Command-line interface for OfflineDraw2CAD."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cad.export import export_step
from .cad.generator import generate_part
from .review.report import generate_report
from .vision.vlm_reader import VLMConfig, VLMReader


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="offlinedraw2cad",
        description=(
            "Reconstruct a dimensioned 2D engineering drawing into "
            "a provisional parametric CAD model using a local VLM."
        ),
    )

    parser.add_argument(
        "drawing",
        type=Path,
        help="Path to the input engineering drawing image.",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("output"),
        help="Output directory. Default: ./output",
    )

    parser.add_argument(
        "--model",
        help="Override the VLM model from .env.",
    )

    parser.add_argument(
        "--base-url",
        help="Override the LM Studio/OpenAI-compatible API URL.",
    )

    parser.add_argument(
        "--no-preprocess",
        action="store_true",
        help="Send the original image without preprocessing.",
    )

    parser.add_argument(
        "--no-step",
        action="store_true",
        help="Only run the VLM and save PartSpec; do not generate STEP.",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the resulting PartSpec JSON to stdout.",
    )

    return parser


def main() -> int:
    """Run the OfflineDraw2CAD pipeline."""
    parser = build_parser()
    args = parser.parse_args()

    drawing = args.drawing

    if not drawing.is_file():
        parser.error(f"Drawing not found: {drawing}")

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ---------------------------------------------------------------
        # 1. Configure VLM
        # ---------------------------------------------------------------
        config = VLMConfig.from_env()

        if args.model:
            config = VLMConfig(
                base_url=config.base_url,
                model=args.model,
                api_key=config.api_key,
                timeout_seconds=config.timeout_seconds,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            )

        if args.base_url:
            config = VLMConfig(
                base_url=args.base_url,
                model=config.model,
                api_key=config.api_key,
                timeout_seconds=config.timeout_seconds,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
            )

        reader = VLMReader(config)

        # ---------------------------------------------------------------
        # 2. Read drawing with local VLM
        # ---------------------------------------------------------------
        print(f"Reading drawing: {drawing}")
        print(f"VLM model: {config.model}")
        print(f"VLM endpoint: {config.base_url}")

        part_spec, warnings = reader.read(
            drawing,
            preprocess=not args.no_preprocess,
        )

        # ---------------------------------------------------------------
        # 3. Save PartSpec
        # ---------------------------------------------------------------
        stem = drawing.stem

        partspec_path = output_dir / f"{stem}_partspec.json"

        partspec_path.write_text(
            part_spec.model_dump_json(indent=2),
            encoding="utf-8",
        )

        print(f"PartSpec: {partspec_path}")

        # ---------------------------------------------------------------
        # 4. Save preprocessing warnings
        # ---------------------------------------------------------------
        warnings_path = output_dir / f"{stem}_warnings.json"

        warnings_path.write_text(
            json.dumps(
                warnings,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        if warnings:
            print(f"Preprocessing warnings: {len(warnings)}")
            for warning in warnings:
                print(f"  - {warning}")

        # ---------------------------------------------------------------
        # 5. Generate review report
        # ---------------------------------------------------------------
        report_path = output_dir / f"{stem}_review.md"

        report = generate_report(part_spec)

        report_path.write_text(
            report,
            encoding="utf-8",
        )

        print(f"Review report: {report_path}")

        # ---------------------------------------------------------------
        # 6. Generate CAD
        # ---------------------------------------------------------------
        if not args.no_step:
            print("Generating CAD...")

            result = generate_part(part_spec)

            step_path = output_dir / f"{stem}.step"

            export_step(
                result.shape,
                step_path,
            )

            print(f"STEP: {step_path}")

        # ---------------------------------------------------------------
        # 7. Optional JSON output
        # ---------------------------------------------------------------
        if args.json:
            print()
            print(part_spec.model_dump_json(indent=2))

        print()
        print("OfflineDraw2CAD completed successfully.")

        return 0

    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

    # except Exception as exc:
    except Exception as exc:  # noqa: BLE001
        print(
            f"Error: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())