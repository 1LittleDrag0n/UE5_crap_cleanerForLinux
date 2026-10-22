#!/usr/bin/env python3
"""UE Cleaner - scan an Unreal Engine install and remove platform files you don't need.

Run:   python3 ue_cleaner.py [/path/to/Linux_Unreal_Engine_X.Y.Z]
Needs: Python 3 + PySide6   (Arch / CachyOS:  sudo pacman -S --needed pyside6)
Native Qt app: runs on Wayland, follows your Plasma theme, uses the KDE folder picker.

By default nothing is deleted: selected folders are MOVED to a holding folder next to
the engine (same drive, so it's instant). Test the editor, then empty the holding folder
to actually free the space - or restore it if something broke.
"""
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass, field

try:
    from PySide6.QtCore import Qt, QThread, QTimer, Signal
    from PySide6.QtGui import QColor, QIcon, QPalette
    from PySide6.QtWidgets import (
        QApplication, QButtonGroup, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
        QMainWindow, QMessageBox, QProgressBar, QPushButton, QRadioButton, QStyleFactory,
        QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
    )
except ImportError:
    sys.exit("PySide6 is missing. Install it with:  sudo pacman -S --needed pyside6")

# --------------------------------------------------------------------------- what can go
# key, label, folder names on disk, what you lose
PLATFORMS = [
    ("android", "Android", ("Android",), "Packaging games for Android"),
    ("linuxarm64", "Linux ARM64", ("LinuxArm64",), "Packaging for ARM Linux only - normal x64 Linux is untouched"),
    ("windows", "Windows (Win64)", ("Win64", "Win32"), "Nothing - a Linux engine can't package Windows builds"),
    ("mac", "macOS", ("Mac",), "Packaging for Mac"),
    ("ios", "iOS", ("IOS",), "Packaging for iPhone / iPad"),
    ("tvos", "tvOS", ("TVOS",), "Packaging for Apple TV"),
    ("visionos", "visionOS", ("VisionOS",), "Packaging for Apple Vision Pro"),
    ("hololens", "HoloLens", ("HoloLens",), "Packaging for HoloLens"),
]
NAME_TO_KEY = {name: key for key, _label, names, _note in PLATFORMS for name in names}

# key, label, paths relative to engine root, what you lose
EXTRAS = [
    ("ddc", "Engine cache (DerivedDataCache)", ("Engine/DerivedDataCache",),
     "Rebuilds itself - first launch afterwards is slower"),
    ("saved", "Engine logs & crash dumps (Engine/Saved)", ("Engine/Saved",),
     "Old logs and crash reports"),
    ("templates", "Templates, samples & feature packs", ("Templates", "Samples", "FeaturePacks"),
     "New Project browser loses its templates"),
]
DEBUG_NOTE = "Readable crash callstacks - keep these if you're debugging crashes"

GROUP_PLATFORMS = "Other platforms (safe if you only target Linux)"
GROUP_EXTRAS = "Extras (read what you lose first)"
HOLD_PREFIX = "_slim_trash_"

_BIN_RE = re.compile(r"(?:^|/)Binaries/(?:ThirdParty/.+/)?[^/]+$")
_INT_RE = re.compile(r"(?:^|/)Intermediate/Build/[^/]+$")


@dataclass
class Item:
    key: str
    label: str
    group: str
    note: str
    paths: list = field(default_factory=list)
    size: int = 0
    checked: bool = False


# --------------------------------------------------------------------------- core logic
def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def disk_usage(path):
    """Bytes actually used on disk (like du), never following symlinks."""
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    total = st.st_blocks * 512
    if not stat.S_ISDIR(st.st_mode):
        return total
    for dirpath, dirnames, filenames in os.walk(path):
        for name in dirnames + filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_blocks * 512
            except OSError:
                pass
    return total


def resolve_root(path):
    """Return the engine root (folder containing Engine/Binaries) or None."""
    if not path or not path.strip():
        return None
    path = os.path.realpath(os.path.expanduser(path.strip()))
    if os.path.basename(path) == "Engine" and os.path.isdir(os.path.join(path, "Binaries")):
        path = os.path.dirname(path)
    if not os.path.isdir(os.path.join(path, "Engine", "Binaries")):
        return None
    return path


