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


@app.command()
def run(
    step_file: Path = typer.Argument(..., help="Surface STEP file"),
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
        False, "--ai",
        help="Use local AI for failure diagnostics",
    ),
    llm_verbose: bool = typer.Option(
        False, "--ai-verbose",
        help="Print the raw AI response",
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
        target_mm=target_mm,
        tolerance_percent=tol_percent,
        max_correction_passes=cfg.extrapolation.max_correction_passes,
        use_llm=llm,
        output_fcstd=output,
    )
    console.print(report.summary())

    if output is not None:
        console.print(f"[green]Saved to[/green] {output}")

@app.command()
def run_folder(
    folder: Path = typer.Argument(..., help="Folder containing surface.stp file"),
    distance: float = typer.Option(None, "--distance", "-d"),
    tolerance: float = typer.Option(None, "--tolerance", "-t"),
    output: Path = typer.Option(None, "--output", "-o"),
    llm: bool = typer.Option(False, "--ai"),
    screenshots: bool = typer.Option(
        False, "--screenshots", help="Save PNG screenshots of the output document"
    ),
    views: str = typer.Option(
        "iso",
        "--views",
        help="Comma-separated screenshot views: iso,front,top,left",
    ),
) -> None:
    """Run the pipeline on a folder. Auto-detects surface vs boundary."""
    from surface_assistant.step_io import resolve_inputs
    from surface_assistant.batch import run_batch
    from surface_assistant.io import save_extended_faces

    surface_path = resolve_inputs(folder)
    console.print(f"[cyan]Surface :[/cyan]  {surface_path.name}")

    cfg = load_config()
    target_mm = distance if distance is not None else cfg.extrapolation.target_distance_mm
    tol_percent = tolerance if tolerance is not None else cfg.extrapolation.tolerance_percent

    console.rule("[bold]Batch extrapolation")
    report = run_batch(
        step_file=surface_path,
        target_mm=target_mm,
        tolerance_percent=tol_percent,
        max_correction_passes=cfg.extrapolation.max_correction_passes,
        use_llm=llm,
        output_fcstd=output
    )
    console.print(report.summary())

    if output is not None:
        console.print(f"[green]Saved to[/green] {output}")

    if screenshots:
        if output is None:
            raise typer.BadParameter("--screenshots requires an output FCStd path via --output")
        requested_views = tuple(view.strip().lower() for view in views.split(",") if view.strip())
        from surface_assistant.io import render_fcstd_to_png_subprocess

        png_path = output.with_suffix(".png")
        if not render_fcstd_to_png_subprocess(output, png_path, views=requested_views):
            raise RuntimeError(f"Failed to create screenshots from {output}")
        if len(requested_views) == 1:
            console.print(f"[green]Screenshot saved to[/green] {png_path}")
        else:
            console.print(
                f"[green]Screenshots saved with base name[/green] "
                f"{png_path.with_suffix('')}_<view>.png"
            )

def main() -> None:
    try:
        app()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()