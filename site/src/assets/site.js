// The home page moves in three places: the funnel in the hero, the cube's ticker and the phone in
// "Watch one case run". Without JavaScript every section still reads, without the motion; with
// reduced motion each shows its finished state. "Pause animations" in the hero stops all three.
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const motion = { paused: false, resume: [] };

// Runs fn only while el is on screen and motion is not paused; fn gets called again on resume.
function whileVisible(el, onShow) {
  const state = { on: true };
  new IntersectionObserver(([e]) => {
    const was = state.on; state.on = e.isIntersecting;
    if (state.on && !was && !motion.paused) onShow();
  }).observe(el);
  motion.resume.push(() => { if (state.on) onShow(); });
  return () => state.on && !motion.paused;
}

function rng(seed) { return () => (seed = (seed * 16807) % 2147483647) / 2147483647; }
const ease = (x) => 1 - Math.pow(1 - Math.min(Math.max(x, 0), 1), 3);

// The hero: test cases at the top of a slowly turning cone, the bugs they find in the middle,
// and each bug dropping into the eye at the tip. The pointer turns and tilts the cone.
function funnel(canvas) {
  const YS = [0.16, 0.52, 0.84];
  const APPEAR = [0, 0.6, 1.2]; // seconds: when each layer settles in
  const FOCAL = 1100;
  const css = getComputedStyle(document.documentElement);
  const col = { dot: css.getPropertyValue("--dot").trim(), edge: css.getPropertyValue("--edge").trim(), bg: css.getPropertyValue("--bg").trim() };
  const view = { yaw: 0, pitch: 0.34, tYaw: 0, tPitch: 0.34 };
  let s, layers, all, tcs, bugs, edges, pulses, stacked, cy;

  function build() {
    const dpr = Math.min(devicePixelRatio || 1, 2), r = canvas.getBoundingClientRect();
    canvas.width = r.width * dpr; canvas.height = r.height * dpr;
    const ctx = canvas.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    s = { ctx, w: r.width, h: r.height, cx: r.width / 2 };
    stacked = innerWidth < 1100;
    cy = s.h * 0.5;
    const R = rng(23);
    const rad = [Math.min(s.w * (stacked ? 0.44 : 0.42), 400), Math.min(s.w * (stacked ? 0.2 : 0.18), 170), 0];
    const count = stacked ? [170, 26, 1] : [360, 40, 1];
    const band = [0.07, 0.05, 0];
    layers = count.map((n, li) => Array.from({ length: n }, () => {
      const a = R() * Math.PI * 2, rr = n === 1 ? 0 : rad[li] * Math.sqrt(0.08 + 0.92 * R());
      return { X: Math.cos(a) * rr, Z: Math.sin(a) * rr, a, rr, Y: s.h * (YS[li] + (R() * 2 - 1) * band[li]) - cy,
        d: R() * 0.35, size: li === 0 ? (R() < 0.15 ? 2.3 : 1.6) : 2.4 };
    }));
    s.r0 = rad[0];
    all = layers.flat();
    const turn = (q, want) => Math.abs(Math.atan2(Math.sin(q.a - want), Math.cos(q.a - want)));
    const nearest = (pool, want) => pool.reduce((b, q) => (turn(q, want) < turn(b, want) ? q : b));
    const nTc = stacked ? 6 : 10, outer = layers[0].filter((p) => p.rr > rad[0] * 0.5);
    tcs = Array.from({ length: nTc }, (_, i) => ({ p: nearest(outer, (i / nTc) * Math.PI * 2), name: "TC-" + String(i + 1).padStart(2, "0") }));
    const mid = layers[1].filter((p) => p.rr > rad[1] * 0.35);
    bugs = [0.3, 2.4, 4.4].map((want, i) => { const p = nearest(mid, want); p.bug = "BUG-" + (i + 1); return p; });
    const tip = layers[2][0];
    edges = [];
    for (const p of layers[0]) {
      const near = layers[1].slice().sort((a, b) => Math.hypot(a.X - p.X, a.Z - p.Z) - Math.hypot(b.X - p.X, b.Z - p.Z));
      for (let j = 0; j < 2; j++) edges.push([p, near[Math.floor(R() * 3)], 0]);
    }
    for (const p of layers[1]) edges.push([p, tip, p.bug ? 2 : 1]);
    pulses = Array.from({ length: stacked ? 6 : 12 }, () => ({ e: edges[Math.floor(R() * edges.length * 0.8)], o: R() }));
  }

  function project() {
    const ca = Math.cos(view.yaw), sa = Math.sin(view.yaw), cb = Math.cos(view.pitch), sb = Math.sin(view.pitch);
    for (const p of all) {
      const x1 = p.X * ca + p.Z * sa, z1 = -p.X * sa + p.Z * ca;
      const y2 = p.Y * cb - z1 * sb, z2 = p.Y * sb + z1 * cb, k = FOCAL / (FOCAL + z2);
      p.sx = s.cx + x1 * k; p.sy = cy + y2 * k; p.depth = z1;
    }
  }

  function eye(ctx, x, y, glow) {
    const g = ctx.createRadialGradient(x, y, 0, x, y, 64);
    g.addColorStop(0, `rgba(242,122,26,${0.4 * glow})`); g.addColorStop(1, "rgba(242,122,26,0)");
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, 64, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath();
    ctx.moveTo(x - 18, y); ctx.quadraticCurveTo(x, y - 16, x + 18, y); ctx.quadraticCurveTo(x, y + 16, x - 18, y);
    ctx.fillStyle = col.bg; ctx.fill();
    ctx.lineWidth = 1.6; ctx.strokeStyle = "#F27A1A"; ctx.stroke();
    ctx.beginPath(); ctx.arc(x, y, 4.8, 0, Math.PI * 2); ctx.fillStyle = "#F27A1A"; ctx.fill();
  }

  function draw(t) {
    const { ctx, w, h } = s, tip = layers[2][0];
    if (!reduceMotion) {
      view.yaw += (t * 0.07 + view.tYaw - view.yaw) * 0.06;
      view.pitch += (view.tPitch - view.pitch) * 0.06;
    }
    project();
    ctx.clearRect(0, 0, w, h);
    const shown = (li) => ease((t - APPEAR[li]) / 0.8);
    const fog = (p) => 0.45 + 0.55 * (1 - (p.depth / s.r0 + 1) / 2); // the far side of the cone is dimmer

    ctx.lineWidth = 0.6;
    for (const [a, b, kind] of edges) {
      const k = shown(kind === 0 ? 1 : 2);
      if (k <= 0) continue;
      ctx.strokeStyle = kind === 2 ? `rgba(210,57,43,${0.45 * k})` : `rgba(${col.edge},${(kind === 1 ? 0.2 : 0.1) * k * fog(a)})`;
      ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(a.sx + (b.sx - a.sx) * k, a.sy + (b.sy - a.sy) * k); ctx.stroke();
    }
    layers.slice(0, 2).forEach((L, li) => {
      for (const p of L) {
        const k = ease((t - APPEAR[li] - p.d) / 0.7);
        if (k <= 0) continue;
        const y = p.sy - (1 - k) * 24;
        if (p.bug) { ctx.fillStyle = `rgba(210,57,43,${k})`; ctx.fillRect(p.sx - 2.4, y - 2.4, 4.8, 4.8); continue; }
        ctx.fillStyle = `rgba(${col.dot},${(li === 1 ? 0.9 : 0.6) * k * fog(p)})`;
        ctx.fillRect(p.sx - p.size / 2, y - p.size / 2, p.size, p.size);
      }
    });

    ctx.textAlign = "left";
    ctx.font = "11px 'IBM Plex Mono', monospace";
    const tk = reduceMotion ? 1 : ease((t - 2.0) / 0.9);
    if (tk > 0) for (const { p, name } of tcs) {
      const a = tk * fog(p);
      ctx.fillStyle = `rgba(${col.dot},${a})`; ctx.fillRect(p.sx - 1.8, p.sy - 1.8, 3.6, 3.6);
      const right = p.sx + 6 + ctx.measureText(name).width < w - 4; // near the edge, the label goes on the dot's left
      ctx.fillStyle = `rgba(${col.dot},${0.7 * a})`; ctx.fillText(name, right ? p.sx + 6 : p.sx - 6 - ctx.measureText(name).width, p.sy - 5);
    }
    const bk = reduceMotion ? 1 : ease((t - 1.6) / 0.8);
    if (bk > 0) { ctx.fillStyle = `rgba(232,96,79,${bk})`; for (const p of bugs) ctx.fillText(p.bug, p.sx + 7, p.sy - 6); }

    if (!reduceMotion && t > 2.4) {
      for (const p of pulses) {
        const k = (p.o + (t - 2.4) * 0.09) % 1, [a, b] = p.e;
        ctx.fillStyle = `rgba(242,122,26,${0.8 * Math.sin(k * Math.PI)})`;
        ctx.fillRect(a.sx + (b.sx - a.sx) * k - 1.1, a.sy + (b.sy - a.sy) * k - 1.1, 2.2, 2.2);
      }
    }

    const te = shown(2);
    if (te <= 0) return;
    let flare = 0;
    if (reduceMotion || t >= 2.4) bugs.forEach((p, i) => {
      const k = reduceMotion ? 0.55 : ((t - 2.4) * 0.32 + i / bugs.length) % 1;
      for (let j = 3; j >= 0; j--) {
        const kk = Math.max(0, k - j * 0.035), sz = 3.6 - j * 0.6;
        ctx.fillStyle = `rgba(210,57,43,${(1 - j * 0.28) * Math.min(1, k * 6)})`;
        ctx.fillRect(p.sx + (tip.sx - p.sx) * kk - sz / 2, p.sy + (tip.sy - p.sy) * kk - sz / 2, sz, sz);
      }
      if (k > 0.9) flare = Math.max(flare, (k - 0.9) * 10);
    });
    ctx.globalAlpha = te;
    eye(ctx, tip.sx, tip.sy, reduceMotion ? 0.5 : 0.3 + 0.7 * flare);
    ctx.globalAlpha = 1;
    ctx.textAlign = "center"; ctx.font = "12px 'IBM Plex Mono', monospace";
    if ("letterSpacing" in ctx) ctx.letterSpacing = "3px";
    ctx.fillStyle = `rgba(${col.dot},${te})`; ctx.fillText("AI AGENT", tip.sx + 1.5, tip.sy + 32);
    ctx.fillStyle = `rgba(242,122,26,${te})`; ctx.fillText("TEESRI NAZAR", tip.sx + 1.5, tip.sy + 50);
    if ("letterSpacing" in ctx) ctx.letterSpacing = "0px";
  }

  build();
  new ResizeObserver(() => { build(); if (reduceMotion) draw(9); }).observe(canvas);
  if (reduceMotion) { draw(9); return; }
  const hero = canvas.closest(".hero");
  hero.addEventListener("pointermove", (e) => {
    const r = hero.getBoundingClientRect();
    view.tYaw = ((e.clientX - r.left) / r.width - 0.5) * 1.6;
    view.tPitch = 0.34 + ((e.clientY - r.top) / r.height - 0.5) * 0.4;
  });
  hero.addEventListener("pointerleave", () => { view.tYaw = 0; view.tPitch = 0.34; });
  // One loop at most, and its clock only runs while it draws, so a pause resumes where it stopped.
  let looping = false, elapsed = 0, last = 0;
  const loop = () => { if (!looping) { looping = true; last = 0; requestAnimationFrame(frame); } };
  const running = whileVisible(canvas, loop);
  function frame(now) {
    if (!running()) { looping = false; return; }
    if (last) elapsed += Math.min(now - last, 50) / 1000;
    last = now;
    draw(elapsed);
    requestAnimationFrame(frame);
  }
  loop();
}

