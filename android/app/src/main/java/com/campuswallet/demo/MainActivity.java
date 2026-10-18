package com.campuswallet.demo;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.res.Configuration;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.ProgressBar;
import android.widget.TextView;

/**
 * The app shell: an embedded Flask server on loopback plus a full-screen WebView
 * showing the unchanged H5 interface.
 *
 * There is exactly one settings entry, the "Settings" tab at the bottom of the
 * page. The gear button that used to sit in the page header is gone; its
 * functions now live in that tab and reach native code through
 * {@link WebBridge} ({@code window.CampusNative}).
 *
 * Startup order matters: {@link PyServer#ensureStarted} imports the real Flask
 * application (which creates the SQLite schema) and only then is the page loaded,
 * so the first request never races the server.
 */
public class MainActivity extends Activity {

    private static final String TAG = "CampusWallet";

    private WebView web;
    private View overlay;
    private ProgressBar spinner;
    private TextView status;
    private Button retry;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        web = findViewById(R.id.web);
        overlay = findViewById(R.id.overlay);
        spinner = findViewById(R.id.spinner);
        status = findViewById(R.id.status);
        retry = findViewById(R.id.retry);

        UiInsets.applyEdgeToEdge(this);
        configureWebView();

        retry.setOnClickListener(v -> bootstrap());
        bootstrap();
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void configureWebView() {
        web.setBackgroundColor(Color.parseColor("#F5F6FA"));

        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setTextZoom(100);

        // The Settings tab's "this device" card reaches AI settings, CSV export,
        // demo data and diagnostics through this bridge. Registered before the
        // first loadUrl so the page sees window.CampusNative immediately.
        web.addJavascriptInterface(new WebBridge(this), "CampusNative");

        // The page implements its own pull-to-refresh (static/js/pull-refresh.js);
        // the WebView's overscroll glow would swallow the first part of that
        // gesture, and it also looks off on a full-screen app.
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                Log.i(TAG, "page finished: " + url);
                hideOverlay();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError error) {
                if (request != null && !request.isForMainFrame()) {
                    return;
                }
                Log.e(TAG, "webview error " + error.getErrorCode() + ": " + error.getDescription());
                showError("无法连接本地服务 (" + error.getErrorCode() + ")\n"
                        + error.getDescription());
            }
        });

        if (BuildConfig.DEBUG) {
            // chrome://inspect on the PC can then see the page.
            WebView.setWebContentsDebuggingEnabled(true);
        }
    }

    private void bootstrap() {
        showOverlay("正在启动 Python 运行时…");
        final long started = System.currentTimeMillis();

        new Thread(() -> {
            try {
                String configPath = SettingsStore.file(getApplicationContext())
                        .getAbsolutePath();
                String dbPath = getApplicationContext()
                        .getDatabasePath("economical_agent.db").getAbsolutePath();
                final int port = PyServer.ensureStarted(
                        getApplicationContext(), configPath, dbPath);
                final long elapsed = System.currentTimeMillis() - started;
                Log.i(TAG, "python server ready on 127.0.0.1:" + port
                        + " after " + elapsed + " ms");
                runOnUiThread(() -> {
                    showOverlay("服务已就绪（" + elapsed + " ms），正在加载界面…");
                    web.loadUrl("http://127.0.0.1:" + port + "/?shell=android");
                });
            } catch (Throwable error) {
                Log.e(TAG, "python bootstrap failed", error);
                runOnUiThread(() -> showError("Python 启动失败：\n"
                        + Log.getStackTraceString(error)));
            }
        }, "py-bootstrap").start();
    }

    @Override
    protected void onResume() {
        super.onResume();
        // Returning from the settings screen with a new key/model: reload so the
        // chat header badge ("DeepSeek" vs "Rule engine") reflects the change.
        if (web != null && SettingsStore.consumeDirty()) {
            Log.i(TAG, "settings changed, reloading the page");
            web.reload();
        }
    }

    private void showOverlay(String message) {
        overlay.setVisibility(View.VISIBLE);
        spinner.setVisibility(View.VISIBLE);
        retry.setVisibility(View.GONE);
        status.setTextColor(getColor(R.color.text_muted));
        status.setText(message);
    }

    private void hideOverlay() {
        overlay.setVisibility(View.GONE);
    }

    private void showError(String message) {
        overlay.setVisibility(View.VISIBLE);
        spinner.setVisibility(View.GONE);
        retry.setVisibility(View.VISIBLE);
        status.setTextColor(getColor(R.color.danger));
        status.setText(message);
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
            return;
        }
        super.onBackPressed();
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        Log.i(TAG, "configuration changed: uiMode=" + newConfig.uiMode
                + " fontScale=" + newConfig.fontScale);
    }
}
