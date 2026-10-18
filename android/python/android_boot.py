"""Android entry point for CampusWallet.

The Android build runs the *same* Flask application the desktop runs, inside the
app process, bound to 127.0.0.1, and shows it in a WebView. This module is the
only glue:

* :func:`start` — read ``config.json`` (DeepSeek key / model / endpoint), map it
  onto the environment ``config.py`` reads, import the real ``app`` module and
  serve it on a loopback port. Returns the port to Kotlin.
* :func:`apply_settings_json` — same mapping without restarting the server:
  ``config`` is reloaded in place and ``llm_agent`` reads ``config.*`` on every
  call, so a newly typed key takes effect on the next chat.
* :func:`test_llm` — one real DeepSeek call, so the settings screen can prove the
  key/endpoint/model work instead of silently falling back to the rule engine.
* :func:`seed_demo` / :func:`reset_data` — the demo-data script and a DB reset,
  both already used by the desktop workflow.
* :func:`diagnostics` / :func:`diagnostics_json` — versions, page size, database
  path/size and the (secret-free) provider status.
* :func:`start_hello` — the M0 self-check page, kept as a troubleshooting entry
  point that does not touch the real application at all.

Everything here is Android-only: the desktop keeps using ``python app.py``.
"""
import contextlib
import importlib
import io
import json
import logging
import os
import platform
import sys
import threading

# Directory holding this module == the root of the packaged Python source set.
_module_dir = os.path.dirname(os.path.abspath(__file__))

_server = None
_log = logging.getLogger("android_boot")

DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"

