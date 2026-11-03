# Audio Guides

Line-reader audio walkthroughs for class packets. A student opens a track (from a QR code or the index page). The voice reads the script, the current line is highlighted and centered, and the audio stops at each card until the student taps "I'm done."

Running it is free. GitHub Pages hosts the site, and GitHub Actions makes the audio with [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), an open-source voice under the Apache 2.0 license. It needs no API keys or subscriptions.

## Layout
```
content/<course>/<day>.md     narration scripts (the only thing you edit routinely)
content/<course>/course.json  the course's display title
content/<course>/cast.json    story characters and their voices
config/pronunciations.json    how the voice says codes and acronyms
tools/build_audio.py          turns scripts into audio + timing files
site/                         the player (HTML, CSS, JS)
.github/workflows/            builds the audio and deploys the site
_site/                        build output (not committed)
```

## One-time setup
1. Create a **public** GitHub repo and push these files to `main`.
2. Go to **Settings → Pages → Build and deployment → Source** and choose **GitHub Actions**.
3. Open the **Actions** tab and watch **Build audio and deploy**. The first run takes about 10 to 15 minutes because it downloads the voice. Later runs only rebuild tracks whose text changed.
4. The site is at `https://<username>.github.io/<repo>/`.

## Links for QR codes
- One track: `https://<username>.github.io/<repo>/player.html?d=fm/w05-d1&t=2`
  - `d` is the course folder and the script name, without `.md`.
  - `t` is the track number.
- One course's track list: `https://<username>.github.io/<repo>/?c=fm`

## Editing
- **Change the narration:** edit `content/<course>/<day>.md` and push.
- **Add a day:** add `content/fm/w05-d2.md`. Zero-pad the week number (`w05`, not `w5`) so the weeks stay in order.
- **Add a course:** add a folder such as `content/apcs/` with a `course.json`.
- **Fix a pronunciation:** add the word to `config/pronunciations.json` and push.
- **Change the voice or speed:** edit `KOKORO_VOICE` / `KOKORO_SPEED` at the top of `tools/build_audio.py`.

Folder and file names must use lowercase letters, numbers, and dashes only.

## Script format
```
# Day title

## Track 0: Start Here

A paragraph. Each sentence becomes one line in the reader.

[PAUSE: Do card 1A.]
```
Write numbers the way students should see them (`$1,195`, `3.5%`, `MB`). The build converts them to speech.

### Stories with character voices
Put `@Name` right before a quote to give it that character's voice:
```
@Marisol"He stays late to fix his mistakes," she said.
```
Everything else is read by the Narrator. Each course lists its characters and their voices in `content/<course>/cast.json`. A name that isn't in the cast stops the build with a message saying who to add. Two Kokoro voices joined with a comma (`"am_michael,am_fenrir"`) blend into a new voice.

## Local test (optional)
```
pip install numpy num2words
python tools/build_audio.py --engine espeak     # robotic test voice; needs espeak-ng and ffmpeg
npx http-server _site                            # any server that supports seeking
```
Python's built-in server doesn't support seeking, so the pauses won't work with it.

## License
The code is MIT licensed (see `LICENSE`). The narration scripts in `content/` are all rights reserved.
