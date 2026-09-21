import io
import json
import os
import unittest
from unittest.mock import patch

from oursteps.typesafe_diagnostics import TypeSafeDiagnostics


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class TypeSafeDiagnosticsTests(unittest.TestCase):
    def test_disabled_is_noop(self):
        with patch.dict(os.environ, {}, clear=True):
            result = TypeSafeDiagnostics().evaluate({"x": 1})
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["answers"], {})

    def test_enabled_returns_only_valid_noul_probabilities(self):
        env = {
            "OURSTEPS_TYPESAFE_ENABLED": "true",
            "TYPESAFE_API_KEY": "secret",
        }
        captured = {}

        def opener(request, timeout):
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data)
            return FakeResponse({
                "model": "jev-test",
                "answers": {
                    "transient_upstream_noise": {"type": "noul", "noul": 0.91},
                    "local_parser_or_layout_issue": {"type": "noul", "noul": 0.08},
                },
                "usage": {"input_tokens": 10, "output_tokens": 2},
            })

        with patch.dict(os.environ, env, clear=True):
            result = TypeSafeDiagnostics().evaluate({"error": "site_busy"}, opener=opener)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["answers"]["transient_upstream_noise"], 0.91)
        self.assertEqual(captured["payload"]["model"], "jev-latest")
        self.assertEqual(len(captured["payload"]["questions"]), 4)
        self.assertEqual(captured["timeout"], 20)


if __name__ == "__main__":
    unittest.main()
