import './style.css';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { VRMLoaderPlugin, VRMUtils, VRMHumanBoneName } from '@pixiv/three-vrm';

const canvas = document.getElementById('avatar-canvas');
const modelStatus = document.getElementById('model-status');
const bridgeStatus = document.getElementById('bridge-status');
const emotionStatus = document.getElementById('emotion-status');
const motionStatus = document.getElementById('motion-status');

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xf5f7fb);

const camera = new THREE.PerspectiveCamera(28, window.innerWidth / window.innerHeight, 0.1, 100);
camera.position.set(0.0, 1.35, 4.4);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 1.25, 0);
controls.enableDamping = true;
controls.enablePan = false;
controls.minDistance = 2.1;
controls.maxDistance = 7.0;

const keyLight = new THREE.DirectionalLight(0xffffff, 2.2);
keyLight.position.set(1.2, 2.2, 2.4);
scene.add(keyLight);
scene.add(new THREE.HemisphereLight(0xffffff, 0xd8d3ff, 2.1));

const floor = new THREE.Mesh(
  new THREE.CircleGeometry(1.25, 48),
  new THREE.MeshBasicMaterial({ color: 0xe8eef8, transparent: true, opacity: 0.8 }),
);
floor.rotation.x = -Math.PI / 2;
floor.position.y = 0.02;
scene.add(floor);

const clock = new THREE.Clock();
const ARM_REST = {
  upperDrop: THREE.MathUtils.degToRad(41),
  upperForward: THREE.MathUtils.degToRad(-14),
  upperOut: THREE.MathUtils.degToRad(20),
  lowerRelax: THREE.MathUtils.degToRad(8),
};
const runtime = {
  vrm: null,
  bones: new Map(),
  baseQuats: new Map(),
  modelRoot: null,
  mouthTarget: 0,
  mouthValue: 0,
  talking: false,
  emotion: 'NEUTRAL',
  emotionIntensity: 0,
  motionValues: {},
  motionValuesReceivedAt: -999,
  motionReleaseS: 0.55,
  frameMotionValues: {},
  modelMove: { x: 0, y: 0, rotation: 0, size: 0 },
  baseModelYaw: Math.PI,
  bodyAction: null,
  idleImpulse: null,
  nextIdleImpulseAt: 0.8,
  gaze: { x: 0, y: 0, targetX: 0, targetY: 0, nextAt: 1.2 },
  motionPhase: 'idle',
};

loadModel();
connectBridge();
wireControls();
animate();

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

async function loadModel() {
  modelStatus.textContent = 'loading';
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  loader.load(
    '/model.vrm',
    (gltf) => {
      const vrm = gltf.userData.vrm;
      VRMUtils.removeUnnecessaryVertices(gltf.scene);
      VRMUtils.removeUnnecessaryJoints(gltf.scene);
      VRMUtils.rotateVRM0(vrm);
      runtime.vrm = vrm;
      runtime.modelRoot = vrm.scene;
      runtime.modelRoot.position.set(0, 0, 0);
      runtime.modelRoot.rotation.y = runtime.baseModelYaw;
      scene.add(runtime.modelRoot);
      cacheBones(vrm);
      frameModel(vrm);
      modelStatus.textContent = 'ready';
    },
    undefined,
    (error) => {
      modelStatus.textContent = 'missing';
      console.error(error);
    },
  );
}

function connectBridge() {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${scheme}//${window.location.host}/ws`);
  ws.addEventListener('open', () => {
    bridgeStatus.textContent = 'online';
  });
  ws.addEventListener('close', () => {
    bridgeStatus.textContent = 'offline';
    setTimeout(connectBridge, 1200);
  });
  ws.addEventListener('message', (event) => {
    try {
      applyCommand(JSON.parse(event.data));
    } catch (error) {
      console.warn('bad bridge command', error);
    }
  });
}

