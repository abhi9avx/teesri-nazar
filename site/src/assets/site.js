// Two moments on the home page: the run replay in the hero, and the arrow in the Dussehra section.
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
    toggle.textContent = "Pause";
    later(step, 400);
  }

  function stop() {
    timers.forEach(clearTimeout);
    timers = [];
    running = false;
    toggle.textContent = "Play";
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
  // Runs only while the hero is on screen, and stays paused once the reader pauses it.
  new IntersectionObserver(([entry]) => {
    if (!entry.isIntersecting) stop();
    else if (!heldByUser) start();
  }).observe(stage);
}

function arrow(section) {
  const button = section.querySelector("#release-arrow");
  const shaft = section.querySelector(".arrow");
  const heads = [...section.querySelectorAll(".heads li")];
  const result = section.querySelector("#arrow-result");
  let shots = 0;
  button.hidden = false;
  button.addEventListener("click", () => {
    shots += 1;
    button.disabled = true;
    shaft.classList.remove("is-flying");
    heads.forEach((h) => h.classList.remove("is-caught"));
    void shaft.offsetWidth;
    shaft.classList.add("is-flying");
    const gap = reduceMotion ? 0 : 110;
    heads.forEach((h, n) => setTimeout(() => h.classList.add("is-caught"), gap * (n + 1)));
    setTimeout(() => {
      result.textContent = shots === 1 ? "All ten caught before release." : `All ten caught again, run ${shots}.`;
      button.textContent = "Shoot again";
      button.disabled = false;
    }, gap * (heads.length + 1));
  });
}

const stage = document.getElementById("stage");
if (stage) replay(stage);
const ravana = document.querySelector(".ravana");
if (ravana) arrow(ravana);
