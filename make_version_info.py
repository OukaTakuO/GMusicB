"""music_builder.py の VERSION から、exeに埋め込むバージョン情報ファイル(version_info.txt)を作る"""
import re
from pathlib import Path

text = Path("music_builder.py").read_text(encoding="utf-8")
match = re.search(r'^VERSION\s*=\s*"([0-9]+(?:\.[0-9]+){0,3})"', text, re.MULTILINE)

if not match:
    raise SystemExit('music_builder.py に VERSION = "0.2" のような行が見つかりません')

version = match.group(1)
numbers = (version.split(".") + ["0", "0", "0", "0"])[:4]
tuple_text = ", ".join(numbers)
dotted = ".".join(numbers)

content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({tuple_text}),
    prodvers=({tuple_text}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [
          StringStruct(u'CompanyName', u'GMusicB'),
          StringStruct(u'FileDescription', u'GMusicB - text score to MIDI converter'),
          StringStruct(u'FileVersion', u'{dotted}'),
          StringStruct(u'InternalName', u'GMusicB'),
          StringStruct(u'LegalCopyright', u'GMusicB'),
          StringStruct(u'OriginalFilename', u'GMusicB.exe'),
          StringStruct(u'ProductName', u'GMusicB'),
          StringStruct(u'ProductVersion', u'{dotted}')
        ])
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""

Path("version_info.txt").write_text(content, encoding="utf-8")
print(f"version_info.txt created (version {dotted})")
