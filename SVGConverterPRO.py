import os
import sys
import zipfile
import threading
import base64
from pathlib import Path
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor, as_completed

import tkinter as tk
from tkinter import filedialog, messagebox

import ttkbootstrap as tb
from ttkbootstrap.constants import *

from tkinterdnd2 import DND_FILES, TkinterDnD
from PIL import Image, ImageTk
from io import BytesIO

try:
    from cairosvg import svg2png, svg2pdf
except ImportError:
    messagebox.showerror("Missing Dependency", "CairoSVG is required.\nRun: pip install cairosvg")
    sys.exit()

import logging
from logging.handlers import RotatingFileHandler

# ---------------- LOGGING ----------------
LOG_DIR = Path.home() / ".svgconverterpro"
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("svgconverterpro")
logger.setLevel(logging.INFO)

handler = RotatingFileHandler(
    LOG_DIR / "app.log",
    maxBytes=1_000_000,
    backupCount=3
)
formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s"
)
handler.setFormatter(formatter)
logger.addHandler(handler)

# ================= CONFIG =================
APP_NAME = "SVG Converter PRO"
APP_VERSION = "2.2.0"

ICON_SIZES_DEFAULT = [16, 32, 48, 64, 128, 256, 512]

# ================= INIT =================
app = TkinterDnD.Tk()
style = tb.Style("darkly")

app.title(f"{APP_NAME} v{APP_VERSION}")
app.geometry("1100x730")
app.resizable(True, True)

# ================= GLOBAL =================
file_paths = []
output_folder = ""
ui_queue = Queue()
progress_var = tk.IntVar(value=0)
mode_var = tk.StringVar(value="SVG → PNG")
zip_var = tk.BooleanVar(value=False)

stop_event = threading.Event()
pause_event = threading.Event()
pause_event.set()

preview_images = []
size_vars = {}

GUMROAD_URL = "https://matetools.gumroad.com"

# ================= FILE / UI =================

def ensure_output():
    global output_folder
    if not output_folder:
        raise RuntimeError("No output folder selected")
    os.makedirs(output_folder, exist_ok=True)

def safe_output_path(path):
    base, ext = os.path.splitext(path)
    counter = 1
    new_path = path

    while os.path.exists(new_path):
        new_path = f"{base}_{counter}{ext}"
        counter += 1

    return new_path

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def select_files():
    global file_paths
    files = filedialog.askopenfilenames(
        filetypes=[("SVG Files", "*.svg")]
    )
    if files:
        file_paths = list(files)
    update_ui()
    update_preview()


def select_output():
    global output_folder
    output_folder = filedialog.askdirectory()
    update_ui()


def update_ui():
    drop_label.config(
        text=", ".join([os.path.basename(p) for p in file_paths]) if file_paths else "Drop files here or click to upload"
    )
    out_label.config(text=output_folder if output_folder else "No output folder selected")


def on_drop(event):
    global file_paths

    files = [f.strip("{}") for f in app.tk.splitlist(event.data)]
    valid_ext = (".svg",)

    valid_files = [f for f in files if f.lower().endswith(valid_ext)]
    invalid_files = [f for f in files if not f.lower().endswith(valid_ext)]

    file_paths = list(dict.fromkeys(file_paths + valid_files))

    if invalid_files:
        messagebox.showwarning(
            "Unsupported files",
            f"{len(invalid_files)} file(s) were ignored.\nOnly SVG files are allowed."
        )

    update_ui()
    update_preview()


def update_preview():
    global preview_images

    preview_canvas.delete("all")
    preview_images.clear()

    x = 10
    for path in file_paths[:10]:
        try:
            buffer = BytesIO()
            svg2png(url=path, write_to=buffer, output_width=200, output_height=200)
            buffer.seek(0)

            img = Image.open(buffer)
            img.thumbnail((100, 100))

            tk_img = ImageTk.PhotoImage(img)
            preview_images.append(tk_img)   # ✅ KEEP reference

            preview_canvas.create_image(x, 60, image=tk_img, anchor="w")
            x += 110

        except Exception as e:
            logger.error(f"Preview error: {e}", exc_info=True)

