# diseño.py — Cassettify (ONEFILE friendly) + Login WebView + cookies AppData + detección anti-bot
import os, sys, shutil, threading, re, ctypes, platform, subprocess
from pathlib import Path
from datetime import datetime
import traceback
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont
from PIL import Image, ImageTk, ImageDraw
import yt_dlp

# ================== DEPENDENCIA LOGIN ==================
# pywebview solo se usa en modo --login, así que no lo importamos arriba para no romper la app si no está instalado.

# winsound sólo en Windows
if platform.system() == "Windows":
    import winsound
else:
    winsound = None


# ================== RUTAS PORTABLE / EMPAQUETADO ==================
def get_app_dir() -> Path:
    """
    Devuelve la carpeta base de la app:
    - En modo empaquetado (PyInstaller onefile/onedir): sys._MEIPASS o carpeta del exe.
    - En modo desarrollo: carpeta del script.
    """
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_appdata_dir(app_name="Cassettify") -> Path:
    """
    ONEFILE-friendly: archivos persistentes en AppData del usuario.
    Evita problemas de permisos si el exe está en Program Files.
    """
    base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home()))
    d = base / app_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_desktop_dir() -> Path:
    """
    Devuelve la ruta real del Escritorio del usuario.
    En Windows intenta usar Known Folders para soportar OneDrive/redirecciones.
    """
    if platform.system() == "Windows":
        try:
            import uuid

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_uint32),
                    ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            def _guid_from_str(guid_str: str) -> GUID:
                u = uuid.UUID(guid_str)
                data4 = (ctypes.c_ubyte * 8)(*u.bytes[8:])
                return GUID(u.time_low, u.time_mid, u.time_hi_version, data4)

            # FOLDERID_Desktop
            folder_id = _guid_from_str("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")
            path_ptr = ctypes.c_wchar_p()

            shell32 = ctypes.windll.shell32
            ole32 = ctypes.windll.ole32
            shell32.SHGetKnownFolderPath.argtypes = [
                ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p),
            ]
            shell32.SHGetKnownFolderPath.restype = ctypes.c_long

            hr = shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(path_ptr))
            if hr == 0 and path_ptr.value:
                try:
                    return Path(path_ptr.value)
                finally:
                    ole32.CoTaskMemFree(path_ptr)
        except Exception:
            pass

        userprofile = Path(os.environ.get("USERPROFILE") or str(Path.home()))
        desktop = userprofile / "Desktop"
        if desktop.is_dir():
            return desktop

    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        return desktop
    return Path.home()


APP_DIR = get_app_dir()
APPDATA_DIR = get_appdata_dir("Cassettify")
COOKIES_TXT = APPDATA_DIR / "cookies.txt"
WEBVIEW_STORAGE = APPDATA_DIR / "webview_storage"
LOG_FILE = APPDATA_DIR / "cassettify.log"
_LOG_LOCK = threading.Lock()
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3
SENSITIVE_QUERY_KEYS = {
    "sig", "lsig", "spc", "xpc", "expire", "ei", "ip", "token", "cpn", "id",
}

# ================== CONFIG OPCIONAL (assets junto al script/app) ==================
POSTER_IMAGE = str(APP_DIR / "imagen.png")
COMPLETION_SOUND = str(APP_DIR / "done.wav")  # sonido al terminar descarga (opcional)
ERROR_SOUND = str(APP_DIR / "error.wav")  # sonido al fallar una descarga (opcional)
TUTORIAL_FLAG = APPDATA_DIR / "tutorial_seen.flag"
BOOTSTRAP_SCRIPT = APP_DIR / "bootstrap.ps1"
UPDATE_DEPS_SCRIPT = APP_DIR / "update_deps.ps1"


def _sanitize_url(url: str) -> str:
    try:
        u = urlsplit(url)
        if not u.query:
            return url
        q = []
        for k, v in parse_qsl(u.query, keep_blank_values=True):
            if k.lower() in SENSITIVE_QUERY_KEYS:
                q.append((k, "***"))
            else:
                q.append((k, v))
        return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), u.fragment))
    except Exception:
        return url


def _sanitize_log_message(message: str) -> str:
    try:
        return re.sub(
            r"https?://[^\s\"']+",
            lambda m: _sanitize_url(m.group(0)),
            str(message),
        )
    except Exception:
        return str(message)


def _rotate_logs_if_needed():
    try:
        if not LOG_FILE.exists() or LOG_FILE.stat().st_size < LOG_MAX_BYTES:
            return
        for idx in range(LOG_BACKUPS, 0, -1):
            src = LOG_FILE.with_name(f"{LOG_FILE.name}.{idx}")
            dst = LOG_FILE.with_name(f"{LOG_FILE.name}.{idx + 1}")
            if src.exists():
                if idx == LOG_BACKUPS:
                    src.unlink(missing_ok=True)
                else:
                    src.replace(dst)
        LOG_FILE.replace(LOG_FILE.with_name(f"{LOG_FILE.name}.1"))
    except Exception:
        pass


def append_log(message: str):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {_sanitize_log_message(message)}\n"
        with _LOG_LOCK:
            _rotate_logs_if_needed()
            with LOG_FILE.open("a", encoding="utf-8", errors="replace") as f:
                f.write(line)
    except Exception:
        pass


class YDLLogger:
    def __init__(self, scope: str):
        self.scope = scope

    def debug(self, msg):
        if msg:
            append_log(f"{self.scope} DEBUG: {msg}")

    def warning(self, msg):
        if msg:
            append_log(f"{self.scope} WARNING: {msg}")

    def error(self, msg):
        if msg:
            append_log(f"{self.scope} ERROR: {msg}")


# ================== util tipografías ==================
def pick_font(master, candidates, size, weight="normal"):
    avail = set(tkfont.families(master))
    for fam in candidates:
        if fam in avail:
            return (fam, size, weight)
    return ("Segoe UI", size, weight)


def compute_fonts(master):
    return {
        "TITLE": pick_font(master, ["Segoe UI", "Inter", "IBM Plex Sans", "Arial"], 20, "bold"),
        "HERO": pick_font(master, ["Segoe UI", "Inter", "IBM Plex Sans", "Arial"], 28, "bold"),
        "UI": pick_font(master, ["Segoe UI", "Inter", "IBM Plex Sans", "Arial"], 12, "normal"),
        "UI_B": pick_font(master, ["Segoe UI", "Inter", "IBM Plex Sans", "Arial"], 12, "bold"),
        "SUB": pick_font(master, ["Segoe UI", "Inter", "IBM Plex Sans", "Arial"], 13, "normal"),
        "MONO": pick_font(master, ["JetBrains Mono", "Fira Code", "Cascadia Mono", "Consolas", "Courier New"], 12, "normal"),
        "MONO_B": pick_font(master, ["JetBrains Mono", "Fira Code", "Cascadia Mono", "Consolas", "Courier New"], 12, "bold"),
    }


# ================== Ventana Retro ==================
class RetroWindow(tk.Tk):
    def __init__(self, title="Cassettify", width=1000, height=780, radius=22, titlebar_height=46):
        super().__init__()
        self.win_w, self.win_h = int(width), int(height)
        self.radius, self.tb_h = int(radius), int(titlebar_height)
        self._drag = {"x": 0, "y": 0}

        self.palette = {
            "bg": "#0b1220",
            "panel": "#101a2f",
            "surface": "#16233d",
            "titlebar": "#12203a",
            "title": "#f3f6ff",
            "text": "#dce7ff",
            "text_muted": "#b5c8e8",
            "accent": "#0ea5a6",
            "accent_hover": "#14b8a6",
            "accent_soft": "#134e4a",
            "outline": "#2a3d66",
            "shadow": "#070d19",
            "btn_shadow": "#0a1324",
            "btn_shadow_hover": "#1a2a48",
            "success": "#34d399",
            "error": "#f87171",
            "transparent": "#00ff00",
            "glow": "#5eead4",
        }

        self.geometry(f"{self.win_w}x{self.win_h}+160+90")
        self.update_idletasks()
        self.overrideredirect(True)
        self.wm_attributes("-transparentcolor", self.palette["transparent"])

        self._borderless = True
        self.bind("<Map>", self._on_map)

        self.fonts = compute_fonts(self)

        self.canvas = tk.Canvas(self, bd=0, highlightthickness=0, bg=self.palette["transparent"])
        self.canvas.pack(fill="both", expand=True)

        self._bg_img = None
        self._bg_item = None
        self._draw_background()

        self.titlebar = tk.Frame(self.canvas, bg=self.palette["titlebar"])
        self.lbl_title = tk.Label(self.titlebar, text=title, fg=self.palette["title"], bg=self.palette["titlebar"], font=self.fonts["TITLE"])

        self.btn_min = tk.Label(self.titlebar, text="–", fg="white", bg=self.palette["accent"], width=3, cursor="hand2")
        self.btn_close = tk.Label(self.titlebar, text="✕", fg="white", bg="#e11d48", width=3, cursor="hand2")

        self.lbl_title.place(relx=0.5, rely=0.5, anchor="center")
        self.btn_min.pack(side="right", padx=4, pady=6, ipady=1)
        self.btn_close.pack(side="right", padx=4, pady=6, ipady=1)

        self.content = tk.Frame(
            self.canvas, bg=self.palette["panel"], highlightthickness=1, highlightbackground=self.palette["outline"]
        )

        self.titlebar_win = self.canvas.create_window(
            self.radius, self.radius, anchor="nw", window=self.titlebar,
            width=self.win_w - 2 * self.radius, height=self.tb_h,
        )
        self.content_win = self.canvas.create_window(
            self.radius, self.radius + self.tb_h, anchor="nw", window=self.content,
            width=self.win_w - 2 * self.radius, height=self.win_h - self.tb_h - 2 * self.radius,
        )

        for w in (self.titlebar, self.lbl_title):
            w.bind("<ButtonPress-1>", self._start_move)
            w.bind("<B1-Motion>", self._on_move)
            w.bind("<Double-Button-1>", lambda e: self._toggle_maximize())

        self.btn_close.bind("<Button-1>", lambda e: self.destroy())
        self.btn_min.bind("<Button-1>", lambda e: self._on_minimize())

        self.btn_min.bind("<Enter>", lambda e: self.btn_min.config(bg=self.palette["accent_hover"]))
        self.btn_min.bind("<Leave>", lambda e: self.btn_min.config(bg=self.palette["accent"]))
        self.btn_close.bind("<Enter>", lambda e: self.btn_close.config(bg="#f43f5e"))
        self.btn_close.bind("<Leave>", lambda e: self.btn_close.config(bg="#e11d48"))

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Configure>", self._on_configure)
        self._enable_shadow()

    def _on_minimize(self):
        try:
            self.overrideredirect(False)
            self.iconify()
        except tk.TclError:
            self.withdraw()

    def _on_map(self, event):
        if getattr(self, "_borderless", False) and self.state() == "normal":
            def _restore_borderless():
                try:
                    self.overrideredirect(True)
                    self.wm_attributes("-transparentcolor", self.palette["transparent"])
                except Exception:
                    pass
            self.after(10, _restore_borderless)

    def _draw_background(self):
        w, h, r = self.win_w, self.win_h, self.radius
        img = Image.new("RGBA", (w, h), self._hex_rgba(self.palette["transparent"]))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((4, 7, w - 4, h - 1), radius=r + 3, fill=self.palette["shadow"])
        d.rounded_rectangle((0, 0, w, h), radius=r, fill=self.palette["bg"])
        d.rounded_rectangle((6, 10, w - 6, h - 6), radius=r - 4, outline=self.palette["outline"], width=1)
        self._bg_img = ImageTk.PhotoImage(img)
        if self._bg_item is None:
            self._bg_item = self.canvas.create_image(0, 0, image=self._bg_img, anchor="nw")
        else:
            self.canvas.itemconfigure(self._bg_item, image=self._bg_img)

    def _start_move(self, e):
        self._drag = {"x": e.x_root - self.winfo_x(), "y": e.y_root - self.winfo_y()}

    def _on_move(self, e):
        self.geometry(f"+{e.x_root - self._drag['x']}+{e.y_root - self._drag['y']}")

    def _toggle_maximize(self):
        try:
            if self.state() != "zoomed":
                self._restore = self.geometry()
                self.state("zoomed")
            else:
                self.state("normal")
                if hasattr(self, "_restore"):
                    self.geometry(self._restore)
        except Exception:
            pass

    def _on_configure(self, e):
        if e.widget is self:
            self.win_w, self.win_h = e.width, e.height
            self.canvas.coords(self.titlebar_win, self.radius, self.radius)
            self.canvas.itemconfigure(self.titlebar_win, width=self.win_w - 2 * self.radius, height=self.tb_h)
            self.canvas.coords(self.content_win, self.radius, self.radius + self.tb_h)
            self.canvas.itemconfigure(
                self.content_win,
                width=self.win_w - 2 * self.radius,
                height=self.win_h - self.tb_h - 2 * self.radius,
            )
            self._draw_background()

    def _enable_shadow(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            DWMWA_NCRENDERING_POLICY = 2
            DWMNCRP_ENABLED = 2
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_NCRENDERING_POLICY,
                ctypes.byref(ctypes.c_int(DWMNCRP_ENABLED)),
                ctypes.sizeof(ctypes.c_int),
            )
        except Exception:
            pass

    @staticmethod
    def _hex_rgba(hx):
        hx = hx.lstrip("#")
        return (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16), 255)


