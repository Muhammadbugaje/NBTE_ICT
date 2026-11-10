/* v2.2 — a light, professional confetti burst (brand colours, ~2.5 s,
 * no libraries). Respects prefers-reduced-motion. Usage:
 *   NBTECelebrate.burst()  or  <body data-celebrate> */
(function () {
  "use strict";
  var COLORS = ["#107a40", "#16a34a", "#22c55e", "#eab308", "#f5d061", "#ffffff"];
  function burst(opts) {
    opts = opts || {};
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    var canvas = document.createElement("canvas");
    canvas.setAttribute("aria-hidden", "true");
    canvas.style.cssText = "position:fixed;inset:0;width:100vw;height:100vh;pointer-events:none;z-index:10000";
    document.body.appendChild(canvas);
    var ctx = canvas.getContext("2d");
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    function size() { canvas.width = innerWidth * dpr; canvas.height = innerHeight * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0); }
    size();
    var count = opts.count || Math.min(110, Math.round(innerWidth / 9));
    var pieces = [];
    for (var i = 0; i < count; i++) {
      var fromLeft = i % 2 === 0;
      pieces.push({
        x: fromLeft ? -10 : innerWidth + 10, y: innerHeight * (0.55 + Math.random() * 0.25),
        vx: (fromLeft ? 1 : -1) * (4 + Math.random() * 7), vy: -(9 + Math.random() * 8),
        w: 5 + Math.random() * 5, h: 8 + Math.random() * 6, r: Math.random() * Math.PI,
        vr: (Math.random() - 0.5) * 0.3, c: COLORS[i % COLORS.length], o: 1
      });
    }
    var start = performance.now(), dur = opts.duration || 2600;
    function frame(t) {
      var k = (t - start) / dur;
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      pieces.forEach(function (p) {
        p.vy += 0.32; p.vx *= 0.985; p.x += p.vx; p.y += p.vy; p.r += p.vr;
        p.o = Math.max(0, 1 - Math.max(0, k - 0.6) / 0.4);
        ctx.save(); ctx.globalAlpha = p.o; ctx.translate(p.x, p.y); ctx.rotate(p.r);
        ctx.fillStyle = p.c; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h * Math.abs(Math.cos(p.r)));
        ctx.restore();
      });
      if (k < 1) requestAnimationFrame(frame); else canvas.remove();
    }
    requestAnimationFrame(frame);
  }
  window.NBTECelebrate = { burst: burst };
  function auto() { if (document.querySelector("[data-celebrate]")) setTimeout(burst, 350); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", auto); else auto();
})();