def holding_dir(root):
    return os.path.join(os.path.dirname(root), HOLD_PREFIX + os.path.basename(root))


def platform_key(rel):
    """Which platform a folder (path relative to engine root, '/' separated) belongs to.

    Only matches folders that are safe to remove: prebuilt binaries, prebuilt static libs,
    third-party libs and platform extensions. Source-code folders are never matched.
    """
    name = rel.rsplit("/", 1)[-1]
    key = NAME_TO_KEY.get(name)
    if key is None:
        return None
    if rel == "Engine/Platforms/" + name:
        return key
    if _BIN_RE.search(rel) or _INT_RE.search(rel):
        return key
    if rel.startswith("Engine/Source/ThirdParty/") and "/lib/" in rel.lower():
        return key
    return None


def scan(root, report=lambda msg: None):
    platforms = {key: Item(key, label, GROUP_PLATFORMS, note, checked=True)
                 for key, label, _names, note in PLATFORMS}
    debug = Item("debug", "Debug symbols (*.debug files)", GROUP_EXTRAS, DEBUG_NOTE)

    walked = 0
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, "Engine")):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        keep = []
        for d in dirnames:
            full = os.path.join(dirpath, d)
            key = None if os.path.islink(full) else platform_key(f"{rel_dir}/{d}")
            if key:
                platforms[key].paths.append(full)
                platforms[key].size += disk_usage(full)
            else:
                keep.append(d)
        dirnames[:] = keep  # don't descend into folders already claimed

        for f in filenames:
            if f.endswith(".debug"):
                p = os.path.join(dirpath, f)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    debug.paths.append(p)
                    debug.size += st.st_blocks * 512

        walked += 1
        if walked % 300 == 0:
            report(f"Scanning... {rel_dir}")

    extras = [debug]
    for key, label, rels, note in EXTRAS:
        it = Item(key, label, GROUP_EXTRAS, note)
        for r in rels:
            p = os.path.join(root, r)
            if os.path.lexists(p):
                it.paths.append(p)
                it.size += disk_usage(p)
        extras.append(it)

    items = list(platforms.values()) + extras
    for it in items:
        if not it.paths:
            it.checked = False
    return items


def merge_move(src, dst, overwrite):
    """Move src to dst, merging into dst if both are folders."""
    if (os.path.isdir(src) and not os.path.islink(src)
            and os.path.isdir(dst) and not os.path.islink(dst)):
        for name in os.listdir(src):
            merge_move(os.path.join(src, name), os.path.join(dst, name), overwrite)
        try:
            os.rmdir(src)
        except OSError:
            pass  # something was skipped and is still inside
        return
    if os.path.lexists(dst):
        if not overwrite:
            return
        if os.path.isdir(dst) and not os.path.islink(dst):
            shutil.rmtree(dst)
        else:
            os.remove(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)  # plain rename when on the same drive


def _inside(path, root):
    try:
        return os.path.commonpath([os.path.realpath(path), root]) == root and os.path.realpath(path) != root
    except ValueError:
        return False


def clean(root, items, mode, progress=lambda i, n, text: None):
    """mode: 'move' (to holding folder) or 'delete' (permanent). Returns list of errors."""
    paths = [p for it in items for p in it.paths]
    hold = holding_dir(root)
    errors = []
    for i, p in enumerate(paths, 1):
        rel = os.path.relpath(p, root)
        progress(i, len(paths), rel)
        if not os.path.lexists(p):
            continue
        if not _inside(p, root):
            errors.append(f"{rel}: outside the engine folder, skipped")
            continue
        try:
            if mode == "move":
                merge_move(p, os.path.join(hold, rel), overwrite=True)
            elif os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
        except OSError as e:
            errors.append(f"{rel}: {e.strerror or e}")
    return errors