function wireControls() {
  document.querySelectorAll('button[data-action]').forEach((button) => {
    button.addEventListener('click', () => {
      const action = button.dataset.action;
      if (action === 'expression') {
        applyCommand({
          type: 'expression',
          emotion_tag: button.dataset.emotion || 'NEUTRAL',
          intensity: 0.85,
        });
      } else if (action === 'body') {
        startBodyAction(button.dataset.body || 'idle_shift', 0.9, 2.2);
      } else if (action === 'reset') {
        applyCommand({ type: 'reset' });
      }
    });
  });
}

function applyCommand(command) {
  const type = String(command.type || '');
  if (type === 'bridge_status') {
    bridgeStatus.textContent = command.status || 'online';
    return;
  }
  if (type === 'expression' || type === 'expression_transition') {
    runtime.emotion = String(command.emotion_tag || 'NEUTRAL').toUpperCase();
    runtime.emotionIntensity = clamp01(Number(command.intensity ?? 0.7));
    emotionStatus.textContent = runtime.emotion;
    return;
  }
  if (type === 'talk') {
    runtime.talking = Boolean(command.talking);
    if (!runtime.talking) {
      runtime.mouthTarget = 0;
    }
    return;
  }
  if (type === 'mouth') {
    runtime.mouthTarget = clamp01(Number(command.value || 0));
    runtime.motionValues = normalizeMotionValues(command.motion_values || {});
    runtime.motionValuesReceivedAt = clock.elapsedTime;
    return;
  }
  if (type === 'move_model') {
    const gain = Boolean(command.relative) ? 1 : 0.2;
    runtime.modelMove.x += Number(command.position_x || 0) * gain;
    runtime.modelMove.y += Number(command.position_y || 0) * gain;
    runtime.modelMove.rotation += THREE.MathUtils.degToRad(Number(command.rotation || 0) * gain);
    runtime.modelMove.size += Number(command.size || 0) * 0.05;
    return;
  }
  if (type === 'body_action') {
    startBodyAction(command.action_id || 'idle_shift', Number(command.intensity || 0.75), Number(command.duration_s || 2.0));
    return;
  }
  if (type === 'reset') {
    runtime.talking = false;
    runtime.mouthTarget = 0;
    runtime.emotion = 'NEUTRAL';
    runtime.emotionIntensity = 0;
    runtime.motionValues = {};
    runtime.motionValuesReceivedAt = -999;
    runtime.modelMove = { x: 0, y: 0, rotation: 0, size: 0 };
    runtime.bodyAction = null;
    runtime.idleImpulse = null;
    runtime.nextIdleImpulseAt = clock.elapsedTime + 1.2;
    runtime.gaze = { x: 0, y: 0, targetX: 0, targetY: 0, nextAt: clock.elapsedTime + 1.0 };
    emotionStatus.textContent = runtime.emotion;
  }
}

function animate() {
  requestAnimationFrame(animate);
  const delta = Math.min(clock.getDelta(), 0.05);
  const elapsed = clock.elapsedTime;
  runtime.mouthValue = THREE.MathUtils.lerp(runtime.mouthValue, runtime.mouthTarget, delta * 18);
  updateAvatar(delta, elapsed);
  controls.update();
  renderer.render(scene, camera);
}

function updateAvatar(delta, elapsed) {
  if (!runtime.vrm) {
    return;
  }
  const idle = makeIdleValues(elapsed);
  updateIdleImpulse(elapsed);
  const impulse = makeIdleImpulseValues(elapsed);
  const action = makeBodyActionValues(elapsed);
  const external = makeExternalMotionValues(elapsed);
  const motion = {
    ...idle,
    ...impulse,
    ...external,
    ...action,
  };
  runtime.frameMotionValues = motion;
  if (motionStatus) {
    motionStatus.textContent = runtime.motionPhase;
  }
  applyBoneMotion(motion, elapsed);
  applyExpressions(elapsed);
  applyModelMove(delta, elapsed);
  runtime.vrm.update(delta);
}