# ================== Tema ttk ==================
def apply_theme(root):
    s = ttk.Style(root)
    try:
        s.theme_use("clam")
    except Exception:
        pass

    s.configure(
        "Cass.TEntry",
        fieldbackground=root.palette["surface"],
        foreground=root.palette["title"],
        padding=11,
        bordercolor=root.palette["outline"],
        font=root.fonts["MONO"],
    )
    s.configure(
        "Cass.TButton",
        background=root.palette["accent"],
        foreground="#ffffff",
        font=root.fonts["UI_B"],
        padding=(16, 11),
        focusthickness=0,
        borderwidth=0,
    )
    s.map("Cass.TButton", background=[("active", root.palette["accent_hover"])], relief=[("pressed", "sunken")])

    s.configure(
        "Cass.Secondary.TButton",
        background=root.palette["surface"],
        foreground=root.palette["text"],
        font=root.fonts["UI_B"],
        padding=(14, 10),
        focusthickness=0,
        borderwidth=0,
    )
    s.map("Cass.Secondary.TButton", background=[("active", root.palette["outline"])], relief=[("pressed", "sunken")])

    s.configure(
        "Cass.TCombobox",
        fieldbackground=root.palette["surface"],
        foreground=root.palette["title"],
        padding=6,
        bordercolor=root.palette["outline"],
        arrowsize=14,
    )
    s.map(
        "Cass.TCombobox",
        fieldbackground=[("readonly", root.palette["surface"]), ("!disabled", root.palette["surface"])],
        foreground=[("readonly", root.palette["title"]), ("!disabled", root.palette["title"])],
        selectbackground=[("readonly", root.palette["accent_soft"])],
        selectforeground=[("readonly", root.palette["title"])],
    )
    root.option_add("*TCombobox*Listbox.background", root.palette["surface"])
    root.option_add("*TCombobox*Listbox.foreground", root.palette["title"])
    root.option_add("*TCombobox*Listbox.selectBackground", root.palette["accent_soft"])
    root.option_add("*TCombobox*Listbox.selectForeground", root.palette["title"])


# ================== Toast ==================
class Toast(tk.Toplevel):
    def __init__(self, master, text, duration=1800):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=master.palette["shadow"])
        self.opacity = 0.0
        self.duration = duration

        fnt = tkfont.Font(master=master, font=master.fonts["UI"])
        text_w = fnt.measure(text)
        width = min(max(260, text_w + 40), 560)
        height = 64

        self.update_idletasks()
        x = master.winfo_rootx() + master.win_w - (width + 20)
        y = master.winfo_rooty() + 60
        self.geometry(f"{width}x{height}+{x}+{y}")

        f = tk.Frame(self, bg=master.palette["surface"], highlightthickness=1, highlightbackground=master.palette["outline"])
        f.pack(fill="both", expand=True)

        lbl = tk.Label(
            f, text=text, fg=master.palette["title"], bg=master.palette["surface"], font=master.fonts["UI"],
            wraplength=width - 24, justify="left",
        )
        lbl.pack(padx=12, pady=10)

        self.after(10, self._fade_in)
        self.after(self.duration, self._fade_out)

    def _fade_in(self):
        self.opacity = min(1.0, self.opacity + 0.15)
        try:
            self.attributes("-alpha", self.opacity)
        except Exception:
            pass
        if self.opacity < 1.0:
            self.after(25, self._fade_in)

    def _fade_out(self):
        self.opacity = max(0.0, self.opacity - 0.12)
        try:
            self.attributes("-alpha", self.opacity)
        except Exception:
            pass
        if self.opacity > 0.0:
            self.after(25, self._fade_out)
        else:
            self.destroy()


# ================== Barra de progreso global ==================
class GlowProgress(tk.Canvas):
    def __init__(self, master, height=16, **kw):
        super().__init__(master, height=height, bd=0, highlightthickness=0, **kw)
        self.bg = "#1a2c4e"
        self.fill = "#2dd4bf"
        self.glow = "#5eead4"
        self.value = 0
        self.anim = False
        self._phase = 0.0
        self.bind("<Configure>", lambda e: self.redraw())

    def set(self, pct):
        self.value = max(0, min(100, int(pct)))
        self.redraw()

    def start(self):
        if not self.anim:
            self.anim = True
            self._tick()

    def stop(self):
        self.anim = False
        self.redraw()

    def _tick(self):
        if not self.anim:
            return
        self._phase = (self._phase + 0.03) % 1.0
        self.redraw()
        self.after(30, self._tick)

    def redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        r = h // 2
        self._rr(1, 1, w - 1, h - 1, r, fill=self.bg, outline="#1e1232")
        filled = int((w - 4) * self.value / 100)
        if filled > 0:
            self._rr(2, 2, 2 + filled, h - 2, r - 1, fill=self.fill, outline=self.fill)
            if self.anim and filled > 12:
                sw = max(20, w // 6)
                pos = int((w + sw) * self._phase) - sw
                x0 = max(2, pos)
                x1 = min(2 + filled, pos + sw)
                if x1 > x0:
                    self.create_rectangle(x0, 2, x1, h - 2, fill="#ffffff", outline="")
        if self.anim:
            self._rr(1, 1, w - 1, h - 1, r, fill="", outline=self.glow)

    def _rr(self, x0, y0, x1, y1, r, **kw):
        self.create_arc(x0, y0, x0 + 2 * r, y0 + 2 * r, start=90, extent=90, **kw)
        self.create_arc(x1 - 2 * r, y0, x1, y0 + 2 * r, start=0, extent=90, **kw)
        self.create_arc(x0, y1 - 2 * r, x0 + 2 * r, y1, start=180, extent=90, **kw)
        self.create_arc(x1 - 2 * r, y1 - 2 * r, x1, y1, start=270, extent=90, **kw)
        self.create_rectangle(x0 + r, y0, x1 - r, y1, **kw)
        self.create_rectangle(x0, y0 + r, x1, y1 - r, **kw)


# ================== Login WebView (otro proceso) ==================
from email.utils import parsedate_to_datetime

def _expires_to_epoch(expires_str: str) -> int:
    if not expires_str:
        return 0
    try:
        dt = parsedate_to_datetime(expires_str)
        return int(dt.timestamp())
    except Exception:
        return 0


def save_cookies_netscape(pywebview_cookies, out_path: Path):
    """
    Guarda cookies en formato Netscape para yt-dlp.
    """
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated by Cassettify",
        "",
    ]

    for c in pywebview_cookies:
        name = getattr(c, "key", "") or ""
        value = getattr(c, "value", "") or ""

        # domain/path
        try:
            domain = (c["domain"] or "").strip()
        except Exception:
            domain = ""
        try:
            path = (c["path"] or "/").strip() or "/"
        except Exception:
            path = "/"

        # expires
        try:
            expires_raw = c.get("expires", "") if hasattr(c, "get") else c["expires"]
        except Exception:
            expires_raw = ""
        expires = _expires_to_epoch(expires_raw)

        # secure
        try:
            secure_attr = c.get("secure", "") if hasattr(c, "get") else c["secure"]
            secure = "TRUE" if secure_attr else "FALSE"
        except Exception:
            secure = "FALSE"

        if not domain:
            continue

        # quedarnos con cookies útiles (YT/Google)
        if not any(domain.endswith(x) for x in ("youtube.com", "google.com", "accounts.google.com")):
            continue

        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        lines.append(f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expires}\t{name}\t{value}")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def run_login_webview_and_save(cookies_path: Path, storage_path: Path):
    """
    Abre YouTube dentro de un WebView.
    Usuario inicia sesión y pulsa "Guardar sesión".
    """
    import webview

    class Api:
        def save_and_close(self):
            try:
                cookies = window.get_cookies()
                save_cookies_netscape(cookies, cookies_path)
            finally:
                try:
                    window.destroy()
                except Exception:
                    pass

        def clear_and_close(self):
            try:
                try:
                    window.clear_cookies()
                except Exception:
                    pass
                try:
                    if cookies_path.exists():
                        cookies_path.unlink()
                except Exception:
                    pass
            finally:
                try:
                    window.destroy()
                except Exception:
                    pass

    window = webview.create_window(
        "Cassettify — Inicia sesión en YouTube",
        "https://www.youtube.com",
        js_api=Api(),
        width=1100,
        height=760,
    )

    def inject_buttons():
        js = r"""
        (function(){
          function mk(text, bg, right, apiMethod){
            const b = document.createElement('button');
            b.innerText = text;
            b.style.position='fixed';
            b.style.right=right+'px';
            b.style.bottom='16px';
            b.style.zIndex='999999';
            b.style.padding='12px 16px';
            b.style.borderRadius='12px';
            b.style.border='0';
            b.style.cursor='pointer';
            b.style.fontSize='14px';
            b.style.boxShadow='0 8px 24px rgba(0,0,0,.35)';
            b.style.background=bg;
            b.style.color='white';
            b.onclick=function(){
              if (window.pywebview?.api?.[apiMethod]) window.pywebview.api[apiMethod]();
              else alert('No se pudo conectar con la app.');
            };
            document.body.appendChild(b);
          }

          mk('Guardar sesión', '#7C3AED', 16, 'save_and_close');
          mk('Cerrar sesión', '#e11d48', 160, 'clear_and_close');
        })();
        """
        try:
            window.evaluate_js(js)
        except Exception:
            pass

    try:
        window.events.loaded += lambda: inject_buttons()
    except Exception:
        pass

    # Persistencia del motor web (cookies/local storage) en AppData.
    webview.start(private_mode=False, storage_path=str(storage_path))


