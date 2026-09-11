"""
Prompt-writing rules for the Write Video Prompt node.

Two families, from the two references this pack ships workflows for:

- minimax: the sectioned MiniMax H3 style ([SCENE], [DIALOGUE], [SHOT LIST],
  [ACTING], [LIGHT AND IMAGE], [CAMERA], [PRODUCTION SOUND], [NEGATIVES]).
- ltx: the LTX 2.3 style: one flowing present-tense paragraph covering shot,
  scene, action, character, camera and sound.

build() returns (system, user): the instruction for the text encoder and the
user brief that goes with it. The node sends both through the clip attached
to it, exactly the way ComfyUI's own Generate Text node does.
"""

MINIMAX_SHARED = """You write production-ready MiniMax video prompts. Return ONE prompt only,
no explanations, no questions, no markdown, no code fences.

Rules:
- Present-tense, observable action. Direct acting through gaze, posture,
  breath and movement, never through emotion labels like sad or happy.
- Exact dialogue goes in double quotes, bound to one speaker, with language
  and delivery when useful. Anything the brief already puts in double quotes
  is locked dialogue: keep it quoted and bound to its speaker, word for word.
- Use only the sections that matter, in this order:
  [SCENE] [DIALOGUE] [SHOT LIST] [ACTING] [LIGHT AND IMAGE] [CAMERA]
  [PRODUCTION SOUND] [NEGATIVES]
- No quoted speech in the brief means the [DIALOGUE] section reads exactly
  N/A, never empty quotes.
- One continuous action: chronological prose. More than one beat:
  consecutive non-overlapping time ranges inside the video duration, one
  primary visible event and one usable end state per range.
- [NEGATIVES] stays short and observable: no extra people, identity drift,
  wardrobe swaps, duplicate props, position reversals, broken eyelines,
  subtitles, unmotivated cuts, unwanted music.
- Never invent characters, dialogue, or voices the brief does not ask for."""

MINIMAX_I2V_EXTRA = """An image is attached: it is the exact first frame. Describe only the
motion and action unfolding from it, in a single continuous take. Never
restate its static visuals and never contradict them. No cuts."""

MINIMAX_FFLF_EXTRA = """Two images are attached: the exact first frame and the exact last
frame. State both boundary states (subject count, identity, composition,
lighting, object positions) and the single causal motion between them, then
arrive naturally at the last frame. No hard cut, teleportation, duplicate
subject, prop swap, or discontinuous light."""

MINIMAX_REF_EXTRA = """Reference images are attached, in order. Give every image one bounded
job and name what it must not contribute, e.g. Image 1 defines the face,
hair and wardrobe only and nothing else. Use separate named roles for
identity, wardrobe, prop, scene, style, motion and camera. Never write
"use the references" or "respectively". Take each image's job from the
brief; if the brief does not say, give the first image identity and the
rest scene, prop, or style in that order."""

LTX_SHARED = """You write LTX video prompts. Return ONE prompt only: a single flowing
paragraph, present-tense verbs, no headings, no labels, no preamble.

Rules:
- Cover shot scale, scene, action in chronological order, character
  appearance, camera movement relative to the subject, and the full
  soundscape (ambient sound, music, effects, speech).
- Dialogue goes in quotation marks; break long lines into short phrases with
  acting directions between them. Anything the brief already puts in double
  quotes is locked dialogue: keep it quoted, word for word.
- Direct performance through physical cues (pauses, glances, breath), never
  through emotion labels like sad or confused.
- Never write readable text or logos into the scene. Keep one consistent
  light logic. Start directly with the action, never with "the scene opens".
- Size the detail to the video duration: a short video gets one tight shot,
  a longer one gets more beats. Never invent characters or dialogue the
  brief does not ask for."""

LTX_I2V_EXTRA = """An image is attached: it is the exact first frame. Describe the motion
and action that unfold from it, not the static details already visible."""

LTX_FFLF_EXTRA = """Two images are attached: the exact first frame and the exact last
frame. Open on the first frame exactly as shown, narrate the single motion
that carries the shot forward, and land on the last frame. One continuous
take, no cuts."""


def build(model, mode, idea, duration=5.0,
          has_first=False, has_last=False):
    """Return (system, user) for the clip to expand."""
    idea = (idea or "").strip()
    seconds = ("%g" % float(duration or 0)) or "5"

    if model == "ltx":
        system = LTX_SHARED
        if mode == "image to video":
            system += "\n" + LTX_I2V_EXTRA
        elif mode == "first and last frame":
            system += "\n" + LTX_FFLF_EXTRA
        system += "\nWrite for a %s-second video." % seconds
    else:
        system = MINIMAX_SHARED
        if mode == "image to video":
            system += "\n" + MINIMAX_I2V_EXTRA
        elif mode == "first and last frame":
            system += "\n" + MINIMAX_FFLF_EXTRA
        elif has_first or has_last:
            system += "\n" + MINIMAX_REF_EXTRA
        system += "\nThe video is %s seconds long." % seconds

    if mode == "first and last frame" and not (has_first and has_last):
        system += ("\nNo boundary images were attached, so invent the shot "
                   "from the brief instead.")

    user = "Brief: %s" % idea
    return system, user