// "Watch one case run": the phone's elements are looked at one by one, beside the commands that do it.
function phoneDemo(root) {
  const SCRIPT = [
    ["cmd", 'tnazar say "TC-01 pay with saved card"', null],
    ["cmd", "tnazar observe", [0, "look"]],
    ["", "6 elements · fast read 0.05 s", [1, "look"]],
    ["cmd", 'tnazar tap desc="Saved card •• 4242"', [2, "look"]],
    ["cmd", 'tnazar type "1240" --into id=amount_input', [3, "look"]],
    ["cmd", "tnazar tap id=btn_pay --wait 10", [4, "look"]],
    ["cmd", 'tnazar check "Payment successful" --for E1', [5, "look"]],
    ["pass", "✓ E1 verified · screenshot saved", [5, "ok"]],
    ["cmd", 'tnazar check "Pay now" --for E2', [4, "look"]],
    ["fail", "✗ E2 found “Pay Now”, expected “Pay now”", [4, "bad"]],
    ["cmd", "tnazar case end", null],
    ["sum", "TC-01 failed · verified 1/2 · report ready", null],
  ];
  const term = root.querySelector(".term"), els = [...root.querySelectorAll(".el")];
  const line = ([cls, text]) => { const d = document.createElement("div"); d.className = cls; d.textContent = text; term.append(d); };
  if (reduceMotion) {
    SCRIPT.forEach((row) => { line(row); if (row[2] && row[2][1] !== "look") els[row[2][0]].classList.add(row[2][1]); });
    return;
  }
  let i = 0;
  const running = whileVisible(root, () => {});
  const step = () => {
    if (!running()) { setTimeout(step, 500); return; }
    if (i === 0) { term.textContent = ""; els.forEach((e) => e.classList.remove("look", "ok", "bad")); }
    const row = SCRIPT[i];
    line(row);
    els.forEach((e) => e.classList.remove("look"));
    if (row[2]) els[row[2][0]].classList.add(row[2][1]);
    i = (i + 1) % SCRIPT.length;
    setTimeout(step, i === 0 ? 3200 : 900);
  };
  step();
}

