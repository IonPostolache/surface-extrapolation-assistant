"""Local LLM client for failure diagnostics.

Uses LM Studio's OpenAI-compatible API with structured JSON output.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import requests


# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------

def _load_dotenv(path: Path | None = None) -> None:
    if path is None:
        path = Path(__file__).resolve().parents[2] / ".env"
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


_load_dotenv()


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class DiagnosisStatus(str, Enum):
    OK = "ok"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_ERROR = "llm_error"
    INVALID_RESPONSE = "invalid_response"


ALLOWED_ACTIONS = {
    "retry_same_parameters",
    "reduce_extension_distance",
    "increase_extension_distance",
    "skip_face",
    "manual_review",
}


@dataclass
class LLMDiagnosis:
    status: DiagnosisStatus
    diagnosis: str = ""
    confidence: float = 0.0
    recommended_actions: list[str] = field(default_factory=list)
    raw_response: str | None = None
    error_message: str | None = None

    def short(self) -> str:
        if self.status != DiagnosisStatus.OK:
            return f"LLM {self.status.value}: {self.error_message or 'no detail'}"
        actions = ", ".join(self.recommended_actions) or "none"
        return (
            f"LLM diagnosis ({self.confidence:.2f}): {self.diagnosis} "
            f"→ actions=[{actions}]"
        )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _config() -> dict[str, Any]:
    return {
        "base_url": os.environ.get("LLM_BASE_URL", "http://localhost:1234/v1"),
        "model": os.environ.get("LLM_MODEL", "qwen3-coder-30b-a3b-instruct"),
        "api_key": os.environ.get("LLM_API_KEY", "lm-studio"),
        "timeout": int(os.environ.get("LLM_TIMEOUT", "120")),
        "temperature": float(os.environ.get("LLM_TEMPERATURE", "0.2")),
    }


# ---------------------------------------------------------------------------
# JSON schema for the response
# ---------------------------------------------------------------------------

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {
            "type": "string",
            "description": "One or two sentences explaining the geometric cause of the failure."
        },
        "confidence": {
            "type": "number",
            "description": "Confidence between 0.0 and 1.0.",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "recommended_actions": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": list(ALLOWED_ACTIONS),
            },
            "description": "One or more recovery actions from the allowed list.",
            "minItems": 1,
        },
    },
    "required": ["diagnosis", "confidence", "recommended_actions"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def is_available() -> bool:
    cfg = _config()
    url = cfg["base_url"].rstrip("/") + "/models"
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a CAD geometry analysis assistant.

You will receive a JSON object describing a failure from a FreeCAD-based
surface extrapolation pipeline.

Key signals:
    - "shell_is_valid": false means the fused shell self-intersects.
      This almost always indicates extensions are OVER-extending or
      extending in the WRONG DIRECTION, not that they are too short.
    - "ratio_suspicious": true on a face means the extension distance
      exceeded the face's own extent, indicating a likely wrong direction.
    - "open_edge_count" counts edges in the shell not shared by two faces.

Your job:
    1. Explain, in one or two sentences, the most likely geometric cause
       of the failure.
    2. Recommend ONE OR MORE recovery actions. You MUST use EXACTLY
       these strings:
         - "retry_same_parameters"
         - "reduce_extension_distance"
         - "increase_extension_distance"
         - "skip_face"
         - "manual_review"
    3. Assign a confidence between 0.0 and 1.0.

Respond ONLY with valid JSON.
"""


