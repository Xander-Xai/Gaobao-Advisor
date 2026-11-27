"""Voice interaction service — ASR → LangGraph → Voice Rendering → TTS.

Adapted from EduAgent backend/app/modules/voice/service.py.
"""

import os

VOICE_RENDER_SYSTEM_PROMPT = (
    "你是教育规划电话模式助手。\n"
    "你会收到一段已经完成推理的规划结论，请把它改写成适合电话里直接说出来的中文回复。\n"
    "语气要求：直接、短句、先结论后展开，不制造焦虑、不施压、不模仿特定人物的口吻。\n"
    "不要使用 markdown 格式。不要列出要点。用口语化的方式串联信息。\n"
    "控制回复在 200 字以内。"
)

SCENE_VOICE_STYLES = {
    "gaokao": "温暖鼓励型：对考生和家长表达理解和支持，用积极的语言引导",
    "kaoyan": "理性分析型：客观分析利弊，用数据和逻辑支撑建议",
    "career": "务实直接型：直击核心，用实际案例和数据说话",
}


class VoiceService:
    """Handles voice rendering (text → oral speech)."""

    def __init__(self) -> None:
        self.chat_api_key = os.getenv("DASHSCOPE_CHAT_API_KEY", "")
        self.chat_model = os.getenv("DASHSCOPE_CHAT_MODEL", "qwen-plus")

    async def render_voice_reply(self, text: str, scene: str = "gaokao") -> str:
        """Use LLM to rewrite structured reply into oral-style speech.

        The call goes through the governed client, so a voice rendering is
        charged against the provider budget like every other model call.
        """
        try:
            from server.services.llm_gateway import governed_client

            style = SCENE_VOICE_STYLES.get(scene, "")
            client = governed_client(
                "dashscope",
                operation="voice",
                config_override={
                    "dashscope": {
                        "api_key": self.chat_api_key,
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "model": self.chat_model,
                    }
                },
            )
            response = client.chat.completions.create(
                model=self.chat_model,
                messages=[
                    {
                        "role": "system",
                        "content": VOICE_RENDER_SYSTEM_PROMPT + f"\n语气风格：{style}",
                    },
                    {
                        "role": "user",
                        "content": f"请将以下规划结论改写为电话中直接说出来的口语：\n\n{text}",
                    },
                ],
                temperature=0.7,
                max_tokens=500,
            )
            return response.choices[0].message.content.strip()
        except Exception:
            return text  # Fallback: return original text if rendering fails

    def is_available(self) -> bool:
        """Check if voice service is configured."""
        return bool(self.chat_api_key)


_voice_service: VoiceService | None = None


def get_voice_service() -> VoiceService:
    global _voice_service
    if _voice_service is None:
        _voice_service = VoiceService()
    return _voice_service
