/*
 * Minimal stand-in for androidx.test (Apache-2.0), which is published only on
 * Google's Maven. Robolectric calls these for its own bookkeeping; the render
 * test doesn't use them. Same names and signatures, almost no behaviour.
 */
package androidx.test.internal.runner.lifecycle;
import android.app.Application;
import androidx.test.runner.lifecycle.ApplicationLifecycleMonitor;
import androidx.test.runner.lifecycle.ApplicationStage;
public final class ApplicationLifecycleMonitorImpl implements ApplicationLifecycleMonitor {
    public void signalLifecycleChange(Application app, ApplicationStage stage) {}
}
