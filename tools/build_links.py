#!/usr/bin/env python3
"""Build short video addresses for printed QR codes.

Reads   config/links.json   {code: {title, urls: [first choice, backups...]}}
Writes  _site/v/<code>/index.html   a page that forwards to the chosen URL
        _site/v/report.json         what was checked and what was chosen

    python tools/build_links.py            # forward to the first URL, no checking
    python tools/build_links.py --check    # test every URL and forward to the first that loads (CI)

A URL "loads" if it answers 200. YouTube watch pages answer 200 even for removed
videos, so those are tested through YouTube's oEmbed endpoint, which answers 404
for a removed or private video. Sites that refuse robots (403, 429) count as
"unknown": used only if no URL in the list loads. Standard library only.
"""
import argparse, html, json, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"

def status(url):
    probe = url
    host = urllib.parse.urlparse(url).netloc
    if "youtube.com" in host or "youtu.be" in host:
        probe = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
    req = urllib.request.Request(probe, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0

def verdict(code):
    if code == 200: return "ok"
    if code in (401, 403, 405, 429) or code >= 500 or code == 0: return "unknown"
    return "dead"

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="0; url={go}">
<title>{title}</title>
<style>body{{font:17px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:34rem;padding:0 1rem}}a{{display:block;margin:.6rem 0}}</style>
</head><body>
<p>Opening <strong>{title}</strong>&hellip;</p>
<p>If nothing happens, tap a link:</p>
{links}
<script>location.replace({go_js});</script>
</body></html>
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "_site"))
    a = ap.parse_args()
    links = {k: v for k, v in json.loads((ROOT / "config" / "links.json").read_text()).items() if not k.startswith("_")}
    out = Path(a.out) / "v"
    report, problems = {}, []
    for code, spec in sorted(links.items()):
        urls = spec["urls"]
        checks = [(u, status(u)) for u in urls] if a.check else [(u, None) for u in urls]
        ok = [u for u, s in checks if s is not None and verdict(s) == "ok"]
        unk = [u for u, s in checks if s is not None and verdict(s) == "unknown"]
        go = (ok or unk or urls)[0]
        if a.check and not ok:
            problems.append(f"{code}: no URL answered 200 ({', '.join(f'{s}' for _, s in checks)})")
        if a.check and go != urls[0]:
            problems.append(f"{code}: first choice skipped; forwarding to {go}")
        d = out / code; d.mkdir(parents=True, exist_ok=True)
        items = "\n".join(f'<a href="{html.escape(u)}">{html.escape(urllib.parse.urlparse(u).netloc)}</a>' for u in urls)
        (d / "index.html").write_text(PAGE.format(go=html.escape(go), go_js=json.dumps(go), title=html.escape(spec["title"]), links=items))
        if a.check:
            print(f"{code} -> {go}  [" + ", ".join(f"{urllib.parse.urlparse(u).netloc}:{s}" for u, s in checks) + "]")
        report[code] = {"title": spec["title"], "chosen": go, "checks": [{"url": u, "status": s} for u, s in checks]}
    (out / "report.json").write_text(json.dumps(report, indent=1))
    print(f"{len(links)} short links written to {out}")
    for p in problems: print("CHECK BY HAND:", p)
    summary = Path(__import__("os").environ.get("GITHUB_STEP_SUMMARY", "/dev/null"))
    if problems and str(summary) != "/dev/null":
        with summary.open("a") as f:
            f.write("### Video links to check by hand\n" + "\n".join(f"- {p}" for p in problems) + "\n")

if __name__ == "__main__":
    main()
