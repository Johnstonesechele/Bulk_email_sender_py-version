# models.py
from dataclasses import dataclass
from typing import Optional

@dataclass
class Campaign:
    id: Optional[int]
    name: str
    subject: str
    body: str
    created_at: Optional[str] = None

@dataclass
class Recipient:
    id: Optional[int]
    campaign_id: int
    email: str
    name: Optional[str] = None
    status: str = "pending"   # pending, sent, failed, responded
    last_error: Optional[str] = None
    attempts: int = 0
    responded_at: Optional[str] = None  # Timestamp for when response was recorded
