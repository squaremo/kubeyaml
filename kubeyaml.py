import sys
import argparse
from ruamel.yaml import YAML

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--namespace')
    p.add_argument('--kind')
    p.add_argument('--name')
    p.add_argument('--container')
    p.add_argument('--image')
    return p.parse_args()

def bail(reason):
        print >> sys.stderr, reason
        sys.exit(2)

def main():
    args = parse_args()
    if args.image is None:
        bail("Argument --image required")

    yaml = YAML()
    yaml.explicit_start = True

    found = False
    for doc in yaml.load_all(sys.stdin):
        if not found:
            for m in manifests(doc):
                c = find_container(args, m)
                if c != None:
                    c['image'] = args.image
                    found = True
                    break
        yaml.dump(doc, sys.stdout)
    if not found:
        bail("Container not found")

def manifests(doc):
    if doc['kind'] == 'List':
        for m in doc['items']:
            yield m
    else:
        yield doc

def find_container(spec, manifest):
    if manifest['kind'] != spec.kind:
        return None
    if manifest['metadata']['name'] != spec.name:
        return None
    for c in manifest['spec']['template']['spec']['containers']:
        if c['name'] == spec.container:
            return c
    return None

if __name__ == "__main__":
    main()