// The cube: which input and device are in play, the case it is on, and the report filling up.
function cubeTicker(flow) {
  const STEPS = [
    ["TC-01 · PAY WITH SAVED CARD", "checking E1", 0, false],
    ["TC-01 · PAY WITH SAVED CARD", "E1 verified → next case", 1, false],
    ["TC-04 · PAY ₹0", "checking E1", 2, false],
    ["TC-04 · PAY ₹0", "app crashed → failed, with the log", 3, true],
    ["TC-08 · KILL AND REOPEN", "E2 verified", 0, false],
    ["REPORT", "verified 9/10 → back to you", 1, false],
  ];
  const tCase = flow.querySelector(".ticker-case"), tWhat = flow.querySelector(".ticker-what");
  const ins = [...flow.querySelectorAll(".flow-in .flow-tile")], devs = [...flow.querySelectorAll(".flow-dev .flow-tile")];
  const hot = flow.querySelector(".wire-hot"), prog = flow.querySelector(".cube-progress");
  let k = 0;
  const tick = () => {
    const [c, w, src, fail] = STEPS[k];
    tCase.textContent = c; tWhat.textContent = w; tWhat.classList.toggle("fail", fail);
    ins.forEach((t, i) => t.classList.toggle("live", i === src));
    devs.forEach((t, i) => t.classList.toggle("live", i === k % 2));
    hot.setAttribute("d", src % 2 === 0 ? "M250 0 C250 60 500 40 500 100" : "M750 0 C750 60 500 40 500 100");
    hot.animate([{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], { duration: 900, fill: "forwards", easing: "ease-out" });
    prog.animate([{ strokeDashoffset: 1 }, { strokeDashoffset: 1 - (k + 1) / STEPS.length }], { duration: 900, fill: "forwards", easing: "ease-out" });
    k = (k + 1) % STEPS.length;
  };
  if (reduceMotion) { hot.style.strokeDashoffset = 0; prog.style.strokeDashoffset = 0.2; return; } // a report most of the way full
  const running = whileVisible(flow, () => {});
  tick();
  setInterval(() => { if (running()) tick(); }, 2200);
}

// "What changes for the team": one panel per role, arrow keys move between tabs.
function roleTabs(root) {
  const tabs = [...root.querySelectorAll('[role="tab"]')];
  const select = (tab) => {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    });
  };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => select(t));
    t.addEventListener("keydown", (e) => {
      const to = { ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
      if (to === undefined) return;
      e.preventDefault();
      const next = tabs[(to + tabs.length) % tabs.length];
      select(next); next.focus();
    });
  });
  // Without JavaScript every panel shows; with it, only the selected one.
  select(tabs[0]);
}

