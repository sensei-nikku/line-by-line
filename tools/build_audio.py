#!/usr/bin/env python3
"""Build the audio-guide site.

Reads   content/<course>/<day>.md   (narration scripts)
        config/pronunciations.json  (how to say codes and acronyms)
        site/                       (static player files)
Writes  _site/                      (the finished site; never committed)

Every track becomes _site/audio/<course>/<day>/tNN.m4a (AAC) plus tNN.json. The JSON
holds the start and end time of every line, so the reader highlights exactly
what the voice is saying. Tracks whose text has not changed are reused from the
previous build (CI restores _site/audio from its cache).

    python tools/build_audio.py                  # Kokoro voice (what CI runs)
    python tools/build_audio.py --engine espeak  # quick robotic test voice
    python tools/build_audio.py --force          # rebuild every track
"""
import argparse, hashlib, json, re, shutil, subprocess, sys, tempfile, wave
from pathlib import Path

import numpy as np
from num2words import num2words

ROOT = Path(__file__).resolve().parent.parent
CONTENT, STATIC = ROOT / "content", ROOT / "site"
PRON_FILE = ROOT / "config" / "pronunciations.json"
SR = 24000                                         # Kokoro's sample rate
# The voice reads a whole paragraph at once, so pauses between sentences are its own.
# Clips are trimmed to the speech itself, so these gaps are the only added pauses.
GAP_PARA, GAP_PAUSE = 0.32, 0.60                   # seconds: between paragraphs, before a card
GAP_VOICE = 0.15                                   # when the speaker changes (was 0.04; turns ran together)
TRIM_DB = -40                                      # anything quieter than this at the ends is cut
KOKORO_VOICE, KOKORO_SPEED = "af_heart", 1.0       # calm American English voice, normal speed
AUDIO_EXT, AUDIO_BITRATE = ".m4a", "40k"            # AAC: plays on every phone and browser
SLUG = re.compile(r"^[a-z0-9-]+$")
PRON = {k: v for k, v in json.loads(PRON_FILE.read_text()).items() if not k.startswith("_")}


# ---------------------------------------------------------------- parsing
VOICED = re.compile(r'@([A-Za-z][\w-]*)\s*"|"')

def split_voices(line, spk):
    """Split a line into (speaker, text) segments. @Name"..." is spoken by Name;
    everything else by the narrator (None). spk is the speaker of a quote left
    open by the previous line. Returns (segments, speaker still open)."""
    segs, pos, buf = [], 0, ""
    for m in VOICED.finditer(line):
        if m[1] and spk is None:                       # @Name" opens a voiced quote
            buf += line[pos:m.start()]
            if buf.strip():
                segs.append((None, buf))
            buf, spk = '"', m[1]
        elif m[1] is None and spk is not None:         # closing quote
            buf += line[pos:m.end()]
            segs.append((spk, buf))
            buf, spk = "", None
        else:                                          # an ordinary quote mark
            buf += line[pos:m.start()] + '"'
        pos = m.end()
    buf += line[pos:]
    if buf.strip():
        segs.append((spk, buf))
    return segs, spk

def parse(md):
    """Return (title, tracks). A track is {n, title, items}; an item is a
    line {k:'line', text, p, segs} or a pause {k:'pause', label}."""
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    m = re.search(r"^#\s+(.+)$", md, re.M)
    title = m[1].strip() if m else "Untitled"
    tracks, cur, para = [], None, 0
    for block in re.split(r"\n\s*\n", md):
        b = re.sub(r"\s+", " ", block).strip()
        h = re.match(r"^##\s+Track\s+(\d+):\s*(.+)$", b)
        if h:
            cur = {"n": int(h[1]), "title": h[2].strip(), "items": []}
            tracks.append(cur)
            continue
        if not b or cur is None or b.startswith("#") or b in ("---", "[END]"):
            continue
        pz = re.match(r"^\[PAUSE(?::\s*(.*))?\]$", b)
        if pz:
            cur["items"].append({"k": "pause", "label": (pz[1] or "Your turn.").strip()})
            continue
        b = re.sub(r"\*\*?(.+?)\*\*?", r"\1", b)            # strip markdown emphasis
        para += 1
        spk = None
        for sent in re.split(r"(?:(?<=[.!?][\"”])|(?<=[.!?]))\s+(?=[\"“@A-Z0-9$])", b):
            if sent.strip():
                segs, spk = split_voices(sent.strip(), spk)
                text = re.sub(r'@[A-Za-z][\w-]*\s*(?=")', "", sent.strip())
                cur["items"].append({"k": "line", "text": text, "p": para, "segs": segs})
    return title, tracks


