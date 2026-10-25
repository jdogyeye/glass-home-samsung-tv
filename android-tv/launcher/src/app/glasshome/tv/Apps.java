package app.glasshome.tv;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.drawable.Drawable;
import android.provider.Settings;

import java.text.Collator;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The apps that can be opened, and the dock's order (kept in preferences as
 * package names). Disabled or uninstalled apps simply stop resolving, so they
 * drop out of the dock without being forgotten: re-enable one and it is back
 * in its old place.
 */
final class Apps {
    static final class App {
        final String pkg;
        final String label;
        final Intent launch;
        final Drawable banner;   // 16:9 TV banner, or null
        final Drawable icon;

        App(String pkg, String label, Intent launch, Drawable banner, Drawable icon) {
            this.pkg = pkg;
            this.label = label;
            this.launch = launch;
            this.banner = banner;
            this.icon = icon;
        }
    }

    /** Used for the first dock when nothing is saved, in this order, if installed. */
    static final String[] PREFERRED = {
        "com.google.android.youtube.tv",
        "com.netflix.ninja",
        "com.amazon.amazonvideo.livingroom",
        "com.disney.disneyplus",
        "com.apple.atve.androidtv.appletv",
        "com.wbd.stream",
        "com.hbo.hbonow",
        "com.spotify.tv.android",
        "com.plexapp.android",
        "org.xbmc.kodi",
        "tv.twitch.android.app",
        "com.google.android.youtube.tvmusic",
    };
    static final int DEFAULT_DOCK_SIZE = 5;   // + Apps + Settings fills the dock without scrolling

    private static final String PREFS = "glass-home";
    private static final String KEY_DOCK = "dock";

    private Apps() {}

    /** Every openable app except this one, by name. Slow-ish (icons): call off the UI thread. */
    static List<App> loadAll(Context ctx) {
        PackageManager pm = ctx.getPackageManager();
        Map<String, App> byPkg = new LinkedHashMap<String, App>();
        // TV apps first, so their banners and TV entry points win over phone ones.
        collect(ctx, pm, Intent.CATEGORY_LEANBACK_LAUNCHER, byPkg);
        collect(ctx, pm, Intent.CATEGORY_LAUNCHER, byPkg);
        List<App> apps = new ArrayList<App>(byPkg.values());
        final Collator collator = Collator.getInstance();
        Collections.sort(apps, new Comparator<App>() {
            @Override public int compare(App a, App b) { return collator.compare(a.label, b.label); }
        });
        return apps;
    }

    private static void collect(Context ctx, PackageManager pm, String category, Map<String, App> out) {
        Intent query = new Intent(Intent.ACTION_MAIN).addCategory(category);
        List<ResolveInfo> found;
        try {
            found = pm.queryIntentActivities(query, 0);
        } catch (RuntimeException e) {
            return;   // a dying package manager during an update; the next refresh fixes it
        }
        for (ResolveInfo ri : found) {
            ActivityInfo ai = ri.activityInfo;
            if (ai == null || ctx.getPackageName().equals(ai.packageName) || out.containsKey(ai.packageName)) continue;
            Intent launch = new Intent(Intent.ACTION_MAIN)
                .addCategory(category)
                .setComponent(new ComponentName(ai.packageName, ai.name))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
            CharSequence label = ri.loadLabel(pm);
            Drawable banner = null;
            if (Intent.CATEGORY_LEANBACK_LAUNCHER.equals(category)) {
                try {
                    banner = ai.loadBanner(pm);
                    if (banner == null) banner = ai.applicationInfo.loadBanner(pm);
                } catch (RuntimeException ignored) { /* fall back to the icon */ }
            }
            Drawable icon;
            try {
                icon = ri.loadIcon(pm);
            } catch (RuntimeException e) {
                icon = null;
            }
            out.put(ai.packageName, new App(ai.packageName, label == null ? ai.packageName : label.toString(),
                launch, banner, icon));
        }
    }

    /** The TV's Settings app, always last in the dock so it can't be lost. */
    static App settings(Context ctx) {
        PackageManager pm = ctx.getPackageManager();
        Intent i = new Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        ResolveInfo ri = null;
        try {
            ri = pm.resolveActivity(i, 0);
        } catch (RuntimeException ignored) { /* use the plain tile */ }
        Drawable banner = null;
        Drawable icon = null;
        if (ri != null && ri.activityInfo != null) {
            try {
                banner = ri.activityInfo.loadBanner(pm);
                if (banner == null) banner = ri.activityInfo.applicationInfo.loadBanner(pm);
                icon = ri.loadIcon(pm);
            } catch (RuntimeException ignored) { /* plain tile */ }
        }
        return new App(ri != null && ri.activityInfo != null ? ri.activityInfo.packageName : "settings",
            "Settings", i, banner, icon);
    }

    // ---- the dock's order ----

    static List<String> dockOrder(Context ctx, List<App> all) {
        String saved = prefs(ctx).getString(KEY_DOCK, null);
        List<String> order = new ArrayList<String>();
        if (saved != null) {
            for (String p : saved.split(",")) if (p.length() > 0 && !order.contains(p)) order.add(p);
            return order;
        }
        Set<String> installed = new HashSet<String>();
        for (App a : all) installed.add(a.pkg);
        for (String p : PREFERRED) {
            if (order.size() >= DEFAULT_DOCK_SIZE) break;
            if (installed.contains(p)) order.add(p);
        }
        for (App a : all) {
            if (order.size() >= DEFAULT_DOCK_SIZE) break;
            if (!order.contains(a.pkg) && !isPlumbing(a.pkg)) order.add(a.pkg);
        }
        return order;
    }

    /** System screens that are poor first-run dock picks (still addable by hand). */
    private static boolean isPlumbing(String pkg) {
        return pkg.startsWith("com.android.") || pkg.equals("com.google.android.tvlauncher")
            || pkg.equals("com.google.android.apps.tv.launcherx") || pkg.contains("settings");
    }

    static void saveDock(Context ctx, List<String> order) {
        StringBuilder sb = new StringBuilder();
        for (String p : order) {
            if (sb.length() > 0) sb.append(',');
            sb.append(p);
        }
        prefs(ctx).edit().putString(KEY_DOCK, sb.toString()).apply();
    }

    /** The dock's apps that are currently installed and enabled, in order. */
    static List<App> dockApps(List<String> order, List<App> all) {
        Map<String, App> byPkg = new LinkedHashMap<String, App>();
        for (App a : all) byPkg.put(a.pkg, a);
        List<App> out = new ArrayList<App>();
        for (String p : order) {
            App a = byPkg.get(p);
            if (a != null) out.add(a);
        }
        return out;
    }

    /**
     * Moves {@code pkg} one visible place left or right. Apps in the saved order
     * that are hidden right now (disabled, uninstalled) keep their positions.
     */
    static void move(List<String> order, Set<String> visible, String pkg, int dir) {
        int i = order.indexOf(pkg);
        if (i < 0) return;
        int j = i + dir;
        while (j >= 0 && j < order.size() && !visible.contains(order.get(j))) j += dir;
        if (j < 0 || j >= order.size()) return;
        order.set(i, order.get(j));
        order.set(j, pkg);
    }

    static SharedPreferences prefs(Context ctx) {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}
