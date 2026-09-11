import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

/**
 * "Grab Frame from Resolve" — a button that fetches the frame straight away.
 *
 * Running the whole workflow just to see what you grabbed is the wrong loop:
 * you park the playhead, press the button, and the frame is there. The picture
 * is then pinned by filename, so tweaking a prompt re-uses the same frame
 * instead of quietly grabbing a new one.
 */


function widget(node, name) {
  return node.widgets?.find((w) => w.name === name);
}

/** Show the grabbed frame on the node, the way LoadImage does. */
function showOnNode(node, name, subfolder) {
  const img = new Image();
  img.onload = () => {
    node.imgs = [img];
    node.setSizeForImage?.();
    app.graph.setDirtyCanvas(true, true);
  };
  img.src = api.apiURL(
    `/view?filename=${encodeURIComponent(name)}&subfolder=${encodeURIComponent(
      subfolder || ""
    )}&type=input&rand=${Math.random()}`
  );
}


/**
 * "Send to Resolve" — a button that pushes the last run's output on demand.
 *
 * The node records what it wrote during execution; the button re-sends exactly
 * those files. That is the difference between auditioning a result and having
 * every experimental run land in your edit.
 */
app.registerExtension({
  name: "SecondUnit.Send",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "ResolveSend") return;

    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      created?.apply(this, arguments);

      const button = this.addWidget("button", "Send to Resolve", null, async () => {
        const last = this.secondUnitLast;
        if (!last?.paths?.length) {
          alert("Second Unit — run the workflow first; there is nothing to send yet.");
          return;
        }

        const original = button.label ?? button.name;
        button.label = "Sending…";
        this.setDirtyCanvas(true);

        try {
          const placeWidget = this.widgets?.find((w) => w.name === "place");
          const secondsWidget = this.widgets?.find((w) => w.name === "seconds");

          const response = await api.fetchApi("/secondunit/send", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              paths: last.paths,
              // Read the widgets NOW, so changing where it should go does not
              // require re-running the whole graph.
              place: placeWidget?.value ?? last.place,
              seconds: secondsWidget?.value ?? last.seconds,
            }),
          });

          const data = await response.json();
          if (!response.ok) throw new Error(data?.error || "Resolve refused it.");

          button.label = "Sent";
          setTimeout(() => {
            button.label = original;
            this.setDirtyCanvas(true);
          }, 2500);
        } catch (err) {
          button.label = original;
          alert("Second Unit — " + (err?.message || err));
        } finally {
          this.setDirtyCanvas(true);
        }
      });
    };

    // Remember what the run produced, and show it, so you can judge the result
    // BEFORE it touches your edit. Pictures and sound get ComfyUI's own preview
    // shapes; video has none, so build a player here.
    const executed = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      executed?.apply(this, arguments);

      const info = message?.secondunit?.[0];
      if (info?.paths?.length) this.secondUnitLast = info;

      // Clear what the LAST run showed. ComfyUI keeps node.imgs when a run
      // returns no images, so unplugging the picture would leave the previous
      // one on screen — and a stale preview is worse than no preview, because
      // nothing tells you it is stale.
      if (!message?.images?.length) this.imgs = [];

      const clip = message?.secondunit_video?.[0];
      if (!clip) {
        if (this.secondUnitVideo) {
          this.secondUnitVideo.removeAttribute("src");
          this.secondUnitVideo.load();
          this.secondUnitVideo.style.display = "none";
        }
        this.setDirtyCanvas(true, true);
        return;
      }
      if (this.secondUnitVideo) this.secondUnitVideo.style.display = "";

      const src = api.apiURL(
        `/view?filename=${encodeURIComponent(clip.filename)}&subfolder=${encodeURIComponent(
          clip.subfolder || ""
        )}&type=${clip.type || "output"}&rand=${Math.random()}`
      );

      // Reuse one player across runs; adding a widget per run would grow the
      // node forever.
      if (!this.secondUnitVideo) {
        const video = document.createElement("video");
        video.controls = true;
        video.loop = true;
        video.style.width = "100%";
        video.style.borderRadius = "8px";
        this.secondUnitVideo = video;
        this.addDOMWidget("secondunit_preview", "video", video, {
          serialize: false,
          hideOnZoom: false,
        });
      }
      this.secondUnitVideo.src = src;
      this.setSize?.([Math.max(this.size[0], 320), Math.max(this.size[1], 420)]);
      this.setDirtyCanvas(true, true);
    };
  },
});

