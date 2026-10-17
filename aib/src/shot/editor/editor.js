/* SPDX-License-Identifier: Apache-2.0 */
"use strict";
// Ez2 截圖編輯器(AIB 截圖小幫手 Control+Shift+S 截圖後開啟;來源:嘸蝦米鼠鬚管安裝包 v3 files/shot-editor)
// 編輯器本體逐字取自 Ez2 AI 人格萃取 App:ez2-ai desktop-shell/src/chat-ui/floating-chat.js
//   SHOT_FORMATS / SHOT_QUALITIES / shotBytes / encodeCanvasShot / el / _saveShotDialog / buildAnnotate
// 只改「跟 App 接的地方」:api.saveImageData / savePref / 關閉 / 剪貼簿 → 原生外框(ez2-shot.js)的訊息。
// 外框在載入前放好 window.__EZ2_SHOT__ = { dataUrl, prefs, name }。
(function () {
  const INIT = window.__EZ2_SHOT__ || {};
  const native = (msg) => { try { window.webkit.messageHandlers.ez2.postMessage(JSON.stringify(msg)); } catch (e) { console.error(e); } };

  // ===== 以下取自 App(原樣)=====
  function el(tag, props, css) {
    const e = document.createElement(tag);
    if (props) for (const k in props) { if (k === "text") e.textContent = props[k]; else e.setAttribute(k, props[k]); }
    if (css) e.style.cssText = css;
    return e;
  }
  const SHOT_FORMATS = {
    png: { label: "PNG", note: "無損", type: "image/png", ext: "png", lossless: true },
    webp: { label: "WebP", note: "小檔透明支援", type: "image/webp", ext: "webp" },
    avif: { label: "AVIF", note: "更小檔案", type: "image/avif", ext: "avif" },
    jpg: { label: "JPG", note: "相容性高", type: "image/jpeg", ext: "jpg" },
  };
  const SHOT_QUALITIES = {
    high: { label: "高畫質", note: "92%", q: 0.92 },
    normal: { label: "標準", note: "82%", q: 0.82 },
    min: { label: "高壓縮", note: "70%", q: 0.70 },
  };
  function shotBytes(dataUrl) {
    return Math.round(((String(dataUrl).split(",")[1] || "").length * 0.75) / 1024);
  }
  function encodeCanvasShot(canvas, format, quality) {
    const fmt = SHOT_FORMATS[format] || SHOT_FORMATS.png;
    const q = SHOT_QUALITIES[quality] || SHOT_QUALITIES.high;
    const dataUrl = fmt.lossless ? canvas.toDataURL(fmt.type) : canvas.toDataURL(fmt.type, q.q);
    if (!dataUrl.startsWith("data:" + fmt.type)) {
      if (format === "avif") {
        const webp = canvas.toDataURL(SHOT_FORMATS.webp.type, q.q);
        if (webp.startsWith("data:" + SHOT_FORMATS.webp.type)) return { dataUrl: webp, ext: "webp", format: "webp", requestedFormat: "avif", quality, fallback: true };
      }
      return { dataUrl: canvas.toDataURL("image/png"), ext: "png", format: "png", quality, fallback: true };
    }
    return { dataUrl, ext: fmt.ext, format: fmt.ext, quality };
  }

  // App 的 this.* 換成這個單頁的對應物
  const view = {
    box: document.getElementById("box"),
    dockEl: null,
    state: { settings: Object.assign({}, INIT.prefs || {}) },
    _annotateOverlay: null,
    _closeAnnotateOverlay() { native({ cmd: "cancel" }); },
    toast(text, ms) { const t = document.getElementById("toast"); t.textContent = text; t.style.display = "block"; if (ms) setTimeout(() => t.style.display = "none", ms); },
  };
  const api = {
    savePref(p) { native({ cmd: "prefs", prefs: p }); },
  };

  // 存圖確認視窗(App 原樣)
  view._saveShotDialog = function (canvas) {
    return new Promise((resolve) => {
      const st = (this.state && this.state.settings) || {};
      const curFormat = SHOT_FORMATS[st.shotFormat] ? st.shotFormat : "png";
      const curQuality = SHOT_QUALITIES[st.shotQuality] ? st.shotQuality : "high";
      const bigCanvas = (canvas.width * canvas.height) > 4000000;
      const dlg = el("div", null, "position:fixed;inset:0;z-index:210;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.5);-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px)");
      const card = el("div", null, "width:min(360px,92%);border-radius:14px;padding:18px;background:var(--glass,rgba(23,27,37,.96));-webkit-backdrop-filter:var(--glass-blur);backdrop-filter:var(--glass-blur);border:1px solid var(--glass-border,var(--line));box-shadow:var(--sh-2);color:var(--text)");
      card.appendChild(el("div", { text: "存圖 · 格式 / 壓縮" }, "font-size:15px;font-weight:700;margin-bottom:3px"));
      card.appendChild(el("div", { text: bigCanvas ? "選擇儲存格式與壓縮比" : "選擇儲存格式與壓縮比，下方會預估大小" }, "font-size:12px;color:var(--muted);margin-bottom:12px"));
      // 維護者 10-10:最上面大大寫「送到哪一個 AI」,不用點開選單就知道(下面選了就跟著變)
      const banner = el("div", null, "display:none;margin:-4px 0 12px;padding:9px 11px;border-radius:10px;font-size:14px;font-weight:700;line-height:1.35;word-break:break-all");
      card.appendChild(banner);
      const grid = el("div", null, "display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px");
      const formatWrap = el("label", null, "display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--muted)");
      formatWrap.appendChild(el("span", { text: "儲存格式" }));
      const formatSel = el("select", null, "height:34px;border-radius:9px;border:1px solid var(--line);background:var(--panel);color:var(--text);padding:0 9px");
      for (const k of ["png", "webp", "avif", "jpg"]) formatSel.appendChild(el("option", { value: k, text: SHOT_FORMATS[k].label + " · " + SHOT_FORMATS[k].note }));
      formatSel.value = curFormat;
      formatWrap.appendChild(formatSel);
      const qualityWrap = el("label", null, "display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--muted)");
      qualityWrap.appendChild(el("span", { text: "壓縮比" }));
      const qualitySel = el("select", null, "height:34px;border-radius:9px;border:1px solid var(--line);background:var(--panel);color:var(--text);padding:0 9px");
      for (const k of ["high", "normal", "min"]) qualitySel.appendChild(el("option", { value: k, text: SHOT_QUALITIES[k].label + " · " + SHOT_QUALITIES[k].note }));
      qualitySel.value = curQuality;
      qualityWrap.appendChild(qualitySel);
      grid.appendChild(formatWrap); grid.appendChild(qualityWrap); card.appendChild(grid);
      const sizeNote = el("div", null, "min-height:18px;font-size:12px;color:var(--muted);margin-bottom:10px");
      const updateSize = () => {
        const fmt = SHOT_FORMATS[formatSel.value] || SHOT_FORMATS.png;
        let text = fmt.lossless ? "PNG 為無損格式，壓縮比會保留為下次 WebP/AVIF/JPG 使用" : "壓縮比越高，檔案越小但細節越少";
        if (!bigCanvas) {
          try {
            const enc = encodeCanvasShot(canvas, formatSel.value, qualitySel.value);
            const kb = shotBytes(enc.dataUrl);
            text = "預估 " + kb + " KB" + (enc.fallback ? "，目前環境不支援此格式，會改存 PNG" : "");
          } catch (e) {}
        }
        sizeNote.textContent = text;
      };
      formatSel.onchange = updateSize; qualitySel.onchange = updateSize; updateSize();
      card.appendChild(sizeNote);
      // AIB 版(10-09 維護者):存回時順便送到某一席的輸入框(預載圖,不送出)。清單由 shot_helper.py 開編輯器前給
      const seatInfo = (INIT.seats && Array.isArray(INIT.seats.seats)) ? INIT.seats : null;
      let seatSel = null, noteInp = null;
      if (seatInfo && seatInfo.seats.length) {
        const seatWrap = el("label", null, "display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--muted);margin-bottom:8px");
        seatWrap.appendChild(el("span", { text: "送到(放進那一席的輸入框,不會送出)" }));
        seatSel = el("select", null, "height:34px;border-radius:9px;border:1px solid var(--line);background:var(--panel);color:var(--text);padding:0 9px");
        seatSel.appendChild(el("option", { value: "", text: "不送(只存檔)" }));
        for (const s of seatInfo.seats) seatSel.appendChild(el("option", { value: s.addr, text: s.label }));
        seatSel.value = seatInfo.seats.some((s) => s.addr === seatInfo.last) ? seatInfo.last : "";
        seatWrap.appendChild(seatSel);
        const updBanner = () => {
          const s = seatInfo.seats.find((x) => x.addr === seatSel.value);
          banner.style.display = "block";
          if (s) {
            banner.textContent = "送到:🟢 " + s.label;
            banner.style.background = "color-mix(in srgb, var(--brand,#3b82f6) 22%, transparent)";
            banner.style.border = "1px solid var(--brand,#3b82f6)"; banner.style.color = "var(--text,#fff)";
          } else {
            banner.textContent = "不送,只存檔";
            banner.style.background = "transparent"; banner.style.border = "1px dashed var(--line,#555)";
            banner.style.color = "var(--muted,#999)";
          }
        };
        seatSel.addEventListener("change", updBanner); updBanner();
        card.appendChild(seatWrap);
        noteInp = el("input", null, "height:32px;border-radius:9px;border:1px solid var(--line);background:var(--panel);color:var(--text);padding:0 9px;margin-bottom:4px");
        noteInp.type = "text"; noteInp.placeholder = "附一句話(可不填)";
        noteInp.addEventListener("keydown", (e) => { e.stopPropagation(); });   // 打字時不要觸發編輯器的快速鍵
        card.appendChild(noteInp);
      }
      const remember = el("input"); remember.type = "checkbox"; remember.style.accentColor = "var(--brand)";
      const remLbl = el("label", null, "display:flex;align-items:center;gap:7px;margin:10px 0 15px;font-size:12px;color:var(--muted);cursor:pointer");
      remLbl.appendChild(remember); remLbl.appendChild(el("span", { text: "記住此選擇為預設(下次自動選)" }));
      card.appendChild(remLbl);
      const foot = el("div", null, "display:flex;gap:8px;justify-content:flex-end");
      const cancelB = el("button", { class: "btn", text: "取消" });
      const okB = el("button", { class: "btn btn-primary", text: "存檔" });
      const close = (val) => { dlg.remove(); view._dialogOpen = false; resolve(val); };
      cancelB.onclick = () => close(null);
      okB.onclick = () => {
        const chosen = { format: formatSel.value, quality: qualitySel.value,
          seat: seatSel ? seatSel.value : "", note: noteInp ? noteInp.value.trim() : "" };
        if (remember.checked) {
          if (this.state && this.state.settings) { this.state.settings.shotFormat = chosen.format; this.state.settings.shotQuality = chosen.quality; }
          try { api.savePref({ shotFormat: chosen.format, shotQuality: chosen.quality }); } catch (e) {}
        }
        close(chosen);
      };
      foot.appendChild(cancelB); foot.appendChild(okB); card.appendChild(foot);
      dlg.appendChild(card);
      dlg.addEventListener("click", (e) => { if (e.target === dlg) close(null); });
      view._dialogOpen = true;
      document.body.appendChild(dlg);
    });
  };

  // 標註編輯器(App 的 buildAnnotate 原樣;只換掉存回/複製/取消三個按鈕接的地方,並加鍵盤快速鍵)
  view.buildAnnotate = function (at, img, startCrop) {
    const brandColor = (getComputedStyle(document.documentElement).getPropertyValue("--brand").trim() || "#58a6ff");
    const overlay = el("div", null, "position:absolute;inset:0;background:rgba(0,0,0,.75);z-index:50;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;padding:16px");
    overlay.setAttribute("data-ez2-annotate-overlay", "1");
    this._annotateOverlay = overlay;
    const bar = el("div", null, "display:flex;gap:6px;align-items:center;flex-wrap:wrap;max-width:100%");
    const wrap = el("div", null, "flex:1;min-height:0;overflow:auto;display:flex;align-items:center;justify-content:center;position:relative");
    const maxW = Math.min(img.width, this.box.clientWidth - 60), scale = Math.min(1, maxW / img.width);
    const base = document.createElement("canvas"); base.width = img.width; base.height = img.height;
    base.getContext("2d").drawImage(img, 0, 0);
    const canvas = el("canvas", null, "border:1px solid var(--line,#2a3142);border-radius:6px;max-width:100%;touch-action:none");
    canvas.width = img.width; canvas.height = img.height; canvas.style.width = Math.round(img.width * scale) + "px";
    const ctx = canvas.getContext("2d");

    let tool = "pen", color = "#ff3b3b", objs = [], sel = null, cropRect = null;
    const setTool = (id) => { tool = id; canvas.style.cursor = id === "select" ? "move" : id === "text" ? "text" : "crosshair"; for (const c of bar.querySelectorAll("button[data-tool]")) c.style.background = c.getAttribute("data-tool") === id ? "var(--brand,#58a6ff)" : "transparent"; };

    const drawArrow = (x1, y1, x2, y2, col, w) => {
      ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = w; ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      const a = Math.atan2(y2 - y1, x2 - x1), h = 6 + w * 2.5;
      ctx.beginPath(); ctx.moveTo(x2, y2);
      ctx.lineTo(x2 - h * Math.cos(a - 0.4), y2 - h * Math.sin(a - 0.4));
      ctx.lineTo(x2 - h * Math.cos(a + 0.4), y2 - h * Math.sin(a + 0.4));
      ctx.closePath(); ctx.fill();
    };
    const drawMosaic = (o) => {
      const x = Math.max(0, Math.round(Math.min(o.x, o.x + o.w))), y = Math.max(0, Math.round(Math.min(o.y, o.y + o.h)));
      const w = Math.round(Math.abs(o.w)), h = Math.round(Math.abs(o.h)); if (w < 2 || h < 2) return;
      const block = 12, bctx = base.getContext("2d");
      for (let by = y; by < y + h; by += block) for (let bx = x; bx < x + w; bx += block) {
        const cw = Math.min(block, x + w - bx), ch = Math.min(block, y + h - by); if (cw < 1 || ch < 1) continue;
        const d = bctx.getImageData(bx, by, cw, ch); let rr = 0, gg = 0, bb = 0, n = d.data.length / 4;
        for (let i = 0; i < d.data.length; i += 4) { rr += d.data[i]; gg += d.data[i + 1]; bb += d.data[i + 2]; }
        ctx.fillStyle = `rgb(${Math.round(rr / n)},${Math.round(gg / n)},${Math.round(bb / n)})`; ctx.fillRect(bx, by, block, block);
      }
    };
    const drawObj = (o) => {
      if (o.type === "pen") { ctx.strokeStyle = o.color; ctx.lineWidth = o.w; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.beginPath(); o.pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)); ctx.stroke(); }
      else if (o.type === "rect") { ctx.strokeStyle = o.color; ctx.lineWidth = o.w; ctx.strokeRect(o.x, o.y, o.w2, o.h2); }
      else if (o.type === "arrow") drawArrow(o.x1, o.y1, o.x2, o.y2, o.color, o.w);
      else if (o.type === "mosaic") drawMosaic(o);
      else if (o.type === "text") { ctx.fillStyle = o.color; ctx.font = "600 " + o.size + "px -apple-system, sans-serif"; ctx.textBaseline = "top"; o.text.split("\n").forEach((ln, i) => ctx.fillText(ln, o.x, o.y + i * o.size * 1.25)); }
    };
    const bbox = (o) => {
      if (o.type === "pen") { const xs = o.pts.map(p => p.x), ys = o.pts.map(p => p.y); return { x: Math.min(...xs) - 6, y: Math.min(...ys) - 6, w: Math.max(...xs) - Math.min(...xs) + 12, h: Math.max(...ys) - Math.min(...ys) + 12 }; }
      if (o.type === "rect" || o.type === "mosaic") { const x = Math.min(o.x, o.x + (o.w2 !== undefined ? o.w2 : o.w)), y = Math.min(o.y, o.y + (o.h2 !== undefined ? o.h2 : o.h)); return { x: x - 4, y: y - 4, w: Math.abs(o.w2 !== undefined ? o.w2 : o.w) + 8, h: Math.abs(o.h2 !== undefined ? o.h2 : o.h) + 8 }; }
      if (o.type === "arrow") return { x: Math.min(o.x1, o.x2) - 8, y: Math.min(o.y1, o.y2) - 8, w: Math.abs(o.x2 - o.x1) + 16, h: Math.abs(o.y2 - o.y1) + 16 };
      if (o.type === "text") { ctx.font = "600 " + o.size + "px sans-serif"; const lines = o.text.split("\n"); const w = Math.max(...lines.map(l => ctx.measureText(l).width)); return { x: o.x - 3, y: o.y - 3, w: w + 6, h: lines.length * o.size * 1.25 + 6 }; }
      return { x: 0, y: 0, w: 0, h: 0 };
    };
    const normRect = (r) => {
      if (!r) return null;
      const x = Math.max(0, Math.min(canvas.width, Math.min(r.x, r.x + r.w)));
      const y = Math.max(0, Math.min(canvas.height, Math.min(r.y, r.y + r.h)));
      const x2 = Math.max(0, Math.min(canvas.width, Math.max(r.x, r.x + r.w)));
      const y2 = Math.max(0, Math.min(canvas.height, Math.max(r.y, r.y + r.h)));
      const w = x2 - x, h = y2 - y;
      return w >= 4 && h >= 4 ? { x, y, w, h } : null;
    };
    const drawCrop = () => {
      const r = normRect(cropRect);
      if (!r) return;
      ctx.save();
      ctx.fillStyle = "rgba(0,0,0,.38)";
      ctx.fillRect(0, 0, canvas.width, r.y);
      ctx.fillRect(0, r.y + r.h, canvas.width, canvas.height - r.y - r.h);
      ctx.fillRect(0, r.y, r.x, r.h);
      ctx.fillRect(r.x + r.w, r.y, canvas.width - r.x - r.w, r.h);
      ctx.strokeStyle = brandColor;
      ctx.lineWidth = 2;
      ctx.setLineDash([8, 5]);
      ctx.strokeRect(r.x, r.y, r.w, r.h);
      ctx.setLineDash([]);
      ctx.restore();
    };
    const redraw = (showCrop = true) => {
      ctx.drawImage(base, 0, 0);
      for (const o of objs) drawObj(o);
      if (sel) { const b = bbox(sel); ctx.strokeStyle = brandColor; ctx.lineWidth = 1; ctx.setLineDash([4, 3]); ctx.strokeRect(b.x, b.y, b.w, b.h); ctx.setLineDash([]); }
      if (showCrop) drawCrop();
    };
    const exportCanvas = () => {
      redraw(false);
      const r = normRect(cropRect);
      if (!r) { redraw(true); return canvas; }
      const out = document.createElement("canvas");
      out.width = Math.round(r.w);
      out.height = Math.round(r.h);
      out.getContext("2d").drawImage(canvas, r.x, r.y, r.w, r.h, 0, 0, out.width, out.height);
      redraw(true);
      return out;
    };
    const hit = (x, y) => { for (let i = objs.length - 1; i >= 0; i--) { const b = bbox(objs[i]); if (x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h) return objs[i]; } return null; };
    const moveObj = (o, dx, dy) => {
      if (o.type === "pen") o.pts.forEach(p => { p.x += dx; p.y += dy; });
      else if (o.type === "arrow") { o.x1 += dx; o.y1 += dy; o.x2 += dx; o.y2 += dy; }
      else { o.x += dx; o.y += dy; }
    };

    const svgOf = (nm, sz) => window.Ez2Icon ? window.Ez2Icon.svg(nm, { size: sz || 15 }) : el("span", { text: "" });
    const annoBtn = (iconName, title, primary) => { const b = el("button", { title: title || "" }, "display:inline-flex;align-items:center;gap:5px;font-size:12px;padding:6px 9px;border-radius:8px;border:1px solid var(--line,#2a3142);background:" + (primary ? "var(--brand,#58a6ff)" : "transparent") + ";color:" + (primary ? "var(--brand-ink,#04121f)" : "var(--text,#eef2ff)") + ";cursor:pointer"); b.appendChild(svgOf(iconName)); return b; };
    const mkTool = (id, iconName, title) => { const b = annoBtn(iconName, title); b.onclick = () => setTool(id); b.setAttribute("data-tool", id); return b; };
    bar.appendChild(mkTool("crop", "scan", "裁切")); bar.appendChild(mkTool("select", "move", "移動")); bar.appendChild(mkTool("pen", "pen", "畫筆")); bar.appendChild(mkTool("arrow", "arrow-right", "箭頭")); bar.appendChild(mkTool("rect", "square", "方框")); bar.appendChild(mkTool("text", "type", "文字")); bar.appendChild(mkTool("mosaic", "layout-grid", "馬賽克"));
    bar.appendChild(el("span", null, "width:1px;height:20px;background:var(--line,#2a3142);margin:0 2px"));
    const setColor = (c) => { color = c; if (colorInput) colorInput.value = c; if (hexLabel) hexLabel.textContent = c; if (sel && sel.color) { sel.color = c; redraw(); } };
    for (const c of ["#ff3b3b", "#ffd400", "#3b82f6"]) { const sw = el("button", { title: c }, "width:22px;height:22px;border-radius:50%;border:2px solid #0006;cursor:pointer;background:" + c); sw.onclick = () => setColor(c); bar.appendChild(sw); }
    const colorInput = el("input", { type: "color", title: "自選顏色(全色板)" }, "width:26px;height:26px;padding:0;border:1px solid var(--line,#2a3142);border-radius:6px;background:none;cursor:pointer");
    colorInput.value = color; colorInput.oninput = () => setColor(colorInput.value);
    bar.appendChild(colorInput);
    const hexLabel = el("span", { text: color, title: "雙擊複製色碼" }, "font-size:11px;font-family:monospace;padding:2px 6px;border:1px solid var(--line,#2a3142);border-radius:5px;cursor:pointer;user-select:none");
    hexLabel.ondblclick = () => { native({ cmd: "copytext", text: hexLabel.textContent }); const o = hexLabel.textContent; hexLabel.textContent = "已複製"; setTimeout(() => hexLabel.textContent = o, 1000); };
    bar.appendChild(hexLabel);
    const dropBtn = annoBtn("pipette", "取色滴管:點畫面任一點取得色碼(整個螢幕)");
    dropBtn.onclick = async () => {
      if (window.EyeDropper) {
        try { const res = await new window.EyeDropper().open(); if (res && res.sRGBHex) { setColor(res.sRGBHex); } }
        catch (e) { /* 使用者取消 */ }
      } else if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.ez2) {
        // Mac 內建網頁元件沒有 EyeDropper ⇒ 叫 macOS 原生取色器(NSColorSampler:有放大鏡、整個螢幕都能取)
        window.ez2SetColor = (hex) => { if (hex) setColor(hex); };
        native({ cmd: "eyedropper" });
      } else { setTool("eyedrop"); }
    };
    bar.appendChild(dropBtn);
    const undo = () => { if (cropRect) cropRect = null; else objs.pop(); sel = null; redraw(); };
    const undoBtn = annoBtn("undo", "復原(⌘Z)"); undoBtn.onclick = undo;
    bar.appendChild(undoBtn);
    const delSel = () => { if (sel) { objs = objs.filter(o => o !== sel); sel = null; redraw(); } };
    const delBtn = annoBtn("trash", "刪除選取"); delBtn.onclick = delSel; bar.appendChild(delBtn);
    bar.appendChild(el("span", null, "flex:1"));
    const pngB64 = (cv) => cv.toDataURL("image/png").split(",")[1];
    // 10-18 維護者:原本這裡有一顆「複製到剪貼簿」圖示(複製後編輯器還開著),跟存回旁的「只存剪貼簿」重複 ⇒ 拿掉,只留後者;存回也會順便複製
    const cancelBtn = annoBtn("x", "取消(Esc):不存檔"); cancelBtn.onclick = () => this._closeAnnotateOverlay(); bar.appendChild(cancelBtn);
    const saveBtn = annoBtn("check", "存回(Enter):存進截圖資料夾並複製到剪貼簿", true); saveBtn.appendChild(el("span", { text: "存回" }));
    const save = async () => {
      sel = null; const out = exportCanvas();
      const chosen = await this._saveShotDialog(out);
      if (!chosen) return;
      const enc = encodeCanvasShot(out, chosen.format, chosen.quality);
      native({ cmd: "save", b64: enc.dataUrl.split(",")[1], ext: enc.ext, png: pngB64(out), seat: chosen.seat || "", note: chosen.note || "" });
      this.toast("存回中…");
    };
    saveBtn.onclick = save;
    // 10-18 維護者:存回旁邊加「只存剪貼簿」—— 複製到剪貼簿就關掉,不存檔、不進第二大腦、不送任何席
    const clipBtn = annoBtn("copy", "只存剪貼簿:複製到剪貼簿就關掉(不存檔)"); clipBtn.appendChild(el("span", { text: "只存剪貼簿" }));
    clipBtn.onclick = () => { sel = null; const out = exportCanvas(); native({ cmd: "cliponly", png: pngB64(out) }); this.toast("已複製到剪貼簿"); };
    bar.appendChild(clipBtn);
    bar.appendChild(saveBtn);

    let editing = false;
    const startTextEdit = (o, clientX, clientY) => {
      const inp = el("textarea", null, "position:fixed;z-index:60;background:#111a;color:" + o.color + ";border:1px solid " + brandColor + ";border-radius:4px;padding:2px 4px;font:600 " + Math.round(o.size * scale) + "px sans-serif;resize:none;min-width:80px");
      inp.value = o.text || ""; inp.style.left = clientX + "px"; inp.style.top = clientY + "px";
      document.body.appendChild(inp); setTimeout(() => inp.focus(), 10); editing = true;
      const commit = () => { o.text = inp.value; if (!o.text.trim()) objs = objs.filter(x => x !== o); try { inp.remove(); } catch (e) {} editing = false; redraw(); };
      inp.addEventListener("blur", commit);
      inp.addEventListener("keydown", (e) => { if (e.isComposing) return; if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); inp.blur(); } if (e.key === "Escape") { inp.value = o.text || ""; inp.blur(); } e.stopPropagation(); });
    };

    const pt = (e) => { const rc = canvas.getBoundingClientRect(); return { x: (e.clientX - rc.left) / scale, y: (e.clientY - rc.top) / scale }; };
    let cur = null, last = null;
    canvas.addEventListener("pointerdown", (e) => {
      const p = pt(e);
      if (tool === "select") { sel = hit(p.x, p.y); last = p; redraw(); return; }
      if (tool === "eyedrop") { const d = ctx.getImageData(Math.round(p.x), Math.round(p.y), 1, 1).data; const hex = "#" + [d[0], d[1], d[2]].map(v => v.toString(16).padStart(2, "0")).join(""); setColor(hex); setTool("pen"); return; }
      if (tool === "text") { e.preventDefault(); const o = { type: "text", color, x: p.x, y: p.y, text: "", size: 22 }; objs.push(o); sel = o; startTextEdit(o, e.clientX, e.clientY); return; }
      if (tool === "crop") { cur = { type: "crop", x: p.x, y: p.y, w: 0, h: 0 }; cropRect = cur; sel = null; redraw(); return; }
      if (tool === "pen") cur = { type: "pen", color, w: 3, pts: [p] };
      else if (tool === "rect") cur = { type: "rect", color, w: 3, x: p.x, y: p.y, w2: 0, h2: 0 };
      else if (tool === "arrow") cur = { type: "arrow", color, w: 3, x1: p.x, y1: p.y, x2: p.x, y2: p.y };
      else if (tool === "mosaic") cur = { type: "mosaic", x: p.x, y: p.y, w: 0, h: 0 };
      if (cur) { objs.push(cur); }
    });
    canvas.addEventListener("pointermove", (e) => {
      const p = pt(e);
      if (tool === "select" && sel && last && (e.buttons & 1)) { moveObj(sel, p.x - last.x, p.y - last.y); last = p; redraw(); return; }
      if (!cur) return;
      if (cur.type === "crop") { cur.w = p.x - cur.x; cur.h = p.y - cur.y; redraw(); return; }
      if (cur.type === "pen") cur.pts.push(p);
      else if (cur.type === "rect") { cur.w2 = p.x - cur.x; cur.h2 = p.y - cur.y; }
      else if (cur.type === "arrow") { cur.x2 = p.x; cur.y2 = p.y; }
      else if (cur.type === "mosaic") { cur.w = p.x - cur.x; cur.h = p.y - cur.y; }
      redraw();
    });
    canvas.addEventListener("pointerup", () => { cur = null; last = null; });
    canvas.addEventListener("dblclick", (e) => { const p = pt(e); const o = hit(p.x, p.y); if (o && o.type === "text") { sel = o; startTextEdit(o, e.clientX, e.clientY); } else if (o) { setTool("select"); sel = o; redraw(); } });

    // 鍵盤(Ez2 截圖補):⌘Z 復原、Delete 刪除選取、Enter 存回、Esc 取消;打字中或存檔視窗開著時不攔
    document.addEventListener("keydown", (e) => {
      if (editing || view._dialogOpen || e.isComposing) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") { e.preventDefault(); undo(); }
      else if (e.key === "Backspace" || e.key === "Delete") { if (sel) { e.preventDefault(); delSel(); } }
      else if (e.key === "Enter") { e.preventDefault(); save(); }
      else if (e.key === "Escape") { e.preventDefault(); this._closeAnnotateOverlay(); }
    });

    setTool(startCrop ? "crop" : "pen");
    redraw();
    wrap.appendChild(canvas);
    overlay.appendChild(bar); overlay.appendChild(wrap);
    this.box.appendChild(overlay);
    native({ cmd: "ready", info: { img: [img.width, img.height], view: [window.innerWidth, window.innerHeight], tools: bar.querySelectorAll("button").length, icons: bar.querySelectorAll("svg").length, scale: Math.round(scale * 100) / 100 } });
  };

  // 外框存好之後回呼
  window.ez2Saved = (ok, msg) => { view.toast(ok ? ("已存回並複製到剪貼簿:" + msg) : ("存回失敗:" + msg), ok ? 0 : 4000); if (ok) setTimeout(() => native({ cmd: "done" }), 700); };

  // ===== 啟動 =====
  if (!INIT.dataUrl) { view.toast("沒有收到截圖", 0); return; }
  const img = new Image();
  img.onload = () => view.buildAnnotate({ rel: INIT.name || "截圖" }, img, false);
  img.onerror = () => view.toast("圖檔載入失敗", 0);
  img.src = INIT.dataUrl;
})();
