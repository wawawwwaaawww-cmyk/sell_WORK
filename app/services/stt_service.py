"""Service for converting speech to text using OpenAI Whisper."""

from io import BytesIO
from typing import Optional

import structlog
from aiogram import Bot
from openai import AsyncOpenAI

from app.config import settings

logger = structlog.get_logger()


class SttService:
    """Service for speech-to-text conversion."""

    def __init__(self):
        if not settings.openai_api_key:
            raise ValueError("OpenAI API key is not configured.")
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.logger = structlog.get_logger()

    async def transcribe_audio(
        self, bot: Bot, file_id: str
    ) -> Optional[str]:
        """
        Download an audio file from Telegram, transcribe it, and return the text.

        Args:
            bot: The aiogram Bot instance.
            file_id: The file_id of the audio message.

        Returns:
            The transcribed text as a string, or None if transcription fails.
        """
        try:
            self.logger.info("stt_transcription_started", file_id=file_id)
            
            file_info = await bot.get_file(file_id)
            if not file_info.file_path:
                self.logger.warning("stt_file_path_missing", file_id=file_id)
                return None

            file_content = await bot.download_file(file_info.file_path)
            if not file_content:
                self.logger.warning("stt_download_failed", file_id=file_id)
                return None

            audio_buffer = BytesIO(file_content.read())
            audio_buffer.name = "voice.ogg"

            response = await self.client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_buffer,
            )
            
            transcribed_text = response.text
            self.logger.info(
                "stt_transcription_succeeded",
                file_id=file_id,
                text_length=len(transcribed_text),
            )
            return transcribed_text

        except Exception as e:
            self.logger.error(
                "stt_transcription_failed",
                error=str(e),
                file_id=file_id,
                exc_info=True,
            )
            return None