_PACKAGES = ("flask", "werkzeug", "jinja2", "markupsafe")
_DATA_FILES = (
    "templates/index.html",
    "static/css/style.css",
    "static/css/android.css",
    "static/js/app.js",
    "static/js/i18n.js",
)


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
def read_settings(cfg_path):
    """Load config.json written by the settings screen; {} when absent/broken."""
    try:
        with open(cfg_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _apply_settings(settings, db_path):
    """Map config.json onto the environment variables config.py reads.

    The Android build talks to DeepSeek only: ``AI_PROVIDER`` is pinned and the
    generic ``AI_API_KEY`` is always removed, because config.resolve_provider()
    treats a lone generic key as "Claude" (the historical behaviour).
    """
    api_key = str(settings.get("api_key") or "").strip()
    model = str(settings.get("model") or "").strip() or DEFAULT_MODEL
    base_url = str(settings.get("base_url") or "").strip() or DEFAULT_BASE_URL
    context = settings.get("context", True)

    os.environ["AI_PROVIDER"] = "deepseek"
    os.environ["AI_MODEL"] = model
    os.environ["AI_BASE_URL"] = base_url.rstrip("/")
    os.environ["AI_CONTEXT"] = "true" if context else "false"
    if api_key:
        os.environ["DEEPSEEK_API_KEY"] = api_key
    else:
        os.environ.pop("DEEPSEEK_API_KEY", None)
    os.environ.pop("AI_API_KEY", None)

    if db_path:
        os.environ["ECONOMICAL_AGENT_DB"] = db_path
    os.environ["ECONOMICAL_AGENT_SHELL"] = "android"


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------
def _package_versions():
    """Versions actually shipped in the APK (not what the build venv has)."""
    from importlib import metadata

    found = {}
    for name in _PACKAGES:
        try:
            found[name] = metadata.version(name)
        except Exception:  # noqa: BLE001 - PackageNotFoundError and friends
            found[name] = "missing"
    return found


def _data_files():
    checked = {}
    for relative in _DATA_FILES:
        path = os.path.join(_module_dir, *relative.split("/"))
        try:
            checked[relative] = os.path.getsize(path)
        except OSError:
            checked[relative] = None
    return checked


def _page_size():
    try:
        return os.sysconf("SC_PAGESIZE")
    except (AttributeError, ValueError, OSError):
        return None


def diagnostics():
    """Everything the settings screen shows. Never contains the API key."""
    import sqlite3

    info = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "sqlite": sqlite3.sqlite_version,
        "page_size": _page_size(),
        "port": _server.server_port if _server else 0,
        "module_dir": _module_dir,
        "home": os.environ.get("HOME"),
        "packages": _package_versions(),
        "data_files": _data_files(),
        "db_path": os.environ.get("ECONOMICAL_AGENT_DB", ""),
        "db_size": None,
        "llm": {},
    }

    db_path = info["db_path"]
    if db_path and os.path.exists(db_path):
        try:
            info["db_size"] = os.path.getsize(db_path)
        except OSError:
            pass

    try:
        import llm_agent

        info["llm"] = llm_agent.provider_status()
    except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
        info["llm"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    return info


def diagnostics_json():
    return json.dumps(diagnostics(), ensure_ascii=False)


# ---------------------------------------------------------------------------
# server lifecycle
# ---------------------------------------------------------------------------
def _basic_logging():
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _bind(flask_app, preferred_port):
    """Bind 127.0.0.1, falling back to an ephemeral port when 5000 is taken."""
    from werkzeug.serving import make_server

    candidates = [preferred_port, 0] if preferred_port else [0]
    last_error = None
    for candidate in candidates:
        try:
            return make_server("127.0.0.1", candidate, flask_app, threaded=True)
        except OSError as exc:
            last_error = exc
            _log.warning("port %s unavailable (%s), trying the next one",
                         candidate, exc)
    raise RuntimeError("could not bind a loopback port: %s" % (last_error,))


def _serve(flask_app, preferred_port):
    global _server
    _server = _bind(flask_app, preferred_port)
    threading.Thread(target=_server.serve_forever, name="flask", daemon=True).start()
    return _server.server_port


def start(files_dir, cfg_path, db_path, preferred_port=5000):
    """Start the real application and return the port it listens on."""
    global _server
    if _server is not None:
        return _server.server_port

    _basic_logging()
    settings = read_settings(cfg_path)
    _apply_settings(settings, db_path)
    _log.info("starting app: files_dir=%s db=%s cfg=%s", files_dir, db_path, cfg_path)

    import app as app_module  # noqa: F401  — importing it creates the schema

    port = _serve(app_module.app, preferred_port)
    status = diagnostics()["llm"]
    _log.info("app ready on 127.0.0.1:%s provider=%s configured=%s model=%s",
              port, status.get("provider"), status.get("configured"),
              status.get("model"))
    return port


def apply_settings_json(cfg_path, db_path):
    """Apply a settings change to the running server (no restart needed)."""
    settings = read_settings(cfg_path)
    _apply_settings(settings, db_path)
    import config

    importlib.reload(config)
    status = diagnostics()["llm"]
    _log.info("settings applied: configured=%s model=%s",
              status.get("configured"), status.get("model"))
    return json.dumps({"ok": True, "llm": status}, ensure_ascii=False)


def test_llm():
    """One real API call, so a wrong key is visible instead of silently ignored."""
    import time

    result = {
        "ok": False,
        "provider": "",
        "model": "",
        "base_url": "",
        "latency_ms": 0,
        "reply": "",
        "error": "",
    }
    started = time.time()
    try:
        import llm_agent

        status = llm_agent.provider_status()
        result["provider"] = status.get("provider", "")
        result["model"] = status.get("model", "")
        result["base_url"] = status.get("base_url", "")
        provider = llm_agent.build_provider()
        turn = provider.complete(
            "You are a connectivity probe. Reply with exactly one word: pong",
            [{"role": "user", "content": "ping"}],
        )
        text = (turn.text or "").strip()
        result["reply"] = text[:300]
        result["ok"] = bool(text)
        if not text:
            result["error"] = "模型返回了空内容"
    except Exception as exc:  # noqa: BLE001 - surfaced to the settings screen
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        result["latency_ms"] = int((time.time() - started) * 1000)
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# demo data
# ---------------------------------------------------------------------------
def seed_demo():
    """Run seed_demo_data.main() — the same script the desktop workflow uses."""
    buffer = io.StringIO()
    try:
        import seed_demo_data

        with contextlib.redirect_stdout(buffer):
            seed_demo_data.main()
        return json.dumps(
            {"ok": True, "log": buffer.getvalue()[-1200:]}, ensure_ascii=False
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {
                "ok": False,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "log": buffer.getvalue()[-600:],
            },
            ensure_ascii=False,
        )


def reset_data():
    """Delete the database and rebuild the empty schema."""
    try:
        import app as app_module

        path = app_module.DB_PATH
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass
        app_module.init_db()
        _log.info("database reset: %s", path)
        return json.dumps(
            {"ok": True, "db_path": path, "detail": diagnostics()["llm"]},
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)},
            ensure_ascii=False,
        )


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
CSV_HEADER = ("发生时间", "类型", "分类", "资金池", "金额", "备注")
_TYPE_LABELS = {"income": "收入", "expense": "支出"}
_TIER_LABELS = {"survival": "生存", "buffer": "缓冲", "learning": "学习",
                "income": "收入"}


