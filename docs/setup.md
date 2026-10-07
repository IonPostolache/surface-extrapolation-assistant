# Setup

## 1. Install and extract FreeCAD

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

This produces `~/.FreeCAD/squashfs-root/`, which contains FreeCAD's
binaries, libraries, and its bundled Python interpreter.

## 2. Create the virtual environment with FreeCAD's Python

FreeCAD 1.1 AppImages are built against Python 3.11. If you create the venv
with a newer interpreter (3.12+), importing FreeCAD will fail with:

```text
ImportError: libFreeCADBase.so: undefined symbol: _Py_PackageContext
```

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

## 3. Configure the FreeCAD library path

Edit `config.yaml` and set the path to FreeCAD's library directory:

```yaml
freecad:
  lib_path: "/home/<you>/.FreeCAD/squashfs-root/usr/lib"
```

Or set the `FREECAD_LIB_PATH` environment variable.

## 4. Install the project

```bash
pip install -e ".[dev]"
```

## 5. Verify the setup

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

```text
FreeCAD version: 1 1
Faces: 6
```

## 6. Optional: local LLM

The geometry pipeline runs without an LLM. To enable failure diagnosis, set
`LLM_API_KEY` in `.env`:

```text
LLM_API_KEY=lm-studio
```

Everything else (endpoint, model, timeout, temperature) is read from
`config.yaml`. Load the model in LM Studio (or Ollama) before running with
`--ai`. Ollama works too — point the `.env` values at
`http://localhost:11434/v1` and pull a compatible model.