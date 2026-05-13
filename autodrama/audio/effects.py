"""Common audio effects — fade, EQ, normalisation."""

from pydub import AudioSegment


class AudioEffects:
    """Static methods for applying audio effects."""

    @staticmethod
    def fade_in(audio: AudioSegment, duration_ms: int = 500) -> AudioSegment:
        return audio.fade_in(duration_ms)

    @staticmethod
    def fade_out(audio: AudioSegment, duration_ms: int = 500) -> AudioSegment:
        return audio.fade_out(duration_ms)

    @staticmethod
    def normalize(audio: AudioSegment, target_dbfs: float = -3.0) -> AudioSegment:
        """Normalise peak amplitude to *target_dbfs*."""
        change = target_dbfs - audio.max_dBFS
        if change != 0:
            return audio.apply_gain(change)
        return audio

    @staticmethod
    def silence(duration_ms: int) -> AudioSegment:
        return AudioSegment.silent(duration=duration_ms)
