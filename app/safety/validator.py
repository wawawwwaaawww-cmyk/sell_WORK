"""Safety validation for LLM responses."""

import re
from typing import Dict, List, Tuple
from dataclasses import dataclass

import structlog

from app.utils.prompt_loader import prompt_loader


@dataclass
class SafetyIssue:
    """Represents a safety issue found in text."""
    type: str
    original: str
    suggestion: str
    severity: str  # "high", "medium", "low"


class SafetyValidator:
    """Validates and sanitizes LLM responses for safety compliance."""
    
    def __init__(self):
        self.logger = structlog.get_logger()
        
        # Load safety patterns from prompt
        safety_prompt = prompt_loader.get_safety_policies()
        self._parse_safety_rules(safety_prompt)
    
    def _parse_safety_rules(self, safety_prompt: str) -> None:
        """Parse safety rules from the prompt file."""
        # Prohibited patterns (high risk)
        self.prohibited_patterns = [
            r"гарантированн(ая|ый|ое)\s+прибыль",
            r"стабильный\s+доход\s+\d+%",
            r"безрисков(ые|ое|ых)\s+инвестиции",
            r"точно\s+заработаешь",
            r"100%\s+результат",
            r"продай\s+квартиру",
            r"бери\s+кредит",
            r"инвестируй\s+все\s+сбережения",
            r"только\s+сегодня!",
            r"последняя\s+возможность",
        ]
        
        # Warning patterns (medium risk)
        self.warning_patterns = [
            r"быстр(ый|ая|ое)\s+(доход|прибыль)",
            r"легк(ие|ий|ая)\s+деньги",
            r"без\s+усилий",
            r"миллион(ы)?\s+за\s+месяц",
        ]
        
        # Safe replacements
        self.safe_replacements = {
            r"гарантированн(ая|ый|ое)\s+прибыль": "изучение потенциальных возможностей",
            r"стабильный\s+доход": "навыки для принятия обоснованных решений",
            r"безрисков(ые|ое|ых)": "стратегии для минимизации рисков",
            r"точно\s+заработаешь": "возможность увеличить шансы на успех",
            r"100%\s+результат": "высокое качество обучения",
            r"только\s+сегодня!": "пока действует специальное предложение",
            r"последняя\s+возможность": "количество мест ограничено",
        }
    
    def validate_response(self, text: str) -> Tuple[str, List[SafetyIssue]]:
        """Validate and sanitize response text."""
        issues = []
        sanitized_text = text
        
        # Check for prohibited patterns
        for pattern in self.prohibited_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                original = match.group()
                # Try to find replacement
                replacement = None
                for rep_pattern, rep_text in self.safe_replacements.items():
                    if re.search(rep_pattern, original, re.IGNORECASE):
                        replacement = rep_text
                        break
                
                if replacement:
                    sanitized_text = re.sub(pattern, replacement, sanitized_text, flags=re.IGNORECASE)
                    issues.append(SafetyIssue(
                        type="prohibited_content",
                        original=original,
                        suggestion=replacement,
                        severity="high"
                    ))
                else:
                    issues.append(SafetyIssue(
                        type="prohibited_content",
                        original=original,
                        suggestion="Требуется ручная проверка",
                        severity="high"
                    ))
        
        # Check for warning patterns
        for pattern in self.warning_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                issues.append(SafetyIssue(
                    type="warning_content",
                    original=match.group(),
                    suggestion="Рекомендуется переформулировать",
                    severity="medium"
                ))
        
        # Add safety disclaimers if needed
        if self._needs_disclaimer(sanitized_text):
            sanitized_text = self._add_disclaimer(sanitized_text)
        
        return sanitized_text, issues
    
    def _needs_disclaimer(self, text: str) -> bool:
        """Check if text needs a safety disclaimer."""
        investment_keywords = [
            "инвестици", "доход", "прибыль", "торговля", 
            "заработок", "капитал", "вложения"
        ]
        
        for keyword in investment_keywords:
            if keyword in text.lower():
                return True
        return False
    
    def _add_disclaimer(self, text: str) -> str:
        """Add safety disclaimer to text."""
        disclaimer = "\n\n⚠️ Помни: любые инвестиции связаны с рисками. Результаты не гарантированы."
        return text + disclaimer
    
    def is_safe_for_auto_send(self, issues: List[SafetyIssue]) -> bool:
        """Check if response is safe for automatic sending."""
        high_severity_issues = [issue for issue in issues if issue.severity == "high"]
        return len(high_severity_issues) == 0
    
    def should_escalate_to_manager(self, confidence: float, issues: List[SafetyIssue]) -> bool:
        """Determine if conversation should be escalated to manager."""
        # Escalate if confidence is very low
        if confidence < 0.5:
            return True
        
        # Escalate if there are high-severity safety issues
        high_severity_issues = [issue for issue in issues if issue.severity == "high"]
        if len(high_severity_issues) > 2:
            return True
        
        return False