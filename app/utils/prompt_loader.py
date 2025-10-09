"""Prompt loader utility for managing LLM prompts."""

import os
from typing import Dict, Optional
from pathlib import Path

import structlog


class PromptLoader:
    """Utility class for loading and managing LLM prompts."""
    
    def __init__(self, prompts_dir: str = "prompts"):
        # Calculate path relative to this file's location to ensure it's project-root-based
        base_path = Path(__file__).parent.parent.parent
        self.prompts_dir = base_path / prompts_dir
        self.logger = structlog.get_logger()
        self._cache: Dict[str, str] = {}
    
    def load_prompt(self, filename: str, use_cache: bool = True) -> Optional[str]:
        """Load a prompt from file."""
        if use_cache and filename in self._cache:
            return self._cache[filename]
        
        file_path = self.prompts_dir / f"{filename}.txt"
        
        try:
            if not file_path.exists():
                self.logger.error("Prompt file not found", filename=filename, path=str(file_path))
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
        return self.load_prompt("system_manager") or ""
    
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