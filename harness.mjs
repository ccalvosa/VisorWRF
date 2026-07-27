// harness.mjs — ejecuta el modulo del visor en Node con DOM y three.js
// simulados. No dibuja nada: sirve para que los errores de ejecucion salten
// con su linea en vez de quedarse mudos en un navegador.
import fs from 'fs';
import path from 'path';

const ROOT = process.argv[2] || '.';

/* ---------- DOM minimo ---------- */
const IDS = new Set();
function mkEl(id = '') {
  const el = {
    id, tagName: 'DIV', className: '', textContent: '', innerHTML: '',
    style: new Proxy({}, { set: () => true, get: () => '' }),
    dataset: {}, children: [], value: '0', max: '0', min: '0',
    clientWidth: 700, clientHeight: 250,
    getBoundingClientRect: () => ({ left:0, top:0, width:700, height:250 }),
    hidden: false, title: '',
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { this.children = this.children.filter(x => x !== c); return c; },
    setAttribute() {}, getAttribute() { return null; },
    addEventListener() {}, scrollIntoView() {}, focus() {},
    getContext(kind) {
      if (kind !== '2d') return null;
      return {
        drawImage() {}, putImageData() {},
        setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {},
        lineTo() {}, stroke() {}, fill() {}, arc() {}, closePath() {},
        fillText() {}, save() {}, restore() {},
        set fillStyle(v) {}, set strokeStyle(v) {}, set lineWidth(v) {},
        set font(v) {}, set textAlign(v) {}, set globalAlpha(v) {},
        createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
        getImageData: (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
      };
    },
  };
  return el;
}
const els = new Map();
globalThis.document = {
  getElementById(id) {
    IDS.add(id);
    if (!els.has(id)) els.set(id, mkEl(id));
    return els.get(id);
  },
  createElement: () => mkEl(),
  addEventListener() {},
};
globalThis.window = globalThis;
globalThis.innerWidth = 1440;
globalThis.innerHeight = 900;
globalThis.devicePixelRatio = 2;
globalThis.addEventListener = () => {};
globalThis.matchMedia = () => ({ matches: false });
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '#888888' });
globalThis.requestAnimationFrame = () => 0;   // no arrancamos el bucle
globalThis.performance = { now: () => 0 };
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
globalThis.prompt = () => 'x';
Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: async () => {} } }, configurable: true });

/* ---------- fetch desde disco ---------- */
globalThis.fetch = async (url) => {
  const p = path.join(ROOT, url);
  if (!fs.existsSync(p)) return { ok: false, status: 404 };
  const buf = fs.readFileSync(p);
  return {
    ok: true, status: 200,
    json: async () => JSON.parse(buf.toString()),
    blob: async () => ({ __png: p }),
  };
};
// PNG: solo hace falta el tamano y unos bytes; el contenido no importa para
// comprobar el flujo de arranque
globalThis.createImageBitmap = async (blob) => {
  const b = fs.readFileSync(blob.__png);
  const w = b.readUInt32BE(16), h = b.readUInt32BE(20);   // cabecera IHDR
  return { width: w, height: h, close() { this.width = 0; this.height = 0; } };
};

/* ---------- three.js simulado ---------- */
const seen = new Set();
function auto(name) {
  const target = function () {};
  return new Proxy(target, {
    get(t, k) {
      if (k === 'then') return undefined;
      if (k === Symbol.toPrimitive) return () => 1;
      if (!(k in t)) {
        if (typeof k === 'string' && /^[A-Z_]+$/.test(k)) t[k] = 1;
        else t[k] = auto(`${name}.${String(k)}`);
      }
      return t[k];
    },
    set(t, k, v) { t[k] = v; return true; },
    apply() { return auto(name + '()'); },
    construct(t, args) { return inst(name, args && args[0]); },
  });
}
function inst(name, args) {
  seen.add(name);
  const o = {
    isObject3D: true, children: [], uniforms: {},
    position: xyz(), rotation: xyz(), up: xyz(), scale: xyz(),
    x: 0, y: 0, z: 0, zoom: 1, top: 100, bottom: -100, left: -100, right: 100,
    value: 0, width: 1, height: 1,
    add() {}, remove() {}, dispose() {}, copy() { return this; },
    set() { return this; }, setSize() {}, setPixelRatio() {}, setClearColor() {},
    setAttribute() {}, setIndex() {}, computeBoundingSphere() {},
    computeVertexNormals() {}, setFromPoints() { return this; },
    setFromCamera() {}, updateProjectionMatrix() {}, update() {},
    project() { return this; }, multiplyScalar() { return this; },
    normalize() { return this; }, clone() { return this; },
    render() {}, appendChild() {}, addEventListener() {},
    domElement: { addEventListener() {}, style: {} },
    debug: {}, ray: {},
  };
  // los materiales reciben sus uniforms en el constructor: hay que copiarlos
  if (args && typeof args === 'object') Object.assign(o, args);
  return new Proxy(o, {
    get(t, k) {
      if (k in t) return t[k];
      t[k] = auto(`${name}.${String(k)}`);
      return t[k];
    },
    set(t, k, v) { t[k] = v; return true; },
  });
}
function xyz() {
  const o = { x: 0, y: 0, z: 0 };
  o.set = (a, b, c) => { o.x = a; o.y = b; o.z = c; return o; };
  o.copy = () => o; o.multiplyScalar = () => o; o.add = () => o;
  o.normalize = () => o; o.clone = () => o; o.project = () => o;
  o.length = () => 1;
  return o;
}
const THREE = auto('THREE');
THREE.Vector3 = function () { return xyz(); };
THREE.Vector2 = function () { return xyz(); };
THREE.Color = function () { return { set() {}, isColor: true }; };
THREE.Scene = function () { return inst('Scene'); };
THREE.WebGLRenderer = function () { return inst('WebGLRenderer'); };