def restore(root):
    hold = holding_dir(root)
    errors = []
    if not os.path.isdir(hold):
        return errors
    for name in os.listdir(hold):
        try:
            merge_move(os.path.join(hold, name), os.path.join(root, name), overwrite=False)
        except OSError as e:
            errors.append(f"{name}: {e.strerror or e}")
    try:
        os.rmdir(hold)
    except OSError:
        if os.path.isdir(hold):
            errors.append("Some files stayed in the holding folder because the engine "
                          "already has a file with the same name.")
    return errors


def editor_running(root):
    """True if a process is running from inside this engine folder."""
    prefix = root.rstrip("/") + "/"
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            exe = os.readlink(f"/proc/{pid}/exe")
        except OSError:
            continue
        if exe.startswith(prefix):
            return True
    return False


# --------------------------------------------------------------------------- UI (Qt)
CHECKED = Qt.CheckState.Checked
UNCHECKED = Qt.CheckState.Unchecked
PARTIAL = Qt.CheckState.PartiallyChecked
ROLE = Qt.ItemDataRole.UserRole
F_ENABLED = Qt.ItemFlag.ItemIsEnabled
F_CHECK = Qt.ItemFlag.ItemIsUserCheckable


class Worker(QThread):
    """Runs fn(worker) off the UI thread; fn can emit progress/status through the worker."""
    progress = Signal(int, int, str)
    status = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            result = self.fn(self)
        except Exception as e:  # report anything unexpected instead of dying silently
            self.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.succeeded.emit(result)


