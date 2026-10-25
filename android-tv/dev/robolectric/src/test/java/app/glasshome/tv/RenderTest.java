package app.glasshome.tv;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.app.AlertDialog;
import android.content.ComponentName;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ActivityInfo;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.os.Looper;
import android.provider.Settings;
import android.view.KeyEvent;
import android.view.View;

import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;
import org.robolectric.annotation.LooperMode;
import org.robolectric.shadows.ShadowDialog;
import org.robolectric.shadows.ShadowPackageManager;

import java.io.File;
import java.io.FileOutputStream;
import java.time.Duration;
import java.util.Arrays;
import java.util.List;

/**
 * Starts the real HomeActivity on Robolectric's Android 14 framework at
 * 1920 x 1080 with a set of made-up TV apps, drives it with remote keys,
 * and saves what it draws as PNGs (android-tv/state/render/).
 *
 * Not a substitute for the TV: software rendering (no elevation shadows),
 * and made-up apps without banners, so tiles show their name.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34, qualifiers = "w960dp-h540dp-land-television-xhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@LooperMode(LooperMode.Mode.PAUSED)
public class RenderTest {
    private static final String[][] APPS = {
        {"com.google.android.youtube.tv", "YouTube"},
        {"com.netflix.ninja", "Netflix"},
        {"com.amazon.amazonvideo.livingroom", "Prime Video"},
        {"com.disney.disneyplus", "Disney+"},
        {"com.spotify.tv.android", "Spotify"},
        {"com.plexapp.android", "Plex"},
        {"org.xbmc.kodi", "Kodi"},
        {"com.example.weather", "Weather"},
        {"com.android.vending", "Play Store"},
        {"com.google.android.tvlauncher", "Android TV Home"},
    };

    private File shots;

    @Before
    public void setUp() {
        shots = new File(System.getProperty("glasshome.shots", "build/render"));
        shots.mkdirs();
        ShadowPackageManager spm = shadowOf(RuntimeEnvironment.getApplication().getPackageManager());
        for (String[] a : APPS) addApp(spm, a[0], a[1], Intent.CATEGORY_LEANBACK_LAUNCHER, "android.intent.action.MAIN");
        addApp(spm, "com.android.tv.settings", "Settings", Intent.CATEGORY_DEFAULT, Settings.ACTION_SETTINGS);
    }

    private static void addApp(ShadowPackageManager spm, String pkg, String label, String category, String action) {
        ApplicationInfo app = new ApplicationInfo();
        app.packageName = pkg;
        app.nonLocalizedLabel = label;
        app.flags = ApplicationInfo.FLAG_INSTALLED;
        app.enabled = true;
        PackageInfo pi = new PackageInfo();
        pi.packageName = pkg;
        pi.applicationInfo = app;
        spm.installPackage(pi);
        ActivityInfo ai = new ActivityInfo();
        ai.packageName = pkg;
        ai.name = pkg + ".Main";
        ai.applicationInfo = app;
        ai.nonLocalizedLabel = label;
        ai.enabled = true;
        ai.exported = true;
        spm.addOrUpdateActivity(ai);
        IntentFilter f = new IntentFilter(action);
        f.addCategory(category);
        spm.addIntentFilterForActivity(new ComponentName(pkg, ai.name), f);
    }

    @Test
    public void homeScreen() throws Exception {
        ActivityController<HomeActivity> c = Robolectric.buildActivity(HomeActivity.class).setup().windowFocusChanged(true);
        HomeActivity a = c.get();
        settle(a);

        // First run: the preferred streaming apps (5), then Apps and Settings.
        List<String> dock = a.dockLabels();
        assertEquals(Arrays.asList("YouTube", "Netflix", "Prime Video", "Disney+", "Spotify", "Apps", "Settings"), dock);
        View focused = a.focused();
        assertTrue("first dock tile has focus", focused instanceof Tile && "YouTube".equals(((Tile) focused).label));
        shoot(a, "01-home");

        key(a, KeyEvent.KEYCODE_DPAD_RIGHT);
        key(a, KeyEvent.KEYCODE_DPAD_RIGHT);
        assertEquals("Prime Video", ((Tile) a.focused()).label);
        shoot(a, "02-focus-moved");

        // Hold OK > Move left: Prime Video swaps with Netflix and is saved.
        a.focused().performLongClick();
        idle(300);
        AlertDialog menu = (AlertDialog) ShadowDialog.getLatestDialog();
        assertNotNull("long press opens the menu", menu);
        shadowOf(menu).clickOnItem(0);
        idle(300);
        assertEquals(Arrays.asList("YouTube", "Prime Video", "Netflix", "Disney+", "Spotify", "Apps", "Settings"),
            a.dockLabels());
        assertEquals("Prime Video", ((Tile) a.focused()).label);
        String saved = Apps.prefs(a).getString("dock", "");
        assertTrue(saved, saved.startsWith("com.google.android.youtube.tv,com.amazon.amazonvideo.livingroom,com.netflix.ninja"));

        // Remove Disney+ from the dock.
        key(a, KeyEvent.KEYCODE_DPAD_RIGHT);
        key(a, KeyEvent.KEYCODE_DPAD_RIGHT);
        assertEquals("Disney+", ((Tile) a.focused()).label);
        a.focused().performLongClick();
        idle(300);
        shadowOf((AlertDialog) ShadowDialog.getLatestDialog()).clickOnItem(2);
        idle(300);
        assertTrue(!a.dockLabels().contains("Disney+"));

        // Open Apps: every app, by name.
        while (!"Apps".equals(((Tile) a.focused()).label)) key(a, KeyEvent.KEYCODE_DPAD_RIGHT);
        key(a, KeyEvent.KEYCODE_DPAD_CENTER);
        settle(a);
        shoot(a, "03-apps");
        // Back closes it and returns to the Apps tile.
        key(a, KeyEvent.KEYCODE_BACK);
        assertEquals("Apps", ((Tile) a.focused()).label);

        // Home again: back to the start of the dock.
        a.onNewIntent(new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME));
        idle(500);
        assertEquals("YouTube", ((Tile) a.focused()).label);

        // Idle for 3 minutes: only the photo stays. The first key only wakes it.
        idle(HomeActivity.IDLE_MS + 2000);
        shoot(a, "04-idle");
        key(a, KeyEvent.KEYCODE_DPAD_RIGHT);
        assertEquals("waking doesn't move focus", "YouTube", ((Tile) a.focused()).label);

        // Next photo after 45 s: the backdrop changes.
        Photos.Photo before = currentPhoto(a);
        idle(HomeActivity.PHOTO_INTERVAL_MS + 100);
        settle(a);
        idle(2000);   // crossfade
        assertTrue("photo rotated", before != currentPhoto(a));
        shoot(a, "05-next-photo");

        // Disabling an app (what atv.py debloat does) drops it from the dock; enabling brings it back in place.
        a.getPackageManager().setApplicationEnabledSetting("com.netflix.ninja",
            PackageManager.COMPONENT_ENABLED_STATE_DISABLED_USER, 0);
        a.sendBroadcast(new Intent(Intent.ACTION_PACKAGE_CHANGED, android.net.Uri.parse("package:com.netflix.ninja")));
        idle(700);
        settle(a);
        assertTrue("disabled app leaves the dock", !a.dockLabels().contains("Netflix"));
        a.getPackageManager().setApplicationEnabledSetting("com.netflix.ninja",
            PackageManager.COMPONENT_ENABLED_STATE_ENABLED, 0);
        a.sendBroadcast(new Intent(Intent.ACTION_PACKAGE_CHANGED, android.net.Uri.parse("package:com.netflix.ninja")));
        idle(700);
        settle(a);
        assertEquals("re-enabled app is back in its place", "Netflix", a.dockLabels().get(2));
        c.pause().stop().destroy();
    }

    private static Photos.Photo currentPhoto(HomeActivity a) throws Exception {
        java.lang.reflect.Field f = HomeActivity.class.getDeclaredField("backdrop");
        f.setAccessible(true);
        return ((Backdrop) f.get(a)).current();
    }

    /** A remote key press, through the window (ViewRootImpl) like a real one, so focus moves happen. */
    private static void key(HomeActivity a, int code) throws Exception {
        View decor = a.getWindow().getDecorView();
        Object root = View.class.getMethod("getViewRootImpl").invoke(decor);
        java.lang.reflect.Method dispatch = root.getClass().getMethod("dispatchInputEvent", android.view.InputEvent.class);
        long now = android.os.SystemClock.uptimeMillis();
        dispatch.invoke(root, new KeyEvent(now, now, KeyEvent.ACTION_DOWN, code, 0));
        idle(50);
        dispatch.invoke(root, new KeyEvent(now, now + 50, KeyEvent.ACTION_UP, code, 0));
        idle(400);
    }

    /** Lets the background thread (apps, photo decoding) finish and the main thread catch up. */
    private static void settle(HomeActivity a) throws InterruptedException {
        for (int i = 0; i < 40; i++) {
            shadowOf(Looper.getMainLooper()).idle();
            Thread.sleep(50);
        }
        idle(500);
    }

    private static void idle(long ms) {
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(ms));
    }

    private void shoot(HomeActivity a, String name) throws Exception {
        View root = a.getWindow().getDecorView();
        Bitmap b = Bitmap.createBitmap(root.getWidth(), root.getHeight(), Bitmap.Config.ARGB_8888);
        root.draw(new Canvas(b));
        File out = new File(shots, name + ".png");
        FileOutputStream os = new FileOutputStream(out);
        try {
            b.compress(Bitmap.CompressFormat.PNG, 100, os);
        } finally {
            os.close();
        }
        System.out.println("shot " + out + " " + b.getWidth() + "x" + b.getHeight());
    }
}