function cacheBones(vrm) {
  const names = [
    VRMHumanBoneName.Hips,
    VRMHumanBoneName.Spine,
    VRMHumanBoneName.Chest,
    VRMHumanBoneName.UpperChest,
    VRMHumanBoneName.Neck,
    VRMHumanBoneName.Head,
    VRMHumanBoneName.LeftUpperArm,
    VRMHumanBoneName.LeftLowerArm,
    VRMHumanBoneName.RightUpperArm,
    VRMHumanBoneName.RightLowerArm,
    VRMHumanBoneName.LeftHand,
    VRMHumanBoneName.RightHand,
  ];
  for (const name of names) {
    const bone = getBone(vrm, name);
    if (bone) {
      runtime.bones.set(name, bone);
      runtime.baseQuats.set(name, bone.quaternion.clone());
    }
  }
}

function getBone(vrm, name) {
  return (
    vrm.humanoid?.getNormalizedBoneNode?.(name)
    || vrm.humanoid?.getRawBoneNode?.(name)
    || null
  );
}

function applyBoneMotion(values, elapsed) {
  const faceX = deg(values.FaceAngleX || 0);
  const faceY = deg(values.FaceAngleY || 0);
  const faceZ = deg(values.FaceAngleZ || 0);
  const bodyX = deg(values.MocopiBodyAngleX || values.MocopiAngleX || 0);
  const bodyY = deg(values.MocopiBodyAngleY || values.MocopiAngleY || 0);
  const bodyZ = deg(values.MocopiBodyAngleZ || values.MocopiAngleZ || 0);
  setBoneEuler(VRMHumanBoneName.Head, faceX * 0.9, faceY * 0.9, faceZ * 0.8);
  setBoneEuler(VRMHumanBoneName.Neck, faceX * 0.22, faceY * 0.22, faceZ * 0.22);
  setBoneEuler(VRMHumanBoneName.Chest, bodyX * 0.32, bodyY * 0.38, bodyZ * 0.32);
  setBoneEuler(VRMHumanBoneName.Spine, bodyX * 0.22, bodyY * 0.24, bodyZ * 0.18);
  setBoneEuler(VRMHumanBoneName.Hips, bodyX * 0.08, bodyY * 0.12, bodyZ * 0.22);

  const leftRaise = Number(values.HandLeftPositionY || 0);
  const rightRaise = Number(values.HandRightPositionY || 0);
  const leftArmY = -ARM_REST.upperOut + deg(values.MocopiUpperArmLeftAngleY || 0) * 0.75 - leftRaise * 0.22;
  const rightArmY = ARM_REST.upperOut + deg(values.MocopiUpperArmRightAngleY || 0) * 0.75 + rightRaise * 0.22;
  const liftScale = runtime.bodyAction ? 1.35 : (runtime.talking ? 0.72 : 0.46);
  const leftUpperZ = ARM_REST.upperDrop + deg(values.MocopiUpperArmLeftAngleZ || values.HandLeftAngleZ || 0) * 0.28 - leftRaise * liftScale;
  const rightUpperZ = -ARM_REST.upperDrop + deg(values.MocopiUpperArmRightAngleZ || values.HandRightAngleZ || 0) * 0.28 + rightRaise * liftScale;
  setBoneEuler(VRMHumanBoneName.LeftUpperArm, ARM_REST.upperForward + leftRaise * -0.18, leftArmY, leftUpperZ);
  setBoneEuler(VRMHumanBoneName.RightUpperArm, ARM_REST.upperForward + rightRaise * -0.18, rightArmY, rightUpperZ);
  setBoneEuler(VRMHumanBoneName.LeftLowerArm, ARM_REST.lowerRelax + deg(values.MocopiLowerArmLeftAngleX || values.HandLeftAngleX || 0) * 0.55, 0, deg(values.HandLeftAngleZ || 0) * 0.25);
  setBoneEuler(VRMHumanBoneName.RightLowerArm, ARM_REST.lowerRelax + deg(values.MocopiLowerArmRightAngleX || values.HandRightAngleX || 0) * 0.55, 0, deg(values.HandRightAngleZ || 0) * 0.25);

  const sway = Math.sin(elapsed * 0.72) * 0.018;
  const root = runtime.modelRoot;
  if (root) {
    root.position.x = THREE.MathUtils.lerp(root.position.x, Number(values.MocopiBodyPositionX || 0) * 0.12, 0.05);
    root.position.y = THREE.MathUtils.lerp(root.position.y, Number(values.MocopiBodyPositionY || 0) * 0.08 + Math.max(0, sway), 0.05);
    root.position.z = THREE.MathUtils.lerp(root.position.z, Number(values.MocopiBodyPositionZ || 0) * 0.08, 0.05);
  }
}

