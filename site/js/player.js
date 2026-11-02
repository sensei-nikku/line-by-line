(() => {
  const qs = new URLSearchParams(location.search);
  const raw = qs.get('d') || '';
  const day = /^[a-z0-9-]+\/[a-z0-9-]+$/.test(raw) ? raw : '';   // course/day, e.g. fm/w05-d1
  const course = day.split('/')[0];
  const n = parseInt(qs.get('t') || '0', 10);
  const pad = String(n).padStart(2, '0');
  const base = `audio/${day}/t${pad}`;
  const $ = id => document.getElementById(id);
  const audio = $('audio'), reader = $('reader');
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  };
  const posKey = `lr:${day}:${n}`;

  let items = [], lines = [], pauses = [], cur = -1, waiting = null;
  let lastUserScroll = 0, autoScrolling = false;
  const speeds = [1, 0.9, 0.8, 1.15];
  let speedIdx = Math.max(0, speeds.indexOf(parseFloat(store.get('lr:speed') || '1')));

  $('home').href = course ? `./?c=${course}` : './';
  fetch(`${base}.json`).then(r => { if (!day || !r.ok) throw new Error('missing'); return r.json(); }).then(data => {
    document.title = `${data.title} | ${data.dayTitle}`;
    $('day').textContent = data.dayTitle;
    $('track').textContent = `Track ${data.n}: ${data.title}`;
    items = data.items;
    let lastP = null;
    items.forEach((it, i) => {
      const el = document.createElement('div');
      if (it.k === 'line') {
        el.className = 'line next' + (lastP !== null && it.p !== lastP ? ' para' : '');
        el.textContent = it.text;
        el.addEventListener('click', () => seekLine(lines.indexOf(it), true));
        lastP = it.p; it.el = el; lines.push(it);
      } else {
        el.className = 'pause';
        el.innerHTML = '<span class="pz">Your turn</span>';
        el.append(document.createTextNode(' ' + it.label));
        it.el = el; it.armed = true; pauses.push(it); lastP = null;
      }
      reader.appendChild(el);
    });
    audio.src = `${base}.mp3`;
    audio.playbackRate = speeds[speedIdx];
    $('speed').textContent = speeds[speedIdx] + '×';
    const saved = parseFloat(store.get(posKey) || '0');
    if (saved > 3 && saved < data.duration - 2) {
      audio.currentTime = saved;
      armPausesAfter(saved);
    }
    setLine(lineAt(audio.currentTime || 0), true);
    loadNextTrack(data);
  }).catch(() => {
    reader.innerHTML = '<div class="line now">This track isn’t ready yet.</div>';
  });

  function loadNextTrack(data) {
    fetch('audio/manifest.json').then(r => r.json()).then(m => {
      const c = m.courses.find(x => x.id === course);
      const d = c && c.days.find(x => `${course}/${x.id}` === day);
      const nx = d && d.tracks.find(t => t.n === n + 1);
      if (nx) {
        const a = $('nextTrack');
        a.href = `player.html?d=${day}&t=${nx.n}`;
        a.textContent = `Next: Track ${nx.n}, ${nx.title} →`;
      }
    }).catch(() => {});
  }

  function lineAt(t) {
    let idx = 0;
    for (let i = 0; i < lines.length; i++) { if (lines[i].t <= t + 0.05) idx = i; else break; }
    return idx;
  }

  function setLine(i, instant) {
    if (i === cur || !lines[i]) return;
    cur = i;
    lines.forEach((l, j) => { l.el.className = 'line ' + (j < i ? 'past' : j === i ? 'now' : 'next') + (l.el.classList.contains('para') ? ' para' : ''); });
    if (Date.now() - lastUserScroll > 4000) center(lines[i].el, instant);
  }

  function center(el, instant) {
    autoScrolling = true;
    el.scrollIntoView({ block: 'center', behavior: instant ? 'auto' : 'smooth' });
    setTimeout(() => { autoScrolling = false; }, 700);
    $('follow').hidden = true;
  }

  function armPausesAfter(t) { pauses.forEach(p => { p.armed = p.t > t + 0.05; p.el.classList.remove('active'); }); }

  function seekLine(i, play) {
    i = Math.max(0, Math.min(lines.length - 1, i));
    clearWait();
    audio.currentTime = lines[i].t;
    armPausesAfter(lines[i].t);
    lastUserScroll = 0;
    setLine(i, false); center(lines[i].el, false);
    if (play) audio.play();
  }

  function wait(p) {
    waiting = p; p.armed = false;
    audio.pause();
    audio.currentTime = p.t;
    p.el.classList.add('active');
    center(p.el, false);
    $('turnLabel').textContent = p.label;
    $('turn').hidden = false;
    document.body.classList.add('waiting');
  }
  function clearWait() {
    if (!waiting) return;
    waiting.el.classList.remove('active');
    waiting = null;
    $('turn').hidden = true;
    document.body.classList.remove('waiting');
  }

  function tick() {
    const t = audio.currentTime;
    const p = pauses.find(p => p.armed && t >= p.t);
    if (p) { wait(p); return; }
    setLine(lineAt(t), false);
    if (audio.duration) $('fill').style.width = (100 * t / audio.duration) + '%';
    if (!audio.paused) requestAnimationFrame(tick);
  }

  audio.addEventListener('play', () => { clearWait(); $('play').innerHTML = '&#10074;&#10074;'; $('play').setAttribute('aria-label', 'Pause'); requestAnimationFrame(tick); });
  audio.addEventListener('pause', () => { $('play').innerHTML = '&#9654;'; $('play').setAttribute('aria-label', 'Play'); store.set(posKey, audio.currentTime); });
  audio.addEventListener('seeked', tick);
  audio.addEventListener('ended', () => {
    store.set(posKey, 0);
    if ($('nextTrack').href) $('nextTrack').hidden = false;
  });

  $('play').addEventListener('click', () => { if (audio.paused) audio.play(); else audio.pause(); });
  $('done').addEventListener('click', () => { clearWait(); audio.currentTime += 0.2; audio.play(); });
  $('prev').addEventListener('click', () => {
    const t = audio.currentTime, i = lineAt(t);
    seekLine(t - lines[i].t > 1.5 ? i : i - 1, !audio.paused);
  });
  $('next').addEventListener('click', () => seekLine(lineAt(audio.currentTime) + 1, !audio.paused));
  $('speed').addEventListener('click', () => {
    speedIdx = (speedIdx + 1) % speeds.length;
    audio.playbackRate = speeds[speedIdx];
    $('speed').textContent = speeds[speedIdx] + '×';
    store.set('lr:speed', speeds[speedIdx]);
  });
  $('follow').addEventListener('click', () => { lastUserScroll = 0; if (lines[cur]) center(lines[cur].el, false); });
  reader.addEventListener('scroll', () => {
    if (autoScrolling) return;
    lastUserScroll = Date.now();
    if (!audio.paused) $('follow').hidden = false;
  }, { passive: true });
  document.addEventListener('keydown', e => {
    if (e.key === ' ') { e.preventDefault(); waiting ? $('done').click() : $('play').click(); }
    if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') $('prev').click();
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') $('next').click();
  });
})();
