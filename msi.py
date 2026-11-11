"""Package the PyInstaller output (dist/IcePlayer) into a Windows Installer (.msi) file.

Used by ``python build.py --msi``. It uses Python's built-in ``msilib`` (Windows only,
available up to Python 3.12 — the qtapp environment), so no extra tools are needed.

What the installer does
-----------------------
* Shows a setup wizard: Welcome → Install folder (changeable) → progress bar →
  Finished (with a "Launch IcePlayer" checkbox). Opening the MSI again while
  IcePlayer is installed offers Repair or Remove. See ``_add_ui``.
* Installs every file of ``dist/IcePlayer`` to ``C:\\Program Files\\IcePlayer``
  (64-bit, for all users; Windows asks for admin rights once).
* Adds "IcePlayer" shortcuts to the Start menu and the desktop.
* Shows IcePlayer with its icon in Settings → Apps, where it can be uninstalled.
* Upgrades: installing a newer version first removes the older one. Installing an
  older version over a newer one is refused with a message.
* The playlist is not in Program Files: the installed exe saves it to
  ``%LOCALAPPDATA%\\IcePlayer\\IceList.json`` (see ``iceplayer.app.default_state_file``),
  so it survives upgrades and uninstalls.
"""
import warnings
from pathlib import Path

with warnings.catch_warnings():               # msilib is deprecated (removed in 3.13)
    warnings.simplefilter("ignore", DeprecationWarning)
    import msilib
    from msilib import schema, sequence

#: Never change this: it is how Windows recognises any IcePlayer MSI as the same
#: product, so a new version replaces the installed one instead of installing twice.
UPGRADE_CODE = "{9CAFC1C8-8C88-49AE-BF49-4BAA22446111}"

_MSI_NULL_INTEGER = -0x80000000   # what an empty integer field reads as


def _execute(db, sql: str):
    """Run one SQL statement on the MSI database.

    Args:
        db:  Open msilib database.
        sql: Statement such as ``UPDATE ... SET ...``.
    """
    view = db.OpenView(sql)
    view.Execute(None)
    view.Close()


# --- setup wizard ---------------------------------------------------------------
# Sizes are in installer "dialog units". Control attribute bits:
_VISIBLE, _ENABLED, _SUNKEN, _INDIRECT = 1, 2, 4, 8
_TEXT = 0x30003            # visible + enabled + transparent + no "&" shortcuts
_BUTTON = _VISIBLE | _ENABLED
_W, _H = 370, 270          # wizard page size
_BTN_Y, _BTN_W, _BTN_H = 243, 56, 17


def _page(db, name: str, heading: str, body: str, first: str, default: str, cancel: str,
          attr: int = 3):
    """Create a wizard page with a bold heading, a body text and a line above the buttons.

    Args:
        db:       Open msilib database.
        name:     Dialog name (referenced by the UI sequence and NewDialog events).
        heading:  Bold title; may contain properties like ``[ProductName]``.
        body:     Explanation under the heading.
        first / default / cancel: Names of the control that gets focus first, the
                  button that Enter presses, and the button that Esc presses.
        attr:     Dialog attributes (3 = visible + modal, 1 = visible modeless).

    Returns:
        The msilib.Dialog, ready for more controls.
    """
    d = msilib.Dialog(db, name, 50, 50, _W, _H, attr, "[ProductName] [ProductVersion] Setup",
                      first, default, cancel)
    d.text("Heading", 20, 15, 330, 30, _TEXT, r"{\HeadingFont}" + heading)
    d.text("Body", 20, 55, 330, 50, _TEXT, body)
    d.line("BottomLine", 0, _BTN_Y - 9, _W, 0)
    return d


def _buttons(d, back: str | None, nxt: str | None, cancel: str | None,
             next_text: str = "Next >", back_enabled: bool = True, cycle_to: str | None = None):
    """Add the Back / Next / Cancel buttons along the bottom of a page.

    Windows Installer requires the Tab order of a dialog to be ONE closed loop
    (error 2810 otherwise), so the buttons are chained Back → Next → Cancel and the
    last one points back to the first, or to ``cycle_to``.

    Args:
        d:            The page from ``_page``.
        back / nxt / cancel: Control names, or None to leave that button out.
        next_text:    Label of the right-hand button ("Next >", "Install", "Finish").
        back_enabled: False draws a greyed-out Back button (first page).
        cycle_to:     Another control on the page (e.g. the folder box) that Tab goes
                      to after the last button; that control must Tab to the first button.

    Returns:
        ``(back, next, cancel)`` msilib.Control objects (None where left out), so
        callers can attach events.
    """
    order = [n for n in (back, nxt, cancel) if n]          # Tab cycles through them
    following = {n: order[(i + 1) % len(order)] for i, n in enumerate(order)}
    if cycle_to:
        following[order[-1]] = cycle_to
    made = []
    for name, x, text, enabled in ((back, 180, "< Back", back_enabled),
                                   (nxt, 236, next_text, True),
                                   (cancel, 304, "Cancel", True)):
        if name is None:
            made.append(None)
            continue
        attr = _BUTTON if enabled else _VISIBLE
        made.append(d.pushbutton(name, x, _BTN_Y, _BTN_W, _BTN_H, attr, text, following[name]))
    if made[2] is not None:
        made[2].event("SpawnDialog", "CancelDlg")
    return tuple(made)


