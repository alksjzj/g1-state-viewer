import * as THREE from './vendor/three.js';
import { OrbitControls } from './vendor/orbit-controls.js';

const $ = id => document.getElementById(id);
const format = (value, digits = 3) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
const vec = (raw, fallback = [0, 0, 0]) => raw ? raw.trim().split(/\s+/).map(Number) : fallback;
const jointChinese = name => name.replace(/^left_/, '左 ').replace(/^right_/, '右 ').replace(/^waist_/, '腰部 ').replace(/_joint$/, '').replace('hip_pitch', '髋俯仰').replace('hip_roll', '髋横滚').replace('hip_yaw', '髋偏航').replace('ankle_pitch', '踝俯仰').replace('ankle_roll', '踝横滚').replace('shoulder_pitch', '肩俯仰').replace('shoulder_roll', '肩横滚').replace('shoulder_yaw', '肩偏航').replace('wrist_roll', '腕横滚').replace('wrist_pitch', '腕俯仰').replace('wrist_yaw', '腕偏航').replace('knee', '膝关节').replace('elbow', '肘关节').replace('yaw', '偏航').replace('roll', '横滚').replace('pitch', '俯仰');
let lastPayload = null;
let lastResponseAt = 0;
let requestError = '';
let modelLoaded = false;
let meshErrorCount = 0;
let modelLoadError = '';
let views = null;
let lastCloudKey = null;
let cloudContext = null;
let cloudFrames = [];
let cloudRenderedCount = 0;
let cloudWindowSeconds = 0.3;
const maxCloudPoints = 100000;
let rowNames = [];
const jointRows = [];
const jointMotions = new Map();
const handSides = ['left', 'right'];
const handRows = { left: [], right: [] };
const lastHands = { left: null, right: null };
const robotMaterials = [];
const robotRoot = new THREE.Group();
const pointGeometry = new THREE.BufferGeometry();
let pointCapacity = 0;
const pointMaterial = new THREE.PointsMaterial({ size: 0.045, vertexColors: true, sizeAttenuation: true, transparent: true, opacity: 0.9 });
const cloud = new THREE.Points(pointGeometry, pointMaterial);
cloud.frustumCulled = false;

function createView(id, kind) {
  const host = $(id);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.prepend(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.01, kind === 'robot' ? 100 : 500);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.09;
  controls.minDistance = kind === 'robot' ? 0.45 : 0.4;
  controls.maxDistance = kind === 'robot' ? 12 : 180;
  const initial = kind === 'robot' ? [1.8, -2.1, 0.9] : [10, -12, 10];
  const target = kind === 'robot' ? [0, 0, -0.12] : [0, 0, 0];
  const reset = () => { camera.position.fromArray(initial); controls.target.fromArray(target); controls.update(); };
  reset();
  const grid = new THREE.GridHelper(kind === 'robot' ? 5 : 30, kind === 'robot' ? 25 : 30, 0x355466, 0x1e3546);
  grid.rotation.x = Math.PI / 2;
  grid.position.z = kind === 'robot' ? -0.795 : -0.015;
  grid.material.transparent = true;
  grid.material.opacity = 0.62;
  scene.add(grid);
  const axes = new THREE.AxesHelper(kind === 'robot' ? 0.3 : 1.5);
  axes.material.transparent = true;
  axes.material.opacity = 0.65;
  scene.add(axes);
  scene.add(new THREE.HemisphereLight(0xc4edff, 0x334d63, 2.6));
  const light = new THREE.DirectionalLight(0xe1f3ff, 3.5);
  light.position.set(3, -3, 5);
  scene.add(light);
  const fill = new THREE.DirectionalLight(0x48dacb, 1.5);
  fill.position.set(-3, 2, 1);
  scene.add(fill);
  const observer = new ResizeObserver(() => {
    const { width, height } = host.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  });
  observer.observe(host);
  return { renderer, scene, camera, controls, reset, observer };
}
try {
  views = { robot: createView('robot-scene', 'robot'), lidar: createView('lidar-scene', 'lidar') };
  views.robot.scene.add(robotRoot);
  views.lidar.scene.add(cloud);
  $('reset-robot').addEventListener('click', views.robot.reset);
  $('reset-lidar').addEventListener('click', views.lidar.reset);
  function render() {
    requestAnimationFrame(render);
    if (document.hidden) return;
    for (const view of Object.values(views)) {
      view.controls.update();
      view.renderer.render(view.scene, view.camera);
    }
  }
  render();
} catch (error) {
  for (const id of ['robot-scene', 'lidar-scene']) {
    const text = document.createElement('div');
    text.className = 'render-error';
    text.textContent = '无法初始化 WebGL。请检查浏览器硬件加速；下方状态数值仍可查看。';
    $(id).append(text);
  }
  modelLoadError = 'WebGL 不可用';
  console.error(error);
}