def export_csv():
    """Render the whole ledger as RFC-4180 CSV for the phone's share sheet.

    Returns the CSV text itself rather than writing a file: only Kotlin can hand a
    file to MediaStore without extra permissions, so it writes what this returns.
    The leading BOM makes Excel open the Chinese headers correctly.
    """
    import csv
    import sqlite3
    import time

    try:
        import app as app_module

        conn = sqlite3.connect(app_module.DB_PATH)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                'SELECT occurred_at, type, category, tier, amount, note '
                'FROM "Transaction" WHERE user_id = ? ORDER BY occurred_at, id',
                (app_module.DEFAULT_USER_ID,),
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)},
            ensure_ascii=False,
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for row in rows:
        writer.writerow([
            row["occurred_at"].replace("T", " "),
            _TYPE_LABELS.get(row["type"], row["type"]),
            row["category"] or "",
            _TIER_LABELS.get(row["tier"], row["tier"] or ""),
            "%.2f" % float(row["amount"]),
            (row["note"] or "").replace("\r", " ").replace("\n", " "),
        ])

    stamp = time.strftime("%Y%m%d-%H%M")
    _log.info("exported %d transactions as CSV", len(rows))
    return json.dumps(
        {
            "ok": True,
            "count": len(rows),
            "filename": "campuswallet_ledger_%s.csv" % stamp,
            "csv": "\ufeff" + buffer.getvalue(),
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# On-device self test (debug builds only)
# ---------------------------------------------------------------------------
# Test classes that cannot run on Android by design: the Claude-SDK compatibility
# tests need the `anthropic` package, which this build deliberately does not ship.
_ANDROID_EXCLUDED_TESTS = ("AnthropicCompatibilityTest",)


def run_tests():
    """Run the project's unittest suite on the phone.

    The tests ship only in debug builds as the ``selftest`` package (see
    android/tools/sync_python.ps1 -IncludeTests and the src/debug source set), so a
    release APK is unaffected. Chaquopy imports Python modules straight out of the
    APK and never writes them to disk, while ``unittest.discover`` needs a real
    directory - that is why the package is declared in
    ``chaquopy.extractPackages``: importing it first forces the extraction, and the
    directory of ``selftest.__file__`` is then a normal folder.

    Each test already runs against a throwaway SQLite file; the scratch directory
    is redirected to the app's own storage because ``tempfile.gettempdir()`` points
    somewhere read-only on Android.
    """
    import time
    import unittest

    try:
        import selftest

        tests_dir = os.path.dirname(os.path.abspath(selftest.__file__))
    except Exception as exc:  # noqa: BLE001 - release build, or extraction failed
        return json.dumps(
            {
                "ok": False,
                "error": "selftest 包不可用（%s: %s）：只有 debug 构建包含它"
                % (type(exc).__name__, exc),
            },
            ensure_ascii=False,
        )

    if not os.path.isdir(tests_dir):
        return json.dumps(
            {"ok": False, "error": "selftest 未解压到磁盘：%s" % tests_dir},
            ensure_ascii=False,
        )

    scratch = os.path.join(os.environ.get("HOME") or _module_dir, "selftest-tmp")
    try:
        os.makedirs(scratch, exist_ok=True)
    except OSError:
        scratch = _module_dir
    os.environ["ECONOMICAL_AGENT_TEST_TMP"] = scratch

    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)

    stream = io.StringIO()
    suite = unittest.TestLoader().discover(
        tests_dir, pattern="test_*.py", top_level_dir=tests_dir
    )

    # The Android build intentionally ships DeepSeek only and does not install the
    # `anthropic` SDK, so the Claude-SDK compatibility tests cannot import here.
    # They are excluded in the Android runner instead of the shared suite, which the
    # desktop still runs in full - and the exclusion is reported, not hidden.
    def walk(node):
        for item in node:
            if isinstance(item, unittest.TestSuite):
                for case in walk(item):
                    yield case
            else:
                yield item

    kept = unittest.TestSuite()
    excluded = []
    for case in walk(suite):
        test_id = case.id()
        if any(marker in test_id for marker in _ANDROID_EXCLUDED_TESTS):
            excluded.append(test_id)
        else:
            kept.addTest(case)
    suite = kept

    started = time.time()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    elapsed = time.time() - started

    problems = []
    for case, trace in list(result.failures) + list(result.errors):
        last_line = trace.strip().splitlines()[-1] if trace.strip() else ""
        problems.append("%s | %s" % (case, last_line))
    skipped = ["%s | %s" % (case, reason) for case, reason in result.skipped]

    summary = "Ran %d tests in %.2fs - %s" % (
        result.testsRun,
        elapsed,
        "OK" if result.wasSuccessful() else "FAILED",
    )
    payload = {
        "ok": result.wasSuccessful(),
        "summary": summary,
        "run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "excluded": len(excluded),
        "excluded_detail": excluded,
        "excluded_reason": "需要 anthropic SDK；Android 构建按设计只接 DeepSeek",
        "elapsed_s": round(elapsed, 2),
        "problems": problems[:20],
        "skipped_detail": skipped[:10],
        "scratch": scratch,
    }

    _log.info("self-test: %s", summary)
    # Printed verbosity=2 output goes to logcat under python.stdout.
    for line in stream.getvalue().splitlines():
        print(line, flush=True)
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# M0 self-check page (kept for troubleshooting; never imports the real app)
# ---------------------------------------------------------------------------
def _render(info):
    import html

    def row(key, value):
        return "<tr><th>%s</th><td>%s</td></tr>" % (
            html.escape(str(key)),
            html.escape(str(value)),
        )

    packages = "".join(row(name, version) for name, version in info["packages"].items())
    files = "".join(
        row(name, "缺失" if size is None else "%d bytes" % size)
        for name, size in info["data_files"].items()
    )
    missing = [name for name, size in info["data_files"].items() if size is None]
    verdict = "自检通过" if not missing else "有数据文件缺失"
    tone = "#21B573" if not missing else "#E5484D"
    basics = "".join(
        row(name, info[name])
        for name in ("python", "implementation", "sqlite", "page_size", "port",
                     "home", "module_dir", "db_path", "db_size")
    )
    llm = info.get("llm") or {}
    llm_rows = "".join(row(key, value) for key, value in llm.items())
    return """<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CampusWallet · M0</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin:0; padding:20px 16px 28px; background:#F5F6FA; color:#1B1D29;
         font-family:-apple-system,"Segoe UI",Roboto,sans-serif; }}
  h1 {{ margin:0 0 4px; font-size:20px; }}
  .badge {{ display:inline-block; margin:8px 0 16px; padding:4px 10px; border-radius:999px;
            font-size:13px; font-weight:600; color:#fff; background:{tone}; }}
  .card {{ background:#fff; border:1px solid #E6E8F0; border-radius:14px;
           padding:12px 14px; margin-bottom:12px; }}
  h2 {{ margin:0 0 8px; font-size:13px; letter-spacing:.04em; text-transform:uppercase;
        color:#6B6F80; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ text-align:left; padding:5px 0; vertical-align:top; }}
  th {{ width:38%; color:#6B6F80; font-weight:500; padding-right:12px; }}
  td {{ word-break:break-all; }}
  code {{ background:#EEF1F8; padding:1px 5px; border-radius:5px; }}
</style></head>
<body>
  <h1>CampusWallet M0</h1>
  <div class="badge">{verdict}</div>
  <div class="card"><h2>运行时</h2><table>{basics}</table></div>
  <div class="card"><h2>打包的依赖</h2><table>{packages}</table></div>
  <div class="card"><h2>数据文件（Flask 模板/静态资源）</h2><table>{files}</table></div>
  <div class="card"><h2>对话后端</h2><table>{llm_rows}</table></div>
  <div class="card"><h2>接口</h2>
    <p>同一份诊断信息的 JSON：<code>/healthz</code></p>
  </div>
</body></html>""".format(tone=tone, verdict=verdict, basics=basics,
                         packages=packages, files=files, llm_rows=llm_rows)


def start_hello(files_dir, db_path, preferred_port=5000):
    """Start the M0 diagnostics server (does not import the real application)."""
    global _server
    if _server is not None:
        return _server.server_port

    _basic_logging()
    os.environ.setdefault("ECONOMICAL_AGENT_DB", db_path)
    _log.info("hello mode: files_dir=%s db=%s", files_dir, db_path)

    from flask import Flask, jsonify

    flask_app = Flask(__name__)

    @flask_app.get("/healthz")
    def healthz():
        return jsonify(diagnostics())

    @flask_app.get("/")
    def index():
        return _render(diagnostics())

    port = _serve(flask_app, preferred_port)
    _log.info("M0 server ready on 127.0.0.1:%s", port)
    print(json.dumps(diagnostics(), ensure_ascii=False), flush=True)
    return port
