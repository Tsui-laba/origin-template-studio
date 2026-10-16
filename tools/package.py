"""Build a portable Windows x64 application using installed pinned packages."""
from pathlib import Path
import hashlib
import importlib.metadata as metadata
import json
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'dist' / 'OriginTemplateStudio'


def ignore(_path, names):
    return [n for n in names if n in {'__pycache__', 'tests', 'test', 'testing'}
            or n.endswith(('.pyc', '.pdb')) or n == 'direct_url.json']


def main():
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12):
        raise SystemExit('Use Windows x64 and Python 3.12.')
    if APP.exists():
        assert APP.resolve().is_relative_to((ROOT / 'dist').resolve())
        shutil.rmtree(APP)
    runtime = Path(sys.base_prefix)
    for folder in ('runtime/Lib/site-packages', 'python_origin_plotter', 'templates', 'licenses'):
        (APP / folder).mkdir(parents=True, exist_ok=True)
    for name in ('origin_plotter.py', 'origin_ui_bridge.py', 'template_configs.json'):
        shutil.copy2(ROOT / 'python_origin_plotter' / name, APP / 'python_origin_plotter' / name)
    for file in (ROOT / 'templates').glob('*.opj'):
        shutil.copy2(file, APP / 'templates' / file.name)
    for name in ('README.txt', 'PRIVACY_CHECK.txt'):
        shutil.copy2(ROOT / 'docs' / name, APP / name)
    shutil.copy2(ROOT / 'tools' / 'CreateDesktopShortcut.vbs', APP / 'CreateDesktopShortcut.vbs')
    shutil.copy2(ROOT / 'versions.json', APP / 'versions.json')
    for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python312.dll',
                 'vcruntime140.dll', 'vcruntime140_1.dll'):
        shutil.copy2(runtime / name, APP / 'runtime' / name)
    shutil.copy2(runtime / 'LICENSE.txt', APP / 'licenses' / 'Python-LICENSE.txt')
    for file in (runtime / 'DLLs').iterdir():
        if file.suffix in {'.pyd', '.dll'}:
            shutil.copy2(file, APP / 'runtime' / file.name)
    with zipfile.ZipFile(APP / 'runtime' / 'python312.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in (runtime / 'Lib').rglob('*'):
            relative = file.relative_to(runtime / 'Lib')
            if any(p in {'site-packages', '__pycache__', 'tests', 'test', 'idlelib',
                         'tkinter', 'ensurepip', 'turtledemo'} for p in relative.parts):
                continue
            if file.is_file() and file.suffix in {'.py', '.txt', '.pem', '.pickle'}:
                archive.write(file, relative.as_posix())
    site = APP / 'runtime' / 'Lib' / 'site-packages'
    versions = json.loads((ROOT / 'versions.json').read_text(encoding='utf-8'))
    for package in versions['dependencies']:
        dist = metadata.distribution(package)
        names = {str(p).split('/')[0] for p in dist.files if '..' not in str(p).split('/')}
        for name in names:
            source = Path(dist.locate_file(name))
            if source.is_dir():
                shutil.copytree(source, site / name, dirs_exist_ok=True, ignore=ignore)
            elif source.is_file() and source.suffix != '.pyc':
                shutil.copy2(source, site / name)
    for file in site.rglob('*.py'):
        content = file.read_bytes()
        if content.startswith(b'#!') and (b':\\' in content.splitlines()[0] or b'Users' in content.splitlines()[0]):
            file.write_bytes(b'#!/usr/bin/env python3\n' + b''.join(content.splitlines(keepends=True)[1:]))
    (APP / 'runtime' / 'python312._pth').write_text(
        'python312.zip\n.\nLib/site-packages\nimport site\n', encoding='ascii')
    (site / 'sitecustomize.py').write_text('import sys\nsys.dont_write_bytecode = True\n', encoding='ascii')
    compiler = Path(os.environ['SystemRoot']) / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319' / 'csc.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', f'/out:{APP / "OriginTemplateStudio.exe"}',
                    *[f'/reference:{name}.dll' for name in ('System.Windows.Forms', 'System.Drawing',
                      'System.Web.Extensions', 'System.IO.Compression', 'System.IO.Compression.FileSystem', 'System.Xml')],
                    str(ROOT / 'src' / 'OriginTemplateStudio.cs')], check=True)
    checksums = [hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.relative_to(APP).as_posix()
                 for p in sorted(APP.rglob('*')) if p.is_file()]
    (APP / 'SHA256SUMS.txt').write_text('\n'.join(checksums) + '\n', encoding='utf-8')
    version = versions['version']
    output = ROOT / 'dist' / f'OriginTemplateStudio-{version}-Windows-x64.zip'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(APP.rglob('*')):
            if file.is_file():
                archive.write(file, 'OriginTemplateStudio/' + file.relative_to(APP).as_posix())
    print(output)


if __name__ == '__main__':
    main()