def _add_ui(db, exe_key: str, icon: Path | None = None):
    """Add the setup wizard and the dialogs Windows Installer itself needs.

    Without these, opening the MSI normally (double-click) fails with error 2803 /
    2726: the standard sequence names FatalError, UserExit and ExitDialog, and
    Windows Installer needs ErrorDlg, CancelDlg and FilesInUse while installing.

    Args:
        db:      Open msilib database.
        exe_key: File key of IcePlayer.exe, started by the "Launch" checkbox.
        icon:    .ico shown in the error box (its "ErrorIcon" control); optional.

    Dialogs added:
        WelcomeDlg → InstallDirDlg → ProgressDlg → ExitDialog   (new install/upgrade)
        MaintenanceDlg (Repair / Remove) → ProgressDlg → ExitDialog   (already installed)
        CancelDlg, ErrorDlg, FilesInUse, UserExit, FatalError    (as needed)
    """
    msilib.add_data(db, "TextStyle", [("DlgFont8", "Tahoma", 8, None, 0),
                                      ("HeadingFont", "Tahoma", 12, None, 1)])
    msilib.add_data(db, "Property", [("DefaultUIFont", "DlgFont8"),
                                     ("ErrorDialog", "ErrorDlg"),
                                     ("_BrowseProperty", "INSTALLDIR"),
                                     ("LAUNCHAPP", "1")])           # checkbox ticked
    msilib.add_data(db, "CheckBox", [("LAUNCHAPP", "1")])
    # 210 = run an installed exe (18) + don't wait (128) + ignore its exit code (64)
    msilib.add_data(db, "CustomAction", [("LaunchApp", 210, exe_key, "")])

    # Which dialog appears when (the three negative ones are standard end screens).
    msilib.add_data(db, "InstallUISequence", [
        ("WelcomeDlg", "NOT Installed", 1230),
        ("MaintenanceDlg", "Installed AND NOT RESUME AND NOT Preselected", 1250),
        ("ProgressDlg", None, 1280)])
    msilib.add_data(db, "AdminUISequence", [("ProgressDlg", None, 1280)])

    # -- Welcome
    d = _page(db, "WelcomeDlg", "Welcome to [ProductName] [ProductVersion] Setup",
              "This will install [ProductName], the music player, on your computer.\r\n\r\n"
              "Click Next to continue, or Cancel to exit Setup.", "Next", "Next", "Cancel")
    _, nxt, _ = _buttons(d, "Back", "Next", "Cancel", back_enabled=False)
    nxt.event("NewDialog", "InstallDirDlg")

    # -- Install folder (the path box edits INSTALLDIR through _BrowseProperty)
    d = _page(db, "InstallDirDlg", "Choose the install folder",
              "[ProductName] will be installed in this folder. To use another folder, "
              "type its path below. Click Install to start.", "FolderEdit", "Next", "Cancel")
    d.text("FolderLabel", 20, 95, 330, 12, _TEXT, "Folder:")   # _TEXT shows "&" literally
    d.control("FolderEdit", "PathEdit", 20, 108, 330, 18,
              _VISIBLE | _ENABLED | _SUNKEN | _INDIRECT, "_BrowseProperty", None, "Back", None)
    back, nxt, _ = _buttons(d, "Back", "Next", "Cancel", next_text="&Install",
                            cycle_to="FolderEdit")   # Tab: folder → Back → Install → Cancel → folder
    back.event("NewDialog", "WelcomeDlg")
    nxt.event("SetTargetPath", "[_BrowseProperty]", ordering=1)
    nxt.event("EndDialog", "Return", ordering=2)

    # -- Already installed: Repair or Remove
    d = _page(db, "MaintenanceDlg", "[ProductName] is already installed",
              "Repair puts back missing or damaged program files.\r\n"
              "Remove uninstalls [ProductName] (your playlist is kept).", "Repair", "Repair", "Cancel")
    repair = d.pushbutton("Repair", 20, 110, 100, 22, _BUTTON, "&Repair", "Remove")
    repair.event("Reinstall", "ALL", ordering=1)
    repair.event("ReinstallMode", "ecmus", ordering=2)
    repair.event("EndDialog", "Return", ordering=3)
    remove = d.pushbutton("Remove", 20, 140, 100, 22, _BUTTON, "Re&move", "Cancel")
    remove.event("[REMOVE]", "ALL", ordering=1)
    remove.event("Remove", "ALL", ordering=2)
    remove.event("EndDialog", "Return", ordering=3)
    d.pushbutton("Cancel", 304, _BTN_Y, _BTN_W, _BTN_H, _BUTTON, "Cancel", "Repair") \
        .event("SpawnDialog", "CancelDlg")

    # -- Progress (modeless: stays up while files are copied)
    d = _page(db, "ProgressDlg", "Working on [ProductName]",
              "Please wait while Setup installs, repairs or removes [ProductName].",
              "Cancel", "Cancel", "Cancel", attr=1)
    d.text("ActionText", 20, 100, 330, 15, _TEXT, "").mapping("ActionText", "Text")
    d.control("ProgressBar", "ProgressBar", 20, 120, 330, 12, 65537, None, "Progress", None, None) \
        .mapping("SetProgress", "Progress")
    _buttons(d, None, None, "Cancel")

    # -- Finished
    d = _page(db, "ExitDialog", "[ProductName] Setup is complete",
              "Click Finish to close Setup.", "Finish", "Finish", "Finish")
    d.text("Removed", 20, 80, 330, 15, _TEXT, "[ProductName] has been removed from your computer.") \
        .condition("Hide", 'REMOVE <> "ALL"')
    launch = d.checkbox("LaunchCheck", 20, 110, 330, 18, _BUTTON, "LAUNCHAPP",
                        "&Launch [ProductName] now", "Finish")
    launch.condition("Hide", 'REMOVE = "ALL" OR Installed')
    finish = d.pushbutton("Finish", 236, _BTN_Y, _BTN_W, _BTN_H, _BUTTON, "&Finish", "LaunchCheck")
    finish.event("DoAction", "LaunchApp", 'LAUNCHAPP = "1" AND NOT Installed AND REMOVE <> "ALL"', 1)
    finish.event("EndDialog", "Return", ordering=2)

    # -- Interrupted by the user / failed
    for name, heading, body in (
            ("UserExit", "[ProductName] Setup was interrupted",
             "Setup was cancelled. Nothing on your computer was changed."),
            ("FatalError", "[ProductName] Setup ended because of an error",
             "Nothing on your computer was changed. You can run Setup again later.")):
        d = _page(db, name, heading, body, "Finish", "Finish", "Finish")
        d.pushbutton("Finish", 236, _BTN_Y, _BTN_W, _BTN_H, _BUTTON, "&Finish", None) \
            .event("EndDialog", "Exit")

    # -- "Are you sure?" when Cancel is clicked
    d = msilib.Dialog(db, "CancelDlg", 50, 10, 260, 85, 3, "[ProductName] Setup", "No", "No", "No")
    d.text("Text", 20, 15, 220, 30, _TEXT, "Are you sure you want to cancel [ProductName] Setup?")
    d.pushbutton("Yes", 72, 57, 56, 17, _BUTTON, "&Yes", "No").event("EndDialog", "Exit")
    d.pushbutton("No", 132, 57, 56, 17, _BUTTON, "&No", "Yes").event("EndDialog", "Return")

    # -- Error messages (Windows Installer fills ErrorText and shows the right buttons)
    d = msilib.Dialog(db, "ErrorDlg", 50, 10, 330, 101, 65543, "[ProductName] Setup",
                      "ErrorText", None, None)
    d.text("ErrorText", 50, 9, 280, 48, _TEXT, "")
    if icon is not None:   # Windows Installer looks for a control called ErrorIcon here
        msilib.add_data(db, "Binary", [("ErrorIconImage", msilib.Binary(str(icon)))])
        d.control("ErrorIcon", "Icon", 15, 15, 24, 24, 5242881, None, "ErrorIconImage", None, None)
    for i, (button, label, result) in enumerate((
            ("N", "&No", "ErrorNo"), ("Y", "&Yes", "ErrorYes"), ("A", "&Abort", "ErrorAbort"),
            ("C", "&Cancel", "ErrorCancel"), ("I", "&Ignore", "ErrorIgnore"),
            ("O", "&OK", "ErrorOk"), ("R", "&Retry", "ErrorRetry"))):
        d.pushbutton(button, 100 + (i % 3) * 60, 72, 56, 21, _BUTTON, label, None) \
            .event("EndDialog", result)

    # -- Files in use (e.g. IcePlayer still running during an upgrade or removal)
    d = _page(db, "FilesInUse", "Files in use",
              "These programs are using files Setup needs to change. Close them, then "
              "click Retry, or click Ignore to continue anyway (a restart may be needed).",
              "Retry", "Retry", "Exit")
    d.control("List", "ListBox", 20, 100, 330, 120, 7, "FileInUseProcess", None, None, None)
    for name, x, label, result, nxt_name in (("Exit", 180, "E&xit", "Exit", "Ignore"),
                                             ("Ignore", 236, "&Ignore", "Ignore", "Retry"),
                                             ("Retry", 304, "&Retry", "Retry", "Exit")):
        d.pushbutton(name, x, _BTN_Y, _BTN_W, _BTN_H, _BUTTON, label, nxt_name) \
            .event("EndDialog", result)