function setBoneEuler(name, x, y, z) {
  const bone = runtime.bones.get(name);
  const base = runtime.baseQuats.get(name);
  if (!bone || !base) {
    return;
  }
  const quat = new THREE.Quaternion().setFromEuler(new THREE.Euler(x, y, z, 'XYZ'));
  bone.quaternion.copy(base).multiply(quat);
}

function applyExpressions(elapsed) {
  const manager = runtime.vrm.expressionManager;
  if (!manager) {
    return;
  }
  const emotion = expressionPreset(runtime.emotion);
  const intensity = clamp01(runtime.emotionIntensity);
  const motion = runtime.frameMotionValues || {};
  for (const name of ['happy', 'angry', 'sad', 'surprised', 'relaxed', 'blink', 'aa', 'ih', 'ou', 'ee', 'oh']) {
    if (manager.getExpressionTrackName?.(name) || manager.expressionMap?.[name] || name === 'aa') {
      manager.setValue(name, 0);
    }
  }
  if (emotion.name) {
    manager.setValue(emotion.name, emotion.weight * intensity);
  }
  const smile = clamp01(Number(motion.MouthSmile || 0));
  if (smile > 0.05 && manager.expressionMap?.surprised) {
    const current = Number(manager.getValue?.('surprised') || 0);
    manager.setValue('surprised', Math.max(current, smile * 0.22));
  }
  const blink = blinkValue(elapsed);
  manager.setValue('blink', blink);
  const emotionalMouth = runtime.emotion === 'HAPPY' || runtime.emotion === 'PLAYFUL' ? 0.045 * intensity : 0;
  const mouth = runtime.talking ? Math.max(runtime.mouthValue, 0.08) : Math.max(runtime.mouthValue, emotionalMouth);
  manager.setValue('aa', clamp01(mouth));
  manager.setValue('ih', clamp01(mouth * 0.16));
  manager.setValue('ou', clamp01(mouth * 0.08));
}

function applyModelMove(delta, elapsed) {
  if (!runtime.modelRoot) {
    return;
  }
  runtime.modelMove.x = THREE.MathUtils.lerp(runtime.modelMove.x, 0, delta * 2.8);
  runtime.modelMove.y = THREE.MathUtils.lerp(runtime.modelMove.y, 0, delta * 2.8);
  runtime.modelMove.rotation = THREE.MathUtils.lerp(runtime.modelMove.rotation, 0, delta * 2.4);
  const breathe = Math.sin(elapsed * 0.95) * 0.012;
  runtime.modelRoot.position.x += runtime.modelMove.x * 0.2;
  runtime.modelRoot.position.y += runtime.modelMove.y * 0.18 + breathe * 0.015;
  runtime.modelRoot.rotation.y = runtime.baseModelYaw + runtime.modelMove.rotation * 0.35;
}

