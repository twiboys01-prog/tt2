"""
TikTok / Douyin Downloader -- UI Kivy.

Satu link : copy link -> buka app -> link otomatis terisi & langsung didownload.
Bulk      : link yang di-copy dikumpulkan di kotak, lalu tekan "Download Semua".
"""
import json
import os
import time
import concurrent.futures

from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.factory import Factory
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle, Triangle
from kivy.metrics import dp, sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.recycleboxlayout import RecycleBoxLayout
from kivy.uix.recycleview import RecycleView
from kivy.uix.recycleview.views import RecycleDataViewBehavior
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.switch import Switch
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.widget import Widget
from kivy.utils import platform

import engine

MAX_WORKERS = 2  # paralel kecil = lebih jarang kena IP-block

# ---------- warna ----------
PINK = (0.996, 0.173, 0.333, 1)
PINK_SOFT = (1.0, 0.925, 0.945, 1)
INK = (0.07, 0.07, 0.10, 1)
GRAY = (0.45, 0.47, 0.55, 1)
BG = (0.935, 0.94, 0.962, 1)
SOFT = (0.94, 0.945, 0.965, 1)
WHITE = (1, 1, 1, 1)
GREEN = (0.10, 0.60, 0.35, 1)
RED = (0.85, 0.20, 0.25, 1)
TEAL, TEAL_SOFT = (0.10, 0.65, 0.60, 1), (0.88, 0.97, 0.95, 1)
PURPLE, PURPLE_SOFT = (0.45, 0.35, 0.80, 1), (0.93, 0.91, 0.98, 1)
BLUE, BLUE_SOFT = (0.20, 0.55, 0.85, 1), (0.90, 0.95, 0.99, 1)


# ====================== clipboard (Android) ======================
# Dibaca lewat UI-thread Android (cara yang sama dipakai Kivy sendiri),
# hasilnya dititip lewat dict ini lalu diambil oleh poll di thread Kivy.
CLIP = {"marker": None, "text": "", "new": False, "manual": False, "err": None}


def _deliver(text, manual):
    CLIP["text"], CLIP["manual"], CLIP["new"] = text or "", manual, True


if platform == "android":
    from android.runnable import run_on_ui_thread

    @run_on_ui_thread
    def android_read_clip(manual):
        try:
            from jnius import autoclass
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            ctx = autoclass("android.content.Context")
            cm = activity.getSystemService(ctx.CLIPBOARD_SERVICE)
            desc = cm.getPrimaryClipDescription()
            if desc is None:                      # clipboard kosong
                if manual:
                    _deliver("", True)
                return
            marker = desc.getTimestamp()
            # cek timestamp dulu (tidak memicu toast "app membaca clipboard")
            if not manual and marker == CLIP["marker"]:
                return
            if not desc.hasMimeType("text/*"):    # bukan teks (gambar dll)
                CLIP["marker"] = marker
                if manual:
                    _deliver("", True)
                return
            clip = cm.getPrimaryClip()
            if clip is None or clip.getItemCount() == 0:
                # Android 10+: baru boleh baca kalau app sudah punya fokus.
                # Marker sengaja TIDAK diupdate -> dicoba lagi di poll berikutnya.
                if manual:
                    _deliver("", True)
                return
            cs = clip.getItemAt(0).coerceToText(activity)
            text = cs.toString() if cs is not None else ""
            CLIP["marker"] = marker
            _deliver(text, manual)
        except Exception as e:
            CLIP["err"] = repr(e)


# ====================== widget kecil ======================
def T(text, size=14, color=INK, bold=False, halign="left", **kw):
    lbl = Label(text=text, font_size=sp(size), color=color, bold=bold,
                halign=halign, valign="middle", markup=kw.pop("markup", False), **kw)
    lbl.bind(size=lambda i, s: setattr(i, "text_size", s))
    return lbl


class Card(BoxLayout):
    def __init__(self, bg=WHITE, radius=dp(20), **kw):
        super().__init__(**kw)
        with self.canvas.before:
            Color(*bg)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[radius])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._rect.pos, self._rect.size = self.pos, self.size


