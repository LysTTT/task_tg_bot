import asyncio
from functools import cache
from io import BytesIO


_transcription_slot = asyncio.Semaphore(1)


@cache
def _model():
    from faster_whisper import WhisperModel

    return WhisperModel("small", device="cpu", compute_type="int8")


def _transcribe(audio: bytes) -> str:
    with BytesIO(audio) as stream:
        segments, _ = _model().transcribe(stream, language="ru", vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments).strip()


async def transcribe_voice(audio: bytes) -> str:
    async with _transcription_slot:
        return await asyncio.to_thread(_transcribe, audio)