function makeIdleValues(elapsed) {
  updateGaze(elapsed);
  const emotion = String(runtime.emotion || 'NEUTRAL').toUpperCase();
  let energy = 1.0;
  if (['HAPPY', 'PLAYFUL', 'EXCITED', 'SURPRISED'].includes(emotion)) {
    energy = 1.28;
  } else if (['SAD', 'CALM', 'SERIOUS'].includes(emotion)) {
    energy = 0.72;
  } else if (['ANGRY', 'ANXIOUS', 'EMBARRASSED'].includes(emotion)) {
    energy = 1.12;
  }
  const breathe = Math.sin(elapsed * 0.82);
  const slow = Math.sin(elapsed * 0.27 + 0.4);
  const tiny = Math.sin(elapsed * 1.67 + 1.8);
  return {
    FaceAngleX: (breathe * 1.25 + runtime.gaze.y * 3.2 + tiny * 0.24) * energy,
    FaceAngleY: (slow * 1.4 + runtime.gaze.x * 4.4) * energy,
    FaceAngleZ: (Math.sin(elapsed * 0.38 + 1.1) * 0.9 - runtime.gaze.x * 0.7) * energy,
    MocopiBodyAngleX: (breathe * 1.3 + tiny * 0.22) * energy,
    MocopiBodyAngleY: Math.sin(elapsed * 0.37) * 2.3 * energy,
    MocopiBodyAngleZ: Math.sin(elapsed * 0.45 + 0.8) * 1.9 * energy,
    MocopiBodyPositionX: Math.sin(elapsed * 0.31 + 0.7) * 0.028 * energy,
    MocopiBodyPositionY: breathe * 0.052 * energy,
    MocopiBodyPositionZ: Math.sin(elapsed * 0.24 + 1.6) * 0.024 * energy,
    HandLeftPositionY: 0.045 + Math.sin(elapsed * 0.72 + 1.4) * 0.014 * energy,
    HandRightPositionY: 0.05 + Math.sin(elapsed * 0.78 + 0.3) * 0.014 * energy,
    HandLeftAngleX: Math.sin(elapsed * 0.73 + 0.8) * 2.2,
    HandRightAngleX: Math.sin(elapsed * 0.77 + 1.3) * -2.2,
    HandLeftAngleZ: Math.sin(elapsed * 0.62) * 3.6 * energy,
    HandRightAngleZ: Math.sin(elapsed * 0.66 + 0.5) * -3.6 * energy,
    MouthSmile: ['HAPPY', 'PLAYFUL', 'EXCITED'].includes(emotion) ? 0.22 + Math.max(0, tiny) * 0.05 : 0.08,
  };
}

function updateGaze(elapsed) {
  if (elapsed >= runtime.gaze.nextAt) {
    runtime.gaze.targetX = THREE.MathUtils.clamp((Math.random() - 0.5) * 0.8, -0.38, 0.38);
    runtime.gaze.targetY = THREE.MathUtils.clamp((Math.random() - 0.45) * 0.46, -0.18, 0.25);
    runtime.gaze.nextAt = elapsed + 1.6 + Math.random() * 3.4;
  }
  runtime.gaze.x = THREE.MathUtils.lerp(runtime.gaze.x, runtime.gaze.targetX, 0.018);
  runtime.gaze.y = THREE.MathUtils.lerp(runtime.gaze.y, runtime.gaze.targetY, 0.018);
}

function updateIdleImpulse(elapsed) {
  if (runtime.bodyAction || runtime.talking) {
    return;
  }
  if (runtime.idleImpulse && elapsed - runtime.idleImpulse.startedAt <= runtime.idleImpulse.duration) {
    return;
  }
  if (runtime.idleImpulse) {
    runtime.idleImpulse = null;
    runtime.nextIdleImpulseAt = elapsed + 0.8 + Math.random() * 2.1;
  }
  if (elapsed < runtime.nextIdleImpulseAt) {
    return;
  }
  const kinds = ['look_away', 'soft_nod', 'weight_shift', 'small_bounce', 'curious_lean'];
  const kind = kinds[Math.floor(Math.random() * kinds.length)];
  runtime.idleImpulse = {
    kind,
    startedAt: elapsed,
    duration: 1.35 + Math.random() * 1.8,
    intensity: 0.72 + Math.random() * 0.34,
  };
}

