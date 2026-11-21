'use strict';
// 待審區 r3(Andy 12:3x「為什麼急的反而是藍色」):一般 = 淡金、等你決定 = 橘、急 = 紅(最大最快)、被擋 = 灰;舊名 dec / shout 留著對到新色
const KC = { gold:'214,196,150', ask:'240,146,60', urg:'235,64,64', hold:'139,139,150', dec:'240,146,60', shout:'235,64,64' };
const URG = (k) => k==='urg' || k==='shout', ASK = (k) => k==='ask' || k==='dec';
// 粒子層(SPEC §2):每顆光點 = 一封信;資料由 app.js 從 GET /api/v1/flows 灌進來(FX.setFlows)
const FX = window.FX = (() => {
  let FLOWS = [];
  const RM = matchMedia('(prefers-reduced-motion: reduce)');
  const MAX = 120, mocks = new Set();
  let calm = false, raf = 0, last = 0;
  // 四個滑桿都是 0–100%、每格 10%;0% = 關(光點大小 / 速度 / 數量任一為 0 ⇒ 靜態線條 + 則數)
  const opt = { sz:70, sp:20, de:10, al:30, tl:100 }; // tl = 尾巴長度:每 10% 多一節，0% = 沒尾巴(不算關)
  // 偏好存在 aibd(2b §4 prefs contacts_fx,SPEC §3「不是只放 localStorage」);鍵名對照:
  const PK = { sz:'size', sp:'speed', de:'count', tl:'length', al:'alpha' };
  const clamp = v => Math.max(0, Math.min(100, Math.round(v/10)*10));
  let rev = null, saveT = 0;
  const SZf = () => .4 + opt.sz/100*1.4, SPf = () => opt.sp/100*.28, DEf = () => opt.de/50, ALf = () => opt.al/100;
  const still = () => calm || RM.matches || !opt.sz || !opt.sp || !opt.de || !opt.al;
  function nodeAt(el, id){
    const box = el.getBoundingClientRect();
    for (const n of el.querySelectorAll(`[data-av="${id}"]`)) {
      const r = n.getBoundingClientRect(); if (!r.width) continue;
      const clip = n.closest('.list,.msgs,.wall'); // 捲出清單的頭像不畫線
      if (clip) { const c = clip.getBoundingClientRect(); if (r.bottom < c.top+4 || r.top > c.bottom-4) continue; }
      const bar = n.closest('.bar'); // 頂列的頭像(👑我)先往下鑽到頂列與篩選列中間的縫，再橫走出去，不穿過上面的金色標題列
      const gapY = bar ? bar.getBoundingClientRect().bottom - box.top + 4 : null;
      return { x:r.left+r.width/2-box.left, y:r.top+r.height/2-box.top, id, gapY };
    }
    return null;
  }
  // 沿物件外框走：每個物件(標題列、膠囊、面板、清單列、卡片、信件…)的最外框往外 3px 是一條路，
  // 轉角往外打水平 / 垂直射線接到最近的另一條外框，組成路網;每顆光點用隨機權重挑自己的路(隨機流動)，轉角做圓角
  const OBJ = '.title,.bar > *,.panel,.p,.card,.m,.tlh,.reply,.search,.grp,.open,.helpbox,.col,.legend'; // 待審區:欄 .col 也是物件,光點只走欄與卡片之間的間格(Andy r1)
  function graph(el){
    const now = performance.now(); if (el._G && now - el._G.at < 400) return el._G;
    const box = el.getBoundingClientRect(), W = box.width, H = box.height, ins = 4, o = 3;
    const R = n => { const r = n.getBoundingClientRect(); return { l:r.left-box.left, r:r.right-box.left, t:r.top-box.top, b:r.bottom-box.top }; };
    const rects = [{ l:ins, t:ins, r:W-ins, b:H-ins, frame:true }];
    for (const n of el.querySelectorAll(OBJ)) {
      const r = R(n); if (r.r - r.l < 4 || r.b - r.t < 4) continue;
      const clip = n.parentElement && n.parentElement.closest('.list,.msgs,.wall');
      if (clip) { const c = R(clip); if (r.b < c.t || r.t > c.b) continue; r.t = Math.max(r.t, c.t); r.b = Math.min(r.b, c.b); }
      rects.push({ l:Math.max(ins, r.l-o), t:Math.max(ins, r.t-o), r:Math.min(W-ins, r.r+o), b:Math.min(H-ins, r.b+o), n });
    }
    const N = [], segs = [];
    const node = (x,y) => { N.push({ x, y, e:[] }); return N.length-1; };
    for (const r of rects) { // 每個外框四邊
      const k = [node(r.l,r.t), node(r.r,r.t), node(r.r,r.b), node(r.l,r.b)]; r.k = k;
      segs.push({ h:true, c:r.t, a:r.l, b:r.r, pts:[k[0],k[1]] }, { h:false, c:r.r, a:r.t, b:r.b, pts:[k[1],k[2]] },
                { h:true, c:r.b, a:r.l, b:r.r, pts:[k[3],k[2]] }, { h:false, c:r.l, a:r.t, b:r.b, pts:[k[0],k[3]] });
    }
    const ray = (x, y, dx, dy, from) => { // 從轉角往外打，接最近那條垂直的外框邊
      let best = null, bd = 80;
      for (const sg of segs) { if (sg.r === from) continue;
        if (dx && !sg.h) { const d = (sg.c - x) * dx; if (d > .5 && d < bd && y > sg.a && y < sg.b) { bd = d; best = sg; } }
        if (dy && sg.h)  { const d = (sg.c - y) * dy; if (d > .5 && d < bd && x > sg.a && x < sg.b) { bd = d; best = sg; } } }
      if (!best) return;
      const hx = dx ? best.c : x, hy = dy ? best.c : y, h = node(hx, hy); best.pts.push(h);
      const f = from; return h;
    };
    for (const sg of segs) sg.r = null;
    rects.forEach((r, i) => { segs[i*4].r = segs[i*4+1].r = segs[i*4+2].r = segs[i*4+3].r = r; });
    const link = (u, v) => { const w = Math.hypot(N[u].x-N[v].x, N[u].y-N[v].y); if (w < .01) { N[u].e.push([v,.01]); N[v].e.push([u,.01]); return; } N[u].e.push([v,w]); N[v].e.push([u,w]); };
    for (const r of rects) { if (r.frame) continue;
      const [tl,tr,br,bl] = r.k;
      for (const [k,dx,dy] of [[tl,-1,0],[tl,0,-1],[tr,1,0],[tr,0,-1],[br,1,0],[br,0,1],[bl,-1,0],[bl,0,1]]) { const h = ray(N[k].x, N[k].y, dx, dy, r); if (h != null) link(k, h); } }
    const av = {}; // 頭像：往左橫走到包住它的那個物件的左外框
    for (const n of el.querySelectorAll('[data-av]')) {
      const id = n.dataset.av; if (av[id] != null) continue;
      const r = R(n); if (!r.r) continue; const cx = (r.l+r.r)/2, cy = (r.t+r.b)/2;
      const own = rects.filter(q => !q.frame && q.n && q.n.contains(n) && cy > q.t && cy < q.b).sort((p,q)=>(p.r-p.l)-(q.r-q.l))[0];
      if (!own) continue; const sg = segs[rects.indexOf(own)*4+3];
      const h = node(sg.c, cy); sg.pts.push(h); const c0 = node(cx, cy); link(c0, h); av[id] = c0;
    }
    for (const sg of segs) { const k = sg.h ? 'x' : 'y'; const u = [...new Set(sg.pts)].sort((p,q)=>N[p][k]-N[q][k]); for (let i=1;i<u.length;i++) link(u[i-1], u[i]); }
    const edge = []; N.forEach((n,i) => { if (n.e.length) edge.push(i); });
    // 外框四邊的點各補一份，讓最右、最下也常被選到
    N.forEach((n,i) => { if (n.e.length && (n.x <= ins+.5 || n.x >= W-ins-.5 || n.y <= ins+.5 || n.y >= H-ins-.5)) edge.push(i, i, i, i, i, i); });
    return el._G = { at:now, N, av, edge };
  }
  function path(G, a, b, jitter, via){ // Dijkstra;jitter>0 ⇒ 每段權重亂數放大;via = 先繞去的隨機點(讓光點跑遍整個畫面，含最右、最下)
    const s0 = G.av[a], t0 = G.av[b]; if (s0 == null || t0 == null) return null;
    if (via != null) { const p1 = walk(G, s0, via, jitter), p2 = walk(G, via, t0, jitter); return p1 && p2 ? rounded(p1.concat(p2.slice(1))) : null; }
    const raw = walk(G, s0, t0, jitter); return raw && rounded(raw);
  }
  function walk(G, s, t, jitter){
    const N = G.N, d = new Float64Array(N.length).fill(Infinity), pv = new Int32Array(N.length).fill(-1), H = [[0,s]]; d[s] = 0;
    const push = it => { H.push(it); let i = H.length-1; while (i) { const p = (i-1)>>1; if (H[p][0] <= H[i][0]) break; [H[p],H[i]] = [H[i],H[p]]; i = p; } };
    const pop = () => { const top = H[0], last = H.pop(); if (H.length) { H[0] = last; let i = 0; for (;;) { const l = 2*i+1, r = l+1; let m = i; if (l < H.length && H[l][0] < H[m][0]) m = l; if (r < H.length && H[r][0] < H[m][0]) m = r; if (m === i) break; [H[m],H[i]] = [H[i],H[m]]; i = m; } } return top; };
    const rnd = new Map();
    while (H.length) { const [du,u] = pop(); if (du > d[u]) continue; if (u === t) break;
      for (const [v,w] of N[u].e) { let ww = w; if (jitter) { const k = u < v ? u+','+v : v+','+u; if (!rnd.has(k)) rnd.set(k, 1 + Math.random()*jitter); ww *= rnd.get(k); }
        if (du + ww < d[v]) { d[v] = du + ww; pv[v] = u; push([d[v], v]); } } }
    if (d[t] === Infinity) return null;
    const raw = []; for (let u = t; u >= 0; u = pv[u]) raw.unshift({ x:N[u].x, y:N[u].y });
    return raw;
  }
  function rounded(P){ // 轉角圓角：每個折點換成半徑 ≤6px 的弧(取樣 6 點)
    const q = P.filter((p,i) => !i || Math.hypot(p.x-P[i-1].x, p.y-P[i-1].y) > .3); if (q.length < 3) return q;
    const out = [q[0]];
    for (let i=1;i<q.length-1;i++) {
      const A = q[i-1], B = q[i], C = q[i+1], l1 = Math.hypot(B.x-A.x,B.y-A.y), l2 = Math.hypot(C.x-B.x,C.y-B.y), r = Math.min(6, l1/2, l2/2);
      const p0 = { x:B.x+(A.x-B.x)/l1*r, y:B.y+(A.y-B.y)/l1*r }, p2 = { x:B.x+(C.x-B.x)/l2*r, y:B.y+(C.y-B.y)/l2*r };
      for (let k=0;k<=6;k++) { const t = k/6, u = 1-t; out.push({ x:u*u*p0.x+2*u*t*B.x+t*t*p2.x, y:u*u*p0.y+2*u*t*B.y+t*t*p2.y }); }
    }
    out.push(q[q.length-1]); return out;
  }
  const poly = pts => { const cum = [0]; for (let i=1;i<pts.length;i++) cum.push(cum[i-1] + Math.hypot(pts[i].x-pts[i-1].x, pts[i].y-pts[i-1].y)); return { pts, cum, L: cum[cum.length-1] || 1 }; };
  function geo(el){ // 靜態版 / 點選用：每條流取最短路
    const out = [], G = graph(el);
    for (const f of FLOWS) {
      if (el._s.pair && f.a!==el._s.pair && f.b!==el._s.pair) continue;
      const pts = path(G, f.a, f.b, 0); if (!pts) continue;
      out.push({ f, ...poly(pts) });
    }
    return out;
  }
  const pt = (g,t) => { const d = t*g.L; let i = 1; while (i < g.cum.length-1 && g.cum[i] < d) i++;
    const seg = (g.cum[i]-g.cum[i-1]) || 1, u = (d-g.cum[i-1])/seg, P = g.pts[i-1], Q = g.pts[i];
    return { x:P.x+(Q.x-P.x)*u, y:P.y+(Q.y-P.y)*u }; };
  function size(el){
    const cv = el._cv, r = el.getBoundingClientRect(), d = Math.min(2, devicePixelRatio||1);
    if (cv.width !== Math.round(r.width*d) || cv.height !== Math.round(r.height*d)) { cv.width = Math.round(r.width*d); cv.height = Math.round(r.height*d); }
    const x = cv.getContext('2d'); x.setTransform(d,0,0,d,0,0); return x;
  }
  function line(x, g, alpha){
    x.strokeStyle = `rgba(${KC[g.f.kind]},${alpha})`; x.lineWidth = 1.3; x.setLineDash(g.f.kind==='hold'?[3,4]:[]);
    x.lineJoin = 'round'; x.beginPath(); x.moveTo(g.pts[0].x,g.pts[0].y); for (const q of g.pts.slice(1)) x.lineTo(q.x,q.y); x.stroke(); x.setLineDash([]);
  }
  function drawStatic(el){
    const x = size(el), G = geo(el); x.clearRect(0,0,el._cv.width,el._cv.height);
    for (const g of G) {
      line(x, g, .55);
      let li = 1; for (let i=2;i<g.pts.length;i++) if (g.cum[i]-g.cum[i-1] > g.cum[li]-g.cum[li-1]) li = i; // 則數標在最長那段縫的中間
      const m = pt(g, (g.cum[li]+g.cum[li-1])/2/g.L), lbl = (g.f.kind==='hold'?'⏸ ':'') + g.f.n;
      x.font = '700 11px "Noto Sans TC",sans-serif'; const w = x.measureText(lbl).width + 10;
      x.fillStyle = 'rgba(22,22,27,.92)'; x.strokeStyle = `rgba(${KC[g.f.kind]},.9)`;
      x.beginPath(); x.roundRect(m.x-w/2, m.y-8, w, 16, 8); x.fill(); x.stroke();
      x.fillStyle = `rgb(${KC[g.f.kind]})`; x.textAlign='center'; x.textBaseline='middle'; x.fillText(lbl, m.x, m.y+.5);
    }
  }
  function frame(now){
    raf = 0; const dt = Math.min(64, now - (last||now)); last = now;
    let total = 0; for (const el of mocks) total += el._p.length;
    for (const el of mocks) {
      if (!el._vis) continue;
      const x = size(el), G = graph(el); x.clearRect(0,0,el._cv.width,el._cv.height);
      for (const f of FLOWS) {
        if (el._s.pair && f.a!==el._s.pair && f.b!==el._s.pair) continue;
        if (G.av[f.a] == null || G.av[f.b] == null) continue;
        // 設計稿定案的節奏:視窗內 n 則 ⇒ 每 10 秒約 n 顆(×數量滑桿);n 是真的則數
        f._acc = (f._acc||0) + dt * f.n * DEf() / 10000;
        if (f._acc >= 1 && total < MAX) { const via = Math.random() < .95 ? G.edge[Math.floor(Math.random()*G.edge.length)] : null; // 多半先繞到外圈隨機一點(四邊平均)
          const pts = path(G, f.a, f.b, 1.5, via); f._acc = 0;
          if (pts) { const g = poly(pts); el._p.push({ f, g, d:0, v:SPf()*(1 + Math.min(5,f.n)*.06)*(URG(f.kind)?1.6:1), hold:f.kind==='hold', life:0 }); total++; } }
        if (f._acc > 2) f._acc = 2;
      }
      el._p = el._p.filter(q => {
        const g = q.g, f = q.f;
        if (q.hold && q.d >= g.L/2) { q.life += dt; if (q.life > 3500) return false; } else q.d += q.v*dt;
        if (q.d >= g.L) return false;
        const c = KC[f.kind], a0 = (q.hold && q.d >= g.L/2 ? .35 + .65*Math.abs(Math.sin(q.life/220)) : 1) * ALf();
        const R = (URG(f.kind) ? 2.8 : ASK(f.kind) ? 2.3 : 1.8) * SZf();
        const TN = Math.round(opt.tl/10);
        for (let k=TN;k>=0;k--) { // 尾巴 TN 節漸淡
          const P = pt(g, Math.max(0, Math.min(q.d, q.hold ? g.L/2 : g.L) - k*R*1.4) / g.L), a = a0 * (1 - k/(TN+1)), rr = R * (1 - k/(TN+1)*.5);
          const grd = x.createRadialGradient(P.x,P.y,0,P.x,P.y,rr*2.2);
          grd.addColorStop(0,`rgba(${c},${a})`); grd.addColorStop(1,`rgba(${c},0)`);
          x.fillStyle = grd; x.beginPath(); x.arc(P.x,P.y,rr*2.2,0,6.283); x.fill();
          if (!k) { x.fillStyle = `rgba(255,248,230,${a})`; x.beginPath(); x.arc(P.x,P.y,rr*.5,0,6.283); x.fill(); }
        }
        return true;
      });
    }
    kick();
  }
  function kick(){ if (raf || document.hidden || still()) return; if ([...mocks].some(m=>m._vis)) raf = requestAnimationFrame(frame); }
  function redraw(){ if (still()) { cancelAnimationFrame(raf); raf = 0; for (const el of mocks) { el._p = []; drawStatic(el); } } else { last = 0; kick(); } }
  async function save(){ // 拉完停 0.6 秒才存一次;revision 不符 ⇒ 重讀伺服器的值(不自動重送,2c §對 2b 意見)
    if (rev == null || !window.AIB) return;
    const value = {}; for (const k in PK) value[PK[k]] = opt[k];
    try { const r = await AIB.prefsSet(rev, value); if (r && r.revision != null) rev = r.revision; }
    catch (e) { try { FX.loadPrefs(await AIB.prefs()); } catch (e2) {} if (FX.onSaveError) FX.onSaveError(e); }
  }
  const io = new IntersectionObserver(es => { for (const e of es) e.target._vis = e.isIntersecting; kick(); });
  document.addEventListener('visibilitychange', () => { last = 0; kick(); });
  RM.addEventListener?.('change', redraw);
  addEventListener('resize', () => still() && redraw());
  return {
    attach(el){ const cv = document.createElement('canvas'); cv.className = 'fx'; cv.setAttribute('aria-hidden','true'); el._cv = cv; el._p = []; el.appendChild(cv); mocks.add(el); io.observe(el); el.addEventListener('scroll', () => still() && drawStatic(el), true); },
    rendered(el){ if (still()) requestAnimationFrame(() => drawStatic(el)); },
    opt,
    setFlows(list){ // r2(Andy「光點沒跑」):預設數量 10% 每條流約 50 秒一顆、從 0 累積 ⇒ 開頁前十幾秒一顆都沒有;新出現的流先給接近 1,0–3 秒內先出一顆,之後照原節奏(預設值不改)
      const was = new Map(FLOWS.map((f) => [f.a + '|' + f.b + '|' + f.kind, f._acc]));
      for (const f of list) { const k = f.a + '|' + f.b + '|' + f.kind; f._acc = was.has(k) ? was.get(k) : 1 - Math.random() * .05; }
      FLOWS = list; for (const el of mocks) { el._G = null; } redraw(); },
    loadPrefs(p){ // p = GET /api/v1/prefs/contacts_fx
      if (!p || !p.value) return; rev = p.revision;
      const was = still(); for (const k in PK) if (Number.isFinite(p.value[PK[k]])) opt[k] = clamp(p.value[PK[k]]);
      if (was !== still()) for (const el of mocks) el._p = []; redraw(); },
    setOpt(k,v){ const was = still(); opt[k] = clamp(v); if (was !== still()) { for (const el of mocks) el._p = []; } redraw();
      clearTimeout(saveT); saveT = setTimeout(save, 600); },
    onSaveError: null,
    setCalm(v){ calm = v; document.documentElement.classList.toggle('calm', v); redraw(); },
    hit(el, e){ // 點到線 15px 內 ⇒ 回傳不是「我」的那一端
      const G = geo(el), b = el.getBoundingClientRect(), mx = e.clientX-b.left, my = e.clientY-b.top;
      let best = null, bd = 15;
      for (const g of G) for (let i=0;i<=120;i++) { const P = pt(g,i/120), d = Math.hypot(P.x-mx,P.y-my); if (d<bd) { bd=d; best = g.f.a==='me' ? g.f.b : g.f.a; } }
      return best;
    },
  };
})();
