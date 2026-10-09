// Cursor trail for `drive.py capture`: osascript -l JavaScript trail.js <control file>
// Control file commands (polled 4x/s, deleted after reading): clear | dump <png path> | stop
ObjC.import('Cocoa');

var HUE_PERIOD = 12, BEAD_S = 0.1, POLL_S = 1 / 120, PAINT_S = 1 / 30, CTL_S = 0.25, MAX_S = 4 * 3600;

function makeOverlay(fr) {
  var win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(fr, 0, 2, false);
  win.opaque = false;
  win.backgroundColor = $.NSColor.clearColor;
  win.ignoresMouseEvents = true;
  win.hasShadow = false;
  win.level = 1000;
  win.collectionBehavior = 1 | 16 | 64 | 256;
  var view = $.NSImageView.alloc.initWithFrame($.NSMakeRect(0, 0, fr.size.width, fr.size.height));
  view.imageScaling = 0;
  win.contentView = view;
  var img = $.NSImage.alloc.initWithSize($.NSMakeSize(fr.size.width, fr.size.height));
  view.image = img;
  win.orderFrontRegardless;
  return { win: win, view: view, img: img, x: fr.origin.x, y: fr.origin.y, w: fr.size.width, h: fr.size.height };
}

function hit(o, p) { return p.x >= o.x - 20 && p.x <= o.x + o.w + 20 && p.y >= o.y - 20 && p.y <= o.y + o.h + 20; }

function onEach(overlays, pts, fn) {
  overlays.forEach(function (o) {
    if (!pts.some(function (p) { return hit(o, p); })) return;
    o.img.lockFocus;
    fn(o);
    o.img.unlockFocus;
    o.dirty = true;
  });
}

function hueColor(t, a) { return $.NSColor.colorWithCalibratedHueSaturationBrightnessAlpha((t / HUE_PERIOD) % 1, 0.9, 1.0, a); }

function dump(overlays, path) {
  var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  overlays.forEach(function (o) { x0 = Math.min(x0, o.x); y0 = Math.min(y0, o.y); x1 = Math.max(x1, o.x + o.w); y1 = Math.max(y1, o.y + o.h); });
  var all = $.NSImage.alloc.initWithSize($.NSMakeSize(x1 - x0, y1 - y0));
  all.lockFocus;
  overlays.forEach(function (o) {
    o.img.drawInRectFromRectOperationFraction($.NSMakeRect(o.x - x0, o.y - y0, o.w, o.h), $.NSZeroRect, 2, 1.0);
  });
  all.unlockFocus;
  var rep = $.NSBitmapImageRep.imageRepWithData(all.TIFFRepresentation);
  rep.representationUsingTypeProperties(4, $()).writeToFileAtomically(path, true);
}

function run(argv) {
  var ctl = argv[0];
  var fm = $.NSFileManager.defaultManager;
  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy(1);
  var screens = $.NSScreen.screens, overlays = [];
  for (var i = 0; i < screens.count; i++) overlays.push(makeOverlay(screens.objectAtIndex(i).frame));
  var t0 = $.NSDate.date, last = $.NSEvent.mouseLocation, lastBead = 0, lastPaint = 0, lastCtl = 0, wasDown = false;
  while (true) {
    var now = -t0.timeIntervalSinceNow;
    if (now > MAX_S) break;
    var cur = $.NSEvent.mouseLocation;
    var dx = cur.x - last.x, dy = cur.y - last.y;
    if (Math.sqrt(dx * dx + dy * dy) >= 0.3) {
      var a = { x: last.x, y: last.y }, b = { x: cur.x, y: cur.y }, bead = now - lastBead >= BEAD_S;
      onEach(overlays, [a, b], function (o) {
        var path = $.NSBezierPath.bezierPath;
        path.moveToPoint($.NSMakePoint(a.x - o.x, a.y - o.y));
        path.lineToPoint($.NSMakePoint(b.x - o.x, b.y - o.y));
        path.lineWidth = 3;
        path.lineCapStyle = 1;  // round
        hueColor(now, 0.9).setStroke;
        path.stroke;
        if (bead) {
          $.NSColor.colorWithCalibratedWhiteAlpha(0, 0.45).setFill;
          $.NSBezierPath.bezierPathWithOvalInRect($.NSMakeRect(b.x - o.x - 2, b.y - o.y - 2, 4, 4)).fill;
        }
      });
      if (bead) lastBead = now;
      last = cur;
    }
    var down = ($.NSEvent.pressedMouseButtons & 1) !== 0;
    if (down && !wasDown) {
      var c = { x: cur.x, y: cur.y };
      onEach(overlays, [c], function (o) {
        var ring = $.NSBezierPath.bezierPathWithOvalInRect($.NSMakeRect(c.x - o.x - 9, c.y - o.y - 9, 18, 18));
        ring.lineWidth = 2;
        hueColor(now, 0.9).setStroke;
        ring.stroke;
        var inner = $.NSBezierPath.bezierPathWithOvalInRect($.NSMakeRect(c.x - o.x - 6, c.y - o.y - 6, 12, 12));
        inner.lineWidth = 1.2;
        $.NSColor.whiteColor.setStroke;
        inner.stroke;
      });
    }
    wasDown = down;
    if (now - lastPaint >= PAINT_S) {
      overlays.forEach(function (o) {
        if (!o.dirty) return;
        o.view.image = $();
        o.view.image = o.img;
        o.dirty = false;
      });
      lastPaint = now;
    }
    if (now - lastCtl >= CTL_S) {
      lastCtl = now;
      if (fm.fileExistsAtPath(ctl)) {
        var cmd = ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(ctl, $.NSUTF8StringEncoding, null)) || '';
        fm.removeItemAtPathError(ctl, null);
        cmd = cmd.trim();
        if (cmd === 'stop') break;
        if (cmd === 'clear') {
          overlays.forEach(function (o) {
            o.img = $.NSImage.alloc.initWithSize($.NSMakeSize(o.w, o.h));
            o.view.image = o.img;
          });
        } else if (cmd.indexOf('dump ') === 0) {
          dump(overlays, cmd.slice(5).trim());
        }
      }
    }
    $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(POLL_S));
  }
  overlays.forEach(function (o) { o.win.orderOut(null); o.win.close; });
}
