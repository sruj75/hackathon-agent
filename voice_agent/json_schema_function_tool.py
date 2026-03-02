"""
FunctionTool variant that emits JSON-schema declarations for Gemini Live.

This avoids legacy schema fields that can cause websocket setup rejection
on certain Live API versions.
"""
from __future__ import annotations

from google.adk.tools._function_tool_declarations import (
    build_function_declaration_with_json_schema,
)
from google.adk.tools.function_tool import FunctionTool
from google.genai import types
from typing_extensions import override


class JsonSchemaFunctionTool(FunctionTool):
    @override
    def _get_declaration(self) -> types.FunctionDeclaration | None:
        return build_function_declaration_with_json_schema(
            func=self.func,
            ignore_params=self._ignore_params,
        )

