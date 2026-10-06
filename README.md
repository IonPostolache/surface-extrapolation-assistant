# Surface Extrapolation Assistant for FreeCAD

Automated boundary-face extension for stamping die-face preparation, built on
FreeCAD's Python API, with a local LLM used only for diagnosing and
suggesting recovery when the deterministic geometry pipeline fails.

## The problem

Extending a complex imported surface (e.g. an automotive die-face patch) by a
fixed distance along its outer boundary, in a tool like CATIA V5, typically
means: extract each boundary face → untrim → extrapolate in curvature → split
against neighbor contours → join → repeat, per face. On a model with many
faces this is slow, and the failure cases are the most time-consuming part.

FreeCAD exposes `Surface::Extend`, but it operates on the face's underlying
mathematical surface — which can un-trim BSpline faces, revealing geometry
that was trimmed away in the source data. There is no built-in batch
traversal, failure handling, or recovery logic.

## Input requirements

The pipeline takes **a single STEP file** containing the surface to extend.

- **The surface may contain holes.** The pipeline detects all boundary
  loops topologically and extends only the **outermost one** (the loop
  with the largest bounding box). Hole boundaries are ignored.


## What this does

1. Imports a STEP surface and identifies all faces touching the outer
   boundary (topologically detected).
2. For each boundary face, determines which of its edges are "extendable":
   they are neither shared with a neighbor face nor on the interior, and
   they lie on the outer boundary.
3. Extends each face along its extendable edges by a target distance in
   millimetres. Two strategies are used depending on surface type (see
   below).
4. Attempts to trim each extended face back to the surface's outer boundary
   so only the outer band survives.
5. Joins the result into a compound shell and reports open edges.
6. On failure, builds a structured geometric diagnostic and (optionally)
   sends it to a **local** LLM (LM Studio or Ollama) that returns a
   structured JSON diagnosis and a recommended action from a fixed
   allow-list.

The geometry pipeline is fully deterministic and runs with no LLM present.
The LLM is consulted only on failure, and it never generates or executes
arbitrary code — it selects from a small set of pre-implemented recovery
actions.

## Extension and trim strategy

The pipeline handles each boundary face as follows:

1. **Extend** the face along its extendable edges.
2. **Trim** the extension back to the outer boundary, keeping only the
   outside band.
3. **Decide** which result to keep:
   - If the trim reduced the area meaningfully → keep the trimmed band +
     the original face.
   - If the trim had no effect (the extension already matches the outer
     boundary) → keep the original face + the untrimmed extension, and mark
     the face as `DEFERRED`.

The `DEFERRED` status is the honest signal: the face could not be trimmed
reliably by FreeCAD's tooling, so the original face and its untrimmed
extension are preserved unchanged, and the report names the face.

### Extension strategies (by surface type)

| Surface type       | Strategy                                        | Notes |
|--------------------|-------------------------------------------------|-------|
| Plane              | Ruled strip, built from offset boundary edges   | Preserves trim topology. Produces a flat strip. |
| BSpline / Cylinder | Curvature-following ribbon                      | Samples the edge, offsets each sample outward in the surface's tangent plane, interpolates a BSpline through the offset points, and lofts between original and offset edges. |
| Other              | Parametric `Surface::Extend` with ratio calibration | Preserved as a fallback. |

The **ribbon** strategy is the closest approximation FreeCAD offers to
CATIA's "extrapolate in curvature" operation. It preserves the original
face unchanged and builds the extension as a separate surface sewn onto it,
so corner fillets and interior geometry are not disturbed.

The **ruled strip** strategy is used on planar faces because the underlying
surface is already flat, so a flat strip is geometrically correct.

## Architecture

```text
STEP surface (single file)
        |
        v
FreeCAD / Python geometry core
        |
        v
Identify boundary faces and extendable edges
(topological outer-boundary detection +
stable geometric fingerprint, not Face-N index)
        |
        v
Per-face extension (ribbon / ruled / parametric)
        |
        v
Trim extension back to outer boundary
        |
   +----+----+
   |         |
trimmed   no effect
   |         |
   v         v
keep band  keep original + untrimmed extension
+ face     (mark as DEFERRED)
   |         |
   +----+----+
        |
        v
Join + report open edges
        |
        v
Optional: local-LLM diagnosis
(on failure, JSON out, allow-listed recovery actions only)
```

## What the LLM does — and does not do

