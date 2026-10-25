/*
 * Minimal stand-in for androidx.test (Apache-2.0), which is published only on
 * Google's Maven. Robolectric calls these for its own bookkeeping; the render
 * test doesn't use them. Same names and signatures, almost no behaviour.
 */
package androidx.test.espresso;
public interface IdlingResource {
    String getName();
    boolean isIdleNow();
    void registerIdleTransitionCallback(ResourceCallback callback);
    interface ResourceCallback { void onTransitionToIdle(); }
}
