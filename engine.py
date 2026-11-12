"""
Mesin download TikTok/Douyin (tanpa Kivy, jadi bisa dites di PC).
Diadaptasi dari perfectv28.py -- versi ringkas untuk Android.
"""
import json
import os
import re
import shutil
import ssl
import urllib.request
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


# Nama file hasil: <uploader>_<id>.<ext> (atau ..._<id>_1.<ext> kalau dulu bentrok)
_ID_IN_NAME = re.compile(r"(?:^|_)(\d{8,})(?=_|$)")


def _ids_in_name(filename):
    stem = os.path.splitext(os.path.basename(filename))[0]
    return _ID_IN_NAME.findall(stem)


def ids_in_folders(out_dirs):
    """Kumpulan ID video yang SUDAH ada di folder hasil (berdasarkan nama file)."""
    ids = set()
    for d in out_dirs:
        try:
            for fn in os.listdir(d):
                ids.update(_ids_in_name(fn))
        except OSError:
            continue
    return ids


def seed_archive(archive_path, out_dirs):
    """
    Isi archive dari isi folder hasil. Jadi 'lewati yang sudah pernah' tetap
    jalan walau archive.txt hilang (app di-uninstall/reinstall, data dihapus)
    atau videonya dulu diunduh dengan cara lain. Return jumlah baris baru.
    """
    if not archive_path:
        return 0
    ids = ids_in_folders(out_dirs)
    if not ids:
        return 0
    have = set()
    try:
        with open(archive_path, "r", encoding="utf-8") as f:
            have = {ln.strip() for ln in f if ln.strip()}
    except OSError:
        pass
    new = [f"{k} {i}" for i in sorted(ids) for k in ("tiktok", "douyin")
           if f"{k} {i}" not in have]
    if new:
        try:
            with open(archive_path, "a", encoding="utf-8") as f:
                f.write("\n".join(new) + "\n")
        except OSError:
            return 0
    return len(new)


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
    if "Fresh cookies" in err:
        return "yt-dlp: Douyin minta cookies segar"
    if "Unsupported URL" in err:
        return "Link tidak didukung"
    if any(s in err for s in PERMANENT):
        return "Video tidak tersedia / privat / dihapus"
    return err


def _is_permanent(err):
    return any(s in err for s in PERMANENT)


def _retryable(err):
    return IP_BLOCK in err or any(s in err for s in RETRYABLE)