# ================= CONTROL =================

def stop_task():
    stop_event.set()
    pause_event.set()
    ui_queue.put(("status", "⛔ Stopping..."))


def pause_task():
    pause_event.clear()
    ui_queue.put(("status", "⏸ Paused"))
    pause_btn.config(state="disabled")
    resume_btn.config(state="normal")


def resume_task():
    pause_event.set()
    ui_queue.put(("status", "▶ Resumed"))
    pause_btn.config(state="normal")
    resume_btn.config(state="disabled")

# ================= SIZE =================

def get_selected_sizes():
    if mode_var.get() != "SVG → ICO":
        return []
    return [s for s, v in size_vars.items() if v.get()]

# ================= CORE =================

def process_single_file(path, mode):
    base = Path(path).stem
    outputs = []

    # ✅ FILE VALIDATION
    if os.path.getsize(path) == 0:
        raise RuntimeError("File is empty")

    if mode == "SVG → PNG":
        outputs = svg_to_png(path)
    elif mode == "SVG → JPG":
        outputs = svg_to_jpg(path)
    elif mode == "SVG → WebP":
        outputs = svg_to_webp(path)
    elif mode == "SVG → ICO":
        outputs = svg_to_ico(path)
    elif mode == "SVG → Base64":
        outputs = svg_to_base64(path)
    elif mode == "SVG → Data URI":
        outputs = svg_to_data_uri(path)
    elif mode == "SVG → Transparent PNG":
        outputs = svg_transparency_creator(path)
    elif mode == "SVG Opacity Changer":
        outputs = svg_opacity_changer(path)
    elif mode == "SVG Noise Filter":
        outputs = svg_noise_filter(path)
    elif mode == "SVG → PDF":
        outputs = svg_to_pdf(path)

    return outputs or []


def process_task():
    ensure_output()
    try:
        mode = mode_var.get()
        generated_files = []

        import time
        start_time = time.time()

        with ThreadPoolExecutor(max_workers = min(4, os.cpu_count() or 2)) as executor:
            futures = {executor.submit(process_single_file, p, mode): p for p in file_paths}

            completed = 0
            total = len(file_paths)

            for future in as_completed(futures):

                if stop_event.is_set():
                    ui_queue.put(("status", "⛔ Stopped"))
                    return

                while not pause_event.is_set():
                    if stop_event.is_set():
                        ui_queue.put(("status", "⛔ Stopped"))
                        return
                    pause_event.wait(timeout=0.2)

                try:
                    result = future.result()
                    generated_files.extend(result)
                except Exception as e:
                    path = futures[future]
                    ui_queue.put(("error", f"{Path(path).stem} failed: {e}"))

                completed += 1
                progress = int(completed / total * 100)

                path = futures[future]
                base = Path(path).stem

                ui_queue.put(("progress", progress))
                ui_queue.put(("file_status", f"{completed}/{total} → {base}"))

                # ⏳ TIME ESTIMATION (ADD HERE)
                elapsed = time.time() - start_time
                if completed > 0:
                    avg = elapsed / completed
                    remaining = avg * (total - completed)
                    ui_queue.put(("status", f"⏳ {int(remaining)}s remaining"))

        if zip_var.get() and generated_files:
            create_zip(generated_files)

        ui_queue.put(("status", "✅ Completed"))

    finally:
        ui_queue.put(("enable_ui", None))

# ================= CONVERSIONS =================

def svg_to_png(path):
    ensure_output()
    base = Path(path).stem

    out = safe_output_path(os.path.join(output_folder, f"{base}.png"))

    svg2png(url=path, write_to=out)
    return [out]