globalThis.__THREE__ = THREE;
globalThis.__ORBIT__ = function () { return inst('OrbitControls'); };

const src = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8')
  .match(/<script type="module">([\s\S]*?)<\/script>/)[1]
  .replace(/^import \* as THREE from 'three';$/m,
           'const THREE = globalThis.__THREE__;')
  .replace(/^import \{ OrbitControls \}.*$/m,
           'const OrbitControls = globalThis.__ORBIT__;');

fs.writeFileSync('/tmp/app_run.mjs', src);

let failed = null;
const origFail = null;
process.on('unhandledRejection', (e) => { failed = e; });

await import('/tmp/app_run.mjs');
await new Promise(r => setTimeout(r, 1200));

/* ---- ejercitar los caminos interactivos ---- */
const G = await import('/tmp/app_run.mjs').then(m => m).catch(() => ({}));
function probe(label, fn){
  try { fn(); console.log('  ok   ' + label); }
  catch (e){ console.log('  FALLO ' + label + ': ' + (e.stack||e).split('\n').slice(0,3).join(' | ')); }
}
console.log('--- caminos interactivos ---');
const handlers = [
  ['selector de campo',   () => els.get('field').onchange?.({target:{value:'t2'}})],
  ['boton 2D',            () => els.get('b2d').onclick?.()],
  ['boton 3D',            () => els.get('b3d').onclick?.()],
  ['exageracion',         () => els.get('vex').oninput?.({target:{value:'12'}})],
  ['modo viento flechas', () => els.get('wmode').onchange?.({target:{value:'arrows'}})],
  ['modo streamlines',    () => els.get('wmode').onchange?.({target:{value:'streams'}})],
  ['densidad',            () => els.get('den').oninput?.({target:{value:'9'}})],
  ['densidad soltar',     () => els.get('den').onchange?.()],
  ['grosor',              () => els.get('wid').oninput?.({target:{value:'4'}})],
  ['color viento',        () => els.get('wcol').onchange?.({target:{value:'#f0a33c'}})],
  ['color por velocidad', () => els.get('wcol').onchange?.({target:{value:'speed'}})],
  ['relieve',             () => els.get('shd').oninput?.({target:{value:'40'}})],
  ['curvas de nivel',     () => els.get('ctr').oninput?.({target:{value:'3'}})],
  ['reticula',            () => els.get('grat').onchange?.({target:{value:'0.25'}})],
  ['slider temporal',     () => { const tr=els.get('tr'); tr.value='3'; tr.oninput?.(); }],
  ['play',                () => els.get('play').onclick?.()],
  ['meteograma sin punto',() => els.get('mbtn').onclick?.()],
  ['meteograma con punto',() => { els.get('mbtn').onclick?.(); }],
  ['clic en el grafico',  () => els.get('mgcv').onclick?.({clientX:300})],
  ['cerrar meteograma',   () => els.get('mgclose').onclick?.()],
];
for (const [n,f] of handlers) probe(n, f);
await new Promise(r => setTimeout(r, 800));

const fp = els.get('failmsg');
const status = els.get('status');
console.log('--- resultado ---');
if (failed) {
  console.log('EXCEPCION NO CAPTURADA:', failed?.stack || failed);
} else if (fp && fp.textContent) {
  console.log('EL VISOR MOSTRO ERROR:', fp.textContent);
} else {
  console.log('arranque completado sin error');
}
console.log('elementos pedidos por getElementById:', [...IDS].join(' '));