def _build_diagnostic_payload(join_result, extrapolation_results):
    faces = []
    for r in extrapolation_results:
        faces.append({
            "face_index": r.face_index,
            "status": r.status.value,
            "requested_mm": r.requested_mm,
            "achieved_mm": r.achieved_mm,
            "percent_error": r.achieved_percent_error,
            "ratio_used": r.ratio_used,             # NEW
            "ratio_suspicious": (                     # NEW
                r.ratio_used is not None and r.ratio_used > 1.0
            ),
            "error": r.error_message,
        })

    payload = {"join": None, "faces": faces}

    if join_result is not None:
        shell_valid = None
        if join_result.sewed_shell is not None:
            try:
                shell_valid = join_result.sewed_shell.isValid()
            except Exception:
                pass

        payload["join"] = {
            "status": join_result.status.value,
            "method_used": join_result.method_used,
            "input_face_count": join_result.input_face_count,
            "open_edge_count": join_result.open_edge_count,
            "tolerance_used_mm": join_result.tolerance_used_mm,
            "shell_is_valid": shell_valid,          # NEW — key signal
            "error": join_result.error_message,
        }

    return payload


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------
def _normalize_action(raw: str) -> str | None:
    s = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    # Strip anything that isn't a-z0-9_
    s = "".join(ch for ch in s if ch.isalnum() or ch == "_")
    if s in ALLOWED_ACTIONS:
        return s
    # Fuzzy fallback for common variants
    aliases = {
        "reduce_extension": "reduce_extension_distance",
        "increase_extension": "increase_extension_distance",
        "reduce_distance": "reduce_extension_distance",
        "increase_distance": "increase_extension_distance",
        "skip": "skip_face",
        "retry": "retry_same_parameters",
        "manual": "manual_review",
    }
    for key, canonical in aliases.items():
        if key in s:
            return canonical
    return None


def _parse_response(raw: str) -> tuple[str, float, list[str]]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

    data = json.loads(text)

    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")

    diagnosis = str(data.get("diagnosis", "")).strip()
    if not diagnosis:
        raise ValueError("missing 'diagnosis'")

    try:
        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.0

    actions = []

    actions_raw = data.get("recommended_actions", [])
    if not isinstance(actions_raw, list):
        raise ValueError("'recommended_actions' must be a list")

    for a in actions_raw:
        normalized = _normalize_action(a)
        if normalized:
            actions.append(normalized)
        else:
            print(f"[llm debug] rejected action: {a!r}")

    # DEBUG: see what the model returned
    print(f"[llm debug] raw actions: {actions_raw}")
    print(f"[llm debug] allowed: {ALLOWED_ACTIONS}")

    # actions = [a for a in actions_raw if a in ALLOWED_ACTIONS]

    return diagnosis, confidence, actions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def diagnose(
    join_result,
    extrapolation_results: list,
    *,
    verbose: bool = False,
) -> LLMDiagnosis:
    """Ask the local LLM to diagnose a join failure."""
    payload = _build_diagnostic_payload(join_result, extrapolation_results)
    user_message = (
        "Diagnose this pipeline failure and recommend recovery actions:\n\n"
        + json.dumps(payload, indent=2)
    )

    cfg = _config()
    url = cfg["base_url"].rstrip("/") + "/chat/completions"

    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": cfg["temperature"],
        # LM Studio requires json_schema (not json_object)
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "diagnosis_response",
                "strict": True,
                "schema": RESPONSE_SCHEMA,
            },
        },
    }

    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=cfg["timeout"],
        )
    except requests.exceptions.ConnectionError as exc:
        return LLMDiagnosis(
            status=DiagnosisStatus.LLM_UNAVAILABLE,
            error_message=f"cannot reach {url}: {exc}",
        )
    except requests.exceptions.Timeout:
        return LLMDiagnosis(
            status=DiagnosisStatus.LLM_UNAVAILABLE,
            error_message=f"timeout after {cfg['timeout']}s",
        )
    except Exception as exc:  # noqa: BLE001
        return LLMDiagnosis(
            status=DiagnosisStatus.LLM_ERROR,
            error_message=str(exc),
        )

    if r.status_code != 200:
        return LLMDiagnosis(
            status=DiagnosisStatus.LLM_ERROR,
            error_message=f"HTTP {r.status_code}: {r.text[:300]}",
        )

    try:
        response_json = r.json()
        raw = response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        return LLMDiagnosis(
            status=DiagnosisStatus.INVALID_RESPONSE,
            error_message=f"unexpected response shape: {exc}",
            raw_response=r.text[:500],
        )

    if verbose:
        print("=== raw LLM response ===")
        print(raw)
        print("========================")

    try:
        diagnosis, confidence, actions = _parse_response(raw)
    except Exception as exc:  # noqa: BLE001
        return LLMDiagnosis(
            status=DiagnosisStatus.INVALID_RESPONSE,
            error_message=str(exc),
            raw_response=raw,
        )

    return LLMDiagnosis(
        status=DiagnosisStatus.OK,
        diagnosis=diagnosis,
        confidence=confidence,
        recommended_actions=actions,
        raw_response=raw,
    )