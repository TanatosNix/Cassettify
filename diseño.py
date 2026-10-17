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
try:
    import customtkinter as ctk
    HAS_CTK = True
except Exception:
    ctk = None
    HAS_CTK = False

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


# ================== Identidad visual / utilidades ==================
FONT_FAMILIES = {
    "DISPLAY": ["Bahnschrift", "Segoe UI Variable Display", "Aptos Display", "Inter", "Segoe UI", "Arial"],
    "TEXT": ["Aptos", "Segoe UI Variable Text", "Segoe UI", "Inter", "IBM Plex Sans", "Arial"],
    "MONO": ["Cascadia Mono", "JetBrains Mono", "Fira Code", "Consolas", "Courier New"],
}

APP_THEME = {
    "bg": "#0b0e13",
    "panel": "#11151d",
    "surface": "#19202b",
    "surface_alt": "#222b38",
    "surface_soft": "#151b24",
    "titlebar": "#0f131a",
    "title": "#f7f3ea",
    "text": "#d8dee9",
    "text_muted": "#9ba7b7",
    "accent": "#2dd4bf",
    "accent_hover": "#5eead4",
    "accent_deep": "#14b8a6",
    "accent_soft": "#123d42",
    "accent_alt": "#f59e0b",
    "accent_alt_soft": "#463311",
    "outline": "#353c48",
    "outline_soft": "#252d39",
    "shadow": "#05070a",
    "btn_shadow": "#090c11",
    "btn_shadow_hover": "#242d3a",
    "success": "#6ee7b7",
    "warning": "#fbbf24",
    "error": "#fb7185",
    "close": "#dc2626",
    "close_hover": "#ef4444",
    "transparent": "#00ff00",
    "glow": "#7dd3fc",
}


def open_with_system(target):
    path = str(target)
    if platform.system() == "Windows":
        os.startfile(path)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def pick_font(master, candidates, size, weight="normal"):
    avail = set(tkfont.families(master))
    for fam in candidates:
        if fam in avail:
            return (fam, size, weight)
    return ("Segoe UI", size, weight)


def compute_fonts(master):
    return {
        "TITLE": pick_font(master, FONT_FAMILIES["DISPLAY"], 14, "bold"),
        "HERO": pick_font(master, FONT_FAMILIES["DISPLAY"], 34, "bold"),
        "HERO_SUB": pick_font(master, FONT_FAMILIES["TEXT"], 13, "normal"),
        "UI": pick_font(master, FONT_FAMILIES["TEXT"], 11, "normal"),
        "UI_B": pick_font(master, FONT_FAMILIES["TEXT"], 11, "bold"),
        "SUB": pick_font(master, FONT_FAMILIES["TEXT"], 12, "normal"),
        "SMALL": pick_font(master, FONT_FAMILIES["TEXT"], 10, "normal"),
        "SMALL_B": pick_font(master, FONT_FAMILIES["TEXT"], 10, "bold"),
        "MONO": pick_font(master, FONT_FAMILIES["MONO"], 11, "normal"),
        "MONO_B": pick_font(master, FONT_FAMILIES["MONO"], 11, "bold"),
    }


