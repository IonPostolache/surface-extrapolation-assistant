# AGENTS.md

## Critical Setup
- Use Python 3.11 (FreeCAD ABI compatibility)
- Extract FreeCAD AppImage to `~/.FreeCAD`
- Set `FREECAD_LIB_PATH` in `.env` or `config.yaml`

## Verification Command
```bash
python -c "from surface_assistant import freecad_setup; import FreeCAD; ..." # See README for full snippet
```

## CLI Usage
- Run: `surface-assistant run model.step --boundary boundary.step --extension 100`
- Required flags: `--boundary`, `--extension` (mm)
- LLM only needed for failure diagnosis (`ollama pull qwen2.5-coder:7b`)

## Notes
- Do NOT use Python >3.11
- Verify FreeCAD version before running tests