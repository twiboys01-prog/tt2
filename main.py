"""
TikTok / Douyin Downloader -- UI Kivy.

Single : copy link -> buka app -> langsung download.
Bulk   : link yang di-copy dikumpulkan di kotak, lalu tekan DOWNLOAD SEMUA.
"""
import os
import concurrent.futures

from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.core.clipboard import Clipboard
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.switch import Switch
from kivy.uix.textinput import TextInput
from kivy.utils import platform

import engine

MAX_WORKERS = 2  # paralel kecil = lebih jarang kena IP-block


def left_label(text):
    lbl = Label(text=text, halign="left", valign="middle")
    lbl.bind(size=lambda i, s: setattr(i, "text_size", s))
    return lbl


class DownloaderApp(App):
    title = "TikTok DL"

    # ---------- UI ----------
    def build(self):
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self.counts = {"pending": 0, "ok": 0, "skip": 0, "fail": 0}
        self.failed = []
        self.lines = []
        self.last_clip_marker = None
        self.work_dir = os.path.join(self.user_data_dir, "work")
        self.archive_path = os.path.join(self.user_data_dir, "archive.txt")
        self.cookie_path = os.path.join(self.user_data_dir, "cookies.txt")

        root = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))

        row = BoxLayout(size_hint_y=None, height=dp(44))
        row.add_widget(left_label("Mode BULK (kumpulkan dulu)"))
        self.sw_bulk = Switch(active=False, size_hint_x=None, width=dp(90))
        self.sw_bulk.bind(active=self.on_mode)
        row.add_widget(self.sw_bulk)
        root.add_widget(row)

        row = BoxLayout(size_hint_y=None, height=dp(44))
        row.add_widget(left_label("Lewati video yang sudah pernah didownload"))
        self.sw_archive = Switch(active=True, size_hint_x=None, width=dp(90))
        row.add_widget(self.sw_archive)
        root.add_widget(row)

        self.lbl_hint = left_label("")
        self.lbl_hint.size_hint_y = None
        self.lbl_hint.height = dp(48)
        root.add_widget(self.lbl_hint)

        self.box = TextInput(hint_text="Link (satu per baris) -- boleh tempel manual",
                             multiline=True, size_hint_y=None, height=dp(120))
        root.add_widget(self.box)

        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        row.add_widget(Button(text="TEMPEL LINK", on_release=self.on_paste))
        row.add_widget(Button(text="DOWNLOAD SEMUA", on_release=self.on_download_box))
        root.add_widget(row)

        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        row.add_widget(Button(text="ULANG GAGAL", on_release=self.on_retry_failed))
        row.add_widget(Button(text="KOSONGKAN KOTAK", on_release=lambda *_: setattr(self.box, "text", "")))
        row.add_widget(Button(text="COOKIES", on_release=self.open_cookies))
        root.add_widget(row)

        self.lbl_status = left_label("")
        self.lbl_status.size_hint_y = None
        self.lbl_status.height = dp(28)
        root.add_widget(self.lbl_status)

        self.log = Label(text="", size_hint_y=None, halign="left", valign="top", font_size=dp(13))
        self.log.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
                      texture_size=lambda i, ts: setattr(i, "height", ts[1]))
        self.scroll = ScrollView()
        self.scroll.add_widget(self.log)
        root.add_widget(self.scroll)

        self.on_mode()
        self.refresh_status()
        return root

    def on_start(self):
        if platform == "android":
            try:
                from android.permissions import request_permissions, Permission
                request_permissions([Permission.WRITE_EXTERNAL_STORAGE,
                                     Permission.READ_EXTERNAL_STORAGE])
            except Exception:
                pass
        dirs = engine.public_dirs()
        self.say("Hasil download: " + (dirs[0] if dirs else self.work_dir))
        Clock.schedule_interval(self.poll_clipboard, 1.0)

    def on_pause(self):
        return True  # wajib di Android supaya app tidak dimatikan saat pindah app

    def on_resume(self):
        self.poll_clipboard()

    def on_stop(self):
        self.pool.shutdown(wait=False, cancel_futures=True)

    def on_mode(self, *_):
        if self.sw_bulk.active:
            self.lbl_hint.text = ("MODE BULK: copy link satu per satu, link otomatis "
                                  "masuk ke kotak. Kalau sudah semua, tekan DOWNLOAD SEMUA.")
        else:
            self.lbl_hint.text = ("MODE SATU LINK: copy link di TikTok/Douyin, lalu buka app ini "
                                  "-- langsung didownload.")

    # ---------- log & status ----------
    @mainthread
    def say(self, msg):
        self.lines = (self.lines + [msg])[-150:]
        self.log.text = "\n".join(self.lines)
        Clock.schedule_once(lambda dt: setattr(self.scroll, "scroll_y", 0), 0.05)

    def refresh_status(self):
        c = self.counts
        self.lbl_status.text = (f"Antrian: {c['pending']} | Berhasil: {c['ok']} | "
                                f"Dilewati: {c['skip']} | Gagal: {c['fail']}")

    # ---------- clipboard ----------
    def _read_clip(self, only_if_new):
        """Return teks clipboard (atau None). Di Android pakai timestamp supaya
        tidak memicu toast 'app membaca clipboard' tiap detik."""
        try:
            if platform == "android":
                from jnius import autoclass
                activity = autoclass("org.kivy.android.PythonActivity").mActivity
                ctx = autoclass("android.content.Context")
                cm = activity.getSystemService(ctx.CLIPBOARD_SERVICE)
                desc = cm.getPrimaryClipDescription()
                if desc is None:
                    return None
                marker = desc.getTimestamp()
                if only_if_new and marker == self.last_clip_marker:
                    return None
                if not desc.hasMimeType("text/*"):
                    self.last_clip_marker = marker
                    return None
                clip = cm.getPrimaryClip()
                if clip is None or clip.getItemCount() == 0:
                    return None
                text = clip.getItemAt(0).coerceToText(activity)
                text = text.toString() if text is not None else ""
                if text:
                    self.last_clip_marker = marker
                return text or None
            text = Clipboard.paste() or ""
            if only_if_new and text == self.last_clip_marker:
                return None
            self.last_clip_marker = text
            return text or None
        except Exception:
            return None

    def poll_clipboard(self, *_):
        text = self._read_clip(only_if_new=True)
        if text:
            self.handle_text(text, manual=False)

    def on_paste(self, *_):
        text = self._read_clip(only_if_new=False)
        if text:
            self.handle_text(text, manual=True)
        else:
            self.say("Clipboard kosong.")

    def handle_text(self, text, manual):
        links = engine.extract_links(text)
        if not links:
            if manual:
                self.say("Clipboard tidak berisi link TikTok/Douyin.")
            return
        if self.sw_bulk.active:
            self.add_to_box(links)
        else:
            self.say(f"Link terdeteksi ({len(links)}), langsung download...")
            self.enqueue(links)

    def add_to_box(self, links):
        existing = engine.extract_links(self.box.text)
        new = [u for u in links if u not in existing]
        if not new:
            self.say("Link sudah ada di kotak.")
            return
        prefix = self.box.text.rstrip("\n") + "\n" if self.box.text.strip() else ""
        self.box.text = prefix + "\n".join(new) + "\n"
        self.say(f"+{len(new)} link masuk kotak (total {len(existing) + len(new)}).")

    # ---------- download ----------
    def on_download_box(self, *_):
        links = engine.extract_links(self.box.text)
        if not links:
            self.say("Kotak kosong / tidak ada link TikTok/Douyin.")
            return
        self.box.text = ""
        self.say(f"Mulai download {len(links)} link...")
        self.enqueue(links)

    def on_retry_failed(self, *_):
        if not self.failed:
            self.say("Tidak ada link gagal.")
            return
        links, self.failed = self.failed, []
        self.say(f"Mengulang {len(links)} link yang gagal...")
        self.enqueue(links)

    def enqueue(self, links):
        archive = self.archive_path if self.sw_archive.active else None
        for url in links:
            self.counts["pending"] += 1
            self.pool.submit(self._job, url, archive)
        self.refresh_status()

    def _job(self, url, archive):
        try:
            status, msg = engine.download_one(
                url, self.work_dir, engine.public_dirs(),
                archive_path=archive, cookie_path=self.cookie_path)
        except Exception as e:
            status, msg = "FAIL", str(e)[:200]
        self._job_done(url, status, msg)

    @mainthread
    def _job_done(self, url, status, msg):
        c = self.counts
        c["pending"] -= 1
        short = url.replace("https://", "").replace("http://", "")[:40]
        if status == "OK":
            c["ok"] += 1
            self.say(f"[BERHASIL] {msg}")
        elif status == "SKIP":
            c["skip"] += 1
            self.say(f"[DILEWATI] {short} -> {msg}")
        else:
            c["fail"] += 1
            self.failed.append(url)
            self.say(f"[GAGAL] {short} -> {msg}")
        self.refresh_status()
        if c["pending"] == 0:
            tail = " Ketuk ULANG GAGAL untuk coba lagi." if self.failed else ""
            self.say("Selesai." + tail)

    # ---------- cookies (opsional, untuk Douyin) ----------
    def open_cookies(self, *_):
        content = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        ti = TextInput(hint_text="Tempel ISI cookies.txt (format Netscape, termasuk baris "
                                 "'# Netscape HTTP Cookie File')", multiline=True)
        content.add_widget(ti)
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        saved = os.path.exists(self.cookie_path)
        pop = Popup(title="Cookies" + (" (sudah tersimpan)" if saved else " (belum ada)"),
                    content=content, size_hint=(0.95, 0.85))

        def save(*_):
            txt = ti.text.strip()
            if txt:
                with open(self.cookie_path, "w", encoding="utf-8") as f:
                    f.write(txt + "\n")
                self.say("Cookies disimpan.")
            pop.dismiss()

        def delete(*_):
            if os.path.exists(self.cookie_path):
                os.remove(self.cookie_path)
            self.say("Cookies dihapus.")
            pop.dismiss()

        row.add_widget(Button(text="SIMPAN", on_release=save))
        row.add_widget(Button(text="HAPUS COOKIES", on_release=delete))
        row.add_widget(Button(text="BATAL", on_release=lambda *_: pop.dismiss()))
        content.add_widget(row)
        pop.open()


if __name__ == "__main__":
    DownloaderApp().run()
