"""
Mesin download TikTok/Douyin (tanpa Kivy, jadi bisa dites di PC).
Diadaptasi dari perfectv28.py -- versi ringkas untuk Android.
"""
import os
import re
import shutil
import tempfile
import time
import random

# Android tidak punya CA bundle sistem yang dikenali Python -> pakai certifi.
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except Exception:
    pass

import yt_dlp

IS_ANDROID = "ANDROID_ARGUMENT" in os.environ

HOSTS = ("tiktok.com", "douyin.com", "iesdouyin.com")
URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&()*+,;=%]+", re.I)

# Error sementara (server TikTok ngambek / rate-limit) -> otomatis dicoba ulang.
RETRYABLE = [
    "Unable to extract universal data for rehydration",
    "Unable to extract webpage video data",
    "Unable to find video in feed",
    "Unexpected response from webpage request",
    "HTTP Error 429", "HTTP Error 500", "HTTP Error 502", "HTTP Error 503",
    "Read timed out", "Remote end closed connection", "Connection reset by peer",
]
IP_BLOCK = "Your IP address is blocked"

# Error permanen (video privat / dihapus / dibatasi) -> JANGAN diulang,
# langsung tandai gagal lalu lanjut ke link berikutnya.
PERMANENT = [
    "You do not have permission to view this post",
    "Video not available",
    "video is private",
    "This video is private",
    "has been removed",
    "Video unavailable",
    "status code 10204",
    "status code 10216",
    "status code 10222",
    "Unsupported URL",
]

MAX_ATTEMPTS = 4
RETRY_DELAY = 5            # detik x nomor percobaan (+ jitter)
IP_BLOCK_DELAY = 30        # cooldown khusus IP-block
IP_BLOCK_MAX_ATTEMPTS = 2  # IP-block: jangan nahan antrean terlalu lama
INITIAL_JITTER = (0.3, 1.5)
HARD_TIMEOUT = 300         # detik; batas keras per link supaya antrean tidak macet


def extract_links(text):
    """Ambil semua link TikTok/Douyin dari teks (teks share TikTok/Douyin biasanya ada kalimat di sekitar link)."""
    found = []
    for raw in URL_RE.findall(text or ""):
        url = raw.rstrip(".,;:!?)]}")
        host = url.split("//", 1)[1]
        for sep in "/?#:":
            host = host.split(sep, 1)[0]
        host = host.lower()
        if any(host == h or host.endswith("." + h) for h in HOSTS):
            found.append(url)
    return list(dict.fromkeys(found))


def public_dirs():
    """Folder tujuan akhir, berurutan (yang pertama dicoba dulu)."""
    dirs = []
    if IS_ANDROID:
        try:
            from android.storage import primary_external_storage_path
            dirs.append(os.path.join(primary_external_storage_path(), "Movies", "TikTokDL"))
        except Exception:
            pass
        try:
            from jnius import autoclass
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            ext = activity.getExternalFilesDir(None)
            if ext is not None:
                dirs.append(ext.getAbsolutePath())
        except Exception:
            pass
    else:
        dirs.append(os.path.abspath("downloads"))
    return dirs


def _scan_media(path):
    """Minta Android memindai file supaya muncul di Galeri."""
    if not IS_ANDROID:
        return
    try:
        from jnius import autoclass
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        scanner = autoclass("android.media.MediaScannerConnection")
        scanner.scanFile(activity, [path], None, None)
    except Exception:
        pass


def _unique(path):
    base, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(path):
        path = f"{base}_{n}{ext}"
        n += 1
    return path


def publish(path, out_dirs):
    """
    Pindahkan file dari folder kerja ke folder tujuan.
    Return (path_akhir, berhasil_ke_folder_publik).
    Nama yang diawali titik (hidden) diperbaiki -- masalah yang sama dengan v28.
    """
    name = os.path.basename(path).lstrip(".") or "video.mp4"
    for target in out_dirs:
        try:
            os.makedirs(target, exist_ok=True)
            dst = _unique(os.path.join(target, name))
            shutil.copyfile(path, dst)
            os.remove(path)
            _scan_media(dst)
            return dst, True
        except Exception:
            continue
    return path, False


class _Logger:
    """Mendeteksi pesan 'sudah ada di archive' dari yt-dlp."""

    def __init__(self):
        self.skipped = False

    def debug(self, msg):
        # Dilonggarkan: versi yt-dlp berbeda menulis "...recorded in archive"
        # atau "...recorded in the archive".
        if "already been recorded in" in msg or "has already been downloaded" in msg:
            self.skipped = True

    info = debug

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def _clean(err):
    err = re.sub(r"\x1b\[[0-9;]*m", "", err or "")
    err = err.replace("ERROR: ", "").strip()
    return err[:200] or "gagal tidak diketahui"


