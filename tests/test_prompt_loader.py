"""Tests for prompt loader resolution logic."""

import pytest

from app.utils.prompt_loader import PromptLoader


@pytest.mark.parametrize(
    "direct_content,variant_content,expected",
    [
        ("Primary prompt", "Alternate prompt", "Primary prompt"),
        ("", "Backup prompt", "Backup prompt"),
    ],
)
def test_load_prompt_resolution_prefers_non_empty_variants(monkeypatch, tmp_path, direct_content, variant_content, expected):
    """Prompt loader should choose most appropriate file among overrides."""
    prompts_root = tmp_path / "prompts"
    prompts_root.mkdir()

    direct_file = prompts_root / "sale_prompt.txt"
    direct_file.write_text(direct_content, encoding="utf-8")

    # Variant file imitates timestamped backup in the same folder
    variant_file = prompts_root / "sale_prompt.txt.snapshot"
    variant_file.write_text(variant_content, encoding="utf-8")

    monkeypatch.setenv("PROMPTS_DIR", str(prompts_root))
    loader = PromptLoader()
    loader.clear_cache()

    result = loader.load_prompt("sale_prompt")
    assert result == expected


def test_load_prompt_reads_nested_local_override(monkeypatch, tmp_path):
    """Local overrides inside subdirectories must also be considered."""
    prompts_root = tmp_path / "prompts"
    local_dir = prompts_root / "local"
    local_dir.mkdir(parents=True)

    (local_dir / "sale_prompt.txt").write_text("Local override", encoding="utf-8")

    monkeypatch.setenv("PROMPTS_DIR", str(prompts_root))
    loader = PromptLoader()
    loader.clear_cache()

    resolved = loader.load_prompt("sale_prompt")
    assert resolved == "Local override"
