"""
Permission system. Permissions are stored as hierarchical strings
like "document:read", "query:execute", "admin:users", etc.

Roles are linked to permissions via the role_permissions table.
Users get permissions through their roles.

Permission checking is enforced server-side on every API call.
"""

from enum import Enum


class Permission(str, Enum):
    """All application permissions. Each maps to a string used in the DB."""

    # ── Auth / User Management ──
    AUTH_LOGIN = "auth:login"
    AUTH_REGISTER = "auth:register"
    USER_READ = "user:read"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"
    ROLE_ASSIGN = "role:assign"
    ROLE_REVOKE = "role:revoke"

    # ── Data Sources ──
    DATASOURCE_READ = "datasource:read"
    DATASOURCE_CREATE = "datasource:create"
    DATASOURCE_UPDATE = "datasource:update"
    DATASOURCE_DELETE = "datasource:delete"
    DATASOURCE_TEST = "datasource:test"

    # ── Catalog ──
    CATALOG_READ = "catalog:read"
    CATALOG_UPDATE = "catalog:update"

    # ── SQL Queries ──
    QUERY_EXECUTE = "query:execute"
    QUERY_VIEW_SQL = "query:view_sql"
    QUERY_EXPORT = "query:export"
    SQL_QUERY = "sql:query"
    SQL_SCHEMA = "sql:schema"

    # ── Documents ──
    DOCUMENT_READ = "document:read"
    DOCUMENT_UPLOAD = "document:upload"
    DOCUMENT_DELETE = "document:delete"
    DOCUMENT_REINDEX = "document:reindex"

    # ── Chat ──
    CHAT_CONVERSATION_READ = "chat:conversation_read"
    CHAT_CONVERSATION_DELETE = "chat:conversation_delete"
    CHAT_FEEDBACK = "chat:feedback"

    # ── Reports ──
    REPORT_GENERATE = "report:generate"
    REPORT_READ = "report:read"
    REPORT_DELETE = "report:delete"

    # ── Audit ──
    AUDIT_READ = "audit:read"
    AUDIT_READ_SENSITIVE = "audit:read_sensitive"

    # ── Admin ──
    ADMIN_PANEL = "admin:panel"
    ADMIN_CONFIG = "admin:config"
    ADMIN_MIGRATE = "admin:migrate"

    # ── Tools ──
    TOOL_EXECUTE = "tool:execute"
    TOOL_APPROVE = "tool:approve"


class RoleName(str, Enum):
    """Built-in role names."""
    ADMIN = "admin"
    STANDARD_USER = "standard_user"
    AUDITOR = "auditor"


# Permission sets for each built-in role
BUILTIN_ROLE_PERMISSIONS: dict[RoleName, list[Permission]] = {
    RoleName.ADMIN: [
        p for p in Permission
    ],
    RoleName.STANDARD_USER: [
        Permission.AUTH_LOGIN,
        Permission.CHAT_CONVERSATION_READ,
        Permission.CHAT_CONVERSATION_DELETE,
        Permission.CHAT_FEEDBACK,
        Permission.DATASOURCE_READ,
        Permission.CATALOG_READ,
        Permission.QUERY_EXECUTE,
        Permission.QUERY_VIEW_SQL,
        Permission.QUERY_EXPORT,
        Permission.SQL_QUERY,
        Permission.SQL_SCHEMA,
        Permission.DOCUMENT_READ,
        Permission.DOCUMENT_UPLOAD,
        Permission.REPORT_GENERATE,
        Permission.REPORT_READ,
        Permission.REPORT_DELETE,
        Permission.TOOL_EXECUTE,
    ],
    RoleName.AUDITOR: [
        Permission.AUTH_LOGIN,
        Permission.AUDIT_READ,
    ],
}


def has_permission(user_permissions: list[str], required: Permission) -> bool:
    """Check if a list of permission strings includes the required permission."""
    return required.value in user_permissions


def check_permission(user_permissions: list[str], required: Permission) -> bool:
    """Alias for has_permission. Kept for readability in route handlers."""
    return has_permission(user_permissions, required)