# ------------------------------------------------------- text -> speech text
def _words(n):
    return num2words(n).replace(" and ", " ")

def _money(m):
    d = int(m[1].replace(",", ""))
    out = f"{_words(d)} dollar{'s' if d != 1 else ''}"
    if m[2] and int(m[2]):
        out += f" and {_words(int(m[2]))} cents"
    return out

def _decimal(m):
    head = "" if m[1] == "0" else _words(int(m[1])) + " "
    return head + "point " + " ".join(_words(int(c)) for c in m[2])

def _time(m):
    h, mi = int(m[1]), int(m[2])
    if mi == 0:
        return f"{_words(h)} o'clock"
    return f"{_words(h)} {'oh ' if mi < 10 else ''}{_words(mi)}"

def _number(m):
    n = int(m[0].replace(",", ""))
    if "," not in m[0] and 1900 <= n <= 2099:
        return num2words(n, to="year")
    return _words(n)

def speakable(text):
    s = text
    for k in sorted(PRON, key=len, reverse=True):           # longest first: 2A-R before 2A
        s = re.sub(rf"(?<![\w-]){re.escape(k)}(?![\w-])", PRON[k], s)
    s = re.sub(r"\$(\d[\d,]*)(?:\.(\d\d))?", _money, s)
    s = re.sub(r"\b(\d{1,2}):(\d{2})\b", _time, s)
    s = re.sub(r"(\d+)\.(\d+)%", lambda m: _decimal(m) + " percent", s)
    s = re.sub(r"(\d+)%", lambda m: _words(int(m[1])) + " percent", s)
    s = re.sub(r"\b(\d+)\.(\d+)\b", _decimal, s)
    s = re.sub(r"\b\d{1,3}(?:,\d{3})+\b|\b\d+\b", _number, s)
    return s


# ---------------------------------------------------------------- voices
class Espeak:
    """Robotic test voice. Each Kokoro voice name gets its own pitch so you can
    hear who is talking."""
    name = "espeak"
    def __call__(self, text, voice):
        pitch = str(20 + int(hashlib.md5(voice.encode()).hexdigest(), 16) % 60)
        variant = "en-us+f3" if voice.startswith(("af_", "bf_")) else "en-us"
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            subprocess.run(["espeak-ng", "-v", variant, "-p", pitch, "-s", "150", "-w", f.name, text], check=True)
            with wave.open(f.name) as w:
                sr = w.getframerate()
                a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        x = np.linspace(0, len(a) - 1, int(len(a) * SR / sr))
        return np.interp(x, np.arange(len(a)), a).astype(np.float32), []   # no word timings

