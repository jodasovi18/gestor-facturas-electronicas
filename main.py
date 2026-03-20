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
from pathlib import Path
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from config_manager import ConfigManager
from imap_downloader import IMAPDownloader
from xml_classifier import XMLClassifier
from folder_manager import FolderManager


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


def _normalize_app_password(raw: str) -> str:
    """
    Las contraseñas de aplicación de Google se copian a veces con espacios
    (formato visual: xxxx xxxx xxxx xxxx). Los eliminamos para obtener
    los 16 caracteres reales que acepta IMAP.
    """
    return raw.replace(" ", "").replace("\t", "").strip()


def _validate_app_password(pwd: str) -> tuple[bool, str]:
    """Valida que la contraseña de aplicación tenga 16 caracteres alfanuméricos."""
    if not pwd:
        return False, "La contraseña no puede estar vacía."
    if len(pwd) != 16:
        return False, (
            f"La contraseña de aplicación debe tener 16 caracteres "
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
        self.root.geometry("1020x700")
        self.root.minsize(900, 600)
        self.root.configure(bg=CLR_BG)

        self.config     = ConfigManager()
        self.classifier = XMLClassifier(
            default_folder=self.config.config.get("default_folder", "CyG")
        )
        self.log_queue    = queue.Queue()
        self._downloading = False
        self._edit_index: int | None = None

        self._build_styles()
        self._build_ui()
        self._refresh_clients()
        self._poll_log()

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

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=12, pady=10)

        self.tab_dl      = ttk.Frame(self.nb)
        self.tab_clients = ttk.Frame(self.nb)
        self.tab_cfg     = ttk.Frame(self.nb)

        self.nb.add(self.tab_dl,      text="  Descarga  ")
        self.nb.add(self.tab_clients, text="  Clientes  ")
        self.nb.add(self.tab_cfg,     text="  Configuración  ")

        self._build_download_tab()
        self._build_clients_tab()
        self._build_config_tab()

    # -----------------------------------------------------------------------
    # TAB: Descarga
    # -----------------------------------------------------------------------

    def _build_download_tab(self):
        outer = ttk.Frame(self.tab_dl)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Panel izquierdo ---
        left = tk.Frame(outer, bg=CLR_BG, width=280)
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)

        self._section_label(left, "Período a procesar")

        period_frm = tk.Frame(left, bg=CLR_BG)
        period_frm.pack(fill="x", pady=(4, 12))

        tk.Label(period_frm, text="Mes:", bg=CLR_BG,
                 font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w", pady=3)
        self.month_var = tk.StringVar(value=MONTHS_ES[datetime.now().month - 1])
        ttk.Combobox(period_frm, textvariable=self.month_var,
                     values=MONTHS_ES, width=16, state="readonly"
                     ).grid(row=0, column=1, padx=(8, 0), pady=3)

        tk.Label(period_frm, text="Año:", bg=CLR_BG,
                 font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=3)
        yr = datetime.now().year
        self.year_var = tk.StringVar(value=str(yr))
        ttk.Combobox(period_frm, textvariable=self.year_var,
                     values=[str(y) for y in range(yr - 3, yr + 2)],
                     width=16, state="readonly"
                     ).grid(row=1, column=1, padx=(8, 0), pady=3)

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=8)

        self._section_label(left, "Clientes a procesar")

        btn_row = tk.Frame(left, bg=CLR_BG)
        btn_row.pack(fill="x", pady=(4, 6))
        ttk.Button(btn_row, text="Todos",
                   command=self._select_all).pack(side="left", padx=(0, 4))
        ttk.Button(btn_row, text="Ninguno",
                   command=self._deselect_all).pack(side="left")
        ttk.Button(btn_row, text="Solo IVA",
                   command=lambda: self._select_by_type("IVA")).pack(side="left", padx=(4, 0))
        ttk.Button(btn_row, text="Solo REA",
                   command=lambda: self._select_by_type("REA")).pack(side="left", padx=(4, 0))

        list_frame = tk.Frame(left, bg=CLR_WHITE, relief="sunken", bd=1)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))

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

        self.dl_btn = tk.Button(
            left, text="  Iniciar Descarga",
            font=("Segoe UI", 11, "bold"),
            bg=CLR_ACCENT, fg=CLR_WHITE, activebackground=CLR_ACCENT_HV,
            relief="flat", pady=10, cursor="hand2",
            command=self._start_download,
        )
        self.dl_btn.pack(fill="x")

        self.progress_var = tk.DoubleVar()
        ttk.Progressbar(left, variable=self.progress_var,
                        maximum=100).pack(fill="x", pady=(8, 0))
        self.progress_lbl = tk.Label(left, text="", bg=CLR_BG,
                                     fg=CLR_MUTED, font=("Segoe UI", 9))
        self.progress_lbl.pack(anchor="w")

        # --- Panel derecho: consola ---
        right = tk.Frame(outer, bg=CLR_BG)
        right.pack(side="right", fill="both", expand=True)

        header_row = tk.Frame(right, bg=CLR_BG)
        header_row.pack(fill="x", pady=(0, 5))
        tk.Label(header_row, text="Registro de actividad",
                 font=("Segoe UI", 11, "bold"),
                 bg=CLR_BG, fg=CLR_HEADER).pack(side="left")
        ttk.Button(header_row, text="Limpiar",
                   command=self._clear_log).pack(side="right")

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
            "Consejo: la contraseña de aplicación se genera en Cuenta de Google "
            "› Seguridad › Contraseñas de aplicación. "
            "Tiene 16 caracteres (sin contar los espacios visuales)."
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

        cols = ("Nombre", "Correo electrónico", "Régimen", "Estado")
        self.tree = ttk.Treeview(tbl_frm, columns=cols,
                                 show="headings", height=8)
        for col in cols:
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

    def _start_download(self):
        if self._downloading:
            messagebox.showwarning("En proceso", "Ya hay una descarga activa.")
            return

        clients = self.config.get_clients()
        selected = [
            c for c, var in zip(clients, self.client_vars) if var.get()
        ]
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

        month_num = MONTHS_ES.index(self.month_var.get()) + 1
        year      = int(self.year_var.get())

        self._downloading = True
        self.dl_btn.configure(state="disabled",
                              text="  Descargando...", bg="#95a5a6")
        self.progress_var.set(0)

        threading.Thread(
            target=self._download_worker,
            args=(selected, year, month_num),
            daemon=True,
        ).start()

    def _download_worker(self, clients: list, year: int, month: int):
        try:
            total = len(clients)

            self._log("=" * 55)
            self._log(f"  Descarga: {MONTHS_ES[month-1]} {year}")
            self._log(f"  Clientes a procesar: {total}")
            self._log("=" * 55)

            grand_total = 0

            for idx, client in enumerate(clients):
                progress = (idx / total) * 100
                self.root.after(0, lambda p=progress: self.progress_var.set(p))
                self.root.after(
                    0,
                    lambda n=client["name"], i=idx, t=total:
                    self.progress_lbl.configure(
                        text=f"Procesando {i+1}/{t}: {n}"),
                )

                tipo      = client.get("tipo", "IVA")
                base_path = self.config.get_base_path_for_client(client)

                self._log(f"\n  Cliente: {client['name']}  [{tipo}]")
                self._log(f"  Correo:  {client['email']}")
                self._log(f"  Ruta:    {base_path}")

                try:
                    folder_mgr = FolderManager(base_path)
                    pwd = self.config.decode_password(client["password"])
                    dl  = IMAPDownloader(client["email"], pwd, log=self._log)
                    dl.connect()
                    stats = dl.download_month(
                        year, month, folder_mgr, self.classifier, client["name"]
                    )
                    dl.disconnect()

                    grand_total += stats["total"]
                    self._log(f"  Archivos descargados: {stats['total']}")
                    for doc_type, count in stats["by_type"].items():
                        self._log(f"    - {doc_type}: {count}")
                    if stats.get("skipped", 0):
                        self._log(f"  Omitidos (ya existían): {stats['skipped']}")
                    if stats["errors"]:
                        self._log(f"  Advertencias: {stats['errors']}")

                except Exception as e:
                    self._log(f"  ERROR: {e}")

            self.root.after(0, lambda: self.progress_var.set(100))
            self._log(f"\n{'=' * 55}")
            self._log(f"  Descarga completada. Total de archivos: {grand_total}")
            self._log("=" * 55)

        except Exception as e:
            self._log(f"\nError crítico: {e}")
        finally:
            self._downloading = False
            self.root.after(0, self._reset_download_btn)

    def _reset_download_btn(self):
        self.dl_btn.configure(state="normal",
                              text="  Iniciar Descarga", bg=CLR_ACCENT)
        self.progress_lbl.configure(text="Listo")

    # -----------------------------------------------------------------------
    # Lógica de clientes
    # -----------------------------------------------------------------------

    def _refresh_clients(self):
        for w in self.checks_frame.winfo_children():
            w.destroy()
        self.client_vars.clear()

        for row in self.tree.get_children():
            self.tree.delete(row)

        for client in self.config.get_clients():
            tipo = client.get("tipo", "IVA")
            tag  = "iva" if tipo == "IVA" else "rea"

            var = tk.BooleanVar(value=True)
            self.client_vars.append(var)

            # Checkbox con etiqueta de tipo
            row_frm = tk.Frame(self.checks_frame, bg=CLR_WHITE)
            row_frm.pack(fill="x", padx=4, pady=1)

            tk.Checkbutton(
                row_frm, text=client["name"],
                variable=var, bg=CLR_WHITE, font=("Segoe UI", 10), anchor="w",
            ).pack(side="left", fill="x", expand=True)

            badge_color = CLR_IVA if tipo == "IVA" else CLR_REA
            tk.Label(row_frm, text=tipo,
                     bg=badge_color, fg=CLR_WHITE,
                     font=("Segoe UI", 7, "bold"),
                     padx=4, pady=1).pack(side="right", padx=(0, 4))

            self.tree.insert("", "end",
                             values=(client["name"], client["email"],
                                     tipo, "Configurado"),
                             tags=(tag,))

    def _collect_form_fields(self) -> tuple[str, str, str, str] | None:
        """
        Lee, limpia y valida los campos del formulario.
        Retorna (name, email, pwd, tipo) o None si hay error.
        """
        name  = self.client_name_var.get().strip()
        email = self.client_email_var.get().strip().lower()
        pwd   = _normalize_app_password(self.client_pwd_var.get())
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

        ok, msg = _validate_app_password(pwd)
        if not ok:
            messagebox.showerror("Contraseña inválida", msg)
            return None

        return name, email, pwd, tipo

    def _add_client(self):
        if self._edit_index is not None:
            self._save_edit()
            return

        result = self._collect_form_fields()
        if result is None:
            return
        name, email, pwd, tipo = result

        if self.config.email_exists(email):
            messagebox.showerror("Duplicado",
                                 f"El correo {email} ya está registrado.")
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

    def _load_for_edit(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Sin selección",
                                   "Seleccione un cliente de la tabla para editar.")
            return
        name = self.tree.item(sel[0])["values"][0]
        clients = self.config.get_clients()
        for i, c in enumerate(clients):
            if c["name"] == name:
                self._edit_index = i
                self.client_name_var.set(c["name"])
                self.client_email_var.set(c["email"])
                self.client_pwd_var.set(
                    self.config.decode_password(c["password"]))
                self.client_tipo_var.set(c.get("tipo", "IVA"))

                self.action_btn.configure(text="Guardar cambios",
                                          bg=CLR_BTN_BLUE)
                self.cancel_edit_btn.pack(side="right", padx=(6, 0))
                self.edit_mode_lbl.configure(
                    text=f"  Editando: {c['name']}  —  modifique los campos "
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

        original = self.config.get_clients()[self._edit_index]
        if email != original["email"] and self.config.email_exists(email):
            messagebox.showerror("Duplicado",
                                 f"El correo {email} ya está registrado en otro cliente.")
            return

        self.config.update_client(self._edit_index, name, email, pwd, tipo)
        self._cancel_edit()
        self._refresh_clients()
        messagebox.showinfo("Cliente actualizado",
                            f'Los datos de "{name}" fueron actualizados correctamente.')

    def _cancel_edit(self):
        self._edit_index = None
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
        name = self.tree.item(sel[0])["values"][0]
        if not messagebox.askyesno("Confirmar eliminación",
                                   f'¿Eliminar al cliente "{name}"?'):
            return
        for i, c in enumerate(self.config.get_clients()):
            if c["name"] == name:
                self.config.remove_client(i)
                break
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
        name = self.tree.item(sel[0])["values"][0]
        for c in self.config.get_clients():
            if c["name"] == name:
                email = c["email"]
                pwd   = self.config.decode_password(c["password"])
                self._run_connection_test(email, pwd)
                return

    def _on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        name = self.tree.item(sel[0])["values"][0]
        for c in self.config.get_clients():
            if c["name"] == name:
                self.client_name_var.set(c["name"])
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

    def _select_all(self):
        for v in self.client_vars:
            v.set(True)

    def _deselect_all(self):
        for v in self.client_vars:
            v.set(False)

    def _select_by_type(self, tipo: str):
        """Selecciona solo los clientes del tipo especificado (IVA o REA)."""
        clients = self.config.get_clients()
        for client, var in zip(clients, self.client_vars):
            var.set(client.get("tipo", "IVA") == tipo)

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
