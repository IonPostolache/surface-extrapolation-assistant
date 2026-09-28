# Recommended Plan — Surface Extrapolation Assistant for FreeCAD

## Why this version differs from the four drafts

- **`Surface::Extend` is ratio-based, not distance-based.** Its real parameters are `ExtendUNeg/UPos` and `ExtendVNeg/VPos` — fractions of the face's existing UV extent — not millimetres. Drafts 1 and 2 present `extrapolate_face(face, distance_mm=100.0)` as if that's a native call; it isn't. You'll need a calibration step (measure current edge length, compute the ratio that yields ~100 mm, extend, re-measure, correct) before you can honestly claim a `distance_mm` API. This is real work — give it its own time slot instead of hiding it inside Day 1.
- **Topological naming instability is a first-class risk, not a footnote.** Only Draft 4 names it. If any face gets recomputed, refreshed, or the document re-touched, `Face7` today is not `Face7` tomorrow. Track faces by geometric fingerprint (surface type + center of mass + bounding box + area) from Day 1, not as a Day-6 patch.
- **Joining/sewing extended faces back into one continuous surface is plausibly the hardest part of this project** — harder than the LLM integration — because `Part::Fuse` has no tolerance parameter and extended BSpline patches rarely line up cleanly. Give it 1.5 days, not a bullet point.
- **9 days, not 7.** A rushed Day-6 "evaluation" with N=3 toy surfaces won't survive five minutes of interview questioning. Fewer, more defensible claims beat a padded benchmark table.
- **The offline-first design from Draft 2 is kept**: the deterministic core must run and produce a usable result with the LLM completely absent. That's the difference between "CAD tool with AI-assisted diagnostics" and "AI toy that touches CAD," and it's the framing that will read as credible to a hiring manager who knows CATIA.

## Plan (9 working days)

**Day 1 — Load, traverse, identify boundary faces**
- `load_step(path)` → import into a headless FreeCAD document (`FreeCADCmd`), return the shape.
- `get_boundary_faces(shape, boundary_curve)` — topological traversal: faces sharing an edge with the supplied outer boundary.
- Geometric fingerprinting from the start: each face gets an ID derived from surface type + center of mass + bounding box + area, not its transient `Face7`-style index.
- Visual check: dump the candidate faces to a compound/color overlay so you can confirm selection before running anything destructive.
- **Deliverable:** correct, stable boundary-face list on 2–3 simple test shapes (cylinder sector, planar patch with a BSpline edge).

**Day 2 — Calibrated extrapolation**
- Wrap `Surface::Extend`. Since it takes UV ratios, not millimetres: measure the face's current extent along the relevant edge, compute the ratio for a target `distance_mm`, apply, re-measure the resulting edge, and do one correction pass if off by more than your tolerance (e.g. 2%).
- `extrapolate_face(face, distance_mm)` becomes the honest public API, with the ratio math hidden inside.
- Per-face success/failure logging (face ID, requested/achieved distance, FreeCAD error text if any).
- **Deliverable:** single-face extrapolation that reliably hits a millimetre target within tolerance, or reports why it couldn't.

**Day 3 — Batch processing + sewing (part 1)**
- Loop over all boundary faces; one failure must not halt the batch.
- Attempt to join successful extensions with `Part::Fuse` + `Part::RefineShape`; where that fails, fall back to sewing (`Part::Sewing` / `BRepBuilderAPI_Sewing` via the Python API) with an explicit tolerance.
- Report remaining open edges after join/sew — this number matters more than "success rate" for judging real usefulness.
- **Deliverable:** batch loop with a join/sew stage that reports what did and didn't knit together.

**Day 4 — Sewing (part 2) + diagnostics extraction**
- Finish hardening the join/sew step against mismatched or overlapping extended patches (this is where most real time will go — budget for it).
- Build the structured failure diagnostic: surface type, area, edge count and lengths, bounding box, degree/periodicity where extractable, and the raw FreeCAD error.
- Generate a simple diagnostic image (failed face highlighted against its boundary) for later LLM/human use.
- **Deliverable:** every failed face produces a structured JSON diagnostic + an image, and the join stage has a defined tolerance behavior.

**Day 5 — Local LLM diagnosis**
- Ollama or LM Studio via an OpenAI-compatible endpoint.
- Enforce JSON-only output (Ollama's `format: "json"`, or a Pydantic-validated parse against a schema either way — don't trust the model to self-police formatting).
- Prompt: structured geometry diagnostic in, `{diagnosis, confidence, recommended_actions[]}` out.
- Allow-listed actions only: `reduce_extension`, `retry`, `split_face`, `skip_face`. The LLM never emits Python or touches the document directly.
- **Deliverable:** a deliberately hard face (high curvature, tangent discontinuity) gets a structured, plausible diagnosis.

**Day 6 — Recovery loop**
- Validate the LLM's suggestion against the allow-list and parameter bounds before executing anything.
- Retry with the recommended parameters; record initial success / recovered / unresolved.
- Minimal CLI: run a file, see the per-face table, see what got recovered.
- **Deliverable:** end-to-end deterministic → fail → diagnose → validate → retry loop.

**Day 7 — Evaluation**
- Run baseline (direct extrapolation only), deterministic-retry (fixed fallback ratios, no LLM), and LLM-assisted recovery across 4–5 real/representative surfaces (not just toy primitives — at least one exported from a real stamped/die-face-like model if you have access to one from your CAE background).
- Record: initial success rate, recovered rate, unresolved count, retries, LLM calls, wall-clock time, achieved-vs-requested distance.
- Be honest in the writeup if the LLM's contribution is marginal on some cases — that honesty is more convincing than an inflated benchmark.
- **Deliverable:** a benchmark table you can defend if someone pushes on it.

**Day 8 — Documentation + limitations**
- Write the architecture doc and the "what the LLM does / doesn't do" table.
- Write "Known Limitations" honestly (see README below) — this is the section technical interviewers actually read.
- Clean up inline docs and the repo structure.

**Day 9 — Portfolio packaging**
- 30–60s screen capture: load STEP → boundary highlight → batch run → a failure → LLM diagnosis → recovery → final joined result.
- Final README pass, LICENSE, requirements.txt, example STEP files or generation scripts, clean commit history.
- Push public.

## Explicitly out of scope for V1
Conversational natural-language control (Draft 4's Day 5, Draft 2's Day 5) is a reasonable V2 feature but adds a translation layer of failure modes on top of a project that already has plenty. Cutting it buys the time the sewing step and honest calibration actually need.