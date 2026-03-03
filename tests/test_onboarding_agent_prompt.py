import pytest

from voice_agent.onboarding_agent import ONBOARDING_INSTRUCTION


pytestmark = pytest.mark.regression


def test_onboarding_prompt_disallows_brand_self_identity():
    assert 'Never say "I am Intentive"' in ONBOARDING_INSTRUCTION
    assert "Do not give yourself a brand/persona identity." in ONBOARDING_INSTRUCTION


def test_onboarding_prompt_enforces_required_capture_flow():
    assert "Collect wake_time and bedtime." in ONBOARDING_INSTRUCTION
    assert "Final values MUST be HH:MM in 24-hour format before completion." in ONBOARDING_INSTRUCTION
    assert "call save_onboarding_progress(...)" in ONBOARDING_INSTRUCTION
    assert "Ask where they struggle with ADHD/executive function." in ONBOARDING_INSTRUCTION
    assert "Never call complete_onboarding until all minimum completion quality checks pass." in ONBOARDING_INSTRUCTION
    assert "Do NOT act like a general assistant" in ONBOARDING_INSTRUCTION
    assert "Do not switch into ongoing task-help mode during onboarding." in ONBOARDING_INSTRUCTION


def test_onboarding_prompt_requires_tool_based_completion():
    assert "Call get_onboarding_context() at the beginning." in ONBOARDING_INSTRUCTION
    assert "Call complete_onboarding(wake_time, bedtime, playbook_json)" in ONBOARDING_INSTRUCTION
    assert "can tap Continue to enter the main assistant." in ONBOARDING_INSTRUCTION
