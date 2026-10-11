"""Stage only the desktop plugin's required files; never install or enable it."""
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent
FILES = (
    'plugin.json', 'mcp.json', 'hooks/hooks.json',
    'skills/aics-desktop/SKILL.md', 'requirements-mcp.txt',
    'desktop_mcp.py', 'desktop_relay.py', 'config.py', 'handoff.py',
    'handoff_check.py', 'native_relay.py', 'tailer.py', 'receipts.py',
    'process_control.py',
)


def build():
    package = ROOT / '.relay' / 'desktop-package' / 'aics-desktop'
    for name in FILES:
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    actual = {str(path.relative_to(package)).replace('\\', '/')
              for path in package.rglob('*') if path.is_file()}
    if actual != set(FILES):
        raise ValueError('Package contains unexpected files; refusing to publish a catalog')
    marketplace = {'name': 'aics-local', 'plugins': [{
        'name': 'aics-desktop', 'source': {'source': 'local', 'path': './aics-desktop'},
        'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'},
        'category': 'Productivity'}]}
    catalog = package.parent / '.agents' / 'plugins' / 'marketplace.json'
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps(marketplace, indent=2)+'\n', encoding='utf-8')
    return package


if __name__ == '__main__':
    package = build()
    print(json.dumps({'package': str(package), 'files': len(FILES),
                      'bytes': sum((package/name).stat().st_size for name in FILES),
                      'installed': False, 'enabled': False}))
