"""Prompt loader utility for managing LLM prompts."""

import os
from typing import Dict, List, Optional
from pathlib import Path

import structlog


class PromptLoader:
    """Utility class for loading and managing LLM prompts."""

    DEFAULT_EXTERNAL_DIR = Path("/home/botseller/sell/prompts")

    def __init__(self, prompts_dir: str = "prompts"):
        # Calculate path relative to this file's location to ensure it's project-root-based
        base_path = Path(__file__).parent.parent.parent
        project_prompts = base_path / prompts_dir
        custom_path = os.getenv("PROMPTS_DIR")

        self.logger = structlog.get_logger()
        self._cache: Dict[str, str] = {}

        self._search_paths: List[Path] = self._build_search_paths(
            project_prompts,
            custom_path,
        )
    
    def load_prompt(self, filename: str, use_cache: bool = True) -> Optional[str]:
        """Load a prompt from file."""
        if use_cache and filename in self._cache:
            return self._cache[filename]
        
        file_path = self._resolve_prompt_path(filename)

        try:
            if not file_path or not file_path.exists():
                self.logger.error(
                    "Prompt file not found",
                    filename=filename,
                    search_paths=[str(path) for path in self._search_paths],
                )
                return None

            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()

            if use_cache:
                self._cache[filename] = content

            self.logger.debug("Prompt loaded successfully", filename=filename)
            return content

        except Exception as e:
            self.logger.error("Error loading prompt", filename=filename, error=str(e))
            return None

    def _build_search_paths(self, project_prompts: Path, custom_path: Optional[str]) -> List[Path]:
        """Assemble search paths for prompts with logging for observability."""

        paths: List[Path] = []

        if custom_path:
            candidate = Path(custom_path).expanduser()
            if candidate not in paths:
                paths.append(candidate)

        if self.DEFAULT_EXTERNAL_DIR not in paths:
            paths.append(self.DEFAULT_EXTERNAL_DIR)

        if project_prompts not in paths:
            paths.append(project_prompts)

        existing = [path for path in paths if path.exists()]
        if not existing:
            existing = [project_prompts]

        self.logger.info(
            "prompt_loader_paths_initialized",
            configured_paths=[str(path) for path in paths],
            existing_paths=[str(path) for path in existing],
        )

        return paths

    def _resolve_prompt_path(self, filename: str) -> Optional[Path]:
        """Find the first matching prompt file in configured directories."""

        for directory in self._search_paths:
            candidate = directory / f"{filename}.txt"
            if candidate.exists():
                self.logger.info(
                    "prompt_file_resolved",
                    filename=filename,
                    path=str(candidate),
                )
                return candidate

        self.logger.warning(
            "prompt_file_missing",
            filename=filename,
            attempted_paths=[str(path) for path in self._search_paths],
        )
        return None
    
    def format_prompt(self, filename: str, **kwargs) -> Optional[str]:
        """Load and format a prompt with provided variables."""
        prompt = self.load_prompt(filename)
        if not prompt:
            return None
        
        try:
            return prompt.format(**kwargs)
        except KeyError as e:
            self.logger.error("Missing variable in prompt", filename=filename, variable=str(e))
            return None
        except Exception as e:
            self.logger.error("Error formatting prompt", filename=filename, error=str(e))
            return None
    
    def get_system_prompt(self) -> str:
        """Get the main system prompt for the LLM."""
        return self.load_prompt("sale_prompt") or ""
    
    def get_safety_policies(self) -> str:
        """Get safety policies prompt."""
        return self.load_prompt("safety_policies") or ""
    
    def get_sales_methodology(self) -> str:
        """Get sales methodology prompt."""
        return self.load_prompt("sales_spin_aida") or ""
    
    def clear_cache(self) -> None:
        """Clear the prompt cache."""
        self._cache.clear()
        self.logger.info("Prompt cache cleared")


# Global prompt loader instance
prompt_loader = PromptLoader()