function motionToggle(button) {
  button.hidden = false;
  button.addEventListener("click", () => {
    motion.paused = !motion.paused;
    button.textContent = motion.paused ? "Play animations" : "Pause animations";
    if (!motion.paused) motion.resume.forEach((fn) => fn());
  });
}

const heroCanvas = document.querySelector(".hero canvas");
if (heroCanvas) funnel(heroCanvas);
const watch = document.getElementById("watch");
if (watch) phoneDemo(watch);
const flow = document.querySelector(".flow");
if (flow) cubeTicker(flow);
const roles = document.querySelector(".roles");
if (roles) roleTabs(roles);
const toggle = document.querySelector(".motion-toggle");
if (toggle && !reduceMotion) motionToggle(toggle);

// One reset timer per button, so rapid clicks do not flicker the label.
function copyButton(button, text, failLabel) {
  let reset = 0;
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text());
      button.textContent = "Copied";
    } catch (e) {
      button.textContent = failLabel;
    }
    clearTimeout(reset);
    reset = setTimeout(() => { button.textContent = "Copy"; }, 1600);
  });
}

// Copy buttons on docs code blocks, in a wrapper so they stay put while the code scrolls.
document.querySelectorAll(".docs pre").forEach((pre) => {
  const wrap = document.createElement("div");
  wrap.className = "code-block";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy";
  button.setAttribute("aria-live", "polite");
  button.textContent = "Copy";
  copyButton(button, () => (pre.querySelector("code") || pre).textContent.trim(), "Select and copy");
  pre.before(wrap);
  wrap.append(pre, button);
});

// Copy-to-clipboard buttons (the email address).
document.querySelectorAll("[data-copy]").forEach((button) => copyButton(button, () => button.dataset.copy, "Select it"));

// On phones the docs menu is one scrolling row: bring the current page into view.
const docsNav = document.querySelector(".docs-nav");
const docsCurrent = docsNav && docsNav.querySelector('[aria-current="page"]');
if (docsCurrent && docsNav.scrollWidth > docsNav.clientWidth) {
  docsNav.scrollLeft = docsCurrent.offsetLeft - (docsNav.clientWidth - docsCurrent.offsetWidth) / 2;
}
