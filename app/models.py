from sqlalchemy import Column, Integer, String, DateTime, Enum, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
import enum
from sqlalchemy.orm import relationship


Base = declarative_base()

class ContentType(enum.Enum):
    TEXT = "text"
    AUDIO = "audio"
    VIDEO = "video"

class MessageStatus(enum.Enum):
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"

class Message(Base):
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, nullable=False)
    receiver_id = Column(Integer, nullable=False)
    content = Column(String, nullable=True)
    content_type = Column(Enum(ContentType), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    status = Column(Enum(MessageStatus), default=MessageStatus.SENT)
    file_url = Column(String, nullable=True)
    chat_id = Column(Integer, ForeignKey('chats.id'))
    chat = relationship("Chat", back_populates="messages")


class Chat(Base):
    __tablename__ = 'chats'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=True)
    is_group = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    messages = relationship("Message", back_populates="chat")
    participants = relationship("ChatParticipant", back_populates="chat")


class ChatParticipant(Base):
    __tablename__ = 'chat_participants'

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey('chats.id'))
    user_id = Column(Integer, nullable=False)

    chat = relationship("Chat", back_populates="participants")


class UploadedFile(Base):
    __tablename__ = 'uploaded_files'

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    file_url = Column(String, nullable=False)
    uploader_id = Column(Integer, ForeignKey('users.id'))
    uploaded_at = Column(DateTime, default=datetime.utcnow())

    uploader = relationship("User", back_populates="uploaded_files")