/**
 * "Grab Cut from Resolve" — both sides of a cut, on one press.
 *
 * Grabbing at execution time would mean the frames could change under you
 * between runs, and 'place at (seconds)' would follow the playhead rather than
 * the cut the frames came from. So the button captures everything once and pins
 * it to the node.
 */

/**
 * "Grab Audio from Resolve" — fetch on click, then play it back on the node.
 *
 * The player is the same one Load Audio uses, so you can hear what you grabbed
 * without running the graph — which is the only check anyone actually trusts.
 */
app.registerExtension({
  name: "SecondUnit.GrabAudio",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "ResolveGrabbedAudio") return;

    // ComfyUI hands the <audio> player to a hardcoded list of its own classes
    // (LoadAudio and friends), so this node has to ask for it by name. AUDIO_UI
    // is a frontend-only widget: it never serialises, so the backend never sees
    // it and old saved graphs still line up.
    //
    // It has to be BUILT before the 'upload' widget core adds for
    // `audio_upload`, whose builder looks the player up by name — miss it and
    // that builder throws, which is why this node had no upload button either.
    // Widgets are built in `input_order` where there is one and in key order
    // where there is not, so both are nudged: the player right after the file
    // it plays, and 'upload' moved to the end of the keys.
    const inputs = nodeData?.input?.required;
    if (inputs && !inputs.audioUI) {
      inputs.audioUI = ["AUDIO_UI", {}];
      if (inputs.upload) {
        const upload = inputs.upload;
        delete inputs.upload;
        inputs.upload = upload;
      }

      const order = nodeData.input_order?.required;
      if (order && !order.includes("audioUI")) {
        const after = order.indexOf("audio");
        order.splice(after < 0 ? order.length : after + 1, 0, "audioUI");
      }
    }

    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      created?.apply(this, arguments);
      const node = this;
      const widget = (name) => node.widgets?.find((w) => w.name === name);

      const set = (name, value) => {
        const w = widget(name);
        if (!w) return;
        if (w.options && Array.isArray(w.options.values) && !w.options.values.includes(value)) {
          w.options.values.unshift(value);
        }
        w.value = value;
        w.callback?.(value);
      };

      const button = this.addWidget("button", "Grab the audio", null, async () => {
        const original = button.label ?? button.name;
        const what = widget("what")?.value ?? "the clip at the playhead";

        // A whole-timeline mixdown is slow; say so rather than looking frozen.
        button.label = what === "the whole timeline" ? "Mixing the timeline…" : "Cutting the clip…";
        this.setDirtyCanvas(true);

        try {
          const response = await api.fetchApi("/secondunit/grab_audio", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              what,
              track: widget("track")?.value ?? 0,
              clip_index: widget("clip_index")?.value ?? 0,
            }),
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data?.error || "Could not get that audio.");

          set("audio", `${data.subfolder}/${data.name}`);
          set("seconds", data.seconds);

          const mins = Math.floor(data.seconds / 60);
          const secs = Math.round(data.seconds % 60);
          const length = `${mins}:${String(secs).padStart(2, "0")}`;
          button.label = data.clip ? `A${data.track} · ${data.clip} · ${length}` : `Got ${length}`;
          if (data.skipped?.length) {
            console.warn("[Second Unit] skipped audio clips:", data.skipped);
          }
          setTimeout(() => {
            button.label = original;
            this.setDirtyCanvas(true);
          }, 4000);
        } catch (err) {
          button.label = original;
          alert("Second Unit — " + (err?.message || err));
        } finally {
          this.setDirtyCanvas(true);
        }
      });

      // --- show only what applies ------------------------------------------
      function sync() {
        const what = widget("what")?.value ?? "the clip at the playhead";
        for (const [w, visible] of [
          [widget("track"), what !== "the whole timeline"],
          [widget("clip_index"), what === "a clip by index"],
        ]) {
          if (w) w.hidden = !visible;
        }
        node.setDirtyCanvas(true);
      }

      const chooser = widget("what");
      if (chooser) {
        const previous = chooser.callback;
        chooser.callback = function () {
          previous?.apply(this, arguments);
          sync();
        };
      }
      sync();
    };

    const configure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      configure?.apply(this, arguments);
      setTimeout(() => this.widgets?.find((w) => w.name === "what")?.callback?.(), 0);
    };
  },
});

