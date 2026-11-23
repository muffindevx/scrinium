# ADR-015: Downloads can live outside the data directory, checked before use

**Status:** accepted by the owner, 2026-11-23. Amends [ADR-002](ADR-002-storage.md) (what lives in the data directory) and [ADR-013](ADR-013-own-provider-by-default.md) (the local install).

## The question

Scrinium downloads llama.cpp, gitleaks and the models into its data directory: about 4 GB with the local chat model, more with any larger model. On the reference machine the system disk had 7 GB free, while a second disk had 1.3 TB. That second disk is NTFS.

The data directory cannot move there. It holds the text of every indexed source, and Scrinium refuses to start on a disk that ignores permissions ([ADR-003](ADR-003-local-runtime-security.md)): on NTFS or exFAT any account on the machine could read it.

So: **can the heavy, public part move to another disk without weakening what protects the private part?** And if those files sit where another account can write, how does Scrinium know that what it runs is what it installed?

## What we decided

1. **A downloads folder, separate from the data directory.** It holds `tools/` and `models/`. By default it is the data directory itself, so nothing changes for anyone who does not choose.
2. **Three ways to choose it,** in this order: the variable `SCRINIUM_DOWNLOADS_DIR`, the folder chosen in Settings › AI models (Local AI engine) or with `scrinium tools move <folder>`, and the data directory.
3. **The data directory never moves with it.** Projects, rules, the indexed text, the keyring references and the integrity record stay on a disk that keeps permissions.
4. **An integrity record in the data directory.** For every file installed outside it: size, modification time and SHA-256; for every link, its target. The record is where another account cannot write, so it cannot be changed along with the files.
5. **Checked before use, outside the data directory only:**
   - llama.cpp's folder (the executable and its libraries) is hashed in full before llama-server starts;
   - a model is hashed again only if its size or modification time changed;
   - `scrinium doctor` hashes everything;
   - a file that appeared in the folder since is an error too, since llama-server would load a library dropped next to it.
6. **A move is a copy that switches only when it is complete:** copy what the manifest uses, read every copied file back and compare its SHA-256, then switch. If any step fails, the target is emptied and nothing changes. The old copy is deleted at the next start, because a running llama-server may still load a model from it.

## How it works

| Piece | Where |
|---|---|
| Choosing, recording, checking, moving | [`infrastructure/tools/downloads.py`](../../runtime/src/scrinium/infrastructure/tools/downloads.py) |
| State and record | `<data dir>/downloads.json` (`0600`): `dir`, `files`, `cleanup` |
| Checks before llama-server | `start_llama_server` in [`cli/main.py`](../../runtime/src/scrinium/cli/main.py) |
| Settings | `GET /api/v1/system/engine` (`downloads`), `POST /api/v1/system/downloads/move`; [`DownloadsSection.tsx`](../../ui/src/screens/DownloadsSection.tsx) |
| CLI | `scrinium tools move <folder>`, `scrinium tools install --dir <folder>`, `scrinium tools status` |
| Clean and uninstall | `scrinium clean` lists the old copy; `scrinium uninstall` removes only `tools/` and `models/` from the downloads folder, never the rest of it |

**What a move refuses,** each with a stable code for the console: a relative path, the same folder, a folder inside the data directory (or containing it), a file, a missing parent, a folder Scrinium cannot write, a `tools/` or `models/` that already has files, too little space (what is in use plus 512 MB), and a folder fixed by the variable.

**Installing outside the data directory.** `scrinium tools install` records each file as it installs it. A file found there without a record is not trusted: a tool is downloaded again; a model is kept only if its bytes match the manifest's SHA-256.

**The demo.** With the downloads in the data directory it links them, as before. Outside it, it shares the same folder and the same record, so it checks them like the real one.

## Why

- **Why not move the data directory?** It is the part that must stay private, and the disk with room does not keep permissions. Moving the public part solves the space problem without touching that.
- **Why can downloads live on a disk without permissions?** They are public files, pinned by version and SHA-256 in the manifest. Their confidentiality does not matter; their integrity does, and the record restores it.
- **Why keep the record in the data directory?** A record next to the files could be rewritten together with them. Next to the database, it has the protection the database has.
- **Why hash llama.cpp in full every time, and models only when they change?** llama.cpp is code Scrinium executes, and about 100 MB: hashing it takes well under a second. A model is several GB: hashing it on every start would add seconds or minutes. Size and time catch accidents and careless changes; `doctor` catches the rest.
- **Why copy and check instead of `mv`?** Between disks, `mv` is a copy anyway, and a copy can fail halfway or write wrong bytes on a failing disk. Reading every file back costs a second pass and gives certainty.
- **Why delete the old copy at the next start?** llama-server loads models on demand. Deleting their files while it runs could break the next load until a restart.

## Alternatives considered

| Option | Why not |
|---|---|
| Move the whole data directory with `SCRINIUM_DATA_DIR` | It already exists, but only to another disk that keeps permissions. On NTFS it would expose the indexed text. |
| An ext4 image file on the NTFS disk | Permissions work inside it, but any account can copy the image and read it. |
| A symbolic link from `models/` to the other disk | Works without code, but nothing checks what is behind it, and `clean` or `uninstall` would follow or break it. |
| Trust the other disk, no record | On a single-user machine it would be fine; on a shared one, another account could swap the executable Scrinium runs. |
| Hash every model on every start | Seconds to minutes per start for a guarantee `doctor` already gives on demand. |
| Let the variable and Settings both choose | Two sources of truth. The variable wins, and Settings says so. |

## Consequences

- The system disk keeps only the data directory: tens of MB for a medium repository.
- A model changed by another account keeping its size and modification time is caught by `doctor`, not by the start. On a single-user machine this does not apply.
- Downloads moved before this ADR, or put in a folder by hand, have no record and are refused outside the data directory until reinstalled or moved by Scrinium.
- Ollama is not affected: it keeps its models where `OLLAMA_MODELS` says (`.models/` for `make ollama`).

## Revisit when

- llama.cpp starts loading files Scrinium did not install (plugins, downloaded projectors); or
- a model can be verified faster than hashing it (signed manifests, block hashes).