class MainWindow(QMainWindow):
    def __init__(self, start_path=""):
        super().__init__()
        self.items = []
        self.engine = None
        self.hold_size = 0
        self.busy = False
        self._updating = False

        self.setWindowTitle("UE Cleaner")
        self.setWindowIcon(QIcon.fromTheme("edit-clear-all"))
        self.resize(1020, 640)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # top: folder picker
        top = QHBoxLayout()
        top.addWidget(QLabel("Unreal Engine folder:"))
        self.path_edit = QLineEdit(start_path)
        self.path_edit.setPlaceholderText("/Linux_Unreal_Engine_5.8.4")
        self.path_edit.returnPressed.connect(self.start_scan)
        top.addWidget(self.path_edit, 1)
        self.btn_browse = QPushButton(QIcon.fromTheme("document-open-folder"), "Browse…")
        self.btn_browse.clicked.connect(self.browse)
        top.addWidget(self.btn_browse)
        self.btn_scan = QPushButton(QIcon.fromTheme("view-refresh"), "Scan")
        self.btn_scan.clicked.connect(self.start_scan)
        top.addWidget(self.btn_scan)
        layout.addLayout(top)

        # middle: checklist
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["What to remove", "Size", "What you lose"])
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.tree.setColumnWidth(0, 360)
        self.tree.itemChanged.connect(self.on_item_changed)
        layout.addWidget(self.tree, 1)

        # summary + mode
        row1 = QHBoxLayout()
        self.sel_label = QLabel("Pick a folder and press Scan.")
        font = self.sel_label.font()
        font.setBold(True)
        self.sel_label.setFont(font)
        row1.addWidget(self.sel_label)
        row1.addStretch(1)
        self.rb_move = QRadioButton("Move to holding folder (safe, can restore)")
        self.rb_delete = QRadioButton("Delete permanently")
        self.rb_move.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.rb_move)
        self.mode_group.addButton(self.rb_delete)
        row1.addWidget(self.rb_move)
        row1.addWidget(self.rb_delete)
        layout.addLayout(row1)

        # holding folder + actions
        row2 = QHBoxLayout()
        self.hold_label = QLabel()
        self.hold_label.setWordWrap(True)
        self.hold_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row2.addWidget(self.hold_label, 1)
        self.btn_restore = QPushButton(QIcon.fromTheme("edit-undo"), "Restore everything")
        self.btn_restore.clicked.connect(self.do_restore)
        self.btn_empty = QPushButton(QIcon.fromTheme("edit-delete"), "Empty holding folder")
        self.btn_empty.clicked.connect(self.do_empty)
        self.btn_clean = QPushButton(QIcon.fromTheme("edit-clear-all"), "Clean selected")
        self.btn_clean.clicked.connect(self.do_clean)
        self.btn_clean.setDefault(True)
        for b in (self.btn_restore, self.btn_empty, self.btn_clean):
            row2.addWidget(b)
        layout.addLayout(row2)

        # status bar with progress
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().showMessage("Ready.")

        self.update_hold()
        self.refresh_buttons()
        if start_path:
            QTimer.singleShot(0, self.start_scan)

    # ---- state
    def refresh_buttons(self):
        idle = not self.busy
        has_sel = any(i.checked and i.paths for i in self.items)
        has_hold = self.hold_size > 0
        self.btn_browse.setEnabled(idle)
        self.btn_scan.setEnabled(idle)
        self.path_edit.setEnabled(idle)
        self.tree.setEnabled(idle)
        self.btn_clean.setEnabled(idle and has_sel)
        self.btn_restore.setEnabled(idle and has_hold)
        self.btn_empty.setEnabled(idle and has_hold)

    def set_busy(self, busy):
        self.busy = busy
        if busy:
            self.progress.setRange(0, 0)  # animated "working"
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
        self.refresh_buttons()

    def run_bg(self, fn, on_done):
        self.set_busy(True)
        w = Worker(fn, self)
        w.status.connect(self.statusBar().showMessage)
        w.progress.connect(self.on_progress)
        w.succeeded.connect(lambda result: self._finish(on_done, result))
        w.failed.connect(self.on_failed)
        w.finished.connect(w.deleteLater)
        w.start()

    def _finish(self, on_done, result):
        self.set_busy(False)
        on_done(result)

    def on_progress(self, i, n, text):
        self.progress.setRange(0, max(n, 1))
        self.progress.setValue(i)
        self.statusBar().showMessage(f"{i}/{n}   {text}")

    def on_failed(self, message):
        self.set_busy(False)
        self.statusBar().showMessage("Something went wrong.")
        QMessageBox.critical(self, "UE Cleaner", message)

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, "Still working",
                                    "Please wait until the current job finishes before closing.")
            event.ignore()
        else:
            event.accept()

    def guard_editor(self):
        if self.engine and editor_running(self.engine):
            QMessageBox.warning(self, "Unreal Editor is running",
                                "Close Unreal Editor (and anything else running from this engine) first.")
            return False
        return True

    # ---- list
    def render(self):
        self._updating = True
        self.tree.clear()
        groups = {}
        for idx, it in enumerate(self.items):
            g = groups.get(it.group)
            if g is None:
                g = QTreeWidgetItem(self.tree, [it.group])
                font = g.font(0)
                font.setBold(True)
                g.setFont(0, font)
                g.setFirstColumnSpanned(True)
                g.setExpanded(True)
                groups[it.group] = g
            row = QTreeWidgetItem(g, [it.label, human(it.size) if it.paths else "not present", it.note])
            row.setData(0, ROLE, idx)
            row.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if it.paths:
                row.setFlags(F_ENABLED | F_CHECK)
                row.setCheckState(0, CHECKED if it.checked else UNCHECKED)
                rels = [os.path.relpath(p, self.engine) for p in it.paths]
                tip = "\n".join(rels[:25]) + (f"\n… and {len(rels) - 25} more" if len(rels) > 25 else "")
                row.setToolTip(0, f"{len(rels)} folder(s)/file(s):\n{tip}")
            else:
                row.setFlags(Qt.ItemFlag.NoItemFlags)  # greyed out, not clickable
        self.sync_groups()
        self._updating = False
        self.update_summary()

    def sync_groups(self):
        for gi in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(gi)
            avail = [self.items[g.child(i).data(0, ROLE)] for i in range(g.childCount())]
            avail = [it for it in avail if it.paths]
            if not avail:
                g.setFlags(F_ENABLED)
                g.setData(0, Qt.ItemDataRole.CheckStateRole, None)
                continue
            g.setFlags(F_ENABLED | F_CHECK)
            n = sum(1 for it in avail if it.checked)
            g.setCheckState(0, CHECKED if n == len(avail) else UNCHECKED if n == 0 else PARTIAL)

    def on_item_changed(self, item, column):
        if self._updating or column != 0:
            return
        self._updating = True
        idx = item.data(0, ROLE)
        if idx is None:  # group row: apply to all available children
            value = item.checkState(0) == CHECKED
            for i in range(item.childCount()):
                child = item.child(i)
                it = self.items[child.data(0, ROLE)]
                if it.paths:
                    it.checked = value
                    child.setCheckState(0, CHECKED if value else UNCHECKED)
        else:
            self.items[idx].checked = item.checkState(0) == CHECKED
        self.sync_groups()
        self._updating = False
        self.update_summary()

    def update_summary(self):
        sel = [i for i in self.items if i.checked and i.paths]
        if self.items:
            n = len(sel)
            self.sel_label.setText(f"Selected: {human(sum(i.size for i in sel))}  ({n} item{'s' if n != 1 else ''})")
        self.refresh_buttons()

    def update_hold(self):
        if self.engine and self.hold_size:
            self.hold_label.setText(
                f"Holding folder: {human(self.hold_size)} waiting — test the editor, then Empty it to "
                f"free the space.\n{holding_dir(self.engine)}")
        else:
            self.hold_label.setText("Holding folder: empty")
        self.refresh_buttons()

    # ---- actions
    def browse(self):
        start = self.path_edit.text().strip()
        if not os.path.isdir(start):
            start = os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Select the Unreal Engine folder", start)
        if path:
            self.path_edit.setText(path)
            self.start_scan()

    def start_scan(self):
        if self.busy:
            return
        root = resolve_root(self.path_edit.text())
        if not root:
            QMessageBox.warning(self, "Not an Unreal Engine folder",
                                "Pick the folder that contains the 'Engine' folder, for example:\n"
                                "/mnt/partions/unrealEngines/Linux_Unreal_Engine_5.8.4")
            return
        self.engine = root
        self.path_edit.setText(root)
        self.statusBar().showMessage("Scanning… (big engines take a minute)")

        def job(worker):
            items = scan(root, worker.status.emit)
            hold = holding_dir(root)
            return items, (disk_usage(hold) if os.path.isdir(hold) else 0)

        self.run_bg(job, self.scan_done)

    def scan_done(self, result):
        self.items, self.hold_size = result
        self.render()
        self.update_hold()
        found = sum(i.size for i in self.items if i.group == GROUP_PLATFORMS)
        self.statusBar().showMessage(f"Scan complete — {human(found)} of other-platform files found.")

    def ask(self, title, text, dangerous=False):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning if dangerous else QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No if dangerous else QMessageBox.StandardButton.Yes)
        return box.exec() == QMessageBox.StandardButton.Yes

    def do_clean(self):
        sel = [i for i in self.items if i.checked and i.paths]
        if not sel or not self.guard_editor():
            return
        lines = "\n".join(f"  • {i.label}  ({human(i.size)})" for i in sel)
        total = human(sum(i.size for i in sel))
        mode = "move" if self.rb_move.isChecked() else "delete"
        if mode == "move":
            ok = self.ask("Move to holding folder?",
                          f"Move {total} to the holding folder?\n\n{lines}\n\n"
                          "Nothing is deleted yet. Test the editor afterwards, then press "
                          "'Empty holding folder' to free the space (or 'Restore everything').")
        else:
            ok = self.ask("Delete permanently?",
                          f"PERMANENTLY delete {total}?\n\n{lines}\n\nThis cannot be undone.", dangerous=True)
        if not ok:
            return
        engine = self.engine
        done_msg = "Moved to holding folder." if mode == "move" else f"Deleted {total}."
        self.run_bg(lambda w: clean(engine, sel, mode, w.progress.emit),
                    lambda errs: self.after_action(errs, done_msg))

    def do_restore(self):
        if not self.guard_editor():
            return
        if not self.ask("Restore everything?", "Put everything from the holding folder back into the engine?"):
            return
        engine = self.engine
        self.statusBar().showMessage("Restoring…")
        self.run_bg(lambda w: restore(engine), lambda errs: self.after_action(errs, "Restored."))

    def do_empty(self):
        hold = holding_dir(self.engine)
        size = human(self.hold_size)
        if not self.ask("Empty holding folder?",
                        f"Permanently delete {size} in\n{hold}?\n\n"
                        "Only do this after the editor and packaging work fine.", dangerous=True):
            return
        self.statusBar().showMessage("Deleting holding folder…")

        def job(worker):
            shutil.rmtree(hold)
            return []

        self.run_bg(job, lambda errs: self.after_action(errs, f"Freed {size}."))

    def after_action(self, errors, message):
        if errors:
            shown = "\n".join(errors[:15]) + (f"\n…and {len(errors) - 15} more" if len(errors) > 15 else "")
            QMessageBox.warning(self, "Finished with problems", shown)
        self.start_scan()
        self.statusBar().showMessage(f"{message} Rescanning…")


