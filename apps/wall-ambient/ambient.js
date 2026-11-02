"use strict";

// Every knob is a query param so dashboards/wall.yaml can retune it without a
// code change. The weekday wall screensaver loads this page; see docs/wall-panels.md.
const params = new URLSearchParams(location.search);
const num = (k, d) => (params.has(k) && Number.isFinite(+params.get(k)) ? +params.get(k) : d);
const cfg = {
  kioskUrl: params.get("kiosk") || "https://immich-kiosk.kblab.me/",
  user: params.get("user") || "wall",
  album: params.get("album") || "b34a9f29-eb85-4da7-903f-a02cc40168dc",
  slideSecs: num("slide_secs", 30),
  scenerySlides: num("scenery", 4),
  trippySecs: num("trippy_secs", 45),
  // Fraction of CSS pixels the shaders render at; the canvas is scaled up.
  // Wall tablets have weak GPUs, and the visuals are soft anyway.
  renderScale: num("render_scale", 0.5),
  shaders: params.get("shaders"),
};

const kiosk = new URL(cfg.kioskUrl);
Object.entries({
  user: cfg.user, album: cfg.album, duration: cfg.slideSecs, layout: "single",
  image_fit: "cover", transition: "cross-fade", show_album_name: "false",
  show_image_date: "false", show_image_location: "false", show_image_description: "true",
}).forEach(([k, v]) => kiosk.searchParams.set(k, v));
document.getElementById("scenery").src = kiosk.toString();

const VERT = "attribute vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }";
const HEAD = "precision mediump float; uniform vec2 r; uniform float t;\n";

const SHADERS = {
  plasma: `
    void main(){
      vec2 u = (gl_FragCoord.xy - .5*r) / r.y;
      float v = sin(u.x*3.+t*.6) + sin(u.y*4.-t*.5) + sin((u.x+u.y)*3.+t*.4) + sin(length(u*5.)-t*.8);
      vec3 c = .5 + .5*cos(6.2831*(v*.18 + t*.02 + vec3(.0, .15, .3)));
      gl_FragColor = vec4(c*(.6 + .4*sin(v*2.)), 1.);
    }`,
  flow: `
    float h(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7)))*43758.5453); }
    float n(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.-2.*f);
      return mix(mix(h(i),h(i+vec2(1,0)),f.x), mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x), f.y); }
    float fbm(vec2 p){ float s=0., a=.5; for(int i=0;i<5;i++){ s+=a*n(p); p=p*2.03+vec2(1.7,9.2); a*=.5; } return s; }
    void main(){
      vec2 u = gl_FragCoord.xy / r.y * 2.2;
      vec2 q = vec2(fbm(u + .05*t), fbm(u + vec2(5.2, 1.3) - .04*t));
      vec2 w = vec2(fbm(u + 4.*q + vec2(1.7, 9.2) + .08*t), fbm(u + 4.*q + vec2(8.3, 2.8) - .06*t));
      float f = fbm(u + 4.*w);
      vec3 c = mix(vec3(.05,.1,.35), vec3(.95,.35,.6), clamp(f*f*2., 0., 1.));
      c = mix(c, vec3(.1,.85,.9), clamp(length(q), 0., 1.)*.55);
      c = mix(c, vec3(1.,.9,.5), clamp(w.x*w.x, 0., 1.)*.6);
      gl_FragColor = vec4(c*(.4 + .9*f), 1.);
    }`,
  kaleido: `
    void main(){
      vec2 u = (gl_FragCoord.xy - .5*r) / r.y;
      float a = atan(u.y, u.x) + t*.05, d = length(u);
      float k = 6.2831853/8.;
      a = abs(mod(a, k) - .5*k);
      vec2 p = vec2(cos(a), sin(a))*d;
      vec3 c = vec3(0.);
      for(int i=0;i<4;i++){
        p = abs(p*1.7) - vec2(.6 + .15*sin(t*.2), .35);
        float g = .012/abs(sin(length(p)*6. - t*.7));
        c += g*(.5 + .5*cos(float(i)*.9 + t*.2 + vec3(0., 2., 4.)));
      }
      gl_FragColor = vec4(1. - exp(-c*1.4), 1.);
    }`,
  julia: `
    void main(){
      vec2 z = (gl_FragCoord.xy - .5*r) / r.y * 2.6;
      vec2 c = .7885*vec2(cos(t*.07), sin(t*.07));
      float m = 0.;
      for(int i=0;i<96;i++){
        z = vec2(z.x*z.x - z.y*z.y, 2.*z.x*z.y) + c;
        if(dot(z,z) > 16.) break;
        m += 1.;
      }
      float s = m/96.;
      vec3 col = .5 + .5*cos(6.2831*(s*1.8 + t*.03 + vec3(.0, .12, .25)));
      gl_FragColor = vec4(col*min(1., pow(s, .35)*1.4), 1.);
    }`,
  tunnel: `
    void main(){
      vec2 u = (gl_FragCoord.xy - .5*r) / r.y;
      u += .08*vec2(sin(t*.3), cos(t*.23));
      float a = atan(u.y, u.x)/3.14159, d = .35/length(u);
      vec2 tc = vec2(a*3. + t*.05, d + t*.35);
      float g = sin(tc.x*6.2831*2.)*sin(tc.y*6.2831);
      vec3 c = .5 + .5*cos(6.2831*(tc.y*.2 + g*.12 + vec3(.55, .65, .8)));
      gl_FragColor = vec4(c*smoothstep(0., .9, length(u)*1.6), 1.);
    }`,
  aurora: `
    void main(){
      vec2 u = gl_FragCoord.xy / r;
      vec3 c = mix(vec3(.01,.02,.06), vec3(.02,.05,.12), u.y);
      for(int i=0;i<5;i++){
        float fi = float(i);
        float y = .55 + .12*sin(u.x*(2.+fi) + t*(.15+.05*fi) + fi*1.7) + .05*sin(u.x*9. - t*.4 + fi);
        float band = exp(-pow((u.y - y)*(9. + 2.*fi), 2.)) * (.6 + .4*sin(u.x*14. + t*.6 + fi*2.));
        c += band*(.5 + .5*cos(fi*.8 + t*.05 + vec3(2.2, .3, 4.)))*.55;
      }
      float s = fract(sin(dot(floor(gl_FragCoord.xy*.5), vec2(12.9898,78.233)))*43758.5453);
      c += step(.9985, s)*u.y*.8;
      gl_FragColor = vec4(c, 1.);
    }`,
};