# ================== Núcleo (yt-dlp + FFmpeg) ==================
class Core:
    def __init__(self, ui):
        self.ui = ui
        self.output_dir = str(get_desktop_dir())
        self.remember_output = False
        self.ffmpeg_path = self.detect_ffmpeg()
        self.js_runtime = self.detect_js_runtime()
        self.pot_script_path = self.detect_pot_script()
        self.restricted_network_mode = False
        append_log(
            "Core init | output_dir="
            f"{self.output_dir} | ffmpeg={self.ffmpeg_path or 'none'} | "
            f"js_runtime={self.js_runtime[0] if self.js_runtime else 'none'} | "
            f"pot_script={self.pot_script_path or 'none'} | "
            f"restricted_network_mode={self.restricted_network_mode}"
        )

    def _log_context(self, scope: str, url: str, opts: dict):
        safe = {
            "format": opts.get("format"),
            "outtmpl": opts.get("outtmpl"),
            "noplaylist": opts.get("noplaylist"),
            "playlist_items": opts.get("playlist_items"),
            "ffmpeg_location": opts.get("ffmpeg_location"),
            "cookiefile": bool(opts.get("cookiefile")),
            "merge_output_format": opts.get("merge_output_format"),
            "postprocessors": opts.get("postprocessors"),
            "restricted_network_mode": self.restricted_network_mode,
        }
        append_log(f"{scope} ctx | url={url} | opts={safe}")

    def set_restricted_network_mode(self, enabled: bool, reason: str = "manual"):
        self.restricted_network_mode = bool(enabled)
        append_log(
            f"restricted_network_mode={'on' if self.restricted_network_mode else 'off'} | reason={reason}"
        )

    @staticmethod
    def _safe_video_format_selector() -> str:
        # Prioriza alta calidad (bestvideo+bestaudio) y deja 18 como último recurso.
        return (
            "bestvideo[ext=mp4][vcodec!=none][protocol^=http]+bestaudio[acodec!=none][protocol^=http]/"
            "bestvideo[vcodec!=none][protocol^=http]+bestaudio[acodec!=none][protocol^=http]/"
            "bestvideo+bestaudio/"
            "best[ext=mp4][acodec!=none][vcodec!=none][protocol^=http]/"
            "best[acodec!=none][vcodec!=none][protocol^=http]/"
            "18"
        )

    @staticmethod
    def _strip_drc_suffix(fmt_id: str) -> str:
        s = str(fmt_id or "")
        return s[:-4] if s.endswith("-drc") else s

    @staticmethod
    def _unique_ordered(items):
        out, seen = [], set()
        for it in items:
            if not it or it in seen:
                continue
            seen.add(it)
            out.append(it)
        return out

    def _build_video_format_chain(self, vid_id: str, aud_id: str = None) -> str:
        vid_raw = str(vid_id or "").strip()
        aud_raw = str(aud_id or "").strip() if aud_id else ""
        vid_clean = self._strip_drc_suffix(vid_raw)
        aud_clean = self._strip_drc_suffix(aud_raw) if aud_raw else ""
        chain = []

        if vid_raw and aud_raw:
            chain.append(f"{vid_raw}+{aud_raw}")
            chain.append(f"{vid_clean}+{aud_clean}")
            chain.append(f"{vid_clean}+bestaudio")
        elif vid_raw:
            chain.append(vid_raw)
            chain.append(vid_clean)
            chain.append(f"{vid_clean}+bestaudio")

        chain.append(self._safe_video_format_selector())
        return "/".join(self._unique_ordered(chain))

    def detect_js_runtime(self):
        # yt-dlp mejora mucho con runtime JS (node/deno/bun/qjs)
        for exe in ("deno", "node", "bun", "qjs", "quickjs"):
            p = shutil.which(exe)
            if p:
                return exe, p
        # Fallback Windows: deno instalado por winget puede no estar aún en PATH.
        if platform.system() == "Windows":
            candidates = [
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "deno.exe",
                Path.home() / ".deno" / "bin" / "deno.exe",
            ]
            winget_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
            try:
                if winget_root.is_dir():
                    pkgs = sorted(winget_root.glob("DenoLand.Deno*"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if pkgs:
                        candidates.append(pkgs[0] / "deno.exe")
            except Exception:
                pass
            for p in candidates:
                try:
                    if p and p.exists():
                        return "deno", str(p)
                except Exception:
                    continue
        return None

    def apply_js_runtime_opts(self, y: dict) -> dict:
        if not self.js_runtime:
            return y
        name, path = self.js_runtime
        # Estructura aceptada por yt-dlp API para runtimes JS.
        y["js_runtimes"] = {name: {"path": path}}
        return y

    def detect_pot_script(self):
        candidates = [
            Path.home() / "bgutil-ytdlp-pot-provider" / "server" / "build" / "generate_once.js",
            APPDATA_DIR / "pot_provider" / "server" / "build" / "generate_once.js",
        ]
        for p in candidates:
            try:
                if p.exists():
                    return str(p)
            except Exception:
                continue
        return None

    def apply_pot_provider_opts(self, y: dict) -> dict:
        extractor_args = y.setdefault("extractor_args", {})
        yt_args = extractor_args.setdefault("youtube", {})
        yt_args.setdefault("fetch_pot", "auto")
        if self.pot_script_path:
            bg_args = extractor_args.setdefault("youtubepot-bgutilscript", {})
            bg_args.setdefault("script_path", self.pot_script_path)
        return y

    def apply_restricted_network_opts(self, y: dict) -> dict:
        # Perfil para redes/IPs propensas a bloqueos 403/429.
        y["retries"] = min(int(y.get("retries", 5)), 3)
        y["extractor_retries"] = min(int(y.get("extractor_retries", 5)), 3)
        y["fragment_retries"] = min(int(y.get("fragment_retries", 5)), 3)
        y["sleep_requests"] = max(int(y.get("sleep_requests", 1)), 2)
        y["sleep_interval"] = max(int(y.get("sleep_interval", 1)), 2)
        y["max_sleep_interval"] = max(int(y.get("max_sleep_interval", 3)), 8)
        y["concurrent_fragment_downloads"] = 1

        headers = y.setdefault("http_headers", {})
        headers["User-Agent"] = (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36"
        )
        headers.setdefault("Accept-Language", "es-ES,es;q=0.9,en;q=0.8")
        headers.setdefault("Referer", "https://www.youtube.com/")

        extractor_args = y.setdefault("extractor_args", {})
        yt_args = extractor_args.setdefault("youtube", {})
        yt_args["player_client"] = ["android", "mweb", "web"]
        return y

    # --- FFmpeg portátil / instalado / PATH ---
    def detect_ffmpeg(self):
        exe_names = ["ffmpeg.exe", "ffmpeg"]
        search_dirs = [APP_DIR, APP_DIR / "ffmpeg", APP_DIR / "ffmpeg" / "bin"]

        try:
            exe_dir = Path(sys.executable).resolve().parent
            if exe_dir not in search_dirs:
                search_dirs.append(exe_dir)
            search_dirs += [exe_dir / "ffmpeg", exe_dir / "ffmpeg" / "bin"]
        except Exception:
            pass

        def _is_valid_ffmpeg(path_str: str) -> bool:
            try:
                subprocess.run([path_str, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                return True
            except Exception:
                return False

        for d in search_dirs:
            try:
                for name in exe_names:
                    cand = d / name
                    if cand.exists() and _is_valid_ffmpeg(str(cand)):
                        return str(cand)
            except Exception:
                continue

        p = shutil.which("ffmpeg")
        if p and _is_valid_ffmpeg(p):
            return p
        return None

    def set_output(self, d):
        if d and os.path.isdir(d):
            self.output_dir = d
            self.ui.set_status(f"Carpeta: {d}")
            return True
        return False

    def normalize_single_video_url(self, url: str) -> str:
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

        if not url:
            return url
        u = urlparse(url.strip())
        q = parse_qs(u.query)
        vid = None
        if "v" in q and q["v"]:
            vid = q["v"][0]
        else:
            parts = [p for p in u.path.split("/") if p]
            if u.netloc.endswith("youtu.be") and parts:
                vid = parts[0]
            elif "shorts" in parts and len(parts) >= 2:
                vid = parts[1]
            elif "embed" in parts and len(parts) >= 2:
                vid = parts[1]
        t = q.get("t", q.get("start", []))
        if vid:
            nq = {}
            if t:
                nq["t"] = t[0]
            base = urlunparse(("https", "www.youtube.com", "/watch", "", urlencode(nq), ""))
            return base + (("&" if nq else "?") + f"v={vid}")
        return url

    # --- detección anti-bot ---
    @staticmethod
    def is_antibot_error(text: str) -> bool:
        t = (text or "").lower()
        triggers = [
            "sign in to confirm you're not a bot",
            "confirm you're not a bot",
            "use --cookies-from-browser",
            "use --cookies",
            "captcha",
            "http error 429",
            "too many requests",
            "http error 403",
            "forbidden",
        ]
        return any(x in t for x in triggers)

    # --- aplicar cookies si existen ---
    def apply_cookiefile_if_exists(self, y: dict) -> dict:
        if COOKIES_TXT.exists():
            y["cookiefile"] = str(COOKIES_TXT)
        return y

    # --- opciones anti-bot light ---
    def apply_safety_opts(self, y: dict) -> dict:
        # Reduce probabilidad de bloqueo
        y.setdefault("retries", 5)
        y.setdefault("extractor_retries", 5)
        y.setdefault("file_access_retries", 3)
        y.setdefault("fragment_retries", 5)
        y.setdefault("sleep_requests", 1)
        y.setdefault("sleep_interval", 1)
        y.setdefault("max_sleep_interval", 3)
        y.setdefault("concurrent_fragment_downloads", 1)
        headers = y.setdefault("http_headers", {})
        headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
        headers.setdefault("Accept-Language", "es-ES,es;q=0.9,en;q=0.8")
        headers.setdefault("Referer", "https://www.youtube.com/")
        self.apply_js_runtime_opts(y)
        self.apply_pot_provider_opts(y)
        if self.restricted_network_mode:
            self.apply_restricted_network_opts(y)
        return y

    def list_formats(self, url):
        url = self.normalize_single_video_url(url)
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            # No validar cada URL en el listado: así no se ocultan formatos altos
            # que YouTube marca inestables de forma temporal (SABR/PO token).
            "check_formats": False,
            "noplaylist": True,
            "playlist_items": "1",
            "no_color": True,
            "verbose": True,
            "logger": YDLLogger("list_formats"),
        }
        self.apply_safety_opts(opts)
        # cookies si existen
        if COOKIES_TXT.exists():
            opts["cookiefile"] = str(COOKIES_TXT)
        self._log_context("list_formats", url, opts)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            append_log("list_formats exception:\n" + traceback.format_exc())
            raise

        videos, audios, progressives = [], [], []

        for f in info.get("formats", []) or []:
            v = f.get("vcodec")
            a = f.get("acodec")
            fmt_id = f.get("format_id")

            # Progresivo: tiene video+audio
            if v != "none" and a != "none":
                progressives.append({
                    "id": fmt_id,
                    "ext": f.get("ext"),
                    "height": f.get("height"),
                    "fps": f.get("fps"),
                    "vcodec": v,
                    "acodec": a,
                    "tbr": f.get("tbr"),
                })

            # Solo video
            elif v != "none" and a == "none":
                videos.append({
                    "id": fmt_id,
                    "ext": f.get("ext"),
                    "height": f.get("height"),
                    "fps": f.get("fps"),
                    "vcodec": v,
                })

            # Solo audio
            elif a != "none" and v == "none":
                audios.append({
                    "id": fmt_id,
                    "ext": f.get("ext"),
                    "abr": f.get("abr") or f.get("tbr"),
                    "acodec": a,
                })

        def _dedupe_prefer_non_drc(items):
            by_base = {}
            for it in items:
                fid = str(it.get("id") or "")
                base = self._strip_drc_suffix(fid)
                prev = by_base.get(base)
                if prev is None:
                    by_base[base] = it
                    continue
                prev_id = str(prev.get("id") or "")
                if prev_id.endswith("-drc") and not fid.endswith("-drc"):
                    by_base[base] = it
            return list(by_base.values())

        videos = _dedupe_prefer_non_drc(videos)
        progressives = _dedupe_prefer_non_drc(progressives)
        audios = _dedupe_prefer_non_drc(audios)

        videos.sort(key=lambda v: (v["height"] or 0, v["fps"] or 0), reverse=True)
        progressives.sort(key=lambda v: (v["height"] or 0, v.get("tbr") or 0), reverse=True)
        audios.sort(key=lambda a: (a["abr"] or 0), reverse=True)

        return info, videos, audios, progressives

    # --- helpers para localizar el archivo final ---
    def _sanitize_title(self, t: str) -> str:
        if not t:
            return ""
        bad = '<>:"/\\|?*'
        for ch in bad:
            t = t.replace(ch, "_")
        return t

    def _guess_final_path(self, job, ext_hint=None):
        title = self._sanitize_title(job.get("title") or "")
        if not title:
            return None
        try:
            names = os.listdir(self.output_dir)
        except Exception:
            return None

        candidates = []
        for name in names:
            full = os.path.join(self.output_dir, name)
            if not os.path.isfile(full):
                continue
            if not name.lower().startswith(title.lower()):
                continue
            if ext_hint and not name.lower().endswith("." + ext_hint.lower()):
                continue
            candidates.append(full)

        if not candidates:
            for name in names:
                full = os.path.join(self.output_dir, name)
                if os.path.isfile(full) and name.lower().startswith(title.lower()):
                    candidates.append(full)

        if not candidates:
            return None
        return max(candidates, key=lambda p: os.path.getmtime(p))

    def _make_progress_hook(self, job):
        def hook(d):
            status = d.get("status")
            filename = d.get("filename") or d.get("info_dict", {}).get("_filename")
            if filename:
                job["path"] = filename
            if status == "downloading":
                tot = d.get("total_bytes") or d.get("total_bytes_estimate")
                got = d.get("downloaded_bytes", 0)
                pct = int(got * 100 / tot) if tot else 0
                self.ui.after(0, lambda: self.ui.update_job_progress(job, pct))
        return hook

    def _retry_if_format_missing(self, exc_text, y, is_video, container, convertir):
        if "Requested format is not available" not in (exc_text or ""):
            return False
        append_log(f"retry_if_format_missing | is_video={is_video} | err={exc_text}")
        self.ui.after(0, lambda: self.ui._toast("El formato elegido ya no está. Probando el mejor compatible…"))
        if is_video:
            y["format"] = self._safe_video_format_selector()
            if self.ffmpeg_path:
                if not convertir:
                    y["merge_output_format"] = container
                else:
                    y.setdefault("postprocessors", []).append({"key": "FFmpegVideoRemuxer", "preferedformat": container})
        else:
            y["format"] = "bestaudio/bestaudio[ext=m4a]/bestaudio[ext=webm]"
        return True

    def _retry_if_empty_file(self, exc_text, y, is_video, container, convertir):
        s = (exc_text or "").lower()
        if "downloaded file is empty" not in s:
            return False
        append_log(f"retry_if_empty_file | is_video={is_video} | err={exc_text}")

        self.ui.after(0, lambda: self.ui._toast("El archivo llegó vacío. Reintentando con formato compatible…", 2400))

        if is_video:
            # Fallback robusto: evitar IDs concretos que pueden fallar en descarga real.
            y["format"] = self._safe_video_format_selector()
            if self.ffmpeg_path:
                if convertir:
                    y["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": container}]
                else:
                    y["merge_output_format"] = container
        else:
            y["format"] = "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio"
        return True

    def _retry_if_forbidden(self, exc_text, y, is_video):
        s = (exc_text or "").lower()
        if "http error 403" not in s and "forbidden" not in s:
            return False
        append_log(f"retry_if_forbidden | is_video={is_video} | err={exc_text}")

        self.ui.after(0, lambda: self.ui._toast("YouTube respondió 403. Reintentando con perfil alternativo…", 2400))

        # Cookies de sesión caducadas a veces fuerzan 403: probamos sin cookiefile.
        if y.pop("cookiefile", None):
            self.ui.after(0, lambda: self.ui._toast("Probando sin cookies guardadas…", 2000))

        self.set_restricted_network_mode(True, reason="auto_forbidden")
        self.apply_restricted_network_opts(y)
        self.ui.after(0, lambda: self.ui.force_restricted_mode(True, auto=True))

        if is_video:
            # En redes bloqueadas, prioriza progresivo (video+audio) para mayor tasa de éxito.
            y["format"] = (
                "best[ext=mp4][acodec!=none][vcodec!=none][protocol^=http]/"
                "18/best[acodec!=none][vcodec!=none]/best"
            )
        else:
            y["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        return True

    # ---------- DESCARGA VIDEO ----------
    def download_video(self, url, vid_id, aud_id, container, convertir, job, on_done):
        def run():
            try:
                self.ui.after(0, self.ui.on_download_start)
                url_final = self.normalize_single_video_url(url)

                y = {
                    "outtmpl": os.path.join(self.output_dir, "%(title)s.%(ext)s"),
                    "noplaylist": True,
                    "playlist_items": "1",
                    "progress_hooks": [self._make_progress_hook(job)],
                    "quiet": True,
                    "no_warnings": True,
                    "no_color": True,
                    "overwrites": True,
                    "verbose": True,
                    "logger": YDLLogger("download_video"),
                }

                self.apply_cookiefile_if_exists(y)
                self.apply_safety_opts(y)

                if self.ffmpeg_path:
                    y["ffmpeg_location"] = str(Path(self.ffmpeg_path).parent)

                if self.ffmpeg_path:
                    # Intentamos SIEMPRE lo elegido por el usuario primero.
                    if vid_id and aud_id:
                        y["format"] = self._build_video_format_chain(vid_id, aud_id)
                    elif vid_id:
                        y["format"] = self._build_video_format_chain(vid_id, None)
                    else:
                        y["format"] = self._safe_video_format_selector()

                    if convertir:
                        y.setdefault("postprocessors", []).append({"key": "FFmpegVideoRemuxer", "preferedformat": container})
                    else:
                        y["merge_output_format"] = container
                else:
                    y["format"] = self._safe_video_format_selector()
                    self.ui.after(0, lambda: self.ui._toast("FFmpeg no detectado: usaré formato progresivo (sin unir)."))

                self._log_context("download_video", url_final, y)
                try:
                    with yt_dlp.YoutubeDL(y) as ydl:
                        ydl.download([url_final])
                except yt_dlp.utils.DownloadError as e:
                    s = str(e)
                    append_log(f"download_video DownloadError: {s}")
                    retry = False
                    # Primer fallback inteligente: mantener el video elegido y pedir mejor audio disponible.
                    if vid_id and "Requested format is not available" in s:
                        y["format"] = self._build_video_format_chain(vid_id, None)
                        append_log(f"download_video retry_keep_video_id | format={y['format']}")
                        retry = True
                    if not retry:
                        retry = self._retry_if_format_missing(s, y, True, container, convertir)
                    if not retry:
                        retry = self._retry_if_empty_file(s, y, True, container, convertir)
                    if not retry:
                        retry = self._retry_if_forbidden(s, y, True)
                    if retry:
                        self._log_context("download_video_retry", url_final, y)
                        with yt_dlp.YoutubeDL(y) as ydl:
                            ydl.download([url_final])
                    else:
                        raise

                final_path = self._guess_final_path(job, container.lower() if container else None)
                if final_path:
                    job["path"] = final_path

                self.ui.after(0, lambda: self.ui.mark_job_finished(job))
                self.ui.after(0, lambda: self.ui.set_status("Descarga completada ✅"))
            except Exception as e:
                msg = str(e)
                append_log("download_video exception:\n" + traceback.format_exc())
                self.ui.after(0, lambda m=msg: self.ui.set_status(f"Error: {m}"))
                self.ui.after(0, lambda m=msg: self.ui.mark_job_error(job, m))

                # detección automática anti-bot -> popup login
                if self.is_antibot_error(msg):
                    self.ui.after(0, lambda: self.ui.show_antibot_popup(msg))
                else:
                    self.ui.after(0, lambda m=msg: messagebox.showerror("Error", f"{m}\n\nLog: {LOG_FILE}"))
            finally:
                self.ui.after(0, on_done)

        threading.Thread(target=run, daemon=True).start()

    # ---------- DESCARGA AUDIO ----------
    def download_audio(self, url, aud_id, codec, job, on_done):
        def run():
            try:
                self.ui.after(0, self.ui.on_download_start)
                url_final = self.normalize_single_video_url(url)

                y = {
                    "outtmpl": os.path.join(self.output_dir, "%(title)s.%(ext)s"),
                    "noplaylist": True,
                    "playlist_items": "1",
                    "progress_hooks": [self._make_progress_hook(job)],
                    "quiet": True,
                    "no_warnings": True,
                    "no_color": True,
                    "overwrites": True,
                    "verbose": True,
                    "logger": YDLLogger("download_audio"),
                }

                self.apply_cookiefile_if_exists(y)
                self.apply_safety_opts(y)

                if self.ffmpeg_path:
                    y["ffmpeg_location"] = str(Path(self.ffmpeg_path).parent)
                    y["format"] = aud_id
                    y["postprocessors"] = [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": codec.lower(),
                        "preferredquality": "192",
                    }]
                else:
                    y["format"] = "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio"
                    self.ui.after(0, lambda: self.ui._toast("FFmpeg no detectado: audio sin convertir (m4a/webm)."))

                self._log_context("download_audio", url_final, y)
                try:
                    with yt_dlp.YoutubeDL(y) as ydl:
                        ydl.download([url_final])
                except yt_dlp.utils.DownloadError as e:
                    s = str(e)
                    append_log(f"download_audio DownloadError: {s}")
                    retry = self._retry_if_format_missing(s, y, False, None, False)
                    if not retry:
                        retry = self._retry_if_forbidden(s, y, False)
                    if retry:
                        self._log_context("download_audio_retry", url_final, y)
                        with yt_dlp.YoutubeDL(y) as ydl:
                            ydl.download([url_final])
                    else:
                        raise

                ext_hint = codec.lower() if self.ffmpeg_path else None
                final_path = self._guess_final_path(job, ext_hint)
                if final_path:
                    job["path"] = final_path

                self.ui.after(0, lambda: self.ui.mark_job_finished(job))
                self.ui.after(0, lambda: self.ui.set_status("Descarga completada ✅"))
            except Exception as e:
                msg = str(e)
                append_log("download_audio exception:\n" + traceback.format_exc())
                self.ui.after(0, lambda m=msg: self.ui.set_status(f"Error: {m}"))
                self.ui.after(0, lambda m=msg: self.ui.mark_job_error(job, m))

                if self.is_antibot_error(msg):
                    self.ui.after(0, lambda: self.ui.show_antibot_popup(msg))
                else:
                    self.ui.after(0, lambda m=msg: messagebox.showerror("Error", f"{m}\n\nLog: {LOG_FILE}"))
            finally:
                self.ui.after(0, on_done)

        threading.Thread(target=run, daemon=True).start()


# ================== UI principal ==================
class UI(tk.Frame):
    YT_REGEX = re.compile(
        r"(https?://)?(www\.)?(youtube\.com/watch\?v=[\w\-]{6,}|youtu\.be/[\w\-]{6,}|youtube\.com/shorts/[\w\-]{6,})",
        re.IGNORECASE,
    )

    def __init__(self, root):
        super().__init__(root.content, bg=root.palette["panel"])
        self.root = root
        apply_theme(root)
        self.pack(fill="both", expand=True)
        self.root.lbl_title.config(text="Cassettify")

        self.jobs = []
        self.job_counter = 0
        self._active_downloads = 0
        self._jobs_scroll_visible = False
        self._tutorial_panel = None
        self._tutorial_steps = []
        self._tutorial_step_index = 0
        self._tutorial_highlight_widget = None
        self._tutorial_highlight_orig = {}
        self._tutorial_highlight_after = None
        self._tutorial_pulse_on = False

        # encabezado visual
        hero = tk.Frame(self, bg=root.palette["panel"])
        hero.pack(padx=20, pady=(12, 6), fill="x")
        tk.Label(
            hero,
            text="Descargas limpias de video y audio, en un solo lugar.",
            fg=root.palette["text_muted"],
            bg=root.palette["panel"],
            font=self.root.fonts["SUB"],
            anchor="center",
            justify="center",
        ).pack(anchor="center", pady=(2, 4))

        # fila URL
        top = tk.Frame(self, bg=root.palette["panel"])
        top.pack(padx=20, pady=(10, 10), fill="x")
        shadow = tk.Frame(top, bg=root.palette["btn_shadow"])
        shadow.pack(side="left", padx=(0, 12), fill="x", expand=True)
        entry_wrap = tk.Frame(shadow, bg=root.palette["panel"])
        entry_wrap.pack(padx=(2, 0), pady=(2, 0), fill="x", expand=True)
        self.url_shadow = shadow
        self.url_wrap = entry_wrap
        self.url = ttk.Entry(entry_wrap, style="Cass.TEntry", font=self.root.fonts["MONO"])
        self.url.pack(fill="x", expand=True, ipady=2)

        # menú contextual
        self._url_menu = tk.Menu(self, tearoff=0)
        self._url_menu.add_command(label="Pegar", command=lambda: self.url.event_generate("<<Paste>>"))
        self._url_menu.add_command(label="Copiar", command=lambda: self.url.event_generate("<<Copy>>"))
        self._url_menu.add_command(label="Cortar", command=lambda: self.url.event_generate("<<Cut>>"))

        def _show_menu(e):
            try:
                self._url_menu.tk_popup(e.x_root, e.y_root)
            finally:
                self._url_menu.grab_release()

        self.url.bind("<Button-3>", _show_menu)
        self.url.bind("<Button-2>", _show_menu)

        # botones acción
        btns = tk.Frame(self, bg=root.palette["panel"])
        btns.pack(pady=(0, 10))
        self.actions_row = btns

        self.btn_video_shadow = tk.Frame(btns, bg=root.palette["btn_shadow"])
        self.btn_audio_shadow = tk.Frame(btns, bg=root.palette["btn_shadow"])
        self.btn_login_shadow = tk.Frame(btns, bg=root.palette["btn_shadow"])
        self.btn_help_shadow = tk.Frame(btns, bg=root.palette["btn_shadow"])

        self.btn_video_shadow.grid(row=0, column=0, padx=8, pady=(2, 0))
        self.btn_audio_shadow.grid(row=0, column=1, padx=8, pady=(2, 0))
        self.btn_login_shadow.grid(row=0, column=2, padx=8, pady=(2, 0))
        self.btn_help_shadow.grid(row=0, column=3, padx=8, pady=(2, 0))

        self.btn_video = ttk.Button(self.btn_video_shadow, text="►  Video", style="Cass.TButton", command=self.on_video)
        self.btn_audio = ttk.Button(self.btn_audio_shadow, text="♪  Audio", style="Cass.TButton", command=self.on_audio)
        self.btn_login = ttk.Button(self.btn_login_shadow, text="🔑  Iniciar sesión", style="Cass.Secondary.TButton", command=self.on_login)
        self.btn_help = ttk.Button(self.btn_help_shadow, text="❔  Tutorial", style="Cass.Secondary.TButton", command=self.show_tutorial)

        self.btn_video.pack(padx=(2, 0), pady=(2, 0))
        self.btn_audio.pack(padx=(2, 0), pady=(2, 0))
        self.btn_login.pack(padx=(2, 0), pady=(2, 0))
        self.btn_help.pack(padx=(2, 0), pady=(2, 0))

        def hover_in(w):
            w.master.configure(bg=self.root.palette["btn_shadow_hover"])
            w.configure(cursor="hand2")

        def hover_out(w):
            w.master.configure(bg=self.root.palette["btn_shadow"])
            w.configure(cursor="")

        for b in (self.btn_video, self.btn_audio, self.btn_login, self.btn_help):
            b.bind("<Enter>", lambda e, bb=b: hover_in(bb))
            b.bind("<Leave>", lambda e, bb=b: hover_out(bb))

        # estado
        info = tk.Frame(self, bg=root.palette["panel"])
        info.pack(padx=20, pady=(6, 4), fill="x")
        self.info_row = info

        self.status = tk.Label(info, text="Listo", fg=root.palette["text"], bg=root.palette["panel"], font=self.root.fonts["UI"], anchor="w")
        self.status.pack(side="left", fill="x", expand=True)

        self.session_lbl = tk.Label(info, text="", fg=root.palette["text_muted"], bg=root.palette["panel"], font=self.root.fonts["UI_B"], anchor="e")
        self.session_lbl.pack(side="right")
        self.restricted_mode_var = tk.BooleanVar(value=False)
        self.chk_restricted_mode = tk.Checkbutton(
            info,
            text="Modo red restringida",
            variable=self.restricted_mode_var,
            onvalue=True,
            offvalue=False,
            command=self._on_toggle_restricted_mode,
            fg=root.palette["text_muted"],
            bg=root.palette["panel"],
            activeforeground=root.palette["text"],
            activebackground=root.palette["panel"],
            selectcolor=root.palette["panel"],
            highlightthickness=0,
            bd=0,
            font=self.root.fonts["UI"],
        )
        self.chk_restricted_mode.pack(side="right", padx=(0, 14))

        # progreso global
        self.pb = GlowProgress(self, height=16, bg=root.palette["panel"])
        self.pb.bg = root.palette["surface"]
        self.pb.fill = root.palette["accent"]
        self.pb.glow = root.palette["glow"]
        self.pb.pack(padx=20, fill="x", pady=(0, 10))

        # lista de descargas
        self.jobs_wrap = tk.Frame(
            self,
            bg=root.palette["surface"],
            highlightthickness=1,
            highlightbackground=root.palette["outline"],
        )
        self.jobs_wrap.pack(padx=20, pady=(0, 16), fill="both", expand=True)
        tk.Label(
            self.jobs_wrap,
            text="Descargas (actuales y completadas)",
            fg=root.palette["title"],
            bg=root.palette["surface"],
            font=self.root.fonts["UI_B"],
        ).pack(anchor="w", padx=10, pady=(8, 6))
        self.jobs_body = tk.Frame(self.jobs_wrap, bg=self.root.palette["surface"])
        self.jobs_body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.jobs_canvas = tk.Canvas(
            self.jobs_body,
            bg=self.root.palette["surface"],
            highlightthickness=0,
            bd=0,
        )
        self.jobs_scroll = ttk.Scrollbar(self.jobs_body, orient="vertical", command=self.jobs_canvas.yview)
        self.jobs_canvas.configure(yscrollcommand=self.jobs_scroll.set)
        self.jobs_canvas.pack(side="left", fill="both", expand=True)

        self.jobs_container = tk.Frame(self.jobs_canvas, bg=self.root.palette["surface"])
        self.jobs_window = self.jobs_canvas.create_window((0, 0), window=self.jobs_container, anchor="nw")
        self.jobs_container.bind("<Configure>", self._sync_jobs_scrollregion)
        self.jobs_canvas.bind("<Configure>", self._resize_jobs_container)
        self.jobs_canvas.bind("<Enter>", self._bind_jobs_wheel)
        self.jobs_canvas.bind("<Leave>", self._unbind_jobs_wheel)
        self.jobs_container.bind("<Enter>", self._bind_jobs_wheel)
        self.jobs_container.bind("<Leave>", self._unbind_jobs_wheel)
        self.after(120, self._sync_jobs_scrollregion)

        # Core
        self.core = Core(self)

        # Estado de FFmpeg
        if self.core.ffmpeg_path:
            self._toast(f"FFmpeg detectado: {self.core.ffmpeg_path}", 2200)
        else:
            self._toast("FFmpeg no detectado: usaré formatos progresivos.", 2200)

        # Portapapeles
        self._last_clip = ""
        self._clipboard_watcher()

        # Atajos
        self.url.bind("<Return>", self._shortcut_video_from_url)
        self.url.bind("<Control-Return>", self._shortcut_audio_from_url)
        self.root.bind("<Control-Shift-U>", lambda _e: self.run_update_dependencies())

        # Estado sesión inicial + refresco
        self.refresh_session_status()
        self.after(2500, self._session_watcher)
        self.after(800, self._auto_show_tutorial_once)
        self.after(1200, self._startup_dependency_check)

    def _dependency_missing(self):
        missing = []
        if not self.core.ffmpeg_path:
            missing.append("FFmpeg")
        if not self.core.js_runtime:
            missing.append("Deno (runtime JS)")
        if not self.core.pot_script_path:
            missing.append("PO Token provider (bgutil script)")
        try:
            import yt_dlp_ejs  # noqa: F401
        except Exception:
            missing.append("yt-dlp-ejs")
        if getattr(sys, "frozen", False) and not (shutil.which("python") or shutil.which("py")):
            missing.append("Python (para auto-actualizaciones)")
        return missing

    def _launch_ps_script(self, script_path: Path):
        if not script_path.exists():
            messagebox.showwarning("Dependencias", f"No se encontró el script:\n{script_path}")
            return False
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            messagebox.showerror("Dependencias", "No se encontró PowerShell para ejecutar la instalación.")
            return False
        try:
            subprocess.Popen([shell, "-ExecutionPolicy", "Bypass", "-File", str(script_path)], cwd=str(APP_DIR))
            return True
        except Exception as e:
            messagebox.showerror("Dependencias", f"No pude iniciar el script:\n{e}")
            return False

    def _startup_dependency_check(self):
        missing = self._dependency_missing()
        if not missing:
            return
        msg = (
            "Faltan o no se detectan algunos componentes:\n\n"
            + "\n".join(f"• {m}" for m in missing)
            + "\n\n¿Quieres ejecutar ahora bootstrap.ps1 para instalar/reparar?"
        )
        if messagebox.askyesno("Dependencias", msg):
            if self._launch_ps_script(BOOTSTRAP_SCRIPT):
                self._toast("Instalador de dependencias iniciado…", 2600)

    def run_update_dependencies(self):
        if not UPDATE_DEPS_SCRIPT.exists():
            messagebox.showwarning("Actualizar", f"No se encontró:\n{UPDATE_DEPS_SCRIPT}")
            return
        if messagebox.askyesno("Actualizar componentes", "Se abrirá el actualizador de dependencias.\n\n¿Continuar?"):
            if self._launch_ps_script(UPDATE_DEPS_SCRIPT):
                self._toast("Actualizador iniciado…", 2400)

    # ---------- estado sesión ----------
    def refresh_session_status(self):
        ok = COOKIES_TXT.exists() and COOKIES_TXT.stat().st_size > 80
        self.session_lbl.config(
            text=f"Sesión YouTube: {'✅' if ok else '❌'}",
            fg=self.root.palette["success"] if ok else self.root.palette["error"],
        )

    def _session_watcher(self):
        # refresca periódicamente por si se acaba de guardar sesión
        self.refresh_session_status()
        self.after(2500, self._session_watcher)

    def _set_jobs_scrollbar_visible(self, visible: bool):
        if visible and not self._jobs_scroll_visible:
            self.jobs_scroll.pack(side="right", fill="y")
            self._jobs_scroll_visible = True
        elif not visible and self._jobs_scroll_visible:
            self.jobs_scroll.pack_forget()
            self._jobs_scroll_visible = False

    def _sync_jobs_scrollregion(self, _event=None):
        self.jobs_canvas.configure(scrollregion=self.jobs_canvas.bbox("all"))
        needs_scroll = self.jobs_container.winfo_reqheight() > self.jobs_canvas.winfo_height() + 2
        self._set_jobs_scrollbar_visible(needs_scroll)

    def _resize_jobs_container(self, event):
        self.jobs_canvas.itemconfigure(self.jobs_window, width=event.width)
        self._sync_jobs_scrollregion()

    def _bind_jobs_wheel(self, _event=None):
        self.jobs_canvas.bind("<MouseWheel>", self._on_jobs_mousewheel)
        self.jobs_container.bind("<MouseWheel>", self._on_jobs_mousewheel)

    def _unbind_jobs_wheel(self, _event=None):
        self.jobs_canvas.unbind("<MouseWheel>")
        self.jobs_container.unbind("<MouseWheel>")

    def _on_jobs_mousewheel(self, event):
        if not self._jobs_scroll_visible:
            return
        delta = -int(event.delta / 120) if getattr(event, "delta", 0) else 0
        if delta != 0:
            self.jobs_canvas.yview_scroll(delta, "units")
            return "break"

    # ---------- UX helpers ----------
    def _shortcut_video_from_url(self, _event=None):
        self.on_video()
        return "break"

    def _shortcut_audio_from_url(self, _event=None):
        self.on_audio()
        return "break"

    def _toast(self, text, ms=1800):
        try:
            Toast(self.root, text, duration=ms)
        except Exception as e:
            print(f"[toast] {e}", file=sys.stderr)

    def _on_toggle_restricted_mode(self):
        enabled = bool(self.restricted_mode_var.get())
        self.core.set_restricted_network_mode(enabled, reason="ui_toggle")
        if enabled:
            self.set_status("Modo red restringida activado")
            self._toast("Modo red restringida activado", 1800)
        else:
            self.set_status("Modo red restringida desactivado")
            self._toast("Modo red restringida desactivado", 1800)

    def force_restricted_mode(self, enabled: bool, auto: bool = False):
        self.restricted_mode_var.set(bool(enabled))
        self.core.set_restricted_network_mode(bool(enabled), reason="auto_forbidden" if auto else "force")
        if enabled and auto:
            self.set_status("Red restringida detectada: modo protegido activado")
            self._toast("Activé modo red restringida por bloqueo de YouTube", 2400)

    def _play_done_sound(self):
        if platform.system() != "Windows":
            return
        if not COMPLETION_SOUND or not os.path.exists(COMPLETION_SOUND):
            return
        if winsound is None:
            return
        try:
            winsound.PlaySound(COMPLETION_SOUND, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as e:
            print(f"[sound] {e}", file=sys.stderr)

    def _play_error_sound(self):
        if platform.system() != "Windows":
            return
        if not ERROR_SOUND or not os.path.exists(ERROR_SOUND):
            return
        if winsound is None:
            return
        try:
            winsound.PlaySound(ERROR_SOUND, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as e:
            print(f"[sound-error] {e}", file=sys.stderr)

    def _tutorial_seen(self):
        try:
            return TUTORIAL_FLAG.exists()
        except Exception:
            return False

    def _mark_tutorial_seen(self):
        try:
            TUTORIAL_FLAG.write_text("seen", encoding="utf-8")
        except Exception:
            pass

    def _auto_show_tutorial_once(self):
        if not self._tutorial_seen():
            self.show_tutorial()

    def _tutorial_is_open(self):
        return self._tutorial_panel is not None and self._tutorial_panel.winfo_exists()

    def _tutorial_build_steps(self):
        return [
            {
                "title": "1/10 - Pega el enlace",
                "body": "Copia un enlace de YouTube y pégalo arriba (Ctrl+V). Si no sabes copiar: selecciona el enlace, clic derecho y 'Copiar'.",
                "target": self.url_wrap,
            },
            {
                "title": "2/10 - Elige qué quieres",
                "body": "'Video' guarda imagen+sonido. 'Audio' guarda solo sonido (música/voz). Si tienes duda, empieza con 'Audio'.",
                "target": self.actions_row,
            },
            {
                "title": "3/10 - Formatos de video",
                "body": "En 'Video' verás calidad (1080p, 720p...). Mayor número = mejor imagen y más peso del archivo.",
                "target": self.actions_row,
            },
            {
                "title": "4/10 - Formatos de audio",
                "body": "En 'Audio' puedes elegir salida: MP3 (más compatible), M4A (buena calidad/tamaño), FLAC/WAV (más pesados).",
                "target": self.actions_row,
            },
            {
                "title": "5/10 - Carpeta de descarga",
                "body": "Al aceptar formato, la app te pedirá carpeta. Elige dónde quieres guardar (por ejemplo Escritorio) y confirma.",
                "target": self.url_wrap,
            },
            {
                "title": "6/10 - Ruta predeterminada",
                "body": "Si marcas 'Usar como predeterminada esta sesión', las siguientes descargas irán a la misma carpeta automáticamente.",
                "target": self.url_wrap,
            },
            {
                "title": "7/10 - Iniciar sesión si hace falta",
                "body": "Si YouTube bloquea o pide verificación, pulsa 'Iniciar sesión' y guarda sesión. A la derecha verás ✅ si está activa.",
                "target": self.info_row,
            },
            {
                "title": "8/10 - Revisa el progreso",
                "body": "La barra muestra avance. 'Listo' significa que no hay descargas activas en ese momento.",
                "target": self.pb,
            },
            {
                "title": "9/10 - Historial de descargas",
                "body": "Cada descarga aparece abajo con su estado. Si hay muchas, desplázate con la rueda del ratón o la barra lateral.",
                "target": self.jobs_wrap,
            },
            {
                "title": "10/10 - Abrir y solucionar errores",
                "body": "Cuando termine: usa 'Abrir' para el archivo y 'Carpeta' para su ubicación. Si falla, reintenta y revisa el log en AppData/Local/Cassettify/cassettify.log.",
                "target": self.jobs_wrap,
            },
        ]

    def _tutorial_clear_highlight(self):
        if self._tutorial_highlight_after:
            try:
                self.after_cancel(self._tutorial_highlight_after)
            except Exception:
                pass
        self._tutorial_highlight_after = None

        w = self._tutorial_highlight_widget
        if w is not None and w.winfo_exists():
            try:
                if "highlightthickness" in w.keys() and "highlightthickness" in self._tutorial_highlight_orig:
                    w.configure(highlightthickness=self._tutorial_highlight_orig["highlightthickness"])
                if "highlightbackground" in w.keys() and "highlightbackground" in self._tutorial_highlight_orig:
                    w.configure(highlightbackground=self._tutorial_highlight_orig["highlightbackground"])
                if "highlightcolor" in w.keys() and "highlightcolor" in self._tutorial_highlight_orig:
                    w.configure(highlightcolor=self._tutorial_highlight_orig["highlightcolor"])
                if "bg" in w.keys() and "bg" in self._tutorial_highlight_orig:
                    w.configure(bg=self._tutorial_highlight_orig["bg"])
            except Exception:
                pass

        self._tutorial_highlight_widget = None
        self._tutorial_highlight_orig = {}

    def _tutorial_pulse_tick(self):
        w = self._tutorial_highlight_widget
        if w is None or not w.winfo_exists():
            return
        try:
            if "highlightbackground" in w.keys():
                color = self.root.palette["accent"] if self._tutorial_pulse_on else self.root.palette["outline"]
                w.configure(highlightbackground=color)
            if "highlightcolor" in w.keys():
                w.configure(highlightcolor=self.root.palette["accent"])
            self._tutorial_pulse_on = not self._tutorial_pulse_on
            self._tutorial_highlight_after = self.after(380, self._tutorial_pulse_tick)
        except Exception:
            self._tutorial_highlight_after = None

    def _tutorial_set_highlight(self, widget):
        self._tutorial_clear_highlight()
        if widget is None or not widget.winfo_exists():
            return
        self._tutorial_highlight_widget = widget
        try:
            if "highlightthickness" in widget.keys():
                self._tutorial_highlight_orig["highlightthickness"] = widget.cget("highlightthickness")
            if "highlightbackground" in widget.keys():
                self._tutorial_highlight_orig["highlightbackground"] = widget.cget("highlightbackground")
            if "highlightcolor" in widget.keys():
                self._tutorial_highlight_orig["highlightcolor"] = widget.cget("highlightcolor")
            if "bg" in widget.keys():
                self._tutorial_highlight_orig["bg"] = widget.cget("bg")
        except Exception:
            pass

        try:
            if "highlightthickness" in widget.keys():
                widget.configure(highlightthickness=2)
            if "highlightbackground" in widget.keys():
                widget.configure(highlightbackground=self.root.palette["accent"])
            if "highlightcolor" in widget.keys():
                widget.configure(highlightcolor=self.root.palette["accent"])
        except Exception:
            return

        self._tutorial_pulse_on = False
        self._tutorial_pulse_tick()

    def _tutorial_close_panel(self, mark_seen=False):
        self._tutorial_clear_highlight()
        panel = self._tutorial_panel
        self._tutorial_panel = None
        if mark_seen:
            self._mark_tutorial_seen()
        if panel is not None and panel.winfo_exists():
            try:
                panel.destroy()
            except Exception:
                pass

    def _tutorial_render_step(self):
        if self._tutorial_panel is None or not self._tutorial_panel.winfo_exists():
            return
        step = self._tutorial_steps[self._tutorial_step_index]
        self._tutorial_title_lbl.config(text=step["title"])
        self._tutorial_body_lbl.config(text=step["body"])
        self._tutorial_counter_lbl.config(text=f"Paso {self._tutorial_step_index + 1} de {len(self._tutorial_steps)}")
        self._tutorial_prev_btn.state(["!disabled"] if self._tutorial_step_index > 0 else ["disabled"])
        self._tutorial_next_btn.config(text="Finalizar" if self._tutorial_step_index == len(self._tutorial_steps) - 1 else "Siguiente")
        self._tutorial_set_highlight(step.get("target"))

    def _tutorial_prev(self):
        if self._tutorial_step_index > 0:
            self._tutorial_step_index -= 1
            self._tutorial_render_step()

    def _tutorial_next(self):
        if self._tutorial_step_index < len(self._tutorial_steps) - 1:
            self._tutorial_step_index += 1
            self._tutorial_render_step()
        else:
            self._tutorial_close_panel(mark_seen=self._tutorial_dont_show.get())

    def show_tutorial(self):
        if self._tutorial_panel is not None and self._tutorial_panel.winfo_exists():
            self._tutorial_panel.lift()
            return

        self._tutorial_steps = self._tutorial_build_steps()
        self._tutorial_step_index = 0
        self._tutorial_dont_show = tk.BooleanVar(value=False)

        panel = tk.Toplevel(self.root)
        self._tutorial_panel = panel
        panel.title("Tutorial guiado")
        panel.configure(bg=self.root.palette["panel"])
        panel.resizable(False, False)
        panel.attributes("-topmost", True)
        panel.geometry(f"460x260+{self.root.winfo_rootx() + self.root.win_w - 490}+{self.root.winfo_rooty() + 100}")

        self._tutorial_title_lbl = tk.Label(panel, fg=self.root.palette["title"], bg=self.root.palette["panel"], font=self.root.fonts["UI_B"], anchor="w")
        self._tutorial_title_lbl.pack(fill="x", padx=14, pady=(12, 4))
        self._tutorial_body_lbl = tk.Label(
            panel,
            fg=self.root.palette["text"],
            bg=self.root.palette["panel"],
            font=self.root.fonts["UI"],
            justify="left",
            wraplength=430,
            anchor="w",
        )
        self._tutorial_body_lbl.pack(fill="x", padx=14, pady=(0, 8))
        self._tutorial_counter_lbl = tk.Label(
            panel,
            fg=self.root.palette["text_muted"],
            bg=self.root.palette["panel"],
            font=self.root.fonts["UI"],
            anchor="w",
        )
        self._tutorial_counter_lbl.pack(fill="x", padx=14, pady=(0, 8))

        tk.Checkbutton(
            panel,
            text="No mostrar al iniciar",
            variable=self._tutorial_dont_show,
            bg=self.root.palette["panel"],
            fg=self.root.palette["text"],
            selectcolor=self.root.palette["surface"],
            font=self.root.fonts["UI"],
        ).pack(anchor="w", padx=14, pady=(0, 10))

        row = tk.Frame(panel, bg=self.root.palette["panel"])
        row.pack(fill="x", padx=14, pady=(0, 12))

        self._tutorial_prev_btn = ttk.Button(row, text="Anterior", style="Cass.Secondary.TButton", command=self._tutorial_prev)
        self._tutorial_prev_btn.pack(side="left")
        self._tutorial_next_btn = ttk.Button(row, text="Siguiente", style="Cass.TButton", command=self._tutorial_next)
        self._tutorial_next_btn.pack(side="left", padx=8)
        ttk.Button(
            row,
            text="Cerrar",
            style="Cass.Secondary.TButton",
            command=lambda: self._tutorial_close_panel(mark_seen=self._tutorial_dont_show.get()),
        ).pack(side="right")

        panel.protocol("WM_DELETE_WINDOW", lambda: self._tutorial_close_panel(mark_seen=self._tutorial_dont_show.get()))
        self._tutorial_render_step()

    # ---------- Portapapeles ----------
    def _clipboard_watcher(self):
        try:
            s = self.clipboard_get()
        except Exception:
            s = ""
        if s != self._last_clip:
            self._last_clip = s
            if self.YT_REGEX.search(s) and not self.url.get().strip():
                self.url.delete(0, tk.END)
                self.url.insert(0, s.strip())
                self._toast("Enlace detectado y pegado 📋")
        self.after(800, self._clipboard_watcher)

    # ---------- UI helpers ----------
    def set_status(self, t):
        self.status.config(text=t)
        self.status.update_idletasks()

    def set_progress(self, p):
        self.pb.set(p)
        self.pb.update_idletasks()

    # selector de carpeta (por sesión)
    def _ask_output_dir_session(self):
        if self.core.remember_output and os.path.isdir(self.core.output_dir):
            return self.core.output_dir
        top = tk.Toplevel(self.root)
        top.title("Carpeta de descarga")
        top.configure(bg=self.root.palette["panel"])
        top.resizable(False, False)
        if not self._tutorial_is_open():
            top.grab_set()

        tk.Label(top, text="Elige carpeta de descarga", fg="#EDE6FF", bg=self.root.palette["panel"], font=self.root.fonts["UI_B"]).pack(padx=14, pady=(12, 8), anchor="w")

        row = tk.Frame(top, bg=self.root.palette["panel"])
        row.pack(padx=14, pady=(0, 10))
        var = tk.StringVar(value=self.core.output_dir)
        e = ttk.Entry(row, style="Cass.TEntry", textvariable=var, width=48)
        e.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(row, text="Examinar", style="Cass.TButton", command=lambda: var.set(filedialog.askdirectory() or var.get())).grid(row=0, column=1)

        remember = tk.BooleanVar(value=False)
        tk.Checkbutton(
            top, text="Usar como predeterminada esta sesión", variable=remember,
            bg=self.root.palette["panel"], fg="#EDE6FF", selectcolor=self.root.palette["surface"], font=self.root.fonts["UI"],
        ).pack(padx=14, pady=(2, 10), anchor="w")

        ttk.Button(top, text="Aceptar", style="Cass.TButton", command=top.destroy).pack(pady=(0, 12))
        top.wait_window()

        folder = var.get().strip()
        if folder:
            if not os.path.isdir(folder):
                try:
                    os.makedirs(folder, exist_ok=True)
                except Exception as e:
                    messagebox.showwarning("Carpeta", f"No pude crear/usar la carpeta:\n{e}")
                    return None
            if not self.core.set_output(folder):
                messagebox.showwarning("Carpeta", "La carpeta seleccionada no es válida.")
                return None
            self.core.remember_output = remember.get()
            return folder
        return None

    # ====== Gestión de lista de descargas ======
    def _create_job(self, title, kind):
        self.job_counter += 1
        job = {"id": self.job_counter, "title": title or "(sin título)", "type": kind, "status": "Preparando…", "progress": 0, "path": None}

        frame = tk.Frame(self.jobs_container, bg=self.root.palette["panel"], highlightthickness=1, highlightbackground=self.root.palette["outline"])
        frame.pack(fill="x", padx=8, pady=4)

        lbl_title = tk.Label(frame, text=f"[{kind}] {job['title']}", fg=self.root.palette["title"], bg=self.root.palette["panel"], font=self.root.fonts["UI"], anchor="w")
        lbl_title.grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 0))

        lbl_status = tk.Label(frame, text=job["status"], fg=self.root.palette["text_muted"], bg=self.root.palette["panel"], font=self.root.fonts["UI"])
        lbl_status.grid(row=1, column=0, sticky="w", padx=8, pady=2)

        lbl_prog = tk.Label(frame, text="0%", fg=self.root.palette["text"], bg=self.root.palette["panel"], font=self.root.fonts["UI_B"])
        # Centrado vertical junto a los botones (ocupa ambas filas de la tarjeta).
        lbl_prog.grid(row=0, column=1, rowspan=2, sticky="ns", padx=8, pady=2)

        btn_open = ttk.Button(frame, text="Abrir", style="Cass.Secondary.TButton", command=lambda j=job: self._open_file(j))
        btn_open.grid(row=0, column=3, rowspan=2, padx=(8, 4), pady=4, sticky="e")

        btn_folder = ttk.Button(frame, text="Carpeta", style="Cass.Secondary.TButton", command=lambda j=job: self._open_in_folder(j))
        btn_folder.grid(row=0, column=4, rowspan=2, padx=(0, 8), pady=4, sticky="e")

        btn_open.state(["disabled"])
        btn_folder.state(["disabled"])

        frame.grid_columnconfigure(0, weight=1)

        job["frame"] = frame
        job["lbl_status"] = lbl_status
        job["lbl_prog"] = lbl_prog
        job["btn_open"] = btn_open
        job["btn_folder"] = btn_folder

        self.jobs.append(job)
        self.after_idle(self._sync_jobs_scrollregion)
        return job

    def update_job_progress(self, job, pct):
        job["progress"] = pct
        job["lbl_prog"].config(text=f"{pct}%")
        job["lbl_status"].config(text="Descargando…")
        self.set_progress(pct)
        self.set_status(f"Descargando… {pct}%")

    def mark_job_finished(self, job):
        job["status"] = "Completado"
        job["progress"] = 100
        job["lbl_prog"].config(text="100%")
        job["lbl_status"].config(text="Completado ✅", fg=self.root.palette["success"])
        job["btn_open"].state(["!disabled"])
        job["btn_folder"].state(["!disabled"])
        self.set_progress(100)

    def mark_job_error(self, job, msg):
        job["status"] = "Error"
        job["lbl_status"].config(text="Error", fg=self.root.palette["error"])
        self._play_error_sound()

    def _open_file(self, job):
        path = job.get("path")
        if not path or not os.path.exists(path):
            messagebox.showwarning("Archivo", "No se ha encontrado el archivo de salida.")
            return
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir el archivo:\n{e}")

    def _open_in_folder(self, job):
        path = job.get("path")
        folder = os.path.dirname(path) if path and os.path.exists(path) else self.core.output_dir
        try:
            if platform.system() == "Windows":
                os.startfile(folder)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir la carpeta:\n{e}")

    # ====== Pop-up anti-bot ======
    def show_antibot_popup(self, err_text: str):
        # Si ya existe sesión, solo avisamos
        if COOKIES_TXT.exists():
            self._toast("YouTube bloqueó la descarga. Prueba a reintentar.", 2400)
            messagebox.showwarning("YouTube", "YouTube ha pedido verificación/bloqueo temporal.\n\n"
                                             "Ya hay sesión guardada, pero puede ser un bloqueo por red/IP.\n"
                                             "Prueba otra vez en 1-2 minutos o cambia de red.")
            return

        top = tk.Toplevel(self.root)
        top.title("YouTube pide verificación")
        top.configure(bg=self.root.palette["panel"])
        top.resizable(False, False)
        if not self._tutorial_is_open():
            top.grab_set()

        tk.Label(
            top,
            text="YouTube ha bloqueado la descarga y pide verificación.",
            fg="#EDE6FF",
            bg=self.root.palette["panel"],
            font=self.root.fonts["UI_B"],
            wraplength=520,
            justify="left",
        ).pack(padx=14, pady=(12, 8), anchor="w")

        tk.Label(
            top,
            text="Solución recomendada: inicia sesión dentro de Cassettify.\nLuego vuelve a intentar la descarga.",
            fg="#EDE6FF",
            bg=self.root.palette["panel"],
            font=self.root.fonts["UI"],
            wraplength=520,
            justify="left",
        ).pack(padx=14, pady=(0, 10), anchor="w")

        row = tk.Frame(top, bg=self.root.palette["panel"])
        row.pack(padx=14, pady=(0, 12), fill="x")

        def do_login():
            top.destroy()
            self.on_login()

        ttk.Button(row, text="🔑 Iniciar sesión", style="Cass.TButton", command=do_login).pack(side="left")
        ttk.Button(row, text="Cerrar", style="Cass.TButton", command=top.destroy).pack(side="left", padx=8)

    # ====== Acciones descarga ======
    def on_video(self):
        url = self.url.get().strip()
        if not url:
            messagebox.showwarning("Falta URL", "Pega o escribe una URL.")
            return
        self._dialog_video(url)

    def on_audio(self):
        url = self.url.get().strip()
        if not url:
            messagebox.showwarning("Falta URL", "Pega o escribe una URL.")
            return
        self._dialog_audio(url)

    def on_login(self):
        try:
            if getattr(sys, "frozen", False):
                # En onefile/onedir: sys.executable es el .exe
                subprocess.Popen([sys.executable, "--login"], cwd=str(APPDATA_DIR))
            else:
                # En modo script: python.exe + ruta_del_script + --login
                subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--login"], cwd=str(APPDATA_DIR))
            self._toast("Se abrió el inicio de sesión 🔑", 2200)
            self._toast("Inicia sesión y pulsa 'Guardar sesión'.", 2400)
        except Exception as e:
            messagebox.showerror("Login", f"No pude abrir el login:\n{e}")

    # hooks globales
    def on_download_start(self):
        self._active_downloads += 1
        if self._active_downloads == 1:
            self.set_status("Preparando…")
            self.set_progress(0)
            self.pb.start()

    def _download_finished(self):
        self._active_downloads = max(0, self._active_downloads - 1)
        if self._active_downloads == 0:
            self.set_status("Listo")
            self.set_progress(0)
            self.pb.stop()
        self.url.delete(0, tk.END)
        self.url.focus_set()
        self._play_done_sound()
        self._toast("Descarga completada ✅")

    # ====== Diálogos ======
    def _dialog_video(self, url):
        self.set_status("Buscando formatos…")

        def worker():
            err, data = None, None
            try:
                data = self.core.list_formats(url)
            except Exception as e:
                err = e
            self.after(0, lambda: self._after_video(url, err, data))

        threading.Thread(target=worker, daemon=True).start()

    def _after_video(self, url, err, data):
        if err:
            msg = str(err)
            if self.core.is_antibot_error(msg):
                self.show_antibot_popup(msg)
                self.set_status("Bloqueado por YouTube")
                return
            messagebox.showerror("Error", f"No pude listar formatos:\n{err}")
            self.set_status("Error")
            return

        info, videos, audios, progressives = data


        win = tk.Toplevel(self.root)
        win.title("Formato de video")
        win.configure(bg=self.root.palette["panel"])
        win.resizable(False, False)
        if not self._tutorial_is_open():
            win.grab_set()

        tk.Label(
            win,
            text=info.get("title", "(sin título)"),
            fg="#EDE6FF",
            bg=self.root.palette["panel"],
            font=self.root.fonts["UI_B"],
            wraplength=520,
            justify="left",
        ).pack(padx=14, pady=(10, 6), anchor="w")

        row = tk.Frame(win, bg=self.root.palette["panel"])
        row.pack(padx=14, pady=6)

        tk.Label(row, text="Video:", fg="#EDE6FF", bg=self.root.palette["panel"], font=self.root.fonts["UI"]).grid(row=0, column=0, sticky="w")
        tk.Label(row, text="Audio:", fg="#EDE6FF", bg=self.root.palette["panel"], font=self.root.fonts["UI"]).grid(row=1, column=0, sticky="w")

        cb_v = ttk.Combobox(row, state="readonly", width=58, style="Cass.TCombobox")
        cb_a = ttk.Combobox(row, state="readonly", width=58, style="Cass.TCombobox")

        video_choices = []

        # Preferimos solo-video para evitar fallos frecuentes en progresivos/SABR.
        for v in videos:
            video_choices.append(("V", v))
        # Fallback: si no hay solo-video, permitimos progresivos para no bloquear.
        if not video_choices:
            for p in progressives:
                video_choices.append(("P", p))
            if video_choices:
                self._toast("No había formatos solo-video; mostrando progresivos como respaldo.", 2600)

        v_vals = []
        for kind, item in video_choices:
            if kind == "P":
                v_vals.append(
                    f'✅ {(item["height"] or "?")}p (fallback video+audio) {item["vcodec"]}+{item["acodec"]} .{item["ext"]}'
                )
            else:
                v_vals.append(
                    f'🎞 {(item["height"] or "?")}p (solo video) {item["vcodec"]} .{item["ext"]}'
                )

        a_vals = [
            f'{int(a.get("abr") or 0)}kbps {a.get("acodec")} .{a.get("ext")}'
            for a in audios
        ] or ["(sin audio separado)"]

        cb_v["values"] = v_vals or ["(sin video)"]
        cb_a["values"] = a_vals

        if v_vals:
            cb_v.current(0)
        if audios:
            cb_a.current(0)


        cb_v.grid(row=0, column=1, padx=8, pady=4)
        cb_a.grid(row=1, column=1, padx=8, pady=4)

        row2 = tk.Frame(win, bg=self.root.palette["panel"])
        row2.pack(padx=14, pady=(4, 10), fill="x")

        tk.Label(row2, text="Contenedor:", fg="#EDE6FF", bg=self.root.palette["panel"], font=self.root.fonts["UI"]).grid(row=0, column=0, sticky="w")
        cb_c = ttk.Combobox(row2, state="readonly", values=["mp4", "mkv", "webm"], width=8, style="Cass.TCombobox")
        cb_c.current(0)
        cb_c.grid(row=0, column=1, padx=8, sticky="w")

        conv = tk.BooleanVar(value=True)
        chk_conv = tk.Checkbutton(
            row2, text="Convertir/Remux con FFmpeg", variable=conv,
            bg=self.root.palette["panel"], fg="#EDE6FF", selectcolor=self.root.palette["surface"], font=self.root.fonts["UI"],
        )
        chk_conv.grid(row=1, column=0, columnspan=2, pady=(6, 0), sticky="w")
        if not self.core.ffmpeg_path:
            conv.set(False)
            chk_conv.configure(state="disabled")

        def kickoff():
            kind, selected = video_choices[cb_v.current()] if video_choices else (None, None)

            if kind == "P":
                # Progresivo: ya trae audio
                vid_id = selected["id"]
                aud_id = None
            else:
                # Solo video: necesita audio
                vid_id = selected["id"] if selected else None
                aud_id = audios[cb_a.current()]["id"] if audios else None

            container = cb_c.get().strip().lower() or "mp4"
            convertir = bool(conv.get())

            if convertir and not self.core.ffmpeg_path:
                convertir = False

            if not self._ask_output_dir_session():
                return

            win.destroy()

            job = self._create_job(info.get("title", "(sin título)"), "video")

            self.core.download_video(
                url, vid_id, aud_id, container, convertir, job,
                on_done=lambda: self.after(0, self._download_finished),
            )


        ttk.Button(win, text="Aceptar", style="Cass.TButton", command=kickoff).pack(pady=(0, 12))

    def _dialog_audio(self, url):
        self.set_status("Buscando formatos…")

        def worker():
            err, data = None, None
            try:
                data = self.core.list_formats(url)
            except Exception as e:
                err = e
            self.after(0, lambda: self._after_audio(url, err, data))

        threading.Thread(target=worker, daemon=True).start()

    def _after_audio(self, url, err, data):
        if err:
            msg = str(err)
            if self.core.is_antibot_error(msg):
                self.show_antibot_popup(msg)
                self.set_status("Bloqueado por YouTube")
                return
            messagebox.showerror("Error", f"No pude listar formatos:\n{err}")
            self.set_status("Error")
            return

        info, _videos, audios, progressives = data
        # Si YouTube no devuelve pistas "solo audio", usamos progresivos (video+audio)
        # para permitir extracción de audio igualmente.
        audio_sources = list(audios) if audios else list(progressives)

        win = tk.Toplevel(self.root)
        win.title("Formato de audio")
        win.configure(bg=self.root.palette["panel"])
        win.resizable(False, False)
        if not self._tutorial_is_open():
            win.grab_set()

        tk.Label(
            win,
            text=info.get("title", "(sin título)"),
            fg="#EDE6FF",
            bg=self.root.palette["panel"],
            font=self.root.fonts["UI_B"],
            wraplength=520,
            justify="left",
        ).pack(padx=14, pady=(10, 6), anchor="w")

        row = tk.Frame(win, bg=self.root.palette["panel"])
        row.pack(padx=14, pady=6)

        tk.Label(row, text="Audio:", fg="#EDE6FF", bg=self.root.palette["panel"], font=self.root.fonts["UI"]).grid(row=0, column=0, sticky="w")
        cb_a = ttk.Combobox(row, state="readonly", width=58, style="Cass.TCombobox")
        if audios:
            a_vals = [f'{int(a.get("abr") or 0)}kbps {a.get("acodec")} .{a.get("ext")}' for a in audios]
        elif progressives:
            a_vals = [f'≈{int((p.get("tbr") or 0))}kbps (progresivo) {p.get("acodec")} .{p.get("ext")}' for p in progressives]
        else:
            a_vals = ["(sin audio)"]
        cb_a["values"] = a_vals
        if audio_sources:
            cb_a.current(0)
        cb_a.grid(row=0, column=1, padx=8, pady=4)

        tk.Label(row, text="Salida:", fg="#EDE6FF", bg=self.root.palette["panel"], font=self.root.fonts["UI"]).grid(row=1, column=0, sticky="w")
        cb_codec = ttk.Combobox(row, state="readonly", values=["mp3", "m4a", "flac", "wav"], width=8, style="Cass.TCombobox")
        cb_codec.current(0)
        cb_codec.grid(row=1, column=1, padx=8, pady=4, sticky="w")

        if audios:
            src_text = "Fuente: solo audio"
        elif progressives:
            src_text = "Fuente: progresivo (se extraerá audio)"
        else:
            src_text = "Fuente: sin pistas disponibles"
        tk.Label(row, text=src_text, fg="#A78BFA", bg=self.root.palette["panel"], font=self.root.fonts["UI"]).grid(row=2, column=1, sticky="w")

        def kickoff():
            if not audio_sources:
                messagebox.showwarning("Formato", "No hay pistas de audio disponibles.")
                return
            aud_id = audio_sources[cb_a.current()]["id"]
            codec = cb_codec.get().strip().lower() or "mp3"
            if not self._ask_output_dir_session():
                return
            win.destroy()
            job = self._create_job(info.get("title", "(sin título)"), "audio")
            self.core.download_audio(
                url, aud_id, codec, job,
                on_done=lambda: self.after(0, self._download_finished),
            )

        ttk.Button(win, text="Aceptar", style="Cass.TButton", command=kickoff).pack(pady=(0, 12))


# ================== Run ==================
if __name__ == "__main__":
    # MODO LOGIN (proceso separado) -> webview
    if "--login" in sys.argv:
        try:
            run_login_webview_and_save(COOKIES_TXT, WEBVIEW_STORAGE)
        except Exception as e:
            # Si pywebview no está instalado o falla
            # (en onefile conviene mostrar algo en consola, pero si vas --noconsole no se verá)
            # como mínimo, creamos un aviso con messagebox si hay Tk.
            try:
                tk.Tk().withdraw()
                messagebox.showerror("Login", f"No se pudo abrir el login.\n\n¿Tienes instalado pywebview?\n\nDetalle:\n{e}")
            except Exception:
                pass
        raise SystemExit(0)

    # APP NORMAL
    root = RetroWindow()
    try:
        icon_path = APP_DIR / "icono.ico"
        if icon_path.exists():
            root.iconbitmap(str(icon_path))
    except Exception:
        pass

    ui = UI(root)
    root.mainloop()
