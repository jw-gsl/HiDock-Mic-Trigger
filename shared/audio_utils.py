"""Audio loading, MFCC feature extraction, and neural speaker embeddings.

Uses soundfile for audio I/O and a pure numpy/scipy MFCC implementation
(no librosa dependency). Optionally uses ONNX Runtime with the TitaNet
model for high-quality neural speaker embeddings.
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


def load_audio_segment(
    path: str | Path,
    *,
    start_seconds: float = 0.0,
    end_seconds: float | None = None,
    sr: int = 16000,
) -> np.ndarray:
    """Load and resample only a bounded portion of an audio file.

    This avoids decoding an entire long meeting when a caller needs to assess
    or embed one representative speaker turn. ``end_seconds=None`` reads from
    the requested start to EOF, matching :func:`load_audio` semantics.
    """
    with sf.SoundFile(str(path)) as source:
        file_sr = source.samplerate
        start_frame = max(0, int(start_seconds * file_sr))
        source.seek(start_frame)
        frames = -1 if end_seconds is None else max(
            0, int((max(start_seconds, end_seconds) - start_seconds) * file_sr)
        )
        audio = source.read(frames=frames, dtype="float32")

    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if file_sr != sr and len(audio):
        duration = len(audio) / file_sr
        n_samples = int(duration * sr)
        indices = np.linspace(0, len(audio) - 1, n_samples)
        audio = np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)
    return np.asarray(audio, dtype=np.float32)


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


def compute_mel_spectrogram(
    audio: np.ndarray,
    sr: int = 16000,
    n_fft: int = 512,
    hop_length: int = 160,
    n_mels: int = 80,
) -> np.ndarray:
    """Compute log-mel spectrogram for neural speaker models.

    Returns:
        Log-mel spectrogram of shape (n_mels, n_frames).
    """
    emphasized = np.append(audio[0], audio[1:] - 0.97 * audio[:-1])
    n_samples = len(emphasized)
    n_frames = 1 + (n_samples - n_fft) // hop_length
    if n_frames < 1:
        emphasized = np.pad(emphasized, (0, n_fft - n_samples))
        n_frames = 1

    frames = np.zeros((n_frames, n_fft))
    for i in range(n_frames):
        start = i * hop_length
        end = start + n_fft
        frame_data = emphasized[start:end]
        frames[i, : len(frame_data)] = frame_data

    window = np.hamming(n_fft)
    frames *= window
    mag_spec = np.abs(np.fft.rfft(frames, n=n_fft))
    power_spec = (mag_spec ** 2) / n_fft

    mel_fb = _mel_filterbank(n_mels, n_fft, sr)
    mel_spec = power_spec @ mel_fb.T
    mel_spec = np.maximum(mel_spec, 1e-10)
    log_mel = np.log(mel_spec)

    return log_mel.T.astype(np.float32)  # (n_mels, n_frames)


def compute_wespeaker_fbank(audio: np.ndarray, sr: int = 16000) -> np.ndarray:
    """Compute the Kaldi filter banks used by WeSpeaker runtime models.

    WeSpeaker's exported ONNX models are trained with Kaldi-compatible
    80-bin filter banks, 16-bit waveform scaling, and utterance-level mean
    normalisation.  That frontend is materially different from the generic
    log-mel frontend above, so keep it explicit instead of silently treating
    every ``(batch, time, 80)`` model as interchangeable.
    """
    try:
        import torch
        import torchaudio.compliance.kaldi as kaldi
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise RuntimeError(
            "WeSpeaker embeddings require torch and torchaudio for the "
            "Kaldi-compatible feature frontend"
        ) from exc

    waveform = torch.from_numpy(np.asarray(audio, dtype=np.float32)).reshape(1, -1)
    features = kaldi.fbank(
        waveform * (1 << 15),
        num_mel_bins=80,
        frame_length=25,
        frame_shift=10,
        dither=0.0,
        sample_frequency=sr,
        window_type="hamming",
        use_energy=False,
    )
    features = features - torch.mean(features, dim=0)
    return features.numpy().astype(np.float32)


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


def extract_neural_embedding(
    audio: np.ndarray,
    sr: int,
    session,  # onnxruntime.InferenceSession
) -> np.ndarray:
    """Extract a neural speaker embedding using the TitaNet ONNX model.

    Args:
        audio: 1-D float32 audio array at the given sample rate.
        sr: Sample rate (should be 16000).
        session: ONNX Runtime InferenceSession for the TitaNet model.

    Returns:
        1-D unit-normalized embedding vector (192-dim for TitaNet Small).
    """
    if len(audio) < 1600:
        # Too short — pad to at least 100ms
        audio = np.pad(audio, (0, 1600 - len(audio)))

    input_info = session.get_inputs()[0]
    input_name = input_info.name

    # Detect whether the model expects raw audio or mel-spectrogram
    input_shape = input_info.shape
    if len(input_shape) == 3 and (input_shape[1] == 80 or input_shape[-1] == 80):
        # Mel-spectrogram model — detect axis order from shape
        output_names = [o.name for o in session.get_outputs()]
        is_wespeaker = input_name == "feats" and "embs" in output_names
        if is_wespeaker:
            # Official WeSpeaker inference frontend: (T, 80), including CMN.
            features = compute_wespeaker_fbank(audio, sr=sr)
            audio_input = features[np.newaxis, :, :]
            length_input = np.array([features.shape[0]], dtype=np.int64)
        else:
            mel = compute_mel_spectrogram(audio, sr=sr)  # (80, T)
            length_input = np.array([mel.shape[1]], dtype=np.int64)
        if not is_wespeaker and input_shape[-1] == 80:
            # CAM++ style: (batch, T, 80)
            audio_input = mel.T[np.newaxis, :, :].astype(np.float32)
        elif not is_wespeaker:
            # TitaNet style: (batch, 80, T)
            audio_input = mel[np.newaxis, :, :].astype(np.float32)
        # Find the "embs" output (embedding, not logits)
        emb_name = next((n for n in output_names if "emb" in n.lower()), output_names[-1])
        inputs = {input_name: audio_input}
        if len(session.get_inputs()) > 1:
            inputs[session.get_inputs()[1].name] = length_input
        outputs = session.run([emb_name], inputs)
    else:
        # Raw audio model: input (batch, samples)
        audio_input = audio[np.newaxis, :].astype(np.float32)
        output_name = session.get_outputs()[0].name
        outputs = session.run([output_name], {input_name: audio_input})

    embedding = outputs[0].flatten().astype(np.float32)

    # Normalize to unit length
    norm = np.linalg.norm(embedding)
    if norm > 1e-10:
        embedding = embedding / norm

    return embedding


def extract_embedding(
    audio: np.ndarray,
    sr: int = 16000,
    n_mfcc: int = 40,
    onnx_session=None,
) -> np.ndarray:
    """Extract a fixed-size speaker embedding from an audio segment.

    If an ONNX session is provided, uses the neural TitaNet model for
    high-quality embeddings. Otherwise falls back to MFCC-based embeddings.

    Args:
        audio: 1-D float32 audio array.
        sr: Sample rate.
        n_mfcc: Number of MFCC coefficients (used only for MFCC fallback).
        onnx_session: Optional ONNX Runtime InferenceSession for neural embeddings.

    Returns:
        1-D embedding vector. Dimension depends on the method used:
        - Neural (TitaNet): 192-dim
        - MFCC fallback (only when no onnx_session is given): n_mfcc-dim
          (default 40)

    Raises:
        RuntimeError: If an onnx_session was provided and neural inference
            fails. A silent MFCC fallback here would return a 40-dim vector
            into batches of 192-dim ones, producing a ragged array that
            crashes (or worse, mis-clusters) downstream — callers that can
            tolerate a missing embedding should catch and skip instead.
    """
    if onnx_session is not None:
        try:
            return extract_neural_embedding(audio, sr, onnx_session)
        except Exception as e:
            raise RuntimeError(f"Neural embedding inference failed: {e}") from e

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
