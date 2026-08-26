from __future__ import annotations

from pathlib import Path
import argparse

import pythoncom
import win32com.client


PROJECT_DIR = Path(__file__).resolve().parents[1]
DOCX_PATH = PROJECT_DIR / "docs" / "artigo_previsao_comportamental_bHave_integrado_mvp_logica.docx"
PDF_PATH = PROJECT_DIR / "docs" / "artigo_previsao_comportamental_bHave_integrado_mvp_logica_word_qa.pdf"


def export_with_word(docx_path: Path = DOCX_PATH, pdf_path: Path = PDF_PATH) -> None:
    pythoncom.CoInitialize()
    word = None
    document = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        word.Options.UpdateLinksAtOpen = False
        document = word.Documents.Open(
            str(docx_path),
            ConfirmConversions=False,
            ReadOnly=False,
            AddToRecentFiles=False,
            Visible=False,
            OpenAndRepair=True,
            NoEncodingDialog=True,
        )
        document.Save()
        document.ExportAsFixedFormat(
            OutputFileName=str(pdf_path),
            ExportFormat=17,
            OpenAfterExport=False,
            OptimizeFor=0,
            Range=0,
            Item=0,
            IncludeDocProps=True,
            KeepIRM=True,
            CreateBookmarks=1,
            DocStructureTags=True,
            BitmapMissingFonts=True,
            UseISO19005_1=False,
        )
        print(f"PDF={pdf_path}")
        print(f"PAGES={document.ComputeStatistics(2)}")
    finally:
        if document is not None:
            document.Close(False)
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("docx", nargs="?", type=Path, default=DOCX_PATH)
    parser.add_argument("pdf", nargs="?", type=Path, default=PDF_PATH)
    args = parser.parse_args()
    args.pdf.parent.mkdir(parents=True, exist_ok=True)
    export_with_word(args.docx.resolve(), args.pdf.resolve())
