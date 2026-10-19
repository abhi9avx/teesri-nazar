// Two moments on the home page: the hero shot and the run replay. Plus the theme toggle on every page.
// Without JavaScript, or with reduced motion, both show their finished state.
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

function replay(stage) {
  const lines = [...stage.querySelectorAll(".log li")];
  const screens = new Map([...stage.querySelectorAll(".phone-screen")].map((s) => [s.dataset.name, s]));
  const phone = stage.querySelector(".phone");
  const ring = stage.querySelector(".tap-ring");
  const toggle = stage.querySelector("#stage-toggle");
  let i = 0;
  let timers = [];
  let running = false;
  let heldByUser = false;

  const show = (name) => screens.forEach((s, key) => s.classList.toggle("is-active", key === name));
  const later = (fn, ms) => timers.push(setTimeout(fn, ms));

  function tapAt(id) {
    const target = phone.querySelector(`[data-id="${id}"]`);
    if (!target) return;
    const p = phone.getBoundingClientRect();
    const t = target.getBoundingClientRect();
    ring.style.setProperty("--x", `${t.left - p.left + t.width / 2}px`);
    ring.style.setProperty("--y", `${t.top - p.top + t.height / 2}px`);
    ring.hidden = false;
    ring.getAnimations().forEach((a) => { a.cancel(); a.play(); });
  }

  function step() {
    timers = [];
    if (i === lines.length) i = 0;
    const line = lines[i];
    lines.forEach((l, n) => {
      l.classList.toggle("is-done", n < i);
      l.classList.toggle("is-current", n === i);
    });
    if (line.dataset.tap) {
      tapAt(line.dataset.tap);
      later(() => show(line.dataset.screen), 450);
    } else {
      show(line.dataset.screen);
    }
    i += 1;
    const wait = i === lines.length ? 4200 : line.classList.contains("log-you") ? 2200 : 1300;
    later(step, wait);
  }

  function start() {
    if (running) return;
    running = true;
    stage.classList.add("is-playing");
    toggle.textContent = "Pause replay";
    if (i > 0) show(lines[i - 1].dataset.screen);
    later(step, 400);
  }

  function stop() {
    timers.forEach(clearTimeout);
    timers = [];
    running = false;
    toggle.textContent = "Play replay";
  }

  stage.classList.add("is-ready");
  show("home");
  if (reduceMotion) return;
  toggle.hidden = false;
  toggle.addEventListener("click", () => {
    heldByUser = running;
    if (running) stop();
    else start();
  });
  // Runs only while the replay is on screen, and stays paused once the reader pauses it.
  new IntersectionObserver(([entry]) => {
    if (!entry.isIntersecting) stop();
    else if (!heldByUser) start();
  }).observe(stage);
}

