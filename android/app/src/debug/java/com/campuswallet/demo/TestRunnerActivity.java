package com.campuswallet.demo;

import android.app.Activity;
import android.graphics.Typeface;
import android.os.Bundle;
import android.util.Log;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

/**
 * Debug-only on-device test runner.
 *
 * Not reachable from the app UI: it exists so M3 can prove functional completeness
 * by running the project's own unittest suite on the phone:
 *
 *     adb shell am start -n com.campuswallet.demo/.TestRunnerActivity
 *
 * The result is shown on screen and also logged as "SELFTEST Ran N tests ...".
 * Release builds do not contain this class nor the tests/ directory.
 */
public class TestRunnerActivity extends Activity {

    private static final String TAG = "CampusWallet";

    private TextView output;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        UiInsets.applyEdgeToEdge(this);

        output = new TextView(this);
        output.setTextSize(11f);
        output.setTypeface(Typeface.MONOSPACE);
        output.setTextIsSelectable(true);
        int pad = Math.round(14 * getResources().getDisplayMetrics().density);
        output.setPadding(pad, pad, pad, pad);
        output.setText("running…");

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(getColor(R.color.app_bg));
        scroll.addView(output);
        setContentView(scroll);

        // 16 MB stack: importing the whole app (app -> flask -> jinja) and running
        // unittest discovery inside the default 1 MB Java thread stack overflows it
        // (observed as SIGSEGV "stack pointer is close to top of stack" on device).
        Thread runner = new Thread(null, this::runSuite, "selftest", 16L * 1024 * 1024);
        runner.start();
    }

    private void runSuite() {
        String text;
        try {
            JSONObject result = new JSONObject(PyServer.runTests());
            text = result.toString(2);
            Log.i(TAG, "SELFTEST " + result.optString("summary"));
        } catch (Throwable error) {
            Log.e(TAG, "SELFTEST failed", error);
            text = "失败：" + Log.getStackTraceString(error);
        }
        final String body = text;
        runOnUiThread(() -> output.setText(body));
    }
}
