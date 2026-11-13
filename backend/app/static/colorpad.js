// SPDX-License-Identifier: AGPL-3.0-or-later
/* Shared Hue/Saturation/Brightness color pad: a 2D saturation-brightness
   pad plus a hue strip, swatch, and readout. Device-agnostic (hue 0-360,
   sat/val 0-100, configurable hue step); pages sync it both ways with
   their own inputs. Pointer Events cover mouse + touch + pen; both
   canvases take keyboard arrows. No dependencies.
   Usage: ColorPad.mount(rootEl, {hue, sat, val, hueStep, onInput(h, s, v)}) */
var ColorPad = (function () {
  "use strict";

  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }

  function hsvToRgb(h, s, v) {
    h = ((h % 360) + 360) % 360;
    s = clamp(s, 0, 100) / 100;
    v = clamp(v, 0, 100) / 100;
    var c = v * s;
    var x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    var m = v - c;
    var r = 0, g = 0, b = 0;
    if (h < 60) { r = c; g = x; }
    else if (h < 120) { r = x; g = c; }
    else if (h < 180) { g = c; b = x; }
    else if (h < 240) { g = x; b = c; }
    else if (h < 300) { r = x; b = c; }
    else { r = c; b = x; }
    return [Math.round((r + m) * 255), Math.round((g + m) * 255), Math.round((b + m) * 255)];
  }

  function css(rgb) {
    return "rgb(" + rgb[0] + "," + rgb[1] + "," + rgb[2] + ")";
  }

  // Fixed generous backing stores: smooth at any CSS size (the pad fills
  // its card), always crisp, no resize bookkeeping per mount.
  var PAD_W = 600, PAD_H = 400, STRIP_W = 600, STRIP_H = 44;

  function mount(root, opts) {
    if (!root) return null;
    opts = opts || {};
    var hueStep = opts.hueStep || 1;
    var H = clamp(Math.round(opts.hue || 0), 0, 360);
    var S = clamp(Math.round(opts.sat || 0), 0, 100);
    var V = clamp(Math.round(opts.val === undefined ? 100 : opts.val), 0, 100);
    var onInput = opts.onInput || function () {};

    root.innerHTML =
      '<div style="width:100%">' +
      '<canvas width="' + PAD_W + '" height="' + PAD_H + '" tabindex="0" role="slider" ' +
      'aria-label="Saturation left to right, brightness bottom to top. Arrow keys adjust." ' +
      'style="width:100%;display:block;touch-action:none;cursor:crosshair;border-radius:6px"></canvas>' +
      '<canvas width="' + STRIP_W + '" height="' + STRIP_H + '" tabindex="0" role="slider" ' +
      'aria-label="Hue. Left and right arrows adjust." ' +
      'style="width:100%;display:block;margin-top:6px;touch-action:none;cursor:ew-resize;border-radius:6px"></canvas>' +
      '<div style="display:flex;align-items:center;gap:8px;margin-top:6px">' +
      '<span style="display:inline-block;width:44px;height:24px;border:1px solid #888;border-radius:4px"></span>' +
      '<span class="hint"></span></div></div>';
    var pad = root.children[0].children[0];
    var strip = root.children[0].children[1];
    var swatch = root.children[0].children[2].children[0];
    var readout = root.children[0].children[2].children[1];
    var pctx = pad.getContext("2d");
    var sctx = strip.getContext("2d");

    function drawPad() {
      var grad = pctx.createLinearGradient(0, 0, PAD_W, 0);
      grad.addColorStop(0, "#fff");
      grad.addColorStop(1, css(hsvToRgb(H, 100, 100)));
      pctx.fillStyle = grad;
      pctx.fillRect(0, 0, PAD_W, PAD_H);
      var shade = pctx.createLinearGradient(0, 0, 0, PAD_H);
      shade.addColorStop(0, "rgba(0,0,0,0)");
      shade.addColorStop(1, "#000");
      pctx.fillStyle = shade;
      pctx.fillRect(0, 0, PAD_W, PAD_H);
      var x = (S / 100) * PAD_W, y = (1 - V / 100) * PAD_H;
      pctx.beginPath();
      pctx.arc(x, y, 13, 0, 2 * Math.PI);
      pctx.lineWidth = 3;
      pctx.strokeStyle = "#fff";
      pctx.stroke();
      pctx.beginPath();
      pctx.arc(x, y, 13, 0, 2 * Math.PI);
      pctx.lineWidth = 1;
      pctx.strokeStyle = "#000";
      pctx.stroke();
    }

    function drawStrip() {
      for (var h = 0; h < 360; h += 6) {
        sctx.fillStyle = css(hsvToRgb(h, 100, 100));
        sctx.fillRect((h / 360) * STRIP_W, 0, (6 / 360) * STRIP_W + 1, STRIP_H);
      }
      var x = (clamp(H, 0, 360) / 360) * STRIP_W;
      sctx.fillStyle = "#fff";
      sctx.fillRect(x - 2, 0, 4, STRIP_H);
      sctx.fillStyle = "#000";
      sctx.fillRect(x - 3, 0, 1, STRIP_H);
      sctx.fillRect(x + 2, 0, 1, STRIP_H);
    }

    function refresh() {
      drawPad();
      drawStrip();
      swatch.style.background = css(hsvToRgb(H, S, V));
      readout.textContent = H + "° · " + S + "% sat · " + V + "%";
      pad.setAttribute("aria-valuenow", S + "," + V);
      strip.setAttribute("aria-valuenow", H);
    }

    function emit() {
      refresh();
      onInput(H, S, V);
    }

    function quantHue(h) {
      return clamp(Math.round(h / hueStep) * hueStep, 0, 360);
    }

    function padPoint(ev) {
      var r = pad.getBoundingClientRect();
      S = clamp(Math.round(((ev.clientX - r.left) / r.width) * 100), 0, 100);
      V = clamp(Math.round((1 - (ev.clientY - r.top) / r.height) * 100), 0, 100);
      emit();
    }

    function stripPoint(ev) {
      var r = strip.getBoundingClientRect();
      H = quantHue(((ev.clientX - r.left) / r.width) * 360);
      emit();
    }

    function drag(el, fn) {
      el.addEventListener("pointerdown", function (ev) {
        ev.preventDefault();
        try { el.setPointerCapture(ev.pointerId); } catch (e) { /* older browsers */ }
        fn(ev);
        var move = function (ev2) { fn(ev2); };
        var up = function () {
          el.removeEventListener("pointermove", move);
          el.removeEventListener("pointerup", up);
          el.removeEventListener("pointercancel", up);
        };
        el.addEventListener("pointermove", move);
        el.addEventListener("pointerup", up);
        el.addEventListener("pointercancel", up);
      });
    }
    drag(pad, padPoint);
    drag(strip, stripPoint);

    pad.addEventListener("keydown", function (ev) {
      var step = ev.shiftKey ? 10 : 1, used = true;
      if (ev.key === "ArrowLeft") S = clamp(S - step, 0, 100);
      else if (ev.key === "ArrowRight") S = clamp(S + step, 0, 100);
      else if (ev.key === "ArrowUp") V = clamp(V + step, 0, 100);
      else if (ev.key === "ArrowDown") V = clamp(V - step, 0, 100);
      else used = false;
      if (used) { ev.preventDefault(); emit(); }
    });
    strip.addEventListener("keydown", function (ev) {
      var step = (ev.shiftKey ? 10 : 1) * hueStep, used = true;
      if (ev.key === "ArrowLeft") H = quantHue(H - step);
      else if (ev.key === "ArrowRight") H = quantHue(H + step);
      else used = false;
      if (used) { ev.preventDefault(); emit(); }
    });

    refresh();
    return {
      el: root,
      set: function (h, s, v) {
        H = quantHue(h);
        S = clamp(Math.round(s), 0, 100);
        V = clamp(Math.round(v), 0, 100);
        refresh();
      }
    };
  }

  return {mount: mount, hsvToRgb: hsvToRgb};
})();
