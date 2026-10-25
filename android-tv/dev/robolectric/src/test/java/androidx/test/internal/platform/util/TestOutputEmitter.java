/*
 * Minimal stand-in for androidx.test (Apache-2.0), which is published only on
 * Google's Maven. Robolectric calls these for its own bookkeeping; the render
 * test doesn't use them. Same names and signatures, almost no behaviour.
 */
package androidx.test.internal.platform.util;
public final class TestOutputEmitter {
    public static void dumpThreadStates(String outputFileName) {}
}