# ================== Ventana Retro ==================
class RetroWindow(tk.Tk):
    def __init__(self, title="Cassettify", width=1080, height=780, radius=24, titlebar_height=42):
        super().__init__()
        self.win_w, self.win_h = int(width), int(height)
        self.radius, self.tb_h = int(radius), int(titlebar_height)
        self.min_win_w, self.min_win_h = 760, 560
        self._drag = {"x": 0, "y": 0}
        self._resize_start = None
        self._resize_mode = ""
        self.resizable_enabled = True  # Permitir redimensionamiento

        self.palette = APP_THEME.copy()

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

        self.btn_menu = tk.Label(self.titlebar, text="Menu", fg=self.palette["text"], bg=self.palette["surface"], width=6, cursor="hand2", font=self.fonts["SMALL_B"])
        self.btn_min = tk.Label(self.titlebar, text="-", fg=self.palette["text"], bg=self.palette["surface"], width=3, cursor="hand2", font=self.fonts["UI_B"])
        self.btn_close = tk.Label(self.titlebar, text="x", fg="#ffffff", bg=self.palette["close"], width=3, cursor="hand2", font=self.fonts["UI_B"])

        self.lbl_title.place(x=18, rely=0.5, anchor="w")
        self.btn_menu.pack(side="right", padx=(4, 10), pady=7, ipady=1)
        self.btn_min.pack(side="right", padx=4, pady=7, ipady=1)
        self.btn_close.pack(side="right", padx=4, pady=7, ipady=1)

        self.content = tk.Frame(
            self.canvas, bg=self.palette["panel"], highlightthickness=0
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
        self.btn_menu.bind("<Button-1>", lambda e: self._show_titlebar_menu(e))

        self.btn_menu.bind("<Enter>", lambda e: self.btn_menu.config(bg=self.palette["surface_alt"]))
        self.btn_menu.bind("<Leave>", lambda e: self.btn_menu.config(bg=self.palette["surface"]))
        self.btn_min.bind("<Enter>", lambda e: self.btn_min.config(bg=self.palette["surface_alt"]))
        self.btn_min.bind("<Leave>", lambda e: self.btn_min.config(bg=self.palette["surface"]))
        self.btn_close.bind("<Enter>", lambda e: self.btn_close.config(bg=self.palette["close_hover"]))
        self.btn_close.bind("<Leave>", lambda e: self.btn_close.config(bg=self.palette["close"]))

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Configure>", self._on_configure)

        # Bindings para redimensionamiento
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<ButtonPress-1>", self._canvas_press)
        self.canvas.bind("<B1-Motion>", self._canvas_motion)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_release)

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

    def _start_resize(self, e):
        """Inicia redimensionamiento detectando la esquina/borde más cercana."""
        if not self.resizable_enabled:
            return
        self._resize_start = {
            "x": e.x_root,
            "y": e.y_root,
            "w": self.win_w,
            "h": self.win_h,
            "win_x": self.winfo_x(),
            "win_y": self.winfo_y(),
        }

        # Detectar qué esquina/borde está siendo arrastrado
        margin = 15
        x_rel = e.x_root - self.winfo_x()
        y_rel = e.y_root - self.winfo_y()

        self._resize_mode = ""
        if x_rel < margin and y_rel < margin:
            self._resize_mode = "nw"
        elif x_rel > self.win_w - margin and y_rel < margin:
            self._resize_mode = "ne"
        elif x_rel < margin and y_rel > self.win_h - margin:
            self._resize_mode = "sw"
        elif x_rel > self.win_w - margin and y_rel > self.win_h - margin:
            self._resize_mode = "se"
        elif x_rel < margin:
            self._resize_mode = "w"
        elif x_rel > self.win_w - margin:
            self._resize_mode = "e"
        elif y_rel < margin:
            self._resize_mode = "n"
        elif y_rel > self.win_h - margin:
            self._resize_mode = "s"

    def _on_resize(self, e):
        """Maneja el redimensionamiento de la ventana."""
        if not self.resizable_enabled or not hasattr(self, "_resize_start") or not self._resize_start:
            return

        dx = e.x_root - self._resize_start["x"]
        dy = e.y_root - self._resize_start["y"]

        new_w = self._resize_start["w"]
        new_h = self._resize_start["h"]
        new_x = self._resize_start["win_x"]
        new_y = self._resize_start["win_y"]

        # Aplicar delta según la dirección
        if "e" in self._resize_mode:
            new_w = max(self.min_win_w, self._resize_start["w"] + dx)
        if "w" in self._resize_mode:
            new_w = max(self.min_win_w, self._resize_start["w"] - dx)
            new_x = self._resize_start["win_x"] + (self._resize_start["w"] - new_w)
        if "s" in self._resize_mode:
            new_h = max(self.min_win_h, self._resize_start["h"] + dy)
        if "n" in self._resize_mode:
            new_h = max(self.min_win_h, self._resize_start["h"] - dy)
            new_y = self._resize_start["win_y"] + (self._resize_start["h"] - new_h)

        # Aplicar nuevas dimensiones
        self.win_w, self.win_h = int(new_w), int(new_h)
        self.geometry(f"{int(new_w)}x{int(new_h)}+{int(new_x)}+{int(new_y)}")

    def _on_motion(self, e):
        """Cambia el cursor según la posición."""
        if not self.resizable_enabled:
            return

        margin = 15
        x_rel = e.x_root - self.winfo_x()
        y_rel = e.y_root - self.winfo_y()

        cursor = ""
        if (x_rel < margin and y_rel < margin) or (x_rel > self.win_w - margin and y_rel > self.win_h - margin):
            cursor = "size_nw_se"
        elif (x_rel > self.win_w - margin and y_rel < margin) or (x_rel < margin and y_rel > self.win_h - margin):
            cursor = "size_ne_sw"
        elif x_rel < margin or x_rel > self.win_w - margin:
            cursor = "size_we"
        elif y_rel < margin or y_rel > self.win_h - margin:
            cursor = "size_ns"

        self.canvas.configure(cursor=cursor)

    def _canvas_press(self, e):
        """Detecta si el click es para mover o redimensionar."""
        margin = 15
        x_rel = e.x_root - self.winfo_x()
        y_rel = e.y_root - self.winfo_y()

        # Si está en los bordes, es redimensionamiento
        if (x_rel < margin or x_rel > self.win_w - margin or
            y_rel < margin or y_rel > self.win_h - margin):
            self._start_resize(e)
        # Si está en la titlebar, es movimiento
        elif y_rel < self.tb_h:
            self._start_move(e)

    def _canvas_motion(self, e):
        """Maneja el movimiento del mouse con botón presionado."""
        if hasattr(self, "_resize_mode") and hasattr(self, "_resize_start") and self._resize_start:
            self._on_resize(e)
        elif e.y_root - self.winfo_y() < self.tb_h:
            self._on_move(e)

    def _canvas_release(self, e):
        """Termina el redimensionamiento."""
        self._resize_start = None
        self._resize_mode = ""

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

    def _show_titlebar_menu(self, event):
        """Muestra un menú desplegable con opciones de la aplicación."""
        menu = tk.Menu(
            self,
            tearoff=0,
            bg=self.palette["surface"],
            fg=self.palette["text"],
            activebackground=self.palette["accent"],
            activeforeground=self.palette["bg"],
            borderwidth=0,
        )
        menu.add_separator()
        menu.add_command(label="Ver logs", command=lambda: self._open_log_file())
        menu.add_command(label="Carpeta de descargas", command=lambda: self._open_downloads_folder())
        menu.add_command(label="AppData de Cassettify", command=lambda: self._open_appdata_folder())
        menu.add_separator()
        menu.add_command(label="Acerca de", command=lambda: self._show_about())
        menu.add_separator()

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_log_file(self):
        """Abre el archivo de log en el editor predeterminado."""
        if LOG_FILE.exists():
            try:
                open_with_system(LOG_FILE)
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo abrir el log:\n{e}")
        else:
            messagebox.showinfo("Log", "No hay archivo de log todavía.")

    def _open_downloads_folder(self):
        """Abre la carpeta de descargas en el explorador."""
        try:
            # Obtener carpeta de descargas del usuario
            downloads = Path.home() / "Downloads"
            if not downloads.exists():
                downloads = Path.home() / "Descargas"
            if downloads.exists():
                open_with_system(downloads)
            else:
                messagebox.showinfo("Descargas", "No se encontró la carpeta de descargas.")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir la carpeta:\n{e}")

    def _open_appdata_folder(self):
        """Abre la carpeta AppData de Cassettify."""
        try:
            open_with_system(APPDATA_DIR)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir AppData:\n{e}")

    def _show_about(self):
        """Muestra información sobre la aplicación."""
        messagebox.showinfo(
            "Acerca de Cassettify",
            "Cassettify v1.0\n"
            "Descargador de video y audio de YouTube\n\n"
            "© 2026 Cassettify\n"
            "Basado en yt-dlp\n\n"
            "Usa Actualizar para mantener las dependencias al dia."
        )

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
        fieldbackground=root.palette["bg"],
        foreground=root.palette["title"],
        insertcolor=root.palette["accent"],
        padding=12,
        bordercolor=root.palette["outline"],
        font=root.fonts["MONO"],
    )
    s.configure(
        "Cass.TButton",
        background=root.palette["accent"],
        foreground=root.palette["bg"],
        font=root.fonts["UI_B"],
        padding=(16, 10),
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
        padding=7,
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

    s.configure(
        "Vertical.TScrollbar",
        background=root.palette["surface_alt"],
        troughcolor=root.palette["panel"],
        bordercolor=root.palette["panel"],
        arrowcolor=root.palette["text_muted"],
        relief="flat",
    )


class CassButton(tk.Canvas):
    def __init__(self, master, root, text, command=None, variant="primary", height=48, radius=14):
        super().__init__(
            master,
            width=1,
            height=height,
            bg=master.cget("bg"),
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            takefocus=1,
        )
        self.root_ref = root
        self.text = text
        self.command = command
        self.variant = variant
        self.radius = radius
        self.is_hover = False
        self.is_pressed = False
        self.is_disabled = False
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", lambda _e: self.invoke())
        self.bind("<space>", lambda _e: self.invoke())
        self._draw()

    def state(self, states=None):
        if states is None:
            return ("disabled",) if self.is_disabled else ()
        for state in states:
            if state == "disabled":
                self.is_disabled = True
                self.configure(cursor="")
            elif state == "!disabled":
                self.is_disabled = False
                self.configure(cursor="hand2")
        self._draw()

    def invoke(self):
        if not self.is_disabled and self.command:
            self.command()

    def _palette(self):
        p = self.root_ref.palette
        if self.is_disabled:
            return p["surface_soft"], p["text_muted"], p["outline_soft"]
        if self.variant == "primary":
            fill = p["accent_hover"] if self.is_hover else p["accent"]
            if self.is_pressed:
                fill = p["accent_deep"]
            return fill, p["bg"], fill
        if self.variant == "warning":
            fill = p["accent_alt"] if self.is_hover else p["accent_alt_soft"]
            return fill, p["title"], p["accent_alt"]
        fill = p["surface_alt"] if self.is_hover else p["surface"]
        if self.is_pressed:
            fill = p["outline"]
        return fill, p["text"], p["outline_soft"]

    def _on_enter(self, _event):
        if self.is_disabled:
            return
        self.is_hover = True
        self._draw()

    def _on_leave(self, _event):
        self.is_hover = False
        self.is_pressed = False
        self._draw()

    def _on_press(self, _event):
        if self.is_disabled:
            return
        self.focus_set()
        self.is_pressed = True
        self._draw()

    def _on_release(self, _event):
        if self.is_disabled:
            return
        was_pressed = self.is_pressed
        self.is_pressed = False
        self._draw()
        if was_pressed:
            self.invoke()

    def _draw(self):
        self.delete("all")
        w = max(8, self.winfo_width())
        h = max(8, self.winfo_height())
        fill, fg, outline = self._palette()
        y = 2 if self.is_pressed else 0
        scale = 3
        img = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle(
            (1 * scale, (1 + y) * scale, (w - 1) * scale, (h - 2 + y) * scale),
            radius=self.radius * scale,
            fill=fill,
            outline=outline,
            width=1 * scale,
        )
        img = img.resize((w, h), Image.Resampling.LANCZOS)
        self._button_img = ImageTk.PhotoImage(img)
        self.create_image(0, 0, image=self._button_img, anchor="nw")
        self.create_text(
            w // 2,
            h // 2 + y,
            text=self.text,
            fill=fg,
            font=self.root_ref.fonts["UI_B"],
        )

    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        r = min(r, max(1, int((y1 - y0) / 2)))
        self.create_arc(x0, y0, x0 + 2 * r, y0 + 2 * r, start=90, extent=90, **kw)
        self.create_arc(x1 - 2 * r, y0, x1, y0 + 2 * r, start=0, extent=90, **kw)
        self.create_arc(x0, y1 - 2 * r, x0 + 2 * r, y1, start=180, extent=90, **kw)
        self.create_arc(x1 - 2 * r, y1 - 2 * r, x1, y1, start=270, extent=90, **kw)
        self.create_rectangle(x0 + r, y0, x1 - r, y1, **kw)
        self.create_rectangle(x0, y0 + r, x1, y1 - r, **kw)


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
        self.bg = APP_THEME["surface"]
        self.fill = APP_THEME["accent"]
        self.glow = APP_THEME["glow"]
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
        self._rr(1, 1, w - 1, h - 1, r, fill=self.bg, outline=APP_THEME["outline_soft"])
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

          mk('Guardar sesión', '#2dd4bf', 16, 'save_and_close');
          mk('Cerrar sesión', '#dc2626', 160, 'clear_and_close');
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
        self.pot_plugin_available = self.detect_pot_plugin()
        self.pot_server_home = self.detect_pot_server_home()
        self.pot_script_path = self.detect_pot_script()
        self.restricted_network_mode = False
        append_log(
            "Core init | output_dir="
            f"{self.output_dir} | ffmpeg={self.ffmpeg_path or 'none'} | "
            f"js_runtime={self.js_runtime[0] if self.js_runtime else 'none'} | "
            f"pot_plugin={self.pot_plugin_available} | "
            f"pot_server_home={self.pot_server_home or 'none'} | "
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

    def detect_pot_plugin(self):
        try:
            import importlib.util

            return importlib.util.find_spec("yt_dlp_plugins.extractor.getpot_bgutil") is not None
        except Exception:
            return False

    def detect_pot_server_home(self):
        candidates = [
            Path.home() / "bgutil-ytdlp-pot-provider" / "server",
            Path(os.environ.get("LOCALAPPDATA", "")) / "bgutil-ytdlp-pot-provider" / "server",
            APPDATA_DIR / "pot_provider" / "server",
        ]
        for p in candidates:
            try:
                if (p / "build" / "generate_once.js").exists() or (p / "src" / "generate_once.ts").exists():
                    return str(p)
            except Exception:
                continue
        return None

    def detect_pot_script(self):
        if not self.pot_server_home:
            return None
        candidates = [
            Path(self.pot_server_home) / "build" / "generate_once.js",
            Path(self.pot_server_home) / "src" / "generate_once.ts",
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
        yt_args.setdefault("fetch_pot", ["auto"])
        if self.pot_server_home:
            bg_args = extractor_args.setdefault("youtubepot-bgutilscript", {})
            bg_args.setdefault("server_home", [self.pot_server_home])
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
                self.ui.after(0, lambda: self.ui.set_status("Descarga completada"))
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
                self.ui.after(0, lambda: self.ui.set_status("Descarga completada"))
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
        self._update_notification_shown = False

        # encabezado visual
        hero = tk.Frame(self, bg=root.palette["panel"])
        hero.pack(padx=24, pady=(18, 12), fill="x")
        hero.grid_columnconfigure(1, weight=1)

        mark = tk.Canvas(hero, width=54, height=54, bg=root.palette["panel"], bd=0, highlightthickness=0)
        mark.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 14), pady=(0, 2))
        mark.create_oval(3, 3, 51, 51, fill=root.palette["accent_soft"], outline=root.palette["outline_soft"])
        mark.create_rectangle(15, 18, 39, 35, fill=root.palette["accent"], outline="")
        mark.create_oval(20, 23, 25, 28, fill=root.palette["bg"], outline="")
        mark.create_oval(29, 23, 34, 28, fill=root.palette["bg"], outline="")

        tk.Label(
            hero,
            text="Cassettify",
            fg=root.palette["title"],
            bg=root.palette["panel"],
            font=self.root.fonts["HERO"],
            anchor="w",
        ).grid(row=0, column=1, sticky="w")
        tk.Label(
            hero,
            text="Convierte audio y video con una experiencia local limpia, rápida y preparada para lotes.",
            fg=root.palette["text_muted"],
            bg=root.palette["panel"],
            font=self.root.fonts["HERO_SUB"],
            anchor="w",
        ).grid(row=1, column=1, sticky="w", pady=(2, 0))

        hero_meta = tk.Frame(hero, bg=root.palette["panel"])
        hero_meta.grid(row=0, column=2, rowspan=2, sticky="e", padx=(18, 0))
        for text, color in (("FFmpeg", root.palette["accent"]), ("yt-dlp", root.palette["accent_alt"])):
            tk.Label(
                hero_meta,
                text=text,
                fg=root.palette["bg"] if color == root.palette["accent"] else root.palette["title"],
                bg=color,
                font=self.root.fonts["SMALL_B"],
                padx=12,
                pady=5,
            ).pack(side="left", padx=(8, 0))

        # fila URL
        top = tk.Frame(self, bg=root.palette["surface"], highlightthickness=1, highlightbackground=root.palette["outline_soft"])
        top.pack(padx=24, pady=(0, 12), fill="x")
        tk.Label(
            top,
            text="ENLACE",
            fg=root.palette["accent"],
            bg=root.palette["surface"],
            font=self.root.fonts["SMALL_B"],
            anchor="w",
        ).pack(anchor="w", padx=16, pady=(12, 4))
        entry_wrap = tk.Frame(top, bg=root.palette["surface_alt"], highlightthickness=1, highlightbackground=root.palette["outline_soft"])
        entry_wrap.pack(padx=16, pady=(0, 14), fill="x", expand=True)
        self.url_shadow = entry_wrap
        self.url_wrap = entry_wrap
        self.url_placeholder = "Pega aquí un enlace de YouTube..."
        self._url_is_placeholder = False
        self.url = tk.Entry(
            entry_wrap,
            bg=root.palette["surface_alt"],
            fg=root.palette["title"],
            insertbackground=root.palette["accent"],
            relief="flat",
            bd=0,
            font=self.root.fonts["MONO"],
        )
        self.url.pack(fill="x", expand=True, padx=14, pady=13)
        self.url.bind("<FocusIn>", self._clear_url_placeholder)
        self.url.bind("<FocusOut>", self._restore_url_placeholder)
        self._restore_url_placeholder()

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
        btns.pack(padx=24, pady=(0, 12), fill="x")
        self.actions_row = btns
        for idx in range(5):
            btns.grid_columnconfigure(idx, weight=1, uniform="actions")

        self.btn_video = CassButton(btns, root, "Video", command=self.on_video, variant="primary")
        self.btn_audio = CassButton(btns, root, "Audio", command=self.on_audio, variant="primary")
        self.btn_login = CassButton(btns, root, "Sesión", command=self.on_login, variant="secondary")
        self.btn_help = CassButton(btns, root, "Guía", command=self.show_tutorial, variant="secondary")
        self.btn_update = CassButton(btns, root, "Actualizar", command=self.run_update_dependencies, variant="secondary")

        self.btn_video.grid(row=0, column=0, padx=(0, 8), sticky="ew")
        self.btn_audio.grid(row=0, column=1, padx=8, sticky="ew")
        self.btn_login.grid(row=0, column=2, padx=8, sticky="ew")
        self.btn_help.grid(row=0, column=3, padx=8, sticky="ew")
        self.btn_update.grid(row=0, column=4, padx=(8, 0), sticky="ew")

        # estado
        info = tk.Frame(self, bg=root.palette["panel"])
        info.pack(padx=24, pady=(0, 10), fill="x")
        self.info_row = info

        self.status = tk.Label(
            info,
            text="Listo para convertir",
            fg=root.palette["text"],
            bg=root.palette["surface_soft"],
            font=self.root.fonts["UI"],
            anchor="w",
            padx=12,
            pady=7,
        )
        self.status.pack(side="left", fill="x", expand=True)

        self.session_lbl = tk.Label(
            info,
            text="",
            fg=root.palette["text_muted"],
            bg=root.palette["surface_soft"],
            font=self.root.fonts["UI_B"],
            anchor="e",
            padx=12,
            pady=7,
        )
        self.session_lbl.pack(side="right", padx=(10, 0))
        self.restricted_mode_var = tk.BooleanVar(value=False)
        self.chk_restricted_mode = tk.Checkbutton(
            info,
            text="Red restringida",
            variable=self.restricted_mode_var,
            onvalue=True,
            offvalue=False,
            command=self._on_toggle_restricted_mode,
            fg=root.palette["text_muted"],
            bg=root.palette["surface_soft"],
            activeforeground=root.palette["text"],
            activebackground=root.palette["surface_alt"],
            selectcolor=root.palette["surface_alt"],
            indicatoron=False,
            relief="flat",
            offrelief="flat",
            highlightthickness=0,
            bd=0,
            font=self.root.fonts["UI"],
            padx=12,
            pady=6,
        )
        self.chk_restricted_mode.pack(side="right", padx=(10, 0))

        # estado de dependencias
        self.deps_strip = tk.Frame(
            self,
            bg=root.palette["surface_alt"],
            highlightthickness=1,
            highlightbackground=root.palette["outline"],
        )
        self.deps_strip.pack(padx=24, pady=(0, 10), fill="x")
        self.deps_title = tk.Label(
            self.deps_strip,
            text="Entorno",
            fg=root.palette["text_muted"],
            bg=root.palette["surface_alt"],
            font=self.root.fonts["UI_B"],
        )
        self.deps_title.pack(side="left", padx=(14, 10), pady=8)
        self.deps_summary = tk.Label(
            self.deps_strip,
            text="Comprobando dependencias...",
            fg=root.palette["text"],
            bg=root.palette["surface_alt"],
            font=self.root.fonts["UI"],
            anchor="w",
        )
        self.deps_summary.pack(side="left", fill="x", expand=True, padx=(0, 14), pady=8)

        # progreso global
        self.pb = GlowProgress(self, height=10, bg=root.palette["panel"])
        self.pb.bg = root.palette["surface"]
        self.pb.fill = root.palette["accent"]
        self.pb.glow = root.palette["glow"]
        self.pb.pack(padx=24, fill="x", pady=(0, 12))

        # lista de descargas
        self.jobs_wrap = tk.Frame(
            self,
            bg=root.palette["surface"],
            highlightthickness=1,
            highlightbackground=root.palette["outline"],
        )
        self.jobs_wrap.pack(padx=24, pady=(0, 18), fill="both", expand=True)
        jobs_head = tk.Frame(self.jobs_wrap, bg=root.palette["surface"])
        jobs_head.pack(fill="x", padx=14, pady=(12, 8))
        tk.Label(
            jobs_head,
            text="Cola de descargas",
            fg=root.palette["title"],
            bg=root.palette["surface"],
            font=self.root.fonts["UI_B"],
        ).pack(side="left")
        self.jobs_count = tk.Label(
            jobs_head,
            text="0 trabajos",
            fg=root.palette["text_muted"],
            bg=root.palette["surface"],
            font=self.root.fonts["SMALL"],
        )
        self.jobs_count.pack(side="right")
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
        self.empty_jobs = tk.Frame(self.jobs_container, bg=self.root.palette["surface"])
        self.empty_jobs.pack(fill="both", expand=True, padx=10, pady=34)
        tk.Label(
            self.empty_jobs,
            text="Todavía no hay descargas",
            fg=self.root.palette["title"],
            bg=self.root.palette["surface"],
            font=self.root.fonts["UI_B"],
        ).pack(anchor="center")
        tk.Label(
            self.empty_jobs,
            text="Pega un enlace, elige Video o Audio y Cassettify irá llenando esta cola.",
            fg=self.root.palette["text_muted"],
            bg=self.root.palette["surface"],
            font=self.root.fonts["UI"],
            wraplength=520,
            justify="center",
        ).pack(anchor="center", pady=(6, 0))
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
        self.after(1400, self._refresh_dependency_strip)
        self.after(3000, self._background_version_checker)  # Verificar actualizaciones en background

    def _refresh_dependency_strip(self):
        missing, warnings = self._dependency_missing()
        if missing:
            self.deps_title.config(text="Atención", fg=self.root.palette["error"])
            self.deps_summary.config(
                text="Falta: " + ", ".join(missing[:3]) + ("..." if len(missing) > 3 else ""),
                fg=self.root.palette["error"],
            )
        elif warnings:
            important = [w for w in warnings if "Sesión YouTube" not in w]
            visible = important or warnings
            self.deps_title.config(text="Calidad", fg=self.root.palette["warning"])
            self.deps_summary.config(
                text="Recomendado: " + ", ".join(visible[:2]) + ("..." if len(visible) > 2 else ""),
                fg=self.root.palette["warning"],
            )
        else:
            runtime = self.core.js_runtime[0] if self.core.js_runtime else "sin runtime JS"
            self.deps_title.config(text="Listo", fg=self.root.palette["success"])
            self.deps_summary.config(
                text=f"FFmpeg activo · {runtime} activo · descargas preparadas",
                fg=self.root.palette["text"],
            )

    def _check_ytdlp_version(self):
        """Verifica si yt-dlp está actualizado (recomendado >= 90 días de diferencia)."""
        try:
            from yt_dlp.version import __version__ as version_str
            from datetime import datetime
            # Intenta extraer fecha de versión (formato: YYYY.MM.DD)
            parts = version_str.split('.')
            if len(parts) >= 3:
                try:
                    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                    build_date = datetime(year, month, day)
                    days_old = (datetime.now() - build_date).days
                    if days_old > 90:
                        return f"yt-dlp antiguo ({days_old} días)"
                    return None
                except (ValueError, TypeError):
                    return None
        except Exception:
            return None
        return None

    def _check_pot_provider(self):
        """Verifica si el POT provider está disponible e instalado correctamente."""
        if not self.core.pot_plugin_available:
            return "plugin bgutil POT provider"
        if not self.core.pot_server_home:
            return "servidor/script bgutil POT provider"
        return None

    def _check_youtube_session(self):
        """Verifica si hay sesión activa en YouTube."""
        if not COOKIES_TXT.exists() or COOKIES_TXT.stat().st_size < 80:
            return "Sesión YouTube (sin sesión = solo 360p)"
        return None

    def _dependency_missing(self):
        """Verifica dependencias críticas y recomendadas."""
        missing = []
        warnings = []

        # CRÍTICAS (rompen la funcionalidad)
        if not self.core.ffmpeg_path:
            missing.append("FFmpeg")
        if not self.core.js_runtime:
            missing.append("runtime JS (Deno o Node para desafíos YouTube)")

        # RECOMENDADAS (mejoran la funcionalidad)
        ytdlp_warn = self._check_ytdlp_version()
        if ytdlp_warn:
            warnings.append(ytdlp_warn)

        pot_warn = self._check_pot_provider()
        if pot_warn:
            warnings.append(pot_warn)

        session_warn = self._check_youtube_session()
        if session_warn:
            warnings.append(session_warn)

        try:
            import yt_dlp_ejs  # noqa: F401
        except Exception:
            missing.append("yt-dlp-ejs")

        if getattr(sys, "frozen", False) and not (shutil.which("python") or shutil.which("py")):
            missing.append("Python (para auto-actualizaciones)")

        return missing, warnings

    def _launch_ps_script(self, script_path: Path, quiet: bool = False):
        if not script_path.exists():
            messagebox.showwarning("Dependencias", f"No se encontró el script:\n{script_path}")
            return False
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            messagebox.showerror("Dependencias", "No se encontró PowerShell para ejecutar la instalación.")
            return False
        try:
            args = [shell, "-ExecutionPolicy", "Bypass", "-File", str(script_path)]
            if quiet:
                args.append("-Quiet")
            subprocess.Popen(args, cwd=str(APP_DIR))
            return True
        except Exception as e:
            messagebox.showerror("Dependencias", f"No pude iniciar el script:\n{e}")
            return False

    def _startup_dependency_check(self):
        missing, warnings = self._dependency_missing()

        # Mostrar solo si hay cosas faltantes O si faltan dependencias críticas para HD
        has_critical_missing = bool(missing)
        has_hd_blocker = any("PO Token" in w or "yt-dlp antiguo" in w for w in warnings)

        if not has_critical_missing and not has_hd_blocker:
            return

        # Construir mensaje
        msg_parts = []

        if missing:
            msg_parts.append("FALTA INSTALAR:\n")
            msg_parts.extend(f"  - {m}\n" for m in missing)
            msg_parts.append("\n")

        if warnings:
            msg_parts.append("ADVERTENCIAS (Afecta calidad de descarga):\n")
            for w in warnings:
                if "Sesión YouTube" in w:
                    msg_parts.append(f"  - {w}\n     -> Inicia sesión para descargar HD\n")
                elif "PO Token" in w:
                    msg_parts.append(f"  - {w}\n     -> YouTube puede bloquear formatos HD sin esto\n")
                elif "yt-dlp antiguo" in w:
                    msg_parts.append(f"  - {w}\n     -> YouTube cambia protecciones constantemente\n")
                else:
                    msg_parts.append(f"  - {w}\n")
            msg_parts.append("\n")

        msg = "".join(msg_parts)

        if has_critical_missing:
            msg += "¿Ejecutar bootstrap.ps1 ahora?"
            if messagebox.askyesno("Dependencias Faltantes", msg):
                if self._launch_ps_script(BOOTSTRAP_SCRIPT):
                    self._toast("Instalador de dependencias iniciado…", 2600)
        else:
            msg += "Puedes ejecutar bootstrap.ps1 después si lo deseas."
            messagebox.showwarning("Advertencia: Calidad Limitada", msg)

    def _background_version_checker(self):
        """Verifica en background si hay actualizaciones disponibles cada 30 minutos."""
        def worker():
            try:
                missing, warnings = self._dependency_missing()
                self.after(0, self._refresh_dependency_strip)

                # Si hay warnings importantes, notificar
                if not self._update_notification_shown and any("antiguo" in w.lower() for w in warnings):
                    self._update_notification_shown = True
                    self.after(0, lambda: self._show_update_available_notification())
            except Exception as e:
                append_log(f"background_version_checker error: {e}")

        # Ejecutar en thread y repetir cada 30 minutos
        threading.Thread(target=worker, daemon=True).start()
        self.after(1800000, self._background_version_checker)  # 30 minutos

    def _show_update_available_notification(self):
        """Muestra una notificación de que hay actualizaciones disponibles."""
        if messagebox.askyesno(
            "Actualizaciones disponibles",
            "Se detectaron actualizaciones disponibles para las dependencias.\n\n"
            "¿Deseas actualizar ahora?\n"
            "(Esto puede tomar algunos minutos)"
        ):
            self.run_update_dependencies()

    def run_update_dependencies(self):
        if not UPDATE_DEPS_SCRIPT.exists():
            messagebox.showwarning("Actualizar", f"No se encontró:\n{UPDATE_DEPS_SCRIPT}")
            return

        # Mostrar lo que se va a actualizar
        msg = (
            "Se actualizarán los siguientes componentes:\n\n"
            "✓ yt-dlp (descargas de YouTube)\n"
            "✓ Deno/Node (runtime JS para desafíos)\n"
            "✓ FFmpeg (codificación de video/audio)\n"
            "✓ bgutil pot-provider (si está instalado)\n\n"
            "Esto puede tomar varios minutos.\n¿Continuar?"
        )

        if messagebox.askyesno("Actualizar dependencias", msg):
            self._toast("Iniciando actualización…", 2200)
            self.set_status("Actualizando dependencias…")

            success = self._launch_ps_script(UPDATE_DEPS_SCRIPT, quiet=True)

            if success:
                self._toast("Actualizador iniciado", 2400)
                self.after(4500, self._refresh_dependency_strip)
                messagebox.showinfo(
                    "Actualización iniciada",
                    "Se está ejecutando el actualizador de dependencias.\n\n"
                    "Se cerrará automáticamente cuando termine.\n"
                    "Puedes volver a Cassettify en unos minutos."
                )
            else:
                self._toast("Error al iniciar actualización", 2400)
                self.set_status("Listo")

    # ---------- estado sesión ----------
    def refresh_session_status(self):
        ok = COOKIES_TXT.exists() and COOKIES_TXT.stat().st_size > 80
        self.session_lbl.config(
            text=f"YouTube: {'sesión activa' if ok else 'sin sesión'}",
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
    def _url_value(self):
        if getattr(self, "_url_is_placeholder", False):
            return ""
        return self.url.get().strip()

    def _clear_url_placeholder(self, _event=None):
        if not getattr(self, "_url_is_placeholder", False):
            return
        self.url.delete(0, tk.END)
        self.url.config(fg=self.root.palette["title"])
        self._url_is_placeholder = False

    def _restore_url_placeholder(self, _event=None):
        if self.url.get().strip():
            return
        self._url_is_placeholder = True
        self.url.config(fg=self.root.palette["text_muted"])
        self.url.delete(0, tk.END)
        self.url.insert(0, self.url_placeholder)

    def _refresh_jobs_header(self):
        total = len(self.jobs)
        self.jobs_count.config(text=f"{total} trabajo{'s' if total != 1 else ''}")
        if total and self.empty_jobs.winfo_ismapped():
            self.empty_jobs.pack_forget()

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
                "title": "1/6 - Pega un enlace",
                "body": "Pega un enlace de YouTube. La práctica no descarga nada todavía: solo valida que el campo esté preparado.",
                "target": self.url_wrap,
            },
            {
                "title": "2/6 - Elige salida",
                "body": "Elige Video si quieres imagen y sonido, o Audio si solo quieres voz o música.",
                "target": self.actions_row,
            },
            {
                "title": "3/6 - Decide formato",
                "body": "Escoge una recomendación de formato. Esto entrena la decisión antes de abrir el diálogo real.",
                "target": self.actions_row,
            },
            {
                "title": "4/6 - Confirma carpeta",
                "body": "Confirma dónde guardarías la descarga. Puedes usar la carpeta actual o elegir otra.",
                "target": self.url_wrap,
            },
            {
                "title": "5/6 - Comprueba entorno",
                "body": "Revisa FFmpeg, sesión, runtime JS y proveedor POT. Así entiendes por qué a veces hay límites de calidad.",
                "target": self.deps_strip,
            },
            {
                "title": "6/6 - Ensayo final",
                "body": "Revisa el resumen. Si todo está marcado, la práctica se puede completar.",
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

    def _tutorial_set_step_valid(self, ok, message):
        if ok:
            self._tutorial_practice["validated"].add(self._tutorial_step_index)
        else:
            self._tutorial_practice["validated"].discard(self._tutorial_step_index)
        self._tutorial_status_lbl.config(
            text=message,
            fg=self.root.palette["success"] if ok else self.root.palette["warning"],
        )
        self._tutorial_next_btn.state(["!disabled"] if ok else ["disabled"])
        return ok

    def _tutorial_paste_clipboard(self):
        try:
            value = self.clipboard_get().strip()
        except Exception:
            value = ""
        self._tutorial_url_var.set(value)
        self._tutorial_practice["validated"].discard(self._tutorial_step_index)
        self._tutorial_status_lbl.config(text="Portapapeles pegado. Ahora valida el enlace.", fg=self.root.palette["text_muted"])
        self._tutorial_next_btn.state(["disabled"])

    def _tutorial_pick_mode(self, mode):
        self._tutorial_practice["mode"] = mode
        self._tutorial_practice["format"] = ""
        self._tutorial_practice["validated"].discard(self._tutorial_step_index)
        self._tutorial_render_step()

    def _tutorial_use_current_folder(self):
        folder = self.core.output_dir
        self._tutorial_practice["folder"] = folder
        self._tutorial_practice["folder_confirmed"] = os.path.isdir(folder)
        self._tutorial_practice["validated"].discard(self._tutorial_step_index)
        self._tutorial_render_step()

    def _tutorial_choose_folder(self):
        folder = filedialog.askdirectory(parent=self._tutorial_panel, initialdir=self._tutorial_practice.get("folder") or str(get_desktop_dir()))
        if not folder:
            return
        if self.core.set_output(folder):
            self._tutorial_practice["folder"] = folder
            self._tutorial_practice["folder_confirmed"] = True
            self._tutorial_practice["validated"].discard(self._tutorial_step_index)
            self._tutorial_render_step()
        else:
            self._tutorial_set_step_valid(False, "No pude usar esa carpeta. Elige otra ubicación.")

    def _tutorial_check_environment(self):
        self._tutorial_practice["env_checked"] = True
        self._tutorial_practice["validated"].discard(self._tutorial_step_index)
        self._tutorial_render_step()

    def _tutorial_mark_complete(self):
        self._tutorial_practice["completed"] = True
        self._tutorial_practice["validated"].discard(self._tutorial_step_index)
        self._tutorial_validate_step()

    def _tutorial_environment_lines(self):
        missing, warnings = self._dependency_missing()
        ok_session = COOKIES_TXT.exists() and COOKIES_TXT.stat().st_size > 80
        lines = [
            ("FFmpeg", "activo" if self.core.ffmpeg_path else "no detectado", bool(self.core.ffmpeg_path)),
            ("Runtime JS", self.core.js_runtime[0] if self.core.js_runtime else "pendiente", bool(self.core.js_runtime)),
            ("Sesión YouTube", "activa" if ok_session else "sin sesión", ok_session),
            ("Avisos", ", ".join((missing + warnings)[:2]) if (missing or warnings) else "sin avisos críticos", not missing),
        ]
        return lines

    def _tutorial_validate_step(self):
        idx = self._tutorial_step_index
        if idx == 0:
            value = self._tutorial_url_var.get().strip()
            if self.YT_REGEX.search(value):
                self._clear_url_placeholder()
                self.url.delete(0, tk.END)
                self.url.insert(0, value)
                self.url.config(fg=self.root.palette["title"])
                return self._tutorial_set_step_valid(True, "Bien: el enlace ya está colocado en el campo principal.")
            return self._tutorial_set_step_valid(False, "Todavía falta un enlace válido de YouTube.")

        if idx == 1:
            mode = self._tutorial_practice.get("mode")
            if mode in ("Video", "Audio"):
                return self._tutorial_set_step_valid(True, f"Bien: elegiste {mode}.")
            return self._tutorial_set_step_valid(False, "Elige Video o Audio antes de avanzar.")

        if idx == 2:
            fmt = self._tutorial_format_var.get().strip() if hasattr(self, "_tutorial_format_var") else ""
            if not self._tutorial_practice.get("mode"):
                return self._tutorial_set_step_valid(False, "Primero vuelve al paso anterior y elige Video o Audio.")
            if fmt:
                self._tutorial_practice["format"] = fmt
                return self._tutorial_set_step_valid(True, f"Bien: usarías {fmt}.")
            return self._tutorial_set_step_valid(False, "Elige una recomendación de formato.")

        if idx == 3:
            folder = self._tutorial_practice.get("folder")
            if self._tutorial_practice.get("folder_confirmed") and folder and os.path.isdir(folder):
                return self._tutorial_set_step_valid(True, "Bien: la carpeta de descarga quedó confirmada.")
            return self._tutorial_set_step_valid(False, "Confirma la carpeta actual o elige otra carpeta.")

        if idx == 4:
            if self._tutorial_practice.get("env_checked"):
                return self._tutorial_set_step_valid(True, "Bien: ya revisaste el estado del entorno.")
            return self._tutorial_set_step_valid(False, "Pulsa Comprobar entorno antes de continuar.")

        if self._tutorial_practice.get("completed"):
            return self._tutorial_set_step_valid(True, "Práctica completada. Ya puedes cerrar la guía o empezar una descarga real.")
        return self._tutorial_set_step_valid(False, "Pulsa Marcar práctica completada para cerrar el recorrido.")

    def _tutorial_render_step(self):
        if self._tutorial_panel is None or not self._tutorial_panel.winfo_exists():
            return
        step = self._tutorial_steps[self._tutorial_step_index]
        self._tutorial_title_lbl.config(text=step["title"])
        self._tutorial_body_lbl.config(text=step["body"])
        self._tutorial_counter_lbl.config(text=f"Paso {self._tutorial_step_index + 1} de {len(self._tutorial_steps)}")
        self._tutorial_prev_btn.state(["!disabled"] if self._tutorial_step_index > 0 else ["disabled"])
        self._tutorial_next_btn.config(text="Finalizar" if self._tutorial_step_index == len(self._tutorial_steps) - 1 else "Siguiente")
        self._tutorial_next_btn.state(["!disabled"] if self._tutorial_step_index in self._tutorial_practice["validated"] else ["disabled"])
        self._tutorial_status_lbl.config(text="Completa la acción y pulsa Validar acción.", fg=self.root.palette["text_muted"])
        for child in self._tutorial_action_frame.winfo_children():
            child.destroy()

        idx = self._tutorial_step_index
        if idx == 0:
            self._tutorial_url_var = tk.StringVar(value=self._tutorial_practice.get("url") or self._url_value())
            ttk.Entry(self._tutorial_action_frame, style="Cass.TEntry", textvariable=self._tutorial_url_var, width=48).pack(side="left", fill="x", expand=True)
            ttk.Button(self._tutorial_action_frame, text="Pegar", style="Cass.Secondary.TButton", command=self._tutorial_paste_clipboard).pack(side="left", padx=(8, 0))

        elif idx == 1:
            tk.Label(
                self._tutorial_action_frame,
                text=f"Selección actual: {self._tutorial_practice.get('mode') or 'ninguna'}",
                fg=self.root.palette["text_muted"],
                bg=self.root.palette["panel"],
                font=self.root.fonts["UI"],
            ).pack(side="left", padx=(0, 10))
            ttk.Button(self._tutorial_action_frame, text="Video", style="Cass.TButton", command=lambda: self._tutorial_pick_mode("Video")).pack(side="left", padx=(0, 8))
            ttk.Button(self._tutorial_action_frame, text="Audio", style="Cass.TButton", command=lambda: self._tutorial_pick_mode("Audio")).pack(side="left")

        elif idx == 2:
            mode = self._tutorial_practice.get("mode")
            values = (
                ["MP4 compatible", "Mejor calidad", "WebM/MKV flexible"]
                if mode == "Video"
                else ["MP3 compatible", "M4A equilibrado", "FLAC/WAV archivo grande"]
            )
            self._tutorial_format_var = tk.StringVar(value=self._tutorial_practice.get("format") or values[0])
            tk.Label(
                self._tutorial_action_frame,
                text=f"Modo: {mode or 'pendiente'}",
                fg=self.root.palette["text_muted"],
                bg=self.root.palette["panel"],
                font=self.root.fonts["UI"],
            ).pack(side="left", padx=(0, 8))
            ttk.Combobox(self._tutorial_action_frame, state="readonly", values=values, textvariable=self._tutorial_format_var, width=26, style="Cass.TCombobox").pack(side="left")

        elif idx == 3:
            folder = self._tutorial_practice.get("folder") or self.core.output_dir
            tk.Label(
                self._tutorial_action_frame,
                text=f"Carpeta: {folder}",
                fg=self.root.palette["text_muted"],
                bg=self.root.palette["panel"],
                font=self.root.fonts["SMALL"],
                wraplength=260,
                justify="left",
            ).pack(side="left", fill="x", expand=True)
            ttk.Button(self._tutorial_action_frame, text="Usar actual", style="Cass.TButton", command=self._tutorial_use_current_folder).pack(side="left", padx=(8, 6))
            ttk.Button(self._tutorial_action_frame, text="Cambiar", style="Cass.Secondary.TButton", command=self._tutorial_choose_folder).pack(side="left")

        elif idx == 4:
            ttk.Button(self._tutorial_action_frame, text="Comprobar entorno", style="Cass.TButton", command=self._tutorial_check_environment).pack(anchor="w")
            if self._tutorial_practice.get("env_checked"):
                for name, value, ok in self._tutorial_environment_lines():
                    tk.Label(
                        self._tutorial_action_frame,
                        text=f"{name}: {value}",
                        fg=self.root.palette["success"] if ok else self.root.palette["warning"],
                        bg=self.root.palette["panel"],
                        font=self.root.fonts["SMALL"],
                        anchor="w",
                    ).pack(fill="x", pady=(4, 0))

        else:
            summary = [
                ("Enlace", "listo" if self.YT_REGEX.search(getattr(self, "_tutorial_url_var", tk.StringVar(value="")).get().strip() or self._url_value()) else "pendiente"),
                ("Salida", self._tutorial_practice.get("mode") or "pendiente"),
                ("Formato", self._tutorial_practice.get("format") or "pendiente"),
                ("Carpeta", "confirmada" if self._tutorial_practice.get("folder_confirmed") else "pendiente"),
                ("Entorno", "revisado" if self._tutorial_practice.get("env_checked") else "pendiente"),
            ]
            for name, value in summary:
                tk.Label(
                    self._tutorial_action_frame,
                    text=f"{name}: {value}",
                    fg=self.root.palette["text"],
                    bg=self.root.palette["panel"],
                    font=self.root.fonts["SMALL"],
                    anchor="w",
                ).pack(fill="x")
            ttk.Button(self._tutorial_action_frame, text="Marcar práctica completada", style="Cass.TButton", command=self._tutorial_mark_complete).pack(anchor="w", pady=(8, 0))

        if self._tutorial_step_index in self._tutorial_practice["validated"]:
            self._tutorial_status_lbl.config(text="Acción validada. Ya puedes continuar.", fg=self.root.palette["success"])
        self._tutorial_set_highlight(step.get("target"))

    def _tutorial_prev(self):
        if self._tutorial_step_index > 0:
            self._tutorial_step_index -= 1
            self._tutorial_render_step()

    def _tutorial_next(self):
        if self._tutorial_step_index not in self._tutorial_practice["validated"]:
            self._tutorial_validate_step()
            return
        if self._tutorial_step_index < len(self._tutorial_steps) - 1:
            self._tutorial_step_index += 1
            self._tutorial_render_step()
        else:
            self._tutorial_close_panel(mark_seen=True)

    def show_tutorial(self):
        if self._tutorial_panel is not None and self._tutorial_panel.winfo_exists():
            self._tutorial_panel.lift()
            return

        self._tutorial_steps = self._tutorial_build_steps()
        self._tutorial_step_index = 0
        self._tutorial_practice = {
            "mode": None,
            "format": "",
            "folder": self.core.output_dir,
            "folder_confirmed": False,
            "env_checked": False,
            "completed": False,
            "validated": set(),
            "url": self._url_value(),
        }
        self._tutorial_dont_show = tk.BooleanVar(value=False)

        panel = tk.Toplevel(self.root)
        self._tutorial_panel = panel
        panel.title("Práctica guiada")
        panel.configure(bg=self.root.palette["panel"])
        panel.resizable(False, False)
        panel.attributes("-topmost", True)
        panel.geometry(f"760x430+{self.root.winfo_rootx() + self.root.win_w - 790}+{self.root.winfo_rooty() + 90}")

        self._tutorial_title_lbl = tk.Label(panel, fg=self.root.palette["title"], bg=self.root.palette["panel"], font=self.root.fonts["UI_B"], anchor="w")
        self._tutorial_title_lbl.pack(fill="x", padx=14, pady=(12, 4))
        self._tutorial_body_lbl = tk.Label(
            panel,
            fg=self.root.palette["text"],
            bg=self.root.palette["panel"],
            font=self.root.fonts["UI"],
            justify="left",
            wraplength=720,
            anchor="w",
        )
        self._tutorial_body_lbl.pack(fill="x", padx=14, pady=(0, 8))

        self._tutorial_action_frame = tk.Frame(panel, bg=self.root.palette["panel"])
        self._tutorial_action_frame.pack(fill="x", padx=14, pady=(0, 8))

        self._tutorial_status_lbl = tk.Label(
            panel,
            text="Completa la acción y pulsa Validar acción.",
            fg=self.root.palette["text_muted"],
            bg=self.root.palette["panel"],
            font=self.root.fonts["UI"],
            anchor="w",
            justify="left",
            wraplength=720,
        )
        self._tutorial_status_lbl.pack(fill="x", padx=14, pady=(0, 8))

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
        self._tutorial_validate_btn = ttk.Button(row, text="Validar acción", style="Cass.TButton", command=self._tutorial_validate_step)
        self._tutorial_validate_btn.pack(side="left", padx=8)
        self._tutorial_next_btn = ttk.Button(row, text="Siguiente", style="Cass.TButton", command=self._tutorial_next)
        self._tutorial_next_btn.pack(side="left")
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
            if self.YT_REGEX.search(s) and not self._url_value():
                self._clear_url_placeholder()
                self.url.delete(0, tk.END)
                self.url.insert(0, s.strip())
                self.url.config(fg=self.root.palette["title"])
                self._toast("Enlace detectado y pegado")
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

        tk.Label(top, text="Elige carpeta de descarga", fg=self.root.palette["title"], bg=self.root.palette["panel"], font=self.root.fonts["UI_B"]).pack(padx=14, pady=(12, 8), anchor="w")

        row = tk.Frame(top, bg=self.root.palette["panel"])
        row.pack(padx=14, pady=(0, 10))
        var = tk.StringVar(value=self.core.output_dir)
        e = ttk.Entry(row, style="Cass.TEntry", textvariable=var, width=48)
        e.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(row, text="Examinar", style="Cass.TButton", command=lambda: var.set(filedialog.askdirectory() or var.get())).grid(row=0, column=1)

        remember = tk.BooleanVar(value=False)
        tk.Checkbutton(
            top, text="Usar como predeterminada esta sesión", variable=remember,
            bg=self.root.palette["panel"], fg=self.root.palette["title"], selectcolor=self.root.palette["surface"], font=self.root.fonts["UI"],
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

        frame = tk.Frame(self.jobs_container, bg=self.root.palette["surface_alt"], highlightthickness=1, highlightbackground=self.root.palette["outline_soft"])
        frame.pack(fill="x", padx=10, pady=6)

        kind_chip = tk.Label(
            frame,
            text=kind.upper(),
            fg=self.root.palette["bg"],
            bg=self.root.palette["accent"] if kind == "video" else self.root.palette["accent_alt"],
            font=self.root.fonts["SMALL_B"],
            padx=8,
            pady=3,
        )
        kind_chip.grid(row=0, column=0, sticky="w", padx=(12, 8), pady=(10, 2))

        lbl_title = tk.Label(
            frame,
            text=job["title"],
            fg=self.root.palette["title"],
            bg=self.root.palette["surface_alt"],
            font=self.root.fonts["UI_B"],
            anchor="w",
        )
        lbl_title.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(10, 2))

        lbl_status = tk.Label(frame, text=job["status"], fg=self.root.palette["text_muted"], bg=self.root.palette["surface_alt"], font=self.root.fonts["UI"])
        lbl_status.grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 10))

        lbl_prog = tk.Label(frame, text="0%", fg=self.root.palette["text"], bg=self.root.palette["surface_alt"], font=self.root.fonts["UI_B"])
        # Centrado vertical junto a los botones (ocupa ambas filas de la tarjeta).
        lbl_prog.grid(row=0, column=2, rowspan=2, sticky="ns", padx=8, pady=6)

        btn_open = CassButton(frame, self.root, "Abrir", command=lambda j=job: self._open_file(j), variant="secondary", height=34, radius=10)
        btn_open.grid(row=0, column=3, rowspan=2, padx=(8, 4), pady=10, sticky="ew")

        btn_folder = CassButton(frame, self.root, "Carpeta", command=lambda j=job: self._open_in_folder(j), variant="secondary", height=34, radius=10)
        btn_folder.grid(row=0, column=4, rowspan=2, padx=(4, 12), pady=10, sticky="ew")

        btn_open.state(["disabled"])
        btn_folder.state(["disabled"])

        frame.grid_columnconfigure(1, weight=1)
        frame.grid_columnconfigure(3, minsize=92)
        frame.grid_columnconfigure(4, minsize=92)

        job["frame"] = frame
        job["lbl_status"] = lbl_status
        job["lbl_prog"] = lbl_prog
        job["btn_open"] = btn_open
        job["btn_folder"] = btn_folder

        self.jobs.append(job)
        self._refresh_jobs_header()
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
        job["lbl_status"].config(text="Completado", fg=self.root.palette["success"])
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
            open_with_system(path)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir el archivo:\n{e}")

    def _open_in_folder(self, job):
        path = job.get("path")
        folder = os.path.dirname(path) if path and os.path.exists(path) else self.core.output_dir
        try:
            open_with_system(folder)
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
            fg=self.root.palette["title"],
            bg=self.root.palette["panel"],
            font=self.root.fonts["UI_B"],
            wraplength=520,
            justify="left",
        ).pack(padx=14, pady=(12, 8), anchor="w")

        tk.Label(
            top,
            text="Solución recomendada: inicia sesión dentro de Cassettify.\nLuego vuelve a intentar la descarga.",
            fg=self.root.palette["text"],
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

        ttk.Button(row, text="Iniciar sesión", style="Cass.TButton", command=do_login).pack(side="left")
        ttk.Button(row, text="Cerrar", style="Cass.TButton", command=top.destroy).pack(side="left", padx=8)

    # ====== Acciones descarga ======
    def on_video(self):
        url = self._url_value()
        if not url:
            messagebox.showwarning("Falta URL", "Pega o escribe una URL.")
            return
        self._dialog_video(url)

    def on_audio(self):
        url = self._url_value()
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
            self._toast("Se abrió el inicio de sesión", 2200)
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
        self._toast("Descarga completada")

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

        # Detectar si YouTube está limitando formatos (síntoma de desafío no resuelto)
        max_height = 0
        if videos:
            max_height = max((v.get("height") or 0) for v in videos)
        if progressives:
            max_height = max(max_height, max((p.get("height") or 0) for p in progressives))

        if max_height <= 360 and max_height > 0:
            # YouTube está limitando a 360p o menos
            missing, warnings = self._dependency_missing()

            reasons = []
            if not COOKIES_TXT.exists() or COOKIES_TXT.stat().st_size < 80:
                reasons.append("No hay sesión YouTube activa")
            if not self.core.pot_plugin_available:
                reasons.append("Falta plugin bgutil POT provider")
            elif not self.core.pot_server_home:
                reasons.append("Falta servidor/script bgutil POT provider")
            if self._check_ytdlp_version():
                reasons.append("yt-dlp está anticuado")
            if not self.core.js_runtime:
                reasons.append("No hay runtime JS (Deno)")

            reason_text = "\n".join(f"  - {r}" for r in reasons) if reasons else "  (razon desconocida)"

            self._toast("YouTube está limitando a 360p", 3000)
            messagebox.showwarning(
                "Calidad Limitada a 360p",
                f"YouTube solo ofrece formato 360p.\n\n"
                f"Posibles causas:\n{reason_text}\n\n"
                f"Recomendaciones:\n"
                f"1. Inicia sesión\n"
                f"2. Ejecuta bootstrap.ps1 para instalar dependencias\n"
                f"3. Actualiza yt-dlp: pip install --upgrade yt-dlp\n\n"
                f"Continuando con descarga en 360p…"
            )

        win = tk.Toplevel(self.root)
        win.title("Formato de video")
        win.configure(bg=self.root.palette["panel"])
        win.resizable(False, False)
        if not self._tutorial_is_open():
            win.grab_set()

        tk.Label(
            win,
            text=info.get("title", "(sin título)"),
            fg=self.root.palette["title"],
            bg=self.root.palette["panel"],
            font=self.root.fonts["UI_B"],
            wraplength=520,
            justify="left",
        ).pack(padx=14, pady=(10, 6), anchor="w")

        row = tk.Frame(win, bg=self.root.palette["panel"])
        row.pack(padx=14, pady=6)

        tk.Label(row, text="Video:", fg=self.root.palette["text"], bg=self.root.palette["panel"], font=self.root.fonts["UI"]).grid(row=0, column=0, sticky="w")
        tk.Label(row, text="Audio:", fg=self.root.palette["text"], bg=self.root.palette["panel"], font=self.root.fonts["UI"]).grid(row=1, column=0, sticky="w")

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
                    f'{(item["height"] or "?")}p (fallback video+audio) {item["vcodec"]}+{item["acodec"]} .{item["ext"]}'
                )
            else:
                v_vals.append(
                    f'{(item["height"] or "?")}p (solo video) {item["vcodec"]} .{item["ext"]}'
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

        tk.Label(row2, text="Contenedor:", fg=self.root.palette["text"], bg=self.root.palette["panel"], font=self.root.fonts["UI"]).grid(row=0, column=0, sticky="w")
        cb_c = ttk.Combobox(row2, state="readonly", values=["mp4", "mkv", "webm"], width=8, style="Cass.TCombobox")
        cb_c.current(0)
        cb_c.grid(row=0, column=1, padx=8, sticky="w")

        conv = tk.BooleanVar(value=True)
        chk_conv = tk.Checkbutton(
            row2, text="Convertir/Remux con FFmpeg", variable=conv,
            bg=self.root.palette["panel"], fg=self.root.palette["title"], selectcolor=self.root.palette["surface"], font=self.root.fonts["UI"],
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

        # Advertencia si hay pocas pistas de audio
        if len(audio_sources) <= 1 and not audios:
            self._toast("Pocas pistas de audio disponibles", 2400)

        win = tk.Toplevel(self.root)
        win.title("Formato de audio")
        win.configure(bg=self.root.palette["panel"])
        win.resizable(False, False)
        if not self._tutorial_is_open():
            win.grab_set()

        tk.Label(
            win,
            text=info.get("title", "(sin título)"),
            fg=self.root.palette["title"],
            bg=self.root.palette["panel"],
            font=self.root.fonts["UI_B"],
            wraplength=520,
            justify="left",
        ).pack(padx=14, pady=(10, 6), anchor="w")

        row = tk.Frame(win, bg=self.root.palette["panel"])
        row.pack(padx=14, pady=6)

        tk.Label(row, text="Audio:", fg=self.root.palette["text"], bg=self.root.palette["panel"], font=self.root.fonts["UI"]).grid(row=0, column=0, sticky="w")
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

        tk.Label(row, text="Salida:", fg=self.root.palette["text"], bg=self.root.palette["panel"], font=self.root.fonts["UI"]).grid(row=1, column=0, sticky="w")
        cb_codec = ttk.Combobox(row, state="readonly", values=["mp3", "m4a", "flac", "wav"], width=8, style="Cass.TCombobox")
        cb_codec.current(0)
        cb_codec.grid(row=1, column=1, padx=8, pady=4, sticky="w")

        if audios:
            src_text = "Fuente: solo audio"
        elif progressives:
            src_text = "Fuente: progresivo (se extraerá audio)"
        else:
            src_text = "Fuente: sin pistas disponibles"
        tk.Label(row, text=src_text, fg=self.root.palette["accent"], bg=self.root.palette["panel"], font=self.root.fonts["UI"]).grid(row=2, column=1, sticky="w")

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


LegacyUI = UI

if HAS_CTK:
    class ModernWindow(ctk.CTk):
        def __init__(self, title="Cassettify", width=1120, height=790):
            ctk.set_appearance_mode("dark")
            super().__init__()
            self.palette = APP_THEME.copy()
            self.fonts = compute_fonts(self)
            self.win_w, self.win_h = int(width), int(height)
            self.title(title)
            self.geometry(f"{self.win_w}x{self.win_h}+140+80")
            self.minsize(900, 660)
            self.configure(fg_color=self.palette["bg"])
            self.content = self

        def _open_log_file(self):
            if LOG_FILE.exists():
                try:
                    open_with_system(LOG_FILE)
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo abrir el log:\n{e}")
            else:
                messagebox.showinfo("Log", "No hay archivo de log todavia.")

        def _open_appdata_folder(self):
            try:
                open_with_system(APPDATA_DIR)
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo abrir AppData:\n{e}")


    class UI(ctk.CTkFrame):
        YT_REGEX = LegacyUI.YT_REGEX

        def __init__(self, root):
            super().__init__(root, fg_color=root.palette["bg"], corner_radius=0)
            self.root = root
            self.pack(fill="both", expand=True, padx=18, pady=18)

            self.jobs = []
            self.job_counter = 0
            self._active_downloads = 0
            self._tutorial_panel = None
            self._tutorial_steps = []
            self._tutorial_step_index = 0
            self._update_notification_shown = False

            self.grid_columnconfigure(0, weight=1)
            self.grid_rowconfigure(6, weight=1)

            self._build_header()
            self._build_url_card()
            self._build_actions()
            self._build_status_area()
            self._build_jobs_area()

            self.core = Core(self)
            self._last_clip = ""
            self._clipboard_watcher()
            self.url.bind("<Return>", self._shortcut_video_from_url)
            self.url.bind("<Control-Return>", self._shortcut_audio_from_url)
            self.root.bind("<Control-Shift-U>", lambda _e: self.run_update_dependencies())

            self.refresh_session_status()
            self.after(2500, self._session_watcher)
            self.after(800, self._auto_show_tutorial_once)
            self.after(1200, self._startup_dependency_check)
            self.after(1400, self._refresh_dependency_strip)
            self.after(3000, self._background_version_checker)

            if self.core.ffmpeg_path:
                self._toast("FFmpeg detectado")
            else:
                self._toast("FFmpeg no detectado: usaré formatos progresivos")

        def _ctk_font(self, key):
            fam, size, weight = self.root.fonts[key]
            return ctk.CTkFont(family=fam, size=size, weight=weight)

        def _build_header(self):
            p = self.root.palette
            header = ctk.CTkFrame(self, fg_color=p["panel"], corner_radius=28, border_width=1, border_color=p["outline_soft"])
            header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
            header.grid_columnconfigure(1, weight=1)

            mark = ctk.CTkFrame(header, width=62, height=62, fg_color=p["accent_soft"], corner_radius=22)
            mark.grid(row=0, column=0, rowspan=2, padx=(22, 16), pady=22, sticky="n")
            mark.grid_propagate(False)
            ctk.CTkLabel(mark, text="CS", text_color=p["accent"], font=self._ctk_font("UI_B")).place(relx=0.5, rely=0.5, anchor="center")

            ctk.CTkLabel(header, text="Cassettify", text_color=p["title"], font=self._ctk_font("HERO")).grid(row=0, column=1, sticky="sw", pady=(22, 0))
            ctk.CTkLabel(
                header,
                text="Conversión multimedia local con una cola limpia, formatos útiles y sesión integrada.",
                text_color=p["text_muted"],
                font=self._ctk_font("HERO_SUB"),
            ).grid(row=1, column=1, sticky="nw", pady=(2, 22))

            tools = ctk.CTkFrame(header, fg_color="transparent")
            tools.grid(row=0, column=2, rowspan=2, sticky="e", padx=22)
            for text, color in (("FFmpeg", p["accent"]), ("yt-dlp", p["accent_alt"])):
                ctk.CTkLabel(
                    tools,
                    text=text,
                    text_color=p["bg"] if color == p["accent"] else p["title"],
                    fg_color=color,
                    corner_radius=999,
                    padx=12,
                    pady=5,
                    font=self._ctk_font("SMALL_B"),
                ).pack(side="left", padx=(8, 0))

        def _build_url_card(self):
            p = self.root.palette
            card = ctk.CTkFrame(self, fg_color=p["surface"], corner_radius=22, border_width=1, border_color=p["outline_soft"])
            card.grid(row=1, column=0, sticky="ew", pady=(0, 12))
            card.grid_columnconfigure(0, weight=1)
            self.url_wrap = card

            ctk.CTkLabel(card, text="ENLACE", text_color=p["accent"], font=self._ctk_font("SMALL_B")).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 4))
            self.url = ctk.CTkEntry(
                card,
                placeholder_text="Pega aquí un enlace de YouTube...",
                fg_color=p["surface_alt"],
                text_color=p["title"],
                placeholder_text_color=p["text_muted"],
                border_color=p["outline_soft"],
                corner_radius=16,
                height=50,
                font=self._ctk_font("MONO"),
            )
            self.url.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 18))

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

        def _build_actions(self):
            p = self.root.palette
            row = ctk.CTkFrame(self, fg_color="transparent")
            row.grid(row=2, column=0, sticky="ew", pady=(0, 12))
            self.actions_row = row
            for idx in range(5):
                row.grid_columnconfigure(idx, weight=1, uniform="actions")

            self.btn_video = self._button(row, "Video", self.on_video, primary=True)
            self.btn_audio = self._button(row, "Audio", self.on_audio, primary=True)
            self.btn_login = self._button(row, "Sesión", self.on_login)
            self.btn_help = self._button(row, "Guía", self.show_tutorial)
            self.btn_update = self._button(row, "Actualizar", self.run_update_dependencies)

            buttons = [self.btn_video, self.btn_audio, self.btn_login, self.btn_help, self.btn_update]
            for idx, button in enumerate(buttons):
                button.grid(row=0, column=idx, sticky="ew", padx=(0 if idx == 0 else 8, 0 if idx == len(buttons) - 1 else 8))

        def _button(self, master, text, command, primary=False):
            p = self.root.palette
            return ctk.CTkButton(
                master,
                text=text,
                command=command,
                height=48,
                corner_radius=16,
                font=self._ctk_font("UI_B"),
                fg_color=p["accent"] if primary else p["surface"],
                hover_color=p["accent_hover"] if primary else p["surface_alt"],
                text_color=p["bg"] if primary else p["text"],
                border_width=0 if primary else 1,
                border_color=p["outline_soft"],
            )

        def _build_status_area(self):
            p = self.root.palette
            info = ctk.CTkFrame(self, fg_color="transparent")
            info.grid(row=3, column=0, sticky="ew", pady=(0, 12))
            info.grid_columnconfigure(0, weight=1)
            self.info_row = info

            self.status = ctk.CTkLabel(
                info,
                text="Listo para convertir",
                text_color=p["text"],
                fg_color=p["surface_soft"],
                corner_radius=14,
                height=38,
                anchor="w",
                padx=14,
                font=self._ctk_font("UI"),
            )
            self.status.grid(row=0, column=0, sticky="ew")

            self.restricted_mode_var = tk.BooleanVar(value=False)
            self.chk_restricted_mode = ctk.CTkSwitch(
                info,
                text="Red restringida",
                variable=self.restricted_mode_var,
                command=self._on_toggle_restricted_mode,
                fg_color=p["surface_alt"],
                progress_color=p["accent"],
                button_color=p["text_muted"],
                button_hover_color=p["title"],
                text_color=p["text_muted"],
                font=self._ctk_font("UI"),
            )
            self.chk_restricted_mode.grid(row=0, column=1, padx=12)

            self.session_lbl = ctk.CTkLabel(
                info,
                text="",
                text_color=p["text_muted"],
                fg_color=p["surface_soft"],
                corner_radius=14,
                height=38,
                padx=14,
                font=self._ctk_font("UI_B"),
            )
            self.session_lbl.grid(row=0, column=2, sticky="e")

            self.deps_strip = ctk.CTkFrame(self, fg_color=p["surface_alt"], corner_radius=16, border_width=1, border_color=p["outline_soft"])
            self.deps_strip.grid(row=4, column=0, sticky="ew", pady=(0, 12))
            self.deps_strip.grid_columnconfigure(1, weight=1)
            self.deps_title = ctk.CTkLabel(self.deps_strip, text="Entorno", text_color=p["text_muted"], font=self._ctk_font("UI_B"))
            self.deps_title.grid(row=0, column=0, padx=(16, 10), pady=10)
            self.deps_summary = ctk.CTkLabel(self.deps_strip, text="Comprobando dependencias...", text_color=p["text"], anchor="w", font=self._ctk_font("UI"))
            self.deps_summary.grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=10)

            self.pb = ctk.CTkProgressBar(self, height=8, corner_radius=999, fg_color=p["surface"], progress_color=p["accent"])
            self.pb.grid(row=5, column=0, sticky="ew", pady=(0, 12))
            self.pb.set(0)

        def _build_jobs_area(self):
            p = self.root.palette
            outer = ctk.CTkFrame(self, fg_color=p["surface"], corner_radius=24, border_width=1, border_color=p["outline_soft"])
            outer.grid(row=6, column=0, sticky="nsew")
            outer.grid_columnconfigure(0, weight=1)
            outer.grid_rowconfigure(1, weight=1)
            self.jobs_wrap = outer

            head = ctk.CTkFrame(outer, fg_color="transparent")
            head.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
            head.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(head, text="Cola de descargas", text_color=p["title"], font=self._ctk_font("UI_B")).grid(row=0, column=0, sticky="w")
            self.jobs_count = ctk.CTkLabel(head, text="0 trabajos", text_color=p["text_muted"], font=self._ctk_font("SMALL"))
            self.jobs_count.grid(row=0, column=1, sticky="e")

            self.jobs_container = ctk.CTkScrollableFrame(
                outer,
                fg_color=p["surface"],
                corner_radius=0,
                scrollbar_fg_color=p["surface"],
                scrollbar_button_color=p["outline_soft"],
                scrollbar_button_hover_color=p["accent_deep"],
            )
            self.jobs_container.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
            self.empty_jobs = ctk.CTkFrame(self.jobs_container, fg_color="transparent")
            self.empty_jobs.pack(fill="both", expand=True, pady=58)
            ctk.CTkLabel(self.empty_jobs, text="Todavía no hay descargas", text_color=p["title"], font=self._ctk_font("UI_B")).pack()
            ctk.CTkLabel(
                self.empty_jobs,
                text="Pega un enlace, elige Video o Audio y Cassettify llenará esta cola.",
                text_color=p["text_muted"],
                font=self._ctk_font("UI"),
                wraplength=520,
            ).pack(pady=(6, 0))

        def _url_value(self):
            return self.url.get().strip()

        def _refresh_jobs_header(self):
            total = len(self.jobs)
            self.jobs_count.configure(text=f"{total} trabajo{'s' if total != 1 else ''}")
            if total and self.empty_jobs.winfo_ismapped():
                self.empty_jobs.pack_forget()

        def _refresh_dependency_strip(self):
            missing, warnings = self._dependency_missing()
            if missing:
                self.deps_title.configure(text="Atención", text_color=self.root.palette["error"])
                self.deps_summary.configure(
                    text="Falta: " + ", ".join(missing[:3]) + ("..." if len(missing) > 3 else ""),
                    text_color=self.root.palette["error"],
                )
            elif warnings:
                important = [w for w in warnings if "Sesión YouTube" not in w]
                visible = important or warnings
                self.deps_title.configure(text="Calidad", text_color=self.root.palette["warning"])
                self.deps_summary.configure(
                    text="Recomendado: " + ", ".join(visible[:2]) + ("..." if len(visible) > 2 else ""),
                    text_color=self.root.palette["warning"],
                )
            else:
                runtime = self.core.js_runtime[0] if self.core.js_runtime else "sin runtime JS"
                self.deps_title.configure(text="Listo", text_color=self.root.palette["success"])
                self.deps_summary.configure(text=f"FFmpeg activo · {runtime} activo · descargas preparadas", text_color=self.root.palette["text"])

        def _check_ytdlp_version(self):
            return LegacyUI._check_ytdlp_version(self)

        def _check_pot_provider(self):
            return LegacyUI._check_pot_provider(self)

        def _check_youtube_session(self):
            return LegacyUI._check_youtube_session(self)

        def _dependency_missing(self):
            return LegacyUI._dependency_missing(self)

        def _launch_ps_script(self, script_path: Path, quiet: bool = False):
            return LegacyUI._launch_ps_script(self, script_path, quiet)

        def _startup_dependency_check(self):
            return LegacyUI._startup_dependency_check(self)

        def _background_version_checker(self):
            return LegacyUI._background_version_checker(self)

        def _show_update_available_notification(self):
            return LegacyUI._show_update_available_notification(self)

        def run_update_dependencies(self):
            return LegacyUI.run_update_dependencies(self)

        def refresh_session_status(self):
            ok = COOKIES_TXT.exists() and COOKIES_TXT.stat().st_size > 80
            self.session_lbl.configure(
                text=f"YouTube: {'sesión activa' if ok else 'sin sesión'}",
                text_color=self.root.palette["success"] if ok else self.root.palette["error"],
            )

        def _session_watcher(self):
            self.refresh_session_status()
            self.after(2500, self._session_watcher)

        def _shortcut_video_from_url(self, _event=None):
            self.on_video()
            return "break"

        def _shortcut_audio_from_url(self, _event=None):
            self.on_audio()
            return "break"

        def set_status(self, t):
            self.status.configure(text=t)
            self.status.update_idletasks()

        def set_progress(self, p):
            self.pb.set(max(0, min(100, int(p))) / 100)
            self.pb.update_idletasks()

        def _toast(self, text, ms=1800):
            p = self.root.palette
            try:
                toast = ctk.CTkToplevel(self.root)
                toast.overrideredirect(True)
                toast.attributes("-topmost", True)
                toast.configure(fg_color=p["surface"])
                label = ctk.CTkLabel(toast, text=text, text_color=p["title"], font=self._ctk_font("UI"), padx=18, pady=12)
                label.pack()
                toast.update_idletasks()
                width = min(max(260, label.winfo_reqwidth() + 24), 560)
                height = max(54, label.winfo_reqheight() + 18)
                x = self.root.winfo_rootx() + self.root.winfo_width() - width - 26
                y = self.root.winfo_rooty() + 72
                toast.geometry(f"{width}x{height}+{x}+{y}")
                toast.after(ms, toast.destroy)
            except Exception:
                pass

        def _on_toggle_restricted_mode(self):
            enabled = bool(self.restricted_mode_var.get())
            self.core.set_restricted_network_mode(enabled, reason="ui_toggle")
            if enabled:
                self.set_status("Modo red restringida activado")
                self._toast("Modo red restringida activado")
            else:
                self.set_status("Modo red restringida desactivado")
                self._toast("Modo red restringida desactivado")

        def force_restricted_mode(self, enabled: bool, auto: bool = False):
            self.restricted_mode_var.set(bool(enabled))
            self.core.set_restricted_network_mode(bool(enabled), reason="auto_forbidden" if auto else "force")
            if enabled and auto:
                self.set_status("Red restringida detectada: modo protegido activado")
                self._toast("Activé modo red restringida por bloqueo de YouTube", 2400)

        def _play_done_sound(self):
            return LegacyUI._play_done_sound(self)

        def _play_error_sound(self):
            return LegacyUI._play_error_sound(self)

        def _tutorial_seen(self):
            return LegacyUI._tutorial_seen(self)

        def _mark_tutorial_seen(self):
            return LegacyUI._mark_tutorial_seen(self)

        def _auto_show_tutorial_once(self):
            if not self._tutorial_seen():
                self.show_tutorial()

        def _tutorial_is_open(self):
            return self._tutorial_panel is not None and self._tutorial_panel.winfo_exists()

        def _guide_status_items(self):
            session_ok = COOKIES_TXT.exists() and COOKIES_TXT.stat().st_size > 80
            js_name = self.core.js_runtime[0] if self.core.js_runtime else ""
            return [
                ("FFmpeg", "Activo" if self.core.ffmpeg_path else "No detectado", bool(self.core.ffmpeg_path)),
                ("Runtime JS", f"{js_name} activo" if js_name else "Deno o Node pendiente", bool(js_name)),
                ("POT provider", "Disponible" if self.core.pot_plugin_available else "Recomendado", bool(self.core.pot_plugin_available)),
                ("Sesión YouTube", "Activa" if session_ok else "Sin sesión", session_ok),
            ]

        def show_tutorial(self):
            if self._tutorial_panel is not None and self._tutorial_panel.winfo_exists():
                self._tutorial_panel.lift()
                return
            p = self.root.palette
            dont_show = tk.BooleanVar(value=False)
            panel = ctk.CTkFrame(self, fg_color=p["bg"], corner_radius=24, border_width=1, border_color=p["outline"])
            panel.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.86, relheight=0.9)
            self._tutorial_panel = panel
            panel.grid_columnconfigure(0, weight=1)
            panel.grid_rowconfigure(2, weight=1)

            header = ctk.CTkFrame(panel, fg_color=p["panel"], corner_radius=20, border_width=1, border_color=p["outline_soft"])
            header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 12))
            header.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(header, text="Guía de Cassettify", text_color=p["title"], font=self._ctk_font("HERO_SUB")).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 2))
            ctk.CTkLabel(
                header,
                text="Atajos de decisión, diagnóstico del entorno y soluciones rápidas para descargar con menos fricción.",
                text_color=p["text_muted"],
                font=self._ctk_font("UI"),
                wraplength=640,
                justify="left",
            ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 14))

            sections = ["Práctica", "Inicio", "Calidad", "Sesión", "Errores"]
            nav = ctk.CTkSegmentedButton(
                panel,
                values=sections,
                fg_color=p["surface"],
                selected_color=p["accent"],
                selected_hover_color=p["accent_hover"],
                unselected_color=p["surface"],
                unselected_hover_color=p["surface_alt"],
                text_color=p["title"],
                font=self._ctk_font("UI_B"),
            )
            nav.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 12))

            content = ctk.CTkScrollableFrame(
                panel,
                fg_color=p["surface"],
                corner_radius=18,
                border_width=1,
                border_color=p["outline_soft"],
                scrollbar_fg_color=p["surface"],
                scrollbar_button_color=p["outline_soft"],
                scrollbar_button_hover_color=p["accent_deep"],
            )
            content.grid(row=2, column=0, sticky="nsew", padx=18)
            content.grid_columnconfigure(0, weight=1)

            footer = ctk.CTkFrame(panel, fg_color="transparent")
            footer.grid(row=3, column=0, sticky="ew", padx=18, pady=18)
            footer.grid_columnconfigure(1, weight=1)
            practice = {
                "step": 0,
                "url_var": tk.StringVar(value=self._url_value()),
                "mode": None,
                "format": None,
                "folder": self.core.output_dir,
                "folder_confirmed": False,
                "env_checked": False,
                "completed": False,
                "validated": set(),
            }

            def close(mark_seen=False):
                if mark_seen:
                    self._mark_tutorial_seen()
                self._tutorial_panel = None
                panel.destroy()

            def clear():
                for child in content.winfo_children():
                    child.destroy()

            def guide_button(master, text, command, primary=False):
                return ctk.CTkButton(
                    master,
                    text=text,
                    command=command,
                    height=38,
                    corner_radius=14,
                    font=self._ctk_font("UI_B"),
                    fg_color=p["accent"] if primary else p["surface_alt"],
                    hover_color=p["accent_hover"] if primary else p["outline_soft"],
                    text_color=p["bg"] if primary else p["text"],
                    border_width=0 if primary else 1,
                    border_color=p["outline_soft"],
                )

            def card(title_text, lines, tone="normal"):
                tone_color = {
                    "normal": p["accent"],
                    "warn": p["warning"],
                    "error": p["error"],
                    "success": p["success"],
                }.get(tone, p["accent"])
                box = ctk.CTkFrame(content, fg_color=p["surface_alt"], corner_radius=16, border_width=1, border_color=p["outline_soft"])
                box.pack(fill="x", padx=12, pady=(12, 0))
                ctk.CTkLabel(box, text=title_text, text_color=tone_color, font=self._ctk_font("UI_B")).pack(anchor="w", padx=16, pady=(14, 6))
                for line in lines:
                    ctk.CTkLabel(
                        box,
                        text=line,
                        text_color=p["text"],
                        font=self._ctk_font("UI"),
                        wraplength=650,
                        justify="left",
                    ).pack(anchor="w", padx=16, pady=(0, 8))
                return box

            def status_grid():
                grid = ctk.CTkFrame(content, fg_color="transparent")
                grid.pack(fill="x", padx=12, pady=(12, 0))
                grid.grid_columnconfigure((0, 1), weight=1, uniform="guide-status")
                for idx, (name, value, ok) in enumerate(self._guide_status_items()):
                    box = ctk.CTkFrame(grid, fg_color=p["surface_alt"], corner_radius=16, border_width=1, border_color=p["outline_soft"])
                    box.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=(0 if idx % 2 == 0 else 8, 8 if idx % 2 == 0 else 0), pady=(0, 10))
                    ctk.CTkLabel(box, text=name, text_color=p["text_muted"], font=self._ctk_font("SMALL_B")).pack(anchor="w", padx=14, pady=(12, 2))
                    ctk.CTkLabel(box, text=value, text_color=p["success"] if ok else p["warning"], font=self._ctk_font("UI_B")).pack(anchor="w", padx=14, pady=(0, 12))

            def sync_practice_url():
                value = practice["url_var"].get().strip()
                self.url.delete(0, tk.END)
                self.url.insert(0, value)
                return value

            def render_practice():
                steps = [
                    ("Pega un enlace", "Escribe o pega un enlace de YouTube. La práctica no descarga nada todavía, solo valida que el campo esté listo."),
                    ("Elige salida", "Selecciona si quieres descargar video completo o extraer solo audio. No avances hasta decidirlo."),
                    ("Decide formato", "Escoge una recomendación de formato. Así sabrás qué elegir cuando aparezca el diálogo real."),
                    ("Confirma carpeta", "Confirma dónde guardarías la descarga. Puedes usar la carpeta actual o cambiarla."),
                    ("Comprueba entorno", "Ejecuta el diagnóstico para ver si FFmpeg, sesión y herramientas auxiliares están preparados."),
                    ("Ensayo final", "Revisa el resumen. Cuando todo esté marcado, la guía se dará por completada."),
                ]
                idx = practice["step"]
                title_text, body_text = steps[idx]
                body = ctk.CTkFrame(content, fg_color=p["surface_alt"], corner_radius=18, border_width=1, border_color=p["outline_soft"])
                body.pack(fill="x", padx=12, pady=12)
                ctk.CTkLabel(body, text=f"Paso {idx + 1} de {len(steps)} · {title_text}", text_color=p["accent"], font=self._ctk_font("UI_B")).pack(anchor="w", padx=16, pady=(16, 6))
                ctk.CTkLabel(body, text=body_text, text_color=p["text"], font=self._ctk_font("UI"), wraplength=650, justify="left").pack(anchor="w", padx=16, pady=(0, 12))

                action_box = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=14, border_width=1, border_color=p["outline_soft"])
                action_box.pack(fill="x", padx=16, pady=(0, 14))
                status = ctk.CTkLabel(
                    body,
                    text="Completa la acción y pulsa Validar acción.",
                    text_color=p["text_muted"],
                    font=self._ctk_font("UI"),
                    wraplength=650,
                    justify="left",
                )
                status.pack(anchor="w", padx=16, pady=(0, 14))

                controls = ctk.CTkFrame(body, fg_color="transparent")
                controls.pack(fill="x", padx=16, pady=(0, 16))
                controls.grid_columnconfigure(1, weight=1)
                format_widget = None

                def set_status(ok, message):
                    if ok:
                        practice["validated"].add(idx)
                    else:
                        practice["validated"].discard(idx)
                    status.configure(text=message, text_color=p["success"] if ok else p["warning"])
                    next_btn.configure(state="normal" if ok else "disabled")
                    return ok

                def paste_clipboard():
                    try:
                        value = self.clipboard_get().strip()
                    except Exception:
                        value = ""
                    practice["url_var"].set(value)
                    practice["validated"].discard(idx)
                    status.configure(text="Portapapeles pegado. Ahora valida el enlace.", text_color=p["text_muted"])
                    next_btn.configure(state="disabled")

                def choose_folder():
                    folder = filedialog.askdirectory(parent=self.root, initialdir=practice["folder"] or str(get_desktop_dir()))
                    if not folder:
                        return
                    if self.core.set_output(folder):
                        practice["folder"] = folder
                        practice["folder_confirmed"] = True
                        practice["validated"].discard(idx)
                        render("Práctica")
                    else:
                        set_status(False, "No pude usar esa carpeta. Elige otra ubicación.")

                def use_current_folder():
                    practice["folder"] = self.core.output_dir
                    practice["folder_confirmed"] = os.path.isdir(practice["folder"])
                    practice["validated"].discard(idx)
                    render("Práctica")

                def check_environment():
                    practice["env_checked"] = True
                    practice["validated"].discard(idx)
                    render("Práctica")

                def mark_complete():
                    practice["completed"] = True
                    practice["validated"].discard(idx)
                    validate()

                if idx == 0:
                    action_box.grid_columnconfigure(0, weight=1)
                    ctk.CTkEntry(
                        action_box,
                        textvariable=practice["url_var"],
                        fg_color=p["surface_alt"],
                        text_color=p["title"],
                        border_color=p["outline_soft"],
                        corner_radius=12,
                        height=42,
                        font=self._ctk_font("MONO"),
                    ).grid(row=0, column=0, sticky="ew", padx=12, pady=12)
                    guide_button(action_box, "Pegar portapapeles", paste_clipboard).grid(row=0, column=1, padx=(0, 12), pady=12)

                elif idx == 1:
                    selected_lbl = ctk.CTkLabel(
                        action_box,
                        text=f"Selección actual: {practice['mode'] or 'ninguna'}",
                        text_color=p["text_muted"],
                        font=self._ctk_font("UI"),
                    )
                    selected_lbl.pack(anchor="w", padx=12, pady=(12, 8))

                    def choose_mode(mode):
                        practice["mode"] = mode
                        practice["format"] = None
                        practice["validated"].discard(idx)
                        render("Práctica")

                    row = ctk.CTkFrame(action_box, fg_color="transparent")
                    row.pack(fill="x", padx=12, pady=(0, 12))
                    guide_button(row, "Video", lambda: choose_mode("Video"), primary=practice["mode"] == "Video").pack(side="left")
                    guide_button(row, "Audio", lambda: choose_mode("Audio"), primary=practice["mode"] == "Audio").pack(side="left", padx=10)

                elif idx == 2:
                    options = (
                        ["MP4 compatible", "Mejor calidad", "WebM/MKV flexible"]
                        if practice["mode"] == "Video"
                        else ["MP3 compatible", "M4A equilibrado", "FLAC/WAV archivo grande"]
                    )
                    if practice["format"] not in options:
                        practice["format"] = None
                    ctk.CTkLabel(action_box, text=f"Modo elegido: {practice['mode'] or 'pendiente'}", text_color=p["text_muted"], font=self._ctk_font("UI")).pack(anchor="w", padx=12, pady=(12, 8))
                    fmt = ctk.CTkSegmentedButton(
                        action_box,
                        values=options,
                        command=lambda value: (practice.update({"format": value}), practice["validated"].discard(idx)),
                        fg_color=p["surface_alt"],
                        selected_color=p["accent"],
                        selected_hover_color=p["accent_hover"],
                        unselected_color=p["surface_alt"],
                        unselected_hover_color=p["outline_soft"],
                        text_color=p["title"],
                        font=self._ctk_font("UI_B"),
                    )
                    format_widget = fmt
                    fmt.pack(fill="x", padx=12, pady=(0, 12))
                    if practice["format"]:
                        fmt.set(practice["format"])

                elif idx == 3:
                    folder_ok = os.path.isdir(practice["folder"])
                    ctk.CTkLabel(
                        action_box,
                        text=f"Carpeta actual: {practice['folder'] or 'sin carpeta'}",
                        text_color=p["success"] if folder_ok else p["warning"],
                        font=self._ctk_font("UI"),
                        wraplength=620,
                        justify="left",
                    ).pack(anchor="w", padx=12, pady=(12, 8))
                    row = ctk.CTkFrame(action_box, fg_color="transparent")
                    row.pack(fill="x", padx=12, pady=(0, 12))
                    guide_button(row, "Usar carpeta actual", use_current_folder, primary=practice["folder_confirmed"]).pack(side="left")
                    guide_button(row, "Cambiar carpeta", choose_folder).pack(side="left", padx=10)

                elif idx == 4:
                    guide_button(action_box, "Comprobar entorno", check_environment, primary=True).pack(anchor="w", padx=12, pady=12)
                    if practice["env_checked"]:
                        for name, value, ok in self._guide_status_items():
                            ctk.CTkLabel(
                                action_box,
                                text=f"{name}: {value}",
                                text_color=p["success"] if ok else p["warning"],
                                font=self._ctk_font("UI"),
                            ).pack(anchor="w", padx=12, pady=(0, 8))

                else:
                    summary = [
                        ("Enlace", "listo" if self.YT_REGEX.search(practice["url_var"].get().strip()) else "pendiente"),
                        ("Salida", practice["mode"] or "pendiente"),
                        ("Formato", practice["format"] or "pendiente"),
                        ("Carpeta", "confirmada" if practice["folder_confirmed"] else "pendiente"),
                        ("Entorno", "revisado" if practice["env_checked"] else "pendiente"),
                    ]
                    for name, value in summary:
                        ok = value not in ("pendiente", "")
                        ctk.CTkLabel(
                            action_box,
                            text=f"{name}: {value}",
                            text_color=p["success"] if ok else p["warning"],
                            font=self._ctk_font("UI"),
                        ).pack(anchor="w", padx=12, pady=(12 if name == "Enlace" else 0, 8))
                    guide_button(action_box, "Marcar práctica completada", mark_complete, primary=True).pack(anchor="w", padx=12, pady=(4, 12))

                def validate():
                    if idx == 0:
                        value = sync_practice_url()
                        if self.YT_REGEX.search(value):
                            return set_status(True, "Bien: el enlace ya está colocado en el campo principal.")
                        return set_status(False, "Todavía falta un enlace válido de YouTube.")
                    if idx == 1:
                        if practice["mode"] in ("Video", "Audio"):
                            return set_status(True, f"Bien: elegiste {practice['mode']}.")
                        return set_status(False, "Elige Video o Audio antes de avanzar.")
                    if idx == 2:
                        if not practice["mode"]:
                            return set_status(False, "Primero vuelve al paso anterior y elige Video o Audio.")
                        selected_format = practice["format"] or (format_widget.get() if format_widget else "")
                        if selected_format:
                            practice["format"] = selected_format
                            return set_status(True, f"Bien: usarías {practice['format']}.")
                        return set_status(False, "Elige una recomendación de formato.")
                    if idx == 3:
                        if practice["folder_confirmed"] and os.path.isdir(practice["folder"]):
                            return set_status(True, "Bien: la carpeta de descarga quedó confirmada.")
                        return set_status(False, "Confirma la carpeta actual o elige otra carpeta.")
                    if idx == 4:
                        if practice["env_checked"]:
                            return set_status(True, "Bien: ya revisaste el estado del entorno.")
                        return set_status(False, "Pulsa Comprobar entorno antes de continuar.")
                    if practice["completed"]:
                        return set_status(True, "Práctica completada. Ya puedes cerrar la guía o empezar una descarga real.")
                    return set_status(False, "Pulsa Marcar práctica completada para cerrar el recorrido.")

                def previous_step():
                    if practice["step"] > 0:
                        practice["step"] -= 1
                        render("Práctica")

                def next_step():
                    if practice["step"] not in practice["validated"]:
                        validate()
                        return
                    if practice["step"] == len(steps) - 1:
                        close(mark_seen=True)
                        return
                    practice["step"] += 1
                    render("Práctica")

                prev_btn = guide_button(controls, "Anterior", previous_step)
                prev_btn.grid(row=0, column=0, sticky="w")
                validate_btn = guide_button(controls, "Validar acción", validate, primary=True)
                validate_btn.grid(row=0, column=1)
                next_btn = guide_button(controls, "Terminar" if idx == len(steps) - 1 else "Siguiente", next_step)
                next_btn.grid(row=0, column=2, sticky="e")
                prev_btn.configure(state="normal" if idx else "disabled")
                next_btn.configure(state="normal" if idx in practice["validated"] else "disabled")
                if idx in practice["validated"]:
                    status.configure(text="Acción validada. Ya puedes continuar.", text_color=p["success"])

            def render(section_name=None):
                selected = section_name or nav.get() or "Práctica"
                clear()
                try:
                    content._parent_canvas.yview_moveto(0)
                except Exception:
                    pass

                if selected == "Práctica":
                    render_practice()

                elif selected == "Inicio":
                    card("Flujo recomendado", [
                        "1. Pega el enlace de YouTube en el campo principal.",
                        "2. Elige Video si quieres imagen y sonido, o Audio si solo quieres voz o música.",
                        "3. Selecciona formato, confirma carpeta y mira el progreso en la cola.",
                        "4. Al terminar, usa Abrir o Carpeta desde la propia tarjeta de descarga.",
                    ], "success")
                    card("Decisiones rápidas", [
                        "Audio: MP3 es el más compatible; M4A suele equilibrar calidad y tamaño.",
                        "Video: MP4 es la opción más cómoda para reproducir en casi cualquier dispositivo.",
                        "Red restringida: actívala solo si YouTube bloquea o tu red falla al listar formatos.",
                    ])

                elif selected == "Calidad":
                    status_grid()
                    card("Para conseguir mejor calidad", [
                        "Mantén yt-dlp actualizado y FFmpeg disponible para combinar video y audio separados.",
                        "Inicia sesión si YouTube limita la descarga a 360p o pide verificación.",
                        "El POT provider y un runtime JS ayudan cuando YouTube cambia sus desafíos.",
                    ], "success")
                    actions = ctk.CTkFrame(content, fg_color="transparent")
                    actions.pack(fill="x", padx=12, pady=(12, 0))
                    guide_button(actions, "Actualizar dependencias", self.run_update_dependencies, primary=True).pack(side="left")

                elif selected == "Sesión":
                    session_ok = COOKIES_TXT.exists() and COOKIES_TXT.stat().st_size > 80
                    card("Estado de YouTube", [
                        "Sesión activa: puedes intentar formatos de mayor calidad con menos bloqueos." if session_ok else "Sin sesión guardada: YouTube puede limitar formatos o pedir verificación.",
                        "La sesión se guarda localmente en AppData de Cassettify.",
                    ], "success" if session_ok else "warn")
                    actions = ctk.CTkFrame(content, fg_color="transparent")
                    actions.pack(fill="x", padx=12, pady=(12, 0))
                    guide_button(actions, "Iniciar sesión", self.on_login, primary=True).pack(side="left")
                    guide_button(actions, "Abrir AppData", self.root._open_appdata_folder).pack(side="left", padx=10)
                    guide_button(actions, "Abrir log", self.root._open_log_file).pack(side="left")
                    card("Cuándo conviene iniciar sesión", [
                        "Cuando la lista de formatos queda corta, aparece solo 360p o YouTube pide confirmar actividad.",
                        "Si una sesión deja de funcionar, inicia sesión de nuevo y guarda otra vez.",
                    ])

                else:
                    card("Solo aparece 360p", [
                        "Primero prueba iniciar sesión. Después actualiza dependencias si sigue igual.",
                        "Si estás en una red pública o filtrada, activa Red restringida y reintenta.",
                    ], "warn")
                    card("No lista formatos o YouTube bloquea", [
                        "Espera uno o dos minutos y reintenta; a veces YouTube aplica bloqueos temporales.",
                        "Comprueba sesión, runtime JS y POT provider desde la pestaña Calidad.",
                    ], "error")
                    card("La descarga falla al final", [
                        "Revisa que FFmpeg esté detectado si elegiste convertir o remuxear.",
                        "Abre el log para ver el detalle técnico y actualiza dependencias si el error viene de yt-dlp.",
                    ])
                    actions = ctk.CTkFrame(content, fg_color="transparent")
                    actions.pack(fill="x", padx=12, pady=(12, 0))
                    guide_button(actions, "Activar red restringida", lambda: self.force_restricted_mode(True), primary=True).pack(side="left")
                    guide_button(actions, "Abrir log", self.root._open_log_file).pack(side="left", padx=10)

            nav.configure(command=render)
            nav.set("Práctica")
            ctk.CTkCheckBox(footer, text="No mostrar al iniciar", variable=dont_show, text_color=p["text"], font=self._ctk_font("UI"), fg_color=p["accent"]).grid(row=0, column=0, sticky="w")
            guide_button(footer, "Cerrar", lambda: close(dont_show.get())).grid(row=0, column=2, sticky="e")
            render()
            panel.lift()

        def _modal(self, title, width, height):
            win = ctk.CTkToplevel(self.root)
            win.title(title)
            win.configure(fg_color=self.root.palette["bg"])
            win.geometry(f"{width}x{height}+{self.root.winfo_rootx() + 80}+{self.root.winfo_rooty() + 80}")
            win.transient(self.root)
            win.resizable(False, False)
            if not self._tutorial_is_open():
                win.grab_set()
            return win

        def _ask_output_dir_session(self):
            if self.core.remember_output and os.path.isdir(self.core.output_dir):
                return self.core.output_dir

            p = self.root.palette
            win = self._modal("Carpeta de descarga", 620, 240)
            var = tk.StringVar(value=self.core.output_dir)
            remember = tk.BooleanVar(value=False)
            result = {"folder": None}

            ctk.CTkLabel(win, text="Elige carpeta de descarga", text_color=p["title"], font=self._ctk_font("UI_B")).pack(anchor="w", padx=22, pady=(22, 8))
            row = ctk.CTkFrame(win, fg_color="transparent")
            row.pack(fill="x", padx=22, pady=(0, 12))
            entry = ctk.CTkEntry(row, textvariable=var, fg_color=p["surface"], border_color=p["outline_soft"], corner_radius=14, height=42, font=self._ctk_font("UI"))
            entry.pack(side="left", fill="x", expand=True)

            def browse():
                folder = filedialog.askdirectory(parent=win, initialdir=var.get() or str(get_desktop_dir()))
                if folder:
                    var.set(folder)

            self._button(row, "Examinar", browse).pack(side="left", padx=(10, 0))
            ctk.CTkCheckBox(win, text="Usar como predeterminada esta sesión", variable=remember, text_color=p["text"], fg_color=p["accent"], font=self._ctk_font("UI")).pack(anchor="w", padx=22, pady=(2, 12))

            buttons = ctk.CTkFrame(win, fg_color="transparent")
            buttons.pack(fill="x", padx=22)

            def accept():
                result["folder"] = var.get().strip()
                win.destroy()

            self._button(buttons, "Cancelar", win.destroy).pack(side="right")
            self._button(buttons, "Aceptar", accept, primary=True).pack(side="right", padx=(0, 10))
            win.wait_window()

            folder = result["folder"]
            if not folder:
                return None
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

        def _clipboard_watcher(self):
            try:
                s = self.clipboard_get()
            except Exception:
                s = ""
            if s != self._last_clip:
                self._last_clip = s
                if self.YT_REGEX.search(s) and not self._url_value():
                    self.url.delete(0, tk.END)
                    self.url.insert(0, s.strip())
                    self._toast("Enlace detectado y pegado")
            self.after(800, self._clipboard_watcher)

        def _create_job(self, title, kind):
            p = self.root.palette
            self.job_counter += 1
            job = {"id": self.job_counter, "title": title or "(sin título)", "type": kind, "status": "Preparando...", "progress": 0, "path": None}

            frame = ctk.CTkFrame(self.jobs_container, fg_color=p["surface_alt"], corner_radius=18, border_width=1, border_color=p["outline_soft"])
            frame.pack(fill="x", padx=4, pady=7)
            frame.grid_columnconfigure(1, weight=1)

            chip_color = p["accent"] if kind == "video" else p["accent_alt"]
            chip = ctk.CTkLabel(frame, text=kind.upper(), fg_color=chip_color, text_color=p["bg"], corner_radius=999, padx=10, pady=4, font=self._ctk_font("SMALL_B"))
            chip.grid(row=0, column=0, padx=(14, 10), pady=(14, 2), sticky="w")

            lbl_title = ctk.CTkLabel(frame, text=job["title"], text_color=p["title"], font=self._ctk_font("UI_B"), anchor="w")
            lbl_title.grid(row=0, column=1, sticky="ew", pady=(14, 2))
            lbl_status = ctk.CTkLabel(frame, text=job["status"], text_color=p["text_muted"], font=self._ctk_font("UI"), anchor="w")
            lbl_status.grid(row=1, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 14))
            lbl_prog = ctk.CTkLabel(frame, text="0%", text_color=p["text"], font=self._ctk_font("UI_B"), width=48)
            lbl_prog.grid(row=0, column=2, rowspan=2, padx=10)

            btn_open = self._button(frame, "Abrir", lambda j=job: self._open_file(j))
            btn_folder = self._button(frame, "Carpeta", lambda j=job: self._open_in_folder(j))
            btn_open.grid(row=0, column=3, rowspan=2, padx=(4, 6), pady=14, sticky="ew")
            btn_folder.grid(row=0, column=4, rowspan=2, padx=(0, 14), pady=14, sticky="ew")
            btn_open.configure(state="disabled")
            btn_folder.configure(state="disabled")

            job.update({"frame": frame, "lbl_status": lbl_status, "lbl_prog": lbl_prog, "btn_open": btn_open, "btn_folder": btn_folder})
            self.jobs.append(job)
            self._refresh_jobs_header()
            return job

        def update_job_progress(self, job, pct):
            job["progress"] = pct
            job["lbl_prog"].configure(text=f"{pct}%")
            job["lbl_status"].configure(text="Descargando...")
            self.set_progress(pct)
            self.set_status(f"Descargando... {pct}%")

        def mark_job_finished(self, job):
            job["status"] = "Completado"
            job["progress"] = 100
            job["lbl_prog"].configure(text="100%")
            job["lbl_status"].configure(text="Completado", text_color=self.root.palette["success"])
            job["btn_open"].configure(state="normal")
            job["btn_folder"].configure(state="normal")
            self.set_progress(100)

        def mark_job_error(self, job, msg):
            job["status"] = "Error"
            job["lbl_status"].configure(text="Error", text_color=self.root.palette["error"])
            self._play_error_sound()

        def _open_file(self, job):
            return LegacyUI._open_file(self, job)

        def _open_in_folder(self, job):
            return LegacyUI._open_in_folder(self, job)

        def show_antibot_popup(self, err_text: str):
            if COOKIES_TXT.exists():
                self._toast("YouTube bloqueó la descarga. Prueba a reintentar.", 2400)
                messagebox.showwarning(
                    "YouTube",
                    "YouTube ha pedido verificación/bloqueo temporal.\n\n"
                    "Ya hay sesión guardada, pero puede ser un bloqueo por red/IP.\n"
                    "Prueba otra vez en 1-2 minutos o cambia de red.",
                )
                return
            p = self.root.palette
            win = self._modal("YouTube pide verificación", 560, 230)
            ctk.CTkLabel(win, text="YouTube ha bloqueado la descarga.", text_color=p["title"], font=self._ctk_font("UI_B")).pack(anchor="w", padx=22, pady=(22, 8))
            ctk.CTkLabel(
                win,
                text="Inicia sesión dentro de Cassettify y vuelve a intentar la descarga.",
                text_color=p["text"],
                font=self._ctk_font("UI"),
                wraplength=500,
            ).pack(anchor="w", padx=22)
            row = ctk.CTkFrame(win, fg_color="transparent")
            row.pack(fill="x", padx=22, pady=(22, 0))

            def do_login():
                win.destroy()
                self.on_login()

            self._button(row, "Iniciar sesión", do_login, primary=True).pack(side="left")
            self._button(row, "Cerrar", win.destroy).pack(side="left", padx=10)

        def on_video(self):
            url = self._url_value()
            if not url:
                messagebox.showwarning("Falta URL", "Pega o escribe una URL.")
                return
            self._dialog_video(url)

        def on_audio(self):
            url = self._url_value()
            if not url:
                messagebox.showwarning("Falta URL", "Pega o escribe una URL.")
                return
            self._dialog_audio(url)

        def on_login(self):
            return LegacyUI.on_login(self)

        def on_download_start(self):
            self._active_downloads += 1
            if self._active_downloads == 1:
                self.set_status("Preparando...")
                self.set_progress(0)
                try:
                    self.pb.start()
                except Exception:
                    pass

        def _download_finished(self):
            self._active_downloads = max(0, self._active_downloads - 1)
            if self._active_downloads == 0:
                self.set_status("Listo")
                self.set_progress(0)
                try:
                    self.pb.stop()
                except Exception:
                    pass
            self.url.delete(0, tk.END)
            self.url.focus_set()
            self._play_done_sound()
            self._toast("Descarga completada")

        def _dialog_video(self, url):
            self.set_status("Buscando formatos...")

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
            max_height = max([0] + [(v.get("height") or 0) for v in videos] + [(p.get("height") or 0) for p in progressives])
            if 0 < max_height <= 360:
                self._toast("YouTube está limitando a 360p", 3000)

            video_choices = [("V", v) for v in videos] or [("P", p) for p in progressives]
            v_vals = []
            for kind, item in video_choices:
                if kind == "P":
                    v_vals.append(f'{(item["height"] or "?")}p · video+audio · {item["vcodec"]}+{item["acodec"]} · .{item["ext"]}')
                else:
                    v_vals.append(f'{(item["height"] or "?")}p · solo video · {item["vcodec"]} · .{item["ext"]}')
            a_vals = [f'{int(a.get("abr") or 0)}kbps · {a.get("acodec")} · .{a.get("ext")}' for a in audios] or ["(sin audio separado)"]
            if not v_vals:
                messagebox.showwarning("Formato", "No hay formatos de video disponibles.")
                self.set_status("Sin formatos")
                return

            p = self.root.palette
            win = self._modal("Formato de video", 680, 410)
            ctk.CTkLabel(win, text=info.get("title", "(sin título)"), text_color=p["title"], font=self._ctk_font("UI_B"), wraplength=620).pack(anchor="w", padx=22, pady=(22, 12))
            ctk.CTkLabel(win, text="Video", text_color=p["text_muted"], font=self._ctk_font("SMALL_B")).pack(anchor="w", padx=22)
            cb_v = ctk.CTkComboBox(win, values=v_vals, width=620, height=38, state="readonly", fg_color=p["surface"], border_color=p["outline_soft"], button_color=p["surface_alt"])
            cb_v.pack(anchor="w", padx=22, pady=(4, 12))
            cb_v.set(v_vals[0])
            ctk.CTkLabel(win, text="Audio", text_color=p["text_muted"], font=self._ctk_font("SMALL_B")).pack(anchor="w", padx=22)
            cb_a = ctk.CTkComboBox(win, values=a_vals, width=620, height=38, state="readonly", fg_color=p["surface"], border_color=p["outline_soft"], button_color=p["surface_alt"])
            cb_a.pack(anchor="w", padx=22, pady=(4, 12))
            cb_a.set(a_vals[0])

            row = ctk.CTkFrame(win, fg_color="transparent")
            row.pack(fill="x", padx=22, pady=(2, 16))
            cb_c = ctk.CTkComboBox(row, values=["mp4", "mkv", "webm"], width=120, height=36, state="readonly", fg_color=p["surface"], border_color=p["outline_soft"])
            cb_c.set("mp4")
            cb_c.pack(side="left")
            conv = tk.BooleanVar(value=bool(self.core.ffmpeg_path))
            ctk.CTkCheckBox(row, text="Convertir/Remux con FFmpeg", variable=conv, text_color=p["text"], fg_color=p["accent"], font=self._ctk_font("UI")).pack(side="left", padx=16)
            if not self.core.ffmpeg_path:
                conv.set(False)

            buttons = ctk.CTkFrame(win, fg_color="transparent")
            buttons.pack(fill="x", padx=22)

            def kickoff():
                v_index = v_vals.index(cb_v.get())
                kind, selected = video_choices[v_index]
                vid_id = selected["id"] if selected else None
                aud_id = None if kind == "P" else (audios[a_vals.index(cb_a.get())]["id"] if audios else None)
                container = cb_c.get().strip().lower() or "mp4"
                convertir = bool(conv.get()) and bool(self.core.ffmpeg_path)
                if not self._ask_output_dir_session():
                    return
                win.destroy()
                job = self._create_job(info.get("title", "(sin título)"), "video")
                self.core.download_video(url, vid_id, aud_id, container, convertir, job, on_done=lambda: self.after(0, self._download_finished))

            self._button(buttons, "Cancelar", win.destroy).pack(side="right")
            self._button(buttons, "Descargar video", kickoff, primary=True).pack(side="right", padx=(0, 10))

        def _dialog_audio(self, url):
            self.set_status("Buscando formatos...")

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
            audio_sources = list(audios) if audios else list(progressives)
            if not audio_sources:
                messagebox.showwarning("Formato", "No hay pistas de audio disponibles.")
                return
            a_vals = (
                [f'{int(a.get("abr") or 0)}kbps · {a.get("acodec")} · .{a.get("ext")}' for a in audios]
                if audios else
                [f'≈{int((p.get("tbr") or 0))}kbps · progresivo · {p.get("acodec")} · .{p.get("ext")}' for p in progressives]
            )

            p = self.root.palette
            win = self._modal("Formato de audio", 680, 330)
            ctk.CTkLabel(win, text=info.get("title", "(sin título)"), text_color=p["title"], font=self._ctk_font("UI_B"), wraplength=620).pack(anchor="w", padx=22, pady=(22, 12))
            ctk.CTkLabel(win, text="Audio", text_color=p["text_muted"], font=self._ctk_font("SMALL_B")).pack(anchor="w", padx=22)
            cb_a = ctk.CTkComboBox(win, values=a_vals, width=620, height=38, state="readonly", fg_color=p["surface"], border_color=p["outline_soft"], button_color=p["surface_alt"])
            cb_a.pack(anchor="w", padx=22, pady=(4, 12))
            cb_a.set(a_vals[0])
            ctk.CTkLabel(win, text="Salida", text_color=p["text_muted"], font=self._ctk_font("SMALL_B")).pack(anchor="w", padx=22)
            cb_codec = ctk.CTkComboBox(win, values=["mp3", "m4a", "flac", "wav"], width=140, height=36, state="readonly", fg_color=p["surface"], border_color=p["outline_soft"])
            cb_codec.pack(anchor="w", padx=22, pady=(4, 18))
            cb_codec.set("mp3")
            buttons = ctk.CTkFrame(win, fg_color="transparent")
            buttons.pack(fill="x", padx=22)

            def kickoff():
                aud_id = audio_sources[a_vals.index(cb_a.get())]["id"]
                codec = cb_codec.get().strip().lower() or "mp3"
                if not self._ask_output_dir_session():
                    return
                win.destroy()
                job = self._create_job(info.get("title", "(sin título)"), "audio")
                self.core.download_audio(url, aud_id, codec, job, on_done=lambda: self.after(0, self._download_finished))

            self._button(buttons, "Cancelar", win.destroy).pack(side="right")
            self._button(buttons, "Descargar audio", kickoff, primary=True).pack(side="right", padx=(0, 10))


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
    root = ModernWindow() if HAS_CTK else RetroWindow()
    try:
        icon_path = APP_DIR / "icono.ico"
        if icon_path.exists():
            root.iconbitmap(str(icon_path))
    except Exception:
        pass

    ui = UI(root)
    root.mainloop()
