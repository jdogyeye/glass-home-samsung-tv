package app.glasshome.tv;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Calendar;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;

/**
 * The photos: the bundled CC0 nature photos (assets/wallpapers) plus any you
 * add to the app's own folder on the TV, which needs no permission:
 * {@code adb push my.jpg /sdcard/Android/data/app.glasshome.tv/files/wallpapers/}.
 * Shuffled once a day.
 */
final class Photos {
    static final class Photo {
        final String asset;   // under assets/, or null
        final File file;      // a photo you added, or null
        final String credit;  // "Title · Author · CC0", or null

        Photo(String asset, File file, String credit) {
            this.asset = asset;
            this.file = file;
            this.credit = credit;
        }
    }

    /** A decoded photo plus a small blurred copy for the glass dock. */
    static final class Decoded {
        final Photo photo;
        final Bitmap full;
        final Bitmap blur;

        Decoded(Photo photo, Bitmap full, Bitmap blur) {
            this.photo = photo;
            this.full = full;
            this.blur = blur;
        }
    }

    private static final String DIR = "wallpapers";
    /** The blurred copy is this many times smaller than the screen. */
    static final int BLUR_DOWNSCALE = 12;

    private Photos() {}

    static List<Photo> list(Context ctx) {
        List<Photo> out = new ArrayList<Photo>();
        Map<String, String> credits = credits(ctx);
        try {
            String[] names = ctx.getAssets().list(DIR);
            if (names != null) {
                Arrays.sort(names);
                for (String n : names) if (isImage(n)) out.add(new Photo(DIR + "/" + n, null, credits.get(n)));
            }
        } catch (IOException ignored) { /* no bundled photos */ }
        File own = null;
        try {
            own = ctx.getExternalFilesDir(DIR);
        } catch (RuntimeException ignored) { /* no shared storage */ }
        File[] files = own == null ? null : own.listFiles();
        if (files != null) {
            Arrays.sort(files);
            for (File f : files) if (f.isFile() && isImage(f.getName())) out.add(new Photo(null, f, null));
        }
        // Same order all day, a new one tomorrow.
        Calendar c = Calendar.getInstance();
        long day = c.get(Calendar.YEAR) * 1000L + c.get(Calendar.DAY_OF_YEAR);
        Collections.shuffle(out, new Random(day));
        return out;
    }

    private static boolean isImage(String name) {
        String n = name.toLowerCase(Locale.ROOT);
        return n.endsWith(".jpg") || n.endsWith(".jpeg") || n.endsWith(".png") || n.endsWith(".webp");
    }

    private static Map<String, String> credits(Context ctx) {
        Map<String, String> out = new HashMap<String, String>();
        try {
            JSONArray list = new JSONArray(readAll(ctx.getAssets().open(DIR + "/manifest.json")));
            for (int i = 0; i < list.length(); i++) {
                JSONObject o = list.getJSONObject(i);
                String licence = o.optString("licence", "");
                out.put(o.getString("file"), o.optString("title") + " · " + o.optString("author")
                    + (licence.startsWith("CC0") ? " · CC0" : ""));
            }
        } catch (Exception ignored) { /* credits are optional */ }
        return out;
    }

    private static String readAll(InputStream in) throws IOException {
        try {
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] b = new byte[8192];
            int n;
            while ((n = in.read(b)) > 0) buf.write(b, 0, n);
            return buf.toString("UTF-8");
        } finally {
            in.close();
        }
    }

    /**
     * Decodes a photo no larger than needed to cover a {@code w} x {@code h}
     * screen, and its blurred copy. Returns null if it can't be read.
     */
    static Decoded decode(Context ctx, Photo p, int w, int h) {
        try {
            BitmapFactory.Options o = new BitmapFactory.Options();
            o.inJustDecodeBounds = true;
            decodeStream(ctx, p, o);
            if (o.outWidth <= 0 || o.outHeight <= 0) return null;
            int sample = 1;
            while (o.outWidth / (sample * 2) >= w && o.outHeight / (sample * 2) >= h) sample *= 2;
            o = new BitmapFactory.Options();
            o.inSampleSize = sample;
            o.inPreferredConfig = Bitmap.Config.ARGB_8888;
            Bitmap full = decodeStream(ctx, p, o);
            if (full == null) return null;
            return new Decoded(p, full, blur(full, w, h));
        } catch (IOException e) {
            return null;
        } catch (OutOfMemoryError e) {
            return null;
        }
    }

    private static Bitmap decodeStream(Context ctx, Photo p, BitmapFactory.Options o) throws IOException {
        InputStream in = p.asset != null ? ctx.getAssets().open(p.asset) : new FileInputStream(p.file);
        try {
            return BitmapFactory.decodeStream(in, null, o);
        } finally {
            in.close();
        }
    }

    /**
     * A tiny copy, box-blurred three times (close to a Gaussian). Drawn scaled
     * up behind the dock it looks like frosted glass, for the cost of one small
     * texture: no RenderScript, works on every Android version.
     */
    static Bitmap blur(Bitmap full, int screenW, int screenH) {
        float cover = Math.max(screenW / (float) full.getWidth(), screenH / (float) full.getHeight());
        int bw = Math.max(8, Math.round(full.getWidth() * cover / BLUR_DOWNSCALE));
        int bh = Math.max(8, Math.round(full.getHeight() * cover / BLUR_DOWNSCALE));
        Bitmap small = Bitmap.createScaledBitmap(full, bw, bh, true);
        if (!small.isMutable() || small == full) small = small.copy(Bitmap.Config.ARGB_8888, true);
        int[] px = new int[bw * bh];
        small.getPixels(px, 0, bw, 0, 0, bw, bh);
        int[] tmp = new int[px.length];
        for (int pass = 0; pass < 3; pass++) {
            boxBlur(px, tmp, bw, bh, 2, true);
            boxBlur(tmp, px, bw, bh, 2, false);
        }
        small.setPixels(px, 0, bw, 0, 0, bw, bh);
        return small;
    }

    /** One pass of a box blur along rows (horizontal) or columns, clamping at the edges. */
    static void boxBlur(int[] src, int[] dst, int w, int h, int r, boolean horizontal) {
        int len = horizontal ? w : h;
        int lines = horizontal ? h : w;
        int div = 2 * r + 1;
        for (int line = 0; line < lines; line++) {
            int ra = 0, rr = 0, rg = 0, rb = 0;
            for (int k = -r; k <= r; k++) {
                int c = src[index(line, clamp(k, len), w, horizontal)];
                ra += c >>> 24; rr += (c >> 16) & 0xff; rg += (c >> 8) & 0xff; rb += c & 0xff;
            }
            for (int i = 0; i < len; i++) {
                dst[index(line, i, w, horizontal)] = ((ra / div) << 24) | ((rr / div) << 16) | ((rg / div) << 8) | (rb / div);
                int out = src[index(line, clamp(i - r, len), w, horizontal)];
                int in = src[index(line, clamp(i + r + 1, len), w, horizontal)];
                ra += (in >>> 24) - (out >>> 24);
                rr += ((in >> 16) & 0xff) - ((out >> 16) & 0xff);
                rg += ((in >> 8) & 0xff) - ((out >> 8) & 0xff);
                rb += (in & 0xff) - (out & 0xff);
            }
        }
    }

    private static int clamp(int i, int len) {
        return i < 0 ? 0 : (i >= len ? len - 1 : i);
    }

    private static int index(int line, int i, int w, boolean horizontal) {
        return horizontal ? line * w + i : i * w + line;
    }
}
