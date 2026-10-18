package com.campuswallet.demo;

import android.content.Context;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;

/**
 * The app's own settings file: {@code <app files>/config.json}.
 *
 * Only three knobs plus one flag. The DeepSeek key lives here (app-private
 * storage: readable by this app and root only) and is never compiled into the
 * APK - the file simply does not exist until the user types a key.
 */
public final class SettingsStore {

    public static final String FILE_NAME = "config.json";
    public static final String DEFAULT_MODEL = "deepseek-flash";
    public static final String DEFAULT_BASE_URL = "https://api.deepseek.com";

    private static volatile boolean dirty;

    private SettingsStore() {
    }

    public static File file(Context context) {
        return new File(context.getFilesDir(), FILE_NAME);
    }

    /** Current settings, with defaults filled in for anything missing. */
    public static Settings load(Context context) {
        Settings settings = new Settings();
        File file = file(context);
        if (!file.isFile()) {
            return settings;
        }
        try {
            JSONObject stored = new JSONObject(readText(file));
            settings.apiKey = stored.optString("api_key", "");
            settings.model = stored.optString("model", DEFAULT_MODEL);
            settings.baseUrl = stored.optString("base_url", DEFAULT_BASE_URL);
            settings.context = stored.optBoolean("context", true);
        } catch (Exception ignored) {
            // A half-written or hand-edited file falls back to defaults; the next
            // save rewrites it.
        }
        return settings;
    }

    public static void save(Context context, Settings settings) throws IOException {
        JSONObject json = new JSONObject();
        String text;
        try {
            json.put("api_key", settings.apiKey);
            json.put("model", settings.model);
            json.put("base_url", settings.baseUrl);
            json.put("context", settings.context);
            // toString(2) is the one call here that declares a checked exception.
            text = json.toString(2);
        } catch (Exception impossible) {
            throw new IOException("could not serialise settings", impossible);
        }
        try (FileOutputStream out = new FileOutputStream(file(context))) {
            out.write(text.getBytes("UTF-8"));
        }
        dirty = true;
    }

    /** True once, after settings changed: the caller should reload the WebView. */
    public static boolean consumeDirty() {
        boolean was = dirty;
        dirty = false;
        return was;
    }

    /**
     * Flag the WebView as stale without touching config.json: used after the demo
     * data was re-seeded or the database was reset, so returning to the app shows
     * the new numbers instead of the previous ones.
     */
    public static void markDirty() {
        dirty = true;
    }

    private static String readText(File file) throws IOException {
        try (InputStream in = new FileInputStream(file)) {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int read;
            while ((read = in.read(chunk)) != -1) {
                buffer.write(chunk, 0, read);
            }
            return buffer.toString("UTF-8");
        }
    }

    /** Mutable holder used by the settings screen. */
    public static final class Settings {
        public String apiKey = "";
        public String model = DEFAULT_MODEL;
        public String baseUrl = DEFAULT_BASE_URL;
        public boolean context = true;
    }
}
