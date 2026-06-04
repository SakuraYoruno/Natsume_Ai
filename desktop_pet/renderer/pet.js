const MODEL_PATH = "../assets/live2d/edge_vts_model/%E5%AE%85%E5%AE%B6%E5%B0%91%E5%A5%B3.model3.json";

const canvas = document.getElementById("stage");
const bubble = document.getElementById("bubble");
const app = new PIXI.Application({
  view: canvas,
  autoStart: true,
  resizeTo: window,
  backgroundAlpha: 0,
  antialias: true,
  transparent: true,
});

let model = null;
let base = {
  x: 0,
  y: 0,
  rotation: 0,
  scale: 1,
};
let tween = null;

function reportState(extra = {}) {
  if (window.desktopPet) {
    window.desktopPet.reportState({
      loaded: Boolean(model),
      model: "edge_vts_model",
      ...extra,
    });
  }
}

function showBubble(text) {
  if (!bubble) return;
  const value = String(text || "").trim();
  bubble.textContent = value || "我在这里";
}

function fitModel() {
  if (!model) return;
  const width = Math.max(1, window.innerWidth);
  const height = Math.max(1, window.innerHeight);
  model.scale.set(1);
  if (model.anchor && typeof model.anchor.set === "function") {
    model.anchor.set(0.5, 0.5);
  }
  const naturalWidth = Math.max(1, model.width);
  const naturalHeight = Math.max(1, model.height);
  const scale = Math.min(width / naturalWidth, height / naturalHeight) * 0.9;
  model.scale.set(scale);
  model.x = width * 0.5;
  model.y = height * 0.56;
  base = {
    x: model.x,
    y: model.y,
    rotation: 0,
    scale,
  };
  reportState({
    modelMetrics: {
      naturalWidth,
      naturalHeight,
      scale,
      x: model.x,
      y: model.y,
      anchor: model.anchor ? { x: model.anchor.x, y: model.anchor.y } : null,
    },
  });
}

function easing(t) {
  return 1 - Math.pow(1 - t, 3);
}

function animateTo(delta, duration = 260) {
  if (!model) return;
  const start = performance.now();
  const from = {
    x: model.x,
    y: model.y,
    rotation: model.rotation,
  };
  const peak = {
    x: base.x + (delta.dx || 0),
    y: base.y + (delta.dy || 0),
    rotation: base.rotation + (delta.rotation || 0),
  };
  tween = (now) => {
    const elapsed = now - start;
    const half = duration;
    if (elapsed <= half) {
      const k = easing(elapsed / half);
      model.x = from.x + (peak.x - from.x) * k;
      model.y = from.y + (peak.y - from.y) * k;
      model.rotation = from.rotation + (peak.rotation - from.rotation) * k;
      requestAnimationFrame(tween);
      return;
    }
    const backElapsed = elapsed - half;
    if (backElapsed <= half) {
      const k = easing(backElapsed / half);
      model.x = peak.x + (base.x - peak.x) * k;
      model.y = peak.y + (base.y - peak.y) * k;
      model.rotation = peak.rotation + (base.rotation - peak.rotation) * k;
      requestAnimationFrame(tween);
      return;
    }
    model.x = base.x;
    model.y = base.y;
    model.rotation = base.rotation;
    tween = null;
  };
  requestAnimationFrame(tween);
}

function availableExpressions() {
  const expressions = model?.internalModel?.settings?.expressions || [];
  return expressions.map((item) => String(item.name || item.Name || "")).filter(Boolean);
}

function applyExpression(expression) {
  if (!model || !expression || typeof model.expression !== "function") return;
  const desired = String(expression).toLowerCase();
  const names = availableExpressions();
  const match = names.find((name) => name.toLowerCase().includes(desired));
  try {
    if (match) {
      model.expression(match);
    } else if (names.length) {
      model.expression(names[Math.floor(Math.random() * names.length)]);
    }
  } catch {
    // Expression support differs per model; motion feedback still carries the action.
  }
}

function playAnyMotion(preferred) {
  if (!model || typeof model.motion !== "function") return;
  const motions = model.internalModel?.settings?.motions || {};
  const groups = Object.keys(motions);
  const group = groups.find((item) => item.toLowerCase().includes(String(preferred || "").toLowerCase())) || groups[0];
  if (!group) return;
  try {
    model.motion(group, 0);
  } catch {
    // Some models do not expose motions through pixi-live2d-display.
  }
}

function touchProfile(touchType) {
  const key = String(touchType || "head").toLowerCase();
  if (key === "poke") return { dx: 24, dy: 0, rotation: 0.12, motion: "tap" };
  if (key === "body") return { dx: 0, dy: 24, rotation: -0.08, motion: "tap" };
  if (key === "hair") return { dx: -16, dy: -10, rotation: -0.1, motion: "flick" };
  if (key === "idle") return { dx: 0, dy: 0, rotation: 0, motion: "idle" };
  return { dx: 0, dy: -24, rotation: 0.08, motion: "tap" };
}

function handleAction(payload = {}) {
  const action = String(payload.action || "touch").toLowerCase();
  if (action === "click_through") {
    reportState({ clickThrough: Boolean(payload.enabled), lastAction: payload });
    return;
  }
  if (action === "touch" || action === "emote" || action === "speak") {
    const profile = touchProfile(payload.touch_type || payload.touchType);
    showBubble(payload.text || payload.expression || payload.emotion_tag || "摸到了");
    document.body.classList.remove("pet-pop");
    void document.body.offsetWidth;
    document.body.classList.add("pet-pop");
    applyExpression(payload.expression || payload.emotion_tag);
    playAnyMotion(payload.motion || profile.motion);
    animateTo(profile);
  }
  reportState({ lastAction: payload });
}

async function boot() {
  try {
    const Live2DModel = window.PIXI?.live2d?.Live2DModel || window.PIXI?.Live2DModel || window.Live2DModel;
    if (!Live2DModel || typeof Live2DModel.from !== "function") {
      throw new Error("pixi-live2d-display did not expose Live2DModel.from");
    }
    model = await Live2DModel.from(MODEL_PATH, { autoInteract: false });
    app.stage.addChild(model);
    fitModel();
    document.body.classList.add("ready");
    document.body.classList.add("model-visible");
    reportState({ loaded: true });
  } catch (error) {
    document.getElementById("loading").textContent = `Live2D load failed: ${String(error && error.message || error)}`;
    document.body.classList.add("ready");
    reportState({ loaded: false, error: String(error && error.message || error) });
  }
}

window.addEventListener("resize", fitModel);
window.desktopPet?.onAction(handleAction);

canvas.addEventListener("pointerup", (event) => {
  if (event.button === 2) {
    window.desktopPet?.setClickThrough(true);
    return;
  }
  handleAction({ action: "touch", touch_type: "head", expression: "HAPPY", source: "local_pointer" });
});

boot();