/**
 * "Grab Video from Resolve" — fetch on click, then play it back on the node.
 *
 * There is no frontend player widget for video the way AUDIO_UI is for sound,
 * so this extension builds its own <video> preview: it plays whatever the
 * node's picker holds, whether that came off the timeline or from an upload —
 * which is the only check anyone actually trusts before wiring it anywhere.
 */
app.registerExtension({
  name: "SecondUnit.GrabVideo",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "ResolveGrabbedVideo") return;

    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      created?.apply(this, arguments);
      const node = this;
      // The picker already plays on this node's own <video> below, so the
      // execution preview ComfyUI adds for VIDEO outputs would be a second
      // player for the same clip. Core does the same for its self-previewing
      // nodes (ImageCropV2, Painter).
      node.hideOutputImages = true;
      const widget = (name) => node.widgets?.find((w) => w.name === name);

      const set = (name, value) => {
        const w = widget(name);
        if (!w) return;
        if (w.options && Array.isArray(w.options.values) && !w.options.values.includes(value)) {
          w.options.values.unshift(value);
        }
        w.value = value;
        w.callback?.(value);
      };

      // --- the preview player -------------------------------------------

      const set = (name, value) => {
        const w = widget(name);
        if (!w) return;
        if (w.options && Array.isArray(w.options.values) && !w.options.values.includes(value)) {
          w.options.values.unshift(value);
        }
        w.value = value;
        w.callback?.(value);
      };

      // --- the preview player -------------------------------------------
      // Reuse one player across grabs; adding a widget per grab would grow
      // the node forever.
      const viewURL = (value) => {
        const cut = value.lastIndexOf("/");
        const name = cut >= 0 ? value.slice(cut + 1) : value;
        const subfolder = cut >= 0 ? value.slice(0, cut) : "";
        return api.apiURL(
          `/view?filename=${encodeURIComponent(name)}&subfolder=${encodeURIComponent(
            subfolder
          )}&type=input&rand=${Math.random()}`
        );
      };

      const preview = () => {
        const value = widget("video")?.value;
        if (typeof value !== "string" || !value) {
          if (node.secondUnitVideoEl) node.secondUnitVideoEl.style.display = "none";
          return;
        }
        node.secondUnitEnsurePreview?.();
        node.secondUnitVideoEl.style.display = "";
        node.secondUnitVideoEl.src = viewURL(value);
        node.setDirtyCanvas(true, true);
      };
      // Saved graphs restore their picker value through onConfigure, which
      // runs after this — so the player builder has to be reachable there.
      node.secondUnitEnsurePreview = () => {
        if (node.secondUnitVideoEl) return;
        const video = document.createElement("video");
        video.controls = true;
        video.loop = true;
        video.preload = "metadata";
        video.style.width = "100%";
        video.style.borderRadius = "8px";
        node.secondUnitVideoEl = video;
        node.addDOMWidget("secondunit_video_preview", "video", video, {
          serialize: false,
          hideOnZoom: false,
        });
        node.setSize?.([Math.max(node.size[0], 320), Math.max(node.size[1], 300)]);
      };

      // Playing follows the picker, not just the button: an upload or a
      // hand-picked file previews too, without running anything.
      const picker = widget("video");
      if (picker && !picker.secondUnitHooked) {
        picker.secondUnitHooked = true;
        const previous = picker.callback;
        picker.callback = function () {
          previous?.apply(this, arguments);
          preview();
        };
      }

      const button = this.addWidget("button", "Grab the video", null, async () => {
        const original = button.label ?? button.name;

        button.label = "Cutting the clip…";
        this.setDirtyCanvas(true);

        try {
          const response = await api.fetchApi("/secondunit/grab_video", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              what: widget("what")?.value ?? "the clip at the playhead",
              track: widget("track")?.value ?? 0,
              clip_index: widget("clip_index")?.value ?? 0,
            }),
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data?.error || "Could not get that video.");

          set("video", `${data.subfolder}/${data.name}`);
          set("seconds", data.seconds);
          preview();

          const mins = Math.floor(data.seconds / 60);
          const secs = Math.round(data.seconds % 60);
          const length = `${mins}:${String(secs).padStart(2, "0")}`;
          button.label = data.clip ? `V${data.track} · ${data.clip} · ${length}` : `Got ${length}`;
          setTimeout(() => {
            button.label = original;
            this.setDirtyCanvas(true);
          }, 4000);
        } catch (err) {
          button.label = original;
          alert("Second Unit — " + (err?.message || err));
        } finally {
          this.setDirtyCanvas(true);
        }
      });

      // --- show only what applies ------------------------------------------
      function sync() {
        const what = widget("what")?.value ?? "the clip at the playhead";
        const index = widget("clip_index");
        if (index) index.hidden = what !== "a clip by index";
        node.setDirtyCanvas(true);
      }

      const chooser = widget("what");
      if (chooser) {
        const previous = chooser.callback;
        chooser.callback = function () {
          previous?.apply(this, arguments);
          sync();
        };
      }
      sync();
      preview();
    };

    const configure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      configure?.apply(this, arguments);
      // A graph saved before this node existed restores fine, but the picker
      // callback that drives the preview is only hooked on creation — so
      // re-run it once the widgets have their saved values.
      setTimeout(() => {
        this.widgets?.find((w) => w.name === "what")?.callback?.();
        this.secondUnitEnsurePreview?.();
        const value = this.widgets?.find((w) => w.name === "video")?.value;
        if (typeof value === "string" && value && this.secondUnitVideoEl) {
          const cut = value.lastIndexOf("/");
          const name = cut >= 0 ? value.slice(cut + 1) : value;
          const subfolder = cut >= 0 ? value.slice(0, cut) : "";
          this.secondUnitVideoEl.style.display = "";
          this.secondUnitVideoEl.src = api.apiURL(
            `/view?filename=${encodeURIComponent(name)}&subfolder=${encodeURIComponent(
              subfolder
            )}&type=input&rand=${Math.random()}`
          );
          this.setDirtyCanvas(true, true);
        }
      }, 0);
    };
  },
});

