# Project Plan — Local Engineering Drawing → Provisional Parametric CAD,
# Measured Against a Real Dataset

## Goal

Two goals, pursued together rather than traded off against each other:

1. Build a **useful, free, fully local tool** that turns a 2D engineering
   drawing into a complete, editable 3D CAD model, clearly flagging every
   assumption it had to make so an engineer can review and correct quickly.
2. Honestly measure **how good current free/local models actually are** at
   this task, using a real dataset with known ground truth — not a
   synthetic set built to be easy, and not just impressions from a handful
   of manual tries.

These aren't in tension as long as the benchmark measures the same
finish-and-flag pipeline the tool actually runs, rather than a separate
"refuse if unsure" mode built just to score well.

## Core principle

> **Don't interrupt the engineer unless missing information prevents a
> meaningful reconstruction. Otherwise, make the best engineering
> assumption, finish the provisional model, and let the engineer review and
> correct the assumptions afterward.**

## Validation principle — one method, usable by anyone

The tool validates a generated model against a **reference STEP file**,
using pure geometry comparison — never a comparison method specific to one
dataset's internal format. This is a deliberate simplicity choice: whether
someone is running the benchmark against CAD-VGDrawing, or a user brings
their own drawing plus a STEP model they already have (e.g. checking an old
drawing against a known-good model, or supplying their own test case), the
exact same checker runs. There is no separate, dataset-specific
"parameter-by-parameter diff" validation path.

Concretely, given `generated.step` and `reference.step`:
1. **Volumetric IoU** — boolean intersection over union of the two solids
   (OCC's `BRepAlgoAPI_Common` / `BRepAlgoAPI_Fuse`). The core correctness
   signal, and the same metric used in published CAD-reconstruction papers
   (Ortho2CAD, CAD-Coder), so results are comparable to reported numbers if
   useful. Wrap boolean operations in try/except — OCC booleans can throw on
   coplanar faces or tight numerical tolerances; fall back to bounding-box
   and mass-property comparison alone for a case rather than letting one
   geometry failure crash a benchmark run.
2. **Mass properties** (`GProp_GProps`) — volume and center-of-mass
   comparison, as a cheap supporting check alongside IoU.
3. **Bounding box comparison** — a fast sanity check before the more
   expensive boolean operation.
4. **Valid-solid rate**, tracked separately from IoU — a case that fails to
   produce any valid solid (generator error, invalid geometry) is a
   different failure mode from a case that produces a valid but wrong
   solid (IoU near 0). Reporting only IoU conflates the two; report both,
   e.g. "92/100 valid solids; IoU 0.81 mean over the valid ones."

**Diagnostic detail, not a second validation gate:** alongside global IoU,
it's worth also computing a coarse per-feature match (e.g. does the
generated PartSpec contain a hole within tolerance of each ground-truth
hole's position and diameter?). This does not replace IoU as the pass/fail
signal — IoU alone can hide a misplaced feature inside an otherwise
plausible bounding shape, which matters given the tool's whole pitch is
feature-level review. This is a diagnostic breakdown layered on top of the
one validation method, not a competing checker.

**What this does *not* replace:** the PartSpec tier classification
(confirmed/inferred/assumed/ambiguous) that drives the tool's review panel.
That classification exists regardless of how validation is done — it's how
the tool decides what to show the engineer. On CAD-VGDrawing specifically,
because the dataset's ground-truth *parameters* happen to be available (not
just a STEP shape), the same PartSpec-vs-ground-truth-parameters comparison
used internally for the review panel can also be reported as an extra,
bonus breakdown (tier-level accuracy: how often was an "assumed" hole
diameter actually close to the real one?). This isn't a second validation
method — it's reusing infrastructure the tool needs anyway, on the one
dataset where richer ground truth happens to be available for free. The
STEP/IoU check above remains the one method that works for every case,
including a user's own drawing with their own reference STEP.

## Non-goals (V1)

- Isometric / photographed / perspective drawings
- Sheet-metal bend features, GD&T, section views, multi-body assemblies
- Beating paid, frontier-scale commercial models — the point is finding out
  where local/free models currently stand, not winning
- Automated repair without human review
- A separate, dataset-specific validation path — see above

## Test data — CAD-VGDrawing

**Dataset:** `github.com/lllssc/Drawing2CAD` (paper: arXiv:2508.18733, ACM
MM 2025). MIT-licensed code; dataset hosted on Google Drive, built from
DeepCAD's CAD models (`github.com/ChrisWu1997/DeepCAD`, MIT-licensed,
sourced from Onshape's public/free-tier models).

**Download from Drawing2CAD, not from DeepCAD directly.** DeepCAD provides
CAD construction sequences only — no drawings at all, since DeepCAD is a
text/sequence-to-CAD project, not a drawing-to-CAD one. Drawing2CAD's
contribution is generating matching engineering drawings from DeepCAD's CAD
models via their own FreeCAD-based pipeline, and their downloads are the
**paired** drawing+CAD data needed here. Files to get:

- `svg_raw.zip` — engineering drawings in SVG (raster-equivalent), four
  views per model (`Front`, `Top`, `Right`, `FrontTopRight`). Convert to
  PNG with the one-line CairoSVG snippet in their README. **This is the
  VLM's input.**
- `cad_vec.zip` — vectorized CAD construction sequences (`.h5`), the same
  underlying data as DeepCAD's, repackaged into Drawing2CAD's pipeline
  format and aligned to their split file. **This is the source ground
  truth — see "Materializing ground-truth STEP files," below.**
- `train_val_test_split.json` — for consistent sampling, and optionally for
  comparing against Drawing2CAD's own published numbers on the same split.
- `svg_vec.zip` — vectorized drawing-command sequences. **Optional.** Only
  needed if testing whether vector-style input helps VLM accuracy versus
  raster; not required for the core pipeline, since real-world scanned
  input (your actual target) is raster by nature and has no vector
  equivalent.

## Materializing ground-truth STEP files

The dataset stores CAD models as **construction sequences** (the ordered
sketch/extrude/fillet/etc. operations and parameters), not finished solids
— there is no `.step` file in the download. DeepCAD's own repository
includes a reconstruction script (using pythonocc, the same OpenCASCADE
bindings build123d is built on) that replays a sequence into an actual
B-rep solid and exports STEP/STL.

**Action:** run DeepCAD's reconstruction script once per sampled test case,
as a one-time local preprocessing step, to produce `reference.step` for
each drawing in the working subset. This reference STEP is then used by the
same validation method as any user-supplied reference model — no special
casing.

**Caveat carried over:** CAD-VGDrawing's drawings are machine-generated
from existing CAD models (clean, single-part, no scan artifacts) — a
controlled first benchmark, not a stand-in for messy real legacy drawings.
Phase 5 still moves to real scans, where there is no reference STEP
available at all (see "Validation without a reference," below).

## Validation without a reference (real-world / Phase 5 use)

For a real scanned drawing with no existing CAD model, there is no
reference STEP to compare against — geometric IoU isn't available. In that
situation the tool falls back to the self-consistency check described in
earlier drafts of this plan: regenerate a plain 2D re-render from the
generated STEP, have the VLM independently re-interpret it, and compare
that reading against the original PartSpec. This is not a competing
validation method either — it's what's left once there's no answer key at
all, and it's not part of the CAD-VGDrawing benchmark (which always has a
reference).

## Design principle (architecture)

The VLM **interprets engineering intent and classifies its own certainty**;
it never touches the CAD kernel directly. A deterministic Python layer
turns its structured output — confirmed, inferred, or assumed alike — into
a complete model, so every value is a regular editable parameter.

```
drawing (raster; vector optional, for one specific experiment)
        │
        ▼
 Qwen3-VL (local, via LM Studio)
        │
        ▼
 PartSpec — every dimension/feature tagged:
   CONFIRMED  — explicit in the drawing
   INFERRED   — strongly derivable (symmetry, pattern, standard practice)
   ASSUMED    — model had to pick a value, drawing is silent
   AMBIGUOUS  — multiple plausible readings; one is picked to build, but
                always surfaced regardless of confidence
        │
        ▼
 schema validation + bounding-box sanity checks
        │
        ▼
 deterministic generator (build123d) — always builds a complete model
        │
        ▼
 generated.step
        │
        ├──────────────► IF a reference STEP exists (benchmark, or a
        │                 user-supplied one): IoU + mass properties +
        │                 bounding box comparison
        │
        └──────────────► ALWAYS: colored review overlay (tool use) +
                          review panel (Accept/Edit per assumed/ambiguous)
```

## Tool stack

| Function | Tool | Notes |
|---|---|---|
| Vision-language model | Qwen3-VL-30B-A3B (primary); a second local model held in reserve as an A/B fallback | Local via LM Studio. VLMs are known to struggle with small text and dense leader-line annotations — if Phase 3 numbers are poor, the open question is "is this model weak here" vs. "is this task hard for local models generally," and a second model is how that gets distinguished rather than assumed |
| CAD kernel | build123d | Parametric — edits regenerate cheaply |
| Geometry validation | OCP (BRepAlgoAPI_Common/Fuse for IoU, GProp_GProps, BRepCheck_Analyzer) | One method for both benchmark and user-supplied references |
| Ground-truth reconstruction | DeepCAD's pythonocc-based script (one-time preprocessing) | Turns CAD-VGDrawing's sequences into reference.step files |
| Image preprocessing | OpenCV | Deskew, denoise — light touch |
| Schema validation | pydantic | Validates VLM output |
| Review UI | Simple local web page | Colored model + review list |

