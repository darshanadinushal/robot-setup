"""Hardware checks the user confirms (screen, touch, speaker, microphone).

Automated checks only prove software is present; these record what the user actually saw
and heard. Results are stored separately with kind "user".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .state import locked
from .system import FAILED, PASSED, SKIPPED

ANSWERS = {"yes": PASSED, "no": FAILED, "skip": SKIPPED}


def _start_luna(ctx):
    from .modules.startup import start_luna
    return start_luna(ctx)


def _speaker(ctx):
    result = ctx.runner.run(["espeak-ng", "Hello, I am Luna. Your speaker works."], timeout=30)
    return "Played the test sentence." if result.ok else "Could not play sound (espeak-ng)."


def _microphone(ctx):
    sample = ctx.paths.state_dir / "microphone-test.wav"
    sample.parent.mkdir(parents=True, exist_ok=True)
    try:
        ctx.log("Recording for 3 seconds. Speak now.")
        record = ctx.runner.run(["arecord", "-q", "-d", "3", "-f", "S16_LE", "-r", "16000",
                                 "-c", "1", sample], timeout=20)
        if not record.ok:
            return "Could not record from the microphone."
        play = ctx.runner.run(["aplay", "-q", sample], timeout=20)
        return "Played your recording." if play.ok else "Could not play the recording."
    finally:
        sample.unlink(missing_ok=True)


@dataclass
class UserCheck:
    id: str
    feature: str
    question: str
    instructions: str
    test: Optional[Callable] = None


USER_CHECKS = [
    UserCheck("screen", "luna", "Did Luna open full screen?",
              "Start Luna. Close her with the X button at the top right to come back.",
              _start_luna),
    UserCheck("touch", "luna", "Did Luna react when you touched the screen?",
              "While Luna is open, tap her eyes or the buttons at the bottom."),
    UserCheck("speaker", "luna", "Did you hear a voice from the speaker?",
              "Play a short test sentence.", _speaker),
    UserCheck("microphone", "voice", "Did you hear your own voice played back?",
              "Record 3 seconds of speech and play it back.", _microphone),
]


def applicable(ctx):
    return [c for c in USER_CHECKS if c.feature in ctx.request.features]


def record_answer(ctx, check, answer):
    with locked(ctx.paths):
        ctx.state.load()
        ctx.state.set_check(check.id, "user", ANSWERS[answer], check.question)
        ctx.state.save()
