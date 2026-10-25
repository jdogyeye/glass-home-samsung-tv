/*
 * Minimal stand-in for androidx.test (Apache-2.0), which is published only on
 * Google's Maven. Robolectric calls these for its own bookkeeping; the render
 * test doesn't use them. Same names and signatures, almost no behaviour.
 */
package androidx.test.runner.intent;
public final class IntentStubberRegistry {
    public static boolean isLoaded() { return false; }
    public static IntentStubber getInstance() { throw new IllegalStateException("no intent stubber"); }
}
