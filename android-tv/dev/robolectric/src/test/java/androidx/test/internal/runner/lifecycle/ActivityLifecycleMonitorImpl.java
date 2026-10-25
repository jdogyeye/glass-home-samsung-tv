/*
 * Minimal stand-in for androidx.test (Apache-2.0), which is published only on
 * Google's Maven. Robolectric calls these for its own bookkeeping; the render
 * test doesn't use them. Same names and signatures, almost no behaviour.
 */
package androidx.test.internal.runner.lifecycle;
import android.app.Activity;
import androidx.test.runner.lifecycle.ActivityLifecycleCallback;
import androidx.test.runner.lifecycle.ActivityLifecycleMonitor;
import androidx.test.runner.lifecycle.Stage;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.WeakHashMap;
public final class ActivityLifecycleMonitorImpl implements ActivityLifecycleMonitor {
    private final Map<Activity, Stage> stages = new WeakHashMap<Activity, Stage>();
    private final List<ActivityLifecycleCallback> callbacks = new ArrayList<ActivityLifecycleCallback>();
    public void signalLifecycleChange(Stage stage, Activity activity) {
        stages.put(activity, stage);
        for (ActivityLifecycleCallback c : new ArrayList<ActivityLifecycleCallback>(callbacks)) c.onActivityLifecycleChanged(activity, stage);
    }
    @Override public void addLifecycleCallback(ActivityLifecycleCallback c) { callbacks.add(c); }
    @Override public void removeLifecycleCallback(ActivityLifecycleCallback c) { callbacks.remove(c); }
    @Override public Stage getLifecycleStageOf(Activity a) { return stages.get(a); }
    @Override public Collection<Activity> getActivitiesInStage(Stage stage) {
        List<Activity> out = new ArrayList<Activity>();
        for (Map.Entry<Activity, Stage> e : stages.entrySet()) if (e.getValue() == stage) out.add(e.getKey());
        return out;
    }
}
