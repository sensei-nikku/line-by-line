#!/usr/bin/env python3
"""Open each candidate video page in real Chrome, press play, and record whether the
video actually advances. Writes checks/results.json plus a before/after screenshot
per URL. A URL passes only if some <video> on the page (or in its player iframe)
reaches currentTime > 2 s with no media error."""
import json, re
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "checks"; OUT.mkdir(exist_ok=True)
CANDS = json.loads((ROOT / "tools" / "video_candidates.json").read_text())

PROBE = """async () => {
  const vs = [...document.querySelectorAll('video')];
  if (!vs.length) return null;
  const v = vs.sort((a,b)=> (b.clientWidth*b.clientHeight)-(a.clientWidth*a.clientHeight))[0];
  v.muted = true;
  try { await Promise.race([v.play(), new Promise(r => setTimeout(r, 4000))]); } catch (e) {}
  await new Promise(r => setTimeout(r, 8000));
  return {t: v.currentTime, d: v.duration, rs: v.readyState, paused: v.paused,
          err: v.error ? v.error.code : null, src: (v.currentSrc || v.src || '').slice(0, 120)};
}"""
PLAY_SELECTORS = ['.ytp-large-play-button', '.vjs-big-play-button', 'button[aria-label*="Play" i]',
                  '[class*="play-button" i]', '[class*="PlayButton" i]', 'button[title*="Play" i]',
                  '.play', '[data-play-button]']
CONSENT = ['Accept Cookies', 'Continue', 'Accept all', 'Accept All', 'Accept', 'I agree', 'I Agree', 'Got it', 'OK', 'Close']

def try_consent(page):
    for t in CONSENT:
        try: page.get_by_role('button', name=t, exact=True).first.click(timeout=700)
        except Exception: pass

def probe_frames(page):
    best = None
    for f in page.frames:
        if f.is_detached(): continue
        try: r = f.evaluate(PROBE)
        except Exception as e: continue
        if r:
            r['frame'] = f.url[:160]
            if best is None or (r.get('t') or 0) > (best.get('t') or 0): best = r
    return best

def click_play(page):
    clicked = []
    for f in page.frames:
        for sel in PLAY_SELECTORS:
            try:
                loc = f.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=1500); clicked.append(f"{sel} in {f.url[:60]}"); return clicked
            except Exception: pass
    # fall back: click the middle of the biggest iframe that looks like a player
    for el in page.query_selector_all('iframe'):
        src = (el.get_attribute('src') or '')
        if re.search(r'youtube|vimeo|player|wistia|brightcove|jwplayer|pbs|video|embed', src, re.I):
            try:
                el.scroll_into_view_if_needed(timeout=2000); box = el.bounding_box()
                if box and box['width'] > 200:
                    page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                    clicked.append('iframe center ' + src[:80]); return clicked
            except Exception: pass
    return clicked

results = {}
with sync_playwright() as p:
    b = p.chromium.launch(channel='chrome', headless=True,
                          args=['--autoplay-policy=no-user-gesture-required'])
    ctx = b.new_context(viewport={'width': 1280, 'height': 900}, locale='en-US')
    ctx.set_default_timeout(20000)
    for code, spec in CANDS.items():
        results[code] = {'title': spec['title'], 'checks': []}
        for i, url in enumerate(spec['urls']):
            r = {'url': url}
            page = ctx.new_page()
            try:
                resp = page.goto(url, wait_until='domcontentloaded', timeout=45000)
                r['status'] = resp.status if resp else None
                page.wait_for_timeout(7000); try_consent(page)
                r['page_title'] = page.title()[:120]
                r['iframes'] = [f.url[:160] for f in page.frames if f != page.main_frame][:8]
                page.screenshot(path=str(OUT / f'{code}-{i}-a.png'))
                v = probe_frames(page)
                if not v or (v.get('t') or 0) <= 2:
                    r['clicked'] = click_play(page)
                    page.wait_for_timeout(3000)
                    v = probe_frames(page) or v
                r['video'] = v
                page.screenshot(path=str(OUT / f'{code}-{i}-b.png'))
                # keep watching: past any sponsor message, into the video itself
                page.wait_for_timeout(15000); try_consent(page)
                page.screenshot(path=str(OUT / f'{code}-{i}-c.png'))
                page.wait_for_timeout(25000)
                page.screenshot(path=str(OUT / f'{code}-{i}-d.png'))
                r['video_later'] = probe_frames(page)
                body = page.inner_text('body')[:20000]
                r['unavailable_text'] = [m for m in ['Video unavailable', 'This video is unavailable', "isn't available",
                    'not available', 'Sign in to confirm', 'Page not found', '404', 'Private video', 'removed']
                    if m.lower() in body.lower()]
                r['pass'] = bool(v and (v.get('t') or 0) > 2 and not v.get('err'))
            except Exception as e:
                r['error'] = str(e)[:300]; r['pass'] = False
            finally:
                page.close()
            results[code]['checks'].append(r)
            print(code, i, 'PASS' if r['pass'] else 'FAIL', url, json.dumps(r.get('video'))[:200], r.get('error', ''))
    b.close()
(OUT / 'results.json').write_text(json.dumps(results, indent=1))
