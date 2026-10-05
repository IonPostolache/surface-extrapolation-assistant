# Design Log — Surface Extrapolation Assistant

A record of the design decisions, abandoned approaches, and findings
from building a FreeCAD-based surface-extension pipeline. Written after
the project reached a stable state, so it includes the reasoning that
isn't visible in the final code.

## Goal

Replicate a small piece of CATIA V5's surface-prep workflow in FreeCAD:

> Given an imported surface (a die-face patch), extend its boundary
> faces outward in curvature by a target distance, trim the extensions
> back to the surface's outer boundary, and join the result into a
> single shell.

The motivating use case is stamping die-face prep, where this extension
step is repeated for every boundary face on every part.

## Final architecture
STEP surface (single file, no holes)
|
v
topology.get_boundary_faces_no_curve
| - detect outer boundary edges topologically
| - identify boundary faces
v
extrapolation.extrapolate_face
| - choose strategy by surface type
| - plane: ruled strip
| - BSpline / cylinder: curvature ribbon
v
io.trim_face_to_outside_boundary
| - ribbon split by outer boundary
| - classify pieces by 2D point-in-polygon score
v
batch.run_batch
| - keep trimmed band if trim reduced area
| - else keep original + untrimmed extension (DEFERRED)
| - join with interior faces
v
io.save_extended_faces → extended.FCStd
llm.diagnose / diagnose_deferred_faces (optional)

text

## Approaches tried and abandoned

### 1. Parametric `Surface::Extend` with ratio calibration

**Hypothesis:** `Surface::Extend` extends a face outward in curvature.
Calibrate the ratio so the extension hits a target distance in mm.

**Why it was tried:** It's the "native" FreeCAD way to extend a surface.

**Why it was abandoned:** `Surface::Extend` operates on the face's
underlying parametric surface. For a BSpline face, extending the
parametric range reveals geometry that was trimmed away in the source
data. The extension "grows" in every direction, including back into
the interior of the part. On simple planar faces it works; on curved
BSplines it produces untrimmed geometry that is not a face extension
in any useful sense.

### 2. Ruled strips for all faces

**Hypothesis:** For each boundary edge, build a ruled surface between
the edge and an offset copy of the edge.

**Why it was tried:** Avoids `Surface::Extend` entirely; the extension
is built geometrically, not parametrically.

**Why it was abandoned (for curved faces):** A ruled surface between
two curves is a flat strip. On a curved face, the extension should
follow the face's curvature, not continue straight. Ruled strips
produce visibly wrong geometry at corners and along curved boundaries.

Still used for **planar faces**, where a flat strip is geometrically
correct.

### 3. Untrim-guard via `Shape.common()`

**Hypothesis:** Extend the face parametrically (over-extending, revealing
the untrimmed parent), then trim the result back to a "keep region"
defined as `original_face ∪ ruled_border`.

**Why it was tried:** A safety net around approach #1 — accept the
un-trimmed extension and clip it back with a boolean.

**Why it was abandoned:** When `Surface::Extend` un-trims a face, the
extended surface **moves in 3D space** (it grows outward in a different
direction than the original face sits). `Shape.common()` between the
extended surface and the keep-region returns nothing useful, because
the two shapes don't overlap. The trim-guard never fires.

### 4. Slab cut by the interior boundary

**Hypothesis:** Build a solid slab from the projected interior boundary
polygon. Cut the extended face with the slab to remove the interior
spill.

**Why it was tried:** Cuts are more reliable than splits for
surface geometry. A solid slab has volume, so `Shape.cut()` works
on any orientation.

**Why it was abandoned:** The interior boundary is a **curved 3D
contour** (not planar). Projecting it onto a plane loses the fillet
curvature at the corners — the projected polygon has straight chords
where the real boundary curves. The slab cuts inside those chords,
removing part of the correct extension along with the spill.

### 5. Per-interior-face slab cuts

**Hypothesis:** For each interior face, extrude it along its own normal
into a solid slab. Cut the extended face with each slab in sequence.
With 47 interior faces, the union of their slabs removes the spill
everywhere it occurs.

**Why it was tried:** Exact — no projection, no chord approximation.
Each slab follows the interior face's own geometry.

**Why it was abandoned:** The interior faces' slabs are 1000 mm thick
by design (to guarantee they cross the extension regardless of
orientation). But the "correct" extension sits in the **same surface
plane** as the interior faces — it's coplanar with them, not below
or above. So each slab removes a chunk of both the spill **and** the
correct extension. After 46 cuts, the extended face was reduced from
10354 mm² to 25 mm² — everything removed.

