"""Validate only reviewed, raster-only community submission folders."""
from pathlib import Path
import importlib.util
import json

ROOT=Path(__file__).resolve().parents[1]
PLUGIN=ROOT/'assets/plugins/astrbot_plugin_dafeiyu_stickers'


def validate(root: Path | None=None) -> list[dict]:
    path=PLUGIN/'custom_catalog.py'
    spec=importlib.util.spec_from_file_location('_aiwu_submission_validator',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    directory=root or PLUGIN/'community/submissions'
    items=module.read_contributions(directory)
    for item in items:
        folder=directory/item['submission']
        expected={'metadata.json','LICENSE.txt',item['file']}
        if {entry.name for entry in folder.iterdir()}!=expected:
            raise ValueError('A submission must contain only its image, metadata and licence')
        if not item['attribution'].strip():
            raise ValueError('A public submission must retain author attribution')
    return items


if __name__=='__main__':
    items=validate()
    print(json.dumps({'valid_submissions':len(items),'image_bytes':sum(item['size'] for item in items)}))
