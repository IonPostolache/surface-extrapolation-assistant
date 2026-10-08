# Surface Extrapolation Assistant for FreeCAD

Automated boundary-face extension for stamping die-face preparation, built on
FreeCAD's Python API, with a local LLM used only for diagnosing and
suggesting recovery when the deterministic geometry pipeline fails.

## The problem

Extending a complex surface (e.g. a sheet-metal car part) along its outer boundary, 
in a tool like CATIA V5, usually won't work in one go. So it typically means: 
extract each boundary face → untrim → extrapolate in curvature → split
against neighbor contours → join → repeat, per face. On a model with many
faces this is slow, and the failure cases are the most time-consuming part.

FreeCAD exposes `Surface::Extend`, but it operates on the face's underlying
mathematical surface — which can un-trim BSpline faces, revealing geometry
that was trimmed away in the source data. There is no built-in batch
traversal, failure handling, or recovery logic.

## What this does

1. Imports a STEP surface and identifies all faces touching the outer
   boundary (topologically detected — holes are ignored; only the
   outermost loop is extended).
2. Extends each boundary face by a target distance in millimetres, using
   one of three strategies depending on surface type (below).
3. Trims each extension back to the surface's outer boundary, keeping only
   the outside band. Where the trim can't be done reliably, the face is
   left unchanged and flagged `DEFERRED` rather than guessed at.
4. Joins the result into a compound shell and reports open edges.
5. On failure or a deferred face, builds a structured diagnostic — plus a
   rendered 6-view image — and (optionally) sends it to a **local** LLM
   (LM Studio or Ollama), which returns a JSON diagnosis and a recommended
   action from a fixed allow-list.

The geometry pipeline is fully deterministic and runs with no LLM present.
The LLM is consulted only on failure, and it never generates or executes
arbitrary code — it selects from a small set of pre-implemented recovery
actions, which are validated before anything is applied.

## Extension strategies (by surface type)

| Surface type       | Strategy                                      | Notes |
|---------------------|-----------------------------------------------|-------|
| Plane               | Ruled strip, offset boundary edges             | Flat strip is geometrically correct on a flat face. |
| BSpline / Cylinder  | Curvature-following ribbon                     | Samples the edge, offsets outward in the surface's tangent plane, lofts a ribbon. Closest approximation to CATIA's "extrapolate in curvature" that FreeCAD's public API allows. |
| Other               | Parametric `Surface::Extend`, ratio-calibrated | Fallback only. |


## Pipeline

```text
identify boundary faces  →  extend per-face (strategy above)
    →  trim back to outer boundary (or DEFERRED, if trim has no effect)
        →  join into a shell, report open edges
            →  on failure/DEFERRED: optional local-LLM diagnosis
```

See [`docs/design_log.md`](docs/design_log.md) for the module-level map and
the reasoning behind each stage.


## What the LLM does — and does not do

| Task | Component |
|---|---|
| Import STEP, detect boundary, build extensions | FreeCAD / Python |
| Trim, join, report open edges | FreeCAD + Python |
| Detect failures, build structured diagnostics | Python |
| Diagnose *why* a face failed | Local LLM |
| Propose a recovery action (from an allow-list) | Local LLM |
| Validate and execute the recovery | Python / FreeCAD |
| Generate arbitrary CAD code | **Never — not a supported path** |

## The one limitation worth knowing up front

**This does not reproduce CATIA's exact numerical output.** The ribbon
strategy is a tangent-plane approximation of curvature continuation, and
large faces that wrap most of the part can fail to trim reliably — these
are left unchanged and reported as `DEFERRED` rather than silently producing
wrong geometry. See [`docs/design_log.md`](docs/design_log.md) for why, and
what a closer-to-CATIA implementation would need.

## Usage

Setup (FreeCAD install, venv, library path, optional local LLM) is in
[`docs/setup.md`](docs/setup.md). Once installed:

```bash
# Inspect a surface without extending
surface-assistant inspect examples/0-flange/0-surface.stp

# Run the pipeline — folder must contain exactly one STEP file
surface-assistant run examples/0-flange --strategy per_face --ai
surface-assistant run examples/0-flange --strategy whole_surface --ai
```

The --ai flag is optional and requires a local LLM (LM Studio or Ollama) to be running. Without it, the pipeline runs fully deterministically and still reports deferred faces in the console.

`per_face` (default) extends each boundary face independently — clean results on simple boundaries. 
`whole_surface` builds one ribbon per outer-boundary edge instead, which handles concave or multi-loop boundaries better at the cost of coarser corners. Produces `<strategy>.FCStd` (openable
in FreeCAD) and `<strategy>_grid.png` (a labeled 6-view grid of the result).


## Sample output

```text
[batch] 47 interior faces identified
[trim-outside] face 0: 13242.35 → 13242.35 mm²
[batch] face 0: kept original + untrimmed extension (trim had no effect)
...
JOIN OK via compound faces=63 open_edges=0 (shell is connected)

AI diagnosis:
  The face likely extends to the boundary of the model's outer surface,
  so when extended outward by 10.0 mm, the resulting ribbon does not
  cross into an interior region that can be trimmed.
  confidence: 0.85
  actions: reduce_extension_distance, manual_review

Deferred faces (left unchanged): [0]
Saved to examples/0-flange/per_face.FCStd
```

## Why this project

Demonstrates a constrained, auditable approach to AI-assisted CAD
automation: deterministic engineering software owns the geometry, and a
local LLM is used only for the reasoning that's genuinely hard to hand-code
— recognizing and responding to novel failure patterns — while every action
it recommends still passes through deterministic validation before touching
the model.