def svg_to_jpg(path):
    ensure_output()
    base = Path(path).stem

    temp_png = os.path.join(output_folder, f"{base}_temp.png")
    out = safe_output_path(os.path.join(output_folder, f"{base}.jpg"))

    svg2png(url=path, write_to=temp_png)   # MUST come first

    img = Image.open(temp_png).convert("RGB")
    img.save(out, "JPEG", quality=95)

    if os.path.exists(temp_png):
        os.remove(temp_png)

    return [out]


def svg_to_webp(path):
    ensure_output()
    base = Path(path).stem

    temp_png = os.path.join(output_folder, f"{base}_temp.png")
    out = safe_output_path(os.path.join(output_folder, f"{base}.webp"))

    svg2png(url=path, write_to=temp_png)

    img = Image.open(temp_png).convert("RGBA")
    img.save(out, "WEBP", quality=95)

    if os.path.exists(temp_png):
        os.remove(temp_png)

    return [out]

def svg_to_ico(path):
    ensure_output()
    base = Path(path).stem

    ico_out = safe_output_path(os.path.join(output_folder, f"{base}.ico"))

    sizes = sorted(set(get_selected_sizes()))
    if not sizes:
        raise RuntimeError("Select at least one ICO size")

    max_size = max(sizes)

    # ⚠️ render bigger for high quality
    render_size = max(max_size * 6, 2048)

    temp_png = os.path.join(output_folder, f"{base}_temp.png")

    svg2png(
        url=path,
        write_to=temp_png,
        output_width=render_size,
        output_height=render_size
    )

    img = Image.open(temp_png).convert("RGBA")

    # -------------------------
    # FIX 1: clamp 512 handling
    # -------------------------
    safe_sizes = []
    for s in sizes:
        if s > 256:
            safe_sizes.append(256)   # ICO safe limit
        else:
            safe_sizes.append(s)

    # -------------------------
    # build ICO
    # -------------------------
    img.save(
        ico_out,
        format="ICO",
        sizes=[(s, s) for s in safe_sizes]
    )

    outputs = [ico_out]

    # -------------------------
    # FIX 2: export real 512 separately as PNG fallback
    # -------------------------
    if 512 in sizes:
        icon_512 = img.resize((512, 512), Image.Resampling.LANCZOS)

        out_512 = safe_output_path(
            os.path.join(output_folder, f"{base}_512.png")
        )
        icon_512.save(out_512, "PNG")

        outputs.append(out_512)

    # -------------------------
    # single-size ICOs (safe)
    # -------------------------
    for s in sizes:
        resized = img.resize((min(s, 256), min(s, 256)), Image.Resampling.LANCZOS)

        ico_size_out = safe_output_path(
            os.path.join(output_folder, f"{base}_{s}.ico")
        )

        resized.save(ico_size_out, format="ICO", sizes=[(min(s, 256), min(s, 256))])
        outputs.append(ico_size_out)

    if os.path.exists(temp_png):
        os.remove(temp_png)

    return outputs

def svg_to_base64(path):

    ensure_output()
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()

    base = Path(path).stem

    out = safe_output_path(os.path.join(output_folder, f"{base}.txt"))

    with open(out, "w") as f:
        f.write(encoded)

    return [out]


def svg_to_data_uri(path):

    ensure_output()
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()

    data_uri = f"data:image/svg+xml;base64,{encoded}"

    base = Path(path).stem

    out = safe_output_path(os.path.join(output_folder, f"{base}_data_uri.txt"))

    with open(out, "w") as f:
        f.write(data_uri)

    return [out]


def svg_transparency_creator(path):

    ensure_output()
    base = Path(path).stem

    out = safe_output_path(os.path.join(output_folder, f"{base}_transparent.png"))

    svg2png(url=path, write_to=out)
    return [out]


def svg_opacity_changer(path, opacity=0.5):

    ensure_output()
    base = Path(path).stem

    out = safe_output_path(os.path.join(output_folder, f"{base}_opacity.svg"))

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # inject opacity into root svg
    content = content.replace("<svg", f'<svg style="opacity:{opacity}"', 1)

    with open(out, "w", encoding="utf-8") as f:
        f.write(content)

    return [out]


