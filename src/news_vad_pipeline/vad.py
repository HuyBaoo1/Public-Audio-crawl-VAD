from __future__ import annotations

import inspect
import os
from pathlib import Path

from .audio import SpeechRegion
from .config import PipelineConfig


class PyannoteVAD:
    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.pipeline = None
        self.device = "cpu"

    def load(self) -> None:
        if self.pipeline is not None:
            return
        token = os.environ.get("HF_TOKEN", "").strip() or os.environ.get("HUGGINGFACE_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "HF_TOKEN is missing. Accept pyannote/segmentation-3.0 conditions and export a read token."
            )
        try:
            import torch
            from pyannote.audio import Model
            from pyannote.audio.pipelines import VoiceActivityDetection
        except ImportError as exc:
            raise RuntimeError("PyAnnote is not installed. Run: python -m pip install -r requirements.txt") from exc

        if self.config.vad_device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("vad.device=cuda but torch.cuda.is_available() is False")
        use_cuda = self.config.vad_device == "cuda" or (
            self.config.vad_device == "auto" and torch.cuda.is_available()
        )
        self.device = "cuda" if use_cuda else "cpu"
        model_kwargs = {"cache_dir": str(self.config.hf_cache_dir)}
        parameters = inspect.signature(Model.from_pretrained).parameters
        if "token" in parameters:
            model_kwargs["token"] = token
        else:
            model_kwargs["use_auth_token"] = token
        print(
            f"[VAD] loading model={self.config.vad_model_id} device={self.device}",
            flush=True,
        )
        model = Model.from_pretrained(self.config.vad_model_id, **model_kwargs)
        if model is None:
            raise RuntimeError(
                f"Could not load {self.config.vad_model_id}. Confirm the Hugging Face gate was accepted."
            )
        model.to(torch.device(self.device))
        pipeline = VoiceActivityDetection(segmentation=model)
        pipeline.instantiate(
            {
                "min_duration_on": self.config.min_duration_on,
                "min_duration_off": self.config.min_duration_off,
            }
        )
        if hasattr(pipeline, "to"):
            pipeline.to(torch.device(self.device))
        self.pipeline = pipeline

    def detect(self, wav_path: Path) -> list[SpeechRegion]:
        self.load()
        assert self.pipeline is not None
        output = self.pipeline(str(wav_path))
        timeline = output.get_timeline().support()
        regions = [
            SpeechRegion(float(segment.start), float(segment.end))
            for segment in timeline
            if float(segment.end) > float(segment.start)
        ]
        print(
            f"[VAD] file={wav_path.name} speech_regions={len(regions)} "
            f"speech_hours={sum(region.duration for region in regions) / 3600.0:.3f}",
            flush=True,
        )
        return regions