def _add_folder(db, cab, directory, folder: Path, feature, counter: list) -> dict:
    """Add a folder's files (one component per folder) and its sub-folders, recursively.

    Args:
        db:        Open msilib database.
        cab:       msilib.CAB collecting the compressed files.
        directory: msilib.Directory for ``folder`` (its files are read from there).
        folder:    Real folder on disk.
        feature:   msilib.Feature the components belong to.
        counter:   One-element list used to number sub-directory ids (d1, d2, ...).

    Returns:
        ``{relative path: MSI file key}`` for every file added, e.g.
        ``{"IcePlayer.exe": "IcePlayer.exe"}``, used to point shortcuts at the exe.
    """
    keys = {}
    directory.start_component(directory.logical, feature, 0)
    entries = sorted(folder.iterdir(), key=lambda p: (p.is_dir(), p.name.lower()))
    for entry in entries:
        if entry.is_file():
            keys[entry.name] = directory.add_file(entry.name)
    for entry in entries:
        if entry.is_dir():
            counter[0] += 1
            sub = msilib.Directory(db, cab, directory, entry.name, f"d{counter[0]}",
                                   f"{directory.make_short(entry.name)}|{entry.name}")
            for rel, key in _add_folder(db, cab, sub, entry, feature, counter).items():
                keys[f"{entry.name}/{rel}"] = key
    return keys


