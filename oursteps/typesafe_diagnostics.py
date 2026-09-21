"""Optional, read-only TypeSafe judgments over existing OurSteps telemetry."""
from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class TypeSafeDiagnostics:
    """Semantic diagnostics only. Never mutates crawler/archive state."""

    def __init__(self) -> None:
        self.enabled = _env_bool("OURSTEPS_TYPESAFE_ENABLED", False)
        self.api_key = os.getenv("TYPESAFE_API_KEY", "").strip()
        self.base_url = os.getenv(
            "TYPESAFE_API_BASE", "https://api.typesafe.ai/v1"
        ).rstrip("/")
        self.model = os.getenv("OURSTEPS_TYPESAFE_MODEL", "jev-latest").strip()

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.api_key)

    def evaluate(self, state: dict, *, opener=urlopen) -> dict:
        if not self.available:
            return {"status": "disabled", "model": self.model, "answers": {}}

        questions = {
            "transient_upstream_noise": {
                "type": "noul",
                "instructions": (
                    "Does the evidence most strongly fit a transient upstream/site "
                    "condition such as Busy or timeout, rather than a local code defect?"
                ),
                "criteria": {
                    "true": "Evidence is dominated by transient remote-site behavior.",
                    "false": "Evidence points elsewhere or is insufficient.",
                },
            },
            "local_parser_or_layout_issue": {
                "type": "noul",
                "instructions": (
                    "Does the evidence suggest a local parser assumption or upstream "
                    "HTML/layout change needs engineering review?"
                ),
                "criteria": {
                    "true": "Parse/layout mismatch is materially supported.",
                    "false": "No meaningful parser/layout evidence is present.",
                },
            },
            "auth_or_challenge_issue": {
                "type": "noul",
                "instructions": (
                    "Does the evidence suggest authentication, permission, CAPTCHA, "
                    "or anti-bot challenge is the current primary problem?"
                ),
                "criteria": {
                    "true": "Current evidence materially supports auth/challenge trouble.",
                    "false": "Current evidence does not support auth/challenge as primary.",
                },
            },
            "observe_without_behavior_change": {
                "type": "noul",
                "instructions": (
                    "Given this diagnostic evidence only, is observation/retry under "
                    "the existing safeguards more appropriate than changing crawler behavior?"
                ),
                "criteria": {
                    "true": "Keep current behavior and observe/retry; no tuning is justified.",
                    "false": "Evidence supports engineering review before ordinary observation.",
                },
            },
        }
        payload = {"state": state, "model": self.model, "questions": questions}
        request = Request(
            f"{self.base_url}/systemone",
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with opener(request, timeout=20) as response:
            raw = json.loads(response.read().decode("utf-8"))
        answers = {}
        for key, answer in (raw.get("answers") or {}).items():
            if (answer or {}).get("type") == "noul":
                value = float(answer["noul"])
                if 0.0 <= value <= 1.0:
                    answers[key] = value
        return {
            "status": "ok",
            "model": raw.get("model", self.model),
            "answers": answers,
            "usage": raw.get("usage") or {},
        }
