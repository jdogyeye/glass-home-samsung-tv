/*
 * Minimal stand-in for androidx.test (Apache-2.0), which is published only on
 * Google's Maven. Robolectric calls these for its own bookkeeping; the render
 * test doesn't use them. Same names and signatures, almost no behaviour.
 */
package androidx.test.internal.runner.intent;
import android.content.Intent;
import androidx.test.runner.intent.IntentMonitor;
public final class IntentMonitorImpl implements IntentMonitor {
    public void signalIntent(Intent intent) {}
}