# ====================== Douyin native (tanpa yt-dlp / cookies) ======================
# yt-dlp untuk Douyin sekarang selalu minta "fresh cookies" (extractor-nya tidak
# bisa membuat signature sendiri). Halaman share mobile iesdouyin.com tidak butuh
# cookies, jadi dipakai sebagai jalur utama untuk link Douyin.
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")
_ROUTER_RE = re.compile(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", re.S)
_VID_RE = re.compile(r"(?:/(?:video|note|slides)/|modal_id=|item_ids?=)(\d{8,})")


class DouyinError(Exception):
    def __init__(self, msg, permanent=False):
        super().__init__(msg)
        self.permanent = permanent


def is_douyin(url):
    host = url.split("//", 1)[-1]
    for sep in "/?#:":
        host = host.split(sep, 1)[0]
    host = host.lower()
    return any(host == h or host.endswith("." + h) for h in DOUYIN_HOSTS)


def _ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _open(url, headers=None, timeout=30):
    """GET dengan User-Agent mobile; ikut redirect. Mudah di-patch untuk tes."""
    h = {"User-Agent": UA_MOBILE, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    h.update(headers or {})
    return urllib.request.urlopen(urllib.request.Request(url, headers=h),
                                  timeout=timeout, context=_ssl_ctx())


def _get_text(url, headers=None):
    with _open(url, headers) as r:
        return r.geturl(), r.read().decode("utf-8", "replace")


def _item_from_html(html):
    m = _ROUTER_RE.search(html or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    for v in (data.get("loaderData") or {}).values():
        if not isinstance(v, dict):
            continue
        info = v.get("videoInfoRes") or v.get("noteInfoRes")
        if not isinstance(info, dict):
            continue
        items = info.get("item_list") or []
        if items:
            return items[0]
        if info.get("filter_list"):
            raise DouyinError("Video privat / dihapus / dibatasi", permanent=True)
    return None


def _douyin_item(url):
    """Return (video_id, item_dict) dari link Douyin apa pun (pendek maupun panjang)."""
    final, html = _get_text(url)
    m = _VID_RE.search(final) or _VID_RE.search(url)
    if not m:
        m = _VID_RE.search(html or "")
    if not m:
        raise DouyinError("ID video tidak ditemukan di link")
    vid = m.group(1)

    item = _item_from_html(html)
    if item is None:
        try:
            _, html = _get_text(f"https://www.iesdouyin.com/share/video/{vid}/")
            item = _item_from_html(html)
        except DouyinError:
            raise
        except Exception:
            item = None
    if item is None:
        # API lama sebagai cadangan terakhir
        try:
            _, txt = _get_text(f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={vid}")
            lst = (json.loads(txt) or {}).get("item_list") or []
            item = lst[0] if lst else None
        except Exception:
            item = None
    if item is None:
        raise DouyinError("data video tidak didapat (diblokir / dihapus?)")
    return vid, item


def _douyin_urls(item):
    video = item.get("video") or {}
    pa = video.get("play_addr") or {}
    urls = [u.replace("playwm", "play") for u in (pa.get("url_list") or []) if u]
    uri = pa.get("uri") or video.get("vid")
    if uri:
        urls.append(f"https://aweme.snssdk.com/aweme/v1/play/?video_id={uri}&ratio=1080p&line=0")
    return list(dict.fromkeys(urls))


def _safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", s or "").strip("._")
    return s[:60] or "douyin"


def _archive_has(archive_path, key):
    if not archive_path:
        return False
    try:
        with open(archive_path, "r", encoding="utf-8") as f:
            return any(ln.strip() == key for ln in f)
    except OSError:
        return False


def _archive_add(archive_path, key):
    if not archive_path:
        return
    try:
        with open(archive_path, "a", encoding="utf-8") as f:
            f.write(key + "\n")
    except OSError:
        pass


def douyin_download(url, work_dir, archive_path=None):
    """
    Unduh video Douyin tanpa watermark. Return (files, skipped).
    Raise DouyinError kalau gagal.
    """
    vid, item = _douyin_item(url)
    key = f"douyin {vid}"
    if _archive_has(archive_path, key):
        return [], True

    urls = _douyin_urls(item)
    if not urls:
        if item.get("images"):
            raise DouyinError("Postingan foto/slideshow belum didukung", permanent=True)
        raise DouyinError("link video tidak ada di data Douyin")

    author = item.get("author") or {}
    nick = _safe_name(author.get("nickname") or author.get("unique_id") or author.get("short_id"))
    os.makedirs(work_dir, exist_ok=True)
    dst = os.path.join(work_dir, f"{nick}_{vid}.mp4")
    part = dst + ".part"

    last = "unduhan gagal"
    for u in urls:
        try:
            total = 0
            with _open(u, {"Referer": "https://www.douyin.com/"}, timeout=30) as r, \
                    open(part, "wb") as f:
                head = b""
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    if len(head) < 16:
                        head += chunk[:16]
                    f.write(chunk)
                    total += len(chunk)
            if total < 10_000 or b"ftyp" not in head:
                raise DouyinError("file bukan video yang valid")
            os.replace(part, dst)
            _archive_add(archive_path, key)
            return [dst], False
        except Exception as e:
            last = _clean(str(e) or repr(e))
            try:
                os.remove(part)
            except OSError:
                pass
    raise DouyinError(f"unduh gagal: {last}")


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


def _finalize(files, out_dirs, archive_path):
    """File sudah jadi di folder kerja -> pindahkan ke folder hasil. Return (status, pesan, infos)."""
    names, infos = [], []
    # Pengaman terakhir: kalau "lewati" aktif dan video itu ternyata sudah
    # ada di folder hasil, buang file baru (jangan bikin salinan _1.mp4).
    known = ids_in_folders(out_dirs) if archive_path else set()
    dup = 0
    for p in files:
        ids = _ids_in_name(p)
        if known and ids and ids[-1] in known:
            try:
                os.remove(p)
            except OSError:
                pass
            dup += 1
            continue
        size = os.path.getsize(p) if os.path.exists(p) else 0
        dst, public_ok = publish(p, out_dirs)
        base = os.path.basename(dst)
        infos.append((base, size))
        names.append(base if public_ok else f"{dst} (tidak bisa ke folder publik)")
    if not infos and dup:
        return "SKIP", "sudah ada di folder hasil", []
    return "OK", ", ".join(names), infos


def download_one(url, work_dir, out_dirs, archive_path=None, cookie_path=None):
    """
    Download satu link. Return (status, pesan, files)
      status: "OK" / "SKIP" / "FAIL"
      files : list (nama_file, ukuran_byte) -- hanya terisi kalau OK
    """
    os.makedirs(work_dir, exist_ok=True)

    # Douyin: jalur native dulu (yt-dlp minta "fresh cookies" untuk Douyin).
    native_err = None
    if is_douyin(url):
        try:
            files, skipped = douyin_download(url, work_dir, archive_path)
            if skipped:
                return "SKIP", "sudah pernah didownload", []
            return _finalize(files, out_dirs, archive_path)
        except DouyinError as e:
            native_err = str(e)[:80]
            if e.permanent:
                return "FAIL", native_err, []
        except Exception as e:
            native_err = _clean(str(e) or repr(e))[:80]

    def fail(msg):
        # kalau jalur native Douyin sudah gagal, tampilkan alasannya lebih dulu
        return "FAIL", (f"{native_err} | {msg}" if native_err else msg), []

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
                    return fail(_friendly(err))
                limit = IP_BLOCK_MAX_ATTEMPTS if IP_BLOCK in err else MAX_ATTEMPTS
                if attempt < limit and _retryable(err):
                    base = IP_BLOCK_DELAY if IP_BLOCK in err else RETRY_DELAY
                    time.sleep(base * attempt + random.uniform(1, 4))
                    continue
                return fail(_friendly(err))

            files = list(dict.fromkeys(saved))
            if not files:
                if logger.skipped:
                    return "SKIP", "sudah pernah didownload", []
                return fail("selesai tanpa error tapi file tidak terbentuk")
            return _finalize(files, out_dirs, archive_path)
        return fail(_friendly(err))
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
