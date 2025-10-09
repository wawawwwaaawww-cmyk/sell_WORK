"""Tests for the scene management system."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from app.scenes.base_scene import BaseScene, SceneState, SceneResponse
from app.scenes.scene_manager import SceneManager
from app.scenes.newbie_scene import NewbieScene
from app.models import User, UserSegment, FunnelStage


class MockSession:
    """Mock database session for testing."""
    
    def __init__(self):
        self.execute = AsyncMock()
        self.add = MagicMock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()


class TestBaseScene:
    """Test cases for BaseScene class."""
    
    @pytest.fixture
    def mock_session(self):
        return MockSession()
    
    @pytest.fixture
    def test_user(self):
        return User(
            id=1,
            telegram_id=123456789,
            username="testuser",
            first_name="Test",
            last_name="User",
            segment=UserSegment.COLD,
            lead_score=3,
            funnel_stage=FunnelStage.WELCOMED
        )
    
    @pytest.fixture 
    def scene_state(self):
        return SceneState()
    
    def test_scene_state_initialization(self):
        """Test SceneState initialization."""
        state = SceneState()
        assert state.current_step == "initial"
        assert state.attempts_count == 0
        assert state.confidence_history == []
        assert state.context_data == {}
        assert state.last_action == "none"
        assert state.escalation_triggered == False
    
    def test_scene_response_initialization(self):
        """Test SceneResponse initialization."""
        response = SceneResponse(message_text="Test message")
        assert response.message_text == "Test message"
        assert response.buttons == []
        assert response.next_scene is None
        assert response.escalate == False
        assert response.log_event is None
    
    def test_should_escalate_keywords(self, mock_session, test_user, scene_state):
        """Test escalation trigger by keywords."""
        scene = NewbieScene(mock_session)
        
        # Test escalation keywords
        assert scene.should_escalate(test_user, "не понимаю что делать", scene_state) == True
        assert scene.should_escalate(test_user, "хочу говорить с менеджером", scene_state) == True
        assert scene.should_escalate(test_user, "это сложно для меня", scene_state) == True
        
        # Test normal messages
        assert scene.should_escalate(test_user, "расскажи о криптовалютах", scene_state) == False
    
    def test_should_escalate_low_confidence(self, mock_session, test_user, scene_state):
        """Test escalation trigger by low confidence."""
        scene = NewbieScene(mock_session)
        
        # Add low confidence history
        scene_state.confidence_history = [0.3, 0.2, 0.1]
        
        assert scene.should_escalate(test_user, "обычное сообщение", scene_state) == True
    
    def test_should_escalate_max_attempts(self, mock_session, test_user, scene_state):
        """Test escalation trigger by max attempts."""
        scene = NewbieScene(mock_session)
        
        scene_state.attempts_count = 5  # Greater than max_attempts (3)
        
        assert scene.should_escalate(test_user, "обычное сообщение", scene_state) == True


class TestSceneManager:
    """Test cases for SceneManager class."""
    
    @pytest.fixture
    def mock_session(self):
        return MockSession()
    
    @pytest.fixture
    def scene_manager(self, mock_session):
        return SceneManager(mock_session)
    
    @pytest.fixture
    def test_user_cold(self):
        return User(
            id=1,
            telegram_id=123456789,
            segment=UserSegment.COLD,
            lead_score=3,
            funnel_stage=FunnelStage.WELCOMED
        )
    
    @pytest.fixture
    def test_user_warm(self):
        return User(
            id=2,
            telegram_id=987654321,
            segment=UserSegment.WARM,
            lead_score=7,
            funnel_stage=FunnelStage.SURVEYED
        )
    
    @pytest.fixture
    def test_user_hot(self):
        return User(
            id=3,
            telegram_id=555666777,
            segment=UserSegment.HOT,
            lead_score=12,
            funnel_stage=FunnelStage.QUALIFIED
        )
    
    def test_scene_selection_by_segment(self, scene_manager, test_user_cold, test_user_warm, test_user_hot):
        """Test scene selection based on user segment."""
        from app.scenes.scene_manager import SceneSession
        
        # Test cold segment -> newbie scene
        session = SceneSession(current_scene="")
        # Using the internal method for testing
        # scene_name = await scene_manager._determine_scene(test_user_cold, session)
        # Would need to run async test for this
        
        # For now, test the scene rules logic
        for (segment, score_check), expected_scene in scene_manager.scene_rules.items():
            if segment == UserSegment.COLD and score_check(3):
                assert expected_scene == "newbie"
            elif segment == UserSegment.WARM and score_check(7):
                assert expected_scene == "trader"
            elif segment == UserSegment.HOT and score_check(12):
                assert expected_scene == "investor"
    
    def test_scene_registration(self, scene_manager):
        """Test scene registration."""
        # Scenes should be registered on initialization
        scene_manager._register_default_scenes()
        
        assert "newbie" in scene_manager.scenes
        assert "trader" in scene_manager.scenes
        assert "investor" in scene_manager.scenes
        assert "skeptic" in scene_manager.scenes
        assert "strategy" in scene_manager.scenes

    @pytest.mark.asyncio
    async def test_process_trigger_with_config(self, mock_session, tmp_path, test_user_cold):
        """Config-driven trigger should follow YAML transitions."""
        config_path = tmp_path / "scenario.yaml"
        config_path.write_text(
            """
