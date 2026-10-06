"""Surface Extrapolation Assistant — CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from surface_assistant import __version__
from surface_assistant.step_io import load_step
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
def inspect(step_file: Path = typer.Argument(..., help="Surface STEP file")) -> None:
    """Load a surface and report boundary faces."""
    from surface_assistant.topology import get_boundary_faces_no_curve

    console.rule("[bold]Load surface")
    _, shape = load_step(step_file)
    console.print(f"Faces: {len(shape.Faces)}  Edges: {len(shape.Edges)}")

    faces = get_boundary_faces_no_curve(shape)
    console.print(f"Boundary faces: {len(faces)}")

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Face #", justify="right")
    table.add_column("Type")
    table.add_column("Area (mm²)", justify="right")

    for bf in faces:
        fp = bf.fingerprint
        table.add_row(str(bf.index), fp.surface_type, f"{fp.area:.3f}")

    console.print(table)


@app.command(name="run")
def run(
    folder: Path = typer.Argument(
        ..., help="Folder containing exactly one STEP file"
    ),
    distance: float = typer.Option(None, "--distance", "-d"),
    tolerance: float = typer.Option(None, "--tolerance", "-t"),
    strategy: str = typer.Option(
        "per_face",
        "--strategy",
        help="Extension strategy: 'per_face' (default) or 'whole_surface'",
    ),
    llm: bool = typer.Option(False, "--ai"),
    ai_verbose: bool = typer.Option(False, "--ai-verbose"),
) -> None:
    """Run the pipeline on a folder containing exactly one STEP file.

    The result is written next to the input STEP
    file as <folder>/<strategy>.FCStd, with a companion <strategy>_grid.png.
    """
    from surface_assistant.step_io import resolve_inputs
    from surface_assistant.batch import run_batch

    surface_path = resolve_inputs(folder)
    console.print(f"[cyan]Surface :[/cyan]  {surface_path.name}")

    cfg = load_config()
    target_mm = distance if distance is not None else cfg.extrapolation.target_distance_mm
    tol_percent = tolerance if tolerance is not None else cfg.extrapolation.tolerance_percent

    output = folder / f"{strategy}.FCStd"

    console.rule("[bold]Batch extrapolation")
    report = run_batch(
        step_file=surface_path,
        target_mm=target_mm,
        tolerance_percent=tol_percent,
        max_correction_passes=cfg.extrapolation.max_correction_passes,
        use_llm=llm,
        output_fcstd=output,
        strategy=strategy,
        ai_verbose=ai_verbose,
    )
    console.print(report.summary())

    grid_path = output.with_name(output.stem + "_grid.png")
    console.print(f"[green]Saved to[/green] {output}")
    if grid_path.exists():
        console.print(f"[green]Grid screenshot[/green] {grid_path}")

def main() -> None:
    try:
        app()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()