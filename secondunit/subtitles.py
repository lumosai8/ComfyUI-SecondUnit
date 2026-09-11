"""
SubRip text, on its way to a subtitle track.

Resolve gives scripting no way to position a subtitle clip. Probed on Studio
20.1.0.20: an `AppendToTimeline` clipInfo with a `recordFrame` lands the cues
at some unrelated frame and drops all but the first, while a plain append puts
every cue at its own timecode, measured from the timeline's start frame. So the
plain append is the only usable call — and the only way to move subtitles
somewhere else is to rewrite the timecodes before Resolve ever sees them.

That is what `shift` is for. Wire the `seconds` output of Grab Audio into Send
to Resolve and a transcript of one clip lands back over the clip it came from,
instead of at the head of the timeline.
"""

import os
import re

# "00:00:01,500 --> 00:00:03,000", with the trailing position hints some tools
# append. A '.' separator is accepted because WebVTT-flavoured output is common
# and the only difference that matters here is the separator.
CUE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def looks_like_a_path(value):
    """True for a string that is a reference to an .srt rather than one itself."""
    if not value or "\n" in value or len(value) > 4096:
        return False
    return value.lower().strip().endswith(".srt") and os.path.exists(value.strip())


def parse(text):
    """
    SubRip text -> [{"start": seconds, "end": seconds, "text": str}].

    Deliberately forgiving. Transcription nodes emit SRT with BOMs, CRLF, blank
    trailing cues and inconsistent numbering, and none of that is worth an error
    message when the timings are perfectly readable. Cue numbers are ignored and
    regenerated on the way out, so a file that counts 1,2,2,3 still works.
    """
    if not text:
        return []

    cues = []
    # Split on blank lines, which is what actually separates SubRip cues.
    for block in re.split(r"\r?\n\s*\r?\n", text.replace("﻿", "").strip()):
        found = CUE.search(block)
        if not found:
            continue

        numbers = [int(n) for n in found.groups()]
        start = _seconds(numbers[0], numbers[1], numbers[2], found.group(4))
        end = _seconds(numbers[4], numbers[5], numbers[6], found.group(8))

        # Everything after the timing line is the caption; anything before it is
        # the cue number, which we do not keep.
        body = block[found.end():].strip("\r\n")
        body = "\n".join(line.strip() for line in body.splitlines() if line.strip())
        if not body:
            continue

        cues.append({"start": start, "end": max(start, end), "text": body})

    return cues


def _seconds(hours, minutes, seconds, fraction):
    # '5' means 500ms, not 5ms — pad rather than divide by a varying power.
    milliseconds = int((fraction or "0").ljust(3, "0")[:3])
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000.0


def shift(cues, seconds):
    """
    Move every cue later (or earlier) by `seconds`.

    Cues pushed before zero are dropped rather than clamped: piling three of
    them onto 00:00:00,000 would show them stacked on top of each other, which
    is worse than the honest loss of a caption that is now off the front of the
    timeline.
    """
    offset = float(seconds or 0.0)
    if not offset:
        return list(cues)

    moved = []
    for cue in cues:
        end = cue["end"] + offset
        if end <= 0:
            continue
        moved.append({
            "start": max(0.0, cue["start"] + offset),
            "end": end,
            "text": cue["text"],
        })
    return moved


def render(cues):
    """[cue, ...] -> SubRip text, renumbered from 1."""
    blocks = []
    for number, cue in enumerate(cues, 1):
        blocks.append("%d\n%s --> %s\n%s\n"
                      % (number, timecode(cue["start"]), timecode(cue["end"]), cue["text"]))
    return "\n".join(blocks)


def timecode(seconds):
    """Seconds -> '00:00:01,500'."""
    total = max(0, int(round(float(seconds) * 1000)))
    milliseconds = total % 1000
    total //= 1000
    return "%02d:%02d:%02d,%03d" % (total // 3600, (total // 60) % 60, total % 60, milliseconds)


def write(text, path, offset_seconds=0.0):
    """
    Write SubRip text to `path`, shifted by `offset_seconds`.

    UTF-8 with a BOM on purpose: Resolve reads plain UTF-8 SRT correctly, but
    several editors downstream guess Latin-1 without it and turn every accented
    character to mojibake. The BOM costs three bytes and removes the guess.
    """
    cues = parse(text)
    if not cues:
        raise RuntimeError(
            "That does not look like SubRip text — no '00:00:00,000 --> 00:00:00,000' "
            "line was found in it."
        )

    cues = shift(cues, offset_seconds)
    if not cues:
        raise RuntimeError(
            "Shifting by %.3fs moved every caption before the start of the timeline."
            % float(offset_seconds)
        )

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="\n") as handle:
        handle.write(render(cues))
    return path
