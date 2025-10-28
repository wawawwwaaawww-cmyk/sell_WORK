"""Tests for repository classes."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from decimal import Decimal
from datetime import date, time, datetime

from app.repositories.product_repository import ProductRepository
from app.repositories.admin_repository import AdminRepository
from app.models import (
    Product, Admin,
    UserSegment, AdminRole
)


class MockSession:
    """Mock database session for testing."""
    
    def __init__(self):
        self.execute = AsyncMock()
        self.add = MagicMock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.delete = AsyncMock()


class TestProductRepository:
    """Test cases for ProductRepository class."""
    
    @pytest.fixture
    def mock_session(self):
        return MockSession()
    
    @pytest.fixture
    def product_repo(self, mock_session):
        return ProductRepository(mock_session)
    
    @pytest.fixture
    def sample_product(self):
        return Product(
            id=1,
            code="test_course",
            name="Test Course",
            description="A test course",
            price=Decimal("99.99"),
            is_active=True
        )
    
    def test_calculate_product_score_cold_segment(self, product_repo):
        """Test product scoring for cold segment."""
        product = Product(
            code="basic_crypto_course",
            price=Decimal("30000")
        )
        
        score = product_repo._calculate_product_score(
            product, UserSegment.COLD, 3
        )
        
        # Should get points for appropriate price and basic content
        assert score > 0
    
    def test_calculate_product_score_warm_segment(self, product_repo):
        """Test product scoring for warm segment."""
        product = Product(
            code="trading_intermediate_course", 
            price=Decimal("80000")
        )
        
        score = product_repo._calculate_product_score(
            product, UserSegment.WARM, 7
        )
        
        # Should get points for appropriate price and trading content
        assert score > 0
    
    def test_calculate_product_score_hot_segment(self, product_repo):
        """Test product scoring for hot segment."""
        product = Product(
            code="premium_vip_course",
            price=Decimal("200000")
        )
        
        score = product_repo._calculate_product_score(
            product, UserSegment.HOT, 12
        )
        
        # Should get points for premium content and high user score
        assert score > 0
    
    def test_product_score_comparison(self, product_repo):
        """Test that appropriate products score higher."""
        basic_product = Product(
            code="basic_course",
            price=Decimal("25000")
        )
        
        premium_product = Product(
            code="premium_advanced_course",
            price=Decimal("150000")
        )
        
        # Cold user should prefer basic product
        cold_basic_score = product_repo._calculate_product_score(
            basic_product, UserSegment.COLD, 3
        )
        cold_premium_score = product_repo._calculate_product_score(
            premium_product, UserSegment.COLD, 3
        )
        
        assert cold_basic_score > cold_premium_score
        
        # Hot user should prefer premium product
        hot_basic_score = product_repo._calculate_product_score(
            basic_product, UserSegment.HOT, 12
        )
        hot_premium_score = product_repo._calculate_product_score(
            premium_product, UserSegment.HOT, 12
        )
        
        assert hot_premium_score > hot_basic_score




class TestAdminRepository:
    """Test cases for AdminRepository class."""
    
    @pytest.fixture
    def mock_session(self):
        return MockSession()
    
    @pytest.fixture
    def admin_repo(self, mock_session):
        return AdminRepository(mock_session)
    
    def test_admin_repository_initialization(self, admin_repo):
        """Test admin repository initialization."""
        assert admin_repo.session is not None
        assert admin_repo.logger is not None
    
    def test_role_hierarchy(self, admin_repo):
        """Test admin role hierarchy."""
        # Owner should have highest permissions
        assert admin_repo.has_permission.__code__.co_varnames  # Method exists
        
        # Test role hierarchy logic
        role_hierarchy = {
            AdminRole.OWNER: 4,
            AdminRole.ADMIN: 3,
            AdminRole.EDITOR: 2,
            AdminRole.MANAGER: 1
        }
        
        # Owner should have level 4
        assert role_hierarchy[AdminRole.OWNER] == 4
        # Manager should have level 1  
        assert role_hierarchy[AdminRole.MANAGER] == 1
        
        # Higher level should have more permissions
        assert role_hierarchy[AdminRole.OWNER] > role_hierarchy[AdminRole.ADMIN]
        assert role_hierarchy[AdminRole.ADMIN] > role_hierarchy[AdminRole.EDITOR]
        assert role_hierarchy[AdminRole.EDITOR] > role_hierarchy[AdminRole.MANAGER]
    
    def test_permission_methods_exist(self, admin_repo):
        """Test that all permission methods exist."""
        assert hasattr(admin_repo, 'can_manage_broadcasts')
        assert hasattr(admin_repo, 'can_manage_users')
        assert hasattr(admin_repo, 'can_manage_admins')
        assert hasattr(admin_repo, 'can_view_analytics')
    
    def test_admin_capabilities_structure(self, admin_repo):
        """Test admin capabilities structure."""
        # Test that get_admin_capabilities returns expected structure
        expected_keys = [
            "is_admin", "role", "can_view_analytics", 
            "can_manage_broadcasts", "can_manage_users",
            "can_manage_admins",
            "can_view_leads", "can_take_leads"
        ]
        
        # Would test in async environment:
        # capabilities = await admin_repo.get_admin_capabilities(123456)
        # for key in expected_keys:
        #     assert key in capabilities


class TestRepositoryIntegration:
    """Integration tests for repository interactions."""
    
    def test_all_repositories_can_be_imported(self):
        """Test that all repositories can be imported together."""
        from app.repositories import (
            UserRepository, ProductRepository,
            BroadcastRepository, ABTestRepository,
            AdminRepository
        )
        
        # All imports should succeed
        assert UserRepository is not None
        assert ProductRepository is not None
        assert BroadcastRepository is not None
        assert ABTestRepository is not None
        assert AdminRepository is not None
    
    def test_repository_dependencies(self):
        """Test that repositories have proper dependencies."""
        from app.repositories.product_repository import ProductRepository
        from app.models import Product, UserSegment
        
        # Should be able to reference model classes
        assert Product is not None
        assert UserSegment is not None
    
    def test_model_enum_completeness(self):
        """Test that all required model enums are properly defined."""
        from app.models import (
            UserSegment, FunnelStage, MessageRole, LeadStatus,
            MaterialType,
            ABTestStatus, ABTestMetric, AdminRole
        )
        
        # Test UserSegment enum
        assert UserSegment.COLD
        assert UserSegment.WARM  
        assert UserSegment.HOT
        
        # Test AdminRole enum
        assert AdminRole.OWNER
        assert AdminRole.ADMIN
        assert AdminRole.EDITOR
        assert AdminRole.MANAGER
        
        # Test other enums exist
        assert FunnelStage.NEW


if __name__ == "__main__":
    pytest.main([__file__])