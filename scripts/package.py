"""Build source-only archives from an explicit allowlist; never include account data."""
import argparse
import hashlib
from pathlib import Path
import tarfile
import zipfile


def sources(root):
    files = [root / name for name in ('README.md', 'NOTICE.md', 'pyproject.toml', 'constraints-tested.txt', '.gitignore', '.gitattributes')]
    for directory, suffixes in {'bosco': {'.py'}, 'scripts': {'.py', '.js'}, 'tests': {'.py'},
                               'docs': {'.md'}, '.github/workflows': {'.yml'}}.items():
        files.extend(path for path in (root / directory).rglob('*') if path.is_file() and path.suffix in suffixes)
    files.extend([root/'config/bosco.config.example.json', root/'tests/fixtures/order-status.v1.schema.json'])
    return sorted(set(files))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    paths = sources(root)
    args.output.mkdir(parents=True, exist_ok=True)
    name = 'bosco-automation-linux'
    archives = [args.output/(name+'.tar.gz'), args.output/(name+'.zip')]
    with tarfile.open(archives[0], 'w:gz') as tar:
        for path in paths:
            info = tar.gettarinfo(str(path), str(Path(name)/path.relative_to(root)))
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mode = 0o644
            with path.open('rb') as stream:
                tar.addfile(info, stream)
    with zipfile.ZipFile(archives[1], 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, str(Path(name)/path.relative_to(root)))
    lines = [hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name for path in archives]
    (args.output/'SHA256SUMS').write_text('\n'.join(lines)+'\n', encoding='utf8')
    print(f'Packaged {len(paths)} source files into {len(archives)} archives; account files excluded.')


if __name__ == '__main__':
    main()
