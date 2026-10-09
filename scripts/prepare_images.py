#!/usr/bin/env python3
"""Offline image manifest validation. Never uploads or reads API credentials.

Usage: python scripts/prepare_images.py --manifest private/images.json
Manifest: {"image_files": [{"url": "https://...", "file": "private/a.png"}]}
Paths are resolved from the repository root and must remain inside private/.
Results go to private/image-validation.json by default.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from threads_publisher.media import validate_image_file, MediaError
from threads_publisher.schema import public_image_url
from threads_publisher.core import SafeError
from threads_publisher.ai import atomic_json


def prepare(manifest_path, output_path):
    private = (ROOT / 'private').resolve()
    def private_path(value):
        path = Path(value)
        path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(private):
            raise SafeError('Image manifests, files and output must remain inside private/')
        return path
    manifest = private_path(manifest_path)
    output = private_path(output_path)
    try:
        entries = json.loads(manifest.read_text(encoding='utf-8'))['image_files']
        if not isinstance(entries, list) or not 1 <= len(entries) <= 140:
            raise ValueError()
        records=[]
        for entry in entries:
            url=public_image_url(entry['url'])
            file=private_path(entry['file'])
            metadata=validate_image_file(file)
            records.append({'url':url,'file':str(file.relative_to(ROOT)),**metadata})
        atomic_json(output, {'images':records,'uploaded':False})
        return len(records)
    except (OSError, ValueError, KeyError, TypeError, MediaError):
        raise SafeError('Image preparation failed; verify private manifest and image formats') from None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', default='private/image-validation.json')
    args=parser.parse_args()
    try:
        count=prepare(args.manifest,args.output)
    except SafeError as error:
        print(str(error),file=sys.stderr)
        return 1
    print(f'Validated {count} local images; nothing uploaded')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