def build_msi(dist_dir: Path, output: Path, version: str, *, name: str = "IcePlayer",
              manufacturer: str = "IcePlayer", exe_name: str = "IcePlayer.exe",
              icon: Path | None = None) -> Path:
    """Create the .msi installer.

    Args:
        dist_dir:     Folder to install, e.g. ``dist/IcePlayer`` (must contain ``exe_name``).
        output:       The .msi file to write (replaced if it exists).
        version:      Product version, numbers only, e.g. ``"2.1.0"``. Must go up for
                      each release, or the new MSI won't replace the installed one.
        name:         Product name (Program Files folder, shortcuts, Settings → Apps).
        manufacturer: Publisher shown in Settings → Apps.
        exe_name:     Program the shortcuts start.
        icon:         .ico file shown in Settings → Apps (optional).

    Returns:
        ``output``.

    Raises:
        FileNotFoundError: ``dist_dir`` or the exe in it is missing.
        ValueError: ``version`` is not 2-4 numbers separated by dots.
    """
    dist_dir, output = Path(dist_dir).resolve(), Path(output).resolve()
    if not (dist_dir / exe_name).is_file():
        raise FileNotFoundError(f"{dist_dir / exe_name} not found — build the exe first")
    parts = version.split(".")
    if not (2 <= len(parts) <= 4 and all(p.isdigit() for p in parts)):
        raise ValueError(f"MSI versions must be numbers like 2.1.0, got {version!r}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    # msilib remembers directory ids for the whole process (module-level set, no public
    # reset); without clearing it a second build would get "TARGETDIR1",
    # "ProgramFiles64Folder1"... and install to the wrong place.
    msilib._directories.clear()

    # A new ProductCode every build = every MSI is a "major upgrade" of the previous one.
    db = msilib.init_database(str(output), schema, name, msilib.gen_uuid(), version, manufacturer)
    msilib.add_tables(db, sequence)          # the standard install/uninstall steps

    properties = [
        ("UpgradeCode", UPGRADE_CODE),
        ("ALLUSERS", "1"),                   # per-machine install into Program Files
        ("ARPNOMODIFY", "1"),                # Settings → Apps: no "Modify" button
        ("SecureCustomProperties", "OLDPRODUCTFOUND;NEWERPRODUCTFOUND"),
    ]
    if icon is not None:
        msilib.add_data(db, "Icon", [("icon.ico", msilib.Binary(str(icon)))])
        properties.append(("ARPPRODUCTICON", "icon.ico"))
    msilib.add_data(db, "Property", properties)

    # Upgrades: remove any older (or same) version; refuse to install over a newer one.
    msilib.add_data(db, "Upgrade", [
        (UPGRADE_CODE, None, version, None, 1 | 512, None, "OLDPRODUCTFOUND"),   # <= this version
        (UPGRADE_CODE, version, None, None, 2, None, "NEWERPRODUCTFOUND"),       # > this, detect only
    ])
    msilib.add_data(db, "CustomAction", [
        ("PreventDowngrade", 19, None, f"A newer version of {name} is already installed."),
    ])
    for table in ("InstallExecuteSequence", "InstallUISequence"):
        msilib.add_data(db, table, [("PreventDowngrade", "NEWERPRODUCTFOUND", 210)])
    # Remove the old version completely *before* installing the new one, so files can't
    # be lost when component ids change between builds.
    _execute(db, "UPDATE `InstallExecuteSequence` SET `Sequence` = 1450 "
                 "WHERE `Action` = 'RemoveExistingProducts'")

    # Folders: TARGETDIR\ProgramFiles64Folder\IcePlayer = INSTALLDIR, plus shortcut folders.
    cab = msilib.CAB("distfiles")
    root = msilib.Directory(db, cab, None, str(dist_dir), "TARGETDIR", "SourceDir")
    program_files = msilib.Directory(db, cab, root, ".", "ProgramFiles64Folder", ".")
    install_dir = msilib.Directory(db, cab, program_files, ".", "INSTALLDIR",
                                   f"{program_files.make_short(name)}|{name}")
    msilib.add_data(db, "Directory", [("ProgramMenuFolder", "TARGETDIR", "."),
                                      ("DesktopFolder", "TARGETDIR", ".")])

    feature = msilib.Feature(db, "DefaultFeature", name, f"{name} program files", 1,
                             directory="INSTALLDIR")
    keys = _add_folder(db, cab, install_dir, dist_dir, feature, [0])

    # Shortcuts belong to the INSTALLDIR component, which holds the exe.
    short_name = f"{install_dir.make_short(name + '.lnk').split('.')[0]}|{name}"
    target = f"[#{keys[exe_name]}]"
    msilib.add_data(db, "Shortcut", [
        ("StartMenuShortcut", "ProgramMenuFolder", short_name, "INSTALLDIR", target,
         None, f"{name} music player", None, None, None, None, "INSTALLDIR"),
        ("DesktopShortcut", "DesktopFolder", short_name, "INSTALLDIR", target,
         None, f"{name} music player", None, None, None, None, "INSTALLDIR"),
    ])

    _add_ui(db, keys[exe_name], icon)         # setup wizard + required dialogs

    cab.commit(db)                            # compress all files into the .msi
    db.Commit()
    return output


def read_table(msi_path: Path, table: str, columns: str = "*") -> list[tuple]:
    """Read rows from a table of an existing .msi (used to check a built installer).

    Args:
        msi_path: The .msi file.
        table:    Table name, e.g. ``"File"``, ``"Shortcut"``, ``"Property"``.
        columns:  SQL column list, e.g. ``"`Property`, `Value`"``.

    Returns:
        One tuple of strings/ints/None per row.
    """
    db = msilib.OpenDatabase(str(msi_path), msilib.MSIDBOPEN_READONLY)
    view = db.OpenView(f"SELECT {columns} FROM `{table}`")
    view.Execute(None)
    info = view.GetColumnInfo(msilib.MSICOLINFO_TYPES)      # e.g. "s72", "i2", "v0"
    kinds = [info.GetString(i)[0].lower() for i in range(1, info.GetFieldCount() + 1)]
    rows = []
    while True:
        try:
            record = view.Fetch()
        except msilib.MSIError:
            break
        if record is None:
            break
        row = []
        for i, kind in enumerate(kinds, start=1):
            if kind == "v":
                row.append("<binary>")
            elif kind == "i":
                value = record.GetInteger(i)
                row.append(None if value == _MSI_NULL_INTEGER else value)
            else:
                row.append(record.GetString(i) or None)    # empty string = NULL in MSI
        rows.append(tuple(row))
    view.Close()
    return rows
