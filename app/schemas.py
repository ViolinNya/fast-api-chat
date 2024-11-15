from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from enum import Enum

class ContentTypeEnum(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"

class MessageSchema(BaseModel):
    action: str = "new_message"
    message_id: int
    sender_id: int
    receiver_id: Optional[int] = None
    chat_id: Optional[int] = None
    content: str
    content_type: ContentTypeEnum
    timestamp: datetime
    file_url: Optional[str] = None
