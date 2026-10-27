"""Acquire only the pinned official UCI archive; never execute workbook content."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
from zipfile import ZipFile
from app.datasets.source import DOWNLOAD_URL, OFFICIAL_WORKBOOK_SHA256


def acquire_workbook(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "Online Retail.xlsx"
    if target.exists():
        if sha256(target.read_bytes()).hexdigest() != OFFICIAL_WORKBOOK_SHA256:
            raise ValueError("Existing workbook differs from the pinned official artifact; it was not overwritten")
        return target
    with urlopen(DOWNLOAD_URL, timeout=60) as response:
        if (urlparse(response.geturl()).hostname != "archive.ics.uci.edu" or urlparse(response.geturl()).scheme != "https"):
            raise ValueError("Download left the official source host")
        archive = response.read(40_000_001)
    if len(archive) > 40_000_000:
        raise ValueError("Official download exceeded the size limit")
    with ZipFile(BytesIO(archive)) as zipped:
        entry = next(item for item in zipped.infolist() if item.filename == "Online Retail.xlsx")
        if entry.file_size > 30_000_000:
            raise ValueError("Workbook exceeded the size limit")
        content = zipped.read(entry)
    if sha256(content).hexdigest() != OFFICIAL_WORKBOOK_SHA256:
        raise ValueError("Official workbook bytes changed; provenance must be reviewed before import")
    with target.open("xb") as output:
        output.write(content)
    return target
