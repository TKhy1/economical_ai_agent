package com.campuswallet.demo;

import android.app.Activity;
import android.os.Bundle;
import android.text.InputType;
import android.util.Log;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.util.concurrent.Callable;

/**
 * "选择接入 AI Agent" 的子页面：DeepSeek Key / 模型 / 接口地址，以及一次真实的
 * 连接测试。所有慢操作（Python 调用、网络）都在工作线程上跑，结果以 JSON 展示。
 */
public class AiSettingsActivity extends Activity {

    private static final String TAG = "CampusWallet";

    private EditText apiKeyField;
    private EditText modelField;
    private EditText baseUrlField;
    private CheckBox showKeyBox;
    private CheckBox contextBox;
    private TextView statusView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_ai_settings);
        UiInsets.applyEdgeToEdge(this);

        apiKeyField = findViewById(R.id.api_key);
        modelField = findViewById(R.id.model);
        baseUrlField = findViewById(R.id.base_url);
        showKeyBox = findViewById(R.id.show_key);
        contextBox = findViewById(R.id.context);
        statusView = findViewById(R.id.status);

        SettingsStore.Settings settings = SettingsStore.load(this);
        apiKeyField.setText(settings.apiKey);
        modelField.setText(settings.model);
        baseUrlField.setText(settings.baseUrl);
        contextBox.setChecked(settings.context);

        showKeyBox.setOnCheckedChangeListener((button, checked) -> apiKeyField.setInputType(
                InputType.TYPE_CLASS_TEXT | (checked
                        ? InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
                        : InputType.TYPE_TEXT_VARIATION_PASSWORD)));

        Button close = findViewById(R.id.close);
        Button save = findViewById(R.id.save);
        Button test = findViewById(R.id.test);
        close.setOnClickListener(v -> finish());
        save.setOnClickListener(v -> saveAndApply());
        test.setOnClickListener(v -> runTask("连接测试", PyServer::testLlm));

        runTask("当前状态", PyServer::diagnostics);
    }

    private void saveAndApply() {
        SettingsStore.Settings settings = new SettingsStore.Settings();
        settings.apiKey = apiKeyField.getText().toString().trim();
        settings.model = modelField.getText().toString().trim();
        settings.baseUrl = baseUrlField.getText().toString().trim();
        settings.context = contextBox.isChecked();

        try {
            SettingsStore.save(this, settings);
        } catch (Exception error) {
            Log.e(TAG, "could not save settings", error);
            statusView.setText("保存失败：" + error);
            return;
        }

        final String configPath = SettingsStore.file(this).getAbsolutePath();
        final String dbPath = getDatabasePath("economical_agent.db").getAbsolutePath();
        runTask("应用设置", () -> PyServer.applySettings(configPath, dbPath));
        Toast.makeText(this, "已保存", Toast.LENGTH_SHORT).show();
    }

    private void runTask(String label, Callable<String> task) {
        statusView.setText(label + " …");
        new Thread(() -> {
            String result;
            try {
                result = task.call();
            } catch (Throwable error) {
                Log.e(TAG, label + " failed", error);
                result = "{\"ok\": false, \"error\": " + JSONObject.quote(String.valueOf(error)) + "}";
            }
            final String text = label + "\n" + pretty(result);
            runOnUiThread(() -> statusView.setText(text));
        }, "ai-settings-task").start();
    }

    private static String pretty(String raw) {
        try {
            return new JSONObject(raw).toString(2);
        } catch (Exception ignored) {
            return raw;
        }
    }
}
