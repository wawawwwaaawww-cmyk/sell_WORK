"""Utilities for creating callback data."""

from typing import Dict, Any, Optional


class CallbackData:
    """Utility class for creating structured callback data."""
    
    @staticmethod
    def create(action: str, subaction: Optional[str] = None, data: Optional[Dict[str, Any]] = None) -> str:
        """Create callback data string."""
        parts = [action]
        
        if subaction:
            parts.append(subaction)
        
        if data:
            for key, value in data.items():
                parts.append(f"{key}:{value}")
        
        callback_str = ":".join(parts)
        
        # Ensure callback data doesn't exceed 64 bytes
        if len(callback_str.encode('utf-8')) > 64:
            # Truncate if too long
            callback_str = callback_str[:60] + "..."
        
        return callback_str
    
    @staticmethod
    def parse(callback_data: str) -> Dict[str, Any]:
        """Parse callback data string."""
        parts = callback_data.split(":")
        
        result = {
            "action": parts[0] if len(parts) > 0 else "",
            "subaction": parts[1] if len(parts) > 1 else None,
            "data": {}
        }
        
        # Parse additional data
        for part in parts[2:]:
            if ":" in part:
                key, value = part.split(":", 1)
                result["data"][key] = value
        
        return result


# Predefined callback data constants
class Callbacks:
    """Predefined callback data strings."""
    
    # Bonus actions
    BONUS_GET = "bonus:get"
    
    # Survey actions  
    SURVEY_START = "survey:start"
    SURVEY_Q1_BEGINNER = "survey:q1:beginner"
    SURVEY_Q1_SOME_EXP = "survey:q1:some_exp"
    SURVEY_Q1_ADVANCED = "survey:q1:advanced"
    
    SURVEY_Q2_LEARN = "survey:q2:learn"
    SURVEY_Q2_INCOME = "survey:q2:income"
    SURVEY_Q2_RETURNS = "survey:q2:returns"
    
    SURVEY_Q3_CONSERVATIVE = "survey:q3:conservative"
    SURVEY_Q3_MODERATE = "survey:q3:moderate"
    SURVEY_Q3_AGGRESSIVE = "survey:q3:aggressive"
    
    SURVEY_Q4_CASUAL = "survey:q4:casual"
    SURVEY_Q4_PARTTIME = "survey:q4:parttime"
    SURVEY_Q4_FULLTIME = "survey:q4:fulltime"
    
    SURVEY_Q5_SMALL = "survey:q5:small"
    SURVEY_Q5_MEDIUM = "survey:q5:medium"
    SURVEY_Q5_LARGE = "survey:q5:large"
    
    # Strategy selection
    STRATEGY_SAFETY = "strategy:safety"
    STRATEGY_GROWTH = "strategy:growth"
    
    # Consultation actions
    CONSULT_OFFER = "consult:offer"
    CONSULT_DATE = "consult:date"
    CONSULT_TIME = "consult:time"
    CONSULT_CUSTOM = "consult:custom"
    CONSULT_CONFIRM = "consult:confirm"
    CONSULT_RESCHEDULE = "consult:reschedule"
    
    # Lead actions
    LEAD_TAKE = "lead:take"
    LEAD_RETURN = "lead:return"
    
    # Payment actions
    OFFER_PAYMENT = "offer:pay"

    # Application form
    APPLICATION_START = "application:start"
    APPLICATION_SKIP_EMAIL = "application:skip_email"
    APPLICATION_TAKE = "application:take"
    
    # Help actions
    FAQ_ITEM = "faq"
    MANAGER_REQUEST = "manager:request"
    
    # User maintenance
    USER_RESET_CONFIRM = "user:reset_confirm"
    USER_RESET_CANCEL = "user:reset_cancel"

    # Admin actions
    ADMIN_BROADCAST = "admin:broadcast"
    ADMIN_STATS = "admin:stats"
    ADMIN_LEADS = "admin:leads"
