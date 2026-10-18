package com.campuswallet.demo;

import android.app.Activity;
import android.content.Intent;
import android.webkit.JavascriptInterface;

/**
 * The only bridge between the web UI and native code.
 *
 * The bottom "Settings" tab is the single settings entry (the old gear button in
 * the page header is gone): its "this device" card calls these methods, and each
 * one immediately posts to the UI thread, so a JavaScript call never blocks the
 * page while Python or the network is working.
 *
 * Exposed to the page as {@code window.CampusNative}; the page is always the
 * loopback Flask app we ship ourselves, and nothing here is reachable from
 * outside the app.
 */
public final class WebBridge {

    private final Activity activity;

    WebBridge(Activity activity) {
        this.activity = activity;
    }

    /** Opens the native AI backend screen (key / model / connection test). */
    @JavascriptInterface
    public void openAiSettings() {
        activity.runOnUiThread(() ->
                activity.startActivity(new Intent(activity, AiSettingsActivity.class)));
    }

    /** Exports the ledger as CSV into Download/CampusWallet and shows the result. */
    @JavascriptInterface
    public void exportCsv() {
        activity.runOnUiThread(() -> NativeActions.exportCsv(activity));
    }

    @JavascriptInterface
    public void seedDemo() {
        activity.runOnUiThread(() -> NativeActions.seedDemo(activity));
    }

    @JavascriptInterface
    public void resetData() {
        activity.runOnUiThread(() -> NativeActions.resetData(activity));
    }

    @JavascriptInterface
    public void showDiagnostics() {
        activity.runOnUiThread(() -> NativeActions.showDiagnostics(activity));
    }
}
