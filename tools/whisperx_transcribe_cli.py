from __future__ import annotations

import argparse
import json
import os
import sys
import time
import inspect
from pathlib import Path


def write_progress(path: str | None, payload: dict) -> None:
    if not path:
        return
    progress_path = Path(path)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def create_diarization_pipeline(pipeline_cls, token: str, device: str):
    signature = inspect.signature(pipeline_cls)
    if "token" in signature.parameters:
        return pipeline_cls(token=token, device=device)
    if "use_auth_token" in signature.parameters:
        return pipeline_cls(use_auth_token=token, device=device)
    return pipeline_cls(device=device)


def main() -> int:
    parser = argparse.ArgumentParser(description="WhisperX transcription helper.")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="medium")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--diarize", action="store_true")
    parser.add_argument("--min-speakers", type=int, default=1)
    parser.add_argument("--max-speakers", type=int, default=4)
    parser.add_argument("--progress-json", default="")
    parser.add_argument("--initial-prompt", default="これはニコニコ生放送の録画音声です")
    parser.add_argument("--hotwords", default="")
    args = parser.parse_args()

    import whisperx

    audio_path = Path(args.audio)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    write_progress(args.progress_json, {"stage": "model_load", "message": "load", "done_seconds": None})

    model = whisperx.load_model(
        args.model,
        args.device,
        compute_type=args.compute_type,
        language="ja",
        asr_options={
            "initial_prompt": args.initial_prompt or "これはニコニコ生放送の録画音声です",
            "hotwords": args.hotwords or None,
        },
    )
    write_progress(
        args.progress_json,
        {
            "stage": "transcribe",
            "message": "transcribe",
            "done_seconds": None,
            "percent": 0.0,
        },
    )

    def transcribe_progress(percent: float) -> None:
        write_progress(
            args.progress_json,
            {
                "stage": "transcribe",
                "message": "transcribe",
                "percent": float(percent),
                "done_seconds": None,
                "elapsed_seconds": round(time.monotonic() - started, 2),
            },
        )

    result = model.transcribe(str(audio_path), batch_size=args.batch_size, progress_callback=transcribe_progress)
    language = result.get("language") or "ja"
    transcribed_end = max((float(segment.get("end") or 0.0) for segment in result.get("segments", [])), default=0.0)
    write_progress(
        args.progress_json,
        {
            "stage": "align",
            "message": "align",
            "done_seconds": transcribed_end,
            "segments": len(result.get("segments", [])),
            "elapsed_seconds": round(time.monotonic() - started, 2),
        },
    )
    align_model, metadata = whisperx.load_align_model(language_code=language, device=args.device)
    result = whisperx.align(
        result["segments"],
        align_model,
        metadata,
        str(audio_path),
        args.device,
        return_char_alignments=False,
    )
    if args.diarize:
        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or ""
        if not token:
            raise RuntimeError("HF_TOKEN or HUGGINGFACE_TOKEN is required for WhisperX diarization")
        aligned_end = max((float(segment.get("end") or 0.0) for segment in result.get("segments", [])), default=transcribed_end)
        write_progress(
            args.progress_json,
            {
                "stage": "diarize",
                "message": "diarize",
                "done_seconds": None,
                "percent": 0.0,
                "segments": len(result.get("segments", [])),
                "elapsed_seconds": round(time.monotonic() - started, 2),
            },
        )
        diarization_pipeline = getattr(whisperx, "DiarizationPipeline", None)
        if diarization_pipeline is None:
            from whisperx.diarize import DiarizationPipeline

            diarization_pipeline = DiarizationPipeline
        diarize_model = create_diarization_pipeline(diarization_pipeline, token, args.device)

        def diarize_progress(percent: float) -> None:
            write_progress(
                args.progress_json,
                {
                    "stage": "diarize",
                    "message": "diarize",
                    "percent": float(percent),
                    "done_seconds": None,
                    "segments": len(result.get("segments", [])),
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                },
            )

        diarize_segments = diarize_model(
            str(audio_path),
            min_speakers=args.min_speakers,
            max_speakers=args.max_speakers,
            progress_callback=diarize_progress,
        )
        result = whisperx.assign_word_speakers(diarize_segments, result)
    final_end = max((float(segment.get("end") or 0.0) for segment in result.get("segments", [])), default=0.0)
    write_progress(
        args.progress_json,
        {
            "stage": "save",
            "message": "save",
            "done_seconds": final_end,
            "segments": len(result.get("segments", [])),
            "elapsed_seconds": round(time.monotonic() - started, 2),
        },
    )

    payload = {
        "python": sys.executable,
        "model": args.model,
        "device": args.device,
        "compute_type": args.compute_type,
        "diarize": bool(args.diarize),
        "language": language,
        "segments": result.get("segments", []),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_progress(
        args.progress_json,
        {
            "stage": "done",
            "message": "done",
            "done_seconds": final_end,
            "segments": len(result.get("segments", [])),
            "elapsed_seconds": round(time.monotonic() - started, 2),
        },
    )
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
