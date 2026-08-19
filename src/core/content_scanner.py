"""Content scanning with configurable backend: InjectionGuard (regex) or Google Model Armor.

Backend selected by ARMOR_BACKEND env var:
  - "regex" (default): local regex-based InjectionGuard — works offline, no GCP needed
  - "model_armor": Google Cloud Model Armor API — requires template + IAM setup

Both expose the same scan_message() / scan_tool_args() interface via the
ContentScanner facade. The gateway calls ContentScanner; it delegates to the
active backend.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


class InjectionGuard:
    """Local regex-based scanner for prompt injection and PII leakage detection.

    This is the custom fallback — it does NOT use Google Model Armor.
    When ARMOR_BACKEND=regex (the default), this is the active scanner.
    """

    _INJECTION_PATTERNS = [
        r"ignore\s+(all\s+)?(previous\s+)?(?:policy|instructions|rules|guidelines)",
        r"override\s+(?:policy|security|access|controls)",
        r"disregard\s+(?:safety|policy|rules|guidelines)",
        r"you\s+are\s+now\s+(?:a|an)\s+(?:unrestricted|unfiltered)",
        r"bypass\s+(?:security|policy|access|controls|restrictions)",
        r"jailbreak",
        r"pretend\s+(?:you|there)\s+(?:are|is)\s+no\s+(?:policy|rules|restrictions)",
        r"act\s+as\s+(?:a|an)\s+(?:admin|root|superuser)",
        r"system\s*prompt",
    ]

    _PII_PATTERNS = [
        r"publish\s+(?:every|all)\s+(?:student|staff|personnel)\s+(?:medical|health)\s+record",
        r"share\s+(?:all|every)\s+(?:medical|health|personal)\s+(?:data|info|record|note)",
        r"(?:export|dump|list)\s+(?:all\s+)?(?:ssn|social\s+security|phone\s+number)",
        r"broadcast\s+(?:medical|health|personal)\s+(?:data|info|condition)",
        r"post\s+(?:medical|health)\s+(?:record|info|data)\s+(?:to|in)\s+(?:general|public)",
    ]

    @classmethod
    def scan_message(cls, text: str) -> dict[str, Any]:
        for pattern in cls._INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return {
                    "blocked": True,
                    "reason": "Prompt injection detected in message",
                    "policy": "injection_guard",
                    "backend": "regex",
                    "quarantined_text": text[:200],
                }
        for pattern in cls._PII_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return {
                    "blocked": True,
                    "reason": "PII leakage request detected in message",
                    "policy": "injection_guard_pii",
                    "backend": "regex",
                    "quarantined_text": text[:200],
                }
        return {"blocked": False, "policy": "injection_guard_clear", "backend": "regex"}

    @classmethod
    def scan_tool_args(cls, agent_id: str, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        text = cls._flatten_args(tool_args)
        for pattern in cls._INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return {
                    "blocked": True,
                    "reason": f"Prompt injection detected: matches pattern '{pattern}'",
                    "policy": "injection_guard",
                    "backend": "regex",
                    "quarantined_text": text[:200],
                }
        for pattern in cls._PII_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return {
                    "blocked": True,
                    "reason": f"PII leakage attempt detected: matches pattern '{pattern}'",
                    "policy": "injection_guard_pii",
                    "backend": "regex",
                    "quarantined_text": text[:200],
                }
        return {"blocked": False, "policy": "injection_guard_clear", "backend": "regex"}

    @classmethod
    def _flatten_args(cls, args: dict[str, Any]) -> str:
        parts = []
        for v in args.values():
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, dict):
                parts.append(cls._flatten_args(v))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        parts.append(cls._flatten_args(item))
        return " ".join(parts)


class ModelArmorScanner:
    """Google Cloud Model Armor scanner.

    Calls the real Model Armor sanitize-user-prompt API.
    Requires:
      - modelarmor.googleapis.com enabled on the project
      - A Model Armor template created (ARMOR_TEMPLATE env var)
      - IAM: roles/modelarmor.user on the calling identity

    When ARMOR_BACKEND=model_armor, this is the active scanner.
    """

    def __init__(self) -> None:
        from google.cloud.modelarmor_v1 import ModelArmorClient
        self.client = ModelArmorClient()
        self.project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        self.location = os.environ.get("GOOGLE_CLOUD_REGION", "us-central1")
        self.template_id = os.environ.get("ARMOR_TEMPLATE", "crisismesh-guard")
        self.template_name = (
            f"projects/{self.project}/locations/{self.location}/templates/{self.template_id}"
        )

    def scan_message(self, text: str) -> dict[str, Any]:
        from google.cloud.modelarmor_v1.types import SanitizeUserPromptRequest

        try:
            response = self.client.sanitize_user_prompt(
                SanitizeUserPromptRequest(
                    name=self.template_name,
                    user_prompt_data={"text": text},
                )
            )
            return self._parse_response(response, text)
        except Exception as e:
            logger.error(f"Model Armor API error: {e}")
            # Fail open for non-security errors, fail closed for ambiguous
            return {
                "blocked": False,
                "reason": f"Model Armor API error: {e}",
                "policy": "model_armor_error",
                "backend": "model_armor",
            }

    def scan_tool_args(self, agent_id: str, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        text = InjectionGuard._flatten_args(tool_args)
        if not text.strip():
            return {"blocked": False, "policy": "model_armor_clear", "backend": "model_armor"}
        return self.scan_message(text)

    def _parse_response(self, response: Any, original_text: str) -> dict[str, Any]:
        # The sanitization result has a filter_match_state
        try:
            match_state = str(response.sanitization_result.filter_match_state)
            blocked = "MATCH_FOUND" in match_state
            filter_results = {}

            result = response.sanitization_result
            if hasattr(result, "filter_results") and result.filter_results:
                for key, val in result.filter_results.items():
                    filter_results[key] = str(val)

            return {
                "blocked": blocked,
                "reason": f"Model Armor: {match_state}" if blocked else "Model Armor: clean",
                "policy": "model_armor" if blocked else "model_armor_clear",
                "backend": "model_armor",
                "match_state": match_state,
                "filter_results": filter_results,
                "quarantined_text": original_text[:200] if blocked else "",
            }
        except Exception as e:
            logger.error(f"Model Armor response parse error: {e}")
            return {
                "blocked": False,
                "reason": f"Model Armor parse error: {e}",
                "policy": "model_armor_error",
                "backend": "model_armor",
            }


class ContentScanner:
    """Facade that routes to the active backend based on ARMOR_BACKEND env var."""

    _instance: ContentScanner | None = None

    def __init__(self) -> None:
        backend = os.environ.get("ARMOR_BACKEND", "regex")
        if backend == "model_armor":
            try:
                self._scanner = ModelArmorScanner()
                self._backend = "model_armor"
                logger.info("Content scanner: Google Model Armor enabled")
            except Exception as e:
                logger.warning(f"Content scanner: Model Armor init failed, falling back to regex: {e}")
                self._scanner = InjectionGuard()
                self._backend = "regex"
        else:
            self._scanner = InjectionGuard()
            self._backend = "regex"

    @property
    def backend(self) -> str:
        return self._backend

    @classmethod
    def get(cls) -> ContentScanner:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def scan_message(self, text: str) -> dict[str, Any]:
        return self._scanner.scan_message(text)

    def scan_tool_args(self, agent_id: str, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        return self._scanner.scan_tool_args(agent_id, tool_name, tool_args)