const canvas = document.getElementById("fx");
const gl = canvas.getContext("webgl", { antialias: false, alpha: false, powerPreference: "low-power" });
const programs = {};

function compile(name) {
  if (programs[name] !== undefined) return programs[name];
  const prog = gl.createProgram();
  [[gl.VERTEX_SHADER, VERT], [gl.FRAGMENT_SHADER, HEAD + SHADERS[name]]].forEach(([type, src]) => {
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) console.error(name, gl.getShaderInfoLog(sh));
    gl.attachShader(prog, sh);
  });
  gl.linkProgram(prog);
  const ok = gl.getProgramParameter(prog, gl.LINK_STATUS);
  programs[name] = ok ? { prog, r: gl.getUniformLocation(prog, "r"), t: gl.getUniformLocation(prog, "t") } : null;
  return programs[name];
}

if (gl) {
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
}

const names = (cfg.shaders ? cfg.shaders.split(",") : Object.keys(SHADERS)).filter((n) => SHADERS[n]);
let current = null, frame = 0, start = 0, last = null;

function resize() {
  canvas.width = Math.max(1, Math.round(innerWidth * cfg.renderScale));
  canvas.height = Math.max(1, Math.round(innerHeight * cfg.renderScale));
  gl.viewport(0, 0, canvas.width, canvas.height);
}

function draw(now) {
  gl.useProgram(current.prog);
  const loc = gl.getAttribLocation(current.prog, "p");
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  gl.uniform2f(current.r, canvas.width, canvas.height);
  gl.uniform1f(current.t, (now - start) / 1000 + 20);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  frame = requestAnimationFrame(draw);
}

function startTrippy() {
  const pool = names.filter((n) => n !== last);
  const name = pool[Math.floor(Math.random() * pool.length)] || names[0];
  current = compile(name);
  if (!current) return false;
  last = name;
  resize();
  start = performance.now();
  frame = requestAnimationFrame(draw);
  document.body.classList.add("trippy");
  return true;
}

function stopTrippy() {
  document.body.classList.remove("trippy");
  // Keep rendering through the 2.5 s fade-out, then stop the GPU work.
  canvas.addEventListener("transitionend", () => cancelAnimationFrame(frame), { once: true });
}

// The rotation is a genuine display schedule (how long each scene stays up),
// not a wait on some other event, so plain timers are the right tool.
function cycle() {
  setTimeout(() => {
    if (!gl || !names.length || !startTrippy()) return cycle();
    setTimeout(() => { stopTrippy(); cycle(); }, cfg.trippySecs * 1000);
  }, cfg.scenerySlides * cfg.slideSecs * 1000);
}
if (cfg.trippySecs > 0) cycle();

const timeEl = document.getElementById("time"), dateEl = document.getElementById("date");
function tick() {
  const d = new Date();
  timeEl.textContent = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }).replace(/\s?[AP]M$/i, "");
  dateEl.textContent = d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
}
tick();
setInterval(tick, 10000);
addEventListener("resize", () => gl && current && resize());
