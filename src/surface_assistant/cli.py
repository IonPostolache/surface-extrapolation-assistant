"""Surface Extrapolation Assistant — CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from surface_assistant import __version__
from surface_assistant.step_io import load_step, load_boundary
from surface_assistant.topology import get_boundary_faces
from surface_assistant.config import load_config

app = typer.Typer(
    add_completion=False,
    help="Surface Extrapolation Assistant",
)
console = Console()


@app.command()
def version() -> None:
    """Print version."""
    console.print(f"surface-extrapolation-assistant v{__version__}")


@app.command()
def inspect(
    step_file: Path = typer.Argument(..., help="Surface STEP file"),
    boundary_file: Path = typer.Option(
        None, "--boundary", "-b", help="Boundary curve STEP file"
    ),
) -> None:
    """Load a surface, optionally a boundary, and report boundary faces."""
    console.rule("[bold]Load surface")
    doc, shape = load_step(step_file)
    console.print(f"Faces: {len(shape.Faces)}  Edges: {len(shape.Edges)}")

    if boundary_file is None:
        console.print("[yellow]No boundary provided — skipping boundary detection.[/yellow]")
        return

    console.rule("[bold]Load boundary")
    _, boundary = load_boundary(boundary_file)
    console.print(f"Boundary edges: {len(boundary.Edges)}")

    console.rule("[bold]Boundary faces")
    faces = get_boundary_faces(shape, boundary)

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Face #", justify="right")
    table.add_column("Type")
    table.add_column("Area (mm²)", justify="right")
    table.add_column("Center of mass")
    table.add_column("Shared edges", justify="right")

    for bf in faces:
        fp = bf.fingerprint
        table.add_row(
            str(bf.index),
            fp.surface_type,
            f"{fp.area:.3f}",
            f"({fp.com[0]}, {fp.com[1]}, {fp.com[2]})",
            str(bf.shared_edge_count),
        )

    console.print(table)


@app.command()
def run(
    step_file: Path = typer.Argument(..., help="Surface STEP file"),
    boundary_file: Path = typer.Option(
        ..., "--boundary", "-b", help="Boundary curve STEP file"
    ),
    distance: float = typer.Option(
        None, "--distance", "-d",
        help="Target extrapolation distance in mm (default: config.yaml)",
    ),
    tolerance: float = typer.Option(
        None, "--tolerance", "-t",
        help="Tolerance as a percentage (default: config.yaml)",
    ),
    output: Path = typer.Option(
        None, "--output", "-o",
        help="Save extended faces to this .FCStd file",
    ),
    llm: bool = typer.Option(
        False, "--llm",
        help="Use local LLM for failure diagnostics",
    ),
    llm_verbose: bool = typer.Option(
        False, "--llm-verbose",
        help="Print the raw LLM response",
    ),
) -> None:
    """Batch-extrapolate all boundary faces of a surface."""
    from surface_assistant.batch import run_batch
    from surface_assistant.io import save_extended_faces

    cfg = load_config()

    # Resolve: CLI value > config value
    target_mm = distance if distance is not None else cfg.extrapolation.target_distance_mm
    tol_percent = tolerance if tolerance is not None else cfg.extrapolation.tolerance_percent

    console.rule("[bold]Batch extrapolation")
    console.print(f"  Target distance : {target_mm} mm (from "
                  f"{'CLI' if distance is not None else 'config.yaml'})")
    console.print(f"  Tolerance       : {tol_percent}% (from "
                  f"{'CLI' if tolerance is not None else 'config.yaml'})")

    report = run_batch(
        step_file=step_file,
        boundary_file=boundary_file,
        target_mm=target_mm,
        tolerance_percent=tol_percent,
        max_correction_passes=cfg.extrapolation.max_correction_passes,
        use_llm=llm,
    )
    console.print(report.summary())

    if output is not None:
        saved = save_extended_faces(report, output)
        console.print(f"[green]Saved to[/green] {saved}")

def main() -> None:
    try:
        app()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()