"""Persistence adapters."""

from .mysql_repository import MySQLCustomerChatRepository, PersistenceError
from .lead_repository import LeadRecord, LeadRepository, LeadSyncStats
from .inbox_repository import (
    EnqueueResult,
    InboxJob,
    InboxPersistenceError,
    MySQLInboxRepository,
)
from .outreach_repository import (
    OutreachJob,
    OutreachPersistenceError,
    MySQLOutreachRepository,
)
from .phone_safety import MySQLKnownPhoneChecker, PhoneSafetyError

__all__ = [
    "LeadRecord",
    "LeadRepository",
    "LeadSyncStats",
    "MySQLCustomerChatRepository",
    "PersistenceError",
    "EnqueueResult",
    "InboxJob",
    "InboxPersistenceError",
    "MySQLInboxRepository",
    "OutreachJob",
    "OutreachPersistenceError",
    "MySQLOutreachRepository",
    "MySQLKnownPhoneChecker",
    "PhoneSafetyError",
]