Not in the V1 stack: FreeCAD/TechDraw, Tesseract, image vectorization as
core infrastructure, automated repair, a dataset-specific validation path.

## PartSpec schema — classification is the spine

```json
{
  "type": "fillet",
  "id": "F2",
  "radius": 3.0,
  "tier": "assumed",
  "confidence": 0.74,
  "reasoning": "Drawing does not specify a radius for this edge; assumed a common value for this feature size."
}
```

```json
{
  "type": "pocket",
  "id": "P1",
  "depth": 5.0,
  "tier": "ambiguous",
  "confidence": 0.52,
  "reasoning": "Depth callout could be read as a blind pocket or a through-cut.",
  "alternatives": [{"depth": "through"}]
}
```

Tiers:
- **Confirmed** — explicit in the drawing. Not shown in review by default.
- **Inferred** — strongly derivable from geometry/convention. Not shown by
  default; visible in an optional "show all" view.
- **Assumed** — no drawing basis; model picked a value via engineering
  convention. Always shown in review.
- **Ambiguous** — multiple plausible readings. Generator builds the most
  likely one; always surfaced regardless of confidence score.

**Decision procedure, needed before Phase 3 prompting (currently
underspecified, and the boundary a VLM is most likely to blur):**
"Assumed" means the drawing gives *no* basis for the value at all — the
model is filling a true gap with convention. "Ambiguous" means the drawing
*does* give a basis, but that basis supports more than one reading (e.g. a
depth callout that could plausibly mean "blind, 5mm" or "through"). If the
VLM can point to specific drawing content that supports two or more
distinct values, it's ambiguous; if it can point to nothing at all, it's
assumed. Write this distinction directly into the classification prompt,
with one example of each, rather than leaving it to the model's judgment —
otherwise the tier statistics will mostly reflect prompt noise rather than
a real distinction.

## Default policy for assumed values

Assumed values should reflect engineering convention, not an arbitrary
guess: a common fillet/chamfer size relative to the adjacent feature, an
assumed symmetric position on an otherwise-symmetric pattern, a standard
general-tolerance class (e.g. ISO 2768) where none is given, the nearest
labeled edge as an implicit dimension reference. Where CAD-VGDrawing's
richer ground truth is available, this policy's accuracy is directly
testable and gets refined from that data.

## Visual overlay

Confirmed features neutral, inferred yellow, assumed/ambiguous red/orange:

```
2 items need attention

🔴 H3 — Hole diameter
   AI estimate: Ø10   Confidence: 58%
   [Accept] [Edit]

🟠 P1 — Pocket depth
   AI estimate: 5 mm   Alternative: through
   [Accept] [Edit]
```

Accepting or editing updates PartSpec and triggers a deterministic
regeneration. True regeneration of only the affected feature, without
rebuilding everything downstream of it in the feature tree, is a build123d
implementation detail to validate rather than assume — a sequential
feature tree (base → hole → pocket → fillet) can require rebuilding
everything after an edited step. Start by guaranteeing "editing a
parameter deterministically regenerates a correct model," and treat true
partial/incremental regeneration as an optimization to confirm during
Phase 1/4, not a claim to make in advance.

## Phases

### Phase 0 — Acquire CAD-VGDrawing, materialize reference STEPs
Download `svg_raw.zip`, `cad_vec.zip`, `train_val_test_split.json` from
Drawing2CAD. Run DeepCAD's reconstruction script once per sampled case to
produce `reference.step` for each; DeepCAD's sequence→solid reconstruction
isn't always perfect (fillets, patterns, and other edge cases can fail or
look wrong), so inspect the resulting STEPs and drop any that fail
`BRepCheck_Analyzer` or look visibly wrong before treating them as ground
truth.

Sample a few hundred cases across a difficulty spread. "A few hundred" is
a starting point sized for a first honest read of the pipeline's behavior
on a local machine within a reasonable compute budget (IoU on B-rep
booleans, plus VLM inference per case, are not free) — not a number backed
by a formal confidence-interval calculation. If the resulting variance
across cases is high, that's itself a finding, and the sample can be grown
later rather than needing to be justified up front.

**Critical constraint for Phase 1, decided now rather than discovered
later:** stratify the sample so a clearly-labeled subset uses *only* V1's
feature vocabulary (rectangular plate/block base, through-holes — see
"Feature vocabulary progression," below). Ground-truth sequences containing
fillets, patterns, or pockets your schema doesn't yet represent cannot
round-trip to IoU ≈ 1.0 in Phase 1 regardless of whether the schema and
generator are correct, and without this split you can't tell a schema bug
from a vocabulary mismatch. The V1.0-only subset is what Phase 1's
round-trip check runs against; the full stratified sample is used from
Phase 3 onward as the vocabulary grows.

