package app.glasshome.tv;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.ColorMatrix;
import android.graphics.ColorMatrixColorFilter;
import android.graphics.LinearGradient;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RadialGradient;
import android.graphics.RectF;
import android.graphics.Shader;
import android.os.SystemClock;
import android.view.View;

/**
 * Everything behind the clock and dock, drawn by one view: the photo with a
 * slow drift (Ken Burns), the crossfade to the next one, the dark gradients
 * that keep white text readable, and the frosted-glass dock panel (the
 * photo's blurred copy drawn with the same transform, so the glass shows
 * exactly what is behind it).
 *
 * Cost: two bitmap draws, a few gradients and one small bitmap inside a
 * clip. Redraws at ~20 fps while only drifting (a few pixels per second
 * doesn't need more) and at display rate during a crossfade.
 */
final class Backdrop extends View {
    private static final class Slide {
        final Photos.Decoded d;
        final long start;
        final float dirX, dirY;

        Slide(Photos.Decoded d, long start, float dirX, float dirY) {
            this.d = d;
            this.start = start;
            this.dirX = dirX;
            this.dirY = dirY;
        }
    }

    private Slide base;      // fully visible
    private Slide incoming;  // fading in over base, or null
    private long fadeStart;

    long fadeMs = 1600;
    long driftMs = 45000 + 1600;
    float kenBurns = 0.06f;
    private boolean running = true;
    private boolean dimmed;  // the all-apps panel is open
    private float glassAlpha = 1;

    private final Paint photoPaint = new Paint(Paint.FILTER_BITMAP_FLAG | Paint.ANTI_ALIAS_FLAG);
    private final Paint glassPhotoPaint = new Paint(Paint.FILTER_BITMAP_FLAG | Paint.ANTI_ALIAS_FLAG);
    private final Paint glassTint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint glassEdge = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint glassShine = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint shadeBottom = new Paint();
    private final Paint shadeTop = new Paint();
    private final Paint dim = new Paint();
    private final Matrix m = new Matrix();
    private final RectF glass = new RectF();
    private final Path glassPath = new Path();
    private float glassRadius;
    private float u = 1;   // px per 1920-wide design px
    private int frameCount;

    Backdrop(Context ctx) {
        super(ctx);
        ColorMatrix saturate = new ColorMatrix();
        saturate.setSaturation(1.6f);
        glassPhotoPaint.setColorFilter(new ColorMatrixColorFilter(saturate));
        glassTint.setColor(0x6b1c1c1e);     // rgba(28,28,30,0.42)
        glassEdge.setStyle(Paint.Style.STROKE);
        glassEdge.setColor(0x1affffff);     // 10% white hairline
        dim.setColor(0xb3000000);
        setWillNotDraw(false);
    }

    /** The dock panel, in this view's coordinates. Empty hides the glass. */
    void setGlass(float l, float t, float r, float b, float radius) {
        if (glass.left == l && glass.top == t && glass.right == r && glass.bottom == b && glassRadius == radius) return;
        glass.set(l, t, r, b);
        glassRadius = radius;
        glassPath.reset();
        if (!glass.isEmpty()) glassPath.addRoundRect(glass, radius, radius, Path.Direction.CW);
        glassShine.setShader(new LinearGradient(0, t, 0, t + (b - t) * 0.4f, 0x0fffffff, 0x00ffffff, Shader.TileMode.CLAMP));
        invalidate();
    }

    /** Fades the glass with the dock (idle screen, apps panel). */
    void setGlassAlpha(float a) {
        if (glassAlpha == a) return;
        glassAlpha = a;
        invalidate();
    }

    void setDimmed(boolean on) {
        dimmed = on;
        invalidate();
    }

    /** Shows {@code d}: instantly for the first photo, else as a crossfade. */
    void show(Photos.Decoded d) {
        long now = SystemClock.uptimeMillis();
        int n = frameCount++;
        // Drift towards a different corner each time.
        float dx = (n % 2 == 0) ? 1 : -1;
        float dy = (n % 4 < 2) ? 0.5f : -0.5f;
        Slide s = new Slide(d, now, dx, dy);
        if (base == null) {
            base = s;
        } else {
            if (incoming != null) base = incoming;   // a fade still running: finish it now
            incoming = s;
            fadeStart = now;
        }
        invalidate();
    }

    Photos.Photo current() {
        Slide s = incoming != null ? incoming : base;
        return s == null ? null : s.d.photo;
    }

