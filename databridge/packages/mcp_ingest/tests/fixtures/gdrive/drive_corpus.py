"""The synthetic Drive the fake server serves: the Larkspur demo corpus uploaded into one folder,
plus the Drive-only shapes a folder crawl must survive. All content is synthetic.

  * every file under fixtures/workspace_demo/files, at its own folder path (uploaded as-is, the
    way docs/GOOGLE_DRIVE_SETUP.md tells a person to upload it) — including `planning/Q3 #roadmap`
  * one .md whose Drive mime is application/octet-stream (Drive does not always know Markdown)
  * two NATIVE Google Docs whose Markdown export is a corpus file's text (so the screen decision
    the corpus expects for that file must hold for the Doc too)
  * a SECOND `notes.md` in the same folder as the first — Drive allows it; identity must not merge
  * a Google Form (no export an extractor can read) — must be recorded, not silently dropped
  * a trashed file — Drive's own listing omits it

`build()` writes the manifest and returns what every file is EQUIVALENT to in
`expected_outcomes.yaml`, so tests derive expected screen decisions instead of typing them.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

MY_DRIVE_ID = "0AFakeMyDriveRootPk9"
ROOT_NAME = "Walnut demo — Larkspur"
FOLDER = "application/vnd.google-apps.folder"
DOC = "application/vnd.google-apps.document"
FORM = "application/vnd.google-apps.form"
UPLOAD_MIME = {".md": "text/markdown", ".txt": "text/plain", ".pdf": "application/pdf",
               ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def drive_id(key: str) -> str:
    """A Drive-shaped id (33 chars of [A-Za-z0-9_-]) derived from a stable key."""
    return "1" + hashlib.sha256(key.encode()).hexdigest()[:32]


ROOT_ID = drive_id("folder:")


@dataclass
class DriveFile:
    id: str
    rel: str                           # folder path below the root + name, "/"-joined
    mime: str                          # the Drive-native mime
    equivalent: str | None             # expected_outcomes.yaml key, or None (no screen verdict)
    native: bool = False


@dataclass
class DriveCorpus:
    manifest: Path
    files: list[DriveFile] = field(default_factory=list)
    folders: dict[str, str] = field(default_factory=dict)     # rel -> id

    def by_id(self) -> dict[str, DriveFile]:
        return {f.id: f for f in self.files}


def _stamp(n: int) -> str:
    return f"2026-09-{1 + n % 28:02d}T09:{n % 60:02d}:00.000Z"


def build(dest: Path, corpus: Path) -> DriveCorpus:
    """Write `dest/drive.json` (+ copied content under `dest/content/`)."""
    dest.mkdir(parents=True, exist_ok=True)
    content = dest / "content"
    content.mkdir(exist_ok=True)
    out = DriveCorpus(manifest=dest / "drive.json")
    items: list[dict] = [{"id": ROOT_ID, "name": ROOT_NAME, "mimeType": FOLDER,
                          "parents": [MY_DRIVE_ID], "createdTime": _stamp(0),
                          "modifiedTime": _stamp(0)}]
    out.folders[""] = ROOT_ID

    def folder(rel: str) -> str:
        if rel in out.folders:
            return out.folders[rel]
        parent_rel, _, name = rel.rpartition("/")
        parent = folder(parent_rel)
        fid = drive_id(f"folder:{rel}")
        items.append({"id": fid, "name": name, "mimeType": FOLDER, "parents": [parent],
                      "createdTime": _stamp(len(items)), "modifiedTime": _stamp(len(items))})
        out.folders[rel] = fid
        return fid

    def add(rel: str, mime: str, *, key: str, equivalent: str | None, native: bool = False,
            text: str | None = None, source: Path | None = None) -> DriveFile:
        parent_rel, _, name = rel.rpartition("/")
        fid = drive_id(key)
        item = {"id": fid, "name": name, "mimeType": mime, "parents": [folder(parent_rel)],
                "createdTime": _stamp(len(items)), "modifiedTime": _stamp(len(items) + 1)}
        if text is not None:
            item["content_text"] = text
        else:
            target = content / fid
            shutil.copyfile(source, target)
            item["content_path"] = f"content/{fid}"
        items.append(item)
        f = DriveFile(id=fid, rel=rel, mime=mime, equivalent=equivalent, native=native)
        out.files.append(f)
        return f

    for path in sorted(p for p in corpus.rglob("*") if p.is_file()):
        rel = path.relative_to(corpus).as_posix()
        mime = UPLOAD_MIME.get(path.suffix.lower(), "application/octet-stream")
        if rel == "sales/Rate_Card_Draft_v2.md":
            mime = "application/octet-stream"          # Drive did not recognise the type
        add(rel, mime, key=f"file:{rel}", equivalent=rel, source=path)

    # native Google Docs: exported as Markdown, same text as a corpus file
    kickoff = "meetings/2026-07-14_kickoff_pellucid.txt"
    add("meetings/Kick-off notes (Pellucid)", DOC, key="doc:kickoff", equivalent=kickoff,
        native=True, text=(corpus / kickoff).read_text(encoding="utf-8"))
    bands = "hr/Compensation_Bands_2026.md"
    add("hr/Compensation Bands 2026", DOC, key="doc:bands", equivalent=bands, native=True,
        text=(corpus / bands).read_text(encoding="utf-8"))

    # a second notes.md in the SAME folder (Drive allows duplicate names in one folder)
    notes = "projects/demand-forecasting/notes.md"
    add(notes, "text/markdown", key="file:dup:" + notes, equivalent=notes,
        text=(corpus / notes).read_text(encoding="utf-8")
        + "\nRevision: a second copy uploaded by Ines Marchetti-Holm on 2026-09-02.\n")

    # a Google Form: nothing an extractor can read — recorded as excluded
    add("Team survey", FORM, key="form:survey", equivalent=None, native=True, text="")

    # a trashed file: never listed
    items.append({"id": drive_id("trashed"), "name": "old draft.md", "mimeType": "text/markdown",
                  "parents": [ROOT_ID], "trashed": True, "content_text": "trashed",
                  "createdTime": _stamp(1), "modifiedTime": _stamp(2)})

    out.manifest.write_text(json.dumps({"my_drive_id": MY_DRIVE_ID, "items": items}, indent=1),
                            encoding="utf-8")
    return out


def edit(manifest: Path, file_id: str, *, text: str | None = None, trashed: bool | None = None,
         modified: str = "2026-09-30T12:00:00.000Z") -> None:
    """Change the Drive between runs: new content (and a new modifiedTime), or trash a file."""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for item in data["items"]:
        if item["id"] == file_id:
            if text is not None:
                item.pop("content_path", None)
                item["content_text"] = text
                item["modifiedTime"] = modified
            if trashed is not None:
                item["trashed"] = trashed
            break
    else:
        raise KeyError(file_id)
    manifest.write_text(json.dumps(data, indent=1), encoding="utf-8")
