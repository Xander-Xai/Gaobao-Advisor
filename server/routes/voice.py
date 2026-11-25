"""Voice WebSocket endpoint — real-time voice interaction."""

import asyncio
import json
import re

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.auth import verify_session_token
from server.graph.graph import get_advisor_graph
from server.services.voice import get_voice_service

router = APIRouter(tags=["voice"])

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{4,64}$")


@router.websocket("/ws/call")
async def voice_call(
    websocket: WebSocket,
    session_id: str = "default",
    scene: str = "gaokao",
    token: str = "",
) -> None:
    """Real-time voice call endpoint.

    Protocol:
    - Client sends: {"type": "asr_result", "text": "..."} for transcribed text
    - Client sends: binary PCM frames for audio
    - Server sends: {"type": "user_text", "text": "..."} when user text received
    - Server sends: {"type": "assistant_text", "text": "..."} for assistant reply
    - Server sends: {"type": "tts_start"} / {"type": "tts_end"} for TTS lifecycle
    """
    from server.industrial_runtime import industrial_enabled

    if industrial_enabled():
        # Legacy voice path bypasses SSE finalization and has no real ASR/TTS.
        await websocket.close(code=1008, reason="Voice not released for industrial use")
        return
    await websocket.accept()

    # Validate session_id
    if not _SESSION_ID_RE.match(session_id):
        await websocket.send_json({"type": "error", "message": "session_id 格式无效：4-64位字母数字下划线连字符"})
        await websocket.close(code=4000, reason="invalid_session_id")
        return

    # Validate session token (from query param)
    if not token or not verify_session_token(session_id, token):
        await websocket.send_json({"type": "error", "message": "Session token 无效或已过期"})
        await websocket.close(code=4001, reason="invalid_token")
        return

    voice_service = get_voice_service()
    graph = get_advisor_graph()
    session_messages: list[dict[str, str]] = []

    try:
        while True:
            data = await websocket.receive()

            if data["type"] == "websocket.receive" and isinstance(data.get("text"), str):
                msg = json.loads(data["text"])

                if msg.get("type") == "asr_result" and msg.get("text"):
                    user_text = msg["text"]
                    from server.middleware.security import detect_injection, sanitize_input

                    if detect_injection(user_text):
                        await websocket.send_json({"type": "error", "message": "输入内容包含不允许的指令"})
                        await websocket.close(code=4002, reason="prompt_injection")
                        return
                    user_text = sanitize_input(user_text)
                    await websocket.send_json({"type": "user_text", "text": user_text})

                    # Run planning graph
                    await websocket.send_json({"type": "assistant_text", "text": "正在分析..."})
                    result = await asyncio.to_thread(
                        graph.invoke,
                        {
                            "session_id": session_id,
                            "input_text": user_text,
                            "scene": scene,
                            "slots": {},
                            "messages": session_messages[-12:],
                            "trace": [],
                        },
                    )

                    reply = result.get("reply", "抱歉，暂时无法回答。")
                    session_messages.append({"role": "user", "content": user_text})
                    session_messages.append({"role": "assistant", "content": reply})

                    # Voice rendering
                    oral_reply = await voice_service.render_voice_reply(reply, scene)
                    await websocket.send_json({"type": "assistant_text", "text": oral_reply})

                    # TTS placeholder — full DashScope TTS streaming to be added
                    await websocket.send_json({"type": "tts_start"})
                    # In production: stream TTS audio frames via DashScope
                    await websocket.send_json({"type": "tts_end"})

            elif data["type"] == "websocket.receive" and isinstance(data.get("bytes"), bytes):
                # Binary audio frames — passthrough to ASR when integrated
                pass

    except WebSocketDisconnect:
        pass
