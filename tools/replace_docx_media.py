from __future__ import annotations

import argparse
import os
import tempfile
import zipfile
from pathlib import Path


def replace_media(document: Path, member: str, replacement: Path) -> None:
    if not document.exists():
        raise FileNotFoundError(document)
    if not replacement.exists():
        raise FileNotFoundError(replacement)

    with tempfile.NamedTemporaryFile(
        prefix=f"{document.stem}_",
        suffix=document.suffix,
        dir=document.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)

    try:
        with zipfile.ZipFile(document, "r") as source, zipfile.ZipFile(
            temporary_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as target:
            if member not in source.namelist():
                raise KeyError(f"Mídia não encontrada no DOCX: {member}")
            for item in source.infolist():
                payload = replacement.read_bytes() if item.filename == member else source.read(item.filename)
                target.writestr(item, payload)
        os.replace(temporary_path, document)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Substitui uma mídia incorporada em um arquivo DOCX.")
    parser.add_argument("document", type=Path)
    parser.add_argument("member", help="Caminho interno, por exemplo word/media/image7.png")
    parser.add_argument("replacement", type=Path)
    args = parser.parse_args()
    replace_media(args.document, args.member, args.replacement)


if __name__ == "__main__":
    main()
