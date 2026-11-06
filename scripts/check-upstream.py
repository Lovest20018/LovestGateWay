#!/usr/bin/env python3
"""Compare fetched upstream objects with the reviewed ledger; never run upstream code."""
import argparse
import html
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r'^[0-9a-f]{40}$')
TAG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$')


def git(root, *args):
    return subprocess.check_output(['git', '-c', 'safe.directory=' + str(root), '-C', str(root), *args])


def ledger_value(path):
    return validate_ledger(json.loads(Path(path).read_text(encoding='utf-8')))


def validate_ledger(value):
    if (value.get('schema') != 1 or value.get('repository') != 'kkkgo/PaoPaoGateWay'
            or value.get('branch') != 'main'
            or not all(SHA.fullmatch(value.get(key, '')) for key in ('merged_base', 'reviewed_head'))
            or not TAG.fullmatch(value.get('reviewed_release', ''))
            or ('initial_release_commit' in value and not SHA.fullmatch(value['initial_release_commit']))):
        raise ValueError('Invalid upstream ledger')
    return value


def inspect(root, ledger, ref, release):
    if release and not TAG.fullmatch(release): raise ValueError('Invalid release tag')
    head = git(root, 'rev-parse', '--verify', ref + '^{commit}').decode().strip()
    if not SHA.fullmatch(head): raise ValueError('Invalid upstream commit')
    baseline = ledger['reviewed_head']
    # Missing reviewed objects are an error, not proof of a rewritten upstream.
    git(root, 'cat-file', '-e', baseline + '^{commit}')
    code = subprocess.run(['git', '-c', 'safe.directory=' + str(root), '-C', str(root),
                           'merge-base', '--is-ancestor', baseline, head], capture_output=True).returncode
    if code not in (0, 1): raise ValueError('Cannot compare upstream ancestry')
    commits = []
    if code == 0:
        rows = git(root, 'log', '--reverse', '--format=%H%x09%s', baseline + '..' + head).decode('utf-8')
        for line in rows.splitlines():
            commit, subject = line.split('\t', 1)
            commits.append({'sha': commit, 'subject': subject})
    files = git(root, 'diff', '--name-only', '-z', baseline, head).decode('utf-8').split('\0')
    return {'schema': 1, 'repository': ledger['repository'], 'reviewed_head': baseline,
            'upstream_head': head, 'reviewed_release': ledger['reviewed_release'],
            'upstream_release': release, 'history_rewritten': code == 1,
            'pending': head != baseline or bool(release and release != ledger['reviewed_release']),
            'commits': commits, 'changed_files': [name for name in files if name]}


def safe(text):
    # Treat upstream commit subjects and paths as data, including Markdown and mentions.
    value = html.escape(text)
    for char in '@`|[]*_!\\':
        value = value.replace(char, f'&#{ord(char)};')
    return value.replace('\n', ' ').replace('\r', ' ')


def markdown(value):
    repo = 'https://github.com/' + value['repository']
    lines = ['# 上游更新待审查', '',
             '此报告只记录上游差异，不修改产品源码、不推进已审查基线、不执行上游脚本、不部署服务。', '',
             f"- 已审查提交：`{value['reviewed_head']}`",
             f"- 当前上游提交：`{value['upstream_head']}`",
             f"- 当前上游 Release：`{value['upstream_release'] or '无稳定发布'}`", '',
             '发现待评审更新。' if value['pending'] else '没有新的待评审提交或稳定发布。', '']
    if value['history_rewritten']:
        lines += ['**上游历史已分叉或重写，必须人工确认新的比较基线。**', '']
    lines += [f"[完整比较]({repo}/compare/{value['reviewed_head']}...{value['upstream_head']})", '', '## 提交', '']
    lines += [f"- [{row['sha'][:12]}]({repo}/commit/{row['sha']}) {safe(row['subject'])}" for row in value['commits'][:100]]
    if len(value['commits']) > 100: lines += ['- 更多提交见完整比较和 JSON 附件。']
    lines += ['', '## 受影响文件', '']
    lines += ['- ' + safe(name) for name in value['changed_files'][:200]]
    if len(value['changed_files']) > 200: lines += ['- 更多文件见 JSON 附件。']
    lines += ['', '## 处理要求', '',
              '逐项记录已合入、已移植、不适用或暂缓原因；功能移植使用独立分支。通过回归和部署验收后才能发布本项目版本。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, default=ROOT / '.github/upstream.json')
    parser.add_argument('--upstream-ref', default='refs/remotes/upstream/main')
    parser.add_argument('--release-tag', default='')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--json-output', type=Path, required=True)
    args = parser.parse_args()
    value = inspect(ROOT, ledger_value(args.ledger), args.upstream_ref, args.release_tag)
    for path in (args.output, args.json_output): path.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown(value), encoding='utf-8')
    args.json_output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value[key] for key in ('pending', 'upstream_head', 'history_rewritten')}))


if __name__ == '__main__': main()
