"""
FunctionTool variant that keeps JSON-schema declarations for Gemini Live.

This wraps ADK's declaration builder so we keep its Gemini-specific safeguards
(notably stripping response schema fields that can trigger INVALID_ARGUMENT).
"""
from __future__ import annotations

from google.adk.tools._function_tool_declarations import (
    build_function_declaration_with_json_schema,
)
from google.adk.tools.function_tool import FunctionTool
from google.adk.utils.variant_utils import GoogleLLMVariant
from google.genai import types
from typing_extensions import override


class JsonSchemaFunctionTool(FunctionTool):
    @override
    def _get_declaration(self) -> types.FunctionDeclaration | None:
        declaration = build_function_declaration_with_json_schema(
            func=self.func,
            ignore_params=self._ignore_params,
        )
        # Gemini Live rejects response JSON schema in some SDK/backend combos.
        # ADK strips this for Gemini in its default builder; mirror that behavior
        # while preserving JSON-schema parameters.
        if self._api_variant != GoogleLLMVariant.VERTEX_AI:
            declaration.response_json_schema = None
        return types.FunctionDeclaration.model_validate(declaration)
