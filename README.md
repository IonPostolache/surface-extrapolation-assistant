# Surface Extrapolation Assistant for FreeCAD

Automated boundary-face extrapolation for stamping die-face preparation, built on FreeCAD's Surface Workbench, with a local LLM used only for diagnosing and suggesting recovery when the deterministic geometry pipeline fails.

## The problem

Extending a complex imported surface (e.g. an automotive die-face patch) by a fixed distance along its outer boundary, in a tool like CATIA V5, typically means: extract each boundary face → untrim → extrapolate → split → join → repeat, per face. On a model with many faces this is slow and the failure cases are the most time-consuming part.

FreeCAD exposes `Surface::Extend`, but it operates on one face at a time, its extension parameters are **ratios of the face's existing UV extent, not millimetres**, and there is no built-in batch traversal, failure handling, or recovery logic.

## What this does

1. Imports a STEP surface and identifies all faces touching a user-supplied outer boundary.
2. Extrapolates each face toward a target distance in millimetres, calibrating FreeCAD's ratio-based `Surface::Extend` parameters to hit that target within tolerance.
3. Joins/sews successful extensions and reports any remaining open edges.
4. For faces that fail, builds a structured geometric diagnostic (surface type, area, edges, curvature where available, the FreeCAD error) and a diagnostic image.
5. Sends that diagnostic to a **local** LLM (Ollama or LM Studio), which returns a structured JSON diagnosis and a recommended action from a fixed allow-list.
6. Validates the suggestion against the allow-list, retries, and records what succeeded, what was recovered, and what remains unresolved.

The geometry pipeline is fully deterministic and runs with no LLM present. The LLM is consulted only on failure, and it never generates or executes arbitrary code — it selects from a small set of pre-implemented recovery actions.

## Architecture

```
                  STEP surface
                       |
                       v
            FreeCAD / Python geometry core
                       |
                       v
          Identify boundary faces (stable
          geometric fingerprint, not Face-N index)
                       |
                       v
        Calibrated extrapolation (target mm ->
             UV ratio -> Surface::Extend)
                       |
              +--------+--------+
              |                 |
           success           failure
              |                 |
              v                 v
        Join / sew        Structured diagnostics
        + open-edge              |
          report                 v
              |             Local LLM
              |            (JSON output)
              |                 |
              |                 v
              |         Recommended action
              |         (allow-listed only)
              |                 |
              |                 v
              |          Validated retry
              |                 |
              +--------+--------+
                       |
                       v
              Result + run report
```

## What the LLM does — and does not do

| Task | Component |
|---|---|
| Import STEP, headless load | FreeCAD |
| Identify boundary faces, track them stably | Python / OpenCascade |
| Calibrate and execute extrapolation | Python + FreeCAD |
| Join / sew results, report open edges | FreeCAD + Python |
| Detect and log failures | Python |
| Build structured diagnostics + image | Python |
| Diagnose *why* a face failed | Local LLM |
| Propose a recovery action (from an allow-list) | Local LLM |
| Validate and execute the recovery | Python / FreeCAD |
| Final geometry validation | Python / OpenCascade |
| Generate arbitrary CAD code | **Never — not a supported path** |

## A note on `Surface::Extend`

| Property | Type | Notes |
|---|---|---|
| `Face` | LinkSub | single face only — no native batch mode |
| `ExtendUNeg` / `ExtendUPos` | Float, ratio | fraction of existing U extent, not mm |
| `ExtendVNeg` / `ExtendVPos` | Float, ratio | fraction of existing V extent, not mm |
| `Tolerance` | Float | geometric tolerance |
| `SampleU` / `SampleV` | Integer | sampling density |

Because the extension is ratio-based, hitting a millimetre target requires measuring the face's current edge length, computing the corresponding ratio, extending, re-measuring, and correcting if the result misses tolerance. This calibration step is treated as first-class in this project, not glossed over.

## Known limitations

- Boundary detection assumes a reasonably clean, connected outer boundary curve; degenerate or multi-shell inputs may need manual override.
- OpenCascade's topological naming is unstable across recomputes — faces are tracked by geometric fingerprint (surface type, center of mass, bounding box, area) rather than by index, but pathological geometry can still produce collisions.
- `Part::Fuse` has no tolerance parameter; joining mismatched extended patches sometimes requires a sewing fallback with an explicit tolerance, and some open edges may remain unresolved without manual cleanup.
- Highly curved or tangent-discontinuous boundaries are the most likely to fail extrapolation outright, calibrated or not.
- LLM recommendations are advisory only and drawn from a fixed action allow-list; all geometry changes are validated deterministically before being kept.
- Evaluated on a handful of representative surfaces, not a large industrial dataset — benchmark numbers should be read as indicative, not comprehensive.

## Project structure

```
surface-extrapolation-assistant/
├── src/surface_assistant/
│   ├── step_io.py         # load_step
│   ├── topology.py        # boundary face detection, geometric fingerprinting
│   ├── extrapolation.py   # calibrated extrapolate_face(face, distance_mm)
│   ├── join.py            # fuse / sew, open-edge reporting
│   ├── diagnostics.py     # structured failure reports + images
│   ├── recovery.py        # allow-listed recovery actions
│   ├── llm.py              # Ollama/LM Studio client, JSON schema validation
│   └── cli.py
├── examples/
├── tests/
├── docs/architecture.md
└── README.md
```

## Quick start

```bash
# Local LLM runtime
ollama pull qwen2.5-coder:7b

# Install
git clone https://github.com/yourusername/surface-extrapolation-assistant
cd surface-extrapolation-assistant
pip install -r requirements.txt

# Run
python -m surface_assistant.cli model.step --boundary boundary.step --extension 100
```

## Evaluation approach

Three configurations compared on 4–5 representative surfaces: direct extrapolation only, deterministic fallback retries (fixed ratio steps, no LLM), and LLM-assisted recovery. Metrics: initial success rate, recovered rate, unresolved count, retries, LLM calls, wall-clock time, and achieved-vs-requested distance.

## Why this project

Demonstrates a constrained, auditable approach to AI-assisted CAD automation: deterministic engineering software owns the geometry, and a local LLM is used only for the reasoning that's genuinely hard to hand-code — recognizing and responding to novel failure patterns — while every action it recommends still passes through deterministic validation before touching the model.