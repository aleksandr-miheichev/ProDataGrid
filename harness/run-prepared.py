#!/usr/bin/env python3
"""Maintainer timing protocol with pre-indexed, externally prepared worktrees.

Only setup differs from run-comparison.py: fixtures are prepared before source
injection to satisfy local CodeGraph requirements. The C# harness, timed command,
warmups, sample count and balanced process orders are unchanged. Optional reuse
keeps identical binaries for reverse-order and same-binary A/A controls.
"""
import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--a', type=Path, required=True)
    parser.add_argument('--b', type=Path, required=True)
    parser.add_argument('--base', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--scenario', action='append', required=True)
    parser.add_argument('--order', choices=['ABBABAAB', 'BAABABBA'], default='ABBABAAB')
    parser.add_argument('--skip-build', action='store_true')
    args = parser.parse_args()
    if args.scenario != ['source', 'scroll']:
        parser.error('Use source and scroll exactly once, in that order.')
    output = args.output.resolve()
    if output.exists():
        parser.error('Output must be new; all earlier attempts are retained.')
    output.mkdir(parents=True)
    source = Path(__file__).resolve().parent
    harness = source / 'ReviewPerformanceTests.cs'
    revisions, worktrees, binaries = {}, {}, {}
    manifest = {'artifact': '5b6504c697040b8ae76ff36e340dd9610a326e3b',
                'harness_sha256': digest(harness), 'runner_sha256': digest(Path(__file__)),
                'original_runner_sha256': digest(source / 'run-comparison.py'),
                'order': args.order, 'skip_build': args.skip_build,
                'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'environment': {k: v for k, v in os.environ.items() if k in {
                    'DOTNET_TieredCompilation', 'COMPlus_TieredCompilation',
                    'DOTNET_gcServer', 'COMPlus_gcServer', 'DOTNET_ReadyToRun',
                    'COMPlus_ReadyToRun', 'DOTNET_GCHeapHardLimit', 'DOTNET_GCConserveMemory',
                    'CORECLR_ENABLE_PROFILING', 'COR_ENABLE_PROFILING'}}}
    stage = 'setup'
    try:
        for side, revision, directory in [('A', args.base, args.a), ('B', args.candidate, args.b)]:
            sha = subprocess.check_output(['git', '-C', str(args.repo), 'rev-parse', revision + '^{commit}'], text=True).strip()
            worktree = directory.resolve()
            actual = subprocess.check_output(['git', '-C', str(worktree), 'rev-parse', 'HEAD'], text=True).strip()
            if actual != sha:
                raise RuntimeError(f'{side} revision mismatch: {actual} != {sha}')
            injected = worktree / 'src/Avalonia.Controls.DataGrid.UnitTests/ReviewPerformanceTests.cs'
            if digest(injected) != digest(harness):
                raise RuntimeError(f'{side} harness differs from the immutable artifact')
            revisions[side], worktrees[side] = sha, worktree
            (output / 'revisions.json').write_text(json.dumps(revisions, indent=2))
            stage = f'build-{side}'
            if not args.skip_build:
                print(stage, flush=True)
                command = ['dotnet', 'build', 'src/Avalonia.Controls.DataGrid.UnitTests', '-c', 'Release', '-p:CollectCoverage=false', '--verbosity', 'quiet']
                with (output / (side + '-build.log')).open('w') as log:
                    subprocess.run(command, cwd=worktree, stdout=log, stderr=subprocess.STDOUT, check=True)
            assembly = worktree / 'src/Avalonia.Controls.DataGrid.UnitTests/bin/Release/net10.0/Avalonia.Controls.DataGrid.UnitTests.dll'
            binaries[side] = {'assembly_sha256': digest(assembly),
                              'runtimeconfig_sha256': digest(assembly.with_suffix('.runtimeconfig.json')),
                              'deps_sha256': digest(assembly.with_suffix('.deps.json')),
                              'sdk': subprocess.check_output(['dotnet', '--version'], cwd=worktree, text=True).strip()}
        manifest.update({'revisions': revisions, 'binaries': binaries})
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        with (output / 'dotnet-info.txt').open('w') as log:
            subprocess.run(['dotnet', '--info'], cwd=args.repo, stdout=log, check=True)
        if any(os.environ.get(k) == '1' for k in ('CORECLR_ENABLE_PROFILING', 'COR_ENABLE_PROFILING')):
            raise RuntimeError('Profiler-enabled environment is not an unprofiled timing run')
        for scenario in args.scenario:
            for index, side in enumerate(args.order):
                stage = f'{scenario}-{index}-{side}'
                print(stage, flush=True)
                result = output / f'{scenario}-{side}.jsonl'
                env = dict(os.environ, DOTNET_TieredCompilation='0', REVIEW_SCENARIO=scenario, REVIEW_OUTPUT=str(result))
                assembly = worktrees[side] / 'src/Avalonia.Controls.DataGrid.UnitTests/bin/Release/net10.0/Avalonia.Controls.DataGrid.UnitTests.dll'
                command = ['dotnet', str(assembly), '-class', '*ReviewPerformanceTests', '-parallel', 'none']
                with (output / f'{stage}.log').open('w') as log:
                    subprocess.run(command, cwd=worktrees[side], env=env, stdout=log, stderr=subprocess.STDOUT, timeout=240, check=True)
            summary = {'scenario': scenario, 'revisions': revisions, 'order': args.order}
            metadata = set()
            for side in 'AB':
                samples = [json.loads(line) for line in (output / f'{scenario}-{side}.jsonl').read_text().splitlines()]
                if len(samples) != 4:
                    raise RuntimeError(f'Expected four processes for {scenario}/{side}')
                for sample in samples:
                    if sample['scenario'] != scenario or sample['count'] != 10000:
                        raise RuntimeError('Unmatched workload metadata')
                    metadata.add(tuple(sample[k] for k in ('count', 'selected', 'selectedCells', 'virtualSurface')))
                    for key in ('times', 'bytes'):
                        if len(sample[key]) != 24 or any(not math.isfinite(v) or v < 0 for v in sample[key]):
                            raise RuntimeError('Invalid sample vector')
                summary[side] = {'process_mean_ms': [statistics.mean(s['times']) for s in samples],
                                 'process_mean_bytes': [statistics.mean(s['bytes']) for s in samples]}
            if len(metadata) != 1:
                raise RuntimeError('A/B workload capabilities differ')
            with (output / 'summary.jsonl').open('a') as log:
                log.write(json.dumps(summary) + '\n')
            print(json.dumps(summary), flush=True)
        stage = 'completed'
    finally:
        manifest.update({'revisions': revisions, 'binaries': binaries, 'last_stage': stage,
                         'ended_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()})
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
