package com.campuswallet.demo;

import android.content.Context;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

import java.io.File;

/**
 * Bridge to the embedded Python side.
 *
 * Every method here must be called off the UI thread: the first call imports Flask
 * and starts the server (a few hundred milliseconds on this device), and the
 * settings helpers make real network calls.
 *
 * Return values are JSON strings produced by android_boot.py, so the Kotlin/Java
 * side does not depend on Chaquopy's container conversions.
 */
public final class PyServer {

    private static volatile int port;

    private PyServer() {
    }

    public static synchronized int ensureStarted(Context context, String configPath,
                                                 String dbPath) {
        if (port > 0) {
            return port;
        }
        ensureDirectory(context.getFilesDir());
        ensureDirectory(new File(dbPath).getParentFile());

        PyObject boot = Python.getInstance().getModule("android_boot");
        PyObject bound = boot.callAttr(
                "start", context.getFilesDir().getAbsolutePath(), configPath, dbPath, 5000);
        port = bound.toInt();
        return port;
    }

    public static String applySettings(String configPath, String dbPath) {
        return boot().callAttr("apply_settings_json", configPath, dbPath).toString();
    }

    public static String testLlm() {
        return boot().callAttr("test_llm").toString();
    }

    public static String seedDemo() {
        return boot().callAttr("seed_demo").toString();
    }

    public static String resetData() {
        return boot().callAttr("reset_data").toString();
    }

    public static String diagnostics() {
        return boot().callAttr("diagnostics_json").toString();
    }

    /** The whole ledger as CSV text; Kotlin writes it to the pictures folder. */
    public static String exportCsv() {
        return boot().callAttr("export_csv").toString();
    }

    /**
     * Runs the packaged unittest suite on the device. Debug builds only: the
     * tests/ directory is not part of a release APK.
     */
    public static String runTests() {
        return boot().callAttr("run_tests").toString();
    }

    public static int currentPort() {
        return port;
    }

    private static PyObject boot() {
        return Python.getInstance().getModule("android_boot");
    }

    private static void ensureDirectory(File directory) {
        if (directory != null && !directory.exists()) {
            //noinspection ResultOfMethodCallIgnored
            directory.mkdirs();
        }
    }
}
