/*
 * Minimal stand-in for androidx.test (Apache-2.0), which is published only on
 * Google's Maven. Robolectric calls these for its own bookkeeping; the render
 * test doesn't use them. Same names and signatures, almost no behaviour.
 */
package androidx.test.runner.lifecycle;
import android.app.Activity;
import java.util.Collection;
public interface ActivityLifecycleMonitor {
    void addLifecycleCallback(ActivityLifecycleCallback callback);
    void removeLifecycleCallback(ActivityLifecycleCallback callback);
    Stage getLifecycleStageOf(Activity activity);
    Collection<Activity> getActivitiesInStage(Stage stage);
}