class RoundButton(Button):
    def __init__(self, bg=PINK, fg=WHITE, radius=dp(26), **kw):
        kw.setdefault("bold", True)
        super().__init__(background_normal="", background_down="",
                         background_color=(0, 0, 0, 0), color=fg, **kw)
        self.bg = bg
        with self.canvas.before:
            self._col = Color(*bg)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[radius])
        self.bind(pos=self._sync, size=self._sync, state=self._sync)

    def _sync(self, *_):
        self._rect.pos, self._rect.size = self.pos, self.size
        r, g, b, a = self.bg
        k = 0.85 if self.state == "down" else 1
        self._col.rgba = (r * k, g * k, b * k, a)


class Seg(ToggleButton):
    """Tombol pilihan (Satu Link | Bulk)."""

    def __init__(self, **kw):
        super().__init__(background_normal="", background_down="",
                         background_color=(0, 0, 0, 0), bold=True, **kw)
        with self.canvas.before:
            self._col = Color(*SOFT)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(18)])
        self.bind(pos=self._sync, size=self._sync, state=self._sync)
        self._sync()

    def _sync(self, *_):
        self._rect.pos, self._rect.size = self.pos, self.size
        on = self.state == "down"
        self._col.rgba = PINK if on else SOFT
        self.color = WHITE if on else GRAY


class Icon(Widget):
    """Ikon sederhana yang digambar dengan canvas (tanpa file gambar)."""

    def __init__(self, kind, tint=PINK, bg=PINK_SOFT, **kw):
        super().__init__(**kw)
        self.kind, self.tint, self.bgc = kind, tint, bg
        self.bind(pos=self.redraw, size=self.redraw)

    def redraw(self, *_):
        self.canvas.clear()
        x, y = self.pos
        w, h = self.size
        s = min(w, h)
        cx, cy, r = x + w / 2, y + h / 2, s / 2
        lw = dp(1.6)
        with self.canvas:
            if self.kind == "logo":
                Color(0.05, 0.05, 0.08, 1)
                RoundedRectangle(pos=(cx - r, cy - r), size=(s, s), radius=[dp(14)])
                Color(0.15, 0.85, 0.85, 1)
                Triangle(points=[cx - r * .22, cy - r * .35, cx - r * .22, cy + r * .35, cx + r * .38, cy])
                Color(*PINK)
                Triangle(points=[cx - r * .14, cy - r * .35, cx - r * .14, cy + r * .35, cx + r * .46, cy])
                Color(1, 1, 1, 1)
                Triangle(points=[cx - r * .18, cy - r * .35, cx - r * .18, cy + r * .35, cx + r * .42, cy])
                return
            Color(*self.bgc)
            Ellipse(pos=(cx - r, cy - r), size=(s, s))
            Color(*self.tint)
            if self.kind == "bolt":
                Line(points=[cx + r * .12, cy + r * .5, cx - r * .25, cy - r * .02,
                             cx + r * .05, cy - r * .02, cx - r * .12, cy - r * .5], width=lw)
            elif self.kind == "nowm":
                Line(circle=(cx, cy, r * .42), width=lw)
                Line(points=[cx - r * .3, cy + r * .3, cx + r * .3, cy - r * .3], width=lw)
            elif self.kind == "phone":
                Line(rounded_rectangle=(cx - r * .28, cy - r * .45, r * .56, r * .9, dp(3)), width=lw)
            elif self.kind in ("hd", "play"):
                Line(rounded_rectangle=(cx - r * .46, cy - r * .33, r * .92, r * .66, dp(3)), width=lw)
                Triangle(points=[cx - r * .1, cy - r * .16, cx - r * .1, cy + r * .16, cx + r * .17, cy])