A one-sided slab (extruded only inward from the interior face) was
tried as a follow-up. It produced the same result because the interior
faces' normals point "up and out" of the surface; extruding inward
pushes the slab into the extension's territory, not away from it.

### 6. Ribbon split by the interior boundary wire

**Hypothesis:** Build a closed ribbon from the interior boundary wire
(48 edges, closed=True), split the extended face by it.

**Why it was tried:** The interior boundary wire is a single connected
loop that fully surrounds the interior region — the correct trim
contour in principle.

**Why it was abandoned:** The split **does** produce two pieces
(7746 and 2607 mm²), but neither corresponds to "spill vs. correct
extension". The interior wire passes **through** face 0, not around
it. When face 0's extension grows outward from its inner contour, the
interior wire is at that inner contour — so the split separates the
extension into two regions that don't match the intended geometry.
Neither piece has a positive point-in-polygon score, so the
classifier can't pick the right one.

### 7. Second-pass trim for pathological faces

**Hypothesis:** For faces where the first-pass trim had no effect,
try a more sophisticated trim (shared-edge ribbon, interior-boundary
slab, etc.).

**Why it was tried:** Face 0 needed *something*, and the pipeline
had spare capacity.

**Why it was abandoned:** Every second-pass approach either removed
too much (per-face slabs) or too little (shared-edge ribbons). The
final decision was to **detect the failure and defer the face**,
rather than risk damaging the geometry. This is the current
behavior — face 0 is reported as `DEFERRED` and left unchanged.


### 8. Whole-surface strategy (alternative)

**Hypothesis:** Instead of extending each boundary face independently,
build a single ribbon from the entire outer boundary wire, so corners
are handled by the loft instead of by joining separate extensions.

**Result on test3:** failed. The loft between the closed outer boundary
wire and its offset wire folds over itself in the concave "keyhole"
region at the bottom of the part. The `_outward_at_boundary_point`
helper is unreliable on concave boundaries — the nearest-face normal
doesn't align with the boundary's local perpendicular.

**When it might work:** surfaces with convex or gently-curved outer
boundaries.

**Why it fails on real parts:** the outer boundary of a stamping panel
is almost always concave somewhere (notches, cutouts, flange bases).
The loft approach cannot handle those cleanly.

## Findings about FreeCAD's public API

These are the load-bearing technical conclusions, independent of the
specific pipeline:

1. **`Surface::Extend` is parametric, not geometric.** Its ratio
   arguments refer to the face's existing UV extent, not to
   millimetres. Calibrating them to hit a mm target is possible but
   the extension itself is over the *parent surface's* parameter
   range, which means un-trimming is a side effect for BSplines.

2. **There is no "untrim" operation in the public API.** CATIA
   stores parent-surface metadata on every face; FreeCAD's STEP
   importer discards it. Recovering the parent's natural bounds
   would require sampling and reverse-engineering, which is
   unreliable for arbitrary BSplines.

3. **`Shape.common()` between a moved surface and a fixed region
   doesn't work as a trim guard.** If the two shapes don't overlap
   in 3D, the boolean returns nothing. This rules out the "trim
   against a keep region" family of approaches.

4. **`Shape.cut()` with a slab is directional only if the slab is
   one-sided.** A two-sided slab removes everything in its volume,
   including correct geometry. A one-sided slab requires knowing
   the correct orientation of the face's normal, which is not
   always derivable from the face alone.

5. **`BOPTools.SplitAPI.slice` requires the cutting tool to fully
   cross the target shape.** A ribbon that passes through the
   interior of a face but doesn't reach its edges does not split
   it — the operation returns one piece.

6. **Curved 3D contours cannot be used directly as cutting tools.**
   A wire that traces a curved path cannot be extruded into a
   clean planar slab; the projection loses curvature and the
   extrusion becomes a warped surface. This is why the slab
   approach was tried with projected (planar) polygons, and why
   that lost fillet accuracy.

## What the pipeline does correctly

Seven of eight boundary faces on the test part (`Part3v3.stp`) are
extended and trimmed automatically:

| Face | Requested | Achieved | Trim result |
|------|-----------|----------|-------------|
| 0 | 5 mm | 5 mm | DEFERRED (trim had no effect) |
| 1 | 5 mm | 5 mm | 88.63 → 32.91 mm² |
| 17 | 5 mm | 5 mm | 88.63 → 32.91 mm² |
| 18 | 5 mm | 5 mm | 1003.10 → 203.87 mm² |
| 30 | 5 mm | 5 mm | 1003.09 → 203.87 mm² |
| 39 | 5 mm | 5 mm | 196.44 → 77.78 mm² |
| 45 | 5 mm | 5 mm | 196.43 → 77.77 mm² |
| 47 | 5 mm | 5 mm | 698.20 → 113.10 mm² |

The output is a joined compound with no open edges, plus the original
surface and untrimmed extension for the deferred face.

## What a production tool would need

To fully replicate CATIA's "extrapolate in curvature" with edge
sub-selection:

1. **Parent-surface metadata on every imported face.** FreeCAD's
   STEP importer does not preserve it. `pythonocc`'s
   `BRep_Tool::Surface(face)` exposes the underlying `Geom_Surface`,
   which is one step closer.

2. **Direct access to `Geom_BSplineSurface::Extend()`.** This extends
   the parent surface exactly as CATIA does. FreeCAD's Python API
   does not expose this; `pythonocc` does.

3. **Explicit trim-wire construction.** Build the new face from the
   extended parent surface and a wire that traces
   `original_boundary + extension`. This requires 2D work in the
   surface's parameter space.

4. **Corner handling as a first-class operation.** Where two
   extension ribbons meet at a corner, they must be stitched or
   mitered — not left as separate pieces that happen to touch.

5. **A robust slicing kernel.** Slicing a large BSpline by a curved
   3D contour is exactly the operation that failed repeatedly. The
   commercial kernels do this reliably; FreeCAD's does not.

Steps 1–3 are the crux. Everything else is polish. `pythonocc` gives
access to all three; whether it gives *reliable* access is a question
that would need its own evaluation.

## Why the LLM layer exists

The pipeline is fully deterministic — it does not need an LLM to run.
The LLM is consulted only when the deterministic pipeline reports a
failure (join failure) or a deferred face. Its job is to translate
structured geometry diagnostics into a natural-language explanation
and a bounded recovery action.

The design principle:

- The LLM never generates CAD code.
- The LLM never touches the FreeCAD document.
- It selects from a fixed allow-list of actions.
- Every action is validated by the deterministic pipeline before
  anything changes.

This keeps the AI layer auditable. A reviewer can inspect the payload
sent to the model, the schema of the response, and the allow-list of
actions — and conclude that the model's output cannot corrupt the
geometry.

## What I'd do differently if starting over

1. **Start with `pythonocc`, not FreeCAD.** The geometry kernel is the
   same, but the API surface is much closer to what CATIA exposes.
   FreeCAD's `Part` module wraps a subset of OCC in a way that hides
   exactly the operations this project needs.

2. **Evaluate the parent-surface access path first.** Before writing
   any pipeline code, verify that the underlying surface of an
   imported BSpline can be extended directly. If not, the whole
   approach is a dead end.

3. **Skip the ribbon approach entirely.** Ribbons approximate
   curvature continuation; they don't reproduce it. A pipeline that
   can't reproduce it should either do the untrim+extend+retrim
   workflow exactly, or not attempt it.

4. **Scope down to a single face type.** Planar-only or
   cylinder-only would have been a more honest v1. Extending the
   scope to BSplines before the planar path was fully solid cost
   time without producing a better result.

## Timeline notes

- initial setup, boundary detection, ratio-calibrated
  `Surface::Extend`. Ran into un-trimming on the first complex part.
- explored ribbon, ruled-strip, and untrim-guard approaches.
  Ribbons worked on simple cases but produced visibly wrong geometry
  on curved faces.
- built the LLM diagnostic layer, JSON schema, and CLI.
- iterated on trimming. Every approach was tried at least
  once, documented, and either shipped or abandoned.
- Final state: 7/8 boundary faces handled automatically; face 0
  deferred.

## References

- CATIA V5 GSD Extrapolate command
- FreeCAD `Surface::Extend` documentation
- FreeCAD `Part::Slicing`, `BOPTools.SplitAPI` behavior
- OpenCASCADE `Geom_BSplineSurface::Extend` (for the proposed
  pythonocc rewrite)
- (Optional) the two AI-generated design responses that were
  consulted during the trim exploration, kept for future reference