function makeIdleImpulseValues(elapsed) {
  const impulse = runtime.idleImpulse;
  if (!impulse) {
    runtime.motionPhase = runtime.talking ? 'talking' : 'idle';
    return {};
  }
  const p = clamp01((elapsed - impulse.startedAt) / impulse.duration);
  const ease = Math.sin(Math.PI * p) * impulse.intensity;
  if (impulse.kind === 'look_away') {
    runtime.motionPhase = 'look';
    return {
      FaceAngleY: 12.0 * ease,
      FaceAngleZ: -3.2 * ease,
      MocopiBodyAngleY: 4.0 * ease,
      EyeLeftX: 0.24 * ease,
      EyeRightX: 0.24 * ease,
    };
  }
  if (impulse.kind === 'soft_nod') {
    runtime.motionPhase = 'nod';
    return {
      FaceAngleX: Math.sin(p * Math.PI * 2.0) * 6.2 * ease,
      MocopiBodyAngleX: 3.8 * ease,
      MocopiBodyPositionY: 0.08 * ease,
    };
  }
  if (impulse.kind === 'hand_adjust') {
    runtime.motionPhase = 'hands';
    return {
      HandLeftPositionY: 0.1 + 0.18 * ease,
      HandRightPositionY: 0.11 + 0.16 * ease,
      HandLeftAngleZ: 12.0 * ease,
      HandRightAngleZ: -12.0 * ease,
      MocopiUpperArmLeftAngleY: -8.0 * ease,
      MocopiUpperArmRightAngleY: 8.0 * ease,
      MouthSmile: 0.13 + 0.11 * ease,
    };
  }
  if (impulse.kind === 'small_bounce') {
    const bounce = Math.max(0, Math.sin(p * Math.PI * 3.0)) * ease;
    runtime.motionPhase = 'bounce';
    return {
      MocopiBodyPositionY: 0.24 * bounce,
      MocopiBodyAngleZ: Math.sin(p * Math.PI * 2) * 4.0 * ease,
      FaceAngleX: 3.8 * bounce,
      HandLeftPositionY: 0.22 + 0.12 * bounce,
      HandRightPositionY: 0.24 + 0.14 * bounce,
      MouthSmile: 0.18 + 0.14 * bounce,
    };
  }
  if (impulse.kind === 'curious_lean') {
    runtime.motionPhase = 'lean';
    return {
      FaceAngleX: 4.4 * ease,
      FaceAngleY: -7.0 * ease,
      MocopiBodyAngleX: 5.2 * ease,
      MocopiBodyPositionZ: 0.16 * ease,
      HandRightPositionY: 0.22 + 0.13 * ease,
      MouthSmile: 0.14 + 0.08 * ease,
    };
  }
  runtime.motionPhase = 'shift';
  return {
    MocopiBodyAngleY: Math.sin(p * Math.PI * 2) * 7.0 * ease,
    MocopiBodyPositionX: Math.sin(p * Math.PI * 2) * 0.12 * ease,
    FaceAngleY: Math.sin(p * Math.PI * 2 + 0.4) * 6.2 * ease,
  };
}

function makeExternalMotionValues(elapsed) {
  const age = elapsed - runtime.motionValuesReceivedAt;
  if (!runtime.motionValues || age > runtime.motionReleaseS) {
    return {};
  }
  const holdS = 0.16;
  const fade = age <= holdS ? 1 : 1 - clamp01((age - holdS) / Math.max(0.05, runtime.motionReleaseS - holdS));
  if (fade <= 0.02) {
    return {};
  }
  const out = {};
  for (const [key, value] of Object.entries(runtime.motionValues)) {
    out[key] = Number(value) * fade;
  }
  runtime.motionPhase = runtime.talking ? 'speech' : 'direct';
  return out;
}

