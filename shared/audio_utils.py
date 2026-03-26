"""Audio loading and MFCC feature extraction.

Uses soundfile for audio I/O and a pure numpy/scipy MFCC implementation
(no librosa dependency). Designed for speaker embedding extraction.
"""
from __future__ import annotations

import numpy as np
import soundfile as sf
from pathlib import Path
from scipy.fftpack import dct


def load_audio(path: str | Path, sr: int = 16000) -> np.ndarray:
    """Load an audio file and resample to the target sample rate.

    Args:
        path: Path to the audio file (wav, mp3, flac, etc.).
        sr: Target sample rate in Hz.

    Returns:
        1-D float32 numpy array of audio samples.
    """
    audio, file_sr = sf.read(str(path), dtype="float32")

    # Convert stereo to mono
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    # Resample if needed (simple linear interpolation)
    if file_sr != sr:
        duration = len(audio) / file_sr
        n_samples = int(duration * sr)
        indices = np.linspace(0, len(audio) - 1, n_samples)
        audio = np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)

    return audio


def _hz_to_mel(hz: float) -> float:
    """Convert frequency in Hz to mel scale."""
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: float) -> float:
    """Convert mel scale to frequency in Hz."""
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _mel_filterbank(n_filters: int, n_fft: int, sr: int) -> np.ndarray:
    """Create a mel-scale filterbank matrix.

    Args:
        n_filters: Number of mel filters.
        n_fft: FFT size.
        sr: Sample rate.

    Returns:
        Filterbank matrix of shape (n_filters, n_fft // 2 + 1).
    """
    low_mel = _hz_to_mel(0)
    high_mel = _hz_to_mel(sr / 2)
    mel_points = np.linspace(low_mel, high_mel, n_filters + 2)
    hz_points = np.array([_mel_to_hz(m) for m in mel_points])
    bin_points = np.floor((n_fft + 1) * hz_points / sr).astype(int)

    n_bins = n_fft // 2 + 1
    filterbank = np.zeros((n_filters, n_bins))

    for i in range(n_filters):
        left = bin_points[i]
        center = bin_points[i + 1]
        right = bin_points[i + 2]

        # Rising slope
        for j in range(left, center):
            if center != left:
                filterbank[i, j] = (j - left) / (center - left)
        # Falling slope
        for j in range(center, right):
            if right != center:
                filterbank[i, j] = (right - j) / (right - center)

    return filterbank


def extract_mfcc(
    audio: np.ndarray,
    sr: int = 16000,
    n_mfcc: int = 40,
    n_fft: int = 512,
    hop_length: int = 160,
    n_mels: int = 80,
) -> np.ndarray:
    """Extract MFCC features from an audio signal.

    Pure numpy/scipy implementation (no librosa required).

    Args:
        audio: 1-D float32 audio array.
        sr: Sample rate.
        n_mfcc: Number of MFCC coefficients to return.
        n_fft: FFT window size.
        hop_length: Hop length in samples.
        n_mels: Number of mel filterbank channels.

    Returns:
        MFCC matrix of shape (n_mfcc, n_frames).
    """
    # Pre-emphasis
    emphasized = np.append(audio[0], audio[1:] - 0.97 * audio[:-1])

    # Framing
    n_samples = len(emphasized)
    n_frames = 1 + (n_samples - n_fft) // hop_length
    if n_frames < 1:
        # Pad short audio
        emphasized = np.pad(emphasized, (0, n_fft - n_samples))
        n_frames = 1

    frames = np.zeros((n_frames, n_fft))
    for i in range(n_frames):
        start = i * hop_length
        end = start + n_fft
        frame_data = emphasized[start:end]
        frames[i, : len(frame_data)] = frame_data

    # Windowing (Hamming)
    window = np.hamming(n_fft)
    frames *= window

    # FFT
    mag_spec = np.abs(np.fft.rfft(frames, n=n_fft))
    power_spec = (mag_spec ** 2) / n_fft

    # Mel filterbank
    mel_fb = _mel_filterbank(n_mels, n_fft, sr)
    mel_spec = power_spec @ mel_fb.T

    # Log compression (avoid log(0))
    mel_spec = np.maximum(mel_spec, 1e-10)
    log_mel = np.log(mel_spec)

    # DCT to get MFCCs
    mfccs = dct(log_mel, type=2, axis=1, norm="ortho")[:, :n_mfcc]

    # Transpose to (n_mfcc, n_frames)
    return mfccs.T


def extract_embedding(audio: np.ndarray, sr: int = 16000, n_mfcc: int = 40) -> np.ndarray:
    """Extract a fixed-size speaker embedding from an audio segment.

    Computes MFCCs and averages them across time to produce a single
    embedding vector. Also appends the standard deviation for each
    coefficient to capture speaker dynamics.

    Args:
        audio: 1-D float32 audio array.
        sr: Sample rate.
        n_mfcc: Number of MFCC coefficients.

    Returns:
        1-D embedding vector of length n_mfcc (mean only, for compact storage).
    """
    if len(audio) < 400:
        # Too short for meaningful features
        return np.zeros(n_mfcc, dtype=np.float32)

    mfccs = extract_mfcc(audio, sr=sr, n_mfcc=n_mfcc)
    # Mean across time frames
    embedding = mfccs.mean(axis=1).astype(np.float32)
    return embedding


def segment_audio(
    audio: np.ndarray, sr: int, segments: list[tuple[float, float]]
) -> list[np.ndarray]:
    """Slice audio into segments given start/end times.

    Args:
        audio: Full audio array.
        sr: Sample rate.
        segments: List of (start_seconds, end_seconds) tuples.

    Returns:
        List of audio arrays, one per segment.
    """
    result = []
    for start, end in segments:
        s = int(start * sr)
        e = int(end * sr)
        s = max(0, s)
        e = min(len(audio), e)
        result.append(audio[s:e])
    return result