class Kokoro:
    name = f"kokoro-{KOKORO_VOICE}-{KOKORO_SPEED}"
    def __init__(self):
        from kokoro import KPipeline
        self.pipe = KPipeline(lang_code="a")
    def __call__(self, text, voice):
        """Return (audio, words): words are (text, start, end) in seconds."""
        parts, words, offset = [], [], 0.0
        for r in self.pipe(text, voice=voice, speed=KOKORO_SPEED):
            a = r.audio
            if a is None:
                continue
            a = a.detach().cpu().numpy() if hasattr(a, "detach") else np.asarray(a)
            for tk in r.tokens or []:
                if tk.start_ts is not None and tk.end_ts is not None:
                    words.append((tk.text, offset + tk.start_ts, offset + tk.end_ts))
            parts.append(a)
            offset += len(a) / SR
        audio = np.concatenate(parts).astype(np.float32) if parts else np.zeros(SR // 4, np.float32)
        return audio, words


# ---------------------------------------------------------------- build
def silence(sec):
    return np.zeros(int(sec * SR), np.float32)

def trim(a, pad=0.02):
    """Cut the silence the voice adds before and after each clip."""
    frame = int(SR * 0.01)
    n = len(a) // frame
    if n == 0:
        return a, 0.0
    rms = np.sqrt((a[:n * frame].reshape(n, frame) ** 2).mean(1) + 1e-12)
    loud = np.where(rms > rms.max() * 10 ** (TRIM_DB / 20))[0]
    if loud.size == 0:
        return a, 0.0
    start = max(0, loud[0] * frame - int(pad * SR))
    end = min(len(a), (loud[-1] + 1) * frame + int(pad * SR))
    return a[start:end], start / SR

def speak_paragraph(tts, lines, cast, narrator):
    """Speak a paragraph in as few voice calls as possible: one per run of the
    same speaker. Returns (audio, [(start, end)] for each line, in seconds)."""
    runs = []                                       # [speaker, [(line index, text)]]
    for li, line in enumerate(lines):
        for spk, text in line["segs"]:
            if not re.search(r"\w", text):
                continue
            if runs and runs[-1][0] == spk:
                runs[-1][1].append((li, text.strip()))
            else:
                runs.append([spk, [(li, text.strip())]])
    parts, t = [], 0.0
    span = [[None, None] for _ in lines]
    for spk, pieces in runs:
        if parts:
            parts.append(silence(GAP_VOICE)); t += GAP_VOICE
        spoken, spans = "", []
        for li, text in pieces:
            if spoken:
                spoken += " "
            start = len(spoken)
            spoken += speakable(text)
            spans.append((li, start, len(spoken)))
        audio, words = tts(spoken, cast[spk] if spk else narrator)
        audio, lead = trim(audio)
        dur = len(audio) / SR
        located, pos = [], 0                        # word timings matched to characters
        for w, ws, we in words:
            w = w.strip()
            k = spoken.find(w, pos) if w else -1
            if k >= 0:
                located.append((k, ws - lead, we - lead))
                pos = k + len(w)
        for li, cs, ce in spans:
            inside = [(ws, we) for k, ws, we in located if cs <= k < ce]
            if inside:
                a, b = inside[0][0], inside[-1][1]
            else:                                   # no timings: estimate by length
                a, b = dur * cs / len(spoken), dur * ce / len(spoken)
            a = min(max(0.0, a), dur); b = min(max(a, b), dur)
            old = span[li]
            span[li] = [t + a if old[0] is None else min(old[0], t + a),
                        t + b if old[1] is None else max(old[1], t + b)]
        parts.append(audio); t += dur
    times = [(a if a is not None else t, b if b is not None else t) for a, b in span]
    return (np.concatenate(parts) if parts else silence(0.2)), times

def build_track(tts, track, out_file, cast, narrator):
    groups = []                                     # ("pause", item) or ("para", [line items])
    for it in track["items"]:
        if it["k"] == "pause":
            groups.append(("pause", it))
        elif groups and groups[-1][0] == "para" and groups[-1][1][-1]["p"] == it["p"]:
            groups[-1][1].append(it)
        else:
            groups.append(("para", [it]))
    chunks, t, items, prev = [], 0.0, [], None
    for kind, g in groups:
        if kind == "pause":
            items.append({"k": "pause", "t": round(t + 0.15, 2), "label": g["label"]})
            chunks.append(silence(GAP_PAUSE)); t += GAP_PAUSE
            prev = "pause"
            continue
        if prev == "para":
            chunks.append(silence(GAP_PARA)); t += GAP_PARA
        audio, times = speak_paragraph(tts, g, cast, narrator)
        for line, (a, b) in zip(g, times):
            items.append({"k": "line", "t": round(t + a, 2), "e": round(t + b, 2),
                          "text": line["text"], "p": line["p"]})
        chunks.append(audio); t += len(audio) / SR
        prev = "para"
    chunks.append(silence(0.5)); t += 0.5
    pcm = np.clip(np.concatenate(chunks), -1, 1).astype(np.float32)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1",
                    "-i", "-", "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-movflags", "+faststart",
                    str(out_file)], input=pcm.tobytes(), check=True)
    return items, round(t, 2)