| Task | Component |
|---|---|
| Import STEP, headless load | FreeCAD |
| Detect outer boundary, boundary faces | Python / OpenCascade |
| Build extension ribbons / strips | Python + FreeCAD |
| Trim, join, report open edges | FreeCAD + Python |
| Detect and log failures | Python |
| Build structured diagnostics | Python |
| Diagnose *why* a face failed | Local LLM |
| Propose a recovery action (from an allow-list) | Local LLM |
| Validate and execute the recovery | Python / FreeCAD |
| Generate arbitrary CAD code | **Never — not a supported path** |

## Known limitations

### Geometric fidelity

- **The ribbon extension is an approximation of CATIA's "extrapolate in
  curvature."** It follows the local tangent direction sampled from the
  surface, but does not reconstruct the exact mathematical continuation of
  the parent surface over the extended range. On gently-curved surfaces
  over a few millimetres the deviation is small; on strongly-curved BSpline
  patches it grows with distance and curvature.
- **FreeCAD does not expose a "true untrim + extrapolate in curvature"
  operation.** CATIA stores parent-surface metadata on every face and can
  untrim in one call; FreeCAD discards that metadata on STEP import.
- **The pipeline does not reproduce CATIA's numerical output.** Users who
  need to match CATIA's extrapolation exactly should use CATIA.
- **Corner regions between adjacent extended faces may have small gaps or
  overlaps.** The ribbon is built per-edge; where two extendable edges meet
  at a corner, their ribbons may not meet exactly.
- **Large BSpline faces that wrap the whole part cannot be trimmed
  reliably.** Their extension grows in every tangent direction, and the
  outer-boundary ribbon does not cross the extension (so the split produces
  one piece instead of separating the spill from the correct extension).
  These faces are detected by their trim having no effect, kept unchanged
  (original face + untrimmed extension), and reported as `DEFERRED`.

### Pipeline

- Boundary detection assumes a clean, connected outer boundary.
- OpenCascade's topological naming is unstable across recomputes — faces
  are tracked by geometric fingerprint (surface type, center of mass,
  bounding box, area) rather than by index.
- `Part::Fuse` has no tolerance parameter; joining mismatched extended
  patches sometimes requires a sewing fallback with an explicit tolerance.
- LLM recommendations are advisory only and drawn from a fixed action
  allow-list; all geometry changes are validated deterministically before
  being kept.
- Evaluated on a handful of representative surfaces, not a large industrial
  dataset. Benchmark numbers should be read as indicative, not comprehensive.

### What a production tool would need

To fully replicate CATIA's `Extrapolate in curvature` with edge sub-selection:

1. Parent-surface metadata preserved on every imported face (which
   FreeCAD's STEP importer does not currently provide).
2. True surface untrim: reconstruct the parent surface with its natural
   parameter bounds.
3. Curvature-continuous surface extension on the parent, then re-trim to
   `original_boundary + extension`.
4. Corner handling as a first-class operation, not a geometric cleanup.
5. A trimming kernel robust enough to slice extended BSpline surfaces
   without losing tangent continuity.

Steps 1–3 are the crux. FreeCAD's public Python API does not expose them.
`pythonocc` does — but rewriting the extension module on `pythonocc` while
keeping the rest of the pipeline in FreeCAD is a substantial project, and is
left as future work.


## Quick start

### 1. Install and extract FreeCAD

FreeCAD cannot be installed via `pip`. It must be present on your system and
reachable from Python. AppImage users should **extract** the AppImage rather
than running it directly, because a running AppImage mounts itself in a
temporary location that disappears when the process exits.

```bash
mkdir -p ~/.FreeCAD
cd ~/.FreeCAD

# Extract the AppImage (adjust the filename)
~/Downloads/FreeCAD_*.AppImage --appimage-extract
```
This produces ~/.FreeCAD/squashfs-root/, which contains FreeCAD's binaries,
libraries, and its bundled Python interpreter.

### 2. Create the virtual environment with FreeCAD's Python
FreeCAD 1.1 AppImages are built against Python 3.11. If you create the
venv with a newer interpreter (3.12+), importing FreeCAD will fail with:

ImportError: libFreeCADBase.so: undefined symbol: _Py_PackageContext
This is a binary compatibility issue: the C symbol signature changed between
3.11 and 3.12, so the compiled FreeCAD modules cannot link against 3.12.

Use FreeCAD's own bundled Python to create the venv:

```bash
cd /path/to/surface-extrapolation-assistant
rm -rf .venv
~/.FreeCAD/squashfs-root/usr/bin/python -m venv .venv
source .venv/bin/activate

# Should print 3.11.x
python --version
```
### 3. Configure the FreeCAD library path
Edit config.yaml and set the path to FreeCAD's library directory:

```yaml
freecad:
  lib_path: "/home/<you>/.FreeCAD/squashfs-root/usr/lib"
```
Or set the FREECAD_LIB_PATH environment variable.

### 4. Install the project
```bash
pip install -e ".[dev]"
```
### 5. Verify the setup
```bash
python -c "
from surface_assistant import freecad_setup
import FreeCAD, Part
box = Part.makeBox(10, 10, 10)
print('FreeCAD version:', FreeCAD.Version()[0], FreeCAD.Version()[1])
print('Faces:', len(box.Faces))
"
```
Expected:

FreeCAD version: 1 1
Faces: 6

### 6. Optional: Local LLM

The geometry pipeline runs without an LLM. To enable failure diagnosis, set `LLM_API_KEY` in `.env`:

    LLM_API_KEY=lm-studio

Everything else (endpoint, model, timeout, temperature) is read from
`config.yaml`. Load the model in LM Studio (or Ollama) before running with `--ai`.


## Usage

### Inspect a surface
```bash
surface-assistant inspect model.step
```

### Run the pipeline
The folder must contain exactly one STEP file. Filenames do not need tofollow a naming pattern:

```bash
surface-assistant run examples/test3 --strategy per_face --ai
surface-assistant run examples/v1_with_holes/test3 --strategy per_face --ai
```

The pipeline always produces two files:
- `<name>.FCStd` — openable in FreeCAD.
- `<name>_grid.png` — a labeled 6-view grid (ISO, FRONT, TOP, LEFT, BACK, BOTTOM) combined into one image.


# Whole-surface
```bash
surface-assistant run examples/test3 --strategy whole_surface --ai
surface-assistant run examples/v1_with_holes/test3 --strategy whole_surface --ai

```

## Strategies

Two strategies are available via `--strategy`:

| Strategy | What it does |
|----------|--------------|
| `per_face` (default) | Extends each boundary face independently, trims each extension back to the outer boundary, joins the trimmed bands with the original and interior faces. Produces clean per-face results. |
| `whole_surface` | Builds one ribbon per boundary edge of the whole surface and compounds them with the original and interior faces. Handles concave and multi-loop boundaries but produces slightly coarser geometry at corners. |


## Sample output

```text
Running the pipeline on `examples/test3/Part3v3.stp` with 
`--strategy per_face --ai`:
[batch] 47 interior faces identified
[trim-outside] face 0: 13242.35 → 13242.35 mm²
[batch] face 0: kept original + untrimmed extension (trim had no effect)
...
[batch] TRIM no_overlap faces=16
Batch report for Part3v3.stp
...
JOIN OK via compound faces=63 open_edges=0 (shell is connected)

AI diagnosis:
image: The image shows a multi-panel view of a mechanical part
with multiple features, viewed from ISO, front, top, back,
bottom, and left perspectives.
The face likely extends to the boundary of the model's outer
surface, so when extended outward by 10.0 mm, the resulting
ribbon does not cross into an interior region that can be trimmed.
confidence: 0.85
actions: reduce_extension_distance, manual_review

...
Deferred faces (left unchanged): [0]
Saved to examples/test3/per_face.FCStd
```

  
Why this project
Demonstrates a constrained, auditable approach to AI-assisted CAD automation:
deterministic engineering software owns the geometry, and a local LLM is used
only for the reasoning that's genuinely hard to hand-code — recognizing and
responding to novel failure patterns — while every action it recommends still
passes through deterministic validation before touching the model.

It also documents, honestly, where FreeCAD's public API hits its limits when
compared to a commercial kernel like CATIA's — and what a pythonocc-based
implementation would need to close the remaining gap.

The LLM receives both a structured diagnostic (JSON) and a rendered 6-view grid of the result, and returns a schema-validated diagnosis and a bounded recovery action. The image is mandatory in the response schema, so the model cannot fake attention to geometry it hasn't seen.

> **Design decisions and abandoned approaches:** see
> [`docs/design_log.md`](docs/design_log.md) for a full record of what
> was tried, what failed, and why.