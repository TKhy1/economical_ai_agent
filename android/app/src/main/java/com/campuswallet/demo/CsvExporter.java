package com.campuswallet.demo;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;

/**
 * Writes the ledger CSV to a folder the user can actually find and share.
 *
 * Android 10+ creates the file through MediaStore, which needs no permission and
 * hands back the content:// URI the share sheet wants. The platform only allows
 * app-created non-media files in <b>Download</b> and <b>Documents</b> — asking for
 * Pictures is rejected with "Primary directory Pictures not allowed for
 * content://media/external_primary/file", which is why the album folder (相册)
 * cannot hold a CSV at all. The first entry that MediaStore accepts wins; if the
 * device refuses both, the app-private Pictures directory is used instead.
 */
public final class CsvExporter {

    /** Preferred locations, most discoverable first. */
    private static final String[] MEDIASTORE_DIRS = {
            "Download/CampusWallet",
            "Documents/CampusWallet",
    };

    public static final class Result {
        public final Uri uri;
        public final String location;

        Result(Uri uri, String location) {
            this.uri = uri;
            this.location = location;
        }

        public boolean canShare() {
            return uri != null;
        }
    }

    private CsvExporter() {
    }

    public static Result save(Context context, String filename, String content)
            throws IOException {
        byte[] bytes = content.getBytes("UTF-8");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            for (String relativeDir : MEDIASTORE_DIRS) {
                try {
                    return saveWithMediaStore(context, relativeDir, filename, bytes);
                } catch (IllegalArgumentException rejected) {
                    // "Primary directory ... not allowed": try the next location.
                }
            }
        }
        return saveToAppStorage(context, filename, bytes);
    }

    private static Result saveWithMediaStore(Context context, String relativeDir,
                                             String filename, byte[] bytes)
            throws IOException {
        ContentResolver resolver = context.getContentResolver();
        ContentValues values = new ContentValues();
        values.put(MediaStore.MediaColumns.DISPLAY_NAME, filename);
        values.put(MediaStore.MediaColumns.MIME_TYPE, "text/csv");
        values.put(MediaStore.MediaColumns.RELATIVE_PATH, relativeDir);
        values.put(MediaStore.MediaColumns.IS_PENDING, 1);

        Uri collection = MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY);
        Uri uri = resolver.insert(collection, values);
        if (uri == null) {
            throw new IOException("MediaStore 拒绝创建文件（" + relativeDir + " 不可写）");
        }
        try (OutputStream out = resolver.openOutputStream(uri)) {
            if (out == null) {
                throw new IOException("无法打开输出流");
            }
            out.write(bytes);
            out.flush();
        } catch (IOException error) {
            resolver.delete(uri, null, null);
            throw error;
        }

        ContentValues published = new ContentValues();
        published.put(MediaStore.MediaColumns.IS_PENDING, 0);
        resolver.update(uri, published, null, null);
        return new Result(uri, humanDirectory(relativeDir) + "/" + filename);
    }

    private static Result saveToAppStorage(Context context, String filename, byte[] bytes)
            throws IOException {
        File externalPictures = context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
        if (externalPictures == null) {
            throw new IOException("外部存储不可用");
        }
        File dir = new File(externalPictures, "CampusWallet");
        if (!dir.exists() && !dir.mkdirs()) {
            throw new IOException("无法创建目录：" + dir);
        }
        File target = new File(dir, filename);
        try (FileOutputStream out = new FileOutputStream(target)) {
            out.write(bytes);
        }
        return new Result(null, target.getAbsolutePath());
    }

    private static String humanDirectory(String relativeDir) {
        if (relativeDir.startsWith("Download")) {
            return "下载/CampusWallet";
        }
        if (relativeDir.startsWith("Documents")) {
            return "文档/CampusWallet";
        }
        return relativeDir;
    }

    public static Intent shareIntent(Result result, String title) {
        Intent send = new Intent(Intent.ACTION_SEND);
        send.setType("text/csv");
        if (result.uri != null) {
            send.putExtra(Intent.EXTRA_STREAM, result.uri);
            send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        }
        return Intent.createChooser(send, title);
    }
}
