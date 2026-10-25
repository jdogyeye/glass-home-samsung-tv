package app.glasshome.tv;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Outline;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewOutlineProvider;
import android.view.animation.DecelerateInterpolator;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * One rounded 16:9 tile: the app's TV banner edge to edge, or its icon and
 * name on frosted glass. Focus lifts and enlarges it with a soft white rim,
 * like the Tizen version (app/css/tv.css: .tile.focused).
 */
final class Tile extends FrameLayout {
    static final float FOCUS_SCALE = 1.14f;

    final Apps.App app;   // null for the built-in "Apps" tile
    final String label;
    private final GradientDrawable rim;
    private final View badge;
    private final float lift;

    Tile(Context ctx, Apps.App app, String label, Drawable glyph, int w, int h, float u) {
        super(ctx);
        this.app = app;
        this.label = label;
        final float radius = 24 * u;
        lift = 10 * u;
        setLayoutParams(new LinearLayout.LayoutParams(w, h));
        setFocusable(true);
        // Also when the TV is in touch mode (started over adb, or a mouse was
        // used), so there is always a highlighted tile for the remote.
        setFocusableInTouchMode(true);
        setClickable(true);
        setLongClickable(true);
        setClipToOutline(true);
        setOutlineProvider(new ViewOutlineProvider() {
            @Override public void getOutline(View v, Outline o) {
                o.setRoundRect(0, 0, v.getWidth(), v.getHeight(), radius);
            }
        });

        GradientDrawable face = new GradientDrawable();
        face.setCornerRadius(radius);
        face.setColor(0x1affffff);          // rgba(255,255,255,0.10)
        face.setStroke(Math.max(1, Math.round(u)), 0x0fffffff);
        setBackground(face);

        Drawable banner = app == null ? null : app.banner;
        if (banner != null) {
            ImageView img = new ImageView(ctx);
            img.setScaleType(ImageView.ScaleType.CENTER_CROP);
            img.setImageDrawable(banner);
            addView(img, new LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT));
        } else {
            LinearLayout col = new LinearLayout(ctx);
            col.setOrientation(LinearLayout.VERTICAL);
            col.setGravity(Gravity.CENTER);
            Drawable icon = glyph != null ? glyph : (app == null ? null : app.icon);
            if (icon != null) {
                ImageView img = new ImageView(ctx);
                img.setScaleType(ImageView.ScaleType.FIT_CENTER);
                img.setImageDrawable(icon);
                int s = Math.round(h * 0.46f);
                col.addView(img, new LinearLayout.LayoutParams(s, s));
            }
            TextView name = new TextView(ctx);
            name.setText(label);
            name.setTextColor(0xffffffff);
            name.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
            name.setTextSize(TypedValue.COMPLEX_UNIT_PX, (icon != null ? 20 : 28) * u);
            name.setGravity(Gravity.CENTER);
            name.setMaxLines(icon != null ? 1 : 2);
            name.setEllipsize(TextUtils.TruncateAt.END);
            int pad = Math.round(12 * u);
            name.setPadding(pad, icon != null ? Math.round(8 * u) : 0, pad, 0);
            col.addView(name, new LinearLayout.LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT));
            addView(col, new LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT));
        }

        // A small dot for "in the dock", shown in the all-apps grid.
        GradientDrawable dot = new GradientDrawable();
        dot.setShape(GradientDrawable.OVAL);
        dot.setColor(0xffffffff);
        dot.setStroke(Math.max(1, Math.round(2 * u)), 0x66000000);
        badge = new View(ctx);
        badge.setBackground(dot);
        int ds = Math.round(16 * u);
        LayoutParams blp = new LayoutParams(ds, ds, Gravity.TOP | Gravity.END);
        blp.setMargins(0, Math.round(10 * u), Math.round(10 * u), 0);
        badge.setVisibility(GONE);
        addView(badge, blp);

        rim = new GradientDrawable();
        rim.setCornerRadius(radius);
        rim.setColor(0x00000000);
        rim.setStroke(Math.max(2, Math.round(3 * u)), 0xb3ffffff);
        rim.setAlpha(0);
        setForeground(rim);
    }

    void setBadge(boolean on) {
        badge.setVisibility(on ? VISIBLE : GONE);
    }

    @Override
    protected void onFocusChanged(boolean focused, int direction, Rect previous) {
        super.onFocusChanged(focused, direction, previous);
        animate().scaleX(focused ? FOCUS_SCALE : 1f).scaleY(focused ? FOCUS_SCALE : 1f)
            .translationY(focused ? -lift : 0).translationZ(focused ? 18 * lift / 10 : 0)
            .setDuration(200).setInterpolator(new DecelerateInterpolator(2f)).start();
        rim.setAlpha(focused ? 255 : 0);
    }

    /** The 3 x 3 dots of the "Apps" tile, drawn rather than shipped as an image. */
    static final class GridGlyph extends Drawable {
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);

        GridGlyph() {
            p.setColor(0xffffffff);
        }

        @Override public void draw(Canvas c) {
            Rect b = getBounds();
            float s = Math.min(b.width(), b.height());
            float cell = s / 3f, r = cell * 0.22f;
            float ox = b.centerX() - s / 2f, oy = b.centerY() - s / 2f;
            for (int y = 0; y < 3; y++)
                for (int x = 0; x < 3; x++)
                    c.drawCircle(ox + cell * (x + 0.5f), oy + cell * (y + 0.5f), r, p);
        }

        @Override public void setAlpha(int alpha) { p.setAlpha(alpha); }
        @Override public void setColorFilter(ColorFilter cf) { p.setColorFilter(cf); }
        @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
    }
}