def svg_noise_filter(path):
    ensure_output()
    base = Path(path).stem

    out = safe_output_path(os.path.join(output_folder, f"{base}_noise.svg"))

    noise_filter = """
    <filter id="noise">
        <feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves="2"/>
        <feDisplacementMap in="SourceGraphic" scale="2"/>
    </filter>
    """

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if "</svg>" not in content:
        raise RuntimeError("Invalid SVG file")

    # inject filter before closing tag

    # safer svg tag edit
    if "<defs>" in content:
        content = content.replace("<defs>", "<defs>" + noise_filter, 1)
    else:
        content = content.replace("<svg", "<svg><defs>" + noise_filter + "</defs>", 1)

    with open(out, "w", encoding="utf-8") as f:
        f.write(content)

    return [out]

def svg_to_pdf(path):

    ensure_output()
    base = Path(path).stem

    out = safe_output_path(os.path.join(output_folder, f"{base}.pdf"))

    svg2pdf(url=path, write_to=out)
    return [out]

# ================= ZIP =================

def create_zip(files):
    zip_path = safe_output_path(os.path.join(output_folder, "output.zip"))
    with zipfile.ZipFile(zip_path, 'w') as z:
        for f in files:
            z.write(f, os.path.basename(f))

# ================= START =================

def start_task():
    if not file_paths:
        messagebox.showwarning("Warning", "Select files first")
        return
    if not output_folder:
        messagebox.showerror("Error", "No output folder selected")
        return

    start_btn.config(state="disabled")
    select_btn.config(state="disabled")
    out_btn.config(state="disabled")
    pause_btn.config(state="disabled")
    resume_btn.config(state="disabled")

    stop_event.clear()
    pause_event.set()

    threading.Thread(target=process_task, daemon=True).start()

# ================= QUEUE =================

def process_queue():
    try:
        while True:
            cmd, data = ui_queue.get_nowait()

            if cmd == "progress":
                progress_var.set(data)

            elif cmd == "file_status":
                file_status_label.config(text=f"Processing: {data}")

            elif cmd == "status":
                status_label.config(text=data)

                if "Stopped" in data:
                    start_btn.config(state="normal")
                    select_btn.config(state="normal")
                    out_btn.config(state="normal")
                    pause_btn.config(state="normal")
                    resume_btn.config(state="normal")

            elif cmd == "error":
                messagebox.showerror("Error", data)

            elif cmd == "enable_ui":
                start_btn.config(state="normal")
                select_btn.config(state="normal")
                out_btn.config(state="normal")
                pause_btn.config(state="normal")
                resume_btn.config(state="normal")

    except Empty:
        pass

    app.after(100, process_queue)

def clear_all():
    global file_paths, output_folder

    # Reset data
    file_paths = []
    output_folder = ""

    # Reset progress
    progress_var.set(0)

    # Reset status labels
    status_label.config(text="Ready")
    file_status_label.config(text="File progress")

    # Reset UI labels
    update_ui()

    # Clear preview
    preview_canvas.delete("all")
    preview_images.clear()

    # Reset file selection button state (optional safety)
    start_btn.config(state="normal")
    select_btn.config(state="normal")
    out_btn.config(state="normal")

    # Reset mode if you want (optional)
    # mode_var.set("ICO → PNG")

    # Reset checkboxes (optional)
    for var in size_vars.values():
        var.set(True)

    # Reset control events
    stop_event.clear()
    pause_event.set()

