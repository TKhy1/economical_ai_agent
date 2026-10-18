package com.campuswallet.demo;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Typeface;
import android.util.Log;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.util.concurrent.Callable;

/**
 * 网页「设置」Tab 里那组本机按钮的原生实现：导出 CSV、灌入演示数据、清空数据、
 * 诊断信息。都由 {@link WebBridge} 从页面里触发，结果用原生对话框展示。
 */
final class NativeActions {

    private static final String TAG = "CampusWallet";

    private NativeActions() {
    }

    // ---- export ------------------------------------------------------------
    static void exportCsv(Activity activity) {
        Toast.makeText(activity, R.string.export_running, Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            try {
                JSONObject result = new JSONObject(PyServer.exportCsv());
                if (!result.optBoolean("ok")) {
                    final String error = result.optString("error", "unknown");
                    post(activity, () -> alert(activity, activity.getString(R.string.row_export_csv), error));
                    return;
                }
                String filename = result.getString("filename");
                String csv = result.getString("csv");
                int count = result.optInt("count");
                CsvExporter.Result saved = CsvExporter.save(
                        activity.getApplicationContext(), filename, csv);
                post(activity, () -> showExportResult(activity, saved, count));
            } catch (Throwable error) {
                Log.e(TAG, "csv export failed", error);
                post(activity, () -> alert(activity,
                        activity.getString(R.string.row_export_csv), String.valueOf(error)));
            }
        }, "csv-export").start();
    }

    private static void showExportResult(Activity activity, CsvExporter.Result saved, int count) {
        String message = activity.getString(R.string.export_done, count) + "\n\n"
                + activity.getString(R.string.export_path, saved.location);
        AlertDialog.Builder builder = new AlertDialog.Builder(activity)
                .setTitle(R.string.row_export_csv)
                .setMessage(message)
                .setPositiveButton(android.R.string.ok, null);
        if (saved.canShare()) {
            builder.setNegativeButton(R.string.export_share, (dialog, which) ->
                    activity.startActivity(CsvExporter.shareIntent(saved,
                            activity.getString(R.string.row_export_csv))));
        }
        builder.show();
    }

    // ---- demo data ---------------------------------------------------------
    static void seedDemo(Activity activity) {
        confirm(activity, activity.getString(R.string.row_seed_confirm), () -> {
            SettingsStore.markDirty();   // 回到 WebView 时按新数据重载
            runReport(activity, activity.getString(R.string.row_seed), PyServer::seedDemo);
        });
    }

    static void resetData(Activity activity) {
        confirm(activity, activity.getString(R.string.row_reset_confirm), () -> {
            SettingsStore.markDirty();
            runReport(activity, activity.getString(R.string.row_reset), PyServer::resetData);
        });
    }

    private static void runReport(Activity activity, String label, Callable<String> task) {
        new Thread(() -> {
            String message;
            try {
                JSONObject result = new JSONObject(task.call());
                message = result.optBoolean("ok")
                        ? activity.getString(R.string.action_done)
                        : result.optString("error", "unknown");
            } catch (Throwable error) {
                Log.e(TAG, label + " failed", error);
                message = String.valueOf(error);
            }
            final String text = message;
            post(activity, () -> alert(activity, label, text));
        }, "native-action").start();
    }

    // ---- diagnostics -------------------------------------------------------
    static void showDiagnostics(Activity activity) {
        new Thread(() -> {
            String body;
            try {
                body = new JSONObject(PyServer.diagnostics()).toString(2);
            } catch (Throwable error) {
                body = String.valueOf(error);
            }
            final String text = body;
            post(activity, () -> {
                TextView view = new TextView(activity);
                view.setText(text);
                view.setTextSize(11f);
                view.setTypeface(Typeface.MONOSPACE);
                view.setTextIsSelectable(true);
                int pad = Math.round(16 * activity.getResources().getDisplayMetrics().density);
                view.setPadding(pad, pad, pad, pad);
                ScrollView scroll = new ScrollView(activity);
                scroll.addView(view);
                new AlertDialog.Builder(activity)
                        .setTitle(R.string.row_diagnostics)
                        .setView(scroll)
                        .setPositiveButton(android.R.string.ok, null)
                        .show();
            });
        }, "diagnostics").start();
    }

    // ---- plumbing ----------------------------------------------------------
    private static void post(Activity activity, Runnable action) {
        activity.runOnUiThread(() -> {
            if (!activity.isFinishing() && !activity.isDestroyed()) {
                action.run();
            }
        });
    }

    private static void confirm(Activity activity, String message, Runnable action) {
        activity.runOnUiThread(() -> new AlertDialog.Builder(activity)
                .setMessage(message)
                .setNegativeButton(android.R.string.cancel, null)
                .setPositiveButton(android.R.string.ok, (dialog, which) -> action.run())
                .show());
    }

    private static void alert(Activity activity, String title, String message) {
        new AlertDialog.Builder(activity)
                .setTitle(title)
                .setMessage(message)
                .setPositiveButton(android.R.string.ok, null)
                .show();
    }
}
