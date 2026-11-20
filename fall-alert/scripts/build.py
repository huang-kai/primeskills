"""Build the standalone skill using the pristine example, even in a configured checkout."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

import yaml

skill = Path(__file__).resolve().parents[1]
builder = skill.parents[1] / 'BotBuddy/src/botbuddy/tools/build_release.py'
example = yaml.safe_load((skill / 'cfg/config.example.yaml').read_text())
if (example['contact']['phone'] or example['sms']['https_gateway']['url']
        or example['sms']['https_gateway']['bearer_token']
        or any(example['sms']['aliyun'][field] for field in
               ('access_key_id', 'access_key_secret', 'sign_name', 'template_code'))):
    sys.exit('发布模板含有联系人或发送账号设置，请先清空。')
dist = skill.parent / 'dist'
dist.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=dist) as directory:
    stage = Path(directory) / skill.name
    stage.mkdir()
    for name in ('main.py', 'README.md', 'SKILL.md', 'requirements.txt', 'LICENSE'):
        shutil.copyfile(skill / name, stage / name)
    for name in ('src', 'scripts'):
        shutil.copytree(skill / name, stage / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.log'))
    cfg = stage / 'cfg'
    cfg.mkdir()
    for name in ('pkg.yaml', 'config.example.yaml'):
        shutil.copyfile(skill / 'cfg' / name, cfg / name)
    shutil.copyfile(cfg / 'config.example.yaml', cfg / 'config.yaml')
    (cfg / 'config.yaml').chmod(0o600)
    out = Path(directory) / 'out'
    subprocess.run([sys.executable, str(builder), '--skill', str(stage), '--output-dir', str(out)],
                   check=True, stdout=subprocess.DEVNULL)
    package, = out.glob('*.rpk')
    # BotBuddy's standalone skill packer omits docs/. Include the linked operator guides.
    with ZipFile(package, 'a', compression=ZIP_DEFLATED) as archive:
        for guide in sorted((skill / 'docs').glob('*.md')):
            archive.write(guide, 'docs/' + guide.name)
    print(package.replace(dist / package.name))