def copy_static(out):
    """Copy site/ into the output, leaving the audio folder alone."""
    for f in STATIC.rglob("*"):
        if f.is_file():
            dest = out / f.relative_to(STATIC)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["kokoro", "espeak"], default="kokoro")
    ap.add_argument("--out", default=str(ROOT / "_site"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    audio_root = out / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    copy_static(out)

    engine_name = Kokoro.name if args.engine == "kokoro" else Espeak.name
    tts, keep = None, set()
    manifest = {"courses": []}
    for cdir in sorted(p for p in CONTENT.iterdir() if p.is_dir()):
        if not SLUG.match(cdir.name):
            sys.exit(f"Course folder '{cdir.name}' must be lowercase letters, numbers, and dashes.")
        meta = json.loads((cdir / "course.json").read_text()) if (cdir / "course.json").exists() else {}
        course = {"id": cdir.name, "title": meta.get("title", cdir.name.upper()), "days": []}
        cast_file = cdir / "cast.json"
        cast = {k: v for k, v in json.loads(cast_file.read_text()).items() if not k.startswith("_")} if cast_file.exists() else {}
        narrator = cast.get("Narrator", KOKORO_VOICE)
        for script in sorted(cdir.glob("*.md")):
            if not SLUG.match(script.stem):
                sys.exit(f"Script '{script.name}' must be named like w05-d1.md.")
            title, tracks = parse(script.read_text(encoding="utf-8"))
            ddir = audio_root / cdir.name / script.stem
            ddir.mkdir(parents=True, exist_ok=True)
            day = {"id": script.stem, "title": title, "tracks": []}
            for tr in tracks:
                audio_file = ddir / f"t{tr['n']:02d}{AUDIO_EXT}"
                js = audio_file.with_suffix(".json")
                keep.update({audio_file, js})
                speakers = {spk for i in tr["items"] if i["k"] == "line" for spk, _ in i["segs"] if spk}
                missing = sorted(sp for sp in speakers if sp not in cast)
                if missing:
                    sys.exit(f"{script.name}: add {', '.join(missing)} to {cdir.name}/cast.json")
                voices = {"Narrator": narrator, **{sp: cast[sp] for sp in sorted(speakers)}}
                spoken = [[(sp, speakable(t)) for sp, t in i["segs"]] if i["k"] == "line" else "[P]" + i["label"]
                          for i in tr["items"]]
                key = hashlib.sha256(json.dumps([engine_name, voices, AUDIO_EXT, AUDIO_BITRATE, "paragraph", GAP_PARA, GAP_PAUSE, GAP_VOICE, TRIM_DB, tr["title"], spoken,
                                                 [i.get("text") for i in tr["items"]]]).encode()).hexdigest()[:16]
                old = json.loads(js.read_text()) if js.exists() else {}
                label = f"{cdir.name}/{script.stem} track {tr['n']}: {tr['title']}"
                if not args.force and old.get("hash") == key and audio_file.exists():
                    print(f"  same   {label}")
                    data = old
                else:
                    if tts is None:
                        tts = Kokoro() if args.engine == "kokoro" else Espeak()
                    print(f"  build  {label}", flush=True)
                    items, dur = build_track(tts, tr, audio_file, cast, narrator)
                    data = {"course": cdir.name, "day": script.stem, "dayTitle": title, "n": tr["n"],
                            "title": tr["title"], "duration": dur, "hash": key, "engine": engine_name, "audio": audio_file.name, "voices": voices,
                            "items": items}
                    js.write_text(json.dumps(data, indent=1), encoding="utf-8")
                day["tracks"].append({"n": tr["n"], "title": tr["title"], "duration": data["duration"]})
            course["days"].append(day)
        manifest["courses"].append(course)

    # drop audio for tracks or days that no longer exist, so the cache doesn't grow forever
    for f in list(audio_root.rglob("*")):
        if f.is_file() and f.suffix in (".mp3", ".m4a", ".json") and f.parent != audio_root and f not in keep:
            f.unlink()
            print(f"  remove {f.relative_to(audio_root)}")
    (audio_root / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print("done")

if __name__ == "__main__":
    sys.exit(main())
