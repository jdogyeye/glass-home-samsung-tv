/*
 * Minimal stand-in for androidx.test (Apache-2.0), which is published only on
 * Google's Maven. Robolectric calls these for its own bookkeeping; the render
 * test doesn't use them. Same names and signatures, almost no behaviour.
 */
package androidx.test.platform.app;
import android.app.Instrumentation;
import android.os.Bundle;
public final class InstrumentationRegistry {
    private static Instrumentation instrumentation;
    private static Bundle arguments = new Bundle();
    public static void registerInstance(Instrumentation i, Bundle args) { instrumentation = i; arguments = args; }
    public static Instrumentation getInstrumentation() { return instrumentation; }
    public static Bundle getArguments() { return arguments; }
}
