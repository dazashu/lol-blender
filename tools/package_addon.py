"""Package a source-built addon without development files or user settings."""
import ast
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / 'plugin/LoL_Blender'


def main():
    tree = ast.parse((ADDON / '__init__.py').read_text(encoding='utf-8'))
    info = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'bl_info' for t in n.targets))
    version = '.'.join(map(str, info['version']))
    if not (ADDON / 'bin/_rsblender.pyd').is_file():
        raise SystemExit('Run build.ps1 first.')
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    archive = output / f'LoL_Blender_{version}.zip'
    files = []
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(ADDON.rglob('*')):
            if (not path.is_file() or '__pycache__' in path.parts
                    or path.name == 'local_settings.json'
                    or path.suffix not in {'.py', '.pyd', '.txt', '.png'}):
                continue
            name = path.relative_to(ADDON.parent).as_posix()
            data = path.read_bytes()
            z.writestr(name, data)
            files.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest()})
        z.write(ROOT / 'LICENSE', 'LoL_Blender/licenses/GPL-3.0.txt')
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
    manifest = {'version': version, 'native_version': '0.25.0', 'files': files,
                'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
    archive.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(archive)


if __name__ == '__main__':
    main()