    void setRunning(boolean on) {
        running = on;
        if (on) invalidate();
    }

    @Override
    protected void onSizeChanged(int w, int h, int oldw, int oldh) {
        u = w / 1920f;
        glassEdge.setStrokeWidth(Math.max(1, u));
        // The same gradients as the Tizen version (app/css/tv.css: #shade-bottom, #shade-top).
        float bh = 560 * u;
        shadeBottom.setShader(new LinearGradient(0, h, 0, h - bh,
            new int[] {0x99000000, 0x57000000, 0x1a000000, 0x00000000},
            new float[] {0f, 0.38f, 0.72f, 1f}, Shader.TileMode.CLAMP));
        // An ellipse 770 x 345 around the top-right corner, fully clear inside its 1100 x 460 box.
        RadialGradient corner = new RadialGradient(0, 0, 1,
            new int[] {0x85000000, 0x42000000, 0x00000000}, new float[] {0f, 0.45f, 1f}, Shader.TileMode.CLAMP);
        Matrix ellipse = new Matrix();
        ellipse.setScale(770 * u, 345 * u);
        ellipse.postTranslate(w, 0);
        corner.setLocalMatrix(ellipse);
        shadeTop.setShader(corner);
    }

    @Override
    protected void onDraw(Canvas c) {
        int w = getWidth(), h = getHeight();
        long now = SystemClock.uptimeMillis();
        c.drawColor(0xff04080b);

        float fade = 1;
        if (incoming != null) {
            fade = Math.min(1f, (now - fadeStart) / (float) fadeMs);
            if (fade >= 1) {
                base = incoming;
                incoming = null;
            }
        }
        if (base != null) drawPhoto(c, base, base.d.full, 255, now, w, h, photoPaint);
        if (incoming != null) drawPhoto(c, incoming, incoming.d.full, Math.round(255 * fade), now, w, h, photoPaint);

        c.drawRect(0, h - 560 * u, w, h, shadeBottom);
        c.drawRect(w - 1100 * u, 0, w, 460 * u, shadeTop);

        if (!glass.isEmpty() && base != null && glassAlpha > 0.01f) {
            int save = glassAlpha < 0.99f
                ? c.saveLayerAlpha(glass.left - 2, glass.top - 2, glass.right + 2, glass.bottom + 2,
                    Math.round(255 * glassAlpha), Canvas.ALL_SAVE_FLAG)
                : c.save();
            c.save();
            c.clipPath(glassPath);
            drawPhoto(c, base, base.d.blur, 255, now, w, h, glassPhotoPaint);
            if (incoming != null) drawPhoto(c, incoming, incoming.d.blur, Math.round(255 * fade), now, w, h, glassPhotoPaint);
            c.drawRect(glass, glassTint);
            c.drawRect(glass, glassShine);
            c.restore();
            c.drawRoundRect(glass, glassRadius, glassRadius, glassEdge);
            c.restoreToCount(save);
        }
        if (dimmed) c.drawRect(0, 0, w, h, dim);

        if (running && base != null) {
            if (incoming != null) postInvalidateOnAnimation();
            else if (now - base.start < driftMs) postInvalidateDelayed(50);
            // else: still until the next photo; show() redraws
        }
    }

    /** Draws {@code bmp} (the photo or its blurred copy) covering the screen, drifted for its age. */
    private void drawPhoto(Canvas c, Slide s, Bitmap bmp, int alpha, long now, int w, int h, Paint p) {
        Bitmap full = s.d.full;
        float t = Math.min(1f, Math.max(0f, (now - s.start) / (float) driftMs));
        // ease in-out, so the drift starts and ends gently
        t = t * t * (3 - 2 * t);
        float cover = Math.max(w / (float) full.getWidth(), h / (float) full.getHeight());
        float scale = cover * (1 + kenBurns * t);
        float sw = full.getWidth() * scale, sh = full.getHeight() * scale;
        float tx = (w - sw) / 2f, ty = (h - sh) / 2f;   // centred; both <= 0
        tx += s.dirX * t * 0.8f * -tx;
        ty += s.dirY * t * 0.8f * -ty;
        float k = full.getWidth() / (float) bmp.getWidth();   // 1, or the blur's downscale
        m.setScale(scale * k, scale * k);
        m.postTranslate(tx, ty);
        p.setAlpha(alpha);
        c.drawBitmap(bmp, m, p);
    }
}
