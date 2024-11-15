from models import Message
from schemas import MessageSchema


def serialize_message(message: Message) -> str:
    message_data = MessageSchema(
        message_id=message.id,
        sender_id=message.sender_id,
        receiver_id=message.receiver_id,
        chat_id=message.chat_id,
        content=message.content,
        content_type=message.content_type,
        timestamp=message.timestamp,
        file_url=message.file_url
    )
    return message_data.json()
