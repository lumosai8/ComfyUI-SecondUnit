# ComfyUI-SecondUnit

A free ComfyUI custom node that connects ComfyUI straight to DaVinci Resolve.
Send images, video, audio, and subtitles back and forth with no manual
exporting and importing — all from inside your workflow.

Grab a frame off your timeline, run it through any model you like, and send
the result back to the media pool or straight onto the timeline at the frame
it came from. Build a personal library of music, sound effects, voice takes,
and clips that is one click from your edit.

No server, no port, nothing to copy anywhere. The nodes run inside ComfyUI's
own Python and talk to Resolve through its scripting module directly, so
ComfyUI's Python version doesn't matter.

## What it does

- **Images, video, audio, subtitles — both directions.** Any workflow can take
  Resolve as input and Resolve as output once you add a Grab node at the start
  and a Send node at the end.
- **AI transitions and animations.** Grab the two sides of a cut, feed them to
  an image-to-video model, and land the result back in the gap it came from.
- **Media library panel.** "Second Unit" in ComfyUI's top bar: clips, music,
  and voice takes kept in one place, each with **Pool** (into the media pool),
  **Timeline** (at the playhead), and **Graph** (drop a loader onto the canvas)
  buttons.
- **Music and voice.** Generate tracks with ComfyUI's own audio workflows,
  save them into the library, and drop them on the timeline — or grab timeline
  audio back into a workflow.
- **Auto-subtitles.** Wire the `srt` output of a transcription node into
  **Send to Resolve** and captions land on a subtitle track, synced to the
  voice.

## Install

1. **Get ComfyUI.** Clone it into your own Python environment, or grab a
   portable build from the ComfyUI releases page. Pick the build for your
   hardware: NVIDIA GPU, AMD, or Intel.
2. **Get Git**, since the nodes are installed with it. On Windows search for
   "Git for Windows" and run the installer with the defaults.
3. **Open a terminal in `ComfyUI/custom_nodes`** and clone:
   ```
   git clone https://github.com/ltdrdata/ComfyUI-Manager
   git clone https://github.com/lumosai8/ComfyUI-SecondUnit
   ```
   The first is ComfyUI Manager (browse and install extra nodes later). The
   second is this node. Optional, only if you want text-to-speech or
   auto-subtitles: also clone the TTS Audio Suite node.
4. **Launch ComfyUI** with the batch file matching your setup
   (`run_nvidia_gpu.bat`, `run_amd_gpu.bat`, `run_intel_gpu.bat`, …) and let it
   finish loading.

Requirements:

- DaVinci Resolve **Studio** (Blackmagic documents the scripting API for
  Studio; external scripting is not available in the free version).
- Resolve running, with a project open.
- **Preferences → System → General → External scripting using** must not be
  `None`. The default (`Local`) is correct.

Nothing to `pip install`: torch, numpy, Pillow, and torchaudio all ship with
ComfyUI, and Resolve's module is found on disk. Two optional extras, only for
the media library: **ffmpeg** on `PATH` for thumbnails, and **yt-dlp** for the
*From a link* button.

## Nodes

Most of them carry a **button**: press it and the thing happens immediately,
without running the workflow. A grabbed frame or clip is **pinned by
filename**, so tweaking a prompt ten times compares against the same media
instead of re-grabbing. Press the button again only when you want fresh media.

**In**

| Node | Buttons | Gives you |
|---|---|---|
| **Grab Frames from Resolve** | *Grab the cut* · *Grab into first / last* · *Upload into first / last* | one still, or the pair either side of a cut |
| **Grab Audio from Resolve** | *Grab the audio* | one clip off the timeline, or the whole mix |
| **Grab Video from Resolve** | *Grab the video* | one video clip off the timeline, as mp4 |
| **Load Timeline Audio from Resolve** | — | the whole mix, re-read on every run |
| **Resolve Timeline Info** | — | fps, size, playhead, and every cut, numbered |