def _kde_colors():
    """Read the Plasma colour scheme and icon theme from ~/.config/kdeglobals (if present)."""
    import configparser
    cfg_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    try:
        cp.read(os.path.join(cfg_home, "kdeglobals"), encoding="utf-8")
    except (configparser.Error, OSError):
        return None, None

    def color(section, key):
        try:
            parts = [int(x) for x in cp.get(section, key).split(",")[:3]]
            return QColor(*parts)
        except (configparser.Error, ValueError, TypeError):
            return None

    icon_theme = cp.get("Icons", "Theme", fallback=None)
    if not cp.has_section("Colors:Window"):
        return None, icon_theme
    pal = QPalette()
    R, G = QPalette.ColorRole, QPalette.ColorGroup
    pairs = [
        (R.Window, "Colors:Window", "BackgroundNormal"), (R.WindowText, "Colors:Window", "ForegroundNormal"),
        (R.Base, "Colors:View", "BackgroundNormal"), (R.AlternateBase, "Colors:View", "BackgroundAlternate"),
        (R.Text, "Colors:View", "ForegroundNormal"), (R.PlaceholderText, "Colors:View", "ForegroundInactive"),
        (R.Button, "Colors:Button", "BackgroundNormal"), (R.ButtonText, "Colors:Button", "ForegroundNormal"),
        (R.Highlight, "Colors:Selection", "BackgroundNormal"),
        (R.HighlightedText, "Colors:Selection", "ForegroundNormal"),
        (R.ToolTipBase, "Colors:Tooltip", "BackgroundNormal"), (R.ToolTipText, "Colors:Tooltip", "ForegroundNormal"),
        (R.Link, "Colors:View", "ForegroundLink"),
    ]
    for role, section, key in pairs:
        c = color(section, key)
        if c is not None:
            pal.setColor(role, c)
    dim = color("Colors:Window", "ForegroundInactive")
    if dim is not None:
        for role in (R.WindowText, R.Text, R.ButtonText):
            pal.setColor(G.Disabled, role, dim)
    return pal, icon_theme


def apply_look(app):
    """Inside an AppImage the bundled Qt can't load KDE's own Breeze plugin.
    Fall back to Fusion painted with the user's Plasma colours and icon theme."""
    if "breeze" in [k.lower() for k in QStyleFactory.keys()]:
        return  # system Qt with KDE integration: already native, nothing to do
    app.setStyle("Fusion")
    pal, icon_theme = _kde_colors()
    if pal is not None:
        app.setPalette(pal)
    paths = QIcon.themeSearchPaths()
    for p in (os.path.expanduser("~/.local/share/icons"), "/usr/local/share/icons", "/usr/share/icons"):
        if p not in paths and os.path.isdir(p):
            paths.append(p)
    QIcon.setThemeSearchPaths(paths)
    QIcon.setThemeName(icon_theme or "breeze")
    QIcon.setFallbackThemeName("hicolor")


def main():
    app = QApplication(sys.argv)
    apply_look(app)
    app.setApplicationName("UE Cleaner")
    app.setDesktopFileName("ue-cleaner")
    win = MainWindow(sys.argv[1] if len(sys.argv) > 1 else "")
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()