def show_about():
    import webbrowser

    about = tk.Toplevel(app)
    about.title(f"About {APP_NAME}")
    about.geometry("480x590")
    about.resizable(False, False)

    # Center window
    app.update_idletasks()
    x = app.winfo_x() + (app.winfo_width() // 2) - 240
    y = app.winfo_y() + (app.winfo_height() // 2) - 280
    about.geometry(f"480x590+{x}+{y}")

    # Icon
    try:
        about.iconbitmap(resource_path("logo.ico"))
    except:
        pass

    about.transient(app)
    about.grab_set()

    container = tb.Frame(about, padding=15)
    container.pack(fill="both", expand=True)

    # ===== HEADER =====
    tb.Label(container, text=APP_NAME, font=("Segoe UI", 20, "bold")).pack(pady=(0, 5))
    tb.Label(
        container,
        text=f"Version {APP_VERSION} • PRO Edition",
        foreground="#9ca3af"
    ).pack(pady=(0, 10))

    # ===== DESCRIPTION =====
    tb.Label(
        container,
        text=(
            "A modern all-in-one SVG processing toolkit designed for speed, precision, and simplicity.\n\n"
            "Easily convert and transform SVG files into PNG, JPG, WebP, ICO, Base64, and Data URI formats. "
            "Includes advanced tools like transparency handling, opacity editing, noise effects, batch processing, "
            "drag & drop support, and background multithreaded processing with a clean, responsive UI."
        ),
        justify="center",
        wraplength=430
    ).pack(pady=(0, 15))

    # ===== FEATURES =====
    tb.Label(
        container,
        text=(
            "🚀 Core Features\n\n"
            "✔ SVG → PNG / JPG / WebP / ICO\n"
            "✔ SVG → Base64 / Data URI\n"
            "✔ Transparency processing\n"
            "✔ Opacity adjustments\n"
            "✔ SVG noise effects\n"
            "✔ Batch processing support\n"
            "✔ Pause / Resume / Stop control\n"
            "✔ Drag & drop file support\n"
            "✔ Multi-threaded processing\n"
            "✔ ZIP export support\n"
            "✔ Fully offline & secure"
        ),
        justify="left"
    ).pack(pady=(0, 15))

    # ===== LINKS =====
    def open_gumroad():
        webbrowser.open(GUMROAD_URL)

    tb.Label(container, text="💎 More Tools & Pro Apps", foreground="#9ca3af").pack(pady=(5, 5))

    links = tb.Frame(container)
    links.pack(pady=(0, 10))

    tb.Button(
        links,
        text="Buy Now",
        bootstyle="success",
        command=lambda: webbrowser.open("https://matetools.gumroad.com/l/wdzph")
    ).pack(side="left", padx=5)

    tb.Button(
        links,
        text="Visit Store",
        bootstyle="warning",
        command=lambda: webbrowser.open(GUMROAD_URL)
    ).pack(side="left", padx=5)

    # ===== FOOTER =====
    tb.Separator(container).pack(fill="x", pady=10)

    tb.Label(
        container,
        text="© 2026 Mate Technologies",
        foreground="#6b7280"
    ).pack(pady=(5, 2))

    tb.Label(
        container,
        text="Built with Python • Tkinter • ttkbootstrap",
        foreground="#6b7280"
    ).pack()

    # ===== CLOSE =====
    tb.Button(
        container,
        text="Close",
        bootstyle="secondary",
        command=about.destroy
    ).pack(pady=10)

# =================== MENU ===================

menubar = tb.Menu(app)

# -------- FILE MENU --------
file_menu = tb.Menu(menubar, tearoff=0)
file_menu.add_command(label="🆕 New Project", command=clear_all)
file_menu.add_command(label="📄 Select Files", command=select_files)
file_menu.add_command(label="📂 Set Output Folder", command=select_output)
file_menu.add_separator()
file_menu.add_command(label="🚀 Start", command=start_task)
file_menu.add_separator()
file_menu.add_command(label="Exit", command=app.quit)

menubar.add_cascade(label="File", menu=file_menu)

# -------- HELP MENU --------
help_menu = tb.Menu(menubar, tearoff=0)
help_menu.add_command(label="About", command=show_about)

menubar.add_cascade(label="Help", menu=help_menu)

# -------- APPLY --------
app.config(menu=menubar)

try:
    icon_img = Image.open(resource_path("logo.ico"))
    icon = ImageTk.PhotoImage(icon_img)
    app.iconphoto(True, icon)
except Exception as e:
    print(f"[Icon Error] {e}")

# ================= UI =================

header = tb.Frame(app, padding=10)
header.pack(fill="x")

tb.Label(header, text=APP_NAME, font=("Segoe UI", 22, "bold")).pack()

# Subtitle
tb.Label(
    header,
    text="Convert and process SVG files with speed and precision.",
    font=("Segoe UI", 10, "italic"),
    foreground="#9ca3af",
    wraplength=900,
    justify="center"
).pack(pady=(0, 5))

main = tb.Frame(app, padding=20)
main.pack(fill="both", expand=True)

# DROP

drop_frame = tb.Frame(main, bootstyle="secondary", padding=30)
drop_frame.pack(fill="x", pady=10)

drop_label = tb.Label(drop_frame, text="Drop SVG files here or click to upload",
                      font=("Segoe UI", 14, "bold"), anchor="center")
drop_label.pack(fill="x")

select_btn = tb.Button(drop_frame, text="📁 Select Files", command=select_files)
select_btn.pack(pady=10)

drop_frame.drop_target_register(DND_FILES)
drop_frame.dnd_bind("<<Drop>>", on_drop)

# PREVIEW

preview_frame = tb.Frame(main)
preview_frame.pack(fill="x", pady=10)

preview_canvas = tk.Canvas(preview_frame, height=120)
preview_canvas.pack(fill="x", expand=True)

# MODE

mode_box = tb.Labelframe(main, text="Mode", padding=10)
mode_box.pack(fill="x", pady=10)

tb.Combobox(mode_box, textvariable=mode_var,
            values=[
                "SVG → PNG",
                "SVG → JPG",
                "SVG → WebP",
                "SVG → ICO",
                "SVG → Base64",
                "SVG → Data URI",
                "SVG → Transparent PNG",
                "SVG Opacity Changer",
                "SVG Noise Filter",
                "SVG → PDF"
            ]).pack(fill="x")

# ICO SIZE SELECTOR

size_box = tb.Labelframe(main, text="ICO Sizes", padding=10)
size_box.pack(fill="x", pady=10)

for s in ICON_SIZES_DEFAULT:
    var = tk.BooleanVar(value=True)
    size_vars[s] = var
    tb.Checkbutton(size_box, text=str(s), variable=var).pack(side="left", padx=5)

def toggle_size_box(*args):
    state = "normal" if mode_var.get() == "SVG → ICO" else "disabled"

    for child in size_box.winfo_children():
        try:
            child.configure(state=state)
        except:
            pass  # some widgets may not support state

# 🔁 Listen for mode changes
mode_var.trace_add("write", toggle_size_box)

# 🚀 Run once at startup
toggle_size_box()

# ACTIONS

actions = tb.Frame(main)
actions.pack(fill="x", pady=10)

out_btn = tb.Button(actions, text="📂 Output Folder", command=select_output)
out_btn.pack(side="left", padx=5)

tb.Checkbutton(actions, text="📦 ZIP Export", variable=zip_var).pack(side="left", padx=5)

start_btn = tb.Button(actions, text="🚀 Start", bootstyle="success", command=start_task)
start_btn.pack(side="right", padx=5)

tb.Button(actions, text="⛔ Stop", bootstyle="danger", command=stop_task).pack(side="right", padx=5)

pause_btn = tb.Button(actions, text="⏸ Pause", command=pause_task)
pause_btn.pack(side="right", padx=5)

resume_btn = tb.Button(actions, text="▶ Resume", command=resume_task)
resume_btn.pack(side="right", padx=5)

# ================= PROGRESS =================

progress = tb.Progressbar(main, variable=progress_var)
progress.pack(fill="x", pady=5)

file_status_label = tb.Label(main, text="File progress")
file_status_label.pack(anchor="w")

status_label = tb.Label(main, text="Ready")
status_label.pack(anchor="w")

out_label = tb.Label(main, text="No output folder selected")
out_label.pack(anchor="w")

# ================= START =================

app.after(100, process_queue)
app.mainloop()
