# Design Log — Surface Extrapolation Assistant

A record of the design decisions, abandoned approaches, and findings
from building a FreeCAD-based surface-extension pipeline. Written after
the project reached a stable state, so it includes the reasoning that
isn't visible in the final code.

## Goal

Replicate a small piece of CATIA V5's surface-prep workflow in FreeCAD:

> Given an imported surface, extend its boundary
> faces outward in curvature by a target distance, trim the extensions
> back to the surface's outer boundary, and join the result into a
> single shell.

The motivating use case is stamping die-face prep, where this extension
step is repeated for every boundary face on every part.

## Module map

See the [README](../README.md) for the conceptual pipeline diagram.


| Stage | Module / function |
|---|---|
| Detect outer boundary edges topologically, identify boundary faces | `topology.get_boundary_faces_no_curve` |
| Choose extension strategy by surface type (plane → ruled strip, BSpline/cylinder → curvature ribbon) | `extrapolation.extrapolate_face` |
| Split the extension by the outer boundary, classify pieces by 2D point-in-polygon score | `io.trim_face_to_outside_boundary` |
| Keep trimmed band if the trim reduced area, else keep original + untrimmed extension (`DEFERRED`); join with interior faces | `batch.run_batch` |
| Save result, optional LLM diagnosis on failure/deferred faces | `io.save_extended_faces`, `llm.diagnose` / `llm.diagnose_deferred_faces` |


## Approaches tried and abandoned

### 1. Parametric Surface::Extend with ratio calibration

Hypothesis: Surface::Extend extends a face outward in curvature. Calibrate the ratio so the extension hits a target distance in mm.

Why it was tried: It's the "native" FreeCAD way to extend a surface.

Why it was abandoned: Surface::Extend operates on the face's underlying parametric surface. For a BSpline face, extending the parametric range reveals geometry that was trimmed away in the source data. The extension "grows" in every direction, including back into the interior of the part. On simple planar faces it works; on curved BSplines it produces untrimmed geometry that is not a face extension in any useful sense.

### 2. Ruled strips for all faces

Hypothesis: For each boundary edge, build a ruled surface between the edge and an offset copy of the edge.

Why it was tried: Avoids Surface::Extend entirely; the extension is built geometrically, not parametrically.

Why it was abandoned (for curved faces): A ruled surface between two curves is a flat strip. On a curved face, the extension should follow the face's curvature, not continue straight. Ruled strips produce visibly wrong geometry at corners and along curved boundaries.

Still used for planar faces, where a flat strip is geometrically correct.

### 3. Boolean/slab-based trims against the interior boundary

Hypothesis: Three variations on the same idea — clip the over-extended face back to a "keep region" defined by the interior boundary, using Shape.common(), a slab cut from the projected boundary polygon, and per-interior-face slab cuts.

Why it was tried: Booleans are generally more reliable than splits for surface geometry, and each variation tried to fix the previous one's failure mode.

Why all three were abandoned: The common thread is that the "correct" extension is coplanar with or directly adjacent to the interior geometry it needs to be trimmed against, not spatially separated from it:

Shape.common() failed because the un-trimmed extension moves in 3D space relative to the original face, so the two shapes don't overlap and the boolean returns nothing.
A slab from the projected (planarized) interior boundary lost the fillet curvature at corners, cutting into correct geometry along the straightened chords.
Per-interior-face slabs, built thick enough to guarantee they crossed the extension, also sliced through the correct extension because it sits in the same surface plane as the interior faces — there was no "safe side" to extrude from. A one-sided version failed the same way, since the interior faces' normals point away from the extension's territory, not into it.

### 4. Ribbon split by the interior boundary wire

Hypothesis: Build a closed ribbon from the full interior boundary wire and split the extended face by it — a single connected loop that should fully separate spill from correct extension.

Why it was tried: In principle the interior wire is the correct trim contour.

Why it was abandoned: The split does produce two pieces, but neither corresponds to "spill vs. correct extension." The interior wire passes through the face in question rather than around it, so the split doesn't align with the intended geometry, and neither resulting piece scores as a clean "correct" region for the classifier to pick.

### 5. Second-pass trim for pathological faces

Hypothesis: For faces where the first-pass trim had no effect, try a more sophisticated trim (shared-edge ribbon, interior-boundary slab, etc.).

Why it was tried: The remaining unresolved face needed something, and the pipeline had spare capacity.

Why it was abandoned: Every second-pass approach either removed too much or too little. The final decision was to detect the failure and defer the face, rather than risk damaging the geometry. This is the current behavior — an unresolved face is reported as DEFERRED and left unchanged.

### 6. Whole-surface strategy (alternative)

Hypothesis: Instead of extending each boundary face independently, build a single ribbon from the entire outer boundary wire, so corners are handled by the loft instead of by joining separate extensions.

Result: succeeded after switching from a single lofted ribbon to one ribbon per boundary edge — the single-loft version folded over itself on concave boundaries, where the offset curve crosses itself. The per-edge version handles concave and multi-loop boundaries naturally, at the cost of being slower and sometimes not aligning perfectly at corners; it's the better choice when per-face extension leaves visible spill on a complex or concave boundary.

### 7. Vision-enabled LLM diagnosis

Hypothesis: A text-only diagnostic (structured JSON with face metrics and error messages) is enough for a local LLM to explain a deferred face. A vision-language model that also sees the geometry would give better diagnoses.

Result: confirmed. The pipeline renders a 6-view grid (ISO, FRONT, TOP, LEFT, REAR, BOTTOM) as a single PNG and sends it alongside the JSON payload.

Implementation detail: the request uses LM Studio's OpenAI-compatible /v1/chat/completions endpoint with response_format.type = json_schema. The schema forces the model to return image_visible and image_description alongside diagnosis, confidence, and recommended_actions — without mandatory image fields, a model can produce a plausible-sounding diagnosis from text alone and never actually look at the geometry. The image_visible boolean is the proof the vision path worked.

### 8. Hole handling via outermost-loop selection

Hypothesis: The pipeline originally required hole-free surfaces because get_outer_boundary_edges returns every boundary edge, mixing the outer perimeter with hole boundaries. Extending both produced garbage across the holes.

Fix: Group boundary edges into connected loops (get_boundary_loops) and select only the loop with the largest bounding box (get_outer_perimeter_loop). The pipeline now treats holes as interior features and extends only the outer perimeter.

Why bounding-box volume, not perimeter length: A hole with many small scallops can have a longer perimeter than a simple outer rectangle. The bounding box is a more robust "outerness" signal for typical stamped panels.

Tradeoff: For surfaces with multiple disconnected outer contours, the pipeline picks the largest and silently drops the rest.


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


## References

- CATIA V5 GSD Extrapolate command
- FreeCAD `Surface::Extend` documentation
- FreeCAD `Part::Slicing`, `BOPTools.SplitAPI` behavior