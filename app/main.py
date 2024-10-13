# main.py

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, BackgroundTasks, UploadFile, File, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from typing import List
from models import Base, Message, MessageStatus, ContentType, Chat, ChatParticipant
from database import SessionLocal, engine
from auth import get_current_user
from utils.connection_manager import ConnectionManager
from datetime import datetime
import json
import asyncio

app = FastAPI()

Base.metadata.create_all(bind=engine)

manager = ConnectionManager()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), user_id: int = Depends(get_current_user)):
    file_location = f"media/{file.filename}"
    with open(file_location, "wb+") as f:
        f.write(await file.read())
    return {"file_url": file_location}

@app.get("/messages/{user_id}")
async def get_messages_with_user(user_id: int, current_user_id: int = Depends(get_current_user)):
    db = SessionLocal()
    try:
        messages = db.query(Message).filter(
            ((Message.sender_id == current_user_id) & (Message.receiver_id == user_id)) |
            ((Message.sender_id == user_id) & (Message.receiver_id == current_user_id))
        ).order_by(Message.timestamp.asc()).all()
        return messages
    finally:
        db.close()

@app.post("/chats/")
async def create_chat(participants: List[int], name: str = None, current_user_id: int = Depends(get_current_user)):
    db = SessionLocal()
    try:
        new_chat = Chat(name=name, is_group=True)
        db.add(new_chat)
        db.commit()
        db.refresh(new_chat)

        participant_ids = set(participants + [current_user_id])
        for user_id in participant_ids:
            chat_participant = ChatParticipant(chat_id=new_chat.id, user_id=user_id)
            db.add(chat_participant)
        db.commit()

        return {"chat_id": new_chat.id}
    finally:
        db.close()


async def send_message_to_chat(message: Message, db):
    chat = db.query(Chat).filter(Chat.id == message.chat_id).first()
    participants = db.query(ChatParticipant).filter(ChatParticipant.chat_id == chat.id).all()

    message_data = {
        "action": "new_message",
        "message_id": message.id,
        "chat_id": chat.id,
        "sender_id": message.sender_id,
        "content": message.content,
        "content_type": message.content_type.value,
        "timestamp": str(message.timestamp),
        "file_url": message.file_url
    }

    for participant in participants:
        receiver_id = participant.user_id
        if receiver_id != message.sender_id:
            await manager.send_personal_message(json.dumps(message_data), receiver_id)


@app.get("/chats/{chat_id}/messages")
async def get_chat_messages(chat_id: int, current_user_id: int = Depends(get_current_user)):
    db = SessionLocal()
    try:
        is_participant = db.query(ChatParticipant).filter(
            ChatParticipant.chat_id == chat_id,
            ChatParticipant.user_id == current_user_id
        ).first()
        if not is_participant:
            raise HTTPException(status_code=403, detail="Access denied")

        messages = db.query(Message).filter(
            Message.chat_id == chat_id
        ).order_by(Message.timestamp.asc()).all()
        return messages
    finally:
        db.close()


@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket, token: str = Depends(oauth2_scheme)):
    user_id = await get_current_user(token)
    await manager.connect(user_id, websocket)
    db = SessionLocal()
    try:
        undelivered_messages = db.query(Message).filter(
            Message.receiver_id == user_id,
            Message.status == MessageStatus.SENT
        ).all()
        for message in undelivered_messages:
            chat = db.query(Chat).filter(Chat.id == message.chat_id).first()
            await websocket.send_text(json.dumps({
                "message_id": message.id,
                "chat_id": chat.id,
                "sender_id": message.sender_id,
                "content": message.content,
                "content_type": message.content_type.value,
                "timestamp": str(message.timestamp),
                "file_url": message.file_url
            }))
            message.status = MessageStatus.DELIVERED
            db.commit()

        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            if action == "send_message":
                chat_id = data.get("chat_id")
                content = data.get("content")
                content_type = data.get("content_type")
                file_url = data.get("file_url")

                new_message = Message(
                    sender_id=user_id,
                    chat_id=chat_id,
                    content=content,
                    content_type=ContentType(content_type),
                    timestamp=datetime.utcnow(),
                    file_url=file_url
                )
                db.add(new_message)
                db.commit()

                await send_message_to_chat(new_message, db)
            elif action == "acknowledge":
                message_id = data.get("message_id")
                message = db.query(Message).filter(Message.id == message_id).first()
                if message:
                    message.status = MessageStatus.READ
                    db.commit()
            else:
                await websocket.send_text(json.dumps({
                    "error": "Unknown action"
                }))
    except WebSocketDisconnect:
        manager.disconnect(user_id)
    finally:
        db.close()

async def send_message_to_user(message: Message, db):
    receiver_id = message.receiver_id
    message_data = {
        "message_id": message.id,
        "sender_id": message.sender_id,
        "content": message.content,
        "content_type": message.content_type.value,
        "timestamp": str(message.timestamp),
        "file_url": message.file_url
    }
    if await manager.send_personal_message(json.dumps(message_data), receiver_id):
        message.status = MessageStatus.DELIVERED
        db.commit()
    else:
        pass

    asyncio.create_task(resend_message_if_no_ack(message.id))

async def resend_message_if_no_ack(message_id: int, attempts: int = 3, delay: int = 10):
    db = SessionLocal()
    try:
        for attempt in range(attempts):
            await asyncio.sleep(delay)
            message = db.query(Message).filter(Message.id == message_id).first()
            if message and message.status != MessageStatus.READ:
                await send_message_to_user(message, db)
            else:
                break
    finally:
        db.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8003)