def _friendly(err):
    """Pesan singkat yang jelas untuk error permanen."""
    if "You do not have permission to view this post" in err:
        return "Video privat/dibatasi/dihapus (dilewati, cookies biasanya tidak membantu)"
    if "Unsupported URL" in err:
        return "Link tidak didukung"
    if any(s in err for s in PERMANENT):
        return "Video tidak tersedia / privat / dihapus"
    return err


def _is_permanent(err):
    return any(s in err for s in PERMANENT)


def _retryable(err):
    return IP_BLOCK in err or any(s in err for s in RETRYABLE)


def _opts(work_dir, archive_path, cookie, variant, logger, saved):
    def hook(d):
        if d.get("status") == "finished" and d.get("filename"):
            saved.append(d["filename"])

    opts = {
        "outtmpl": os.path.join(work_dir, "%(uploader).60s_%(id)s.%(ext)s"),
        # Android tidak punya ffmpeg -> pakai format tunggal (video+audio sudah
        # jadi satu di TikTok/Douyin), tidak perlu merge.
        "format": "best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extractor_args": {"tiktok": {"webpage_download": ["false" if variant % 2 else "true"]}},
        "logger": logger,
        "progress_hooks": [hook],
        "socket_timeout": 30,
        "retries": 3,
    }
    if archive_path:
        opts["download_archive"] = archive_path
    if cookie:
        opts["cookiefile"] = cookie
    return opts


def download_one(url, work_dir, out_dirs, archive_path=None, cookie_path=None):
    """
    Download satu link. Return (status, pesan, files)
      status: "OK" / "SKIP" / "FAIL"
      files : list (nama_file, ukuran_byte) -- hanya terisi kalau OK
    """
    os.makedirs(work_dir, exist_ok=True)

    # Salinan cookie sementara per job, supaya download paralel tidak
    # menulis ulang file cookie yang sama (penyebab corrupt di v28).
    tmp_cookie = None
    if cookie_path and os.path.exists(cookie_path):
        fd, tmp_cookie = tempfile.mkstemp(suffix=".txt", prefix="ck_", dir=work_dir)
        os.close(fd)
        shutil.copyfile(cookie_path, tmp_cookie)

    err = "gagal tidak diketahui"
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if attempt == 1:
                time.sleep(random.uniform(*INITIAL_JITTER))
            logger, saved = _Logger(), []
            opts = _opts(work_dir, archive_path, tmp_cookie, attempt - 1, logger, saved)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.extract_info(url, download=True)
            except Exception as e:
                err = _clean(str(e))
                if _is_permanent(err):
                    return "FAIL", _friendly(err), []
                limit = IP_BLOCK_MAX_ATTEMPTS if IP_BLOCK in err else MAX_ATTEMPTS
                if attempt < limit and _retryable(err):
                    base = IP_BLOCK_DELAY if IP_BLOCK in err else RETRY_DELAY
                    time.sleep(base * attempt + random.uniform(1, 4))
                    continue
                return "FAIL", _friendly(err), []

            files = list(dict.fromkeys(saved))
            if not files:
                if logger.skipped:
                    return "SKIP", "sudah pernah didownload", []
                return "FAIL", "selesai tanpa error tapi file tidak terbentuk", []

            names, infos = [], []
            for p in files:
                size = os.path.getsize(p) if os.path.exists(p) else 0
                dst, public_ok = publish(p, out_dirs)
                base = os.path.basename(dst)
                infos.append((base, size))
                names.append(base if public_ok else f"{dst} (tidak bisa ke folder publik)")
            return "OK", ", ".join(names), infos
        return "FAIL", _friendly(err), []
    finally:
        if tmp_cookie and os.path.exists(tmp_cookie):
            try:
                os.remove(tmp_cookie)
            except OSError:
                pass


def download_one_safe(url, work_dir, out_dirs, archive_path=None, cookie_path=None,
                      timeout=HARD_TIMEOUT):
    """
    Pembungkus download_one yang TIDAK PERNAH melempar error dan TIDAK PERNAH
    menggantung lebih dari `timeout` detik. Apa pun yang terjadi, hasilnya
    selalu (status, pesan, files) sehingga antrean bulk lanjut ke link berikutnya.
    """
    import threading
    box = {}

    def run():
        try:
            box["r"] = download_one(url, work_dir, out_dirs, archive_path, cookie_path)
        except BaseException as ex:  # termasuk SystemExit dari library
            box["r"] = ("FAIL", _friendly(_clean(str(ex) or repr(ex))), [])

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return "FAIL", f"timeout > {timeout} detik (dilewati)", []
    return box.get("r", ("FAIL", "gagal tidak diketahui", []))