// The hero shot: the arrow leaves the bow and passes through the ten heads; each head it
// reaches gets its badge and lights up its check. The finished state is the page default.
function shot(figure) {
  const svg = figure.querySelector("svg");
  const path = svg.querySelector("#shot-path");
  const trail = svg.querySelector(".trail");
  const flying = svg.querySelector(".arrow-flying");
  const hits = [...svg.querySelectorAll(".hit")];
  const checks = [...figure.querySelectorAll(".checks li")];
  const button = figure.querySelector("#shoot");
  const callout = figure.querySelector("#callout");
  const string = svg.querySelector("#bowstring");
  const restArrow = svg.querySelector(".arrow-rest use");
  const [stringTop, , stringBottom] = string.getAttribute("points").split(" ");
  const nockY = Number(string.getAttribute("points").split(" ")[1].split(",")[1]);
  const drawn = Number(string.dataset.drawn);
  const rest = Number(string.dataset.rest);
  const arrowLength = Number(restArrow.getAttribute("transform").match(/translate\(([\d.]+)/)[1]) - drawn;
  // The nock is where the string meets the arrow; the arrow rides on it until release.
  const setNock = (x, withArrow) => {
    string.setAttribute("points", `${stringTop} ${x},${nockY} ${stringBottom}`);
    if (withArrow) restArrow.setAttribute("transform", `translate(${x + arrowLength} ${nockY})`);
  };
  const length = path.getTotalLength();
  trail.style.strokeDasharray = length;

  let inFlight = false;
  const verdict = figure.querySelector("#verdict");
  const verdictText = verdict.textContent;
  let clearCallout = 0;

  function arm() {
    clearTimeout(clearCallout);
    verdict.textContent = "";
    figure.classList.remove("is-celebrating");
    figure.classList.remove("is-done");
    hits.concat(checks).forEach((el) => el.classList.remove("is-hit"));
    trail.style.strokeDashoffset = length;
    flying.style.opacity = 0;
    callout.className = "callout";
    setNock(drawn, true);
  }

  function strike(n) {
    const hit = hits[n];
    hit.classList.add("is-hit");
    checks[n].classList.add("is-hit");
    callout.textContent = `Head ${n + 1}: ${hit.dataset.label}`;
    callout.className = `callout is-shown ${hit.classList.contains("hit-bug") ? "is-bug" : "is-pass"}`;
    figure.classList.remove("is-struck");
    void figure.offsetWidth;
    figure.classList.add("is-struck");
  }

  function fire() {
    if (inFlight) return;
    inFlight = true;
    arm();
    button.setAttribute("aria-disabled", "true");
    const begin = performance.now() + 500;
    const duration = 2600;
    const a = 0.25; // speeds up off the bow, then flies at a steady pace
    let held = 0; // time spent holding on heads, which does not count as flight
    let holdUntil = 0;
    let last = begin;
    let next = 0;
    // Draw: the string creeps back for a beat. Release: it snaps past straight and rings down.
    const pull = 12;
    const drawStart = performance.now();
    function bow(now) {
      const since = now - drawStart;
      if (since < 450) {
        const e = since / 450;
        setNock(drawn - pull * e * e, true);
      } else {
        if (!figure.classList.contains("is-firing")) figure.classList.add("is-firing");
        const r = since - 450;
        const swing = (drawn - pull - rest) * Math.exp(-r / 70) * Math.cos(r / 22);
        setNock(rest + swing, false);
        if (r > 600) { setNock(rest, false); return; }
      }
      requestAnimationFrame(bow);
    }
    requestAnimationFrame(bow);
    function frame(now) {
      if (now < holdUntil) {
        held += now - last;
        last = now;
        requestAnimationFrame(frame);
        return;
      }
      last = now;
      const t = Math.min(1, Math.max(0, (now - begin - held) / duration));
      const d = length * (t < a ? (t * t) / (a * (2 - a)) : (2 * t - a) / (2 - a));
      const p = path.getPointAtLength(d);
      const q = path.getPointAtLength(Math.min(length, d + 1));
      const angle = (Math.atan2(q.y - p.y, q.x - p.x) * 180) / Math.PI;
      flying.setAttribute("transform", `translate(${p.x} ${p.y}) rotate(${angle})`);
      flying.style.opacity = t > 0 ? 1 : 0;
      trail.style.strokeDashoffset = length - d;
      if (next < hits.length && p.x >= Number(hits[next].dataset.x)) {
        strike(next);
        next += 1;
        holdUntil = now + 420; // a beat on each head, long enough to read what it found
      }
      if (t < 1 || next < hits.length) requestAnimationFrame(frame);
      else {
        // The arrow hangs past the last head for a beat, then fades as the verdict rises.
        clearCallout = setTimeout(() => {
          callout.className = "callout";
          figure.classList.remove("is-firing", "is-struck");
          figure.classList.add("is-done");
          figure.classList.add("is-celebrating");
          verdict.textContent = verdictText;
          button.removeAttribute("aria-disabled");
          inFlight = false;
        }, 700);
      }
    }
    requestAnimationFrame(frame);
  }

  if (reduceMotion) return;
  button.hidden = false;
  button.addEventListener("click", () => { io.disconnect(); fire(); });
  const io = new IntersectionObserver(([entry]) => {
    if (!entry.isIntersecting) return;
    io.disconnect();
    fire();
  }, { threshold: 0.4 });
  arm();
  // Transitions start only after arming, so the page does not fade from the finished pose on load.
  requestAnimationFrame(() => figure.classList.add("is-live"));
  io.observe(svg);
}

function themeToggle(button) {
  const root = document.documentElement;
  const label = button.querySelector(".theme-label");
  const sync = () => {
    const dark = root.dataset.theme === "dark";
    button.setAttribute("aria-pressed", String(dark));
    label.textContent = dark ? "Dark" : "Light";
    document.querySelector('meta[name="theme-color"]').content = dark ? "#0A0A0A" : "#F27A1A";
  };
  button.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (e) { /* private mode: the choice lasts this page only */ }
    sync();
  });
  sync();
}

const stage = document.getElementById("stage");
if (stage) replay(stage);
const hero = document.getElementById("shot");
if (hero) shot(hero);
themeToggle(document.getElementById("theme-toggle"));

// Copy buttons on docs code blocks.
document.querySelectorAll(".docs pre").forEach((pre) => {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy";
  button.textContent = "Copy";
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText((pre.querySelector("code") || pre).textContent.trim());
      button.textContent = "Copied";
    } catch (e) {
      button.textContent = "Select and copy";
    }
    setTimeout(() => { button.textContent = "Copy"; }, 1600);
  });
  pre.prepend(button);
});

// Copy-to-clipboard buttons (the email address).
document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = "Copied";
    } catch (e) {
      button.textContent = "Select it";
    }
    setTimeout(() => { button.textContent = "Copy"; }, 1600);
  });
});
