package app.glasshome.tv;

import android.animation.ValueAnimator;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Point;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.provider.Settings;
import android.text.TextUtils;
import android.text.format.DateFormat;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewTreeObserver;
import android.widget.FrameLayout;
import android.widget.GridLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextClock;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * The home screen. Laid out on a 1920 x 1080 design grid scaled to the
 * screen (the same sizes as the Tizen version's app/css/tv.css), so it
 * looks the same on 720p, 1080p and 4K TVs whatever density they report.
 *
 * Remote: Left/Right along the dock, OK opens, hold OK for Move / Remove /
 * App info. "Apps" opens every app (hold OK there to add one to the dock).
 * Back closes the apps panel and otherwise does nothing. After 3 minutes
 * idle only the photos stay; the first key press just brings the dock back.
 */
public class HomeActivity extends Activity {
    static final long PHOTO_INTERVAL_MS = 45000;
    static final long IDLE_MS = 3 * 60 * 1000;

    // design px (1920 x 1080)
    private static final int TILE_W = 208, TILE_H = 117, GAP = 24, PAD_X = 24, PAD_Y = 24;
    private static final int DOCK_BOTTOM = 150, DOCK_RADIUS = 46, DOCK_MAX_W = 1680;
    // Room around a row of tiles for a focused, enlarged one. It is padding
    // inside the row, so the row's parent can clip normally (a scrolled-away
    // tile must not show outside the dock).
    private static final int OVERFLOW = 30;
    private static final int GRID_COLS = 6;

    private final Handler main = new Handler(Looper.getMainLooper());
    private HandlerThread ioThread;
    private Handler io;

    private float u;
    private int screenW, screenH;
    private FrameLayout root;
    private Backdrop backdrop;
    private View chrome;              // everything that fades out when idle
    private View clock;
    private HorizontalScrollView dockScroll;
    private LinearLayout dockRow;
    private TextView label, credit;
    private FrameLayout appsPanel;
    private GridLayout appsGrid;
    private ScrollView appsScroll;
    private AlertDialog menu;

    private List<Apps.App> all = new ArrayList<Apps.App>();
    private Apps.App settingsApp;
    private List<String> order = new ArrayList<String>();
    private List<Photos.Photo> photos = new ArrayList<Photos.Photo>();
    private int photoIndex;
    private long lastPhotoAt;
    private boolean ambient;
    private boolean started;
    private float glassAlpha = 1;
    private ValueAnimator glassAnim;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        Point size = new Point();
        getWindowManager().getDefaultDisplay().getRealSize(size);
        screenW = Math.max(size.x, size.y);
        screenH = Math.min(size.x, size.y);
        u = screenW / 1920f;

        ioThread = new HandlerThread("glass-home-io");
        ioThread.start();
        io = new Handler(ioThread.getLooper());

        buildViews();
        setContentView(root);
        hideSystemBars();

        IntentFilter pf = new IntentFilter();
        pf.addAction(Intent.ACTION_PACKAGE_ADDED);
        pf.addAction(Intent.ACTION_PACKAGE_REMOVED);
        pf.addAction(Intent.ACTION_PACKAGE_CHANGED);
        pf.addAction(Intent.ACTION_PACKAGE_REPLACED);
        pf.addDataScheme("package");
        registerReceiver(packages, pf);

        loadApps();
        loadPhotos();
    }

    @Override
    protected void onDestroy() {
        unregisterReceiver(packages);
        main.removeCallbacksAndMessages(null);
        ioThread.quit();
        super.onDestroy();
    }

    @Override
    protected void onStart() {
        super.onStart();
        started = true;
        backdrop.setRunning(true);
        long since = System.currentTimeMillis() - lastPhotoAt;
        main.removeCallbacks(nextPhoto);
        if (lastPhotoAt != 0) main.postDelayed(nextPhoto, Math.max(0, PHOTO_INTERVAL_MS - since));
        resetIdle();
    }

    @Override
    protected void onStop() {
        started = false;
        backdrop.setRunning(false);
        main.removeCallbacks(nextPhoto);
        main.removeCallbacks(goAmbient);
        super.onStop();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideSystemBars();
    }

    /** Home pressed while already home: back to the start of the dock. */
    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (menu != null) menu.dismiss();
        closeApps();
        wake();
        if (dockRow.getChildCount() > 0) {
            dockScroll.smoothScrollTo(0, 0);
            dockRow.getChildAt(0).requestFocus();
        }
    }

    @Override
    public void onBackPressed() {
        if (appsPanel.getVisibility() == View.VISIBLE) closeApps();
        // A home screen has nowhere to go back to.
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        if (e.getAction() == KeyEvent.ACTION_DOWN) {
            boolean wasAmbient = ambient;
            resetIdle();
            if (wasAmbient) return true;   // this press only wakes the screen
        } else if (ambient) {
            return true;
        }
        return super.dispatchKeyEvent(e);
    }

    // ---------------------------------------------------------------- views

    private int px(float design) {
        return Math.round(design * u);
    }

    private void buildViews() {
        root = new FrameLayout(this);
        root.setBackgroundColor(0xff04080b);
        backdrop = new Backdrop(this);
        backdrop.driftMs = PHOTO_INTERVAL_MS + backdrop.fadeMs;
        root.addView(backdrop, new FrameLayout.LayoutParams(-1, -1));

        FrameLayout ui = new FrameLayout(this);
        chrome = ui;
        root.addView(ui, new FrameLayout.LayoutParams(-1, -1));

        // clock and date, top right
        LinearLayout clock = new LinearLayout(this);
        this.clock = clock;
        clock.setOrientation(LinearLayout.VERTICAL);
        clock.setGravity(Gravity.END);
        TextClock time = new TextClock(this);
        time.setFormat12Hour("h:mm");
        time.setFormat24Hour("H:mm");
        styleText(time, 84, 0xffffffff, "sans-serif-medium");
        time.setLetterSpacing(-0.025f);
        TextClock date = new TextClock(this);
        String datePattern = DateFormat.getBestDateTimePattern(Locale.getDefault(), "EEEEdMMMM");
        date.setFormat12Hour(datePattern);
        date.setFormat24Hour(datePattern);
        styleText(date, 30, 0xb8ffffff, "sans-serif-medium");
        date.setPadding(0, px(12), 0, 0);
        clock.addView(time);
        clock.addView(date);
        FrameLayout.LayoutParams clp = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.END);
        clp.setMargins(0, px(52), px(80), 0);
        ui.addView(clock, clp);

        // the dock
        dockScroll = new HorizontalScrollView(this);
        dockScroll.setHorizontalScrollBarEnabled(false);
        dockScroll.setFillViewport(false);
        dockScroll.setHorizontalFadingEdgeEnabled(true);
        dockScroll.setFadingEdgeLength(px(80));
        dockScroll.setSmoothScrollingEnabled(true);
        dockRow = new LinearLayout(this);
        dockRow.setOrientation(LinearLayout.HORIZONTAL);
        dockRow.setClipChildren(false);
        dockRow.setClipToPadding(false);
        dockRow.setPadding(px(PAD_X), px(OVERFLOW + PAD_Y), px(PAD_X), px(OVERFLOW + PAD_Y));
        dockScroll.addView(dockRow, new FrameLayout.LayoutParams(-2, -1));
        FrameLayout.LayoutParams dlp = new FrameLayout.LayoutParams(px(PAD_X * 2),
            px(TILE_H + 2 * (PAD_Y + OVERFLOW)), Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        dlp.bottomMargin = px(DOCK_BOTTOM - OVERFLOW);
        ui.addView(dockScroll, dlp);
        dockScroll.addOnLayoutChangeListener(new View.OnLayoutChangeListener() {
            @Override public void onLayoutChange(View v, int l, int t, int r, int b, int ol, int ot, int or, int ob) {
                updateGlass();
                placeLabel();
            }
        });
        dockScroll.getViewTreeObserver().addOnScrollChangedListener(new ViewTreeObserver.OnScrollChangedListener() {
            @Override public void onScrollChanged() { placeLabel(); }
        });

        // the focused app's name, under its tile
        label = new TextView(this);
        styleText(label, 30, 0xffffffff, "sans-serif-medium");
        label.setGravity(Gravity.CENTER_HORIZONTAL);
        label.setSingleLine(true);
        label.setEllipsize(TextUtils.TruncateAt.END);
        FrameLayout.LayoutParams llp = new FrameLayout.LayoutParams(px(560), -2, Gravity.BOTTOM | Gravity.START);
        llp.bottomMargin = px(DOCK_BOTTOM - 74);
        ui.addView(label, llp);

        // the photo's credit, bottom left
        credit = new TextView(this);
        styleText(credit, 18, 0x99ffffff, "sans-serif-medium");
        credit.setSingleLine(true);
        credit.setEllipsize(TextUtils.TruncateAt.END);
        credit.setMaxWidth(px(900));
        FrameLayout.LayoutParams crlp = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.START);
        crlp.setMargins(px(72), 0, 0, px(34));
        ui.addView(credit, crlp);

        buildAppsPanel();
    }

    private void styleText(TextView t, float size, int color, String family) {
        t.setTextColor(color);
        t.setTextSize(TypedValue.COMPLEX_UNIT_PX, size * u);
        t.setTypeface(Typeface.create(family, Typeface.NORMAL));
        t.setShadowLayer(22 * u, 0, 2 * u, 0x59000000);
        t.setIncludeFontPadding(false);
    }

    private void buildAppsPanel() {
        appsPanel = new FrameLayout(this);
        appsPanel.setVisibility(View.GONE);
        TextView title = new TextView(this);
        title.setText("Apps");
        styleText(title, 46, 0xffffffff, "sans-serif-medium");
        FrameLayout.LayoutParams tlp = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START);
        tlp.setMargins(px(120), px(70), 0, 0);
        appsPanel.addView(title, tlp);
        TextView hint = new TextView(this);
        hint.setText("Hold OK to add an app to the dock, or to remove it");
        styleText(hint, 22, 0x99ffffff, "sans-serif");
        FrameLayout.LayoutParams hlp = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START);
        hlp.setMargins(px(122), px(132), 0, 0);
        appsPanel.addView(hint, hlp);

        appsScroll = new ScrollView(this);
        appsScroll.setVerticalScrollBarEnabled(false);
        appsScroll.setVerticalFadingEdgeEnabled(true);
        appsScroll.setFadingEdgeLength(px(60));
        appsGrid = new GridLayout(this);
        appsGrid.setColumnCount(GRID_COLS);
        appsGrid.setClipChildren(false);
        appsGrid.setClipToPadding(false);
        appsGrid.setPadding(px(120), px(OVERFLOW), px(120), px(80));
        appsScroll.addView(appsGrid, new FrameLayout.LayoutParams(-1, -2));
        FrameLayout.LayoutParams slp = new FrameLayout.LayoutParams(-1, -1);
        slp.topMargin = px(190);
        appsPanel.addView(appsScroll, slp);
        root.addView(appsPanel, new FrameLayout.LayoutParams(-1, -1));
    }

    private void hideSystemBars() {
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LAYOUT_STABLE
            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_FULLSCREEN
            | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
    }

    // ---------------------------------------------------------------- dock

    private void loadApps() {
        io.post(new Runnable() {
            @Override public void run() {
                final List<Apps.App> loaded = Apps.loadAll(HomeActivity.this);
                final Apps.App settings = Apps.settings(HomeActivity.this);
                main.post(new Runnable() {
                    @Override public void run() {
                        all = loaded;
                        settingsApp = settings;
                        order = Apps.dockOrder(HomeActivity.this, all);
                        String keep = focusedKey();
                        buildDock(keep);
                        if (appsPanel.getVisibility() == View.VISIBLE) buildAppsGrid(focusedKey());
                    }
                });
            }
        });
    }

    private void buildDock(String focusKey) {
        dockRow.removeAllViews();
        List<Apps.App> apps = Apps.dockApps(order, all);
        for (Apps.App a : apps) addDockTile(new Tile(this, a, a.label, null, px(TILE_W), px(TILE_H), u));
        addDockTile(new Tile(this, null, "Apps", new Tile.GridGlyph(), px(TILE_W), px(TILE_H), u));
        if (settingsApp != null) addDockTile(new Tile(this, settingsApp, "Settings", null, px(TILE_W), px(TILE_H), u));

        int n = dockRow.getChildCount();
        int content = n * px(TILE_W) + (n - 1) * px(GAP) + 2 * px(PAD_X);   // dockRow's width
        ViewGroup.LayoutParams lp = dockScroll.getLayoutParams();
        lp.width = Math.min(content, px(DOCK_MAX_W));
        dockScroll.setLayoutParams(lp);

        View target = dockRow.getChildAt(0);
        for (int i = 0; i < n; i++) {
            Tile t = (Tile) dockRow.getChildAt(i);
            if (focusKey != null && focusKey.equals(keyOf(t))) target = t;
        }
        if (target != null && appsPanel.getVisibility() != View.VISIBLE) target.requestFocus();
    }

    private void addDockTile(final Tile t) {
        LinearLayout.LayoutParams lp = (LinearLayout.LayoutParams) t.getLayoutParams();
        if (dockRow.getChildCount() > 0) lp.leftMargin = px(GAP);
        t.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                if (t.app == null) openApps();
                else launch(t.app);
            }
        });
        t.setOnLongClickListener(new View.OnLongClickListener() {
            @Override public boolean onLongClick(View v) {
                if (t.app != null && t.app != settingsApp) dockMenu(t.app);
                return true;
            }
        });
        t.setOnFocusChangeListener(new View.OnFocusChangeListener() {
            @Override public void onFocusChange(View v, boolean has) {
                if (has) {
                    label.setText(t.label);
                    placeLabel();
                }
            }
        });
        dockRow.addView(t, lp);
    }

    /** The focused view (same as getCurrentFocus(), which test frameworks may stub). */
    View focused() {
        return getWindow().getDecorView().findFocus();
    }

    private static String keyOf(Tile t) {
        return t.app == null ? "#apps" : t.app.pkg;
    }

    private String focusedKey() {
        View f = focused();
        return f instanceof Tile ? keyOf((Tile) f) : null;
    }

    private void updateGlass() {
        float l = dockScroll.getLeft(), t = dockScroll.getTop() + px(OVERFLOW);
        float r = dockScroll.getRight(), b = dockScroll.getBottom() - px(OVERFLOW);
        backdrop.setGlass(l, t, r, b, DOCK_RADIUS * u);
        backdrop.setGlassAlpha(glassAlpha);
    }

    private void placeLabel() {
        View f = focused();
        if (!(f instanceof Tile) || f.getParent() != dockRow) return;
        float center = dockScroll.getLeft() + f.getLeft() + f.getWidth() / 2f - dockScroll.getScrollX();
        float half = label.getLayoutParams().width / 2f;
        label.setTranslationX(Math.max(0, Math.min(screenW - 2 * half, center - half)));
    }

    private void dockMenu(final Apps.App app) {
        final Set<String> visible = new HashSet<String>();
        for (Apps.App a : Apps.dockApps(order, all)) visible.add(a.pkg);
        String[] items = {"Move left", "Move right", "Remove from dock", "App info", "Cancel"};
        menu = new AlertDialog.Builder(this, AlertDialog.THEME_DEVICE_DEFAULT_DARK)
            .setTitle(app.label)
            .setItems(items, new DialogInterface.OnClickListener() {
                @Override public void onClick(DialogInterface d, int which) {
                    if (which == 0 || which == 1) {
                        Apps.move(order, visible, app.pkg, which == 0 ? -1 : 1);
                        Apps.saveDock(HomeActivity.this, order);
                        buildDock(app.pkg);
                    } else if (which == 2) {
                        order.remove(app.pkg);
                        Apps.saveDock(HomeActivity.this, order);
                        buildDock(null);
                    } else if (which == 3) {
                        appInfo(app);
                    }
                }
            })
            .create();
        menu.show();
    }

    // ---------------------------------------------------------------- all apps

    private void openApps() {
        appsPanel.setVisibility(View.VISIBLE);
        dockScroll.setVisibility(View.INVISIBLE);
        label.setVisibility(View.INVISIBLE);
        clock.setVisibility(View.INVISIBLE);
        backdrop.setDimmed(true);
        animateGlass(0);
        buildAppsGrid(null);
    }

    private void closeApps() {
        if (appsPanel.getVisibility() != View.VISIBLE) return;
        appsPanel.setVisibility(View.GONE);
        dockScroll.setVisibility(View.VISIBLE);
        label.setVisibility(View.VISIBLE);
        clock.setVisibility(View.VISIBLE);
        backdrop.setDimmed(false);
        animateGlass(1);
        for (int i = 0; i < dockRow.getChildCount(); i++) {
            Tile t = (Tile) dockRow.getChildAt(i);
            if (t.app == null) {   // back on "Apps", where we came from
                t.requestFocus();
                break;
            }
        }
    }

    private void buildAppsGrid(String focusKey) {
        appsGrid.removeAllViews();
        int cellGap = (screenW - 2 * px(120) - GRID_COLS * px(TILE_W)) / (GRID_COLS - 1);
        Set<String> docked = new HashSet<String>(order);
        View target = null;
        for (int i = 0; i < all.size(); i++) {
            final Apps.App a = all.get(i);
            final Tile t = new Tile(this, a, a.label, null, px(TILE_W), px(TILE_H), u);
            t.setBadge(docked.contains(a.pkg));
            GridLayout.LayoutParams lp = new GridLayout.LayoutParams();
            lp.width = px(TILE_W);
            lp.height = px(TILE_H);
            lp.setMargins(i % GRID_COLS == 0 ? 0 : cellGap, i < GRID_COLS ? 0 : px(64), 0, 0);
            t.setOnClickListener(new View.OnClickListener() {
                @Override public void onClick(View v) {
                    launch(a);
                }
            });
            t.setOnLongClickListener(new View.OnLongClickListener() {
                @Override public boolean onLongClick(View v) {
                    gridMenu(a);
                    return true;
                }
            });
            appsGrid.addView(t, lp);
            if (target == null || a.pkg.equals(focusKey)) target = t;
        }
        if (target != null) target.requestFocus();
    }

    private void gridMenu(final Apps.App app) {
        final boolean docked = order.contains(app.pkg);
        String[] items = {docked ? "Remove from dock" : "Add to dock", "App info", "Cancel"};
        menu = new AlertDialog.Builder(this, AlertDialog.THEME_DEVICE_DEFAULT_DARK)
            .setTitle(app.label)
            .setItems(items, new DialogInterface.OnClickListener() {
                @Override public void onClick(DialogInterface d, int which) {
                    if (which == 0) {
                        if (docked) order.remove(app.pkg);
                        else order.add(app.pkg);
                        Apps.saveDock(HomeActivity.this, order);
                        buildDock(null);
                        buildAppsGrid(app.pkg);
                    } else if (which == 1) {
                        appInfo(app);
                    }
                }
            })
            .create();
        menu.show();
    }

    // ---------------------------------------------------------------- launching

    private void launch(Apps.App app) {
        try {
            startActivity(app.launch);
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, app.label + " can't be opened", Toast.LENGTH_SHORT).show();
            loadApps();
        } catch (SecurityException e) {
            Toast.makeText(this, app.label + " can't be opened", Toast.LENGTH_SHORT).show();
        }
    }

    private void appInfo(Apps.App app) {
        try {
            startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.fromParts("package", app.pkg, null)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, "No app info screen on this TV", Toast.LENGTH_SHORT).show();
        }
    }

    private final BroadcastReceiver packages = new BroadcastReceiver() {
        @Override public void onReceive(Context c, Intent i) {
            main.removeCallbacks(reloadApps);
            main.postDelayed(reloadApps, 500);   // installs send several of these at once
        }
    };

    private final Runnable reloadApps = new Runnable() {
        @Override public void run() { loadApps(); }
    };

    // ---------------------------------------------------------------- photos

    private void loadPhotos() {
        io.post(new Runnable() {
            @Override public void run() {
                final List<Photos.Photo> list = Photos.list(HomeActivity.this);
                main.post(new Runnable() {
                    @Override public void run() {
                        photos = list;
                        // Start somewhere different each time the launcher starts.
                        photoIndex = photos.isEmpty() ? 0
                            : (int) ((System.currentTimeMillis() / PHOTO_INTERVAL_MS) % photos.size());
                        showPhoto();
                    }
                });
            }
        });
    }

    private final Runnable nextPhoto = new Runnable() {
        @Override public void run() {
            if (photos.isEmpty()) return;
            photoIndex = (photoIndex + 1) % photos.size();
            showPhoto();
        }
    };

    private void showPhoto() {
        main.removeCallbacks(nextPhoto);
        if (photos.isEmpty()) return;
        final Photos.Photo p = photos.get(photoIndex);
        lastPhotoAt = System.currentTimeMillis();
        io.post(new Runnable() {
            @Override public void run() {
                final Photos.Decoded d = Photos.decode(HomeActivity.this, p, screenW, screenH);
                main.post(new Runnable() {
                    @Override public void run() {
                        if (d != null) {
                            backdrop.show(d);
                            credit.setText(p.credit == null ? "" : p.credit);
                        }
                        if (started && photos.size() > 1) main.postDelayed(nextPhoto, PHOTO_INTERVAL_MS);
                    }
                });
            }
        });
    }

    // ---------------------------------------------------------------- idle

    private final Runnable goAmbient = new Runnable() {
        @Override public void run() {
            if (appsPanel.getVisibility() == View.VISIBLE || (menu != null && menu.isShowing())) {
                resetIdle();
                return;
            }
            ambient = true;
            chrome.animate().alpha(0).setDuration(1200).start();
            animateGlass(0);
        }
    };

    private void resetIdle() {
        main.removeCallbacks(goAmbient);
        if (ambient) wake();
        main.postDelayed(goAmbient, IDLE_MS);
    }

    private void wake() {
        if (!ambient) return;
        ambient = false;
        chrome.animate().alpha(1).setDuration(300).start();
        if (appsPanel.getVisibility() != View.VISIBLE) animateGlass(1);
    }

    private void animateGlass(float to) {
        if (glassAnim != null) glassAnim.cancel();
        glassAnim = ValueAnimator.ofFloat(glassAlpha, to);
        glassAnim.setDuration(to > glassAlpha ? 300 : 900);
        glassAnim.addUpdateListener(new ValueAnimator.AnimatorUpdateListener() {
            @Override public void onAnimationUpdate(ValueAnimator a) {
                glassAlpha = (Float) a.getAnimatedValue();
                backdrop.setGlassAlpha(glassAlpha);
            }
        });
        glassAnim.start();
    }

    // For tests: the dock's labels in order.
    List<String> dockLabels() {
        List<String> out = new ArrayList<String>();
        for (int i = 0; i < dockRow.getChildCount(); i++) out.add(((Tile) dockRow.getChildAt(i)).label);
        return Collections.unmodifiableList(out);
    }
}
