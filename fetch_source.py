"""Fetch only the official SQLGlot archive at the competition's pinned commit."""
from pathlib import Path
import tarfile
import urllib.request

PIN = 'eb11aec4be8cb2122afe713a4816662c3f0fb2fa'
ROOT = Path(__file__).resolve().parent

if __name__ == '__main__':
    archive = Path('/tmp/sqlglot-source.tar.gz')
    urllib.request.urlretrieve(f'https://codeload.github.com/tobymao/sqlglot/tar.gz/{PIN}', archive)
    with tarfile.open(archive) as source:
        source.extractall(ROOT, filter='data')
    (ROOT / f'sqlglot-{PIN}').rename(ROOT / 'sqlglot')
    archive.unlink()