**Output:** a local subset of `{drawing.png, reference.step}` pairs,
explicitly split by which feature-vocabulary tier each case requires.

### Phase 1 — Deterministic CAD generator (PartSpec → build123d)
Build the generator functions. Validate by round-tripping, **using only the
V1.0-vocabulary subset from Phase 0**: decode ground-truth sequences into
your PartSpec schema, generate a STEP from that PartSpec, and IoU-check it
against the matching `reference.step`. This should score at or near 1.0 by
construction — if it doesn't, the schema or generator has a real bug,
since vocabulary mismatch has already been ruled out by the sampling
constraint above. This is the keystone check: don't proceed to Phase 3
until it holds on the full V1.0 subset.

**Output:** `partspec_to_cad.py`, validated against Phase 0's V1.0-only
references.

### Phase 2 — Geometric validation
1. Schema validation on PartSpec
2. Bounding-box sanity checks
3. Solid validity (`BRepCheck_Analyzer`)
4. **IoU + mass properties against a reference STEP**, when one is
   available — the one unified correctness check

**Output:** `validate.py`, taking `generated.step` and an optional
`reference.step`.

### Phase 3 — VLM reading + classification + first benchmark pass
Bring in Qwen3-VL. Prompt it to emit PartSpec with tier classification.
Run over the Phase 0 sample, generate STEP, validate via IoU against each
case's `reference.step`.

Report:
- Overall IoU distribution (mean, median, worst cases)
- What fraction of values needed no guess (confirmed/inferred) vs. a guess
  (assumed/ambiguous) — from PartSpec's own tier tags
- Where CAD-VGDrawing's richer ground truth allows it: tier-level accuracy
  (how close were assumed/ambiguous guesses to the real parameter values)
- Optionally: raster vs. vector (`svg_vec`) input, A/B'd on IoU

**Output:** `vlm_reader.py`, plus a real benchmark report using the same
validation method the tool uses for everyone.

### Phase 4 — Visual overlay + review panel
Tier-colored model rendering, review list with Accept/Edit, partial
regeneration on edit.

**Output:** the user-facing tool.

### Phase 5 — Real scans (no reference STEP available)
Move to real scanned legacy drawings. No reference STEP exists here, so
IoU-based validation isn't available — this phase uses the self-consistency
fallback (re-render, re-interpret, compare) described above instead, and is
otherwise qualitative: does the tool produce something useful, does the
default-assumption policy need adjusting for real drafting conventions and
scan artifacts.

**Output:** a tool exercised on real drawings; refined default policy and
classification prompt; a clear note in the README about the gap between
CAD-VGDrawing benchmark numbers (with reference STEPs) and real-scan
performance (without them).

**Explicit reporting rule:** Phase 5's self-consistency results are not
comparable to Phase 3's IoU-based numbers and must not be presented in the
same results table. A model that consistently misreads a dimension the
same way twice will pass its own self-consistency check while still being
wrong — this fallback measures internal consistency, not correctness, and
should be labeled as such wherever it's reported.

## Feature vocabulary progression

- **V1.0** — rectangular plate/block base; through-holes; orthographic
  views
- **V1.1** — fillets, chamfers
- **V1.2** — patterns
- **V1.3** — pockets, slots
- **V1.4** — counterbores, basic tolerances
- **V2** — sheet metal, section views, isometric-supplementary views

## Cut or deferred, and why

- **A dataset-specific parameter-diff validation path** — dropped in favor
  of one universal STEP/IoU-based method, usable by anyone with any
  reference model, not just CAD-VGDrawing. The tier-level accuracy bonus
  reporting on CAD-VGDrawing reuses the tool's own PartSpec classification
  rather than introducing a second checker.
- **Building a synthetic dataset generator** — superseded by CAD-VGDrawing.
- **FreeCAD/TechDraw** — not needed for the reference-based validation
  path; only the no-reference fallback (Phase 5) needs any re-rendering,
  and that uses build123d/OCP's own projection, not TechDraw.
- **Automated repair** — the engineer reviews and corrects.
- **Tesseract, custom vectorization pipeline** — CAD-VGDrawing's paired
  raster/vector versions allow testing this on real data without building
  a custom vectorizer first.
- **Refuse-if-unsure as the operating principle** — rejected; see "Core
  principle," above.

  ## ===================================================

  What your data tells us

You have:

630,364 SVG files
157,591 .npy vector files
4 SVG views per CAD object in the example:
Front
Top
Right
FrontTopRight

What we now know
There are 157,591 CAD objects.
Each object has exactly 4 SVG views:
Front
Top
Right
FrontTopRight
There are no <text> elements in the 0090 subset.
Your search also found no <circle>, <polygon>, or <polyline> there.
The richer candidate 00905686_Front.svg has 10 <path> elements, but that still does not mean it contains dimensions/annotations.
