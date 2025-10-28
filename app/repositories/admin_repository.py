"""Admin repository for managing administrator roles and permissions."""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Admin, AdminRole


class AdminRepository:
    """Repository for admin database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()
    
    async def create_admin(
        self,
        user_id: int,
        role: AdminRole
    ) -> Admin:
        """Create a new admin."""
        admin = Admin(
            telegram_id=user_id,  # Map user_id to telegram_id field
            role=role
        )
        
        self.session.add(admin)
        await self.session.flush()
        await self.session.refresh(admin)
        
        self.logger.info(
            "Admin created",
            telegram_id=user_id,
            role=role
        )
        
        return admin
    
    async def get_by_telegram_id(self, telegram_id: int) -> Optional[Admin]:
        """Get admin by telegram ID."""
        stmt = select(Admin).where(Admin.telegram_id == telegram_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_admin_by_user_id(self, user_id: int) -> Optional[Admin]:
        """Get admin by user ID (alias for get_by_telegram_id)."""
        return await self.get_by_telegram_id(user_id)
    
    async def get_all_admins(self) -> List[Admin]:
        """Get all admins."""
        stmt = select(Admin).order_by(Admin.created_at)
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_admins_by_role(self, role: AdminRole) -> List[Admin]:
        """Get admins by role."""
        stmt = select(Admin).where(Admin.role == role).order_by(Admin.created_at)
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def update_admin_role(
        self,
        telegram_id: int,
        new_role: AdminRole
    ) -> Optional[Admin]:
        """Update admin role."""
        admin = await self.get_by_telegram_id(telegram_id)
        if not admin:
            return None
        
        old_role = admin.role
        admin.role = new_role
        await self.session.flush()
        
        self.logger.info(
            "Admin role updated",
            telegram_id=telegram_id,
            old_role=old_role,
            new_role=new_role
        )
        
        return admin
    
    async def remove_admin(self, user_id: int) -> bool:
        """Remove admin privileges."""
        admin = await self.get_by_telegram_id(user_id)
        if not admin:
            return False
        
        await self.session.delete(admin)
        await self.session.flush()
        
        self.logger.info("Admin removed", telegram_id=user_id)
        return True
    
    async def is_admin(self, telegram_id: int) -> bool:
        """Check if user is an admin."""
        admin = await self.get_by_telegram_id(telegram_id)
        return admin is not None
    
    async def has_permission(
        self,
        telegram_id: int,
        required_role: AdminRole
    ) -> bool:
        """Check if admin has required permission level."""
        admin = await self.get_by_telegram_id(telegram_id)
        if not admin:
            return False
        
        # Define role hierarchy
        role_hierarchy = {
            AdminRole.OWNER: 4,
            AdminRole.ADMIN: 3,
            AdminRole.EDITOR: 2,
            AdminRole.MANAGER: 1
        }
        
        # Convert string role to AdminRole enum if needed
        admin_role = admin.role
        if isinstance(admin_role, str):
            try:
                admin_role = AdminRole(admin_role)
            except ValueError:
                return False
        
        admin_level = role_hierarchy.get(admin_role, 0)
        required_level = role_hierarchy.get(required_role, 0)
        
        return admin_level >= required_level
    
    async def can_manage_broadcasts(self, telegram_id: int) -> bool:
        """Check if admin can manage broadcasts."""
        return await self.has_permission(telegram_id, AdminRole.EDITOR)
    
    async def can_manage_users(self, telegram_id: int) -> bool:
        """Check if admin can manage users."""
        return await self.has_permission(telegram_id, AdminRole.ADMIN)
    
    async def can_manage_admins(self, telegram_id: int) -> bool:
        """Check if admin can manage other admins."""
        return await self.has_permission(telegram_id, AdminRole.OWNER)

    async def can_manage_materials(self, telegram_id: int) -> bool:
        """Check if admin can manage marketing materials."""
        return await self.has_permission(telegram_id, AdminRole.EDITOR)

    async def can_manage_products(self, telegram_id: int) -> bool:
        """Check if admin can manage products."""
        return await self.has_permission(telegram_id, AdminRole.ADMIN)
    
    async def can_view_analytics(self, telegram_id: int) -> bool:
        """Check if admin can view analytics."""
        return await self.has_permission(telegram_id, AdminRole.MANAGER)
    
    
    async def get_admin_capabilities(self, telegram_id: int) -> Dict[str, bool]:
        """Get all capabilities for an admin."""
        admin = await self.get_by_telegram_id(telegram_id)
        if not admin:
            return {}
        
        # Convert string role to AdminRole enum if needed
        admin_role = admin.role
        if isinstance(admin_role, str):
            try:
                admin_role = AdminRole(admin_role)
            except ValueError:
                admin_role = AdminRole.MANAGER  # Default fallback
        
        capabilities = {
            "is_admin": True,
            "role": admin_role.value,
            "can_view_analytics": await self.can_view_analytics(telegram_id),
            "can_manage_broadcasts": await self.can_manage_broadcasts(telegram_id),
            "can_manage_users": await self.can_manage_users(telegram_id),
            "can_manage_admins": await self.can_manage_admins(telegram_id),
            "can_manage_materials": await self.can_manage_materials(telegram_id),
            "can_manage_products": await self.can_manage_products(telegram_id),
            "can_view_leads": True,  # All admins can view leads
            "can_take_leads": admin_role in [AdminRole.MANAGER, AdminRole.ADMIN, AdminRole.OWNER],
        }
        
        return capabilities
    
    async def get_managers_for_notifications(self) -> List[Admin]:
        """Get all managers who should receive lead notifications."""
        stmt = select(Admin).where(
            Admin.role.in_([AdminRole.MANAGER, AdminRole.ADMIN, AdminRole.OWNER])
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def create_default_admin(self, owner_telegram_id: int) -> Admin:
        """Create default owner admin if none exists."""
        existing_owner = await self.get_admins_by_role(AdminRole.OWNER)
        if existing_owner:
            return existing_owner[0]
        
        return await self.create_admin(owner_telegram_id, AdminRole.OWNER)
    
    async def get_admin_activity_log(
        self,
        days: int = 30,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get admin activity log (would need to implement activity tracking)."""
        # This would require an additional AdminActivity model
        # For now, return basic admin info
        
        admins = await self.get_all_admins()
        
        activity_log = []
        for admin in admins:
            # Convert string role to AdminRole enum if needed
            admin_role = admin.role
            if isinstance(admin_role, str):
                try:
                    admin_role = AdminRole(admin_role)
                except ValueError:
                    admin_role = AdminRole.MANAGER  # Default fallback
            
            activity_log.append({
                "telegram_id": admin.telegram_id,
                "role": admin_role.value,
                "created_at": admin.created_at.isoformat(),
                "last_activity": "N/A"  # Would track from activity table
            })
        
        return activity_log
    
    async def bulk_update_admin_roles(
        self,
        role_updates: List[Dict[str, Any]],
        updated_by_telegram_id: int
    ) -> List[Dict[str, Any]]:
        """Bulk update admin roles."""
        
        # Check if updater has permission
        if not await self.can_manage_admins(updated_by_telegram_id):
            return []
        
        results = []
        
        for update in role_updates:
            telegram_id = update.get("telegram_id")
            new_role = update.get("role")
            
            if not telegram_id or not new_role:
                continue
            
            try:
                new_role_enum = AdminRole(new_role)
                admin = await self.update_admin_role(telegram_id, new_role_enum)
                
                results.append({
                    "telegram_id": telegram_id,
                    "success": admin is not None,
                    "new_role": new_role if admin else None,
                    "error": None if admin else "Admin not found"
                })
                
            except ValueError:
                results.append({
                    "telegram_id": telegram_id,
                    "success": False,
                    "new_role": None,
                    "error": f"Invalid role: {new_role}"
                })
        
        return results