function originTransform(group, origin) {
  if (!origin) return;
  group.position.fromArray(vec(origin.getAttribute('xyz')));
  const rpy = vec(origin.getAttribute('rpy'));
  group.quaternion.setFromEuler(new THREE.Euler(rpy[0], rpy[1], rpy[2], 'ZYX'));
}
function parseSTL(buffer) {
  let positions;
  const data = new DataView(buffer);
  const count = buffer.byteLength >= 84 ? data.getUint32(80, true) : 0;
  if (count && 84 + count * 50 === buffer.byteLength) {
    positions = new Float32Array(count * 9);
    for (let i = 0; i < count; i++) {
      const offset = 84 + i * 50 + 12;
      for (let j = 0; j < 9; j++) positions[i * 9 + j] = data.getFloat32(offset + j * 4, true);
    }
  } else {
    const text = new TextDecoder().decode(buffer);
    const values = [];
    const expression = /vertex\s+([-+\d.eE]+)\s+([-+\d.eE]+)\s+([-+\d.eE]+)/g;
    for (const match of text.matchAll(expression)) values.push(Number(match[1]), Number(match[2]), Number(match[3]));
    if (!values.length || values.length % 9 || !values.every(Number.isFinite)) throw new Error('无效 STL 网格');
    positions = new Float32Array(values);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.computeVertexNormals();
  return geometry;
}
async function loadRobot() {
  const base = '/assets/g1_viz/';
  const response = await fetch(base + 'g1_29dof_with_hand_rev_1_0_viz.urdf');
  if (!response.ok) throw new Error(`URDF HTTP ${response.status}`);
  const documentXML = new DOMParser().parseFromString(await response.text(), 'application/xml');
  if (documentXML.querySelector('parsererror')) throw new Error('URDF 解析失败');
  const links = new Map();
  const geometryCache = new Map();
  const tasks = [];
  for (const link of documentXML.querySelectorAll('robot > link')) {
    const group = new THREE.Group();
    group.name = link.getAttribute('name');
    links.set(group.name, group);
    for (const visual of link.querySelectorAll(':scope > visual')) {
      const meshXML = visual.querySelector('geometry > mesh');
      if (!meshXML) continue;
      const filename = meshXML.getAttribute('filename');
      const meshURL = new URL(filename, window.location.origin + base);
      if (meshURL.origin !== window.location.origin || !meshURL.pathname.startsWith(base)) throw new Error('网格路径超出模型目录');
      if (!geometryCache.has(meshURL.href)) {
        geometryCache.set(meshURL.href, fetch(meshURL).then(r => {
          if (!r.ok) throw new Error(`STL HTTP ${r.status}`);
          return r.arrayBuffer();
        }).then(parseSTL));
      }
      const rgba = vec(visual.querySelector('material > color')?.getAttribute('rgba'), [0.65, 0.7, 0.75, 1]);
      const baseColor = new THREE.Color(rgba[0], rgba[1], rgba[2]);
      const material = new THREE.MeshStandardMaterial({ color: baseColor, roughness: 0.64, metalness: 0.25 });
      material.userData.liveColor = baseColor.clone();
      material.userData.referenceColor = new THREE.Color(0x566573);
      material.userData.handSide = handSides.find(side => group.name.startsWith(`${side}_hand_`)) || null;
      robotMaterials.push(material);
      tasks.push(geometryCache.get(meshURL.href).then(geometry => {
        const mesh = new THREE.Mesh(geometry, material);
        originTransform(mesh, visual.querySelector(':scope > origin'));
        mesh.scale.fromArray(vec(meshXML.getAttribute('scale'), [1, 1, 1]));
        group.add(mesh);
      }).catch(error => { meshErrorCount++; console.error(filename, error); }));
    }
  }
  const children = new Set();
  for (const joint of documentXML.querySelectorAll('robot > joint')) {
    const parent = links.get(joint.querySelector('parent')?.getAttribute('link'));
    const child = links.get(joint.querySelector('child')?.getAttribute('link'));
    if (!parent || !child) continue;
    const origin = new THREE.Group();
    originTransform(origin, joint.querySelector(':scope > origin'));
    const motion = new THREE.Group();
    const axis = new THREE.Vector3().fromArray(vec(joint.querySelector('axis')?.getAttribute('xyz'), [1, 0, 0])).normalize();
    parent.add(origin);
    origin.add(motion);
    motion.add(child);
    children.add(child.name);
    jointMotions.set(joint.getAttribute('name'), { motion, axis, type: joint.getAttribute('type') });
  }
  for (const [name, group] of links) if (!children.has(name)) robotRoot.add(group);
  await Promise.all(tasks);
  modelLoaded = true;
  updatePose();
}
if (views) loadRobot().catch(error => {
  modelLoadError = error.message;
  updatePose();
  console.error(error);
});

function transportDown() { return !lastResponseAt || performance.now() - lastResponseAt > 2000; }
function freshChannel(name) { return !transportDown() && lastPayload?.channels?.[name]?.status === 'live'; }
function channelStatus(name) {
  if (transportDown()) return 'error';
  return lastPayload?.channels?.[name]?.status || 'waiting';
}
function handData(side) { return lastPayload?.[`hand_${side}`] || lastHands[side]; }
function handFresh(side) { return !!lastPayload?.[`hand_${side}`] && freshChannel(`hand_${side}`); }
function updatePose() {
  const state = lastPayload?.state;
  const fresh = freshChannel('state');
  const coordinateValid = state && state.mode_pr === 0;
  const articulate = coordinateValid;
  for (const joint of jointMotions.values()) joint.motion.quaternion.identity();
  robotRoot.quaternion.identity();
  if (articulate) {
    for (const motor of state.motors || []) {
      const name = lastPayload.joint_names?.[motor.index] || motor.name;
      const joint = jointMotions.get(name);
      if (joint && Number.isFinite(motor.q)) joint.motion.quaternion.setFromAxisAngle(joint.axis, motor.q);
    }
    const q = state.quaternion_wxyz;
    if (Array.isArray(q) && q.length === 4 && q.every(Number.isFinite)) robotRoot.quaternion.set(q[1], q[2], q[3], q[0]);
  }
  // Dex3 reports its own joint angles, independently of the body's PR/AB mode.
  for (const side of handSides) {
    for (const motor of handData(side)?.motors || []) {
      const joint = jointMotions.get(motor.name);
      if (motor.name?.startsWith(`${side}_hand_`) && joint && Number.isFinite(motor.q)) joint.motion.quaternion.setFromAxisAngle(joint.axis, motor.q);
    }
  }
  for (const material of robotMaterials) {
    const side = material.userData.handSide;
    const materialFresh = side ? handFresh(side) : fresh && coordinateValid;
    material.color.copy(materialFresh ? material.userData.liveColor : material.userData.referenceColor);
  }
  $('hand-pose-note').textContent = '骨盆位置固定 · ' + handSides.map(side => {
    const label = side === 'left' ? '左手' : '右手';
    const hand = handData(side);
    if (!hand) return `${label}默认零位`;
    const valid = (hand.motors || []).filter(motor => motor.name?.startsWith(`${side}_hand_`) && jointMotions.has(motor.name) && Number.isFinite(motor.q)).length;
    return !handFresh(side) ? `${label}灰色历史姿态` : `${label}${lastPayload?.mode === 'demo' ? '演示' : '实时'}${valid < 7 ? ` ${valid}/7 关节` : ''}`;
  }).join('；');
  let note = !state ? '灰色零位参考 · 尚无有效状态' : !coordinateValid ? 'AB 坐标模式 · 模型关节映射已停用，显示零位参考' : fresh ? '关节角与 IMU 姿态已同步' : '灰色历史姿态 · 状态已过期，请勿视为实时';
  if (lastPayload?.mode === 'demo' && state && coordinateValid) note = fresh ? '演示姿态 · 非真实机器人数据' : '灰色历史演示姿态 · 数据已过期';
  $('pose-note').textContent = note;
  const badge = $('model-status');
  badge.className = 'view-badge';
  if (modelLoadError) { badge.textContent = '模型不可用：' + modelLoadError; badge.classList.add('error'); }
  else if (!modelLoaded) badge.textContent = '正在加载离线 URDF / STL 模型…';
  else if (meshErrorCount) { badge.textContent = `模型不完整 · ${meshErrorCount} 个网格加载失败`; badge.classList.add('error'); }
  else { badge.textContent = !state ? '零位参考 · 等待状态' : !coordinateValid ? 'AB 模式 · 姿态映射停用' : !fresh ? '状态过期 · 保留最后姿态' : lastPayload?.mode === 'demo' ? '演示模型 · 29 关节' : '实时姿态 · 29 关节'; badge.classList.add(fresh && coordinateValid ? 'live' : 'warning'); }
}
function updateCloud() {
  const lidar = lastPayload?.lidar;
  const fresh = freshChannel('lidar');
  const context = JSON.stringify([lastPayload?.mode, lastPayload?.started_at, lidar?.frame_id]);
  if (context !== cloudContext) {
    cloudContext = context;
    cloudFrames = [];
    lastCloudKey = null;
    cloudRenderedCount = 0;
    pointGeometry.setDrawRange(0, 0);
  }
  const badge = $('cloud-status');
  badge.className = 'view-badge ' + (fresh ? 'live' : 'warning');
  badge.textContent = !lidar ? '等待点云 · 独立源坐标系' : !fresh ? '点云过期 · 保留最后画面' : lastPayload?.mode === 'demo' ? '演示点云 · 非真实雷达数据' : '实时点云 · 独立源坐标系';
  if (fresh && lidar && lidar.input_points <= 1) {
    badge.className = 'view-badge warning';
    badge.textContent = '源点云不足：本帧仅 ' + lidar.input_points + ' 个原始点';
  }
  pointMaterial.opacity = fresh ? 0.9 : 0.25;
  if (pointMaterial.vertexColors !== fresh) { pointMaterial.vertexColors = fresh; pointMaterial.needsUpdate = true; }
  pointMaterial.color.set(fresh ? 0xffffff : 0x8897a6);
  $('lidar-frame').textContent = 'frame: ' + (lidar?.frame_id || '未提供');
  $('lidar-topic').textContent = 'topic: ' + (lastPayload?.channels?.lidar?.topic || '—');
  $('lidar-density-note').textContent = cloudWindowSeconds ? `近${cloudWindowSeconds.toFixed(1)}秒叠加 · 未做运动补偿，移动时可能重影` : '单帧显示 · 不叠加历史';
  $('point-detail').textContent = lidar ? `当前帧原始 ${Number(lidar.input_points || 0).toLocaleString('zh-CN')} 点 · 送显 ${Number(lidar.shown_points ?? lidar.points?.length ?? 0).toLocaleString('zh-CN')} 点` : '当前帧原始点数：等待数据';
  if (!fresh || !lidar) {
    // Retain the grey rendering, but never carry pre-outage frames into a resumed stream.
    cloudFrames = [];
    lastCloudKey = null;
    $('point-count').textContent = cloudRenderedCount ? cloudRenderedCount.toLocaleString('zh-CN') : '—';
    return;
  }
  const now = performance.now();
  const key = JSON.stringify([lastPayload.started_at, lidar.seq, lidar.stamp?.sec, lidar.stamp?.nanosec]);
  let dirty = false;
  if (key !== lastCloudKey) {
    lastCloudKey = key;
    const framePoints = (lidar.points || []).filter(p => Array.isArray(p) && p.length >= 3 && p.slice(0, 3).every(Number.isFinite) && p.slice(0, 3).some(v => v !== 0)).slice(-maxCloudPoints);
    if (!cloudWindowSeconds) cloudFrames = [];
    cloudFrames.push({ receivedAt: now, points: framePoints });
    dirty = true;
  }
  if (cloudWindowSeconds && dirty) {
    const activeFrames = cloudFrames.filter(frame => now - frame.receivedAt <= cloudWindowSeconds * 1000);
    if (activeFrames.length !== cloudFrames.length) dirty = true;
    cloudFrames = activeFrames;
  }
  let total = cloudFrames.reduce((count, frame) => count + frame.points.length, 0);
  while (total > maxCloudPoints && cloudFrames.length) {
    const excess = total - maxCloudPoints;
    const first = cloudFrames[0];
    if (first.points.length <= excess) { total -= first.points.length; cloudFrames.shift(); }
    else { first.points = first.points.slice(excess); total = maxCloudPoints; }
    dirty = true;
  }
  cloudRenderedCount = total;
  $('point-count').textContent = total.toLocaleString('zh-CN');
  if (!views || !dirty) return;
  const points = cloudFrames.flatMap(frame => frame.points);
  if (points.length > pointCapacity) {
    // Release existing GPU buffers before replacing attributes; retain capacity on shorter frames.
    pointGeometry.dispose();
    pointCapacity = Math.min(maxCloudPoints, Math.max(points.length, Math.ceil(pointCapacity * 1.5), 1024));
    const positions = new THREE.BufferAttribute(new Float32Array(pointCapacity * 3), 3);
    const colors = new THREE.BufferAttribute(new Float32Array(pointCapacity * 3), 3);
    positions.setUsage(THREE.DynamicDrawUsage);
    colors.setUsage(THREE.DynamicDrawUsage);
    pointGeometry.setAttribute('position', positions);
    pointGeometry.setAttribute('color', colors);
  }
  if (!pointCapacity) {
    pointGeometry.setDrawRange(0, 0);
    return;
  }
  const positionsAttribute = pointGeometry.getAttribute('position');
  const colorsAttribute = pointGeometry.getAttribute('color');
  const positions = positionsAttribute.array;
  const colors = colorsAttribute.array;
  const color = new THREE.Color();
  for (let i = 0; i < points.length; i++) {
    positions.set(points[i].slice(0, 3), i * 3);
    const distance = Math.hypot(...points[i].slice(0, 3));
    color.setHSL(0.44 + Math.min(distance / 20, 1) * 0.28, 0.9, 0.62);
    colors.set([color.r, color.g, color.b], i * 3);
  }
  positionsAttribute.clearUpdateRanges();
  colorsAttribute.clearUpdateRanges();
  if (points.length) {
    positionsAttribute.addUpdateRange(0, points.length * 3);
    colorsAttribute.addUpdateRange(0, points.length * 3);
    positionsAttribute.needsUpdate = true;
    colorsAttribute.needsUpdate = true;
  }
  pointGeometry.setDrawRange(0, points.length);
}
$('lidar-density').addEventListener('change', event => {
  cloudWindowSeconds = Number(event.target.value);
  cloudFrames = [];
  lastCloudKey = null;
  updateCloud();
});

function initVector(id, labels) {
  const cells = labels.map(label => {
    const row = document.createElement('div');
    const dt = document.createElement('dt');
    const dd = document.createElement('dd');
    dt.textContent = label; dd.textContent = '—'; row.append(dt, dd); $(id).append(row);
    return dd;
  });
  return (values, multiplier = 1) => cells.forEach((cell, i) => { cell.textContent = format(typeof values?.[i] === 'number' ? values[i] * multiplier : null); });
}
const setRPY = initVector('rpy', ['Roll', 'Pitch', 'Yaw']);
const setGyro = initVector('gyro', ['X', 'Y', 'Z']);
const setAccel = initVector('accel', ['X', 'Y', 'Z']);
function updateJoints() {
  const names = lastPayload?.joint_names || [];
  if (names.join('|') !== rowNames.join('|')) {
    rowNames = [...names];
    jointRows.length = 0;
    $('joint-rows').replaceChildren();
    for (let index = 0; index < names.length; index++) {
      const tr = document.createElement('tr');
      const cells = Array.from({ length: 8 }, () => document.createElement('td'));
      cells[0].textContent = String(index).padStart(2, '0');
      cells[1].textContent = jointChinese(names[index]);
      cells[1].title = names[index];
      cells.slice(2).forEach(c => { c.textContent = '—'; });
      tr.append(...cells); $('joint-rows').append(tr); jointRows.push(cells);
    }
  }
  const motors = lastPayload?.state?.motors || [];
  const byIndex = new Map(motors.map(motor => [motor.index, motor]));
  $('joint-count').textContent = `${motors.length} / 29`;
  for (let index = 0; index < jointRows.length; index++) {
    const cells = jointRows[index], motor = byIndex.get(index);
    cells[2].textContent = format(motor?.q);
    cells[3].textContent = format(motor?.dq);
    cells[4].textContent = format(motor?.tau_est, 2);
    cells[5].textContent = Array.isArray(motor?.temperature) ? motor.temperature.map(t => format(t, 0)).join(' / ') : format(motor?.temperature, 0);
    cells[5].classList.toggle('temperature-hot', Array.isArray(motor?.temperature) && motor.temperature.some(t => t >= 70));
    cells[6].textContent = format(motor?.vol, 1);
    cells[7].textContent = motor?.motorstate == null ? '—' : String(motor.motorstate);
  }
}
function updateDetails() {
  const state = lastPayload?.state;
  setRPY(state?.rpy, 180 / Math.PI);
  setGyro(state?.gyroscope);
  setAccel(state?.accelerometer);
  $('quaternion').textContent = state?.quaternion_wxyz?.map(q => format(q, 4)).join('  ') || '—';
  $('mode-pr').textContent = state ? `${state.mode_pr} · ${state.mode_pr === 0 ? 'PR' : 'AB / 未映射'}` : '—';
  $('mode-machine').textContent = state ? String(state.mode_machine) : '—';
  $('tick').textContent = 'tick ' + (state?.tick ?? '—');
  $('imu-note').textContent = !state ? '等待状态' : !freshChannel('state') ? '历史读数 · 状态已过期' : lastPayload?.mode === 'demo' ? '演示读数' : '来自 LowState IMU';
  updateJoints();
}
function updateHands() {
  for (const side of handSides) {
    const hand = handData(side);
    const motors = hand?.motors || [];
    const container = $(`hand_${side}-rows`);
    if (!handRows[side].length) {
      for (let index = 0; index < 7; index++) {
        const row = document.createElement('tr');
        const cells = Array.from({ length: 5 }, () => document.createElement('td'));
        row.append(...cells); container.append(row); handRows[side].push(cells);
      }
    }
    const byIndex = new Map(motors.map(motor => [motor.index, motor]));
    handRows[side].forEach((cells, index) => {
      const motor = byIndex.get(index);
      cells[0].textContent = String(index);
      cells[1].textContent = motor?.name?.replace(`${side}_hand_`, '').replace(/_joint$/, '') || '等待关节名称';
      cells[1].title = motor?.name || '';
      cells[2].textContent = format(motor?.q);
      cells[3].textContent = Array.isArray(motor?.temperature) ? motor.temperature.map(t => format(t, 0)).join(' / ') : '—';
      cells[3].classList.toggle('temperature-hot', Array.isArray(motor?.temperature) && motor.temperature.some(t => t >= 70));
      cells[4].textContent = motor?.motorstate == null ? '—' : String(motor.motorstate);
    });
    const fresh = handFresh(side);
    container.classList.toggle('historical', !fresh);
    $(`hand_${side}-count`).textContent = `${motors.filter(m => Number.isFinite(m.q)).length} / 7`;
    $(`hand_${side}-note`).textContent = !hand ? '尚无 Dex3 数据 · 模型显示默认零位' : !fresh ? '历史读数 · 保留最后手指角度，模型手部已变灰' : lastPayload?.mode === 'demo' ? '演示手指读数 · 非实机数据' : 'Dex3 独立角度 · 实时同步';
    $(`hand_${side}-power`).textContent = hand ? `电源 ${format(hand.power_v, 1)} V / ${format(hand.power_a, 2)} A · 设备错误 ${Array.isArray(hand.error) ? hand.error.join(' / ') : '—'}` : '电源 — · 设备错误 —';
    $(`hand_${side}-power`).classList.toggle('device-error', !!hand?.error?.some(code => code !== 0));
  }
}
const displayRaw = value => value == null ? '—' : Array.isArray(value) ? '[' + value.map(item => item == null ? '—' : String(item)).join(', ') + ']' : String(value);
const freshnessText = name => transportDown() ? '服务离线 · 历史' : ({ live: lastPayload?.mode === 'demo' ? '人工演示' : '数据在线', stale: '数据过期 · 历史', waiting: '等待数据', error: '通道错误' }[channelStatus(name)] || '状态未知');
function setFreshness(id, name) {
  $(id).textContent = freshnessText(name);
  $(id).className = 'freshness ' + channelStatus(name);
}
function maximumMotorTemperature(motors) {
  let highest = null;
  for (const motor of motors || []) {
    const temperatures = Array.isArray(motor.temperature) ? motor.temperature : [motor.temperature];
    for (const temperature of temperatures) if (typeof temperature === 'number' && Number.isFinite(temperature) && (!highest || temperature > highest.value)) highest = { value: temperature, motor };
  }
  return highest;
}
function renderRawFields(id, source, fields) {
  const fragment = document.createDocumentFragment();
  for (const [key, label] of fields) {
    const row = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = label; dd.textContent = displayRaw(source?.[key]); row.append(dt, dd); fragment.append(row);
  }
  $(id).replaceChildren(fragment);
}
function updateHealth() {
  const battery = lastPayload?.battery;
  const soc = typeof battery?.soc_pct === 'number' && Number.isFinite(battery.soc_pct) ? battery.soc_pct : null;
  $('battery-soc').textContent = format(soc, 0);
  const meter = $('battery-meter');
  const validSoc = soc != null && soc >= 0 && soc <= 100;
  $('battery-fill').style.width = validSoc ? soc + '%' : '0%';
  if (validSoc) meter.setAttribute('aria-valuenow', String(soc)); else meter.removeAttribute('aria-valuenow');
  meter.setAttribute('aria-valuetext', soc == null ? '无数据' : `${soc}%${freshChannel('battery') ? '' : '，历史读数'}`);
  meter.classList.toggle('low', validSoc && soc <= 20);
  $('battery-brief').textContent = `循环 ${displayRaw(battery?.cycle)} · SOH 原值 ${displayRaw(battery?.soh_raw)}` + (soc != null && !validSoc ? ' · SOC 超出 0–100 范围' : '');
  setFreshness('battery-freshness', 'battery');
  $('battery-soc').closest('.health-cell').classList.toggle('historical', !freshChannel('battery'));
  const motion = lastPayload?.motion;
  $('motion-label').textContent = motion?.mode_label || (motion ? '未知运动模式' : '—');
  $('motion-ids').textContent = `FSM ID ${displayRaw(motion?.fsm_id)} · FSM mode ${displayRaw(motion?.fsm_mode)}`;
  $('motion-task').textContent = `任务 ${displayRaw(motion?.task_id)} · 时长原值 ${displayRaw(motion?.task_time)}`;
  setFreshness('motion-freshness', 'motion');
  $('motion-label').closest('.health-cell').classList.toggle('historical', !freshChannel('motion'));
  const highest = maximumMotorTemperature(lastPayload?.state?.motors);
  $('body-temperature').replaceChildren(document.createTextNode(format(highest?.value, 0)));
  const unit = document.createElement('small'); unit.textContent = ' °C'; $('body-temperature').append(unit);
  $('body-temperature').classList.toggle('temperature-hot', !!highest && highest.value >= 70);
  $('body-temperature-joint').textContent = highest ? jointChinese(lastPayload?.joint_names?.[highest.motor.index] || highest.motor.name || `关节 ${highest.motor.index}`) : '尚无有效关节温度';
  setFreshness('body-freshness', 'state');
  $('body-temperature').closest('.health-cell').classList.toggle('historical', !freshChannel('state'));
  for (const side of handSides) {
    const temperature = maximumMotorTemperature(handData(side)?.motors);
    $(`${side}-temperature`).textContent = temperature ? `${format(temperature.value, 0)} °C` : '—';
    $(`${side}-temperature`).title = temperature?.motor?.name || '尚无有效手部温度';
    $(`${side}-temperature`).classList.toggle('temperature-hot', !!temperature && temperature.value >= 70);
    $(`${side}-temperature-state`).textContent = freshnessText(`hand_${side}`);
    $(`${side}-temperature-state`).className = channelStatus(`hand_${side}`);
  }
  renderRawFields('battery-raw', battery, [['soc_pct', 'SOC %'], ['soh_raw', 'SOH 原值'], ['cycle', 'cycle 原值'], ['voltage_raw', 'voltage 原值'], ['current_raw', 'current 原值'], ['temperature_raw', 'temperature 原值'], ['cell_voltage_raw', 'cell_voltage 原值'], ['state_raw', 'state 原值']]);
  renderRawFields('mainboard-raw', lastPayload?.mainboard, [['temperature_raw', 'temperature 原值'], ['fan_state_raw', 'fan_state 原值'], ['value_raw', 'value 原值'], ['state_raw', 'state 原值']]);
  $('imu-temperature-raw').textContent = `IMU temperature 原值 ${displayRaw(lastPayload?.state?.imu_temperature_raw)} · ${freshnessText('state')}`;
}
let videoRequest = null;
let videoObjectURL = null;
let videoAttemptKey = null;
let videoRenderedKey = null;
let videoRenderedAt = 0;
let videoImageError = '';
let videoLastAttempt = 0;
let videoContext = null;
function hideVideo() {
  $('video-image').hidden = true;
  $('video-placeholder').hidden = false;
}
function updateVideo() {
  const video = lastPayload?.video;
  const fresh = freshChannel('video');
  const context = JSON.stringify([lastPayload?.mode, lastPayload?.started_at]);
  if (context !== videoContext) {
    videoContext = context;
    if (videoRequest) videoRequest.abort();
    videoAttemptKey = videoRenderedKey = null;
    videoRenderedAt = 0;
    videoImageError = '';
    if (videoObjectURL) URL.revokeObjectURL(videoObjectURL);
    videoObjectURL = null;
    $('video-image').removeAttribute('src');
    hideVideo();
  }
  $('video-meta').textContent = `格式 ${video?.format || '—'} · 尺寸 ${video?.width && video?.height ? `${video.width} × ${video.height}` : '—'} · ${video?.bytes == null ? '—' : Number(video.bytes).toLocaleString('zh-CN')} bytes`;
  $('video-topic').textContent = 'topic: ' + (lastPayload?.channels?.video?.topic || '—');
  const key = JSON.stringify([context, video?.seq ?? lastPayload?.channels?.video?.count, video?.time_frame]);
  const badge = $('video-badge');
  const imageFresh = !!videoRenderedKey && videoRenderedAt > 0 && performance.now() - videoRenderedAt <= 2000;
  badge.className = 'video-badge' + (fresh && video?.image_available && imageFresh && !videoImageError ? ' live' : channelStatus('video') === 'error' ? ' error' : '');
  if (!fresh || !video || !video.image_available) {
    if (videoRequest) videoRequest.abort();
    hideVideo();
    videoRenderedKey = null;
    videoRenderedAt = 0;
    // A transport interruption can recover before the DDS frame changes.
    // Permit the same sequence to be fetched again after this hidden state.
    videoAttemptKey = null;
    videoImageError = '';
    $('video-message').textContent = transportDown() ? '观测服务已断开' : lastPayload?.mode === 'demo' && !video ? '人工演示不提供相机画面' : !video ? '等待前置视频' : channelStatus('video') === 'error' ? '视频通道错误' : !fresh ? '视频数据已过期' : lastPayload?.mode === 'demo' ? '演示模式不提供相机画面' : '等待可显示的视频画面';
    $('video-note').textContent = transportDown() || (video && !fresh) ? '画面已隐藏，等待新鲜图像恢复后显示。' : video?.note || '接收到受支持的图像后显示画面。';
    badge.textContent = !video ? freshnessText('video') : !fresh ? freshnessText('video') : '当前消息没有可显示图像';
    return;
  }
  if (videoImageError || (videoRenderedKey && !imageFresh)) {
    hideVideo(); $('video-message').textContent = '画面传输中断，等待新图'; $('video-note').textContent = videoImageError || '超过 2 秒未成功取得新图像；DDS 接收在线不代表画面仍在更新。'; badge.textContent = '图像传输中断 · 等待新图';
  } else if (!videoRenderedKey) {
    hideVideo(); $('video-message').textContent = '正在读取前置图像'; $('video-note').textContent = '画面按新到达的数据帧更新。'; badge.textContent = '正在获取图像';
  } else {
    $('video-image').hidden = false; $('video-placeholder').hidden = true;
    badge.textContent = '最近收到的前置图像 · ' + freshnessText('video');
  }
  if (!videoRequest && !document.hidden && key !== videoAttemptKey && performance.now() - videoLastAttempt >= 100) requestVideoFrame(key, context);
}
async function requestVideoFrame(key, context) {
  const controller = new AbortController();
  videoRequest = controller;
  videoAttemptKey = key;
  videoLastAttempt = performance.now();
  // Keep a failed transfer visible until a new image successfully decodes.
  const timeout = setTimeout(() => controller.abort(), 1800);
  let nextURL = null;
  try {
    const response = await fetch(`/api/video/frame?seq=${encodeURIComponent(lastPayload?.video?.seq ?? lastPayload?.channels?.video?.count ?? 0)}`, { cache: 'no-store', signal: controller.signal });
    if (response.status === 204) throw new Error('服务暂未提供可显示图像，等待下一帧。');
    if (!response.ok) throw new Error(`图像请求 HTTP ${response.status}`);
    if (!/^image\/(jpeg|png)(;|$)/i.test(response.headers.get('Content-Type') || '')) throw new Error('图像响应格式暂不支持。');
    const blob = await response.blob();
    if (blob.size > 16 * 1024 * 1024) throw new Error('当前图像超过页面显示大小限制。');
    nextURL = URL.createObjectURL(blob);
    const decoded = new Image(); decoded.src = nextURL; await decoded.decode();
    if (controller.signal.aborted || context !== videoContext || !freshChannel('video') || !lastPayload?.video?.image_available) return;
    const previous = videoObjectURL;
    videoObjectURL = nextURL; nextURL = null;
    $('video-image').src = videoObjectURL;
    videoRenderedKey = key;
    videoRenderedAt = performance.now();
    videoImageError = '';
    if (previous) URL.revokeObjectURL(previous);
  } catch (error) {
    if (context === videoContext && freshChannel('video')) videoImageError = error.name === 'AbortError' ? '图像读取超时，等待下一帧。' : error.message;
  } finally {
    clearTimeout(timeout);
    if (nextURL) URL.revokeObjectURL(nextURL);
    if (videoRequest === controller) videoRequest = null;
    updateVideo();
  }
}
function updateStatus() {
  const offline = transportDown();
  document.body.classList.toggle('transport-offline', offline);
  const alert = $('transport-alert');
  if (offline) {
    alert.hidden = false;
    alert.className = 'alert error';
    alert.textContent = lastResponseAt ? '观测服务连接中断，当前为历史数据。' + (requestError ? ' ' + requestError : '') : '无法获取观测服务数据，正在重试。' + (requestError ? ' ' + requestError : '');
  } else if (lastPayload?.mode === 'demo') {
    alert.hidden = false; alert.className = 'alert demo';
    alert.textContent = '人工演示模式：姿态、IMU、关节、点云、电池与运动状态均为合成示例，不代表真实机器人；不提供伪相机画面。';
  } else {
    alert.hidden = true;
    const channelLabels = { state: '状态', lidar: '雷达', hand_left: '左手', hand_right: '右手', battery: '电池', motion: '运动', mainboard: '主板', video: '视频' };
    const errors = Object.keys(channelLabels).filter(name => lastPayload?.channels?.[name]?.status === 'error');
    if (errors.length) {
      alert.hidden = false; alert.className = 'alert error';
      alert.textContent = errors.map(name => `${channelLabels[name]}通道错误：${lastPayload.channels[name].error || '请检查服务日志'}`).join('；');
    }
  }
  const mode = $('mode');
  mode.textContent = offline ? '服务离线' : lastPayload?.mode === 'demo' ? 'DEMO 演示' : 'LIVE 实机数据源';
  mode.className = 'pill ' + (offline ? 'neutral' : lastPayload?.mode === 'demo' ? 'demo' : '');
  $('network').textContent = lastPayload?.mode === 'demo' ? '离线演示 / 不连接机器人' : lastPayload ? `${lastPayload.interface || '—'} / ${lastPayload.domain ?? '—'}` : '— / —';
  for (const name of ['state', 'lidar', 'hand_left', 'hand_right', 'battery', 'motion', 'mainboard', 'video']) {
    const channel = lastPayload?.channels?.[name];
    const status = channelStatus(name);
    const label = $(`${name}-status`);
    label.textContent = offline ? '服务断开' : ({ live: '数据在线', stale: '数据过期', waiting: '等待数据', error: '通道错误' }[status] || '状态未知');
    label.className = 'status-text ' + status;
    $(`${name}-hz`).textContent = (name === 'video' ? '接收率 ' : '') + format(channel?.hz, 1) + ' Hz';
    const age = channel?.age_s;
    const matchInfo = name.startsWith('hand_') && channel?.matched != null ? ` · DDS 匹配 ${channel.matched}` : '';
    $(`${name}-detail`).textContent = (channel?.error || (age != null ? `距接收 ${format(age, 1)} s · 累计 ${Number(channel?.count || 0).toLocaleString('zh-CN')} 帧${offline ? ' · 历史' : ''}` : '尚未接收到数据')) + matchInfo;
  }
  updatePose();
  updateHands();
  updateCloud();
  updateHealth();
  updateVideo();
  if (lastPayload?.state && !freshChannel('state')) $('imu-note').textContent = '历史读数 · 状态已过期';
}
async function poll() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1800);
  try {
    const response = await fetch('/api/state', { cache: 'no-store', signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (!payload || !payload.channels || !['live', 'demo'].includes(payload.mode)) throw new Error('服务响应格式异常');
    if (lastPayload && lastPayload.mode !== payload.mode) for (const side of handSides) lastHands[side] = null;
    for (const side of handSides) if (payload[`hand_${side}`]) lastHands[side] = payload[`hand_${side}`];
    lastPayload = payload;
    lastResponseAt = performance.now();
    requestError = '';
    $('updated').textContent = '服务响应 ' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
    updateDetails();
  } catch (error) {
    requestError = error.name === 'AbortError' ? '请求超时。' : String(error.message);
  } finally {
    clearTimeout(timeout);
    updateStatus();
    setTimeout(poll, document.hidden ? 1000 : 125);
  }
}
setInterval(updateStatus, 500);
poll();
