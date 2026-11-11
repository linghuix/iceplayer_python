"""Package the PyInstaller output (dist/IcePlayer) into a Windows Installer (.msi) file.

Used by ``python build.py --msi``. It uses Python's built-in ``msilib`` (Windows only,
available up to Python 3.12 — the qtapp environment), so no extra tools are needed.

What the installer does
-----------------------
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
