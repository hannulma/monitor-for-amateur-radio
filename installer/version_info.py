"""Windows-versiotiedot: tulostaa numeerisen version ja kirjoittaa PyInstallerin --version-file -tiedoston.

Käyttö:  python installer/version_info.py build/version_info.txt   → tulostaa esim. 0.14.0.207
Numeerinen versio kasvaa aina: aN → N, bN → 200+N, rcN → 400+N, vakaa → 999.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ver = re.search(r'__version__ = "([^"]+)"', (ROOT / "bmmonitor" / "__init__.py").read_text()).group(1)
m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$", ver)
if not m:
    sys.exit(f"Tuntematon versiomuoto: {ver}")
major, minor, patch, pre, n = m.groups()
build = {"a": 0, "b": 200, "rc": 400}[pre] + int(n) if pre else 999
nums = (int(major), int(minor), int(patch), build)
num = ".".join(map(str, nums))

if len(sys.argv) > 1:
    Path(sys.argv[1]).parent.mkdir(parents=True, exist_ok=True)
    Path(sys.argv[1]).write_text(f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
                    date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'OH2BEA'),
      StringStruct('FileDescription', 'Monitor for Amateur Radio'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'MonitorForBrandMeister'),
      StringStruct('LegalCopyright', 'MIT License, OH2BEA'),
      StringStruct('OriginalFilename', 'MonitorForBrandMeister.exe'),
      StringStruct('ProductName', 'Monitor for Amateur Radio'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
print(num)
