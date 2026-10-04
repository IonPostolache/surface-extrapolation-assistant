# Design log — what was built, what was abandoned, and why

## Final architecture

- Single STEP file input.
- Boundary faces detected topologically via `get_boundary_faces_no_curve`.
- Extension via two strategies: ruled strips for planar faces, curvature
  ribbons for BSpline and cylindrical faces.
- Trim via a thin ribbon extruded from the surface's outer boundary;
  pieces are classified by a 2D point-in-polygon score.
- Join via `Part::Fuse`, fallback to `Part::Sewing`.

## Approaches tried and abandoned

1. **Parametric `Surface::Extend` with ratio calibration** — abandoned
   because it un-trims BSpline faces, revealing parent-surface geometry
   that wasn't part of the original face.

2. **Ruled strips for all faces** — abandoned for curved faces because
   the strip is flat and doesn't follow the surface's curvature.

3. **Slab cut by the interior boundary** — abandoned because the
   projection to a planar slab loses fillet curvature at the corners.

4. **Per-interior-face slab cuts** — abandoned because 46 sequential
   boolean cuts remove the correct extension along with the spill.

5. **Ribbon split by the interior boundary wire** — abandoned because
   the split produces pieces that don't correspond to inside/outside
   of the contour when the contour is a curved 3D loop.

6. **Second-pass trim for pathological faces** — abandoned; the current
   pipeline detects "trim had no effect" and marks the face as
   `DEFERRED` instead of trying more elaborate strategies.

## Known limitations carried forward

- Large BSpline faces that wrap the whole part cannot be trimmed; they
  are kept unchanged and reported as `DEFERRED`.
- The ribbon extension approximates CATIA's "extrapolate in curvature";
  it does not reconstruct the exact mathematical continuation of the
  parent surface.

## What a future version would do differently

- Move the extension module to `pythonocc` for direct access to
  `Geom_BSplineSurface::Extend()` and explicit trim-wire construction.
- Add a corner-handling step for the ribbon joints.