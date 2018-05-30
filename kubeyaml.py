import sys
import argparse
import functools
from ruamel.yaml import YAML

class NotFound(Exception):
    pass

def parse_args():
    p = argparse.ArgumentParser()
    subparsers = p.add_subparsers()

    image = subparsers.add_parser('image', help='update an image ref')
    image.add_argument('--namespace', required=True)
    image.add_argument('--kind', required=True)
    image.add_argument('--name', required=True)
    image.add_argument('--container', required=True)
    image.add_argument('--image', required=True)
    image.set_defaults(func=update_image)

    def note(s):
        k, v = s.split('=')
        return k, v

    annotation = subparsers.add_parser('annotate', help='update annotations')
    annotation.add_argument('--namespace', required=True)
    annotation.add_argument('--kind', required=True)
    annotation.add_argument('--name', required=True)
    annotation.add_argument('notes', nargs='+', type=note)
    annotation.set_defaults(func=update_annotations)

    return p.parse_args()

def yaml():
    y = YAML()
    y.explicit_start = True
    y.preserve_quotes = True
    return y

def bail(reason):
        sys.stderr.write(reason); sys.stderr.write('\n')
        sys.exit(2)

def apply_to_yaml(fn, infile, outfile):
    # fn :: iterator a -> iterator b
    y = yaml()
    docs = y.load_all(infile)
    for doc in fn(docs):
        y.dump(doc, outfile)

def update_image(args, docs):
    """Update the manifest specified by args, in the stream of docs"""
    found = False
    for doc in docs:
        if not found:
            for m in manifests(doc):
                c = find_container(args, m)
                if c != None:
                    set_container_image(m, c, args.image)
                    found = True
                    break
        yield doc
    if not found:
        raise NotFound()

def update_annotations(spec, docs):
    def ensure(d, *keys):
        for k in keys:
            try:
                d = d[k]
            except KeyError:
                d[k] = dict()
                d = d[k]
        return d

    found = False
    for doc in docs:
        if not found:
            for m in manifests(doc):
                if match_manifest(spec, m):
                    notes = ensure(m, 'metadata', 'annotations')
                    for k, v in spec.notes:
                        if v == '':
                            try:
                                del notes[k]
                            except KeyError:
                                pass
                        else:
                            notes[k] = v
                    if len(notes) == 0:
                        del m['metadata']['annotations']
                    found = True
                    break
        yield doc
    if not found:
        raise NotFound()

def manifests(doc):
    if doc['kind'] == 'List':
        for m in doc['items']:
            yield m
    else:
        yield doc

def match_manifest(spec, manifest):
    try:
        # NB treat the Kind as case-insensitive
        if manifest['kind'].lower() != spec.kind.lower():
            return False
        if manifest['metadata'].get('namespace', 'default') != spec.namespace:
            return False
        if manifest['metadata']['name'] != spec.name:
            return False
    except KeyError:
        return False
    return True

def containers(manifest):
    if manifest['kind'] == 'CronJob':
        return manifest['spec']['jobTemplate']['spec']['template']['spec']['containers']
    elif manifest['kind'] == 'FluxHelmRelease':
        return [{
            'name': manifest['spec']['chartGitPath'],
            'image': manifest['spec']['values']['image']
        }]
    return manifest['spec']['template']['spec']['containers']

def find_container(spec, manifest):
    if not match_manifest(spec, manifest):
        return None
    for c in containers(manifest):
        if c['name'] == spec.container:
            return c
    return None

def set_container_image(manifest, container, image):
    if manifest['kind'] == 'FluxHelmRelease':
        manifest['spec']['values']['image'] = image
    else:
        container['image'] = image

def main():
    args = parse_args()
    try:
        apply_to_yaml(functools.partial(args.func, args), sys.stdin, sys.stdout)
    except NotFound:
        bail("manifest not found")

if __name__ == "__main__":
    main()
