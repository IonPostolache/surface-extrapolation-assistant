# AGENTS.md

Project-specific notes for AI coding assistants working on this repo.

## Critical setup

- **Use Python 3.11** — FreeCAD's AppImage is compiled against 3.11 ABI.
  A venv created with Python 3.12 will fail with:
  `ImportError: libFreeCADBase.so: undefined symbol: _Py_PackageContext`
- **Extract the FreeCAD AppImage** to `~/.FreeCAD/squashfs-root/`:
  ```bash
  mkdir -p ~/.FreeCAD && cd ~/.FreeCAD
  ~/Downloads/FreeCAD_*.AppImage --appimage-extract
  ```

**Create the venv with FreeCAD's bundled Python:

```bash
~/.FreeCAD/squashfs-root/usr/bin/python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```
FreeCAD library path must be set in config.yaml or via
FREECAD_LIB_PATH. freecad_setup.py reads it at import time.

## Verification command
```bash
python -c "
from surface_assistant import freecad_setup
import FreeCAD, Part
box = Part.makeBox(10, 10, 10)
print('FreeCAD version:', FreeCAD.Version()[0], FreeCAD.Version()[1])
print('Faces:', len(box.Faces))
"
```
Expected: FreeCAD version: 1 1 and Faces: 6.

## Input contract
The pipeline takes one STEP file containing the surface.

No boundary-curve file is needed — the outer boundary is detected
topologically.

The surface must have no holes. Fill interior holes in the source
CAD before running.

## CLI usage
```bash
# Folder with exactly one STEP file
surface-assistant run examples/test3 --distance 5 -o out.FCStd

# Inspect a surface without extending
surface-assistant inspect model.step
```
Flags:
- `--distance` / `-d` — extension distance in mm
- `--tolerance` / `-t` — tolerance percentage (default 2.0)
- `--strategy` — `per_face` (default) or `whole_surface`
- `--ai` — enable local-LLM diagnostics on failure or DEFERRED faces
- `--ai-verbose` — print the raw AI response
- `--output` / `-o` — save the result to `.FCStd`; also renders
  `<name>_grid.png` beside it


## Local LLM (optional)
The geometry pipeline runs without any LLM. To enable diagnosis:

LM Studio (default): create .env with

```bash
LLM_API_KEY=lm-studio
```
Load the model in LM Studio before running with --ai.

Ollama works too — change the .env values to point at
http://localhost:11434/v1 and pull a compatible model.

## Things NOT to do
Do not use Python > 3.11 in the venv.

Do not run pip install freecad — FreeCAD is not pip-installable.

Do not import FreeCAD before from surface_assistant import freecad_setup.
The bootstrap module adds FreeCAD's bundled site-packages to sys.path.

Do not assume the input STEP file has a boundary curve or is hole-free
without checking.

## Architecture notes
step_io.py — STEP loading and folder resolution

topology.py — outer-boundary detection, face fingerprinting

extrapolation.py — ribbon / ruled extension per face

io.py — trim by outer boundary, save to .FCStd, render PNGs

join.py — fuse / sew and open-edge reporting

llm.py — local LLM client (LM Studio / Ollama), JSON-schema validated

batch.py — orchestrator; produces a BatchReport

cli.py — typer CLI

## Strategies

- `--strategy per_face` (default) — extend each boundary face
  independently; trim each back to the outer boundary.
- `--strategy whole_surface` — build a ribbon along the whole outer
  boundary. Faster to express, coarser result on concave boundaries.


## Output

When `--output` / `-o` is given, the pipeline produces:
- `<name>.FCStd` — openable in FreeCAD.
- `<name>_grid.png` — a 6-view grid (ISO, FRONT, TOP, LEFT, BACK,
  BOTTOM) in a single image.

There is no `--screenshots` flag. The grid is always rendered.


## Known limitations to keep in mind
Large BSpline faces that wrap the whole part are reported as DEFERRED —
they can be extended but not reliably trimmed. Do not "fix" this by
retrying the trim; it is a documented FreeCAD kernel limitation.

The ribbon extension is an approximation of CATIA's "extrapolate in
curvature"; do not claim exact CATIA equivalence.