class NavButton(ButtonBehavior, BoxLayout):
    def __init__(self, text, **kw):
        super().__init__(orientation="vertical", **kw)
        anchor = AnchorLayout(size_hint_y=None, height=dp(3), anchor_x="center")
        self.mark = Widget(size_hint=(None, None), size=(dp(28), dp(3)))
        with self.mark.canvas:
            self._col = Color(0, 0, 0, 0)
            self._rect = RoundedRectangle(pos=self.mark.pos, size=self.mark.size, radius=[dp(2)])
        self.mark.bind(pos=self._sync, size=self._sync)
        anchor.add_widget(self.mark)
        self.lbl = Label(text=text, font_size=sp(12), color=GRAY)
        self.add_widget(anchor)
        self.add_widget(self.lbl)

    def _sync(self, *_):
        self._rect.pos, self._rect.size = self.mark.pos, self.mark.size

    def set_active(self, on):
        self.lbl.color = PINK if on else GRAY
        self.lbl.bold = on
        self._col.rgba = PINK if on else (0, 0, 0, 0)


class ItemRow(RecycleDataViewBehavior, Card):
    """Satu baris download. Dipakai ulang (recycle) supaya riwayat panjang tetap ringan."""

    def __init__(self, **kw):
        super().__init__(orientation="horizontal", padding=dp(10), spacing=dp(10), **kw)
        self.it = None
        self.add_widget(Icon("play", PINK, PINK_SOFT, size_hint=(None, None),
                             size=(dp(46), dp(46)), pos_hint={"center_y": .5}))
        col = BoxLayout(orientation="vertical")
        self.t1 = T("", 14, INK, True, shorten=True, shorten_from="right")
        self.t2 = T("", 11, GRAY, shorten=True, shorten_from="right")
        col.add_widget(self.t1)
        col.add_widget(self.t2)
        self.add_widget(col)
        right = AnchorLayout(size_hint_x=None, width=dp(72))
        self.stat = T("", 12, GREEN, True, "right")
        self.btn = RoundButton(text="Ulangi", font_size=sp(12), size_hint=(None, None),
                               size=(dp(70), dp(34)), radius=dp(17),
                               on_release=self._retry)
        right.add_widget(self.stat)
        right.add_widget(self.btn)
        self.add_widget(right)

    def _retry(self, *_):
        if self.it is not None:
            App.get_running_app().retry(self.it)

    def apply(self, it):
        self.it = it
        st = it["status"]
        self.t1.text = it["name"]
        self.t2.text = it["detail"]
        self.t2.color = {"fail": RED, "ok": GRAY, "skip": GRAY, "run": PINK}[st]
        self.btn.opacity = 1 if st == "fail" else 0
        self.btn.disabled = st != "fail"
        self.stat.text = {"ok": "Selesai", "run": "..."}.get(st, "")
        self.stat.color = GREEN if st == "ok" else PINK

    def refresh_view_attrs(self, rv, index, data):
        self.apply(data["it"])
        return super().refresh_view_attrs(rv, index, data)


Factory.register("ItemRow", cls=ItemRow)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def fmt_size(n):
    return f"{n / 1048576:.1f} MB" if n else ""


def short_url(u):
    return u.replace("https://", "").replace("http://", "").replace("www.", "")


