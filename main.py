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
    manifest = yaml.load(sys.stdin)
    c = find_container(args, manifest)
    if c is None:
        bail("container not found")
    c['image'] = args.image
    yaml.dump(manifest, sys.stdout)

def find_container(spec, manifest):
    if manifest['kind'] != spec.kind:
        bail("kind in manifest does not match that given")
    if manifest['metadata']['name'] != spec.name:
        bail("name in manifest does not match that given")
    for c in manifest['spec']['template']['spec']['containers']:
        if c['name'] == spec.container:
            return c
    return None

if __name__ == "__main__":
    main()
