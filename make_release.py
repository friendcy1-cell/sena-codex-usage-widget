"""Build local candidate archives from an allowlist. Never uploads or authenticates."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import struct
import zipfile

ROOT = Path(__file__).resolve().parent
SOURCE_FILES = (
    'widget.py', 'usage_core.py', 'screens.py', 'i18n.py', 'presentation.py',
    'character_packs.py', 'voice_player.py', 'wave_output.py', 'bundle_paths.py', 'bundle_check.py', 'make_release.py', 'requirements.txt',
    'requirements-build.txt', 'VERSION', 'Start-Sena.cmd', 'Start-Sena.ps1', 'Preview-Sena.cmd',
    'README.md', 'LICENSE-CODE.md', 'LICENSE-ASSETS.md', 'PRIVACY.md', 'CHANGELOG.md',
    'PACK_FORMAT.md', 'VOICE_BRIEF_JA.md', 'VOICE_CREDITS.md', 'VOICE_REVIEW_KO.md', 'RELEASE_CHECKLIST.md', '.gitignore',
    'test_widget.py', 'test_screens.py', 'test_features.py', 'test_release.py', 'test_voices.py', 'test_bundle.py',
    'INSTALL_KO.md', 'AGENTS.md',
    '.github/ISSUE_TEMPLATE/bug_report.md',
)
ASSETS = ('idle_alt.png', 'success.png', 'fail.png', 'frame_1.png', 'frame_2.png', 'frame_3.png')
OPTIONAL_PACKS = ('rina', 'nari')
BUNDLE_DOCS = ('README.md', 'LICENSE-CODE.md', 'LICENSE-ASSETS.md', 'PRIVACY.md', 'CHANGELOG.md',
               'PACK_FORMAT.md', 'VOICE_BRIEF_JA.md', 'VOICE_CREDITS.md', 'VOICE_REVIEW_KO.md',
               'INSTALL_KO.md', 'VERSION', 'Preview-Sena.cmd')


def embedded_files(public_files):
    # Never embed a whole source/assets tree: it may contain old artwork or data.
    return {name: path for name, path in public_files.items()
            if name.startswith('assets/') or name in
            ('VERSION', 'LICENSE-CODE.md', 'LICENSE-ASSETS.md', 'VOICE_CREDITS.md', 'PRIVACY.md')}


def runtime_files(public_files, onefile=False):
    return {name: path for name, path in public_files.items()
            if name in BUNDLE_DOCS or (not onefile and name.startswith('assets/'))}


def hash_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def source_files():
    selected = {}
    for name in SOURCE_FILES:
        path = ROOT / name
        if name == 'README.md' and (ROOT / 'PUBLIC_README.md').exists():
            path = ROOT / 'PUBLIC_README.md'
        text = path.read_text(encoding='utf8')
        private_key_pattern = '-----' + r'BEGIN [A-Z ]*PRIVATE KEY' + '-----'
        if re.search(r'(?i)(?:sk-[a-z0-9_-]{20,}|gh[pousr]_[a-z0-9]{20,})', text) or re.search(private_key_pattern, text):
            raise ValueError(f'Possible credential in {name}; not packaged')
        if re.search(r'(?i)[a-z]:[\\/]Users[\\/][^\s/\\"\']+', text):
            raise ValueError(f'Personal absolute path in {name}; not packaged')
        selected[name] = path
    for name in ASSETS:
        selected['assets/' + name] = ROOT / 'assets' / name
    from voice_player import scaled_wave
    for event in ('full', 'low', 'empty'):
        path = ROOT / 'assets' / 'voices' / 'ja' / f'{event}.wav'
        if path.exists():
            scaled_wave(path, 100)
            selected[f'assets/voices/ja/{event}.wav'] = path
    return selected


def archive_files(path, prefix, files):
    # Never overwrite a previously handed-off candidate with different bytes.
    if path.exists():
        raise FileExistsError(f'Output already exists: {path.name}; use a fresh output directory/version')
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in sorted(files.items()):
            archive.write(source, (prefix + '/' if prefix else '') + name)


def optional_pack_files(pack_id):
    from character_packs import discover_packs, image_path, voice_path
    from voice_player import scaled_wave
    pack = next((p for p in discover_packs(ROOT, ROOT) if p['id'] == pack_id), None)
    if pack_id not in OPTIONAL_PACKS or pack is None:
        raise ValueError('Missing approved local pack')
    prefix = f'packs/{pack_id}/'
    files = {prefix + 'pack.json': pack['root'] / 'pack.json'}
    from PIL import Image
    for pose in pack['images']:
        path = image_path(pack, pose)
        with Image.open(path) as image:
            if image.format != 'PNG' or max(image.size) > 4096:
                raise ValueError('Invalid pack image')
            image.verify()
        files[prefix + str(path.relative_to(pack['root'])).replace('\\', '/')] = path
    for event in ('full', 'low', 'empty'):
        path = voice_path(pack, 'ja', event)
        scaled_wave(path, 100)
        files[prefix + f'voices/ja/{event}.wav'] = path
    for name in ('PACK_FORMAT.md', 'VOICE_CREDITS.md', 'LICENSE-ASSETS.md'):
        files[name] = ROOT / name
    return files


def license_destination(package, file):
    parts = list(Path(file).parts)
    metadata = next((i for i, part in enumerate(parts) if part.endswith('.dist-info')), None)
    if metadata is not None:
        parts = parts[metadata + 1:]
        if parts and parts[0].lower() == 'licenses':
            parts = parts[1:]
    return Path(package).joinpath(*parts)


def collect_licenses(license_dir):
    license_dir.mkdir(parents=True, exist_ok=True)
    for name in ('LICENSE.txt', 'LICENSE'):
        candidate = Path(sys.base_prefix) / name
        if candidate.is_file(): shutil.copy2(candidate, license_dir / ('Python-' + name))
    for package in ('Pillow', 'charset-normalizer'):
        try:
            distribution = importlib.metadata.distribution(package)
        except importlib.metadata.PackageNotFoundError:
            continue
        for file in distribution.files or []:
            if 'license' in str(file).lower() or 'copying' in str(file).lower():
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    dest = license_dir / license_destination(package, file)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.exists() and dest.read_bytes() != source.read_bytes():
                        raise ValueError('Conflicting third-party notice names')
                    shutil.copy2(source, dest)
    for library in ('tcl8.6', 'tk8.6'):
        source = Path(sys.base_prefix) / 'tcl' / library / 'license.terms'
        if source.is_file():
            dest = license_dir / 'TclTk' / (library + '-license.terms')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)


def build_windows(output, public_files, version, onefile=False):
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise RuntimeError('Build the Windows x64 package on Windows x64')
    import PyInstaller
    build = output / ('build-onefile' if onefile else 'build')
    build.mkdir(parents=True, exist_ok=True)
    dist = build / 'dist'
    bundle = build / 'package' / 'SenaUsageWidget' if onefile else dist / 'SenaUsageWidget'
    notices = build / 'notices'
    collect_licenses(notices)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm',
               '--onefile' if onefile else '--onedir', '--windowed', '--noupx',
               '--exclude-module', 'numpy', '--name', 'SenaUsageWidget', '--distpath', str(dist),
               '--workpath', str(build / 'work'), '--specpath', str(build)]
    if onefile:
        for name, source in embedded_files(public_files).items():
            destination = Path(name).parent.as_posix()
            command.extend(['--add-data', f'{source}:{destination}'])
        command.extend(['--add-data', f'{notices}:THIRD_PARTY_LICENSES'])
    command.append(str(ROOT / 'widget.py'))
    subprocess.run(command, cwd=ROOT, check=True)
    bundle.mkdir(parents=True, exist_ok=True)
    if onefile:
        shutil.copy2(dist / 'SenaUsageWidget.exe', bundle / 'SenaUsageWidget.exe')
    for name, source in runtime_files(public_files, onefile).items():
        dest = bundle / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    shutil.copytree(notices, bundle / 'THIRD_PARTY_LICENSES', dirs_exist_ok=True)
    files = {str(p.relative_to(bundle)).replace('\\', '/'): p for p in bundle.rglob('*') if p.is_file()}
    variant = 'windows-x64-onefile' if onefile else 'windows-x64'
    archive = output / f'SenaUsageWidget-{version}-{variant}.zip'
    archive_files(archive, f'SenaUsageWidget-{version}-{variant}', files)
    return archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--windows', action='store_true')
    parser.add_argument('--onefile', action='store_true', help='Also build a single-EXE Windows ZIP.')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = (ROOT / 'VERSION').read_text(encoding='utf8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-rc\d+)?', version):
        raise ValueError('Invalid version')
    files = source_files()
    archives = [output / f'SenaUsageWidget-{version}-source.zip']
    archive_files(archives[0], f'SenaUsageWidget-{version}-source', files)
    if args.windows:
        archives.append(build_windows(output, files, version))
    if args.onefile:
        archives.append(build_windows(output, files, version, onefile=True))
    pack_counts = {}
    for pack_id in OPTIONAL_PACKS:
        if not (ROOT / 'packs' / pack_id / 'pack.json').exists():
            continue  # Basic edition never requires an optional pack to build.
        pack_files = optional_pack_files(pack_id)
        archive = output / f'SenaUsageWidget-{version}-pack-{pack_id}.zip'
        archive_files(archive, '', pack_files)
        archives.append(archive)
        pack_counts[pack_id] = len(pack_files)
    checksums = ''.join(f'{hash_file(path)}  {path.name}\n' for path in archives)
    (output / 'SHA256SUMS.txt').write_text(checksums, encoding='utf8')
    voices_ready = all(f'assets/voices/ja/{event}.wav' in files for event in ('full', 'low', 'empty'))
    report = {
        'version': version, 'candidate_only': True, 'uploaded': False,
        'file_count_source': len(files), 'japanese_voice_files_complete': voices_ready,
        'windows_variants': (['folder'] if args.windows else []) + (['onefile'] if args.onefile else []),
        'optional_pack_file_counts': pack_counts,
        'voice_rights_and_listening_review_approved': False,
        'code_and_asset_terms_approved': False, 'different_account_clean_pc_test_passed': False,
        'public_release_ready': False,
        'archives': [{'name': p.name, 'sha256': hash_file(p), 'bytes': p.stat().st_size} for p in archives],
        'blockers': ['Owner listening/character review and final voice redistribution terms',
                     'Owner decision on code and character/audio licenses',
                     'Clean Windows PC and different Codex account validation'],
    }
    (output / 'CANDIDATE_STATUS.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