# ====================== aplikasi ======================
class DownloaderApp(App):
    title = "TikTok DL"

    # ---------- build ----------
    def build(self):
        Window.clearcolor = BG
        Window.softinput_mode = "below_target"

        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)
        d = self.user_data_dir
        self.work_dir = os.path.join(d, "work")
        self.archive_path = os.path.join(d, "archive.txt")
        self.cookie_path = os.path.join(d, "cookies.txt")
        self.history_path = os.path.join(d, "history.json")
        self.settings_path = os.path.join(d, "settings.json")

        st = load_json(self.settings_path, {})
        self.bulk = bool(st.get("bulk", False))
        self.auto = bool(st.get("auto", True))
        self.use_archive = bool(st.get("archive", True))

        self.stats = {"ok": 0, "skip": 0, "fail": 0}
        self.items = []
        for h in load_json(self.history_path, []):
            self.items.append({"url": h.get("url", ""), "name": h.get("name", "video.mp4"),
                               "status": "ok", "size": h.get("size", 0), "ts": h.get("ts", 0),
                               "detail": self._detail_ok(h.get("size", 0), h.get("ts", 0))})
        self.last_text = ""
        self._render_ev = None
        self._hist_dirty = False
        self._wl = None
        self.clip_err_shown = False
        self.use_kivy_clip = False

        root = BoxLayout(orientation="vertical")
        self.sm = ScreenManager(transition=NoTransition())
        self.sm.add_widget(self._build_home())
        self.sm.add_widget(self._build_history())
        self.sm.add_widget(self._build_settings())
        root.add_widget(self.sm)
        root.add_widget(self._build_nav())

        self.apply_mode(self.bulk, save=False)
        self.show("home")
        return root

    # ---------- layar: Beranda ----------
    def _build_home(self):
        scr = Screen(name="home")
        sv = ScrollView(do_scroll_x=False)
        g = GridLayout(cols=1, spacing=dp(14), padding=dp(16), size_hint_y=None)
        g.bind(minimum_height=g.setter("height"))

        # header
        head = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(12))
        head.add_widget(Icon("logo", size_hint=(None, None), size=(dp(48), dp(48)),
                             pos_hint={"center_y": .5}))
        head.add_widget(T("[b]TikTok[/b] [color=7a7f8c]Downloader[/color]", 24, markup=True))
        g.add_widget(head)
        g.add_widget(T("Simpan video TikTok & Douyin favoritmu dengan cepat dan mudah.", 13, GRAY,
                       size_hint_y=None, height=dp(34)))

        # kartu input
        card = Card(orientation="vertical", size_hint_y=None, padding=dp(14), spacing=dp(10))
        card.bind(minimum_height=card.setter("height"))

        seg = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8))
        self.btn_single = Seg(text="Satu Link", group="mode", allow_no_selection=False)
        self.btn_bulk = Seg(text="Bulk (banyak link)", group="mode", allow_no_selection=False)
        self.btn_bulk.bind(state=lambda i, s: self.apply_mode(s == "down"))
        seg.add_widget(self.btn_single)
        seg.add_widget(self.btn_bulk)
        card.add_widget(seg)

        self.pill = Card(bg=SOFT, radius=dp(16), size_hint_y=None, height=dp(54),
                         padding=[dp(8), dp(6), dp(6), dp(6)], spacing=dp(6))
        self.field = TextInput(multiline=False, font_size=sp(14), background_normal="",
                               background_active="", background_color=(0, 0, 0, 0),
                               foreground_color=INK, hint_text_color=(0.55, 0.57, 0.65, 1),
                               cursor_color=PINK, padding=[dp(6), dp(14)])
        self.field.bind(text=self.on_field_text, on_text_validate=lambda *_: self.start_download())
        self.pill.add_widget(self.field)
        anc = AnchorLayout(size_hint_x=None, width=dp(74), anchor_y="center")
        anc.add_widget(RoundButton(text="Tempel", bg=PINK_SOFT, fg=PINK, font_size=sp(13),
                                   size_hint_y=None, height=dp(36), radius=dp(18),
                                   on_release=self.on_paste))
        self.pill.add_widget(anc)
        card.add_widget(self.pill)

        self.btn_dl = RoundButton(text="Download", font_size=sp(16), size_hint_y=None,
                                  height=dp(52), on_release=lambda *_: self.start_download())
        card.add_widget(self.btn_dl)
        self.lbl_msg = T("", 12, GRAY, size_hint_y=None, height=dp(18))
        card.add_widget(self.lbl_msg)
        g.add_widget(card)

        # fitur
        feats = GridLayout(cols=4, size_hint_y=None, height=dp(96))
        for kind, tint, soft, a, b in (
                ("hd", PINK, PINK_SOFT, "Kualitas HD", "Terbaik"),
                ("bolt", TEAL, TEAL_SOFT, "Cepat", "Hitungan detik"),
                ("nowm", PURPLE, PURPLE_SOFT, "Tanpa Logo", "Video bersih"),
                ("phone", BLUE, BLUE_SOFT, "Semua HP", "Android")):
            cell = BoxLayout(orientation="vertical", spacing=dp(2))
            cell.add_widget(Icon(kind, tint, soft, size_hint=(None, None), size=(dp(46), dp(46)),
                                 pos_hint={"center_x": .5}))
            cell.add_widget(T(a, 11, INK, True, "center", size_hint_y=None, height=dp(18)))
            cell.add_widget(T(b, 10, GRAY, False, "center", size_hint_y=None, height=dp(14)))
            feats.add_widget(cell)
        g.add_widget(feats)

        # kartu status
        sc = Card(bg=PINK_SOFT, size_hint_y=None, height=dp(84), padding=dp(14), spacing=dp(8))
        col = BoxLayout(orientation="vertical")
        self.lbl_state = T("Siap mengunduh", 15, INK, True)
        self.lbl_count = T("", 12, GRAY)
        col.add_widget(self.lbl_state)
        col.add_widget(self.lbl_count)
        sc.add_widget(col)
        self.btn_retry_all = RoundButton(text="Ulangi gagal", font_size=sp(12), size_hint=(None, None),
                                         size=(dp(100), dp(36)), pos_hint={"center_y": .5},
                                         radius=dp(18), on_release=lambda *_: self.retry_failed())
        sc.add_widget(self.btn_retry_all)
        g.add_widget(sc)

        # daftar terbaru
        hdr = BoxLayout(size_hint_y=None, height=dp(30))
        hdr.add_widget(T("Terbaru", 16, INK, True))
        hdr.add_widget(Button(text="Lihat semua >", font_size=sp(12), color=GRAY, size_hint_x=None,
                              width=dp(110), background_normal="", background_down="",
                              background_color=(0, 0, 0, 0), on_release=lambda *_: self.show("hist")))
        g.add_widget(hdr)
        self.home_list = GridLayout(cols=1, spacing=dp(8), size_hint_y=None)
        self.home_list.bind(minimum_height=self.home_list.setter("height"))
        g.add_widget(self.home_list)

        sv.add_widget(g)
        scr.add_widget(sv)
        return scr

    # ---------- layar: Riwayat ----------
    def _build_history(self):
        scr = Screen(name="hist")
        box = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(10))
        box.add_widget(T("Riwayat", 22, INK, True, size_hint_y=None, height=dp(40)))
        self.hist_empty = T("Belum ada download.", 13, GRAY, halign="center",
                            size_hint_y=None, height=dp(60))
        box.add_widget(self.hist_empty)
        self.hist_rv = RecycleView(viewclass="ItemRow", do_scroll_x=False, bar_width=dp(3))
        lay = RecycleBoxLayout(default_size=(None, dp(68)), default_size_hint=(1, None),
                               size_hint_y=None, orientation="vertical", spacing=dp(8))
        lay.bind(minimum_height=lay.setter("height"))
        self.hist_rv.add_widget(lay)
        box.add_widget(self.hist_rv)
        scr.add_widget(box)
        return scr

    # ---------- layar: Atur ----------
    def _build_settings(self):
        scr = Screen(name="set")
        box = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
        box.add_widget(T("Atur", 22, INK, True, size_hint_y=None, height=dp(40)))

        def row(title, sub, active, cb):
            c = Card(size_hint_y=None, height=dp(72), padding=dp(14), spacing=dp(8))
            col = BoxLayout(orientation="vertical")
            col.add_widget(T(title, 14, INK, True))
            col.add_widget(T(sub, 11, GRAY))
            c.add_widget(col)
            sw = Switch(active=active, size_hint_x=None, width=dp(90))
            sw.bind(active=lambda i, v: cb(v))
            c.add_widget(sw)
            box.add_widget(c)

        row("Download otomatis", "Link di clipboard langsung diunduh",
            self.auto, lambda v: self.set_opt("auto", v))
        row("Lewati yang sudah pernah", "Video yang sudah ada tidak diunduh lagi",
            self.use_archive, lambda v: self.set_opt("archive", v))

        c = Card(size_hint_y=None, height=dp(72), padding=dp(14), spacing=dp(8))
        col = BoxLayout(orientation="vertical")
        col.add_widget(T("Cookies (opsional)", 14, INK, True))
        col.add_widget(T("Untuk Douyin yang meminta cookies", 11, GRAY))
        c.add_widget(col)
        c.add_widget(RoundButton(text="Atur", bg=PINK_SOFT, fg=PINK, size_hint=(None, None),
                                 size=(dp(80), dp(38)), pos_hint={"center_y": .5}, radius=dp(19),
                                 on_release=self.open_cookies))
        box.add_widget(c)

        dirs = engine.public_dirs()
        box.add_widget(T("Hasil download: " + (dirs[0] if dirs else self.work_dir), 11, GRAY,
                         size_hint_y=None, height=dp(40)))
        box.add_widget(Widget())
        scr.add_widget(box)
        return scr

    def _build_nav(self):
        bar = Card(bg=WHITE, radius=0, size_hint_y=None, height=dp(58))
        self.navs = {}
        for key, text in (("home", "Beranda"), ("hist", "Riwayat"), ("set", "Atur")):
            b = NavButton(text)
            b.bind(on_release=lambda i, k=key: self.show(k))
            self.navs[key] = b
            bar.add_widget(b)
        return bar

    def show(self, name):
        self.sm.current = name
        for k, b in self.navs.items():
            b.set_active(k == name)
        self.render()

    # ---------- lifecycle ----------
    def on_start(self):
        if platform == "android":
            try:
                from android.permissions import request_permissions, Permission
                request_permissions([Permission.WRITE_EXTERNAL_STORAGE,
                                     Permission.READ_EXTERNAL_STORAGE])
            except Exception:
                pass
        Clock.schedule_interval(self.poll_clipboard, 0.6)
        Clock.schedule_once(self.poll_clipboard, 0.3)

    def on_pause(self):
        self.save_history()
        return True  # wajib di Android supaya app tidak dimatikan saat pindah app

    def on_resume(self):
        self.request_clip(False)
        Clock.schedule_once(self.poll_clipboard, 0.4)
        # Paksa gambar ulang: setelah balik dari background layar kadang kosong.
        Clock.schedule_once(self._redraw, 0.2)

    def _redraw(self, dt):
        self.render()
        try:
            Window.canvas.ask_update()
            self.sm.canvas.ask_update()
        except Exception:
            pass

    def on_stop(self):
        self._wake(False)
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _wake(self, on):
        """Tahan CPU tetap hidup selama ada antrean, supaya download tidak
        ikut tidur saat layar mati / app di background."""
        if platform != "android":
            return
        try:
            if on and self._wl is None:
                from jnius import autoclass
                activity = autoclass("org.kivy.android.PythonActivity").mActivity
                ctx = autoclass("android.content.Context")
                pm = activity.getSystemService(ctx.POWER_SERVICE)
                self._wl = pm.newWakeLock(1, "tiktokdl:download")  # 1 = PARTIAL_WAKE_LOCK
                self._wl.acquire()
            elif not on and self._wl is not None:
                self._wl.release()
                self._wl = None
        except Exception:
            self._wl = None

    # ---------- pengaturan ----------
    def set_opt(self, key, val):
        if key == "auto":
            self.auto = val
        elif key == "archive":
            self.use_archive = val
        self.save_settings()

    def save_settings(self):
        save_json(self.settings_path, {"bulk": self.bulk, "auto": self.auto,
                                       "archive": self.use_archive})

    def apply_mode(self, bulk, save=True):
        self.bulk = bulk
        self.btn_bulk.state = "down" if bulk else "normal"
        self.btn_single.state = "normal" if bulk else "down"
        self.field.multiline = bulk
        self.pill.height = dp(130) if bulk else dp(54)
        self.field.hint_text = ("Satu link per baris -- link yang kamu copy otomatis masuk ke sini"
                                if bulk else "Tempel link TikTok / Douyin di sini...")
        self.on_field_text()
        if save:
            self.save_settings()

    def on_field_text(self, *_):
        n = len(engine.extract_links(self.field.text))
        if self.bulk:
            self.btn_dl.text = f"Download Semua ({n})" if n else "Download Semua"
        else:
            self.btn_dl.text = "Download"

    def flash(self, msg, color=GRAY):
        self.lbl_msg.text = msg
        self.lbl_msg.color = color

    # ---------- clipboard ----------
    def request_clip(self, manual):
        if platform == "android" and not self.use_kivy_clip:
            android_read_clip(manual)
            return
        # Desktop, atau cadangan kalau jalur Android error
        try:
            text = Clipboard.paste() or ""
        except Exception:
            text = ""
        if manual or (text and text != self.last_text):
            if text:
                self.last_text = text
            _deliver(text, manual)

    def poll_clipboard(self, *_):
        if CLIP["err"] and not self.clip_err_shown:
            self.clip_err_shown = True
            self.use_kivy_clip = True      # pindah ke cadangan
            self.flash("Clipboard (jalur 1) error: " + CLIP["err"][:90], RED)
        if CLIP["new"]:
            CLIP["new"] = False
            self.handle_clip(CLIP["text"], CLIP["manual"])
        self.request_clip(False)

    def on_paste(self, *_):
        CLIP["marker"] = None
        self.request_clip(True)

    def handle_clip(self, text, manual):
        links = engine.extract_links(text)
        if not links:
            if manual:
                self.flash("Clipboard kosong atau bukan link TikTok/Douyin.", RED)
            return
        if self.bulk:
            existing = engine.extract_links(self.field.text)
            new = [u for u in links if u not in existing]
            if new:
                keep = self.field.text.rstrip("\n")
                self.field.text = (keep + "\n" if keep.strip() else "") + "\n".join(new) + "\n"
                self.flash(f"+{len(new)} link masuk (total {len(existing) + len(new)})", GREEN)
            else:
                self.flash("Link sudah ada di kotak.")
            return
        # mode satu link: tempel otomatis ke kotak
        self.field.text = "\n".join(links)
        if self.auto and not manual:
            self.flash("Link terdeteksi, mulai download...", GREEN)
            Clock.schedule_once(lambda dt: self.start_download(), 0.5)
        else:
            self.flash("Link ditempel. Tekan Download.", GREEN)

    # ---------- download ----------
    def start_download(self):
        links = engine.extract_links(self.field.text)
        if not links:
            self.flash("Tempel link TikTok/Douyin dulu.", RED)
            return
        self.field.text = ""
        self.flash(f"Mengunduh {len(links)} video...", GREEN)
        self.enqueue(links)

    def enqueue(self, links):
        archive = self.archive_path if self.use_archive else None
        running = {i["url"] for i in self.items if i["status"] == "run"}
        for url in links:
            if url in running:
                continue
            it = {"url": url, "name": short_url(url)[:40], "status": "run",
                  "detail": "Mengunduh...", "size": 0, "ts": time.time()}
            self.items.insert(0, it)
            self.pool.submit(self._job, it, archive)
        self.render()

    def _job(self, it, archive):
        # Apa pun yang terjadi pada SATU link (gagal, error aneh, hang),
        # worker harus selalu lanjut ke link berikutnya di antrean.
        status, msg, files = "FAIL", "gagal tidak diketahui", []
        try:
            status, msg, files = engine.download_one_safe(
                it["url"], self.work_dir, engine.public_dirs(),
                archive_path=archive, cookie_path=self.cookie_path)
        except BaseException as e:
            status, msg, files = "FAIL", (str(e) or repr(e))[:200], []
        finally:
            self._job_done(it, status, msg, files)

    @mainthread
    def _job_done(self, it, status, msg, files):
        it["ts"] = time.time()
        try:
            if status == "OK":
                self.stats["ok"] += 1
                size = sum(s for _, s in files)
                it.update(status="ok", size=size,
                          name=files[0][0] if len(files) == 1 else f"{len(files)} video",
                          detail=self._detail_ok(size, it["ts"]))
                self._hist_dirty = True
            elif status == "SKIP":
                self.stats["skip"] += 1
                it.update(status="skip", detail="Sudah pernah didownload (dilewati)")
            else:
                self.stats["fail"] += 1
                it.update(status="fail", detail=(msg or "gagal")[:90])
        except Exception as e:
            # jangan sampai error tampilan membuat item nyangkut di "Mengunduh..."
            it.update(status="fail", detail=("error internal: " + repr(e))[:90])
        self.request_render()

    def request_render(self):
        """Gabungkan banyak update jadi SATU render. Tanpa ini, 80 link selesai
        = 80x bongkar-pasang daftar, dan menumpuk saat app di background."""
        if self._render_ev is None:
            self._render_ev = Clock.schedule_once(self._do_render, 0.5)

    def _do_render(self, dt):
        self._render_ev = None
        if self._hist_dirty:
            self._hist_dirty = False
            self.save_history()
        try:
            self.render()
        except Exception:
            pass

    def _detail_ok(self, size, ts):
        when = time.strftime("%d/%m %H:%M", time.localtime(ts)) if ts else ""
        return " • ".join(x for x in (fmt_size(size), when) if x)

    def save_history(self):
        ok = [{"url": i["url"], "name": i["name"], "size": i["size"], "ts": i["ts"]}
              for i in self.items if i["status"] == "ok"][:200]
        save_json(self.history_path, ok)

    def retry(self, it):
        if it in self.items:
            self.items.remove(it)
        if self.stats["fail"] > 0:
            self.stats["fail"] -= 1
        self.enqueue([it["url"]])

    def retry_failed(self):
        urls = [i["url"] for i in self.items if i["status"] == "fail"]
        if not urls:
            return
        self.items = [i for i in self.items if i["status"] != "fail"]
        self.stats["fail"] = 0
        self.flash(f"Mengulang {len(urls)} link...", GREEN)
        self.enqueue(urls)

    # ---------- tampilan daftar ----------
    def render(self):
        pending = sum(1 for i in self.items if i["status"] == "run")
        fails = sum(1 for i in self.items if i["status"] == "fail")
        self.lbl_state.text = f"Mengunduh {pending} video..." if pending else "Siap mengunduh"
        s = self.stats
        self.lbl_count.text = f"Berhasil {s['ok']}  •  Dilewati {s['skip']}  •  Gagal {s['fail']}"
        self.btn_retry_all.opacity = 1 if fails else 0
        self.btn_retry_all.disabled = not fails
        self._wake(pending > 0)

        if self.sm.current == "home":
            self._fill(self.home_list, self.items[:5])
        elif self.sm.current == "hist":
            shown = self.items[:200]
            self.hist_empty.height = 0 if shown else dp(60)
            self.hist_empty.opacity = 0 if shown else 1
            self.hist_rv.data = [{"it": it} for it in shown]

    def _fill(self, box, items):
        box.clear_widgets()
        if not items:
            box.add_widget(T("Belum ada download.", 13, GRAY, halign="center",
                             size_hint_y=None, height=dp(60)))
            return
        for it in items:
            box.add_widget(self._row(it))

    def _row(self, it):
        row = ItemRow(size_hint_y=None, height=dp(68))
        row.apply(it)
        return row

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
                self.flash("Cookies disimpan.", GREEN)
            pop.dismiss()

        def delete(*_):
            if os.path.exists(self.cookie_path):
                os.remove(self.cookie_path)
            self.flash("Cookies dihapus.")
            pop.dismiss()

        row.add_widget(Button(text="SIMPAN", on_release=save))
        row.add_widget(Button(text="HAPUS COOKIES", on_release=delete))
        row.add_widget(Button(text="BATAL", on_release=lambda *_: pop.dismiss()))
        content.add_widget(row)
        pop.open()


if __name__ == "__main__":
    DownloaderApp().run()
