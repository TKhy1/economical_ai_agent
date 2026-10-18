package com.campuswallet.demo;

import android.app.Application;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

/**
 * Starts the embedded CPython runtime exactly once per process.
 *
 * Python.start() is called on the Java main thread on purpose: the thread that
 * starts the interpreter is treated as the interpreter's main thread, so it must
 * be a long-lived one. The Flask server itself is started later, from a worker
 * thread, and runs in a Python daemon thread.
 */
public class App extends Application {

    @Override
    public void onCreate() {
        super.onCreate();
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(this));
        }
    }
}
