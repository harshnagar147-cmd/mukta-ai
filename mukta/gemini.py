"""Thin wrapper around the Gemini API.

Isolates every SDK detail behind two methods so the UI never imports the
Google SDK directly, and so the app degrades to a clearly-labelled stub when
no key is configured (useful when demoing offline).

Uses the current ``google-genai`` SDK. The older ``google-generativeai``
package this project first targeted reached end of life and no longer receives
fixes, so the call site was moved rather than pinned to a dead dependency.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import config

try:
    from google import genai
    from google.genai import types as genai_types
    SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    genai = None
    genai_types = None
    SDK_AVAILABLE = False


class GeminiError(RuntimeError):
    """Raised when the model cannot be reached or returns nothing usable."""


@dataclass
class GeminiClient:
    api_key: str | None = None
    model_name: str = config.DEFAULT_MODEL
    max_retries: int = 3
    _client: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.enabled:
            self._client = genai.Client(api_key=self.api_key)

    @property
    def enabled(self) -> bool:
        return bool(SDK_AVAILABLE and self.api_key)

    # --- Core call ---------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> str:
        """Return the model's text output, retrying on transient failures."""
        if not self.enabled:
            raise GeminiError(
                "Gemini is not configured. Add an API key in the sidebar "
                "(or set GOOGLE_API_KEY) to generate live responses."
            )

        gen_config = genai_types.GenerateContentConfig(
            temperature=config.GENERATION_TEMPERATURE if temperature is None else temperature,
            max_output_tokens=config.MAX_OUTPUT_TOKENS,
            # Constrain the decoder to JSON rather than relying on the prompt
            # asking nicely and then repairing whatever comes back.
            response_mime_type="application/json" if json_mode else None,
        )

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=gen_config,
                )
                text = (response.text or "").strip()
                if text:
                    return text
                last_error = GeminiError(_describe_empty(response))
            except Exception as exc:  # SDK raises a wide range of transport errors
                last_error = exc
                if _is_permanent(exc):
                    # A retired model or a bad key will not fix itself on retry.
                    raise GeminiError(_explain(exc)) from exc
            if attempt < self.max_retries - 1:
                time.sleep(1.5 * (2 ** attempt))  # 1.5s, 3s

        raise GeminiError(f"Gemini call failed after {self.max_retries} attempts: {last_error}")

    # --- JSON helper -------------------------------------------------------

    def generate_json(self, prompt: str, *, temperature: float | None = None) -> Any:
        """Call the model and parse its output as JSON, tolerating code fences."""
        raw = self.generate(
            prompt,
            temperature=config.ANALYSIS_TEMPERATURE if temperature is None else temperature,
            json_mode=True,
        )
        return parse_json_response(raw)


def _describe_empty(response) -> str:
    """Say why a response carried no text, instead of just 'empty'.

    An empty body almost always means the budget ran out (often on reasoning
    tokens) or a safety filter fired. Both are actionable; "empty" is not.
    """
    detail = []
    try:
        candidate = (response.candidates or [None])[0]
        reason = getattr(candidate, "finish_reason", None)
        if reason is not None:
            name = getattr(reason, "name", str(reason))
            detail.append(f"finish_reason={name}")
            if "MAX_TOKEN" in name.upper():
                detail.append(
                    f"the {config.MAX_OUTPUT_TOKENS}-token budget was consumed before "
                    "any answer was emitted - raise MAX_OUTPUT_TOKENS in config.py"
                )
            elif "SAFETY" in name.upper() or "RECITATION" in name.upper():
                detail.append("the response was blocked by a content filter")
    except Exception:  # diagnostics must never mask the original failure
        pass
    try:
        usage = response.usage_metadata
        if usage is not None:
            detail.append(
                f"tokens: prompt={getattr(usage, 'prompt_token_count', '?')}, "
                f"thoughts={getattr(usage, 'thoughts_token_count', '?')}, "
                f"output={getattr(usage, 'candidates_token_count', '?')}"
            )
    except Exception:
        pass
    return "Model returned an empty response. " + ("; ".join(detail) if detail else "")


# --- Error classification --------------------------------------------------

# Config problems, not transport hiccups: retrying these only adds delay before
# showing the same failure.
_PERMANENT_MARKERS = (
    "NOT_FOUND",
    "INVALID_ARGUMENT",
    "PERMISSION_DENIED",
    "UNAUTHENTICATED",
    "API_KEY_INVALID",
    "API key not valid",
)


def _is_permanent(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in _PERMANENT_MARKERS)


def _explain(exc: Exception) -> str:
    """Turn a permanent API failure into something an account manager can act on."""
    text = str(exc)
    if "no longer available" in text or "NOT_FOUND" in text:
        return (
            f"The model is not available: {text} "
            f"Set a current model name in the sidebar (default is {config.DEFAULT_MODEL})."
        )
    if "API key not valid" in text or "API_KEY_INVALID" in text or "UNAUTHENTICATED" in text:
        return (
            "The Gemini API key was rejected. Check the key in the sidebar, or "
            "create a new one at aistudio.google.com/app/apikey."
        )
    if "PERMISSION_DENIED" in text:
        return f"This key is not permitted to call that model: {text}"
    return text


def parse_json_response(raw: str) -> Any:
    """Strip markdown fences and parse. Raises GeminiError on unparseable output."""
    cleaned = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        return _unwrap(json.loads(cleaned))
    except json.JSONDecodeError:
        # Last resort: grab the outermost array or object in the text.
        match = re.search(r"(\[.*\]|\{.*\})", cleaned, re.DOTALL)
        if match:
            try:
                return _unwrap(json.loads(match.group(1)))
            except json.JSONDecodeError:
                pass
    raise GeminiError(f"Could not parse model output as JSON:\n{raw[:400]}")


def _unwrap(parsed: Any) -> Any:
    """Return the array a caller expects, even if the model wrapped it.

    Models asked for a bare array will sometimes return {"results": [...]} or
    {"feedback": [...]}. Unwrapping here turns what used to be a silent no-op -
    a dict where a list was expected, so nothing matched and nothing was said -
    back into working output.
    """
    if isinstance(parsed, dict):
        lists = [v for v in parsed.values() if isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
    return parsed