app.registerExtension({
  name: "SecondUnit.Frames",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "ResolveFrames") return;

    const viewURL = (name, subfolder) =>
      api.apiURL(
        `/view?filename=${encodeURIComponent(name)}&subfolder=${encodeURIComponent(
          subfolder || ""
        )}&type=input&rand=${Math.random()}`
      );

    const load = (src) =>
      new Promise((resolve) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => resolve(null);
        img.src = src;
      });

    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      created?.apply(this, arguments);
      const node = this;

      const widget = (name) => node.widgets?.find((w) => w.name === name);

      const set = (name, value) => {
        const w = widget(name);
        if (!w) return;
        if (w.options && Array.isArray(w.options.values) && !w.options.values.includes(value)) {
          w.options.values.unshift(value);
        }
        w.value = value;
        w.callback?.(value);
      };

      /** Redraw whichever frames are currently chosen. */
      const repaint = async () => {
        const two = widget("second_frame")?.value !== false;
        const chosen = [widget("first_frame")?.value];
        if (two) chosen.push(widget("last_frame")?.value);

        const images = [];
        for (const value of chosen) {
          if (typeof value !== "string" || !value) continue;
          const cut = value.lastIndexOf("/");
          const img = await load(
            cut >= 0 ? viewURL(value.slice(cut + 1), value.slice(0, cut)) : viewURL(value, "")
          );
          if (img) images.push(img);
        }
        node.imgs = images;
        node.setSizeForImage?.();
        app.graph.setDirtyCanvas(true, true);
      };

      const busy = async (button, label, work) => {
        const original = button.label ?? button.name;
        button.label = label;
        node.setDirtyCanvas(true);
        try {
          await work();
        } catch (err) {
          alert("Second Unit — " + (err?.message || err));
        } finally {
          button.label = original;
          node.setDirtyCanvas(true);
        }
      };

      // --- both frames at once, from a cut -------------------------------
      const cutButton = node.addWidget("button", "Grab the cut", null, () =>
        busy(cutButton, "Grabbing both…", async () => {
          const response = await api.fetchApi("/secondunit/grab_cut", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              which: widget("which")?.value,
              cut_index: widget("cut_index")?.value,
            }),
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data?.error || "Could not grab that cut.");

          // A cut is inherently two frames, so make sure both are on.
          set("second_frame", true);
          set("first_frame", `${data.subfolder}/${data.first}`);
          set("last_frame", `${data.subfolder}/${data.last}`);
          set("cut_duration", data.gap_seconds ?? 0);
          sync();
          await repaint();
        })
      );

      // --- one frame into a chosen slot -----------------------------------
      const grabInto = (slot, label) => {
        const button = node.addWidget("button", label, null, () =>
          busy(button, "Grabbing…", async () => {
            const response = await api.fetchApi("/secondunit/grab", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                where: widget("where")?.value ?? "playhead",
                track: widget("track")?.value ?? 1,
                clip_index: widget("clip_index")?.value ?? 0,
              }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data?.error || "Could not grab that frame.");
            set(slot, `${data.subfolder}/${data.name}`);
            // A lone frame has no cut gap behind it; clear any older duration
            // so the node's seconds output cannot go stale.
            set("cut_duration", 0);
            await repaint();
          })
        );
        return button;
      };

      // --- upload into a chosen slot --------------------------------------
      // ComfyUI's built-in uploader binds one per node, and this node has two
      // slots, so each gets its own.
      const uploadInto = (slot, label) => {
        const button = node.addWidget("button", label, null, () => {
          const picker = document.createElement("input");
          picker.type = "file";
          picker.accept = "image/png,image/jpeg,image/webp";
          picker.style.display = "none";
          document.body.appendChild(picker);
          picker.onchange = () =>
            busy(button, "Uploading…", async () => {
              const file = picker.files?.[0];
              picker.remove();
              if (!file) return;
              const form = new FormData();
              form.append("image", file);
              form.append("subfolder", "second-unit");
              form.append("type", "input");
              form.append("overwrite", "false");
              const response = await api.fetchApi("/upload/image", { method: "POST", body: form });
              const data = await response.json();
              if (!response.ok) throw new Error(data?.error || "Upload failed.");
              set(slot, data.subfolder ? `${data.subfolder}/${data.name}` : data.name);
              set("cut_duration", 0);
              await repaint();
            });
          picker.click();
        });
        return button;
      };

      const grabFirst = grabInto("first_frame", "Grab into first");
      const grabLast = grabInto("last_frame", "Grab into last");
      const upFirst = uploadInto("first_frame", "Upload into first");
      const upLast = uploadInto("last_frame", "Upload into last");

      // --- show only what applies ------------------------------------------
      function sync() {
        const two = widget("second_frame")?.value !== false;
        const perClip = (widget("where")?.value ?? "playhead") !== "playhead";

        for (const [w, visible] of [
          [widget("last_frame"), two],
          [grabLast, two],
          [upLast, two],
          [widget("which"), two],
          [widget("cut_index"), two && widget("which")?.value === "by index"],
          [cutButton, two],
          [widget("track"), perClip],
          [widget("clip_index"), perClip],
        ]) {
          if (w) w.hidden = !visible;
        }
        node.setDirtyCanvas(true);
      }

      for (const name of ["second_frame", "where", "which"]) {
        const w = widget(name);
        if (!w) continue;
        const previous = w.callback;
        w.callback = function () {
          previous?.apply(this, arguments);
          sync();
          if (name === "second_frame") repaint();
        };
      }
      sync();
    };

    const configure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      configure?.apply(this, arguments);
      // Graphs saved before cut_duration existed can restore a null into it
      // (button widgets serialize as null and shift the positional mapping),
      // and core rejects float(None) at queue time. A non-number here always
      // means "no cut grabbed yet", i.e. 0.
      const duration = this.widgets?.find((w) => w.name === "cut_duration");
      if (duration && typeof duration.value !== "number") duration.value = 0;
      setTimeout(() => this.widgets?.find((w) => w.name === "second_frame")?.callback?.(), 0);
    };
  },
});
