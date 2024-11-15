from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, UploadFile, File, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from typing import List
from models import Base, Message, MessageStatus, ContentType, Chat, ChatParticipant, UploadedFile, User
from database import SessionLocal, engine
from auth import get_current_user
from utils.connection_manager import ConnectionManager, authenticate_user, create_access_token
from datetime import datetime
import json
import asyncio
import uuid
import logging

from utils.message_serializer import serialize_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("chat_app")

app = FastAPI()
Base.metadata.create_all(bind=engine)
manager = ConnectionManager()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token") # при необходимости изменить


@app.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    db = SessionLocal()
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверное имя пользователя или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(
        data={"sub": user.username}
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/upload")
async def upload_file(file: UploadFile = File(...), user_id: int = Depends(get_current_user)):
    unique_filename = f"{uuid.uuid4()}_{file.filename}"
    file_location = f"media/{unique_filename}"

    db = SessionLocal()
    try:
        # Сохранение файла
        with open(file_location, "wb+") as f:
            f.write(await file.read())
        logger.info(f"User {user_id} uploaded file {unique_filename}")

        uploaded_file = UploadedFile(
            filename=file.filename,
            file_url=file_location,
            uploader_id=user_id
        )
        db.add(uploaded_file)
        db.commit()
        db.refresh(uploaded_file)
        logger.info(f"File {unique_filename} saved to database by user {user_id}")

        return {"file_url": file_location}
    except Exception as e:
        logger.error(f"Error uploading file for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при загрузке файла")
    finally:
        db.close()


@app.get("/messages/{user_id}")
async def get_messages_with_user(user_id: int, current_user_id: int = Depends(get_current_user)):
    db = SessionLocal()
    try:
        messages = db.query(Message).filter(
            ((Message.sender_id == current_user_id) & (Message.receiver_id == user_id)) |
            ((Message.sender_id == user_id) & (Message.receiver_id == current_user_id))
        ).order_by(Message.timestamp.asc()).all()
        logger.info(f"User {current_user_id} fetched messages with user {user_id}")
        return messages
    except Exception as e:
        logger.error(f"Error fetching messages for user {current_user_id} with user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при получении сообщений")
    finally:
        db.close()


@app.post("/send_message")
async def send_message(
        receiver_id: int = None,
        chat_id: int = None,
        content: str = "",
        content_type: ContentType = ContentType.TEXT,
        file_url: str = None,
        current_user_id: int = Depends(get_current_user)
):
    db = SessionLocal()
    try:
        if not receiver_id and not chat_id:
            raise HTTPException(status_code=400, detail="Необходимо указать receiver_id или chat_id")

        new_message = Message(
            sender_id=current_user_id,
            receiver_id=receiver_id,
            chat_id=chat_id,
            content=content,
            content_type=content_type,
            timestamp=datetime.utcnow(),
            file_url=file_url
        )
        db.add(new_message)
        db.commit()
        db.refresh(new_message)

        if receiver_id:
            await send_message_to_user(new_message, db)
            logger.info(f"User {current_user_id} sent message to user {receiver_id}")
        elif chat_id:
            await send_message_to_chat(new_message, db)
            logger.info(f"User {current_user_id} sent message to chat {chat_id}")

        return {"message_id": new_message.id}
    except HTTPException as he:
        logger.warning(f"HTTPException in send_message: {he.detail}")
        raise he
    except Exception as e:
        logger.error(f"Error sending message from user {current_user_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при отправке сообщения")
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
        logger.info(f"User {current_user_id} created chat {new_chat.id} with participants {participants}")

        participant_ids = set(participants + [current_user_id])
        for user_id in participant_ids:
            chat_participant = ChatParticipant(chat_id=new_chat.id, user_id=user_id)
            db.add(chat_participant)
        db.commit()
        logger.info(f"Chat {new_chat.id} participants added: {participant_ids}")

        return {"chat_id": new_chat.id}
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating chat for user {current_user_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при создании чата")
    finally:
        db.close()


async def send_message_to_chat(message: Message, db):
    chat = db.query(Chat).filter(Chat.id == message.chat_id).first()
    if not chat:
        logger.warning(f"Chat {message.chat_id} not found")
        return

    participants = db.query(ChatParticipant).filter(ChatParticipant.chat_id == chat.id).all()

    for participant in participants:
        receiver_id = participant.user_id
        if receiver_id != message.sender_id:
            message_copy = Message(
                sender_id=message.sender_id,
                receiver_id=receiver_id,
                chat_id=message.chat_id,
                content=message.content,
                content_type=message.content_type,
                timestamp=message.timestamp,
                file_url=message.file_url,
                status=MessageStatus.SENT
            )
            db.add(message_copy)
            db.commit()
            await send_message_to_user(message_copy, db)
    logger.info(f"Message {message.id} sent to chat {chat.id}")


@app.get("/chats/{chat_id}/messages")
async def get_chat_messages(chat_id: int, current_user: User = Depends(get_current_user)):
    current_user_id = current_user.id
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


async def send_undelivered_personal_messages(user_id: int, websocket: WebSocket):
    db = SessionLocal()
    try:
        undelivered_messages = db.query(Message).filter(
            Message.receiver_id == user_id,
            Message.status == MessageStatus.SENT,
            Message.chat_id == None
        ).all()
        for message in undelivered_messages:
            message_json = serialize_message(message)
            await websocket.send_text(message_json)
            message.status = MessageStatus.DELIVERED
        db.commit()
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected while sending undelivered personal messages to user {user_id}")
    except Exception as e:
        logger.error(f"An error occurred in send_undelivered_personal_messages for user {user_id}: {e}")

async def send_undelivered_group_messages(user_id: int, websocket: WebSocket):
    db = SessionLocal()

    try:
        undelivered_messages = db.query(Message).join(ChatParticipant).filter(
            ChatParticipant.user_id == user_id,
            Message.status == MessageStatus.SENT,
            Message.chat_id != None  # Групповые сообщения
        ).all()
        for message in undelivered_messages:
            message_json = serialize_message(message)
            await websocket.send_text(message_json)
            message.status = MessageStatus.DELIVERED
        db.commit()
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected while sending undelivered group messages to user {user_id}")
    except Exception as e:
        logger.error(f"An error occurred in send_undelivered_group_messages for user {user_id}: {e}")



async def receive_messages(websocket: WebSocket, user_id: int):
    try:
        db = SessionLocal()
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            if action == "send_message":
                receiver_id = data.get("receiver_id")
                chat_id = data.get("chat_id")
                content = data.get("content")
                content_type = data.get("content_type")
                file_url = data.get("file_url")

                if receiver_id:
                    new_message = Message(
                        sender_id=user_id,
                        receiver_id=receiver_id,
                        content=content,
                        content_type=ContentType(content_type),
                        timestamp=datetime.utcnow(),
                        file_url=file_url,
                        status=MessageStatus.SENT
                    )
                    db.add(new_message)
                    db.commit()

                    await send_message_to_user(new_message, db)
                elif chat_id:
                    new_message = Message(
                        sender_id=user_id,
                        chat_id=chat_id,
                        content=content,
                        content_type=ContentType(content_type),
                        timestamp=datetime.utcnow(),
                        file_url=file_url,
                        status=MessageStatus.SENT
                    )
                    db.add(new_message)
                    db.commit()

                    await send_message_to_chat(new_message, db)
                else:
                    await websocket.send_text(json.dumps({
                        "error": "receiver_id или chat_id должны быть указаны"
                    }))

            elif action == "acknowledge":
                message_id = data.get("message_id")
                message = db.query(Message).filter(Message.id == message_id).first()
                if message:
                    message.status = MessageStatus.READ
                    db.commit()
            else:
                await websocket.send_text(json.dumps({
                    "error": "Неизвестное действие"
                }))
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for user {user_id}")
        await manager.disconnect(user_id)
    except Exception as e:
        logger.error(f"An error occurred in receive_messages for user {user_id}: {e}")


@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket, token: str = None):
    if token is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        logger.warning("WebSocket connection closed: Missing token")
        return
    current_user = await get_current_user(token)
    user_id = current_user.id
    await manager.connect(user_id, websocket)
    db = SessionLocal()

    try:
        receive_task = asyncio.create_task(receive_messages(websocket, user_id))

        await send_undelivered_personal_messages(user_id, websocket)
        await send_undelivered_group_messages(user_id, websocket)

        await receive_task

    except WebSocketDisconnect:
        await manager.disconnect(user_id)
    finally:
        db.close()

async def send_message_to_user(message: Message, db):
    receiver_id = message.receiver_id
    message_json = serialize_message(message)
    if await manager.send_personal_message(message_json, receiver_id):
        message.status = MessageStatus.DELIVERED
        db.commit()
        logger.info(f"Message {message.id} delivered to user {receiver_id}")
    else:
        logger.info(f"User {receiver_id} is offline. Message {message.id} not delivered.")
        asyncio.create_task(resend_message_if_no_ack(message.id))

async def resend_message_if_no_ack(message_id: int, attempts: int = 3, delay: int = 10):
    db = SessionLocal()
    try:
        for attempt in range(attempts):
            await asyncio.sleep(delay)
            message = db.query(Message).filter(Message.id == message_id).first()
            if message and message.status != MessageStatus.READ:
                if message.receiver_id:
                    await send_message_to_user(message, db)
                else:
                    await send_message_to_user(message, db)
            else:
                break
    finally:
        db.close()
