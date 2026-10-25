/*
 * Minimal stand-in for androidx.test (Apache-2.0), which is published only on
 * Google's Maven. Robolectric calls these for its own bookkeeping; the render
 * test doesn't use them. Same names and signatures, almost no behaviour.
 */
package androidx.test.runner.intent;
public final class IntentMonitorRegistry {
    private static IntentMonitor instance;
    public static IntentMonitor getInstance() { return instance; }
    public static void registerInstance(IntentMonitor m) { instance = m; }
}