**Out**

| Node | Button | Does |
|---|---|---|
| **Send to Resolve** | *Send to Resolve* | saves picture / video / sound / subtitles and imports them |
| **Import File into Resolve** | — | any file already on disk; pairs with ComfyUI's own Save nodes |

`second frame` on **Grab Frames from Resolve** decides the shape: off is a
plain one-picture loader, on is a first/last pair for video models. Each slot
fills three ways that mix freely: grab from the timeline at the playhead, grab
both sides of a cut in one press, or upload your own picture.

**Send to Resolve** has a `send automatically` toggle. Leave it on and the
result goes to Resolve as soon as the run finishes; turn it off and the run
only saves the files, so you can look first and press the button when happy.
The `place` menu decides where it lands: `media pool only`, `at the playhead`,
`at the cut it came from`, or `at a time` with a `seconds` value.

## The library, and sending files to Resolve

Open **Second Unit** in the top bar and point it at a folder (e.g. a new
`Library` folder on your drive). Anything you drop in there — music, sound
effects, voice-overs, video — shows up for preview. Rename tracks, organise
into subfolders; the folders on disk are the truth, so files copied in from
outside simply appear.

To send a file into Resolve: create a timeline if needed (`Ctrl+N`), find the
file in the library, and press **Pool** (media pool only) or **Timeline** (at
the playhead). It shows up instantly.

## Turn any workflow into a Resolve workflow

1. Add **Grab Frames from Resolve** at the start instead of a file loader.
   Park the playhead and press *Grab into first / last*, or park on a cut and
   press *Grab the cut* to take the last frame of the outgoing clip and the
   first frame of the incoming one in one go.
2. Run your model (image-to-video for transitions, upscalers, background
   removal, lip-sync — anything).
3. Replace the Save node with **Send to Resolve**. For bridging a cut pick
   `at the cut it came from` (or wire `cut duration` into your animation
   length and `seconds` with `at a time`) so the clip fills the exact gap.

## Subtitles and audio

- **Auto-subtitles:** load a transcription workflow (e.g. via TTS Audio
  Suite), feed it audio, and connect its `srt` output to **Send to Resolve**.
  Captions land on a subtitle track; a track is added if there is none. Since
  Resolve's API cannot position subtitle clips, `place` is honoured by
  shifting the timecodes before import — wire **Grab Audio**'s `seconds` into
  `seconds` with `at a time` to put a clip's transcript back over that clip.
- **Grab audio:** park on a clip and press *Grab the audio* to pull that
  clip's sound (same duration) into ComfyUI, or take the whole-timeline mix.
- **Music:** run a music workflow from Templates, save a track you like into
  the library, and send it to the pool or timeline whenever you need it.

## Example workflows

In `example_workflows/` (each with a `.jpg` thumbnail for the template
browser):

| | |
|---|---|
| `01-round-trip` | Grab a frame, send it straight back. No models. |
| `02-bridge-a-cut` | Both sides of a cut, wired to land back in the gap. |
| `03-timeline-audio` | The timeline's audio, mixed down. No models. |
| `04-timeline-info` | What is on the timeline, and where the cuts are. No models. |
| `05-picture-to-resolve` | Text-to-image straight into your media pool. |

Each carries a note saying which models it needs and where the files go.

## Known limits

- Frames come from the **source media**, so grade, titles, compound clips, and
  transforms are not baked in. Generators and offline clips cannot be grabbed.
- Positioned placement only lands in **free space**; on an occupied frame the
  clip is appended at the end and the node says so.
- A fully hosted (browser-only cloud) ComfyUI cannot reach your Resolve. Local
  ComfyUI using cloud API nodes works fine — the save node is what must be
  local.
- Run only one tool that scripts Resolve at a time. A second process holding a
  scripting connection throttles every call (~1/sec for everyone).

## Licence

MIT. Free forever — similar bridges sell for $50+, this one stays free.
