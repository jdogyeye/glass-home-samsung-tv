/*
 * Minimal stand-in for androidx.test (Apache-2.0), which is published only on
 * Google's Maven. Robolectric calls these for its own bookkeeping; the render
 * test doesn't use them. Same names and signatures, almost no behaviour.
 */
package androidx.test.runner.lifecycle;
public final class ApplicationLifecycleMonitorRegistry {
    private static ApplicationLifecycleMonitor instance;
    public static ApplicationLifecycleMonitor getInstance() { return instance; }
    public static void registerInstance(ApplicationLifecycleMonitor m) { instance = m; }
}
