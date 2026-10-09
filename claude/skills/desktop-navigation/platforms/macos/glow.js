// Edge glow for `drive.py glow start|stop`: osascript -l JavaScript glow.js start|stop
// One borderless, click-through window per screen; each frame is drawn into an NSImage shown by an NSImageView.
ObjC.import('Cocoa');

var DURATION = 1.4, SEGMENTS = 96, BINS = 64;

function smoothstep(x) { x = Math.min(1, Math.max(0, x)); return x * x * (3 - 2 * x); }

function frame(kind, p, depth) {
  if (kind === 'start') {
    var a = p < 0.12 ? p / 0.12 : (p < 0.6 ? 1 : Math.max(0, (1 - p) / 0.4));
    return { thick: depth * smoothstep(p / 0.35), alpha: a, slide: 0.3 * p };
  }
  var q = Math.max(0, (p - 0.5) / 0.5);
  return { thick: depth * (1 - smoothstep(q)), alpha: Math.min(1, p / 0.12) * (1 - q * q), slide: 0.3 * p };
}

function gradients() {
  // per hue bin: edge half 0.55 -> 0.20, inner half 0.20 -> 0
  var out = [];
  for (var b = 0; b < BINS; b++) {
    var h = b / BINS;
    var c = function (a) { return $.NSColor.colorWithCalibratedHueSaturationBrightnessAlpha(h, 0.72, 1.0, a); };
    out.push([$.NSGradient.alloc.initWithStartingColorEndingColor(c(0.55), c(0.20)),
              $.NSGradient.alloc.initWithStartingColorEndingColor(c(0.20), c(0.0))]);
  }
  return out;
}

function makeWindow(rect) {
  var win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(rect, 0, 2, false);
  win.opaque = false;
  win.backgroundColor = $.NSColor.clearColor;
  win.ignoresMouseEvents = true;
  win.hasShadow = false;
  win.level = 1000;
  win.collectionBehavior = 1 | 16 | 64 | 256;
  var view = $.NSImageView.alloc.initWithFrame($.NSMakeRect(0, 0, rect.size.width, rect.size.height));
  view.imageScaling = 0;
  win.contentView = view;
  win.orderFrontRegardless;
  return { win: win, view: view };
}

function draw(W, H, depth, f, grads) {
  var img = $.NSImage.alloc.initWithSize($.NSMakeSize(W, H));
  img.lockFocus;
  var T = f.thick, P = 2 * W + 2 * H, slide = f.slide * P;
  if (T >= 1) {
    var edges = [
      // [length, s at start, s direction, rect(i, len), angle pointing inward from the screen edge]
      [W, 0, function (a, l) { return $.NSMakeRect(a, H - T, l, T); }, 270],
      [H - 2 * T, W + T, function (a, l) { return $.NSMakeRect(W - T, H - T - a - l, T, l); }, 180],
      [W, W + H, function (a, l) { return $.NSMakeRect(W - a - l, 0, l, T); }, 90],
      [H - 2 * T, 2 * W + H + T, function (a, l) { return $.NSMakeRect(0, T + a, T, l); }, 0]
    ];
    edges.forEach(function (e) {
      var len = e[0], n = Math.max(1, Math.round(len / P * SEGMENTS)), step = len / n;
      for (var i = 0; i < n; i++) {
        var s = e[1] + (i + 0.5) * step - slide;
        var b = Math.floor((((s / P) % 1) + 1) % 1 * BINS) % BINS;
        var r = e[2](i * step, step + 1);
        var half = T / 2, outer, inner;
        if (e[3] === 270) { outer = $.NSMakeRect(r.origin.x, H - half, r.size.width, half); inner = $.NSMakeRect(r.origin.x, H - T, r.size.width, half); }
        else if (e[3] === 90) { outer = $.NSMakeRect(r.origin.x, 0, r.size.width, half); inner = $.NSMakeRect(r.origin.x, half, r.size.width, half); }
        else if (e[3] === 180) { outer = $.NSMakeRect(W - half, r.origin.y, half, r.size.height); inner = $.NSMakeRect(W - T, r.origin.y, half, r.size.height); }
        else { outer = $.NSMakeRect(0, r.origin.y, half, r.size.height); inner = $.NSMakeRect(half, r.origin.y, half, r.size.height); }
        grads[b][0].drawInRectAngle(outer, e[3]);
        grads[b][1].drawInRectAngle(inner, e[3]);
      }
    });
  }
  img.unlockFocus;
  return img;
}

function run(argv) {
  var kind = argv[0] === 'stop' ? 'stop' : 'start';
  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy(1);  // accessory: no Dock icon, never activates
  var grads = gradients();
  var screens = $.NSScreen.screens, overlays = [];
  for (var i = 0; i < screens.count; i++) {
    var fr = screens.objectAtIndex(i).frame;
    var o = makeWindow(fr);
    o.W = fr.size.width; o.H = fr.size.height;
    overlays.push(o);
  }
  var t0 = $.NSDate.date;
  while (true) {
    var p = Math.min(1, -t0.timeIntervalSinceNow / DURATION);
    overlays.forEach(function (o) {
      var f = frame(kind, p, 72);
      o.view.image = draw(o.W, o.H, 72, f, grads);
      o.win.alphaValue = f.alpha;
      o.view.display;
    });
    if (p >= 1) break;
    $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(1 / 60));
  }
  overlays.forEach(function (o) { o.win.orderOut(null); o.win.close; });
}