version: 1
metadata: {}
states:
  START:
    entry:
      steps:
        - action: send_message
          template: "Начинаем"
    transitions:
      - trigger: command:/start
        target: HELLO
  HELLO:
    entry:
      steps:
        - action: send_message
          template: "Привет из YAML"
    transitions: []
""".strip(),
            encoding="utf-8",
        )

        manager = SceneManager(mock_session, config_path=str(config_path))
        assert manager.config_enabled is True

        response = await manager.process_trigger(test_user_cold, "command:/start")
        assert "Привет из YAML" in response.message_text


class TestNewbieScene:
    """Test cases for NewbieScene class."""
    
    @pytest.fixture
    def mock_session(self):
        return MockSession()
    
    @pytest.fixture
    def newbie_scene(self, mock_session):
        return NewbieScene(mock_session)
    
    @pytest.fixture
    def test_user(self):
        return User(
            id=1,
            telegram_id=123456789,
            segment=UserSegment.COLD,
            lead_score=3,
            funnel_stage=FunnelStage.WELCOMED
        )
    
    def test_scene_initialization(self, newbie_scene):
        """Test NewbieScene initialization."""
        assert newbie_scene.scene_name == "newbie"
        assert newbie_scene.confidence_threshold == 0.4  # Lower for newbies
    
    def test_educational_tone_addition(self, newbie_scene):
        """Test educational tone addition."""
        original_text = "Это простой ответ"
        modified_text = newbie_scene._add_educational_tone(original_text)
        
        # Should add educational ending
        assert len(modified_text) > len(original_text)
        assert any(icon in modified_text for icon in ["💡", "📚", "🎯"])
    
    def test_educational_tone_preservation(self, newbie_scene):
        """Test that educational tone is not added if already present."""
        original_text = "Важно понимать основы криптовалют"
        modified_text = newbie_scene._add_educational_tone(original_text)
        
        # Should not modify text that already has educational tone
        assert modified_text == original_text
    
    def test_scene_prompts(self, newbie_scene):
        """Test scene-specific prompts."""
        prompts = newbie_scene.get_scene_prompts()
        
        assert "system_addition" in prompts
        assert "НОВИЧОК" in prompts["system_addition"]
        assert "безопасность" in prompts["system_addition"].lower()
        
        assert "tone" in prompts
        assert "терпеливый" in prompts["tone"]
        assert "образовательный" in prompts["tone"]


class TestRepositoryClasses:
    """Test cases for repository classes."""
    
    @pytest.fixture
    def mock_session(self):
        return MockSession()
    
    def test_appointment_repository_import(self):
        """Test that AppointmentRepository can be imported."""
        from app.repositories.appointment_repository import AppointmentRepository
        assert AppointmentRepository is not None
    
    def test_product_repository_import(self):
        """Test that ProductRepository can be imported."""
        from app.repositories.product_repository import ProductRepository
        assert ProductRepository is not None
    
    def test_payment_repository_import(self):
        """Test that PaymentRepository can be imported."""
        from app.repositories.payment_repository import PaymentRepository
        assert PaymentRepository is not None
    
    def test_broadcast_repository_import(self):
        """Test that BroadcastRepository can be imported."""
        from app.repositories.broadcast_repository import BroadcastRepository, ABTestRepository
        assert BroadcastRepository is not None
        assert ABTestRepository is not None
    
    def test_admin_repository_import(self):
        """Test that AdminRepository can be imported."""
        from app.repositories.admin_repository import AdminRepository
        assert AdminRepository is not None


if __name__ == "__main__":
    pytest.main([__file__])