function startBodyAction(actionId, intensity, duration) {
  runtime.bodyAction = {
    actionId,
    intensity: clamp01(intensity),
    startedAt: clock.elapsedTime,
    duration: Math.max(0.4, duration || 2.0),
  };
}

function makeBodyActionValues(elapsed) {
  const action = runtime.bodyAction;
  if (!action) {
    return {};
  }
  runtime.motionPhase = action.actionId;
  const p = clamp01((elapsed - action.startedAt) / action.duration);
  if (p >= 1) {
    runtime.bodyAction = null;
    return {};
  }
  const ease = Math.sin(Math.PI * p) * (0.75 + action.intensity * 0.5);
  if (action.actionId === 'wave') {
    return {
      FaceAngleY: -6 * ease,
      MocopiBodyAngleZ: -5 * ease,
      HandRightPositionY: 0.72 * ease,
      HandRightAngleZ: -32 * ease + Math.sin(p * Math.PI * 8) * 12 * ease,
      MocopiUpperArmRightAngleY: -34 * ease,
      MocopiLowerArmRightAngleX: 28 * ease,
      MouthSmile: 0.46 * ease,
    };
  }
  if (action.actionId === 'happy_bounce') {
    const bounce = Math.max(0, Math.sin(p * Math.PI * 5)) * ease;
    return {
      FaceAngleX: 4 * bounce,
      MocopiBodyPositionY: 0.42 * bounce,
      HandLeftPositionY: 0.36 * ease,
      HandRightPositionY: 0.42 * ease,
      MouthSmile: 0.58 * ease,
    };
  }
  return {
    FaceAngleX: 4 * ease,
    MocopiBodyAngleY: Math.sin(p * Math.PI * 2) * 7 * ease,
    MouthSmile: 0.22 * ease,
  };
}

function normalizeMotionValues(values) {
  const out = {};
  for (const [key, value] of Object.entries(values || {})) {
    const number = Number(value);
    if (Number.isFinite(number)) {
      out[key] = number;
    }
  }
  return out;
}

function expressionPreset(emotion) {
  const tag = String(emotion || 'NEUTRAL').toUpperCase();
  if (['ANGRY', 'FRUSTRATED', 'DISGUSTED'].includes(tag)) {
    return { name: 'angry', weight: 1.0 };
  }
  if (['SURPRISED', 'STARTLED', 'EXCITED'].includes(tag)) {
    return { name: 'surprised', weight: 0.8 };
  }
  if (['SAD', 'SADNESS', 'LONELY'].includes(tag)) {
    return { name: 'sad', weight: 0.75 };
  }
  if (['HAPPY', 'JOY', 'PLAYFUL', 'LOVING', 'AFFECTION', 'GRATEFUL', 'PROUD'].includes(tag)) {
    return { name: 'surprised', weight: 0.28 };
  }
  if (['CALM', 'RELAXED', 'SOOTHING'].includes(tag)) {
    return { name: 'relaxed', weight: 0.55 };
  }
  return { name: null, weight: 0 };
}

function blinkValue(elapsed) {
  const phase = elapsed % 4.8;
  if (phase < 0.12) {
    return Math.sin((phase / 0.12) * Math.PI);
  }
  const second = (elapsed + 0.18) % 7.1;
  if (second < 0.1) {
    return Math.sin((second / 0.1) * Math.PI);
  }
  return 0;
}

function frameModel(vrm) {
  const box = new THREE.Box3().setFromObject(vrm.scene);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  vrm.scene.position.x -= center.x;
  vrm.scene.position.z -= center.z;
  vrm.scene.position.y -= box.min.y;
  const height = Math.max(size.y, 0.1);
  const scale = 1.82 / height;
  vrm.scene.scale.setScalar(scale);
  controls.target.set(0, 1.08, 0);
  camera.position.set(0, 1.35, 4.1);
  controls.update();
}

function clamp01(value) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(1, value));
}

function deg(value) {
  return THREE.MathUtils.degToRad(Number(value || 0));
}
