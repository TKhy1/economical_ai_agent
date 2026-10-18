package com.campuswallet.demo;

import android.app.Activity;
import android.os.Build;
import android.view.View;
import android.view.WindowInsets;

/**
 * Android 15+ draws every activity edge to edge whether the app asks for it or
 * not, so both screens pad their content by the system bars and the IME.
 *
 * Without this the tab bar sits under the gesture handle and the chat input ends
 * up behind the keyboard.
 */
public final class UiInsets {

    private UiInsets() {
    }

    public static void applyEdgeToEdge(Activity activity) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            return;
        }
        activity.getWindow().setDecorFitsSystemWindows(false);
        activity.getWindow().getDecorView().setOnApplyWindowInsetsListener((view, insets) -> {
            android.graphics.Insets bars = insets.getInsets(
                    WindowInsets.Type.systemBars() | WindowInsets.Type.ime());
            view.setPadding(0, bars.top, 0, bars.bottom);
            return insets;
        });
    }

    /** Kept for callers that want the padding on an inner view instead. */
    public static void padWith(View view, WindowInsets insets) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            return;
        }
        android.graphics.Insets bars = insets.getInsets(
                WindowInsets.Type.systemBars() | WindowInsets.Type.ime());
        view.setPadding(0, bars.top, 0, bars.bottom);
    }
}
