"""
main.py
Gestor de Facturas Electrónicas — Interfaz principal.

Reemplaza Mail Attachment Downloader con descarga automática,
clasificación por tipo de comprobante y estructura de carpetas inteligente.

Uso: python main.py
"""

from __future__ import annotations   # compatibilidad con Python 3.9

import os
import re
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from config_manager import ConfigManager
from imap_downloader import IMAPDownloader
from xml_classifier import XMLClassifier
from folder_manager import FolderManager
from auditor import Auditor, AuditReport

# Integración opcional con el Sistema XML
try:
    from sistema_xml_bridge import (
        is_flask_running, open_in_sistema_xml,
        trigger_procesamiento, get_bridge_status,
        get_resolved_folder_name,
    )
    _BRIDGE_AVAILABLE = True
except ImportError:
    _BRIDGE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Paleta de colores
# ---------------------------------------------------------------------------
CLR_BG        = "#f5f6fa"
CLR_HEADER    = "#2c3e50"
CLR_ACCENT    = "#27ae60"
CLR_ACCENT_HV = "#2ecc71"
CLR_BTN_BLUE  = "#2980b9"
CLR_BTN_RED   = "#e74c3c"
CLR_MUTED     = "#7f8c8d"
CLR_WHITE     = "#ffffff"
CLR_CONSOLE   = "#1e2430"
CLR_LOG_TEXT  = "#a8d8a8"
CLR_IVA       = "#1a6fb5"   # azul para etiqueta IVA
CLR_REA       = "#2e7d32"   # verde para etiqueta REA

MONTHS_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]

# Regex básica de e-mail
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Sentinel que se muestra en el campo de contraseña al editar un cliente existente.
# Si el usuario no lo modifica, se conserva la contraseña guardada sin exponerla.
_PWD_UNCHANGED = "••••••••••••••••"


def _normalize_app_password(raw: str) -> str:
    """
    Las contraseñas de aplicación de Google se copian a veces con espacios
    (formato visual: xxxx xxxx xxxx xxxx). Los eliminamos para obtener
    los 16 caracteres reales que acepta IMAP.
    Para Outlook u otros proveedores, solo elimina espacios al inicio/fin.
    """
    return raw.replace(" ", "").replace("\t", "").strip()


def _validate_app_password(pwd: str, email: str = "") -> tuple[bool, str]:
    """
    Valida la contraseña según el proveedor detectado.
    - Gmail: 16 caracteres alfanuméricos (contraseña de aplicación)
    - Outlook y otros: cualquier contraseña no vacía
    """
    from imap_downloader import get_imap_config
    if not pwd:
        return False, "La contraseña no puede estar vacía."

    config = get_imap_config(email) if email else None
    requires_app_pwd = config.get("requires_app_password", True) if config else True

    if requires_app_pwd:
        if len(pwd) != 16:
            return False, (
                f"La contraseña de aplicación de Gmail debe tener 16 caracteres "
                f"(tiene {len(pwd)}). Verifique que la copió completa y sin espacios extra."
            )
        if not pwd.isalnum():
            return False, "La contraseña de aplicación solo debe contener letras y números."

    return True, ""


# ---------------------------------------------------------------------------
# Aplicación principal
# ---------------------------------------------------------------------------

class GestorFacturasApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Gestor de Facturas Electrónicas")
        self.root.geometry("1160x740")
        self.root.minsize(1020, 660)
        self.root.configure(bg=CLR_BG)

        self.config     = ConfigManager()
        self.classifier = XMLClassifier(
            default_folder=self.config.config.get("default_folder", "CyG")
        )
        self.log_queue    = queue.Queue()
        self._downloading = False
        self._cancel_event = threading.Event()
        self._log_lock     = threading.Lock()
        self._edit_index: int | None = None
        self._edit_email: str = ""

        self._build_styles()
        self._build_ui()
        self._refresh_clients()
        self._poll_log()
        self._check_paths()   # Validar rutas al arrancar

    # -----------------------------------------------------------------------
    # Estilos ttk
    # -----------------------------------------------------------------------

    def _build_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook",        background=CLR_BG,   borderwidth=0)
        style.configure("TNotebook.Tab",    font=("Segoe UI", 10), padding=(14, 6))
        style.configure("TFrame",           background=CLR_BG)
        style.configure("TLabel",           background=CLR_BG,   font=("Segoe UI", 10))
        style.configure("TEntry",           font=("Segoe UI", 10))
        style.configure("TCombobox",        font=("Segoe UI", 10))
        style.configure("Treeview",         font=("Segoe UI", 10), rowheight=24)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("TProgressbar",     troughcolor="#dfe6e9",
                        background=CLR_ACCENT, thickness=8)

    # -----------------------------------------------------------------------
    # Estructura de la ventana
    # -----------------------------------------------------------------------

    def _build_ui(self):
        hdr = tk.Frame(self.root, bg=CLR_HEADER, pady=14)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Gestor de Facturas Electrónicas",
                 font=("Segoe UI", 16, "bold"),
                 bg=CLR_HEADER, fg=CLR_WHITE).pack()
        tk.Label(hdr,
                 text="Descarga, clasificación y organización automática de comprobantes",
                 font=("Segoe UI", 9), bg=CLR_HEADER, fg="#bdc3c7").pack()

        # Indicador de conexión con el Sistema XML
        self.bridge_lbl = tk.Label(
            hdr, text="", font=("Segoe UI", 8),
            bg=CLR_HEADER, fg="#bdc3c7",
        )
        self.bridge_lbl.pack(pady=(2, 0))
        self._update_bridge_indicator()

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=12, pady=10)

        self.tab_dl      = ttk.Frame(self.nb)
        self.tab_clients = ttk.Frame(self.nb)
        self.tab_audit   = ttk.Frame(self.nb)
        self.tab_cfg     = ttk.Frame(self.nb)

        self.nb.add(self.tab_dl,      text="  Descarga  ")
        self.nb.add(self.tab_clients, text="  Clientes  ")
        self.nb.add(self.tab_audit,   text="  Auditoría  ")
        self.nb.add(self.tab_cfg,     text="  Configuración  ")

        self._build_download_tab()
        self._build_clients_tab()
        self._build_audit_tab()
        self._build_config_tab()

        # Validar rutas al cambiar de pestaña (útil si se configuró en la sesión)
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # -----------------------------------------------------------------------
    # TAB: Descarga
    # -----------------------------------------------------------------------

    def _build_download_tab(self):
        outer = ttk.Frame(self.tab_dl)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        # ── Panel izquierdo ──────────────────────────────────────────────────
        # Estructura: top (controles fijos) + middle (lista expansible) + bottom (acciones fijas)
        # El truco es NO usar pack_propagate(False) en el contenedor exterior,
        # sino dividirlo en tres zonas con pack + side="bottom" para la zona de acción.
        left = tk.Frame(outer, bg=CLR_BG, width=360)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)

        # ── ZONA SUPERIOR: período + selectores + botones de filtro ──────────
        top = tk.Frame(left, bg=CLR_BG)
        top.pack(fill="x", side="top")

        self._section_label(top, "Período a procesar")

        # ── Fila compacta: Año + botón toggle ───────────────────────────────
        period_header = tk.Frame(top, bg=CLR_BG)
        period_header.pack(fill="x", pady=(4, 2))
        period_header.columnconfigure(1, weight=1)

        tk.Label(period_header, text="Año:", bg=CLR_BG,
                 font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w")
        yr = datetime.now().year
        self.year_var = tk.StringVar(value=str(yr))
        year_cb = ttk.Combobox(period_header, textvariable=self.year_var,
                               values=[str(y) for y in range(yr - 3, yr + 2)],
                               width=10, state="readonly")
        year_cb.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        year_cb.bind("<<ComboboxSelected>>", lambda _: self._refresh_clients())

        # Botón toggle — muestra los meses seleccionados cuando está colapsado
        self._period_expanded = tk.BooleanVar(value=False)
        self._period_toggle_btn = tk.Button(
            period_header, text="▼ Meses",
            font=("Segoe UI", 8), bg=CLR_BG, fg=CLR_HEADER,
            relief="flat", cursor="hand2", padx=6,
            command=self._toggle_period_panel,
        )
        self._period_toggle_btn.grid(row=0, column=2, padx=(6, 0))

        # Indicador de meses seleccionados (visible cuando está colapsado)
        self._period_summary_lbl = tk.Label(
            top, text="", bg=CLR_BG, fg=CLR_ACCENT,
            font=("Segoe UI", 8), anchor="w",
        )
        self._period_summary_lbl.pack(fill="x", pady=(0, 2))

        # ── Panel colapsable con grilla de meses ─────────────────────────────
        self._months_panel = tk.Frame(top, bg=CLR_BG)
        # Arranca oculto

        # Botones de selección rápida dentro del panel
        quick_frm = tk.Frame(self._months_panel, bg=CLR_BG)
        quick_frm.pack(fill="x", pady=(2, 4))
        for col in range(5):
            quick_frm.columnconfigure(col, weight=1)

        ttk.Button(quick_frm, text="Todos",
                   command=self._select_all_months).grid(
                       row=0, column=0, sticky="ew", padx=(0, 2))
        ttk.Button(quick_frm, text="Limpiar",
                   command=self._clear_months).grid(
                       row=0, column=1, sticky="ew", padx=(0, 2))
        ttk.Button(quick_frm, text="C1",
                   command=lambda: self._select_cuatrimestre(1)).grid(
                       row=0, column=2, sticky="ew", padx=(0, 2))
        ttk.Button(quick_frm, text="C2",
                   command=lambda: self._select_cuatrimestre(2)).grid(
                       row=0, column=3, sticky="ew", padx=(0, 2))
        ttk.Button(quick_frm, text="C3",
                   command=lambda: self._select_cuatrimestre(3)).grid(
                       row=0, column=4, sticky="ew")

        # Checkboxes de meses
        months_grid = tk.Frame(self._months_panel, bg=CLR_BG)
        months_grid.pack(fill="x")
        for col in range(4):
            months_grid.columnconfigure(col, weight=1)

        self.month_vars: list[tk.BooleanVar] = []
        now_month = datetime.now().month

        for i, mes in enumerate(MONTHS_ES):
            var = tk.BooleanVar(value=(i + 1 == now_month))
            self.month_vars.append(var)
            cb = tk.Checkbutton(
                months_grid, text=mes[:3],
                variable=var, bg=CLR_BG, font=("Segoe UI", 9),
                command=self._on_month_changed,
            )
            cb.grid(row=i // 4, column=i % 4, sticky="w", padx=2, pady=1)

        self.month_var = tk.StringVar(value=MONTHS_ES[now_month - 1])

        # Actualizar el indicador inicial
        self._update_period_summary()

        ttk.Separator(top, orient="horizontal").pack(fill="x", pady=(2, 6))

        # Banner de advertencia de rutas
        self.path_warning_frm = tk.Frame(top, bg="#fdecea", relief="flat")
        self.path_warning_lbl = tk.Label(
            self.path_warning_frm,
            text="", bg="#fdecea", fg="#c0392b",
            font=("Segoe UI", 8, "bold"),
            wraplength=330, justify="left",
            padx=8, pady=5,
        )
        self.path_warning_lbl.pack(fill="x")
        self.path_warning_frm.pack(fill="x", pady=(0, 4))
        self.path_warning_frm.pack_forget()

        self._section_label(top, "Clientes a procesar")

        # Botones de selección 2×2
        btn_grid = tk.Frame(top, bg=CLR_BG)
        btn_grid.pack(fill="x", pady=(4, 6))
        btn_grid.columnconfigure(0, weight=1)
        btn_grid.columnconfigure(1, weight=1)

        ttk.Button(btn_grid, text="Todos",
                   command=self._select_all).grid(
                       row=0, column=0, sticky="ew", padx=(0, 3), pady=(0, 3))
        ttk.Button(btn_grid, text="Ninguno",
                   command=self._deselect_all).grid(
                       row=0, column=1, sticky="ew", padx=(3, 0), pady=(0, 3))
        ttk.Button(btn_grid, text="Solo IVA",
                   command=lambda: self._select_by_type("IVA")).grid(
                       row=1, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(btn_grid, text="Solo REA",
                   command=lambda: self._select_by_type("REA")).grid(
                       row=1, column=1, sticky="ew", padx=(3, 0))

        # ── Barra de búsqueda ────────────────────────────────────────────────
        search_frm = tk.Frame(top, bg=CLR_BG)
        search_frm.pack(fill="x", pady=(0, 4))
        search_frm.columnconfigure(0, weight=1)

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._refresh_clients())

        self._search_entry = ttk.Entry(
            search_frm, textvariable=self.search_var,
            font=("Segoe UI", 9),
        )
        self._search_entry.grid(row=0, column=0, sticky="ew", ipady=3)
        self._search_entry.insert(0, "Buscar cliente...")
        self._search_entry.configure(foreground="#aaaaaa")

        def _search_focus_in(_e):
            if self.search_var.get() == "Buscar cliente...":
                self._search_entry.delete(0, "end")
                self._search_entry.configure(foreground="")

        def _search_focus_out(_e):
            if not self.search_var.get():
                self._search_entry.configure(foreground="#aaaaaa")
                self._search_entry.insert(0, "Buscar cliente...")

        self._search_entry.bind("<FocusIn>",  _search_focus_in)
        self._search_entry.bind("<FocusOut>", _search_focus_out)

        tk.Button(
            search_frm, text="×", font=("Segoe UI", 11),
            bg=CLR_BG, fg=CLR_MUTED, relief="flat",
            cursor="hand2", padx=2,
            command=self._clear_search,
        ).grid(row=0, column=1, padx=(2, 0))

        # ── ZONA INFERIOR FIJA: botones de acción — siempre visibles ─────────
        bottom = tk.Frame(left, bg=CLR_BG)
        bottom.pack(fill="x", side="bottom", pady=(6, 0))

        # Separador visual entre lista y botones
        ttk.Separator(bottom, orient="horizontal").pack(fill="x", pady=(0, 8))

        self.dl_btn = tk.Button(
            bottom, text="  Iniciar Descarga",
            font=("Segoe UI", 11, "bold"),
            bg=CLR_ACCENT, fg=CLR_WHITE, activebackground=CLR_ACCENT_HV,
            relief="flat", pady=10, cursor="hand2",
            command=self._toggle_download,
        )
        self.dl_btn.pack(fill="x")

        # Barra de progreso
        self.progress_var = tk.DoubleVar()
        ttk.Progressbar(bottom, variable=self.progress_var,
                        maximum=100).pack(fill="x", pady=(6, 0))
        self.progress_lbl = tk.Label(bottom, text="", bg=CLR_BG,
                                     fg=CLR_MUTED, font=("Segoe UI", 8))
        self.progress_lbl.pack(anchor="w")

        # Botón "Abrir en Sistema XML"
        self._last_cyg_path:  str = ""
        self.open_xml_btn = self._flat_btn(
            bottom, "  Abrir en Sistema XML", CLR_BTN_BLUE,
            self._open_last_in_sistema_xml,
        )
        self.open_xml_btn.pack(fill="x", pady=(6, 0))
        self.open_xml_btn.pack_forget()

        # ── ZONA MEDIA: lista de clientes scrollable — se expande ────────────
        list_frame = tk.Frame(left, bg=CLR_WHITE, relief="sunken", bd=1)
        list_frame.pack(fill="both", expand=True, pady=(4, 0))

        self.clients_canvas = tk.Canvas(list_frame, bg=CLR_WHITE, highlightthickness=0)
        scroll_y = tk.Scrollbar(list_frame, orient="vertical",
                                command=self.clients_canvas.yview)
        self.clients_canvas.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side="right", fill="y")
        self.clients_canvas.pack(side="left", fill="both", expand=True)

        self.checks_frame = tk.Frame(self.clients_canvas, bg=CLR_WHITE)
        self._canvas_win = self.clients_canvas.create_window(
            (0, 0), window=self.checks_frame, anchor="nw"
        )
        self.checks_frame.bind("<Configure>", self._on_checks_configure)
        self.clients_canvas.bind("<Configure>", self._on_canvas_resize)

        self.client_vars: list[tk.BooleanVar] = []

        # ── Panel derecho: consola ───────────────────────────────────────────
        right = tk.Frame(outer, bg=CLR_BG)
        right.pack(side="right", fill="both", expand=True)

        header_row = tk.Frame(right, bg=CLR_BG)
        header_row.pack(fill="x", pady=(0, 5))
        tk.Label(header_row, text="Registro de actividad",
                 font=("Segoe UI", 11, "bold"),
                 bg=CLR_BG, fg=CLR_HEADER).pack(side="left")
        ttk.Button(header_row, text="Limpiar",
                   command=self._clear_log).pack(side="right")
        ttk.Button(header_row, text="Exportar log",
                   command=self._export_log).pack(side="right", padx=(0, 6))

        console = tk.Frame(right, bg=CLR_CONSOLE, relief="flat")
        console.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            console, bg=CLR_CONSOLE, fg=CLR_LOG_TEXT,
            font=("Consolas", 9), wrap="word",
            state="disabled", relief="flat", padx=10, pady=8,
            insertbackground=CLR_LOG_TEXT,
        )
        log_scroll = ttk.Scrollbar(console, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

    # -----------------------------------------------------------------------
    # TAB: Clientes
    # -----------------------------------------------------------------------

    def _build_clients_tab(self):
        form = tk.LabelFrame(self.tab_clients, text=" Agregar / Editar cliente ",
                             font=("Segoe UI", 10, "bold"),
                             bg=CLR_BG, padx=12, pady=10)
        form.pack(fill="x", padx=12, pady=10)

        # ── Fila 0: Nombre + botón para detectar desde carpeta ──────────────
        tk.Label(form, text="Nombre del cliente:", bg=CLR_BG,
                 font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w", pady=4)

        name_row = tk.Frame(form, bg=CLR_BG)
        name_row.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=4)

        self.client_name_var = tk.StringVar()
        ttk.Entry(name_row, textvariable=self.client_name_var,
                  width=36).pack(side="left", fill="x", expand=True)
        ttk.Button(name_row, text="📁 Desde carpeta",
                   command=self._browse_client_folder).pack(side="left", padx=(6, 0))

        # ── Fila 1: Correo ───────────────────────────────────────────────────
        tk.Label(form, text="Correo electrónico:", bg=CLR_BG,
                 font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=4)
        self.client_email_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.client_email_var,
                  width=42).grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=4)

        # ── Fila 2: Contraseña de aplicación ────────────────────────────────
        tk.Label(form, text="Contraseña de aplicación:", bg=CLR_BG,
                 font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", pady=4)

        pwd_row = tk.Frame(form, bg=CLR_BG)
        pwd_row.grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=4)

        self.client_pwd_var = tk.StringVar()
        self._pwd_entry = ttk.Entry(pwd_row, textvariable=self.client_pwd_var,
                                    width=36, show="*")
        self._pwd_entry.pack(side="left", fill="x", expand=True)

        # Botón mostrar/ocultar contraseña
        self._show_pwd = tk.BooleanVar(value=False)
        tk.Checkbutton(
            pwd_row, text="Ver", variable=self._show_pwd,
            bg=CLR_BG, font=("Segoe UI", 9),
            command=self._toggle_pwd_visibility,
        ).pack(side="left", padx=(6, 0))

        # ── Fila 3: Tipo de declaración ──────────────────────────────────────
        tk.Label(form, text="Tipo de régimen:", bg=CLR_BG,
                 font=("Segoe UI", 10)).grid(row=3, column=0, sticky="w", pady=4)

        tipo_row = tk.Frame(form, bg=CLR_BG)
        tipo_row.grid(row=3, column=1, sticky="w", padx=(12, 0), pady=4)

        self.client_tipo_var = tk.StringVar(value="IVA")
        tk.Radiobutton(tipo_row, text="IVA  (declaración mensual)",
                       variable=self.client_tipo_var, value="IVA",
                       bg=CLR_BG, font=("Segoe UI", 10),
                       fg=CLR_IVA, selectcolor=CLR_BG,
                       activebackground=CLR_BG).pack(side="left")
        tk.Radiobutton(tipo_row, text="REA  (régimen agropecuario)",
                       variable=self.client_tipo_var, value="REA",
                       bg=CLR_BG, font=("Segoe UI", 10),
                       fg=CLR_REA, selectcolor=CLR_BG,
                       activebackground=CLR_BG).pack(side="left", padx=(20, 0))

        # ── Fila 4: Tip ──────────────────────────────────────────────────────
        tip = (
            "Gmail: la contraseña de aplicación se genera en Cuenta de Google "
            "› Seguridad › Contraseñas de aplicación (16 caracteres).\n"
            "Outlook/Hotmail: use la contraseña normal de la cuenta "
            "(habilite IMAP en Configuración › Correo › Sincronizar)."
        )
        tk.Label(form, text=tip, bg=CLR_BG, fg=CLR_MUTED,
                 font=("Segoe UI", 8), wraplength=540,
                 justify="left").grid(row=4, column=0, columnspan=2,
                                      sticky="w", pady=(6, 0))

        # ── Fila 5: Banner modo edición ──────────────────────────────────────
        self.edit_mode_lbl = tk.Label(form, text="", bg="#fef9e7",
                                      fg="#d35400", font=("Segoe UI", 9, "bold"),
                                      relief="flat", padx=8, pady=4)
        self.edit_mode_lbl.grid(row=5, column=0, columnspan=2,
                                sticky="ew", pady=(8, 0))
        self.edit_mode_lbl.grid_remove()

        # ── Fila 6: Botones de acción ────────────────────────────────────────
        btns = tk.Frame(form, bg=CLR_BG)
        btns.grid(row=6, column=0, columnspan=2, sticky="e", pady=(10, 0))

        self.cancel_edit_btn = self._flat_btn(btns, "Cancelar edición",
                                              CLR_MUTED, self._cancel_edit)
        self.cancel_edit_btn.pack(side="right", padx=(6, 0))
        self.cancel_edit_btn.pack_forget()

        self._flat_btn(btns, "Probar conexión", CLR_BTN_BLUE,
                       self._test_connection).pack(side="right", padx=(6, 0))

        self.action_btn = self._flat_btn(btns, "Agregar cliente",
                                         CLR_ACCENT, self._add_client)
        self.action_btn.pack(side="right")

        form.columnconfigure(1, weight=1)

        # ── Tabla de clientes ────────────────────────────────────────────────
        tbl_frm = tk.LabelFrame(self.tab_clients,
                                text=" Clientes registrados ",
                                font=("Segoe UI", 10, "bold"),
                                bg=CLR_BG, padx=12, pady=10)
        tbl_frm.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        cols = ("Nombre", "Correo electrónico", "Régimen", "Estado", "_email")
        self.tree = ttk.Treeview(tbl_frm, columns=cols,
                                 show="headings", height=8,
                                 displaycolumns=("Nombre", "Correo electrónico", "Régimen", "Estado"))
        for col in ("Nombre", "Correo electrónico", "Régimen", "Estado"):
            self.tree.heading(col, text=col)
        self.tree.column("Nombre",              width=220)
        self.tree.column("Correo electrónico",  width=260)
        self.tree.column("Régimen",             width=80, anchor="center")
        self.tree.column("Estado",              width=100, anchor="center")

        # Etiquetas visuales para IVA y REA
        self.tree.tag_configure("iva", foreground=CLR_IVA)
        self.tree.tag_configure("rea", foreground=CLR_REA)

        # Botones ANTES del Treeview para que pack les reserve espacio
        tbl_btn_row = tk.Frame(tbl_frm, bg=CLR_BG)
        tbl_btn_row.pack(side="bottom", anchor="e", pady=(8, 0))
        self._flat_btn(tbl_btn_row, "Editar seleccionado", "#e67e22",
                       self._load_for_edit).pack(side="left", padx=(0, 6))
        self._flat_btn(tbl_btn_row, "Gestionar correos", "#8e44ad",
                       self._open_emails_manager).pack(side="left", padx=(0, 6))
        self._flat_btn(tbl_btn_row, "Probar conexión del seleccionado", CLR_BTN_BLUE,
                       self._test_connection_stored).pack(side="left", padx=(0, 6))
        self._flat_btn(tbl_btn_row, "Eliminar seleccionado", CLR_BTN_RED,
                       self._delete_client).pack(side="left")

        tree_sb = ttk.Scrollbar(tbl_frm, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_sb.set)
        tree_sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

    # -----------------------------------------------------------------------
    # TAB: Auditoría
    # -----------------------------------------------------------------------

    def _build_audit_tab(self):
        outer = ttk.Frame(self.tab_audit)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        # ── Panel izquierdo ──────────────────────────────────────────────────
        left_outer = tk.Frame(outer, bg=CLR_BG, width=340)
        left_outer.pack(side="left", fill="y", padx=(0, 8))
        left_outer.pack_propagate(False)

        # Controles superiores
        top_frm = tk.Frame(left_outer, bg=CLR_BG)
        top_frm.pack(fill="x")

        self._section_label(top_frm, "Período a auditar")

        period_frm = tk.Frame(top_frm, bg=CLR_BG)
        period_frm.pack(fill="x", pady=(4, 6))
        period_frm.columnconfigure(1, weight=1)

        tk.Label(period_frm, text="Año:", bg=CLR_BG,
                 font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w", pady=3)
        yr = datetime.now().year
        self.audit_year_var = tk.StringVar(value=str(yr))
        ttk.Combobox(period_frm, textvariable=self.audit_year_var,
                     values=[str(y) for y in range(yr - 3, yr + 2)],
                     width=18, state="readonly"
                     ).grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=3)

        # Grilla de meses — misma estructura que en Descarga
        audit_months_outer = tk.Frame(top_frm, bg=CLR_BG)
        audit_months_outer.pack(fill="x", pady=(4, 2))

        # Botones de selección rápida
        audit_quick_frm = tk.Frame(audit_months_outer, bg=CLR_BG)
        audit_quick_frm.pack(fill="x", pady=(0, 4))
        for col in range(5):
            audit_quick_frm.columnconfigure(col, weight=1)

        ttk.Button(audit_quick_frm, text="Todos",
                   command=self._select_all_audit_months).grid(
                       row=0, column=0, sticky="ew", padx=(0, 2))
        ttk.Button(audit_quick_frm, text="Limpiar",
                   command=self._clear_audit_months).grid(
                       row=0, column=1, sticky="ew", padx=(0, 2))
        ttk.Button(audit_quick_frm, text="C1",
                   command=lambda: self._select_cuatrimestre(1, self.audit_month_vars)).grid(
                       row=0, column=2, sticky="ew", padx=(0, 2))
        ttk.Button(audit_quick_frm, text="C2",
                   command=lambda: self._select_cuatrimestre(2, self.audit_month_vars)).grid(
                       row=0, column=3, sticky="ew", padx=(0, 2))
        ttk.Button(audit_quick_frm, text="C3",
                   command=lambda: self._select_cuatrimestre(3, self.audit_month_vars)).grid(
                       row=0, column=4, sticky="ew")

        # Checkboxes de meses
        audit_months_grid = tk.Frame(audit_months_outer, bg=CLR_BG)
        audit_months_grid.pack(fill="x")
        for col in range(4):
            audit_months_grid.columnconfigure(col, weight=1)

        self.audit_month_vars: list[tk.BooleanVar] = []
        now_month = datetime.now().month

        for i, mes in enumerate(MONTHS_ES):
            var = tk.BooleanVar(value=(i + 1 == now_month))
            self.audit_month_vars.append(var)
            cb = tk.Checkbutton(
                audit_months_grid, text=mes[:3],
                variable=var,
                bg=CLR_BG, font=("Segoe UI", 9),
            )
            cb.grid(row=i // 4, column=i % 4, sticky="w", padx=2, pady=1)

        # Mantener audit_month_var para compatibilidad con código existente
        self.audit_month_var = tk.StringVar(value=MONTHS_ES[now_month - 1])

        ttk.Separator(top_frm, orient="horizontal").pack(fill="x", pady=(2, 8))

        self._section_label(top_frm, "Cliente a auditar")

        # Barra de búsqueda — igual a Descarga
        audit_search_frm = tk.Frame(top_frm, bg=CLR_BG)
        audit_search_frm.pack(fill="x", pady=(4, 4))
        audit_search_frm.columnconfigure(0, weight=1)

        self.audit_search_var = tk.StringVar()
        self.audit_search_var.trace_add("write", lambda *_: self._refresh_audit_clients())

        self._audit_search_entry = ttk.Entry(
            audit_search_frm, textvariable=self.audit_search_var,
            font=("Segoe UI", 9),
        )
        self._audit_search_entry.grid(row=0, column=0, sticky="ew", ipady=3)
        self._audit_search_entry.insert(0, "Buscar cliente...")
        self._audit_search_entry.configure(foreground="#aaaaaa")

        def _audit_focus_in(_e):
            if self.audit_search_var.get() == "Buscar cliente...":
                self._audit_search_entry.delete(0, "end")
                self._audit_search_entry.configure(foreground="")

        def _audit_focus_out(_e):
            if not self.audit_search_var.get():
                self._audit_search_entry.configure(foreground="#aaaaaa")
                self._audit_search_entry.insert(0, "Buscar cliente...")

        self._audit_search_entry.bind("<FocusIn>",  _audit_focus_in)
        self._audit_search_entry.bind("<FocusOut>", _audit_focus_out)

        tk.Button(
            audit_search_frm, text="×", font=("Segoe UI", 11),
            bg=CLR_BG, fg=CLR_MUTED, relief="flat",
            cursor="hand2", padx=2,
            command=self._clear_audit_search,
        ).grid(row=0, column=1, padx=(2, 0))

        # Lista scrollable de clientes para auditar (radio buttons — solo uno a la vez)
        audit_list_frame = tk.Frame(top_frm, bg=CLR_WHITE, relief="sunken", bd=1)
        audit_list_frame.pack(fill="both", expand=True, pady=(0, 4))

        audit_canvas = tk.Canvas(audit_list_frame, bg=CLR_WHITE,
                                 highlightthickness=0, height=140)
        audit_scroll = tk.Scrollbar(audit_list_frame, orient="vertical",
                                    command=audit_canvas.yview)
        audit_canvas.configure(yscrollcommand=audit_scroll.set)
        audit_scroll.pack(side="right", fill="y")
        audit_canvas.pack(side="left", fill="both", expand=True)

        self.audit_checks_frame = tk.Frame(audit_canvas, bg=CLR_WHITE)
        self._audit_canvas_win = audit_canvas.create_window(
            (0, 0), window=self.audit_checks_frame, anchor="nw"
        )

        def _on_audit_checks_configure(_e):
            audit_canvas.configure(
                scrollregion=audit_canvas.bbox("all")
            )

        def _on_audit_canvas_resize(_e):
            audit_canvas.itemconfig(
                self._audit_canvas_win, width=audit_canvas.winfo_width()
            )

        self.audit_checks_frame.bind("<Configure>", _on_audit_checks_configure)
        audit_canvas.bind("<Configure>", _on_audit_canvas_resize)

        self.audit_client_var = tk.StringVar()
        self._audit_canvas = audit_canvas
        self._refresh_audit_clients()

        # Botones fijos al fondo
        bottom_frm = tk.Frame(left_outer, bg=CLR_BG)
        bottom_frm.pack(fill="x", side="bottom")

        ttk.Separator(bottom_frm, orient="horizontal").pack(fill="x", pady=(0, 8))

        self.export_audit_btn = self._flat_btn(
            bottom_frm, "  Exportar reporte", CLR_MUTED,
            self._export_audit_report,
        )
        self.export_audit_btn.pack(fill="x", ipady=4, pady=(0, 6))
        self.export_audit_btn.configure(state="disabled")

        self.audit_progress_var = tk.DoubleVar()
        ttk.Progressbar(bottom_frm, variable=self.audit_progress_var,
                        maximum=100).pack(fill="x", pady=(0, 2))
        self.audit_progress_lbl = tk.Label(
            bottom_frm, text="", bg=CLR_BG, fg=CLR_MUTED, font=("Segoe UI", 8)
        )
        self.audit_progress_lbl.pack(anchor="w", pady=(0, 6))

        self.audit_btn = self._flat_btn(
            bottom_frm, "  Iniciar Auditoría", CLR_BTN_BLUE,
            self._start_audit,
        )
        self.audit_btn.pack(fill="x", ipady=6)

        # --- Panel derecho: resultados ---
        right = tk.Frame(outer, bg=CLR_BG)
        right.pack(side="right", fill="both", expand=True)

        hdr_row = tk.Frame(right, bg=CLR_BG)
        hdr_row.pack(fill="x", pady=(0, 6))
        tk.Label(hdr_row, text="Resultado de la auditoría",
                 font=("Segoe UI", 11, "bold"),
                 bg=CLR_BG, fg=CLR_HEADER).pack(side="left")

        # Resumen visual (3 contadores)
        summary_row = tk.Frame(right, bg=CLR_BG)
        summary_row.pack(fill="x", pady=(0, 8))

        self._audit_total_lbl  = self._stat_box(summary_row, "En Gmail",  "—", CLR_BTN_BLUE)
        self._audit_ok_lbl     = self._stat_box(summary_row, "En disco",  "—", CLR_ACCENT)
        self._audit_miss_lbl   = self._stat_box(summary_row, "Faltantes", "—", CLR_BTN_RED)

        # Tabla de faltantes
        tk.Label(right, text="Archivos NO encontrados en disco:",
                 font=("Segoe UI", 10, "bold"),
                 bg=CLR_BG, fg=CLR_HEADER).pack(anchor="w", pady=(4, 2))

        tbl_frm = tk.Frame(right, bg=CLR_BG)
        tbl_frm.pack(fill="both", expand=True)

        cols = ("Archivo", "Extensión", "Origen", "Fecha del correo")
        self.audit_tree = ttk.Treeview(tbl_frm, columns=cols,
                                       show="headings", height=12)
        self.audit_tree.heading("Archivo",          text="Archivo")
        self.audit_tree.heading("Extensión",        text="Ext")
        self.audit_tree.heading("Origen",           text="Origen")
        self.audit_tree.heading("Fecha del correo", text="Fecha del correo")
        self.audit_tree.column("Archivo",          width=300)
        self.audit_tree.column("Extensión",        width=50,  anchor="center")
        self.audit_tree.column("Origen",           width=100)
        self.audit_tree.column("Fecha del correo", width=180)

        audit_sb = ttk.Scrollbar(tbl_frm, command=self.audit_tree.yview)
        self.audit_tree.configure(yscrollcommand=audit_sb.set)
        audit_sb.pack(side="right", fill="y")
        self.audit_tree.pack(fill="both", expand=True)

        self._last_audit_report: AuditReport | None = None

    def _stat_box(self, parent, label: str, value: str, color: str) -> tk.Label:
        """Crea una caja de estadística con etiqueta arriba y número grande abajo."""
        box = tk.Frame(parent, bg=color, padx=16, pady=8)
        box.pack(side="left", expand=True, fill="x", padx=(0, 6))
        tk.Label(box, text=label,  bg=color, fg=CLR_WHITE,
                 font=("Segoe UI", 8)).pack()
        lbl = tk.Label(box, text=value, bg=color, fg=CLR_WHITE,
                       font=("Segoe UI", 18, "bold"))
        lbl.pack()
        return lbl

    def _clear_audit_search(self):
        """Limpia la barra de búsqueda de auditoría."""
        self.audit_search_var.set("")
        self._audit_search_entry.delete(0, "end")
        self._audit_search_entry.insert(0, "Buscar cliente...")
        self._audit_search_entry.configure(foreground="#aaaaaa")
        self._refresh_audit_clients()

    def _refresh_audit_clients(self):
        if not hasattr(self, "audit_checks_frame"):
            return
        # Limpiar lista anterior
        for w in self.audit_checks_frame.winfo_children():
            w.destroy()

        # Construir lista de nombres únicos ordenados
        seen: set = set()
        names: list[str] = []
        for c in self.config.get_clients():
            nombre_base = c["name"].split(" (")[0]
            cedula      = c.get("_cedula", "")
            key         = cedula if cedula else nombre_base
            if key not in seen:
                seen.add(key)
                names.append(nombre_base)
        names.sort()

        # Aplicar filtro
        raw = self.audit_search_var.get().strip()
        filtro = raw.lower() if raw and raw != "Buscar cliente..." else ""
        if filtro:
            names = [n for n in names if filtro in n.lower()]

        # Preservar selección actual si sigue visible
        current = self.audit_client_var.get()
        if names and current not in names:
            self.audit_client_var.set(names[0])
        elif not names:
            self.audit_client_var.set("")

        # Dibujar radio buttons
        for name in names:
            rb = tk.Radiobutton(
                self.audit_checks_frame,
                text=name,
                variable=self.audit_client_var,
                value=name,
                bg=CLR_WHITE,
                font=("Segoe UI", 9),
                anchor="w",
                activebackground=CLR_WHITE,
            )
            rb.pack(fill="x", padx=6, pady=1)

    # -----------------------------------------------------------------------
    # Lógica de auditoría
    # -----------------------------------------------------------------------

    def _start_audit(self):
        name = self.audit_client_var.get().strip()
        if not name:
            messagebox.showwarning("Sin cliente",
                                   "Seleccione un cliente para auditar.")
            return

        client = next((c for c in self.config.get_clients()
                       if c["name"].split(" (")[0] == name
                       or c["name"] == name), None)
        if not client:
            messagebox.showerror("Cliente no encontrado",
                                 f'No se encontró el cliente "{name}".')
            return

        months = self._get_selected_audit_months()
        if not months:
            messagebox.showwarning("Sin período",
                                   "Seleccione al menos un mes para auditar.")
            return

        year = int(self.audit_year_var.get())

        # Limpiar tabla y contadores
        for row in self.audit_tree.get_children():
            self.audit_tree.delete(row)
        self._audit_total_lbl.configure(text="…")
        self._audit_ok_lbl.configure(text="…")
        self._audit_miss_lbl.configure(text="…")
        self.audit_progress_var.set(0)
        self.audit_progress_lbl.configure(text="Conectando…")
        self.audit_btn.configure(state="disabled")
        self.export_audit_btn.configure(state="disabled")
        self._last_audit_report = None

        threading.Thread(
            target=self._audit_worker,
            args=(client, year, months),
            daemon=True,
        ).start()

    def _audit_worker(self, client: dict, year: int, months: list[int]):
        """
        Corre la auditoría para cada mes seleccionado y consolida los resultados
        en un único AuditReport que se muestra en la UI.
        """
        try:
            base_path = self.config.get_base_path_for_client(client)
            pwd       = self.config.decode_password(client["password"], client["email"])
            n_meses   = len(months)

            def log(msg: str):
                self.root.after(
                    0,
                    lambda m=msg: self.audit_progress_lbl.configure(
                        text=m.strip().lstrip("✓⚠❌ℹ│└┌").strip()[:60]
                    ),
                )

            # Correr auditoría por cada mes y acumular en el primer report
            combined: AuditReport | None = None

            for idx, month in enumerate(months):
                mes_label = MONTHS_ES[month - 1]
                log(f"Auditando {mes_label} {year}…")

                auditor = Auditor(client["email"], pwd, log=log)
                report  = auditor.run(
                    client_name=client["name"].split(" (")[0],
                    year=year,
                    month=month,
                    base_path=base_path,
                )

                pct = 20 + (70 * (idx + 1) / n_meses)
                self.root.after(0, lambda p=pct: self.audit_progress_var.set(p))

                if combined is None:
                    combined = report
                    # Ajustar label de período para mostrar rango
                    combined.month = months[0]
                else:
                    # Acumular contadores y entradas
                    combined.total_in_gmail += report.total_in_gmail
                    combined.total_on_disk  += report.total_on_disk
                    combined.ok_count       += report.ok_count
                    combined.missing_count  += report.missing_count
                    combined.entries        += report.entries
                    combined.errors         += report.errors

            # Ajustar label de período en el report
            if combined and n_meses > 1:
                meses_label = ", ".join(MONTHS_ES[m - 1] for m in months)
                combined._period_label = meses_label
            elif combined:
                combined._period_label = MONTHS_ES[months[0] - 1]

            self.root.after(0, lambda: self.audit_progress_var.set(90))
            self.root.after(0, lambda r=combined: self._show_audit_results(r))

        except Exception as exc:
            self.root.after(
                0,
                lambda e=str(exc): messagebox.showerror(
                    "Error de auditoría", f"Ocurrió un error:\n{e}"
                ),
            )
        finally:
            self.root.after(0, lambda: self.audit_btn.configure(state="normal"))

    def _show_audit_results(self, report: AuditReport):
        self._last_audit_report = report
        self.audit_progress_var.set(100)

        self._audit_total_lbl.configure(text=str(report.total_in_gmail))
        self._audit_ok_lbl.configure(text=str(report.ok_count))
        self._audit_miss_lbl.configure(text=str(report.missing_count))

        status = "Listo — sin faltantes ✅" if report.missing_count == 0 \
            else f"Listo — {report.missing_count} faltante(s) ⚠"
        self.audit_progress_lbl.configure(text=status)

        for entry in report.missing:
            self.audit_tree.insert("", "end", values=(
                entry.filename_sanitized,
                entry.extension,
                entry.source,
                entry.message_date,
            ))

        if report.missing_count > 0:
            messagebox.showwarning(
                "Auditoría completada",
                f"Se encontraron {report.missing_count} archivo(s) en Gmail "
                f"que NO están en disco.\n\nRevise la tabla para ver el detalle.",
            )
        else:
            messagebox.showinfo(
                "Auditoría completada",
                f"✅ Todo en orden.\n\n"
                f"{report.total_in_gmail} adjuntos auditados — todos presentes en disco.",
            )

        self.export_audit_btn.configure(state="normal")

    def _export_audit_report(self):
        if not self._last_audit_report:
            return

        r = self._last_audit_report
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"auditoria_{r.client_name.replace(' ', '_')}_{r.year}{r.month:02d}_{ts}.txt"

        filepath = filedialog.asksaveasfilename(
            title="Guardar reporte de auditoría",
            initialfile=default_name,
            defaultextension=".txt",
            filetypes=[("Archivo de texto", "*.txt"), ("Todos los archivos", "*.*")],
        )
        if not filepath:
            return

        try:
            Path(filepath).write_text(r.to_text(), encoding="utf-8")
            messagebox.showinfo("Reporte exportado",
                                f"Reporte guardado en:\n{filepath}")
        except OSError as exc:
            messagebox.showerror("Error al guardar", str(exc))

    # -----------------------------------------------------------------------
    # TAB: Configuración
    # -----------------------------------------------------------------------

    def _build_config_tab(self):
        frm = ttk.Frame(self.tab_cfg)
        frm.pack(fill="both", expand=True, padx=20, pady=20)

        tk.Label(frm, text="Configuración general",
                 font=("Segoe UI", 14, "bold"),
                 bg=CLR_BG, fg=CLR_HEADER).pack(anchor="w", pady=(0, 16))

        # ── Ruta IVA ──────────────────────────────────────────────────────────
        iva_box = tk.LabelFrame(
            frm, text=" Carpeta base — Clientes IVA (declaración mensual) ",
            font=("Segoe UI", 10, "bold"), fg=CLR_IVA,
            bg=CLR_BG, padx=12, pady=10,
        )
        iva_box.pack(fill="x", pady=(0, 10))

        self.base_path_iva_var = tk.StringVar(value=self.config.get_base_path_iva())
        iva_row = tk.Frame(iva_box, bg=CLR_BG)
        iva_row.pack(fill="x")
        ttk.Entry(iva_row, textvariable=self.base_path_iva_var,
                  font=("Segoe UI", 10)).pack(side="left", fill="x",
                                               expand=True, padx=(0, 10))
        ttk.Button(iva_row, text="Explorar...",
                   command=lambda: self._browse_base_path(
                       self.base_path_iva_var)).pack(side="right")

        tk.Label(iva_box,
                 text="Ejemplo: C:\\Users\\Usuario\\OneDrive\\OFICINA\\CONTAS\\IVA",
                 bg=CLR_BG, fg=CLR_MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))

        # ── Ruta REA ──────────────────────────────────────────────────────────
        rea_box = tk.LabelFrame(
            frm, text=" Carpeta base — Clientes REA (Régimen Especial Agropecuario) ",
            font=("Segoe UI", 10, "bold"), fg=CLR_REA,
            bg=CLR_BG, padx=12, pady=10,
        )
        rea_box.pack(fill="x", pady=(0, 14))

        self.base_path_rea_var = tk.StringVar(value=self.config.get_base_path_rea())
        rea_row = tk.Frame(rea_box, bg=CLR_BG)
        rea_row.pack(fill="x")
        ttk.Entry(rea_row, textvariable=self.base_path_rea_var,
                  font=("Segoe UI", 10)).pack(side="left", fill="x",
                                               expand=True, padx=(0, 10))
        ttk.Button(rea_row, text="Explorar...",
                   command=lambda: self._browse_base_path(
                       self.base_path_rea_var)).pack(side="right")

        tk.Label(rea_box,
                 text="Ejemplo: C:\\Users\\Usuario\\OneDrive\\OFICINA\\CONTAS\\REA",
                 bg=CLR_BG, fg=CLR_MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))

        # ── Vista previa ──────────────────────────────────────────────────────
        prev_box = tk.LabelFrame(frm, text=" Estructura de carpetas generada ",
                                 font=("Segoe UI", 10, "bold"),
                                 bg=CLR_BG, padx=12, pady=10)
        prev_box.pack(fill="x", pady=(0, 14))

        preview = (
            "[Carpeta base IVA o REA]\n"
            "  Apellido Apellido Nombre\n"
            "    2026\n"
            "      3-Mar\n"
            "        Facturas\n"
            "        Notas de Crédito\n"
            "        Notas de Débito\n"
            "        Tiquetes\n"
            "        Facturas de Compra\n"
            "        CyG  (comprobantes sin clasificar)\n"
            "        PDFs\n"
        )
        tk.Label(prev_box, text=preview, bg=CLR_BG, fg="#2c3e50",
                 font=("Consolas", 9), justify="left").pack(anchor="w")

        self._flat_btn(frm, "  Guardar configuración", CLR_BTN_BLUE,
                       self._save_config).pack(fill="x", ipady=6, pady=(10, 0))

    # -----------------------------------------------------------------------
    # Lógica de descarga
    # -----------------------------------------------------------------------

    def _toggle_download(self):
        """Alterna entre iniciar y cancelar la descarga activa."""
        if self._downloading:
            self._cancel_event.set()
            self._log("\n  ⛔ Cancelación solicitada — esperando que el cliente actual termine...")
            self.dl_btn.configure(state="disabled", text="  Cancelando...", bg="#95a5a6")
        else:
            self._start_download()

    def _start_download(self):
        if self._downloading:
            return

        # Reconstruir los mismos grupos que _refresh_clients para emparejar vars
        all_clients = self.config.get_clients()
        grupos_all: list[list[dict]] = []
        visto: dict[str, int] = {}
        for c in all_clients:
            cedula = c.get("_cedula", "").strip()
            if cedula and cedula in visto:
                grupos_all[visto[cedula]].append(c)
            else:
                grupos_all.append([c])
                if cedula:
                    visto[cedula] = len(grupos_all) - 1

        # Mismo orden que _refresh_clients: alfabético, luego filtrado
        grupos_all.sort(key=lambda g: g[0]["name"].split(" (")[0].lower())
        raw_search = self.search_var.get().strip()
        filtro = raw_search.lower() \
            if raw_search and raw_search != "Buscar cliente..." else ""
        if filtro:
            grupos_all = [
                g for g in grupos_all
                if filtro in g[0]["name"].split(" (")[0].lower()
                or any(filtro in c["email"].lower() for c in g)
            ]

        # selected = lista plana de clientes cuyo grupo está marcado
        selected = []
        for grupo, var in zip(grupos_all, self.client_vars):
            if var.get():
                selected.extend(grupo)

        if not selected:
            messagebox.showwarning("Sin clientes",
                                   "Seleccione al menos un cliente.")
            return

        # Verificar que las rutas base necesarias están configuradas
        needs_iva = any(c.get("tipo", "IVA") == "IVA" for c in selected)
        needs_rea = any(c.get("tipo", "IVA") == "REA" for c in selected)

        missing = []
        if needs_iva and not self.config.get_base_path_iva():
            missing.append("IVA")
        if needs_rea and not self.config.get_base_path_rea():
            missing.append("REA")

        if missing:
            messagebox.showerror(
                "Ruta no configurada",
                f"Falta configurar la carpeta base para: {', '.join(missing)}.\n\n"
                "Vaya a la pestaña Configuración y defina la ruta antes de continuar."
            )
            self.nb.select(self.tab_cfg)
            return

        months = self._get_selected_months()
        if not months:
            messagebox.showwarning("Sin período",
                                   "Seleccione al menos un mes para descargar.")
            return

        year = int(self.year_var.get())

        self._downloading = True
        self._cancel_event.clear()
        self.dl_btn.configure(
            text="  ⛔ Cancelar descarga", bg=CLR_BTN_RED,
            activebackground="#c0392b",
        )
        self.progress_var.set(0)

        threading.Thread(
            target=self._download_worker,
            args=(selected, year, months),
            daemon=True,
        ).start()

    def _download_worker(self, clients: list, year: int, months: list[int]):
        """
        Descarga en paralelo usando ThreadPoolExecutor.
        Itera sobre cada mes seleccionado en orden cronológico.
        Dentro de cada mes, los clientes se procesan en paralelo (MAX_WORKERS).
        """
        MAX_WORKERS = 4

        # Agrupar por cédula (misma cédula = mismo cliente con varios correos)
        # Correos compartidos entre clientes con distinta cédula se detectan
        # aparte — una sola conexión IMAP descarga para ambos.
        grupos: list[list[dict]] = []
        visto_cedulas: dict[str, int] = {}
        for c in clients:
            cedula = c.get("_cedula", "").strip()
            if cedula and cedula in visto_cedulas:
                grupos[visto_cedulas[cedula]].append(c)
            else:
                grupos.append([c])
                if cedula:
                    visto_cedulas[cedula] = len(grupos) - 1

        # Detectar correos compartidos entre grupos distintos
        # email → lista de índices de grupos que lo usan
        email_to_grupos: dict[str, list[int]] = {}
        for gi, grupo in enumerate(grupos):
            for c in grupo:
                email_to_grupos.setdefault(c["email"], []).append(gi)

        # Correos que aparecen en más de un grupo
        correos_compartidos: dict[str, list[int]] = {
            email: idxs for email, idxs in email_to_grupos.items()
            if len(idxs) > 1
        }
        if correos_compartidos:
            emails_str = ", ".join(correos_compartidos.keys())
            self._log(f"  ℹ Correos compartidos detectados: {emails_str}")
            self._log("  ℹ Una conexión por correo — archivos distribuidos por cédula")

        n_grupos    = len(grupos)
        n_meses     = len(months)
        total_ops   = n_grupos * n_meses
        completed   = 0
        grand_total = 0
        cancelled   = False

        meses_label = ", ".join(MONTHS_ES[m - 1] for m in months)
        self._log("=" * 55)
        self._log(f"  Descarga: {meses_label} {year}")
        self._log(f"  Clientes: {n_grupos}  |  Meses: {n_meses}  |  "
                  f"Conexiones: {min(MAX_WORKERS, len(clients))}")
        self._log("=" * 55)

        def process_single(client: dict, month: int,
                           extra_clients: list[dict] = None) -> dict:
            """
            Descarga un mes desde una cuenta Gmail.
            Si extra_clients no es None, el mismo correo pertenece a varios
            clientes distintos — se conecta una vez y descarga para todos.
            """
            name = client["name"].split(" (")[0]
            # Usar la carpeta ya registrada si existe, para evitar duplicados
            # cuando el nombre del cliente varía levemente entre descargas.
            if _BRIDGE_AVAILABLE:
                name = get_resolved_folder_name(client["email"], name)
            tipo = client.get("tipo", "IVA")

            if self._cancel_event.is_set():
                return {"name": name, "email": client["email"],
                        "month": month, "cancelled": True}

            resultado = {
                "name": name, "email": client["email"],
                "month": month, "tipo": tipo,
                "cancelled": False, "error": None,
                "stats": None, "cyg_path": "", "year_path": "",
            }

            try:
                base_path  = self.config.get_base_path_for_client(client)
                folder_mgr = FolderManager(base_path)
                pwd        = self.config.decode_password(
                    client["password"], client["email"]
                )
                dl = IMAPDownloader(client["email"], pwd, log=self._log)

                # Construir lista de todos los destinos para este correo
                destinos = [(name, base_path, folder_mgr)]
                if extra_clients:
                    for ec in extra_clients:
                        ec_name      = ec["name"].split(" (")[0]
                        if _BRIDGE_AVAILABLE:
                            ec_name = get_resolved_folder_name(ec["email"], ec_name)
                        ec_base_path = self.config.get_base_path_for_client(ec)
                        ec_folder    = FolderManager(ec_base_path)
                        destinos.append((ec_name, ec_base_path, ec_folder))

                with self._log_lock:
                    if extra_clients:
                        destinos_str = ", ".join(d[0] for d in destinos)
                        self._log(f"  │  Correo: {client['email']}  "
                                  f"[compartido → {destinos_str}]")
                    else:
                        self._log(f"  │  Correo: {client['email']}  "
                                  f"[{MONTHS_ES[month-1]}]")

                dl.connect()

                # Descargar para el primer destino
                stats = dl.download_month(
                    year, month, folder_mgr, self.classifier, name
                )

                # Descargar para destinos adicionales (correo compartido)
                for ec_name, ec_base, ec_folder in destinos[1:]:
                    extra_stats = dl.download_month(
                        year, month, ec_folder, self.classifier, ec_name
                    )
                    # Sumar stats del destino extra
                    stats["total"]   += extra_stats["total"]
                    stats["skipped"] += extra_stats.get("skipped", 0)
                    stats["errors"]  += extra_stats["errors"]
                    for k, v in extra_stats["by_type"].items():
                        stats["by_type"][k] = stats["by_type"].get(k, 0) + v

                dl.disconnect()
                resultado["stats"] = stats

                if _BRIDGE_AVAILABLE:
                    try:
                        carpeta_base = str(Path(base_path) / name)
                        self.config.sync_folder_to_registro(
                            client["email"], carpeta_base
                        )
                    except Exception:
                        pass

                resultado["cyg_path"]  = str(
                    folder_mgr.get_subfolder_path(name, year, month, "CyG")
                )
                resultado["year_path"] = str(Path(base_path) / name / str(year))

            except Exception as exc:
                resultado["error"] = str(exc)
                with self._log_lock:
                    self._log(f"  │  ❌ ERROR ({client['email']} / "
                              f"{MONTHS_ES[month-1]}): {exc}")

            return resultado

        def process_group_month(grupo: list[dict], month: int) -> dict:
            """Procesa un cliente (con uno o más correos) para un mes dado."""
            name      = grupo[0]["name"].split(" (")[0]
            tipo      = grupo[0].get("tipo", "IVA")
            base_path = self.config.get_base_path_for_client(grupo[0])

            if self._cancel_event.is_set():
                return {"name": name, "month": month, "cancelled": True}

            with self._log_lock:
                n_c    = len(grupo)
                sufijo = f"  [{n_c} correos]" if n_c > 1 else f"  [{tipo}]"
                self._log(f"\n  ┌─ {name}{sufijo}  — {MONTHS_ES[month-1]} {year}")
                self._log(f"  │  Ruta: {base_path}")

            # Descargar cada correo (paralelo si hay varios)
            # Para correos compartidos con otros grupos, pasar los clientes extra
            resultados_correos = []
            if len(grupo) == 1:
                c = grupo[0]
                # Detectar si este correo está compartido con otros grupos
                otros_gi = [gi for gi in correos_compartidos.get(c["email"], [])
                            if grupos[gi] is not grupo]
                extra = [ec for gi in otros_gi for ec in grupos[gi]
                         if ec["email"] == c["email"]] if otros_gi else None
                resultados_correos.append(process_single(c, month, extra))
            else:
                with ThreadPoolExecutor(
                    max_workers=min(2, len(grupo))
                ) as sub_pool:
                    futures_list = []
                    for c in grupo:
                        otros_gi = [gi for gi in correos_compartidos.get(c["email"], [])
                                    if grupos[gi] is not grupo]
                        extra = [ec for gi in otros_gi for ec in grupos[gi]
                                 if ec["email"] == c["email"]] if otros_gi else None
                        futures_list.append(
                            sub_pool.submit(process_single, c, month, extra)
                        )
                    for f in as_completed(futures_list):
                        resultados_correos.append(f.result())

            # Consolidar
            stats_total = {"total": 0, "skipped": 0, "by_type": {}, "errors": 0}
            cyg_path = year_path = ""
            for r in resultados_correos:
                if r.get("stats"):
                    s = r["stats"]
                    stats_total["total"]   += s["total"]
                    stats_total["skipped"] += s.get("skipped", 0)
                    stats_total["errors"]  += s["errors"]
                    for k, v in s["by_type"].items():
                        stats_total["by_type"][k] = \
                            stats_total["by_type"].get(k, 0) + v
                if r.get("cyg_path"):  cyg_path  = r["cyg_path"]
                if r.get("year_path"): year_path = r["year_path"]

            if stats_total["total"] > 0 or stats_total["skipped"] > 0:
                self.config.add_history_entry(
                    name, year, month,
                    stats_total["total"], stats_total["skipped"]
                )

            with self._log_lock:
                self._log(
                    f"  │  Descargados: {stats_total['total']}  |  "
                    f"Omitidos: {stats_total['skipped']}  |  "
                    f"Errores: {stats_total['errors']}"
                )
                for doc_type, count in stats_total["by_type"].items():
                    self._log(f"  │    · {doc_type}: {count}")
                self._log(f"  └─ ✓ {name} / {MONTHS_ES[month-1]} completado")

                if _BRIDGE_AVAILABLE and cyg_path and stats_total["total"] > 0:
                    periodo  = f"{year}{month:02d}"
                    t_result = trigger_procesamiento(cyg_path, periodo)
                    if t_result.get("triggered"):
                        self._log("  ★ Sistema XML: procesamiento iniciado")
                    elif not t_result.get("ok"):
                        self._log(f"  ⚠ Sistema XML: {t_result['message']}")

            return {
                "name":      name,
                "month":     month,
                "cancelled": any(r.get("cancelled") for r in resultados_correos),
                "error":     next((r["error"] for r in resultados_correos
                                   if r.get("error")), None),
                "stats":     stats_total,
                "cyg_path":  cyg_path,
                "year_path": year_path,
            }

        try:
            # Iterar meses en orden; dentro de cada mes los grupos van en paralelo
            for month in months:
                if self._cancel_event.is_set():
                    break

                self._log(f"\n  {'─'*20} {MONTHS_ES[month-1]} {year} {'─'*20}")

                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    futures = {
                        executor.submit(process_group_month, g, month): g
                        for g in grupos
                    }
                    for future in as_completed(futures):
                        resultado = future.result()
                        completed += 1

                        if resultado.get("stats"):
                            grand_total += resultado["stats"]["total"]
                        if resultado.get("cyg_path"):
                            self._last_cyg_path  = resultado["cyg_path"]

                        if resultado.get("cancelled"):
                            cancelled = True
                            self._log(
                                f"  ⛔ {resultado['name']} / "
                                f"{MONTHS_ES[resultado['month']-1]} — cancelado"
                            )

                        pct = (completed / total_ops) * 100
                        self.root.after(0, lambda p=pct: self.progress_var.set(p))
                        self.root.after(
                            0,
                            lambda done=completed, t=total_ops:
                            self.progress_lbl.configure(
                                text=f"{done}/{t} operaciones procesadas"
                            ),
                        )

        except Exception as exc:
            self._log(f"\nError crítico: {exc}")

        finally:
            self._downloading = False
            self._cancel_event.clear()
            self.root.after(0, self._reset_download_btn)

        # Resumen final
        self._log(f"\n{'=' * 55}")
        if cancelled:
            self._log(f"  ⛔ Descarga cancelada. Archivos descargados hasta el momento: {grand_total}")
        else:
            self._log(f"  ✅ Descarga completada. Total de archivos: {grand_total}")
        self._log("=" * 55)

    def _reset_download_btn(self):
        self.dl_btn.configure(
            state="normal", text="  Iniciar Descarga",
            bg=CLR_ACCENT, activebackground=CLR_ACCENT_HV,
        )
        lbl = "Cancelado" if self._cancel_event.is_set() else "Listo"
        self.progress_lbl.configure(text=lbl)
        self._refresh_clients()
        self._update_bridge_indicator()

        # Mostrar botón "Abrir en Sistema XML" si el bridge está disponible
        # y hubo al menos una descarga exitosa en esta sesión
        if _BRIDGE_AVAILABLE and self._last_cyg_path:
            self.open_xml_btn.pack(fill="x", pady=(8, 0))
        else:
            self.open_xml_btn.pack_forget()

    # -----------------------------------------------------------------------
    # Lógica de clientes
    # -----------------------------------------------------------------------

    def _refresh_clients(self):
        if not hasattr(self, "checks_frame"):
            return
        for w in self.checks_frame.winfo_children():
            w.destroy()
        self.client_vars.clear()

        for row in self.tree.get_children():
            self.tree.delete(row)

        # Leer período actual para mostrar badge de historial
        try:
            selected_months = self._get_selected_months()
            year            = int(self.year_var.get())
        except (ValueError, AttributeError):
            selected_months = [datetime.now().month]
            year            = datetime.now().year

        # Texto de búsqueda activo (ignorar el placeholder)
        raw_search = self.search_var.get().strip()
        filtro = raw_search.lower() \
            if raw_search and raw_search != "Buscar cliente..." else ""

        # Agrupar clientes por cédula para mostrar una sola fila por cliente real
        grupos_display: list[list[dict]] = []
        visto_cedulas: dict[str, int]    = {}

        for client in self.config.get_clients():
            cedula = client.get("_cedula", "").strip()
            if cedula and cedula in visto_cedulas:
                grupos_display[visto_cedulas[cedula]].append(client)
            else:
                grupos_display.append([client])
                if cedula:
                    visto_cedulas[cedula] = len(grupos_display) - 1

        # Ordenar grupos alfabéticamente por nombre base
        grupos_display.sort(key=lambda g: g[0]["name"].split(" (")[0].lower())

        # Aplicar filtro de búsqueda
        if filtro:
            grupos_display = [
                g for g in grupos_display
                if filtro in g[0]["name"].split(" (")[0].lower()
                or any(filtro in c["email"].lower() for c in g)
            ]

        for grupo in grupos_display:
            # Nombre base (sin sufijo de correo)
            nombre_base = grupo[0]["name"].split(" (")[0]
            tipo        = grupo[0].get("tipo", "IVA")
            tag         = "iva" if tipo == "IVA" else "rea"

            var = tk.BooleanVar(value=True)
            # Un solo BooleanVar por grupo
            self.client_vars.append(var)

            # Fila contenedora
            row_frm = tk.Frame(self.checks_frame, bg=CLR_WHITE)
            row_frm.pack(fill="x", padx=4, pady=1)

            # Columna izquierda: checkbox + nombre + email(s)
            left_col = tk.Frame(row_frm, bg=CLR_WHITE)
            left_col.pack(side="left", fill="x", expand=True)

            tk.Checkbutton(
                left_col, text=nombre_base,
                variable=var, bg=CLR_WHITE,
                font=("Segoe UI", 9), anchor="w",
            ).pack(anchor="w")

            # Correos en línea pequeña debajo del nombre — truncados si son largos
            if len(grupo) == 1:
                correo_txt = grupo[0]["email"]
                # Truncar si es muy largo para el panel
                if len(correo_txt) > 38:
                    correo_txt = correo_txt[:35] + "…"
            else:
                # Múltiples correos: mostrar cantidad y primer correo abreviado
                primer = grupo[0]["email"]
                if len(primer) > 28:
                    primer = primer[:25] + "…"
                correo_txt = f"{primer}  +{len(grupo) - 1} más"

            tk.Label(
                left_col, text=correo_txt,
                bg=CLR_WHITE, fg="#95a5a6",
                font=("Segoe UI", 7),
                anchor="w",
            ).pack(anchor="w", padx=(20, 0))

            # Badge de historial — suma de archivos en todos los meses seleccionados
            total_hist = 0
            for m in selected_months:
                h = self.config.get_history_for(nombre_base, year, m)
                if h:
                    total_hist += h["files"]
            if total_hist > 0:
                tk.Label(
                    row_frm,
                    text=f"✓ {total_hist}",
                    bg="#27ae60", fg=CLR_WHITE,
                    font=("Segoe UI", 7, "bold"),
                    padx=4, pady=1,
                ).pack(side="right", padx=(0, 2))

            badge_color = CLR_IVA if tipo == "IVA" else CLR_REA
            tk.Label(row_frm, text=tipo,
                     bg=badge_color, fg=CLR_WHITE,
                     font=("Segoe UI", 7, "bold"),
                     padx=4, pady=1).pack(side="right", padx=(0, 4))

            # Insertar en la tabla de Clientes (primera entrada del grupo)
            primer = grupo[0]
            email_display = primer["email"] if len(grupo) == 1 \
                else f"{primer['email']} (+{len(grupo)-1})"
            self.tree.insert("", "end",
                             values=(nombre_base, email_display,
                                     tipo, "Configurado", primer["email"]),
                             tags=(tag,))

        # Mantener sincronizado el combobox de auditoría
        self._refresh_audit_clients()

    def _collect_form_fields(self) -> tuple[str, str, str, str] | None:
        """
        Lee, limpia y valida los campos del formulario.
        Retorna (name, email, pwd, tipo) o None si hay error.
        Si pwd == _PWD_UNCHANGED (modo edición sin cambio de contraseña),
        se retorna el sentinel para que _save_edit lo maneje.
        """
        name  = self.client_name_var.get().strip()
        email = self.client_email_var.get().strip().lower()
        pwd   = self.client_pwd_var.get()
        tipo  = self.client_tipo_var.get()

        if not name:
            messagebox.showerror("Campo requerido", "El nombre del cliente no puede estar vacío.")
            return None

        if not email:
            messagebox.showerror("Campo requerido", "El correo electrónico no puede estar vacío.")
            return None

        if not _RE_EMAIL.match(email):
            messagebox.showerror("Correo inválido",
                                 f'"{email}" no parece un correo electrónico válido.')
            return None

        # Si el campo muestra el placeholder, no validar ni tocar la contraseña
        if pwd != _PWD_UNCHANGED:
            pwd = _normalize_app_password(pwd)
            ok, msg = _validate_app_password(pwd, email)
            if not ok:
                messagebox.showerror("Contraseña inválida", msg)
                return None

        return name, email, pwd, tipo

    def _add_client(self):
        if self._edit_email:
            self._save_edit()
            return

        result = self._collect_form_fields()
        if result is None:
            return
        name, email, pwd, tipo = result

        if self.config.email_exists(email):
            # Correo ya registrado — preguntar si es intencional (correo compartido)
            otros = [c["name"].split(" (")[0]
                     for c in self.config.get_clients()
                     if c["email"] == email and c["name"].split(" (")[0] != name]
            if otros:
                otros_str = ", ".join(set(otros))
                if not messagebox.askyesno(
                    "Correo compartido",
                    f"El correo {email} ya está registrado para:\n  {otros_str}\n\n"
                    f"¿Desea asociarlo también a {name}?\n\n"
                    "El sistema descargará una sola vez y distribuirá\n"
                    "los archivos según la cédula de cada documento.",
                ):
                    return

        if self.config.name_exists(name):
            if not messagebox.askyesno(
                "Nombre duplicado",
                f'Ya existe un cliente llamado "{name}".\n'
                "¿Desea agregar otro con el mismo nombre?"
            ):
                return

        # Advertir si la ruta base del tipo elegido no está configurada
        path = (self.config.get_base_path_iva() if tipo == "IVA"
                else self.config.get_base_path_rea())
        if not path:
            messagebox.showwarning(
                "Ruta sin configurar",
                f"El cliente se agregará como {tipo}, pero la carpeta base de {tipo} "
                f"aún no está configurada.\n\n"
                f"Recuerde configurarla en la pestaña Configuración antes de descargar."
            )

        self.config.add_client(name, email, pwd, tipo)
        self._refresh_clients()
        self._clear_form()
        messagebox.showinfo("Cliente agregado",
                            f'"{name}" fue agregado correctamente como cliente {tipo}.')

    def _selected_email(self) -> str:
        """Retorna el email (columna oculta) del item seleccionado en el Treeview."""
        sel = self.tree.selection()
        if not sel:
            return ""
        values = self.tree.item(sel[0])["values"]
        # La columna _email es la índice 4 (quinta columna, oculta)
        return str(values[4]) if len(values) > 4 else ""

    def _load_for_edit(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Sin selección",
                                   "Seleccione un cliente de la tabla para editar.")
            return
        email_sel = self._selected_email()
        if not email_sel:
            return
        clients = self.config.get_clients()
        for c in clients:
            if c["email"] == email_sel:
                # Guardar email en lugar de índice — estable entre ambas fuentes de datos
                self._edit_email = email_sel
                self._edit_index  = None   # ya no se usa

                self.client_name_var.set(c["name"].split(" (")[0])
                self.client_email_var.set(c["email"])
                self.client_pwd_var.set(_PWD_UNCHANGED)
                self.client_tipo_var.set(c.get("tipo", "IVA"))

                self.action_btn.configure(text="Guardar cambios",
                                          bg=CLR_BTN_BLUE)
                self.cancel_edit_btn.pack(side="right", padx=(6, 0))
                self.edit_mode_lbl.configure(
                    text=f"  Editando: {c['name'].split(' (')[0]}  —  modifique los campos "
                         f"y presione Guardar cambios"
                )
                self.edit_mode_lbl.grid()
                self.nb.select(self.tab_clients)
                return

    def _save_edit(self):
        result = self._collect_form_fields()
        if result is None:
            return
        name, email, pwd, tipo = result

        # Buscar el cliente original por email (no por índice)
        email_original = getattr(self, "_edit_email", None)
        if not email_original:
            messagebox.showerror("Error", "No hay cliente en edición.")
            return

        clients = self.config.get_clients()
        original = next((c for c in clients if c["email"] == email_original), None)
        if not original:
            messagebox.showerror("Error",
                                 f"No se encontró el cliente con correo {email_original}.")
            return

        if email != email_original and self.config.email_exists(email):
            messagebox.showerror("Duplicado",
                                 f"El correo {email} ya está registrado en otro cliente.")
            return

        if pwd == _PWD_UNCHANGED:
            pwd = self.config.decode_password(original["password"], email_original)

        # Buscar índice en el config LOCAL (no en get_clients) para update_client
        local_clients = self.config.config.get("clients", [])
        local_idx = next(
            (i for i, c in enumerate(local_clients) if c["email"] == email_original),
            None
        )

        if local_idx is not None:
            # Cliente existe en config local — actualizar ahí
            self.config.update_client(local_idx, name, email, pwd, tipo)
        else:
            # Cliente viene solo del registro del Sistema XML — agregar al config local
            self.config.add_client(name, email, pwd, tipo)

        self._cancel_edit()
        self._refresh_clients()
        messagebox.showinfo("Cliente actualizado",
                            f'Los datos de "{name}" fueron actualizados correctamente.')

    def _cancel_edit(self):
        self._edit_index = None
        self._edit_email = ""
        self._clear_form()
        self.action_btn.configure(text="Agregar cliente", bg=CLR_ACCENT)
        self.cancel_edit_btn.pack_forget()
        self.edit_mode_lbl.grid_remove()

    def _clear_form(self):
        self.client_name_var.set("")
        self.client_email_var.set("")
        self.client_pwd_var.set("")
        self.client_tipo_var.set("IVA")

    def _delete_client(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Sin selección",
                                   "Seleccione un cliente de la tabla.")
            return
        name      = self.tree.item(sel[0])["values"][0]
        email_sel = self._selected_email()
        if not messagebox.askyesno("Confirmar eliminación",
                                   f'¿Eliminar al cliente "{name}"?'):
            return
        local_clients = self.config.config.get("clients", [])
        local_idx = next(
            (i for i, c in enumerate(local_clients) if c["email"] == email_sel),
            None
        )
        if local_idx is not None:
            self.config.remove_client(local_idx)
        else:
            messagebox.showwarning(
                "No se puede eliminar",
                f'"{name}" proviene del registro del Sistema XML.\n'
                "Para eliminarlo, hacélo desde el Sistema XML."
            )
        self._refresh_clients()

    # -----------------------------------------------------------------------
    # Browse "desde carpeta" — auto-rellena nombre y tipo ──────────────────
    # -----------------------------------------------------------------------

    def _browse_client_folder(self):
        """
        Abre un selector de carpeta. Si el usuario elige la carpeta del cliente
        (p.ej. .../IVA/Ramírez Trejos Gerald), se extrae:
          - El nombre del cliente = último componente de la ruta
          - El tipo (IVA/REA) = si algún padre se llama "IVA" o "REA"
        """
        folder = filedialog.askdirectory(
            title="Seleccionar carpeta del cliente"
        )
        if not folder:
            return

        folder = folder.replace("/", os.sep)
        parts  = Path(folder).parts

        # Nombre: último componente de la ruta
        client_name = parts[-1] if parts else ""
        self.client_name_var.set(client_name)

        # Tipo: buscar "IVA" o "REA" en los componentes del path (mayúsculas o minúsculas)
        detected_tipo = None
        for part in parts:
            if part.upper() == "IVA":
                detected_tipo = "IVA"
                break
            if part.upper() == "REA":
                detected_tipo = "REA"
                break

        if detected_tipo:
            self.client_tipo_var.set(detected_tipo)
            messagebox.showinfo(
                "Carpeta detectada",
                f"Nombre: {client_name}\n"
                f"Régimen detectado: {detected_tipo}\n\n"
                f"Verifique los datos y complete correo y contraseña."
            )
        else:
            messagebox.showinfo(
                "Carpeta seleccionada",
                f"Nombre cargado: {client_name}\n\n"
                f"No se pudo detectar automáticamente el tipo (IVA/REA). "
                f"Selecciónelo manualmente."
            )

    # -----------------------------------------------------------------------
    # Conexión
    # -----------------------------------------------------------------------

    def _run_connection_test(self, email: str, pwd: str):
        def _test():
            dl = IMAPDownloader(email, pwd)
            ok = dl.test_connection()
            if ok:
                self.root.after(0, lambda: messagebox.showinfo(
                    "Conexión exitosa",
                    f"La conexión con {email} fue exitosa."))
            else:
                self.root.after(0, lambda: messagebox.showerror(
                    "Error de conexión",
                    f"No se pudo conectar a {email}.\n"
                    "Verifique el correo y la contraseña de aplicación."))
        threading.Thread(target=_test, daemon=True).start()

    def _test_connection(self):
        """Prueba con los datos del formulario (cliente aún no guardado)."""
        email = self.client_email_var.get().strip().lower()
        pwd   = _normalize_app_password(self.client_pwd_var.get())
        if not (email and pwd):
            messagebox.showerror("Datos incompletos",
                                 "Ingrese correo y contraseña para probar.")
            return
        self._run_connection_test(email, pwd)

    def _test_connection_stored(self):
        """Prueba con la contraseña guardada del cliente seleccionado."""
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Sin selección",
                                   "Seleccione un cliente de la tabla primero.")
            return
        email_sel = self._selected_email()
        for c in self.config.get_clients():
            if c["email"] == email_sel:
                email = c["email"]
                pwd   = self.config.decode_password(c["password"], email)
                self._run_connection_test(email, pwd)
                return

    def _on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        email_sel = self._selected_email()
        for c in self.config.get_clients():
            if c["email"] == email_sel:
                self.client_name_var.set(c["name"].split(" (")[0])
                self.client_email_var.set(c["email"])
                self.client_pwd_var.set("")
                self.client_tipo_var.set(c.get("tipo", "IVA"))
                break

    # -----------------------------------------------------------------------
    # Configuración
    # -----------------------------------------------------------------------

    def _browse_base_path(self, var: tk.StringVar):
        path = filedialog.askdirectory(title="Seleccionar carpeta base")
        if path:
            var.set(path.replace("/", os.sep))

    def _save_config(self):
        iva_path = self.base_path_iva_var.get().strip()
        rea_path = self.base_path_rea_var.get().strip()

        warnings = []
        if iva_path and not os.path.isdir(iva_path):
            warnings.append(f"La carpeta IVA no existe:\n{iva_path}")
        if rea_path and not os.path.isdir(rea_path):
            warnings.append(f"La carpeta REA no existe:\n{rea_path}")

        if warnings:
            if not messagebox.askyesno(
                "Carpeta no encontrada",
                "\n\n".join(warnings) + "\n\n¿Guardar de todas formas?"
            ):
                return

        self.config.set_base_path_iva(iva_path)
        self.config.set_base_path_rea(rea_path)
        messagebox.showinfo("Guardado", "Configuración guardada correctamente.")

    # -----------------------------------------------------------------------
    # Selección de clientes (descarga)
    # -----------------------------------------------------------------------

    def _clear_search(self):
        """Limpia la barra de búsqueda y restaura el placeholder."""
        self.search_var.set("")
        self._search_entry.delete(0, "end")
        self._search_entry.insert(0, "Buscar cliente...")
        self._search_entry.configure(foreground="#aaaaaa")
        self._refresh_clients()

    def _toggle_period_panel(self):
        """Despliega o colapsa el selector de meses."""
        if self._period_expanded.get():
            self._months_panel.pack_forget()
            self._period_expanded.set(False)
            self._period_toggle_btn.configure(text="▼ Meses")
        else:
            self._months_panel.pack(fill="x",
                                    after=self._period_summary_lbl)
            self._period_expanded.set(True)
            self._period_toggle_btn.configure(text="▲ Meses")

    def _update_period_summary(self):
        """Actualiza el label que muestra los meses seleccionados."""
        selected = self._get_selected_months()
        if not selected:
            text = "Sin meses seleccionados"
            color = CLR_BTN_RED
        elif len(selected) == 12:
            text = "Todos los meses"
            color = CLR_ACCENT
        elif len(selected) == 1:
            text = MONTHS_ES[selected[0] - 1]
            color = CLR_ACCENT
        else:
            abrev = [MONTHS_ES[m - 1][:3] for m in selected]
            text = ", ".join(abrev)
            color = CLR_ACCENT
        self._period_summary_lbl.configure(text=text, fg=color)

    def _on_month_changed(self):
        """Callback al marcar/desmarcar un mes."""
        self._update_period_summary()
        self._refresh_clients()

    def _select_all_months(self):
        for v in self.month_vars:
            v.set(True)
        self._update_period_summary()
        self._refresh_clients()

    def _clear_months(self):
        for v in self.month_vars:
            v.set(False)
        self._update_period_summary()
        self._refresh_clients()

    def _select_all_audit_months(self):
        for v in self.audit_month_vars:
            v.set(True)

    def _clear_audit_months(self):
        for v in self.audit_month_vars:
            v.set(False)

    def _select_cuatrimestre(self, c: int, month_vars: list = None):
        """Selecciona los 4 meses del cuatrimestre.
        C1=Ene-Abr, C2=May-Ago, C3=Sep-Dic.
        """
        ranges = {1: (1, 4), 2: (5, 8), 3: (9, 12)}
        start, end = ranges[c]
        target = month_vars if month_vars is not None else self.month_vars
        for i, v in enumerate(target):
            v.set(start <= i + 1 <= end)
        if month_vars is None:
            self._update_period_summary()
            self._refresh_clients()

    def _get_selected_months(self) -> list[int]:
        """Retorna la lista de números de mes seleccionados en Descarga (1–12)."""
        return [i + 1 for i, v in enumerate(self.month_vars) if v.get()]

    def _get_selected_audit_months(self) -> list[int]:
        """Retorna la lista de números de mes seleccionados en Auditoría (1–12)."""
        return [i + 1 for i, v in enumerate(self.audit_month_vars) if v.get()]

    def _select_all(self):
        for v in self.client_vars:
            v.set(True)

    def _deselect_all(self):
        for v in self.client_vars:
            v.set(False)

    def _select_by_type(self, tipo: str):
        """Selecciona solo los grupos cuyo tipo coincide, respetando el orden y filtro actual."""
        all_clients = self.config.get_clients()
        grupos: list[list[dict]] = []
        visto: dict[str, int] = {}
        for c in all_clients:
            cedula = c.get("_cedula", "").strip()
            if cedula and cedula in visto:
                grupos[visto[cedula]].append(c)
            else:
                grupos.append([c])
                if cedula:
                    visto[cedula] = len(grupos) - 1

        grupos.sort(key=lambda g: g[0]["name"].split(" (")[0].lower())
        raw_search = self.search_var.get().strip()
        filtro = raw_search.lower() \
            if raw_search and raw_search != "Buscar cliente..." else ""
        if filtro:
            grupos = [
                g for g in grupos
                if filtro in g[0]["name"].split(" (")[0].lower()
                or any(filtro in c["email"].lower() for c in g)
            ]

        for grupo, var in zip(grupos, self.client_vars):
            var.set(grupo[0].get("tipo", "IVA") == tipo)

    def _toggle_pwd_visibility(self):
        self._pwd_entry.config(show="" if self._show_pwd.get() else "*")

    # -----------------------------------------------------------------------
    # Log / consola
    # -----------------------------------------------------------------------

    def _log(self, msg: str):
        self.log_queue.put(msg)

    def _poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(80, self._poll_log)

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # -----------------------------------------------------------------------
    # Validación de rutas base
    # -----------------------------------------------------------------------

    def _check_paths(self):
        """
        Verifica si las rutas base IVA y REA existen en disco.
        Muestra u oculta el banner de advertencia según el resultado.
        Se llama al arrancar la app y al volver a la pestaña Descarga.
        """
        import os as _os

        problems = []
        iva = self.config.get_base_path_iva()
        rea = self.config.get_base_path_rea()

        # Rutas configuradas pero que no existen (p.ej. OneDrive desconectado)
        if iva and not _os.path.isdir(iva):
            problems.append(f"⚠ Carpeta IVA no encontrada:\n   {iva}")
        if rea and not _os.path.isdir(rea):
            problems.append(f"⚠ Carpeta REA no encontrada:\n   {rea}")

        # Rutas no configuradas — solo avisar si hay clientes del tipo correspondiente
        if not iva and not rea:
            problems.append("⚠ Las rutas base IVA y REA no están configuradas.\n"
                            "   Vaya a Configuración antes de descargar.")
        else:
            if not iva and any(c.get("tipo", "IVA") == "IVA"
                               for c in self.config.get_clients()):
                problems.append("⚠ La ruta base IVA no está configurada.")
            if not rea and any(c.get("tipo") == "REA"
                               for c in self.config.get_clients()):
                problems.append("⚠ La ruta base REA no está configurada.")

        if problems:
            self.path_warning_lbl.configure(text="\n".join(problems))
            self.path_warning_frm.pack(fill="x", pady=(0, 4))
        else:
            self.path_warning_frm.pack_forget()

    def _on_tab_changed(self, _event=None):
        """Actualiza validación de rutas al volver a la pestaña Descarga."""
        try:
            current = self.nb.tab(self.nb.select(), "text").strip()
            if current == "Descarga":
                self._check_paths()
        except Exception:
            pass

    def _update_bridge_indicator(self):
        """
        Actualiza el indicador de estado del Sistema XML en el header.
        Se ejecuta al arrancar y se puede llamar manualmente.
        """
        if not _BRIDGE_AVAILABLE:
            self.bridge_lbl.configure(
                text="◦ Sistema XML: bridge no instalado",
                fg="#7f8c8d",
            )
            return

        status = self.config.get_bridge_status()

        if not status.get("sistema_xml_encontrado"):
            self.bridge_lbl.configure(
                text="◦ Sistema XML: no encontrado en ../Sistema XML",
                fg="#e74c3c",
            )
            return

        n_clientes = status.get("total_clientes", 0)
        flask_ok   = status.get("flask_running", False)

        if flask_ok:
            self.bridge_lbl.configure(
                text=f"● Sistema XML conectado  ·  {n_clientes} clientes  ·  servidor activo",
                fg="#2ecc71",
            )
        else:
            self.bridge_lbl.configure(
                text=f"◉ Sistema XML vinculado  ·  {n_clientes} clientes  ·  servidor apagado",
                fg="#f39c12",
            )

    def _open_emails_manager(self):
        """
        Abre una ventana emergente para gestionar todos los correos
        asociados al cliente seleccionado en la tabla.
        Permite agregar correos nuevos (con contraseña) y eliminar existentes.
        """
        email_sel = self._selected_email()
        if not email_sel:
            messagebox.showwarning("Sin selección",
                                   "Seleccione un cliente de la tabla primero.")
            return

        # Encontrar el cliente en el registro para obtener todos sus correos
        clientes    = self.config.get_clients()
        cliente_ref = next((c for c in clientes if c["email"] == email_sel), None)
        if not cliente_ref:
            return

        nombre_base = cliente_ref["name"].split(" (")[0]
        cedula      = cliente_ref.get("_cedula", "")

        # Obtener todos los correos del mismo cliente (misma cédula)
        if cedula:
            correos_actuales = [
                c for c in clientes
                if c.get("_cedula") == cedula
            ]
        else:
            correos_actuales = [cliente_ref]

        # ── Ventana emergente ────────────────────────────────────────────────
        win = tk.Toplevel(self.root)
        win.title(f"Correos — {nombre_base}")
        win.geometry("520x420")
        win.minsize(480, 360)
        win.configure(bg=CLR_BG)
        win.transient(self.root)
        win.grab_set()

        tk.Label(win, text=f"Correos de: {nombre_base}",
                 font=("Segoe UI", 11, "bold"),
                 bg=CLR_BG, fg=CLR_HEADER).pack(pady=(14, 4), padx=16, anchor="w")
        tk.Label(win, text="Cada correo es una cuenta Gmail independiente "
                 "que se descarga en paralelo.",
                 font=("Segoe UI", 8), bg=CLR_BG, fg=CLR_MUTED,
                 wraplength=480, justify="left").pack(padx=16, anchor="w")

        ttk.Separator(win, orient="horizontal").pack(fill="x", padx=16, pady=8)

        # ── Lista de correos actuales ────────────────────────────────────────
        tk.Label(win, text="Correos registrados:",
                 font=("Segoe UI", 9, "bold"),
                 bg=CLR_BG).pack(anchor="w", padx=16)

        list_frm = tk.Frame(win, bg=CLR_WHITE, relief="sunken", bd=1)
        list_frm.pack(fill="x", padx=16, pady=(4, 8))

        def _rebuild_list():
            for w in list_frm.winfo_children():
                w.destroy()
            clientes_fresh = self.config.get_clients()
            if cedula:
                correos = [c for c in clientes_fresh if c.get("_cedula") == cedula]
            else:
                correos = [c for c in clientes_fresh if c["email"] == email_sel]

            if not correos:
                tk.Label(list_frm, text="  (sin correos configurados)",
                         bg=CLR_WHITE, fg=CLR_MUTED,
                         font=("Segoe UI", 9, "italic")).pack(pady=6)
                return

            for c in correos:
                row = tk.Frame(list_frm, bg=CLR_WHITE)
                row.pack(fill="x", padx=8, pady=3)

                # Ícono + correo
                tk.Label(row, text="✉", bg=CLR_WHITE,
                         font=("Segoe UI", 9)).pack(side="left")
                tk.Label(row, text=c["email"], bg=CLR_WHITE,
                         font=("Segoe UI", 9), anchor="w").pack(
                             side="left", padx=(4, 0), fill="x", expand=True)

                # Tipo
                badge_color = CLR_IVA if c.get("tipo", "IVA") == "IVA" else CLR_REA
                tk.Label(row, text=c.get("tipo", "IVA"),
                         bg=badge_color, fg=CLR_WHITE,
                         font=("Segoe UI", 7, "bold"),
                         padx=4, pady=1).pack(side="right", padx=(0, 4))

                # Botón eliminar (solo si hay más de uno)
                if len(correos) > 1:
                    def _make_delete(email_to_del):
                        def _do():
                            if not messagebox.askyesno(
                                "Eliminar correo",
                                f"¿Eliminar {email_to_del} de {nombre_base}?\n\n"
                                "Se eliminará la contraseña guardada.",
                                parent=win
                            ):
                                return
                            local = self.config.config.get("clients", [])
                            idx = next((i for i, x in enumerate(local)
                                        if x["email"] == email_to_del), None)
                            if idx is not None:
                                self.config.remove_client(idx)
                            _rebuild_list()
                            self._refresh_clients()
                        return _do
                    tk.Button(row, text="✕", font=("Segoe UI", 8),
                              bg=CLR_WHITE, fg=CLR_BTN_RED,
                              relief="flat", cursor="hand2",
                              command=_make_delete(c["email"])
                              ).pack(side="right")

        _rebuild_list()

        ttk.Separator(win, orient="horizontal").pack(fill="x", padx=16, pady=(0, 8))

        # ── Formulario para agregar correo nuevo ─────────────────────────────
        tk.Label(win, text="Agregar correo nuevo:",
                 font=("Segoe UI", 9, "bold"),
                 bg=CLR_BG).pack(anchor="w", padx=16)

        add_frm = tk.Frame(win, bg=CLR_BG)
        add_frm.pack(fill="x", padx=16, pady=(4, 0))
        add_frm.columnconfigure(1, weight=1)

        tk.Label(add_frm, text="Correo:", bg=CLR_BG,
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", pady=3)
        new_email_var = tk.StringVar()
        ttk.Entry(add_frm, textvariable=new_email_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 0), pady=3)

        tk.Label(add_frm, text="Contraseña:", bg=CLR_BG,
                 font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=3)

        pwd_frm = tk.Frame(add_frm, bg=CLR_BG)
        pwd_frm.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=3)
        pwd_frm.columnconfigure(0, weight=1)

        new_pwd_var  = tk.StringVar()
        show_pwd_var = tk.BooleanVar(value=False)
        pwd_entry    = ttk.Entry(pwd_frm, textvariable=new_pwd_var, show="*")
        pwd_entry.grid(row=0, column=0, sticky="ew")

        def _toggle_show():
            pwd_entry.configure(show="" if show_pwd_var.get() else "*")

        tk.Checkbutton(pwd_frm, text="Ver", variable=show_pwd_var,
                       bg=CLR_BG, font=("Segoe UI", 8),
                       command=_toggle_show).grid(row=0, column=1, padx=(6, 0))

        def _add_email():
            email_new = new_email_var.get().strip().lower()
            pwd_new   = _normalize_app_password(new_pwd_var.get())

            if not email_new:
                messagebox.showerror("Campo requerido",
                                     "Ingrese el correo electrónico.", parent=win)
                return
            if not _RE_EMAIL.match(email_new):
                messagebox.showerror("Correo inválido",
                                     f'"{email_new}" no es un correo válido.', parent=win)
                return
            ok, msg = _validate_app_password(pwd_new, email_new)
            if not ok:
                messagebox.showerror("Contraseña inválida", msg, parent=win)
                return
            if self.config.email_exists(email_new):
                otros = [c["name"].split(" (")[0]
                         for c in self.config.get_clients()
                         if c["email"] == email_new
                         and c["name"].split(" (")[0] != nombre_base]
                if otros:
                    otros_str = ", ".join(set(otros))
                    if not messagebox.askyesno(
                        "Correo compartido",
                        f"{email_new} ya está registrado para:\n  {otros_str}\n\n"
                        f"¿Asociarlo también a {nombre_base}?\n\n"
                        "El sistema descargará una sola vez y distribuirá\n"
                        "los archivos según la cédula de cada documento.",
                        parent=win,
                    ):
                        return

            # Agregar con el mismo nombre y tipo del cliente
            self.config.add_client(nombre_base, email_new, pwd_new,
                                   cliente_ref.get("tipo", "IVA"))
            new_email_var.set("")
            new_pwd_var.set("")
            _rebuild_list()
            self._refresh_clients()
            messagebox.showinfo("Correo agregado",
                                f"{email_new} fue agregado correctamente.", parent=win)

        btn_row = tk.Frame(win, bg=CLR_BG)
        btn_row.pack(fill="x", padx=16, pady=(8, 14))

        self._flat_btn(btn_row, "Agregar correo", CLR_ACCENT,
                       _add_email).pack(side="right")
        self._flat_btn(btn_row, "Cerrar", CLR_MUTED,
                       win.destroy).pack(side="right", padx=(0, 6))

    def _open_last_in_sistema_xml(self):
        """
        Abre el Sistema XML en el navegador con el último cliente procesado activo.
        - Si el servidor está corriendo: establece la carpeta activa y abre el navegador.
        - Si el servidor no está corriendo: ofrece abrir el explorador en la carpeta
          del Sistema XML para que el usuario ejecute ejecutar.bat.
        """
        if not _BRIDGE_AVAILABLE:
            messagebox.showinfo(
                "Bridge no disponible",
                "El módulo sistema_xml_bridge.py no está instalado.\n"
                "Copíelo a la carpeta del Extractor para habilitar esta función."
            )
            return

        if not self._last_cyg_path:
            messagebox.showinfo(
                "Sin cliente reciente",
                "No hay ningún cliente procesado en esta sesión.\n"
                "Realizá una descarga primero."
            )
            return

        carpeta_para_abrir = self._last_cyg_path

        if not is_flask_running():
            sistema_dir = Path(__file__).parent.parent / "Sistema XML"
            respuesta = messagebox.askyesno(
                "Sistema XML apagado",
                "El servidor del Sistema XML no está corriendo.\n\n"
                "¿Querés abrir la carpeta del Sistema XML para iniciarlo?\n"
                "(Ejecutá ejecutar.bat y luego volvé a presionar este botón)"
            )
            if respuesta:
                import subprocess
                if sistema_dir.is_dir():
                    subprocess.Popen(f'explorer "{sistema_dir}"')
                else:
                    messagebox.showerror(
                        "No encontrado",
                        f"No se encontró la carpeta del Sistema XML en:\n{sistema_dir}"
                    )
            return

        # Servidor activo — abrir en hilo para no bloquear la UI
        def _abrir():
            open_in_sistema_xml(carpeta_para_abrir)
        threading.Thread(target=_abrir, daemon=True).start()
        self._log(f"\n  ★ Abriendo Sistema XML → {carpeta_para_abrir}")

    def _export_log(self):
        """Guarda el contenido actual de la consola en un archivo .txt."""
        content = self.log_text.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("Log vacío", "No hay contenido en el registro para exportar.")
            return

        timestamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"log_facturas_{timestamp}.txt"

        filepath = filedialog.asksaveasfilename(
            title="Guardar registro",
            initialfile=default_name,
            defaultextension=".txt",
            filetypes=[("Archivo de texto", "*.txt"), ("Todos los archivos", "*.*")],
        )
        if not filepath:
            return

        try:
            header = (
                f"Gestor de Facturas Electrónicas — Registro de actividad\n"
                f"Exportado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"{'=' * 55}\n\n"
            )
            Path(filepath).write_text(header + content, encoding="utf-8")
            messagebox.showinfo("Log exportado",
                                f"Registro guardado correctamente en:\n{filepath}")
        except OSError as exc:
            messagebox.showerror("Error al guardar", f"No se pudo guardar el archivo:\n{exc}")

    # -----------------------------------------------------------------------
    # Utilidades de layout
    # -----------------------------------------------------------------------

    def _on_checks_configure(self, _event=None):
        self.clients_canvas.configure(
            scrollregion=self.clients_canvas.bbox("all")
        )

    def _on_canvas_resize(self, event):
        self.clients_canvas.itemconfig(self._canvas_win, width=event.width)

    @staticmethod
    def _section_label(parent, text: str):
        tk.Label(parent, text=text, font=("Segoe UI", 10, "bold"),
                 bg=CLR_BG, fg=CLR_HEADER).pack(anchor="w", pady=(10, 0))

    @staticmethod
    def _flat_btn(parent, text: str, color: str, command) -> tk.Button:
        return tk.Button(
            parent, text=text, font=("Segoe UI", 10, "bold"),
            bg=color, fg=CLR_WHITE, activebackground=color,
            relief="flat", padx=14, pady=6, cursor="hand2",
            command=command,
        )


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def main():
    root = tk.Tk()
    try:
        root.iconbitmap(default="")
    except Exception:
        pass
    app = GestorFacturasApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
