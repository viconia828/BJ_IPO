"""Extract verified page locations for auditable four-case research."""
import hashlib
import json
from pathlib import Path
import sys
from pypdf import PdfReader
try:
    import fitz
except ImportError:
    fitz = None  # Optional raster previews; extraction uses the pinned pypdf dependency.

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
from ifind_helper import now_iso

PAGES = {
    '920202': [9, 18, 19, 105, 106, 107, 109, 184, 231, 246, 247, 272],
    '920157': [8, 16, 17, 18, 134, 135, 136, 138, 210, 300, 301],
    '920025': [7, 8, 18, 19, 135, 136, 137, 138, 202, 274, 312],
    '920285': [9, 15, 16, 17, 105, 106, 107, 190, 257, 278, 286],
}


def main():
    output = ROOT / 'data/fundamental_cases/evidence'
    output.mkdir(parents=True, exist_ok=True)
    preview = ROOT / '.tmp/fundamental_stage2/visual'
    preview.mkdir(parents=True, exist_ok=True)
    for code, pages in PAGES.items():
        path = next(p for p in (ROOT / '公告文件').glob(f'{code}*.pdf') if '招股说明书' in p.name)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        doc = PdfReader(path)
        for page in pages:
            destination = output / f'{code}_p{page}.json'
            if not destination.exists():
                evidence = {'source_file': path.relative_to(ROOT).as_posix(), 'source_sha256': digest,
                            'pdf_page': page, 'page_text': doc.pages[page - 1].extract_text(),
                            'retrieved_at': now_iso(), 'source_publication_date': None,
                            'historical_pit_eligible': False}
                destination.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # Review original page geometry for shares, earnings and product mix.
        selected = {'920202': [18, 184, 231, 272], '920157': [16, 17, 138, 300],
                    '920025': [18, 202, 274, 312], '920285': [15, 16, 106, 257]}[code]
        if fitz is not None:
            with fitz.open(path) as rendered:
                for page in selected:
                    rendered[page - 1].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(str(preview / f'{code}_p{page}.png'))
        else:
            print('Optional PyMuPDF unavailable; inspect original PDF visually:', path.name)
    print('Evidence:', sum(map(len, PAGES.values())), 'pages; preview:', preview)


if __name__ == '__main__':
    main()
