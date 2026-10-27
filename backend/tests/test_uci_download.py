"""No network: official-host and pinned-byte acquisition checks use test-only archives."""
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile
import pytest
from app.datasets import download

def test_cached_pinned_workbook_is_not_downloaded_or_overwritten(tmp_path,monkeypatch):
    data=b"test-only fixture"
    target=tmp_path/"Online Retail.xlsx";target.write_bytes(data)
    monkeypatch.setattr(download,"OFFICIAL_WORKBOOK_SHA256",sha256(data).hexdigest())
    monkeypatch.setattr(download,"urlopen",lambda *args,**kwargs:pytest.fail("network was not expected"))
    assert download.acquire_workbook(tmp_path)==target
    assert target.read_bytes()==data

def test_changed_existing_workbook_is_preserved_and_rejected(tmp_path):
    target=tmp_path/"Online Retail.xlsx";target.write_bytes(b"unverified local file")
    with pytest.raises(ValueError,match="not overwritten"):
        download.acquire_workbook(tmp_path)
    assert target.read_bytes()==b"unverified local file"

def test_download_refuses_a_redirect_outside_the_official_host(tmp_path,monkeypatch):
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def geturl(self):return "https://untrusted.example/retail.zip"
    monkeypatch.setattr(download,"urlopen",lambda *args,**kwargs:Response())
    with pytest.raises(ValueError,match="official source host"):
        download.acquire_workbook(tmp_path)
    assert not (tmp_path/"Online Retail.xlsx").exists()

def test_valid_archive_is_pinned_before_writing_fixed_filename(tmp_path,monkeypatch):
    content=b"test-only workbook bytes";stream=BytesIO()
    with ZipFile(stream,"w") as archive:
        archive.writestr("Online Retail.xlsx",content)
        archive.writestr("../unwanted.txt",b"never extracted")
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def geturl(self):return download.DOWNLOAD_URL
        def read(self,*args):return stream.getvalue()
    monkeypatch.setattr(download,"urlopen",lambda *args,**kwargs:Response())
    monkeypatch.setattr(download,"OFFICIAL_WORKBOOK_SHA256",sha256(content).hexdigest())
    target=download.acquire_workbook(tmp_path)
    assert target.read_bytes()==content
    assert [p.name for p in tmp_path.iterdir()]==["Online Retail.xlsx"]
